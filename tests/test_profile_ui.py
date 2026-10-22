"""Profile and SSO session dialogs, the welcome panel, and the main window's
profile management actions."""

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from termswitch.config_store import ConfigError, ProfileStore

from test_store_and_launcher import CREDS, SAMPLE


@pytest.fixture
def store(tmp_path: Path) -> ProfileStore:
    cfg = tmp_path / "config"
    cfg.write_text(SAMPLE)
    creds = tmp_path / "credentials"
    creds.write_text(CREDS)
    s = ProfileStore(cfg, creds)
    s.load()
    return s


def close_window(w, app):
    """Close and delete a main window now, so its start-up timers can't start
    background jobs during later tests."""
    from PySide6.QtCore import QEvent
    w.close()
    w.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


@pytest.fixture
def window(qapp, store, tmp_path, monkeypatch):
    from termswitch import ui
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("check_on_startup", False)
    monkeypatch.setattr(ui.MainWindow, "run_dependency_check", lambda *a, **k: None)
    w = ui.MainWindow(store=store, settings=settings)
    yield w
    close_window(w, qapp)


# ---- ProfileDialog ----------------------------------------------------------

def test_new_sso_profile_dialog(qapp, store):
    from termswitch.profile_dialogs import KIND_SSO, ProfileDialog
    dlg = ProfileDialog(store, group="Team/Dev", kind=KIND_SSO, sso_session="acme")
    dlg.name_edit.setText("team-dev")
    with pytest.raises(ConfigError, match="account ID"):
        dlg.collect()
    dlg.sso_account.setText("123456789012")
    dlg.sso_role.setText("Developer")
    dlg.region.setCurrentText("us-west-2")
    dlg.collect()
    assert dlg.name == "team-dev" and dlg.group == "Team/Dev"
    assert dlg.props == {"sso_session": "acme", "sso_account_id": "123456789012",
                         "sso_role_name": "Developer", "region": "us-west-2"}


def test_new_profile_dialog_validation(qapp, store):
    from termswitch.profile_dialogs import KIND_ROLE, ProfileDialog
    dlg = ProfileDialog(store, kind=KIND_ROLE)
    dlg.name_edit.setText("cross-account")
    with pytest.raises(ConfigError, match="already exists"):
        dlg.collect()
    dlg.name_edit.setText("deploy")
    dlg.role_arn.setText("not-an-arn")
    with pytest.raises(ConfigError, match="role_arn"):
        dlg.collect()
    dlg.role_arn.setText("arn:aws:iam::123456789012:role/Deploy")
    dlg.source.setCurrentText("acme-dev-admin")
    dlg._append("aws_secret_access_key", "x")
    with pytest.raises(ConfigError, match="credentials"):
        dlg.collect()


def test_edit_dialog_reports_only_changes(qapp, store):
    from termswitch.profile_dialogs import ProfileDialog
    prof = store.profiles["acme-prod-admin"]
    dlg = ProfileDialog(store, profile=prof)
    assert not dlg.kind_box.isEnabled() and dlg.name_edit.isReadOnly()
    dlg.collect()
    assert dlg.changes == {}
    dlg.region.setCurrentText("eu-west-1")
    dlg.group_box.setCurrentText("Acme/Prod")
    dlg._append("cli_pager", "")
    dlg.table.item(dlg.table.rowCount() - 1, 1).setText("less")
    dlg.collect()
    assert dlg.changes == {"region": "eu-west-1", "x_group": "Acme/Prod", "cli_pager": "less"}


def test_edit_dialog_keeps_secrets_and_nested_read_only(qapp, tmp_path):
    from PySide6.QtCore import Qt

    from termswitch.profile_dialogs import ProfileDialog
    cfg = tmp_path / "config"
    cfg.write_text("[profile k]\naws_access_key_id = AKIAEXAMPLE1234\n"
                   "aws_secret_access_key = s3cr3t\nregion = us-west-2\n"
                   "s3 =\n    max_concurrent_requests = 10\n")
    s = ProfileStore(cfg, tmp_path / "creds")
    s.load()
    dlg = ProfileDialog(s, profile=s.profiles["k"])
    rows = {dlg.table.item(r, 0).text(): dlg.table.item(r, 1) for r in range(dlg.table.rowCount())}
    assert rows["aws_secret_access_key"].text() == "<set>"
    assert not rows["aws_secret_access_key"].flags() & Qt.ItemFlag.ItemIsEditable
    assert "nested" in rows["s3"].text()
    dlg.region.setCurrentText("us-east-1")
    dlg.collect()
    assert dlg.changes == {"region": "us-east-1"}
    s.update_profile("k", dlg.changes)
    text = cfg.read_text()
    assert "aws_secret_access_key = s3cr3t" in text
    assert "s3 =\n    max_concurrent_requests = 10" in text


def test_credential_process_warning(qapp, store):
    from termswitch.profile_dialogs import KIND_CUSTOM, ProfileDialog
    dlg = ProfileDialog(store, kind=KIND_CUSTOM)
    assert dlg.warning.isHidden()
    dlg._append("credential_process", "/usr/local/bin/get-creds")
    dlg._update_warning()
    assert not dlg.warning.isHidden()


def test_sso_session_dialog(qapp, store):
    from termswitch.profile_dialogs import SsoSessionDialog
    dlg = SsoSessionDialog(store)
    dlg.name_edit.setText("corp")
    with pytest.raises(ConfigError):
        dlg.collect()
    dlg.url.setText("https://corp.awsapps.com/start")
    dlg.collect()
    assert dlg.props["sso_registration_scopes"] == "sso:account:access"
    edit = SsoSessionDialog(store, name="acme")
    edit.region.setCurrentText("us-west-2")
    edit.collect()
    assert edit.changes == {"sso_region": "us-west-2"}


# ---- main window -------------------------------------------------------------

def press(monkeypatch, text):
    """Make QMessageBox.exec click the button with this text."""
    def fake_exec(self):
        for b in self.buttons():
            if b.text().replace("&", "") == text:
                b.click()
                return 0
        raise AssertionError(f"no {text!r} button in {[b.text() for b in self.buttons()]}")
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)


def accept_with(dialog_cls, monkeypatch, setup):
    def fake_exec(self):
        setup(self)
        self.collect()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(dialog_cls, "exec", fake_exec)


def test_welcome_panel_when_no_profiles(qapp, tmp_path, monkeypatch):
    from termswitch import ui
    s = ProfileStore(tmp_path / "aws" / "config", tmp_path / "aws" / "credentials")
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("check_on_startup", False)
    monkeypatch.setattr(ui.MainWindow, "run_dependency_check", lambda *a, **k: None)
    w = ui.MainWindow(store=s, settings=settings)
    assert w.left_stack.currentWidget() is w.welcome
    assert "There is no AWS config file yet" in w.welcome.body.text()
    close_window(w, qapp)


def test_new_edit_rename_delete_and_undo(window, monkeypatch, qapp):
    from termswitch import profile_dialogs, ui
    store = window.store
    assert window.left_stack.currentWidget() is not window.welcome

    def fill(dlg):
        dlg.name_edit.setText("ops")
        dlg.kind_box.setCurrentIndex(dlg.kind_box.findData(profile_dialogs.KIND_ROLE))
        dlg.role_arn.setText("arn:aws:iam::123456789012:role/Ops")
        dlg.source.setCurrentText("acme-dev-admin")
    accept_with(profile_dialogs.ProfileDialog, monkeypatch, fill)
    window.new_profile(group="Ops")
    assert store.profiles["ops"].group == "Ops"
    assert window._current_profile().name == "ops"
    assert window.act_undo.isEnabled() and "Create profile ops" in window.act_undo.text()

    accept_with(profile_dialogs.ProfileDialog, monkeypatch,
                lambda d: d.region.setCurrentText("us-east-2"))
    window.edit_profile(store.profiles["ops"])
    assert store.profiles["ops"].region == "us-east-2"

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("ops2", True))
    window.rename_profile(store.profiles["ops"])
    assert "ops2" in store.profiles and "ops" not in store.profiles

    press(monkeypatch, "Delete")
    window.delete_profiles([store.profiles["ops2"]])
    assert "ops2" not in store.profiles

    window.undo_config_change()
    assert "ops2" in store.profiles


def test_production_delete_needs_typed_name(window, monkeypatch):
    store = window.store
    prod = store.profiles["acme-prod-admin"]
    assert window._is_production(prod)
    press(monkeypatch, "Delete")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("wrong", True))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    window.delete_profiles([prod])
    assert "acme-prod-admin" in store.profiles
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("acme-prod-admin", True))
    window.delete_profiles([store.profiles["acme-prod-admin"]])
    assert "acme-prod-admin" not in store.profiles


def test_rename_updates_references_and_marks_tabs(window, monkeypatch):
    from PySide6.QtWidgets import QPlainTextEdit

    from termswitch.terminal_tabs import TabInfo
    store = window.store
    # A stand-in tab for the profile being renamed.
    fake = QPlainTextEdit()
    fake.is_alive = lambda: True
    idx = window.terminals.tabs.addTab(fake, "aws: acme-dev-admin")
    window.terminals._infos[id(fake)] = TabInfo(store.profiles["acme-dev-admin"], None,
                                                fake, "aws: acme-dev-admin")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("dev", True))
    press(monkeypatch, "Update them")
    window.rename_profile(store.profiles["acme-dev-admin"])
    assert store.profiles["cross-account"].props["source_profile"] == "dev"
    assert window.terminals.tabs.tabText(idx).endswith("(renamed)")
    window.terminals.tabs.removeTab(idx)
    window.terminals._infos.clear()


def test_reset_shows_welcome_and_undo_restores(window, monkeypatch):
    press(monkeypatch, "Reset")
    window.store.credentials_file.unlink()     # credentials-only profiles keep the tree
    window.reset_config()
    assert window.left_stack.currentWidget() is window.welcome
    window.undo_config_change()
    assert window.left_stack.currentWidget() is not window.welcome
    assert window.store.config_file.read_text() == SAMPLE


def test_in_app_editor_refuses_secret_configs(window, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    window.store.config_file.write_text(SAMPLE + "\n[profile k]\naws_secret_access_key = x\n")
    window.edit_config_in_app()
    assert shown and "access keys" in shown[0]


def test_config_editor_validates(qapp, store):
    from termswitch.config_dialogs import ConfigEditorDialog
    dlg = ConfigEditorDialog(store)
    assert dlg.btn_save.isEnabled()
    dlg.editor.setPlainText("[ok]\na = 1\nbroken line\n")
    assert not dlg.btn_save.isEnabled() and dlg.highlighter.error_line == 3
    dlg.editor.setPlainText("[profile x]\naws_secret_access_key = abc\n")
    assert not dlg.btn_save.isEnabled()


def test_setup_tab_runs_aws_configure_sso(window, monkeypatch):
    from termswitch import ui
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(ui, "aws_setup_command",
                        lambda args: (["/bin/echo", *args], {"PATH": "/usr/bin:/bin"}))
    window.add_profiles_from_sso("acme")
    info = window.terminals.current_info()
    assert info.profile is None and info.title == "aws configure sso"
    window.terminals.close_all()


def test_closing_the_window_stops_background_jobs(qapp, store, tmp_path, monkeypatch):
    """Quitting while the command list loads or checks run must not leave Python
    threads running into Qt's shutdown (that crashed at exit)."""
    import threading
    import time

    from PySide6.QtCore import QThreadPool

    from termswitch import commands_pane, ui
    started = threading.Event()

    def slow_catalog(force=False, progress=None):
        started.set()
        for i in range(200):
            progress(i, 200)          # raises once the pane is shut down
            time.sleep(0.02)
        raise AssertionError("the load was not cancelled")
    monkeypatch.setattr(commands_pane, "load_catalog", slow_catalog)
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("check_on_startup", False)
    monkeypatch.setattr(ui.MainWindow, "run_dependency_check", lambda *a, **k: None)
    w = ui.MainWindow(store=store, settings=settings)
    w.commands.load()
    assert started.wait(5)
    t0 = time.monotonic()
    close_window(w, qapp)
    assert QThreadPool.globalInstance().activeThreadCount() == 0
    assert time.monotonic() - t0 < 3


def test_toolbar_icons_match_button_icons(window):
    from PySide6.QtWidgets import QStyle, QToolBar
    tb = window.findChild(QToolBar)
    side = window.style().pixelMetric(QStyle.PixelMetric.PM_ButtonIconSize)
    assert tb.iconSize().width() == side == tb.iconSize().height()


def test_commands_labels_are_not_covered(window, qapp):
    from conftest import pump
    window.resize(1100, 600)
    window.show()
    pump(qapp, 0.2)
    pane = window.commands
    for label, field in ((pane.service_label, pane.service), (pane.name_label, pane.name)):
        label_right = label.mapTo(pane, label.rect().topRight()).x()
        field_left = field.mapTo(pane, field.rect().topLeft()).x()
        assert label_right < field_left, label.text()
        assert label.width() >= label.sizeHint().width(), label.text()
