"""The macOS .dmg install window: layout, background and the release check."""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DMG = ROOT / "packaging" / "dmg"
LAYOUT = json.loads((DMG / "layout.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(ROOT / "tools"))


def settings(app="dist/termSwitch.app"):
    """packaging/dmg/settings.py as dmgbuild runs it: only `defines` in scope."""
    scope = {"defines": {"app": app}}
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        code = (DMG / "settings.py").read_text(encoding="utf-8")
        exec(compile(code, "settings.py", "exec"), scope, scope)
    finally:
        os.chdir(cwd)
    return scope


def png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def test_icons_sit_where_the_background_expects_them():
    s = settings()
    assert s["icon_locations"] == {
        "termSwitch.app": (LAYOUT["app"]["x"], LAYOUT["app"]["y"]),
        "Applications": (LAYOUT["applications"]["x"], LAYOUT["applications"]["y"]),
    }
    assert s["window_rect"][1] == (LAYOUT["window"]["width"], LAYOUT["window"]["height"])
    assert s["icon_size"] == LAYOUT["icon_size"] and s["text_size"] == LAYOUT["text_size"]


def test_window_is_a_plain_install_window():
    s = settings()
    assert s["files"] == ["dist/termSwitch.app"]
    assert s["symlinks"] == {"Applications": "/Applications"}
    assert s["hide_extensions"] == ["termSwitch.app"]        # label reads "termSwitch"
    assert s["default_view"] == "icon-view"
    for bar in ("show_toolbar", "show_sidebar", "show_status_bar", "show_pathbar",
                "show_tab_view"):
        assert s[bar] is False, bar
    assert (ROOT / s["icon"]).is_file()
    assert s["format"] == "ULMO"


def test_background_matches_the_window_at_1x_and_2x():
    w, h = LAYOUT["window"]["width"], LAYOUT["window"]["height"]
    assert Path(settings()["background"]).name == "background.png"
    assert png_size(DMG / "background.png") == (w, h)
    assert png_size(DMG / "background@2x.png") == (2 * w, 2 * h)   # sharp on Retina


def test_icons_and_labels_fit_inside_the_window():
    w, h = LAYOUT["window"]["width"], LAYOUT["window"]["height"]
    half, pill = LAYOUT["icon_size"] / 2, LAYOUT["label_pill"]
    for key in ("app", "applications"):
        x, y = LAYOUT[key]["x"], LAYOUT[key]["y"]
        assert half <= x <= w - half and half <= y <= h - half, key
        assert pill["width"] / 2 <= x <= w - pill["width"] / 2, key
        assert y + pill["offset_y"] + pill["height"] / 2 < LAYOUT["footer"]["y"] - 12, key


def test_check_dmg_reports_what_is_missing(tmp_path):
    import check_dmg
    found = check_dmg.problems(tmp_path)
    assert "termSwitch.app is missing or incomplete" in found
    assert "Applications is not a link to /Applications" in found
    assert "the window background is missing" in found
    assert "the window layout (.DS_Store) is missing" in found


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra rights on Windows")
def test_check_dmg_reads_the_layout(tmp_path):
    ds_store = pytest.importorskip("ds_store")      # macOS build lock only
    import check_dmg
    app = tmp_path / "termSwitch.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "termSwitch").write_text("")
    os.symlink("/Applications", tmp_path / "Applications")
    (tmp_path / ".background.tiff").write_bytes(b"II*\x00")
    with ds_store.DSStore.open(str(tmp_path / ".DS_Store"), "w+") as d:
        d["termSwitch.app"]["Iloc"] = (LAYOUT["app"]["x"], LAYOUT["app"]["y"])
        d["Applications"]["Iloc"] = (LAYOUT["applications"]["x"] + 40, LAYOUT["applications"]["y"])
        d["."]["icvp"] = {"iconSize": float(LAYOUT["icon_size"])}
    found = check_dmg.problems(tmp_path)
    assert found == [f"Applications is at ({LAYOUT['applications']['x'] + 40}, "
                     f"{LAYOUT['applications']['y']}), the background expects "
                     f"({LAYOUT['applications']['x']}, {LAYOUT['applications']['y']})"]


def test_release_builds_and_checks_the_install_window():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "python -m dmgbuild -s packaging/dmg/settings.py" in wf
    assert 'python tools/check_dmg.py "$mnt" || status=$?' in wf
    assert "hdiutil create" not in wf
    reqs = (ROOT / "requirements-build.in").read_text(encoding="utf-8")
    assert 'dmgbuild>=1.6.7 ; sys_platform == "darwin"' in reqs
