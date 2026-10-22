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

from .fileutil import replace_file

SETTINGS_KEY = "favorite_commands"
DEFAULTS_ADDED_KEY = "favorite_commands_defaults_added"

DEFAULTS_VERSION_KEY = "favorite_commands_defaults_version"
DEFAULTS_VERSION = 2

# Starter favorites: five everyday AWS CLI commands. Who am I, what's running,
# what's in my buckets, copy files, and what are my logs saying.
DEFAULT_FAVORITES = (
    "sts get-caller-identity",
    "s3 ls",
    "s3 cp",
    "ec2 describe-instances",
    "logs tail",
)

# The first set of starter favorites (version 1), taken from every command in "Top 20
# AWS CLI Commands Every DevOps Engineer Should Know". Kept to slim down untouched
# lists on upgrade.
PREVIOUS_DEFAULTS = (
    "sts get-caller-identity", "ec2 describe-instances", "ec2 start-instances",
    "ec2 stop-instances", "ec2 create-snapshot", "s3 ls", "s3 cp", "s3 sync",
    "iam list-users", "iam list-roles", "iam list-policies", "iam list-groups",
    "iam attach-user-policy", "iam attach-group-policy", "iam attach-role-policy",
    "lambda list-functions", "lambda invoke", "cloudformation validate-template",
    "cloudformation deploy", "ecr get-login-password", "ecs list-clusters",
    "eks list-clusters", "eks describe-cluster", "ecs describe-clusters",
    "logs describe-log-groups", "logs filter-log-events",
    "autoscaling describe-auto-scaling-groups", "elbv2 describe-load-balancers",
    "rds describe-db-instances", "cloudwatch get-metric-data",
)
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

    def __init__(self, settings: QSettings | None = None, parent=None,
                 defaults: tuple[str, ...] = ()):
        super().__init__(parent)
        self.settings = settings if settings is not None else QSettings()
        self.defaults = tuple(d for d in defaults if is_valid_key(d))
        raw = self.settings.value(SETTINGS_KEY, [], type=list) or []
        self._items: list[str] = []
        for value in raw:
            if is_valid_key(value) and value not in self._items:
                self._items.append(value)
        # Starter favorites are added once. After that the list is the user's own:
        # removing them, or all favorites, never brings them back on restart.
        if self.defaults and not self.settings.value(DEFAULTS_ADDED_KEY, False, type=bool):
            self.settings.setValue(DEFAULTS_ADDED_KEY, True)
            self.settings.setValue(DEFAULTS_VERSION_KEY, DEFAULTS_VERSION)
            self.add_defaults()
        elif self.defaults and self.settings.value(DEFAULTS_VERSION_KEY, 1, type=int) < DEFAULTS_VERSION:
            self.settings.setValue(DEFAULTS_VERSION_KEY, DEFAULTS_VERSION)
            self._slim_previous_defaults()

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

    def add_defaults(self) -> int:
        """Add any starter favorites that are missing. Returns how many were added."""
        added = 0
        for key in self.defaults:
            if key not in self._items and len(self._items) < MAX_FAVORITES:
                self._items.append(key)
                added += 1
        if added:
            self._save()
        else:
            self.settings.sync()
        return added

    def _slim_previous_defaults(self) -> None:
        """Upgrade from the 30 first-version starters to the current five, but only
        when all 30 are still there, so a list the user has edited is never
        touched. Favorites the user added themselves are always kept."""
        if not set(PREVIOUS_DEFAULTS) <= set(self._items):
            self.settings.sync()
            return
        drop = set(PREVIOUS_DEFAULTS) - set(self.defaults)
        self._items = [k for k in self._items if k not in drop]
        for key in self.defaults:
            if key not in self._items:
                self._items.append(key)
        self._save()

    def missing_defaults(self) -> int:
        return sum(1 for key in self.defaults if key not in self._items)

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
            replace_file(tmp, path)
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
