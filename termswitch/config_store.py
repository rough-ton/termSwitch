"""Read AWS profiles and write group assignments back to the AWS config file.

The config file is the source of truth. Each profile's group lives in a custom
key (``x_group``) inside its own section, using ``/`` for nesting:

    [profile acme-prod-admin]
    sso_session = acme
    x_group = Acme/Prod

Groups that have no profiles yet are kept in an extra section so they survive
a reload:

    [x-profile-launcher]
    groups = Acme/Sandbox, Personal

Botocore and the AWS CLI ignore keys and sections they do not recognize.

Writes go through ``configupdater`` so comments, ordering and formatting are
preserved. Every write is validated, the first write of each session makes a
timestamped backup, and the file is replaced atomically.
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

from configupdater import ConfigUpdater

GROUP_KEY = "x_group"
COLOR_KEY = "x_color"
META_SECTION = "x-termswitch"
LEGACY_META_SECTIONS = ("x-switchyard", "x-profile-launcher")
META_GROUPS_KEY = "groups"
SEP = "/"
BACKUPS_TO_KEEP = 5

SECRET_KEYS = {"aws_secret_access_key", "aws_session_token", "aws_security_token"}
SECRET_PLACEHOLDER = "<set>"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class ConfigError(Exception):
    """Raised when the config cannot be parsed or a write would be unsafe."""


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def default_config_path() -> Path:
    env = os.environ.get("AWS_CONFIG_FILE")
    return Path(env).expanduser() if env else Path.home() / ".aws" / "config"


def default_credentials_path() -> Path:
    env = os.environ.get("AWS_SHARED_CREDENTIALS_FILE")
    return Path(env).expanduser() if env else Path.home() / ".aws" / "credentials"


# --------------------------------------------------------------------------
# Group path helpers
# --------------------------------------------------------------------------

def normalize_group(path: str | None) -> str | None:
    """Trim segments and drop empty ones. Returns None for an empty path."""
    if not path:
        return None
    parts = [p.strip() for p in str(path).split(SEP)]
    joined = SEP.join(p for p in parts if p)
    return joined or None


def parent_group(path: str | None) -> str | None:
    if not path or SEP not in path:
        return None
    return path.rsplit(SEP, 1)[0]


def leaf_name(path: str) -> str:
    return path.rsplit(SEP, 1)[-1]


def join_group(parent: str | None, child: str) -> str | None:
    return normalize_group(f"{parent}{SEP}{child}" if parent else child)


def ancestors(path: str | None) -> Iterator[str]:
    """Yield every prefix of a path: 'A/B/C' -> 'A', 'A/B', 'A/B/C'."""
    if not path:
        return
    parts = path.split(SEP)
    for i in range(1, len(parts) + 1):
        yield SEP.join(parts[:i])


def is_within(path: str | None, prefix: str) -> bool:
    return bool(path) and (path == prefix or path.startswith(prefix + SEP))


def remap(path: str | None, old: str, new: str | None) -> str | None:
    """Move ``path`` from under ``old`` to under ``new`` (None = top level)."""
    if not is_within(path, old):
        return path
    rest = path[len(old):].lstrip(SEP)
    return normalize_group(SEP.join(p for p in (new, rest) if p))


def natural_key(text: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def unsafe_name_reason(name: str) -> str | None:
    """Why a profile name is refused for launching, or None if it is fine.

    Names reach shells, AppleScript and terminal titles. They are always quoted,
    but control characters could still inject terminal escape sequences.
    """
    if not name or not name.strip():
        return "Profile name is empty."
    if _CONTROL_CHARS.search(name):
        return "Profile name contains control characters."
    if len(name) > 128:
        return "Profile name is longer than 128 characters."
    return None


def safe_https_url(url: str | None) -> str | None:
    """Return the URL only if it is a plain https:// URL without control characters."""
    if not url or _CONTROL_CHARS.search(url):
        return None
    if not re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?(/[^\s]*)?$", url.strip()):
        return None
    return url.strip()


def scrub_value(key: str, value: str) -> str:
    """Never keep secret values in memory; keep only that they exist."""
    if key in SECRET_KEYS:
        return SECRET_PLACEHOLDER
    if key == "aws_access_key_id":
        return value[:4] + "..." + value[-4:] if len(value) > 8 else SECRET_PLACEHOLDER
    return value


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

def section_to_profile_name(section: str) -> str | None:
    if section == "default":
        return "default"
    if section.startswith("profile "):
        name = section[len("profile "):].strip()
        return name or None
    return None


@dataclass
class Profile:
    name: str
    props: dict[str, str] = field(default_factory=dict)
    sources: set[str] = field(default_factory=set)  # {"config", "credentials"}
    config_section: str | None = None
    sso_session_props: dict[str, str] = field(default_factory=dict)

    @property
    def group(self) -> str | None:
        return normalize_group(self.props.get(GROUP_KEY))

    @property
    def color(self) -> str | None:
        value = (self.props.get(COLOR_KEY) or "").strip()
        return value or None

    @property
    def problem(self) -> str | None:
        return unsafe_name_reason(self.name)

    @property
    def kind(self) -> str:
        p = self.props
        if "sso_session" in p or "sso_start_url" in p:
            return "sso"
        if "credential_process" in p:
            return "process"
        if "role_arn" in p:
            return "role"
        if "aws_access_key_id" in p:
            return "static"
        return "other"

    @property
    def is_sso(self) -> bool:
        return self.kind == "sso"

    @property
    def sso_session(self) -> str | None:
        return self.props.get("sso_session")

    @property
    def sso_start_url(self) -> str | None:
        return self.props.get("sso_start_url") or self.sso_session_props.get("sso_start_url")

    @property
    def sso_login_key(self) -> str | None:
        """Profiles with the same key share one SSO token."""
        if not self.is_sso:
            return None
        if self.sso_session:
            return f"session:{self.sso_session}"
        return f"url:{self.sso_start_url}"

    @property
    def account_id(self) -> str | None:
        if self.props.get("sso_account_id"):
            return self.props["sso_account_id"]
        match = re.match(r"arn:aws[\w-]*:iam::(\d{12}):", self.props.get("role_arn", ""))
        return match.group(1) if match else None

    @property
    def role(self) -> str | None:
        if self.props.get("sso_role_name"):
            return self.props["sso_role_name"]
        arn = self.props.get("role_arn")
        return arn.rsplit("/", 1)[-1] if arn else None

    @property
    def region(self) -> str | None:
        return self.props.get("region")

    def display_props(self) -> list[tuple[str, str]]:
        """Properties for display with secrets masked."""
        return [(key, scrub_value(key, self.props[key])) for key in sorted(self.props)]


def _parser() -> configparser.RawConfigParser:
    return configparser.RawConfigParser(strict=False, interpolation=None)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------

class ProfileStore:
    def __init__(self, config_file: Path | str | None = None,
                 credentials_file: Path | str | None = None):
        self.config_file = Path(config_file) if config_file else default_config_path()
        self.credentials_file = (Path(credentials_file) if credentials_file
                                 else default_credentials_path())
        self.profiles: dict[str, Profile] = {}
        self.sso_sessions: dict[str, dict[str, str]] = {}
        self.extra_groups: set[str] = set()
        self.loaded_text: str = ""
        self._backup_done = False

    # ---- reading ---------------------------------------------------------

    def load(self) -> None:
        text = _read_text(self.config_file)
        cfg = _parser()
        try:
            cfg.read_string(text, source=str(self.config_file))
        except configparser.Error as exc:
            raise ConfigError(f"Could not parse {self.config_file}:\n{exc}") from exc

        creds = _parser()
        try:
            creds.read_string(_read_text(self.credentials_file),
                              source=str(self.credentials_file))
        except configparser.Error:
            creds = _parser()  # a broken credentials file should not block the app

        sessions: dict[str, dict[str, str]] = {}
        profiles: dict[str, Profile] = {}
        extra: set[str] = set()

        for section in cfg.sections():
            if section.startswith("sso-session "):
                sessions[section[len("sso-session "):].strip()] = dict(cfg.items(section))
            elif section == META_SECTION or section in LEGACY_META_SECTIONS:
                raw = cfg.get(section, META_GROUPS_KEY, fallback="")
                for item in re.split(r"[,\n]", raw):
                    g = normalize_group(item)
                    if g:
                        extra.add(g)
            else:
                name = section_to_profile_name(section)
                if not name:
                    continue
                prof = profiles.setdefault(name, Profile(name=name))
                # [profile default] and [default] can both exist; merge them.
                prof.props.update({k: scrub_value(k, v) for k, v in cfg.items(section)})
                prof.sources.add("config")
                if prof.config_section is None or section == "default":
                    prof.config_section = section

        for section in creds.sections():
            prof = profiles.setdefault(section, Profile(name=section))
            for key, value in creds.items(section):
                prof.props.setdefault(key, scrub_value(key, value))
            prof.sources.add("credentials")

        for prof in profiles.values():
            if prof.sso_session:
                prof.sso_session_props = sessions.get(prof.sso_session, {})

        self.profiles = profiles
        self.sso_sessions = sessions
        self.extra_groups = extra
        self.loaded_text = text

    def file_changed_on_disk(self) -> bool:
        return _read_text(self.config_file) != self.loaded_text

    def all_groups(self) -> set[str]:
        groups: set[str] = set()
        for g in [p.group for p in self.profiles.values()] + list(self.extra_groups):
            groups.update(ancestors(g))
        return groups

    def profiles_in(self, group: str, recursive: bool = True) -> list[Profile]:
        if recursive:
            return [p for p in self.profiles.values() if is_within(p.group, group)]
        return [p for p in self.profiles.values() if p.group == group]

    # ---- write operations ------------------------------------------------

    def set_profile_groups(self, assignments: dict[str, str | None]) -> None:
        """Assign profiles to groups. None clears the assignment."""
        self.move(profile_targets=assignments)

    def move(self, profile_targets: dict[str, str | None] | None = None,
             group_moves: Iterable[tuple[str, str | None]] = ()) -> None:
        """Move profiles and/or groups in a single write.

        ``group_moves`` is a list of (old_path, new_path). new_path of None
        dissolves the group into the top level.
        """
        profile_targets = {k: normalize_group(v) for k, v in (profile_targets or {}).items()}
        group_moves = [(normalize_group(o), normalize_group(n)) for o, n in group_moves]
        for old, new in group_moves:
            if not old:
                raise ConfigError("Cannot move the top level.")
            if new and is_within(new, old):
                raise ConfigError(f"Cannot move '{old}' into itself.")

        def edit(u: ConfigUpdater) -> None:
            groups = _current_groups(u, self._meta_groups(u))
            for old, new in group_moves:
                for _name, sec in _profile_sections(u):
                    current = _get_group(sec)
                    updated = remap(current, old, new)
                    if updated != current:
                        _set_option(sec, GROUP_KEY, updated)
                groups = {g for g in (remap(x, old, new) for x in groups) if g}
            for name, target in profile_targets.items():
                sec = self._ensure_profile_section(u, name)
                _set_option(sec, GROUP_KEY, target)
            # Groups emptied by this move are kept until deleted explicitly.
            self._write_meta_groups(u, groups)

        self._edit(edit)

    def add_group(self, path: str) -> None:
        path = normalize_group(path)
        if not path:
            raise ConfigError("Group name is empty.")
        if path in self.all_groups():
            return

        def edit(u: ConfigUpdater) -> None:
            meta = self._meta_groups(u)
            meta.add(path)
            self._write_meta_groups(u, meta)

        self._edit(edit)

    def rename_group(self, old: str, new: str) -> None:
        self.move(group_moves=[(old, new)])

    def delete_group(self, path: str) -> None:
        """Remove a group. Its contents move up one level."""
        path = normalize_group(path)
        parent = parent_group(path)

        def edit(u: ConfigUpdater) -> None:
            groups = _current_groups(u, self._meta_groups(u))
            for _name, sec in _profile_sections(u):
                current = _get_group(sec)
                updated = remap(current, path, parent)
                if updated != current:
                    _set_option(sec, GROUP_KEY, updated)
            remaining = set()
            for g in groups:
                if g == path:
                    continue
                m = remap(g, path, parent)
                if m:
                    remaining.add(m)
            self._write_meta_groups(u, remaining)

        self._edit(edit)

    def set_profile_color(self, name: str, color: str | None) -> None:
        def edit(u: ConfigUpdater) -> None:
            _set_option(self._ensure_profile_section(u, name), COLOR_KEY, color or None)

        self._edit(edit)

    # ---- internals -------------------------------------------------------

    def _ensure_profile_section(self, u: ConfigUpdater, name: str):
        for candidate in (["default", "profile default"] if name == "default"
                          else [f"profile {name}"]):
            if u.has_section(candidate):
                return u[candidate]
        if name not in self.profiles:
            raise ConfigError(f"Profile '{name}' no longer exists. Reload and try again.")
        # Credentials-only profile: create a config section to hold the group.
        return _add_section(u, "default" if name == "default" else f"profile {name}")

    @staticmethod
    def _meta_groups(u: ConfigUpdater) -> set[str]:
        groups: set[str] = set()
        for name in (META_SECTION, *LEGACY_META_SECTIONS):
            if not u.has_section(name):
                continue
            opt = u[name].get(META_GROUPS_KEY)
            raw = opt.value if opt is not None and opt.value else ""
            groups.update(g for g in (normalize_group(x) for x in re.split(r"[,\n]", raw)) if g)
        return groups

    @staticmethod
    def _write_meta_groups(u: ConfigUpdater, groups: set[str]) -> None:
        # Only groups that would otherwise vanish need to be stored.
        used = set()
        for _name, sec in _profile_sections(u):
            used.update(ancestors(_get_group(sec)))
        keep = sorted((g for g in groups if g not in used), key=natural_key)
        for legacy in LEGACY_META_SECTIONS:
            if u.has_section(legacy):
                u.remove_section(legacy)
        if not keep:
            if u.has_section(META_SECTION):
                u.remove_section(META_SECTION)
            return
        sec = u[META_SECTION] if u.has_section(META_SECTION) else _add_section(u, META_SECTION)
        _set_option(sec, META_GROUPS_KEY, ", ".join(keep))

    def _edit(self, fn: Callable[[ConfigUpdater], None]) -> None:
        original = _read_text(self.config_file)
        source = original if not original or original.endswith("\n") else original + "\n"
        updater = ConfigUpdater(strict=False)
        try:
            updater.read_string(source)
        except Exception as exc:  # configupdater raises several error types
            raise ConfigError(f"Could not parse {self.config_file} for writing:\n{exc}") from exc

        fn(updater)
        new_text = str(updater)
        if new_text == source:
            self.load()
            return

        # Validate the result before touching the real file.
        check = _parser()
        try:
            check.read_string(new_text)
        except configparser.Error as exc:
            raise ConfigError(f"Refusing to write an invalid config:\n{exc}") from exc

        self._write_target()  # fail before making a backup if the target is unsafe
        self._backup(original)
        self._atomic_write(new_text)
        self.load()

    def _write_target(self) -> Path:
        """The real file to replace. Follows a symlink (dotfile repos) instead of
        replacing the link, and refuses anything that is not a regular file the
        current user owns."""
        target = self.config_file
        if target.is_symlink():
            target = target.resolve(strict=False)
        if target.exists():
            st = target.lstat()
            import stat as _stat
            if not _stat.S_ISREG(st.st_mode):
                raise ConfigError(f"{target} is not a regular file. Refusing to write.")
            if hasattr(os, "getuid") and st.st_uid != os.getuid():
                raise ConfigError(f"{target} is owned by another user. Refusing to write.")
        return target

    def _backup(self, original: str) -> None:
        if self._backup_done or not original:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.config_file.with_name(f"{self.config_file.name}.termswitch-{stamp}.bak")
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(original)
        old = sorted(self.config_file.parent.glob(f"{self.config_file.name}.termswitch-*.bak"))
        for stale in old[:-BACKUPS_TO_KEEP]:
            try:
                stale.unlink()
            except OSError:
                pass
        self._backup_done = True

    def _atomic_write(self, text: str) -> None:
        target = self._write_target()
        target.parent.mkdir(parents=True, exist_ok=True)
        # mkstemp creates the file 0600 in the same directory, so os.replace is atomic.
        fd, tmp = tempfile.mkstemp(prefix=".config-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            if target.exists():
                shutil.copymode(target, tmp)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# --------------------------------------------------------------------------
# configupdater helpers
# --------------------------------------------------------------------------

def _profile_sections(u: ConfigUpdater):
    for section in u.sections():
        name = section_to_profile_name(section)
        if name:
            yield name, u[section]


def _current_groups(u: ConfigUpdater, meta: set[str]) -> set[str]:
    groups = set()
    for g in meta:
        groups.update(ancestors(g))
    for _name, sec in _profile_sections(u):
        groups.update(ancestors(_get_group(sec)))
    return groups


def _get_group(sec) -> str | None:
    opt = sec.get(GROUP_KEY)
    return normalize_group(opt.value) if opt is not None else None


def _set_option(sec, key: str, value: str | None) -> None:
    """Set or remove an option, inserting new ones after the section's last
    option so they don't land below trailing comments or blank lines."""
    opt = sec.get(key)
    if value is None:
        if opt is not None:
            del sec[key]
        return
    if opt is not None:
        opt.value = value
        return
    options = list(sec.iter_options())
    if options:
        options[-1].add_after.option(key, value)
        return
    blocks = list(sec.iter_blocks())
    if blocks:
        blocks[0].add_before.option(key, value)
    else:
        sec[key] = value


def _add_section(u: ConfigUpdater, name: str):
    text = str(u)
    u.add_section(name)
    sec = u[name]
    if text.strip() and not text.endswith("\n\n"):
        sec.add_before.space()
    return sec
