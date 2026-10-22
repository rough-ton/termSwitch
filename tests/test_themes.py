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
    allowed_files = {"theme.py", "term_schemes.py", "system_terminal.py", "terminal_core.py",
                     "icons.py", "build_icons.py"}
    allowed = {"#9aa0a6", "#f2b01e", "#2ea44f", "#e8710a", "#d93025",   # status dots
               "#c0392b", "#2d7ff9"}
    found = []
    for path in (ROOT / "termswitch").glob("*.py"):
        if path.name in allowed_files:
            continue
        for m in re.finditer(r"#[0-9a-fA-F]{6}\b", path.read_text(encoding="utf-8")):
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
    # termSwitch's own schemes are tuned for contrast. The popular ones below keep
    # their projects' published colors as they are.
    for name in ("Dark", "Light", "High contrast"):
        s = ts.BUILTIN[name]
        assert _contrast(s.foreground, s.background) >= 7, s.name
        for color in s.ansi[1:7]:                         # red .. cyan
            assert _contrast(color, s.background) >= 2.5, (s.name, color)


def test_popular_schemes_are_bundled():
    assert ts.POPULAR == ("Dracula", "Catppuccin Mocha", "Catppuccin Latte", "Solarized Dark",
                          "Solarized Light", "Nord", "Gruvbox Dark")
    for name in ts.POPULAR:
        s = ts.BUILTIN[name]
        assert s.builtin and s.name == name and len(s.ansi) == 16
        # Solarized is deliberately soft: its light body text is 4.1:1.
        assert _contrast(s.foreground, s.background) >= 4, name
    light = {n for n in ts.POPULAR if not ts.BUILTIN[n].is_dark}
    assert light == {"Catppuccin Latte", "Solarized Light"}
    # Spot checks against the published palettes.
    assert ts.BUILTIN["Dracula"].background == "#282a36"
    assert ts.BUILTIN["Solarized Dark"].background == "#002b36"
    assert ts.BUILTIN["Nord"].background == "#2e3440"
    assert ts.BUILTIN["Gruvbox Dark"].ansi[9] == "#fb4934"
    assert ts.BUILTIN["Catppuccin Mocha"].foreground == "#cdd6f4"


def test_an_import_cannot_replace_a_bundled_scheme(tmp_path):
    folder = tmp_path / "schemes"
    folder.mkdir()
    fake = {**ts.BUILTIN["Dark"].to_json(), "name": "Dracula"}
    (folder / "Dracula.json").write_text(json.dumps(fake), encoding="utf-8")
    assert "Dracula" not in ts.load_user_schemes(folder)
    assert ts.all_schemes(folder)["Dracula"].background == "#282a36"
    with pytest.raises(ts.SchemeError):
        ts.save_user_scheme(ts.BUILTIN["Nord"], folder)


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
    single.write_text(json.dumps(WT), encoding="utf-8")
    [s] = ts.import_file(single)
    assert s.name == "Campbell Powershell" and s.background == "#012456"
    assert s.ansi[5] == "#881798"                         # purple is magenta
    settings = tmp_path / "settings.json"
    # Windows Terminal saves settings.json with a byte order mark.
    data = {"profiles": {}, "schemes": [WT, {**WT, "name": "B"}]}
    settings.write_text("\ufeff" + json.dumps(data), encoding="utf-8")
    assert [x.name for x in ts.import_file(settings)] == ["Campbell Powershell", "B"]


@pytest.mark.parametrize("content", [
    json.dumps({**WT, "red": "javascript:alert(1)"}), json.dumps({"name": "x"}), "not json",
    json.dumps({**WT, "name": "bad\nname"})])
def test_import_windows_terminal_rejects_bad_files(tmp_path, content):
    f = tmp_path / "bad.json"
    f.write_text(content, encoding="utf-8")
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
    (tmp_path / "schemes" / "broken.json").write_text("{nope", encoding="utf-8")
    loaded = ts.load_user_schemes(tmp_path / "schemes")
    assert list(loaded) == ["Campbell Powershell"] and loaded[s.name] == s
    with pytest.raises(ts.SchemeError):
        ts.save_user_scheme(ts.BUILTIN["Dark"], tmp_path / "schemes")


def _write(path: Path, data) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
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
    cfg.write_text("[profile a]\nregion = us-west-2\n", encoding="utf-8")
    store = ProfileStore(cfg, tmp_path / "credentials")
    store.load()
    store.set_profile_scheme("a", "High contrast")
    assert "x_term_scheme = High contrast" in cfg.read_text(encoding="utf-8")
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
    cfg.write_text("[profile a]\nregion = us-west-2\n[profile b]\nregion = us-east-1\n",
                   encoding="utf-8")
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


def test_schemes_never_change_anything_outside_termswitch(qapp, tmp_path, monkeypatch):
    """Switching schemes only repaints termSwitch's own tabs. It writes no files
    outside the app (other terminals' settings, shell startup files) and sends
    nothing to the running shell, such as color-changing escape codes."""
    from PySide6.QtCore import QSettings

    from termswitch import ui
    from termswitch.config_store import ProfileStore
    from termswitch.terminal_tabs import TabInfo
    from termswitch.terminal_widget import TerminalWidget
    home = tmp_path / "home"
    others = {
        # Windows Terminal, iTerm2, Terminal.app and shell startup files.
        "AppData/Local/Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/"
        "settings.json": '{"schemes": []}',
        "Library/Preferences/com.googlecode.iterm2.plist": "<plist/>",
        "Library/Preferences/com.apple.Terminal.plist": "<plist/>",
        ".zshrc": "# zsh", ".bashrc": "# bash",
        "Documents/PowerShell/Microsoft.PowerShell_profile.ps1": "# pwsh",
    }
    for rel, text in others.items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(text, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def snapshot():
        return {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    before = snapshot()

    cfg = tmp_path / "config"
    cfg.write_text("[profile a]\nregion = us-west-2\n", encoding="utf-8")
    store = ProfileStore(cfg, tmp_path / "credentials")
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("check_on_startup", False)
    monkeypatch.setattr(ui.MainWindow, "run_dependency_check", lambda *a, **k: None)
    w = ui.MainWindow(store=store, settings=settings)

    class RecordingPty:
        def __init__(self):
            self.sent = []

        def write(self, text):
            self.sent.append(text)

        def is_alive(self):
            return True
    t = TerminalWidget(["/bin/sh", "-c", "true"], {})
    t.pty = RecordingPty()
    w.terminals._infos[id(t)] = TabInfo(store.profiles["a"], None, t, "a")
    w.terminals.tabs.addTab(t, "a")
    try:
        for name in ts.POPULAR:                   # the default for every tab
            settings.setValue("term_scheme", name)
            w.terminals.apply_schemes(store.profiles)
            assert t.scheme.name == name
        for name in ts.POPULAR:                   # one profile's own scheme
            w.set_profile_scheme([store.profiles["a"]], name)
            assert t.scheme.name == name
        w.theme.apply("dark")
        w.theme.apply("light")
        assert t.pty.sent == []                   # nothing sent to the shell
        assert snapshot() == before               # no files changed outside the app
    finally:
        t.pty = None
        w.terminals.tabs.clear()
        w.terminals._infos.clear()
        w.close()
        w.deleteLater()


# ---- 256-color palette codes and Powerline separators -------------------------------

def test_palette_codes_use_the_scheme_colors():
    """Prompt themes pick colors 0 to 15 with 38;5;n / 48;5;n. Those are the
    scheme's colors, not fixed xterm values."""
    import pyte

    from termswitch.terminal_core import TerminalScreen
    s = TerminalScreen(20, 2)
    pyte.Stream(s).feed("\x1b[38;5;4;48;5;13mA\x1b[38;5;200mB\x1b[105mC\x1b[38;5;1;39mD"
                        "\x1b[0mE\x1b[38;2;1;2;3mF\x1b[38;5;9;1mG")
    cells = [(c.data, c.fg, c.bg) for c in s.buffer[0].values()]
    assert cells == [("A", "blue", "brightmagenta"), ("B", "ff00d7", "brightmagenta"),
                     ("C", "ff00d7", "brightmagenta"), ("D", "default", "brightmagenta"),
                     ("E", "default", "default"), ("F", "010203", "default"),
                     ("G", "brightred", "default")]
    dracula = ts.BUILTIN["Dracula"]
    assert dracula.resolve("blue", "#000000") == "#bd93f9"
    assert dracula.resolve("brightmagenta", "#000000") == "#ff92df"


def test_powerline_arrows_fill_the_cell(qapp):
    """The solid arrow is drawn as a shape: its flat side fills the whole cell
    height, so it joins the segment before it with no seam."""
    from PySide6.QtGui import QColor, QImage

    from termswitch.terminal_widget import TerminalWidget
    t = TerminalWidget(["/bin/true"], {})
    t.set_scheme(ts.BUILTIN["Dracula"])
    t.screen.resize(2, 20)
    # A red segment, then a red arrow on the default background.
    t.stream.feed("\x1b[41m  \x1b[0;31m\ue0b0\x1b[0m")
    # Paint straight into an image. Showing and grabbing a top-level window
    # crashed Python 3.12 on CI, and nothing here needs a window.
    cw, ch = t._cw, t._ch
    t.viewport().resize(int(cw * 20) + 2, int(ch * 2) + 2)
    img = QImage(t.viewport().size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    t.viewport().render(img)
    red = QColor(ts.BUILTIN["Dracula"].ansi[1])
    bg = QColor(ts.BUILTIN["Dracula"].background)

    def px(x, y):
        return img.pixelColor(int(x), int(y))

    def near(a, b):
        return all(abs(x - y) <= 24 for x, y in zip(a.getRgb()[:3], b.getRgb()[:3]))
    x0 = 2 * cw                                          # where the arrow cell starts
    # No seam where the segment meets the arrow: solid red across the boundary.
    for y in (ch / 4, ch / 3, ch / 2, ch * 2 / 3, ch * 3 / 4):
        for x in (x0 - 1, x0 - 0.5, x0, x0 + 0.5, x0 + 1):
            assert near(px(x, y), red), (x, y)
    # Inside the point, well clear of its anti-aliased edge (cells are as narrow as
    # 8 to 13 pixels depending on the platform's font).
    assert near(px(x0 + cw * 0.6, ch / 2), red)
    assert near(px(x0 + cw - 1, 1), bg)                   # outside the arrow
    assert near(px(x0 + cw - 1, ch - 1.5), bg)
    t.deleteLater()


def test_theme_change_skips_widgets_being_deleted(qapp, monkeypatch):
    """Switching themes right after a tab closes must not touch deleted widgets."""
    from PySide6.QtWidgets import QApplication, QLabel

    alive = QLabel("x")
    theme.set_role(alive, "hint")
    doomed = QLabel("y")

    class Gone:
        def property(self, name):
            raise RuntimeError("Internal C++ object already deleted.")
    monkeypatch.setattr(QApplication, "allWidgets", staticmethod(lambda: [Gone(), doomed, alive]))
    theme._recolor_roles()                      # no error
    assert alive.palette().color(alive.foregroundRole()).name() == theme.colors().hint
    alive.deleteLater()
    doomed.deleteLater()
