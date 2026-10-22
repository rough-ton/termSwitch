<p align="center">
  <img src="termswitch/assets/termswitch-128.png" alt="termSwitch icon" width="96" height="96">
</p>

<h1 align="center">termSwitch</h1>

<p align="center"><em>Flip the switch, not the profile.</em></p>

<p align="center">
  <a href="https://github.com/rough-ton/termSwitch/actions/workflows/ci.yml"><img src="https://github.com/rough-ton/termSwitch/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/rough-ton/termSwitch/releases/latest"><img src="https://img.shields.io/github/v/release/rough-ton/termSwitch" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-informational" alt="Windows and macOS">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license"></a>
</p>

termSwitch is a desktop app for Windows and macOS that keeps your AWS profiles in a tree,
grouped the way you work. Double-click a profile and a terminal tab opens inside the app,
already set to that profile.

![The termSwitch main window](docs/images/main-window.png)

## Features

- **Grouped profiles.** Drag profiles into nested groups. Groups are saved in
  `~/.aws/config`, so they follow the file to any machine.
- **Embedded terminals.** One tab per profile, with PowerShell on Windows and your login
  shell on macOS. Full-screen programs, scrollback and colored tabs work as expected.
- **Commands pane.** Browse every AWS CLI command your installed CLI supports, fill in a
  parameter form, then run, insert or copy the result. Star the ones you use most.
- **Credential status and SSO.** Status dots show which profiles have valid credentials,
  and SSO logins start automatically when needed.
- **Production safeguards.** Production profiles show in red, and termSwitch asks before
  opening them or running commands that change something.
- **Security first.** termSwitch never holds or writes secrets, scrubs stray AWS keys
  from every shell, and checks its dependencies and your config at every launch.

## Download

Get the latest build from the
[Releases page](https://github.com/rough-ton/termSwitch/releases/latest). Every merge to
`main` publishes a new release.

| Platform | File |
| --- | --- |
| Windows 10/11 (x64) | `termSwitch-windows-x64.exe`: a single file, no installer |
| macOS 12+ (Apple Silicon) | `termSwitch-macos-arm64.dmg`: drag termSwitch to Applications |

Each release includes `SHA256SUMS.txt` for verifying downloads. The builds are not
code-signed yet, so Windows SmartScreen and macOS Gatekeeper warn on first launch.

<details>
<summary>Verifying a download and getting past the first-launch warning</summary>

```powershell
# Windows: compare with SHA256SUMS.txt
(Get-FileHash .\termSwitch-windows-x64.exe -Algorithm SHA256).Hash
```

```bash
# macOS: compare with SHA256SUMS.txt, then clear the quarantine flag
shasum -a 256 termSwitch-macos-arm64.dmg
xattr -dr com.apple.quarantine /Applications/termSwitch.app
```

On Windows, click **More info > Run anyway** in the SmartScreen prompt. On macOS, you can
also right-click the app and choose **Open**.

</details>

## Quick start

1. Install the [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
   (2.9 or newer). Everything else is bundled.
2. Download and open termSwitch. Your profiles from `~/.aws/config` appear in the tree.
3. Drag profiles into groups, then double-click one to open an authenticated terminal.

To try it without touching your real config, set `AWS_CONFIG_FILE` to another file
before launching. Running from source is covered in [Development](docs/development.md).

## Documentation

| Guide | What it covers |
| --- | --- |
| [Using termSwitch](docs/usage.md) | Groups, terminal tabs, shortcuts, the Commands pane and parameter form |
| [Security model](docs/security.md) | How credentials, processes, config writes and dependencies are protected |
| [Dependency check](docs/dependency-check.md) | What the launch check looks for and how to run it from the command line |
| [Releases and building](docs/releasing.md) | Versioning, the release workflow, local builds and code signing |
| [Development](docs/development.md) | Running from source, tests, dependency locks and project layout |

## Contributing

Issues and pull requests are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md)
first. To report a security problem, follow [SECURITY.md](SECURITY.md) instead of
opening a public issue.

## Credits

Icons from the [Material Icon Theme](https://github.com/material-extensions/vscode-material-icon-theme)
by Philipp Kief and Material Extensions, used under the MIT license. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

termSwitch is released under the [MIT license](LICENSE). The downloads bundle
third-party software under its own licenses, including Qt for Python under the LGPL 3.0;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), or Help > Licenses in the app.
