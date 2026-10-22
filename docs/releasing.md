# Releases and building

[Back to README](../README.md)

Releases are built by GitHub Actions (`.github/workflows/release.yml`) on Windows and
macOS runners.

**Every merge to `main` publishes a new versioned release.** After the Windows and macOS
builds pass their smoke tests, the workflow:

- creates tag `vX.Y.Z` on the merged commit
- publishes the release with the `.exe`, `.dmg` and `SHA256SUMS.txt`, plus notes listing
  the merged PRs
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

This produces `dist/termSwitch.exe` on Windows and `dist/termSwitch.app` on macOS.

The `.spec` file builds one self-contained file on Windows. macOS apps must be bundles,
and PyInstaller no longer supports one-file `.app` builds, so on macOS the single
download is a `.dmg`.

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

1. `build` makes both downloads on GitHub-hosted runners. The Windows file is uploaded
   as `unsigned-termSwitch-windows-x64.exe`, which never reaches the release.
2. `sign-windows` (Linux runner, `release` environment) sends it to SignPath and waits
   up to an hour for you to approve the request in SignPath.
3. `verify-windows` checks the signature on Windows: valid, signed by SignPath
   Foundation, timestamped, and the app still starts. It then attests the signed file.
4. `publish` runs only if that check passed, computes `SHA256SUMS.txt` from the signed
   file, and names the signer in the release notes.

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
   - Artifact configuration: paste
     [signing/signpath-artifact-configuration.xml](../signing/signpath-artifact-configuration.xml)
     and make it the default.
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

6. **Release as usual.** Approve the signing request in SignPath when it arrives; the
   workflow waits up to an hour.

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
