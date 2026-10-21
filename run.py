"""Entry point for PyInstaller and for running without -m."""
import sys

from termswitch.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
