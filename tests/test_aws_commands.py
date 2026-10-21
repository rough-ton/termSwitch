import gzip
import json
import os
import sys
from pathlib import Path

import pytest

from termswitch import aws_commands as ac


def write_model(root: Path, service: str, version: str, ops: dict, full="Svc", gz=False):
    d = root / service / version
    d.mkdir(parents=True, exist_ok=True)
    model = {"metadata": {"serviceFullName": full},
             "operations": {name: {"name": name, "documentation": doc} for name, doc in ops.items()}}
    data = json.dumps(model).encode()
    if gz:
        (d / "service-2.json.gz").write_bytes(gzip.compress(data))
    else:
        (d / "service-2.json").write_bytes(data)


@pytest.fixture
def data_dir(tmp_path):
    root = tmp_path / "aws-cli" / "awscli" / "botocore" / "data"
    write_model(root, "sts", "2011-06-15", {"GetCallerIdentity": "<p>Returns details about the caller. More.</p>"},
                "AWS Security Token Service")
    write_model(root, "ec2", "2015-01-01", {"OldOnly": "old"}, "EC2 old")
    write_model(root, "ec2", "2016-11-15", {"DescribeInstances": "<p>Describes instances.</p>",
                                            "ImportInstance": "removed by the CLI",
                                            "DescribeDBThings": ""}, "Amazon EC2")
    write_model(root, "s3", "2006-03-01", {"ListObjectsV2": "<p>Lists &amp; returns objects.</p>"},
                "Amazon S3", gz=True)
    write_model(root, "codedeploy", "2014-10-06", {"CreateApplication": "Creates."}, "CodeDeploy")
    write_model(root, "socialmessaging", "2024-01-01",
                {"CreateWhatsAppFlow": "Creates a flow."}, "Social Messaging")
    return root


def test_names_match_the_cli():
    assert ac.cli_operation_name("DescribeInstances") == "describe-instances"
    assert ac.cli_operation_name("ListObjectsV2") == "list-objects-v2"
    assert ac.cli_operation_name("DescribeDBInstances") == "describe-db-instances"
    assert ac.cli_operation_name("ListACLs") == "list-acls"
    assert ac.cli_operation_name("CreateWhatsAppFlow") == "create-whatsapp-flow"   # override table


def test_first_sentence():
    assert ac.first_sentence("<p>Returns details. More text.</p>") == "Returns details."
    assert ac.first_sentence("<p>A &amp; B</p>") == "A & B"
    assert ac.first_sentence("") == ""
    assert len(ac.first_sentence("x" * 500)) <= 220


def test_build_catalog(data_dir):
    cat = ac.build_catalog(data_dir, "2.99.0")
    names = {c.cli for s in cat.services.values() for c in s.commands}
    assert "aws sts get-caller-identity" in names
    assert "aws ec2 describe-instances" in names
    assert "aws ec2 old-only" not in names            # newest API version only
    assert "aws ec2 import-instance" not in names     # removed by the CLI
    assert "aws s3api list-objects-v2" in names       # s3 -> s3api, gzipped model
    assert "aws deploy create-application" in names   # codedeploy -> deploy
    assert "aws socialmessaging create-whatsapp-flow" in names
    assert "aws s3 cp" in names and "aws sso login" in names    # curated
    assert cat.services["ec2"].title == "Amazon EC2"
    sts = cat.services["sts"].commands[0]
    assert sts.doc == "Returns details about the caller." and sts.operation == "GetCallerIdentity"
    assert [c.name for c in cat.services["ec2"].commands] == sorted(
        c.name for c in cat.services["ec2"].commands)


def test_no_models_still_lists_curated():
    cat = ac.build_catalog(None)
    assert cat.notice and "aws s3 cp" in {c.cli for c in cat.services["s3"].commands}


@pytest.mark.skipif(sys.platform.startswith("win"), reason="symlinks need privileges")
def test_model_outside_install_is_refused(data_dir, tmp_path):
    outside = tmp_path / "evil.json"
    outside.write_text(json.dumps({"metadata": {}, "operations": {"Evil": {}}}))
    d = data_dir / "evilsvc" / "2020-01-01"
    d.mkdir(parents=True)
    os.symlink(outside, d / "service-2.json")
    cat = ac.build_catalog(data_dir)
    assert "evilsvc" not in cat.services


def test_oversized_model_is_skipped(data_dir, monkeypatch):
    monkeypatch.setattr(ac, "MAX_MODEL_BYTES", 10)
    cat = ac.build_catalog(data_dir)
    assert "sts" not in cat.services


@pytest.mark.parametrize("layout", [
    ("Amazon/AWSCLIV2/aws.exe", "Amazon/AWSCLIV2/awscli/botocore/data"),          # Windows MSI
    ("aws-cli/aws", "aws-cli/awscli/botocore/data"),                                # macOS pkg
    ("aws-cli/v2/2.9.0/bin/aws", "aws-cli/v2/2.9.0/dist/awscli/botocore/data"),     # Linux
    ("Cellar/awscli/2.9.0/bin/aws",
     "Cellar/awscli/2.9.0/libexec/lib/python3.13/site-packages/awscli/botocore/data"),  # Homebrew
])
def test_find_model_dir_layouts(tmp_path, layout):
    exe, data = layout
    (tmp_path / exe).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / exe).write_text("")
    (tmp_path / data / "sts").mkdir(parents=True)
    assert ac.find_model_dir(str(tmp_path / exe)) == (tmp_path / data).resolve()


def test_cache_round_trip(data_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "cache_dir", lambda: tmp_path / "cache")
    cat = ac.build_catalog(data_dir, "2.99.0")
    ac.save_cache(cat, data_dir)
    again = ac.load_cached("2.99.0", data_dir)
    assert again and again.command_count == cat.command_count
    assert ac.load_cached("2.100.0", data_dir) is None     # new CLI version: rebuild
