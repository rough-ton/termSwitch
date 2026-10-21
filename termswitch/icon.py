"""Application icon and Windows taskbar identity."""

from __future__ import annotations

import sys
from importlib import resources

from PySide6.QtGui import QIcon, QPixmap

APP_ID = "rough-ton.termSwitch"


def app_icon() -> QIcon:
    icon = QIcon()
    assets = resources.files("termswitch") / "assets"
    for size in (16, 32, 64, 128, 256, 512):
        data = (assets / f"termswitch-{size}.png").read_bytes()
        pm = QPixmap()
        if pm.loadFromData(data, "PNG"):
            icon.addPixmap(pm)
    return icon


def set_windows_app_id() -> None:
    """Without this, Windows groups the window under python.exe and shows the
    Python icon in the taskbar."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)  # type: ignore[attr-defined]
    except Exception:
        pass
