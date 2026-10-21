"""Regenerate termswitch/aws_arg_renames.py from the AWS CLI source.

The AWS CLI renames some arguments (awscli/customizations/argrename.py). The
file is read with ast, never imported or executed.

    git clone --depth 1 -b v2 https://github.com/aws/aws-cli
    python tools/gen_arg_renames.py path/to/aws-cli
"""
import ast
import pprint
import sys
from pathlib import Path


def main(src: str) -> None:
    path = Path(src) / "awscli" / "customizations" / "argrename.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    renames = None
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", "") == "ARGUMENT_RENAMES"):
            renames = ast.literal_eval(node.value)
    if not isinstance(renames, dict):
        raise SystemExit("ARGUMENT_RENAMES not found")
    out = Path(__file__).resolve().parents[1] / "termswitch" / "aws_arg_renames.py"
    out.write_text(
        '"""Argument names the AWS CLI renames, keyed "<service>.<command>.<arg>" with\n'
        '"*" wildcards. Built by tools/gen_arg_renames.py from the AWS CLI source."""\n\n'
        "ARGUMENT_RENAMES = " + pprint.pformat(dict(sorted(renames.items())), width=96) + "\n",
        encoding="utf-8")
    print(f"{len(renames)} renames written to {out}")


if __name__ == "__main__":
    main(sys.argv[1])
