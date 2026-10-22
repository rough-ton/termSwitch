"""Help > About: what termSwitch is, links, update status and system information."""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QIcon, QPalette
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QToolButton, QVBoxLayout)

from . import BASE_VERSION, IS_RELEASE, __commit__, __version__, links

TAGLINE = "Flip the switch, not the profile."
DESCRIPTION = ("Grouped AWS profiles that open authenticated terminals, with an AWS CLI "
               "Commands pane for building and running commands.")
COPYRIGHT = "© 2026 Mike Roughton. MIT license."
ICON_CREDIT = "Icons: Material Icon Theme (MIT)."
WIDTH = 520


def version_text() -> str:
    """'0.6.4 (1a2b3c4)' for release builds, '0.6.0 (source)' for source runs."""
    return f"{__version__} ({__commit__})" if IS_RELEASE else f"{BASE_VERSION} (source)"


def os_text() -> str:
    if sys.platform == "darwin":
        name = f"macOS {platform.mac_ver()[0] or platform.release()}"
    elif sys.platform.startswith("win"):
        name = f"Windows {platform.release()} ({platform.version()})"
    else:
        name = f"{platform.system()} {platform.release()}"
    return f"{name}, {platform.machine() or 'unknown architecture'}"


def system_info(config_file: Path | str, aws_cli: str) -> str:
    """Details for bug reports. Only versions and paths: never profile names,
    account IDs, ARNs or credentials."""
    from PySide6 import QtCore, __version__ as pyside_version

    backend = "ConPTY" if sys.platform.startswith("win") else "pty"
    lines = [
        f"termSwitch: {version_text()}",
        f"OS: {os_text()}",
        f"Python: {platform.python_version()} ({platform.python_implementation()})",
        f"Qt: {QtCore.qVersion()}",
        f"PySide6: {pyside_version}",
        f"AWS CLI: {aws_cli}",
        f"Terminal backend: {backend}",
        f"Config file: {config_file}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Background lookups: the AWS CLI version, and optionally the latest release
# --------------------------------------------------------------------------

class _Signals(QObject):
    aws_cli = Signal(str)
    update = Signal(object)     # latest version string, or None when unknown


class _Lookup(QRunnable):
    def __init__(self, signals: _Signals, online: bool):
        super().__init__()
        self.signals, self.online = signals, online

    def run(self):
        from .deps import check_aws_cli, latest_termswitch
        try:
            check = check_aws_cli(None, False)
            self.signals.aws_cli.emit(check.found)
        except Exception as exc:   # never break the dialog
            self.signals.aws_cli.emit(f"unknown ({exc})")
        latest = None
        if self.online and IS_RELEASE:
            try:
                latest = latest_termswitch()
            except Exception:
                latest = None      # offline, private repo, rate limit: show nothing
        self.signals.update.emit(latest)


# --------------------------------------------------------------------------
# Dialog
# --------------------------------------------------------------------------

class AboutDialog(QDialog):
    def __init__(self, config_file: Path | str, open_url: Callable[[str], None],
                 show_licenses: Callable[[], None], check_online: bool = True,
                 icon: QIcon | None = None, parent=None, run_lookup: bool = True):
        super().__init__(parent)
        self.setWindowTitle("About termSwitch")
        self.setFixedWidth(WIDTH)
        self.config_file = config_file
        self._open_url = open_url
        self.aws_cli = "checking..."

        # Header
        icon_label = QLabel()
        if icon is not None and not icon.isNull():
            icon_label.setPixmap(icon.pixmap(96, 96))
        icon_label.setFixedSize(96, 96)
        name = QLabel("termSwitch")
        f = name.font()
        f.setPointSizeF(f.pointSizeF() * 2.0)
        f.setWeight(QFont.Weight.Bold)
        name.setFont(f)
        tagline = QLabel(TAGLINE)
        ft = tagline.font()
        ft.setItalic(True)
        tagline.setFont(ft)
        self.version = QLabel(f"Version {version_text()}")
        self.update_status = QLabel()
        self.update_status.setTextFormat(Qt.TextFormat.RichText)
        self.update_status.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.update_status.linkActivated.connect(self._open_url)
        self.update_status.hide()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        for w in (name, tagline, self.version, self.update_status):
            titles.addWidget(w)
        titles.addStretch(1)
        header = QHBoxLayout()
        header.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        header.addSpacing(12)
        header.addLayout(titles, 1)

        description = QLabel(DESCRIPTION)
        description.setWordWrap(True)

        # Links
        self.link_buttons: dict[str, QPushButton] = {}

        def link(label: str, slot: Callable[[], None], tip: str) -> QPushButton:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.setAutoDefault(False)
            b.clicked.connect(slot)
            self.link_buttons[label] = b
            return b

        row1 = QHBoxLayout()
        row1.addWidget(link("Website", lambda: self._open_url(links.WEBSITE), links.WEBSITE))
        row1.addWidget(link("GitHub", lambda: self._open_url(links.REPO_URL), links.REPO_URL))
        notes = links.release_notes_url(__version__, IS_RELEASE)
        row1.addWidget(link("Release notes", lambda: self._open_url(notes), notes))
        row1.addStretch(1)
        row2 = QHBoxLayout()
        row2.addWidget(link("Report an issue", self._report_issue,
                            "Open a new GitHub issue with your version and OS filled in"))
        row2.addWidget(link("Licenses", show_licenses,
                            "termSwitch's license and the third-party notices"))
        row2.addStretch(1)

        # System information, collapsed
        self.info_toggle = QToolButton()
        self.info_toggle.setText("System information")
        self.info_toggle.setCheckable(True)
        self.info_toggle.setAutoRaise(True)
        self.info_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.info_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.info_toggle.toggled.connect(self._toggle_info)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setAutoDefault(False)
        self.copy_button.setToolTip("Copy the system information for a bug report")
        self.copy_button.clicked.connect(self.copy_info)
        info_row = QHBoxLayout()
        info_row.addWidget(self.info_toggle)
        info_row.addStretch(1)
        info_row.addWidget(self.copy_button)
        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.info.setFixedHeight(150)
        self.info.hide()
        self._refresh_info()

        # Footer
        footer_text = QLabel(f"{COPYRIGHT}<br>{ICON_CREDIT}")
        footer_text.setTextFormat(Qt.TextFormat.RichText)
        footer_text.setForegroundRole(QPalette.ColorRole.PlaceholderText)  # follows the theme
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        footer = QHBoxLayout()
        footer.addWidget(footer_text, 1)
        footer.addWidget(close, 0, Qt.AlignmentFlag.AlignBottom)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.addLayout(header)
        lay.addSpacing(8)
        lay.addWidget(description)
        lay.addSpacing(6)
        lay.addLayout(row1)
        lay.addLayout(row2)
        lay.addSpacing(6)
        lay.addLayout(info_row)
        lay.addWidget(self.info)
        lay.addSpacing(6)
        lay.addLayout(footer)

        # Every label's text can be selected and copied.
        for label in self.findChildren(QLabel):
            if label is not icon_label and label is not self.update_status:
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._signals = _Signals()
        self._signals.aws_cli.connect(self._on_aws_cli)
        self._signals.update.connect(self.show_update)
        if run_lookup:
            QThreadPool.globalInstance().start(_Lookup(self._signals, check_online))

    # ---- content ---------------------------------------------------------

    def info_text(self) -> str:
        return system_info(self.config_file, self.aws_cli)

    def _refresh_info(self):
        self.info.setPlainText(self.info_text())

    def _on_aws_cli(self, text: str):
        self.aws_cli = text
        self._refresh_info()

    def _toggle_info(self, on: bool):
        self.info.setVisible(on)
        self.info_toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self.adjustSize()

    def show_update(self, latest: str | None):
        """Update status for release builds. Hidden when the lookup failed."""
        from .deps import behind
        if not latest or not IS_RELEASE:
            self.update_status.hide()
            return
        if behind(__version__, latest):
            color = self.palette().color(QPalette.ColorRole.Link).name()   # theme-aware
            self.update_status.setText(
                f"termSwitch {latest} is available. "
                f"<a href='{links.LATEST_RELEASE_URL}' style='color:{color}'>Download</a>")
        else:
            self.update_status.setText("Up to date")
        self.update_status.show()

    # ---- actions ---------------------------------------------------------

    def copy_info(self):
        QGuiApplication.clipboard().setText(self.info_text())
        self.copy_button.setText("Copied")

    def _report_issue(self):
        self._open_url(links.new_issue_url(version_text(), os_text()))
