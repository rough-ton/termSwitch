<#
.SYNOPSIS
  Build the Windows downloads from the PyInstaller one-folder build:
    dist\termSwitch-windows-x64-setup.exe      (the installer)
    dist\termSwitch-windows-x64-portable.zip   (the same files, no installer)

.EXAMPLE
  pyinstaller --noconfirm --clean termswitch.spec
  pwsh packaging/windows/build_installer.ps1 -Version 0.7.0

  Needs Inno Setup 6 (https://jrsoftware.org/isdl.php). The release workflow installs
  the version pinned there.
#>
param(
  [Parameter(Mandatory)][string]$Version,
  [string]$AppDir = 'dist/termSwitch',
  [string]$OutDir = 'dist',
  # Fail unless Inno Setup is this version (the release workflow pins it).
  [string]$InnoVersion
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version must look like 1.2.3, not '$Version'" }
$app = (Resolve-Path $AppDir).Path
if (-not (Test-Path (Join-Path $app 'termSwitch.exe'))) {
  throw "$app\termSwitch.exe not found: run pyinstaller termswitch.spec first"
}
New-Item -ItemType Directory -Force $OutDir | Out-Null
$out = (Resolve-Path $OutDir).Path

$iscc = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 (ISCC.exe) not found: https://jrsoftware.org/isdl.php' }
# ISCC.exe itself reports 0.0.0.0, so read the version from the compiler library or
# Compil32.exe beside it, then from Inno Setup's entry in Apps.
$found = $null
foreach ($file in 'ISCmplr.dll', 'Compil32.exe', 'ISCC.exe') {
  $path = Join-Path (Split-Path $iscc) $file
  if (Test-Path $path) {
    $v = (Get-Item $path).VersionInfo
    foreach ($candidate in $v.ProductVersion, $v.FileVersion) {
      if ("$candidate" -match '^\d+\.\d+\.\d+' -and "$candidate" -notmatch '^0\.0\.0') { $found = "$candidate"; break }
    }
  }
  if ($found) { break }
}
if (-not $found) {
  $found = Get-ItemProperty -ErrorAction SilentlyContinue -Path @(
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1'
  ) | Select-Object -ExpandProperty DisplayVersion -First 1
}
Write-Host "Inno Setup: $iscc ($found)"
if ($InnoVersion -and -not "$found".StartsWith($InnoVersion)) {
  throw "Inno Setup $InnoVersion is required, found $found"
}

$script = Join-Path $PSScriptRoot 'termswitch.iss'
$compile = & $iscc "/DAppVersion=$Version" "/DAppDir=$app" "/O$out" $script 2>&1
$compile | Out-Host
if ($LASTEXITCODE -ne 0) {
  # ISCC prints the failing line near the end; carry it into the error.
  $why = ($compile | Where-Object { "$_".Trim() } | Select-Object -Last 4) -join ' | '
  throw "ISCC failed with exit code ${LASTEXITCODE}: $why"
}

# The portable download: the same folder, unpacked anywhere and run in place.
$zip = Join-Path $out 'termSwitch-windows-x64-portable.zip'
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path $app -DestinationPath $zip -CompressionLevel Optimal

Get-Item (Join-Path $out 'termSwitch-windows-x64-setup.exe'), $zip | Select-Object Name, Length
