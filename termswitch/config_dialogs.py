"""Whole-file tools for the AWS config: the welcome panel shown when there are no
profiles yet, the in-app config editor, and the backups list."""

from __future__ import annotations

import re
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QSyntaxHighlighter, QTextCharFormat,
                           QTextCursor)
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from . import theme
from .config_store import ProfileStore

SECRETS_REFUSED = ("This config file contains access keys or session tokens. termSwitch "
                   "doesn't load secrets into its own windows. Use File > Open in external "
                   "editor instead, and consider moving the keys to ~/.aws/credentials or "
                   "switching to AWS SSO.")


# --------------------------------------------------------------------------
# Welcome panel
# --------------------------------------------------------------------------

class WelcomePanel(QWidget):
    """Shown in place of the profile tree when the config has no profiles."""

    setup_sso = Signal()
    add_profile = Signal()
    choose_file = Signal()
    check_dependencies = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = QLabel("<h3>Welcome to termSwitch</h3>")
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.cli_note = QLabel()
        self.cli_note.setWordWrap(True)
        theme.set_role(self.cli_note, "warning")
        self.btn_sso = QPushButton("Set up AWS SSO...")
        self.btn_sso.setToolTip("Recommended: sign in through your organization's access "
                                "portal (IAM Identity Center)")
        self.btn_sso.clicked.connect(self.setup_sso)
        self.btn_profile = QPushButton("Add a profile...")
        self.btn_profile.clicked.connect(self.add_profile)
        self.btn_file = QPushButton("Use an existing config file...")
        self.btn_file.clicked.connect(self.choose_file)
        self.btn_deps = QPushButton("Check dependencies")
        self.btn_deps.clicked.connect(self.check_dependencies)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.addWidget(self.title)
        lay.addWidget(self.body)
        lay.addSpacing(8)
        for b in (self.btn_sso, self.btn_profile, self.btn_file):
            lay.addWidget(b)
        lay.addSpacing(8)
        lay.addWidget(self.cli_note)
        lay.addWidget(self.btn_deps)
        lay.addStretch(1)

    def update_state(self, config_file: Path, has_cli: bool):
        import html
        exists = config_file.exists()
        where = html.escape(str(config_file))
        if exists:
            text = f"<p>{where} has no profiles yet.</p>"
        else:
            text = f"<p>There is no AWS config file yet. termSwitch will create {where}.</p>"
        text += ("<p>Pick how you sign in to AWS. If your organization uses IAM Identity "
                 "Center (AWS SSO), start with <b>Set up AWS SSO</b>.</p>")
        self.body.setText(text)
        self.cli_note.setText("" if has_cli else
                              "The AWS CLI v2 isn't installed. Signing in and opening "
                              "terminals need it.")
        self.cli_note.setVisible(not has_cli)
        self.btn_deps.setVisible(not has_cli)


# --------------------------------------------------------------------------
# Config editor
# --------------------------------------------------------------------------

class IniHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)

        def fmt(color: str, bold: bool = False) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            return f

        c = theme.colors()
        self.section = fmt(c.section, bold=True)
        self.comment = fmt(c.hint)
        self.key = fmt(c.key)
        self.error = QTextCharFormat()
        self.error.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
        self.error.setUnderlineColor(QColor(c.error))
        self.error_line: int | None = None    # 1-based

    def highlightBlock(self, text: str):
        stripped = text.lstrip()
        if stripped.startswith(("#", ";")):
            self.setFormat(0, len(text), self.comment)
        elif stripped.startswith("["):
            self.setFormat(0, len(text), self.section)
        else:
            m = re.match(r"^(\s*)([^=:\s][^=:]*?)\s*[=:]", text)
            if m:
                self.setFormat(len(m.group(1)), len(m.group(2)), self.key)
        if self.error_line is not None and self.currentBlock().blockNumber() + 1 == self.error_line:
            self.setFormat(0, max(len(text), 1), self.error)


class ConfigEditorDialog(QDialog):
    """Edit the config file as text. Saving parses it first and refuses invalid
    text, showing the line with the problem."""

    def __init__(self, store: ProfileStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle(f"Edit {store.config_file}")
        self.resize(820, 640)
        self._loaded = store.read_raw()
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setPlainText(self._loaded)
        self.highlighter = IniHighlighter(self.editor.document())
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.editor.textChanged.connect(self._check)
        self.editor.cursorPositionChanged.connect(self._show_position)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        self.btn_save = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        self.position = QLabel()
        theme.set_role(self.position, "hint")
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.position)
        lay = QVBoxLayout(self)
        lay.addWidget(self.editor, 1)
        lay.addLayout(bottom)
        lay.addWidget(buttons)
        self._check()

    def _show_position(self):
        c = self.editor.textCursor()
        self.position.setText(f"Line {c.blockNumber() + 1}, column {c.positionInBlock() + 1}")

    def _check(self) -> bool:
        c = theme.colors()
        text = self.editor.toPlainText()
        problem = self.store.parse_problem(text if text.endswith("\n") else text + "\n")
        line = problem[0] if problem else None
        if line != self.highlighter.error_line:
            self.highlighter.error_line = line
            self.highlighter.rehighlight()
        if problem:
            where = f"Line {line}: " if line else ""
            self.status.setText(f"<span style='color:{c.error}'>{where}{problem[1]}</span>")
        elif self.store.contains_secrets(text):
            self.status.setText(f"<span style='color:{c.error}'>termSwitch doesn't save "
                                "access keys or session tokens in the config. Use "
                                "~/.aws/credentials or AWS SSO.</span>")
        else:
            self.status.setText(f"<span style='color:{c.hint}'>Valid. Saving makes a backup "
                                "and can be undone.</span>")
        ok = problem is None and not self.store.contains_secrets(text)
        self.btn_save.setEnabled(ok)
        return ok

    def go_to_line(self, line: int):
        block = self.editor.document().findBlockByNumber(max(line - 1, 0))
        cursor = QTextCursor(block)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def text(self) -> str:
        return self.editor.toPlainText()

    def _save(self):
        if not self._check():
            if self.highlighter.error_line:
                self.go_to_line(self.highlighter.error_line)
            return
        if self.store.read_raw() != self._loaded:
            answer = QMessageBox.warning(
                self, "Config changed",
                "The config file changed outside termSwitch while you were editing. "
                "Save anyway and replace those changes?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Save:
                return
        self.accept()


# --------------------------------------------------------------------------
# Backups
# --------------------------------------------------------------------------

class BackupsDialog(QDialog):
    """termSwitch's backups of the config, newest first, with a preview."""

    def __init__(self, store: ProfileStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.selected: Path | None = None
        self.setWindowTitle("Config backups")
        self.resize(820, 520)
        self.list = QListWidget()
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(self.preview)
        split.setSizes([300, 520])
        note = QLabel("termSwitch saves a backup before the first change of each session, "
                      "before every restore, and when the config is reset. Restoring backs "
                      "up the current file first, and can be undone.")
        note.setWordWrap(True)
        theme.set_role(note, "hint")
        buttons = QDialogButtonBox()
        self.btn_restore = buttons.addButton("Restore...", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self._restore)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addWidget(split, 1)
        lay.addWidget(buttons)
        self.list.currentItemChanged.connect(self._show)
        for path in store.backups():
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
            kind = " (reset)" if "-reset-" in path.name else ""
            item = QListWidgetItem(f"{stamp}{kind}")
            item.setToolTip(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.preview.setPlainText("No backups yet.")
        self.btn_restore.setEnabled(self.list.count() > 0)

    def _show(self, item, _prev=None):
        if item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            self.preview.setPlainText(str(exc))
            return
        if self.store.contains_secrets(text):
            self.preview.setPlainText("Preview hidden: this backup contains access keys.")
        else:
            self.preview.setPlainText(text)

    def _restore(self):
        item = self.list.currentItem()
        if item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        answer = QMessageBox.question(
            self, "Restore backup",
            f"Replace {self.store.config_file} with the backup from {item.text()}?\n\n"
            "The current file is backed up first.")
        if answer == QMessageBox.StandardButton.Yes:
            self.selected = path
            self.accept()

