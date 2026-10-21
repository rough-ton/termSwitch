import json

from test_aws_params import MODEL, PAGERS, model_dir  # noqa: F401 (fixture)

from termswitch.aws_commands import Command
from termswitch.aws_params import load_spec


def make_form(qapp, model_dir, op, name):
    from termswitch.param_form import ParameterForm
    form = ParameterForm()
    form.set_target("posix", "us-west-2", True)
    form.set_spec(load_spec(Command("ec2", name, "doc", op), str(model_dir),
                            str(model_dir.parents[1])))
    return form


def test_required_fields_gate_run_and_insert(qapp, model_dir):
    form = make_form(qapp, model_dir, "CreateImage", "create-image")
    form._update_preview()
    assert not form.btn_run.isEnabled() and "Required" in form.error.text()
    form._fields["instance-id"][1].setText("i-123")
    form._fields["name"][1].setText("my image")
    form._update_preview()
    assert form.btn_run.isEnabled() and form.btn_insert.isEnabled()
    assert form.preview.toPlainText() == "aws ec2 create-image --instance-id i-123 --name 'my image'"
    assert "us-west-2 (from profile)" in form._common["region"].placeholderText()


def test_emit_run_and_insert(qapp, model_dir):
    form = make_form(qapp, model_dir, "CreateImage", "create-image")
    form._fields["instance-id"][1].setText("i-1")
    form._fields["name"][1].setText("n")
    form._fields["no-reboot"][1].setCurrentIndex(2)          # --reboot
    got = []
    form.insert_requested.connect(lambda text, run, cmd: got.append((text, run, cmd.name)))
    form._update_preview()
    form.btn_insert.click()
    form.btn_run.click()
    assert got[0] == ("aws ec2 create-image --instance-id i-1 --name n --reboot ", False, "create-image")
    assert got[1] == ("aws ec2 create-image --instance-id i-1 --name n --reboot", True, "create-image")


def test_no_terminal_disables_run(qapp, model_dir):
    form = make_form(qapp, model_dir, "DescribeThings", "describe-things")
    form.set_target("posix", "", False)
    assert not form.btn_run.isEnabled() and not form.btn_insert.isEnabled()
    assert form.btn_copy.isEnabled()


def test_common_section_and_json_example(qapp, model_dir):
    form = make_form(qapp, model_dir, "DescribeThings", "describe-things")
    assert "dry_run" in form._common and "no_paginate" in form._common
    form._common["dry_run"].setChecked(True)
    form._common["output"].setCurrentIndex(2)               # table
    form._fields["filters"][1].setPlainText(json.dumps([{"Name": "a", "Values": ["b"]}]))
    form._update_preview()
    text = form.preview.toPlainText()
    assert "--dry-run" in text and "--output table" in text and "--filters '[{" in text


def test_powershell_target_notes_temp_files(qapp, model_dir):
    form = make_form(qapp, model_dir, "DescribeThings", "describe-things")
    form.set_target("powershell", "", True)
    form._fields["filters"][1].setPlainText('[{"Name":"a"}]')
    form._update_preview()
    assert "file://" in form.error.text()
    got = []
    form.insert_requested.connect(lambda text, run, cmd: got.append(text))
    form.btn_insert.click()
    assert "--filters file://" in got[0] or "--filters 'file://" in got[0]
