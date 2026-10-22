"""Main window: grouped profile tree on the left, details on the right."""

from __future__ import annotations

import html
import os
import re
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import (QFileSystemWatcher, QSize, QItemSelectionModel, QSettings, Qt, QTimer,
                            QUrl, Signal)
from PySide6.QtGui import (QAction, QBrush, QColor, QDesktopServices, QFont, QIcon,
                           QKeySequence, QPainter, QPixmap, QShortcut, QGuiApplication)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QColorDialog,
                               QComboBox, QDialog, QDialogButtonBox, QFontComboBox,
                               QFormLayout, QSpinBox,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
                               QMenu, QMessageBox, QPushButton, QSplitter, QStackedWidget,
                               QStyle, QTextBrowser, QToolBar, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget, QFileDialog)

from .config_store import (SEP, ConfigError, Profile, ProfileStore, is_within, join_group,
                           leaf_name, natural_key, parent_group, safe_https_url)
from .launcher import (TERMINAL_CHOICES, LaunchError, LaunchOptions, aws_setup_command,
                       find_aws, find_ssh, launch, platform_key, ssh_env)
from .config_dialogs import (SECRETS_REFUSED, BackupsDialog, ConfigEditorDialog,
                             WelcomePanel)
from .profile_dialogs import (KIND_ROLE, KIND_SSO, ProfileDialog, SsoSessionDialog,
                              SsoSessionsDialog)
from .deps import ERROR as DEP_ERROR, WARN as DEP_WARN, summary as dep_summary, worst
from .depsui import DependencyDialog, DependencyRunner
from .legal import LicensesDialog
from . import theme
from .term_schemes import (MATCH_APP, SchemeError, all_schemes, import_file, save_user_scheme,
                           scheme_for)
from .ssh_dialogs import DELETE as PW_DELETE, SET as PW_SET, SshSessionDialog
from .ssh_sessions import (PasswordAutofill, PasswordStore, SessionError, SessionStore,
                           SshSession, display_command, key_file_problem, ssh_argv)
from .icons import (AUTO_GROUP_ICONS, PROVIDER_DOT_CORNER, PROVIDER_ICONS, decorate_menu, icon as material_icon,
                    install_icon_theme, shell_icon)
from .aws_params import is_destructive, needs_confirmation
from .commands_pane import CommandsPane, VerticalTab
from .terminal_tabs import TerminalTabs
from .terminal_widget import default_font
from .status import CHECKING, ERROR, EXPIRED, UNKNOWN, VALID, StatusChecker, StatusResult

ROLE = Qt.ItemDataRole.UserRole
KIND_GROUP, KIND_AUTO, KIND_PROFILE = "group", "auto", "profile"
KIND_SSH_ROOT, KIND_SSH_GROUP, KIND_SSH = "sshroot", "sshgroup", "ssh"
SSH_KINDS = (KIND_SSH_ROOT, KIND_SSH_GROUP, KIND_SSH)
SSH_ROOT_LABEL = "SSH sessions"
SSH_CONFIG_KEY = "\x00ssh-config"      # tree key for the ~/.ssh/config folder

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
    "app_theme": theme.SYSTEM,
    "term_scheme": MATCH_APP,
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
                    if i.data(0, ROLE)[0] in (KIND_GROUP, KIND_PROFILE, KIND_SSH)]
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
            elif kind in (KIND_AUTO, KIND_SSH_ROOT, KIND_SSH_GROUP):
                target = None  # an automatic or SSH folder: take it out of any folder
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
    def __init__(self, settings: QSettings, parent=None, schemes: dict | None = None):
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

        self.app_theme = QComboBox()
        for value, label in theme.THEME_CHOICES:
            self.app_theme.addItem(label, value)
        self._select(self.app_theme, get_setting(settings, "app_theme"))
        form.addRow("Theme:", self.app_theme)

        self.schemes = schemes if schemes is not None else all_schemes()
        self.term_scheme = QComboBox()
        self._fill_schemes(get_setting(settings, "term_scheme"))
        import_btn = QPushButton("Import...")
        import_btn.setToolTip("Import a Windows Terminal (.json) or iTerm2 (.itermcolors) "
                              "color scheme")
        import_btn.clicked.connect(self._import_scheme)
        scheme_row = QHBoxLayout()
        scheme_row.addWidget(self.term_scheme, 1)
        scheme_row.addWidget(import_btn)
        form.addRow("Terminal colors:", scheme_row)

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

    def _fill_schemes(self, current: str):
        self.term_scheme.clear()
        self.term_scheme.addItem("Match the app theme", MATCH_APP)
        for name in sorted(self.schemes, key=lambda n: (not self.schemes[n].builtin, n.lower())):
            self.term_scheme.addItem(name, name)
        self._select(self.term_scheme, current)

    def _import_scheme(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import terminal colors", "",
            "Color schemes (*.json *.itermcolors);;All files (*)")
        if not path:
            return
        try:
            imported = import_file(Path(path))
            for scheme in imported:
                save_user_scheme(scheme)
        except (SchemeError, OSError) as exc:
            QMessageBox.warning(self, "Import terminal colors", str(exc))
            return
        self.schemes = all_schemes()
        self._fill_schemes(imported[0].name)
        QMessageBox.information(self, "Import terminal colors",
                                "Imported " + ", ".join(s.name for s in imported) + ".")

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
        s.setValue("app_theme", self.app_theme.currentData())
        s.setValue("term_scheme", self.term_scheme.currentData())
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


def apply_config_file_setting(settings: QSettings) -> None:
    """Use the config file chosen in File > Use a different config file. An
    AWS_CONFIG_FILE set by the user's environment wins. Setting the variable here
    also makes status checks and new terminal tabs use the same file."""
    chosen = settings.value("config_file", "", type=str)
    if chosen and not os.environ.get("AWS_CONFIG_FILE"):
        os.environ["AWS_CONFIG_FILE"] = chosen


def get_setting(settings: QSettings, key: str):
    default = DEFAULTS[key]
    return settings.value(key, default, type=type(default))


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, store: ProfileStore | None = None, settings: QSettings | None = None):
        super().__init__()
        install_icon_theme(QApplication.instance())
        self.settings = settings or QSettings()
        if store is None:
            apply_config_file_setting(self.settings)
        self.store = store or ProfileStore()
        self.status: dict[str, StatusResult] = {}
        self.theme = theme.manager(QApplication.instance())
        self.theme.apply(get_setting(self.settings, "app_theme"))
        self.theme.changed.connect(self._on_theme_changed)
        self.schemes = all_schemes()
        self.ssh_store = SessionStore()
        self.passwords = PasswordStore()
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
        # Menu bar menus can be native (macOS), so decorate them directly too.
        for menu_action in self.menuBar().actions():
            if menu_action.menu() is not None:
                decorate_menu(menu_action.menu())

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

        self.welcome = WelcomePanel()
        self.welcome.setup_sso.connect(self.setup_sso)
        self.welcome.add_profile.connect(lambda: self.new_profile())
        self.welcome.choose_file.connect(self.choose_config_file)
        self.welcome.check_dependencies.connect(self.show_dependency_dialog)
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(left)
        self.left_stack.addWidget(self.welcome)

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
        self.btn_edit = QPushButton("Edit...")
        self.btn_edit.setToolTip("Edit the selected profile")
        self.btn_edit.clicked.connect(self.edit_selected_profile)
        for b in (self.btn_open, self.btn_login, self.btn_check, self.btn_edit):
            buttons.addWidget(b)
        buttons.addStretch(1)
        rv.addLayout(buttons)

        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.addWidget(self.left_stack)
        self.left_splitter.addWidget(details_box)
        self.left_splitter.setStretchFactor(0, 3)
        self.left_splitter.setStretchFactor(1, 2)
        self.left_splitter.setChildrenCollapsible(True)

        self.terminals = TerminalTabs(self._placeholder_html())
        self.terminals.open_external_requested.connect(
            lambda prof: self.open_profiles([prof], external=True))
        self.terminals.count_changed.connect(self._on_tab_count)
        self.terminals.scheme_for = self._scheme_for
        self.terminals.display_color = (
            lambda color: theme.colors().prod if color == PROD_COLOR else color)
        self._apply_terminal_settings()

        self.commands = CommandsPane(settings=self.settings)
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
        # Profile shortcuts only apply while the tree has focus, so keys such as
        # Ctrl+Z and Ctrl+N still reach the shell in a terminal tab.
        for key, slot in ((QKeySequence(Qt.Key.Key_F2), self._rename_current),
                          (QKeySequence(Qt.Key.Key_Delete), self._delete_current),
                          (QKeySequence("Ctrl+Backspace"), self._delete_current),
                          (QKeySequence("Ctrl+N"), self._new_profile_here),
                          (QKeySequence("Ctrl+E"), self.edit_selected_profile)):
            sc = QShortcut(key, self.tree)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(slot)

    def _build_actions(self):
        style = self.style()
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        # Same icon size as push buttons (usually 16 px), not the larger toolbar default.
        side = style.pixelMetric(QStyle.PixelMetric.PM_ButtonIconSize)
        tb.setIconSize(QSize(side, side))
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
        self.act_new_profile = action("New profile", lambda: self._new_profile_here())
        self.act_new_profile.setIcon(material_icon("key"))
        self.act_new_profile.setToolTip("Create a profile in the selected group (Ctrl+N in the tree)")
        for a in (self.act_open, self.act_new_group, self.act_check_all, self.act_reload,
                  self.act_settings):
            tb.addAction(a)
            if a is self.act_open:
                tb.addAction(self.act_new_profile)

        self.act_undo = action("Undo", self.undo_config_change)
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_undo.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.tree.addAction(self.act_undo)
        self.act_use_default_config = action("Use the default config file",
                                             self.use_default_config_file)

        menu_file = self.menuBar().addMenu("&File")
        menu_file.addAction(self.act_reload)
        menu_file.addSeparator()
        menu_file.addAction(action("Set up AWS SSO...", self.setup_sso))
        menu_file.addAction(action("Add profiles from AWS SSO...", self.add_profiles_from_sso))
        menu_file.addAction(action("SSO sessions...", self.manage_sso_sessions))
        menu_file.addSeparator()
        menu_file.addAction(action("Edit config file...", self.edit_config_in_app))
        menu_file.addAction(action("Open in external editor", self.edit_config))
        menu_file.addAction(action("Show config folder", self.show_config_folder))
        menu_file.addAction(action("Backups...", self.show_backups))
        menu_file.addSeparator()
        menu_file.addAction(action("Use a different config file...", self.choose_config_file))
        menu_file.addAction(self.act_use_default_config)
        menu_file.addAction(action("Reset config...", self.reset_config))
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
        menu_profiles.addAction(self.act_new_profile)
        menu_profiles.addAction(action("New SSH session...", lambda: self.new_ssh_session()))
        menu_profiles.addAction(action("Edit profile...", self.edit_selected_profile))
        menu_profiles.addAction(action("Rename profile...", self._rename_current))
        menu_profiles.addAction(action("Duplicate profile...", self._duplicate_current))
        menu_profiles.addAction(action("Delete profile...", self._delete_current))
        menu_profiles.addSeparator()
        menu_profiles.addAction(self.act_new_group)
        menu_profiles.addSeparator()
        menu_profiles.addAction(self.act_undo)
        menu_profiles.aboutToShow.connect(self._sync_undo)
        self._sync_undo()

        menu_help = self.menuBar().addMenu("&Help")
        menu_help.addAction(action("Check dependencies...", self.show_dependency_dialog))
        menu_help.addAction(action("Licenses...", lambda: self.show_licenses()))
        about = action("About", self.about)
        about.setMenuRole(QAction.MenuRole.AboutRole)
        menu_help.addAction(about)

    # ---- loading and tree building --------------------------------------

    def reload(self):
        self.ssh_store.load()
        for warning in self.ssh_store.warnings:
            self.statusBar().showMessage(warning, 8000)
        try:
            self.store.load()
        except ConfigError as exc:
            QMessageBox.critical(self, "Config error", str(exc))
            return
        self._watch()
        self.rebuild()
        self._update_left_view()
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

    def _scheme_for(self, profile: Profile | None):
        """Terminal colors for a tab: the profile's own scheme, else the default."""
        choice = (profile.term_scheme if profile is not None and profile.term_scheme
                  else get_setting(self.settings, "term_scheme"))
        return scheme_for(choice, theme.current(), self.schemes)

    def _display_color(self, profile: Profile) -> str | None:
        """Text color for a profile name: production red adjusted for the theme."""
        color = self._profile_color(profile)
        return theme.colors().prod if color == PROD_COLOR else color

    def _on_theme_changed(self, _effective: str):
        self.terminals.placeholder.setText(self._placeholder_html())
        self.rebuild()
        self.terminals.apply_schemes(self.store.profiles)
        self.commands.model.layoutChanged.emit()
        self.commands.form.set_favorite(self.commands.form.star.isChecked())

    def set_profile_scheme(self, profiles: list[Profile], scheme: str | None):
        for p in profiles:
            if not self._write(self.store.set_profile_scheme, p.name, scheme):
                return
        self.terminals.apply_schemes(self.store.profiles)

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

        # Folders are shared: AWS profiles and SSH sessions can sit in the same one.
        for g in sorted(self.store.all_groups() | self.ssh_store.groups(), key=natural_key):
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
            it.setForeground(0, QBrush(QColor(theme.colors().hint)))
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

        ssh_items = self._build_ssh_items(group_item)

        # Expansion state
        for key, it in ssh_items.items():
            it.setExpanded(self._first_build or key in self.expanded)
        for key_path, it in list(group_items.items()):
            key = item_key(KIND_GROUP, key_path)
            it.setExpanded(self._first_build or key in self.expanded)
        for label, it in auto_items.items():
            key = item_key(KIND_AUTO, label)
            it.setExpanded(self._first_build or key in self.expanded)
        if self._first_build:
            self.expanded = {item_key(*i.data(0, ROLE))
                             for i in list(group_items.values()) + list(auto_items.values())
                             + list(ssh_items.values())}
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

    def _build_ssh_items(self, group_item) -> dict[str, QTreeWidgetItem]:
        """SSH sessions go in the shared folders (``group_item(path)``). Sessions in no
        folder go in an "SSH sessions" folder, and ~/.ssh/config hosts not placed in a
        folder in a read-only "~/.ssh/config" folder inside it. Returns those two
        folders by key, for expansion state."""
        sessions = self.ssh_store.all()
        folders: dict[str, QTreeWidgetItem] = {}

        def root() -> QTreeWidgetItem:
            key = item_key(KIND_SSH_ROOT, "")
            if key not in folders:
                it = QTreeWidgetItem(self.tree.invisibleRootItem(), [SSH_ROOT_LABEL])
                it.setData(0, ROLE, (KIND_SSH_ROOT, ""))
                it.setIcon(0, material_icon(PROVIDER_ICONS["ssh"]))
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                            | Qt.ItemFlag.ItemIsDropEnabled)
                f = it.font(0)
                f.setItalic(True)
                it.setFont(0, f)
                it.setForeground(0, QBrush(QColor(theme.colors().hint)))
                it.setToolTip(0, "SSH sessions that aren't in a folder.\nDrag one onto a "
                                 "folder to move it there.")
                folders[key] = it
            return folders[key]

        def config_folder() -> QTreeWidgetItem:
            # One folder, not a path: its name contains "/".
            key = item_key(KIND_SSH_GROUP, SSH_CONFIG_KEY)
            if key not in folders:
                it = QTreeWidgetItem(root(), ["~/.ssh/config"])
                it.setData(0, ROLE, (KIND_SSH_GROUP, SSH_CONFIG_KEY))
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                            | Qt.ItemFlag.ItemIsDropEnabled)
                it.setIcon(0, material_icon("folder-config"))
                fi = it.font(0)
                fi.setItalic(True)
                it.setFont(0, fi)
                it.setForeground(0, QBrush(QColor(theme.colors().hint)))
                it.setToolTip(0, "Hosts from ~/.ssh/config (read-only). Drag one onto a "
                                 "folder to show it there.")
                folders[key] = it
            return folders[key]

        for s in sorted(sessions, key=lambda s: (s.from_ssh_config, natural_key(s.name))):
            if s.group:
                parent = group_item(s.group)
            elif s.from_ssh_config:
                parent = config_folder()
            else:
                parent = root()
            it = QTreeWidgetItem(parent, [s.name])
            it.setData(0, ROLE, (KIND_SSH, s.id))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsDragEnabled)
            it.setIcon(0, material_icon(PROVIDER_ICONS["ssh"], dot=s.color,
                                        corner=PROVIDER_DOT_CORNER))
            port = f":{s.port}" if s.port != 22 else ""
            tip = [s.name, f"SSH {s.destination}{port}"]
            if s.jump:
                tip.append(f"Through {s.jump}")
            if s.from_ssh_config:
                tip.append("From ~/.ssh/config (read-only)")
            it.setToolTip(0, "\n".join(tip))
            if s.color:
                it.setForeground(0, QBrush(QColor(s.color)))
        return folders

    def _decorate_profile(self, it: QTreeWidgetItem, prof: Profile):
        result = self.status.get(prof.name)
        state = result.state if result else UNKNOWN
        color = self._display_color(prof)
        # Provider icon (AWS) with the status dot in the corner.
        it.setIcon(0, material_icon(PROVIDER_ICONS["aws"], dot=STATUS_COLORS[state],
                                    corner=PROVIDER_DOT_CORNER))
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

    def selected_ssh(self) -> list[SshSession]:
        found: list[SshSession] = []
        for it in self.tree.selectedItems():
            kind, value = it.data(0, ROLE)
            s = self.ssh_store.get(value) if kind == KIND_SSH else None
            if s is not None and s not in found:
                found.append(s)
        return found

    def ssh_under(self, item: QTreeWidgetItem) -> list[SshSession]:
        found, stack = [], [item]
        while stack:
            node = stack.pop()
            kind, value = node.data(0, ROLE)
            if kind == KIND_SSH and self.ssh_store.get(value):
                found.append(self.ssh_store.get(value))
            stack.extend(node.child(i) for i in range(node.childCount()))
        return found

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
        for s in self.selected_ssh():
            self.connect_ssh(s)

    def _shift_held(self) -> bool:
        return bool(QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)

    def _on_double_click(self, item: QTreeWidgetItem, _col: int):
        kind, value = item.data(0, ROLE)
        if kind == KIND_PROFILE:
            self.open_profiles([self.store.profiles[value]], invert=self._shift_held())
        elif kind == KIND_SSH and self.ssh_store.get(value):
            self.connect_ssh(self.ssh_store.get(value))

    def _on_click(self, item: QTreeWidgetItem, _col: int):
        if not get_setting(self.settings, "single_click"):
            return
        if QGuiApplication.keyboardModifiers() != Qt.KeyboardModifier.NoModifier:
            return
        kind, value = item.data(0, ROLE)
        if kind == KIND_PROFILE:
            self.open_profiles([self.store.profiles[value]])
        elif kind == KIND_SSH and self.ssh_store.get(value):
            self.connect_ssh(self.ssh_store.get(value))

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
        region = info.profile.region if info and info.profile and info.profile.region else ""
        self.commands.set_target(shell, region, bool(term and term.is_alive()))

    def _confirm_run(self, text: str, command, info) -> bool:
        """Ask before running destructive commands anywhere, and anything that
        changes state on a production profile."""
        destructive = is_destructive(command, text)
        prod = bool(info and info.profile and self._profile_color(info.profile) == PROD_COLOR)
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
        return (f"<div style='color:{theme.colors().hint}'>"
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
        self._undo_parts, self._undo_label = {"aws"}, self.store.undo_label
        self._watch()
        self.rebuild()
        self._update_left_view()
        self._sync_undo()
        return True

    def _apply_move(self, keys: list[str], target: str | None):
        profile_targets: dict[str, str | None] = {}
        ssh_targets: dict[str, str | None] = {}
        group_moves: list[tuple[str, str | None]] = []
        for key in keys:
            kind, value = key.split(":", 1)
            if kind == KIND_PROFILE:
                prof = self.store.profiles.get(value)
                if prof and prof.group != target:
                    profile_targets[value] = target
            elif kind == KIND_SSH:
                s = self.ssh_store.get(value)
                if s and s.group != target:
                    ssh_targets[value] = target
            elif kind == KIND_GROUP:
                new = join_group(target, leaf_name(value))
                if new != value:
                    group_moves.append((value, new))
        if not profile_targets and not group_moves and not ssh_targets:
            return

        def ssh_change():
            for old, new in group_moves:
                self.ssh_store.remap_groups(old, new)
            if ssh_targets:
                self.ssh_store.set_groups(ssh_targets)

        aws_change = ((self.store.move, profile_targets, group_moves)
                      if profile_targets or group_moves else None)
        n = len(profile_targets) + len(group_moves) + len(ssh_targets)
        where = target or "the top level"
        if not self._change_both(f"Move {n} item(s) to {where}", ssh_change, aws_change):
            return
        for old, new in group_moves:
            self._remap_expanded(old, new)
        if target:
            self.expanded.add(item_key(KIND_GROUP, target))
            self._save_expanded()
            self.rebuild()
        self.statusBar().showMessage(f"Moved {n} item(s) to {where}.", 4000)

    # ---- changes to the AWS config and the SSH sessions file together -----

    def _change_both(self, label: str, ssh_change=None, aws_change=None) -> bool:
        """Apply a change that may touch the SSH sessions file and the AWS config.
        The SSH part goes first; if the AWS part then fails, the SSH part is put
        back. Undo then reverses both."""
        if self.store.file_changed_on_disk():
            self.reload()
        ssh_before = self.ssh_store.undo_label, self.ssh_store._undo_text, self.ssh_store._undo_expected
        ssh_done = False
        try:
            if ssh_change is not None:
                text_before = self.ssh_store._read_text()
                ssh_change()
                ssh_done = self.ssh_store._read_text() != text_before
            if aws_change is not None:
                aws_change[0](*aws_change[1:])
        except (ConfigError, SessionError, OSError) as exc:
            if ssh_done:
                try:
                    self.ssh_store.undo()
                except (SessionError, OSError):
                    pass
                (self.ssh_store.undo_label, self.ssh_store._undo_text,
                 self.ssh_store._undo_expected) = ssh_before
            QMessageBox.warning(self, "Could not save the change", str(exc))
            self.reload()
            return False
        parts = set()
        if ssh_done:
            parts.add("ssh")
        if aws_change is not None:
            parts.add("aws")
        if parts:
            self._undo_parts, self._undo_label = parts, label
        self._watch()
        self.rebuild()
        self._update_left_view()
        self._sync_undo()
        return True

    def _ssh_write(self, label: str, fn, *args) -> bool:
        return self._change_both(label, ssh_change=lambda: fn(*args))

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
        if self._change_both(f"Rename folder {leaf_name(path)} to {name}",
                             lambda: self.ssh_store.remap_groups(path, new),
                             (self.store.rename_group, path, new)):
            self._remap_expanded(path, new)
            self.rebuild()

    def delete_group(self, path: str):
        count = len(self.store.profiles_in(path))
        ssh_count = sum(1 for s in self.ssh_store.all() if is_within(s.group, path))
        parent = parent_group(path)
        dest = f"'{parent}'" if parent else "their automatic groups"
        msg = f"Delete group '{path}'?"
        if count:
            msg += f"\n\nIts {count} profile(s) and subgroups move to {dest}."
        if ssh_count:
            msg += (f"\n\nIts {ssh_count} SSH session(s) move to "
                    f"{repr(parent) if parent else 'SSH sessions'}.")
        if QMessageBox.question(self, "Delete group", msg) != QMessageBox.StandardButton.Yes:
            return
        if self._change_both(f"Delete folder {leaf_name(path)}",
                             lambda: self.ssh_store.remap_groups(path, parent),
                             (self.store.delete_group, path)):
            self._remap_expanded(path, parent)

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


    # ---- profile management ---------------------------------------------

    def _current_profile(self) -> Profile | None:
        profiles = self.selected_profiles()
        return profiles[0] if len(profiles) == 1 else None

    def _is_production(self, profile: Profile) -> bool:
        return self._profile_color(profile) == PROD_COLOR

    def _confirm_production(self, profiles: list[Profile], verb: str) -> bool:
        """Second confirmation for production profiles: type the name (or the count)."""
        prod = [p for p in profiles if self._is_production(p)]
        if not prod:
            return True
        expected = prod[0].name if len(prod) == 1 else f"{verb} {len(prod)}"
        names = ", ".join(p.name for p in prod[:6]) + (" ..." if len(prod) > 6 else "")
        text, ok = QInputDialog.getText(
            self, "Production profile",
            f"{names} {'is a production profile' if len(prod) == 1 else 'are production profiles'}."
            f"\n\nType {expected} to confirm:")
        if not ok:
            return False
        if text.strip() != expected:
            QMessageBox.information(self, "Production profile", "The text didn't match. Nothing changed.")
            return False
        return True

    def _create_session_from_dialog(self) -> str | None:
        dlg = SsoSessionDialog(self.store, parent=self)
        if not dlg.exec():
            return None
        if self._write(self.store.create_sso_session, dlg.name, dlg.props):
            self.statusBar().showMessage(f"Created SSO session {dlg.name}.", 4000)
            return dlg.name
        return None

    def new_profile(self, group: str | None = None, kind: str | None = None,
                    sso_session: str | None = None):
        if kind is None and not self.store.sso_sessions:
            kind = KIND_ROLE if self.store.profiles else KIND_SSO
        dlg = ProfileDialog(self.store, group=group, kind=kind, sso_session=sso_session,
                            new_session=self._create_session_from_dialog, parent=self)
        if not dlg.exec():
            return
        if self._write(self.store.create_profile, dlg.name, dlg.props, dlg.group):
            if dlg.group:
                for anc in [dlg.group]:
                    self.expanded.add(item_key(KIND_GROUP, anc))
                self._save_expanded()
                self.rebuild()
            self._select_profile(dlg.name)
            self.statusBar().showMessage(f"Created profile {dlg.name}.", 4000)

    def _new_profile_here(self):
        it = self.tree.currentItem()
        group = None
        if it is not None:
            kind, value = it.data(0, ROLE)
            if kind == KIND_GROUP:
                group = value
            elif kind == KIND_PROFILE and value in self.store.profiles:
                group = self.store.profiles[value].group
        self.new_profile(group=group)

    def edit_selected_profile(self):
        prof = self._current_profile()
        if prof:
            self.edit_profile(prof)
            return
        sessions = self.selected_ssh()
        if len(sessions) == 1:
            self.edit_ssh(sessions[0])

    def edit_profile(self, prof: Profile):
        if "config" not in prof.sources:
            QMessageBox.information(
                self, "Edit profile",
                f"'{prof.name}' is only in {self.store.credentials_file}, which termSwitch "
                "doesn't change. Use `aws configure` to edit it, or move it to AWS SSO.")
            return
        dlg = ProfileDialog(self.store, profile=prof,
                            new_session=self._create_session_from_dialog, parent=self)
        if not dlg.exec() or not dlg.changes:
            return
        if self._write(self.store.update_profile, prof.name, dlg.changes):
            self.status.pop(prof.name, None)
            self.rebuild()
            self.statusBar().showMessage(f"Saved {prof.name}.", 4000)

    def rename_profile(self, prof: Profile):
        new, ok = QInputDialog.getText(self, "Rename profile", "New name:", text=prof.name)
        new = new.strip() if ok else ""
        if not new or new == prof.name:
            return
        refs = self.store.references_to(prof.name)
        update = True
        if refs:
            listed = ", ".join(refs[:8]) + (" ..." if len(refs) > 8 else "")
            box = QMessageBox(QMessageBox.Icon.Question, "Rename profile",
                              f"{len(refs)} profile(s) use '{prof.name}' as their "
                              f"source_profile:\n{listed}", parent=self)
            box.setInformativeText("Update them to use the new name?\n\nScripts and "
                                   "environment variables that use the old name aren't "
                                   "changed.")
            yes = box.addButton("Update them", QMessageBox.ButtonRole.YesRole)
            box.addButton("Leave them", QMessageBox.ButtonRole.NoRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(yes)
            box.exec()
            clicked = box.clickedButton()
            if clicked not in box.buttons() or box.buttonRole(clicked) == QMessageBox.ButtonRole.RejectRole:
                return
            update = clicked is yes
        if not self._confirm_production([prof], "rename"):
            return
        if self._write(self.store.rename_profile, prof.name, new, update):
            if prof.name in self.status:
                self.status[new] = self.status.pop(prof.name)
            n = self.terminals.mark_profile_gone(prof.name, "renamed")
            self._select_profile(new)
            note = f" {n} open tab(s) still use the old name." if n else ""
            self.statusBar().showMessage(f"Renamed {prof.name} to {new}.{note}", 6000)

    def duplicate_profile(self, prof: Profile):
        if "config" not in prof.sources:
            QMessageBox.information(self, "Duplicate profile",
                                    f"'{prof.name}' has no settings in the config to copy.")
            return
        suggestion = f"{prof.name}-copy"
        while suggestion in self.store.profiles:
            suggestion += "-copy"
        new, ok = QInputDialog.getText(self, "Duplicate profile",
                                       "Name for the copy (static keys aren't copied):",
                                       text=suggestion)
        new = new.strip() if ok else ""
        if not new:
            return
        if self._write(self.store.duplicate_profile, prof.name, new):
            self._select_profile(new)
            self.edit_profile(self.store.profiles[new])

    def delete_profiles(self, profiles: list[Profile]):
        if not profiles:
            return
        names = [p.name for p in profiles]
        listed = "\n".join(names[:12]) + ("\n..." if len(names) > 12 else "")
        msg = (f"Delete profile '{names[0]}'?" if len(names) == 1
               else f"Delete these {len(names)} profiles?\n\n{listed}")
        refs = sorted({r for n in names for r in self.store.references_to(n)} - set(names))
        if refs:
            msg += ("\n\nThese profiles use a deleted profile as their source_profile and "
                    "will stop working: " + ", ".join(refs))
        creds = [p.name for p in profiles if "credentials" in p.sources]
        if creds:
            msg += (f"\n\n{', '.join(creds)} also {'has' if len(creds) == 1 else 'have'} keys "
                    f"in {self.store.credentials_file.name}, which termSwitch doesn't change, "
                    "so they will still be listed.")
        msg += "\n\nYou can undo this from the Profiles menu."
        box = QMessageBox(QMessageBox.Icon.Warning, "Delete profile", msg, parent=self)
        delete = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not delete:
            return
        if not self._confirm_production(profiles, "delete"):
            return
        if self._write(self.store.delete_profiles, names):
            tabs = sum(self.terminals.mark_profile_gone(n, "deleted") for n in names
                       if n not in self.store.profiles)
            for n in names:
                if n not in self.store.profiles:
                    self.status.pop(n, None)
            note = f" {tabs} open tab(s) keep running." if tabs else ""
            self.statusBar().showMessage(f"Deleted {len(names)} profile(s).{note}", 6000)

    def _rename_current(self):
        it = self.tree.currentItem()
        if it is None:
            return
        kind, value = it.data(0, ROLE)
        if kind == KIND_GROUP:
            self.rename_group(value)
        elif kind == KIND_PROFILE and value in self.store.profiles:
            self.rename_profile(self.store.profiles[value])

    def _duplicate_current(self):
        prof = self._current_profile()
        if prof:
            self.duplicate_profile(prof)

    def _delete_current(self):
        profiles = self.selected_profiles()
        if profiles:
            self.delete_profiles(profiles)
            return
        it = self.tree.currentItem()
        if it is not None and it.data(0, ROLE)[0] == KIND_GROUP:
            self.delete_group(it.data(0, ROLE)[1])

    def _select_profile(self, name: str):
        for it in self._profile_items(name):
            self.tree.clearSelection()
            parent = it.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            # NoUpdate: don't let held modifier keys turn this into a toggle.
            self.tree.setCurrentItem(it, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
            it.setSelected(True)
            self.tree.scrollToItem(it)
            return

    def _can_undo(self) -> bool:
        parts = getattr(self, "_undo_parts", set())
        if not parts:
            return False
        return (("aws" not in parts or self.store.can_undo())
                and ("ssh" not in parts or self.ssh_store.can_undo()))

    def _sync_undo(self):
        can = self._can_undo()
        self.act_undo.setEnabled(can)
        self.act_undo.setText(f"Undo {self._undo_label}" if can else "Undo")

    def undo_config_change(self):
        """Undo the last change, to the AWS config, the SSH sessions file, or both.
        Undoing again redoes it."""
        if not self._can_undo():
            return
        parts, label = self._undo_parts, self._undo_label
        problems = [p for p in (
            self.store.undo_blocked() if "aws" in parts else None,
            self.ssh_store.undo_blocked() if "ssh" in parts else None) if p]
        if problems:
            QMessageBox.warning(self, "Undo", "\n\n".join(problems))
            return
        try:
            if "ssh" in parts:
                self.ssh_store.undo()
            if "aws" in parts:
                try:
                    self.store.undo()
                except (ConfigError, OSError):
                    if "ssh" in parts:
                        self.ssh_store.undo()          # put the SSH part back as it was
                    raise
        except (ConfigError, SessionError, OSError) as exc:
            QMessageBox.warning(self, "Undo", str(exc))
            self.reload()
            return
        self._undo_label = label[6:] if label.startswith("Undo: ") else f"Undo: {label}"
        self.reload()
        self._sync_undo()
        self.statusBar().showMessage(f"Undid: {label}", 5000)

    # ---- SSO sessions and setup ------------------------------------------

    def setup_sso(self):
        if self.store.sso_sessions:
            names = sorted(self.store.sso_sessions, key=natural_key)
            choice, ok = QInputDialog.getItem(
                self, "Set up AWS SSO", "Use an existing SSO session, or create a new one:",
                names + ["New SSO session..."], 0, False)
            if not ok:
                return
            session = None if choice == "New SSO session..." else choice
        else:
            session = None
        if session is None:
            session = self._create_session_from_dialog()
            if not session:
                return
        box = QMessageBox(QMessageBox.Icon.Question, "Set up AWS SSO",
                          f"SSO session '{session}' is ready. How do you want to add profiles?",
                          parent=self)
        box.setInformativeText(
            "Sign in and pick accounts: the AWS CLI opens your browser to sign in, then "
            "lists the accounts and roles you can use. termSwitch picks up each profile "
            "it creates.\n\nAdd a profile by hand: enter an account ID and role name.")
        signin = box.addButton("Sign in and pick accounts", QMessageBox.ButtonRole.AcceptRole)
        manual = box.addButton("Add a profile by hand", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is signin:
            self.add_profiles_from_sso(session)
        elif box.clickedButton() is manual:
            self.new_profile(kind=KIND_SSO, sso_session=session)

    def add_profiles_from_sso(self, session: str | None = None):
        """Run `aws configure sso` in a tab. The AWS CLI does the sign-in and the
        account and role listing itself, so termSwitch never handles the SSO token."""
        if session is None and self.store.sso_sessions:
            names = sorted(self.store.sso_sessions, key=natural_key)
            session, ok = QInputDialog.getItem(self, "Add profiles from AWS SSO",
                                               "SSO session:", names, 0, False)
            if not ok:
                return
        hint = (f"At the first prompt, enter the SSO session name <b>{html.escape(session)}</b>. "
                if session else "")
        QMessageBox.information(
            self, "Add profiles from AWS SSO",
            f"<p>The AWS CLI will run <code>aws configure sso</code> in a new tab.</p>"
            f"<p>{hint}Sign in when your browser opens, then pick an account and a role, "
            "and give the profile a name. Run it again for each account and role you "
            "want.</p><p>New profiles appear in the tree as soon as the CLI saves them.</p>")
        try:
            argv, env = aws_setup_command(["configure", "sso"])
        except LaunchError as exc:
            QMessageBox.warning(self, "Add profiles from AWS SSO", str(exc))
            return
        self.terminals.open_command("aws configure sso", argv, env,
                                    tooltip="Adds SSO profiles to " + str(self.store.config_file))

    def manage_sso_sessions(self):
        def edit(name: str):
            dlg = SsoSessionDialog(self.store, name=name, parent=self)
            if dlg.exec() and dlg.changes:
                self._write(self.store.update_sso_session, name, dlg.changes)

        def delete(name: str):
            self._write(self.store.delete_sso_session, name)

        SsoSessionsDialog(self.store, self._create_session_from_dialog, edit, delete,
                          parent=self).exec()

    # ---- the whole config file -------------------------------------------

    def _update_left_view(self):
        empty = not self.store.profiles and not self.ssh_store.all()
        if empty:
            self.welcome.update_state(self.store.config_file, bool(find_aws()))
        self.left_stack.setCurrentWidget(self.welcome if empty else self.left_stack.widget(0))
        self.act_use_default_config.setEnabled(
            bool(self.settings.value("config_file", "", type=str)))

    def edit_config_in_app(self):
        if self.store.contains_secrets():
            QMessageBox.information(self, "Edit config file", SECRETS_REFUSED)
            return
        dlg = ConfigEditorDialog(self.store, parent=self)
        if dlg.exec():
            if self._write(self.store.save_raw, dlg.text()):
                self.statusBar().showMessage("Saved the config file.", 4000)

    def show_backups(self):
        dlg = BackupsDialog(self.store, parent=self)
        if dlg.exec() and dlg.selected:
            if self._write(self.store.restore_backup, dlg.selected):
                self.statusBar().showMessage("Restored the backup.", 5000)

    def reset_config(self):
        path = self.store.config_file
        if not path.exists():
            QMessageBox.information(self, "Reset config", f"{path} doesn't exist yet.")
            return
        box = QMessageBox(QMessageBox.Icon.Warning, "Reset config",
                          f"Move {path} aside and start with no profiles?", parent=self)
        box.setInformativeText("The file is kept as a backup next to it, and you can "
                               "restore it from File > Backups or undo right away. The AWS "
                               "CLI won't find any profiles until you set new ones up.")
        reset = box.addButton("Reset", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not reset:
            return
        try:
            dest = self.store.reset_config()
        except (ConfigError, OSError) as exc:
            QMessageBox.warning(self, "Reset config", str(exc))
            return
        self._undo_parts, self._undo_label = {"aws"}, self.store.undo_label
        self.status.clear()
        self.rebuild()
        self._update_left_view()
        self._sync_undo()
        self.statusBar().showMessage(f"Config moved to {dest}.", 8000)

    def choose_config_file(self):
        start = str(self.store.config_file.parent)
        path, _ = QFileDialog.getOpenFileName(self, "Use a config file", start,
                                              "All files (*)")
        if not path:
            return
        candidate = ProfileStore(Path(path), self.store.credentials_file)
        try:
            candidate.load()
        except ConfigError as exc:
            QMessageBox.warning(self, "Use a config file", str(exc))
            return
        self._switch_config_file(Path(path))

    def use_default_config_file(self):
        self.settings.remove("config_file")
        os.environ.pop("AWS_CONFIG_FILE", None)
        self._switch_config_file(None)

    def _switch_config_file(self, path: Path | None):
        """Point termSwitch, its status checks and new tabs at another config file.
        Open tabs keep the file they started with."""
        if path is not None:
            self.settings.setValue("config_file", str(path))
            os.environ["AWS_CONFIG_FILE"] = str(path)
        self.store.discard_undo()
        old = self.watcher.files() + self.watcher.directories()
        if old:
            self.watcher.removePaths(old)
        self.store = ProfileStore(credentials_file=self.store.credentials_file)
        self.status.clear()
        self.reload()
        self._sync_undo()


    # ---- SSH sessions ------------------------------------------------------

    def connect_ssh(self, session: SshSession):
        ssh = find_ssh()
        if not ssh:
            QMessageBox.warning(self, "SSH", "The OpenSSH client (ssh) was not found. Install "
                                "it, or on Windows add the OpenSSH Client optional feature.")
            return
        problem = key_file_problem(session.key_file) if session.auth == "key" else None
        if problem and "other users" in problem:
            QMessageBox.warning(self, "SSH key", problem + "\n\nssh refuses keys that other "
                                "users can read.")
        try:
            argv = ssh_argv(session, ssh)
        except SessionError as exc:
            QMessageBox.warning(self, "SSH", str(exc))
            return
        factory = None
        if (session.remember_password and not session.from_ssh_config
                and self.passwords.available):
            # The password is read from the store only when ssh actually asks.
            factory = self._autofill_factory(session)
        self.terminals.open_ssh(session, argv, ssh_env(), autofill_factory=factory,
                                tooltip=display_command(argv))

    def _autofill_factory(self, session: SshSession):
        sid = session.id

        def make(term):
            return PasswordAutofill(session, lambda: self.passwords.get(sid), term.write_secret)
        return make

    def new_ssh_session(self, group: str | None = None):
        dlg = SshSessionDialog(self.ssh_store, self.passwords, group=group,
                               folders=self.store.all_groups() | self.ssh_store.groups(),
                               parent=self)
        if dlg.exec():
            self._save_ssh(dlg)

    def edit_ssh(self, session: SshSession):
        if session.from_ssh_config:
            QMessageBox.information(
                self, "Edit SSH session",
                f"'{session.name}' comes from ~/.ssh/config, which termSwitch doesn't change. "
                "Edit that file, or use Duplicate to make a termSwitch session from it.")
            return
        dlg = SshSessionDialog(self.ssh_store, self.passwords, session=session,
                               folders=self.store.all_groups() | self.ssh_store.groups(),
                               parent=self)
        if dlg.exec():
            self._save_ssh(dlg)

    def _save_ssh(self, dlg: SshSessionDialog):
        session = dlg.session
        try:
            # Password store first: if it fails, nothing claims a password is saved.
            if dlg.password_action == PW_SET:
                self.passwords.set(session.id, dlg.take_password())
        except (SessionError, OSError) as exc:
            QMessageBox.warning(self, "SSH session", str(exc))
            return
        except Exception as exc:     # errors from the system password store
            QMessageBox.warning(self, "SSH session",
                                f"The {self.passwords.store_name()} refused the password: {exc}")
            return
        label = (f"Edit SSH session {session.name}" if session.id in self.ssh_store.sessions
                 else f"Create SSH session {session.name}")
        if not self._ssh_write(label, self.ssh_store.save, session):
            return
        if dlg.password_action == PW_DELETE:
            self.passwords.delete(session.id)
        if session.group:
            from .config_store import ancestors
            for anc in ancestors(session.group):
                self.expanded.add(item_key(KIND_GROUP, anc))
        self.expanded.add(item_key(KIND_SSH_ROOT, ""))
        self._save_expanded()
        self.rebuild()
        self._select_ssh(session.id)
        self.statusBar().showMessage(f"Saved SSH session {session.name}.", 4000)

    def duplicate_ssh(self, session: SshSession):
        name, ok = QInputDialog.getText(self, "Duplicate SSH session", "Name for the copy:",
                                        text=f"{session.name}-copy")
        name = name.strip() if ok else ""
        if not name:
            return
        made = []
        if not self._ssh_write(f"Duplicate SSH session {session.name}",
                               lambda: made.append(self.ssh_store.duplicate(session, name))):
            return
        copy = made[0]
        self._select_ssh(copy.id)
        self.edit_ssh(copy)

    def delete_ssh(self, sessions: list[SshSession]):
        own = [s for s in sessions if not s.from_ssh_config]
        if not own:
            QMessageBox.information(self, "Delete SSH session",
                                    "Hosts from ~/.ssh/config can only be removed in that file.")
            return
        names = "\n".join(s.name for s in own[:12]) + ("\n..." if len(own) > 12 else "")
        box = QMessageBox(QMessageBox.Icon.Warning, "Delete SSH session",
                          f"Delete {'this SSH session' if len(own) == 1 else f'these {len(own)} SSH sessions'}?"
                          f"\n\n{names}", parent=self)
        box.setInformativeText("Saved passwords for them are removed from the "
                               f"{self.passwords.store_name()} too.")
        delete = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not delete:
            return
        label = (f"Delete SSH session {own[0].name}" if len(own) == 1
                 else f"Delete {len(own)} SSH sessions")
        if self._ssh_write(label, self.ssh_store.delete_many, [s.id for s in own]):
            # Saved passwords go now. Undo brings a session back, but not its password,
            # so it asks for the password in the tab until one is saved again.
            for s in own:
                self.passwords.delete(s.id)

    def forget_ssh_password(self, session: SshSession):
        from dataclasses import replace as _replace
        self.passwords.delete(session.id)
        self._ssh_write(f"Forget password for {session.name}", self.ssh_store.save,
                        _replace(session, remember_password=False))
        self.statusBar().showMessage(f"Forgot the saved password for {session.name}.", 4000)

    def _select_ssh(self, session_id: str):
        for it in self._iter_items():
            if it.data(0, ROLE) == (KIND_SSH, session_id):
                self.tree.clearSelection()
                parent = it.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self.tree.setCurrentItem(it, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
                it.setSelected(True)
                self.tree.scrollToItem(it)
                return

    def _ssh_menu(self, menu: QMenu, sessions: list[SshSession]):
        label = "Connect" if len(sessions) == 1 else f"Connect {len(sessions)} sessions"
        menu.addAction(label, lambda: [self.connect_ssh(s) for s in sessions])
        menu.addSeparator()
        if len(sessions) == 1:
            s = sessions[0]
            menu.addAction("Edit...", lambda: self.edit_ssh(s))
            menu.addAction("Duplicate...", lambda: self.duplicate_ssh(s))
            ssh = find_ssh() or "ssh"
            try:
                command = display_command(ssh_argv(s, ssh))
                menu.addAction("Copy ssh command",
                               lambda: QApplication.clipboard().setText(command))
            except SessionError:
                pass
            if s.remember_password and not s.from_ssh_config:
                menu.addAction("Forget saved password", lambda: self.forget_ssh_password(s))
        if any(not s.from_ssh_config for s in sessions):
            menu.addAction("Delete..." if len(sessions) == 1 else
                           f"Delete {len(sessions)} sessions...", lambda: self.delete_ssh(sessions))

    def _ssh_html(self, s: SshSession) -> str:
        hint = theme.colors().hint
        auth = {"password": "Password (asked in the tab)", "key": "Key file",
                "agent": "SSH agent or ~/.ssh defaults"}.get(s.auth, s.auth)
        if s.auth == "password" and s.remember_password:
            auth = f"Password (saved in the {self.passwords.store_name()})"
        rows = [("Host", s.host), ("Port", str(s.port)), ("User", s.user or "(default)"),
                ("Sign in", auth), ("Key file", s.key_file), ("Certificate", s.cert_file),
                ("Jump host", s.jump), ("Group", s.display_group)]
        rows += [(k, v) for k, v in sorted(s.options.items())]
        if s.from_ssh_config:
            rows.append(("Source", "~/.ssh/config (read-only)"))
        problem = key_file_problem(s.key_file) if s.auth == "key" else None
        table = "".join(
            f"<tr><td style='color:{hint};padding-right:12px'>{html.escape(k)}</td>"
            f"<td>{html.escape(v)}</td></tr>" for k, v in rows if v)
        warn = (f"<p style='color:{theme.colors().warning}'>{html.escape(problem)}</p>"
                if problem else "")
        return (f"<h2>{html.escape(s.name)}</h2><table cellspacing=2>{table}</table>{warn}")

    # ---- context menu ----------------------------------------------------

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            self.tree.clearSelection()
            menu.addAction("New profile...", lambda: self.new_profile())
            menu.addAction("New SSH session...", lambda: self.new_ssh_session())
            menu.addAction("New group...", lambda: self.new_group(None))
            menu.addAction("Check all", self.check_all)
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
            self.tree.setCurrentItem(item)
        kind, value = item.data(0, ROLE)

        if kind == KIND_SSH:
            self._ssh_menu(menu, self.selected_ssh())
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return
        if kind in (KIND_SSH_ROOT, KIND_SSH_GROUP):
            under = self.ssh_under(item)
            if under:
                menu.addAction(f"Connect all {len(under)}",
                               lambda: [self.connect_ssh(s) for s in under])
                menu.addSeparator()
            menu.addAction("New SSH session...", lambda: self.new_ssh_session(None))
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return

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
            if len(profiles) == 1:
                prof = profiles[0]
                menu.addAction("Edit...", lambda: self.edit_profile(prof))
                menu.addAction("Rename...", lambda: self.rename_profile(prof))
                menu.addAction("Duplicate...", lambda: self.duplicate_profile(prof))
            menu.addAction("Delete..." if len(profiles) == 1 else f"Delete {len(profiles)} profiles...",
                           lambda: self.delete_profiles(profiles))
            menu.addSeparator()
            colors_menu = menu.addMenu("Terminal colors")
            current = {p.term_scheme for p in profiles}
            default = colors_menu.addAction("Default", lambda: self.set_profile_scheme(profiles, None))
            default.setCheckable(True)
            default.setChecked(current == {None})
            colors_menu.addSeparator()
            for name in sorted(self.schemes, key=lambda n: (not self.schemes[n].builtin, n.lower())):
                act = colors_menu.addAction(name, lambda n=name: self.set_profile_scheme(profiles, n))
                act.setCheckable(True)
                act.setChecked(current == {name})
            menu.addAction("Set tab color...", lambda: self.set_color(profiles))
            if any(p.color for p in profiles):
                menu.addAction("Clear tab color", lambda: self.set_color(profiles, clear=True))
            if any(p.group for p in profiles):
                menu.addAction("Remove from group", lambda: self.remove_from_group(profiles))
        elif kind == KIND_GROUP:
            under = self.profiles_under(item)
            ssh_under = self.ssh_under(item)
            total = len(under) + len(ssh_under)
            if total:
                menu.addAction(f"Open all {total} terminals",
                               lambda: (self.open_profiles(under) if under else None,
                                        [self.connect_ssh(s) for s in ssh_under]))
            if under:
                menu.addAction("Check status of group", lambda: self.check_profiles(under))
                if any(p.is_sso for p in under):
                    menu.addAction("SSO login for group", lambda: self.login_profiles(under))
                menu.addSeparator()
            menu.addAction("New profile here...", lambda: self.new_profile(group=value))
            menu.addAction("New SSH session here...", lambda: self.new_ssh_session(value))
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
            menu.addAction("New profile...", lambda: self.new_profile())
            menu.addAction("New group...", lambda: self.new_group(None))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ---- details pane ----------------------------------------------------

    def _show_details(self):
        hint = theme.colors().hint
        items = self.tree.selectedItems()
        if len(items) == 1 and items[0].data(0, ROLE)[0] == KIND_SSH:
            s = self.ssh_store.get(items[0].data(0, ROLE)[1])
            self.btn_open.setEnabled(s is not None)
            self.btn_edit.setEnabled(s is not None and not s.from_ssh_config)
            for b in (self.btn_check, self.btn_login):
                b.setEnabled(False)
            if s is not None:
                self.details.setHtml(self._ssh_html(s))
            return
        items = self.tree.selectedItems()
        profiles = self.selected_profiles()
        self.btn_open.setEnabled(bool(profiles))
        self.btn_check.setEnabled(bool(profiles))
        self.btn_login.setEnabled(any(p.is_sso for p in profiles))
        self.btn_edit.setEnabled(len(profiles) == 1)

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
        if p.props.get("credential_process"):
            rows.append(("Note", "Runs a program (credential_process) to get credentials."))
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
            f"<tr><td style='color:{hint};padding-right:12px'>{k}</td>"
            f"<td>{cell(v, k in raw_keys)}</td></tr>" for k, v in rows if v)
        props = "".join(
            f"<tr><td style='color:{hint};padding-right:12px'><code>{html.escape(k)}</code></td>"
            f"<td><code>{html.escape(v)}</code></td></tr>" for k, v in p.display_props())
        sources = ", ".join(sorted(p.sources))
        color = self._display_color(p)
        title_style = f" style='color:{color}'" if color else ""
        self.details.setHtml(
            f"<h2{title_style}>{html.escape(p.name)}</h2>"
            f"<table cellspacing=2>{table}</table>"
            f"<h4>Raw settings <span style='color:{hint};font-weight:normal'>({sources})</span></h4>"
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
            "<li>Right-click for SSO login, status checks, tab colors and group actions, "
            "or to edit, rename, duplicate and delete profiles.</li>"
            "<li><b>New profile</b> in the toolbar (Ctrl+N in the tree) adds a profile to the "
            "selected group. Changes can be undone from the Profiles menu (Ctrl+Z in the "
            "tree).</li>"
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
        dlg = SettingsDialog(self.settings, self, schemes=self.schemes)
        if dlg.exec():
            self.schemes = all_schemes()
            self._apply_terminal_settings()
            self._sync_auto_group_actions()
            if get_setting(self.settings, "app_theme") != self.theme.mode:
                self.theme.apply(get_setting(self.settings, "app_theme"))   # rebuilds
            else:
                self.rebuild()
                self.terminals.apply_schemes(self.store.profiles)

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
        from .about import AboutDialog
        dlg = AboutDialog(
            self.store.config_file,
            open_url=lambda url: self._open_link(QUrl(url)),
            show_licenses=lambda: self.show_licenses(),
            check_online=get_setting(self.settings, "check_updates_online"),
            icon=self.windowIcon() if not self.windowIcon().isNull()
            else QApplication.windowIcon(),
            parent=self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.exec()

    def show_licenses(self, start: str | None = None):
        LicensesDialog(self, start=start).exec()

    def shutdown_background_work(self, timeout_ms: int = 5000):
        """Cancel queued jobs and wait briefly for running ones (catalog load,
        dependency and status checks). Python worker threads still running when
        Qt and the interpreter shut down can crash the app on quit."""
        from PySide6.QtCore import QThreadPool
        for timer in (self._reload_timer,):
            timer.stop()
        self.commands.shutdown()
        pools = (QThreadPool.globalInstance(), self.checker.pool)
        for pool in pools:
            pool.clear()
        for pool in pools:
            pool.waitForDone(timeout_ms)

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
        self.shutdown_background_work()
        self.store.discard_undo()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("main_splitter_v2", self.splitter.saveState())
        self.settings.setValue("commands_visible", self.commands.isVisible())
        self.settings.setValue("left_splitter", self.left_splitter.saveState())
        self._save_expanded()
        super().closeEvent(event)

