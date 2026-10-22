"""App themes, terminal color schemes, scheme import and per-profile schemes."""

import json
import plistlib
import re
from pathlib import Path

import pytest

from termswitch import term_schemes as ts
from termswitch import theme

ROOT = Path(__file__).resolve().parent.parent


# ---- app theme ------------------------------------------------------------------

def test_light_and_dark_palettes(qapp):
    from PySide6.QtGui import QPalette
    m = theme.manager(qapp)
    assert m.apply(theme.DARK) == theme.DARK and theme.current() == theme.DARK
    window = qapp.palette().color(QPalette.ColorRole.Window)
    assert window.lightness() < 80
    assert theme.colors() is theme.DARK_COLORS
    from PySide6.QtWidgets import QLabel
    label = QLabel("x")
    theme.set_role(label, "error")
    assert label.palette().color(QPalette.ColorRole.WindowText).name() == "#ff6b5f"
    assert m.apply(theme.LIGHT) == theme.LIGHT
    assert qapp.palette().color(QPalette.ColorRole.Window).lightness() > 200
    assert theme.colors() is theme.LIGHT_COLORS
    assert label.palette().color(QPalette.ColorRole.WindowText).name() == "#c5221f"
    assert "fusion" in qapp.style().metaObject().className().lower()


def test_follow_system(qapp, monkeypatch):
    m = theme.manager(qapp)
    monkeypatch.setattr(theme, "system_is_dark", lambda app=None: True)
    assert m.apply(theme.SYSTEM) == theme.DARK
    seen = []
    m.changed.connect(seen.append)
    monkeypatch.setattr(theme, "system_is_dark", lambda app=None: False)
    m._system_changed()                                    # the OS switched to light
    assert seen[-1] == theme.LIGHT and theme.current() == theme.LIGHT
    m.apply(theme.DARK)
    monkeypatch.setattr(theme, "system_is_dark", lambda app=None: False)
    m._system_changed()                                    # ignored: not following
    assert theme.current() == theme.DARK
    m.apply(theme.LIGHT)


def _contrast(a: str, b: str) -> float:
    def lum(h):
        def ch(v):
            v = int(v, 16) / 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b_ = ch(h[1:3]), ch(h[3:5]), ch(h[5:7])
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("colors,background", [
    (theme.LIGHT_COLORS, "#ffffff"), (theme.LIGHT_COLORS, "#f3f3f3"),
    (theme.DARK_COLORS, "#1e1f22"), (theme.DARK_COLORS, "#2b2d30")])
def test_semantic_colors_are_readable(colors, background):
    for field in ("hint", "error", "warning", "success", "link", "prod"):
        assert _contrast(getattr(colors, field), background) >= 4.5, field


def test_no_hard_coded_colors_in_ui_code():
    """Colors with a meaning come from theme.py. Allowed: status dots, the production
    marker, the color picker's default, icon and terminal scheme definitions."""
    allowed_files = {"theme.py", "term_schemes.py", "terminal_core.py", "icons.py",
                     "build_icons.py"}
    allowed = {"#9aa0a6", "#f2b01e", "#2ea44f", "#e8710a", "#d93025",   # status dots
               "#c0392b", "#2d7ff9"}
    found = []
    for path in (ROOT / "termswitch").glob("*.py"):
        if path.name in allowed_files:
            continue
        for m in re.finditer(r"#[0-9a-fA-F]{6}\b", path.read_text()):
            if m.group(0).lower() not in allowed:
                found.append(f"{path.name}: {m.group(0)}")
    assert not found, found


def test_set_role(qapp):
    from PySide6.QtWidgets import QLabel
    label = QLabel("x")
    theme.set_role(label, "error")
    assert label.property("role") == "error"
    theme.set_role(label, None)
    assert label.property("role") is None


# ---- schemes --------------------------------------------------------------------

def test_builtin_schemes():
    assert {"Dark", "Light", "High contrast"} <= set(ts.BUILTIN)
    assert ts.BUILTIN["Dark"].is_dark and not ts.BUILTIN["Light"].is_dark
    for s in ts.BUILTIN.values():
        assert _contrast(s.foreground, s.background) >= 7, s.name
        for color in s.ansi[1:7]:                         # red .. cyan
            assert _contrast(color, s.background) >= 2.5, (s.name, color)


def test_resolve():
    dark = ts.BUILTIN["Dark"]
    assert dark.resolve("default", "#000000") == "#000000"
    assert dark.resolve("red", "#000000") == dark.ansi[1]
    assert dark.resolve("brightred", "#000000") == dark.ansi[9]
    assert dark.resolve("yellow", "#000000") == dark.resolve("brown", "#000000") == dark.ansi[3]
    assert dark.resolve("FF8800", "#000000") == "#ff8800"
    assert dark.resolve("bogus", "#123456") == "#123456"


@pytest.mark.parametrize("kwargs", [
    {"name": "x\nevil"}, {"foreground": "red"}, {"background": "#12345"},
    {"ansi": ("#000000",) * 15}, {"selection": "#gg0000"}])
def test_validation(kwargs):
    base = dict(name="Ok", foreground="#ffffff", background="#000000", cursor="#ffffff",
                selection="#333333", ansi=("#000000",) * 16)
    base.update(kwargs)
    with pytest.raises(ts.SchemeError):
        ts.ColorScheme(**base)


def test_scheme_for():
    schemes = ts.all_schemes(Path("/nonexistent"))
    assert ts.scheme_for(ts.MATCH_APP, "dark", schemes).name == "Dark"
    assert ts.scheme_for(ts.MATCH_APP, "light", schemes).name == "Light"
    assert ts.scheme_for("High contrast", "light", schemes).name == "High contrast"
    assert ts.scheme_for("Deleted scheme", "light", schemes).name == "Light"


WT = {"name": "Campbell Powershell", "foreground": "#CCCCCC", "background": "#012456",
      "cursorColor": "#FFFFFF", "selectionBackground": "#FFFFFF",
      "black": "#0C0C0C", "red": "#C50F1F", "green": "#13A10E", "yellow": "#C19C00",
      "blue": "#0037DA", "purple": "#881798", "cyan": "#3A96DD", "white": "#CCCCCC",
      "brightBlack": "#767676", "brightRed": "#E74856", "brightGreen": "#16C60C",
      "brightYellow": "#F9F1A5", "brightBlue": "#3B78FF", "brightPurple": "#B4009E",
      "brightCyan": "#61D6D6", "brightWhite": "#F2F2F2"}


def test_import_windows_terminal(tmp_path):
    single = tmp_path / "scheme.json"
    single.write_text(json.dumps(WT))
    [s] = ts.import_file(single)
    assert s.name == "Campbell Powershell" and s.background == "#012456"
    assert s.ansi[5] == "#881798"                         # purple is magenta
    settings = tmp_path / "settings.json"
    settings.write_text("﻿" + json.dumps({"profiles": {}, "schemes": [WT, {**WT, "name": "B"}]}))
    assert [x.name for x in ts.import_file(settings)] == ["Campbell Powershell", "B"]


@pytest.mark.parametrize("content", [
    json.dumps({**WT, "red": "javascript:alert(1)"}), json.dumps({"name": "x"}), "not json",
    json.dumps({**WT, "name": "bad\nname"})])
def test_import_windows_terminal_rejects_bad_files(tmp_path, content):
    f = tmp_path / "bad.json"
    f.write_text(content)
    with pytest.raises(ts.SchemeError):
        ts.import_file(f)


def _iterm(path: Path, extra: bytes = b""):
    def color(r, g, b):
        return {"Red Component": r, "Green Component": g, "Blue Component": b,
                "Color Space": "sRGB"}
    data = {f"Ansi {i} Color": color(i / 15, 0.5, 1 - i / 15) for i in range(16)}
    data["Foreground Color"] = color(0.9, 0.9, 0.9)
    data["Background Color"] = color(0.1, 0.1, 0.12)
    data["Cursor Color"] = color(1, 1, 1)
    raw = plistlib.dumps(data, fmt=plistlib.FMT_XML)
    if extra:
        raw = raw.replace(b"<plist", extra + b"<plist", 1)
    path.write_bytes(raw)
    return path


def test_import_iterm(tmp_path):
    [s] = ts.import_file(_iterm(tmp_path / "Night Owl.itermcolors"))
    assert s.name == "Night Owl" and s.foreground == "#e6e6e6" and s.background == "#1a1a1f"
    assert len(s.ansi) == 16 and s.selection == "#264f78"


def test_import_iterm_refuses_entities_and_big_files(tmp_path):
    evil = _iterm(tmp_path / "evil.itermcolors",
                  b'<!DOCTYPE x [<!ENTITY a "aaaaaaaaaa">]>\n')
    with pytest.raises(ts.SchemeError):
        ts.import_file(evil)
    big = tmp_path / "big.itermcolors"
    big.write_bytes(b" " * (ts.MAX_IMPORT_BYTES + 1))
    with pytest.raises(ts.SchemeError):
        ts.import_file(big)
    with pytest.raises(ts.SchemeError):
        ts.import_file(tmp_path / "x.txt")


def test_save_and_load_user_schemes(tmp_path):
    [s] = ts.import_windows_terminal(_write(tmp_path / "a.json", WT))
    path = ts.save_user_scheme(s, tmp_path / "schemes")
    assert path.name == "Campbell_Powershell.json"
    (tmp_path / "schemes" / "broken.json").write_text("{nope")
    loaded = ts.load_user_schemes(tmp_path / "schemes")
    assert list(loaded) == ["Campbell Powershell"] and loaded[s.name] == s
    with pytest.raises(ts.SchemeError):
        ts.save_user_scheme(ts.BUILTIN["Dark"], tmp_path / "schemes")


def _write(path: Path, data) -> Path:
    path.write_text(json.dumps(data))
    return path


# ---- terminals and profiles ----------------------------------------------------------

def test_terminal_recolors_in_place(qapp):
    from termswitch.terminal_widget import TerminalWidget
    t = TerminalWidget(["/bin/sh", "-c", "true"], {})
    assert t.scheme.name == "Dark"
    t.set_scheme(ts.BUILTIN["Light"])
    assert t.scheme.name == "Light" and "#ffffff" in t.styleSheet()
    assert t.pty is None                                   # nothing restarted
    t.deleteLater()


def test_profile_scheme_round_trip(tmp_path):
    from termswitch.config_store import ConfigError, ProfileStore
    cfg = tmp_path / "config"
    cfg.write_text("[profile a]\nregion = us-west-2\n")
    store = ProfileStore(cfg, tmp_path / "credentials")
    store.load()
    store.set_profile_scheme("a", "High contrast")
    assert "x_term_scheme = High contrast" in cfg.read_text()
    assert store.profiles["a"].term_scheme == "High contrast"
    store.set_profile_scheme("a", None)
    assert store.profiles["a"].term_scheme is None
    with pytest.raises(ConfigError):
        store.set_profile_scheme("a", "bad\nname")


def test_main_window_schemes_and_theme_switch(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from termswitch import ui
    from termswitch.config_store import ProfileStore
    from termswitch.terminal_tabs import TabInfo
    from termswitch.terminal_widget import TerminalWidget
    cfg = tmp_path / "config"
    cfg.write_text("[profile a]\nregion = us-west-2\n[profile b]\nregion = us-east-1\n")
    store = ProfileStore(cfg, tmp_path / "credentials")
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("check_on_startup", False)
    settings.setValue("app_theme", "light")
    monkeypatch.setattr(ui.MainWindow, "run_dependency_check", lambda *a, **k: None)
    w = ui.MainWindow(store=store, settings=settings)
    # Stand-in tabs for profiles a and b.
    terms = {}
    for name in ("a", "b"):
        t = TerminalWidget(["/bin/sh", "-c", "true"], {})
        w.terminals._infos[id(t)] = TabInfo(store.profiles[name], None, t, name)
        w.terminals.tabs.addTab(t, name)
        terms[name] = t
    w.terminals.apply_schemes(store.profiles)
    assert terms["a"].scheme.name == "Light"
    w.set_profile_scheme([store.profiles["b"]], "High contrast")
    assert terms["b"].scheme.name == "High contrast"
    w.theme.apply("dark")                                  # live switch
    assert terms["a"].scheme.name == "Dark"
    assert terms["b"].scheme.name == "High contrast"       # its own scheme stays
    settings.setValue("term_scheme", "High contrast")
    w.terminals.apply_schemes(store.profiles)
    assert terms["a"].scheme.name == "High contrast"
    w.theme.apply("light")
    w.terminals.tabs.clear()
    w.terminals._infos.clear()
    w.close()
    w.deleteLater()


def test_settings_dialog_saves_theme_and_scheme(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from termswitch import ui
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    dlg = ui.SettingsDialog(settings, schemes=ts.all_schemes(tmp_path / "none"))
    assert dlg.app_theme.currentData() == theme.SYSTEM
    assert dlg.term_scheme.currentData() == ts.MATCH_APP
    assert dlg.term_scheme.itemText(0) == "Match the app theme"
    dlg.app_theme.setCurrentIndex(dlg.app_theme.findData(theme.DARK))
    dlg.term_scheme.setCurrentIndex(dlg.term_scheme.findData("High contrast"))
    dlg._save()
    assert settings.value("app_theme") == "dark"
    assert settings.value("term_scheme") == "High contrast"
    dlg.deleteLater()
