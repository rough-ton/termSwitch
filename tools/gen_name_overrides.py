"""Regenerate termswitch/aws_name_overrides.py from botocore's naming exceptions.

    pip install botocore
    python tools/gen_name_overrides.py
"""
import importlib.metadata
import pprint
from pathlib import Path

import botocore

overrides = {k[0]: v for k, v in botocore._xform_cache.items() if k[1] == "-"}
out = Path(__file__).resolve().parents[1] / "termswitch" / "aws_name_overrides.py"
out.write_text(
    '"""Operation names the AWS CLI spells differently from the generic\n'
    "CamelCase-to-kebab rule. Built by tools/gen_name_overrides.py from\n"
    f'botocore {importlib.metadata.version("botocore")}; regenerate when botocore adds entries."""\n\n'
    "OVERRIDES = " + pprint.pformat(dict(sorted(overrides.items())), width=96) + "\n",
    encoding="utf-8")
print(f"{len(overrides)} overrides written to {out}")
