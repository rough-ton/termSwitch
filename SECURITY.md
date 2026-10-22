# Security

termSwitch launches shells with access to AWS accounts, so security issues are taken
seriously.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting instead:
**Security > Report a vulnerability** on this repository. Include steps to reproduce
and the termSwitch version (`termSwitch --version`).

## Design summary

- termSwitch never stores or handles AWS secrets. It sets `AWS_PROFILE` and lets the
  AWS CLI resolve credentials.
- It never writes to `~/.aws/credentials`.
- Writes to `~/.aws/config` are validated, atomic and backed up.
- Executables are resolved to absolute paths, never from the current directory.
- Release builds come from hash-pinned dependencies on GitHub-hosted runners, and ship
  with SHA-256 checksums.

See the [security model](docs/security.md) for the full list.
