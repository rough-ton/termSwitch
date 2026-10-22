"""The home view shown in the terminal area when no terminals are open, and the
footer at the bottom left of the main window."""

from __future__ import annotations

import html
from dataclasses import dataclass

from PySide6.QtCore import QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPalette
from PySide6.QtWidgets import (QCompleter, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSizePolicy, QStatusBar, QToolButton,
                               QVBoxLayout, QWidget)

from . import __version__, theme
from .icon import app_icon
from .icons import shell_icon
from .links import WEBSITE

RECENT_LIMIT = 9            # a 3 by 3 grid, like MobaXterm's recent sessions
RECENT_COLUMNS = 3


@dataclass(frozen=True)
class HomeItem:
    """A profile or SSH session the home view can open. ``key`` is "aws:<name>"
    or "ssh:<id>"."""
    key: str
    name: str
    icon: QIcon
    tooltip: str = ""


class HomeView(QWidget):
    """Logo and name, quick actions, a search box, and recently opened items."""

    open_requested = Signal(str)          # an item key
    local_requested = Signal()
    new_profile_requested = Signal()
    new_ssh_requested = Signal()
    reopen_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: dict[str, HomeItem] = {}
        self._by_label: dict[str, str] = {}

        column = QVBoxLayout()
        column.setSpacing(18)

        # Logo and name.
        header = QHBoxLayout()
        header.setSpacing(20)
        self.logo = QLabel()
        self.logo.setPixmap(app_icon().pixmap(QSize(96, 96)))
        self.logo.setFixedSize(96, 96)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.title = QLabel("termSwitch")
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 2.6)
        self.title.setFont(font)
        self.tagline = QLabel("Flip the switch, not the profile.")
        theme.set_role(self.tagline, "hint")
        title_box.addStretch(1)
        title_box.addWidget(self.title)
        title_box.addWidget(self.tagline)
        title_box.addStretch(1)
        header.addStretch(1)
        header.addWidget(self.logo)
        header.addLayout(title_box)
        header.addStretch(1)
        column.addLayout(header)

        # Quick actions.
        actions = QHBoxLayout()
        actions.setSpacing(12)
        self.local_btn = QPushButton("Local terminal")
        self.local_btn.setToolTip("Open a shell with no AWS profile or SSH session "
                                  "(Ctrl+Shift+L)")
        self.local_btn.clicked.connect(self.local_requested)
        self.local_btn.setIcon(shell_icon())
        self.new_profile_btn = QPushButton("New profile...")
        self.new_profile_btn.clicked.connect(self.new_profile_requested)
        self.new_ssh_btn = QPushButton("New SSH session...")
        self.new_ssh_btn.clicked.connect(self.new_ssh_requested)
        self.reopen_btn = QPushButton("Reopen previous tabs")
        self.reopen_btn.clicked.connect(self.reopen_requested)
        for b in (self.local_btn, self.new_profile_btn, self.new_ssh_btn, self.reopen_btn):
            b.setMinimumHeight(34)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            actions.addWidget(b)
        column.addLayout(actions)

        # Search: type a name, pick it, and it opens.
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a profile or SSH session...")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(30)
        self._names = QStringListModel(self)
        self.completer = QCompleter(self._names, self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.activated.connect(self._open_label)
        self.search.setCompleter(self.completer)
        self.search.returnPressed.connect(lambda: self._open_label(self.search.text()))
        column.addWidget(self.search)

        # Recently opened.
        self.recent_box = QFrame()
        self.recent_box.setFrameShape(QFrame.Shape.NoFrame)
        self.recent_box.setAutoFillBackground(True)
        self.recent_box.setBackgroundRole(QPalette.ColorRole.AlternateBase)
        box = QVBoxLayout(self.recent_box)
        box.setContentsMargins(18, 12, 18, 14)
        heading = QLabel("Recent")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hfont = heading.font()
        hfont.setBold(True)
        hfont.setPointSizeF(hfont.pointSizeF() * 1.25)
        heading.setFont(hfont)
        box.addWidget(heading)
        self.recent_grid = QGridLayout()
        self.recent_grid.setHorizontalSpacing(12)
        self.recent_grid.setVerticalSpacing(4)
        box.addLayout(self.recent_grid)
        self.recent_empty = QLabel("Profiles and SSH sessions you open appear here.")
        self.recent_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.set_role(self.recent_empty, "hint")
        box.addWidget(self.recent_empty)
        column.addWidget(self.recent_box)

        self.hints = QLabel(
            "Double-click a profile or SSH session in the tree to open it here. "
            "Shift+double-click opens a profile in an external terminal.<br>"
            "<code>Ctrl+Tab</code> next tab &nbsp; <code>Ctrl+1</code> / "
            "<code>Ctrl+2</code> profiles / terminal")
        self.hints.setTextFormat(Qt.TextFormat.RichText)
        self.hints.setWordWrap(True)
        self.hints.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.set_role(self.hints, "hint")
        column.addWidget(self.hints)

        holder = QWidget()
        holder.setLayout(column)
        holder.setMaximumWidth(680)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(2)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(holder, 20)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(3)
        self.set_reopen_count(0)

    # ---- content ---------------------------------------------------------

    def set_items(self, items: list[HomeItem]) -> None:
        """Everything the search box can find."""
        self._items = {it.key: it for it in items}
        names = [it.name for it in items]
        self._by_label = {}
        for it in items:
            label = it.name
            # A profile and an SSH session can share a name; tell them apart.
            if names.count(it.name) > 1 and it.key.startswith("ssh:"):
                label = f"{it.name} (SSH)"
            self._by_label.setdefault(label, it.key)
        self._names.setStringList(sorted(self._by_label, key=str.lower))

    def set_recent(self, keys: list[str]) -> None:
        """Recently opened items, newest first. Unknown keys are skipped."""
        while self.recent_grid.count():
            w = self.recent_grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        shown = [self._items[k] for k in keys if k in self._items][:RECENT_LIMIT]
        for i, item in enumerate(shown):
            b = QToolButton()
            b.setText(item.name)
            b.setIcon(item.icon)
            b.setIconSize(QSize(18, 18))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setAutoRaise(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(item.tooltip or f"Open {item.name}")
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setProperty("home_key", item.key)
            b.clicked.connect(lambda _=False, k=item.key: self.open_requested.emit(k))
            self.recent_grid.addWidget(b, i // RECENT_COLUMNS, i % RECENT_COLUMNS)
        for c in range(RECENT_COLUMNS):
            self.recent_grid.setColumnStretch(c, 1)
        self.recent_empty.setVisible(not shown)

    def recent_buttons(self) -> list[QToolButton]:
        return [self.recent_grid.itemAt(i).widget() for i in range(self.recent_grid.count())]

    def set_reopen_count(self, count: int) -> None:
        self.reopen_btn.setEnabled(count > 0)
        self.reopen_btn.setText(f"Reopen previous tabs ({count})" if count
                                else "Reopen previous tabs")
        self.reopen_btn.setToolTip("Open the tabs that were open when termSwitch last closed"
                                   if count else "No tabs were open when termSwitch last closed.")

    def _open_label(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        key = self._by_label.get(text)
        if key is None:
            lowered = text.lower()
            exact = [k for label, k in self._by_label.items() if label.lower() == lowered]
            partial = sorted((label for label in self._by_label if lowered in label.lower()),
                             key=str.lower)
            key = exact[0] if exact else (self._by_label[partial[0]] if len(partial) == 1
                                          else None)
        if key is not None:
            self.search.clear()
            self.open_requested.emit(key)


class Footer(QLabel):
    """Name, version and a link to support termSwitch development, at the bottom
    left of the main window."""

    def __init__(self, version: str = __version__, url: str = WEBSITE, parent=None):
        super().__init__(parent)
        self.version = version
        self.url = url
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setOpenExternalLinks(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
                                     | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.setToolTip(url)
        self.setContentsMargins(4, 0, 8, 0)
        self.refresh()

    def refresh(self) -> None:
        """Re-render with the current theme's colors."""
        c = theme.colors()
        shown = html.escape(self.url.split("://", 1)[-1].rstrip("/"))
        self.setText(
            f"<b>termSwitch</b> <span style='color:{c.hint}'>v{html.escape(self.version)}"
            f" &nbsp;·&nbsp; Please support termSwitch development:</span> "
            f"<a href='{html.escape(self.url, quote=True)}' "
            f"style='color:{c.link}'>{shown}</a>")


class AppStatusBar(QStatusBar):
    """A status bar that keeps the footer visible at the bottom left.

    QStatusBar hides its left-hand widgets while a message is shown, so messages
    go to a label beside the footer instead. ``showMessage``, ``clearMessage`` and
    ``currentMessage`` work as before for the rest of the app."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.footer = Footer()
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setMinimumWidth(1)       # long messages are cut, never widen the window
        self._clear = QTimer(self)
        self._clear.setSingleShot(True)
        self._clear.timeout.connect(self.clearMessage)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        self.addWidget(self.footer)
        self.addWidget(divider)
        self.addWidget(self.message, 1)

    def showMessage(self, text: str, timeout: int = 0) -> None:   # noqa: N802 (Qt name)
        self.message.setText(text)
        self.message.setToolTip(text)
        if timeout > 0:
            self._clear.start(timeout)
        else:
            self._clear.stop()

    def clearMessage(self) -> None:                                # noqa: N802
        self._clear.stop()
        self.message.clear()
        self.message.setToolTip("")

    def currentMessage(self) -> str:                               # noqa: N802
        return self.message.text()
