"""AWS CLI command catalog, read from the AWS CLI the user has installed.

AWS CLI v2 ships botocore's service models inside its own install
(``.../awscli/botocore/data/<service>/<api-version>/service-2.json``). Reading
them means the list always matches the user's CLI version, needs no network
access and nothing has to be bundled. CLI commands that are not in the models
(``s3 cp``, ``sso login``...) come from a curated list.

Model files are treated as data only: read with size limits and only from
inside the resolved CLI install directory.
"""

from __future__ import annotations

import gzip
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .aws_name_overrides import OVERRIDES
from .launcher import build_env, find_aws

INDEX_VERSION = 1
MAX_MODEL_BYTES = 64 * 1024 * 1024

# The CLI exposes some services under a different command name.
SERVICE_RENAMES = {"s3": "s3api", "codedeploy": "deploy", "config": "configservice"}

# Operations the CLI removes (awscli/customizations/removals.py): event-stream
# and legacy calls that do not work from the command line.
REMOVED = {
    "ses": {"delete-verified-email-address", "list-verified-email-addresses",
            "verify-email-address"},
    "ec2": {"import-instance", "import-volume"},
    "emr": {"run-job-flow", "describe-job-flows", "add-job-flow-steps", "terminate-job-flows",
            "list-bootstrap-actions", "list-instance-groups", "set-termination-protection",
            "set-keep-job-flow-alive-when-no-steps", "set-visible-to-all-users",
            "set-unhealthy-node-replacement"},
    "kinesis": {"subscribe-to-shard"},
    "lexv2-runtime": {"start-conversation"},
    "lambda": {"invoke-with-response-stream"},
    "sagemaker-runtime": {"invoke-endpoint-with-response-stream"},
    "bedrock-runtime": {"invoke-model-with-bidirectional-stream",
                        "invoke-model-with-response-stream", "converse-stream"},
    "bedrock-agent-runtime": {"agentic-retrieve-stream", "invoke-agent", "invoke-flow",
                              "invoke-inline-agent", "optimize-prompt",
                              "retrieve-and-generate-stream"},
    "qbusiness": {"chat"},
    "iotsitewise": {"invoke-assistant"},
    "logs": {"get-log-object"},
    "polly": {"start-speech-synthesis-stream"},
}

# High-level commands the CLI adds on top of the service models.
CUSTOM_COMMANDS = {
    "s3": ("Amazon S3 (high-level commands)", {
        "cp": "Copy a local file or S3 object to another location locally or in S3.",
        "ls": "List S3 objects and common prefixes under a prefix, or all S3 buckets.",
        "mb": "Create an S3 bucket.",
        "mv": "Move a local file or S3 object to another location locally or in S3.",
        "presign": "Generate a pre-signed URL for an Amazon S3 object.",
        "rb": "Delete an empty S3 bucket (or a non-empty one with --force).",
        "rm": "Delete an S3 object.",
        "sync": "Sync directories and S3 prefixes recursively.",
        "website": "Set the website configuration for a bucket.",
    }),
    "sso": (None, {
        "login": "Retrieve and cache an IAM Identity Center access token.",
        "logout": "Remove cached IAM Identity Center tokens and credentials.",
    }),
    "configure": ("AWS CLI configuration", {
        "export-credentials": "Export the resolved credentials for a profile.",
        "get": "Get a configuration value from the config file.",
        "list": "List the AWS CLI configuration data.",
        "list-profiles": "List the profile names in the config files.",
        "set": "Set a configuration value in the config file.",
        "sso": "Configure a profile to use IAM Identity Center.",
        "sso-session": "Configure an sso-session section in the config file.",
    }),
    "logs": (None, {
        "start-live-tail": "Stream log events from log groups in real time.",
        "tail": "Show log events from a log group, optionally following new events.",
    }),
    "cloudformation": (None, {
        "deploy": "Deploy a CloudFormation template using a change set.",
        "package": "Upload local artifacts referenced by a template to S3.",
    }),
    "eks": (None, {
        "get-token": "Get a token for authenticating to an EKS cluster.",
        "update-kubeconfig": "Configure kubectl to connect to an EKS cluster.",
    }),
    "ecr": (None, {
        "get-login-password": "Get a password to log Docker in to an ECR registry.",
    }),
    "codeartifact": (None, {
        "login": "Configure pip, npm, twine, NuGet or Swift to use a CodeArtifact repository.",
    }),
    "deploy": (None, {
        "push": "Bundle and upload an application revision to S3.",
        "register": "Register an on-premises instance with CodeDeploy.",
    }),
    "emr": (None, {
        "create-cluster": "Create an Amazon EMR cluster.",
        "ssh": "SSH into the master node of an EMR cluster.",
    }),
    "history": ("AWS CLI command history", {
        "list": "Show previously run AWS CLI commands (if history is enabled).",
        "show": "Show the details of a previously run AWS CLI command.",
    }),
}


@dataclass
class Command:
    service: str              # CLI service name, e.g. "ec2"
    name: str                 # CLI operation name, e.g. "describe-instances"
    doc: str = ""             # first sentence of the documentation
    operation: str = ""       # model operation name, e.g. "DescribeInstances"; "" if custom

    @property
    def cli(self) -> str:
        return f"aws {self.service} {self.name}"


@dataclass
class Service:
    name: str                 # CLI name
    title: str                # serviceFullName
    model_dir: str = ""       # directory holding service-2.json, "" if custom only
    commands: list[Command] = field(default_factory=list)


@dataclass
class Catalog:
    cli_version: str = ""
    data_dir: str = ""
    services: dict[str, Service] = field(default_factory=dict)
    notice: str = ""          # explains a partial catalog (no models found...)

    @property
    def command_count(self) -> int:
        return sum(len(s.commands) for s in self.services.values())


# --------------------------------------------------------------------------
# Names and docs
# --------------------------------------------------------------------------

_FIRST_CAP = re.compile(r"(.)([A-Z][a-z]+)")
_END_CAP = re.compile(r"([a-z0-9])([A-Z])")
_SPECIAL_PLURAL = re.compile(r"[A-Z]{2,}s$")


def cli_operation_name(name: str) -> str:
    """CamelCase operation name to the CLI's kebab-case, matching botocore's
    xform_name: DescribeInstances -> describe-instances, ListACLs -> list-acls."""
    if name in OVERRIDES:
        return OVERRIDES[name]
    special = _SPECIAL_PLURAL.search(name)
    if special:
        matched = special.group()
        name = f"{name[:-len(matched)]}-{matched.lower()}"
    s1 = _FIRST_CAP.sub(r"\1-\2", name)
    return _END_CAP.sub(r"\1-\2", s1).lower()


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def first_sentence(doc_html: str, limit: int = 220) -> str:
    text = _WS.sub(" ", html.unescape(_TAG.sub(" ", doc_html or ""))).strip()
    if not text:
        return ""
    m = re.search(r"(.+?[.!?])(\s|$)", text)
    sentence = m.group(1) if m else text
    return sentence if len(sentence) <= limit else sentence[:limit - 3].rstrip() + "..."


# --------------------------------------------------------------------------
# Finding the models
# --------------------------------------------------------------------------

def aws_cli_version(aws: str | None = None) -> str:
    aws = aws or find_aws()
    if not aws:
        return ""
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        proc = subprocess.run([aws, "--version"], capture_output=True, text=True, timeout=20,
                              env=build_env(), stdin=subprocess.DEVNULL, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = re.search(r"aws-cli/(\S+)", proc.stdout + proc.stderr)
    return m.group(1) if m else ""


def _install_root(aws_path: str) -> Path:
    """The directory the AWS CLI is installed in, following the launcher
    symlink (macOS pkg and Homebrew put a symlink on PATH)."""
    real = Path(os.path.realpath(aws_path))
    return real.parent


def find_model_dir(aws_path: str | None = None) -> Path | None:
    aws_path = aws_path or find_aws()
    if not aws_path:
        return None
    start = _install_root(aws_path)
    # Walk up a few levels: <root>/bin/aws, <root>/v2/current/bin/aws ...
    roots = [start, *list(start.parents)[:4]]
    patterns = [
        "awscli/botocore/data",                                # Windows MSI, macOS pkg
        "dist/awscli/botocore/data",                           # Linux installer
        "libexec/lib/python3*/site-packages/awscli/botocore/data",   # Homebrew
        "lib/python3*/site-packages/awscli/botocore/data",
    ]
    for root in roots:
        for pat in patterns:
            for cand in sorted(root.glob(pat), reverse=True):
                if cand.is_dir() and (cand / "sts").is_dir():
                    return cand.resolve()
    return None


def _read_model(path: Path, root: Path) -> dict | None:
    """Read a service-2.json(.gz) safely: inside root, size-limited, JSON only."""
    try:
        real = path.resolve(strict=True)
        real.relative_to(root)          # never follow a link out of the CLI install
        if real.stat().st_size > MAX_MODEL_BYTES:
            return None
        if real.suffix == ".gz":
            with gzip.open(real, "rb") as fh:
                data = fh.read(MAX_MODEL_BYTES + 1)
            if len(data) > MAX_MODEL_BYTES:
                return None
        else:
            data = real.read_bytes()
        model = json.loads(data)
    except (OSError, ValueError):
        return None
    return model if isinstance(model, dict) else None


def _newest_model(service_dir: Path) -> Path | None:
    versions = sorted((d for d in service_dir.iterdir() if d.is_dir()),
                      key=lambda d: d.name, reverse=True)
    for v in versions:
        for name in ("service-2.json", "service-2.json.gz"):
            if (v / name).is_file():
                return v / name
    return None


# --------------------------------------------------------------------------
# Building the catalog
# --------------------------------------------------------------------------

def build_catalog(data_dir: Path | None, cli_version: str = "", progress=None) -> Catalog:
    cat = Catalog(cli_version=cli_version, data_dir=str(data_dir or ""))
    if data_dir and data_dir.is_dir():
        root = data_dir.resolve()
        dirs = sorted(d for d in root.iterdir() if d.is_dir())
        for i, sdir in enumerate(dirs):
            if progress:
                progress(i + 1, len(dirs))
            model_path = _newest_model(sdir)
            if not model_path:
                continue
            model = _read_model(model_path, root)
            if not model:
                continue
            meta = model.get("metadata", {}) if isinstance(model.get("metadata"), dict) else {}
            cli_name = SERVICE_RENAMES.get(sdir.name, sdir.name)
            removed = REMOVED.get(cli_name, set())
            svc = Service(cli_name, str(meta.get("serviceFullName") or cli_name),
                          str(model_path.parent))
            ops = model.get("operations", {})
            if not isinstance(ops, dict):
                continue
            for op_name, op in ops.items():
                if not isinstance(op, dict):
                    continue
                cli_op = cli_operation_name(op_name)
                if cli_op in removed:
                    continue
                svc.commands.append(Command(cli_name, cli_op,
                                            first_sentence(str(op.get("documentation", ""))),
                                            op_name))
            cat.services[cli_name] = svc
    else:
        cat.notice = ("AWS CLI v2 service models were not found, so only the built-in list "
                      "of common commands is shown. Run Help > Check dependencies.")

    for svc_name, (title, cmds) in CUSTOM_COMMANDS.items():
        svc = cat.services.get(svc_name)
        if svc is None:
            svc = cat.services[svc_name] = Service(svc_name, title or svc_name)
        existing = {c.name for c in svc.commands}
        for name, doc in cmds.items():
            if name not in existing:
                svc.commands.append(Command(svc_name, name, doc))
    for svc in cat.services.values():
        svc.commands.sort(key=lambda c: c.name)
    return cat


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def cache_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "termSwitch" / "cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "termSwitch"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "termswitch"


def _cache_file() -> Path:
    return cache_dir() / "aws-commands.json"


def _cache_key(cli_version: str, data_dir: Path | None) -> dict:
    mtime = 0.0
    if data_dir:
        try:
            mtime = data_dir.stat().st_mtime
        except OSError:
            pass
    return {"index": INDEX_VERSION, "cli": cli_version, "dir": str(data_dir or ""),
            "mtime": mtime, "custom": len(CUSTOM_COMMANDS)}


def load_cached(cli_version: str, data_dir: Path | None) -> Catalog | None:
    try:
        raw = json.loads(_cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if raw.get("key") != _cache_key(cli_version, data_dir):
        return None
    try:
        cat = Catalog(raw["cli_version"], raw["data_dir"], {}, raw.get("notice", ""))
        for s in raw["services"]:
            cat.services[s["name"]] = Service(
                s["name"], s["title"], s.get("model_dir", ""),
                [Command(**c) for c in s["commands"]])
        return cat
    except (KeyError, TypeError):
        return None


def save_cache(cat: Catalog, data_dir: Path | None) -> None:
    path = _cache_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "key": _cache_key(cat.cli_version, data_dir),
            "cli_version": cat.cli_version, "data_dir": cat.data_dir, "notice": cat.notice,
            "services": [{"name": s.name, "title": s.title, "model_dir": s.model_dir,
                          "commands": [asdict(c) for c in s.commands]}
                         for s in cat.services.values()],
        }
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".aws-commands-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass


def load_catalog(force: bool = False, progress=None) -> Catalog:
    """Cached catalog for the installed CLI, rebuilt when the CLI changes."""
    aws = find_aws()
    version = aws_cli_version(aws) if aws else ""
    data_dir = find_model_dir(aws) if aws else None
    if not force:
        cached = load_cached(version, data_dir)
        if cached:
            return cached
    cat = build_catalog(data_dir, version, progress)
    if not aws:
        cat.notice = ("The AWS CLI was not found, so only the built-in list of common "
                      "commands is shown. Run Help > Check dependencies.")
    save_cache(cat, data_dir)
    return cat


def matches(cmd: Command, needle: str) -> bool:
    """Case-insensitive substring match on 'service command'."""
    return needle in f"{cmd.service} {cmd.name}"
