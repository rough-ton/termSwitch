# Releases and building

[Back to README](../README.md)

Releases are built by GitHub Actions (`.github/workflows/release.yml`) on Windows and
macOS runners.

**Every merge to `main` publishes a new versioned release.** After the Windows and macOS
builds pass their smoke tests, the workflow:

- creates tag `vX.Y.Z` on the merged commit
- publishes the release with the Windows installer and portable `.zip`, the macOS `.dmg`
  and `SHA256SUMS.txt`, plus notes listing the merged PRs
- marks it as the latest release
- attests build provenance (public repos only)

## Version numbers

- `BASE_VERSION` in `termswitch/__init__.py` sets `MAJOR.MINOR`. Raise it yourself when a
  release adds features, for example `0.6.0` to `0.7.0`.
- The patch number is automatic: one more than the highest `vMAJOR.MINOR.*` tag
  (`tools/next_version.py`). The first merge after a bump releases `vMAJOR.MINOR.0`.
- The release build stamps the exact version and the short commit into the app
  (`termswitch/_version.py`, not committed), so About, `--version` and the in-app update
  check match the release. Source checkouts report `BASE_VERSION (source)` in About.
- Releases run one at a time, so two quick merges never get the same number.
- Don't push `v*` tags by hand. The workflow creates them.

## Building without publishing

Run the release workflow by hand from the Actions tab (Release > Run workflow). The files appear as run artifacts. With the GitHub CLI:

```bash
gh workflow run release.yml
```

## Building locally

On the platform you are on:

```bash
python -m pip install --require-hashes -r requirements.lock -r requirements-build.lock
pyinstaller --noconfirm --clean termswitch.spec
```

This produces `dist/termSwitch/termSwitch.exe` (one folder) on Windows and
`dist/termSwitch.app` on macOS.

On Windows the folder becomes an installer and a portable `.zip`. On macOS, apps must be
bundles and PyInstaller no longer supports one-file `.app` builds, so the single download
is a `.dmg`.

### The Windows installer

`packaging/windows/termswitch.iss` is an [Inno Setup](https://jrsoftware.org/isinfo.php)
script (free, open source). With Inno Setup 6 installed, after the PyInstaller build:

```powershell
pwsh packaging/windows/build_installer.ps1 -Version 0.7.0
pwsh packaging/windows/test_installer.ps1 -Setup dist/termSwitch-windows-x64-setup.exe -Version 0.7.0
```

The first writes `dist/termSwitch-windows-x64-setup.exe` and
`dist/termSwitch-windows-x64-portable.zip`. The second is the release workflow's smoke
test: it installs silently for the current user, checks the version, shortcuts and that
the app starts, installs again over it, uninstalls (settings kept), then installs and
uninstalls with `/REMOVEUSERDATA` (settings removed, `~/.aws` untouched). It installs into
your own profile, so run it on a test machine or a runner.

What the installer does:

- Installs for the current user by default, in `%LOCALAPPDATA%\Programs\termSwitch`
  with no admin rights. The first page offers "all users" (Program Files, needs admin).
- Adds a Start menu shortcut; a desktop shortcut is offered, unchecked. "Launch
  termSwitch" at the end is unchecked.
- A newer version installs over the old one in place and keeps settings. If termSwitch is
  running, the installer offers to close it first.
- Uninstalling removes the program. It asks whether to remove your termSwitch settings
  too (default No); Yes runs `termSwitch.exe --remove-user-data`, which deletes the
  settings, saved SSH sessions, their saved passwords in Credential Manager and the
  caches. `~/.aws`, `~/.ssh` and the config backups next to `~/.aws/config` are never
  touched. Silent uninstall keeps settings unless `/REMOVEUSERDATA` is passed.

Silent install and uninstall, for scripted setups:

```powershell
.\termSwitch-windows-x64-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER
& "$env:LOCALAPPDATA\Programs\termSwitch\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES
```

Never change `AppId` in the script: Windows uses it to find the installed copy for
upgrades and uninstalling. The release workflow pins the Inno Setup version
(`INNO_SETUP_VERSION`); raise it on purpose.

### The macOS install window

The `.dmg` opens to a branded install window: the termSwitch title and instructions,
the app on the left, an arrow, and the Applications folder on the right. To build it
from the `.app`:

```bash
python -m dmgbuild -s packaging/dmg/settings.py -D app=dist/termSwitch.app \
  termSwitch dist/termSwitch-macos-arm64.dmg
```

To check a built image the way the release workflow does:

```bash
mnt=$(mktemp -d) && hdiutil attach -nobrowse -readonly -mountpoint "$mnt" dist/termSwitch-macos-arm64.dmg
python tools/check_dmg.py "$mnt"; hdiutil detach "$mnt"
```

The window layout lives in `packaging/dmg/layout.json`: window size, icon positions,
icon and label sizes, and the text. After changing it, redraw the background (it reads
the same file, so icons stay on their spots) and look at the preview, which shows the
icons and Finder's labels in light and dark mode:

```bash
python tools/make_dmg_background.py --preview dmg-preview.png
```

Finder draws each icon's name in black in light mode and white in dark mode, and a
`.dmg` can't change that, so the background puts a mid-grey pill behind each name that
reads with either.

The `.app` includes the Automation usage string shown in the "termSwitch wants to
control Terminal" prompt. UPX compression is off because antivirus tools often flag it.

## Code signing

Windows downloads are signed for free by SignPath Foundation, through its program for
open source projects. macOS downloads are not signed: Gatekeeper only accepts Apple's
Developer ID certificates, which need the paid Apple Developer Program. What is signed
and how is public in the [code signing policy](code-signing-policy.md).

Signing is built into the [release workflow](../.github/workflows/release.yml) and stays
off until the `SIGNPATH_ORGANIZATION_ID` repository variable is set. With it off, releases
are published unsigned, as before.

### How a signed release runs

Windows signing takes **two approvals** in SignPath per release: the program, then the
installer built around it, so both the installer and the installed `termSwitch.exe` are
signed.

1. `build` makes both platforms' downloads on GitHub-hosted runners and smoke tests the
   (unsigned) installer. The Windows build is uploaded as staging artifacts
   (`unsigned-windows-exe`, `windows-app-folder`) that never reach the release.
2. `sign-windows-app` (Linux runner, `release` environment) sends `termSwitch.exe` to
   SignPath (artifact configuration `windows-app`) and waits up to an hour for your
   approval.
3. `package-windows` checks that signature, puts the signed exe in the app folder, and
   builds and smoke tests the installer and portable `.zip`.
4. `sign-windows-setup` sends the installer to SignPath (artifact configuration
   `windows-setup`) and waits for your second approval.
5. `verify-windows` checks on Windows that the installer, the exe in the portable `.zip`
   and the installed exe are all validly signed by SignPath Foundation and timestamped,
   runs the install smoke test again, and attests both files.
6. `publish` runs only if that check passed, computes `SHA256SUMS.txt` from the signed
   files, and names the signer in the release notes.

The uninstaller (`unins000.exe`), which Inno Setup writes at install time, is not
signed.

If signing or the check fails, nothing is published. Re-run the failed jobs once it's
fixed.

### Turning it on

SignPath Foundation signs only open source projects whose source is public, so this
follows the repo going public.

1. **Before going public:** scan the history for secrets, then make the repo public.

   ```bash
   docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:latest git /repo
   gh repo edit rough-ton/termSwitch --visibility public --accept-visibility-change-consequences
   ```

   Once public, the release workflow's build attestations switch on by themselves, and
   anyone can check a download with `gh attestation verify`.

2. **Turn on MFA** for GitHub, and later for SignPath. SignPath requires it for everyone
   on the project.

3. **Apply** at [signpath.org/apply](https://signpath.org/apply). The README's "Code
   signing policy" section and [code-signing-policy.md](code-signing-policy.md) are the
   policy page the application asks for.

4. **Set up the SignPath project** once approved:
   - Project slug `termSwitch`, linked to the predefined **GitHub.com** trusted build
     system.
   - Two artifact configurations: paste
     [signing/signpath-windows-app.xml](../signing/signpath-windows-app.xml) with the slug
     `windows-app`, and
     [signing/signpath-windows-setup.xml](../signing/signpath-windows-setup.xml) with the
     slug `windows-setup`.
   - Signing policy slug `release-signing`, with manual approval.
   - A CI user with the submitter role, and an API token for it.

5. **Give the workflow access** with a `release` environment that only `main` can use:

   ```bash
   gh api -X PUT repos/rough-ton/termSwitch/environments/release \
     -F 'deployment_branch_policy[protected_branches]=false' \
     -F 'deployment_branch_policy[custom_branch_policies]=true'
   gh api -X POST repos/rough-ton/termSwitch/environments/release/deployment-branch-policies \
     -f name=main -f type=branch
   gh secret set SIGNPATH_API_TOKEN --env release -R rough-ton/termSwitch      # paste the token
   gh variable set SIGNPATH_ORGANIZATION_ID -R rough-ton/termSwitch --body '<organization id>'
   ```

   Only if you chose other slugs in SignPath:

   ```bash
   gh variable set SIGNPATH_PROJECT_SLUG -R rough-ton/termSwitch --body '<project slug>'
   gh variable set SIGNPATH_POLICY_SLUG -R rough-ton/termSwitch --body '<policy slug>'
   ```

6. **Release as usual.** Approve the two signing requests in SignPath as they arrive
   (the program, then the installer); the workflow waits up to an hour for each.

7. **Update the docs:** remove "is applying" from the README's Code signing policy
   section and the status note in [code-signing-policy.md](code-signing-policy.md), and
   the Windows first-launch step from the README.

To turn signing off again: `gh variable delete SIGNPATH_ORGANIZATION_ID -R rough-ton/termSwitch`.

A newly signed app can still show a SmartScreen warning for its first downloads, until
Microsoft's reputation service has seen enough of them. That applies to every
certificate, paid or free.

### macOS later

Signing and notarizing for macOS needs the Apple Developer Program ($99 a year). The
build already reads `CODESIGN_IDENTITY` (see `termswitch.spec`); notarizing would add
`xcrun notarytool submit --wait` and `xcrun stapler staple` to the release workflow.
Until then the README explains the first-launch steps for each macOS version.

## Icons

To regenerate the icons after editing `termswitch/assets/termswitch.svg`:

```bash
pip install pillow
python tools/build_icons.py
```
