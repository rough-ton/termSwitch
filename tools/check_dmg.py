"""Check a mounted termSwitch .dmg: its contents, background and icon positions.

    hdiutil attach -nobrowse -readonly -mountpoint /tmp/ts dist/termSwitch-macos-arm64.dmg
    python tools/check_dmg.py /tmp/ts

Exits non-zero, naming each problem, if the window would not look as designed. Run by
the release workflow on macOS; ds_store comes from requirements-build.lock there.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAYOUT = json.loads((ROOT / "packaging" / "dmg" / "layout.json").read_text(encoding="utf-8"))


def problems(mount: Path) -> list[str]:
    found = []
    app = mount / "termSwitch.app"
    if not (app / "Contents" / "MacOS" / "termSwitch").is_file():
        found.append("termSwitch.app is missing or incomplete")
    link = mount / "Applications"
    if not link.is_symlink() or os.readlink(link) != "/Applications":
        found.append("Applications is not a link to /Applications")
    backgrounds = list((mount / ".background").glob("*")) + list(mount.glob(".background.*"))
    if not backgrounds:
        found.append("the window background is missing")
    store = mount / ".DS_Store"
    if not store.is_file():
        return found + ["the window layout (.DS_Store) is missing"]

    from ds_store import DSStore
    with DSStore.open(str(store), "r") as d:
        for name, key in (("termSwitch.app", "app"), ("Applications", "applications")):
            want = (LAYOUT[key]["x"], LAYOUT[key]["y"])
            try:
                got = tuple(d[name]["Iloc"])
            except KeyError:
                found.append(f"{name} has no position in the window layout")
                continue
            if got != want:
                found.append(f"{name} is at {got}, the background expects {want}")
        try:
            d["."]["icvp"]
        except KeyError:
            found.append("the window has no icon view settings (background, icon size)")
    return found


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__)
        return 2
    found = problems(Path(args[0]))
    for p in found:
        print(f"::error::dmg: {p}")
    if not found:
        print("dmg layout ok")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
