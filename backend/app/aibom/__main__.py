"""Write the AI-BOM to a file (for CI or an audit):

    python -m app.aibom --level public --out agent-studio.cdx.json --validate

Reads the same database the app uses (AGENT_STUDIO_HOME, default <repo>/data). Never prints or writes secrets.
Exit status: 0 ok, 1 the document failed validation (with --validate), 2 bad arguments.
"""
from __future__ import annotations

import argparse
import sys

from .redact import LEVELS


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.aibom", description="Write the Agent Studio AI bill of materials (CycloneDX 1.7).")
    ap.add_argument("--level", choices=LEVELS, default="public", help="redaction level (default: public)")
    ap.add_argument("--scope", default="system", help="'system' or the bom-ref of one agent, MCP server, model or skill, e.g. agent:engineer")
    ap.add_argument("--out", default="-", help="file to write, or - for stdout (default)")
    ap.add_argument("--random-serial", action="store_true", help="use a random serial number instead of one derived from the content")
    ap.add_argument("--validate", action="store_true", help="validate against the CycloneDX 1.7 schema; exit 1 when it fails")
    a = ap.parse_args(argv)
    from ..main import State                       # imported late: building State opens the database
    from . import builder
    from .validate import validate_bom
    try:
        doc = builder.build_bom(State(), a.level, a.scope, not a.random_serial)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    raw = builder.serialize(doc)
    if a.out == "-":
        sys.stdout.buffer.write(raw)
    else:
        with open(a.out, "wb") as f:
            f.write(raw)
        print(f"wrote {a.out} ({len(raw) // 1024} KB, {len(doc['components'])} components, {len(doc.get('services', []))} services)", file=sys.stderr)
    if a.validate:
        v = validate_bom(doc)
        if not v["ok"]:
            for e in v["errors"][:20]:
                print(f"invalid: {e['path']}: {e['message']}", file=sys.stderr)
            return 1
        print("valid CycloneDX 1.7", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
