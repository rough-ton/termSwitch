"""Draw the Windows installer's images from the app icon (termswitch/assets/termswitch.svg).

    python tools/make_installer_images.py

Writes packaging/windows/images/:

- wizard-*.bmp: the tall panel on the installer's first and last pages. The icon's
  navy gradient, the icon, and the name.
- header-*.bmp: the small image at the top right of every other page. The icon on
  white, the wizard's header color.

Each comes at 100%, 150% and 200% display scaling; Inno Setup picks the closest.
Inno Setup takes bitmaps, so these are 24-bit .bmp files.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QImage,  # noqa: E402
                           QLinearGradient, QPainter)
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging" / "windows" / "images"
ICON = ROOT / "termswitch" / "assets" / "termswitch.svg"

NAVY_TOP, NAVY_BOTTOM = "#16314f", "#0a1422"      # the icon's background gradient
TITLE, QUIET = "#f4f7fb", "#8fa3b8"
SCALES = (100, 150, 200)
WIZARD = (164, 314)          # Inno Setup's size at 100% for WizardImageFile
HEADER = 55                  # and for WizardSmallImageFile (square)


def wizard_files() -> list[str]:
    return [f"wizard-{s}.bmp" for s in SCALES]


def header_files() -> list[str]:
    return [f"header-{s}.bmp" for s in SCALES]


def _font(size: float, weight: QFont.Weight) -> QFont:
    families = QFontDatabase.families()
    family = next((f for f in ("Segoe UI", "Inter", "Helvetica Neue", "Arial")
                   if f in families), "")
    font = QFont(family) if family else QFont()
    font.setPixelSize(round(size))
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def _painter(img: QImage) -> QPainter:
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    return p


def draw_wizard(scale: int) -> QImage:
    k = scale / 100
    w, h = round(WIZARD[0] * k), round(WIZARD[1] * k)
    img = QImage(w, h, QImage.Format.Format_RGB32)
    p = _painter(img)
    g = QLinearGradient(0, 0, w * 0.4, h)
    g.setColorAt(0, QColor(NAVY_TOP))
    g.setColorAt(1, QColor(NAVY_BOTTOM))
    p.fillRect(0, 0, w, h, g)
    size = 104 * k
    top = h * 0.30
    QSvgRenderer(str(ICON)).render(p, QRectF((w - size) / 2, top, size, size))
    p.setPen(QColor(TITLE))
    p.setFont(_font(21 * k, QFont.Weight.DemiBold))
    p.drawText(QRectF(0, top + size + 14 * k, w, 30 * k),
               Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "termSwitch")
    p.setPen(QColor(QUIET))
    p.setFont(_font(10.5 * k, QFont.Weight.Normal))
    p.drawText(QRectF(10 * k, top + size + 44 * k, w - 20 * k, 40 * k),
               Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
               | Qt.TextFlag.TextWordWrap,
               "AWS profiles, SSH sessions\nand terminals in one place")
    p.end()
    return img


def draw_header(scale: int) -> QImage:
    side = round(HEADER * scale / 100)
    img = QImage(side, side, QImage.Format.Format_RGB32)
    img.fill(QColor("#ffffff"))
    p = _painter(img)
    QSvgRenderer(str(ICON)).render(p, QRectF(0, 0, side, side))
    p.end()
    return img


def main() -> int:
    # Qt needs an application object for fonts; keep it alive while drawing.
    main.qt_app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    OUT.mkdir(parents=True, exist_ok=True)
    for scale, wizard, header in zip(SCALES, wizard_files(), header_files()):
        for name, img in ((wizard, draw_wizard(scale)), (header, draw_header(scale))):
            if not img.save(str(OUT / name), "BMP"):
                print("could not write", name)
                return 1
            print("wrote", (OUT / name).relative_to(ROOT), f"{img.width()}x{img.height()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
