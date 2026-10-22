"""Favorite commands for the Commands pane.

Favorites are stored per user in termSwitch's settings as ``"<service> <command>"``
strings, for example ``"ec2 describe-instances"``, in the order they were added.
Nothing is written to ``~/.aws/config``. Export and import use a small JSON file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal

SETTINGS_KEY = "favorite_commands"
FILE_FORMAT = "termswitch-favorites"
FILE_VERSION = 1
MAX_IMPORT_BYTES = 1_000_000
MAX_FAVORITES = 5_000

# CLI service and command names: lowercase letters, digits and hyphens.
_KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,99} [a-z0-9][a-z0-9-]{0,199}")


def is_valid_key(value) -> bool:
    return isinstance(value, str) and bool(_KEY.fullmatch(value))


def key_for(service: str, name: str) -> str:
    return f"{service} {name}"


class Favorites(QObject):
    """An ordered set of favorite command keys, saved in QSettings."""

    changed = Signal()

    def __init__(self, settings: QSettings | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings if settings is not None else QSettings()
        raw = self.settings.value(SETTINGS_KEY, [], type=list) or []
        self._items: list[str] = []
        for value in raw:
            if is_valid_key(value) and value not in self._items:
                self._items.append(value)

    # ---- queries ---------------------------------------------------------

    def items(self) -> list[str]:
        """Favorites in the order they were added."""
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: str) -> bool:
        return key in self._items

    # ---- changes ---------------------------------------------------------

    def add(self, key: str) -> bool:
        if not is_valid_key(key) or key in self._items or len(self._items) >= MAX_FAVORITES:
            return False
        self._items.append(key)
        self._save()
        return True

    def remove(self, key: str) -> bool:
        if key not in self._items:
            return False
        self._items.remove(key)
        self._save()
        return True

    def toggle(self, key: str) -> bool:
        """Add or remove; returns True if the key is now a favorite."""
        if key in self._items:
            self.remove(key)
            return False
        return self.add(key)

    def _save(self):
        self.settings.setValue(SETTINGS_KEY, list(self._items))
        self.settings.sync()
        self.changed.emit()

    # ---- export and import -----------------------------------------------

    def export_file(self, path: str | os.PathLike) -> int:
        """Write all favorites to a JSON file atomically. Returns the count."""
        path = Path(path)
        data = {"format": FILE_FORMAT, "version": FILE_VERSION, "favorites": self.items()}
        text = json.dumps(data, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".favorites-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return len(self._items)

    def import_file(self, path: str | os.PathLike) -> tuple[int, int]:
        """Merge favorites from a JSON file.

        Accepts the exported object or a plain JSON list of strings. Entries that
        aren't valid ``"service command"`` strings are rejected. Returns
        ``(added, rejected)``; entries that are already favorites count as neither.
        Raises ``ValueError`` for files that aren't favorites files.
        """
        path = Path(path)
        if not path.is_file():
            raise ValueError("Not a file.")
        if path.stat().st_size > MAX_IMPORT_BYTES:
            raise ValueError("The file is too large to be a favorites file.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Not a valid JSON file: {exc}") from None
        if isinstance(data, dict):
            if data.get("format") != FILE_FORMAT:
                raise ValueError("Not a termSwitch favorites file.")
            entries = data.get("favorites")
        else:
            entries = data
        if not isinstance(entries, list):
            raise ValueError("The file has no list of favorites.")
        added = rejected = 0
        for value in entries:
            if isinstance(value, str):
                value = " ".join(value.split())   # tolerate stray whitespace
            if not is_valid_key(value):
                rejected += 1
            elif value not in self._items and len(self._items) < MAX_FAVORITES:
                self._items.append(value)
                added += 1
        if added:
            self._save()
        return added, rejected
