"""One-shot entry point for the Agent Studio UI: read {"tool": ..., "args": {...}} as JSON on stdin, run it with the same
code the MCP server uses, print the JSON result as the last line of stdout.

Runs inside the isolated time-series environment: `<env python> -m app.ts_cli`. Models load fresh on every call.
"""
from __future__ import annotations

import json
import sys

from . import mcp_ts

TOOLS = {"forecast": mcp_ts.run_forecast, "compare": mcp_ts.compare}


def main(raw: str | None = None) -> int:
    try:
        req = json.loads(sys.stdin.read() if raw is None else raw)
        fn = TOOLS[req["tool"]]
        out = fn(**req["args"])
    except (ValueError, KeyError, TypeError) as e:
        out = {"ok": False, "error": f"bad request: {type(e).__name__}: {str(e)[:200]}"}
    print(json.dumps(out), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
