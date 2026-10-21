"""Render termswitch/assets/termswitch.svg to PNG, ICO and ICNS.

    pip install pillow   # build-time only
    python tools/build_icons.py
"""
import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "termswitch" / "assets"
SIZES = [16, 24, 32, 48, 64, 128, 256, 512, 1024]


def render(renderer: QSvgRenderer, size: int) -> Image.Image:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(p)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main() -> int:
    QGuiApplication(sys.argv)
    renderer = QSvgRenderer(str(ASSETS / "termswitch.svg"))
    if not renderer.isValid():
        print("SVG failed to load")
        return 1
    images = {s: render(renderer, s) for s in SIZES}
    for s in (16, 32, 64, 128, 256, 512):
        images[s].save(ASSETS / f"termswitch-{s}.png")
    images[256].save(ASSETS / "termswitch.ico",
                     sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    images[1024].save(ASSETS / "termswitch.icns")
    print("Wrote icons to", ASSETS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
