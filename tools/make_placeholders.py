"""Generate placeholder screenshots for the docs.

Each image is a neutral gray frame with its caption, at 2x scale, so the README and
docs pages show where a real screenshot goes. Replace a file with a real capture of the
same name when it is ready; nothing else needs to change.

    pip install pillow
    python tools/make_placeholders.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
SCALE = 2

# file name -> (width, height in points, caption)
SHOTS = {
    "main-window.png": (1200, 720, "Main window: profile tree, terminal tabs, Commands pane"),
    "profile-groups.png": (480, 560, "Profile groups with status dots"),
    "terminal-tabs.png": (960, 560, "Terminal tabs, one per profile"),
    "commands-pane.png": (420, 640, "Commands pane"),
    "parameter-form.png": (520, 720, "Parameter form"),
    "run-confirmation.png": (560, 300, "Run confirmation for a destructive command"),
    "dependency-check.png": (760, 520, "Dependency check report"),
    "commands-collapsed.png": (960, 560, "Commands pane collapsed to the right edge"),
}


def _font(size):
    for name in ("DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttc", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def make(path, width, height, caption):
    w, h = width * SCALE, height * SCALE
    img = Image.new("RGB", (w, h), "#e6e8eb")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline="#b8bdc4", width=2 * SCALE)
    # fake title bar
    bar = 28 * SCALE
    d.rectangle([0, 0, w, bar], fill="#d3d7dc")
    for i, c in enumerate(("#c0392b", "#e0a030", "#2e9e5b")):
        x = (14 + i * 18) * SCALE
        d.ellipse([x - 5 * SCALE, bar // 2 - 5 * SCALE, x + 5 * SCALE, bar // 2 + 5 * SCALE], fill=c)
    size = 22
    big = _font(size * SCALE)
    while size > 10 and d.textlength(caption, font=big) > w * 0.85:
        size -= 1
        big = _font(size * SCALE)
    small = _font(14 * SCALE)
    lines = [(caption, big, "#3b4048"), ("Screenshot coming soon", small, "#6b727c")]
    heights = [d.textbbox((0, 0), t, font=f)[3] for t, f, _ in lines]
    y = (h + bar - sum(heights) - 12 * SCALE) // 2
    for (text, font, color), lh in zip(lines, heights):
        tw = d.textlength(text, font=font)
        d.text(((w - tw) / 2, y), text, font=font, fill=color)
        y += lh + 12 * SCALE
    img.save(path, optimize=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (width, height, caption) in SHOTS.items():
        make(OUT / name, width, height, caption)
        print(OUT / name)


if __name__ == "__main__":
    main()
