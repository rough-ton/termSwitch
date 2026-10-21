# termSwitch

termSwitch is a small desktop app for macOS and Windows. It shows your AWS profiles in a
tree grouped the way you want. Double-click a profile and it opens a terminal tab, right
inside the app, already set to that profile.

*Flip the switch, not the profile.*

![icon](termswitch/assets/termswitch-128.png)

The icon is a terminal prompt feeding a switch that splits into three tracks. The green,
amber and red ends match the status dots in the app.

## Download

Grab the newest build from the [Releases page](https://github.com/rough-ton/termSwitch/releases/latest).
Every merge to `main` publishes a new versioned release automatically. Each release has:

| Platform | File |
| --- | --- |
| Windows 10/11 (x64) | `termSwitch-windows-x64.exe`. One file, no install, just run it. |
| macOS 12+ (Apple Silicon) | `termSwitch-macos-arm64.dmg`. Open it and drag termSwitch to Applications. |

Every release includes `SHA256SUMS.txt`. Verify your download before running it:

```powershell
# Windows
(Get-FileHash .\termSwitch-windows-x64.exe -Algorithm SHA256).Hash
```

```bash
# macOS
shasum -a 256 termSwitch-macos-arm64.dmg
```

When the repo is public, each file also has a signed build provenance attestation:

```bash
gh attestation verify termSwitch-windows-x64.exe --repo rough-ton/termSwitch
```

The builds are not code-signed yet, so the OS will warn you the first time:

- Windows: SmartScreen shows "Windows protected your PC". Click More info > Run anyway.
- macOS: Gatekeeper blocks the first launch. Right-click the app > Open, or run:

  ```bash
  xattr -dr com.apple.quarantine /Applications/termSwitch.app
  ```

The Windows exe unpacks itself to a temp folder on launch, so it takes a second or two
to start.

You still need the AWS CLI v2 installed. Everything else is bundled.

## Features

- **Embedded terminal tabs.** Each profile opens in its own tab next to the profile tree:
  - PowerShell on Windows (through ConPTY), your login shell on macOS
  - colored tabs (red for production)
  - 10,000 lines of scrollback, and full-screen programs such as `less`, `vim` and the
    `aws ... help` pager
  - Shift+double-click, or Ctrl+Shift+T, opens the profile in Windows Terminal or
    iTerm2 instead. Change the default under Settings > Open profiles in.

- **Groups stored in `~/.aws/config`.** They live in an `x_group` key on each profile, so
  they follow the file to any machine. Drag and drop rewrites only the lines it needs to,
  and comments and formatting are kept.
- **Credential status.** Green, orange or red dots come from
  `aws sts get-caller-identity`, which runs in the background.
- **SSO login.** When an SSO profile needs a login, `aws sso login` runs automatically in
  the new terminal. You can also log in from the app. One login refreshes every profile
  on that SSO session.
- **Production safeguards.** Profiles that match a pattern (`prod` by default) show in red
  and get a red tab color. By default, termSwitch asks before opening one.
- **Dependency and security check.** This runs at every launch. It is described below.

## Requirements

- Python 3.11 or newer. 3.10 works, but it reached end of life in October 2026.
- AWS CLI v2.9 or newer.
- Windows: Windows Terminal is recommended; without it, termSwitch opens a PowerShell
  console. It uses PowerShell 7 if installed, otherwise Windows PowerShell 5.1.
- macOS: iTerm2 or Terminal.app.

## Run from source

Install from `requirements.lock`, which pins every package to an exact version and
SHA-256 hash, so pip refuses anything that has been tampered with.

macOS:

```bash
cd termswitch
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m termswitch
```

Windows (PowerShell):

```powershell
cd termswitch
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements.lock
python -m termswitch
```

`requirements.txt` has loose version ranges if you would rather take the newest
releases. Run `pip install .` to get a `termswitch` command, plus `tsw` for the CLI.

To try it without touching your real config, point it at a different file:

```bash
AWS_CONFIG_FILE=~/.aws/config.dummy python -m termswitch
```

In PowerShell: `$env:AWS_CONFIG_FILE = "$HOME\.aws\config.dummy"; python -m termswitch`.

## Dependency check

The check runs in the background at every launch. The status bar shows the result;
click it, or use Help > Check dependencies, to see the full report. The report window
opens on its own when something fails, or when there is a warning (you can turn off
the warning case).

| Check | Error | Warning |
| --- | --- | --- |
| Python | Older than 3.10 | Past end of life |
| PySide6-Essentials, shiboken6, configupdater, pyte, pywinpty (Windows) / ptyprocess (macOS) | Missing or below the minimum | A minor or major version behind PyPI |
| AWS CLI | Missing, v1, or older than 2.9 | A minor version behind the latest v2 release |
| Terminal | No usable terminal (macOS: `osascript` missing) | Windows Terminal not installed |
| AWS config | Does not parse | Missing; writable by other users; owned by someone else; profile names with control characters |
| AWS credentials | | Readable or writable by other users |
| Environment | | Running as admin/root; relative entries in PATH |

Being a patch release behind is reported as info only, because the AWS CLI ships patch
releases almost daily. Every finding includes a fix command, and "Copy fix commands"
puts them all on the clipboard.

The same check runs from the command line and exits 1 on errors:

```bash
python -m termswitch --check            # includes latest-version lookups (or: tsw --check)
python -m termswitch --check --offline  # minimums only, no network
```

The latest-version lookup makes HTTPS requests to `pypi.org/pypi/<package>/json`,
the AWS CLI v2 changelog on `raw.githubusercontent.com`, and the latest termSwitch release on `api.github.com`. Certificates are verified,
responses are size-capped, and nothing is sent except a User-Agent string. You can turn
the lookup off in Settings.

## Security model

**Credentials**

- termSwitch never holds a secret in memory. When it reads the config and credentials
  files, it replaces secret keys and session tokens with `<set>`, and access key IDs
  with `AKIA...1234`.
- It never writes to `~/.aws/credentials`.
- It never handles credentials itself. Terminals get `AWS_PROFILE`, and the AWS CLI
  resolves credentials from there.
- It removes `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`,
  `AWS_PROFILE` and related variables from every process it starts. A stray key in your
  environment can never override the profile you picked.
- Exporting credentials into the shell is off by default. When on, it uses
  `aws configure export-credentials`; those credentials expire and do not refresh.

**Starting programs**

- Embedded shells get the same scrubbed environment as external terminals, plus
  `AWS_PROFILE`. The shell is an absolute path:
  - Windows: `pwsh`, or the system PowerShell
  - macOS: your login shell, checked against `/etc/shells`

- Executables are resolved to absolute paths. PATH lookups skip relative entries and the
  current directory, so a planted `aws` or `wt` in the working directory is never picked
  up.
- On Windows, only `.exe` files are matched. `.cmd` and `.bat` shims are refused because
  `cmd.exe` re-parses their arguments.
- System tools come from fixed locations: `/usr/bin/osascript`, `/usr/bin/open`,
  `%SystemRoot%\System32\...\powershell.exe` and `notepad.exe`.
- Nothing runs through a shell string. Every process gets an argument list.
  - Windows init scripts are passed with `-EncodedCommand`.
  - macOS commands are quoted with `shlex.quote` and passed to AppleScript as arguments,
    not spliced into the script.
- Profile names containing control characters are refused. Those characters could
  otherwise inject terminal escape sequences through the window title.
- Tab colors must be `#RRGGBB`.
- Only `https://` links are opened, such as the SSO start URL. `file:`, `javascript:` and
  plain `http:` are blocked.

**Reading AWS CLI models (Commands pane)**

- Model files are parsed as JSON data only. Nothing in them is executed.
- They are only read from inside the resolved AWS CLI install. A symlink pointing outside
  it is refused.
- Each model is size-limited (64 MB, also after decompression).
- Inserted commands are typed with newlines and control characters removed, and never
  include Enter.

**Writing the config**

- Writes go through `configupdater`, which changes only the affected lines.
- The new text is parsed before it replaces anything, then swapped in atomically: a temp
  file created with mode 0600 in the same directory, then `os.replace`.
- termSwitch refuses to write to anything that is not a regular file, or, on macOS, a
  file owned by another user.
- A symlinked config (dotfiles repo) is written through to its target, so the link
  survives.
- The first write of each session saves a mode-0600
  `config.termswitch-YYYYMMDD-HHMMSS.bak`. The newest 5 are kept.
- Outside changes, such as `aws configure sso` or an editor, are picked up before each
  write, so they are not overwritten.

**Supply chain**

- The dependencies are kept small:
  - `PySide6-Essentials`, not the full PySide6, which adds Qt WebEngine and other large
    modules
  - `configupdater`
  - for terminals: `pyte` (VT emulation), `pywinpty` (Windows) or `ptyprocess` (macOS)
- The terminal is drawn by a native Qt widget, not a browser engine.
- On Windows, termSwitch talks to ConPTY directly. It does not use pywinpty's
  `PtyProcess` helper, which relays output through a localhost TCP socket.
- `requirements.lock`, `requirements-build.lock` and `requirements-dev.lock` pin exact
  versions with hashes for every platform. CI and release builds install only from
  them, with `--require-hashes`. Regenerate after editing a `.in` file:

  ```bash
  pip install uv
  for f in requirements requirements-build requirements-dev; do
    uv pip compile --universal --generate-hashes --python-version 3.12 \
      "$f.in" -o "$f.lock"
  done
  ```

- GitHub Actions are pinned to full commit SHAs. Dependabot opens PRs when they update.
- Workflows run with read-only tokens. Only the publish job can write, and only to
  create the release.

- To audit the installed packages: `pip install pip-audit && pip-audit -r requirements.lock`.

**What it does not protect against.** Anyone who can write your `~/.aws/config` can
already make the AWS CLI run arbitrary commands, through `credential_process`. That is
why the dependency check warns when the file is writable by other users.

## How groups are stored

```ini
[profile acme-prod-admin]
sso_session = acme
sso_account_id = 111111111111
sso_role_name = AdministratorAccess
region = us-west-2
x_group = Acme/Prod
x_color = #c0392b
```

Groups with no profiles yet are kept here so they survive a reload:

```ini
[x-termswitch]
groups = Acme/Sandbox
```

Botocore, the AWS CLI and the SDKs ignore keys and sections they do not recognize.
Profiles without `x_group` appear in italic automatic groups. By default those are
grouped by SSO session or credential type; change it under View > Group untagged
profiles by. Dropping a profile on an automatic group, or on empty space, removes its
`x_group`.

## Commands pane

To the right of the terminal tabs is a **Commands** pane listing every AWS CLI command,
modeled on PowerShell ISE's Commands view.

- **Service:** pick a service, or All. Each entry shows its full name, for example
  `ec2 (Amazon Elastic Compute Cloud)`.
- **Name:** filter as you type. `describe-inst` or `s3 cp` both work. Press Enter to
  jump to the first match.
- **The list:** alphabetical, with letter headers. Hover a command for its description.
- **Double-click:** inserts the bare `aws <service> <command> ` at the prompt.

**Parameter form.** Selecting a command shows a form built from its service model,
like ISE's Show-Command panel:

- **Required:** marked with `*` and listed first. Run and Insert stay disabled until
  they are filled in.
- **Optional:** collapsed when there are many, with a filter box. Each option shows the
  model's description as a tooltip.
- **Field types:**
  - drop-downs for enum values
  - whole-number fields for integers
  - on/off/not-set choices for flags. The CLI's renamed negations are used, for example
    `--no-reboot` / `--reboot`.
  - space-separated lists
  - a JSON or shorthand editor for nested options, with an **Example** button
  - `fileb://` hints for binary input
- **Common:**
  - `--region` (shows the profile's region), `--output`, `--query`
  - `--dry-run`, where the operation supports it
  - pagination options for paginated commands: `--no-paginate`, `--max-items`,
    `--page-size`, `--starting-token`
  - free-form extra arguments
- **Output file:** commands that write their result to a file, such as
  `s3api get-object` and `lambda invoke`, ask for an output file.
- **Live preview:** the **Command** box shows exactly what will be typed, quoted for the
  tab's shell (PowerShell on Windows, POSIX on macOS).
  - In PowerShell, values containing double quotes (such as JSON) are passed as
    `file://` references to private temp files, which are removed when termSwitch
    exits. PowerShell 5.1 and 7 hand embedded quotes to native programs differently, so
    this is the only form that works in both.
- **Buttons:**
  - **Run** types the command and presses Enter.
  - **Insert** types it without pressing Enter.
  - **Copy** copies it.
  - **Docs** opens the AWS CLI reference page.
- **Safety:**
  - Run always asks first for destructive commands: `delete-*`, `terminate-*`,
    `remove-*`, `deregister-*`, `disable-*`, `revoke-*`, `detach-*`, `purge-*`,
    `s3 rm/rb/mv`, and `s3 sync --delete`.
  - On a production profile, Run also asks before any command that changes something.
  - The prompt shows the profile, the account and the exact command, and Cancel is the
    default.
  - Insert never runs anything.
- **Collapsing:**
  - The **X** collapses the pane to a thin "Commands" tab on the right edge; click the
    tab to bring it back.
  - Ctrl+Shift+K toggles the pane, and Ctrl+Shift+F jumps to the Name filter.
  - The pane remembers whether it was open, and its width.

The list comes from **the AWS CLI you have installed**, so it always matches your
version:
- **Source:** CLI v2 ships the botocore service models inside its install, and termSwitch
  reads them from there.
- **Naming:** the CLI's own service and command names are used. `s3api`, `deploy` and
  `configservice` keep their CLI names, and commands the CLI removes are left out.
- **Extra commands:** a curated list adds commands that aren't in the models, such as
  `s3 cp`/`sync`, `sso login`, `configure`, `logs tail`, `eks update-kubeconfig` and
  `ecr get-login-password`.
- **Caching:** the index is cached per CLI version, so later launches are instant.
  **Refresh** rescans, for example after you upgrade the CLI.
- **Cache location:**
  - Windows: `%LOCALAPPDATA%\termSwitch\cache`
  - macOS: `~/Library/Caches/termSwitch`

## Terminal tabs

| Action | Windows | macOS |
| --- | --- | --- |
| Copy | Ctrl+Shift+C, or Ctrl+C with text selected | Cmd+C |
| Paste | Ctrl+V or Ctrl+Shift+V | Cmd+V |
| Close tab | Ctrl+Shift+W | Cmd+W |
| Next / previous tab | Ctrl+Tab / Ctrl+Shift+Tab | same |
| Focus profiles / terminal | Ctrl+1 / Ctrl+2 | same |
| Show / hide Commands | Ctrl+Shift+K | Cmd+Shift+K |
| Find a command | Ctrl+Shift+F | Cmd+Shift+F |
| Scroll back | Shift+PgUp / PgDn, mouse wheel | same |
| Clear scrollback | right-click > Clear scrollback | Cmd+K |

Other keys work as usual:
- Ctrl+C interrupts.
- Ctrl+R searches history.
- Tab completes.

Right-click a tab to duplicate, rename, restart (after the shell exits), or open the same
profile in an external terminal.

Pasting text with more than one line asks first, since each line could run as a command.
Pasted text is stripped of control characters and escape sequences, and is sent as a
bracketed paste when the shell supports it, so it can't inject keystrokes. Clipboard
(OSC 52) and hyperlink (OSC 8) escape sequences from programs are ignored.

Closing a tab that is running a command asks first. On Windows, any live shell counts.
Quitting termSwitch with terminals open asks first, then ends them.

## Using it

| Action | How |
| --- | --- |
| Open terminal tab | Double-click, Enter, Ctrl+T, or right-click (single-click is an option) |
| Open in external terminal | Shift+double-click or Ctrl+Shift+T |
| Open several | Multi-select then Enter, or right-click a group > Open all |
| Move | Drag profiles or groups onto a group |
| New group | Toolbar or Ctrl+Shift+N (`A/B` makes nested groups) |
| Rename / delete group | F2 / right-click > Delete (contents move up one level) |
| Status | Check all (Ctrl+Shift+R), or right-click |
| SSO login | Button or right-click (opens your browser) |
| Tab color | Right-click a profile > Set tab color |

## Releases and building

Releases are built by GitHub Actions (`.github/workflows/release.yml`) on Windows and
macOS runners.

**Every merge to `main` publishes a new versioned release.** After the Windows and macOS
builds pass their smoke tests, the workflow:

- creates tag `vX.Y.Z` on the merged commit
- publishes the release with the `.exe`, `.dmg` and `SHA256SUMS.txt`, plus notes listing
  the merged PRs
- marks it as the latest release
- attests build provenance (public repos only)

How the version is chosen:

- `BASE_VERSION` in `termswitch/__init__.py` sets `MAJOR.MINOR`. Raise it yourself when a
  release adds features, for example `0.6.0` to `0.7.0`.
- The patch number is automatic: one more than the highest `vMAJOR.MINOR.*` tag
  (`tools/next_version.py`). The first merge after a bump releases `vMAJOR.MINOR.0`.
- The release build stamps the exact version into the app (`termswitch/_version.py`,
  not committed), so About, `--version` and the in-app update check match the release.
  Source checkouts report `BASE_VERSION`.
- Releases run one at a time, so two quick merges never get the same number.
- Don't push `v*` tags by hand. The workflow creates them.

To build the downloads without publishing, run it by hand from the Actions tab
(Release > Run workflow). The files appear as run artifacts. With the GitHub CLI:

```bash
gh workflow run release.yml
```

To build locally on the platform you are on:

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

To sign builds later:

- macOS: set `CODESIGN_IDENTITY` to your Developer ID before running PyInstaller, then
  notarize with `notarytool`.
- Windows: sign with `signtool` or Azure Trusted Signing.

To regenerate the icons after editing `termswitch/assets/termswitch.svg`:

```bash
pip install pillow
python tools/build_icons.py
```

## Credits

Icons from the [Material Icon Theme](https://github.com/material-extensions/vscode-material-icon-theme)
by Philipp Kief and Material Extensions, used under the MIT license. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Tests

```bash
python -m pip install --require-hashes -r requirements.lock -r requirements-dev.lock
python -m pytest -q
```

CI runs them on Windows, macOS and Linux with Python 3.12 and 3.13.

## Layout

```
termswitch/
  config_store.py  read profiles, write groups back safely
  launcher.py      safe executable lookup and terminal commands
  status.py        background sts checks and SSO logins
  deps.py          dependency and security checks (--check)
  depsui.py        dependency report window
  icon.py          app icon, Windows taskbar identity
  ui.py            main window, drag-and-drop tree, settings
  terminal_tabs.py embedded terminal tabs
  terminal_widget.py  terminal view: painting, keys, selection, clipboard
  terminal_core.py screen model (scrollback, alternate screen), key encoding, paste safety
  pty_backend.py   ConPTY (Windows) and POSIX pty processes
  aws_commands.py  AWS CLI command catalog from the installed CLI's models
  aws_name_overrides.py  CLI spellings for unusual operation names (tools/gen_name_overrides.py)
  commands_pane.py Commands pane and its collapsed right-edge tab
  aws_params.py    parameters from the model, command-line rendering and quoting, run safety
  aws_arg_renames.py  CLI argument renames (tools/gen_arg_renames.py)
  param_form.py    the parameter form
  assets/          app icon in SVG, PNG, ICO, ICNS; icons/material/ (MIT)
  icons.py         Material Icon Theme icons for groups, tabs and the toolbar
tests/
tools/build_icons.py
.github/workflows/ ci.yml (tests), release.yml (builds and publishes downloads)
termswitch.spec    PyInstaller build
```
