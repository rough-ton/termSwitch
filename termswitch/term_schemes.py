"""Terminal color schemes: the 16 ANSI colors plus foreground, background, cursor and
selection.

Built-in schemes ship with the app. More can be imported from Windows Terminal
(``.json``) and iTerm2 (``.itermcolors``) files; those are parsed as data only, every
color is validated as ``#RRGGBB``, and file sizes are capped. Imported schemes are
saved as JSON in termSwitch's data folder.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .fileutil import replace_file

MATCH_APP = "match"                 # setting value: follow the app theme
MAX_IMPORT_BYTES = 512 * 1024
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_NAME = re.compile(r"^[\w .()+'-]{1,48}$")

# pyte color names, in ANSI order. "brown" is pyte's name for yellow.
ANSI_NAMES = ("black", "red", "green", "brown", "blue", "magenta", "cyan", "white")


class SchemeError(ValueError):
    pass


@dataclass(frozen=True)
class ColorScheme:
    name: str
    foreground: str
    background: str
    cursor: str
    selection: str
    ansi: tuple[str, ...]            # 16 colors: 8 normal, then 8 bright
    builtin: bool = False

    def __post_init__(self):
        validate(self)

    @property
    def is_dark(self) -> bool:
        return _luminance(self.background) < 0.4

    def resolve(self, name: str, default: str) -> str:
        """A pyte cell color (a name such as "red" or "brightred", a 6-digit hex
        value, or "default") to #RRGGBB."""
        if not name or name == "default":
            return default
        bright = name.startswith("bright")
        base = name[6:] if bright else name
        if base == "yellow":
            base = "brown"
        if base in ANSI_NAMES:
            return self.ansi[ANSI_NAMES.index(base) + (8 if bright else 0)]
        if len(name) == 6:
            try:
                int(name, 16)
                return "#" + name.lower()
            except ValueError:
                pass
        return default

    def to_json(self) -> dict:
        data = asdict(self)
        data.pop("builtin")
        data["ansi"] = list(self.ansi)
        return data


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def validate(s: ColorScheme) -> None:
    if not isinstance(s.name, str) or not _NAME.match(s.name):
        raise SchemeError(f"Invalid scheme name: {s.name!r}")
    colors = [s.foreground, s.background, s.cursor, s.selection, *s.ansi]
    if len(s.ansi) != 16:
        raise SchemeError(f"{s.name}: a scheme needs exactly 16 ANSI colors.")
    for c in colors:
        if not isinstance(c, str) or not _HEX.match(c):
            raise SchemeError(f"{s.name}: {c!r} is not a #RRGGBB color.")


# --------------------------------------------------------------------------
# Built-in schemes
# --------------------------------------------------------------------------

BUILTIN = {
    # The previous default: Windows Terminal's "Campbell" colors on near-black, with a
    # lighter blue and magenta so they stay readable on the dark background.
    "Dark": ColorScheme(
        "Dark", "#cccccc", "#0c0c0c", "#ffffff", "#264f78",
        ("#0c0c0c", "#c50f1f", "#13a10e", "#c19c00", "#3a6fe0", "#a347c4", "#3a96dd",
         "#cccccc", "#767676", "#e74856", "#16c60c", "#f9f1a5", "#3b78ff", "#b4009e",
         "#61d6d6", "#f2f2f2"), builtin=True),
    # A light scheme with colors dark enough to read on white.
    "Light": ColorScheme(
        "Light", "#1f2328", "#ffffff", "#1f2328", "#b6d6fd",
        ("#24292f", "#cf222e", "#116329", "#7d4e00", "#0550ae", "#8250df", "#1b7c83",
         "#6e7781", "#57606a", "#a40e26", "#1a7f37", "#633c01", "#0969da", "#6639ba",
         "#3192aa", "#8c959f"), builtin=True),
    # Maximum contrast on black, for low vision or bright rooms.
    "High contrast": ColorScheme(
        "High contrast", "#ffffff", "#000000", "#ffff00", "#1aebff",
        ("#000000", "#ff4d4d", "#3dff3d", "#ffff3d", "#5c9dff", "#ff5cff", "#3dffff",
         "#ffffff", "#9e9e9e", "#ff8080", "#80ff80", "#ffff80", "#8cb8ff", "#ff8cff",
         "#80ffff", "#ffffff"), builtin=True),
}

# Popular schemes, with each project's published colors (MIT licensed; credited in
# THIRD_PARTY_NOTICES.md). Order: foreground, background, cursor, selection, then
# black, red, green, yellow, blue, magenta, cyan, white and their bright versions.
# They only color termSwitch's own tabs; nothing outside the app is changed.
_POPULAR = [
    # Dracula, from dracula/windows-terminal.
    ("Dracula", "#f8f8f2", "#282a36", "#f8f8f2", "#44475a",
     ("#21222c", "#ff5555", "#50fa7b", "#f1fa8c", "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
      "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5", "#d6acff", "#ff92df", "#a4ffff", "#ffffff")),
    # Catppuccin Mocha and Latte, from catppuccin/windows-terminal.
    ("Catppuccin Mocha", "#cdd6f4", "#1e1e2e", "#f5e0dc", "#585b70",
     ("#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
      "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8")),
    ("Catppuccin Latte", "#4c4f69", "#eff1f5", "#dc8a78", "#acb0be",
     ("#5c5f77", "#d20f39", "#40a02b", "#df8e1d", "#1e66f5", "#ea76cb", "#179299", "#acb0be",
      "#acb0be", "#d20f39", "#40a02b", "#df8e1d", "#1e66f5", "#ea76cb", "#179299", "#bcc0cc")),
    # Solarized, from the palette and terminal mapping in altercation/solarized.
    ("Solarized Dark", "#839496", "#002b36", "#93a1a1", "#073642",
     ("#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
      "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3")),
    ("Solarized Light", "#657b83", "#fdf6e3", "#586e75", "#eee8d5",
     ("#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
      "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3")),
    # Nord, from the palette in nordtheme/nord, mapped as in its terminal ports.
    ("Nord", "#d8dee9", "#2e3440", "#d8dee9", "#434c5e",
     ("#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
      "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4")),
    # Gruvbox Dark, from the palette in morhetz/gruvbox.
    ("Gruvbox Dark", "#ebdbb2", "#282828", "#ebdbb2", "#504945",
     ("#282828", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#a89984",
      "#928374", "#fb4934", "#b8bb26", "#fabd2f", "#83a598", "#d3869b", "#8ec07c", "#ebdbb2")),
]
POPULAR = tuple(name for name, *_ in _POPULAR)
for _name, _fg, _bg, _cursor, _sel, _ansi in _POPULAR:
    BUILTIN[_name] = ColorScheme(_name, _fg, _bg, _cursor, _sel, _ansi, builtin=True)

DEFAULT_DARK, DEFAULT_LIGHT = "Dark", "Light"


# --------------------------------------------------------------------------
# User schemes
# --------------------------------------------------------------------------

def user_dir() -> Path:
    from PySide6.QtCore import QStandardPaths
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base or Path.home() / ".termswitch") / "schemes"


def _from_dict(data: dict) -> ColorScheme:
    if not isinstance(data, dict):
        raise SchemeError("Not a color scheme.")
    try:
        return ColorScheme(str(data["name"]).strip(), str(data["foreground"]).lower(),
                           str(data["background"]).lower(), str(data["cursor"]).lower(),
                           str(data["selection"]).lower(),
                           tuple(str(c).lower() for c in data["ansi"]))
    except KeyError as exc:
        raise SchemeError(f"Missing {exc.args[0]!r}.") from None


def load_user_schemes(folder: Path | None = None) -> dict[str, ColorScheme]:
    """Imported schemes. Invalid files are skipped."""
    folder = folder or user_dir()
    found: dict[str, ColorScheme] = {}
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob("*.json")):
        try:
            if path.stat().st_size > MAX_IMPORT_BYTES:
                continue
            scheme = _from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if scheme.name not in BUILTIN:
            found[scheme.name] = scheme
    return found


def all_schemes(folder: Path | None = None) -> dict[str, ColorScheme]:
    return {**BUILTIN, **load_user_schemes(folder)}


def save_user_scheme(scheme: ColorScheme, folder: Path | None = None) -> Path:
    if scheme.name in BUILTIN:
        raise SchemeError(f"'{scheme.name}' is the name of a built-in scheme.")
    folder = folder or user_dir()
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", scheme.name).strip("._") or "scheme"
    target = folder / f"{safe}.json"
    fd, tmp = tempfile.mkstemp(prefix=".scheme-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(scheme.to_json(), fh, indent=2)
        replace_file(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def scheme_for(choice: str, app_theme: str, schemes: dict[str, ColorScheme]) -> ColorScheme:
    """The scheme to use: a named one, or the default for the app theme."""
    if choice and choice != MATCH_APP and choice in schemes:
        return schemes[choice]
    return BUILTIN[DEFAULT_DARK if app_theme == "dark" else DEFAULT_LIGHT]


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------

_WT_KEYS = ("black", "red", "green", "yellow", "blue", "purple", "cyan", "white",
            "brightBlack", "brightRed", "brightGreen", "brightYellow", "brightBlue",
            "brightPurple", "brightCyan", "brightWhite")


def _read_capped(path: Path) -> bytes:
    path = Path(path)
    if not path.is_file():
        raise SchemeError("Not a file.")
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise SchemeError("The file is too large to be a color scheme.")
    return path.read_bytes()


def _wt_scheme(data: dict) -> ColorScheme:
    if not isinstance(data, dict):
        raise SchemeError("Not a Windows Terminal color scheme.")
    try:
        fg, bg = data["foreground"], data["background"]
        return ColorScheme(
            str(data.get("name", "Imported")).strip()[:48], str(fg).lower(), str(bg).lower(),
            str(data.get("cursorColor", fg)).lower(),
            str(data.get("selectionBackground", "#264f78")).lower(),
            tuple(str(data[k]).lower() for k in _WT_KEYS))
    except KeyError as exc:
        raise SchemeError(f"Missing {exc.args[0]!r}.") from None


def import_windows_terminal(path: Path) -> list[ColorScheme]:
    """One scheme object, a list of them, or a settings.json with a "schemes" list."""
    raw = _read_capped(path)
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemeError(f"Not valid JSON: {exc}") from None
    if isinstance(data, dict) and "schemes" in data:
        data = data["schemes"]
    items = data if isinstance(data, list) else [data]
    schemes = [_wt_scheme(item) for item in items[:50]]
    if not schemes:
        raise SchemeError("No color schemes found.")
    return schemes


def _iterm_color(entry) -> str:
    if not isinstance(entry, dict):
        raise SchemeError("Not an iTerm2 color.")
    try:
        parts = [float(entry[f"{c} Component"]) for c in ("Red", "Green", "Blue")]
    except (KeyError, TypeError, ValueError):
        raise SchemeError("An iTerm2 color is missing a component.") from None
    if not all(0.0 <= p <= 1.0 for p in parts):
        raise SchemeError("An iTerm2 color component is out of range.")
    return "#" + "".join(f"{round(p * 255):02x}" for p in parts)


def import_iterm(path: Path) -> list[ColorScheme]:
    raw = _read_capped(path)
    # Refuse DTD tricks (entity expansion) before parsing.
    if b"<!ENTITY" in raw.upper() or b"<!DOCTYPE" in raw.upper().replace(
            b'<!DOCTYPE PLIST PUBLIC "-//APPLE//DTD PLIST 1.0//EN" '
            b'"HTTP://WWW.APPLE.COM/DTDS/PROPERTYLIST-1.0.DTD">', b""):
        raise SchemeError("The file has a document type declaration termSwitch won't read.")
    try:
        data = plistlib.loads(raw, fmt=plistlib.FMT_XML)
    except Exception as exc:     # plistlib raises several types for bad input
        raise SchemeError(f"Not an iTerm2 color file: {exc}") from None
    if not isinstance(data, dict):
        raise SchemeError("Not an iTerm2 color file.")
    try:
        ansi = tuple(_iterm_color(data[f"Ansi {i} Color"]) for i in range(16))
        fg = _iterm_color(data["Foreground Color"])
        bg = _iterm_color(data["Background Color"])
    except KeyError as exc:
        raise SchemeError(f"Missing {exc.args[0]!r}.") from None
    cursor = _iterm_color(data["Cursor Color"]) if "Cursor Color" in data else fg
    selection = (_iterm_color(data["Selection Color"]) if "Selection Color" in data
                 else "#264f78")
    name = re.sub(r"[^\w .()+'-]", "", Path(path).stem)[:48] or "Imported"
    return [ColorScheme(name, fg, bg, cursor, selection, ansi)]


def import_file(path: Path) -> list[ColorScheme]:
    suffix = Path(path).suffix.lower()
    if suffix == ".itermcolors":
        return import_iterm(path)
    if suffix == ".json":
        return import_windows_terminal(path)
    raise SchemeError("Choose a Windows Terminal .json or an iTerm2 .itermcolors file.")
