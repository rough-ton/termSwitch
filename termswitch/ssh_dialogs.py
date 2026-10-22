"""Editor for saved SSH sessions."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSpinBox, QVBoxLayout)

from . import theme
from .config_store import natural_key, normalize_group
from .ssh_sessions import (AUTH_AGENT, AUTH_KEY, AUTH_METHODS, AUTH_PASSWORD, PasswordStore,
                           SessionError, SessionStore, SshSession, key_file_problem, validate)

KEEP, SET, DELETE = "keep", "set", "delete"


class SshSessionDialog(QDialog):
    """New or edit. After Accepted: ``session`` is the validated session, and
    ``password_action`` is KEEP, SET (with ``password``) or DELETE for the system
    password store. The caller saves both."""

    def __init__(self, store: SessionStore, passwords: PasswordStore,
                 session: SshSession | None = None, group: str | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self.passwords = passwords
        self.original = session
        self.editing = session is not None
        self.session: SshSession | None = None
        self.password_action = KEEP
        self.password = ""
        s = session or SshSession(name="", host="", group=group)
        self.setWindowTitle(f"Edit SSH session {s.name}" if self.editing else "New SSH session")
        self.setMinimumWidth(540)

        form = QFormLayout()
        self.name = QLineEdit(s.name)
        self.name.setPlaceholderText("for example web-01")
        form.addRow("Name:", self.name)
        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.addItems([""] + sorted(store.groups(), key=natural_key))
        self.group.setCurrentText(s.group or "")
        self.group.setToolTip("Use / for nested groups, for example Homelab/Servers")
        form.addRow("Group:", self.group)

        self.host = QLineEdit(s.host)
        self.host.setPlaceholderText("host name or IP address")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(s.port)
        host_row = QHBoxLayout()
        host_row.addWidget(self.host, 1)
        host_row.addWidget(QLabel("Port:"))
        host_row.addWidget(self.port)
        form.addRow("Host:", host_row)
        self.user = QLineEdit(s.user)
        self.user.setPlaceholderText("leave empty to use your local user name or ~/.ssh/config")
        form.addRow("User:", self.user)

        self.auth = QComboBox()
        for key, label in AUTH_METHODS.items():
            self.auth.addItem(label, key)
        self.auth.setCurrentIndex(max(self.auth.findData(s.auth), 0))
        self.auth.currentIndexChanged.connect(self._sync)
        form.addRow("Sign in with:", self.auth)

        self.key_file = QLineEdit(s.key_file)
        self.key_file.setPlaceholderText("~/.ssh/id_ed25519")
        self.key_file.textChanged.connect(self._check_key)
        key_btn = QPushButton("Browse...")
        key_btn.clicked.connect(lambda: self._browse(self.key_file, "Choose a private key"))
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_file, 1)
        key_row.addWidget(key_btn)
        self.key_label = QLabel("Key file:")
        form.addRow(self.key_label, key_row)
        self.cert_file = QLineEdit(s.cert_file)
        self.cert_file.setPlaceholderText("optional, for example ~/.ssh/id_ed25519-cert.pub")
        cert_btn = QPushButton("Browse...")
        cert_btn.clicked.connect(lambda: self._browse(self.cert_file, "Choose a certificate"))
        cert_row = QHBoxLayout()
        cert_row.addWidget(self.cert_file, 1)
        cert_row.addWidget(cert_btn)
        self.cert_label = QLabel("Certificate:")
        form.addRow(self.cert_label, cert_row)
        self.key_note = QLabel()
        self.key_note.setWordWrap(True)
        theme.set_role(self.key_note, "warning")
        form.addRow(self.key_note)

        # Password: asked for in the tab unless the user chooses to remember it.
        store_name = passwords.store_name()
        self.remember = QCheckBox(f"Remember the password in the {store_name}")
        self.remember.setChecked(s.remember_password)
        self.remember.toggled.connect(self._sync)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self._has_saved = bool(self.editing and s.remember_password and passwords.available
                               and passwords.has(s.id))
        self.password_edit.setPlaceholderText(
            f"Saved in the {store_name}. Type to replace it." if self._has_saved
            else "Password")
        self.password_note = QLabel(
            f"By default the password is asked for in the tab each time. If you remember it, "
            f"termSwitch keeps it only in the {store_name}, never in its own files, and "
            f"types it once at ssh's password prompt for this host.")
        self.password_note.setWordWrap(True)
        theme.set_role(self.password_note, "hint")
        if not passwords.available:
            self.remember.setChecked(False)
            self.remember.setEnabled(False)
            self.remember.setToolTip(f"The {store_name} isn't available, so the password "
                                     "will be asked for in the tab.")
        form.addRow("", self.remember)
        self.password_label = QLabel("Password:")
        form.addRow(self.password_label, self.password_edit)
        form.addRow(self.password_note)

        self.jump = QLineEdit(s.jump)
        self.jump.setPlaceholderText("optional: [user@]bastion[:port]")
        form.addRow("Jump host:", self.jump)
        self.keepalive = QSpinBox()
        self.keepalive.setRange(0, 3600)
        self.keepalive.setSuffix(" s")
        self.keepalive.setSpecialValueText("off")
        self.keepalive.setValue(int(s.options.get("ServerAliveInterval", "0") or 0))
        self.keepalive.setToolTip("ServerAliveInterval: send a keepalive this often")
        form.addRow("Keepalive:", self.keepalive)
        self.forward_agent = QCheckBox("Forward the SSH agent")
        self.forward_agent.setChecked(s.options.get("ForwardAgent") == "yes")
        self.forward_agent.setToolTip("Lets the remote host use your local keys while you're "
                                      "connected. Only enable this for hosts you trust.")
        form.addRow("", self.forward_agent)

        self.error = QLabel()
        self.error.setWordWrap(True)
        theme.set_role(self.error, "error")
        self.error.hide()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.error)
        lay.addWidget(buttons)
        self._form = form
        self._sync()
        self._check_key()

    # ---- layout ------------------------------------------------------------

    def _sync(self, *_):
        auth = self.auth.currentData()
        is_key = auth == AUTH_KEY
        is_password = auth == AUTH_PASSWORD
        for w in (self.key_label, self.key_file, self.cert_label, self.cert_file):
            w.setEnabled(is_key or auth == AUTH_AGENT)
        self.key_file.setEnabled(is_key)
        self.key_label.setEnabled(is_key)
        self.remember.setVisible(is_password)
        self.password_note.setVisible(is_password)
        show_pw = is_password and self.remember.isChecked()
        self.password_label.setVisible(show_pw)
        self.password_edit.setVisible(show_pw)

    def _browse(self, edit: QLineEdit, title: str):
        import os
        start = os.path.expanduser("~/.ssh")
        path, _ = QFileDialog.getOpenFileName(self, title, start, "All files (*)")
        if path:
            edit.setText(path)

    def _check_key(self, *_):
        problem = key_file_problem(self.key_file.text().strip()) if self.key_file.isEnabled() else None
        self.key_note.setText(problem or "")
        self.key_note.setVisible(bool(problem))

    # ---- result ------------------------------------------------------------

    def collect(self) -> None:
        options = {}
        if self.keepalive.value():
            options["ServerAliveInterval"] = str(self.keepalive.value())
        if self.forward_agent.isChecked():
            options["ForwardAgent"] = "yes"
        # Keep allowed options set elsewhere (for example by hand in the file).
        if self.original is not None:
            for k, v in self.original.options.items():
                if k not in ("ServerAliveInterval", "ForwardAgent"):
                    options[k] = v
        auth = self.auth.currentData()
        remember = auth == AUTH_PASSWORD and self.remember.isChecked()
        base = self.original or SshSession(name="", host="")
        session = replace(
            base, name=self.name.text().strip(),
            group=normalize_group(self.group.currentText()),
            host=self.host.text().strip(), port=self.port.value(),
            user=self.user.text().strip(), auth=auth,
            key_file=self.key_file.text().strip() if auth == AUTH_KEY else "",
            cert_file=self.cert_file.text().strip() if auth in (AUTH_KEY, AUTH_AGENT) else "",
            jump=self.jump.text().strip(), options=options, remember_password=remember)
        validate(session)
        if session.name in {s.name for s in self.store.sessions.values() if s.id != session.id}:
            raise SessionError(f"A session named '{session.name}' already exists.")
        typed = self.password_edit.text()
        if remember:
            if typed:
                self.password_action, self.password = SET, typed
            elif self._has_saved:
                self.password_action = KEEP
            else:
                raise SessionError("Type the password to remember, or uncheck Remember.")
        else:
            had = self.original is not None and self.original.remember_password
            self.password_action = DELETE if had else KEEP
        self.session = session

    def _accept(self):
        try:
            self.collect()
        except SessionError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        self.password_edit.clear()
        self.accept()

    def take_password(self) -> str:
        """Hand over the typed password once and forget it here."""
        value, self.password = self.password, ""
        return value
