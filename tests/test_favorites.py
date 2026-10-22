"""Favorite commands: storage, the Favorites view and section, and import/export."""

import json
import os

import pytest
from PySide6.QtCore import QSettings

from test_aws_commands import data_dir, write_model  # noqa: F401  (fixture)


def settings_at(tmp_path, name="s.ini", fresh=False):
    """Settings for a test. Unless ``fresh``, the starter favorites count as already
    added, so the test starts with an empty list."""
    s = QSettings(str(tmp_path / name), QSettings.Format.IniFormat)
    if not fresh:
        s.setValue("favorite_commands_defaults_added", True)
    return s


def make_pane(qapp, data_dir, settings):
    from termswitch.aws_commands import build_catalog
    from termswitch.commands_pane import CommandsPane
    pane = CommandsPane(settings=settings)
    pane._on_loaded(build_catalog(data_dir, "2.99.0"))
    return pane


def rows(pane):
    m = pane.model
    return [m.data(m.index(i)) for i in range(m.rowCount())]


def select(pane, text):
    m = pane.model
    for i in range(m.rowCount()):
        if m.data(m.index(i)) == text:
            pane.list.setCurrentIndex(m.index(i))
            return
    raise AssertionError(f"{text!r} not in {rows(pane)}")


# ---- Favorites store -------------------------------------------------------

def test_store_toggle_order_and_persistence(qapp, tmp_path):
    from termswitch.favorites import Favorites
    fav = Favorites(settings_at(tmp_path))
    assert fav.toggle("sts get-caller-identity") is True
    assert fav.add("ec2 describe-instances")
    assert not fav.add("ec2 describe-instances")          # no duplicates
    assert fav.items() == ["sts get-caller-identity", "ec2 describe-instances"]
    assert fav.toggle("sts get-caller-identity") is False
    again = Favorites(settings_at(tmp_path))
    assert again.items() == ["ec2 describe-instances"]


@pytest.mark.parametrize("bad", ["", "ec2", "EC2 describe", "ec2 describe instances",
                                 "ec2 describe;rm", "ec2 describe\n", " ec2 x", 42, None])
def test_store_rejects_invalid_keys(qapp, tmp_path, bad):
    from termswitch.favorites import Favorites
    fav = Favorites(settings_at(tmp_path))
    assert not fav.add(bad)
    assert len(fav) == 0


def test_store_ignores_tampered_settings(qapp, tmp_path):
    from termswitch.favorites import SETTINGS_KEY, Favorites
    s = settings_at(tmp_path)
    s.setValue(SETTINGS_KEY, ["s3 ls", "bad entry here", "\x1b[31m x", "s3 ls"])
    assert Favorites(s).items() == ["s3 ls"]


# ---- Export and import -----------------------------------------------------

def test_export_import_round_trip(qapp, tmp_path):
    from termswitch.favorites import Favorites
    a = Favorites(settings_at(tmp_path, "a.ini"))
    a.add("sts get-caller-identity")
    a.add("s3 cp")
    out = tmp_path / "fav.json"
    assert a.export_file(out) == 2
    data = json.loads(out.read_text())
    assert data["format"] == "termswitch-favorites" and data["version"] == 1
    b = Favorites(settings_at(tmp_path, "b.ini"))
    b.add("s3 cp")
    assert b.import_file(out) == (1, 0)                    # s3 cp already present
    assert b.items() == ["s3 cp", "sts get-caller-identity"]


def test_import_rejects_invalid_entries(qapp, tmp_path):
    from termswitch.favorites import Favorites
    f = tmp_path / "list.json"
    f.write_text(json.dumps(["ec2  describe-instances", "rm -rf /", {"x": 1}, "s3 ls"]))
    fav = Favorites(settings_at(tmp_path))
    assert fav.import_file(f) == (2, 2)
    assert fav.items() == ["ec2 describe-instances", "s3 ls"]


@pytest.mark.parametrize("content", ['{"format": "other", "favorites": []}',
                                     '{"format": "termswitch-favorites"}',
                                     "not json", '"a string"'])
def test_import_rejects_wrong_files(qapp, tmp_path, content):
    from termswitch.favorites import Favorites
    f = tmp_path / "x.json"
    f.write_text(content)
    with pytest.raises(ValueError):
        Favorites(settings_at(tmp_path)).import_file(f)


def test_import_refuses_large_files(qapp, tmp_path):
    from termswitch.favorites import MAX_IMPORT_BYTES, Favorites
    f = tmp_path / "big.json"
    with open(f, "wb") as fh:
        fh.write(b"[" + b" " * MAX_IMPORT_BYTES + b"]")
    with pytest.raises(ValueError):
        Favorites(settings_at(tmp_path)).import_file(f)


# ---- Commands pane ---------------------------------------------------------

def test_favorites_view_and_live_count(qapp, data_dir, tmp_path):
    from termswitch.commands_pane import FAVORITES, FAVORITES_HINT
    pane = make_pane(qapp, data_dir, settings_at(tmp_path))
    fav_index = pane.service.findData(FAVORITES)
    assert fav_index == 1 and pane.service.itemText(1) == "Favorites (0)"
    pane.service.setCurrentIndex(fav_index)
    assert rows(pane) == [FAVORITES_HINT]
    assert not pane.model.flags(pane.model.index(0)) & pane.model.flags(pane.model.index(0)).ItemIsSelectable
    pane.favorites.add("sts get-caller-identity")
    pane.favorites.add("ec2 describe-instances")
    assert pane.service.itemText(1) == "Favorites (2)"
    assert rows(pane) == ["E:", "ec2 describe-instances", "S:", "sts get-caller-identity"]
    pane.name.setText("caller")
    pane._refilter()
    assert rows(pane) == ["S:", "sts get-caller-identity"]


def test_favorites_section_above_az(qapp, data_dir, tmp_path):
    pane = make_pane(qapp, data_dir, settings_at(tmp_path))
    assert "Favorites:" not in rows(pane)
    pane.favorites.add("s3 cp")
    r = rows(pane)
    assert r[:2] == ["Favorites:", "s3 cp"] and r[2].endswith(":")
    # Rows are starred wherever they appear.
    star_rows = [i for i in range(pane.model.rowCount())
                 if pane.model.data(pane.model.index(i)) == "s3 cp"]
    assert len(star_rows) == 2
    # A filter hides the section; the list is plain A-Z again.
    pane.name.setText("s3")
    pane._refilter()
    assert "Favorites:" not in rows(pane)
    # In a single service, there is no section.
    pane.name.clear()
    pane.service.setCurrentIndex(pane.service.findData("ec2"))
    assert "Favorites:" not in rows(pane)


def test_star_button_and_shortcut_keep_form_values(qapp, data_dir, tmp_path):
    pane = make_pane(qapp, data_dir, settings_at(tmp_path))
    select(pane, "ec2 describe-instances")
    form = pane.form
    assert not form.star.isHidden()
    assert not form.star.isChecked()
    form.star.click()
    assert "ec2 describe-instances" in pane.favorites
    assert form.star.isChecked()
    assert pane.selected_command().name == "describe-instances"   # selection kept
    spec_before = form.spec
    pane.toggle_selected_favorite()                                # Ctrl+D path
    assert "ec2 describe-instances" not in pane.favorites
    assert not form.star.isChecked()
    assert form.spec is spec_before                                # form not rebuilt


def test_shortcut_is_scoped_to_pane(qapp, data_dir, tmp_path):
    from PySide6.QtCore import Qt
    pane = make_pane(qapp, data_dir, settings_at(tmp_path))
    assert pane.fav_shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut
    assert pane.fav_shortcut.key().toString() == "Ctrl+D"


def test_missing_favorite_is_grayed_not_dropped(qapp, data_dir, tmp_path):
    from PySide6.QtCore import Qt
    from termswitch.commands_pane import FAVORITES
    s = settings_at(tmp_path)
    s.setValue("favorite_commands", ["ec2 old-removed-command", "s3 cp"])
    pane = make_pane(qapp, data_dir, s)
    pane.service.setCurrentIndex(pane.service.findData(FAVORITES))
    r = rows(pane)
    assert "ec2 old-removed-command" in r
    row = r.index("ec2 old-removed-command")
    idx = pane.model.index(row)
    assert pane.model.is_missing(row)
    assert "Not available in AWS CLI 2.99.0" in pane.model.data(idx, Qt.ItemDataRole.ToolTipRole)
    pane.list.setCurrentIndex(idx)
    assert pane.form.spec is None                 # nothing to run
    assert pane.favorites.items() == ["ec2 old-removed-command", "s3 cp"]
    pane.favorites.remove("ec2 old-removed-command")
    assert "ec2 old-removed-command" not in rows(pane)


def test_pane_import_export(qapp, data_dir, tmp_path):
    pane = make_pane(qapp, data_dir, settings_at(tmp_path))
    pane.favorites.add("s3 cp")
    out = tmp_path / "out.json"
    pane.export_favorites(str(out))
    assert json.loads(out.read_text())["favorites"] == ["s3 cp"]
    other = make_pane(qapp, data_dir, settings_at(tmp_path, "other.ini"))
    other.import_favorites(str(out))
    assert other.favorites.items() == ["s3 cp"]
    assert "Imported 1" in other.status.text()


def test_export_is_atomic_and_leaves_no_temp(qapp, tmp_path):
    from termswitch.favorites import Favorites
    fav = Favorites(settings_at(tmp_path))
    fav.add("s3 ls")
    fav.export_file(tmp_path / "f.json")
    assert not [p for p in os.listdir(tmp_path) if p.startswith(".favorites-")]


def test_ctrl_d_reaches_terminal_but_toggles_in_pane(qapp, data_dir, tmp_path):
    """Ctrl+D in a terminal means end-of-input; it must not be taken by the pane."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QWidget

    from conftest import pump

    class FakeTerminal(QPlainTextEdit):
        def __init__(self):
            super().__init__()
            self.keys = []

        def keyPressEvent(self, e):
            self.keys.append((e.key(), e.modifiers()))

    window = QWidget()
    term = FakeTerminal()
    pane = make_pane(qapp, data_dir, settings_at(tmp_path))
    lay = QHBoxLayout(window)
    lay.addWidget(term)
    lay.addWidget(pane)
    window.show()
    window.activateWindow()
    pump(qapp, 0.2)
    select(pane, "s3 cp")

    term.setFocus()
    pump(qapp, 0.1)
    QTest.keyClick(term, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    pump(qapp, 0.1)
    assert "s3 cp" not in pane.favorites
    assert term.keys and term.keys[-1][0] == Qt.Key.Key_D

    pane.list.setFocus()
    pump(qapp, 0.1)
    QTest.keyClick(pane.list, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    pump(qapp, 0.1)
    assert "s3 cp" in pane.favorites
    window.close()


# ---- Default favorites ------------------------------------------------------

def test_defaults_are_valid_unique_cli_names():
    from termswitch.aws_commands import CUSTOM_COMMANDS, cli_operation_name
    from termswitch.favorites import DEFAULT_FAVORITES, is_valid_key
    assert len(DEFAULT_FAVORITES) == len(set(DEFAULT_FAVORITES)) == 30
    assert all(is_valid_key(k) for k in DEFAULT_FAVORITES)
    # Model operations: the API name must turn into exactly this CLI name.
    api = {
        "sts get-caller-identity": "GetCallerIdentity",
        "ec2 describe-instances": "DescribeInstances", "ec2 start-instances": "StartInstances",
        "ec2 stop-instances": "StopInstances", "ec2 create-snapshot": "CreateSnapshot",
        "iam list-users": "ListUsers", "iam list-roles": "ListRoles",
        "iam list-policies": "ListPolicies", "iam list-groups": "ListGroups",
        "iam attach-user-policy": "AttachUserPolicy",
        "iam attach-group-policy": "AttachGroupPolicy",
        "iam attach-role-policy": "AttachRolePolicy",
        "lambda list-functions": "ListFunctions", "lambda invoke": "Invoke",
        "cloudformation validate-template": "ValidateTemplate",
        "ecs list-clusters": "ListClusters", "eks list-clusters": "ListClusters",
        "eks describe-cluster": "DescribeCluster", "ecs describe-clusters": "DescribeClusters",
        "logs describe-log-groups": "DescribeLogGroups",
        "logs filter-log-events": "FilterLogEvents",
        "autoscaling describe-auto-scaling-groups": "DescribeAutoScalingGroups",
        "elbv2 describe-load-balancers": "DescribeLoadBalancers",
        "rds describe-db-instances": "DescribeDBInstances",
        "cloudwatch get-metric-data": "GetMetricData",
    }
    for key in DEFAULT_FAVORITES:
        service, name = key.split(" ")
        if key in api:
            assert cli_operation_name(api[key]) == name, key
        else:   # high-level commands the CLI adds itself
            assert name in CUSTOM_COMMANDS[service][1], key


def test_defaults_added_once_on_first_run(qapp, tmp_path):
    from termswitch.favorites import DEFAULT_FAVORITES, Favorites
    s = settings_at(tmp_path, fresh=True)
    fav = Favorites(s, defaults=DEFAULT_FAVORITES)
    assert fav.items() == list(DEFAULT_FAVORITES)
    fav.remove("s3 ls")
    for key in fav.items():
        fav.remove(key)
    again = Favorites(settings_at(tmp_path, fresh=True), defaults=DEFAULT_FAVORITES)
    assert again.items() == []                    # removing them is respected


def test_defaults_merge_with_existing_favorites(qapp, tmp_path):
    from termswitch.favorites import DEFAULT_FAVORITES, SETTINGS_KEY, Favorites
    s = settings_at(tmp_path, fresh=True)
    s.setValue(SETTINGS_KEY, ["route53 list-hosted-zones", "s3 ls"])
    fav = Favorites(s, defaults=DEFAULT_FAVORITES)
    items = fav.items()
    assert items[:2] == ["route53 list-hosted-zones", "s3 ls"]   # user's own stay first
    assert set(DEFAULT_FAVORITES) <= set(items) and len(items) == 31


def test_restore_default_favorites(qapp, data_dir, tmp_path):
    from termswitch.commands_pane import FAVORITES
    pane = make_pane(qapp, data_dir, settings_at(tmp_path, fresh=True))
    assert pane.service.itemText(pane.service.findData(FAVORITES)) == "Favorites (30)"
    pane.favorites.remove("sts get-caller-identity")
    pane.favorites.remove("s3 cp")
    pane.favorites.add("route53 list-hosted-zones")
    assert pane.favorites.missing_defaults() == 2
    pane.restore_default_favorites()
    assert pane.favorites.missing_defaults() == 0
    assert "route53 list-hosted-zones" in pane.favorites
    assert "Added 2 default" in pane.status.text()
