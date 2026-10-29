<#
.SYNOPSIS
  Smoke test the Windows installer the way a user would meet it, silently:

  1. Install for the current user; check the version Windows records, the Start menu
     shortcut, no desktop shortcut (unchecked by default), and that the app starts.
  2. Install again over it (upgrade in place).
  3. Uninstall: the install folder goes, termSwitch's settings stay.
  4. Install, then uninstall with /REMOVEUSERDATA: termSwitch's settings go too, and
     ~/.aws is never touched.

  -RequireSignature also checks that the installed termSwitch.exe is signed.

.EXAMPLE
  pwsh packaging/windows/test_installer.ps1 -Setup dist/termSwitch-windows-x64-setup.exe -Version 0.7.0
#>
param(
  [Parameter(Mandatory)][string]$Setup,
  [Parameter(Mandatory)][string]$Version,
  [switch]$RequireSignature
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$AppId = '{B617AD00-0E53-4AAB-9696-C5E1CAFAD7C5}'     # packaging/windows/termswitch.iss
$Setup = (Resolve-Path $Setup).Path
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\termSwitch'
$Exe = Join-Path $InstallDir 'termSwitch.exe'
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\${AppId}_is1"
$StartMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'termSwitch.lnk'
$Desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) 'termSwitch.lnk'
$DataDir = Join-Path $env:APPDATA 'rough-ton\termSwitch'
$Logs = Join-Path ($env:RUNNER_TEMP ?? $env:TEMP) 'termswitch-installer-logs'
New-Item -ItemType Directory -Force $Logs | Out-Null

function Step($text) { Write-Host "`n== $text" }

function Get-LogTail($log) {
  if (Test-Path $log) { (Get-Content $log | Select-Object -Last 3) -join ' | ' } else { 'no log' }
}

function Install($name) {
  $log = Join-Path $Logs "$name.log"
  $p = Start-Process -FilePath $Setup -PassThru -Wait -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/CURRENTUSER', '/NORESTART', "/LOG=`"$log`"")
  if ($p.ExitCode -ne 0) { Get-Content $log | Out-Host; throw "Setup exited $($p.ExitCode): $(Get-LogTail $log)" }
  if (-not (Test-Path $Exe)) { Get-Content $log | Out-Host; throw "$Exe was not installed: $(Get-LogTail $log)" }
}

function Uninstall($name, [string[]]$extra = @()) {
  $uninstaller = (Get-ItemProperty $UninstallKey).UninstallString.Trim('"')
  $log = Join-Path $Logs "$name.log"
  $p = Start-Process -FilePath $uninstaller -PassThru -Wait -ArgumentList (@(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$log`"") + $extra)
  if ($p.ExitCode -ne 0) { throw "Uninstall exited $($p.ExitCode)" }
  # The uninstaller hands over to a copy of itself in TEMP; wait for it to finish.
  $deadline = (Get-Date).AddSeconds(120)
  while ((Test-Path $InstallDir) -or (Test-Path $UninstallKey)) {
    if ((Get-Date) -gt $deadline) {
      Get-ChildItem $Logs -Filter "$name*" | Get-Content | Out-Host
      throw "Uninstall did not remove $InstallDir"
    }
    Start-Sleep -Milliseconds 500
  }
}

function Run-App([string[]]$arguments) {
  $p = Start-Process -FilePath $Exe -ArgumentList $arguments -Wait -PassThru -NoNewWindow
  return $p.ExitCode
}

try {
  Step 'Install for the current user'
  Install 'install'
  $entry = Get-ItemProperty $UninstallKey
  if ($entry.DisplayVersion -ne $Version) { throw "Installed version is $($entry.DisplayVersion), expected $Version" }
  if ($entry.DisplayName -ne 'termSwitch') { throw "Apps list shows '$($entry.DisplayName)'" }
  if (-not (Test-Path $StartMenu)) { throw "No Start menu shortcut at $StartMenu" }
  if (Test-Path $Desktop) { throw 'A desktop shortcut was created, but it should be unchecked by default' }
  if ((Run-App '--version') -ne 0) { throw 'termSwitch --version failed' }
  # Informational: the runner has no AWS config, so warnings are expected.
  Run-App '--check', '--offline' | Out-Null
  if ($RequireSignature) { & (Join-Path $PSScriptRoot 'check_signature.ps1') $Exe }

  Step 'Install again over it (upgrade in place)'
  New-Item -ItemType Directory -Force $DataDir | Out-Null
  Set-Content (Join-Path $DataDir 'installer-test.txt') 'keep me'
  Install 'reinstall'
  if ((Run-App '--version') -ne 0) { throw 'termSwitch --version failed after reinstalling' }

  Step 'Uninstall, keeping settings'
  Uninstall 'uninstall'
  if (-not (Test-Path (Join-Path $DataDir 'installer-test.txt'))) { throw 'Uninstall removed settings it should keep' }
  if (Test-Path $StartMenu) { throw 'The Start menu shortcut is still there' }

  Step 'Install, then uninstall with /REMOVEUSERDATA'
  $aws = Join-Path $HOME '.aws'
  $guard = Join-Path $aws 'termswitch-installer-guard'
  New-Item -ItemType Directory -Force $aws | Out-Null
  Set-Content $guard 'never removed'
  Install 'install-2'
  Uninstall 'uninstall-remove-data' @('/REMOVEUSERDATA')
  if (Test-Path $DataDir) { throw "/REMOVEUSERDATA left $DataDir" }
  if (-not (Test-Path $guard)) { throw '/REMOVEUSERDATA touched ~/.aws' }
  Remove-Item $guard

  Step 'Installer smoke test passed'
}
catch {
  # In GitHub Actions, show the reason on the run's summary page too.
  if ($env:GITHUB_ACTIONS) { Write-Host "::error title=Installer smoke test::$($_.Exception.Message)" }
  throw
}
finally {
  if (Test-Path $UninstallKey) {
    try { Uninstall 'cleanup' } catch { Write-Warning $_ }
  }
}
