"""Removing termSwitch's own data (the uninstaller's "Remove my termSwitch settings")."""

import json

import pytest
from PySide6.QtCore import QSettings

from termswitch import __main__ as entry
from termswitch import userdata
from termswitch.ssh_sessions import FILE_FORMAT, FILE_NAME

S1, S2 = "a" * 32, "b" * 32


class FakePasswords:
    def __init__(self):
        self.deleted = []

    def delete(self, sid):
        self.deleted.append(sid)


def make_data(tmp_path):
    home = tmp_path / "home"
    aws, ssh = home / ".aws", home / ".ssh"
    for d in (aws, ssh):
        d.mkdir(parents=True)
    (aws / "config").write_text("[profile dev]\n", encoding="utf-8")
    (aws / "config.termswitch-1.bak").write_text("backup", encoding="utf-8")
    (ssh / "config").write_text("Host box\n", encoding="utf-8")

    data = tmp_path / "AppData" / "Roaming" / "rough-ton" / "termSwitch"
    (data / "schemes").mkdir(parents=True)
    (data / "schemes" / "mine.json").write_text("{}", encoding="utf-8")
    sessions = {"format": FILE_FORMAT, "sessions": [
        {"id": S1, "name": "one", "host": "one.example.com"},
        {"id": S2, "name": "two", "host": "two.example.com"},
    ]}
    (data / FILE_NAME).write_text(json.dumps(sessions), encoding="utf-8")
    caches = tmp_path / "AppData" / "Local" / "termSwitch"
    (caches / "cache").mkdir(parents=True)
    (caches / "cache" / "aws-commands.json").write_text("{}", encoding="utf-8")

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("theme/name", "Dracula")
    settings.sync()
    return home, data, caches, settings


def test_removes_only_termswitch_data(tmp_path):
    home, data, caches, settings = make_data(tmp_path)
    passwords = FakePasswords()
    removed = userdata.remove(settings=settings, data_dir=data, caches=caches,
                              passwords=passwords)
    assert sorted(passwords.deleted) == sorted([S1, S2])
    assert not data.exists() and not caches.exists()
    assert not data.parent.exists()            # the empty rough-ton folder goes too
    assert settings.allKeys() == []
    assert not (tmp_path / "settings.ini").exists()
    assert removed[0] == "saved passwords for 2 SSH session(s)"
    assert "settings" in removed
    # Never touched:
    assert (home / ".aws" / "config").read_text(encoding="utf-8") == "[profile dev]\n"
    assert (home / ".aws" / "config.termswitch-1.bak").exists()
    assert (home / ".ssh" / "config").exists()


def test_nothing_to_remove(tmp_path):
    settings = QSettings(str(tmp_path / "empty.ini"), QSettings.Format.IniFormat)
    removed = userdata.remove(settings=settings, data_dir=tmp_path / "x" / "termSwitch",
                              caches=tmp_path / "y" / "termSwitch", passwords=FakePasswords())
    assert removed == []


def test_keeps_a_shared_organization_folder(tmp_path):
    _, data, caches, settings = make_data(tmp_path)
    (data.parent / "otherApp").mkdir()
    userdata.remove(settings=settings, data_dir=data, caches=caches, passwords=FakePasswords())
    assert (data.parent / "otherApp").is_dir()


@pytest.mark.parametrize("bad", ["home", ".aws", ".ssh", "Documents"])
def test_refuses_folders_that_are_not_termswitch(tmp_path, monkeypatch, bad):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(userdata.Path, "home", classmethod(lambda cls: home))
    target = home if bad == "home" else home / bad
    target.mkdir(exist_ok=True)
    (target / "keep").write_text("x", encoding="utf-8")
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    with pytest.raises(userdata.UserDataError):
        userdata.remove(settings=settings, data_dir=target, caches=tmp_path / "termSwitch",
                        passwords=FakePasswords())
    assert (target / "keep").exists()


def test_refuses_a_termswitch_folder_inside_dot_aws(tmp_path):
    inside = tmp_path / ".aws" / "termSwitch"
    inside.mkdir(parents=True)
    assert not userdata._owned(inside)
    assert userdata._owned(tmp_path / "rough-ton" / "termSwitch")
    assert userdata._owned(tmp_path / ".termswitch")


def test_cache_root_is_the_termswitch_folder(monkeypatch, tmp_path):
    from termswitch import aws_commands
    monkeypatch.setattr(aws_commands, "cache_dir", lambda: tmp_path / "termSwitch" / "cache")
    assert userdata.cache_root() == tmp_path / "termSwitch"
    monkeypatch.setattr(aws_commands, "cache_dir", lambda: tmp_path / "termswitch")
    assert userdata.cache_root() == tmp_path / "termswitch"


def test_command_line_flag(monkeypatch, capsys):
    monkeypatch.setattr(userdata, "set_names", lambda: None)
    calls = []
    monkeypatch.setattr(userdata, "remove", lambda: calls.append(1) or ["settings"])
    assert entry.main(["--remove-user-data"]) == 0
    assert calls == [1]
    assert "Removed: settings" in capsys.readouterr().out


def test_command_line_flag_reports_failure(monkeypatch, capsys):
    def boom():
        raise userdata.UserDataError("nope")
    monkeypatch.setattr(userdata, "set_names", lambda: None)
    monkeypatch.setattr(userdata, "remove", boom)
    assert entry.main(["--remove-user-data"]) == 1
    assert "nope" in capsys.readouterr().err
