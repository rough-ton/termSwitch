"""Open a terminal with AWS_PROFILE set.

Windows: Windows Terminal (wt.exe) tab or window, or a plain PowerShell console.
macOS:   Terminal.app or iTerm2 through osascript.
Linux:   gnome-terminal, konsole, xfce4-terminal or x-terminal-emulator.

The shell runs a short init script that sets the profile, checks the caller
identity, runs ``aws sso login`` when an SSO token is missing or expired, and
optionally exports credentials into the environment for tools that ignore
AWS_PROFILE.
"""

from __future__ import annotations

import base64
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from .config_store import Profile


class LaunchError(Exception):
    pass


TERMINAL_CHOICES = {
    "win32": [("auto", "Auto (Windows Terminal if installed)"),
              ("wt", "Windows Terminal"),
              ("powershell", "PowerShell console window")],
    "darwin": [("auto", "Auto (iTerm2 if installed)"),
               ("iterm", "iTerm2"),
               ("terminal", "Terminal.app")],
    "linux": [("auto", "Auto"),
              ("gnome-terminal", "GNOME Terminal"),
              ("konsole", "Konsole"),
              ("xfce4-terminal", "Xfce Terminal"),
              ("x-terminal-emulator", "x-terminal-emulator")],
}


def platform_key(platform: str | None = None) -> str:
    platform = platform or sys.platform
    if platform.startswith("win"):
        return "win32"
    if platform == "darwin":
        return "darwin"
    return "linux"


@dataclass
class LaunchOptions:
    terminal: str = "auto"
    new_window: bool = False        # tab vs window where the terminal supports it
    auto_sso_login: bool = True
    export_credentials: bool = False
    tab_color: str | None = None    # "#RRGGBB"


# --------------------------------------------------------------------------
# Finding things
# --------------------------------------------------------------------------

_EXTRA_PATHS = {
    "win32": [r"C:\Program Files\Amazon\AWSCLIV2",
              r"C:\Program Files (x86)\Amazon\AWSCLIV2"],
    # GUI apps started from Finder or the Dock get a minimal PATH.
    "darwin": ["/opt/homebrew/bin", "/usr/local/bin", "/usr/local/aws-cli"],
    "linux": ["/usr/local/bin", str(Path.home() / ".local" / "bin")],
}


# Removed from every environment the app passes on, so a stray key in the
# parent environment can never override the profile the user picked.
CREDENTIAL_ENV_VARS = (
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN", "AWS_CREDENTIAL_EXPIRATION", "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
)

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def safe_path_dirs(path_value: str, platform: str | None = None) -> list[str]:
    """Absolute, existing PATH directories only. Relative entries such as '.'
    or '' (which means the current directory on POSIX) are dropped so a file
    planted in the working directory can never be picked up."""
    seen, dirs = set(), []
    for entry in path_value.split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not os.path.isabs(entry) or not os.path.isdir(entry):
            continue
        norm = os.path.normcase(os.path.normpath(entry))
        if norm not in seen:
            seen.add(norm)
            dirs.append(entry)
    for extra in _EXTRA_PATHS[platform_key(platform)]:
        norm = os.path.normcase(os.path.normpath(extra))
        if norm not in seen and os.path.isdir(extra):
            seen.add(norm)
            dirs.append(extra)
    return dirs


def build_env(platform: str | None = None) -> dict[str, str]:
    env = dict(os.environ)
    for var in CREDENTIAL_ENV_VARS:
        env.pop(var, None)
    env["PATH"] = os.pathsep.join(safe_path_dirs(env.get("PATH", ""), platform))
    return env


def secure_which(name: str, platform: str | None = None) -> str | None:
    """Like shutil.which, but never searches the current directory and, on
    Windows, only matches .exe files. Batch-file shims (.cmd/.bat) are skipped
    because cmd.exe re-parses their arguments."""
    windows = platform_key(platform) == "win32"
    candidates = [name if name.lower().endswith(".exe") else name + ".exe"] if windows else [name]
    for directory in safe_path_dirs(os.environ.get("PATH", ""), platform):
        for cand in candidates:
            full = os.path.join(directory, cand)
            if os.path.isfile(full) and (windows or os.access(full, os.X_OK)):
                return os.path.abspath(full)
    return None


def find_aws(platform: str | None = None) -> str | None:
    return secure_which("aws", platform)


def _which(name: str, platform: str | None = None) -> str | None:
    return secure_which(name, platform)


def system_powershell() -> str:
    import ntpath
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return ntpath.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


OSASCRIPT = "/usr/bin/osascript"


def _iterm_installed() -> bool:
    return any(p.exists() for p in (Path("/Applications/iTerm.app"),
                                    Path.home() / "Applications" / "iTerm.app"))


def resolve_terminal(choice: str, platform: str | None = None) -> str:
    key = platform_key(platform)
    if choice != "auto":
        return choice
    if key == "win32":
        return "wt" if _which("wt", platform) else "powershell"
    if key == "darwin":
        return "iterm" if _iterm_installed() else "terminal"
    for name in ("gnome-terminal", "konsole", "xfce4-terminal", "x-terminal-emulator"):
        if _which(name, platform):
            return name
    raise LaunchError("No supported terminal emulator found.")


# --------------------------------------------------------------------------
# Init scripts
# --------------------------------------------------------------------------

def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def powershell_script(profile: Profile, opts: LaunchOptions, aws: str | None) -> str:
    p = _ps_quote(profile.name)
    aws_cmd = _ps_quote(aws or "aws")
    lines = [
        f"$env:AWS_PROFILE = {p}",
        "Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY, "
        "Env:AWS_SESSION_TOKEN -ErrorAction SilentlyContinue",
        f"$Host.UI.RawUI.WindowTitle = {_ps_quote('aws: ' + profile.name)}",
        f"Write-Host ('AWS profile: ' + {p}) -ForegroundColor Cyan",
        f"& {aws_cmd} sts get-caller-identity --output table",
    ]
    if profile.is_sso and opts.auto_sso_login:
        lines += [
            "if ($LASTEXITCODE -ne 0) {",
            "  Write-Host 'No valid SSO token, starting aws sso login...' -ForegroundColor Yellow",
            f"  & {aws_cmd} sso login --profile {p}",
            f"  if ($LASTEXITCODE -eq 0) {{ & {aws_cmd} sts get-caller-identity --output table }}",
            "}",
        ]
    if opts.export_credentials:
        lines += [
            f"$awsCreds = & {aws_cmd} configure export-credentials --profile {p} --format powershell",
            "if ($LASTEXITCODE -eq 0) { $awsCreds | Invoke-Expression; "
            "Write-Host 'Exported temporary credentials to the environment.' -ForegroundColor DarkGray }",
            "Remove-Variable awsCreds -ErrorAction SilentlyContinue",
        ]
    return "\n".join(lines)


def encode_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _iterm_color_escape(color: str | None) -> str:
    if not color or len(color) != 7 or not color.startswith("#"):
        return ""
    try:
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return ""
    seq = "".join(f"\\033]6;1;bg;{name};brightness;{val}\\a"
                  for name, val in (("red", r), ("green", g), ("blue", b)))
    return f"printf '{seq}'"


def posix_script(profile: Profile, opts: LaunchOptions, aws: str | None,
                 iterm: bool = False) -> str:
    p = shlex.quote(profile.name)
    aws_cmd = shlex.quote(aws or "aws")
    title = shlex.quote("aws: " + profile.name)
    parts = [
        f"export AWS_PROFILE={p}",
        "unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN",
        "clear",
        f"printf '\\033]0;%s\\007' {title}",
    ]
    if iterm:
        esc = _iterm_color_escape(opts.tab_color)
        if esc:
            parts.append(esc)
    parts.append(f"echo \"AWS profile: $AWS_PROFILE\"")
    check = f"{aws_cmd} sts get-caller-identity --output table"
    if profile.is_sso and opts.auto_sso_login:
        parts.append(f"{check} || {{ echo 'No valid SSO token, starting aws sso login...'; "
                     f"{aws_cmd} sso login --profile {p} && {check}; }}")
    else:
        parts.append(check)
    if opts.export_credentials:
        parts.append(f"eval \"$({aws_cmd} configure export-credentials --profile {p} --format env)\""
                     " && echo 'Exported temporary credentials to the environment.'")
    # Leading space keeps it out of history when HISTCONTROL/HIST_IGNORE_SPACE is on.
    return " " + "; ".join(parts)


# --------------------------------------------------------------------------
# Launchers
# --------------------------------------------------------------------------

def _safe_title(profile: Profile) -> str:
    # wt.exe treats ';' as a command separator.
    return ("aws: " + profile.name).replace(";", ",")


def build_command(profile: Profile, opts: LaunchOptions, platform: str | None = None,
                  aws: str | None = None) -> tuple[list[str], dict]:
    """Return (argv, popen_kwargs) for the configured terminal."""
    key = platform_key(platform)
    problem = profile.problem
    if problem:
        raise LaunchError(f"Refusing to open '{profile.name!r}': {problem}")
    if opts.tab_color and not _HEX_COLOR.match(opts.tab_color):
        opts = replace(opts, tab_color=None)
    terminal = resolve_terminal(opts.terminal, platform)

    if key == "win32":
        shell = _which("pwsh", platform) or system_powershell()
        encoded = encode_powershell(powershell_script(profile, opts, aws))
        shell_args = [shell, "-NoExit", "-NoLogo", "-EncodedCommand", encoded]
        if terminal == "wt":
            wt = _which("wt", platform)
            if not wt:
                raise LaunchError("Windows Terminal (wt.exe) was not found. Pick "
                                  "'PowerShell console window' in Settings or install it with:\n"
                                  "winget install --id Microsoft.WindowsTerminal -e")
            argv = [wt, "-w", "new" if opts.new_window else "0", "new-tab",
                    "--title", _safe_title(profile), "--suppressApplicationTitle"]
            if opts.tab_color:
                argv += ["--tabColor", opts.tab_color]
            return argv + ["--"] + shell_args, {}
        if terminal == "powershell":
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
            return shell_args, {"creationflags": flags}
        raise LaunchError(f"Unknown Windows terminal '{terminal}'.")

    if key == "darwin":
        if terminal == "terminal":
            script = posix_script(profile, opts, aws)
            applescript = [
                "on run argv",
                '  tell application "Terminal"',
                "    activate",
                "    set t to do script (item 1 of argv)",
                "    set custom title of t to (item 2 of argv)",
                "  end tell",
                "end run",
            ]
            argv = [OSASCRIPT]
            for line in applescript:
                argv += ["-e", line]
            return argv + ["--", script, _safe_title(profile)], {}
        if terminal == "iterm":
            script = posix_script(profile, opts, aws, iterm=True)
            applescript = [
                "on run argv",
                '  tell application "iTerm"',
                "    activate",
                '    if (count of windows) = 0 or (item 3 of argv) = "window" then',
                "      set w to (create window with default profile)",
                "    else",
                "      set w to current window",
                "      tell w to create tab with default profile",
                "    end if",
                "    tell current session of w",
                "      set name to (item 2 of argv)",
                "      write text (item 1 of argv)",
                "    end tell",
                "  end tell",
                "end run",
            ]
            argv = [OSASCRIPT]
            for line in applescript:
                argv += ["-e", line]
            mode = "window" if opts.new_window else "tab"
            return argv + ["--", script, _safe_title(profile), mode], {}
        raise LaunchError(f"Unknown macOS terminal '{terminal}'.")

    # Linux
    script = posix_script(profile, opts, aws) + "; exec \"${SHELL:-bash}\""
    exe = _which(terminal, platform)
    if not exe:
        raise LaunchError(f"{terminal} was not found on PATH.")
    if terminal == "gnome-terminal":
        argv = [exe, "--title", _safe_title(profile)]
        if not opts.new_window:
            argv.append("--tab")
        return argv + ["--", "bash", "-c", script], {}
    return [exe, "-e", "bash", "-c", script], {}


def launch(profile: Profile, opts: LaunchOptions, platform: str | None = None) -> None:
    aws = find_aws(platform)
    argv, kwargs = build_command(profile, opts, platform, aws)
    env = build_env(platform)
    try:
        if platform_key(platform) == "darwin":
            # osascript returns as soon as the terminal has the command, so wait for
            # it and surface errors such as a denied Automation permission.
            proc = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                err = proc.stderr.strip()
                if "-1743" in err or "not allowed" in err.lower() or "not authorized" in err.lower():
                    raise LaunchError(
                        "macOS blocked termSwitch from controlling the terminal.\n\n"
                        "Allow it under System Settings > Privacy & Security > Automation, "
                        "or reset the prompt with:\n  tccutil reset AppleEvents\n\n" + err)
                raise LaunchError(f"The terminal did not open:\n{err}")
            return
        subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise LaunchError("Timed out waiting for the terminal to open.") from exc
    except FileNotFoundError as exc:
        raise LaunchError(f"Could not start the terminal ({argv[0]}): {exc}") from exc
    except OSError as exc:
        raise LaunchError(f"Failed to open a terminal: {exc}") from exc
