from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATERIAL = ROOT / "termswitch" / "assets" / "icons" / "material"


def test_every_used_icon_is_bundled_and_renders(qapp):
    from termswitch.icons import AUTO_GROUP_ICONS, icon
    used = set(AUTO_GROUP_ICONS.values()) | {
        "folder-base", "folder-secure", "folder-aws", "console", "powershell",
        "verified", "settings"}
    for name in used:
        assert (MATERIAL / f"{name}.svg").is_file(), name
        ic = icon(name)
        assert not ic.isNull(), name
        assert not ic.pixmap(32, 32).isNull(), name


def test_dot_and_gray_variants(qapp):
    from termswitch.icons import shell_icon
    assert not shell_icon(dot="#c0392b").isNull()
    assert not shell_icon(exited=True).isNull()


def test_license_and_notice_are_present():
    assert "MIT" in (MATERIAL / "LICENSE").read_text()
    notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    assert "Material Icon Theme" in notice and "Permission is hereby granted" in notice


def test_every_mapped_icon_is_bundled_and_every_bundled_icon_is_used(qapp):
    import re

    from termswitch import icons
    mapped = ({n for _p, n in icons.TEXT_ICONS}
              | set(icons.SERVICE_ICONS.values()) | set(icons.AUTO_GROUP_ICONS.values()))
    for name in mapped:
        assert (MATERIAL / f"{name}.svg").is_file(), name
        assert not icons.icon(name).pixmap(32, 32).isNull(), name
    # Icons referenced directly in code count as used too.
    code = "\n".join(p.read_text() for p in (ROOT / "termswitch").glob("*.py"))
    direct = set(re.findall(r'(?:material_icon|icon)\("([a-z0-9-]+)"', code))
    direct |= {"console", "powershell", "markdown", "license"}
    bundled = {p.stem for p in MATERIAL.glob("*.svg")}
    assert bundled <= mapped | direct, sorted(bundled - mapped - direct)


def test_bundled_svgs_are_plain_artwork():
    for svg in MATERIAL.glob("*.svg"):
        text = svg.read_text()
        assert "<script" not in text and "http://" not in text.replace(
            "http://www.w3.org", ""), svg.name


def test_icon_for_text():
    from termswitch.icons import icon_for_text
    cases = {
        "Reload": "folder-update", "&Settings": "settings", "New profile...": "key",
        "Open 3 terminals": "console", "Open all 12 terminals": "console",
        "Open in external terminal": "command", "Check status of group": "verified",
        "Delete 2 profiles...": "folder-trash", "Undo Delete profile ops": "folder-backup",
        "Duplicate tab": "console", "Duplicate...": "template",
        "Edit config file...": "editorconfig", "Edit...": "settings",
        "Restore default favorites (2)": "folder-backup", "Report an issue": "folder-debug",
        "Licenses...": "license", "Run": "console", "Docs": "readme",
    }
    for text, name in cases.items():
        assert icon_for_text(text) == name, text
    for text in ("Close", "Cancel", "OK", "Copy", "Quit", "Check dependencies later"):
        assert icon_for_text(text) in (None, "dependencies-update"), text
    assert icon_for_text("Copy") is None and icon_for_text("Close") is None


def test_menus_buttons_and_toolbars_get_icons(qapp):
    from conftest import pump
    from PySide6.QtWidgets import QMenu, QPushButton, QToolBar, QVBoxLayout, QWidget

    from termswitch.icons import install_icon_theme
    install_icon_theme(qapp)
    install_icon_theme(qapp)                         # idempotent
    w = QWidget()
    lay = QVBoxLayout(w)
    bar = QToolBar()
    reload_action = bar.addAction("Reload")
    lay.addWidget(bar)
    run = QPushButton("Run")
    plain = QPushButton("Close")
    lay.addWidget(run)
    lay.addWidget(plain)
    w.show()
    pump(qapp, 0.1)
    assert not reload_action.icon().isNull()
    assert not run.icon().isNull() and plain.icon().isNull()
    menu = QMenu()
    rename = menu.addAction("Rename...")
    menu.show()
    pump(qapp, 0.1)
    assert not rename.icon().isNull()
    undo = menu.addAction("Undo Rename profile a to b")
    menu.aboutToShow.emit()                          # items added later are decorated too
    assert not undo.icon().isNull()
    menu.close()
    w.close()


def test_provider_icons_are_termswitch_artwork(qapp):
    """AWS and SSH icons are termSwitch's own (the theme has none), drawn in the
    Material style, and take precedence over the Material folder."""
    from termswitch.icons import PROVIDER_ICONS, icon
    own = ROOT / "termswitch" / "assets" / "icons" / "termswitch"
    assert set(PROVIDER_ICONS) == {"aws", "ssh"}
    for name in PROVIDER_ICONS.values():
        svg = own / f"{name}.svg"
        assert svg.is_file(), name
        text = svg.read_text()
        assert "<script" not in text and "href" not in text
        assert not icon(name, dot="#2ea44f").pixmap(32, 32).isNull()
    assert {p.stem for p in own.glob("*.svg")} == set(PROVIDER_ICONS.values())


def test_tree_uses_the_aws_icon(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from termswitch import icons, ui
    from termswitch.config_store import ProfileStore
    cfg = tmp_path / "config"
    cfg.write_text("[profile a]\nregion = us-west-2\n[profile b]\nrole_arn = "
                   "arn:aws:iam::123456789012:role/x\nsource_profile = a\n")
    store = ProfileStore(cfg, tmp_path / "credentials")
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("check_on_startup", False)
    monkeypatch.setattr(ui.MainWindow, "run_dependency_check", lambda *a, **k: None)
    used = []
    real = icons.icon.__wrapped__

    def spy(name, dot=None, gray=False):
        used.append(name)
        return real(name, dot, gray)
    monkeypatch.setattr(ui, "material_icon", spy)
    w = ui.MainWindow(store=store, settings=settings)
    w.rebuild()
    assert used.count("aws") >= 2
    w.close()
    w.deleteLater()
