import argparse
import sys

from . import __version__


def _attach_console() -> None:
    """The packaged Windows exe has no console of its own. When it is run from a
    terminal with --check, attach to that terminal so the report is visible."""
    if sys.stdout is not None or not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            sys.stderr = sys.stdout
            print()
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="termswitch",
                                     description="Grouped AWS profiles that open authenticated terminals.")
    parser.add_argument("--check", action="store_true",
                        help="run the dependency check, print a report and exit (1 on errors)")
    parser.add_argument("--offline", action="store_true",
                        help="with --check, skip the latest-version lookups")
    parser.add_argument("--version", action="version", version=f"termSwitch {__version__}")
    args, qt_args = parser.parse_known_args(argv)

    if args.check:
        _attach_console()
        from .deps import ERROR, print_report, run_checks, worst
        checks = run_checks(online=not args.offline)
        print_report(checks)
        return 1 if worst(checks) == ERROR else 0

    from PySide6.QtWidgets import QApplication

    from .icon import app_icon, set_windows_app_id
    from .ui import MainWindow

    set_windows_app_id()
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("termSwitch")
    app.setApplicationDisplayName("termSwitch")
    app.setOrganizationName("rough-ton")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
