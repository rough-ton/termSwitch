"""Main window: grouped profile tree on the left, details on the right."""

from __future__ import annotations

import html
import re
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import (QFileSystemWatcher, QItemSelectionModel, QSettings, Qt, QTimer,
                            QUrl, Signal)
from PySide6.QtGui import (QAction, QBrush, QColor, QDesktopServices, QFont, QIcon,
                           QKeySequence, QPainter, QPixmap, QShortcut, QGuiApplication)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QColorDialog,
                               QComboBox, QDialog, QDialogButtonBox, QFontComboBox,
                               QFormLayout, QSpinBox,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
                               QMenu, QMessageBox, QPushButton, QSplitter, QStyle,
                               QTextBrowser, QToolBar, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from . import __version__
from .config_store import (SEP, ConfigError, Profile, ProfileStore, is_within, join_group,
                           leaf_name, natural_key, parent_group, safe_https_url)
from .launcher import (TERMINAL_CHOICES, LaunchError, LaunchOptions, find_aws, launch,
                       platform_key)
from .deps import ERROR as DEP_ERROR, WARN as DEP_WARN, summary as dep_summary, worst
from .depsui import DependencyDialog, DependencyRunner
from .legal import LicensesDialog
from .icons import AUTO_GROUP_ICONS, icon as material_icon, shell_icon
from .aws_params import is_destructive, needs_confirmation
from .commands_pane import CommandsPane, VerticalTab
from .terminal_tabs import TerminalTabs
from .terminal_widget import default_font
from .status import CHECKING, ERROR, EXPIRED, UNKNOWN, VALID, StatusChecker, StatusResult

ROLE = Qt.ItemDataRole.UserRole
KIND_GROUP, KIND_AUTO, KIND_PROFILE = "group", "auto", "profile"

STATUS_COLORS = {
    UNKNOWN: "#9aa0a6", CHECKING: "#f2b01e", VALID: "#2ea44f",
    EXPIRED: "#e8710a", ERROR: "#d93025",
}
STATUS_LABELS = {
    UNKNOWN: "Not checked", CHECKING: "Checking...", VALID: "Authenticated",
    EXPIRED: "Not logged in or expired", ERROR: "Error",
}
PROD_COLOR = "#c0392b"

AUTO_GROUP_MODES = [
    ("sso_session", "SSO session / credential type"),
    ("account", "Account ID"),
    ("type", "Credential type"),
    ("none", "Single 'Ungrouped' folder"),
]

DEFAULTS = {
    "terminal": "auto",
    "new_window": False,
    "auto_sso_login": True,
    "export_credentials": False,
    "check_on_startup": True,
    "single_click": False,
    "auto_group": "sso_session",
    "prod_pattern": r"prod",
    "confirm_prod": True,
    "check_updates_online": True,
    "show_dep_warnings": True,
    "open_mode": "embedded",
    "term_font_family": "",
    "term_font_size": 0,
    "term_scrollback": 10000,
    "confirm_close_tab": True,
    "confirm_multiline_paste": True,
}

OPEN_MODES = [("embedded", "Embedded tab in termSwitch"), ("external", "External terminal")]

KIND_LABELS = {"sso": "IAM Identity Center (SSO)", "role": "Assume role",
               "process": "credential_process", "static": "Static access keys",
               "other": "Other"}


def item_key(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def auto_group_icon_name(profile: Profile, mode: str) -> str:
    if mode == "none":
        return "folder-base"
    if mode == "account":
        return "folder-aws" if profile.account_id else "folder-base"
    if mode == "sso_session" and profile.is_sso:
        return "folder-aws"
    return AUTO_GROUP_ICONS[profile.kind]


def auto_group_label(profile: Profile, mode: str) -> str:
    if mode == "none":
        return "Ungrouped"
    if mode == "account":
        return f"Account {profile.account_id}" if profile.account_id else "No account ID"
    if mode == "sso_session" and profile.is_sso:
        if profile.sso_session:
            return f"SSO: {profile.sso_session}"
        url = profile.sso_start_url or ""
        host = re.sub(r"^https?://", "", url).split("/")[0] or "legacy"
        return f"SSO: {host}"
    return KIND_LABELS[profile.kind]


def dot_icon(color: str, ring: str | None = None) -> QIcon:
    size = 32
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if ring:
        p.setBrush(QColor(ring))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(2, 2, size - 4, size - 4)
        p.setBrush(QColor(color))
        p.drawEllipse(8, 8, size - 16, size - 16)
    else:
        p.setBrush(QColor(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(6, 6, size - 12, size - 12)
    p.end()
    return QIcon(pm)


# --------------------------------------------------------------------------
# Tree widget with drag-and-drop that maps onto config writes
# --------------------------------------------------------------------------

class ProfileTree(QTreeWidget):
    # (list of item keys, target group path or None for top level)
    move_requested = Signal(list, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setUniformRowHeights(True)
        self.setAnimated(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    # Which items are being dragged (top-most only).
    def dragged_items(self) -> list[QTreeWidgetItem]:
        selected = [i for i in self.selectedItems()
                    if i.data(0, ROLE)[0] in (KIND_GROUP, KIND_PROFILE)]
        chosen = set(id(i) for i in selected)
        result = []
        for item in selected:
            parent, covered = item.parent(), False
            while parent is not None:
                if id(parent) in chosen:
                    covered = True
                    break
                parent = parent.parent()
            if not covered:
                result.append(item)
        return result

    def _target(self, event) -> tuple[bool, str | None]:
        """Return (valid, target_group) for the current drop position."""
        item = self.itemAt(event.position().toPoint())
        indicator = self.dropIndicatorPosition()
        if item is None or indicator == QAbstractItemView.DropIndicatorPosition.OnViewport:
            target = None
        else:
            kind, value = item.data(0, ROLE)
            on_item = indicator == QAbstractItemView.DropIndicatorPosition.OnItem
            if on_item and kind == KIND_GROUP:
                target = value
            elif kind == KIND_AUTO:
                target = None  # dropping on an automatic group clears x_group
            else:
                parent = item.parent()
                # A parent that is an automatic group also means "clear x_group".
                target = parent.data(0, ROLE)[1] if (
                    parent is not None and parent.data(0, ROLE)[0] == KIND_GROUP) else None

        dragged = self.dragged_items()
        if not dragged:
            return False, None
        for d in dragged:
            kind, value = d.data(0, ROLE)
            if kind == KIND_GROUP and target and is_within(target, value):
                return False, None
        return True, target

    def dragEnterEvent(self, event):
        if event.source() is not self:
            event.ignore()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.source() is not self:
            event.ignore()
            return
        super().dragMoveEvent(event)  # updates dropIndicatorPosition()
        valid, _ = self._target(event)
        if valid:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.source() is not self:
            event.ignore()
            return
        valid, target = self._target(event)
        keys = [item_key(*i.data(0, ROLE)) for i in self.dragged_items()]
        # Never let Qt move the items itself; the tree is rebuilt from the file.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self.stopAutoScroll()
        self.setState(QAbstractItemView.State.NoState)
        self.viewport().update()
        if valid and keys:
            self.move_requested.emit(keys, target)


# --------------------------------------------------------------------------
# Settings dialog
# --------------------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        form = QFormLayout()

        self.open_mode = QComboBox()
        for value, label in OPEN_MODES:
            self.open_mode.addItem(label, value)
        self._select(self.open_mode, get_setting(settings, "open_mode"))
        self.open_mode.setToolTip("Shift+double-click a profile to use the other one.")
        form.addRow("Open profiles in:", self.open_mode)

        self.font_family = QFontComboBox()
        self.font_family.setFontFilters(QFontComboBox.FontFilter.MonospacedFonts)
        current_font = default_font(get_setting(settings, "term_font_family"),
                                    get_setting(settings, "term_font_size"))
        self.font_family.setCurrentFont(current_font)
        self.font_size = QSpinBox()
        self.font_size.setRange(6, 36)
        self.font_size.setValue(current_font.pointSize())
        font_row = QHBoxLayout()
        font_row.addWidget(self.font_family, 1)
        font_row.addWidget(self.font_size)
        form.addRow("Terminal font:", font_row)

        self.scrollback = QSpinBox()
        self.scrollback.setRange(1000, 100000)
        self.scrollback.setSingleStep(1000)
        self.scrollback.setValue(get_setting(settings, "term_scrollback"))
        form.addRow("Scrollback lines:", self.scrollback)

        self.confirm_close = QCheckBox("Ask before closing a tab that is running a command")
        self.confirm_close.setChecked(get_setting(settings, "confirm_close_tab"))
        form.addRow("", self.confirm_close)

        self.confirm_paste = QCheckBox("Ask before pasting more than one line")
        self.confirm_paste.setChecked(get_setting(settings, "confirm_multiline_paste"))
        form.addRow("", self.confirm_paste)

        self.terminal = QComboBox()
        for value, label in TERMINAL_CHOICES[platform_key()]:
            self.terminal.addItem(label, value)
        self._select(self.terminal, get_setting(settings, "terminal"))
        form.addRow("External terminal:", self.terminal)

        self.new_window = QCheckBox("Open a new window instead of a new tab")
        self.new_window.setChecked(get_setting(settings, "new_window"))
        form.addRow("", self.new_window)

        self.auto_login = QCheckBox("Run 'aws sso login' in the terminal when the token is expired")
        self.auto_login.setChecked(get_setting(settings, "auto_sso_login"))
        form.addRow("", self.auto_login)

        self.export = QCheckBox("Also export temporary credentials into the environment")
        self.export.setToolTip("Uses 'aws configure export-credentials'. Useful for tools "
                               "that do not read AWS_PROFILE.")
        self.export.setChecked(get_setting(settings, "export_credentials"))
        form.addRow("", self.export)

        self.single_click = QCheckBox("Open a profile with a single click")
        self.single_click.setChecked(get_setting(settings, "single_click"))
        form.addRow("", self.single_click)

        self.check_startup = QCheckBox("Check credential status on startup")
        self.check_startup.setChecked(get_setting(settings, "check_on_startup"))
        form.addRow("", self.check_startup)

        self.auto_group = QComboBox()
        for value, label in AUTO_GROUP_MODES:
            self.auto_group.addItem(label, value)
        self._select(self.auto_group, get_setting(settings, "auto_group"))
        form.addRow("Group untagged profiles by:", self.auto_group)

        self.prod_pattern = QLineEdit(get_setting(settings, "prod_pattern"))
        self.prod_pattern.setToolTip("Case-insensitive regex matched against the profile name. "
                                     "Matches are shown in red and get a red tab color. "
                                     "A profile's x_color overrides this.")
        form.addRow("Production pattern:", self.prod_pattern)

        self.confirm_prod = QCheckBox("Ask before opening production profiles")
        self.confirm_prod.setChecked(get_setting(settings, "confirm_prod"))
        form.addRow("", self.confirm_prod)

        self.check_online = QCheckBox("Check pypi.org and GitHub for newer versions at startup")
        self.check_online.setToolTip("Only version numbers are fetched over HTTPS. Nothing about "
                                     "you or your profiles is sent.")
        self.check_online.setChecked(get_setting(settings, "check_updates_online"))
        form.addRow("Dependencies:", self.check_online)

        self.dep_warnings = QCheckBox("Show the dependency window at startup for warnings")
        self.dep_warnings.setChecked(get_setting(settings, "show_dep_warnings"))
        form.addRow("", self.dep_warnings)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    @staticmethod
    def _select(combo: QComboBox, value) -> None:
        idx = combo.findData(value)
        combo.setCurrentIndex(max(idx, 0))

    def _save(self):
        pattern = self.prod_pattern.text().strip()
        try:
            re.compile(pattern)
        except re.error as exc:
            QMessageBox.warning(self, "Invalid pattern", f"Production pattern: {exc}")
            return
        s = self.settings
        s.setValue("terminal", self.terminal.currentData())
        s.setValue("open_mode", self.open_mode.currentData())
        s.setValue("term_font_family", self.font_family.currentFont().family())
        s.setValue("term_font_size", self.font_size.value())
        s.setValue("term_scrollback", self.scrollback.value())
        s.setValue("confirm_close_tab", self.confirm_close.isChecked())
        s.setValue("confirm_multiline_paste", self.confirm_paste.isChecked())
        s.setValue("new_window", self.new_window.isChecked())
        s.setValue("auto_sso_login", self.auto_login.isChecked())
        s.setValue("export_credentials", self.export.isChecked())
        s.setValue("single_click", self.single_click.isChecked())
        s.setValue("check_on_startup", self.check_startup.isChecked())
        s.setValue("auto_group", self.auto_group.currentData())
        s.setValue("prod_pattern", pattern)
        s.setValue("confirm_prod", self.confirm_prod.isChecked())
        s.setValue("check_updates_online", self.check_online.isChecked())
        s.setValue("show_dep_warnings", self.dep_warnings.isChecked())
        self.accept()


def get_setting(settings: QSettings, key: str):
    default = DEFAULTS[key]
    return settings.value(key, default, type=type(default))


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, store: ProfileStore | None = None, settings: QSettings | None = None):
        super().__init__()
        self.store = store or ProfileStore()
        self.settings = settings or QSettings()
        self.status: dict[str, StatusResult] = {}
        self.expanded: set[str] = set(self.settings.value("expanded", [], type=list) or [])
        self._first_build = not self.settings.contains("expanded")
        self._tracking = True
        self._icons: dict[str, QIcon] = {}

        self.setWindowTitle("termSwitch")
        self.resize(1280, 780)

        self.checker = StatusChecker(self)
        self.checker.status_ready.connect(self._on_status)
        self.checker.login_finished.connect(self._on_login_finished)

        self._build_ui()
        self._build_actions()

        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._schedule_reload)
        self.watcher.directoryChanged.connect(self._schedule_reload)
        self._reload_timer = QTimer(self, singleShot=True, interval=400)
        self._reload_timer.timeout.connect(self._reload_if_changed)

        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        for key, splitter in (("main_splitter_v2", self.splitter),
                              ("left_splitter", self.left_splitter)):
            state = self.settings.value(key)
            if state is not None:
                splitter.restoreState(state)
        self.set_commands_visible(self.settings.value("commands_visible", True, type=bool),
                                  save=False)
        QTimer.singleShot(200, self.commands.load)

        self.reload()

        self.deps = DependencyRunner(self)
        self.deps.finished.connect(self._on_deps_done)
        self._deps_startup = True
        self._dep_dialog: DependencyDialog | None = None
        self.dep_button = QPushButton("Checking dependencies...")
        self.dep_button.setFlat(True)
        self.dep_button.clicked.connect(self.show_dependency_dialog)
        self.statusBar().addPermanentWidget(self.dep_button)
        QTimer.singleShot(100, lambda: self.run_dependency_check(startup=True))

        if get_setting(self.settings, "check_on_startup"):
            QTimer.singleShot(300, self.check_all)

    # ---- layout ----------------------------------------------------------

    def _build_ui(self):
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(6, 6, 0, 6)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter profiles, accounts, roles, groups...")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        lv.addWidget(self.filter)

        self.tree = ProfileTree()
        self.tree.move_requested.connect(
            lambda keys, target: QTimer.singleShot(0, lambda: self._apply_move(keys, target)))
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.itemClicked.connect(self._on_click)
        self.tree.currentItemChanged.connect(lambda *_: self._show_details())
        self.tree.itemSelectionChanged.connect(self._show_details)
        self.tree.itemExpanded.connect(lambda i: self._track_expand(i, True))
        self.tree.itemCollapsed.connect(lambda i: self._track_expand(i, False))
        self.tree.customContextMenuRequested.connect(self._context_menu)
        lv.addWidget(self.tree)

        details_box = QWidget()
        rv = QVBoxLayout(details_box)
        rv.setContentsMargins(6, 0, 0, 6)
        self.details = QTextBrowser()
        # Links are opened by us, and only when they are plain https URLs.
        self.details.setOpenLinks(False)
        self.details.anchorClicked.connect(self._open_link)
        rv.addWidget(self.details)
        buttons = QHBoxLayout()
        self.btn_open = QPushButton("Open")
        self.btn_open.setToolTip("Open the selected profile (Shift+click: the other mode)")
        self.btn_open.clicked.connect(lambda: self.open_selected(
            invert=bool(QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)))
        self.btn_login = QPushButton("SSO login")
        self.btn_login.clicked.connect(self.login_selected)
        self.btn_check = QPushButton("Check")
        self.btn_check.clicked.connect(self.check_selected)
        for b in (self.btn_open, self.btn_login, self.btn_check):
            buttons.addWidget(b)
        buttons.addStretch(1)
        rv.addLayout(buttons)

        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.addWidget(left)
        self.left_splitter.addWidget(details_box)
        self.left_splitter.setStretchFactor(0, 3)
        self.left_splitter.setStretchFactor(1, 2)
        self.left_splitter.setChildrenCollapsible(True)

        self.terminals = TerminalTabs(self._placeholder_html())
        self.terminals.open_external_requested.connect(
            lambda prof: self.open_profiles([prof], external=True))
        self.terminals.count_changed.connect(self._on_tab_count)
        self._apply_terminal_settings()

        self.commands = CommandsPane()
        self.commands.close_requested.connect(lambda: self.set_commands_visible(False))
        self.commands.insert_requested.connect(self._insert_command)
        self.commands.open_url_requested.connect(lambda url: self._open_link(QUrl(url)))
        self.commands_tab = VerticalTab("Commands")
        self.commands_tab.clicked.connect(lambda: self.set_commands_visible(True))
        self.terminals.tabs.currentChanged.connect(lambda _i: self._update_command_target())
        self.terminals.count_changed.connect(lambda _n: self._update_command_target())
        self.terminals.terminal_state_changed.connect(self._update_command_target)

        self.splitter = QSplitter()
        self.splitter.addWidget(self.left_splitter)
        self.splitter.addWidget(self.terminals)
        self.splitter.addWidget(self.commands)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setCollapsible(1, False)
        self.splitter.setCollapsible(2, False)
        self.splitter.setSizes([300, 700, 320])

        central = QWidget()
        cl = QHBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self.splitter, 1)
        cl.addWidget(self.commands_tab)
        self.setCentralWidget(central)

        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            sc = QShortcut(QKeySequence(key), self.tree)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(self.open_selected)
        rename = QShortcut(QKeySequence(Qt.Key.Key_F2), self.tree)
        rename.setContext(Qt.ShortcutContext.WidgetShortcut)
        rename.activated.connect(self._rename_current_group)

    def _build_actions(self):
        style = self.style()
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(tb)

        def action(text, slot, icon=None, shortcut=None):
            a = QAction(text, self)
            if icon is not None:
                a.setIcon(style.standardIcon(icon))
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            return a

        self.act_open = action("Open terminal", lambda: self.open_selected(), None, "Ctrl+T")
        self.act_open.setIcon(shell_icon())
        self.act_open_ext = action("Open in external terminal",
                                   lambda: self.open_selected(invert=None, external=True),
                                   QStyle.StandardPixmap.SP_ComputerIcon, "Ctrl+Shift+T")
        self.act_close_tab = action("Close tab", self.close_current_tab, None,
                                    QKeySequence.StandardKey.Close if platform_key() == "darwin"
                                    else "Ctrl+Shift+W")
        self.act_next_tab = action("Next tab", lambda: self._cycle_tab(1), None, "Ctrl+Tab")
        self.act_prev_tab = action("Previous tab", lambda: self._cycle_tab(-1), None,
                                   "Ctrl+Shift+Tab")
        self.act_focus_tree = action("Focus profiles", lambda: self.tree.setFocus(), None,
                                     "Ctrl+1")
        self.act_focus_term = action("Focus terminal", self._focus_terminal, None, "Ctrl+2")
        self.act_commands = action("Commands", lambda: self.set_commands_visible(
            not self.commands.isVisible()), None, "Ctrl+Shift+K")
        self.act_commands.setCheckable(True)
        self.act_find_command = action("Find command...", self._find_command, None,
                                       "Ctrl+Shift+F")
        for a in (self.act_close_tab, self.act_next_tab, self.act_prev_tab,
                  self.act_focus_tree, self.act_focus_term, self.act_commands,
                  self.act_find_command):
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            self.addAction(a)
        self.act_new_group = action("New group", lambda: self.new_group(None), None,
                                    "Ctrl+Shift+N")
        self.act_new_group.setIcon(material_icon("folder-base"))
        self.act_check_all = action("Check all", self.check_all, None, "Ctrl+Shift+R")
        self.act_check_all.setIcon(material_icon("verified"))
        self.act_reload = action("Reload", self.reload,
                                 QStyle.StandardPixmap.SP_BrowserReload, QKeySequence.StandardKey.Refresh)
        self.act_settings = action("Settings", self.open_settings, None, "Ctrl+,")
        self.act_settings.setIcon(material_icon("settings"))
        for a in (self.act_open, self.act_new_group, self.act_check_all, self.act_reload,
                  self.act_settings):
            tb.addAction(a)

        menu_file = self.menuBar().addMenu("&File")
        menu_file.addAction(self.act_reload)
        menu_file.addAction(action("Edit config file", self.edit_config))
        menu_file.addAction(action("Show config folder", self.show_config_folder))
        menu_file.addSeparator()
        menu_file.addAction(self.act_settings)
        menu_file.addSeparator()
        quit_act = action("Quit", self.close, shortcut=QKeySequence.StandardKey.Quit)
        quit_act.setMenuRole(QAction.MenuRole.QuitRole)
        menu_file.addAction(quit_act)

        menu_view = self.menuBar().addMenu("&View")
        menu_view.addAction(self.act_commands)
        menu_view.addAction(self.act_find_command)
        menu_view.addSeparator()
        menu_view.addAction(self.act_close_tab)
        menu_view.addAction(self.act_next_tab)
        menu_view.addAction(self.act_prev_tab)
        menu_view.addAction(self.act_focus_tree)
        menu_view.addAction(self.act_focus_term)
        menu_view.addSeparator()
        menu_view.addAction(action("Expand all", self.tree.expandAll))
        menu_view.addAction(action("Collapse all", self.tree.collapseAll))
        menu_view.addSeparator()
        sub = menu_view.addMenu("Group untagged profiles by")
        self._auto_group_actions = []
        for value, label in AUTO_GROUP_MODES:
            a = QAction(label, self, checkable=True)
            a.setData(value)
            a.triggered.connect(lambda _=False, v=value: self._set_auto_group(v))
            sub.addAction(a)
            self._auto_group_actions.append(a)
        self._sync_auto_group_actions()

        menu_profiles = self.menuBar().addMenu("&Profiles")
        menu_profiles.addAction(self.act_open)
        menu_profiles.addAction(self.act_open_ext)
        menu_profiles.addAction(action("SSO login", self.login_selected))
        menu_profiles.addAction(action("Check selected", self.check_selected))
        menu_profiles.addAction(self.act_check_all)
        menu_profiles.addSeparator()
        menu_profiles.addAction(self.act_new_group)

        menu_help = self.menuBar().addMenu("&Help")
        menu_help.addAction(action("Check dependencies...", self.show_dependency_dialog))
        menu_help.addAction(action("Licenses...", lambda: self.show_licenses()))
        about = action("About", self.about)
        about.setMenuRole(QAction.MenuRole.AboutRole)
        menu_help.addAction(about)

    # ---- loading and tree building --------------------------------------

    def reload(self):
        try:
            self.store.load()
        except ConfigError as exc:
            QMessageBox.critical(self, "Config error", str(exc))
            return
        self._watch()
        self.rebuild()
        self.statusBar().showMessage(
            f"{len(self.store.profiles)} profiles from {self.store.config_file}", 5000)

    def _watch(self):
        paths = [str(self.store.config_file), str(self.store.config_file.parent),
                 str(self.store.credentials_file)]
        current = set(self.watcher.files()) | set(self.watcher.directories())
        for p in paths:
            if p not in current and Path(p).exists():
                self.watcher.addPath(p)

    def _schedule_reload(self, *_):
        self._reload_timer.start()

    def _reload_if_changed(self):
        self._watch()  # editors that replace the file drop it from the watcher
        if self.store.file_changed_on_disk():
            self.reload()
            self.statusBar().showMessage("Config changed on disk, reloaded.", 4000)

    def _icon(self, state: str, color: str | None = None) -> QIcon:
        key = f"{state}|{color}"
        if key not in self._icons:
            self._icons[key] = dot_icon(STATUS_COLORS[state], color)
        return self._icons[key]

    def _profile_color(self, profile: Profile) -> str | None:
        if profile.color:
            return profile.color
        pattern = get_setting(self.settings, "prod_pattern")
        if pattern:
            try:
                if re.search(pattern, profile.name, re.IGNORECASE):
                    return PROD_COLOR
            except re.error:
                pass
        return None

    def rebuild(self):
        selected = {item_key(*i.data(0, ROLE)) for i in self.tree.selectedItems()}
        current = self.tree.currentItem()
        current_key = item_key(*current.data(0, ROLE)) if current else None

        self._tracking = False
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        prod_pattern = get_setting(self.settings, "prod_pattern")

        def group_icon(path: str) -> QIcon:
            try:
                if prod_pattern and re.search(prod_pattern, leaf_name(path), re.IGNORECASE):
                    return material_icon("folder-secure")
            except re.error:
                pass
            return material_icon("folder-base")
        group_items: dict[str, QTreeWidgetItem] = {}
        drag_drop = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                     | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)

        def group_item(path: str) -> QTreeWidgetItem:
            if path in group_items:
                return group_items[path]
            parent_path = parent_group(path)
            parent = group_item(parent_path) if parent_path else self.tree.invisibleRootItem()
            it = QTreeWidgetItem(parent, [leaf_name(path)])
            it.setData(0, ROLE, (KIND_GROUP, path))
            it.setIcon(0, group_icon(path))
            it.setFlags(drag_drop)
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
            group_items[path] = it
            return it

        for g in sorted(self.store.all_groups(), key=natural_key):
            group_item(g)

        mode = get_setting(self.settings, "auto_group")
        untagged = [p for p in self.store.profiles.values() if not p.group]
        auto_items: dict[str, QTreeWidgetItem] = {}
        auto_icons = {auto_group_label(p, mode): auto_group_icon_name(p, mode) for p in untagged}
        for label in sorted(auto_icons, key=natural_key):
            it = QTreeWidgetItem(self.tree.invisibleRootItem(), [label])
            it.setData(0, ROLE, (KIND_AUTO, label))
            it.setIcon(0, material_icon(auto_icons[label]))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            f = it.font(0)
            f.setItalic(True)
            it.setFont(0, f)
            it.setForeground(0, QBrush(QColor("#8a8f98")))
            it.setToolTip(0, "Automatic group for profiles without x_group.\n"
                             "Drag a profile into a named group to assign it, or\n"
                             "back here to remove its x_group.")
            auto_items[label] = it

        for prof in sorted(self.store.profiles.values(), key=lambda p: natural_key(p.name)):
            parent = (group_items[prof.group] if prof.group
                      else auto_items[auto_group_label(prof, mode)])
            it = QTreeWidgetItem(parent, [prof.name])
            it.setData(0, ROLE, (KIND_PROFILE, prof.name))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsDragEnabled)
            self._decorate_profile(it, prof)

        # Expansion state
        for key_path, it in list(group_items.items()):
            key = item_key(KIND_GROUP, key_path)
            it.setExpanded(self._first_build or key in self.expanded)
        for label, it in auto_items.items():
            key = item_key(KIND_AUTO, label)
            it.setExpanded(self._first_build or key in self.expanded)
        if self._first_build:
            self.expanded = {item_key(*i.data(0, ROLE))
                             for i in list(group_items.values()) + list(auto_items.values())}
            self._first_build = False
            self._save_expanded()

        # Selection
        for it in self._iter_items():
            key = item_key(*it.data(0, ROLE))
            if key in selected:
                it.setSelected(True)
            if key == current_key:
                self.tree.setCurrentItem(it, 0, QItemSelectionModel.SelectionFlag.NoUpdate)

        self.tree.setUpdatesEnabled(True)
        self._tracking = True
        self._apply_filter(self.filter.text())
        self._show_details()

    def _decorate_profile(self, it: QTreeWidgetItem, prof: Profile):
        result = self.status.get(prof.name)
        state = result.state if result else UNKNOWN
        color = self._profile_color(prof)
        it.setIcon(0, self._icon(state))
        if color:
            it.setForeground(0, QBrush(QColor(color)))
        if prof.problem:
            it.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning))
            it.setText(0, repr(prof.name)[1:-1])
            it.setToolTip(0, f"{prof.problem}\nThis profile cannot be opened. Rename it in the config.")
            return
        tip = [prof.name, KIND_LABELS[prof.kind]]
        if prof.account_id:
            tip.append(f"Account: {prof.account_id}")
        if prof.role:
            tip.append(f"Role: {prof.role}")
        tip.append(f"Status: {STATUS_LABELS[state]}")
        if result and result.detail and state != VALID:
            tip.append(result.detail)
        it.setToolTip(0, "\n".join(tip))

    def _iter_items(self):
        stack = [self.tree.invisibleRootItem()]
        while stack:
            node = stack.pop()
            for i in range(node.childCount()):
                child = node.child(i)
                yield child
                stack.append(child)

    def _profile_items(self, name: str):
        for it in self._iter_items():
            kind, value = it.data(0, ROLE)
            if kind == KIND_PROFILE and value == name:
                yield it

    # ---- filter and expansion --------------------------------------------

    def _apply_filter(self, text: str):
        text = text.strip().lower()

        def matches(it: QTreeWidgetItem) -> bool:
            kind, value = it.data(0, ROLE)
            if kind == KIND_PROFILE:
                p = self.store.profiles.get(value)
                hay = [value, p.group or "", p.account_id or "", p.role or "",
                       p.sso_session or ""] if p else [value]
                return any(text in h.lower() for h in hay)
            return text in it.text(0).lower()

        def visit(it: QTreeWidgetItem, parent_matched: bool) -> bool:
            self_match = matches(it)
            any_child = False
            for i in range(it.childCount()):
                if visit(it.child(i), parent_matched or self_match):
                    any_child = True
            visible = not text or self_match or any_child or parent_matched
            it.setHidden(not visible)
            return self_match or any_child

        self._tracking = False
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            visit(root.child(i), False)
        if text:
            self.tree.expandAll()
        else:
            for it in self._iter_items():
                kind, value = it.data(0, ROLE)
                if kind in (KIND_GROUP, KIND_AUTO):
                    it.setExpanded(item_key(kind, value) in self.expanded)
        self._tracking = True

    def _track_expand(self, item: QTreeWidgetItem, expanded: bool):
        if not self._tracking or self.filter.text().strip():
            return
        key = item_key(*item.data(0, ROLE))
        if expanded:
            self.expanded.add(key)
        else:
            self.expanded.discard(key)
        self._save_expanded()

    def _save_expanded(self):
        self.settings.setValue("expanded", sorted(self.expanded))

    # ---- selection helpers ----------------------------------------------

    def selected_profiles(self) -> list[Profile]:
        names: list[str] = []
        for it in self.tree.selectedItems():
            kind, value = it.data(0, ROLE)
            if kind == KIND_PROFILE and value not in names:
                names.append(value)
        return [self.store.profiles[n] for n in names if n in self.store.profiles]

    def profiles_under(self, item: QTreeWidgetItem) -> list[Profile]:
        out = []
        stack = [item]
        while stack:
            node = stack.pop()
            kind, value = node.data(0, ROLE)
            if kind == KIND_PROFILE and value in self.store.profiles:
                out.append(self.store.profiles[value])
            for i in range(node.childCount()):
                stack.append(node.child(i))
        return out

    # ---- actions: terminals ---------------------------------------------

    def _launch_options(self, profile: Profile) -> LaunchOptions:
        return LaunchOptions(
            terminal=get_setting(self.settings, "terminal"),
            new_window=get_setting(self.settings, "new_window"),
            auto_sso_login=get_setting(self.settings, "auto_sso_login"),
            export_credentials=get_setting(self.settings, "export_credentials"),
            tab_color=self._profile_color(profile),
        )

    def _external_for(self, invert: bool | None, external: bool | None) -> bool:
        if external is not None:
            return external
        is_external = get_setting(self.settings, "open_mode") == "external"
        return (not is_external) if invert else is_external

    def open_profiles(self, profiles: list[Profile], external: bool | None = None,
                      invert: bool | None = False):
        if not profiles:
            return
        bad = [p for p in profiles if p.problem]
        if bad:
            QMessageBox.warning(self, "Cannot open profile",
                                "\n".join(f"{p.name!r}: {p.problem}" for p in bad))
            profiles = [p for p in profiles if not p.problem]
            if not profiles:
                return
        if len(profiles) > 8:
            ok = QMessageBox.question(self, "Open terminals",
                                      f"Open {len(profiles)} terminals?")
            if ok != QMessageBox.StandardButton.Yes:
                return
        if get_setting(self.settings, "confirm_prod"):
            prod = [p.name for p in profiles if self._profile_color(p) == PROD_COLOR]
            if prod:
                ok = QMessageBox.question(
                    self, "Production profile",
                    "Open a terminal for production profile(s)?\n\n" + "\n".join(prod))
                if ok != QMessageBox.StandardButton.Yes:
                    return
        use_external = self._external_for(invert, external)
        for prof in profiles:
            try:
                if use_external:
                    launch(prof, self._launch_options(prof))
                else:
                    self.terminals.open_profile(prof, self._launch_options(prof))
            except LaunchError as exc:
                QMessageBox.warning(self, "Could not open terminal", str(exc))
                return
        names = ", ".join(p.name for p in profiles)
        where = "external terminal" if use_external else "tab"
        self.statusBar().showMessage(f"Opened {where}: {names}", 4000)

    def open_selected(self, invert: bool | None = False, external: bool | None = None):
        self.open_profiles(self.selected_profiles(), external=external, invert=invert)

    def _shift_held(self) -> bool:
        return bool(QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)

    def _on_double_click(self, item: QTreeWidgetItem, _col: int):
        kind, value = item.data(0, ROLE)
        if kind == KIND_PROFILE:
            self.open_profiles([self.store.profiles[value]], invert=self._shift_held())

    def _on_click(self, item: QTreeWidgetItem, _col: int):
        if not get_setting(self.settings, "single_click"):
            return
        if QGuiApplication.keyboardModifiers() != Qt.KeyboardModifier.NoModifier:
            return
        kind, value = item.data(0, ROLE)
        if kind == KIND_PROFILE:
            self.open_profiles([self.store.profiles[value]])

    # ---- terminal tabs ---------------------------------------------------

    def _apply_terminal_settings(self):
        t = self.terminals
        t.font = default_font(get_setting(self.settings, "term_font_family"),
                              get_setting(self.settings, "term_font_size"))
        t.scrollback = get_setting(self.settings, "term_scrollback")
        t.confirm_close = get_setting(self.settings, "confirm_close_tab")
        t.confirm_multiline_paste = get_setting(self.settings, "confirm_multiline_paste")
        for term in t.terminals():
            term.set_font(t.font)
            term.confirm_multiline_paste = t.confirm_multiline_paste

    def close_current_tab(self):
        idx = self.terminals.tabs.currentIndex()
        if idx >= 0 and self.terminals.tabs.count():
            self.terminals.close_tab(idx)

    def _cycle_tab(self, step: int):
        tabs = self.terminals.tabs
        if tabs.count():
            tabs.setCurrentIndex((tabs.currentIndex() + step) % tabs.count())

    def _focus_terminal(self):
        term = self.terminals.current_terminal()
        if term:
            term.setFocus()

    # ---- commands pane ---------------------------------------------------

    def set_commands_visible(self, visible: bool, save: bool = True):
        if visible and not self.commands.isVisible():
            self.commands.show()
            sizes = self.splitter.sizes()
            if len(sizes) == 3 and sizes[2] < 200:
                take = 320 - sizes[2]
                sizes[1] = max(sizes[1] - take, 300)
                sizes[2] = 320
                self.splitter.setSizes(sizes)
        elif not visible:
            self.commands.hide()
        self.commands_tab.setVisible(not visible)
        if hasattr(self, "act_commands"):
            self.act_commands.setChecked(visible)
        if save:
            self.settings.setValue("commands_visible", visible)

    def _find_command(self):
        self.set_commands_visible(True)
        self.commands.focus_filter()

    def _update_command_target(self):
        term = self.terminals.current_terminal()
        info = self.terminals.current_info()
        shell = "powershell" if platform_key() == "win32" else "posix"
        region = info.profile.region if info and info.profile.region else ""
        self.commands.set_target(shell, region, bool(term and term.is_alive()))

    def _confirm_run(self, text: str, command, info) -> bool:
        """Ask before running destructive commands anywhere, and anything that
        changes state on a production profile."""
        destructive = is_destructive(command, text)
        prod = bool(info and self._profile_color(info.profile) == PROD_COLOR)
        if not needs_confirmation(command, text, prod):
            return True
        prof = info.profile if info else None
        who = prof.name if prof else "this terminal"
        acct = f" (account {prof.account_id})" if prof and prof.account_id else ""
        kind = "a destructive command" if destructive else "a command that changes resources"
        where = " on a production profile" if prod else ""
        box = QMessageBox(QMessageBox.Icon.Warning, "Confirm command",
                          f"You are about to run {kind}{where}.", parent=self)
        box.setInformativeText(f"Profile: {who}{acct}\n\n{text}")
        run_btn = box.addButton("Run", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is run_btn

    def _insert_command(self, text: str, run: bool, command=None):
        term = self.terminals.current_terminal()
        if not term or not term.is_alive():
            QGuiApplication.clipboard().setText(text.strip())
            self.statusBar().showMessage(
                "No terminal tab is open, so the command was copied to the clipboard.", 5000)
            return
        if run and command is not None and not self._confirm_run(
                text, command, self.terminals.current_info()):
            self.statusBar().showMessage("Command not run.", 3000)
            return
        term.insert_command(text, run=run)
        term.setFocus()

    def _on_tab_count(self, count: int):
        self.act_close_tab.setEnabled(count > 0)

    def _placeholder_html(self) -> str:
        return ("<div style='color:#8a8f98'>"
                "<h2>No terminals open</h2>"
                "<p>Double-click a profile to open it in a tab here.<br>"
                "Shift+double-click opens it in an external terminal instead.</p>"
                "<p><code>Ctrl+Tab</code> next tab &nbsp; <code>Ctrl+1</code> / "
                "<code>Ctrl+2</code> profiles / terminal</p></div>")

    # ---- actions: status and login --------------------------------------

    def check_profiles(self, profiles: list[Profile]):
        started = self.checker.check([p.name for p in profiles])
        for name in started:
            self.status[name] = StatusResult(name, CHECKING)
            for it in self._profile_items(name):
                self._decorate_profile(it, self.store.profiles[name])
        self._show_details()

    def check_selected(self):
        self.check_profiles(self.selected_profiles())

    def check_all(self):
        self.check_profiles(list(self.store.profiles.values()))

    def _on_status(self, result: StatusResult):
        self.status[result.profile] = result
        prof = self.store.profiles.get(result.profile)
        if prof:
            for it in self._profile_items(prof.name):
                self._decorate_profile(it, prof)
        self._show_details()

    def login_profiles(self, profiles: list[Profile]):
        seen = set()
        for prof in profiles:
            if not prof.is_sso or prof.sso_login_key in seen:
                continue
            seen.add(prof.sso_login_key)
            if self.checker.login(prof.name):
                self.statusBar().showMessage(
                    f"SSO login started for {prof.name}. Finish it in your browser.", 0)

    def login_selected(self):
        profiles = self.selected_profiles()
        if profiles and not any(p.is_sso for p in profiles):
            QMessageBox.information(self, "SSO login",
                                    "None of the selected profiles use IAM Identity Center.")
            return
        self.login_profiles(profiles)

    def _on_login_finished(self, name: str, ok: bool, output: str):
        if not ok:
            self.statusBar().showMessage(f"SSO login failed for {name}.", 6000)
            QMessageBox.warning(self, "SSO login failed", output or "Login failed.")
            return
        self.statusBar().showMessage(f"SSO login complete for {name}.", 5000)
        prof = self.store.profiles.get(name)
        if prof:
            same = [p for p in self.store.profiles.values()
                    if p.sso_login_key == prof.sso_login_key]
            self.check_profiles(same)

    # ---- actions: groups (all write to the config) ----------------------

    def _write(self, fn, *args) -> bool:
        if self.store.file_changed_on_disk():
            # Pick up outside edits first; the operation then applies on top of them.
            self.reload()
        try:
            fn(*args)
        except ConfigError as exc:
            QMessageBox.warning(self, "Could not update config", str(exc))
            self.reload()
            return False
        except OSError as exc:
            QMessageBox.critical(self, "Could not write config", str(exc))
            self.reload()
            return False
        self.rebuild()
        return True

    def _apply_move(self, keys: list[str], target: str | None):
        profile_targets: dict[str, str | None] = {}
        group_moves: list[tuple[str, str | None]] = []
        for key in keys:
            kind, value = key.split(":", 1)
            if kind == KIND_PROFILE:
                prof = self.store.profiles.get(value)
                if prof and prof.group != target:
                    profile_targets[value] = target
            elif kind == KIND_GROUP:
                new = join_group(target, leaf_name(value))
                if new != value:
                    group_moves.append((value, new))
        if not profile_targets and not group_moves:
            return
        for old, new in group_moves:
            self._remap_expanded(old, new)
        if self._write(self.store.move, profile_targets, group_moves):
            if target:
                self.expanded.add(item_key(KIND_GROUP, target))
                self._save_expanded()
                self.rebuild()
            n = len(profile_targets) + len(group_moves)
            where = target or "the top level"
            self.statusBar().showMessage(f"Moved {n} item(s) to {where}.", 4000)

    def _remap_expanded(self, old: str, new: str | None):
        updated = set()
        for key in self.expanded:
            kind, value = key.split(":", 1)
            if kind == KIND_GROUP and is_within(value, old):
                if new:
                    rest = value[len(old):]
                    updated.add(item_key(KIND_GROUP, new + rest))
            else:
                updated.add(key)
        self.expanded = updated
        self._save_expanded()

    def _ask_group_name(self, title: str, label: str, text: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, label, text=text)
        if not ok:
            return None
        name = name.strip()
        return name or None

    def new_group(self, parent: str | None):
        where = f" inside '{parent}'" if parent else ""
        name = self._ask_group_name("New group", f"Group name{where} (use / to nest):")
        if not name:
            return
        path = join_group(parent, name)
        if self._write(self.store.add_group, path):
            for anc in [parent] if parent else []:
                self.expanded.add(item_key(KIND_GROUP, anc))
            self.expanded.add(item_key(KIND_GROUP, path))
            self._save_expanded()
            self.rebuild()

    def rename_group(self, path: str):
        name = self._ask_group_name("Rename group", "New name:", leaf_name(path))
        if not name or name == leaf_name(path):
            return
        if SEP in name:
            QMessageBox.information(self, "Rename group",
                                    "Use drag and drop to move a group. Names cannot contain '/'.")
            return
        new = join_group(parent_group(path), name)
        self._remap_expanded(path, new)
        self._write(self.store.rename_group, path, new)

    def _rename_current_group(self):
        it = self.tree.currentItem()
        if it and it.data(0, ROLE)[0] == KIND_GROUP:
            self.rename_group(it.data(0, ROLE)[1])

    def delete_group(self, path: str):
        count = len(self.store.profiles_in(path))
        parent = parent_group(path)
        dest = f"'{parent}'" if parent else "their automatic groups"
        msg = f"Delete group '{path}'?"
        if count:
            msg += f"\n\nIts {count} profile(s) and subgroups move to {dest}."
        if QMessageBox.question(self, "Delete group", msg) != QMessageBox.StandardButton.Yes:
            return
        self._remap_expanded(path, parent)
        self._write(self.store.delete_group, path)

    def remove_from_group(self, profiles: list[Profile]):
        targets = {p.name: None for p in profiles if p.group}
        if targets:
            self._write(self.store.set_profile_groups, targets)

    def set_color(self, profiles: list[Profile], clear: bool = False):
        color = None
        if not clear:
            initial = QColor(profiles[0].color or self._profile_color(profiles[0]) or "#2d7ff9")
            chosen = QColorDialog.getColor(initial, self, "Tab color")
            if not chosen.isValid():
                return
            color = chosen.name()
        for p in profiles:
            if not self._write(self.store.set_profile_color, p.name, color):
                return

    # ---- context menu ----------------------------------------------------

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            self.tree.clearSelection()
            menu.addAction("New group...", lambda: self.new_group(None))
            menu.addAction("Check all", self.check_all)
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
            self.tree.setCurrentItem(item)
        kind, value = item.data(0, ROLE)

        if kind == KIND_PROFILE:
            profiles = self.selected_profiles()
            label = "Open terminal" if len(profiles) == 1 else f"Open {len(profiles)} terminals"
            menu.addAction(label, lambda: self.open_profiles(profiles))
            if any(p.is_sso for p in profiles):
                menu.addAction("SSO login", lambda: self.login_profiles(profiles))
            menu.addAction("Check status", lambda: self.check_profiles(profiles))
            menu.addSeparator()
            if len(profiles) == 1:
                prof = profiles[0]
                menu.addAction("Copy profile name",
                               lambda: QApplication.clipboard().setText(prof.name))
                export = (f"$env:AWS_PROFILE = '{prof.name}'" if platform_key() == "win32"
                          else f"export AWS_PROFILE={prof.name}")
                menu.addAction("Copy AWS_PROFILE command",
                               lambda: QApplication.clipboard().setText(export))
                if safe_https_url(prof.sso_start_url):
                    menu.addAction("Open SSO portal",
                                   lambda: self._open_link(QUrl(prof.sso_start_url)))
                menu.addSeparator()
            menu.addAction("Set tab color...", lambda: self.set_color(profiles))
            if any(p.color for p in profiles):
                menu.addAction("Clear tab color", lambda: self.set_color(profiles, clear=True))
            if any(p.group for p in profiles):
                menu.addAction("Remove from group", lambda: self.remove_from_group(profiles))
        elif kind == KIND_GROUP:
            under = self.profiles_under(item)
            if under:
                menu.addAction(f"Open all {len(under)} terminals",
                               lambda: self.open_profiles(under))
                menu.addAction("Check status of group", lambda: self.check_profiles(under))
                if any(p.is_sso for p in under):
                    menu.addAction("SSO login for group", lambda: self.login_profiles(under))
                menu.addSeparator()
            menu.addAction("New subgroup...", lambda: self.new_group(value))
            menu.addAction("Rename...", lambda: self.rename_group(value))
            menu.addAction("Delete group", lambda: self.delete_group(value))
        else:  # automatic group
            under = self.profiles_under(item)
            if under:
                menu.addAction(f"Open all {len(under)} terminals",
                               lambda: self.open_profiles(under))
                menu.addAction("Check status of group", lambda: self.check_profiles(under))
                if any(p.is_sso for p in under):
                    menu.addAction("SSO login for group", lambda: self.login_profiles(under))
            menu.addSeparator()
            menu.addAction("New group...", lambda: self.new_group(None))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ---- details pane ----------------------------------------------------

    def _show_details(self):
        items = self.tree.selectedItems()
        profiles = self.selected_profiles()
        self.btn_open.setEnabled(bool(profiles))
        self.btn_check.setEnabled(bool(profiles))
        self.btn_login.setEnabled(any(p.is_sso for p in profiles))

        if len(items) == 1 and items[0].data(0, ROLE)[0] != KIND_PROFILE:
            self.details.setHtml(self._group_html(items[0]))
            return
        if len(profiles) != 1:
            if profiles:
                names = "".join(f"<li>{html.escape(p.name)}</li>" for p in profiles)
                self.details.setHtml(f"<h3>{len(profiles)} profiles selected</h3><ul>{names}</ul>")
            else:
                self.details.setHtml(self._help_html())
            return

        p = profiles[0]
        result = self.status.get(p.name)
        state = result.state if result else UNKNOWN
        rows = [
            ("Group", p.group or f"<i>{html.escape(auto_group_label(p, get_setting(self.settings, 'auto_group')))} (automatic)</i>"),
            ("Type", KIND_LABELS[p.kind]),
            ("Account", p.account_id or ""),
            ("Role", p.role or ""),
            ("Region", p.region or ""),
        ]
        if p.sso_session:
            rows.append(("SSO session", p.sso_session))
        if p.sso_start_url:
            url = html.escape(p.sso_start_url)
            link = (f'<a href="{url}">{url}</a>' if safe_https_url(p.sso_start_url)
                    else f"{url} <i>(not an https URL, link disabled)</i>")
            rows.append(("SSO start URL", link))
        if p.props.get("source_profile"):
            rows.append(("Source profile", p.props["source_profile"]))
        colour = STATUS_COLORS[state]
        status_html = f'<span style="color:{colour}">&#9679;</span> {STATUS_LABELS[state]}'
        rows.append(("Status", status_html))
        if result and result.arn:
            rows.append(("Identity", result.arn))
        if result and result.detail and state not in (VALID, CHECKING):
            rows.append(("Detail", html.escape(result.detail)))

        def cell(v: str, raw: bool) -> str:
            return v if raw else html.escape(v)

        raw_keys = {"Group", "SSO start URL", "Status"}
        table = "".join(
            f"<tr><td style='color:#8a8f98;padding-right:12px'>{k}</td>"
            f"<td>{cell(v, k in raw_keys)}</td></tr>" for k, v in rows if v)
        props = "".join(
            f"<tr><td style='color:#8a8f98;padding-right:12px'><code>{html.escape(k)}</code></td>"
            f"<td><code>{html.escape(v)}</code></td></tr>" for k, v in p.display_props())
        sources = ", ".join(sorted(p.sources))
        color = self._profile_color(p)
        title_style = f" style='color:{color}'" if color else ""
        self.details.setHtml(
            f"<h2{title_style}>{html.escape(p.name)}</h2>"
            f"<table cellspacing=2>{table}</table>"
            f"<h4>Raw settings <span style='color:#8a8f98;font-weight:normal'>({sources})</span></h4>"
            f"<table cellspacing=2>{props}</table>")

    def _group_html(self, item: QTreeWidgetItem) -> str:
        kind, value = item.data(0, ROLE)
        under = self.profiles_under(item)
        counts: dict[str, int] = {}
        for p in under:
            r = self.status.get(p.name)
            counts[r.state if r else UNKNOWN] = counts.get(r.state if r else UNKNOWN, 0) + 1
        summary = "".join(
            f"<li><span style='color:{STATUS_COLORS[s]}'>&#9679;</span> {STATUS_LABELS[s]}: {n}</li>"
            for s, n in counts.items())
        if kind == KIND_GROUP:
            head = f"<h2>{html.escape(value)}</h2><p>Stored as <code>x_group = {html.escape(value)}</code> on each profile.</p>"
        else:
            head = (f"<h2><i>{html.escape(value)}</i></h2><p>Automatic group for profiles "
                    "without an <code>x_group</code>. Drag profiles into a named group to "
                    "assign them.</p>")
        return f"{head}<p>{len(under)} profile(s)</p><ul>{summary}</ul>"

    def _help_html(self) -> str:
        return (
            "<h2>termSwitch</h2>"
            f"<p>Config: <code>{html.escape(str(self.store.config_file))}</code></p>"
            "<ul>"
            "<li><b>Double-click</b> a profile (or press Enter) to open an authenticated terminal.</li>"
            "<li><b>Drag</b> profiles or groups onto a group to move them. Drop a profile on "
            "empty space or an automatic (italic) group to remove its group.</li>"
            "<li>Groups are saved to the config as <code>x_group = Parent/Child</code>.</li>"
            "<li>Right-click for SSO login, status checks, tab colors and group actions.</li>"
            "</ul>")

    # ---- misc ------------------------------------------------------------

    def _set_auto_group(self, value: str):
        self.settings.setValue("auto_group", value)
        self._sync_auto_group_actions()
        self.rebuild()

    def _sync_auto_group_actions(self):
        current = get_setting(self.settings, "auto_group")
        for a in self._auto_group_actions:
            a.setChecked(a.data() == current)

    def open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            self._apply_terminal_settings()
            self._sync_auto_group_actions()
            self.rebuild()

    def edit_config(self):
        path = str(self.store.config_file)
        try:
            if sys.platform.startswith("win"):
                import ntpath, os
                notepad = ntpath.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                      "System32", "notepad.exe")
                subprocess.Popen([notepad, path])
            elif sys.platform == "darwin":
                subprocess.Popen(["/usr/bin/open", "-t", path])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except OSError as exc:
            QMessageBox.warning(self, "Edit config", str(exc))

    # ---- dependency check -------------------------------------------------

    def run_dependency_check(self, startup: bool = False):
        self._deps_startup = startup
        if self.deps.start(get_setting(self.settings, "check_updates_online")):
            self.dep_button.setText("Checking dependencies...")
            self.dep_button.setIcon(QIcon())

    def _on_deps_done(self, checks):
        level = worst(checks)
        icon = {DEP_ERROR: QStyle.StandardPixmap.SP_MessageBoxCritical,
                DEP_WARN: QStyle.StandardPixmap.SP_MessageBoxWarning}.get(
                    level, QStyle.StandardPixmap.SP_DialogApplyButton)
        self.dep_button.setIcon(self.style().standardIcon(icon))
        self.dep_button.setText(dep_summary(checks))
        self.dep_button.setToolTip("Click for the dependency report")
        if self._dep_dialog is not None and self._dep_dialog.isVisible():
            self._dep_dialog.set_checks(checks)
            return
        startup, self._deps_startup = self._deps_startup, False
        if (not startup or level == DEP_ERROR
                or (level == DEP_WARN and get_setting(self.settings, "show_dep_warnings"))):
            self.show_dependency_dialog()

    def show_dependency_dialog(self):
        if not self.deps.results:
            self.run_dependency_check(startup=False)
            return
        if self._dep_dialog is not None and self._dep_dialog.isVisible():
            self._dep_dialog.raise_()
            return
        dlg = DependencyDialog(self.deps.results,
                               get_setting(self.settings, "show_dep_warnings"), self)
        dlg.recheck_requested.connect(lambda: self.run_dependency_check(startup=False))
        dlg.finished.connect(lambda _r: self.settings.setValue(
            "show_dep_warnings", dlg.show_warnings.isChecked()))
        self._dep_dialog = dlg
        dlg.open()

    def _open_link(self, url: QUrl):
        target = safe_https_url(url.toString())
        if not target:
            QMessageBox.warning(self, "Link blocked",
                                f"Only https links are opened.\n\n{url.toString()}")
            return
        QDesktopServices.openUrl(QUrl(target))

    def show_config_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.config_file.parent)))

    def about(self):
        box = QMessageBox(self)
        box.setWindowTitle("About termSwitch")
        box.setIconPixmap(self.windowIcon().pixmap(64, 64))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        box.setText(
            f"<b>termSwitch</b> {html.escape(__version__)}<br><br>"
            f"Config: {html.escape(str(self.store.config_file))}<br>"
            f"AWS CLI: {html.escape(find_aws() or 'not found')}<br><br>"
            "Copyright (c) 2026 Mike Roughton. Released under the MIT license.<br>"
            "Icons from the <a href='https://github.com/material-extensions/"
            "vscode-material-icon-theme'>Material Icon Theme</a> by Philipp Kief and "
            "Material Extensions (MIT).<br>"
            "Built with Qt for Python under the LGPL 3.0. See Licenses for all "
            "third-party notices.")
        licenses = box.addButton("Licenses...", QMessageBox.ButtonRole.HelpRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is licenses:
            self.show_licenses()

    def show_licenses(self, start: str | None = None):
        LicensesDialog(self, start=start).exec()

    def closeEvent(self, event):
        running = self.terminals.running_count()
        if running:
            ok = QMessageBox.question(
                self, "Quit termSwitch",
                f"{running} terminal(s) are still running. Quit and end them?")
            if ok != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.terminals.close_all()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("main_splitter_v2", self.splitter.saveState())
        self.settings.setValue("commands_visible", self.commands.isVisible())
        self.settings.setValue("left_splitter", self.left_splitter.saveState())
        self._save_expanded()
        super().closeEvent(event)

