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

The builds are not signed yet. Signing is tracked in
[issue #14](https://github.com/rough-ton/termSwitch/issues/14). The plan:

- macOS: sign with an Apple Developer ID (set `CODESIGN_IDENTITY` before running
  PyInstaller), then notarize with `notarytool` and staple the ticket to the `.dmg`.
- Windows: sign the `.exe` with Azure Artifact Signing.

Until then, see the first-launch notes in the [README](../README.md#download).

## Icons

To regenerate the icons after editing `termswitch/assets/termswitch.svg`:

```bash
pip install pillow
python tools/build_icons.py
```
