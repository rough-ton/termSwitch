"""Parameters for an AWS CLI command, built from its service model, plus the
logic that turns filled-in values into a command line for a given shell.

Mirrors how the AWS CLI builds its argument table:
- each input member becomes --<kebab-name> (botocore's naming),
- booleans get both --flag and --no-flag,
- argument renames from awscli/customizations/argrename.py,
- paginated operations hide their token/limit members and get
  --starting-token, --page-size, --max-items and --no-paginate,
- operations with a streaming output take a positional output file.
"""

from __future__ import annotations

import atexit
import fnmatch
import json
import os
import re
import shlex
import shutil
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .aws_arg_renames import ARGUMENT_RENAMES
from .aws_commands import Command, _read_model, cli_operation_name, first_sentence

SCALARS = {"string", "integer", "long", "float", "double", "boolean", "timestamp", "blob"}


@dataclass
class Param:
    name: str                       # CLI name without dashes, e.g. "instance-ids"
    member: str                     # model member name, e.g. "InstanceIds"
    kind: str                       # string|integer|float|boolean|timestamp|blob|enum|list|json
    required: bool = False
    doc: str = ""
    enum: list[str] = field(default_factory=list)
    item_kind: str = ""             # for kind == "list": scalar type of the items
    skeleton: str = ""              # for kind == "json": an example JSON value
    deprecated: bool = False
    negative: str = ""              # for booleans: the --no-... flag, after CLI renames

    @property
    def flag(self) -> str:
        return f"--{self.name}"

    @property
    def negative_flag(self) -> str:
        return f"--{self.negative or 'no-' + self.name}"


@dataclass
class CommandSpec:
    command: Command
    params: list[Param] = field(default_factory=list)
    paginated: bool = False
    outfile: bool = False           # needs a positional output file (streaming output)
    supports_dry_run: bool = False
    custom: bool = False            # a CLI customization without a model
    note: str = ""


# --------------------------------------------------------------------------
# Building the spec from the model
# --------------------------------------------------------------------------

def _rename(service: str, op: str, arg: str) -> str:
    exact = f"{service}.{op}.{arg}"
    if exact in ARGUMENT_RENAMES:
        return ARGUMENT_RENAMES[exact]
    for pattern, new in ARGUMENT_RENAMES.items():
        if "*" in pattern and fnmatch.fnmatchcase(exact, pattern):
            return new
    return arg


def _skeleton(shapes: dict, name: str, depth: int = 0, seen: frozenset = frozenset()):
    shape = shapes.get(name, {})
    t = shape.get("type")
    if name in seen or depth > 4:
        return {}
    seen = seen | {name}
    if t == "structure":
        return {m: _skeleton(shapes, ref.get("shape", ""), depth + 1, seen)
                for m, ref in list(shape.get("members", {}).items())[:12]}
    if t == "list":
        return [_skeleton(shapes, shape.get("member", {}).get("shape", ""), depth + 1, seen)]
    if t == "map":
        return {"KeyName": _skeleton(shapes, shape.get("value", {}).get("shape", ""),
                                     depth + 1, seen)}
    if "enum" in shape:
        return shape["enum"][0]
    return {"string": "string", "integer": 0, "long": 0, "float": 0.0, "double": 0.0,
            "boolean": True, "timestamp": "1970-01-01T00:00:00", "blob": "blob"}.get(t, "")


@lru_cache(maxsize=16)
def _cached_model(path: str, root: str) -> dict | None:
    """Parsed models are reused while the user moves around one service."""
    return _read_model(Path(path), Path(root))


def _paginator(model_dir: str, op_name: str, root: Path) -> dict | None:
    for name in ("paginators-1.json", "paginators-1.json.gz"):
        p = Path(model_dir) / name
        if p.is_file():
            data = _cached_model(str(p), str(root)) or {}
            return data.get("pagination", {}).get(op_name)
    return None


def load_spec(command: Command, model_dir: str, data_root: str) -> CommandSpec:
    spec = CommandSpec(command)
    if not command.operation or not model_dir or not data_root:
        spec.custom = True
        spec.note = ("This command is an AWS CLI customization, so its options are not in "
                     "the service models. Type them in Extra arguments, or use Docs.")
        return spec
    root = Path(data_root).resolve()
    model_path = None
    for name in ("service-2.json", "service-2.json.gz"):
        if (Path(model_dir) / name).is_file():
            model_path = Path(model_dir) / name
            break
    model = _cached_model(str(model_path), str(root)) if model_path else None
    if not model:
        spec.note = "The service model could not be read."
        return spec
    shapes = model.get("shapes", {})
    op = model.get("operations", {}).get(command.operation, {})

    # Same rule as botocore's has_streaming_output: the output's payload member
    # is a blob, so the CLI takes a positional output file.
    out_shape = shapes.get(op.get("output", {}).get("shape", ""), {})
    payload = out_shape.get("payload")
    if payload:
        ref = out_shape.get("members", {}).get(payload, {})
        if shapes.get(ref.get("shape", ""), {}).get("type") == "blob":
            spec.outfile = True

    pager = _paginator(model_dir, command.operation, root)
    hidden = set()
    if pager:
        spec.paginated = True
        tokens = pager.get("input_token", [])
        hidden.update(tokens if isinstance(tokens, list) else [tokens])
        if pager.get("limit_key"):
            hidden.add(pager["limit_key"])

    in_shape = shapes.get(op.get("input", {}).get("shape", ""), {})
    required = set(in_shape.get("required", []))
    for member, ref in in_shape.get("members", {}).items():
        if member in hidden or not isinstance(ref, dict):
            continue
        target = shapes.get(ref.get("shape", ""), {})
        t = target.get("type", "string")
        doc = first_sentence(ref.get("documentation") or target.get("documentation") or "")
        cli = _rename(command.service, command.name, cli_operation_name(member))
        p = Param(cli, member, "string", member in required, doc,
                  deprecated=bool(ref.get("deprecated") or target.get("deprecated")))
        if member == "DryRun":
            spec.supports_dry_run = True
            continue                       # offered in the Common section
        if "enum" in target:
            p.kind, p.enum = "enum", list(target["enum"])
        elif t in ("integer", "long"):
            p.kind = "integer"
        elif t in ("float", "double"):
            p.kind = "float"
        elif t == "boolean":
            p.kind = "boolean"
            p.negative = _rename(command.service, command.name, "no-" + cli_operation_name(member))
        elif t in ("timestamp", "blob"):
            p.kind = t
        elif t == "list":
            item = shapes.get(target.get("member", {}).get("shape", ""), {})
            if item.get("type", "string") in SCALARS:
                p.kind, p.item_kind = "list", item.get("type", "string")
            else:
                p.kind = "json"
                p.skeleton = json.dumps(_skeleton(shapes, ref.get("shape", "")), indent=2)
        elif t in ("structure", "map"):
            p.kind = "json"
            p.skeleton = json.dumps(_skeleton(shapes, ref.get("shape", "")), indent=2)
        spec.params.append(p)
    spec.params.sort(key=lambda p: (not p.required, p.name))
    return spec


# --------------------------------------------------------------------------
# Values and command lines
# --------------------------------------------------------------------------

@dataclass
class Common:
    region: str = ""
    output: str = ""
    query: str = ""
    no_paginate: bool = False
    page_size: str = ""
    max_items: str = ""
    starting_token: str = ""
    dry_run: bool = False
    outfile: str = ""
    extra: str = ""                 # free-form arguments, appended as typed


class ParamError(Exception):
    """A filled-in value that cannot be used (bad number, invalid JSON...)."""


_TEMP_DIR: str | None = None


def _temp_dir() -> str:
    global _TEMP_DIR
    if _TEMP_DIR is None or not os.path.isdir(_TEMP_DIR):
        _TEMP_DIR = tempfile.mkdtemp(prefix="termswitch-params-")
        atexit.register(shutil.rmtree, _TEMP_DIR, True)
    return _TEMP_DIR


def write_param_file(value: str) -> str:
    """Save a value to a private temp file and return a file:// reference.

    Used for JSON and quote-heavy values in PowerShell, where passing them
    inline to a native program is unreliable across PowerShell versions."""
    fd, path = tempfile.mkstemp(dir=_temp_dir(), suffix=".json", prefix="param-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value)
    return "file://" + path.replace("\\", "/")


def _list_items(text: str) -> list[str]:
    text = text.strip()
    if text.startswith("["):
        try:
            data = json.loads(text)
            return [str(x) for x in data]
        except ValueError:
            raise ParamError("List value is not valid JSON.")
    return [t for t in re.split(r"[\s,]+", text) if t]


def build_args(spec: CommandSpec, values: dict[str, object], common: Common) -> list[tuple[str, bool]]:
    """Return argv after 'aws': a list of (token, is_value).

    ``values`` maps Param.name to the entered value: str for text fields,
    bool or None for booleans (None = not set)."""
    cmd = spec.command
    args: list[tuple[str, bool]] = [(cmd.service, False), (cmd.name, False)]
    missing = []
    for p in spec.params:
        v = values.get(p.name)
        if p.kind == "boolean":
            if v is True:
                args.append((p.flag, False))
            elif v is False:
                args.append((p.negative_flag, False))
            elif p.required:
                missing.append(p.flag)
            continue
        text = (v or "").strip() if isinstance(v, str) else ""
        if not text:
            if p.required:
                missing.append(p.flag)
            continue
        if p.kind == "integer":
            if not re.fullmatch(r"-?\d+", text):
                raise ParamError(f"{p.flag} must be a whole number.")
        elif p.kind == "float":
            try:
                float(text)
            except ValueError:
                raise ParamError(f"{p.flag} must be a number.")
        elif p.kind == "enum" and p.enum and text not in p.enum:
            raise ParamError(f"{p.flag} must be one of: {', '.join(p.enum[:8])}")
        if p.kind == "list":
            items = _list_items(text)
            args.append((p.flag, False))
            args.extend((item, True) for item in items)
            continue
        if p.kind == "json" and text[:1] in "[{":
            try:
                json.loads(text)
            except ValueError as exc:
                raise ParamError(f"{p.flag} is not valid JSON: {exc}")
            args.append((p.flag, False))
            args.append((text, True))
            continue
        args.append((p.flag, False))
        args.append((text, True))
    if missing:
        raise ParamError("Required: " + ", ".join(missing))

    if spec.outfile:
        if not common.outfile.strip():
            raise ParamError("This command writes its result to a file. Enter an output file.")
        args.append((common.outfile.strip(), True))
    if common.dry_run and spec.supports_dry_run:
        args.append(("--dry-run", False))
    if spec.paginated:
        if common.no_paginate:
            args.append(("--no-paginate", False))
        for flag, val in (("--page-size", common.page_size), ("--max-items", common.max_items)):
            if val.strip():
                if not val.strip().isdigit():
                    raise ParamError(f"{flag} must be a whole number.")
                args += [(flag, False), (val.strip(), True)]
        if common.starting_token.strip():
            args += [("--starting-token", False), (common.starting_token.strip(), True)]
    if common.region.strip():
        if not re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d+", common.region.strip()):
            raise ParamError("Region looks wrong (for example us-west-2).")
        args += [("--region", False), (common.region.strip(), True)]
    if common.output:
        args += [("--output", False), (common.output, True)]
    if common.query.strip():
        args += [("--query", False), (common.query.strip(), True)]
    return args


_PS_SAFE = re.compile(r"^[A-Za-z0-9._/:=+\-]+$")


def quote_powershell(token: str, is_value: bool) -> str:
    if not is_value or _PS_SAFE.match(token):
        return token
    return "'" + token.replace("'", "''") + "'"


def render(spec: CommandSpec, values: dict[str, object], common: Common, shell: str,
           use_files: bool = True) -> str:
    """The full command line for ``shell`` ("powershell" or "posix")."""
    args = build_args(spec, values, common)
    out = ["aws"]
    for token, is_value in args:
        if shell == "powershell":
            if is_value and '"' in token and use_files:
                # PowerShell 5.1 and 7.x pass embedded double quotes to native
                # programs differently; a file:// reference avoids the problem.
                token = write_param_file(token)
            out.append(quote_powershell(token, is_value))
        else:
            out.append(shlex.quote(token) if is_value else token)
    extra = " ".join(common.extra.replace("\r", " ").replace("\n", " ").split())
    if extra:
        out.append(extra)
    return " ".join(out)


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------

READ_ONLY_PREFIXES = ("describe-", "list-", "get-", "head-", "lookup-", "batch-get-",
                      "search-", "query", "scan", "select", "validate-", "estimate-",
                      "preview-", "simulate-", "test-", "check-", "detect-", "filter-")
READ_ONLY_COMMANDS = {("s3", "ls"), ("s3", "presign"), ("sts", "get-caller-identity"),
                      ("configure", "list"), ("configure", "get"),
                      ("configure", "list-profiles"), ("configure", "export-credentials"),
                      ("logs", "tail"), ("logs", "start-live-tail"), ("eks", "get-token"),
                      ("ecr", "get-login-password"), ("history", "list"), ("history", "show"),
                      ("sso", "login"), ("sso", "logout")}
DESTRUCTIVE_PREFIXES = ("delete-", "terminate-", "remove-", "deregister-", "disable-",
                        "revoke-", "detach-", "purge-", "batch-delete-", "reject-")
DESTRUCTIVE_COMMANDS = {("s3", "rm"), ("s3", "rb"), ("s3", "mv")}


def is_read_only(command: Command) -> bool:
    if (command.service, command.name) in READ_ONLY_COMMANDS:
        return True
    return command.name.startswith(READ_ONLY_PREFIXES)


def is_destructive(command: Command, command_line: str = "") -> bool:
    if (command.service, command.name) in DESTRUCTIVE_COMMANDS:
        return True
    if command.service == "s3" and command.name == "sync" and " --delete" in command_line:
        return True
    return command.name.startswith(DESTRUCTIVE_PREFIXES)


def needs_confirmation(command: Command, command_line: str, production: bool) -> bool:
    """Run asks first for destructive commands on any profile, and for anything
    that is not read-only on a production profile."""
    if is_destructive(command, command_line):
        return True
    return production and not is_read_only(command)
