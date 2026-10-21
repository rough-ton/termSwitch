import base64
import configparser
from pathlib import Path

import pytest

from termswitch.config_store import ConfigError, ProfileStore, remap
from termswitch.launcher import LaunchOptions, build_command

SAMPLE = """# Managed by aws configure sso, plus hand edits
[default]
region = us-west-2
s3 =
    max_concurrent_requests = 10

# Production admin
[profile acme-prod-admin]
sso_session = acme
sso_account_id = 111111111111
sso_role_name = AdministratorAccess
region = us-west-2

[profile acme-dev-admin]
sso_session = acme
sso_account_id = 222222222222
sso_role_name = AdministratorAccess
region = us-west-2

[profile cross-account]
role_arn = arn:aws:iam::333333333333:role/Deployer
source_profile = acme-dev-admin

# Session block comment
[sso-session acme]
sso_start_url = https://acme.awsapps.com/start
sso_region = us-east-1
sso_registration_scopes = sso:account:access
"""

CREDS = """[homelab]
aws_access_key_id = AKIAEXAMPLEKEY12345
aws_secret_access_key = secret
"""


@pytest.fixture
def store(tmp_path: Path) -> ProfileStore:
    cfg = tmp_path / "config"
    cfg.write_text(SAMPLE)
    creds = tmp_path / "credentials"
    creds.write_text(CREDS)
    s = ProfileStore(cfg, creds)
    s.load()
    return s


def parsed(store: ProfileStore) -> configparser.RawConfigParser:
    p = configparser.RawConfigParser(interpolation=None)
    p.read_string(store.config_file.read_text())
    return p


def test_load(store):
    assert set(store.profiles) == {"default", "acme-prod-admin", "acme-dev-admin",
                                   "cross-account", "homelab"}
    prod = store.profiles["acme-prod-admin"]
    assert prod.kind == "sso"
    assert prod.sso_start_url == "https://acme.awsapps.com/start"
    assert store.profiles["cross-account"].account_id == "333333333333"
    assert store.profiles["homelab"].kind == "static"
    assert dict(store.profiles["homelab"].display_props())["aws_secret_access_key"] == "<set>"


def test_assign_preserves_comments_and_layout(store):
    store.set_profile_groups({"acme-prod-admin": "Acme/Prod"})
    text = store.config_file.read_text()
    assert "# Production admin\n[profile acme-prod-admin]" in text
    # New key goes after the last option, before the blank line and next section.
    assert "region = us-west-2\nx_group = Acme/Prod\n\n[profile acme-dev-admin]" in text
    assert "    max_concurrent_requests = 10" in text
    assert store.profiles["acme-prod-admin"].group == "Acme/Prod"
    # Only the one line changed.
    assert len(text.splitlines()) == len(SAMPLE.splitlines()) + 1


def test_backup_created_once(store):
    store.set_profile_groups({"acme-prod-admin": "A"})
    store.set_profile_groups({"acme-dev-admin": "A"})
    backups = list(store.config_file.parent.glob("config.termswitch-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == SAMPLE


def test_credentials_only_profile_gets_section(store):
    store.set_profile_groups({"homelab": "Personal"})
    p = parsed(store)
    assert p.get("profile homelab", "x_group") == "Personal"
    assert "aws_secret_access_key" not in store.config_file.read_text()


def test_clear_group(store):
    store.set_profile_groups({"acme-prod-admin": "Acme/Prod"})
    store.set_profile_groups({"acme-prod-admin": None})
    assert store.profiles["acme-prod-admin"].group is None
    # The emptied group is kept until it is deleted.
    assert "Acme/Prod" in store.all_groups()


def test_rename_nested(store):
    store.set_profile_groups({"acme-prod-admin": "Acme/Prod",
                              "acme-dev-admin": "Acme/Dev/Team"})
    store.rename_group("Acme", "Work")
    assert store.profiles["acme-prod-admin"].group == "Work/Prod"
    assert store.profiles["acme-dev-admin"].group == "Work/Dev/Team"
    assert not any(g.startswith("Acme") for g in store.all_groups())


def test_move_group_into_other(store):
    store.set_profile_groups({"acme-prod-admin": "Prod", "acme-dev-admin": "Work"})
    store.move(group_moves=[("Prod", "Work/Prod")])
    assert store.profiles["acme-prod-admin"].group == "Work/Prod"


def test_cannot_move_group_into_itself(store):
    store.set_profile_groups({"acme-prod-admin": "A/B"})
    with pytest.raises(ConfigError):
        store.move(group_moves=[("A", "A/B/A")])


def test_empty_group_persists_and_delete(store):
    store.add_group("Sandbox/Experiments")
    store.load()
    assert {"Sandbox", "Sandbox/Experiments"} <= store.all_groups()
    assert parsed(store).get("x-termswitch", "groups") == "Sandbox/Experiments"
    store.set_profile_groups({"acme-dev-admin": "Sandbox/Experiments"})
    # Group now has a profile, so the meta section is not needed.
    assert not parsed(store).has_section("x-termswitch")
    store.delete_group("Sandbox")
    assert store.profiles["acme-dev-admin"].group == "Experiments"
    store.delete_group("Experiments")
    assert store.profiles["acme-dev-admin"].group is None
    assert store.all_groups() == set()


def test_color(store):
    store.set_profile_color("acme-prod-admin", "#ff0000")
    assert store.profiles["acme-prod-admin"].color == "#ff0000"
    store.set_profile_color("acme-prod-admin", None)
    assert store.profiles["acme-prod-admin"].color is None


def test_missing_trailing_newline(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("[profile a]\nregion = us-east-1")
    s = ProfileStore(cfg, tmp_path / "nope")
    s.load()
    s.add_group("Empty")
    text = cfg.read_text()
    assert text == "[profile a]\nregion = us-east-1\n\n[x-termswitch]\ngroups = Empty\n"


def test_outside_edit_is_respected(store):
    # Simulate `aws configure sso` adding a profile after we loaded.
    store.config_file.write_text(SAMPLE + "\n[profile new-one]\nregion = eu-west-1\n")
    store.set_profile_groups({"acme-prod-admin": "Prod"})
    assert "new-one" in store.profiles
    assert "[profile new-one]" in store.config_file.read_text()


def test_remap():
    assert remap("A/B/C", "A/B", "X") == "X/C"
    assert remap("A/B", "A/B", None) is None
    assert remap("AB/C", "A", "X") == "AB/C"


# ---- launcher command construction ----------------------------------------

def test_windows_terminal_command(store, monkeypatch):
    monkeypatch.setattr("termswitch.launcher._which",
                        lambda name, platform=None: {"wt": r"C:\wt.exe",
                                                     "pwsh": r"C:\pwsh.exe"}.get(name))
    prof = store.profiles["acme-prod-admin"]
    argv, kwargs = build_command(prof, LaunchOptions(tab_color="#c0392b"), "win32",
                                 r"C:\Program Files\Amazon\AWSCLIV2\aws.exe")
    assert argv[:4] == [r"C:\wt.exe", "-w", "0", "new-tab"]
    assert "--tabColor" in argv and "#c0392b" in argv
    script = base64.b64decode(argv[-1]).decode("utf-16-le")
    assert "$env:AWS_PROFILE = 'acme-prod-admin'" in script
    assert "sso login --profile 'acme-prod-admin'" in script
    assert argv[argv.index("--") + 1] == r"C:\pwsh.exe"


def test_powershell_fallback(store, monkeypatch):
    monkeypatch.setattr("termswitch.launcher._which", lambda name, platform=None: None)
    prof = store.profiles["cross-account"]
    argv, kwargs = build_command(prof, LaunchOptions(export_credentials=True), "win32", None)
    assert argv[0].endswith(r"WindowsPowerShell\v1.0\powershell.exe") and "creationflags" in kwargs
    script = base64.b64decode(argv[-1]).decode("utf-16-le")
    assert "sso login" not in script  # not an SSO profile
    assert "export-credentials" in script


def test_macos_iterm_command(store, monkeypatch):
    monkeypatch.setattr("termswitch.launcher._iterm_installed", lambda: True)
    prof = store.profiles["acme-prod-admin"]
    argv, _ = build_command(prof, LaunchOptions(tab_color="#c0392b"), "darwin",
                            "/opt/homebrew/bin/aws")
    assert argv[0] == "/usr/bin/osascript"
    sep = argv.index("--")
    script, title, mode = argv[sep + 1:]
    assert "export AWS_PROFILE=acme-prod-admin" in script
    assert "bg;red;brightness;192" in script
    assert title == "aws: acme-prod-admin" and mode == "tab"


def test_macos_terminal_app_quotes_names(store):
    from termswitch.config_store import Profile
    prof = Profile(name="weird name's", props={"region": "us-east-1"})
    argv, _ = build_command(prof, LaunchOptions(terminal="terminal"), "darwin", None)
    assert argv[0] == "/usr/bin/osascript"
    script = argv[argv.index("--") + 1]
    assert "export AWS_PROFILE='weird name'\"'\"'s'" in script


# ---- security ------------------------------------------------------------

def test_secrets_never_kept_in_memory(store):
    values = [v for p in store.profiles.values() for v in p.props.values()]
    assert "secret" not in values
    assert "AKIAEXAMPLEKEY12345" not in values
    assert store.profiles["homelab"].props["aws_access_key_id"] == "AKIA...2345"


def test_control_characters_refused(tmp_path):
    from termswitch.config_store import Profile
    from termswitch.launcher import LaunchError
    prof = Profile(name="evil\x07\x1b]0;pwned", props={})
    assert prof.problem
    with pytest.raises(LaunchError):
        build_command(prof, LaunchOptions(terminal="terminal"), "darwin", None)


def test_bad_tab_color_dropped(store, monkeypatch):
    monkeypatch.setattr("termswitch.launcher._which",
                        lambda name, platform=None: {"wt": r"C:\wt.exe"}.get(name))
    argv, _ = build_command(store.profiles["default"],
                            LaunchOptions(terminal="wt", tab_color="red; calc"), "win32", None)
    assert "--tabColor" not in argv


def test_https_only_urls():
    from termswitch.config_store import safe_https_url
    assert safe_https_url("https://acme.awsapps.com/start") == "https://acme.awsapps.com/start"
    assert safe_https_url("http://acme.awsapps.com/start") is None
    assert safe_https_url("file:///etc/passwd") is None
    assert safe_https_url("javascript:alert(1)") is None


def test_secure_which_ignores_cwd_and_relative(tmp_path, monkeypatch):
    from termswitch.launcher import secure_which
    planted = tmp_path / "aws"
    planted.write_text("#!/bin/sh\n")
    planted.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", ".:relative/bin:")
    assert secure_which("aws", "linux") != str(planted)


def test_build_env_scrubs_credentials(monkeypatch):
    from termswitch.launcher import build_env
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("AWS_PROFILE", "other")
    env = build_env("linux")
    assert "AWS_SECRET_ACCESS_KEY" not in env and "AWS_PROFILE" not in env


@pytest.mark.skipif(__import__("sys").platform.startswith("win"),
                    reason="symlinks need extra privileges on Windows")
def test_symlinked_config_written_through(tmp_path):
    real = tmp_path / "dotfiles" / "aws-config"
    real.parent.mkdir()
    real.write_text(SAMPLE)
    link = tmp_path / "config"
    link.symlink_to(real)
    s = ProfileStore(link, tmp_path / "none")
    s.load()
    s.set_profile_groups({"acme-dev-admin": "Dev"})
    assert link.is_symlink()
    assert "x_group = Dev" in real.read_text()


def test_switchyard_meta_section_migrates(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("[profile a]\nregion = us-east-1\n\n[x-switchyard]\ngroups = Old\n")
    s = ProfileStore(cfg, tmp_path / "none")
    s.load()
    s.add_group("New")
    assert "x-switchyard" not in cfg.read_text() and "[x-termswitch]" in cfg.read_text()


def test_legacy_meta_section_migrates(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("[profile a]\nregion = us-east-1\n\n[x-profile-launcher]\ngroups = Old\n")
    s = ProfileStore(cfg, tmp_path / "none")
    s.load()
    assert "Old" in s.all_groups()
    s.add_group("New")
    text = cfg.read_text()
    assert "x-profile-launcher" not in text and "groups = New, Old" in text


def test_backup_is_private(store):
    import os, stat
    store.set_profile_groups({"default": "A"})
    backup = next(store.config_file.parent.glob("config.termswitch-*.bak"))
    if os.name == "posix":
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
