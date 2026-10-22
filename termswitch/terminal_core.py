"""Terminal logic that does not depend on Qt widgets: screen model, key
encoding, and paste sanitizing. Kept separate so it can be unit tested."""

from __future__ import annotations

from collections import deque
from copy import copy
from typing import Callable

import pyte
from pyte.screens import Margins

# Private (DEC) modes, as pyte stores them (mode << 5).
DECCKM = 1 << 5             # application cursor keys
BRACKETED_PASTE = 2004 << 5
ALT_SCREEN_MODES = (47, 1047, 1049)

# The default dark scheme's colors. Terminal widgets use a ColorScheme
# (term_schemes.py); these names remain for code and tests that want the defaults.
from .term_schemes import ANSI_NAMES as _ANSI_NAMES, BUILTIN as _BUILTIN  # noqa: E402

_DARK = _BUILTIN["Dark"]
PALETTE = {**{n: _DARK.ansi[i] for i, n in enumerate(_ANSI_NAMES)},
           **{"bright" + n: _DARK.ansi[i + 8] for i, n in enumerate(_ANSI_NAMES)},
           "yellow": _DARK.ansi[3], "brightyellow": _DARK.ansi[11]}
DEFAULT_FG = _DARK.foreground
DEFAULT_BG = _DARK.background
CURSOR_COLOR = _DARK.cursor
SELECTION_BG = _DARK.selection


def resolve_color(name: str, default: str) -> str:
    if not name or name == "default":
        return default
    if name in PALETTE:
        return PALETTE[name]
    if len(name) == 6:
        try:
            int(name, 16)
            return "#" + name
        except ValueError:
            pass
    return default


class TerminalScreen(pyte.Screen):
    """pyte screen with scrollback, the alternate screen buffer, and replies
    to device queries sent back to the shell."""

    def __init__(self, columns: int, lines: int, history: int = 10000,
                 reply: Callable[[str], None] | None = None,
                 on_title: Callable[[str], None] | None = None):
        self.scrollback: deque = deque(maxlen=history)
        self.alt_active = False
        self._saved_main = None
        self._reply = reply
        self._on_title = on_title
        super().__init__(columns, lines)

    # Palette colors 0 to 15 picked with the 256-color codes (38;5;n and 48;5;n), as
    # prompt themes such as Powerlevel10k do, are the scheme's 16 colors. pyte turns
    # them into fixed xterm values instead, so they're mapped back to color names
    # here. This also fixes pyte's misspelled bright magenta background (105).
    _INDEX_NAMES = ("black", "red", "green", "brown", "blue", "magenta", "cyan", "white")

    def select_graphic_rendition(self, *attrs) -> None:
        super().select_graphic_rendition(*attrs)
        fg = bg = None                       # the name to use, if the last change was ours
        i, n = 0, len(attrs)
        while i < n:
            a = attrs[i]
            if a in (38, 48) and i + 1 < n and attrs[i + 1] == 5 and i + 2 < n:
                idx = attrs[i + 2]
                name = None
                if 0 <= idx < 16:
                    name = ("bright" if idx >= 8 else "") + self._INDEX_NAMES[idx % 8]
                if a == 38:
                    fg = name
                else:
                    bg = name
                i += 3
                continue
            if a in (38, 48) and i + 1 < n and attrs[i + 1] == 2:
                if a == 38:
                    fg = None
                else:
                    bg = None
                i += 5
                continue
            if a == 0:
                fg = bg = None
            elif 30 <= a <= 39 or 90 <= a <= 97:
                fg = None
            elif 40 <= a <= 49:
                bg = None
            elif 100 <= a <= 107:
                bg = "bright" + self._INDEX_NAMES[a - 100]
            i += 1
        if fg or bg:
            changes = {}
            if fg:
                changes["fg"] = fg
            if bg:
                changes["bg"] = bg
            self.cursor.attrs = self.cursor.attrs._replace(**changes)

    # Replies to DSR / DA queries go back to the program.
    def write_process_input(self, data: str) -> None:
        if self._reply:
            self._reply(data)

    def set_title(self, param: str) -> None:
        super().set_title(param)
        if self._on_title:
            self._on_title(self.title)

    def bell(self, *args) -> None:
        pass

    # Scrollback: keep lines that scroll off the top of the main screen.
    def index(self) -> None:
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if (not self.alt_active and top == 0 and self.cursor.y == bottom):
            self.scrollback.append(self.buffer[top])
        super().index()

    # Alternate screen (vim, less, the AWS CLI pager).
    def set_mode(self, *modes, **kwargs) -> None:
        if kwargs.get("private") and any(m in ALT_SCREEN_MODES for m in modes):
            self._enter_alt(save_cursor=1049 in modes)
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes, **kwargs) -> None:
        if kwargs.get("private") and any(m in ALT_SCREEN_MODES for m in modes):
            self._leave_alt(restore_cursor=1049 in modes)
        super().reset_mode(*modes, **kwargs)

    def _enter_alt(self, save_cursor: bool) -> None:
        if self.alt_active:
            return
        self._saved_main = (dict(self.buffer), copy(self.cursor) if save_cursor else None)
        self.alt_active = True
        self.buffer.clear()
        self.dirty.update(range(self.lines))

    def _leave_alt(self, restore_cursor: bool) -> None:
        if not self.alt_active:
            return
        saved_buffer, saved_cursor = self._saved_main
        self.buffer.clear()
        self.buffer.update(saved_buffer)
        if restore_cursor and saved_cursor is not None:
            self.cursor = saved_cursor
        self.alt_active = False
        self._saved_main = None
        self.dirty.update(range(self.lines))

    def resize(self, lines: int | None = None, columns: int | None = None) -> None:
        lines = lines or self.lines
        columns = columns or self.columns
        if lines < self.lines:
            # pyte drops lines from the top unconditionally. Instead, drop only
            # what is needed to keep the cursor on screen, and move those lines
            # into scrollback so shrinking the window loses nothing.
            drop = max(0, self.cursor.y + 1 - lines)
            if drop and not self.alt_active:
                for y in range(drop):
                    self.scrollback.append(self.buffer[y])
            kept = {y - drop: self.buffer[y] for y in range(drop, drop + lines)
                    if y in self.buffer}
            self.buffer.clear()
            self.buffer.update(kept)
            self.cursor.y -= drop
            self.lines = lines
            self.dirty.update(range(lines))
        old_columns = self.columns
        super().resize(lines, columns)
        if self.columns > old_columns:
            # pyte only sets default tab stops when the screen is created, so a
            # wider screen would have none past the old width.
            start = ((old_columns - 1) // 8 + 1) * 8
            self.tabstops.update(range(start, self.columns, 8))
        self.cursor.y = min(self.cursor.y, self.lines - 1)
        self.cursor.x = min(self.cursor.x, self.columns - 1)

    def erase_in_display(self, how: int = 0, *args, **kwargs) -> None:
        if how == 3:  # "clear" also asks for the scrollback to be erased
            self.scrollback.clear()
        super().erase_in_display(how, *args, **kwargs)

    @property
    def app_cursor_keys(self) -> bool:
        return DECCKM in self.mode

    @property
    def bracketed_paste(self) -> bool:
        return BRACKETED_PASTE in self.mode

    def line_text(self, line, columns: int | None = None) -> str:
        cols = columns or self.columns
        return "".join(line[x].data for x in range(cols)).rstrip()

    def all_lines(self) -> list:
        """Scrollback lines followed by the visible screen lines."""
        return list(self.scrollback) + [self.buffer[y] for y in range(self.lines)]


# --------------------------------------------------------------------------
# Keyboard
# --------------------------------------------------------------------------

# Key names are Qt-independent so this can be tested without a display.
_CURSOR = {"up": "A", "down": "B", "right": "C", "left": "D", "home": "H", "end": "F"}
_TILDE = {"insert": 2, "delete": 3, "pageup": 5, "pagedown": 6,
          "f5": 15, "f6": 17, "f7": 18, "f8": 19, "f9": 20, "f10": 21, "f11": 23, "f12": 24}
_SS3_F = {"f1": "P", "f2": "Q", "f3": "R", "f4": "S"}


def encode_key(key: str, text: str = "", shift: bool = False, ctrl: bool = False,
               alt: bool = False, app_cursor: bool = False) -> str:
    """Return the bytes a VT/xterm terminal sends for a key press.

    ``key`` is a lowercase name ("up", "f5", "a", "enter"...); ``text`` is the
    text the keyboard produced, used for ordinary characters.
    """
    mod = 1 + (1 if shift else 0) + (2 if alt else 0) + (4 if ctrl else 0)

    if key in ("enter", "return"):
        return "\x1b\r" if alt else "\r"
    if key == "backspace":
        base = "\x08" if ctrl else "\x7f"
        return "\x1b" + base if alt else base
    if key == "tab":
        return "\x1b[Z" if shift else "\t"
    if key == "backtab":
        return "\x1b[Z"
    if key == "escape":
        return "\x1b"
    if key in _CURSOR:
        final = _CURSOR[key]
        if mod > 1:
            return f"\x1b[1;{mod}{final}"
        return ("\x1bO" if app_cursor else "\x1b[") + final
    if key in _TILDE:
        n = _TILDE[key]
        return f"\x1b[{n};{mod}~" if mod > 1 else f"\x1b[{n}~"
    if key in _SS3_F:
        final = _SS3_F[key]
        return f"\x1b[1;{mod}{final}" if mod > 1 else "\x1bO" + final

    if ctrl and len(key) == 1:
        c = key.lower()
        if "a" <= c <= "z":
            out = chr(ord(c) - ord("a") + 1)
        else:
            out = {" ": "\x00", "@": "\x00", "2": "\x00", "[": "\x1b", "3": "\x1b",
                   "\\": "\x1c", "4": "\x1c", "]": "\x1d", "5": "\x1d", "^": "\x1e",
                   "6": "\x1e", "_": "\x1f", "-": "\x1f", "/": "\x1f", "7": "\x1f",
                   "8": "\x7f", "?": "\x7f"}.get(c, text)
        return ("\x1b" + out) if (alt and out) else out
    if text:
        return ("\x1b" + text) if alt else text
    return ""


# --------------------------------------------------------------------------
# Paste
# --------------------------------------------------------------------------

def paste_line_count(text: str) -> int:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return normalized.count("\n") + 1 if normalized else 0


def sanitize_paste(text: str, bracketed: bool) -> str:
    """Prepare clipboard text for the terminal.

    Line endings become CR (what the Enter key sends). Every other control
    character, including ESC, is removed, so a paste can never inject escape
    sequences or end a bracketed paste early.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "".join(ch for ch in text
                      if ch in "\n\t" or not (ord(ch) < 32 or 0x7f <= ord(ch) < 0xa0))
    cleaned = cleaned.replace("\n", "\r")
    if bracketed:
        return "\x1b[200~" + cleaned + "\x1b[201~"
    return cleaned
