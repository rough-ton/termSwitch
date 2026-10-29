; Inno Setup script for termSwitch's Windows installer.
;
; Build it from the PyInstaller one-folder build (dist\termSwitch), on Windows with
; Inno Setup 6 (the release workflow pins the version):
;
;   pwsh packaging/windows/build_installer.ps1 -Version 1.2.3
;
; which runs: ISCC /DAppVersion=1.2.3 /DAppDir=<dist\termSwitch> /O<dist> termswitch.iss
; and writes dist\termSwitch-windows-x64-setup.exe.
;
; - Installs for the current user by default (%LOCALAPPDATA%\Programs\termSwitch, no
;   admin rights). The first page offers "Install for all users" instead.
; - Installing a newer version over an older one upgrades in place: same folder, same
;   settings. A running termSwitch is closed first, after asking.
; - Uninstalling never touches ~/.aws, ~/.ssh or your saved passwords and settings,
;   unless you choose "Remove my termSwitch settings" (silent: /REMOVEUSERDATA).

#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=1.2.3 termswitch.iss
#endif
#ifndef AppDir
  #define AppDir "..\..\dist\termSwitch"
#endif
#define AppName "termSwitch"
#define AppExe "termSwitch.exe"
#define SetupName "termSwitch-windows-x64-setup"

[Setup]
; Never change AppId: Windows uses it to find the installed copy for upgrades and
; uninstalling.
AppId={{B617AD00-0E53-4AAB-9696-C5E1CAFAD7C5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=rough-ton
AppPublisherURL=https://github.com/rough-ton/termSwitch
AppSupportURL=https://github.com/rough-ton/termSwitch/issues
AppUpdatesURL=https://github.com/rough-ton/termSwitch/releases
AppCopyright=Copyright (c) 2026 Mike Roughton. MIT License.
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoCompany=rough-ton
VersionInfoDescription={#AppName} Setup
VersionInfoOriginalFileName={#SetupName}.exe
; Per user by default; the first page offers all users (which asks for admin rights).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
; 64-bit Windows 10 1809 or later: the embedded terminals need ConPTY.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
LicenseFile=..\..\LICENSE
SetupIconFile=..\..\termswitch\assets\termswitch.ico
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
; Close a running termSwitch before replacing its files (the wizard asks first).
CloseApplications=yes
RestartApplications=no
Compression=lzma2/max
SolidCompression=yes
OutputDir=..\..\dist
OutputBaseFilename={#SetupName}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An upgrade replaces the bundled libraries wholesale, so nothing stale is left behind.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; The same AppUserModelID as the app (termswitch/icon.py), so the taskbar groups them.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "rough-ton.termSwitch"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "rough-ton.termSwitch"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
Type: dirifempty; Name: "{app}"

[Code]
var
  RemoveUserData: Boolean;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if UninstallSilent() then
    RemoveUserData := Pos('/REMOVEUSERDATA', Uppercase(GetCmdTail())) > 0
  else
    RemoveUserData := MsgBox(
      'Remove my termSwitch settings too?' + #13#10 + #13#10 +
      'Yes also deletes termSwitch''s settings, saved SSH sessions, their saved ' +
      'passwords and its caches.' + #13#10 + #13#10 +
      'No keeps them for a later reinstall.' + #13#10 + #13#10 +
      'Your AWS config (.aws) and SSH keys and config (.ssh) are never touched.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if (CurUninstallStep <> usUninstall) or not RemoveUserData then
    Exit;
  // The app removes its own data, including saved passwords in Credential Manager
  // (termswitch/userdata.py). It runs before its files are removed.
  if not Exec(ExpandConstant('{app}\{#AppExe}'), '--remove-user-data', '',
              SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
    Log('termSwitch --remove-user-data did not finish; removing folders directly.');
  // In case the app could not run: the settings and folders (not the passwords).
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\rough-ton\termSwitch');
  RegDeleteKeyIfEmpty(HKCU, 'Software\rough-ton');
  DelTree(ExpandConstant('{userappdata}\rough-ton\termSwitch'), True, True, True);
  RemoveDir(ExpandConstant('{userappdata}\rough-ton'));
  DelTree(ExpandConstant('{localappdata}\termSwitch'), True, True, True);
end;
