"""App theme: Light, Dark or Follow system.

termSwitch uses the Fusion style with its own light and dark palettes on every
platform, so both themes look the same on Windows and macOS. Colors that carry a
meaning (hints, errors, warnings, production red) come from ``colors()`` for the
current theme instead of being hard-coded in the UI.

Labels mark their meaning with ``theme.set_role(label, "hint")`` (or "error",
"warning", "success"). Their color comes from the current theme and is updated when
the theme changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette

SYSTEM, LIGHT, DARK = "system", "light", "dark"
THEME_CHOICES = [(SYSTEM, "Follow system"), (LIGHT, "Light"), (DARK, "Dark")]


@dataclass(frozen=True)
class Colors:
    """Colors with a meaning, chosen to stay readable on each theme's background."""
    hint: str          # secondary text: hints, placeholders, automatic groups
    error: str
    warning: str
    success: str
    link: str
    prod: str          # production profiles
    section: str       # config editor: [section] headers
    key: str           # config editor: setting names


LIGHT_COLORS = Colors(hint="#646a75", error="#c5221f", warning="#a15c00", success="#1e7e34",
                      link="#1a63d8", prod="#c0392b", section="#1a63d8", key="#9a4f00")
DARK_COLORS = Colors(hint="#9aa0a6", error="#ff6b5f", warning="#f0a43a", success="#4cc06a",
                     link="#6aa8ff", prod="#ff6b5b", section="#6aa8ff", key="#e3a35a")


def _palette(spec: dict) -> QPalette:
    pal = QPalette()
    for role, color in spec["active"].items():
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            pal.setColor(group, role, QColor(color))
    for role, color in spec["disabled"].items():
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(color))
    return pal


R = QPalette.ColorRole
LIGHT_SPEC = {
    "active": {
        R.Window: "#f3f3f3", R.WindowText: "#1f2328", R.Base: "#ffffff",
        R.AlternateBase: "#f6f8fa", R.Text: "#1f2328", R.Button: "#f6f6f6",
        R.ButtonText: "#1f2328", R.BrightText: "#ffffff", R.Highlight: "#2d7ff9",
        R.HighlightedText: "#ffffff", R.Link: LIGHT_COLORS.link, R.LinkVisited: "#7a3fd0",
        R.ToolTipBase: "#ffffdc", R.ToolTipText: "#1f2328",
        R.PlaceholderText: LIGHT_COLORS.hint, R.Light: "#ffffff", R.Midlight: "#e8e8e8",
        R.Mid: "#c8c8c8", R.Dark: "#a0a0a0", R.Shadow: "#5a5a5a",
    },
    "disabled": {
        R.WindowText: "#9a9fa6", R.Text: "#9a9fa6", R.ButtonText: "#9a9fa6",
        R.Base: "#f3f3f3", R.Button: "#ededed", R.Highlight: "#c8c8c8",
        R.HighlightedText: "#ffffff", R.PlaceholderText: "#b8bcc2",
    },
}
DARK_SPEC = {
    "active": {
        R.Window: "#2b2d30", R.WindowText: "#e6e6e6", R.Base: "#1e1f22",
        R.AlternateBase: "#26282b", R.Text: "#e6e6e6", R.Button: "#3a3d41",
        R.ButtonText: "#e6e6e6", R.BrightText: "#ffffff", R.Highlight: "#2f6fd6",
        R.HighlightedText: "#ffffff", R.Link: DARK_COLORS.link, R.LinkVisited: "#b48cff",
        R.ToolTipBase: "#3a3d41", R.ToolTipText: "#e6e6e6",
        R.PlaceholderText: DARK_COLORS.hint, R.Light: "#4a4d52", R.Midlight: "#404348",
        R.Mid: "#33363a", R.Dark: "#1a1b1e", R.Shadow: "#000000",
    },
    "disabled": {
        R.WindowText: "#7d8187", R.Text: "#7d8187", R.ButtonText: "#7d8187",
        R.Base: "#26282b", R.Button: "#33363a", R.Highlight: "#4a4d52",
        R.HighlightedText: "#cfcfcf", R.PlaceholderText: "#6a6e74",
    },
}

_current = LIGHT


def colors() -> Colors:
    """Semantic colors for the theme in use."""
    return DARK_COLORS if _current == DARK else LIGHT_COLORS


def current() -> str:
    """LIGHT or DARK: the theme actually shown (Follow system resolved)."""
    return _current


def system_is_dark(app=None) -> bool:
    app = app or QGuiApplication.instance()
    hints = app.styleHints() if app else None
    if hints is not None and hasattr(hints, "colorScheme"):
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    return False


def resolve(mode: str, app=None) -> str:
    if mode == DARK:
        return DARK
    if mode == LIGHT:
        return LIGHT
    return DARK if system_is_dark(app) else LIGHT


ROLES = ("hint", "error", "warning", "success")


def _apply_role_color(widget) -> None:
    role = widget.property("role")
    pal = widget.palette()
    if role in ROLES:
        color = QColor(getattr(colors(), role))
    else:
        color = QGuiApplication.palette().color(QPalette.ColorRole.WindowText)
    pal.setColor(QPalette.ColorRole.WindowText, color)
    pal.setColor(QPalette.ColorRole.Text, color)
    widget.setPalette(pal)


def set_role(widget, role: str | None) -> None:
    """Color a label by meaning: "hint", "error", "warning", "success", or None for
    normal text. The color follows theme changes."""
    widget.setProperty("role", role)
    _apply_role_color(widget)


def _recolor_roles() -> None:
    from PySide6.QtWidgets import QApplication
    for widget in QApplication.allWidgets():
        if widget.property("role") is not None:
            _apply_role_color(widget)


class ThemeManager(QObject):
    """Applies the chosen theme and re-applies it when the OS switches between
    light and dark while "Follow system" is selected."""

    changed = Signal(str)        # LIGHT or DARK

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.mode = SYSTEM
        hints = app.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._system_changed)

    def apply(self, mode: str) -> str:
        global _current
        self.mode = mode if mode in (SYSTEM, LIGHT, DARK) else SYSTEM
        hints = self.app.styleHints()
        # Ask the platform for matching native parts (title bars, menus) on Qt 6.8+.
        if hasattr(hints, "setColorScheme"):
            hints.setColorScheme({LIGHT: Qt.ColorScheme.Light, DARK: Qt.ColorScheme.Dark}
                                 .get(self.mode, Qt.ColorScheme.Unknown))
        effective = resolve(self.mode, self.app)
        # Set the style once; changing styles on a running app re-polishes every
        # widget. Theme switches only swap the palette.
        if "fusion" not in self.app.style().metaObject().className().lower():
            self.app.setStyle("Fusion")
        _current = effective
        self.app.setPalette(_palette(DARK_SPEC if effective == DARK else LIGHT_SPEC))
        _recolor_roles()
        self.changed.emit(effective)
        return effective

    def _system_changed(self, *_):
        if self.mode == SYSTEM:
            self.apply(SYSTEM)


_manager: ThemeManager | None = None


def manager(app=None) -> ThemeManager:
    """The app's ThemeManager, created on first use."""
    global _manager
    app = app or QGuiApplication.instance()
    if _manager is None or _manager.app is not app:
        _manager = ThemeManager(app)
    return _manager
