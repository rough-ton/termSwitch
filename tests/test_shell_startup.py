"""Embedded tabs must keep their profile even when the user's shell startup
files change AWS_PROFILE or export keys (seen with `export AWS_PROFILE=...`
in ~/.zshrc on macOS)."""
import os
import shutil
import sys
import threading
import time

import pytest

from termswitch.config_store import Profile
from termswitch.launcher import LaunchOptions, embedded_command
from termswitch.pty_backend import PtyProcess

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX shells only")

RC_POSIX = ('export AWS_PROFILE=mzr-sso\nexport AWS_ACCESS_KEY_ID=AKIAFROMRC\n'
            'export USER_RC_{name}=ran\n')


def make_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    for name in (".zshenv", ".zprofile", ".zshrc", ".zlogin", ".bash_profile"):
        (home / name).write_text(RC_POSIX.format(name=name.strip(".").upper()))
    fish = home / ".config" / "fish"
    fish.mkdir(parents=True)
    (fish / "config.fish").write_text(
        "set -gx AWS_PROFILE mzr-sso\nset -gx AWS_ACCESS_KEY_ID AKIAFROMRC\nset -gx USER_RC_FISH ran\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    aws = fake_bin / "aws"
    aws.write_text("#!/bin/sh\necho fake-aws \"$@\"\n")
    aws.chmod(0o755)
    return home, aws


def run_tab(shell, tmp_path, monkeypatch, probe):
    home, aws = make_home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.setattr("termswitch.launcher.user_shell", lambda platform=None: shell)
    prof = Profile(name="acme-tio-sandbox", props={"sso_session": "x"})
    argv, env = embedded_command(prof, LaunchOptions(auto_sso_login=False), "linux", str(aws))
    env["HOME"] = str(home)
    out, done = [], threading.Event()
    p = PtyProcess(argv, env, str(home), 200, 40, out.append, lambda c: done.set())
    time.sleep(1.5)
    p.write(probe + "\r")
    time.sleep(1.0)
    p.write("exit\r")
    done.wait(10)
    p.terminate()
    return "".join(out)


POSIX_PROBE = ('echo "AP=[$AWS_PROFILE] KEY=[${AWS_ACCESS_KEY_ID:-none}] '
               'RC=[$USER_RC_ZSHRC$USER_RC_BASH_PROFILE] TS=[${TERMSWITCH_AWS_PROFILE:-unset}]"')


@pytest.mark.skipif(not shutil.which("zsh"), reason="zsh not installed")
def test_zsh_reapplies_profile_after_startup_files(tmp_path, monkeypatch):
    text = run_tab(shutil.which("zsh"), tmp_path, monkeypatch,
                   POSIX_PROBE + '; echo "ZD=[$ZDOTDIR] LOGIN=[$USER_RC_ZLOGIN]"')
    assert "AP=[acme-tio-sandbox] KEY=[none] RC=[ran] TS=[unset]" in text
    assert f"ZD=[{tmp_path / 'home'}] LOGIN=[ran]" in text       # user's ZDOTDIR restored
    assert "your shell startup set AWS_PROFILE=mzr-sso" in text
    assert "exported AWS keys" in text


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not installed")
def test_bash_reapplies_profile_after_startup_files(tmp_path, monkeypatch):
    text = run_tab(shutil.which("bash"), tmp_path, monkeypatch, POSIX_PROBE)
    assert "AP=[acme-tio-sandbox] KEY=[none] RC=[ran] TS=[unset]" in text
    assert "your shell startup set AWS_PROFILE=mzr-sso" in text


@pytest.mark.skipif(not shutil.which("fish"), reason="fish not installed")
def test_fish_reapplies_profile_after_startup_files(tmp_path, monkeypatch):
    text = run_tab(shutil.which("fish"), tmp_path, monkeypatch,
                   'echo "AP=[$AWS_PROFILE] KEY=[$AWS_ACCESS_KEY_ID] RC=[$USER_RC_FISH]"')
    assert "AP=[acme-tio-sandbox] KEY=[] RC=[ran]" in text
    assert "your shell startup set AWS_PROFILE=mzr-sso" in text


def test_wrapper_dir_is_private():
    from termswitch.launcher import shell_wrapper_dir
    d = shell_wrapper_dir()
    assert (os.stat(d).st_mode & 0o077) == 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            assert (os.stat(os.path.join(root, f)).st_mode & 0o077) == 0


def test_no_startup_changes_means_no_warning(tmp_path, monkeypatch):
    home, aws = make_home(tmp_path)
    (home / ".bash_profile").write_text("export USER_RC_BASH_PROFILE=ran\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("termswitch.launcher.user_shell", lambda platform=None: "/bin/bash")
    argv, env = embedded_command(Profile(name="dev"), LaunchOptions(), "linux", str(aws))
    env["HOME"] = str(home)
    out, done = [], threading.Event()
    p = PtyProcess(argv, env, str(home), 200, 40, out.append, lambda c: done.set())
    time.sleep(1.2)
    p.write('echo "AP=[$AWS_PROFILE]"\rexit\r')
    done.wait(10)
    text = "".join(out)
    assert "AP=[dev]" in text and "termSwitch:" not in text
