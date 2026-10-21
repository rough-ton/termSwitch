"""Material Icon Theme icons (MIT), rendered from the bundled SVGs.

SVGs are drawn with QSvgRenderer instead of relying on Qt's SVG image plugin,
so they render the same in source runs and packaged builds.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from importlib import resources

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

SIZES = (16, 20, 24, 32, 48, 64)


def _svg_bytes(name: str) -> bytes:
    path = resources.files("termswitch") / "assets" / "icons" / "material" / f"{name}.svg"
    return path.read_bytes()


def _render(renderer: QSvgRenderer, size: int, dot: str | None, gray: bool) -> QPixmap:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    if dot:
        # Small status/color dot in the bottom-right corner, with a dark ring
        # so it stays visible on any icon color.
        d = max(size * 0.45, 6.0)
        x = y = size - d
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawEllipse(QRectF(x - 1, y - 1, d + 2, d + 2))
        p.setBrush(QColor(dot))
        p.drawEllipse(QRectF(x, y, d, d))
    p.end()
    if gray:
        alpha = img.convertToFormat(QImage.Format.Format_Alpha8)
        g = img.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(
            QImage.Format.Format_ARGB32)
        g.setAlphaChannel(alpha)
        img = g
    return QPixmap.fromImage(img)


@lru_cache(maxsize=128)
def icon(name: str, dot: str | None = None, gray: bool = False) -> QIcon:
    """A QIcon for a bundled Material icon, optionally with a colored dot."""
    renderer = QSvgRenderer(QByteArray(_svg_bytes(name)))
    result = QIcon()
    if not renderer.isValid():
        return result
    for size in SIZES:
        result.addPixmap(_render(renderer, size, dot, gray))
    return result


def shell_icon(dot: str | None = None, exited: bool = False) -> QIcon:
    name = "powershell" if sys.platform.startswith("win") else "console"
    return icon(name, dot=None if exited else dot, gray=exited)


# Folder icon for each automatic group kind (see ui.auto_group_label).
AUTO_GROUP_ICONS = {
    "sso": "folder-aws",
    "account": "folder-aws",
    "role": "folder-shared",
    "process": "folder-config",
    "static": "folder-keys",
    "other": "folder-base",
}
