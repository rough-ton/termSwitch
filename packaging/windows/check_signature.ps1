<#
.SYNOPSIS
  Fail unless each file has a valid, timestamped Authenticode signature from
  SignPath Foundation. Used by the release workflow before anything is published.

.EXAMPLE
  pwsh packaging/windows/check_signature.ps1 dist/termSwitch-windows-x64-setup.exe
#>
param([Parameter(Mandatory, ValueFromRemainingArguments)][string[]]$Path)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

foreach ($file in $Path) {
  $sig = Get-AuthenticodeSignature $file
  Write-Host "== $file"
  $sig | Format-List Status, StatusMessage, SignerCertificate, TimeStamperCertificate | Out-Host
  if ($sig.Status -ne 'Valid') { throw "${file}: signature is $($sig.Status): $($sig.StatusMessage)" }
  if ($sig.SignerCertificate.Subject -notmatch 'SignPath Foundation') {
    throw "${file}: unexpected signer: $($sig.SignerCertificate.Subject)"
  }
  if (-not $sig.TimeStamperCertificate) { throw "${file}: the signature has no timestamp" }
}
