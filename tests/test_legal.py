"""The project license, third-party notices, and the Help > Licenses viewer."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"
LOCKS = ["requirements.lock", "requirements-build.lock"]
# Build-only packages that are not shipped inside the app. PyInstaller itself is listed
# because its bootloader is embedded; its helper packages are not.
# Build tools whose code never ships in the app. dmgbuild, ds-store and mac-alias only
# write the .dmg's window layout (.DS_Store and an alias); none of their code is in it.
BUILD_ONLY = {"altgraph", "macholib", "packaging", "pefile", "pyinstaller-hooks-contrib",
              "pywin32-ctypes", "setuptools", "dmgbuild", "ds-store", "mac-alias"}


def _locked(lock):
    text = (ROOT / lock).read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)", text, re.M))


def _notice_rows():
    """{package: version} from the summary table's first two columns."""
    rows = {}
    for name, version in re.findall(r"^\| `([^`]+)`[^|]*\| ([^|]+?) \|", NOTICES.read_text(), re.M):
        rows[name.lower()] = version.strip()
    return rows


def test_license_is_mit():
    text = (ROOT / "LICENSE").read_text()
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 Mike Roughton" in text


def test_pyproject_declares_license():
    text = (ROOT / "pyproject.toml").read_text()
    assert 'license = "MIT"' in text
    assert "license-files" in text


def test_every_locked_runtime_package_has_a_notice():
    rows = _notice_rows()
    missing, stale = [], []
    for name, version in _locked("requirements.lock").items():
        key = name.lower()
        if key not in rows:
            missing.append(name)
        elif rows[key] != version:
            stale.append(f"{name} {rows[key]} (locked {version})")
    assert not missing, f"add these to THIRD_PARTY_NOTICES.md: {missing}"
    assert not stale, f"update versions in THIRD_PARTY_NOTICES.md: {stale}"


def test_bundled_build_packages_have_a_notice():
    rows = _notice_rows()
    for name, version in _locked("requirements-build.lock").items():
        key = name.lower()
        if key in BUILD_ONLY:
            continue
        assert key in rows, f"{name} is in the build lock but not in THIRD_PARTY_NOTICES.md"
        assert rows[key] == version, f"{name}: notices say {rows[key]}, lock has {version}"


def test_full_license_texts_present():
    for name, marker in [("LGPL-3.0.txt", "GNU LESSER GENERAL PUBLIC LICENSE"),
                         ("GPL-3.0.txt", "Version 3, 29 June 2007"),
                         ("GPL-2.0.txt", "Version 2, June 1991"),
                         ("Apache-2.0.txt", "Apache License")]:
        assert marker in (ROOT / "licenses" / name).read_text(), name


def test_spec_bundles_legal_files():
    spec = (ROOT / "termswitch.spec").read_text()
    for needle in ('"LICENSE"', '"THIRD_PARTY_NOTICES.md"', '"licenses/*.txt"', "LEGAL"):
        assert needle in spec


def test_documents_found_from_source_checkout():
    from termswitch import legal

    docs = legal.documents()
    names = [d.path.name for d in docs]
    assert names[:2] == ["LICENSE", "THIRD_PARTY_NOTICES.md"]
    for n in ("LGPL-3.0.txt", "GPL-3.0.txt", "GPL-2.0.txt", "Apache-2.0.txt"):
        assert n in names


def test_documents_from_bundle_layout(tmp_path, monkeypatch):
    from termswitch import legal

    bundle = tmp_path / "legal"
    (bundle / "licenses").mkdir(parents=True)
    (bundle / "LICENSE").write_text("MIT License")
    (bundle / "THIRD_PARTY_NOTICES.md").write_text("# Notices")
    (bundle / "licenses" / "Python-LICENSE.txt").write_text("PSF")
    (bundle / "licenses" / "LGPL-3.0.txt").write_text("LGPL")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert legal.legal_dir() == bundle
    titles = [d.title for d in legal.documents()]
    assert titles == ["termSwitch license (MIT)", "Third-party notices",
                      "GNU LGPL 3.0 (Qt, pyte)", "Python Software Foundation License"]


def test_read_text_refuses_large_files(tmp_path):
    import pytest

    from termswitch import legal

    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * (legal.MAX_BYTES + 1))
    with pytest.raises(OSError):
        legal.read_text(big)


def test_dialog_shows_each_document(qapp):
    from termswitch.legal import LicensesDialog

    dlg = LicensesDialog(start="Third-party notices")
    assert dlg.list.currentItem().text() == "Third-party notices"
    assert "Third-party notices" in dlg.view.toPlainText()
    for row in range(dlg.list.count()):
        dlg.list.setCurrentRow(row)
        assert dlg.view.toPlainText().strip(), dlg.list.item(row).text()
    dlg.list.setCurrentRow(0)
    assert "MIT License" in dlg.view.toPlainText()
    dlg.close()


def test_relative_link_switches_document(qapp):
    from PySide6.QtCore import QUrl

    from termswitch.legal import LicensesDialog

    dlg = LicensesDialog()
    dlg._open_link(QUrl("licenses/LGPL-3.0.txt"))
    assert dlg.list.currentItem().text().startswith("GNU LGPL 3.0")
    dlg.close()
