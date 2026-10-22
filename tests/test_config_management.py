"""Creating, editing, renaming, duplicating and deleting profiles and SSO sessions,
raw edits, backups, reset and undo."""

import configparser
import os
import stat
import sys
from pathlib import Path

import pytest

from termswitch.config_store import (ConfigError, ProfileStore, check_settings,
                                     profile_name_problem)

from test_store_and_launcher import CREDS, SAMPLE


@pytest.fixture
def store(tmp_path: Path) -> ProfileStore:
    cfg = tmp_path / "config"
    cfg.write_text(SAMPLE)
    creds = tmp_path / "credentials"
    creds.write_text(CREDS)
    s = ProfileStore(cfg, creds)
    s.load()
    return s


def parsed(store):
    p = configparser.RawConfigParser(interpolation=None)
    p.read_string(store.config_file.read_text())
    return p


# ---- validation -----------------------------------------------------------

@pytest.mark.parametrize("name", ["", " lead", "trail ", "a[b", "a]b", "x\x1b[31m",
                                  "sso-session foo", "a" * 129])
def test_bad_profile_names(name):
    assert profile_name_problem(name)


@pytest.mark.parametrize("name", ["dev", "team.prod-admin", "my profile", "default"])
def test_good_profile_names(name):
    assert profile_name_problem(name) is None


@pytest.mark.parametrize("props", [
    {"region": "us-west"}, {"region": "US-WEST-2"}, {"sso_account_id": "12345"},
    {"role_arn": "arn:aws:iam::12:role/x"}, {"aws_secret_access_key": "x"},
    {"aws_access_key_id": "AKIA..."}, {"bad key": "v"}, {"region": "us-west-2\n[evil]"},
    {"sso_start_url": "http://x"}, {"duration_seconds": "60"}, {"output": "xml"},
    {"sso_role_name": "a b"},
])
def test_bad_settings(props):
    with pytest.raises(ConfigError):
        check_settings(props)


def test_good_settings():
    check_settings({"region": "us-gov-west-1", "sso_account_id": "123456789012",
                    "role_arn": "arn:aws:iam::123456789012:role/path/Deployer",
                    "sso_start_url": "https://x.awsapps.com/start", "output": "table",
                    "duration_seconds": "3600", "credential_process": "/usr/bin/tool --x"})


# ---- create -------------------------------------------------------------------

def test_create_sso_profile_in_group(store):
    store.create_profile("new-dev", {"sso_session": "acme", "sso_account_id": "444444444444",
                                     "sso_role_name": "ReadOnly", "region": "us-west-2"},
                         group="Acme/Dev")
    p = parsed(store)
    assert p.get("profile new-dev", "sso_account_id") == "444444444444"
    assert p.get("profile new-dev", "x_group") == "Acme/Dev"
    assert store.profiles["new-dev"].is_sso
    assert "# Session block comment" in store.config_file.read_text()


def test_create_refuses_duplicates_and_secrets(store):
    with pytest.raises(ConfigError, match="already exists"):
        store.create_profile("cross-account", {"region": "us-west-2"})
    with pytest.raises(ConfigError, match="already exists"):
        store.create_profile("homelab", {"region": "us-west-2"})   # credentials-only
    with pytest.raises(ConfigError):
        store.create_profile("x", {"aws_secret_access_key": "s"})


def test_create_first_config_from_nothing(tmp_path):
    aws = tmp_path / ".aws"
    s = ProfileStore(aws / "config", aws / "credentials")
    s.load()
    assert not s.profiles
    s.create_sso_session("corp", {"sso_start_url": "https://corp.awsapps.com/start",
                                  "sso_region": "us-east-1",
                                  "sso_registration_scopes": "sso:account:access"})
    s.create_profile("corp-dev", {"sso_session": "corp", "sso_account_id": "123456789012",
                                  "sso_role_name": "Dev"})
    assert s.profiles["corp-dev"].sso_start_url == "https://corp.awsapps.com/start"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(aws).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(aws / "config").st_mode) == 0o600


# ---- edit ---------------------------------------------------------------------

def test_update_changes_only_given_keys(store):
    before = store.config_file.read_text()
    store.update_profile("default", {"region": "eu-west-1", "output": "json"})
    after = store.config_file.read_text()
    assert "region = eu-west-1" in after and "output = json" in after
    assert "    max_concurrent_requests = 10" in after     # nested settings untouched
    assert after.count("\n") == before.count("\n") + 1


def test_update_removes_key_and_sets_group(store):
    store.update_profile("cross-account", {"source_profile": None, "x_group": " A / B "})
    p = parsed(store)
    assert not p.has_option("profile cross-account", "source_profile")
    assert p.get("profile cross-account", "x_group") == "A/B"


def test_update_never_writes_secrets(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("[profile k]\naws_access_key_id = AKIAEXAMPLE\naws_secret_access_key = s3cr3t\n")
    s = ProfileStore(cfg, tmp_path / "creds")
    s.load()
    with pytest.raises(ConfigError):
        s.update_profile("k", {"aws_secret_access_key": "<set>"})
    s.update_profile("k", {"region": "us-west-2"})
    assert "aws_secret_access_key = s3cr3t" in cfg.read_text()   # untouched


def test_update_credentials_only_profile_refused(store):
    with pytest.raises(ConfigError, match="credentials"):
        store.update_profile("homelab", {"region": "us-west-2"})


# ---- rename -------------------------------------------------------------------

def test_rename_updates_source_profile_references(store):
    assert store.references_to("acme-dev-admin") == ["cross-account"]
    refs = store.rename_profile("acme-dev-admin", "acme-dev")
    assert refs == ["cross-account"]
    p = parsed(store)
    assert p.has_section("profile acme-dev") and not p.has_section("profile acme-dev-admin")
    assert p.get("profile cross-account", "source_profile") == "acme-dev"


def test_rename_without_reference_update(store):
    store.rename_profile("acme-dev-admin", "dev2", update_references=False)
    assert parsed(store).get("profile cross-account", "source_profile") == "acme-dev-admin"


def test_rename_default_and_refusals(store):
    store.rename_profile("default", "base")
    p = parsed(store)
    assert p.has_section("profile base") and not p.has_section("default")
    with pytest.raises(ConfigError, match="already exists"):
        store.rename_profile("base", "cross-account")
    with pytest.raises(ConfigError, match="credentials"):
        store.rename_profile("homelab", "home")
    with pytest.raises(ConfigError):
        store.rename_profile("base", "bad]name")


# ---- duplicate -----------------------------------------------------------------

def test_duplicate_copies_settings_but_not_keys(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("[profile src]\nregion = us-west-2\naws_access_key_id = AKIAX\n"
                   "aws_secret_access_key = s\ns3 =\n    max_concurrent_requests = 10\n")
    s = ProfileStore(cfg, tmp_path / "creds")
    s.load()
    s.duplicate_profile("src", "copy")
    text = cfg.read_text()
    copy = text[text.index("[profile copy]"):]
    assert "region = us-west-2" in copy
    assert "s3 =\n    max_concurrent_requests = 10" in copy
    assert "aws_access_key_id" not in copy and "aws_secret_access_key" not in copy


# ---- delete ---------------------------------------------------------------------

def test_delete_profiles(store):
    remaining = store.delete_profiles(["acme-prod-admin", "default"])
    assert remaining == []
    p = parsed(store)
    assert not p.has_section("profile acme-prod-admin") and not p.has_section("default")
    assert "acme-prod-admin" not in store.profiles


def test_delete_credentials_profile_reports_it_remains(store):
    store.set_profile_groups({"homelab": "Home"})          # creates a config section
    assert store.delete_profiles(["homelab"]) == ["homelab"]
    assert "homelab" in store.profiles                      # still in credentials
    assert not parsed(store).has_section("profile homelab")


# ---- SSO sessions ---------------------------------------------------------------

def test_sso_session_crud(store):
    store.create_sso_session("other", {"sso_start_url": "https://o.awsapps.com/start",
                                       "sso_region": "us-east-2"})
    assert store.sso_sessions["other"]["sso_region"] == "us-east-2"
    store.update_sso_session("other", {"sso_region": "eu-west-1",
                                       "sso_registration_scopes": "sso:account:access"})
    assert store.sso_sessions["other"]["sso_region"] == "eu-west-1"
    assert store.sso_session_users("acme") == ["acme-dev-admin", "acme-prod-admin"]
    store.delete_sso_session("other")
    assert "other" not in store.sso_sessions


@pytest.mark.parametrize("name,props", [
    ("bad name", {"sso_start_url": "https://x/start", "sso_region": "us-east-1"}),
    ("ok", {"sso_region": "us-east-1"}),
    ("ok", {"sso_start_url": "javascript:alert(1)", "sso_region": "us-east-1"}),
    ("acme", {"sso_start_url": "https://x/start", "sso_region": "us-east-1"}),
])
def test_sso_session_validation(store, name, props):
    with pytest.raises(ConfigError):
        store.create_sso_session(name, props)


# ---- raw edits, backups, reset ---------------------------------------------------

def test_raw_save_validates(store):
    problem = store.parse_problem("[ok]\na = 1\nnot a valid line\n")
    assert problem and problem[0] == 3
    with pytest.raises(ConfigError, match="line 3"):
        store.save_raw("[ok]\na = 1\nnot a valid line\n")
    store.save_raw("[profile only]\nregion = us-west-2")
    assert set(store.profiles) == {"only", "homelab"}


def test_contains_secrets(store):
    assert not store.contains_secrets()
    assert store.contains_secrets("[profile x]\naws_secret_access_key = abc\n")


def test_backups_and_restore(store):
    store.update_profile("default", {"region": "eu-west-1"})
    backups = store.backups()
    assert len(backups) == 1 and backups[0].read_text() == SAMPLE
    store.restore_backup(backups[0])
    assert store.config_file.read_text() == SAMPLE
    assert len(store.backups()) >= 1
    outsider = store.config_file.with_name("evil.bak")
    outsider.write_text("[profile x]\n")
    with pytest.raises(ConfigError):
        store.restore_backup(outsider)


def test_reset_moves_config_aside(store):
    dest = store.reset_config()
    assert dest and dest.read_text() == SAMPLE and "reset" in dest.name
    assert not store.config_file.exists()
    assert set(store.profiles) == {"homelab"}
    assert dest in store.backups()
    store.undo()
    assert store.config_file.read_text() == SAMPLE


def test_reset_refuses_symlink(tmp_path):
    if sys.platform == "win32":
        pytest.skip("symlinks need privileges on Windows")
    real = tmp_path / "dotfiles-config"
    real.write_text(SAMPLE)
    link = tmp_path / "config"
    link.symlink_to(real)
    s = ProfileStore(link, tmp_path / "creds")
    s.load()
    with pytest.raises(ConfigError, match="symlink"):
        s.reset_config()


# ---- undo ------------------------------------------------------------------------

def test_undo_and_redo(store):
    assert not store.can_undo()
    store.delete_profiles(["cross-account"])
    assert store.can_undo() and store.undo_label == "Delete profile cross-account"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(store.undo_file).st_mode) == 0o600
    assert store.undo() == "Delete profile cross-account"
    assert store.config_file.read_text() == SAMPLE
    assert store.undo_label == "Undo: Delete profile cross-account"
    store.undo()                                         # redo
    assert "cross-account" not in store.profiles


def test_undo_refused_after_outside_edit(store):
    store.update_profile("default", {"region": "eu-west-1"})
    store.config_file.write_text(store.config_file.read_text() + "\n# edited elsewhere\n")
    with pytest.raises(ConfigError, match="outside termSwitch"):
        store.undo()


def test_discard_undo_removes_file(store):
    store.update_profile("default", {"region": "eu-west-1"})
    store.discard_undo()
    assert not store.can_undo() and not store.undo_file.exists()
