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
