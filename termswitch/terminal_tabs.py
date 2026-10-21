"""Tabbed area holding embedded terminals, one per opened profile."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon
from PySide6.QtWidgets import (QInputDialog, QLabel, QMenu, QMessageBox, QStackedWidget,
                               QTabWidget, QVBoxLayout, QWidget)

from .config_store import Profile
from .icons import shell_icon
from .launcher import LaunchError, LaunchOptions, embedded_command, find_aws
from .terminal_widget import TerminalWidget


def _color_icon(color: str | None, exited: bool = False) -> QIcon:
    """Shell icon for a tab, with the profile color as a dot (production red)."""
    return shell_icon(dot=color, exited=exited)


@dataclass
class TabInfo:
    profile: Profile
    options: LaunchOptions
    widget: TerminalWidget
    title: str


class TerminalTabs(QStackedWidget):
    """Shows a placeholder when empty, otherwise the terminal tabs."""

    open_external_requested = Signal(object)     # Profile
    count_changed = Signal(int)
    terminal_state_changed = Signal()            # a shell exited or restarted

    def __init__(self, placeholder_html: str = "", parent=None):
        super().__init__(parent)
        self.font = None
        self.scrollback = 10000
        self.confirm_close = True
        self.confirm_multiline_paste = True
        self._infos: dict[int, TabInfo] = {}

        self.placeholder = QLabel(placeholder_html)
        self.placeholder.setWordWrap(True)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setTextFormat(Qt.TextFormat.RichText)
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.addStretch(1)
        lay.addWidget(self.placeholder)
        lay.addStretch(1)
        self.addWidget(holder)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._focus_current)
        bar = self.tabs.tabBar()
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._tab_menu)
        self.addWidget(self.tabs)

    # ---- opening ---------------------------------------------------------

    def open_profile(self, profile: Profile, options: LaunchOptions,
                     title: str | None = None) -> TerminalWidget:
        argv, env = embedded_command(profile, options, aws=find_aws())
        term = TerminalWidget(argv, env, font=self.font, scrollback=self.scrollback,
                              confirm_multiline_paste=self.confirm_multiline_paste)
        title = title or f"aws: {profile.name}"
        info = TabInfo(profile, options, term, title)
        self._infos[id(term)] = info
        idx = self.tabs.addTab(term, _color_icon(options.tab_color), title)
        self.tabs.setTabToolTip(idx, self._tooltip(profile))
        if options.tab_color:
            self.tabs.tabBar().setTabTextColor(idx, QColor(options.tab_color))
        term.exited.connect(lambda code, t=term: self._on_exited(t, code))
        self.setCurrentWidget(self.tabs)
        self.tabs.setCurrentIndex(idx)
        term.start()
        term.setFocus()
        self.count_changed.emit(self.tabs.count())
        return term

    @staticmethod
    def _tooltip(profile: Profile) -> str:
        parts = [profile.name]
        if profile.account_id:
            parts.append(f"Account {profile.account_id}")
        if profile.role:
            parts.append(profile.role)
        return " | ".join(parts)

    # ---- state -----------------------------------------------------------

    def current_terminal(self) -> TerminalWidget | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, TerminalWidget) else None

    def terminals(self) -> list[TerminalWidget]:
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def running_count(self) -> int:
        return sum(1 for t in self.terminals() if t.is_alive())

    def _index_of(self, term: TerminalWidget) -> int:
        return self.tabs.indexOf(term)

    def _focus_current(self, _idx: int):
        t = self.current_terminal()
        if t:
            t.setFocus()

    def _on_exited(self, term: TerminalWidget, code):
        idx = self._index_of(term)
        info = self._infos.get(id(term))
        if idx < 0 or not info:
            return
        self.tabs.setTabIcon(idx, _color_icon(None, exited=True))
        self.tabs.setTabText(idx, f"{info.title} (exited)")
        self.terminal_state_changed.emit()

    # ---- closing ---------------------------------------------------------

    def close_tab(self, idx: int) -> bool:
        term = self.tabs.widget(idx)
        if not isinstance(term, TerminalWidget):
            return False
        if self.confirm_close and term.has_foreground_job():
            info = self._infos.get(id(term))
            name = info.title if info else "this terminal"
            ok = QMessageBox.question(
                self, "Close terminal",
                f"{name} is still running. Close it and end its processes?")
            if ok != QMessageBox.StandardButton.Yes:
                return False
        term.terminate()
        self._infos.pop(id(term), None)
        self.tabs.removeTab(idx)
        term.deleteLater()
        if self.tabs.count() == 0:
            self.setCurrentIndex(0)
        self.count_changed.emit(self.tabs.count())
        return True

    def close_all(self) -> None:
        for t in self.terminals():
            t.terminate()
        while self.tabs.count():
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            w.deleteLater()
        self._infos.clear()
        self.setCurrentIndex(0)

    # ---- tab menu --------------------------------------------------------

    def _tab_menu(self, pos):
        bar = self.tabs.tabBar()
        idx = bar.tabAt(pos)
        if idx < 0:
            return
        term = self.tabs.widget(idx)
        info = self._infos.get(id(term))
        if not info:
            return
        menu = QMenu(self)
        menu.addAction("Duplicate tab", lambda: self.open_profile(info.profile, info.options))
        if not term.is_alive():
            menu.addAction("Restart", lambda: self._restart(term))
        menu.addAction("Rename tab...", lambda: self._rename(idx))
        menu.addAction("Open in external terminal",
                       lambda: self.open_external_requested.emit(info.profile))
        menu.addAction("Copy profile name",
                       lambda: QGuiApplication.clipboard().setText(info.profile.name))
        menu.addSeparator()
        menu.addAction("Close tab", lambda: self.close_tab(self._index_of(term)))
        if self.tabs.count() > 1:
            menu.addAction("Close other tabs", lambda: self._close_others(term))
        menu.exec(bar.mapToGlobal(pos))

    def _restart(self, term: TerminalWidget):
        info = self._infos.get(id(term))
        idx = self._index_of(term)
        if info and idx >= 0:
            self.tabs.setTabText(idx, info.title)
            self.tabs.setTabIcon(idx, _color_icon(info.options.tab_color))
        term.restart()
        self.terminal_state_changed.emit()

    def _rename(self, idx: int):
        term = self.tabs.widget(idx)
        info = self._infos.get(id(term))
        if not info:
            return
        text, ok = QInputDialog.getText(self, "Rename tab", "Tab name:", text=info.title)
        if ok and text.strip():
            info.title = text.strip()
            self.tabs.setTabText(idx, info.title + ("" if term.is_alive() else " (exited)"))

    def _close_others(self, keep: TerminalWidget):
        for t in self.terminals():
            if t is not keep:
                if not self.close_tab(self._index_of(t)):
                    break
