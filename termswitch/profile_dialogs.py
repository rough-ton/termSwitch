"""Dialogs for creating and editing profiles and SSO sessions.

The dialogs only collect and validate input. The main window performs the write
through ``ProfileStore`` so every change gets the usual backup, undo and reload.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from .config_store import (COLOR_KEY, GROUP_KEY, PROTECTED_KEYS, ConfigError, Profile,
                           ProfileStore, key_problem, natural_key, normalize_group,
                           profile_name_problem, scrub_value, sso_session_name_problem,
                           value_problem)

# Suggestions only; any valid region name is accepted.
REGIONS = [
    "us-east-1", "us-east-2", "us-west-1", "us-west-2", "ca-central-1", "ca-west-1",
    "sa-east-1", "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1", "eu-central-2",
    "eu-north-1", "eu-south-1", "eu-south-2", "me-south-1", "me-central-1", "il-central-1",
    "af-south-1", "ap-east-1", "ap-south-1", "ap-south-2", "ap-southeast-1",
    "ap-southeast-2", "ap-southeast-3", "ap-southeast-4", "ap-northeast-1",
    "ap-northeast-2", "ap-northeast-3", "us-gov-west-1", "us-gov-east-1",
]
OUTPUTS = ["", "json", "table", "text", "yaml", "yaml-stream"]
DEFAULT_SCOPES = "sso:account:access"

KIND_SSO, KIND_ROLE, KIND_CUSTOM = "sso", "role", "custom"
KIND_TITLES = {KIND_SSO: "AWS SSO (IAM Identity Center)", KIND_ROLE: "Assume a role",
               KIND_CUSTOM: "Custom"}
SSO_KEYS = ("sso_session", "sso_account_id", "sso_role_name")
ROLE_KEYS = ("role_arn", "source_profile", "external_id", "mfa_serial",
             "role_session_name", "duration_seconds")
COMMON_KEYS = ("region", "output")
HIDDEN_KEYS = {GROUP_KEY, COLOR_KEY}       # set elsewhere in the app
CREDENTIAL_PROCESS_NOTE = ("credential_process runs a program to get credentials every "
                           "time this profile is used. Only use programs you trust.")


def _combo(items, editable=True, current=""):
    box = QComboBox()
    box.setEditable(editable)
    box.addItems(items)
    box.setCurrentText(current)
    return box


def _text(widget) -> str:
    return (widget.currentText() if isinstance(widget, QComboBox) else widget.text()).strip()


def profile_kind(profile: Profile | None) -> str:
    if profile is None:
        return KIND_SSO
    if profile.props.get("sso_session"):
        return KIND_SSO
    if profile.props.get("role_arn") and not profile.props.get("sso_start_url"):
        return KIND_ROLE
    return KIND_CUSTOM


class ProfileDialog(QDialog):
    """New or edit. After ``exec()`` returns Accepted:

    - new: ``name``, ``props`` and ``group``
    - edit: ``changes``, only the keys that changed (None removes a key)
    """

    def __init__(self, store: ProfileStore, profile: Profile | None = None,
                 group: str | None = None, kind: str | None = None,
                 sso_session: str | None = None,
                 new_session: Callable[[], str | None] | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self.profile = profile
        self.editing = profile is not None
        self._new_session = new_session
        self.name = profile.name if profile else ""
        self.props: dict[str, str] = {}
        self.group: str | None = None
        self.changes: dict[str, str | None] = {}
        initial = dict(profile.props) if profile else {}
        self._initial = initial
        self.kind = profile_kind(profile) if self.editing else (kind or KIND_SSO)
        self.setWindowTitle(f"Edit profile {profile.name}" if self.editing else "New profile")
        self.setMinimumWidth(520)

        form = QFormLayout()
        self.name_edit = QLineEdit(self.name)
        self.name_edit.setPlaceholderText("for example team-dev-admin")
        if self.editing:
            self.name_edit.setReadOnly(True)
            self.name_edit.setToolTip("Use Rename to change the name")
        form.addRow("Name:", self.name_edit)
        self.kind_box = QComboBox()
        for k in (KIND_SSO, KIND_ROLE, KIND_CUSTOM):
            self.kind_box.addItem(KIND_TITLES[k], k)
        self.kind_box.setCurrentIndex(self.kind_box.findData(self.kind))
        self.kind_box.setEnabled(not self.editing)
        form.addRow("Type:", self.kind_box)

        # --- SSO page
        sso_page = QWidget()
        sf = QFormLayout(sso_page)
        sf.setContentsMargins(0, 0, 0, 0)
        sessions = sorted(store.sso_sessions, key=natural_key)
        self.sso_session = _combo(sessions, editable=False)
        if sso_session or initial.get("sso_session"):
            self.sso_session.setCurrentText(sso_session or initial.get("sso_session"))
        session_row = QHBoxLayout()
        session_row.addWidget(self.sso_session, 1)
        if new_session:
            btn = QPushButton("New...")
            btn.clicked.connect(self._add_session)
            session_row.addWidget(btn)
        sf.addRow("SSO session:", session_row)
        self.sso_account = QLineEdit(initial.get("sso_account_id", ""))
        self.sso_account.setPlaceholderText("12-digit account ID")
        sf.addRow("Account ID:", self.sso_account)
        self.sso_role = QLineEdit(initial.get("sso_role_name", ""))
        self.sso_role.setPlaceholderText("for example AdministratorAccess")
        sf.addRow("Role name:", self.sso_role)
        hint = QLabel("Not sure of the account ID or role? Use File > Set up AWS SSO > "
                      "Sign in and pick accounts, which lets the AWS CLI list them.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8a8f98;")
        sf.addRow(hint)

        # --- Role page
        role_page = QWidget()
        rf = QFormLayout(role_page)
        rf.setContentsMargins(0, 0, 0, 0)
        self.role_arn = QLineEdit(initial.get("role_arn", ""))
        self.role_arn.setPlaceholderText("arn:aws:iam::123456789012:role/Name")
        rf.addRow("Role ARN:", self.role_arn)
        others = sorted((n for n in store.profiles if n != self.name), key=natural_key)
        self.source = _combo([""] + others, current=initial.get("source_profile", ""))
        rf.addRow("Source profile:", self.source)
        self.external_id = QLineEdit(initial.get("external_id", ""))
        rf.addRow("External ID:", self.external_id)
        self.mfa = QLineEdit(initial.get("mfa_serial", ""))
        self.mfa.setPlaceholderText("optional MFA device ARN")
        rf.addRow("MFA serial:", self.mfa)
        self.session_name = QLineEdit(initial.get("role_session_name", ""))
        rf.addRow("Session name:", self.session_name)
        self.duration = QLineEdit(initial.get("duration_seconds", ""))
        self.duration.setPlaceholderText("900 to 43200")
        rf.addRow("Duration (s):", self.duration)

        self.pages = QStackedWidget()
        self.pages.addWidget(sso_page)
        self.pages.addWidget(role_page)
        self.pages.addWidget(QWidget())
        self._page_index = {KIND_SSO: 0, KIND_ROLE: 1, KIND_CUSTOM: 2}
        self.kind_box.currentIndexChanged.connect(self._kind_changed)

        common = QFormLayout()
        self.region = _combo([""] + REGIONS, current=initial.get("region", ""))
        common.addRow("Region:", self.region)
        self.output = _combo(OUTPUTS, editable=False, current=initial.get("output", ""))
        common.addRow("Output:", self.output)
        groups = sorted(store.all_groups(), key=natural_key)
        start_group = profile.group if profile else group
        self.group_box = _combo([""] + groups, current=start_group or "")
        self.group_box.setToolTip("Use / for nested groups, for example Team/Prod")
        common.addRow("Group:", self.group_box)

        # --- Advanced: every other key
        adv = QGroupBox("Other settings")
        av = QVBoxLayout(adv)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Setting", "Value"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.itemChanged.connect(lambda _i: self._update_warning())
        av.addWidget(self.table)
        row = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(self._add_row)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_row)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        av.addLayout(row)
        self.warning = QLabel(CREDENTIAL_PROCESS_NOTE)
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #b26a00;")
        av.addWidget(self.warning)
        self._fill_table()

        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #d93025;")
        self.error.hide()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.pages)
        lay.addLayout(common)
        lay.addWidget(adv, 1)
        lay.addWidget(self.error)
        lay.addWidget(buttons)
        self._kind_changed()
        if not self.editing:
            self.name_edit.setFocus()

    # ---- layout helpers ---------------------------------------------------

    def _form_keys(self) -> set[str]:
        keys = set(COMMON_KEYS) | HIDDEN_KEYS
        if self.kind == KIND_SSO:
            keys |= set(SSO_KEYS)
        elif self.kind == KIND_ROLE:
            keys |= set(ROLE_KEYS)
        return keys

    def _kind_changed(self, *_):
        from PySide6.QtWidgets import QSizePolicy
        self.kind = self.kind_box.currentData()
        current = self._page_index[self.kind]
        # Size the stack to the visible page only, so short pages leave no gap.
        for i in range(self.pages.count()):
            policy = (QSizePolicy.Policy.Preferred if i == current
                      else QSizePolicy.Policy.Ignored)
            self.pages.widget(i).setSizePolicy(policy, policy)
        self.pages.setCurrentIndex(current)
        self.pages.adjustSize()
        if not self.editing:
            self._fill_table()

    def _fill_table(self):
        """Other settings, minus the ones the form shows. Static keys and nested
        settings are listed read-only: termSwitch never writes them."""
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        shown = self._form_keys()
        for key in sorted(self._initial):
            if key in shown:
                continue
            value = self._initial[key]
            readonly = key.lower() in PROTECTED_KEYS or "\n" in value
            display = scrub_value(key, value) if key.lower() in PROTECTED_KEYS else value
            if "\n" in value:
                display = "(nested settings; edit in the config file)"
            self._append(key, display, readonly)
        self.table.blockSignals(False)
        self._update_warning()

    def _append(self, key: str, value: str, readonly: bool = False):
        r = self.table.rowCount()
        self.table.insertRow(r)
        k, v = QTableWidgetItem(key), QTableWidgetItem(value)
        if readonly:
            for it in (k, v):
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                it.setForeground(Qt.GlobalColor.gray)
                it.setToolTip("termSwitch doesn't change this setting")
            k.setData(Qt.ItemDataRole.UserRole, "readonly")
        self.table.setItem(r, 0, k)
        self.table.setItem(r, 1, v)

    def _add_row(self):
        self._append("", "")
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 0))

    def _remove_row(self):
        r = self.table.currentRow()
        if r < 0:
            return
        if self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == "readonly":
            self._show_error("termSwitch doesn't remove static keys or nested settings. "
                             "Edit the config file for those.")
            return
        self.table.removeRow(r)
        self._update_warning()

    def _update_warning(self):
        keys = {(self.table.item(r, 0).text().strip() if self.table.item(r, 0) else "")
                for r in range(self.table.rowCount())}
        self.warning.setVisible("credential_process" in keys)

    def _add_session(self):
        name = self._new_session() if self._new_session else None
        if name:
            if self.sso_session.findText(name) < 0:
                self.sso_session.addItem(name)
            self.sso_session.setCurrentText(name)

    def _show_error(self, text: str):
        self.error.setText(text)
        self.error.setVisible(bool(text))

    # ---- result -----------------------------------------------------------

    def _form_values(self) -> dict[str, str]:
        values = {"region": _text(self.region), "output": _text(self.output)}
        if self.kind == KIND_SSO:
            values.update(sso_session=_text(self.sso_session),
                          sso_account_id=_text(self.sso_account),
                          sso_role_name=_text(self.sso_role))
        elif self.kind == KIND_ROLE:
            values.update(role_arn=_text(self.role_arn), source_profile=_text(self.source),
                          external_id=_text(self.external_id), mfa_serial=_text(self.mfa),
                          role_session_name=_text(self.session_name),
                          duration_seconds=_text(self.duration))
        return values

    def _table_values(self) -> tuple[dict[str, str], set[str]]:
        """(editable settings, read-only keys) from the Other settings table."""
        values, readonly = {}, set()
        for r in range(self.table.rowCount()):
            k_item, v_item = self.table.item(r, 0), self.table.item(r, 1)
            key = k_item.text().strip() if k_item else ""
            if k_item and k_item.data(Qt.ItemDataRole.UserRole) == "readonly":
                readonly.add(key)
                continue
            value = v_item.text().strip() if v_item else ""
            if not key and not value:
                continue
            if key in values or key in self._form_keys():
                raise ConfigError(f"'{key}' is listed twice.")
            values[key] = value
        return values, readonly

    def collect(self) -> None:
        """Validate the input and fill ``name``/``props``/``group`` or ``changes``.
        Raises ConfigError with a message for the user."""
        name = self.name_edit.text()
        if not self.editing:
            problem = profile_name_problem(name)
            if problem:
                raise ConfigError(problem)
            if name in self.store.profiles:
                raise ConfigError(f"A profile named '{name}' already exists.")
        form = self._form_values()
        table, readonly = self._table_values()
        if self.kind == KIND_SSO:
            missing = [label for key, label in (("sso_session", "an SSO session"),
                                                ("sso_account_id", "an account ID"),
                                                ("sso_role_name", "a role name"))
                       if not form[key]]
            if missing:
                raise ConfigError("An SSO profile needs " + ", ".join(missing) + ".")
        if self.kind == KIND_ROLE and not form["role_arn"]:
            raise ConfigError("An assume-role profile needs a role ARN.")
        if self.kind == KIND_ROLE and form["source_profile"] == name:
            raise ConfigError("A profile can't be its own source profile.")
        merged = {**{k: v for k, v in form.items() if v}, **table}
        for key, value in merged.items():
            problem = key_problem(key) or value_problem(key, value)
            if problem:
                raise ConfigError(problem)
        group = normalize_group(_text(self.group_box))

        if not self.editing:
            self.name, self.props, self.group = name, merged, group
            return
        changes: dict[str, str | None] = {}
        for key in set(self._initial) | set(merged):
            if key in HIDDEN_KEYS or key in readonly or key.lower() in PROTECTED_KEYS:
                continue
            if "\n" in self._initial.get(key, ""):
                continue
            new = merged.get(key)
            if new != self._initial.get(key):
                changes[key] = new
        if group != (self.profile.group if self.profile else None):
            changes[GROUP_KEY] = group
        self.changes = changes

    def _accept(self):
        try:
            self.collect()
        except ConfigError as exc:
            self._show_error(str(exc))
            return
        self.accept()


class SsoSessionDialog(QDialog):
    """New or edit an ``[sso-session]``. Results in ``name`` and ``props`` (new) or
    ``changes`` (edit)."""

    def __init__(self, store: ProfileStore, name: str | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self.editing = name is not None
        initial = dict(store.sso_sessions.get(name, {})) if name else {}
        self._initial = initial
        self.name = name or ""
        self.props: dict[str, str] = {}
        self.changes: dict[str, str | None] = {}
        self.setWindowTitle(f"Edit SSO session {name}" if name else "New SSO session")
        self.setMinimumWidth(480)

        intro = QLabel("An SSO session holds your organization's AWS access portal. "
                       "Profiles that use it share one sign-in. Find the start URL in "
                       "your access portal or ask your AWS administrator.")
        intro.setWordWrap(True)
        form = QFormLayout()
        self.name_edit = QLineEdit(self.name)
        self.name_edit.setPlaceholderText("for example my-company")
        self.name_edit.setReadOnly(self.editing)
        form.addRow("Session name:", self.name_edit)
        self.url = QLineEdit(initial.get("sso_start_url", ""))
        self.url.setPlaceholderText("https://my-company.awsapps.com/start")
        form.addRow("Start URL:", self.url)
        self.region = _combo(REGIONS, current=initial.get("sso_region", "us-east-1"))
        self.region.setToolTip("The region where IAM Identity Center is set up")
        form.addRow("SSO region:", self.region)
        self.scopes = QLineEdit(initial.get("sso_registration_scopes",
                                            "" if self.editing else DEFAULT_SCOPES))
        form.addRow("Scopes:", self.scopes)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #d93025;")
        self.error.hide()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(self.error)
        lay.addWidget(buttons)

    def collect(self) -> None:
        name = self.name_edit.text().strip()
        if not self.editing:
            problem = sso_session_name_problem(name)
            if problem:
                raise ConfigError(problem)
            if name in self.store.sso_sessions:
                raise ConfigError(f"An SSO session named '{name}' already exists.")
        values = {"sso_start_url": self.url.text().strip(),
                  "sso_region": _text(self.region),
                  "sso_registration_scopes": self.scopes.text().strip()}
        for key in ("sso_start_url", "sso_region"):
            if not values[key]:
                raise ConfigError("A start URL and an SSO region are required.")
        for key, value in values.items():
            if value:
                problem = value_problem(key, value)
                if problem:
                    raise ConfigError(problem)
        if not self.editing:
            self.name = name
            self.props = {k: v for k, v in values.items() if v}
            return
        self.changes = {k: (v or None) for k, v in values.items()
                        if (v or None) != self._initial.get(k)}

    def _accept(self):
        try:
            self.collect()
        except ConfigError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        self.accept()


class SsoSessionsDialog(QDialog):
    """List of SSO sessions with New, Edit and Delete. The callbacks perform the
    writes, so each change is saved (and undoable) on its own."""

    def __init__(self, store: ProfileStore, on_new: Callable[[], str | None],
                 on_edit: Callable[[str], None], on_delete: Callable[[str], None],
                 parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("SSO sessions")
        self.setMinimumSize(520, 320)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._edit())
        self._on_new, self._on_edit, self._on_delete = on_new, on_edit, on_delete
        btn_new = QPushButton("New...")
        btn_new.clicked.connect(lambda: (self._on_new(), self.refresh()))
        self.btn_edit = QPushButton("Edit...")
        self.btn_edit.clicked.connect(self._edit)
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.clicked.connect(self._delete)
        side = QVBoxLayout()
        for b in (btn_new, self.btn_edit, self.btn_delete):
            side.addWidget(b)
        side.addStretch(1)
        body = QHBoxLayout()
        body.addWidget(self.list, 1)
        body.addLayout(side)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(body)
        lay.addWidget(close)
        self.list.currentItemChanged.connect(lambda *_: self._sync())
        self.refresh()

    def refresh(self):
        current = self.current()
        self.list.clear()
        for name in sorted(self.store.sso_sessions, key=natural_key):
            props = self.store.sso_sessions[name]
            users = self.store.sso_session_users(name)
            item = QListWidgetItem(f"{name}    {props.get('sso_start_url', '')}    "
                                   f"({len(users)} profile{'s' if len(users) != 1 else ''})")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(item)
            if name == current:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self._sync()

    def current(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _sync(self):
        has = self.current() is not None
        self.btn_edit.setEnabled(has)
        self.btn_delete.setEnabled(has)

    def _edit(self):
        name = self.current()
        if name:
            self._on_edit(name)
            self.refresh()

    def _delete(self):
        name = self.current()
        if not name:
            return
        users = self.store.sso_session_users(name)
        msg = f"Delete SSO session '{name}'?"
        if users:
            listed = ", ".join(users[:8]) + (" ..." if len(users) > 8 else "")
            msg += (f"\n\n{len(users)} profile(s) use it and will stop working until they "
                    f"point at another session:\n{listed}")
        if QMessageBox.question(self, "Delete SSO session", msg) != QMessageBox.StandardButton.Yes:
            return
        self._on_delete(name)
        self.refresh()
