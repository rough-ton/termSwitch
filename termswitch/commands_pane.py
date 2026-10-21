"""Commands pane: browse AWS CLI commands, modeled on PowerShell ISE's
Commands view. Docked to the right of the terminal tabs; the X collapses it to
a thin tab on the window's right edge."""

from __future__ import annotations

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QObject, QRunnable, QSize,
                            QThreadPool, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPalette
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListView, QPushButton, QSizePolicy, QSplitter,
                               QToolButton, QVBoxLayout, QWidget)

from .aws_commands import Catalog, Command, load_catalog
from .aws_params import load_spec
from .param_form import ParameterForm

DOCS_URL = "https://awscli.amazonaws.com/v2/documentation/api/latest/reference/{svc}/{op}.html"
ALL = "__all__"


# --------------------------------------------------------------------------
# Loading in the background
# --------------------------------------------------------------------------

class _LoaderSignals(QObject):
    progress = Signal(int, int)
    done = Signal(object)


class _LoadJob(QRunnable):
    def __init__(self, force: bool, signals: _LoaderSignals):
        super().__init__()
        self.force, self.signals = force, signals

    def run(self):
        try:
            cat = load_catalog(force=self.force,
                               progress=lambda i, n: self.signals.progress.emit(i, n))
        except Exception as exc:     # never let a bad model crash the app
            cat = Catalog(notice=f"Could not read AWS CLI commands: {exc}")
        self.signals.done.emit(cat)


# --------------------------------------------------------------------------
# List model with ISE-style letter headers
# --------------------------------------------------------------------------

class CommandListModel(QAbstractListModel):
    HEADER, COMMAND = 0, 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[int, str, Command | None]] = []

    def set_commands(self, commands: list[Command], show_service: bool):
        rows, letter = [], None
        for cmd in commands:
            text = f"{cmd.service} {cmd.name}" if show_service else cmd.name
            first = text[:1].upper()
            if first != letter:
                letter = first
                rows.append((self.HEADER, f"{first}:", None))
            rows.append((self.COMMAND, text, cmd))
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def command_at(self, row: int) -> Command | None:
        if 0 <= row < len(self._rows):
            return self._rows[row][2]
        return None

    def first_command_row(self) -> int:
        for i, (kind, _t, _c) in enumerate(self._rows):
            if kind == self.COMMAND:
                return i
        return -1

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        kind, text, cmd = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return text
        if role == Qt.ItemDataRole.ToolTipRole and cmd:
            return f"{cmd.cli}\n\n{cmd.doc}" if cmd.doc else cmd.cli
        if role == Qt.ItemDataRole.FontRole and kind == self.HEADER:
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ItemDataRole.ForegroundRole and kind == self.HEADER:
            return QColor("#8a8f98")
        return None

    def flags(self, index):
        if index.isValid() and self._rows[index.row()][0] == self.HEADER:
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.catalog: Catalog | None = None
        self._terminal_available = False
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
        self.list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.list.selectionModel().currentChanged.connect(lambda cur, _p: self._on_current(cur))
        self.list.doubleClicked.connect(lambda idx: self._insert_index(idx))
        self.list.activated.connect(lambda idx: self._insert_index(idx))

        self.status = QLabel("Loading AWS CLI commands...")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #8a8f98;")

        # Parameter form for the selected command (Show-Command style).
        self.form = ParameterForm()
        self.form.insert_requested.connect(self.insert_requested)
        self.form.copy_requested.connect(lambda text: QGuiApplication.clipboard().setText(text))
        self.form.docs_requested.connect(self._docs_for)
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
        self._on_current(QModelIndex())

    # ---- loading ---------------------------------------------------------

    def load(self, force: bool = False):
        if self._loading:
            return
        self._loading = True
        self.refresh.setEnabled(False)
        self.status.setText("Reading the installed AWS CLI...")
        self.status.show()
        QThreadPool.globalInstance().start(_LoadJob(force, self._signals))

    def _on_progress(self, i: int, n: int):
        if i % 25 == 0 or i == n:
            self.status.setText(f"Reading AWS CLI service models... {i}/{n}")

    def _on_loaded(self, cat: Catalog):
        self._loading = False
        self.refresh.setEnabled(True)
        self.catalog = cat
        current = self.service.currentData()
        self.service.blockSignals(True)
        self.service.clear()
        self.service.addItem(f"All ({len(cat.services)} services)", ALL)
        for svc in sorted(cat.services.values(), key=lambda s: s.name):
            label = svc.name if svc.title == svc.name else f"{svc.name} ({svc.title})"
            self.service.addItem(label, svc.name)
            self.service.setItemData(self.service.count() - 1, svc.title,
                                     Qt.ItemDataRole.ToolTipRole)
        idx = self.service.findData(current) if current else 0
        self.service.setCurrentIndex(max(idx, 0))
        self.service.blockSignals(False)
        version = f"AWS CLI {cat.cli_version}: " if cat.cli_version else ""
        summary = f"{version}{cat.command_count:,} commands"
        self.status.setText(cat.notice + ("\n" if cat.notice else "") + summary)
        self._refilter()

    # ---- filtering -------------------------------------------------------

    def _refilter(self):
        if not self.catalog:
            return
        svc_name = self.service.currentData()
        needle = " ".join(self.name.text().lower().split())
        if svc_name and svc_name != ALL:
            services = [self.catalog.services[svc_name]] if svc_name in self.catalog.services else []
        else:
            services = sorted(self.catalog.services.values(), key=lambda s: s.name)
        cmds = [c for s in services for c in s.commands
                if not needle or needle in f"{c.service} {c.name}"]
        self.model.set_commands(cmds, show_service=(svc_name == ALL or not svc_name))
        self._on_current(QModelIndex())

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
        cmd = self.model.command_at(index.row()) if index.isValid() else None
        spec = None
        if cmd and self.catalog:
            svc = self.catalog.services.get(cmd.service)
            spec = load_spec(cmd, svc.model_dir if svc else "", self.catalog.data_dir)
        self.form.set_spec(spec)
        self.command_selected.emit(cmd)

    def set_target(self, shell: str, region_hint: str, terminal_available: bool):
        self.form.set_target(shell, region_hint, terminal_available)

    def set_terminal_available(self, available: bool):
        self.form.set_target(self.form.shell, self.form.region_hint, available)

    def _insert_index(self, index):
        """Double-click: insert the bare command, ready for options."""
        cmd = self.model.command_at(index.row())
        if cmd:
            self.insert_requested.emit(cmd.cli + " ", False, cmd)

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
