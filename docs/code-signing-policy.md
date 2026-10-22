# Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/).

> **Status:** termSwitch is applying to SignPath Foundation's free signing program for
> open source projects. Until it's approved, releases are not signed and their notes
> say so. This page describes how signing works once it's on.

## What is signed

| Download | Signed | By |
| --- | --- | --- |
| `termSwitch-windows-x64.exe` | Yes (Authenticode, timestamped) | SignPath Foundation |
| `termSwitch-macos-arm64.dmg` | No: Apple only accepts its own paid Developer ID certificates | |

Only release builds are signed:

- Built by the [release workflow](../.github/workflows/release.yml) from a commit on
  `main` of this repository, on GitHub-hosted runners. Pull request and CI builds are
  never signed.
- SignPath verifies that the file came from that workflow run before signing it.
- Each signing request is approved by hand in SignPath. The workflow then checks the
  signature on Windows and stops the release if it isn't valid.
- The signed file is what's published, and `SHA256SUMS.txt` is computed from it.

The signature shows the publisher as **SignPath Foundation**. termSwitch's name and
version are in the file's details (right-click > Properties > Details).

## Team roles

| Role | Who |
| --- | --- |
| Committers and reviewers | [Mike Roughton (rough-ton)](https://github.com/rough-ton) |
| Approvers | [Mike Roughton (rough-ton)](https://github.com/rough-ton) |

Every team member uses multi-factor authentication for GitHub and SignPath.

## Privacy policy

termSwitch never sends information to its author, and has no telemetry, analytics or
crash reporting. The only connections it makes are below; the ones that run on their
own can be turned off:

| Connection | When | What is sent |
| --- | --- | --- |
| `api.github.com` (latest termSwitch release) | At launch, as part of the dependency check, and when you open About | Nothing about you: a version lookup. Off under Settings > Dependencies. |
| `pypi.org` and `raw.githubusercontent.com` (latest versions of bundled packages and the AWS CLI) | At launch, as part of the dependency check | Nothing about you: version lookups. Off under Settings > Dependencies. |
| AWS, through your installed AWS CLI | At launch when Settings > Profiles > Check credential status on startup is on (the default); when you check credentials, sign in with SSO or run a command | What the AWS CLI sends for that command, using your own credentials |
| SSH servers you saved | When you connect | What the system `ssh` client sends |
| Your web browser | When you open a link, such as an SSO sign-in or AWS documentation | The address you chose |

Version lookups use HTTPS. Profiles, settings, saved SSH sessions and passwords stay on
your computer; saved passwords go only to the macOS Keychain or Windows Credential
Manager.

## Verifying a download

```powershell
# Windows: the signature (once signing is on)
Get-AuthenticodeSignature .\termSwitch-windows-x64.exe | Format-List Status, SignerCertificate
```

```bash
# Any download: built by this repository's release workflow (once the repo is public)
gh attestation verify termSwitch-windows-x64.exe -R rough-ton/termSwitch
gh attestation verify termSwitch-macos-arm64.dmg -R rough-ton/termSwitch

# Any download: the checksum matches the release
shasum -a 256 termSwitch-macos-arm64.dmg     # compare with SHA256SUMS.txt
```

To report a signed file that should not have been signed, follow [SECURITY.md](../SECURITY.md).
