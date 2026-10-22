"""SSH sessions: validation, the ssh command, storage, ~/.ssh/config, the password
store and password filling."""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from termswitch import ssh_sessions as ss
from termswitch.ssh_sessions import SessionError, SshSession

SSH = "/usr/bin/ssh"


def sess(**kw):
    base = dict(name="web-01", host="web01.example.com", user="deploy")
    base.update(kw)
    return SshSession(**base)


# ---- validation and argv ---------------------------------------------------------

@pytest.mark.parametrize("kw", [
    {"host": "-oProxyCommand=touch /tmp/pwned"}, {"host": "web 01"}, {"host": "a;b"},
    {"host": ""}, {"user": "-oProxyCommand=x"}, {"user": "a b"}, {"port": 0},
    {"port": 70000}, {"auth": "telnet"}, {"auth": "key", "key_file": ""},
    {"auth": "key", "key_file": "relative/key"}, {"auth": "key", "key_file": "-i/etc/x"},
    {"jump": "-oProxyCommand=x"}, {"jump": "bastion;rm -rf /"}, {"jump": "a b"},
    {"options": {"ProxyCommand": "nc %h %p"}}, {"options": {"StrictHostKeyChecking": "no"}},
    {"options": {"UserKnownHostsFile": "/dev/null"}}, {"options": {"LocalCommand": "x"}},
    {"options": {"ServerAliveInterval": "10; x"}}, {"name": "bad\nname"},
    {"auth": "agent", "remember_password": True}, {"color": "red"},
])
def test_rejects_unsafe_sessions(kw):
    with pytest.raises(SessionError):
        ss.validate(sess(**kw))


def test_argv_is_safe_and_ends_options():
    argv = ss.ssh_argv(sess(port=2222, auth="key", key_file="~/.ssh/id_ed25519",
                            cert_file="~/.ssh/id_ed25519-cert.pub", jump="admin@bastion:2200",
                            options={"ServerAliveInterval": "30"}), SSH)
    home = os.path.expanduser("~")
    assert argv == [SSH, "-p", "2222", "-l", "deploy", "-i", f"{home}/.ssh/id_ed25519",
                    "-o", "IdentitiesOnly=yes",
                    "-o", f"CertificateFile={home}/.ssh/id_ed25519-cert.pub",
                    "-J", "admin@bastion:2200", "-o", "ServerAliveInterval=30",
                    "--", "web01.example.com"]
    assert argv[-2] == "--"


def test_argv_password_and_agent_and_config_alias():
    pw = ss.ssh_argv(sess(auth="password"), SSH)
    assert "PreferredAuthentications=keyboard-interactive,password" in pw
    assert "PubkeyAuthentication=no" in pw
    assert ss.ssh_argv(sess(auth="agent"), SSH) == [SSH, "-l", "deploy", "--",
                                                    "web01.example.com"]
    alias = SshSession(name="prod-db", host="10.0.0.5", from_ssh_config=True, id="sshconfig-x")
    assert ss.ssh_argv(alias, SSH) == [SSH, "--", "prod-db"]


def test_ipv6_and_windows_user():
    assert ss.ssh_argv(sess(host="fe80::1", user="CORP\\alice"), SSH)[-1] == "fe80::1"
    ss.validate(sess(user="alice@corp.example"))


def test_no_secret_ever_in_argv_or_json():
    s = sess(auth="password", remember_password=True)
    assert "password" not in json.dumps(s.to_json()).replace('"password"', "").replace(
        "remember_password", "")
    assert not any("secret" in a for a in ss.ssh_argv(s, SSH))


# ---- storage --------------------------------------------------------------------

def test_store_round_trip_and_private_file(tmp_path):
    path = tmp_path / "data" / "ssh_sessions.json"
    store = ss.SessionStore(path, tmp_path / "no-config")
    store.load()
    a = sess(group="Homelab/Servers", options={"ServerAliveInterval": "15"})
    store.save(a)
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    again = ss.SessionStore(path, tmp_path / "no-config")
    again.load()
    assert again.sessions[a.id] == a
    assert again.groups() == {"Homelab", "Homelab/Servers"}
    with pytest.raises(SessionError, match="already exists"):
        store.save(sess())                       # same name, new id
    store.delete(a.id)
    assert a.id not in store.sessions
    assert not list(path.parent.glob(".ssh_sessions-*"))   # no temp files left


def test_store_skips_tampered_entries(tmp_path):
    path = tmp_path / "ssh_sessions.json"
    good = sess().to_json()
    bad = {**sess(name="evil").to_json(), "host": "-oProxyCommand=sh"}
    path.write_text(json.dumps({"format": "termswitch-ssh-sessions", "version": 1,
                                "sessions": [good, bad, "junk"]}))
    if os.name == "posix":
        path.chmod(0o600)
    store = ss.SessionStore(path, tmp_path / "none")
    store.load()
    assert [s.name for s in store.sessions.values()] == ["web-01"]
    assert len(store.warnings) == 2


def test_store_refuses_non_regular_file(tmp_path):
    if os.name != "posix":
        pytest.skip("symlink check is POSIX")
    real = tmp_path / "real.json"
    real.write_text("{}")
    link = tmp_path / "ssh_sessions.json"
    link.symlink_to(real)
    store = ss.SessionStore(link, tmp_path / "none")
    store.load()
    assert store.warnings and "not a regular file" in store.warnings[0]
    with pytest.raises(SessionError):
        store.save(sess())


def test_ssh_config_hosts_are_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    ssh_dir = tmp_path / ".ssh"
    (ssh_dir / "conf.d").mkdir(parents=True)
    (ssh_dir / "config").write_text(
        "Host *\n  ServerAliveInterval 30\n"
        "Host prod-db db\n  HostName 10.0.0.5\n  User admin\n  Port 2222\n"
        "  IdentityFile ~/.ssh/prod\n  ProxyJump bastion\n"
        "Host !nope *.internal -evil\n  User x\n"
        "Include conf.d/*\nInclude /etc/ssh/ssh_config\n")
    (ssh_dir / "conf.d" / "extra").write_text("Host lab\n  HostName lab.local\n")
    store = ss.SessionStore(tmp_path / "s.json", ssh_dir / "config")
    store.load()
    hosts = {s.name: s for s in store.config_hosts.values()}
    assert set(hosts) == {"prod-db", "db", "lab"}
    assert hosts["prod-db"].host == "10.0.0.5" and hosts["prod-db"].port == 2222
    assert hosts["prod-db"].user == "admin" and hosts["prod-db"].jump == "bastion"
    assert hosts["lab"].host == "lab.local"
    with pytest.raises(SessionError, match="read-only"):
        store.save(hosts["db"])
    copy = store.duplicate(hosts["prod-db"], "prod-db-copy")
    assert not copy.from_ssh_config and copy.host == "10.0.0.5"
    assert (ssh_dir / "config").read_text().startswith("Host *")   # never written


# ---- password store -----------------------------------------------------------------

class FakeKeychain:
    def __init__(self):
        self.items = {}

    def set_password(self, service, user, pw):
        self.items[(service, user)] = pw

    def get_password(self, service, user):
        return self.items.get((service, user))

    def delete_password(self, service, user):
        del self.items[(service, user)]


def test_password_store_round_trip():
    kc = FakeKeychain()
    store = ss.PasswordStore(backend=kc)
    store.set("abc", "s3cret")
    assert kc.items == {("termSwitch SSH", "abc"): "s3cret"}
    assert store.get("abc") == "s3cret" and store.has("abc")
    store.delete("abc")
    assert not store.has("abc")
    store.delete("abc")                                # deleting twice is fine
    with pytest.raises(SessionError):
        store.set("abc", "line\nbreak")


def _fake_keyring_module(monkeypatch, backend):
    import types
    mod = types.ModuleType("keyring")
    mod.get_keyring = lambda: backend
    monkeypatch.setitem(sys.modules, "keyring", mod)


def test_only_trusted_backends_are_used(monkeypatch):
    class PlaintextKeyring(FakeKeychain):
        pass
    PlaintextKeyring.__module__ = "keyrings.alt.file"
    _fake_keyring_module(monkeypatch, PlaintextKeyring())
    store = ss.PasswordStore()
    assert not store.available
    with pytest.raises(SessionError):
        store.set("x", "pw")

    class Keyring(FakeKeychain):
        pass
    Keyring.__module__ = "keyring.backends.macOS"
    _fake_keyring_module(monkeypatch, Keyring())
    assert ss.PasswordStore().available

    class ChainerBackend:
        backends = [Keyring(), PlaintextKeyring()]
    _fake_keyring_module(monkeypatch, ChainerBackend())
    assert not ss.PasswordStore().available              # one untrusted inside: refuse


# ---- password filling -------------------------------------------------------------------

def make_fill(session=None, password="s3cret"):
    sent, fetches, now = [], [], [0.0]

    def fetch():
        fetches.append(1)
        return password
    fill = ss.PasswordAutofill(session or sess(), fetch, sent.append, clock=lambda: now[0])
    return fill, sent, fetches, now


def test_fills_once_at_the_matching_prompt():
    fill, sent, fetches, _ = make_fill()
    assert not fill.feed("Warning: Permanently added 'web01.example.com' (ED25519)\r\n")
    assert fetches == []                                   # store not touched yet
    assert fill.feed("deploy@web01.example.com's password: ")
    assert sent == ["s3cret\r"]
    assert not fill.feed("\r\ndeploy@web01.example.com's password: ")   # retry: no
    assert sent == ["s3cret\r"] and len(fetches) == 1


def test_keyboard_interactive_prompt():
    fill, sent, _, _ = make_fill()
    assert fill.feed("(deploy@web01.example.com) Password: ")
    assert sent == ["s3cret\r"]


@pytest.mark.parametrize("text", [
    "Are you sure you want to continue connecting (yes/no/[fingerprint])? ",
    "Enter passphrase for key '/home/u/.ssh/id_ed25519': ",
    "admin@bastion's password: ",                         # a jump host's prompt
    "deploy@web01.example.com's password: and more",      # not at the end
    "evil@web01.example.com's password: ",                # another user
    "deploy@web01.example.com.evil.net's password: ",
])
def test_never_answers_other_prompts(text):
    fill, sent, fetches, _ = make_fill()
    assert not fill.feed(text)
    assert sent == [] and fetches == []


def test_stops_after_user_input_or_timeout():
    fill, sent, _, now = make_fill()
    fill.user_typed()
    assert not fill.feed("deploy@web01.example.com's password: ")
    fill, sent, _, now = make_fill()
    now[0] = ss.PasswordAutofill.WINDOW_SECONDS + 1
    assert not fill.feed("deploy@web01.example.com's password: ")
    assert sent == []


def test_prompt_split_across_chunks():
    fill, sent, _, _ = make_fill()
    assert not fill.feed("deploy@web01.exa")
    assert fill.feed("mple.com's password: ")
    assert sent == ["s3cret\r"]


def test_missing_password_sends_nothing():
    fill, sent, _, _ = make_fill(password=None)
    assert not fill.feed("deploy@web01.example.com's password: ")
    assert sent == [] and fill.done


# ---- terminal and UI -------------------------------------------------------------------

def test_terminal_hooks_report_output_and_user_input(qapp):
    from termswitch.terminal_widget import TerminalWidget
    t = TerminalWidget(["/bin/sh", "-c", "true"], {})
    out, typed = [], []
    t.output_observers.append(out.append)
    t.input_observers.append(lambda: typed.append(1))

    class FakePty:
        written = []

        def is_alive(self):
            return True

        def write(self, data):
            self.written.append(data)
    t.pty = FakePty()
    t.write_secret("pw\r")
    assert typed == []                                    # not counted as typing
    t.send_text("ls\r")
    assert typed == [1] and FakePty.written == ["pw\r", "ls\r"]
    t._pending.append("hello")
    t._feed()
    assert out == ["hello"]
    t.pty = None
    t.deleteLater()


def test_key_file_problem(tmp_path):
    key = tmp_path / "id"
    key.write_text("x")
    if os.name == "posix":
        key.chmod(0o644)
        assert "other users" in ss.key_file_problem(str(key))
        key.chmod(0o600)
    assert ss.key_file_problem(str(key)) is None
    assert "doesn't exist" in ss.key_file_problem(str(tmp_path / "missing"))


def test_dialog_collects_password_actions(qapp, tmp_path):
    from termswitch.ssh_dialogs import DELETE, KEEP, SET, SshSessionDialog
    store = ss.SessionStore(tmp_path / "s.json", tmp_path / "none")
    store.load()
    kc = ss.PasswordStore(backend=FakeKeychain())
    dlg = SshSessionDialog(store, kc)
    dlg.name.setText("db")
    dlg.host.setText("db.example.com")
    dlg.user.setText("admin")
    dlg.auth.setCurrentIndex(dlg.auth.findData("password"))
    dlg.remember.setChecked(True)
    with pytest.raises(SessionError, match="Type the password"):
        dlg.collect()
    dlg.password_edit.setText("hunter2")
    dlg.collect()
    assert dlg.password_action == SET and dlg.take_password() == "hunter2"
    assert dlg.take_password() == ""                      # handed over once
    saved = dlg.session
    kc.set(saved.id, "hunter2")
    store.save(saved)
    edit = SshSessionDialog(store, kc, session=saved)
    assert "Saved in" in edit.password_edit.placeholderText()
    edit.collect()
    assert edit.password_action == KEEP
    edit.remember.setChecked(False)
    edit.collect()
    assert edit.password_action == DELETE and not edit.session.remember_password


def test_dialog_without_keychain_cannot_remember(qapp, tmp_path, monkeypatch):
    from termswitch.ssh_dialogs import SshSessionDialog
    monkeypatch.setitem(sys.modules, "keyring", None)     # not installed (Linux)
    passwords = ss.PasswordStore()
    assert not passwords.available
    store = ss.SessionStore(tmp_path / "s.json", tmp_path / "none")
    dlg = SshSessionDialog(store, passwords)
    assert not dlg.remember.isChecked() and not dlg.remember.isEnabled()


def test_main_window_ssh_flow(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from termswitch import ui
    from termswitch.config_store import ProfileStore
    cfg = tmp_path / "config"
    cfg.write_text("[profile a]\nregion = us-west-2\n")
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("check_on_startup", False)
    monkeypatch.setattr(ui.MainWindow, "run_dependency_check", lambda *a, **k: None)
    w = ui.MainWindow(store=ProfileStore(cfg, tmp_path / "credentials"), settings=settings)
    w.ssh_store = ss.SessionStore(tmp_path / "ssh.json", tmp_path / "none")
    w.passwords = ss.PasswordStore(backend=FakeKeychain())
    w.ssh_store.load()
    s = sess(auth="password", remember_password=True, group="Lab")
    w.passwords.set(s.id, "pw1")
    w.ssh_store.save(s)
    (tmp_path / "sshconfig").write_text("Host jump\n  HostName 10.1.1.1\n")
    w.ssh_store.ssh_config = tmp_path / "sshconfig"
    w.ssh_store.load()
    w.rebuild()
    labels = [it.text(0) for it in w._iter_items()]
    assert "SSH sessions" in labels and "Lab" in labels and "web-01" in labels
    assert "~/.ssh/config" in labels and "~" not in labels and ".ssh" not in labels
    jump = [it for it in w._iter_items() if it.text(0) == "jump"][0]
    assert jump.parent().text(0) == "~/.ssh/config"
    # Connect: a fake ssh that asks for the password like ssh does, so the saved
    # password is typed once. getpass reads from the terminal without echo on
    # Windows (ConPTY) and macOS or Linux (pty) alike.
    fake_ssh = tmp_path / "fake_ssh.py"
    fake_ssh.write_text("import getpass\n"
                        "pw = getpass.getpass(\"deploy@web01.example.com's password: \")\n"
                        "print('got:' + pw, flush=True)\n", encoding="utf-8")
    monkeypatch.setattr(ui, "find_ssh", lambda: sys.executable)
    monkeypatch.setattr(ui, "ssh_argv", lambda session, ssh: [
        sys.executable, "-I", "-u", str(fake_ssh)])
    from conftest import pump
    w.resize(1000, 600)
    w.show()
    pump(qapp, 0.2)
    w.connect_ssh(s)
    term = w.terminals.current_terminal()
    info = w.terminals.current_info()
    assert info.ssh_session is s and info.autofill is not None
    pump(qapp, 20, until=lambda: "got:pw1" in "".join(
        "".join(c.data for c in line.values()) for line in term.screen.buffer.values()))
    screen = "\n".join("".join(term.screen.buffer[y][x].data for x in range(term.screen.columns))
                       for y in range(term.screen.lines))
    assert "got:pw1" in screen and screen.count("pw1") == 1     # printed only by fake ssh
    assert info.autofill.done
    w.terminals.close_all()
    # Delete removes the saved password too.
    from PySide6.QtWidgets import QMessageBox

    def press_delete(box):
        for b in box.buttons():
            if b.text().replace("&", "") == "Delete":
                b.click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", press_delete)
    w.delete_ssh([s])
    assert s.id not in w.ssh_store.sessions and not w.passwords.has(s.id)
    w.close()
    w.deleteLater()


# ---- shared folders and two-file undo ---------------------------------------------------

def test_store_undo_and_redo(tmp_path):
    store = ss.SessionStore(tmp_path / "s.json", tmp_path / "none")
    store.load()
    a = sess()
    store.save(a)
    assert store.undo_label == "Create SSH session web-01"
    store.undo()                                           # back to no file at all
    assert not (tmp_path / "s.json").exists() and not store.sessions
    store.undo()                                           # redo
    assert a.id in store.sessions
    store.set_groups({a.id: "Prod"})
    (tmp_path / "s.json").write_text((tmp_path / "s.json").read_text() + " ")
    assert "changed since" in store.undo_blocked()


def test_config_host_placement_and_remap(tmp_path):
    (tmp_path / "sshc").write_text("Host db\n  HostName 10.0.0.9\n")
    store = ss.SessionStore(tmp_path / "s.json", tmp_path / "sshc")
    store.load()
    db = next(iter(store.config_hosts.values()))
    a = sess(group="Prod/Web")
    store.save(a)
    store.set_groups({db.id: "Prod"})
    again = ss.SessionStore(tmp_path / "s.json", tmp_path / "sshc")
    again.load()
    assert next(iter(again.config_hosts.values())).group == "Prod"
    assert again.groups() == {"Prod", "Prod/Web"}
    assert again.remap_groups("Prod", "Production") == 2
    assert again.sessions[a.id].group == "Production/Web"
    assert next(iter(again.config_hosts.values())).group == "Production"
    assert "Host db" in (tmp_path / "sshc").read_text()    # ~/.ssh/config untouched


@pytest.fixture
def shared_window(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from termswitch import ui
    from termswitch.config_store import ProfileStore
    cfg = tmp_path / "config"
    cfg.write_text("[profile prod-admin]\nregion = us-west-2\nx_group = Prod\n"
                   "[profile dev]\nregion = us-west-2\n")
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("check_on_startup", False)
    monkeypatch.setattr(ui.MainWindow, "run_dependency_check", lambda *a, **k: None)
    w = ui.MainWindow(store=ProfileStore(cfg, tmp_path / "credentials"), settings=settings)
    (tmp_path / "sshc").write_text("Host jump\n  HostName 10.1.1.1\n")
    w.ssh_store = ss.SessionStore(tmp_path / "ssh.json", tmp_path / "sshc")
    w.passwords = ss.PasswordStore(backend=FakeKeychain())
    w.reload()
    yield w
    w.close()
    w.deleteLater()


def _parent_label(w, name):
    it = [i for i in w._iter_items() if i.text(0) == name][0]
    return it.parent().text(0) if it.parent() else None


def test_ssh_sessions_share_folders_with_profiles(shared_window):
    w = shared_window
    web = sess(name="prod-web", group="Prod")
    loose = sess(name="scratch")
    w.ssh_store.save(web)
    w.ssh_store.save(loose)
    w.rebuild()
    assert _parent_label(w, "prod-web") == "Prod" == _parent_label(w, "prod-admin")
    assert _parent_label(w, "scratch") == "SSH sessions"
    assert _parent_label(w, "jump") == "~/.ssh/config"
    prod = [i for i in w._iter_items() if i.text(0) == "Prod"][0]
    assert [s.name for s in w.ssh_under(prod)] == ["prod-web"]
    assert [p.name for p in w.profiles_under(prod)] == ["prod-admin"]


def test_drag_ssh_sessions_and_config_hosts_into_folders(shared_window):
    w = shared_window
    loose = sess(name="scratch")
    w.ssh_store.save(loose)
    w.rebuild()
    jump_id = next(iter(w.ssh_store.config_hosts))
    w._apply_move([f"ssh:{loose.id}", f"ssh:{jump_id}", "profile:dev"], "Prod")
    assert w.ssh_store.sessions[loose.id].group == "Prod"
    assert w.ssh_store.get(jump_id).group == "Prod"
    assert w.store.profiles["dev"].group == "Prod"
    assert _parent_label(w, "jump") == "Prod"
    assert w.act_undo.isEnabled() and "Move 3 item(s) to Prod" in w.act_undo.text()
    w.undo_config_change()                                 # both files at once
    assert w.ssh_store.sessions[loose.id].group is None
    assert w.ssh_store.get(jump_id).group is None
    assert w.store.profiles["dev"].group is None
    w.undo_config_change()                                 # and redo
    assert w.ssh_store.sessions[loose.id].group == "Prod"
    assert w.store.profiles["dev"].group == "Prod"


def test_rename_and_delete_folder_move_ssh_sessions(shared_window, monkeypatch):
    w = shared_window
    web = sess(name="prod-web", group="Prod/Web")
    w.ssh_store.save(web)
    w.rebuild()
    monkeypatch.setattr(ui_mod().QInputDialog, "getText", lambda *a, **k: ("Production", True))
    w.rename_group("Prod")
    assert w.store.profiles["prod-admin"].group == "Production"
    assert w.ssh_store.sessions[web.id].group == "Production/Web"
    w.undo_config_change()
    assert w.store.profiles["prod-admin"].group == "Prod"
    assert w.ssh_store.sessions[web.id].group == "Prod/Web"
    monkeypatch.setattr(ui_mod().QMessageBox, "question",
                        lambda *a, **k: ui_mod().QMessageBox.StandardButton.Yes)
    w.delete_group("Prod/Web")
    assert w.ssh_store.sessions[web.id].group == "Prod"


def test_failed_aws_write_rolls_back_ssh_change(shared_window, monkeypatch):
    from termswitch.config_store import ConfigError
    w = shared_window
    web = sess(name="prod-web", group="Prod")
    w.ssh_store.save(web)
    w.rebuild()

    def boom(*a, **k):
        raise ConfigError("disk full")
    monkeypatch.setattr(w.store, "rename_group", boom)
    monkeypatch.setattr(ui_mod().QInputDialog, "getText", lambda *a, **k: ("Production", True))
    monkeypatch.setattr(ui_mod().QMessageBox, "warning", lambda *a, **k: None)
    w.rename_group("Prod")
    assert w.ssh_store.sessions[web.id].group == "Prod"     # put back


def test_undo_refused_when_sessions_file_changed(shared_window, monkeypatch):
    w = shared_window
    loose = sess(name="scratch")
    w.ssh_store.save(loose)
    w._apply_move([f"ssh:{loose.id}"], "Prod")
    w.ssh_store.path.write_text(w.ssh_store.path.read_text() + "\n")
    shown = []
    monkeypatch.setattr(ui_mod().QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    w.undo_config_change()
    assert shown and "changed since" in shown[0]
    assert w.ssh_store.sessions[loose.id].group == "Prod"


def ui_mod():
    from termswitch import ui
    return ui
