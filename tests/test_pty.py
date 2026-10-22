import sys
import threading

import pytest

from termswitch.pty_backend import IS_WINDOWS, PtyError, PtyProcess


def _shell_cmd(script_posix: str, script_win: str):
    if IS_WINDOWS:
        import os
        cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        return [cmd, "/d", "/c", script_win]
    return ["/bin/sh", "-c", script_posix]


def run(argv, write=None, cols=100, rows=30, timeout=15, resize=None):
    out, done, codes = [], threading.Event(), []
    p = PtyProcess(argv, dict(__import__("os").environ), None, cols, rows,
                   on_data=out.append, on_exit=lambda c: (codes.append(c), done.set()))
    if resize:
        import time
        time.sleep(0.3)
        p.resize(*resize)
    if write:
        import time
        time.sleep(0.5)
        p.write(write)
    assert done.wait(timeout), "process did not exit"
    return "".join(out), codes[0], p


def test_spawn_output_and_exit_code():
    out, code, p = run(_shell_cmd("echo hello-pty; exit 3", "echo hello-pty & exit /b 3"))
    assert "hello-pty" in out
    assert code == 3
    assert not p.is_alive()


def test_write_reaches_child():
    out, code, _ = run(_shell_cmd("read x; echo got:$x", "set /p x= & call echo got:%x%"),
                       write="abc\r")
    assert "got:abc" in out
    assert code == 0


@pytest.mark.skipif(IS_WINDOWS, reason="stty is POSIX only")
def test_size_and_resize():
    """The child prints its size, waits for a line, then prints it again. Resizing
    only after the first size arrives keeps the test independent of timing (a fixed
    delay sometimes resized before the shell had started)."""
    import os
    import time
    out, done, codes = [], threading.Event(), []
    p = PtyProcess(["/bin/sh", "-c", "stty size; read x; stty size"], dict(os.environ),
                   None, 90, 25, on_data=out.append,
                   on_exit=lambda c: (codes.append(c), done.set()))
    deadline = time.monotonic() + 10
    while "25 90" not in "".join(out) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "25 90" in "".join(out), "".join(out)
    p.resize(120, 40)
    p.write("\r")
    assert done.wait(10), "process did not exit"
    assert "40 120" in "".join(out).split("25 90", 1)[1]


def test_relative_shell_refused():
    with pytest.raises(PtyError):
        PtyProcess(["sh"], {}, None, 80, 24, lambda d: None, lambda c: None)


def test_terminate():
    argv = _shell_cmd("sleep 30", "ping -n 30 127.0.0.1 >NUL")
    done = threading.Event()
    p = PtyProcess(argv, dict(__import__("os").environ), None, 80, 24,
                   lambda d: None, lambda c: done.set())
    p.terminate()
    assert done.wait(10)
    assert not p.is_alive()


def test_is_alive_safe_while_child_exits():
    """is_alive() must never raise while the reader thread reaps the child."""
    import time
    for _ in range(20):
        done = threading.Event()
        p = PtyProcess(_shell_cmd("exit 0", "exit /b 0"), dict(__import__("os").environ),
                       None, 80, 24, lambda d: None, lambda c: done.set())
        end = time.time() + 5
        while not done.is_set() and time.time() < end:
            p.is_alive()
            p.has_foreground_job()
        assert done.wait(5)
        assert p.is_alive() is False
