"""Embedded terminal: a pyte screen drawn by a Qt widget, attached to a PTY."""

from __future__ import annotations

import sys
import threading
import time
from collections import deque

import pyte
from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication,
                           QKeySequence, QPainter, QPainterPath, QPen, QPolygonF)
from PySide6.QtWidgets import QAbstractScrollArea, QMenu, QMessageBox

from .pty_backend import PtyError, PtyProcess
from .term_schemes import BUILTIN, DEFAULT_DARK, ColorScheme
from .terminal_core import (TerminalScreen, encode_key, paste_line_count,
                            sanitize_paste)

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform.startswith("win")

# Powerline separators (U+E0B0 to U+E0BF), used by prompt themes such as Powerlevel10k,
# Oh My Posh and Starship. They are drawn as shapes that fill the cell exactly, as
# Windows Terminal and kitty do, so segments meet without seams and the arrows show
# even when the font has no Nerd Font symbols.
POWERLINE = frozenset(range(0xE0B0, 0xE0C0))


def draw_powerline(p: QPainter, code: int, rect: QRectF, color: QColor) -> None:
    from PySide6.QtCore import QPointF
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    left, right, top, bottom, mid = x, x + w, y, y + h, y + h / 2
    # Solid shapes reach a little past their flat side, into the neighbouring
    # segment, so no background line shows between them.
    pad = 0.6
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    line = QPen(color, max(1.0, w / 8))
    line.setCapStyle(Qt.PenCapStyle.FlatCap)
    line.setJoinStyle(Qt.PenJoinStyle.MiterJoin)

    def solid(*pts):
        p.drawPolygon(QPolygonF([QPointF(a, b) for a, b in pts]))

    def stroke(*pts):
        p.setPen(line)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(QPolygonF([QPointF(a, b) for a, b in pts]))

    if code == 0xE0B0:                         # right-pointing solid arrow
        solid((left - pad, top), (right, mid), (left - pad, bottom))
    elif code == 0xE0B1:                       # right-pointing thin arrow
        stroke((left, top), (right, mid), (left, bottom))
    elif code == 0xE0B2:                       # left-pointing solid arrow
        solid((right + pad, top), (left, mid), (right + pad, bottom))
    elif code == 0xE0B3:                       # left-pointing thin arrow
        stroke((right, top), (left, mid), (right, bottom))
    elif code in (0xE0B4, 0xE0B5, 0xE0B6, 0xE0B7):   # rounded caps
        path = QPainterPath()
        if code in (0xE0B4, 0xE0B5):           # bulge to the right
            path.moveTo(left - pad, top)
            path.arcTo(QRectF(left - w, top, 2 * w, h), 90, -180)
            if code == 0xE0B4:
                path.lineTo(left - pad, bottom)
        else:                                  # bulge to the left
            path.moveTo(right + pad, top)
            path.arcTo(QRectF(left, top, 2 * w, h), 90, 180)
            if code == 0xE0B6:
                path.lineTo(right + pad, bottom)
        if code in (0xE0B4, 0xE0B6):
            path.closeSubpath()
            p.drawPath(path)
        else:
            p.setPen(line)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
    elif code == 0xE0B8:                       # lower-left triangle
        solid((left - pad, top), (right, bottom + pad), (left - pad, bottom + pad))
    elif code == 0xE0BA:                       # lower-right triangle
        solid((right + pad, top), (right + pad, bottom + pad), (left, bottom + pad))
    elif code == 0xE0BC:                       # upper-left triangle
        solid((left - pad, top - pad), (right, top - pad), (left - pad, bottom))
    elif code == 0xE0BE:                       # upper-right triangle
        solid((left, top - pad), (right + pad, top - pad), (right + pad, bottom))
    elif code in (0xE0B9, 0xE0BF):             # backslash separators
        stroke((left, top), (right, bottom))
    elif code in (0xE0BB, 0xE0BD):             # forward slash separators
        stroke((right, top), (left, bottom))
    p.restore()


MAX_FEED_PER_TICK = 256 * 1024   # keep the UI responsive during huge output
FRAME_MS = 16

_KEY_NAMES = {
    Qt.Key.Key_Return: "enter", Qt.Key.Key_Enter: "enter", Qt.Key.Key_Backspace: "backspace",
    Qt.Key.Key_Tab: "tab", Qt.Key.Key_Backtab: "backtab", Qt.Key.Key_Escape: "escape",
    Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down", Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right", Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end",
    Qt.Key.Key_Insert: "insert", Qt.Key.Key_Delete: "delete",
    Qt.Key.Key_PageUp: "pageup", Qt.Key.Key_PageDown: "pagedown",
    **{getattr(Qt.Key, f"Key_F{i}"): f"f{i}" for i in range(1, 13)},
}


def default_font(family: str = "", size: int = 0) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    preferred = [family] if family else []
    preferred += (["Cascadia Mono", "Consolas"] if IS_WINDOWS else
                  ["SF Mono", "Menlo"] if IS_MAC else ["DejaVu Sans Mono", "Liberation Mono"])
    available = set(QFontDatabase.families())
    for name in preferred:
        if name in available:
            font = QFont(name)
            break
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    font.setPointSize(size or (13 if IS_MAC else 10))
    return font


class _Bridge(QObject):
    """Carries PTY events from the reader thread to the UI thread."""
    data = Signal()
    exited = Signal(object)


class TerminalWidget(QAbstractScrollArea):
    # Draw Powerline separators as shapes (Settings > Terminal). Applies to all tabs.
    draw_powerline = True

    title_changed = Signal(str)
    exited = Signal(object)          # exit code or None

    def __init__(self, argv: list[str], env: dict[str, str], cwd: str | None = None,
                 font: QFont | None = None, scrollback: int = 10000,
                 confirm_multiline_paste: bool = True, parent=None):
        super().__init__(parent)
        self.argv, self.env, self.cwd = argv, env, cwd
        self.confirm_multiline_paste = confirm_multiline_paste
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.viewport().setAutoFillBackground(False)
        self.scheme: ColorScheme = BUILTIN[DEFAULT_DARK]
        self._apply_scheme_style()

        self._scrollback_size = scrollback
        # Hooks used by SSH password filling: output_observers get each batch of
        # output text; input_observers are told when the user sends input.
        self.output_observers: list = []
        self.input_observers: list = []
        self.screen = TerminalScreen(80, 24, history=scrollback, reply=self._reply,
                                     on_title=lambda t: self.title_changed.emit(t))
        self.stream = pyte.Stream(self.screen)
        self.pty: PtyProcess | None = None
        self.exit_code = None
        self.running = False

        self._pending: deque[str] = deque()
        self._pending_lock = threading.Lock()
        self._bridge = _Bridge()
        self._bridge.data.connect(self._schedule_feed, Qt.ConnectionType.QueuedConnection)
        self._bridge.exited.connect(self._on_exit, Qt.ConnectionType.QueuedConnection)
        self._feed_timer = QTimer(self, singleShot=True, interval=0)
        self._feed_timer.timeout.connect(self._feed)
        self._resize_timer = QTimer(self, singleShot=True, interval=40)
        self._resize_timer.timeout.connect(self._apply_resize)

        self._scroll = 0                 # lines scrolled up from the bottom
        self._want_start = False
        self._sel_anchor = None          # (abs_line, col)
        self._sel_end = None
        self._selecting = False
        self._last_click = (0.0, None)

        self.verticalScrollBar().valueChanged.connect(self._on_scrollbar)
        self.set_font(font or default_font())

    # ---- process ---------------------------------------------------------

    def start(self) -> None:
        """Start the shell once the widget has its real size, so the first
        program output is laid out for the right width."""
        self._want_start = True
        if self.isVisible() and self.viewport().width() > 50:
            self._spawn()
        else:
            self._schedule_resize()

    def _spawn(self) -> None:
        self._want_start = False
        cols, rows = self._grid_size()
        self.screen.resize(rows, cols)
        try:
            self.pty = PtyProcess(self.argv, self.env, self.cwd, cols, rows,
                                  on_data=self._on_pty_data,
                                  on_exit=lambda code: self._bridge.exited.emit(code))
        except PtyError as exc:
            self._print_notice(f"Could not start the shell: {exc}")
            self.exit_code = None
            self.exited.emit(None)
            return
        self.running = True

    def restart(self) -> None:
        self.terminate()
        self.screen.reset()
        self.screen.scrollback.clear()
        self._scroll = 0
        self.exit_code = None
        self._spawn()
        self.viewport().update()

    def terminate(self) -> None:
        if self.pty and self.pty.is_alive():
            self.pty.terminate()
            self.pty.wait(2)
        self.running = False

    def is_alive(self) -> bool:
        return bool(self.pty and self.pty.is_alive())

    def has_foreground_job(self) -> bool:
        return bool(self.pty and self.pty.is_alive() and self.pty.has_foreground_job())

    def _on_pty_data(self, text: str) -> None:   # reader thread
        with self._pending_lock:
            first = not self._pending
            self._pending.append(text)
        if first:
            self._bridge.data.emit()

    def _schedule_feed(self) -> None:
        if not self._feed_timer.isActive():
            self._feed_timer.start(0)

    def _feed(self) -> None:
        budget = MAX_FEED_PER_TICK
        chunks = []
        with self._pending_lock:
            while self._pending and budget > 0:
                chunk = self._pending.popleft()
                budget -= len(chunk)
                chunks.append(chunk)
            more = bool(self._pending)
        if chunks:
            before = len(self.screen.scrollback)
            text = "".join(chunks)
            self.stream.feed(text)
            # After the screen is updated, so observers can read it.
            for observe in list(self.output_observers):
                observe(text)
            if self._scroll:
                # Keep the view still while new output arrives.
                self._scroll = min(self._scroll + len(self.screen.scrollback) - before,
                                   len(self.screen.scrollback))
            self._update_scrollbar()
            self.viewport().update()
        if more:
            self._feed_timer.start(FRAME_MS)

    def _on_exit(self, code) -> None:
        self._feed()
        self.running = False
        self.exit_code = code
        msg = f"[process exited with code {code}]" if code is not None else "[process exited]"
        self._print_notice(msg)
        self.exited.emit(code)

    def _print_notice(self, msg: str) -> None:
        self.stream.feed(f"\r\n\x1b[90m{msg}\x1b[0m\r\n")
        self.viewport().update()

    def _reply(self, data: str) -> None:
        if self.pty:
            self.pty.write(data)

    # ---- public input API (used by the Commands pane) -------------------

    def send_text(self, text: str) -> None:
        if self.pty and self.pty.is_alive():
            for observe in list(self.input_observers):
                observe()
            self.pty.write(text)
            self._scroll_to_bottom()

    def text_before_cursor(self, max_rows: int = 4) -> tuple[str, int]:
        """The screen text from up to ``max_rows`` rows above the cursor through the
        cursor, rows joined without separators, plus the screen width. A line that
        wrapped reads as one piece; each row starts at a multiple of the width."""
        cols = self.screen.columns
        y, x = self.screen.cursor.y, min(self.screen.cursor.x, cols)
        rows = []
        for r in range(max(0, y - max_rows + 1), y + 1):
            line = self.screen.buffer[r]
            width = cols if r < y else x
            rows.append("".join((line[c].data or " ") for c in range(width)))
        return "".join(rows), cols

    def write_secret(self, text: str) -> None:
        """Send text to the program without recording or echoing it on our side (used
        to answer a password prompt). Not reported to input observers."""
        if self.pty and self.pty.is_alive():
            self.pty.write(text)

    def insert_command(self, command: str, run: bool = False) -> None:
        """Type a command at the prompt; press Enter only when run=True."""
        # Newlines and control characters are removed, so the text is typed as
        # plain input (a bracketed paste would make some shells highlight it).
        safe = sanitize_paste(command.replace("\r", " ").replace("\n", " "), bracketed=False)
        self.send_text(safe + ("\r" if run else ""))

    # ---- font and geometry -----------------------------------------------

    def set_scheme(self, scheme: ColorScheme) -> None:
        """Change colors in place; the shell keeps running."""
        if scheme == self.scheme:
            return
        self.scheme = scheme
        self._apply_scheme_style()
        self.viewport().update()

    def _apply_scheme_style(self) -> None:
        self.setStyleSheet(
            f"QAbstractScrollArea {{ background: {self.scheme.background}; border: none; }}")

    def set_font(self, font: QFont) -> None:
        self._font = QFont(font)
        self._font_bold = QFont(font)
        self._font_bold.setBold(True)
        self._font_italic = QFont(font)
        self._font_italic.setItalic(True)
        fm = QFontMetricsF(self._font)
        self._cw = max(fm.horizontalAdvance("M"), 1.0)
        self._ch = max(fm.lineSpacing(), 1.0)
        self._ascent = fm.ascent()
        self._schedule_resize()

    def _grid_size(self) -> tuple[int, int]:
        vp = self.viewport().size()
        return max(int(vp.width() // self._cw), 10), max(int(vp.height() // self._ch), 3)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_resize()

    def _schedule_resize(self):
        self._resize_timer.start()

    def _apply_resize(self):
        if getattr(self, "_want_start", False):
            if self.isVisible() and self.viewport().width() > 50:
                self._spawn()
            else:
                self._resize_timer.start()
            return
        cols, rows = self._grid_size()
        if (cols, rows) != (self.screen.columns, self.screen.lines):
            self.screen.resize(rows, cols)
            if self.pty:
                self.pty.resize(cols, rows)
            self._update_scrollbar()
            self.viewport().update()

    # ---- scrolling -------------------------------------------------------

    def _update_scrollbar(self):
        sb = self.verticalScrollBar()
        hist = 0 if self.screen.alt_active else len(self.screen.scrollback)
        sb.blockSignals(True)
        sb.setRange(0, hist)
        sb.setPageStep(self.screen.lines)
        sb.setValue(hist - self._scroll)
        sb.blockSignals(False)

    def _on_scrollbar(self, value: int):
        hist = len(self.screen.scrollback)
        self._scroll = max(0, min(hist, hist - value))
        self.viewport().update()

    def scroll_lines(self, delta: int):
        if self.screen.alt_active:
            return
        hist = len(self.screen.scrollback)
        self._scroll = max(0, min(hist, self._scroll + delta))
        self._update_scrollbar()
        self.viewport().update()

    def _scroll_to_bottom(self):
        if self._scroll:
            self._scroll = 0
            self._update_scrollbar()
            self.viewport().update()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 40
        if self.screen.alt_active:
            # Alternate scroll mode: arrows for full-screen apps (less, man, vim).
            key = "up" if steps > 0 else "down"
            seq = encode_key(key, app_cursor=self.screen.app_cursor_keys)
            self.send_text(seq * max(1, int(abs(steps))))
        else:
            self.scroll_lines(int(round(steps)) or (1 if steps > 0 else -1))
        event.accept()

    # ---- painting --------------------------------------------------------

    def _visible_lines(self):
        """Yield (abs_line_index, line) for each visible row."""
        hist = [] if self.screen.alt_active else self.screen.scrollback
        top = len(hist) - (0 if self.screen.alt_active else self._scroll)
        rows = self.screen.lines
        for r in range(rows):
            idx = top + r
            if idx < len(hist):
                yield idx, hist[idx]
            else:
                yield idx, self.screen.buffer[idx - len(hist)]

    def _selection_range(self):
        if not self._sel_anchor or not self._sel_end or self._sel_anchor == self._sel_end:
            return None
        a, b = sorted([self._sel_anchor, self._sel_end])
        return a, b

    def _is_selected(self, line_idx: int, col: int, sel) -> bool:
        if not sel:
            return False
        (l1, c1), (l2, c2) = sel
        if line_idx < l1 or line_idx > l2:
            return False
        if l1 == l2:
            return c1 <= col < c2
        if line_idx == l1:
            return col >= c1
        if line_idx == l2:
            return col < c2
        return True

    def paintEvent(self, event):
        p = QPainter(self.viewport())
        s = self.scheme
        default_fg, default_bg = s.foreground, s.background
        cursor_color, selection_bg = s.cursor, s.selection
        resolve = s.resolve
        p.fillRect(self.viewport().rect(), QColor(default_bg))
        screen = self.screen
        cols = screen.columns
        cw, ch, asc = self._cw, self._ch, self._ascent
        sel = self._selection_range()
        reverse_screen = (5 << 5) in screen.mode     # DECSCNM

        for row, (abs_idx, line) in enumerate(self._visible_lines()):
            y = row * ch
            col = 0
            while col < cols:
                char = line[col]
                fg = resolve(char.fg, default_fg)
                bg = resolve(char.bg, default_bg)
                if char.reverse != reverse_screen:
                    fg, bg = bg, fg
                selected = self._is_selected(abs_idx, col, sel)
                if selected:
                    bg = selection_bg
                style = (fg, bg, char.bold, char.italics, char.underscore,
                         char.strikethrough, selected)
                # Group a run of cells with the same style and single-width text.
                start = col
                text = [char.data or " "]
                wide = len(char.data) == 1 and col + 1 < cols and line[col + 1].data == ""
                powerline = (self.draw_powerline and len(char.data) == 1
                             and ord(char.data) in POWERLINE)
                col += 2 if wide else 1
                if not wide and not powerline:
                    while col < cols:
                        nxt = line[col]
                        nfg = resolve(nxt.fg, default_fg)
                        nbg = resolve(nxt.bg, default_bg)
                        if nxt.reverse != reverse_screen:
                            nfg, nbg = nbg, nfg
                        nsel = self._is_selected(abs_idx, col, sel)
                        if nsel:
                            nbg = selection_bg
                        nstyle = (nfg, nbg, nxt.bold, nxt.italics, nxt.underscore,
                                  nxt.strikethrough, nsel)
                        if nstyle != style or (col + 1 < cols and line[col + 1].data == ""):
                            break
                        if nxt.data and (len(nxt.data) != 1 or ord(nxt.data) > 0x2fff):
                            break
                        text.append(nxt.data or " ")
                        col += 1
                width_cells = col - start
                rect = QRectF(start * cw, y, width_cells * cw, ch)
                if bg != default_bg:
                    p.fillRect(rect, QColor(bg))
                run = "".join(text)
                if powerline:
                    draw_powerline(p, ord(run), rect, QColor(fg))
                elif run.strip():
                    font = (self._font_bold if style[2] else
                            self._font_italic if style[3] else self._font)
                    if style[3] and style[2]:
                        font = QFont(self._font_bold)
                        font.setItalic(True)
                    font.setUnderline(bool(style[4]))
                    font.setStrikeOut(bool(style[5]))
                    p.setFont(font)
                    p.setPen(QColor(fg))
                    p.drawText(QRectF(start * cw, y, width_cells * cw + cw, ch),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, run)
                    font.setUnderline(False)
                    font.setStrikeOut(False)

        # Cursor
        if not screen.cursor.hidden and (self._scroll == 0 or screen.alt_active):
            cx, cy = screen.cursor.x, screen.cursor.y
            if cx < cols and cy < screen.lines:
                rect = QRectF(cx * cw, cy * ch, cw, ch)
                if self.hasFocus():
                    p.fillRect(rect, QColor(cursor_color))
                    ch_data = screen.buffer[cy][cx].data
                    if ch_data.strip():
                        p.setFont(self._font)
                        p.setPen(QColor(default_bg))
                        p.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                                   ch_data)
                else:
                    p.setPen(QPen(QColor(cursor_color), 1))
                    p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.viewport().update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.viewport().update()

    # ---- keyboard --------------------------------------------------------

    def event(self, e):
        # Keep Tab and Shift+Tab for the shell instead of focus navigation.
        if (e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
                and not (e.modifiers() & Qt.KeyboardModifier.ControlModifier)):
            self.keyPressEvent(e)
            return True
        if e.type() == QEvent.Type.ShortcutOverride:
            # Let the terminal see Ctrl+letter even if the window has shortcuts.
            mods = e.modifiers()
            term_ctrl = Qt.KeyboardModifier.MetaModifier if IS_MAC else Qt.KeyboardModifier.ControlModifier
            app_keys = (Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_1, Qt.Key.Key_2)
            if (mods & term_ctrl and not (mods & Qt.KeyboardModifier.ShiftModifier)
                    and e.key() not in app_keys):
                e.accept()
                return True
        return super().event(e)

    def keyPressEvent(self, e):
        key = e.key()
        mods = e.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        # On macOS, Qt reports Cmd as Control and the physical Ctrl key as Meta.
        if IS_MAC:
            cmd = bool(mods & Qt.KeyboardModifier.ControlModifier)
            ctrl = bool(mods & Qt.KeyboardModifier.MetaModifier)
        else:
            cmd = False
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)

        # App shortcuts first.
        if IS_MAC and cmd:
            if key == Qt.Key.Key_C:
                self.copy_selection()
            elif key == Qt.Key.Key_V:
                self.paste()
            elif key == Qt.Key.Key_A:
                self.select_all()
            elif key == Qt.Key.Key_K:
                self.clear_scrollback()
            else:
                e.ignore()
            return
        if not IS_MAC and ctrl and shift and key in (Qt.Key.Key_C, Qt.Key.Key_V):
            self.copy_selection() if key == Qt.Key.Key_C else self.paste()
            return
        if not IS_MAC and ctrl and not shift and key == Qt.Key.Key_C and self._selection_range():
            self.copy_selection()      # like Windows Terminal: copy if text is selected
            self.clear_selection()
            return
        if not IS_MAC and ctrl and not shift and key == Qt.Key.Key_V:
            self.paste()
            return
        if shift and key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown) and not self.screen.alt_active:
            page = self.screen.lines - 1
            self.scroll_lines(page if key == Qt.Key.Key_PageUp else -page)
            return
        if shift and key in (Qt.Key.Key_Home, Qt.Key.Key_End) and not self.screen.alt_active:
            self.scroll_lines(len(self.screen.scrollback) if key == Qt.Key.Key_Home
                              else -len(self.screen.scrollback))
            return

        name = _KEY_NAMES.get(key)
        text = e.text()
        if name is None:
            # Qt key codes for ASCII keys equal their character codes, which is
            # more reliable than e.text() while Ctrl is held.
            name = chr(key).lower() if ctrl and 0 < key < 128 else text
        seq = encode_key(name or "", text, shift=shift, ctrl=ctrl, alt=alt and not IS_MAC,
                         app_cursor=self.screen.app_cursor_keys)
        if seq:
            self.clear_selection()
            self.send_text(seq)
            e.accept()
        else:
            e.ignore()

    def inputMethodEvent(self, e):
        if e.commitString():
            self.send_text(e.commitString())
        e.accept()

    # ---- selection and clipboard ----------------------------------------

    def _cell_at(self, pos: QPoint) -> tuple[int, int]:
        col = max(0, min(self.screen.columns, int(pos.x() // self._cw + 0.5)))
        row = max(0, min(self.screen.lines - 1, int(pos.y() // self._ch)))
        hist = 0 if self.screen.alt_active else len(self.screen.scrollback)
        top = hist - (0 if self.screen.alt_active else self._scroll)
        return top + row, col

    def _line(self, abs_idx: int):
        hist = [] if self.screen.alt_active else self.screen.scrollback
        if abs_idx < len(hist):
            return hist[abs_idx]
        return self.screen.buffer[abs_idx - len(hist)]

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            cell = self._cell_at(e.position().toPoint())
            now = time.monotonic()
            last_t, last_cell = self._last_click
            if last_cell and now - last_t < 0.4 and last_cell[0] == cell[0]:
                self._select_word(cell)
                self._last_click = (0.0, None)
            else:
                self._sel_anchor = self._sel_end = cell
                self._selecting = True
                self._last_click = (now, cell)
            self.viewport().update()
        elif e.button() == Qt.MouseButton.MiddleButton and not IS_WINDOWS:
            self.paste()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._selecting:
            self._sel_end = self._cell_at(e.position().toPoint())
            y = e.position().y()
            if y < 0:
                self.scroll_lines(1)
            elif y > self.viewport().height():
                self.scroll_lines(-1)
            self.viewport().update()

    def mouseReleaseEvent(self, e):
        self._selecting = False
        super().mouseReleaseEvent(e)

    def _select_word(self, cell):
        line_idx, col = cell
        line = self._line(line_idx)
        cols = self.screen.columns

        def is_word(c):
            d = line[c].data
            return bool(d) and (d.isalnum() or d in "-_./:@~=+%")

        start, end = col, col
        while start > 0 and is_word(start - 1):
            start -= 1
        while end < cols and is_word(end):
            end += 1
        self._sel_anchor, self._sel_end = (line_idx, start), (line_idx, end)

    def select_all(self):
        total = len(self.screen.all_lines()) if not self.screen.alt_active else self.screen.lines
        self._sel_anchor, self._sel_end = (0, 0), (total - 1, self.screen.columns)
        self.viewport().update()

    def clear_selection(self):
        if self._sel_anchor:
            self._sel_anchor = self._sel_end = None
            self.viewport().update()

    def selected_text(self) -> str:
        sel = self._selection_range()
        if not sel:
            return ""
        (l1, c1), (l2, c2) = sel
        out = []
        for idx in range(l1, l2 + 1):
            line = self._line(idx)
            start = c1 if idx == l1 else 0
            end = c2 if idx == l2 else self.screen.columns
            out.append("".join(line[x].data for x in range(start, end)).rstrip())
        return "\n".join(out)

    def copy_selection(self):
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)

    def paste(self):
        text = QGuiApplication.clipboard().text()
        if not text:
            return
        lines = paste_line_count(text)
        if lines > 1 and self.confirm_multiline_paste:
            preview = "\n".join(text.splitlines()[:8])
            if lines > 8:
                preview += f"\n... ({lines - 8} more lines)"
            box = QMessageBox(QMessageBox.Icon.Warning, "Paste multiple lines",
                              f"You are about to paste {lines} lines. Each line may run "
                              "as a command.", parent=self)
            box.setDetailedText(preview)
            box.setInformativeText(preview[:400])
            paste_btn = box.addButton("Paste", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is not paste_btn:
                return
        self.send_text(sanitize_paste(text, self.screen.bracketed_paste))

    def clear_scrollback(self):
        self.screen.scrollback.clear()
        self._scroll = 0
        self.clear_selection()
        self._update_scrollbar()
        self.viewport().update()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        copy_act = menu.addAction("Copy", self.copy_selection)
        copy_act.setEnabled(bool(self._selection_range()))
        copy_act.setShortcut(QKeySequence("Ctrl+C" if IS_MAC else "Ctrl+Shift+C"))
        paste_act = menu.addAction("Paste", self.paste)
        paste_act.setShortcut(QKeySequence("Ctrl+V" if IS_MAC else "Ctrl+Shift+V"))
        menu.addAction("Select all", self.select_all)
        menu.addSeparator()
        menu.addAction("Clear scrollback", self.clear_scrollback)
        menu.exec(e.globalPos())
