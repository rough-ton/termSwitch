"""termSwitch's own data for this user, and removing it.

Used by `termSwitch --remove-user-data`, which the Windows uninstaller runs when you
choose to remove your termSwitch settings. It removes only what termSwitch itself
created:

- settings (window layout, groups, favorites, theme: QSettings)
- saved SSH sessions, imported color schemes (the app data folder)
- saved SSH passwords, from the macOS Keychain or Windows Credential Manager
- the AWS command cache

Never ~/.aws or ~/.ssh, and never the backups termSwitch keeps next to ~/.aws/config.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ORGANIZATION = "rough-ton"
APPLICATION = "termSwitch"


class UserDataError(Exception):
    pass


def set_names() -> None:
    """The names QSettings and QStandardPaths build paths from (as in __main__)."""
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.setOrganizationName(ORGANIZATION)
    QCoreApplication.setApplicationName(APPLICATION)


def app_data_dir() -> Path:
    """Saved SSH sessions and imported schemes (see ssh_sessions and term_schemes)."""
    from .ssh_sessions import default_path
    return default_path().parent


def cache_root() -> Path:
    """The folder holding termSwitch's caches (on Windows, the cache folder's parent)."""
    from .aws_commands import cache_dir
    path = cache_dir()
    return path.parent if path.name == "cache" else path


def _owned(path: Path) -> bool:
    """Only a folder named after termSwitch is ever removed, so a broken path can never
    point the removal at a home folder, ~/.aws or ~/.ssh."""
    path = Path(path)
    home = Path.home()
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if resolved in (home.resolve(), Path(resolved.anchor)):
        return False
    if any(part.lower() in (".aws", ".ssh") for part in resolved.parts):
        return False
    return path.name.lower().lstrip(".") == "termswitch"


def _delete_passwords(sessions_file: Path, passwords) -> int:
    from .ssh_sessions import SessionStore
    if not sessions_file.exists():
        return 0
    store = SessionStore(path=sessions_file, ssh_config=sessions_file.parent / "no-ssh-config")
    store.load()
    for sid in store.sessions:
        passwords.delete(sid)
    return len(store.sessions)


def _delete_windows_key() -> None:
    """QSettings.clear() empties the registry key but leaves it; remove it too, and
    the organization key above it when nothing else is in it."""
    import winreg
    path = rf"Software\{ORGANIZATION}\{APPLICATION}"

    def delete_tree(root, sub):
        try:
            with winreg.OpenKey(root, sub, 0, winreg.KEY_READ) as key:
                children = []
                i = 0
                while True:
                    try:
                        children.append(winreg.EnumKey(key, i))
                    except OSError:
                        break
                    i += 1
        except FileNotFoundError:
            return
        for child in children:
            delete_tree(root, rf"{sub}\{child}")
        winreg.DeleteKey(root, sub)

    delete_tree(winreg.HKEY_CURRENT_USER, path)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"Software\{ORGANIZATION}")
    except OSError:
        pass                # not empty, or already gone


def remove(*, settings=None, data_dir: Path | None = None, caches: Path | None = None,
           passwords=None) -> list[str]:
    """Remove termSwitch's data for this user. Returns what was removed, in words.
    The arguments exist for tests; by default the real locations are used."""
    from PySide6.QtCore import QSettings

    from .ssh_sessions import FILE_NAME, PasswordStore

    done: list[str] = []
    data_dir = Path(data_dir) if data_dir is not None else app_data_dir()
    caches = Path(caches) if caches is not None else cache_root()
    for folder in (data_dir, caches):
        if folder.exists() and not _owned(folder):
            raise UserDataError(f"Refusing to remove {folder}: it isn't a termSwitch folder.")

    # Passwords first: they're found through the saved sessions.
    passwords = passwords if passwords is not None else PasswordStore()
    count = _delete_passwords(data_dir / FILE_NAME, passwords)
    if count:
        done.append(f"saved passwords for {count} SSH session(s)")

    real_settings = settings is None
    settings = settings if settings is not None else QSettings()
    if settings.allKeys():
        done.append("settings")
    settings.clear()
    settings.sync()
    if real_settings and sys.platform.startswith("win") \
            and settings.format() == QSettings.Format.NativeFormat:
        _delete_windows_key()
    else:
        file = Path(settings.fileName())
        if file.suffix in (".ini", ".conf") and file.is_file():
            file.unlink()

    for label, folder in (("saved SSH sessions and color schemes", data_dir),
                          ("caches", caches)):
        if folder.exists():
            shutil.rmtree(folder)
            done.append(f"{label} ({folder})")
            if folder.parent.name == ORGANIZATION:
                try:
                    folder.parent.rmdir()   # the organization folder, when now empty
                except OSError:
                    pass
    return done
