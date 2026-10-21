"""Dependency check dialog and background runner."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QGuiApplication
import html

from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSplitter, QStyle, QTextBrowser, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout)

from .deps import ERROR, INFO, OK, WARN, Check, run_checks, summary, worst

LEVEL_TEXT = {OK: "OK", INFO: "Info", WARN: "Warning", ERROR: "Error"}


class _Signals(QObject):
    done = Signal(list)


class _Job(QRunnable):
    def __init__(self, online: bool, signals: _Signals):
        super().__init__()
        self.online, self.signals = online, signals

    def run(self):
        try:
            checks = run_checks(online=self.online)
        except Exception as exc:  # never let a check crash the app
            checks = [Check("Dependency check", ERROR, "failed", f"The check itself failed: {exc}")]
        self.signals.done.emit(checks)


class DependencyRunner(QObject):
    finished = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)
        self.running = False
        self.results: list[Check] = []

    def start(self, online: bool) -> bool:
        if self.running:
            return False
        self.running = True
        QThreadPool.globalInstance().start(_Job(online, self._signals))
        return True

    def _on_done(self, checks):
        self.running = False
        self.results = checks
        self.finished.emit(checks)


class DependencyDialog(QDialog):
    recheck_requested = Signal()

    def __init__(self, checks: list[Check], show_warnings: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dependency check")
        self.resize(880, 440)
        style = self.style()
        self._icons = {
            OK: style.standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton),
            INFO: style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation),
            WARN: style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning),
            ERROR: style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical),
        }

        self.header = QLabel()
        self.header.setWordWrap(True)
        self.table = QTreeWidget()
        self.table.setColumnCount(4)
        self.table.setHeaderLabels(["Component", "Found", "Details", "Fix"])
        self.table.setRootIsDecorated(False)
        self.table.setWordWrap(True)
        self.table.setAlternatingRowColors(True)
        h = self.table.header()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(3, 230)
        self.table.currentItemChanged.connect(lambda cur, _prev: self._show(cur))

        self.detail = QTextBrowser()
        self.detail.setOpenLinks(False)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([300, 120])

        self.show_warnings = QCheckBox("Show this window at startup for warnings (errors always show)")
        self.show_warnings.setChecked(show_warnings)
        copy = QPushButton("Copy fix commands")
        copy.clicked.connect(self._copy_fixes)
        self.recheck = QPushButton("Check again")
        self.recheck.clicked.connect(self._on_recheck)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addWidget(self.show_warnings)
        buttons.addStretch(1)
        buttons.addWidget(copy)
        buttons.addWidget(self.recheck)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(split)
        layout.addLayout(buttons)
        self.set_checks(checks)

    def set_checks(self, checks: list[Check]):
        self.checks = checks
        self.recheck.setEnabled(True)
        self.recheck.setText("Check again")
        self.table.clear()
        order = {ERROR: 0, WARN: 1, INFO: 2, OK: 3}
        for c in sorted(checks, key=lambda c: order[c.level]):
            it = QTreeWidgetItem([c.name, c.found, c.message, c.fix if c.level != OK else ""])
            it.setIcon(0, self._icons[c.level])
            it.setToolTip(0, LEVEL_TEXT[c.level])
            for col in range(4):
                it.setToolTip(col, it.text(col) if col else LEVEL_TEXT[c.level])
            it.setData(0, Qt.ItemDataRole.UserRole, c)
            self.table.addTopLevelItem(it)
        if self.table.topLevelItemCount():
            self.table.setCurrentItem(self.table.topLevelItem(0))
        level = worst(checks)
        text = summary(checks)
        if level == ERROR:
            text = (f"<b>{text}.</b> Some features will not work until the errors are fixed. "
                    "Grouping and the config editor still work.")
        elif level == WARN:
            text = f"<b>{text}.</b> Everything works, but review the items below."
        else:
            text = f"<b>{text}.</b>"
        self.header.setText(text)

    def _show(self, item):
        if item is None:
            self.detail.clear()
            return
        c: Check = item.data(0, Qt.ItemDataRole.UserRole)
        parts = [f"<b>{html.escape(c.name)}</b> ({LEVEL_TEXT[c.level]})",
                 f"<p>{html.escape(c.message)}</p>",
                 f"<p>Found: <code>{html.escape(c.found)}</code></p>"]
        if c.fix and c.level != OK:
            parts.append(f"<p>Fix: <code>{html.escape(c.fix)}</code></p>")
        self.detail.setHtml("".join(parts))

    def _on_recheck(self):
        self.recheck.setEnabled(False)
        self.recheck.setText("Checking...")
        self.recheck_requested.emit()

    def _copy_fixes(self):
        fixes = [c.fix for c in self.checks if c.fix and c.level in (WARN, ERROR, INFO)]
        QGuiApplication.clipboard().setText("\n".join(dict.fromkeys(fixes)))
