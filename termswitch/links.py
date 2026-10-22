"""Every web address termSwitch links to, in one place.

Change WEBSITE here when about.mroughton.com goes live somewhere else.
"""

from __future__ import annotations

from urllib.parse import urlencode

WEBSITE = "https://about.mroughton.com"
REPO = "rough-ton/termSwitch"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"{REPO_URL}/releases"
LATEST_RELEASE_URL = f"{RELEASES_URL}/latest"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
NEW_ISSUE_URL = f"{REPO_URL}/issues/new"
NOTICES_URL = f"{REPO_URL}/blob/main/THIRD_PARTY_NOTICES.md"


def release_notes_url(version: str, is_release: bool) -> str:
    """The release page for the running version, or the releases list for source runs."""
    return f"{RELEASES_URL}/tag/v{version}" if is_release else RELEASES_URL


def new_issue_url(version: str, system: str) -> str:
    """The new-issue page with the version and OS filled in."""
    body = ("## What happened\n\n\n\n## Steps to reproduce\n\n1. \n\n"
            "## What you expected\n\n\n\n"
            f"## Environment\n\n- termSwitch: {version}\n- OS: {system}\n")
    return f"{NEW_ISSUE_URL}?{urlencode({'body': body})}"
