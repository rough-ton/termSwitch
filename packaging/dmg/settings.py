# dmgbuild settings for termSwitch-macos-arm64.dmg. Used by the release workflow:
#
#   python -m dmgbuild -s packaging/dmg/settings.py -D app=dist/termSwitch.app \
#       termSwitch dist/termSwitch-macos-arm64.dmg
#
# The window layout comes from layout.json, which also drives the background artwork
# (tools/make_dmg_background.py), so icons always land on their spots in the picture.
# dmgbuild runs this file with `defines` (from -D) in scope.

import json
import os.path

# dmgbuild runs this file with only `defines` in scope (no __file__), from the repo root.
_here = defines.get("dmg_dir", os.path.join("packaging", "dmg"))  # noqa: F821
with open(os.path.join(_here, "layout.json"), encoding="utf-8") as _fh:
    _layout = json.load(_fh)

application = defines.get("app", "dist/termSwitch.app")  # noqa: F821
appname = os.path.basename(application)

# Contents: the app and a link to /Applications to drop it on.
files = [application]
symlinks = {"Applications": "/Applications"}
hide_extensions = [appname]          # the label reads "termSwitch", not "termSwitch.app"

# Compressed with LZMA (macOS 10.15 and later; termSwitch needs macOS 12).
format = "ULMO"
filesystem = "HFS+"

# The mounted disk shows the app icon.
icon = "termswitch/assets/termswitch.icns"

# Window: a fixed icon view with the background, no toolbar or sidebar.
background = os.path.join(_here, "background.png")    # background@2x.png is picked up too
_w, _h = _layout["window"]["width"], _layout["window"]["height"]
window_rect = ((200, 140), (_w, _h))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
show_icon_preview = False
show_item_info = False
include_icon_view_settings = True
include_list_view_settings = False
arrange_by = None
label_pos = "bottom"
icon_size = _layout["icon_size"]
text_size = _layout["text_size"]
icon_locations = {
    appname: (_layout["app"]["x"], _layout["app"]["y"]),
    "Applications": (_layout["applications"]["x"], _layout["applications"]["y"]),
}
