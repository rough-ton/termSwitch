"""Show-Command style parameter form for the Commands pane."""

from __future__ import annotations

import html
import sys

from PySide6.QtCore import QRegularExpression, Qt, QTimer, Signal
from PySide6.QtGui import QDoubleValidator, QFont, QFontDatabase, QRegularExpressionValidator
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from .aws_commands import Command
from .aws_params import Common, CommandSpec, Param, ParamError, render

OUTPUTS = ["", "json", "table", "text", "yaml", "yaml-stream"]
BOOL_CHOICES = ["(not set)", "on", "off"]
PREVIEW_PLACEHOLDER = "Select a command to see its options."


class _Section(QWidget):
    """A titled group that can be collapsed."""

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.toggle.setAutoRaise(True)
        f = self.toggle.font()
        f.setBold(True)
        self.toggle.setFont(f)
        self.body = QWidget()
        self.form = QFormLayout(self.body)
        self.form.setContentsMargins(4, 0, 0, 6)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        # The pane is narrow and option names are long: label above field.
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.form.setVerticalSpacing(3)
        self.body.setVisible(expanded)
        self.toggle.toggled.connect(self._toggled)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.toggle)
        lay.addWidget(self.body)

    def _toggled(self, on: bool):
        self.body.setVisible(on)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)


class ParameterForm(QWidget):
    insert_requested = Signal(str, bool, object)     # command line, run, Command
    copy_requested = Signal(str)
    docs_requested = Signal(object)                  # Command
    favorite_toggled = Signal(object)                # Command

    def __init__(self, parent=None):
        super().__init__(parent)
        self.spec: CommandSpec | None = None
        self.shell = "posix"
        self.region_hint = ""
        self.terminal_available = False
        self._fields: dict[str, tuple[Param, QWidget]] = {}
        self._common: dict[str, QWidget] = {}
        self._timer = QTimer(self, singleShot=True, interval=120)
        self._timer.timeout.connect(self._update_preview)

        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        # Star in the header toggles the command as a favorite.
        self.star = QToolButton()
        self.star.setAutoRaise(True)
        self.star.setCheckable(True)
        self.star.clicked.connect(
            lambda: self.spec and self.favorite_toggled.emit(self.spec.command))
        self.set_favorite(False)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(self.title, 1)
        title_row.addWidget(self.star, 0, Qt.AlignmentFlag.AlignTop)

        self.fields_box = QWidget()
        self.fields_layout = QVBoxLayout(self.fields_box)
        self.fields_layout.setContentsMargins(0, 0, 4, 0)
        self.fields_layout.setSpacing(2)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.fields_box)

        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono)
        self.preview.setMaximumHeight(72)
        self.preview.setPlaceholderText(PREVIEW_PLACEHOLDER)
        self.preview.setToolTip("Exactly what Insert, Run and Copy will use")
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #d93025;")
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #8a8f98;")

        self.btn_run = QPushButton("Run")
        self.btn_run.setToolTip("Type the command in the active tab and press Enter")
        self.btn_run.clicked.connect(lambda: self._emit(True))
        self.btn_insert = QPushButton("Insert")
        self.btn_insert.setToolTip("Type the command at the prompt of the active tab "
                                   "(does not press Enter)")
        self.btn_insert.clicked.connect(lambda: self._emit(False))
        self.btn_copy = QPushButton("Copy")
        self.btn_copy.clicked.connect(self._copy)
        self.btn_docs = QPushButton("Docs")
        self.btn_docs.setToolTip("Open the AWS CLI reference page for this command")
        self.btn_docs.clicked.connect(lambda: self.spec and self.docs_requested.emit(self.spec.command))
        actions = QHBoxLayout()
        for b in (self.btn_run, self.btn_insert, self.btn_copy):
            actions.addWidget(b)
        actions.addStretch(1)
        actions.addWidget(self.btn_docs)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 0)
        lay.setSpacing(4)
        lay.addLayout(title_row)
        lay.addWidget(self.scroll, 1)
        lay.addWidget(self.note)
        lay.addWidget(QLabel("Command:"))
        lay.addWidget(self.preview)
        lay.addWidget(self.error)
        lay.addLayout(actions)
        self.set_spec(None)

    # ---- context ---------------------------------------------------------

    def set_favorite(self, on: bool):
        """Show whether the current command is a favorite."""
        from .icons import star_icon

        outline = self.palette().color(self.palette().ColorRole.WindowText).name()
        self.star.setChecked(on)
        self.star.setIcon(star_icon(on, outline))
        tip = "Remove from favorites" if on else "Add to favorites"
        self.star.setToolTip(f"{tip} (Ctrl+D)" if sys.platform != "darwin" else f"{tip} (Cmd+D)")

    def set_target(self, shell: str, region_hint: str, terminal_available: bool):
        self.shell = shell
        self.region_hint = region_hint
        self.terminal_available = terminal_available
        region = self._common.get("region")
        if isinstance(region, QLineEdit):
            region.setPlaceholderText(self._region_placeholder())
        self._update_preview()

    def _region_placeholder(self) -> str:
        return f"{self.region_hint} (from profile)" if self.region_hint else "profile default"

    # ---- building the form ----------------------------------------------

    def set_spec(self, spec: CommandSpec | None):
        self.spec = spec
        self._fields.clear()
        self._common.clear()
        while self.fields_layout.count():
            item = self.fields_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.star.setVisible(spec is not None)
        if spec is None:
            self.title.setText("<span style='color:#8a8f98'>Select a command. Double-click "
                               "inserts it at the prompt.</span>")
            self.note.clear()
            self.note.hide()
            self._update_preview()
            return
        cmd = spec.command
        doc = html.escape(cmd.doc or "No description available.")
        self.title.setText(f"<b><code>{html.escape(cmd.cli)}</code></b><br>{doc}")
        self.note.setText(spec.note)
        self.note.setVisible(bool(spec.note))

        required = [p for p in spec.params if p.required]
        optional = [p for p in spec.params if not p.required]
        if required or spec.outfile:
            sec = _Section(f"Required ({len(required) + int(spec.outfile)})")
            for p in required:
                self._add_field(sec.form, p)
            if spec.outfile:
                w = self._line("outfile", "path of the file to write the result to")
                sec.form.addRow(self._label("outfile *", "The CLI writes the response body "
                                                         "to this file."), w)
            self.fields_layout.addWidget(sec)
        if optional:
            sec = _Section(f"Optional ({len(optional)})", expanded=len(optional) <= 8)
            if len(optional) > 12:
                find = QLineEdit()
                find.setPlaceholderText("Filter options...")
                find.setClearButtonEnabled(True)
                sec.form.addRow(find)
                rows = []
                for p in optional:
                    rows.append((p, self._add_field(sec.form, p)))
                find.textChanged.connect(lambda t, r=rows, f=sec.form: self._filter_rows(f, r, t))
            else:
                for p in optional:
                    self._add_field(sec.form, p)
            self.fields_layout.addWidget(sec)

        common = _Section("Common", expanded=False)
        self._add_common(common.form, spec)
        self.fields_layout.addWidget(common)
        self.fields_layout.addStretch(1)
        self._update_preview()

    @staticmethod
    def _filter_rows(form: QFormLayout, rows, text: str):
        needle = text.strip().lower()
        for p, row in rows:
            show = not needle or needle in p.name or needle in p.doc.lower()
            form.setRowVisible(row, show)

    def _label(self, text: str, tip: str) -> QLabel:
        lab = QLabel(text)
        lab.setToolTip(tip)
        f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        f.setPointSize(max(self.font().pointSize(), 9))
        lab.setFont(f)
        return lab

    def _line(self, key: str, placeholder: str = "") -> QLineEdit:
        w = QLineEdit()
        w.setPlaceholderText(placeholder)
        w.textChanged.connect(lambda _t: self._timer.start())
        self._common[key] = w
        return w

    def _add_field(self, form: QFormLayout, p: Param) -> int:
        tip = p.doc + (" (deprecated)" if p.deprecated else "")
        label = self._label(p.flag + (" *" if p.required else ""), tip or p.flag)
        if p.kind == "enum":
            w = QComboBox()
            w.addItems([""] + p.enum)
            w.currentIndexChanged.connect(lambda _i: self._timer.start())
        elif p.kind == "boolean":
            w = QComboBox()
            w.addItems([BOOL_CHOICES[0], p.flag, p.negative_flag])
            w.currentIndexChanged.connect(lambda _i: self._timer.start())
        elif p.kind == "json":
            w = QPlainTextEdit()
            w.setPlaceholderText("JSON or shorthand syntax")
            w.setMaximumHeight(80)
            w.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
            w.textChanged.connect(self._timer.start)
            if p.skeleton and p.skeleton not in ("{}", "[]", '""'):
                example = QToolButton()
                example.setText("Example")
                example.setToolTip("Fill in an example JSON value")
                example.clicked.connect(lambda _c=False, ed=w, sk=p.skeleton: ed.setPlainText(sk))
                box = QWidget()
                bl = QHBoxLayout(box)
                bl.setContentsMargins(0, 0, 0, 0)
                bl.addWidget(w, 1)
                bl.addWidget(example, 0, Qt.AlignmentFlag.AlignTop)
                self._fields[p.name] = (p, w)
                form.addRow(label, box)
                return form.rowCount() - 1
        else:
            w = QLineEdit()
            hints = {"integer": "whole number", "float": "number",
                     "timestamp": "2026-01-31T00:00:00Z",
                     "blob": "fileb://path/to/file",
                     "list": "value1 value2 (or a JSON array)"}
            w.setPlaceholderText(hints.get(p.kind, ""))
            if p.kind == "integer":
                w.setValidator(QRegularExpressionValidator(QRegularExpression(r"-?\d*"), w))
            elif p.kind == "float":
                w.setValidator(QDoubleValidator(w))
            w.textChanged.connect(lambda _t: self._timer.start())
        w.setToolTip(tip)
        self._fields[p.name] = (p, w)
        form.addRow(label, w)
        return form.rowCount() - 1

    def _add_common(self, form: QFormLayout, spec: CommandSpec):
        region = self._line("region", self._region_placeholder())
        form.addRow(self._label("--region", "Overrides the profile's region"), region)
        output = QComboBox()
        output.addItems(OUTPUTS)
        output.setItemText(0, "(profile default)")
        output.currentIndexChanged.connect(lambda _i: self._timer.start())
        self._common["output"] = output
        form.addRow(self._label("--output", "Output format"), output)
        form.addRow(self._label("--query", "JMESPath query to filter the output"),
                    self._line("query", "Reservations[].Instances[].InstanceId"))
        if spec.supports_dry_run:
            dry = QCheckBox("--dry-run")
            dry.setToolTip("Check permissions without making the request")
            dry.toggled.connect(lambda _c: self._timer.start())
            self._common["dry_run"] = dry
            form.addRow("", dry)
        if spec.paginated:
            nopage = QCheckBox("--no-paginate")
            nopage.setToolTip("Return only the first page of results")
            nopage.toggled.connect(lambda _c: self._timer.start())
            self._common["no_paginate"] = nopage
            form.addRow("", nopage)
            form.addRow(self._label("--max-items", "Total number of items to return"),
                        self._line("max_items", "whole number"))
            form.addRow(self._label("--page-size", "Items per service call"),
                        self._line("page_size", "whole number"))
            form.addRow(self._label("--starting-token", "Resume from a NextToken"),
                        self._line("starting_token"))
        form.addRow(self._label("Extra arguments", "Typed as-is after the command"),
                    self._line("extra", "--debug, --cli-read-timeout 30 ..."))

    # ---- values and preview ---------------------------------------------

    def values(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for name, (p, w) in self._fields.items():
            if p.kind == "boolean":
                out[name] = {0: None, 1: True, 2: False}[w.currentIndex()]
            elif isinstance(w, QComboBox):
                out[name] = w.currentText()
            elif isinstance(w, QPlainTextEdit):
                out[name] = w.toPlainText()
            else:
                out[name] = w.text()
        return out

    def common(self) -> Common:
        c = Common()
        for key in ("region", "query", "max_items", "page_size", "starting_token",
                    "outfile", "extra"):
            w = self._common.get(key)
            if isinstance(w, QLineEdit):
                setattr(c, key, w.text())
        out = self._common.get("output")
        if isinstance(out, QComboBox) and out.currentIndex() > 0:
            c.output = OUTPUTS[out.currentIndex()]
        for key in ("dry_run", "no_paginate"):
            w = self._common.get(key)
            if isinstance(w, QCheckBox):
                setattr(c, key, w.isChecked())
        return c

    def command_line(self, use_files: bool = True) -> str:
        """Raises ParamError when the form is not complete or valid."""
        if not self.spec:
            raise ParamError("No command selected.")
        return render(self.spec, self.values(), self.common(), self.shell, use_files)

    def _update_preview(self):
        ok = False
        if not self.spec:
            self.preview.setPlainText("")
            self.error.clear()
        else:
            try:
                text = self.command_line(use_files=False)
                ok = True
                self.error.clear()
                if self.shell == "powershell" and '"' in text:
                    self.error.setStyleSheet("color: #8a8f98;")
                    self.error.setText("Values containing double quotes are passed to the "
                                       "CLI through a private temp file (file://).")
                else:
                    self.error.setStyleSheet("color: #d93025;")
            except ParamError as exc:
                text = ""
                self.error.setStyleSheet("color: #d93025;")
                self.error.setText(str(exc))
            self.preview.setPlainText(text)
        has = self.spec is not None
        self.btn_copy.setEnabled(has and ok)
        self.btn_docs.setEnabled(has)
        self.btn_insert.setEnabled(has and self.terminal_available and ok)
        self.btn_run.setEnabled(has and self.terminal_available and ok)

    def _emit(self, run: bool):
        if not self.spec:
            return
        try:
            text = self.command_line(use_files=True)
        except ParamError as exc:
            self.error.setText(str(exc))
            return
        self.insert_requested.emit(text if run else text + " ", run, self.spec.command)

    def _copy(self):
        try:
            self.copy_requested.emit(self.command_line(use_files=True))
        except ParamError as exc:
            self.error.setText(str(exc))
