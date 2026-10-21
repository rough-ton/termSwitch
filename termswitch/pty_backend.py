"""Pseudo-terminal processes for the embedded terminal.

Windows uses ConPTY through pywinpty's low-level ``PTY`` object. Its
``PtyProcess`` helper is avoided on purpose: it relays output through a
localhost TCP socket that any local process could connect to first.

macOS and Linux use ``ptyprocess``.

Output is read on a background thread and handed to the caller as decoded
text through a callback, so the UI thread never blocks on the pty.
"""

from __future__ import annotations

import codecs
import os
import subprocess
import sys
import threading
from typing import Callable

IS_WINDOWS = sys.platform.startswith("win")

OnData = Callable[[str], None]
OnExit = Callable[[int | None], None]


class PtyError(Exception):
    pass


class PtyProcess:
    """A child process attached to a pseudo-terminal."""

    def __init__(self, argv: list[str], env: dict[str, str], cwd: str | None,
                 cols: int, rows: int, on_data: OnData, on_exit: OnExit):
        if not argv or not os.path.isabs(argv[0]):
            raise PtyError("The shell must be given as an absolute path.")
        self.argv = argv
        self.cols, self.rows = max(cols, 2), max(rows, 1)
        self._on_data, self._on_exit = on_data, on_exit
        self._closed = threading.Event()
        self._write_lock = threading.Lock()
        self.exit_code: int | None = None
        cwd = cwd or os.path.expanduser("~")
        if IS_WINDOWS:
            self._impl = _WindowsPty(argv, env, cwd, self.cols, self.rows)
        else:
            self._impl = _PosixPty(argv, env, cwd, self.cols, self.rows)
        self.pid = self._impl.pid
        self._thread = threading.Thread(target=self._reader, name=f"pty-{self.pid}",
                                        daemon=True)
        self._thread.start()

    # ---- I/O -------------------------------------------------------------

    def _reader(self) -> None:
        try:
            while not self._closed.is_set():
                data = self._impl.read()
                if data is None:  # EOF
                    break
                if data:
                    self._on_data(data)
        except Exception:
            pass
        finally:
            self.exit_code = self._impl.wait_exit()
            self._closed.set()
            self._on_exit(self.exit_code)

    def write(self, text: str) -> None:
        if self._closed.is_set() or not text:
            return
        with self._write_lock:
            try:
                self._impl.write(text)
            except Exception:
                pass

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(cols, 2), max(rows, 1)
        if (cols, rows) == (self.cols, self.rows) or self._closed.is_set():
            return
        self.cols, self.rows = cols, rows
        try:
            self._impl.resize(cols, rows)
        except Exception:
            pass

    # ---- lifecycle -------------------------------------------------------

    def is_alive(self) -> bool:
        return not self._closed.is_set() and self._impl.is_alive()

    def has_foreground_job(self) -> bool:
        """True when something other than the shell owns the terminal (a
        running command). Only detectable on POSIX; Windows reports alive."""
        return self._impl.has_foreground_job()

    def terminate(self) -> None:
        if self._closed.is_set():
            return
        try:
            self._impl.terminate()
        except Exception:
            pass

    def wait(self, timeout: float | None = None) -> bool:
        return self._closed.wait(timeout)


# --------------------------------------------------------------------------
# POSIX
# --------------------------------------------------------------------------

class _PosixPty:
    def __init__(self, argv, env, cwd, cols, rows):
        import ptyprocess
        try:
            self.proc = ptyprocess.PtyProcess.spawn(argv, cwd=cwd, env=env,
                                                    dimensions=(rows, cols))
        except Exception as exc:
            raise PtyError(f"Could not start {argv[0]}: {exc}") from exc
        self.pid = self.proc.pid
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def read(self) -> str | None:
        try:
            data = self.proc.read(65536)
        except EOFError:
            return None
        return self.decoder.decode(data)

    def write(self, text: str) -> None:
        self.proc.write(text.encode("utf-8"))

    def resize(self, cols, rows) -> None:
        self.proc.setwinsize(rows, cols)

    def is_alive(self) -> bool:
        return self.proc.isalive()

    def has_foreground_job(self) -> bool:
        try:
            return os.tcgetpgrp(self.proc.fd) != self.proc.pid
        except OSError:
            return False

    def terminate(self) -> None:
        self.proc.terminate(force=True)

    def wait_exit(self) -> int | None:
        try:
            self.proc.wait()
        except Exception:
            pass
        status = self.proc.exitstatus
        if status is None and self.proc.signalstatus is not None:
            return 128 + self.proc.signalstatus
        return status


# --------------------------------------------------------------------------
# Windows (ConPTY)
# --------------------------------------------------------------------------

class _WindowsPty:
    def __init__(self, argv, env, cwd, cols, rows):
        try:
            from winpty import PTY, Backend
        except ImportError as exc:
            raise PtyError("pywinpty is not installed.") from exc
        self.pty = PTY(cols, rows, backend=Backend.ConPTY)
        env_block = "\0".join(f"{k}={v}" for k, v in env.items()) + "\0"
        # The program is passed separately; the command line is the arguments
        # only, quoted with the MSVCRT rules.
        cmdline = " " + subprocess.list2cmdline(argv[1:]) if len(argv) > 1 else None
        try:
            ok = self.pty.spawn(argv[0], cmdline=cmdline, cwd=cwd, env=env_block)
        except Exception as exc:
            raise PtyError(f"Could not start {argv[0]}: {exc}") from exc
        if not ok:
            raise PtyError(f"Could not start {argv[0]}.")
        self.pid = self.pty.pid
        self._exited = threading.Event()
        # A blocking ConPTY read is not always woken when the shell exits, so a
        # watcher cancels the pending read shortly after the process ends.
        threading.Thread(target=self._watch, name=f"conpty-watch-{self.pid}",
                         daemon=True).start()

    def _watch(self) -> None:
        import time
        while self.pty.isalive():
            time.sleep(0.25)
        time.sleep(0.4)  # let the last output drain
        self._exited.set()
        try:
            self.pty.cancel_io()
        except Exception:
            pass

    def read(self) -> str | None:
        try:
            data = self.pty.read(blocking=True)
        except Exception:
            return None
        if not data and (self._exited.is_set() or self.pty.iseof()):
            return None
        return data

    def write(self, text: str) -> None:
        self.pty.write(text)

    def resize(self, cols, rows) -> None:
        self.pty.set_size(cols, rows)

    def is_alive(self) -> bool:
        return self.pty.isalive()

    def has_foreground_job(self) -> bool:
        return self.is_alive()

    def terminate(self) -> None:
        if self.pid:
            # The shell may have children; end the whole tree.
            root = os.environ.get("SystemRoot", r"C:\Windows")
            taskkill = os.path.join(root, "System32", "taskkill.exe")
            subprocess.run([taskkill, "/PID", str(self.pid), "/T", "/F"],
                           capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            self.pty.cancel_io()
        except Exception:
            pass

    def wait_exit(self) -> int | None:
        try:
            return self.pty.get_exitstatus()
        except Exception:
            return None
