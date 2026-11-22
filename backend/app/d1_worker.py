"""A tiny line-protocol worker around the local d1 decision model, used by Auto mode's router.

Runs INSIDE the isolated d1 environment (`<env python> -m app.d1_worker`, env D1_HOME / D1_MODEL / PYTHONPATH), started by
`d1_client.D1Worker` - never by the user and never before an install exists. It keeps the model warm between requests.

Protocol (one JSON object per line):
    in : {"id": 1, "op": "warm"}                                   load the model now
         {"id": 2, "op": "decide", "state": ..., "questions": {...}}  one forward pass
         {"id": 3, "op": "ping"}
    out: "@@d1 " + {"id": 2, "ok": true, "answers": {...}, ...}     (mcp_d1.run's result, plus the id)
Only lines starting with "@@d1 " are replies; anything else the model libraries print goes to stderr.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable, TextIO

PREFIX = "@@d1 "


def _warm() -> dict:
    from . import mcp_d1
    try:
        loaded, fresh = mcp_d1._get()
    except Exception as e:  # noqa: BLE001 - reported to the parent, which shows it
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
    return {"ok": True, "device": loaded.device, "dtype": loaded.dtype, "load_seconds": loaded.load_seconds, "loaded_now": fresh}


def handle(line: str, run: Callable[..., dict], warm: Callable[[], dict]) -> dict:
    """One request line -> one reply dict. Never raises."""
    try:
        req: Any = json.loads(line)
    except ValueError:
        return {"id": None, "ok": False, "error": "the request is not valid JSON"}
    if not isinstance(req, dict):
        return {"id": None, "ok": False, "error": "the request must be a JSON object"}
    rid, op = req.get("id"), req.get("op", "decide")
    if op == "ping":
        return {"id": rid, "ok": True}
    if op == "warm":
        return {"id": rid, **warm()}
    if op != "decide":
        return {"id": rid, "ok": False, "error": f"unknown op '{op}'"}
    return {"id": rid, **run(req.get("state"), req.get("questions"))}


def serve(stdin: TextIO, out: TextIO, run: Callable[..., dict] | None = None, warm: Callable[[], dict] | None = None) -> None:
    """Answer requests until stdin closes (the parent exited or closed the pipe)."""
    if run is None:
        from . import mcp_d1
        run = mcp_d1.run
    warm = warm or _warm
    for line in stdin:
        if not line.strip():
            continue
        out.write(PREFIX + json.dumps(handle(line, run, warm), default=str) + "\n")
        out.flush()


def main() -> None:
    proto = sys.stdout
    sys.stdout = sys.stderr            # library chatter must never corrupt the protocol stream
    serve(sys.stdin, proto)


if __name__ == "__main__":
    main()
