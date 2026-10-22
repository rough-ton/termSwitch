"""replace_file: retries on Windows while another process holds the file."""

import os

import pytest

from termswitch import fileutil


def _flaky_replace(failures):
    calls = {"n": 0}
    real = os.replace

    def fake(src, dst):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise PermissionError(5, "Access is denied")
        real(src, dst)
    return fake, calls


def test_retries_on_windows_until_the_file_is_free(tmp_path, monkeypatch):
    src, dst = tmp_path / "new", tmp_path / "config"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    fake, calls = _flaky_replace(3)
    monkeypatch.setattr(fileutil.sys, "platform", "win32")
    monkeypatch.setattr(fileutil.os, "replace", fake)
    fileutil.replace_file(src, dst)
    assert calls["n"] == 4
    assert dst.read_text(encoding="utf-8") == "new" and not src.exists()


def test_gives_up_on_windows_after_the_time_limit(tmp_path, monkeypatch):
    src, dst = tmp_path / "new", tmp_path / "config"
    src.write_text("new", encoding="utf-8")
    fake, calls = _flaky_replace(10_000)
    monkeypatch.setattr(fileutil.sys, "platform", "win32")
    monkeypatch.setattr(fileutil.os, "replace", fake)
    with pytest.raises(PermissionError):
        fileutil.replace_file(src, dst, retry_seconds=0.1)
    assert calls["n"] > 1 and src.exists()


def test_no_retry_off_windows(tmp_path, monkeypatch):
    src, dst = tmp_path / "new", tmp_path / "config"
    src.write_text("new", encoding="utf-8")
    fake, calls = _flaky_replace(1)
    monkeypatch.setattr(fileutil.sys, "platform", "linux")
    monkeypatch.setattr(fileutil.os, "replace", fake)
    with pytest.raises(PermissionError):
        fileutil.replace_file(src, dst)
    assert calls["n"] == 1


def test_other_errors_are_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(fileutil.sys, "platform", "win32")
    with pytest.raises(FileNotFoundError):
        fileutil.replace_file(tmp_path / "missing", tmp_path / "config")
