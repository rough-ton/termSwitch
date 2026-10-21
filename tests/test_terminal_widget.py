import os
import sys

import pytest

from conftest import pump
from termswitch.config_store import Profile
from termswitch.launcher import LaunchOptions, embedded_command

IS_WINDOWS = sys.platform.startswith("win")


def _shell():
    if IS_WINDOWS:
        return [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                             "System32", "cmd.exe"), "/d", "/q"]
    return ["/bin/sh"]


def test_widget_runs_shell_and_types(qapp):
    from termswitch.terminal_widget import TerminalWidget
    t = TerminalWidget(_shell(), dict(os.environ))
    t.resize(800, 500)
    t.show()
    t.start()
    assert pump(qapp, 10, until=lambda: t.is_alive())
    t.insert_command("echo marker-123", run=True)

    def seen():
        return any("marker-123" in t.screen.line_text(line)
                   for line in t.screen.all_lines()[-60:])
    assert pump(qapp, 10, until=seen)
    t.send_text("exit\r")
    assert pump(qapp, 10, until=lambda: not t.is_alive())
    t.close()


def test_insert_does_not_press_enter(qapp):
    from termswitch.terminal_widget import TerminalWidget
    t = TerminalWidget(_shell(), {})
    sent = []
    t.send_text = sent.append
    t.insert_command("aws s3 ls\nrm -rf /", run=False)
    assert sent == ["aws s3 ls rm -rf /"]
    sent.clear()
    t.insert_command("aws sts get-caller-identity", run=True)
    assert sent == ["aws sts get-caller-identity\r"]


def test_selection_text(qapp):
    from termswitch.terminal_widget import TerminalWidget
    t = TerminalWidget(_shell(), {})
    t.stream.feed("first line\r\nsecond line\r\n")
    t._sel_anchor, t._sel_end = (0, 6), (1, 6)
    assert t.selected_text() == "line\nsecond"


def test_embedded_command_posix(monkeypatch):
    # Runs on every OS: the shell lookup is stubbed so Windows runners pass too.
    monkeypatch.setattr("termswitch.launcher.user_shell", lambda platform=None: "/bin/zsh")
    prof = Profile(name="dev-admin", props={"sso_session": "corp"})
    argv, env = embedded_command(prof, LaunchOptions(), "linux", "/usr/local/bin/aws")
    assert argv[0] == "/bin/sh" and argv[1] == "-c"
    assert "sso login --profile dev-admin" in argv[2]
    assert argv[2].rstrip().endswith("exec /bin/zsh -l")
    assert env["AWS_PROFILE"] == "dev-admin" and env["TERM"] == "xterm-256color"
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_embedded_command_windows(monkeypatch):
    import base64
    monkeypatch.setattr("termswitch.launcher._which", lambda n, p=None: None)
    prof = Profile(name="prod", props={"role_arn": "arn:aws:iam::123456789012:role/x"})
    argv, env = embedded_command(prof, LaunchOptions(), "win32", None)
    assert argv[0].lower().endswith("powershell.exe")
    script = base64.b64decode(argv[-1]).decode("utf-16-le")
    assert "$env:AWS_PROFILE = 'prod'" in script and "sso login" not in script
    assert env["AWS_PROFILE"] == "prod"


def test_embedded_command_refuses_bad_names():
    from termswitch.launcher import LaunchError
    with pytest.raises(LaunchError):
        embedded_command(Profile(name="bad\x1bname"), LaunchOptions(), "linux", None)
