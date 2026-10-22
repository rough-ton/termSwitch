"""Keep the README and docs/ pages tidy: links resolve and the README stays short."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md", *sorted((ROOT / "docs").glob("*.md"))]

MD_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
HTML_REF = re.compile(r"""(?:src|href)="([^"]+)\"""")
FENCE = re.compile(r"^```.*?^```", re.S | re.M)


def _targets(text):
    text = FENCE.sub("", text)
    return MD_LINK.findall(text) + HTML_REF.findall(text)


def _slug(heading):
    s = heading.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def _anchors(path):
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    return {_slug(m) for m in re.findall(r"^#{1,6}\s+(.+)$", text, re.M)}


def test_readme_is_short():
    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    assert len(lines) < 150


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_relative_links_resolve(page):
    broken = []
    for target in _targets(page.read_text(encoding="utf-8")):
        if re.match(r"^[a-z]+:", target):
            continue
        file_part, _, anchor = target.partition("#")
        dest = (page.parent / file_part).resolve() if file_part else page
        if not dest.exists():
            broken.append(target)
        elif anchor and dest.suffix == ".md" and anchor not in _anchors(dest):
            broken.append(target)
    assert not broken, f"{page.name}: broken links {broken}"


def test_every_placeholder_image_is_used():
    images = {p.name for p in (ROOT / "docs" / "images").glob("*.png")}
    used = set()
    for page in PAGES:
        used |= {Path(t).name for t in _targets(page.read_text(encoding="utf-8"))}
    assert images and images <= used
