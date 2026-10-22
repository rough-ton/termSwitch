import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True, scope="session")
def _isolated_settings(tmp_path_factory):
    """Keep QSettings() in tests away from the real user settings."""
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      str(tmp_path_factory.mktemp("settings")))


class UnexpectedDialog(AssertionError):
    pass


@pytest.fixture(autouse=True)
def _no_unexpected_dialogs(monkeypatch):
    """Fail fast if code under test opens a modal dialog the test didn't patch.

    A real modal dialog waits for a click that never comes, so CI hangs until
    the job times out with no hint of what was asked. Tests that expect a
    dialog patch it themselves, which overrides these stand-ins.
    """
    from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

    def refuse(kind):
        def _raise(*args, **kwargs):
            texts = [a for a in args if isinstance(a, str)]
            if args and isinstance(args[0], QMessageBox):
                texts = [args[0].windowTitle(), args[0].text()]
            elif args and isinstance(args[0], QDialog):
                texts = [args[0].windowTitle()]
            raise UnexpectedDialog(f"unexpected {kind}: {' | '.join(t for t in texts if t)}")
        return _raise

    for name in ("critical", "warning", "information", "question", "about"):
        monkeypatch.setattr(QMessageBox, name, refuse(f"QMessageBox.{name}"))
    for name in ("getText", "getItem", "getInt", "getMultiLineText"):
        monkeypatch.setattr(QInputDialog, name, refuse(f"QInputDialog.{name}"))
    for name in ("getOpenFileName", "getSaveFileName", "getExistingDirectory"):
        monkeypatch.setattr(QFileDialog, name, refuse(f"QFileDialog.{name}"))
    monkeypatch.setattr(QMessageBox, "exec", refuse("QMessageBox"))
    monkeypatch.setattr(QDialog, "exec", refuse("dialog"))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    # Tear Qt down in order. Widgets that outlive the QApplication crash the
    # interpreter at exit (seen as a segfault on Linux and exit code 1 on Windows).
    QThreadPool.globalInstance().clear()
    QThreadPool.globalInstance().waitForDone(30000)
    icon_filter = getattr(app, "_termswitch_icon_filter", None)
    if icon_filter is not None:
        app.removeEventFilter(icon_filter)
    import shiboken6
    from PySide6.QtCore import QEvent
    # Delete parentless windows one at a time; their child windows (dialogs,
    # menus) go with them. Deleting everything in one batch crashed.
    for widget in QApplication.topLevelWidgets():
        if not shiboken6.isValid(widget) or widget.parent() is not None:
            continue
        widget.close()
        widget.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def pump(app, seconds: float, until=None):
    import time
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if until and until():
            return True
        time.sleep(0.01)
    return bool(until and until())


def pytest_runtest_logreport(report):
    """In GitHub Actions, surface each failure as an annotation on the run."""
    if not os.environ.get("GITHUB_ACTIONS") or not report.failed:
        return
    path, line, _ = report.location
    detail = str(report.longrepr).strip().splitlines()
    tail = " | ".join(detail[-6:])[:900]
    msg = f"{report.nodeid} ({report.when}): {tail}"
    msg = msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"\n::error file=tests/{os.path.basename(path)},line={(line or 0) + 1}::{msg}")
