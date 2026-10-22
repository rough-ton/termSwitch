"""Commands pane: browse AWS CLI commands, modeled on PowerShell ISE's
Commands view. Docked to the right of the terminal tabs; the X collapses it to
a thin tab on the window's right edge."""

from __future__ import annotations

import threading

from PySide6.QtCore import (QAbstractListModel, QEvent, QModelIndex, QObject, QRunnable, QSize,
                            QThreadPool, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QKeySequence, QPainter, QPalette,
                           QShortcut)
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListView, QMenu, QMessageBox,
                               QPushButton, QSizePolicy, QSplitter, QToolButton, QVBoxLayout,
                               QWidget)

from .aws_commands import Catalog, Command, load_catalog
from .aws_params import load_spec
from .favorites import DEFAULT_FAVORITES, Favorites, key_for
from .icons import service_icon, star_icon
from .param_form import ParameterForm

DOCS_URL = "https://awscli.amazonaws.com/v2/documentation/api/latest/reference/{svc}/{op}.html"
ALL = "__all__"
FAVORITES = "__favorites__"
FAVORITES_HINT = "Star a command to add it here."


# --------------------------------------------------------------------------
# Loading in the background
# --------------------------------------------------------------------------

class _LoaderSignals(QObject):
    progress = Signal(int, int)
    done = Signal(object)


class _Cancelled(Exception):
    pass


class _LoadJob(QRunnable):
    def __init__(self, force: bool, signals: _LoaderSignals, cancel: threading.Event):
        super().__init__()
        self.force, self.signals, self.cancel = force, signals, cancel

    def _progress(self, i: int, n: int):
        if self.cancel.is_set():
            raise _Cancelled()        # the pane is closing: stop between models
        self.signals.progress.emit(i, n)

    def run(self):
        try:
            cat = load_catalog(force=self.force, progress=self._progress)
        except _Cancelled:
            return
        except Exception as exc:     # never let a bad model crash the app
            cat = Catalog(notice=f"Could not read AWS CLI commands: {exc}")
        if not self.cancel.is_set():
            self.signals.done.emit(cat)


# --------------------------------------------------------------------------
# List model with ISE-style letter headers
# --------------------------------------------------------------------------

class CommandListModel(QAbstractListModel):
    HEADER, COMMAND, HINT = 0, 1, 2

    def __init__(self, parent=None):
        super().__init__(parent)
        # (kind, text, command, is_favorite, is_missing)
        self._rows: list[tuple[int, str, Command | None, bool, bool]] = []
        self.missing_tip = ""

    def set_commands(self, commands: list[Command], show_service: bool,
                     favorites: frozenset[str] = frozenset(),
                     favorite_section: list[Command] | None = None,
                     missing: frozenset[str] = frozenset(), hint: str = ""):
        """Fill the list.

        - ``commands`` get A-Z letter headers.
        - ``favorite_section``, when given, is listed first under a "Favorites" header.
        - ``favorites`` marks rows with a star; ``missing`` grays rows whose command
          is not in the installed CLI.
        - ``hint`` is shown as a single non-selectable row when there is nothing else.
        """
        def row(cmd, text):
            key = f"{cmd.service} {cmd.name}"
            return (self.COMMAND, text, cmd, key in favorites, key in missing)

        rows = []
        if favorite_section:
            rows.append((self.HEADER, "Favorites:", None, False, False))
            rows += [row(c, f"{c.service} {c.name}") for c in favorite_section]
        letter = None
        for cmd in commands:
            text = f"{cmd.service} {cmd.name}" if show_service else cmd.name
            first = text[:1].upper()
            if first != letter:
                letter = first
                rows.append((self.HEADER, f"{first}:", None, False, False))
            rows.append(row(cmd, text))
        if not rows and hint:
            rows.append((self.HINT, hint, None, False, False))
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def command_at(self, row: int) -> Command | None:
        if 0 <= row < len(self._rows):
            return self._rows[row][2]
        return None

    def is_missing(self, row: int) -> bool:
        return 0 <= row < len(self._rows) and self._rows[row][4]

    def first_command_row(self) -> int:
        for i, r in enumerate(self._rows):
            if r[0] == self.COMMAND:
                return i
        return -1

    def row_of(self, key: str) -> int:
        for i, (kind, _t, cmd, _f, _m) in enumerate(self._rows):
            if kind == self.COMMAND and f"{cmd.service} {cmd.name}" == key:
                return i
        return -1

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        kind, text, cmd, fav, missing = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return text
        if role == Qt.ItemDataRole.DecorationRole and kind == self.COMMAND:
            return star_icon(True) if fav else star_icon(False, empty=True)
        if role == Qt.ItemDataRole.ToolTipRole and cmd:
            if missing:
                return f"{cmd.cli}\n\n{self.missing_tip}"
            return f"{cmd.cli}\n\n{cmd.doc}" if cmd.doc else cmd.cli
        if role == Qt.ItemDataRole.FontRole and kind == self.HEADER:
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ItemDataRole.FontRole and kind == self.HINT:
            f = QFont()
            f.setItalic(True)
            return f
        if role == Qt.ItemDataRole.ForegroundRole and (kind != self.COMMAND or missing):
            return QColor("#8a8f98")
        return None

    def flags(self, index):
        if index.isValid() and self._rows[index.row()][0] != self.COMMAND:
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


# --------------------------------------------------------------------------
# Pane
# --------------------------------------------------------------------------

class CommandsPane(QWidget):
    close_requested = Signal()
    insert_requested = Signal(str, bool, object)   # command line, run, Command
    open_url_requested = Signal(str)
    command_selected = Signal(object)              # Command or None

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.catalog: Catalog | None = None
        self._by_key: dict[str, Command] = {}
        self._quiet = False          # set while the list is rebuilt around a kept selection
        self._terminal_available = False
        self.favorites = Favorites(settings, self, defaults=DEFAULT_FAVORITES)
        self.favorites.changed.connect(self._favorites_changed)
        self.setMinimumWidth(240)

        # Header: "Commands" with an X, like the ISE tab.
        header = QWidget()
        header.setObjectName("commandsHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(8, 4, 4, 4)
        title = QLabel("Commands")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        close = QToolButton()
        close.setText("✕")
        close.setAutoRaise(True)
        close.setToolTip("Collapse to the right (Ctrl+Shift+K)")
        close.clicked.connect(self.close_requested)
        hl.addWidget(title)
        hl.addStretch(1)
        hl.addWidget(close)

        self.service = QComboBox()
        self.service.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.service.setMinimumContentsLength(12)
        self.service.currentIndexChanged.connect(self._refilter)
        self.refresh = QPushButton("Refresh")
        self.refresh.setToolTip("Rescan the installed AWS CLI (for example after an upgrade)")
        self.refresh.clicked.connect(lambda: self.load(force=True))
        svc_row = QHBoxLayout()
        svc_row.addWidget(self.service, 1)
        svc_row.addWidget(self.refresh)

        self.name = QLineEdit()
        self.name.setPlaceholderText("describe-inst, s3 cp ...")
        self.name.setClearButtonEnabled(True)
        self._filter_timer = QTimer(self, singleShot=True, interval=120)
        self._filter_timer.timeout.connect(self._refilter)
        self.name.textChanged.connect(lambda _t: self._filter_timer.start())
        self.name.returnPressed.connect(self._select_first)

        form = QFormLayout()
        form.setContentsMargins(8, 4, 8, 0)
        form.addRow("Service:", svc_row)
        form.addRow("Name:", self.name)

        self.model = CommandListModel(self)
        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setUniformItemSizes(True)
        side = max(10, self.list.fontMetrics().height() - 4)
        self.list.setIconSize(QSize(side, side))
        self.list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.list.selectionModel().currentChanged.connect(lambda cur, _p: self._on_current(cur))
        # Insert on double-click or Enter. Not `activated`: it also fires on a
        # double-click (and on a single click on some Linux desktops), which inserted
        # the command twice.
        self.list.doubleClicked.connect(lambda idx: self._insert_index(idx))
        # Enter is handled on the list itself rather than with a QShortcut, which
        # only fires while the window is active.
        self.list.installEventFilter(self)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)

        self.status = QLabel("Loading AWS CLI commands...")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #8a8f98;")

        # Parameter form for the selected command (Show-Command style).
        self.form = ParameterForm()
        self.form.insert_requested.connect(self.insert_requested)
        self.form.copy_requested.connect(lambda text: QGuiApplication.clipboard().setText(text))
        self.form.docs_requested.connect(self._docs_for)
        self.form.favorite_toggled.connect(self._toggle_command)
        # Ctrl+D (Cmd+D on macOS) toggles the selected command. The shortcut only
        # applies inside this pane, so Ctrl+D still reaches the shell in a terminal.
        self.fav_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        self.fav_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.fav_shortcut.activated.connect(self.toggle_selected_favorite)
        self.btn_run = self.form.btn_run
        self.btn_insert = self.form.btn_insert
        self.btn_copy = self.form.btn_copy
        self.btn_docs = self.form.btn_docs

        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)
        tl.addLayout(form)
        tl.addWidget(self.list, 1)
        status_box = QHBoxLayout()
        status_box.setContentsMargins(8, 0, 8, 0)
        status_box.addWidget(self.status)
        tl.addLayout(status_box)

        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.addWidget(top)
        self.split.addWidget(self.form)
        self.split.setStretchFactor(0, 2)
        self.split.setStretchFactor(1, 3)
        self.split.setChildrenCollapsible(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(4)
        lay.addWidget(header)
        lay.addWidget(self.split, 1)

        self._signals = _LoaderSignals()
        self._signals.progress.connect(self._on_progress)
        self._signals.done.connect(self._on_loaded)
        self._loading = False
        self._cancel = threading.Event()
        self._on_current(QModelIndex())

    # ---- loading ---------------------------------------------------------

    def load(self, force: bool = False):
        if self._loading:
            return
        self._loading = True
        self.refresh.setEnabled(False)
        self.status.setText("Reading the installed AWS CLI...")
        self.status.show()
        QThreadPool.globalInstance().start(_LoadJob(force, self._signals, self._cancel))

    def shutdown(self):
        """Stop a running catalog load; called when the window closes."""
        self._cancel.set()

    def _on_progress(self, i: int, n: int):
        if i % 25 == 0 or i == n:
            self.status.setText(f"Reading AWS CLI service models... {i}/{n}")

    def _on_loaded(self, cat: Catalog):
        self._loading = False
        self.refresh.setEnabled(True)
        self.catalog = cat
        self._by_key = {key_for(c.service, c.name): c
                        for s in cat.services.values() for c in s.commands}
        self.model.missing_tip = (f"Not available in AWS CLI {cat.cli_version}"
                                  if cat.cli_version else "Not available in the installed AWS CLI")
        current = self.service.currentData()
        self.service.blockSignals(True)
        self.service.clear()
        self.service.addItem(f"All ({len(cat.services)} services)", ALL)
        self.service.addItem(self._favorites_label(), FAVORITES)
        self.service.setItemData(1, star_icon(True), Qt.ItemDataRole.DecorationRole)
        for svc in sorted(cat.services.values(), key=lambda s: s.name):
            label = svc.name if svc.title == svc.name else f"{svc.name} ({svc.title})"
            self.service.addItem(label, svc.name)
            self.service.setItemData(self.service.count() - 1, svc.title,
                                     Qt.ItemDataRole.ToolTipRole)
            svc_icon = service_icon(svc.name)
            if svc_icon is not None:
                self.service.setItemIcon(self.service.count() - 1, svc_icon)
        idx = self.service.findData(current) if current else 0
        self.service.setCurrentIndex(max(idx, 0))
        self.service.blockSignals(False)
        version = f"AWS CLI {cat.cli_version}: " if cat.cli_version else ""
        summary = f"{version}{cat.command_count:,} commands"
        self.status.setText(cat.notice + ("\n" if cat.notice else "") + summary)
        self._refilter()

    # ---- filtering -------------------------------------------------------

    def _favorites_label(self) -> str:
        return f"Favorites ({len(self.favorites)})"

    def _favorite_commands(self) -> list[Command]:
        """Favorites as commands, sorted by service and name. Favorites missing from
        the installed CLI are included as placeholders so they can still be removed."""
        cmds = []
        for key in self.favorites.items():
            cmd = self._by_key.get(key)
            if cmd is None:
                service, _, name = key.partition(" ")
                cmd = Command(service=service, name=name)
            cmds.append(cmd)
        return sorted(cmds, key=lambda c: (c.service, c.name))

    def _refilter(self, keep: str | None = None):
        """Rebuild the list. ``keep`` re-selects that command afterwards without
        resetting the parameter form, so starring doesn't lose typed values."""
        if not self.catalog:
            return
        svc_name = self.service.currentData()
        needle = " ".join(self.name.text().lower().split())
        favs = frozenset(self.favorites.items())
        missing = frozenset(k for k in favs if k not in self._by_key)
        match = lambda c: not needle or needle in f"{c.service} {c.name}"  # noqa: E731
        section, hint = None, ""
        if svc_name == FAVORITES:
            cmds = [c for c in self._favorite_commands() if match(c)]
            hint = FAVORITES_HINT if not favs else ""
            show_service = True
        else:
            if svc_name and svc_name != ALL:
                services = ([self.catalog.services[svc_name]]
                            if svc_name in self.catalog.services else [])
            else:
                services = sorted(self.catalog.services.values(), key=lambda s: s.name)
                if not needle and favs:
                    section = self._favorite_commands()
            cmds = [c for s in services for c in s.commands if match(c)]
            show_service = svc_name == ALL or not svc_name
        self._quiet = True
        try:
            self.model.set_commands(cmds, show_service=show_service, favorites=favs,
                                    favorite_section=section, missing=missing, hint=hint)
            row = self.model.row_of(keep) if keep else -1
            if row >= 0:
                self.list.setCurrentIndex(self.model.index(row))
                self.list.scrollTo(self.model.index(row))
        finally:
            self._quiet = False
        if row < 0:
            self._on_current(QModelIndex())
        else:
            self.form.set_favorite(keep in favs)

    def _select_first(self):
        row = self.model.first_command_row()
        if row >= 0:
            idx = self.model.index(row)
            self.list.setCurrentIndex(idx)
            self.list.setFocus()

    # ---- selection and actions ------------------------------------------

    def selected_command(self) -> Command | None:
        return self.model.command_at(self.list.currentIndex().row())

    def _on_current(self, index):
        if self._quiet:
            return
        cmd = self.model.command_at(index.row()) if index.isValid() else None
        if cmd and self.model.is_missing(index.row()):
            cmd = None   # a favorite the installed CLI no longer has: nothing to show
        spec = None
        if cmd and self.catalog:
            svc = self.catalog.services.get(cmd.service)
            spec = load_spec(cmd, svc.model_dir if svc else "", self.catalog.data_dir)
        self.form.set_spec(spec)
        if cmd:
            self.form.set_favorite(key_for(cmd.service, cmd.name) in self.favorites)
        self.command_selected.emit(cmd)

    def set_target(self, shell: str, region_hint: str, terminal_available: bool):
        self.form.set_target(shell, region_hint, terminal_available)

    def set_terminal_available(self, available: bool):
        self.form.set_target(self.form.shell, self.form.region_hint, available)

    def eventFilter(self, obj, event):
        if (obj is self.list and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and not event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier):
            self._insert_index(self.list.currentIndex())
            return True
        return super().eventFilter(obj, event)

    def _insert_index(self, index):
        """Double-click or Enter: insert the bare command, ready for options."""
        if not index.isValid() or self.model.is_missing(index.row()):
            return
        cmd = self.model.command_at(index.row())
        if cmd:
            self.insert_requested.emit(cmd.cli + " ", False, cmd)

    # ---- favorites -------------------------------------------------------

    def _toggle_command(self, cmd: Command | None):
        if cmd is not None:
            self.favorites.toggle(key_for(cmd.service, cmd.name))

    def toggle_selected_favorite(self):
        cmd = self.selected_command()
        if cmd is None and self.form.spec is not None:
            cmd = self.form.spec.command
        self._toggle_command(cmd)

    def _favorites_changed(self):
        idx = self.service.findData(FAVORITES)
        if idx >= 0:
            self.service.setItemText(idx, self._favorites_label())
        cmd = self.selected_command()
        self._refilter(keep=key_for(cmd.service, cmd.name) if cmd else None)

    def _list_menu(self, pos):
        menu = QMenu(self)
        index = self.list.indexAt(pos)
        cmd = self.model.command_at(index.row()) if index.isValid() else None
        if cmd is not None:
            key = key_for(cmd.service, cmd.name)
            if key in self.favorites:
                menu.addAction("Remove from favorites", lambda: self.favorites.remove(key))
            else:
                menu.addAction("Add to favorites", lambda: self.favorites.add(key))
        if self.service.currentData() == FAVORITES:
            if not menu.isEmpty():
                menu.addSeparator()
            export = menu.addAction("Export favorites...", self.export_favorites)
            export.setEnabled(len(self.favorites) > 0)
            menu.addAction("Import favorites...", self.import_favorites)
            missing = self.favorites.missing_defaults()
            restore = menu.addAction(
                f"Restore default favorites ({missing})" if missing
                else "Restore default favorites", self.restore_default_favorites)
            restore.setEnabled(missing > 0)
            restore.setToolTip("Add back the starter favorites you removed. Your own "
                               "favorites are kept.")
        if not menu.isEmpty():
            menu.exec(self.list.viewport().mapToGlobal(pos))

    def export_favorites(self, path: str | None = None):
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export favorites", "termswitch-favorites.json", "JSON files (*.json)")
        if not path:
            return
        try:
            n = self.favorites.export_file(path)
        except OSError as exc:
            QMessageBox.warning(self, "Export favorites", f"Could not save the file:\n{exc}")
            return
        self._flash(f"Exported {n} favorite(s).")

    def import_favorites(self, path: str | None = None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Import favorites", "", "JSON files (*.json);;All files (*)")
        if not path:
            return
        try:
            added, rejected = self.favorites.import_file(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import favorites", f"Could not import the file:\n{exc}")
            return
        msg = f"Imported {added} favorite(s)."
        if rejected:
            msg += f" Skipped {rejected} invalid entr{'y' if rejected == 1 else 'ies'}."
        self._flash(msg)

    def restore_default_favorites(self):
        added = self.favorites.add_defaults()
        self._flash(f"Added {added} default favorite(s)." if added
                    else "All default favorites are already here.")

    def _flash(self, message: str, ms: int = 6000):
        """Show a message in the status line, then restore the catalog summary."""
        summary = self.status.text()
        self.status.setText(message)
        QTimer.singleShot(ms, lambda: self.status.text() == message
                          and self.status.setText(summary))

    def _docs_for(self, cmd: Command):
        self.open_url_requested.emit(DOCS_URL.format(svc=cmd.service, op=cmd.name))

    def focus_filter(self):
        self.name.setFocus()
        self.name.selectAll()


def _esc(text: str) -> str:
    import html
    return html.escape(text)


# --------------------------------------------------------------------------
# Collapsed state: a thin vertical tab on the right edge
# --------------------------------------------------------------------------

class VerticalTab(QAbstractButton):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Show {text} (Ctrl+Shift+K)")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(fm.height() + 10, fm.horizontalAdvance(self.text()) + 24)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _e):
        p = QPainter(self)
        pal = self.palette()
        bg = pal.color(QPalette.ColorRole.Button)
        if self.underMouse():
            bg = pal.color(QPalette.ColorRole.Midlight)
        p.fillRect(self.rect(), bg)
        p.setPen(pal.color(QPalette.ColorRole.Mid))
        p.drawLine(0, 0, 0, self.height())
        p.setPen(pal.color(QPalette.ColorRole.ButtonText))
        # After a 90-degree rotation the glyphs extend toward +x from the baseline.
        fm = self.fontMetrics()
        p.translate((self.width() - fm.ascent() + fm.descent()) / 2, 12)
        p.rotate(90)
        p.drawText(0, 0, self.text())
        p.end()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)
