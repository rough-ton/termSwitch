"""Saved SSH sessions.

Sessions open in terminal tabs by running the system OpenSSH client, so host key
checking, known_hosts, agents and ~/.ssh/config behave exactly as in any terminal.

Two sources:

- termSwitch's own sessions file (``ssh_sessions.json`` in the app data folder),
  created mode 0600, owner-checked and replaced atomically.
- ``Host`` entries in ``~/.ssh/config``, shown read-only. termSwitch never writes
  that file; connecting to one runs ``ssh <alias>`` and OpenSSH applies its settings.

Secrets never go in either file. Key and certificate *paths* are stored, never their
contents. A password is only kept if the user asks, and then only in the system
password store (macOS Keychain, Windows Credential Manager) through ``keyring``.
"""

from __future__ import annotations

import glob
import json
import os
import re
import stat
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from .config_store import normalize_group

FILE_NAME = "ssh_sessions.json"
FILE_FORMAT = "termswitch-ssh-sessions"
FILE_VERSION = 1
MAX_FILE_BYTES = 1_000_000
MAX_SESSIONS = 2000
SSH_CONFIG_GROUP = "~/.ssh/config"
KEYRING_SERVICE = "termSwitch SSH"

AUTH_PASSWORD, AUTH_KEY, AUTH_AGENT = "password", "key", "agent"
AUTH_METHODS = {
    AUTH_PASSWORD: "Password",
    AUTH_KEY: "Key file (and optional certificate)",
    AUTH_AGENT: "SSH agent or ~/.ssh defaults",
}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Host names, IPv4, or IPv6 (with or without brackets). Never starting with "-",
# which ssh would read as an option.
_HOST = re.compile(r"^(?:[A-Za-z0-9_](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9_])?|\[?[0-9A-Fa-f:.%]+\]?)$")
_USER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._@\\-]{0,63}$")
# ProxyJump: [user@]host[:port], comma-separated.
_JUMP_PART = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,63}@)?"
                        r"(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,252}|\[[0-9A-Fa-f:.%]+\])(?::\d{1,5})?$")

# Extra options a session may set, each with a value check. Anything that could run
# commands (ProxyCommand, LocalCommand) or weaken host checking
# (StrictHostKeyChecking, UserKnownHostsFile) is deliberately not here.
ALLOWED_OPTIONS = {
    "ServerAliveInterval": re.compile(r"^\d{1,4}$"),
    "ServerAliveCountMax": re.compile(r"^\d{1,3}$"),
    "ConnectTimeout": re.compile(r"^\d{1,4}$"),
    "Compression": re.compile(r"^(yes|no)$"),
    "ForwardAgent": re.compile(r"^(yes|no)$"),
    "AddKeysToAgent": re.compile(r"^(yes|no|ask|confirm)$"),
    "IdentitiesOnly": re.compile(r"^(yes|no)$"),
}


class SessionError(ValueError):
    pass


@dataclass
class SshSession:
    name: str
    host: str
    user: str = ""
    port: int = 22
    auth: str = AUTH_AGENT
    key_file: str = ""
    cert_file: str = ""
    jump: str = ""
    group: str | None = None
    color: str | None = None
    options: dict[str, str] = field(default_factory=dict)
    remember_password: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    from_ssh_config: bool = False       # read-only Host entry from ~/.ssh/config

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    @property
    def display_group(self) -> str:
        if self.from_ssh_config:
            return SSH_CONFIG_GROUP
        return self.group or ""

    def to_json(self) -> dict:
        data = asdict(self)
        data.pop("from_ssh_config")
        return data


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _path_problem(label: str, value: str) -> str | None:
    if not value:
        return None
    if _CONTROL.search(value) or len(value) > 1024:
        return f"{label}: not a valid path."
    if value.startswith("-"):
        return f"{label}: a path can't start with '-'."
    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded):
        return f"{label}: use a full path, such as ~/.ssh/id_ed25519."
    return None


def validate(s: SshSession) -> None:
    """Raise SessionError for anything that isn't safe to pass to ssh."""
    if not s.name or s.name != s.name.strip() or _CONTROL.search(s.name) or len(s.name) > 128:
        raise SessionError("The name can't be empty, start or end with a space, or contain "
                           "control characters.")
    if s.from_ssh_config:
        return
    if not _HOST.match(s.host or ""):
        raise SessionError("Host: enter a host name or IP address (letters, digits, '.', "
                           "'-', '_', or an IPv6 address). It can't start with '-'.")
    if s.user and not _USER.match(s.user):
        raise SessionError("User: letters, digits, '.', '_', '-', '@' and '\\' only, and it "
                           "can't start with '-'.")
    if not isinstance(s.port, int) or not 1 <= s.port <= 65535:
        raise SessionError("Port: a number from 1 to 65535.")
    if s.auth not in AUTH_METHODS:
        raise SessionError("Choose a sign-in method.")
    if s.auth == AUTH_KEY and not s.key_file:
        raise SessionError("Key file: choose the private key to sign in with.")
    for label, value in (("Key file", s.key_file), ("Certificate", s.cert_file)):
        problem = _path_problem(label, value)
        if problem:
            raise SessionError(problem)
    if s.cert_file and not s.key_file and s.auth != AUTH_AGENT:
        raise SessionError("Certificate: also choose the key file it belongs to.")
    if s.jump:
        parts = s.jump.split(",")
        if len(parts) > 5 or not all(_JUMP_PART.match(p) for p in parts):
            raise SessionError("Jump host: use [user@]host[:port], separated by commas.")
    if s.remember_password and s.auth != AUTH_PASSWORD:
        raise SessionError("Only password sign-in can remember a password.")
    for key, value in s.options.items():
        check = ALLOWED_OPTIONS.get(key)
        if check is None:
            raise SessionError(f"{key} isn't one of the options termSwitch allows.")
        if not isinstance(value, str) or not check.match(value):
            raise SessionError(f"{key}: {value!r} isn't a valid value.")
    if s.group is not None and normalize_group(s.group) != s.group:
        raise SessionError("Group: remove extra spaces and empty parts.")
    if s.color and not re.match(r"^#[0-9a-fA-F]{6}$", s.color):
        raise SessionError("Tab color must be #RRGGBB.")


# --------------------------------------------------------------------------
# The ssh command
# --------------------------------------------------------------------------

def ssh_argv(s: SshSession, ssh: str) -> list[str]:
    """argv for the system ssh client. Every value is a separate argument, and "--"
    ends the options, so nothing in a session can be read as an ssh option."""
    validate(s)
    if s.from_ssh_config:
        return [ssh, "--", s.name]
    argv = [ssh]
    if s.port != 22:
        argv += ["-p", str(s.port)]
    if s.user:
        argv += ["-l", s.user]
    if s.auth == AUTH_KEY:
        argv += ["-i", os.path.expanduser(s.key_file), "-o", "IdentitiesOnly=yes"]
    if s.cert_file:
        argv += ["-o", f"CertificateFile={os.path.expanduser(s.cert_file)}"]
    if s.auth == AUTH_PASSWORD:
        argv += ["-o", "PreferredAuthentications=keyboard-interactive,password",
                 "-o", "PubkeyAuthentication=no"]
    if s.jump:
        argv += ["-J", s.jump]
    for key, value in sorted(s.options.items()):
        argv += ["-o", f"{key}={value}"]
    return argv + ["--", s.host]


def display_command(argv: list[str]) -> str:
    """A copyable command line for the user (paths and values quoted)."""
    import shlex
    return " ".join(shlex.quote(a) for a in ["ssh", *argv[1:]])


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def default_path() -> Path:
    from PySide6.QtCore import QStandardPaths
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base or Path.home() / ".termswitch") / FILE_NAME


def _check_private(path: Path) -> None:
    """Refuse a sessions file that isn't a regular file owned by this user."""
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise SessionError(f"{path} is not a regular file.")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise SessionError(f"{path} is owned by another user.")


class SessionStore:
    """termSwitch's sessions plus the read-only ~/.ssh/config hosts."""

    def __init__(self, path: Path | None = None, ssh_config: Path | None = None):
        self.path = Path(path) if path else default_path()
        self.ssh_config = Path(ssh_config) if ssh_config else Path.home() / ".ssh" / "config"
        self.sessions: dict[str, SshSession] = {}
        self.config_hosts: dict[str, SshSession] = {}
        self.warnings: list[str] = []

    # ---- reading ---------------------------------------------------------

    def load(self) -> None:
        self.warnings = []
        self.sessions = {}
        if self.path.exists():
            try:
                _check_private(self.path)
                if self.path.stat().st_size > MAX_FILE_BYTES:
                    raise SessionError("The sessions file is too large.")
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("format") != FILE_FORMAT:
                    raise SessionError("Not a termSwitch sessions file.")
                for raw in data.get("sessions", [])[:MAX_SESSIONS]:
                    try:
                        s = _from_json(raw)
                        validate(s)
                    except (SessionError, TypeError, ValueError) as exc:
                        self.warnings.append(f"Skipped a saved session: {exc}")
                        continue
                    self.sessions[s.id] = s
            except (OSError, ValueError) as exc:
                self.warnings.append(f"Could not read {self.path}: {exc}")
        self.config_hosts = {}
        for s in read_ssh_config_hosts(self.ssh_config):
            self.config_hosts[s.id] = s

    def all(self) -> list[SshSession]:
        return [*self.sessions.values(), *self.config_hosts.values()]

    def get(self, session_id: str) -> SshSession | None:
        return self.sessions.get(session_id) or self.config_hosts.get(session_id)

    def groups(self) -> set[str]:
        from .config_store import ancestors
        found: set[str] = set()
        for s in self.sessions.values():
            found.update(ancestors(s.group))
        return found

    # ---- writing ---------------------------------------------------------

    def save(self, session: SshSession) -> None:
        if session.from_ssh_config:
            raise SessionError("Hosts from ~/.ssh/config are read-only. Edit that file, or "
                               "duplicate the host into a termSwitch session.")
        validate(session)
        if session.id not in self.sessions and len(self.sessions) >= MAX_SESSIONS:
            raise SessionError("Too many saved sessions.")
        names = {s.name for s in self.sessions.values() if s.id != session.id}
        if session.name in names:
            raise SessionError(f"A session named '{session.name}' already exists.")
        updated = {**self.sessions, session.id: session}
        self._write(updated)
        self.sessions = updated

    def delete(self, session_id: str) -> None:
        if session_id not in self.sessions:
            raise SessionError("That session no longer exists.")
        updated = {k: v for k, v in self.sessions.items() if k != session_id}
        self._write(updated)
        self.sessions = updated

    def duplicate(self, session: SshSession, name: str) -> SshSession:
        copy = replace(session, name=name, id=uuid.uuid4().hex, from_ssh_config=False,
                       remember_password=False)
        if session.from_ssh_config:
            copy = replace(copy, host=session.host or session.name)
        self.save(copy)
        return copy

    def _write(self, sessions: dict[str, SshSession]) -> None:
        data = {"format": FILE_FORMAT, "version": FILE_VERSION,
                "sessions": [s.to_json() for s in sorted(sessions.values(),
                                                          key=lambda s: s.name.lower())]}
        text = json.dumps(data, indent=2) + "\n"
        folder = self.path.parent
        if not folder.exists():
            folder.mkdir(mode=0o700, parents=True)
        if self.path.exists():
            _check_private(self.path)
        # mkstemp creates the file mode 0600 next to the target, so the replace is atomic.
        fd, tmp = tempfile.mkstemp(prefix=".ssh_sessions-", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _from_json(raw: dict) -> SshSession:
    if not isinstance(raw, dict):
        raise SessionError("not an object")
    options = raw.get("options") or {}
    if not isinstance(options, dict):
        raise SessionError("options must be an object")
    port = raw.get("port", 22)
    if isinstance(port, bool) or not isinstance(port, int):
        raise SessionError("port must be a number")
    sid = str(raw.get("id") or uuid.uuid4().hex)
    if not re.match(r"^[0-9a-f]{32}$", sid):
        raise SessionError("invalid id")
    return SshSession(
        name=str(raw["name"]), host=str(raw["host"]), user=str(raw.get("user", "")),
        port=port, auth=str(raw.get("auth", AUTH_AGENT)), key_file=str(raw.get("key_file", "")),
        cert_file=str(raw.get("cert_file", "")), jump=str(raw.get("jump", "")),
        group=raw.get("group") or None, color=raw.get("color") or None,
        options={str(k): str(v) for k, v in options.items()},
        remember_password=bool(raw.get("remember_password", False)), id=sid)


# --------------------------------------------------------------------------
# ~/.ssh/config (read-only)
# --------------------------------------------------------------------------

def read_ssh_config_hosts(path: Path, _depth: int = 0, _seen: set | None = None) -> list[SshSession]:
    """Concrete Host aliases (no wildcards or negations), with HostName, User and
    Port for display. Include directives are followed inside the home folder."""
    seen = _seen if _seen is not None else set()
    try:
        real = path.resolve()
    except OSError:
        return []
    if _depth > 4 or real in seen or not real.is_file():
        return []
    seen.add(real)
    try:
        if real.stat().st_size > MAX_FILE_BYTES:
            return []
        lines = real.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    hosts: list[SshSession] = []
    current: list[SshSession] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\S+?)\s*(?:=\s*|\s+)(.*)$", line)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2).strip().strip('"')
        if key == "host":
            current = []
            for alias in value.split():
                if any(c in alias for c in "*?!") or _CONTROL.search(alias):
                    continue
                if not _HOST.match(alias):
                    continue
                s = SshSession(name=alias, host=alias, id="sshconfig-" + alias,
                               from_ssh_config=True)
                current.append(s)
                hosts.append(s)
        elif key == "match":
            current = []
        elif key == "include" and _depth < 4:
            base = Path.home() / ".ssh"
            for pattern in value.split():
                pat = os.path.expanduser(pattern)
                if not os.path.isabs(pat):
                    pat = str(base / pat)
                for inc in sorted(glob.glob(pat))[:50]:
                    inc_path = Path(inc)
                    try:
                        inc_path.resolve().relative_to(Path.home().resolve())
                    except (OSError, ValueError):
                        continue        # only follow includes inside the home folder
                    hosts.extend(read_ssh_config_hosts(inc_path, _depth + 1, seen))
        elif current:
            for s in current:
                if key == "hostname" and _HOST.match(value):
                    s.host = value
                elif key == "user" and _USER.match(value):
                    s.user = value
                elif key == "port" and value.isdigit() and 1 <= int(value) <= 65535:
                    s.port = int(value)
                elif key == "identityfile" and not _CONTROL.search(value):
                    s.key_file = s.key_file or value
                elif key == "proxyjump" and not _CONTROL.search(value):
                    s.jump = value
    # The same alias in two files: the first one wins, as in OpenSSH.
    unique: dict[str, SshSession] = {}
    for s in hosts:
        unique.setdefault(s.name, s)
    return list(unique.values())


# --------------------------------------------------------------------------
# Passwords in the system password store
# --------------------------------------------------------------------------

# Backends termSwitch accepts. Anything else (plain-text or third-party backends,
# or keyring's "fail" backend) means passwords can't be remembered.
TRUSTED_BACKENDS = {
    "keyring.backends.macOS.Keyring",
    "keyring.backends.Windows.WinVaultKeyring",
}


class PasswordStore:
    """Wraps keyring, limited to the macOS Keychain and Windows Credential Manager.
    Passwords are looked up by session ID only when needed and never cached."""

    def __init__(self, backend=None):
        self._backend = backend
        self._checked = backend is not None

    def _get_backend(self):
        if not self._checked:
            self._checked = True
            try:
                import keyring
                backend = keyring.get_keyring()
                # A chainer wraps the real backends; accept it only if every backend
                # inside is trusted.
                inner = getattr(backend, "backends", None) or [backend]
                names = {f"{type(b).__module__}.{type(b).__name__}" for b in inner}
                if names and names <= TRUSTED_BACKENDS:
                    self._backend = backend
            except Exception:
                self._backend = None
        return self._backend

    @property
    def available(self) -> bool:
        return self._get_backend() is not None

    @staticmethod
    def store_name() -> str:
        if sys.platform == "darwin":
            return "macOS Keychain"
        if sys.platform.startswith("win"):
            return "Windows Credential Manager"
        return "system password store"

    def set(self, session_id: str, password: str) -> None:
        backend = self._get_backend()
        if backend is None:
            raise SessionError(f"The {self.store_name()} isn't available.")
        if not password or len(password) > 4096 or "\n" in password or "\r" in password:
            raise SessionError("The password can't be empty or contain line breaks.")
        backend.set_password(KEYRING_SERVICE, session_id, password)

    def get(self, session_id: str) -> str | None:
        backend = self._get_backend()
        if backend is None:
            return None
        try:
            return backend.get_password(KEYRING_SERVICE, session_id)
        except Exception:
            return None

    def has(self, session_id: str) -> bool:
        return self.get(session_id) is not None

    def delete(self, session_id: str) -> None:
        backend = self._get_backend()
        if backend is None:
            return
        try:
            backend.delete_password(KEYRING_SERVICE, session_id)
        except Exception:
            pass          # nothing saved


# --------------------------------------------------------------------------
# Filling in a saved password
# --------------------------------------------------------------------------

class PasswordAutofill:
    """Types a saved password into the tab once, at ssh's own first password prompt.

    The password is sent straight to the terminal's input (ssh has echo turned off),
    never through arguments, the environment, files or logs. It's sent at most once,
    only if the prompt names the expected user and host, only within the first
    seconds of the connection, and never after the user has typed anything. Host key
    questions and every other prompt stay in the tab for the user to answer."""

    WINDOW_SECONDS = 60

    def __init__(self, session: SshSession, fetch, write, clock=None):
        import time
        self._clock = clock or time.monotonic
        self._start = self._clock()
        self._fetch = fetch          # () -> str | None, reads the password store
        self._write = write          # (str) -> None, writes to the pty
        self.done = False
        self._tail = ""
        user = re.escape(session.user) if session.user else r"[^@\s]+"
        host = re.escape(session.host)
        # OpenSSH: "user@host's password: " and keyboard-interactive "Password: " /
        # "(user@host) Password: ".
        self._prompt = re.compile(
            rf"(?:^|\n|\r)(?:{user}@{host}'s password: |\({user}@{host}\) Password: )$")

    def user_typed(self) -> None:
        self.done = True

    def feed(self, text: str) -> bool:
        """Watch terminal output. Returns True when the password was sent."""
        if self.done:
            return False
        if self._clock() - self._start > self.WINDOW_SECONDS:
            self.done = True
            return False
        self._tail = (self._tail + text)[-512:]
        if not self._prompt.search(self._tail):
            return False
        self.done = True
        password = self._fetch()
        if not password:
            return False
        self._write(password + "\r")
        return True


# --------------------------------------------------------------------------
# Key file checks (dependency check and editor warnings)
# --------------------------------------------------------------------------

def key_file_problem(path: str) -> str | None:
    """Why a private key file is a concern, or None."""
    if not path:
        return None
    p = Path(os.path.expanduser(path))
    if not p.exists():
        return f"{path} doesn't exist."
    if not p.is_file():
        return f"{path} is not a file."
    if os.name == "posix":
        mode = p.stat().st_mode
        if mode & 0o077:
            return (f"{path} can be read by other users (mode {stat.S_IMODE(mode):o}). "
                    f"Fix with: chmod 600 {path}")
    return None

