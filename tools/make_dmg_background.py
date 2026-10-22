"""Draw the .dmg window background from packaging/dmg/layout.json.

    python tools/make_dmg_background.py              # writes background.png and @2x
    python tools/make_dmg_background.py --preview out.png
        # also draws the icons and Finder's labels on top, in light and dark mode,
        # to check the design without a Mac

The design follows the app icon: its navy gradient, and its orange prompt chevron as
the arrow. Finder draws each icon's name under it in
black (light mode) or white (dark mode) and that can't be changed, so a mid-grey pill
sits behind each name, readable with either.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QImage,  # noqa: E402
                           QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient)
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DMG = ROOT / "packaging" / "dmg"
LAYOUT = json.loads((DMG / "layout.json").read_text(encoding="utf-8"))

NAVY_TOP, NAVY_BOTTOM = "#16314f", "#0a1422"      # the icon's background gradient
ORANGE = "#ff9f1c"                                  # the icon's prompt chevron
TITLE, BODY, QUIET = "#f4f7fb", "#c9d4e0", "#8fa3b8"
PILL = "#6f7884"   # 4.7:1 against black text and 4.5:1 against white text


def _font(size: float, weight: QFont.Weight) -> QFont:
    families = QFontDatabase.families()
    family = next((f for f in ("Inter", "SF Pro Display", "Helvetica Neue", "Arial")
                   if f in families), "")
    font = QFont(family) if family else QFont()
    font.setPixelSize(round(size))
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def _centered(p: QPainter, text: str, y: float, font: QFont, color: str, width: int) -> None:
    p.setFont(font)
    p.setPen(QColor(color))
    p.drawText(QRectF(0, y - font.pixelSize(), width, font.pixelSize() * 1.6),
               Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)


def draw(scale: int) -> QImage:
    w, h = LAYOUT["window"]["width"], LAYOUT["window"]["height"]
    img = QImage(w * scale, h * scale, QImage.Format.Format_ARGB32)
    img.setDevicePixelRatio(scale)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    # Background: the icon's gradient, with a soft light behind the title.
    grad = QLinearGradient(0, 0, w, h)
    grad.setColorAt(0, QColor(NAVY_TOP))
    grad.setColorAt(1, QColor(NAVY_BOTTOM))
    p.fillRect(QRectF(0, 0, w, h), grad)
    glow = QRadialGradient(QPointF(w / 2, 40), w * 0.55)
    glow.setColorAt(0, QColor(255, 255, 255, 26))
    glow.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(QRectF(0, 0, w, h), glow)
    # Faint diagonal light rays, as in many installer windows.
    p.setPen(QPen(QColor(255, 255, 255, 14), 1.2))
    for x0, x1 in ((90, 230), (150, 250), (w - 90, w - 230), (w - 150, w - 250)):
        p.drawLine(QPointF(x0, 0), QPointF(x1, h * 0.55))

    # Title, instruction and footer.
    _centered(p, LAYOUT["title"]["text"], LAYOUT["title"]["y"],
              _font(40, QFont.Weight.DemiBold), TITLE, w)
    _centered(p, LAYOUT["subtitle"]["text"], LAYOUT["subtitle"]["y"],
              _font(15, QFont.Weight.Normal), BODY, w)
    _centered(p, LAYOUT["footer"]["text"], LAYOUT["footer"]["y"],
              _font(12, QFont.Weight.Normal), QUIET, w)

    # The arrow: the icon's orange prompt chevron, twice.
    ax, ay = (LAYOUT["app"]["x"] + LAYOUT["applications"]["x"]) / 2, LAYOUT["app"]["y"]
    pen = QPen(QColor(ORANGE), 9)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for dx, alpha in ((-14, 140), (14, 255)):
        c = QColor(ORANGE)
        c.setAlpha(alpha)
        pen.setColor(c)
        p.setPen(pen)
        path = QPainterPath(QPointF(ax + dx - 11, ay - 22))
        path.lineTo(ax + dx + 11, ay)
        path.lineTo(ax + dx - 11, ay + 22)
        p.drawPath(path)

    # A pill behind each name Finder draws under an icon.
    pill = LAYOUT["label_pill"]
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(PILL))
    for key in ("app", "applications"):
        cx, cy = LAYOUT[key]["x"], LAYOUT[key]["y"] + pill["offset_y"]
        p.drawRoundedRect(QRectF(cx - pill["width"] / 2, cy - pill["height"] / 2,
                                 pill["width"], pill["height"]), pill["height"] / 2,
                          pill["height"] / 2)
    p.end()
    return img


def _folder(p: QPainter, cx: float, cy: float, size: float) -> None:
    """A stand-in for Finder's Applications folder icon, for the preview only."""
    w, h = size * 0.9, size * 0.7
    x, y = cx - w / 2, cy - h / 2 + size * 0.05
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#5ab4f0"))
    p.drawRoundedRect(QRectF(x, y - size * 0.08, w * 0.42, size * 0.2), 8, 8)
    p.setBrush(QColor("#7cc8f8"))
    p.drawRoundedRect(QRectF(x, y, w, h), 10, 10)
    pen = QPen(QColor("#4a9fd8"), 7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(cx - 16, cy + 26), QPointF(cx, cy - 10))
    p.drawLine(QPointF(cx + 16, cy + 26), QPointF(cx, cy - 10))
    p.drawLine(QPointF(cx - 20, cy + 14), QPointF(cx + 20, cy + 14))


def preview(out: Path) -> None:
    """The background with the icons and Finder's labels on top, light and dark."""
    w, h = LAYOUT["window"]["width"], LAYOUT["window"]["height"]
    icon = QSvgRenderer(str(ROOT / "termswitch" / "assets" / "termswitch.svg"))
    size = LAYOUT["icon_size"]
    sheet = QImage(w * 2, (h + 28) * 2 * 2 + 20 * 2, QImage.Format.Format_ARGB32)
    sheet.setDevicePixelRatio(2)
    sheet.fill(QColor("#e8e8ea"))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for row, (mode, label_color, bar) in enumerate((("light", "#000000", "#ececec"),
                                                    ("dark", "#ffffff", "#2b2b2d"))):
        top = row * (h + 28 + 20)
        p.fillRect(QRectF(0, top, w, 28), QColor(bar))
        p.setFont(_font(13, QFont.Weight.DemiBold))
        p.setPen(QColor("#4a4a4a" if mode == "light" else "#d0d0d0"))
        p.drawText(QRectF(0, top, w, 28), Qt.AlignmentFlag.AlignCenter,
                   f"termSwitch  ({mode} mode)")
        p.drawImage(QRectF(0, top + 28, w, h), draw(2))
        app = LAYOUT["app"]
        icon.render(p, QRectF(app["x"] - size / 2, top + 28 + app["y"] - size / 2, size, size))
        _folder(p, LAYOUT["applications"]["x"], top + 28 + LAYOUT["applications"]["y"], size)
        p.setFont(_font(LAYOUT["text_size"], QFont.Weight.Normal))
        p.setPen(QColor(label_color))
        for key, name in (("app", "termSwitch"), ("applications", "Applications")):
            cy = top + 28 + LAYOUT[key]["y"] + LAYOUT["label_pill"]["offset_y"]
            p.drawText(QRectF(LAYOUT[key]["x"] - 90, cy - 10, 180, 20),
                       Qt.AlignmentFlag.AlignCenter, name)
    p.end()
    sheet.save(str(out))


def main(argv: list[str] | None = None) -> int:
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--preview", type=Path, help="also write a preview with icons and labels")
    ns = args.parse_args(argv)
    # Qt needs an application object for fonts; keep it alive while drawing.
    main.qt_app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    for scale, name in ((1, "background.png"), (2, "background@2x.png")):
        draw(scale).save(str(DMG / name))
        print("wrote", (DMG / name).relative_to(ROOT))
    if ns.preview:
        preview(ns.preview)
        print("wrote", ns.preview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
