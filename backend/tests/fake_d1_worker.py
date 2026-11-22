"""A stand-in for `python -m app.d1_worker` that the D1Worker client can start for real (stdin/stdout pipes, a real process).

FAKE_D1_MODE: ok | slow | crash_decide | crash_warm | warm_fail | noise | quiet_decide
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.d1_worker import PREFIX, serve   # noqa: E402

MODE = os.environ.get("FAKE_D1_MODE", "ok")
ANSWERS = {"complexity": {"type": "score", "score": 1.0, "confidence": 0.8},
           "code": {"type": "noul", "noul": 0.1}, "reasoning": {"type": "noul", "noul": 0.9}, "tools": {"type": "noul", "noul": 0.2}}


def run(state, questions):
    if MODE == "crash_decide":
        sys.stderr.write("boom: cuda out of memory\n")
        sys.stderr.flush()
        os._exit(3)
    if MODE == "slow":
        time.sleep(1.5)
    if MODE == "quiet_decide":
        time.sleep(30)
    return {"ok": True, "answers": ANSWERS, "meta": {"echo": state}}


def warm():
    if MODE == "crash_warm":
        os._exit(4)
    if MODE == "warm_fail":
        return {"ok": False, "error": "RuntimeError: weights are missing"}
    return {"ok": True, "device": "cuda", "load_seconds": 0.5}


if MODE == "noise":
    sys.stdout.write("some library banner\n")
    sys.stdout.write(PREFIX + "{not json\n")
    sys.stdout.write(PREFIX + json.dumps({"id": 9999, "ok": True}) + "\n")      # a reply nobody is waiting for
    sys.stdout.flush()
serve(sys.stdin, sys.stdout, run, warm)
