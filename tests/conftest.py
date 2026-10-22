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


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    # Tear Qt down in order. Widgets that outlive the QApplication crash the
    # interpreter at exit (seen as a segfault on Linux and exit code 1 on Windows).
    QThreadPool.globalInstance().waitForDone(5000)
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, 0)


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
