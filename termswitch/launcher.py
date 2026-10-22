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


def find_ssh(platform: str | None = None) -> str | None:
    """The OpenSSH client: ssh.exe on Windows 10+ (System32\\OpenSSH), ssh elsewhere."""
    found = secure_which("ssh", platform)
    if found or platform_key(platform) != "win32":
        return found
    import ntpath
    builtin = ntpath.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "OpenSSH",
                          "ssh.exe")
    return builtin if os.path.isfile(builtin) else None


def ssh_env(platform: str | None = None) -> dict[str, str]:
    """Environment for an SSH tab: the usual scrubbed terminal environment, with any
    askpass override removed so ssh asks its questions in the tab itself."""
    env = terminal_env(platform)
    for var in ("SSH_ASKPASS", "SSH_ASKPASS_REQUIRE", "AWS_PROFILE"):
        env.pop(var, None)
    return env


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


# --------------------------------------------------------------------------
# Embedded terminal tabs
# --------------------------------------------------------------------------

def user_shell(platform: str | None = None) -> str:
    """The user's login shell on macOS/Linux, validated to be a real executable."""
    candidates = []
    env_shell = os.environ.get("SHELL", "")
    if env_shell:
        candidates.append(env_shell)
    try:
        import pwd
        candidates.append(pwd.getpwuid(os.getuid()).pw_shell)
    except (ImportError, KeyError):
        pass
    allowed = None
    try:
        with open("/etc/shells", encoding="utf-8") as fh:
            allowed = {ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")}
    except OSError:
        pass
    for cand in candidates:
        if (cand and os.path.isabs(cand) and os.path.isfile(cand) and os.access(cand, os.X_OK)
                and (allowed is None or cand in allowed)):
            return cand
    for fallback in ("/bin/zsh", "/bin/bash", "/bin/sh"):
        if os.path.exists(fallback):
            return fallback
    raise LaunchError("No usable shell found.")


def posix_init_script(profile: Profile, opts: LaunchOptions, aws: str | None) -> str:
    """Identity check and SSO login, in POSIX sh. Credential export happens later,
    after the user's shell startup files (see SHELL_REAPPLY)."""
    p = shlex.quote(profile.name)
    aws_cmd = shlex.quote(aws or "aws")
    check = f"{aws_cmd} sts get-caller-identity --output table"
    parts = ['printf \'\\033[36mAWS profile: %s\\033[0m\\n\' "$AWS_PROFILE"']
    if profile.is_sso and opts.auto_sso_login:
        parts.append(f"{check} || {{ printf '\\033[33mNo valid SSO token, starting aws sso "
                     f"login...\\033[0m\\n'; {aws_cmd} sso login --profile {p} && {check}; }}")
    else:
        parts.append(check)
    return "; ".join(parts)


# --------------------------------------------------------------------------
# Re-applying the tab's profile after the user's shell startup files
# --------------------------------------------------------------------------
#
# A tab starts the user's own login shell, whose startup files run after
# termSwitch set AWS_PROFILE. A line like `export AWS_PROFILE=other` in
# ~/.zshrc would silently switch every command in the tab to another account.
# So each shell is started through small wrapper files that load the user's
# real startup files first and then re-apply the tab's profile. The wrappers
# read everything from TERMSWITCH_* environment variables, so their content
# is fixed and they are written once per run to a private (0700) directory.

SHELL_REAPPLY = r"""# termSwitch: re-apply the tab's AWS profile after the user's startup files.
if [ -n "${TERMSWITCH_AWS_PROFILE-}" ]; then
  if [ "${AWS_PROFILE-}" != "$TERMSWITCH_AWS_PROFILE" ]; then
    printf '\033[33mtermSwitch: your shell startup set AWS_PROFILE=%s. This tab uses %s.\033[0m\n' \
      "${AWS_PROFILE-}" "$TERMSWITCH_AWS_PROFILE"
  fi
  if [ -n "${AWS_ACCESS_KEY_ID-}${AWS_SECRET_ACCESS_KEY-}${AWS_SESSION_TOKEN-}" ]; then
    printf '\033[33mtermSwitch: your shell startup exported AWS keys. They were removed so the profile is used.\033[0m\n'
  fi
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN \
        AWS_CREDENTIAL_EXPIRATION AWS_DEFAULT_PROFILE
  export AWS_PROFILE="$TERMSWITCH_AWS_PROFILE"
  if [ "${TERMSWITCH_EXPORT_CREDS-}" = 1 ]; then
    eval "$("${TERMSWITCH_AWS:-aws}" configure export-credentials --profile "$AWS_PROFILE" --format env)" \
      && echo 'Exported temporary credentials to the environment.'
  fi
fi
unset TERMSWITCH_AWS_PROFILE TERMSWITCH_EXPORT_CREDS TERMSWITCH_AWS TERMSWITCH_SHELL_DIR \
      TERMSWITCH_ZDOTDIR TERMSWITCH_USER_ZDOTDIR
"""

# zsh reads $ZDOTDIR/.zshenv, .zprofile, .zshrc, .zlogin in that order for an
# interactive login shell. Each wrapper sources the user's file with ZDOTDIR
# pointing at the user's directory, then points it back at the wrappers.
_ZSH_SOURCE = r"""TERMSWITCH_ZDOTDIR="$ZDOTDIR"
ZDOTDIR="${TERMSWITCH_USER_ZDOTDIR:-$HOME}"
[ -f "$ZDOTDIR/%(name)s" ] && . "$ZDOTDIR/%(name)s"
TERMSWITCH_USER_ZDOTDIR="$ZDOTDIR"
ZDOTDIR="$TERMSWITCH_ZDOTDIR"
"""
ZSH_WRAPPERS = {
    ".zshenv": _ZSH_SOURCE % {"name": ".zshenv"},
    ".zprofile": _ZSH_SOURCE % {"name": ".zprofile"},
    ".zshrc": _ZSH_SOURCE % {"name": ".zshrc"},
    # Last file of startup: hand ZDOTDIR back to the user, then re-apply.
    ".zlogin": (_ZSH_SOURCE % {"name": ".zlogin"}).replace(
        'ZDOTDIR="$TERMSWITCH_ZDOTDIR"\n', 'ZDOTDIR="$TERMSWITCH_USER_ZDOTDIR"\n')
        + '. "$TERMSWITCH_SHELL_DIR/reapply.sh"\n',
}

# bash: run as an interactive shell with this rc file, which loads what a
# login shell would, then re-applies.
BASH_RC = r"""[ -f /etc/profile ] && . /etc/profile
if [ -f "$HOME/.bash_profile" ]; then . "$HOME/.bash_profile"
elif [ -f "$HOME/.bash_login" ]; then . "$HOME/.bash_login"
elif [ -f "$HOME/.profile" ]; then . "$HOME/.profile"
elif [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"
fi
. "$TERMSWITCH_SHELL_DIR/reapply.sh"
"""

# fish runs -C commands after its config files.
FISH_REAPPLY = r"""if set -q TERMSWITCH_AWS_PROFILE
  if test "$AWS_PROFILE" != "$TERMSWITCH_AWS_PROFILE"
    printf '\e[33mtermSwitch: your shell startup set AWS_PROFILE=%s. This tab uses %s.\e[0m\n' "$AWS_PROFILE" "$TERMSWITCH_AWS_PROFILE"
  end
  if set -q AWS_ACCESS_KEY_ID; or set -q AWS_SECRET_ACCESS_KEY; or set -q AWS_SESSION_TOKEN
    printf '\e[33mtermSwitch: your shell startup exported AWS keys. They were removed so the profile is used.\e[0m\n'
  end
  for v in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN AWS_CREDENTIAL_EXPIRATION AWS_DEFAULT_PROFILE
    set -e $v
  end
  set -gx AWS_PROFILE $TERMSWITCH_AWS_PROFILE
  if test "$TERMSWITCH_EXPORT_CREDS" = 1
    $TERMSWITCH_AWS configure export-credentials --profile $AWS_PROFILE --format env | source
    and echo 'Exported temporary credentials to the environment.'
  end
end
for v in TERMSWITCH_AWS_PROFILE TERMSWITCH_EXPORT_CREDS TERMSWITCH_AWS TERMSWITCH_SHELL_DIR
  set -e $v
end
"""

_SHELL_DIR: str | None = None


def shell_wrapper_dir() -> str:
    """Private directory holding the wrapper startup files for this run."""
    global _SHELL_DIR
    if _SHELL_DIR and os.path.isdir(_SHELL_DIR):
        return _SHELL_DIR
    import atexit
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix="termswitch-shell-")       # created 0700
    zdir = os.path.join(d, "zsh")
    os.mkdir(zdir, 0o700)
    files = {os.path.join(d, "reapply.sh"): SHELL_REAPPLY,
             os.path.join(d, "bashrc"): BASH_RC,
             os.path.join(d, "reapply.fish"): FISH_REAPPLY}
    files.update({os.path.join(zdir, name): body for name, body in ZSH_WRAPPERS.items()})
    for path, body in files.items():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
    atexit.register(shutil.rmtree, d, True)
    _SHELL_DIR = d
    return d


def terminal_env(platform: str | None = None) -> dict[str, str]:
    """Environment for anything running in a termSwitch tab: credentials scrubbed,
    plus the terminal type and a locale on macOS and Linux."""
    env = build_env(platform)
    env["TERM_PROGRAM"] = "termSwitch"
    if platform_key(platform) != "win32":
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        if not env.get("LANG") and not env.get("LC_ALL"):
            env["LANG"] = "en_US.UTF-8"   # apps started from Finder get no locale
    return env


def aws_setup_command(args: list[str], platform: str | None = None,
                      aws: str | None = None) -> tuple[list[str], dict[str, str]]:
    """argv and environment to run one AWS CLI command (such as ``configure sso``)
    directly in a tab, with no profile selected."""
    aws = aws or find_aws(platform)
    if not aws:
        raise LaunchError("The AWS CLI was not found. Install AWS CLI v2 first.")
    return [aws, *args], terminal_env(platform)


def embedded_command(profile: Profile, opts: LaunchOptions, platform: str | None = None,
                     aws: str | None = None) -> tuple[list[str], dict[str, str]]:
    """argv and environment for a shell running inside a termSwitch tab."""
    problem = profile.problem
    if problem:
        raise LaunchError(f"Refusing to open '{profile.name!r}': {problem}")
    key = platform_key(platform)
    env = terminal_env(platform)
    env["AWS_PROFILE"] = profile.name

    if key == "win32":
        shell = _which("pwsh", platform) or system_powershell()
        script = powershell_script(profile, opts, aws)
        return [shell, "-NoLogo", "-NoExit", "-EncodedCommand", encode_powershell(script)], env

    shell = user_shell(platform)
    wrappers = shell_wrapper_dir()
    env["TERMSWITCH_AWS_PROFILE"] = profile.name
    env["TERMSWITCH_EXPORT_CREDS"] = "1" if opts.export_credentials else "0"
    env["TERMSWITCH_AWS"] = aws or "aws"
    env["TERMSWITCH_SHELL_DIR"] = wrappers
    # /bin/sh runs the identity check, then replaces itself with the user's
    # interactive shell, started so that the tab's profile is re-applied after
    # the user's own startup files.
    init = posix_init_script(profile, opts, aws)
    q = shlex.quote(shell)
    name = os.path.basename(shell)
    if name == "zsh":
        env["TERMSWITCH_USER_ZDOTDIR"] = os.environ.get("ZDOTDIR") or os.path.expanduser("~")
        env["ZDOTDIR"] = os.path.join(wrappers, "zsh")
        start = f"exec {q} -l"
    elif name == "bash":
        start = f"exec {q} --rcfile {shlex.quote(os.path.join(wrappers, 'bashrc'))} -i"
    elif name == "fish":
        start = f"exec {q} -l -C {shlex.quote('source ' + os.path.join(wrappers, 'reapply.fish'))}"
    else:
        # Other shells: start as before. Startup files could still override.
        start = f"exec {q} -l"
    return ["/bin/sh", "-c", f"{init}; {start}"], env


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
