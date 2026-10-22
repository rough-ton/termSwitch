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

from .fileutil import replace_file

GROUP_KEY = "x_group"
COLOR_KEY = "x_color"
SCHEME_KEY = "x_term_scheme"
META_SECTION = "x-termswitch"
LEGACY_META_SECTIONS = ("x-switchyard", "x-profile-launcher")
META_GROUPS_KEY = "groups"
SEP = "/"
BACKUPS_TO_KEEP = 5

SECRET_KEYS = {"aws_secret_access_key", "aws_session_token", "aws_security_token"}
SECRET_PLACEHOLDER = "<set>"
# Keys termSwitch never writes. Static keys belong in ~/.aws/credentials and are
# managed with `aws configure`.
PROTECTED_KEYS = SECRET_KEYS | {"aws_access_key_id"}
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_KEY_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$")
_SESSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d{1,2}$")
_ACCOUNT_ID = re.compile(r"^\d{12}$")
_ROLE_ARN = re.compile(r"^arn:aws[a-z-]*:iam::\d{12}:role/[\w+=,.@/-]{1,512}$")
_ROLE_NAME = re.compile(r"^[\w+=,.@-]{1,64}$")


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


def profile_name_problem(name: str) -> str | None:
    """Why a name can't be used for a new or renamed profile, or None."""
    reason = unsafe_name_reason(name)
    if reason:
        return reason
    if name != name.strip():
        return "Profile name can't start or end with a space."
    if any(c in name for c in "[]"):
        return "Profile name can't contain [ or ]."
    if name.lower().startswith(("sso-session ", "services ")) or name in (META_SECTION,):
        return "That name is reserved."
    return None


def sso_session_name_problem(name: str) -> str | None:
    if not _SESSION_NAME.match(name or ""):
        return ("SSO session names use letters, digits, '.', '_' and '-', "
                "starting with a letter or digit.")
    return None


def key_problem(key: str) -> str | None:
    if not _KEY_NAME.match(key or ""):
        return f"'{key}' is not a valid setting name."
    if key.lower() in PROTECTED_KEYS:
        return (f"termSwitch doesn't store '{key}'. Keys belong in ~/.aws/credentials; "
                "use `aws configure` or, better, AWS SSO.")
    return None


def value_problem(key: str, value: str) -> str | None:
    """Check one setting's value. Only well-known settings get format checks."""
    if _CONTROL_CHARS.search(value):
        return f"{key}: the value can't contain line breaks or control characters."
    if value != value.strip():
        return f"{key}: remove the leading or trailing spaces."
    if not value:
        return f"{key}: the value is empty."
    checks = {
        "region": (_REGION, "a region such as us-west-2"),
        "sso_region": (_REGION, "a region such as us-east-1"),
        "sso_account_id": (_ACCOUNT_ID, "a 12-digit account ID"),
        "role_arn": (_ROLE_ARN, "an IAM role ARN such as arn:aws:iam::123456789012:role/Name"),
        "sso_role_name": (_ROLE_NAME, "an IAM role name"),
    }
    if key in checks and not checks[key][0].match(value):
        return f"{key}: expected {checks[key][1]}."
    if key == "sso_start_url" and not safe_https_url(value):
        return "sso_start_url: expected an https:// URL."
    if key == "duration_seconds" and not (value.isdigit() and 900 <= int(value) <= 43200):
        return "duration_seconds: expected a number from 900 to 43200."
    if key == "output" and value not in ("json", "text", "table", "yaml", "yaml-stream"):
        return "output: expected json, text, table, yaml or yaml-stream."
    return None


def check_settings(props: dict[str, str]) -> None:
    """Raise ConfigError for the first invalid key or value."""
    for key, value in props.items():
        problem = key_problem(key) or (value_problem(key, value) if value is not None else None)
        if problem:
            raise ConfigError(problem)


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
    def term_scheme(self) -> str | None:
        """Terminal color scheme chosen for this profile, if any."""
        value = (self.props.get(SCHEME_KEY) or "").strip()
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
        self.undo_label: str | None = None     # what the last change did, if undoable
        self._undo_expected: str | None = None  # file text right after that change

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

    def set_profile_scheme(self, name: str, scheme: str | None) -> None:
        """Pick a terminal color scheme for a profile; None uses the default."""
        if scheme is not None and (_CONTROL_CHARS.search(scheme) or len(scheme) > 48
                                   or scheme != scheme.strip() or not scheme):
            raise ConfigError("Invalid color scheme name.")

        def edit(u: ConfigUpdater) -> None:
            _set_option(self._ensure_profile_section(u, name), SCHEME_KEY, scheme)

        self._edit(edit, f"Set terminal colors for {name}")

    def set_profile_color(self, name: str, color: str | None) -> None:
        def edit(u: ConfigUpdater) -> None:
            _set_option(self._ensure_profile_section(u, name), COLOR_KEY, color or None)

        self._edit(edit)


    # ---- profiles ------------------------------------------------------------

    def config_section_name(self, name: str) -> str:
        return "default" if name == "default" else f"profile {name}"

    def references_to(self, name: str) -> list[str]:
        """Profiles that use ``name`` as their source_profile."""
        return sorted((p.name for p in self.profiles.values()
                       if p.props.get("source_profile") == name), key=natural_key)

    def sso_session_users(self, session: str) -> list[str]:
        return sorted((p.name for p in self.profiles.values() if p.sso_session == session),
                      key=natural_key)

    def create_profile(self, name: str, props: dict[str, str],
                       group: str | None = None) -> None:
        problem = profile_name_problem(name)
        if problem:
            raise ConfigError(problem)
        if name in self.profiles:
            raise ConfigError(f"A profile named '{name}' already exists.")
        props = {k: v for k, v in props.items() if v not in (None, "")}
        check_settings(props)
        section = self.config_section_name(name)

        def edit(u: ConfigUpdater) -> None:
            if u.has_section(section) or (name == "default" and u.has_section("profile default")):
                raise ConfigError(f"A profile named '{name}' already exists.")
            sec = _add_section(u, section)
            for key, value in props.items():
                _set_option(sec, key, value)
            if normalize_group(group):
                _set_option(sec, GROUP_KEY, normalize_group(group))

        self._edit(edit, f"Create profile {name}")

    def update_profile(self, name: str, changes: dict[str, str | None]) -> None:
        """Apply only the given changes; None removes a key. Keys that aren't in
        ``changes`` are left exactly as they are, including secrets and nested
        settings termSwitch doesn't show."""
        if not changes:
            return
        changes = dict(changes)
        if GROUP_KEY in changes:
            changes[GROUP_KEY] = normalize_group(changes[GROUP_KEY])
        check_settings(changes)

        def edit(u: ConfigUpdater) -> None:
            sec = self._existing_config_section(u, name)
            for key, value in changes.items():
                _set_option(sec, key, value)

        self._edit(edit, f"Edit profile {name}")

    def rename_profile(self, old: str, new: str, update_references: bool = True) -> list[str]:
        """Rename a profile's config section. Returns the profiles whose
        source_profile was updated."""
        problem = profile_name_problem(new)
        if problem:
            raise ConfigError(problem)
        if new == old:
            return []
        if new in self.profiles:
            raise ConfigError(f"A profile named '{new}' already exists.")
        prof = self.profiles.get(old)
        if prof is None:
            raise ConfigError(f"Profile '{old}' no longer exists. Reload and try again.")
        if "credentials" in prof.sources:
            raise ConfigError(f"'{old}' also has keys in {self.credentials_file.name}, which "
                              "termSwitch doesn't change. Rename it there and in the config "
                              "by hand, or with `aws configure`.")
        refs = self.references_to(old) if update_references else []

        def edit(u: ConfigUpdater) -> None:
            sec = self._existing_config_section(u, old)
            sec.name = self.config_section_name(new)
            for ref_name in refs:
                _set_option(self._existing_config_section(u, ref_name), "source_profile", new)

        self._edit(edit, f"Rename profile {old} to {new}")
        return refs

    def duplicate_profile(self, source: str, new: str) -> None:
        """Copy a profile's settings to a new profile. Static keys are not copied."""
        prof = self.profiles.get(source)
        if prof is None:
            raise ConfigError(f"Profile '{source}' no longer exists. Reload and try again.")
        problem = profile_name_problem(new)
        if problem:
            raise ConfigError(problem)
        if new in self.profiles:
            raise ConfigError(f"A profile named '{new}' already exists.")
        section = self.config_section_name(new)

        def edit(u: ConfigUpdater) -> None:
            src = self._existing_config_section(u, source)
            dst = _add_section(u, section)
            for opt in src.iter_options():
                if opt.key.lower() in PROTECTED_KEYS:
                    continue
                _copy_option(opt, dst)

        self._edit(edit, f"Duplicate profile {source} as {new}")

    def delete_profiles(self, names: Iterable[str]) -> list[str]:
        """Remove profiles' config sections. Returns the names that still exist
        afterwards because they also have keys in the credentials file."""
        names = list(names)
        if not names:
            return []

        def edit(u: ConfigUpdater) -> None:
            for name in names:
                candidates = (["default", "profile default"] if name == "default"
                              else [f"profile {name}"])
                found = [c for c in candidates if u.has_section(c)]
                if not found and "credentials" not in self.profiles.get(name, Profile(name)).sources:
                    raise ConfigError(f"Profile '{name}' no longer exists. Reload and try again.")
                for c in found:
                    u.remove_section(c)

        label = f"Delete profile {names[0]}" if len(names) == 1 else f"Delete {len(names)} profiles"
        self._edit(edit, label)
        return [n for n in names if n in self.profiles]

    # ---- SSO sessions ----------------------------------------------------

    def create_sso_session(self, name: str, props: dict[str, str]) -> None:
        problem = sso_session_name_problem(name)
        if problem:
            raise ConfigError(problem)
        if name in self.sso_sessions:
            raise ConfigError(f"An SSO session named '{name}' already exists.")
        props = {k: v for k, v in props.items() if v not in (None, "")}
        for required in ("sso_start_url", "sso_region"):
            if required not in props:
                raise ConfigError(f"{required} is required.")
        check_settings(props)

        def edit(u: ConfigUpdater) -> None:
            sec = _add_section(u, f"sso-session {name}")
            for key, value in props.items():
                _set_option(sec, key, value)

        self._edit(edit, f"Create SSO session {name}")

    def update_sso_session(self, name: str, changes: dict[str, str | None]) -> None:
        for required in ("sso_start_url", "sso_region"):
            if required in changes and not changes[required]:
                raise ConfigError(f"{required} is required.")
        check_settings({k: v for k, v in changes.items()})

        def edit(u: ConfigUpdater) -> None:
            section = f"sso-session {name}"
            if not u.has_section(section):
                raise ConfigError(f"SSO session '{name}' no longer exists. Reload and try again.")
            for key, value in changes.items():
                _set_option(u[section], key, value)

        self._edit(edit, f"Edit SSO session {name}")

    def delete_sso_session(self, name: str) -> None:
        def edit(u: ConfigUpdater) -> None:
            section = f"sso-session {name}"
            if not u.has_section(section):
                raise ConfigError(f"SSO session '{name}' no longer exists. Reload and try again.")
            u.remove_section(section)

        self._edit(edit, f"Delete SSO session {name}")

    # ---- the whole file -----------------------------------------------------

    def read_raw(self) -> str:
        return _read_text(self.config_file)

    def contains_secrets(self, text: str | None = None) -> bool:
        """True if the config file itself holds static keys (it's allowed, but
        termSwitch won't load such a file into its editor)."""
        text = self.read_raw() if text is None else text
        return bool(re.search(r"^\s*(aws_secret_access_key|aws_session_token|"
                              r"aws_security_token)\s*[=:]", text, re.I | re.M))

    @staticmethod
    def parse_problem(text: str) -> tuple[int | None, str] | None:
        """(line, message) for the first parse error in config text, or None."""
        try:
            _parser().read_string(text)
        except configparser.Error as exc:
            line = getattr(exc, "lineno", None)
            if line is None:
                m = re.search(r"line:?\s*(\d+)", str(exc))
                line = int(m.group(1)) if m else None
            return line, str(exc).splitlines()[0]
        return None

    def save_raw(self, text: str) -> None:
        if not text.endswith("\n"):
            text += "\n"
        problem = self.parse_problem(text)
        if problem:
            line, msg = problem
            where = f" (line {line})" if line else ""
            raise ConfigError(f"The config doesn't parse{where}: {msg}")
        self._replace_text(text, "Edit config file")

    def backups(self) -> list[Path]:
        """termSwitch's backups of this config, newest first."""
        found = self.config_file.parent.glob(f"{self.config_file.name}.termswitch-*.bak")
        return sorted((p for p in found if p.is_file()),
                      key=lambda p: p.stat().st_mtime, reverse=True)

    def restore_backup(self, backup: Path) -> None:
        backup = Path(backup)
        if backup.resolve() not in {b.resolve() for b in self.backups()}:
            raise ConfigError("That file is not one of termSwitch's backups of this config.")
        text = backup.read_text(encoding="utf-8")
        problem = self.parse_problem(text)
        if problem:
            raise ConfigError(f"The backup doesn't parse: {problem[1]}")
        self._replace_text(text, f"Restore {backup.name}", force_backup=True)

    def reset_config(self) -> Path | None:
        """Move the config aside as a backup, leaving no config file.

        Returns the backup path, or None if there was no file. A symlinked config is
        refused, because moving it would break the link to wherever it is managed."""
        if self.config_file.is_symlink():
            raise ConfigError(f"{self.config_file} is a symlink. Reset it where it is managed.")
        if not self.config_file.exists():
            return None
        self._write_target()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self.config_file.with_name(f"{self.config_file.name}.termswitch-reset-{stamp}.bak")
        replace_file(self.config_file, dest)
        self._save_undo(dest.read_text(encoding="utf-8"), "Reset config")
        self._undo_expected = ""
        self.load()
        return dest

    # ---- undo ----------------------------------------------------------------

    @property
    def undo_file(self) -> Path:
        return self.config_file.with_name(f".{self.config_file.name}.termswitch-undo")

    def can_undo(self) -> bool:
        return self.undo_label is not None and self.undo_file.is_file()

    def undo_blocked(self) -> str | None:
        """Why the last change can't be undone safely, or None."""
        if not self.can_undo():
            return "There is nothing to undo."
        if _read_text(self.config_file) != self._undo_expected:
            return ("The config changed outside termSwitch after that change, so it can't "
                    "be undone safely. Use File > Backups instead.")
        return None

    def undo(self) -> str:
        """Put back the config as it was before the last change. Undoing again
        redoes it. Returns what was undone."""
        if not self.can_undo():
            raise ConfigError("There is nothing to undo.")
        if _read_text(self.config_file) != self._undo_expected:
            raise ConfigError("The config changed outside termSwitch after that change, so "
                              "it can't be undone safely. Use File > Backups instead.")
        label = self.undo_label
        text = self.undo_file.read_text(encoding="utf-8")
        self._replace_text(text, f"Undo: {label}")
        self.undo_label = f"Undo: {label}" if not label.startswith("Undo: ") else label[6:]
        return label

    def discard_undo(self) -> None:
        self.undo_label = None
        self._undo_expected = None
        try:
            self.undo_file.unlink()
        except OSError:
            pass

    # ---- internals -------------------------------------------------------

    def _existing_config_section(self, u: ConfigUpdater, name: str):
        for candidate in (["default", "profile default"] if name == "default"
                          else [f"profile {name}"]):
            if u.has_section(candidate):
                return u[candidate]
        if name in self.profiles and "credentials" in self.profiles[name].sources:
            raise ConfigError(f"'{name}' is only in {self.credentials_file.name}, which "
                              "termSwitch doesn't change.")
        raise ConfigError(f"Profile '{name}' no longer exists. Reload and try again.")

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

    def _edit(self, fn: Callable[[ConfigUpdater], None], label: str = "Change config") -> None:
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
        self._save_undo(original, label)
        self._atomic_write(new_text)
        self._undo_expected = new_text
        self.load()

    def _replace_text(self, text: str, label: str, force_backup: bool = False) -> None:
        """Replace the whole file (raw edit, restore, undo) with the same safety
        steps as any other write."""
        original = _read_text(self.config_file)
        if text == original:
            self.load()
            return
        self._write_target()
        self._backup(original, force=force_backup)
        self._save_undo(original, label)
        self._atomic_write(text)
        self._undo_expected = text
        self.load()

    def _save_undo(self, original: str, label: str) -> None:
        """Keep the text from before this change in a private file (mode 0600) next
        to the config, so the change can be undone. Only the last change is kept."""
        path = self.undo_file
        _ensure_private_dir(path.parent)
        fd, tmp = tempfile.mkstemp(prefix=".undo-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(original)
            replace_file(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.undo_label = label

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

    def _backup(self, original: str, force: bool = False) -> None:
        if (self._backup_done and not force) or not original:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.config_file.with_name(f"{self.config_file.name}.termswitch-{stamp}.bak")
        n = 1
        while backup.exists() and backup.read_text(encoding="utf-8") != original:
            n += 1
            backup = self.config_file.with_name(
                f"{self.config_file.name}.termswitch-{stamp}-{n}.bak")
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(original)
        # Rotate only the regular backups; reset backups are kept until removed by hand.
        old = sorted(self.config_file.parent.glob(f"{self.config_file.name}.termswitch-[0-9]*.bak"))
        for stale in old[:-BACKUPS_TO_KEEP]:
            try:
                stale.unlink()
            except OSError:
                pass
        self._backup_done = True

    def _atomic_write(self, text: str) -> None:
        target = self._write_target()
        _ensure_private_dir(target.parent)
        # mkstemp creates the file 0600 in the same directory, so the replace is atomic.
        fd, tmp = tempfile.mkstemp(prefix=".config-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            if target.exists():
                shutil.copymode(target, tmp)
            replace_file(tmp, target)
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


def _ensure_private_dir(path: Path) -> None:
    """Create a missing config folder (such as ~/.aws) readable only by the user."""
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
        try:
            os.chmod(path, 0o700)   # mkdir's mode is reduced by the umask
        except OSError:
            pass


def _copy_option(opt, dst) -> None:
    """Copy one option, including multi-line (nested) values, to another section."""
    if opt.value is not None and "\n" in str(opt.value):
        values = str(opt.value).split("\n")
        dst.set(opt.key, None)
        if values[0] == "":
            dst[opt.key].set_values(values[1:])
        else:
            dst[opt.key].set_values(values, prepend_newline=False)
    else:
        _set_option(dst, opt.key, opt.value)


def _add_section(u: ConfigUpdater, name: str):
    text = str(u)
    u.add_section(name)
    sec = u[name]
    if text.strip() and not text.endswith("\n\n"):
        sec.add_before.space()
    return sec
