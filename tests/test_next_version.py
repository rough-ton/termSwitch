import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("next_version", ROOT / "tools" / "next_version.py")
nv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nv)


def test_first_release_uses_base():
    assert nv.next_version((0, 6, 0), []) == "0.6.0"
    assert nv.next_version((0, 6, 0), ["v0.5.0", "latest", "v0.5.7"]) == "0.6.0"


def test_patch_increments_per_release():
    assert nv.next_version((0, 6, 0), ["v0.6.0"]) == "0.6.1"
    assert nv.next_version((0, 6, 0), ["v0.6.0", "v0.6.1", "v0.6.9"]) == "0.6.10"


def test_base_patch_is_a_floor():
    assert nv.next_version((0, 6, 5), ["v0.6.1"]) == "0.6.5"
    assert nv.next_version((0, 6, 5), ["v0.6.7"]) == "0.6.8"


def test_minor_bump_restarts_patch():
    assert nv.next_version((0, 7, 0), ["v0.6.0", "v0.6.1"]) == "0.7.0"


def test_ignores_non_semver_tags():
    assert nv.next_version((1, 0, 0), ["v1.0", "v1.0.0-rc1", "release-1.0.3", "v1.0.2"]) == "1.0.3"


def test_reads_base_version_from_package():
    text = (ROOT / "termswitch" / "__init__.py").read_text()
    major, minor, patch = nv.base_version(text)
    import termswitch
    assert termswitch.BASE_VERSION == f"{major}.{minor}.{patch}"
