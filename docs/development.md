# Development

[Back to README](../README.md)

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
git clone https://github.com/rough-ton/termSwitch.git
cd termSwitch
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m termswitch
```

Windows (PowerShell):

```powershell
git clone https://github.com/rough-ton/termSwitch.git
cd termSwitch
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

## Tests

```bash
python -m pip install --require-hashes -r requirements.lock -r requirements-dev.lock
python -m pytest -q
```

CI runs them on Windows, macOS and Linux with Python 3.12 and 3.13.

A test fails at once if the code opens a message box, input dialog, file dialog or
other modal dialog that the test didn't patch, and the failure names the dialog and
its text. Without this, a dialog waits for a click that never comes and CI hangs until
the job times out. Patch the dialog in tests that expect one (see `press` and
`accept_with` in `tests/test_profile_ui.py`).

## Dependency locks

`requirements.lock`, `requirements-build.lock` and `requirements-dev.lock` pin exact
versions with hashes for every platform. Regenerate them after editing a `.in` file:

```bash
pip install uv
for f in requirements requirements-build requirements-dev; do
  uv pip compile --universal --generate-hashes --python-version 3.12 \
    "$f.in" -o "$f.lock"
done
```

## Licenses and notices

The project license is in `LICENSE`. Everything the downloads bundle is listed in
`THIRD_PARTY_NOTICES.md`, with full texts of the longer licenses in `licenses/`. The
release build copies all of these, plus the license of the Python interpreter it
bundles, into the app, where Help > Licenses shows them. `tests/test_legal.py` fails
if a package in `requirements.lock` is missing from the notices or listed with the
wrong version.

## Commit policy

Read [CONTRIBUTING.md](../CONTRIBUTING.md) before opening a pull request. To run the
same check CI runs:

```bash
python tools/check_attribution.py --commits --files
```

## Screenshots

The images in `docs/images/` are placeholders made by `tools/make_placeholders.py`.
To add a real screenshot, capture it at 2x scale and save it over the placeholder with the
same file name. The pages pick it up with no other changes. `tests/test_docs.py` checks
that every image and relative link in the docs resolves.

## Layout

```
termswitch/
  config_store.py  read profiles, write groups back safely
  fileutil.py      file replace that retries while Windows holds a file open
  launcher.py      safe executable lookup and terminal commands
  status.py        background sts checks and SSO logins
  deps.py          dependency and security checks (--check)
  depsui.py        dependency report window
  icon.py          app icon, Windows taskbar identity
  ui.py            main window, drag-and-drop tree, settings
  terminal_tabs.py embedded terminal tabs
  home_view.py     home view when no tabs are open, footer and status bar
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
  legal.py         Help > Licenses viewer for the license and third-party notices
  profile_dialogs.py  profile and SSO session editors
  config_dialogs.py   welcome panel, config editor, backups
  about.py         Help > About dialog and system information
  links.py         every web address the app links to (website, GitHub, releases)
  theme.py         light and dark themes, colors with a meaning
  term_schemes.py  terminal color schemes and scheme import
  system_terminal.py  reads Windows Terminal, iTerm2 and Terminal.app colors (read only)
  ssh_sessions.py  SSH sessions: storage, ~/.ssh/config, the ssh command, saved passwords
  ssh_dialogs.py   the SSH session editor
tests/
tools/build_icons.py
.github/workflows/ ci.yml (tests), release.yml (builds and publishes downloads)
termswitch.spec    PyInstaller build
```
