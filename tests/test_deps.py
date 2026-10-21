from datetime import date

from termswitch import deps


def test_behind():
    assert deps.behind("2.27.3", "2.38.4") == "minor"
    assert deps.behind("2.38.1", "2.38.4") == "patch"
    assert deps.behind("1.9", "2.0") == "major"
    assert deps.behind("6.12.0", "6.12.0") is None
    assert deps.behind("4.0", "4.0.0") is None


def test_python_eol(monkeypatch):
    monkeypatch.setattr(deps.sys, "version_info", (3, 10, 14, "final", 0))
    assert deps.check_python(date(2026, 11, 1)).level == deps.WARN
    monkeypatch.setattr(deps.sys, "version_info", (3, 9, 0, "final", 0))
    assert deps.check_python(date(2026, 11, 1)).level == deps.ERROR
    monkeypatch.setattr(deps.sys, "version_info", (3, 13, 1, "final", 0))
    assert deps.check_python(date(2026, 11, 1)).level == deps.OK


def test_missing_package():
    c = deps.check_package("definitely-not-installed-pkg", "1.0", None, False)
    assert c.level == deps.ERROR and "requirements" in c.fix


def test_outdated_package(monkeypatch):
    monkeypatch.setattr(deps.metadata, "version", lambda d: "6.6.0")
    assert deps.check_package("PySide6-Essentials", "6.6.0", "6.12.0", True).level == deps.WARN
    assert deps.check_package("PySide6-Essentials", "6.8.0", "6.12.0", True).level == deps.ERROR


def _fake_aws(monkeypatch, version_line):
    class P:
        stdout, stderr, returncode = version_line, "", 0
    monkeypatch.setattr(deps, "find_aws", lambda: "/usr/local/bin/aws")
    monkeypatch.setattr(deps.subprocess, "run", lambda *a, **k: P())


def test_aws_cli_v1_is_error(monkeypatch):
    _fake_aws(monkeypatch, "aws-cli/1.33.0 Python/3.11 Darwin/23")
    assert deps.check_aws_cli(None, False).level == deps.ERROR


def test_aws_cli_too_old(monkeypatch):
    _fake_aws(monkeypatch, "aws-cli/2.7.0 Python/3.9")
    assert deps.check_aws_cli("2.38.4", True).level == deps.ERROR


def test_aws_cli_patch_behind_is_info(monkeypatch):
    _fake_aws(monkeypatch, "aws-cli/2.38.1 Python/3.13")
    assert deps.check_aws_cli("2.38.4", True).level == deps.INFO


def test_aws_cli_missing(monkeypatch):
    monkeypatch.setattr(deps, "find_aws", lambda: None)
    assert deps.check_aws_cli(None, False).level == deps.ERROR


def test_fetch_refuses_http():
    import pytest
    with pytest.raises(ValueError):
        deps._fetch("http://pypi.org/pypi/x/json")
