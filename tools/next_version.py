"""Print the version the next release from main should use.

MAJOR.MINOR comes from BASE_VERSION in termswitch/__init__.py. The patch is
one more than the highest existing vMAJOR.MINOR.* tag, and never lower than
BASE_VERSION's own patch. With no tags for MAJOR.MINOR yet, BASE_VERSION is
used as is.

    python tools/next_version.py            # uses `git tag`
    python tools/next_version.py --stamp    # also writes termswitch/_version.py
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def base_version(init_text: str) -> tuple[int, int, int]:
    m = re.search(r'^BASE_VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', init_text, re.M)
    if not m:
        raise SystemExit("BASE_VERSION = \"X.Y.Z\" not found in termswitch/__init__.py")
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def next_version(base: tuple[int, int, int], tags: list[str]) -> str:
    major, minor, patch = base
    used = []
    for tag in tags:
        m = SEMVER.match(tag.strip())
        if m and int(m.group(1)) == major and int(m.group(2)) == minor:
            used.append(int(m.group(3)))
    nxt = max(patch, max(used) + 1) if used else patch
    return f"{major}.{minor}.{nxt}"


def git_tags() -> list[str]:
    out = subprocess.run(["git", "tag", "--list", "v*"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    return out.split()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stamp", action="store_true", help="write termswitch/_version.py")
    args = ap.parse_args()
    version = next_version(base_version((ROOT / "termswitch" / "__init__.py").read_text()),
                           git_tags())
    if args.stamp:
        (ROOT / "termswitch" / "_version.py").write_text(
            f'__version__ = "{version}"\n', encoding="utf-8")
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
