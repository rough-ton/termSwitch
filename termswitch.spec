# PyInstaller build spec for termSwitch.
#
#   pyinstaller termswitch.spec
#
# Windows: dist/termSwitch/termSwitch.exe   (one folder; packaging/windows turns it
#          into the installer and the portable .zip)
# macOS:   dist/termSwitch.app   (the release workflow packs it into one .dmg)
# Linux:   dist/termSwitch       (one self-contained file)
#
# macOS apps have to be bundles, and PyInstaller no longer supports one-file .app
# builds, so on macOS the single download is a .dmg holding the .app. On Windows a
# folder starts faster than one file (nothing is unpacked to TEMP at each launch) and
# is what the installer ships.
import os
import sys

sys.path.insert(0, SPECPATH)  # noqa: F821 (defined by PyInstaller)
from termswitch import __version__  # noqa: E402

is_mac = sys.platform == "darwin"
is_win = sys.platform.startswith("win")
NAME = "termSwitch"

from PyInstaller.utils.hooks import collect_all, copy_metadata  # noqa: E402

# Package metadata lets the in-app dependency check report bundled versions.
METADATA = (copy_metadata("PySide6-Essentials") + copy_metadata("shiboken6")
            + copy_metadata("configupdater") + copy_metadata("pyte")
            + copy_metadata("wcwidth"))
EXTRA_BINARIES, EXTRA_HIDDEN = [], []
if is_win:
    # pywinpty loads conpty.dll / OpenConsole.exe at runtime, so collect the
    # whole package rather than relying on import analysis.
    w_datas, w_binaries, w_hidden = collect_all("winpty")
    METADATA += w_datas + copy_metadata("pywinpty")
    EXTRA_BINARIES += w_binaries
    EXTRA_HIDDEN += w_hidden
else:
    METADATA += copy_metadata("ptyprocess")
if is_win or is_mac:
    # keyring finds its backends through package metadata (entry points). Only the
    # system store backend for this platform is used (see ssh_sessions.PasswordStore).
    METADATA += copy_metadata("keyring")
    EXTRA_HIDDEN += ["keyring.backends.Windows" if is_win else "keyring.backends.macOS"]

# License texts shown in Help > Licenses (termswitch/legal.py reads them from here).
# On macOS, PyInstaller places data files in termSwitch.app/Contents/Resources.
LEGAL = [("LICENSE", "legal"), ("THIRD_PARTY_NOTICES.md", "legal"),
         ("licenses/*.txt", "legal/licenses")]


def _python_license():
    """The license file of the interpreter being bundled, so the notice is exact."""
    ver = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    for rel in ("LICENSE.txt", os.path.join("lib", ver, "LICENSE.txt")):
        path = os.path.join(sys.base_prefix, rel)
        if os.path.isfile(path):
            return path
    raise SystemExit("Python's LICENSE.txt was not found; it must ship with the build.")


import shutil  # noqa: E402

_py_lic = os.path.join(workpath, "Python-LICENSE.txt")  # noqa: F821
os.makedirs(workpath, exist_ok=True)  # noqa: F821
shutil.copyfile(_python_license(), _py_lic)
LEGAL.append((_py_lic, "legal/licenses"))

a = Analysis(
    ["run.py"],
    datas=[("termswitch/assets", "termswitch/assets"), *LEGAL, *METADATA],
    binaries=EXTRA_BINARIES,
    hiddenimports=["pyte", *EXTRA_HIDDEN],
    # Keep the bundle small: none of these are used.
    excludes=["tkinter", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtOpenGL", "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest",
              "PySide6.QtDBus", "PySide6.QtConcurrent"],
    noarchive=False,
)
pyz = PYZ(a.pure)

version_file = None
if is_win:
    from PyInstaller.utils.win32 import versioninfo as vi
    parts = tuple(int(x) for x in (__version__.split(".") + ["0"] * 4)[:4])
    info = vi.VSVersionInfo(
        ffi=vi.FixedFileInfo(filevers=parts, prodvers=parts),
        kids=[
            vi.StringFileInfo([vi.StringTable("040904B0", [
                vi.StringStruct("CompanyName", "rough-ton"),
                vi.StringStruct("FileDescription", "termSwitch: grouped AWS profile terminals"),
                vi.StringStruct("FileVersion", __version__),
                vi.StringStruct("InternalName", NAME),
                vi.StringStruct("OriginalFilename", f"{NAME}.exe"),
                vi.StringStruct("ProductName", NAME),
                vi.StringStruct("ProductVersion", __version__),
            ])]),
            vi.VarFileInfo([vi.VarStruct("Translation", [1033, 1200])]),
        ],
    )
    version_file = os.path.join(workpath, "version_info.txt")
    os.makedirs(workpath, exist_ok=True)
    with open(version_file, "w", encoding="utf-8") as fh:
        fh.write(str(info))

icon = ("termswitch/assets/termswitch.icns" if is_mac
        else "termswitch/assets/termswitch.ico")

if is_mac:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=NAME,
        console=False,
        icon=icon,
        codesign_identity=os.environ.get("CODESIGN_IDENTITY") or None,
        entitlements_file=None,
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=icon,
        bundle_identifier="com.rough-ton.termswitch",
        version=__version__,
        info_plist={
            "CFBundleName": NAME,
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": __version__,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            # Shown in the macOS prompt the first time a terminal is opened.
            "NSAppleEventsUsageDescription":
                "termSwitch opens Terminal or iTerm2 windows set to the AWS profile you choose.",
        },
    )
elif is_win:
    # One folder: termSwitch.exe plus its libraries in _internal.
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=NAME,
        console=False,
        icon=icon,
        version=version_file,
        upx=False,          # UPX-packed binaries are often flagged by antivirus
        strip=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)
else:
    # One file: everything is packed into the executable.
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name=NAME,
        console=False,
        icon=icon,
        version=version_file,
        upx=False,          # UPX-packed binaries are often flagged by antivirus
        strip=False,
        runtime_tmpdir=None,
    )
