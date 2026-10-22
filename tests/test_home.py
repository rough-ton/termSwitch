"""The home view in the empty terminal area, and the footer at the bottom left."""

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMessageBox

from termswitch import __version__
from termswitch import ssh_sessions as ss
from termswitch.config_store import ProfileStore
from termswitch.home_view import AppStatusBar, Footer, HomeItem, HomeView
from termswitch.links import WEBSITE

from conftest import pump
from test_profile_ui import close_window
from test_store_and_launcher import CREDS, SAMPLE


def items():
    return [HomeItem("aws:dev", "dev", QIcon()), HomeItem("aws:prod-admin", "prod-admin", QIcon()),
            HomeItem("ssh:s1", "web-01", QIcon()), HomeItem("ssh:s2", "dev", QIcon())]


# ---- footer and status bar --------------------------------------------------

def test_footer_shows_name_version_and_support_link(qapp):
    f = Footer()
    text = f.text()
    assert "termSwitch" in text and f"v{__version__}" in text
    assert "Please support termSwitch development" in text
    assert f"href='{WEBSITE}'" in text and "about.mroughton.com</a>" in text
    assert f.openExternalLinks()


def test_messages_never_hide_the_footer(qapp):
    bar = AppStatusBar()
    bar.resize(900, 30)
    bar.show()
    bar.showMessage("Saved dev.", 50)
    pump(qapp, 0.05)
    assert bar.footer.isVisible() and bar.currentMessage() == "Saved dev."
    pump(qapp, 0.3, until=lambda: bar.currentMessage() == "")
    assert bar.currentMessage() == "" and bar.footer.isVisible()
    bar.showMessage("Stays")
    pump(qapp, 0.1)
    assert bar.currentMessage() == "Stays"
    bar.clearMessage()
    assert bar.currentMessage() == ""
    bar.close()


# ---- home view --------------------------------------------------------------

def test_recent_grid_opens_items_and_skips_unknown_ones(qapp):
    home = HomeView()
    home.set_items(items())
    opened = []
    home.open_requested.connect(opened.append)
    home.set_recent(["ssh:s1", "aws:gone", "aws:dev"])
    buttons = home.recent_buttons()
    assert [b.text() for b in buttons] == ["web-01", "dev"]
    assert home.recent_empty.isHidden()
    buttons[0].click()
    assert opened == ["ssh:s1"]
    home.set_recent([])
    assert home.recent_buttons() == [] and not home.recent_empty.isHidden()


def test_recent_grid_shows_at_most_nine(qapp):
    home = HomeView()
    home.set_items([HomeItem(f"aws:p{i}", f"p{i}", QIcon()) for i in range(12)])
    home.set_recent([f"aws:p{i}" for i in range(12)])
    assert len(home.recent_buttons()) == 9


def test_search_opens_the_match(qapp):
    home = HomeView()
    home.set_items(items())
    opened = []
    home.open_requested.connect(opened.append)
    # A profile and an SSH session share "dev": the session is labeled.
    assert "dev (SSH)" in home._names.stringList() and "dev" in home._names.stringList()
    for text in ("PROD-ADMIN", "dev (SSH)", "web", "  dev  "):
        home.search.setText(text)
        home.search.returnPressed.emit()
    assert opened == ["aws:prod-admin", "ssh:s2", "ssh:s1", "aws:dev"]
    assert home.search.text() == ""
    home.search.setText("e")                  # matches several: nothing opens
    home.search.returnPressed.emit()
    home.search.setText("nothing")
    home.search.returnPressed.emit()
    assert len(opened) == 4 and home.search.text() == "nothing"


def test_reopen_button_shows_the_count(qapp):
    home = HomeView()
    home.set_reopen_count(0)
    assert not home.reopen_btn.isEnabled()
    home.set_reopen_count(3)
    assert home.reopen_btn.isEnabled() and "(3)" in home.reopen_btn.text()


# ---- main window ------------------------------------------------------------

@pytest.fixture
def window(qapp, tmp_path: Path, monkeypatch):
    from termswitch import ui
    cfg = tmp_path / "config"
    cfg.write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "credentials").write_text(CREDS, encoding="utf-8")
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("check_on_startup", False)
    settings.setValue("confirm_prod", False)
    monkeypatch.setattr(ui.MainWindow, "run_dependency_check", lambda *a, **k: None)
    w = ui.MainWindow(store=ProfileStore(cfg, tmp_path / "credentials"), settings=settings)
    w.ssh_store = ss.SessionStore(tmp_path / "ssh.json", tmp_path / "none")
    w.ssh_store.load()
    w.ssh_store.save(ss.SshSession(name="web-01", host="web01.example.com", user="deploy"))
    w.rebuild()
    # Record what would open instead of starting real terminals.
    opened = []
    monkeypatch.setattr(w.terminals, "open_profile",
                        lambda prof, opts, title=None: opened.append(f"aws:{prof.name}"))
    monkeypatch.setattr(ui, "find_ssh", lambda: "/usr/bin/ssh")
    monkeypatch.setattr(w.terminals, "open_ssh",
                        lambda session, *a, **k: opened.append(f"ssh:{session.id}"))
    w.opened = opened
    yield w
    w.terminals.close_all()
    close_window(w, qapp)


def test_home_is_the_empty_terminal_area(window):
    assert window.terminals.currentWidget() is window.home
    assert isinstance(window.statusBar(), AppStatusBar)
    names = window.home._names.stringList()
    assert "acme-dev-admin" in names and "web-01" in names


def test_opening_items_fills_recent(window):
    web = next(s for s in window.ssh_store.all() if s.name == "web-01")
    window.open_profiles([window.store.profiles["acme-dev-admin"]], external=False)
    window.connect_ssh(web)
    assert window.opened == ["aws:acme-dev-admin", f"ssh:{web.id}"]
    assert [b.text() for b in window.home.recent_buttons()] == ["web-01", "acme-dev-admin"]
    # Opening again moves it to the front instead of adding it twice.
    window.home.recent_buttons()[1].click()
    assert [b.text() for b in window.home.recent_buttons()] == ["acme-dev-admin", "web-01"]
    assert window.opened[-1] == "aws:acme-dev-admin"


def test_rename_keeps_the_recent_entry(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    window.open_profiles([window.store.profiles["acme-dev-admin"]], external=False)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("dev2", True))

    def answer(box):
        for b in box.buttons():
            if b.text().replace("&", "") in ("Update them", "Rename"):
                b.click()
                return 0
        return 0
    monkeypatch.setattr(QMessageBox, "exec", answer)
    window.rename_profile(window.store.profiles["acme-dev-admin"])
    assert "dev2" in window.store.profiles
    assert [b.text() for b in window.home.recent_buttons()] == ["dev2"]


def test_quit_remembers_tabs_and_reopen_opens_them(window, qapp):
    from termswitch.terminal_tabs import TabInfo
    from PySide6.QtWidgets import QPlainTextEdit
    web = next(s for s in window.ssh_store.all() if s.name == "web-01")
    # Stand-in tabs: an AWS profile, an SSH session and a one-off setup tab.
    for info in (dict(profile=window.store.profiles["acme-dev-admin"]),
                 dict(profile=None, ssh_session=web), dict(profile=None)):
        fake = QPlainTextEdit()
        fake.is_alive = lambda: False
        fake.terminate = lambda: None
        window.terminals.tabs.addTab(fake, "tab")
        window.terminals._infos[id(fake)] = TabInfo(widget=fake, options=None, title="tab",
                                                     **info)
    assert window._open_tab_keys() == ["aws:acme-dev-admin", f"ssh:{web.id}"]
    window.close()
    assert window.settings.value(window.LAST_TABS_KEY, type=list) == [
        "aws:acme-dev-admin", f"ssh:{web.id}"]
    window._refresh_home()
    assert "(2)" in window.home.reopen_btn.text()
    window.opened.clear()
    window.home.reopen_btn.click()
    assert window.opened == ["aws:acme-dev-admin", f"ssh:{web.id}"]


def test_reopen_skips_items_that_are_gone(window):
    window.settings.setValue(window.LAST_TABS_KEY, ["aws:deleted", "ssh:missing"])
    window.reopen_previous_tabs()
    assert window.opened == []
    assert "None of the previous tabs" in window.statusBar().currentMessage()
