"""Startup dependency and environment checks.

Run from the GUI at launch, or from the command line:

    python -m termswitch --check            # exit 1 if anything is an error
    python -m termswitch --check --offline  # skip the latest-version lookups

Every check reports a level: ok, info, warn or error. Latest-version lookups
use HTTPS to two fixed hosts (pypi.org and raw.githubusercontent.com), send no
data beyond a User-Agent, time out quickly, and are skipped when offline.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import ssl
import stat
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from importlib import metadata

from . import __version__
from .config_store import ConfigError, ProfileStore
from .launcher import (CREDENTIAL_ENV_VARS, _iterm_installed, build_env, find_aws,
                       platform_key, secure_which)

OK, INFO, WARN, ERROR = "ok", "info", "warn", "error"
LEVEL_ORDER = {OK: 0, INFO: 1, WARN: 2, ERROR: 3}

MIN_PYTHON = (3, 10)
# Upstream end-of-life month for each Python minor version.
PYTHON_EOL = {(3, 9): date(2025, 10, 31), (3, 10): date(2026, 10, 31),
              (3, 11): date(2027, 10, 31), (3, 12): date(2028, 10, 31),
              (3, 13): date(2029, 10, 31), (3, 14): date(2030, 10, 31)}

# Distribution name -> minimum version this app is tested with.
PACKAGES = {
    "PySide6-Essentials": "6.6.0",
    "shiboken6": "6.6.0",
    "configupdater": "3.1.0",
    "pyte": "0.8.2",
}
if sys.platform.startswith("win"):
    PACKAGES["pywinpty"] = "2.0.14"     # ConPTY for embedded terminals
else:
    PACKAGES["ptyprocess"] = "0.7.0"
# AWS CLI 2.9 added sso-session support and 'configure export-credentials'.
MIN_AWS_CLI = "2.9.0"

HTTP_TIMEOUT = 5
MAX_RESPONSE = 2 * 1024 * 1024
USER_AGENT = f"termSwitch/{__version__} (dependency check)"
from .links import LATEST_RELEASE_API, LATEST_RELEASE_URL as RELEASES_URL  # noqa: E402
FROZEN = bool(getattr(sys, "frozen", False))  # running from the packaged app

# Module that carries each distribution's version, for packaged builds where
# package metadata is not bundled.
_VERSION_MODULES = {"PySide6-Essentials": "PySide6", "shiboken6": "shiboken6",
                    "configupdater": "configupdater", "ptyprocess": "ptyprocess",
                    "pywinpty": "winpty"}


@dataclass
class Check:
    name: str
    level: str
    found: str
    message: str
    fix: str = ""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def parse_version(text: str | None) -> tuple[int, ...]:
    if not text:
        return ()
    match = re.match(r"(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(x) for x in match.group(1).split(".")) if match else ()


def _pad(v: tuple[int, ...], n: int = 3) -> tuple[int, ...]:
    return v + (0,) * (n - len(v))


def behind(installed: str, latest: str) -> str | None:
    """'major', 'minor', 'patch' or None if installed is current."""
    a, b = _pad(parse_version(installed)), _pad(parse_version(latest))
    if not a or not b or a >= b:
        return None
    if a[0] != b[0]:
        return "major"
    if a[1] != b[1]:
        return "minor"
    return "patch"


def _fetch(url: str) -> str:
    if not url.startswith("https://"):
        raise ValueError("only https URLs are allowed")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    ctx = ssl.create_default_context()  # verifies certificates and hostnames
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=ctx) as resp:
        return resp.read(MAX_RESPONSE).decode("utf-8", "replace")


def latest_pypi(dist: str) -> str | None:
    data = json.loads(_fetch(f"https://pypi.org/pypi/{dist}/json"))
    return data.get("info", {}).get("version")


def latest_termswitch() -> str | None:
    """Latest published release. A private repo or no releases returns None."""
    data = json.loads(_fetch(LATEST_RELEASE_API))
    tag = data.get("tag_name") or ""
    return tag.lstrip("v") or None


def installed_version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        pass
    module = _VERSION_MODULES.get(dist)
    if module:
        try:
            return getattr(__import__(module), "__version__", None)
        except ImportError:
            return None
    return None


def latest_aws_cli() -> str | None:
    # The v2 branch changelog starts with the newest release number.
    text = _fetch("https://raw.githubusercontent.com/aws/aws-cli/v2/CHANGELOG.rst")
    for line in text.splitlines()[:40]:
        if re.fullmatch(r"\d+\.\d+\.\d+", line.strip()):
            return line.strip()
    return None


def _is_elevated() -> bool:
    if platform_key() == "win32":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _no_window() -> dict:
    if platform_key() == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def _upgrade_cmd(dist: str) -> str:
    return f"python -m pip install --upgrade {dist}"


def _aws_install_hint() -> str:
    key = platform_key()
    if key == "win32":
        return "winget install --id Amazon.AWSCLI -e"
    if key == "darwin":
        return "brew install awscli   (or the official .pkg from AWS)"
    return "See https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"


def _aws_upgrade_hint(aws_path: str) -> str:
    key = platform_key()
    if key == "win32":
        return "winget upgrade --id Amazon.AWSCLI -e"
    if key == "darwin":
        return "brew upgrade awscli" if "homebrew" in aws_path or "/usr/local/Cellar" in aws_path \
            else "Reinstall the latest AWS CLI .pkg from AWS"
    return "Re-run the AWS CLI installer with --update"


# --------------------------------------------------------------------------
# individual checks
# --------------------------------------------------------------------------

def check_app(latest: str | None, online: bool) -> Check:
    found = __version__ + (" (packaged)" if FROZEN else " (source)")
    if latest and behind(__version__, latest):
        fix = (f"Download it from {RELEASES_URL}" if FROZEN
               else "git pull, then python -m pip install --require-hashes -r requirements.lock")
        return Check("termSwitch", WARN, found, f"termSwitch {latest} is available.", fix)
    if latest:
        return Check("termSwitch", OK, found, "Up to date.")
    return Check("termSwitch", OK, found,
                 "No published release to compare with." if online else "Update check skipped.")


def check_python(today: date | None = None) -> Check:
    today = today or date.today()
    v = sys.version_info[:2]
    found = ".".join(map(str, sys.version_info[:3]))
    if v < MIN_PYTHON:
        return Check("Python", ERROR, found, f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required.",
                     "Install a current Python from python.org, then recreate the venv.")
    eol = PYTHON_EOL.get(v)
    if eol and today > eol:
        return Check("Python", WARN, found,
                     f"Python {v[0]}.{v[1]} reached end of life on {eol:%Y-%m-%d} and no "
                     "longer gets security fixes.",
                     f"Download the latest termSwitch from {RELEASES_URL}" if FROZEN else
                     "Install Python 3.12 or newer, then recreate the venv.")
    if FROZEN:
        return Check("Python", OK, found + " (bundled)", "Supported.")
    if eol and (eol - today).days < 180:
        return Check("Python", INFO, found, f"Supported until {eol:%Y-%m-%d}. Plan an upgrade.")
    return Check("Python", OK, found, "Supported.")


def check_package(dist: str, minimum: str, latest: str | None, online: bool) -> Check:
    installed = installed_version(dist)
    if not installed:
        return Check(dist, ERROR, "not installed", "Required package is missing.",
                     "python -m pip install --require-hashes -r requirements.lock")
    if FROZEN:
        # Bundled libraries can only be updated by a new termSwitch release.
        if latest and behind(installed, latest):
            return Check(dist, INFO, f"{installed} (bundled)",
                         f"Upstream has {latest}. It ships with the next termSwitch release.")
        return Check(dist, OK, f"{installed} (bundled)", "Bundled with termSwitch.")
    if parse_version(installed) < parse_version(minimum):
        return Check(dist, ERROR, installed, f"Version {minimum} or newer is required.",
                     _upgrade_cmd(dist))
    if latest:
        lag = behind(installed, latest)
        if lag in ("major", "minor"):
            return Check(dist, WARN, installed, f"Out of date. Latest is {latest}.",
                         _upgrade_cmd(dist))
        if lag == "patch":
            return Check(dist, INFO, installed, f"Patch update available: {latest}.",
                         _upgrade_cmd(dist))
        return Check(dist, OK, installed, "Up to date.")
    note = "Could not check for a newer version." if online else "Latest-version check skipped."
    return Check(dist, OK, installed, f"Meets the minimum ({minimum}). {note}")


def check_aws_cli(latest: str | None, online: bool) -> Check:
    aws = find_aws()
    if not aws:
        return Check("AWS CLI", ERROR, "not found",
                     "AWS CLI v2 was not found on PATH. Status checks, SSO login and the "
                     "terminal identity check need it.", _aws_install_hint())
    try:
        proc = subprocess.run([aws, "--version"], capture_output=True, text=True, timeout=20,
                              env=build_env(), stdin=subprocess.DEVNULL, **_no_window())
        out = (proc.stdout + proc.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check("AWS CLI", ERROR, aws, f"Could not run 'aws --version': {exc}")
    match = re.search(r"aws-cli/(\d+\.\d+\.\d+)", out)
    if not match:
        return Check("AWS CLI", ERROR, aws, f"Unrecognized version output: {out[:200]}")
    version = match.group(1)
    found = f"{version} ({aws})"
    if parse_version(version)[0] < 2:
        return Check("AWS CLI", ERROR, found, "AWS CLI v1 is installed. v2 is required for "
                     "IAM Identity Center sso-session profiles.", _aws_install_hint())
    if parse_version(version) < parse_version(MIN_AWS_CLI):
        return Check("AWS CLI", ERROR, found, f"Version {MIN_AWS_CLI} or newer is required.",
                     _aws_upgrade_hint(aws))
    if latest:
        lag = behind(version, latest)
        if lag in ("major", "minor"):
            return Check("AWS CLI", WARN, found, f"Out of date. Latest is {latest}.",
                         _aws_upgrade_hint(aws))
        if lag == "patch":
            return Check("AWS CLI", INFO, found,
                         f"Newer release available: {latest}. AWS ships patch releases "
                         "almost daily, so this is informational.", _aws_upgrade_hint(aws))
        return Check("AWS CLI", OK, found, "Up to date.")
    note = "Could not check for a newer version." if online else "Latest-version check skipped."
    return Check("AWS CLI", OK, found, f"Meets the minimum ({MIN_AWS_CLI}). {note}")


def check_terminals() -> list[Check]:
    key = platform_key()
    checks = []
    if key == "win32":
        wt = secure_which("wt")
        checks.append(Check("Windows Terminal", OK if wt else WARN, wt or "not found",
                            "Tabs, titles and tab colors available." if wt else
                            "Not found. Terminals will open as plain PowerShell windows.",
                            "" if wt else "winget install --id Microsoft.WindowsTerminal -e"))
        pwsh = secure_which("pwsh")
        checks.append(Check("PowerShell 7", OK if pwsh else INFO, pwsh or "not found",
                            "Used for new terminals." if pwsh else
                            "Not found. Windows PowerShell 5.1 will be used instead.",
                            "" if pwsh else "winget install --id Microsoft.PowerShell -e"))
    elif key == "darwin":
        if not os.path.exists("/usr/bin/osascript"):
            checks.append(Check("osascript", ERROR, "missing",
                                "/usr/bin/osascript is required to open terminals."))
        iterm = _iterm_installed()
        checks.append(Check("Terminal", OK, "iTerm2" if iterm else "Terminal.app",
                            "iTerm2 found." if iterm else
                            "Using Terminal.app. Install iTerm2 for tabs and tab colors.",
                            "" if iterm else "brew install --cask iterm2"))
    else:
        found = next((t for t in ("gnome-terminal", "konsole", "xfce4-terminal",
                                  "x-terminal-emulator") if secure_which(t)), None)
        checks.append(Check("Terminal", OK if found else ERROR, found or "not found",
                            "Found." if found else "No supported terminal emulator found."))
    return checks


def _posix_mode_checks(label: str, path, private: bool) -> list[Check]:
    checks = []
    try:
        real = path.resolve()
        st = real.stat()
    except (FileNotFoundError, OSError):
        return checks
    mode = stat.S_IMODE(st.st_mode)
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        checks.append(Check(label, WARN, str(real), "Owned by another user. termSwitch will "
                            "not write to it.", f"sudo chown $(id -u) {real}"))
    if mode & 0o022:
        checks.append(Check(label, WARN, f"{real} ({mode:o})",
                            "Writable by other users. Someone could change role ARNs or "
                            "credential_process commands.", f"chmod 600 {real}"))
    elif private and mode & 0o077:
        checks.append(Check(label, WARN, f"{real} ({mode:o})",
                            "Readable by other users and it may hold access keys.",
                            f"chmod 600 {real}"))
    return checks


def check_config_files(store: ProfileStore | None = None) -> list[Check]:
    store = store or ProfileStore()
    checks = []
    cfg = store.config_file
    if not cfg.exists():
        checks.append(Check("AWS config", WARN, str(cfg), "File does not exist yet. Create "
                            "profiles with 'aws configure sso'.", "aws configure sso"))
    else:
        try:
            store.load()
        except ConfigError as exc:
            checks.append(Check("AWS config", ERROR, str(cfg), str(exc).splitlines()[-1],
                                "Fix the syntax error in the file, then reload."))
        else:
            bad = [p.name for p in store.profiles.values() if p.problem]
            if bad:
                checks.append(Check("AWS config", WARN, str(cfg),
                                    f"{len(bad)} profile name(s) contain unsafe characters "
                                    "and will not be opened."))
            n = len(store.profiles)
            detail = f"{n} profile(s) loaded."
            if cfg.is_symlink():
                detail += f" Symlink to {cfg.resolve()}; writes go to the target."
            checks.append(Check("AWS config", OK, str(cfg), detail))
    if platform_key() != "win32":
        checks += _posix_mode_checks("AWS config", cfg, private=False)
        checks += _posix_mode_checks("AWS credentials", store.credentials_file, private=True)
    return checks


def check_environment() -> list[Check]:
    checks = []
    if _is_elevated():
        checks.append(Check("Privileges", WARN, "elevated",
                            "Running as administrator/root. Terminals it opens inherit "
                            "those rights. Run termSwitch as your normal user."))
    stray = [v for v in CREDENTIAL_ENV_VARS if os.environ.get(v)]
    if stray:
        checks.append(Check("Environment", INFO, ", ".join(stray),
                            "Set in termSwitch's environment. They are removed before "
                            "launching terminals and checks, so they cannot override the "
                            "profile you pick."))
    raw_path = os.environ.get("PATH", "").split(os.pathsep)
    relative = [p for p in raw_path if p and not os.path.isabs(p.strip('"'))]
    if relative or (platform_key() != "win32" and "" in raw_path):
        checks.append(Check("PATH", WARN, ", ".join(relative) or "(empty entry)",
                            "PATH has relative entries. termSwitch ignores them, but other "
                            "tools may run programs from the current directory."))
    return checks


# --------------------------------------------------------------------------
# run all
# --------------------------------------------------------------------------

def run_checks(online: bool = True, store: ProfileStore | None = None) -> list[Check]:
    latest: dict[str, str | None] = {}
    if online:
        jobs = {name: (latest_pypi, name) for name in PACKAGES}
        jobs["aws-cli"] = (latest_aws_cli,)
        jobs["termswitch"] = (latest_termswitch,)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {k: pool.submit(v[0], *v[1:]) for k, v in jobs.items()}
            for k, fut in futures.items():
                try:
                    latest[k] = fut.result(timeout=HTTP_TIMEOUT + 2)
                except Exception:
                    latest[k] = None

    checks = [check_app(latest.get("termswitch"), online), check_python()]
    for dist, minimum in PACKAGES.items():
        checks.append(check_package(dist, minimum, latest.get(dist), online))
    checks.append(check_aws_cli(latest.get("aws-cli"), online))
    checks += check_terminals()
    checks += check_config_files(store)
    checks += check_environment()
    if online and not any(v for k, v in latest.items() if k != "termswitch"):
        checks.append(Check("Update check", INFO, "offline",
                            "Could not reach pypi.org or GitHub. Version minimums were "
                            "still checked locally."))
    return checks


def worst(checks: list[Check]) -> str:
    return max((c.level for c in checks), key=LEVEL_ORDER.get, default=OK)


def summary(checks: list[Check]) -> str:
    errors = sum(c.level == ERROR for c in checks)
    warns = sum(c.level == WARN for c in checks)
    if errors:
        return f"{errors} error(s), {warns} warning(s)"
    if warns:
        return f"{warns} warning(s)"
    return "All dependencies OK"


def print_report(checks: list[Check]) -> None:
    marks = {OK: "OK  ", INFO: "INFO", WARN: "WARN", ERROR: "FAIL"}
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{marks[c.level]}] {c.name:<{width}}  {c.found}")
        print(f"       {' ' * width}  {c.message}")
        if c.fix and c.level != OK:
            print(f"       {' ' * width}  fix: {c.fix}")
    print()
    print(summary(checks))
