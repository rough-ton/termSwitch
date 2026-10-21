import json
import os
import shlex

import pytest

from termswitch.aws_commands import Command
from termswitch.aws_params import (Common, ParamError, is_destructive, is_read_only, load_spec,
                                   quote_powershell, render)

MODEL = {
    "metadata": {"serviceFullName": "Test"},
    "operations": {
        "DescribeThings": {"name": "DescribeThings", "input": {"shape": "DescribeThingsRequest"},
                           "output": {"shape": "DescribeThingsResult"}},
        "CreateImage": {"name": "CreateImage", "input": {"shape": "CreateImageRequest"}},
        "GetBlob": {"name": "GetBlob", "input": {"shape": "GetBlobRequest"},
                    "output": {"shape": "GetBlobOutput"}},
    },
    "shapes": {
        "DescribeThingsRequest": {"type": "structure", "members": {
            "ThingIds": {"shape": "IdList", "documentation": "<p>Thing IDs.</p>"},
            "Filters": {"shape": "FilterList"},
            "State": {"shape": "StateEnum"},
            "DryRun": {"shape": "Boolean"},
            "NextToken": {"shape": "String"},
            "MaxResults": {"shape": "Integer"},
        }},
        "DescribeThingsResult": {"type": "structure", "members": {}},
        "CreateImageRequest": {"type": "structure", "required": ["InstanceId", "Name"],
                               "members": {"InstanceId": {"shape": "String"},
                                           "Name": {"shape": "String"},
                                           "NoReboot": {"shape": "Boolean"},
                                           "Count": {"shape": "Integer"}}},
        "GetBlobRequest": {"type": "structure", "required": ["Key"],
                           "members": {"Key": {"shape": "String"}}},
        "GetBlobOutput": {"type": "structure", "payload": "Body",
                          "members": {"Body": {"shape": "Blob"}}},
        "IdList": {"type": "list", "member": {"shape": "String"}},
        "FilterList": {"type": "list", "member": {"shape": "Filter"}},
        "Filter": {"type": "structure", "members": {"Name": {"shape": "String"},
                                                    "Values": {"shape": "IdList"}}},
        "StateEnum": {"type": "string", "enum": ["running", "stopped"]},
        "String": {"type": "string"}, "Integer": {"type": "integer"},
        "Boolean": {"type": "boolean"}, "Blob": {"type": "blob"},
    },
}
PAGERS = {"pagination": {"DescribeThings": {"input_token": "NextToken",
                                            "limit_key": "MaxResults"}}}


@pytest.fixture
def model_dir(tmp_path):
    d = tmp_path / "data" / "ec2" / "2016-11-15"
    d.mkdir(parents=True)
    (d / "service-2.json").write_text(json.dumps(MODEL))
    (d / "paginators-1.json").write_text(json.dumps(PAGERS))
    return d


def spec(model_dir, op, name):
    return load_spec(Command("ec2", name, "", op), str(model_dir), str(model_dir.parents[1]))


def test_spec_from_model(model_dir):
    s = spec(model_dir, "DescribeThings", "describe-things")
    names = {p.name: p for p in s.params}
    assert set(names) == {"thing-ids", "filters", "state"}        # token/limit/DryRun hidden
    assert s.paginated and s.supports_dry_run and not s.outfile
    assert names["thing-ids"].kind == "list" and names["thing-ids"].doc == "Thing IDs."
    assert names["filters"].kind == "json" and '"Name"' in names["filters"].skeleton
    assert names["state"].kind == "enum" and names["state"].enum == ["running", "stopped"]


def test_required_first_and_boolean_rename(model_dir):
    s = spec(model_dir, "CreateImage", "create-image")
    assert [p.name for p in s.params][:2] == ["instance-id", "name"]
    reboot = next(p for p in s.params if p.kind == "boolean")
    assert reboot.flag == "--no-reboot" and reboot.negative_flag == "--reboot"


def test_outfile_detected(model_dir):
    assert spec(model_dir, "GetBlob", "get-blob").outfile


def test_custom_command_has_note(model_dir):
    s = load_spec(Command("s3", "cp", "copy"), "", "")
    assert s.custom and s.note


def test_render_posix(model_dir):
    s = spec(model_dir, "DescribeThings", "describe-things")
    vals = {"thing-ids": "t-1, t-2", "filters": '[{"Name":"x","Values":["a b"]}]', "state": "running"}
    line = render(s, vals, Common(region="us-west-2", output="table", query="Things[?x=='y']",
                                  dry_run=True, no_paginate=True, max_items="5"), "posix")
    tokens = shlex.split(line)
    assert tokens[:3] == ["aws", "ec2", "describe-things"]
    assert tokens[tokens.index("--thing-ids") + 1:tokens.index("--thing-ids") + 3] == ["t-1", "t-2"]
    assert json.loads(tokens[tokens.index("--filters") + 1])[0]["Values"] == ["a b"]
    for flag in ("--dry-run", "--no-paginate", "--max-items", "--region", "--output", "--query"):
        assert flag in tokens
    assert tokens[tokens.index("--query") + 1] == "Things[?x=='y']"


def test_render_powershell_uses_files_for_double_quotes(model_dir):
    s = spec(model_dir, "DescribeThings", "describe-things")
    vals = {"filters": '[{"Name":"x"}]'}
    preview = render(s, vals, Common(query="a[?b=='c']"), "powershell", use_files=False)
    assert "--query 'a[?b==''c'']'" in preview
    line = render(s, vals, Common(), "powershell", use_files=True)
    rest = line.split("--filters ", 1)[1]
    # The reference is quoted when the temp path has characters such as spaces
    # or '~' (Windows short names), and bare otherwise.
    ref = rest[1:rest.index("'", 1)] if rest.startswith("'") else rest.split()[0]
    assert ref.startswith("file://")
    path = ref[len("file://"):]
    assert json.loads(open(path, encoding="utf-8").read()) == [{"Name": "x"}]
    if os.name == "posix":
        assert (os.stat(path).st_mode & 0o077) == 0              # private temp file


def test_powershell_quoting():
    assert quote_powershell("--flag", False) == "--flag"
    assert quote_powershell("us-west-2", True) == "us-west-2"
    assert quote_powershell("a b", True) == "'a b'"
    assert quote_powershell("it's", True) == "'it''s'"
    assert quote_powershell("a,b", True) == "'a,b'"            # a bare comma makes an array
    assert quote_powershell("$env:X", True) == "'$env:X'"
    assert (quote_powershell("file://C:/Users/Mike Roughton/x.json", True)
            == "'file://C:/Users/Mike Roughton/x.json'")


def test_booleans(model_dir):
    s = spec(model_dir, "CreateImage", "create-image")
    base = {"instance-id": "i-1", "name": "img"}
    assert "--no-reboot" in render(s, {**base, "no-reboot": True}, Common(), "posix")
    assert "--reboot" in render(s, {**base, "no-reboot": False}, Common(), "posix")
    assert "reboot" not in render(s, {**base, "no-reboot": None}, Common(), "posix")


def test_validation_errors(model_dir):
    s = spec(model_dir, "CreateImage", "create-image")
    with pytest.raises(ParamError, match="--instance-id"):
        render(s, {}, Common(), "posix")
    with pytest.raises(ParamError, match="whole number"):
        render(s, {"instance-id": "i", "name": "n", "count": "1.5"}, Common(), "posix")
    with pytest.raises(ParamError, match="Region"):
        render(s, {"instance-id": "i", "name": "n"}, Common(region="nowhere"), "posix")
    d = spec(model_dir, "DescribeThings", "describe-things")
    with pytest.raises(ParamError, match="JSON"):
        render(d, {"filters": "[{bad json"}, Common(), "posix")
    with pytest.raises(ParamError, match="one of"):
        render(d, {"state": "exploded"}, Common(), "posix")
    with pytest.raises(ParamError, match="output file"):
        render(spec(model_dir, "GetBlob", "get-blob"), {"key": "k"}, Common(), "posix")


def test_newlines_in_extra_args_are_flattened(model_dir):
    s = spec(model_dir, "GetBlob", "get-blob")
    line = render(s, {"key": "k"}, Common(outfile="out.bin", extra="--debug\nrm -rf /"), "posix")
    assert "\n" not in line and line.endswith("--debug rm -rf /")


def test_safety_classification():
    def c(svc, name):
        return Command(svc, name)
    assert is_read_only(c("ec2", "describe-instances")) and is_read_only(c("s3", "ls"))
    assert not is_read_only(c("ec2", "run-instances"))
    assert is_destructive(c("ec2", "terminate-instances"))
    assert is_destructive(c("s3", "rm")) and is_destructive(c("s3", "rb"))
    assert is_destructive(c("s3", "sync"), "aws s3 sync . s3://b --delete")
    assert not is_destructive(c("s3", "sync"), "aws s3 sync . s3://b")
    assert not is_destructive(c("ec2", "describe-instances"))


def test_needs_confirmation():
    from termswitch.aws_params import needs_confirmation
    read = Command("ec2", "describe-instances")
    change = Command("ec2", "run-instances")
    destroy = Command("ec2", "terminate-instances")
    assert not needs_confirmation(read, "", production=True)
    assert not needs_confirmation(change, "", production=False)
    assert needs_confirmation(change, "", production=True)
    assert needs_confirmation(destroy, "", production=False)
    assert needs_confirmation(Command("s3", "sync"), "aws s3 sync . s3://b --delete", False)
