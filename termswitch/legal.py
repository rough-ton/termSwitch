"""termSwitch's license and the third-party notices, shown in Help > Licenses.

Release builds carry the files in a ``legal`` folder next to the bundled code (see
``termswitch.spec``). A source checkout reads them from the repository root.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QListWidget,
                               QListWidgetItem, QTextBrowser, QVBoxLayout)

MAX_BYTES = 1_000_000  # the largest text here is ~35 KB; refuse anything odd

# Friendly names for the files in licenses/, in display order.
LICENSE_TITLES = {
    "LGPL-3.0.txt": "GNU LGPL 3.0 (Qt, pyte)",
    "GPL-3.0.txt": "GNU GPL 3.0 (referenced by the LGPL)",
    "GPL-2.0.txt": "GNU GPL 2.0 (PyInstaller)",
    "Apache-2.0.txt": "Apache License 2.0 (PyInstaller runtime)",
    "Python-LICENSE.txt": "Python Software Foundation License",
}


@dataclass(frozen=True)
class LegalDoc:
    title: str
    path: Path
    markdown: bool = False


def legal_dir() -> Path:
    """Where LICENSE, THIRD_PARTY_NOTICES.md and licenses/ live.

    - release builds: the bundled ``legal`` folder
    - source checkouts: the repository root
    - ``pip install``: the ``licenses`` folder of the installed package's metadata
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "legal"
    root = Path(__file__).resolve().parent.parent
    if (root / "LICENSE").is_file():
        return root
    try:
        from importlib import metadata

        for f in metadata.distribution("termswitch").files or []:
            if f.parts[-1] == "LICENSE" and "licenses" in f.parts:
                return Path(f.locate()).parent
    except Exception:  # metadata is optional; fall back to the root
        pass
    return root


def _python_license() -> Path | None:
    """The running interpreter's license, for source checkouts."""
    ver = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    for rel in ("LICENSE.txt", Path("lib") / ver / "LICENSE.txt"):
        path = Path(sys.base_prefix) / rel
        if path.is_file():
            return path
    return None


def documents(base: Path | None = None) -> list[LegalDoc]:
    """Every legal document that exists, project license first."""
    base = base or legal_dir()
    docs = [LegalDoc("termSwitch license (MIT)", base / "LICENSE"),
            LegalDoc("Third-party notices", base / "THIRD_PARTY_NOTICES.md", markdown=True)]
    lic = base / "licenses"
    names = sorted(p.name for p in lic.glob("*.txt")) if lic.is_dir() else []
    order = list(LICENSE_TITLES)
    names.sort(key=lambda n: (order.index(n) if n in order else len(order), n))
    docs += [LegalDoc(LICENSE_TITLES.get(n, n.removesuffix(".txt")), lic / n) for n in names]
    if "Python-LICENSE.txt" not in names:
        py = _python_license()
        if py:
            docs.append(LegalDoc(LICENSE_TITLES["Python-LICENSE.txt"], py))
    return [d for d in docs if d.path.is_file()]


def read_text(path: Path) -> str:
    """Read a legal file as text, refusing anything that isn't a small regular file."""
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise OSError(f"{path.name} is missing or too large")
    return path.read_text(encoding="utf-8", errors="replace")


class LicensesDialog(QDialog):
    """Read-only viewer: a list of documents on the left, the text on the right."""

    def __init__(self, parent=None, start: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Licenses")
        self.resize(900, 620)
        self.docs = documents()

        self.list = QListWidget()
        self.list.setMaximumWidth(280)
        from .icons import icon
        for doc in self.docs:
            item = QListWidgetItem(icon("markdown" if doc.markdown else "license"), doc.title)
            item.setData(Qt.ItemDataRole.UserRole, doc)
            self.list.addItem(item)

        self.view = QTextBrowser()
        self.view.setReadOnly(True)
        # Links in the notices point to source code. https links open in the browser;
        # relative links switch documents; anything else is ignored.
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.view.anchorClicked.connect(self._open_link)

        body = QHBoxLayout()
        body.addWidget(self.list)
        body.addWidget(self.view, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(buttons)

        self.list.currentItemChanged.connect(self._show)
        row = 0
        if start:
            row = next((i for i, d in enumerate(self.docs) if d.title == start), 0)
        if self.docs:
            self.list.setCurrentRow(row)
        else:
            self.view.setPlainText("The license files are missing from this installation.\n"
                                   "See https://github.com/rough-ton/termSwitch")

    def _show(self, item, _previous=None):
        if item is None:
            return
        doc: LegalDoc = item.data(Qt.ItemDataRole.UserRole)
        try:
            text = read_text(doc.path)
        except OSError as exc:
            self.view.setPlainText(str(exc))
            return
        if doc.markdown:
            self.view.document().setDefaultFont(self.font())
            self.view.setMarkdown(text)
        else:
            self.view.document().setDefaultFont(
                QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
            self.view.setPlainText(text)
        self.view.verticalScrollBar().setValue(0)

    def _open_link(self, url):
        from PySide6.QtGui import QDesktopServices

        if url.scheme() == "https":
            QDesktopServices.openUrl(url)
            return
        # Relative links such as licenses/LGPL-3.0.txt jump to that document.
        name = Path(url.path()).name
        for i, doc in enumerate(self.docs):
            if doc.path.name == name:
                self.list.setCurrentRow(i)
                return
