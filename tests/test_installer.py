"""The Windows installer (packaging/windows): what it installs, how it upgrades and
uninstalls, and that the release workflow builds and tests it. Read as text: the
installer itself is built and exercised on Windows by the release workflow."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIN = ROOT / "packaging" / "windows"
ISS = (WIN / "termswitch.iss").read_text(encoding="utf-8")
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def setup_value(key: str) -> str:
    m = re.search(rf"^{key}=(.*)$", ISS, re.MULTILINE)
    assert m, key
    return m.group(1).strip()


def app_id() -> str:
    return setup_value("AppId").replace("{{", "{")


def test_app_id_is_fixed_and_shared_with_the_smoke_test():
    assert re.fullmatch(r"\{[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}\}", app_id())
    test = (WIN / "test_installer.ps1").read_text(encoding="utf-8")
    assert f"$AppId = '{app_id()}'" in test


def test_installs_per_user_by_default_with_an_all_users_option():
    assert setup_value("PrivilegesRequired") == "lowest"
    assert setup_value("PrivilegesRequiredOverridesAllowed") == "dialog"
    assert setup_value("DefaultDirName") == r"{autopf}\{#AppName}"
    assert setup_value("ArchitecturesAllowed") == "x64compatible"
    assert setup_value("ArchitecturesInstallIn64BitMode") == "x64compatible"


def test_version_is_stamped_by_the_build():
    assert "#ifndef AppVersion\n  #error" in ISS
    assert setup_value("AppVersion") == "{#AppVersion}"
    assert setup_value("VersionInfoVersion") == "{#AppVersion}"
    assert setup_value("OutputBaseFilename") == "{#SetupName}"


def test_wizard_shows_name_icon_and_license():
    assert setup_value("LicenseFile") == r"..\..\LICENSE"
    assert (WIN / "../../LICENSE").resolve().is_file()
    assert setup_value("SetupIconFile") == r"..\..\termswitch\assets\termswitch.ico"
    assert (ROOT / "termswitch" / "assets" / "termswitch.ico").is_file()
    assert setup_value("UninstallDisplayIcon") == r"{app}\{#AppExe}"
    assert setup_value("AppName") == "{#AppName}"
    assert '#define AppName "termSwitch"' in ISS


def test_shortcuts_start_menu_always_desktop_unchecked():
    assert re.search(r'^Name: "\{autoprograms\}\\\{#AppName\}"; Filename: "\{app\}\\\{#AppExe\}"',
                     ISS, re.MULTILINE)
    assert re.search(r'^Name: "desktopicon";.*Flags: unchecked$', ISS, re.MULTILINE)
    assert re.search(r'^Name: "\{autodesktop\}.*Tasks: desktopicon$', ISS, re.MULTILINE)
    from termswitch.icon import APP_ID
    assert ISS.count(f'AppUserModelID: "{APP_ID}"') == 2


def test_launch_at_end_is_unchecked():
    run = re.search(r"^\[Run\]\n(.*?)\n\n", ISS, re.MULTILINE | re.DOTALL).group(1)
    assert "postinstall" in run and "unchecked" in run and "skipifsilent" in run


def test_upgrade_closes_the_app_after_asking():
    assert setup_value("CloseApplications") == "yes"
    assert setup_value("RestartApplications") == "no"
    assert 'Type: filesandordirs; Name: "{app}\\_internal"' in ISS


def test_uninstall_keeps_user_data_unless_asked():
    code = ISS.split("[Code]", 1)[1]
    assert "MB_YESNO or MB_DEFBUTTON2" in code          # the default answer is No
    assert "'/REMOVEUSERDATA'" in code and "UninstallSilent()" in code
    assert "'--remove-user-data'" in code
    # Only termSwitch's own places; never ~/.aws, ~/.ssh or a whole profile folder.
    removed = re.findall(r"(?:DelTree|RemoveDir|RegDelete\w+)\((HKCU|ExpandConstant\('[^']*'\)|.)",
                         code)
    assert removed == [
        "HKCU", "HKCU",
        "ExpandConstant('{userappdata}\\rough-ton\\termSwitch')",
        "ExpandConstant('{userappdata}\\rough-ton')",
        "ExpandConstant('{localappdata}\\termSwitch')",
    ]
    assert "'Software\\rough-ton\\termSwitch'" in code
    assert ".aws" not in code.replace("(.aws)", "") and ".ssh" not in code.replace("(.ssh)", "")


def test_uninstall_paths_match_the_app():
    from termswitch import userdata
    assert userdata.ORGANIZATION == "rough-ton" and userdata.APPLICATION == "termSwitch"
    main = (ROOT / "termswitch" / "__main__.py").read_text(encoding="utf-8")
    assert "app.setOrganizationName(userdata.ORGANIZATION)" in main
    aws_commands = (ROOT / "termswitch" / "aws_commands.py").read_text(encoding="utf-8")
    assert 'return base / "termSwitch" / "cache"' in aws_commands


def test_spec_builds_one_folder_on_windows():
    spec = (ROOT / "termswitch.spec").read_text(encoding="utf-8")
    win = spec.split("elif is_win:", 1)[1].split("\nelse:", 1)[0]
    assert "exclude_binaries=True" in win and "COLLECT(" in win
    assert "version=version_file" in win and "upx=False" in win


def test_release_builds_and_smoke_tests_the_installer():
    assert re.search(r'^  INNO_SETUP_VERSION: "\d+\.\d+\.\d+"', RELEASE, re.MULTILINE)
    assert RELEASE.count('choco install innosetup --version "$env:INNO_SETUP_VERSION"') == 2
    assert RELEASE.count("-InnoVersion $env:INNO_SETUP_VERSION") == 2
    build = RELEASE.split("\n  build:\n", 1)[1].split("\n  sign-windows-app:\n", 1)[0]
    assert "files: dist/termSwitch-windows-x64-*" in build
    assert "test_installer.ps1 -Setup dist/termSwitch-windows-x64-setup.exe" in build
    test = (WIN / "test_installer.ps1").read_text(encoding="utf-8")
    for arg in ("'/VERYSILENT'", "'/SUPPRESSMSGBOXES'", "'/CURRENTUSER'", "'/REMOVEUSERDATA'"):
        assert arg in test, arg
    assert "Programs\\termSwitch" in test
    build_ps = (WIN / "build_installer.ps1").read_text(encoding="utf-8")
    assert "termSwitch-windows-x64-portable.zip" in build_ps
    assert '"/DAppVersion=$Version"' in build_ps
