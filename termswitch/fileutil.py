"""File helpers shared by everything that saves files."""

from __future__ import annotations

import os
import sys
import time

# How long to keep trying when Windows refuses to replace a file.
RETRY_SECONDS = 2.0


def replace_file(src, dst, retry_seconds: float = RETRY_SECONDS) -> None:
    """os.replace that copes with Windows sharing rules.

    Windows refuses to replace a file while another process has it open: a virus
    scanner, the search indexer, a sync client, or the AWS CLI reading its config.
    Those holds last milliseconds, so on Windows the swap is retried for a short
    while before the error is raised. On macOS and Linux this is os.replace.
    """
    if sys.platform != "win32":
        os.replace(src, dst)
        return
    deadline = time.monotonic() + retry_seconds
    delay = 0.01
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.2)
