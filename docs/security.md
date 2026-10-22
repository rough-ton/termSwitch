# Security model

[Back to README](../README.md)

termSwitch is built to stay out of the way of your credentials. This page covers what it
does to protect them, and what it can't protect against. To report a vulnerability, see
[SECURITY.md](../SECURITY.md).

## Credentials

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

## Starting programs

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

## Reading AWS CLI models (Commands pane)

- Model files are parsed as JSON data only. Nothing in them is executed.
- They are only read from inside the resolved AWS CLI install. A symlink pointing outside
  it is refused.
- Each model is size-limited (64 MB, also after decompression).
- Inserted commands are typed with newlines and control characters removed, and never
  include Enter.

## Writing the config

- Writes go through `configupdater`, which changes only the affected lines.
- The new text is parsed before it replaces anything, then swapped in atomically: a temp
  file created with mode 0600 in the same directory, then `os.replace`.
- termSwitch refuses to write to anything that is not a regular file, or, on macOS, a
  file owned by another user.
- A symlinked config (dotfiles repo) is written through to its target, so the link
  survives.
- The first write of each session saves a mode-0600
  `config.termswitch-YYYYMMDD-HHMMSS.bak`. The newest 5 are kept. Restoring a backup
  always backs up the current file first, and a reset keeps the old file as
  `config.termswitch-reset-YYYYMMDD-HHMMSS.bak`, which is never rotated away.
- Outside changes, such as `aws configure sso` or an editor, are picked up before each
  write, so they are not overwritten.
- To support Undo, the text from before the last change is kept in a mode-0600
  `.config.termswitch-undo` file next to the config. It's removed when termSwitch
  quits. Undo is refused if the config changed outside termSwitch since that change.
- When termSwitch creates `~/.aws` for a new user, the folder is created mode 0700 and
  the config mode 0600.
- Profile edits write only the settings that changed. Names, keys and values are
  checked first, and values can't contain line breaks or control characters, so an
  edit can't inject new sections or settings.
- termSwitch never writes `aws_access_key_id`, `aws_secret_access_key` or session
  tokens, in either file. Duplicating a profile skips them, and the in-app config
  editor refuses to open or save a config that contains them.
- **Sign in and pick accounts** runs `aws configure sso` itself. The AWS CLI handles
  the sign-in and the SSO token; termSwitch never reads the token cache.
- Choosing another config file sets `AWS_CONFIG_FILE` for termSwitch's own status
  checks and new tabs. An `AWS_CONFIG_FILE` already set in your environment wins.

## Supply chain

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

## What it does not protect against

Anyone who can write your `~/.aws/config` can
already make the AWS CLI run arbitrary commands, through `credential_process`. That is
why the dependency check warns when the file is writable by other users.
