"""Background credential checks and SSO logins."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .launcher import build_env, find_aws

UNKNOWN, CHECKING, VALID, EXPIRED, ERROR = "unknown", "checking", "valid", "expired", "error"

_EXPIRED_HINTS = (
    "token has expired", "token is expired", "sso session", "error loading sso token",
    "the sso session associated with this profile has expired",
    "unable to locate credentials", "expiredtoken", "refresh failed",
)


@dataclass
class StatusResult:
    profile: str
    state: str
    detail: str = ""
    arn: str | None = None
    account: str | None = None


def _popen_kwargs() -> dict:
    if sys.platform.startswith("win"):
        # Stop a console window from flashing for every check.
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def check_profile(name: str, timeout: int = 25) -> StatusResult:
    aws = find_aws()
    if not aws:
        return StatusResult(name, ERROR, "AWS CLI not found on PATH.")
    env = build_env()
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        env.pop(var, None)
    try:
        proc = subprocess.run(
            [aws, "sts", "get-caller-identity", "--profile", name, "--output", "json"],
            capture_output=True, text=True, timeout=timeout, env=env,
            stdin=subprocess.DEVNULL, **_popen_kwargs())
    except subprocess.TimeoutExpired:
        return StatusResult(name, ERROR, f"Timed out after {timeout}s.")
    except OSError as exc:
        return StatusResult(name, ERROR, str(exc))

    if proc.returncode == 0:
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return StatusResult(name, VALID, "Authenticated.")
        return StatusResult(name, VALID, "Authenticated.", data.get("Arn"), data.get("Account"))

    message = (proc.stderr or proc.stdout).strip().splitlines()
    detail = message[-1] if message else f"aws exited with {proc.returncode}"
    lowered = (proc.stderr or "").lower()
    state = EXPIRED if any(h in lowered for h in _EXPIRED_HINTS) else ERROR
    return StatusResult(name, state, detail)


def sso_login(name: str, timeout: int = 600) -> tuple[bool, str]:
    aws = find_aws()
    if not aws:
        return False, "AWS CLI not found on PATH."
    try:
        proc = subprocess.run([aws, "sso", "login", "--profile", name],
                              capture_output=True, text=True, timeout=timeout,
                              env=build_env(), stdin=subprocess.DEVNULL, **_popen_kwargs())
    except subprocess.TimeoutExpired:
        return False, "SSO login timed out."
    except OSError as exc:
        return False, str(exc)
    output = (proc.stdout + "\n" + proc.stderr).strip()
    return proc.returncode == 0, output


class _Signals(QObject):
    status = Signal(object)            # StatusResult
    login = Signal(str, bool, str)     # profile, ok, output


class _Job(QRunnable):
    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self) -> None:
        self.fn(*self.args)


class StatusChecker(QObject):
    """Runs checks and logins on a small thread pool and reports via signals."""

    status_ready = Signal(object)
    login_finished = Signal(str, bool, str)

    def __init__(self, parent=None, max_threads: int = 4):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max_threads)
        self._signals = _Signals()
        self._signals.status.connect(self.status_ready)
        self._signals.login.connect(self.login_finished)
        self._pending: set[str] = set()
        self._logins: set[str] = set()
        self.status_ready.connect(lambda r: self._pending.discard(r.profile))

    def check(self, names) -> list[str]:
        started = []
        for name in names:
            if name in self._pending:
                continue
            self._pending.add(name)
            started.append(name)
            self.pool.start(_Job(lambda n=name: self._signals.status.emit(check_profile(n))))
        return started

    def login(self, name: str) -> bool:
        if name in self._logins:
            return False
        self._logins.add(name)

        def work(n=name):
            ok, out = sso_login(n)
            self._logins.discard(n)
            self._signals.login.emit(n, ok, out)

        self.pool.start(_Job(work))
        return True
