"""Read the color scheme of the user's own terminal, for "Match my system terminal".

Read only: termSwitch never writes to these files. They are parsed as data (JSON or
property lists), sizes are capped, and every color is validated by ColorScheme.

- Windows: Windows Terminal's settings.json (the Store, Preview and unpackaged
  installs). The default profile's color scheme, or Campbell when none is set. Without
  Windows Terminal, the console host's Campbell colors.
- macOS: iTerm2's default profile when the external terminal setting is iTerm2 (or Auto
  with iTerm2 installed), otherwise Terminal.app's default profile.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
from dataclasses import dataclass
from pathlib import Path

from .term_schemes import ColorScheme, SchemeError, _iterm_color, _wt_scheme

MAX_BYTES = 5 * 1024 * 1024
MATCH_SYSTEM = "system-terminal"          # setting value for "Match my system terminal"


@dataclass(frozen=True)
class SystemScheme:
    """What was found: a scheme, or the reason there isn't one."""
    source: str                           # "Windows Terminal", "iTerm2", "Terminal.app"
    scheme: ColorScheme | None
    detail: str                           # shown in Settings


# --------------------------------------------------------------------------
# Windows Terminal
# --------------------------------------------------------------------------

# Windows Terminal's built-in schemes, from microsoft/terminal
# (src/cascadia/TerminalSettingsModel/defaults.json, MIT, Copyright (c) Microsoft
# Corporation). Most profiles use one of these, and settings.json only lists schemes
# the user added.
WT_BUILTIN: dict[str, dict] = {
    "Dimidium": {
        "background": "#141414", "foreground": "#bab7b6", "cursorColor": "#37e57b",
        "selectionBackground": "#8db8e5", "black": "#000000", "red": "#cf494c", "green":
        "#60b442", "yellow": "#db9c11", "blue": "#0575d8", "purple": "#af5ed2", "cyan":
        "#1db6bb", "white": "#bab7b6", "brightBlack": "#817e7e", "brightRed": "#ff643b",
        "brightGreen": "#37e57b", "brightYellow": "#fccd1a", "brightBlue": "#688dfd",
        "brightPurple": "#ed6fe9", "brightCyan": "#32e0fb", "brightWhite": "#dee3e4"
    },
    "Ottosson": {
        "background": "#000000", "foreground": "#bebebe", "cursorColor": "#ffffff",
        "selectionBackground": "#92a4fd", "black": "#000000", "red": "#be2c21", "green":
        "#3fae3a", "yellow": "#be9a4a", "blue": "#204dbe", "purple": "#bb54be", "cyan":
        "#00a7b2", "white": "#bebebe", "brightBlack": "#808080", "brightRed": "#ff3e30",
        "brightGreen": "#58ea51", "brightYellow": "#ffc944", "brightBlue": "#2f6aff",
        "brightPurple": "#fc74ff", "brightCyan": "#00e1f0", "brightWhite": "#ffffff"
    },
    "Campbell": {
        "background": "#0c0c0c", "foreground": "#cccccc", "cursorColor": "#ffffff", "black":
        "#0c0c0c", "red": "#c50f1f", "green": "#13a10e", "yellow": "#c19c00", "blue": "#0037da",
        "purple": "#881798", "cyan": "#3a96dd", "white": "#cccccc", "brightBlack": "#767676",
        "brightRed": "#e74856", "brightGreen": "#16c60c", "brightYellow": "#f9f1a5",
        "brightBlue": "#3b78ff", "brightPurple": "#b4009e", "brightCyan": "#61d6d6",
        "brightWhite": "#f2f2f2"
    },
    "Campbell Powershell": {
        "background": "#012456", "foreground": "#cccccc", "cursorColor": "#ffffff", "black":
        "#0c0c0c", "red": "#c50f1f", "green": "#13a10e", "yellow": "#c19c00", "blue": "#0037da",
        "purple": "#881798", "cyan": "#3a96dd", "white": "#cccccc", "brightBlack": "#767676",
        "brightRed": "#e74856", "brightGreen": "#16c60c", "brightYellow": "#f9f1a5",
        "brightBlue": "#3b78ff", "brightPurple": "#b4009e", "brightCyan": "#61d6d6",
        "brightWhite": "#f2f2f2"
    },
    "Vintage": {
        "background": "#000000", "foreground": "#c0c0c0", "cursorColor": "#ffffff", "black":
        "#000000", "red": "#800000", "green": "#008000", "yellow": "#808000", "blue": "#000080",
        "purple": "#800080", "cyan": "#008080", "white": "#c0c0c0", "brightBlack": "#808080",
        "brightRed": "#ff0000", "brightGreen": "#00ff00", "brightYellow": "#ffff00",
        "brightBlue": "#0000ff", "brightPurple": "#ff00ff", "brightCyan": "#00ffff",
        "brightWhite": "#ffffff"
    },
    "One Half Dark": {
        "background": "#282c34", "foreground": "#dcdfe4", "cursorColor": "#ffffff", "black":
        "#282c34", "red": "#e06c75", "green": "#98c379", "yellow": "#e5c07b", "blue": "#61afef",
        "purple": "#c678dd", "cyan": "#56b6c2", "white": "#dcdfe4", "brightBlack": "#5a6374",
        "brightRed": "#e06c75", "brightGreen": "#98c379", "brightYellow": "#e5c07b",
        "brightBlue": "#61afef", "brightPurple": "#c678dd", "brightCyan": "#56b6c2",
        "brightWhite": "#dcdfe4"
    },
    "One Half Light": {
        "background": "#fafafa", "foreground": "#383a42", "cursorColor": "#4f525d",
        "selectionBackground": "#383a42", "black": "#383a42", "red": "#e45649", "green":
        "#50a14f", "yellow": "#c18301", "blue": "#0184bc", "purple": "#a626a4", "cyan":
        "#0997b3", "white": "#fafafa", "brightBlack": "#4f525d", "brightRed": "#df6c75",
        "brightGreen": "#98c379", "brightYellow": "#e4c07a", "brightBlue": "#61afef",
        "brightPurple": "#c577dd", "brightCyan": "#56b5c1", "brightWhite": "#ffffff"
    },
    "Solarized Dark": {
        "background": "#002b36", "foreground": "#839496", "cursorColor": "#ffffff", "black":
        "#002b36", "red": "#dc322f", "green": "#859900", "yellow": "#b58900", "blue": "#268bd2",
        "purple": "#d33682", "cyan": "#2aa198", "white": "#eee8d5", "brightBlack": "#073642",
        "brightRed": "#cb4b16", "brightGreen": "#586e75", "brightYellow": "#657b83",
        "brightBlue": "#839496", "brightPurple": "#6c71c4", "brightCyan": "#93a1a1",
        "brightWhite": "#fdf6e3"
    },
    "Solarized Light": {
        "background": "#fdf6e3", "foreground": "#657b83", "cursorColor": "#002b36",
        "selectionBackground": "#2c4d57", "black": "#002b36", "red": "#dc322f", "green":
        "#859900", "yellow": "#b58900", "blue": "#268bd2", "purple": "#d33682", "cyan":
        "#2aa198", "white": "#eee8d5", "brightBlack": "#073642", "brightRed": "#cb4b16",
        "brightGreen": "#586e75", "brightYellow": "#657b83", "brightBlue": "#839496",
        "brightPurple": "#6c71c4", "brightCyan": "#93a1a1", "brightWhite": "#fdf6e3"
    },
    "Tango Dark": {
        "background": "#000000", "foreground": "#d3d7cf", "cursorColor": "#ffffff", "black":
        "#000000", "red": "#cc0000", "green": "#4e9a06", "yellow": "#c4a000", "blue": "#3465a4",
        "purple": "#75507b", "cyan": "#06989a", "white": "#d3d7cf", "brightBlack": "#555753",
        "brightRed": "#ef2929", "brightGreen": "#8ae234", "brightYellow": "#fce94f",
        "brightBlue": "#729fcf", "brightPurple": "#ad7fa8", "brightCyan": "#34e2e2",
        "brightWhite": "#eeeeec"
    },
    "Tango Light": {
        "background": "#ffffff", "foreground": "#555753", "cursorColor": "#000000",
        "selectionBackground": "#141414", "black": "#000000", "red": "#cc0000", "green":
        "#4e9a06", "yellow": "#c4a000", "blue": "#3465a4", "purple": "#75507b", "cyan":
        "#06989a", "white": "#d3d7cf", "brightBlack": "#555753", "brightRed": "#ef2929",
        "brightGreen": "#8ae234", "brightYellow": "#fce94f", "brightBlue": "#729fcf",
        "brightPurple": "#ad7fa8", "brightCyan": "#34e2e2", "brightWhite": "#eeeeec"
    },
    "Dark+": {
        "background": "#1e1e1e", "foreground": "#cccccc", "cursorColor": "#808080",
        "selectionBackground": "#ffffff", "black": "#000000", "red": "#cd3131", "green":
        "#0dbc79", "yellow": "#e5e510", "blue": "#2472c8", "purple": "#bc3fbc", "cyan":
        "#11a8cd", "white": "#e5e5e5", "brightBlack": "#666666", "brightRed": "#f14c4c",
        "brightGreen": "#23d18b", "brightYellow": "#f5f543", "brightBlue": "#3b8eea",
        "brightPurple": "#d670d6", "brightCyan": "#29b8db", "brightWhite": "#e5e5e5"
    },
    "VSCode Dark Modern": {
        "background": "#1f1f1f", "foreground": "#cccccc", "cursorColor": "#ffffff",
        "selectionBackground": "#264f78", "black": "#000000", "red": "#cd3131", "green":
        "#0dbc79", "yellow": "#e5e510", "blue": "#2472c8", "purple": "#bc3fbc", "cyan":
        "#11a8cd", "white": "#e5e5e5", "brightBlack": "#666666", "brightRed": "#f14c4c",
        "brightGreen": "#23d18b", "brightYellow": "#f5f543", "brightBlue": "#3b8eea",
        "brightPurple": "#d670d6", "brightCyan": "#29b8db", "brightWhite": "#e5e5e5"
    },
    "VSCode Light Modern": {
        "background": "#ffffff", "foreground": "#3b3b3b", "cursorColor": "#000000",
        "selectionBackground": "#add6ff", "black": "#000000", "red": "#cd3131", "green":
        "#00bc00", "yellow": "#949800", "blue": "#0451a5", "purple": "#bc05bc", "cyan":
        "#0598bc", "white": "#555555", "brightBlack": "#666666", "brightRed": "#cd3131",
        "brightGreen": "#14ce14", "brightYellow": "#b5ba00", "brightBlue": "#0451a5",
        "brightPurple": "#bc05bc", "brightCyan": "#0598bc", "brightWhite": "#a5a5a5"
    },
    "CGA": {
        "background": "#000000", "foreground": "#aaaaaa", "cursorColor": "#00aa00",
        "selectionBackground": "#ffffff", "black": "#000000", "red": "#aa0000", "green":
        "#00aa00", "yellow": "#aa5500", "blue": "#0000aa", "purple": "#aa00aa", "cyan":
        "#00aaaa", "white": "#aaaaaa", "brightBlack": "#555555", "brightRed": "#ff5555",
        "brightGreen": "#55ff55", "brightYellow": "#ffff55", "brightBlue": "#5555ff",
        "brightPurple": "#ff55ff", "brightCyan": "#55ffff", "brightWhite": "#ffffff"
    },
    "IBM 5153": {
        "background": "#000000", "foreground": "#aaaaaa", "cursorColor": "#00aa00",
        "selectionBackground": "#ffffff", "black": "#000000", "red": "#aa0000", "green":
        "#00aa00", "yellow": "#c47e00", "blue": "#0000aa", "purple": "#aa00aa", "cyan":
        "#00aaaa", "white": "#aaaaaa", "brightBlack": "#555555", "brightRed": "#ff5555",
        "brightGreen": "#55ff55", "brightYellow": "#ffff55", "brightBlue": "#5555ff",
        "brightPurple": "#ff55ff", "brightCyan": "#55ffff", "brightWhite": "#ffffff"
    },
}
WT_DEFAULT_SCHEME = "Campbell"


def loads_jsonc(text: str):
    """JSON with // and /* */ comments and trailing commas, as Windows Terminal writes."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':                                   # copy strings untouched
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    return json.loads(cleaned)


def _read_capped(path: Path) -> bytes:
    if path.stat().st_size > MAX_BYTES:
        raise SchemeError(f"{path.name} is too large to read.")
    return path.read_bytes()


def windows_terminal_settings(env: dict | None = None) -> list[Path]:
    env = os.environ if env is None else env
    local = env.get("LOCALAPPDATA")
    if not local:
        return []
    base = Path(local)
    candidates = [
        base / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState",
        base / "Packages" / "Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe" / "LocalState",
        base / "Microsoft" / "Windows Terminal",
    ]
    return [d / "settings.json" for d in candidates if (d / "settings.json").is_file()]


def _scheme_choice(value, dark: bool) -> str | None:
    """A profile's colorScheme: a name, or {"light": ..., "dark": ...}."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        picked = value.get("dark" if dark else "light")
        return picked if isinstance(picked, str) else None
    return None


def windows_terminal_scheme(path: Path, dark: bool = True) -> SystemScheme:
    data = loads_jsonc(_read_capped(path).decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise SchemeError("settings.json isn't a settings object.")
    profiles = data.get("profiles", {})
    defaults, plist = ({}, profiles) if isinstance(profiles, list) else (
        profiles.get("defaults", {}) if isinstance(profiles, dict) else {},
        profiles.get("list", []) if isinstance(profiles, dict) else [])
    default_id = data.get("defaultProfile")
    profile = next((p for p in plist if isinstance(p, dict)
                    and default_id and p.get("guid", "").lower() == str(default_id).lower()),
                   None)
    name = (_scheme_choice(profile.get("colorScheme"), dark) if profile else None) \
        or _scheme_choice(defaults.get("colorScheme") if isinstance(defaults, dict) else None,
                          dark) \
        or WT_DEFAULT_SCHEME
    user = {s.get("name"): s for s in data.get("schemes", []) if isinstance(s, dict)}
    raw = user.get(name) or WT_BUILTIN.get(name)
    who = f"default profile '{profile.get('name')}'" if profile and profile.get("name") \
        else "default profile"
    if raw is None:
        return SystemScheme("Windows Terminal", None,
                            f"The {who} uses '{name}', which isn't in settings.json.")
    scheme = _wt_scheme({**raw, "name": _safe_name(f"Windows Terminal {name}")})
    return SystemScheme("Windows Terminal", scheme, f"{who}: {name}")


# --------------------------------------------------------------------------
# macOS: iTerm2 and Terminal.app
# --------------------------------------------------------------------------

def _prefs(name: str, home: Path) -> Path:
    return home / "Library" / "Preferences" / name


def iterm_scheme(path: Path, dark: bool = True) -> SystemScheme:
    data = plistlib.loads(_read_capped(path))
    profiles = data.get("New Bookmarks") or []
    guid = data.get("Default Bookmark Guid")
    profile = next((p for p in profiles if isinstance(p, dict) and p.get("Guid") == guid),
                   profiles[0] if profiles else None)
    if not isinstance(profile, dict):
        return SystemScheme("iTerm2", None, "iTerm2 has no profiles yet.")
    # Profiles can keep separate colors for light and dark mode.
    suffix = (" (Dark)" if dark else " (Light)") if profile.get(
        "Use Separate Colors for Light and Dark Mode") else ""

    def color(key: str, fallback: str | None = None) -> str:
        entry = profile.get(key + suffix, profile.get(key))
        if entry is None:
            if fallback is None:
                raise SchemeError(f"iTerm2 profile has no {key}.")
            return fallback
        return _iterm_color(entry)
    fg = color("Foreground Color")
    scheme = ColorScheme(
        _safe_name(f"iTerm2 {profile.get('Name', 'Default')}"), fg,
        color("Background Color"), color("Cursor Color", fg),
        color("Selection Color", "#264f78"),
        tuple(color(f"Ansi {i} Color") for i in range(16)))
    return SystemScheme("iTerm2", scheme, f"default profile '{profile.get('Name', '')}'")


_TERMINAL_APP_KEYS = ("Black", "Red", "Green", "Yellow", "Blue", "Magenta", "Cyan", "White")


def _archived_color(data) -> str | None:
    """An NSColor archived by NSKeyedArchiver (how Terminal.app stores colors)."""
    if not isinstance(data, bytes):
        return None
    try:
        archive = plistlib.loads(data)
    except Exception:
        return None
    for obj in archive.get("$objects", []) if isinstance(archive, dict) else []:
        if not isinstance(obj, dict):
            continue
        raw = obj.get("NSRGB") or obj.get("NSWhite")
        if isinstance(raw, bytes):
            try:
                parts = [float(x) for x in raw.rstrip(b"\x00").split()]
            except ValueError:
                return None
            if "NSWhite" in obj and not obj.get("NSRGB"):
                parts = [parts[0]] * 3
            if len(parts) >= 3 and all(0.0 <= p <= 1.0 for p in parts[:3]):
                return "#" + "".join(f"{round(p * 255):02x}" for p in parts[:3])
    return None


def terminal_app_scheme(path: Path, fallback_ansi=None) -> SystemScheme:
    data = plistlib.loads(_read_capped(path))
    name = data.get("Default Window Settings") or "Basic"
    profile = (data.get("Window Settings") or {}).get(name)
    if not isinstance(profile, dict):
        return SystemScheme("Terminal.app", None,
                            f"Terminal.app's '{name}' profile uses colors termSwitch can't "
                            "read (built-in profiles that were never changed).")
    fg = _archived_color(profile.get("TextColor"))
    bg = _archived_color(profile.get("BackgroundColor"))
    if not fg or not bg:
        return SystemScheme("Terminal.app", None,
                            f"Terminal.app's '{name}' profile has no readable colors.")
    ansi = []
    for i, base in enumerate(_TERMINAL_APP_KEYS * 2):
        key = f"ANSI{'Bright' if i >= 8 else ''}{base}Color"
        value = _archived_color(profile.get(key))
        if value is None and fallback_ansi is not None:
            value = fallback_ansi[i]
        if value is None:
            return SystemScheme("Terminal.app", None,
                                f"Terminal.app's '{name}' profile has no {key}.")
        ansi.append(value)
    scheme = ColorScheme(_safe_name(f"Terminal.app {name}"), fg, bg,
                         _archived_color(profile.get("CursorColor")) or fg,
                         _archived_color(profile.get("SelectionColor")) or "#b4d5fe",
                         tuple(ansi))
    return SystemScheme("Terminal.app", scheme, f"default profile '{name}'")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    return re.sub(r"[^\w .()+'-]", "", name)[:48].strip() or "System terminal"


def read_system_scheme(platform: str, dark: bool, terminal_choice: str = "auto",
                       home: Path | None = None, env: dict | None = None,
                       iterm_installed: bool | None = None) -> SystemScheme:
    """The user's own terminal colors, or why they couldn't be read. Never raises."""
    home = home or Path.home()
    try:
        if platform == "win32":
            found = windows_terminal_settings(env)
            if not found:
                scheme = _wt_scheme({**WT_BUILTIN[WT_DEFAULT_SCHEME], "name": "Console Campbell"})
                return SystemScheme("Windows console", scheme,
                                    "Windows Terminal isn't installed: the console's "
                                    "Campbell colors")
            return windows_terminal_scheme(found[0], dark)
        if platform == "darwin":
            iterm = _prefs("com.googlecode.iterm2.plist", home)
            terminal_app = _prefs("com.apple.Terminal.plist", home)
            if iterm_installed is None:
                iterm_installed = iterm.is_file()
            use_iterm = terminal_choice == "iterm" or (terminal_choice == "auto"
                                                      and iterm_installed)
            if use_iterm and iterm.is_file():
                return iterm_scheme(iterm, dark)
            if terminal_app.is_file():
                from .term_schemes import BUILTIN
                base = BUILTIN["Dark" if dark else "Light"].ansi
                return terminal_app_scheme(terminal_app, fallback_ansi=base)
            return SystemScheme("Terminal.app", None, "No Terminal.app settings were found.")
        return SystemScheme("System terminal", None,
                            "Reading the system terminal's colors is supported on Windows "
                            "and macOS.")
    except (OSError, ValueError, SchemeError, plistlib.InvalidFileException) as exc:
        return SystemScheme("System terminal", None, f"Couldn't read the colors: {exc}")


