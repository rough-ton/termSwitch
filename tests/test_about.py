"""Help > About: version text, links, update status and system information."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from termswitch import about, links
from termswitch.config_store import ProfileStore, safe_https_url

from test_store_and_launcher import CREDS, SAMPLE


def test_version_text_source_and_release(monkeypatch):
    monkeypatch.setattr(about, "IS_RELEASE", False)
    monkeypatch.setattr(about, "BASE_VERSION", "0.6.0")
    assert about.version_text() == "0.6.0 (source)"
    monkeypatch.setattr(about, "IS_RELEASE", True)
    monkeypatch.setattr(about, "__version__", "0.6.4")
    monkeypatch.setattr(about, "__commit__", "1a2b3c4")
    assert about.version_text() == "0.6.4 (1a2b3c4)"


def test_links():
    assert links.WEBSITE == "https://about.mroughton.com"
    assert links.release_notes_url("0.6.4", True).endswith("/releases/tag/v0.6.4")
    assert links.release_notes_url("0.6.0", False).endswith("/releases")
    url = links.new_issue_url("0.6.4 (1a2b3c4)", "macOS 15.1, arm64")
    parsed = urlparse(url)
    assert url.startswith(links.NEW_ISSUE_URL + "?")
    body = parse_qs(parsed.query)["body"][0]
    assert "termSwitch: 0.6.4 (1a2b3c4)" in body and "OS: macOS 15.1, arm64" in body
    for u in (links.WEBSITE, links.REPO_URL, links.LATEST_RELEASE_URL, url,
              links.release_notes_url("1.0.0", True)):
        assert safe_https_url(u) == u        # the app's link guard accepts them all


def test_system_info_has_no_profile_or_account_data(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text(SAMPLE)
    (tmp_path / "credentials").write_text(CREDS)
    store = ProfileStore(cfg, tmp_path / "credentials")
    store.load()
    text = about.system_info(store.config_file, "2.31.0 (/usr/local/bin/aws)")
    assert str(cfg) in text and "AWS CLI: 2.31.0" in text
    for line in ("termSwitch:", "OS:", "Python:", "Qt:", "PySide6:", "Terminal backend:"):
        assert line in text
    forbidden = list(store.profiles) + ["111111111111", "222222222222", "333333333333",
                                        "arn:", "AKIA", "secret", "acme",
                                        "awsapps.com"]
    for word in forbidden:
        assert word not in text, word


@pytest.fixture
def dialog(qapp, tmp_path):
    opened, licenses = [], []
    dlg = about.AboutDialog(tmp_path / "config", open_url=opened.append,
                            show_licenses=lambda: licenses.append(True),
                            check_online=False, run_lookup=False)
    dlg.opened, dlg.licenses = opened, licenses
    yield dlg
    dlg.close()


def test_dialog_links(dialog, monkeypatch):
    b = dialog.link_buttons
    b["Website"].click()
    b["GitHub"].click()
    b["Release notes"].click()
    b["Report an issue"].click()
    b["Licenses"].click()
    assert dialog.opened[:2] == [links.WEBSITE, links.REPO_URL]
    assert dialog.opened[2].startswith(links.RELEASES_URL)
    assert dialog.opened[3].startswith(links.NEW_ISSUE_URL)
    assert dialog.licenses == [True]
    assert dialog.width() == about.WIDTH


def test_copy_system_information(dialog):
    from PySide6.QtGui import QGuiApplication
    assert dialog.info.isHidden()                  # collapsed by default
    dialog.info_toggle.setChecked(True)
    assert not dialog.info.isHidden()
    dialog._on_aws_cli("2.31.0 (/usr/local/bin/aws)")
    dialog.copy_button.click()
    copied = QGuiApplication.clipboard().text()
    assert copied == dialog.info_text() and "AWS CLI: 2.31.0" in copied


def test_update_status(dialog, monkeypatch):
    monkeypatch.setattr(about, "IS_RELEASE", True)
    monkeypatch.setattr(about, "__version__", "0.6.4")
    dialog.show_update("0.7.0")
    assert not dialog.update_status.isHidden()
    assert "0.7.0 is available" in dialog.update_status.text()
    assert links.LATEST_RELEASE_URL in dialog.update_status.text()
    dialog.show_update("0.6.4")
    assert dialog.update_status.text() == "Up to date"
    dialog.show_update(None)                       # offline or no release
    assert dialog.update_status.isHidden()
    monkeypatch.setattr(about, "IS_RELEASE", False)
    dialog.show_update("9.9.9")                    # source runs never show it
    assert dialog.update_status.isHidden()


def test_lookup_survives_offline(qapp, monkeypatch):
    from termswitch import deps

    def offline():
        raise OSError("network is unreachable")
    monkeypatch.setattr(deps, "latest_termswitch", offline)
    monkeypatch.setattr(deps, "check_aws_cli",
                        lambda *_: deps.Check("AWS CLI", "ok", "2.31.0 (/x/aws)", ""))
    monkeypatch.setattr(about, "IS_RELEASE", True)
    got = {}
    signals = about._Signals()
    signals.aws_cli.connect(lambda s: got.setdefault("aws", s))
    signals.update.connect(lambda v: got.setdefault("update", v))
    about._Lookup(signals, online=True).run()
    assert got == {"aws": "2.31.0 (/x/aws)", "update": None}


def test_lookup_skips_network_when_disabled(qapp, monkeypatch):
    from termswitch import deps
    calls = []
    monkeypatch.setattr(deps, "latest_termswitch", lambda: calls.append(1) or "9.9.9")
    monkeypatch.setattr(deps, "check_aws_cli",
                        lambda *_: deps.Check("AWS CLI", "ok", "not found", ""))
    monkeypatch.setattr(about, "IS_RELEASE", True)
    about._Lookup(about._Signals(), online=False).run()
    assert calls == []


def test_release_workflow_stamps_commit():
    wf = (Path(__file__).resolve().parent.parent / ".github/workflows/release.yml").read_text()
    assert '__commit__ = \\"${GITHUB_SHA::7}\\"' in wf
