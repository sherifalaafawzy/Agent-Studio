"""Evidence checks for the scorecards: "was it really tested?" must mean a test that SUCCEEDED, not just a record that someone tried.

Definitions (tests hold the cards to them):

* A red-team run counts only when it finished (`done`) AND at least one probe really reached the agent (`requests > 0`). A failed run,
  a run that sent 0 probes, a cancelled run and an interrupted run never count; they show as "tried but did not work" with the reason.
* A successful run older than `REDTEAM_FRESH_DAYS` is "out of date": still shown, but not green.
* An evaluation run counts only when it finished (`done`) and at least one case got an answer (not every case an error), within the
  last `EVAL_FRESH_DAYS` days.
"""
from __future__ import annotations

import json
import time
from typing import Any

from . import redteam as RT

REDTEAM_FRESH_DAYS = 90
EVAL_FRESH_DAYS = 30
FIX_REDTEAM = "Install a red-team tool and run it against {who}."
TRIED = {"failed": "failed", "cancelled": "was cancelled", "interrupted": "was interrupted when the app closed", "done": "finished but sent no probes"}


def _days(t: float, now: float) -> int:
    return max(0, int((now - t) // 86400))


def redteam(s: Any, agent: str | None = None, now: float | None = None) -> tuple[str, str, str]:
    """(status, detail, fix) for the "attacked on purpose" check; `agent` limits it to runs aimed at that agent."""
    now = time.time() if now is None else now
    who = "this agent" if agent else "your main agent"
    rows = s.db.query("SELECT id, tool, status, requests, started, ended, error FROM redteam_runs WHERE status!='running'" + (" AND agent=?" if agent else "") + " ORDER BY started DESC",
                      (agent,) if agent else ())
    good = [r for r in rows if r["status"] == "done" and (r["requests"] or 0) > 0]
    if good:
        last = good[0]
        age = _days(last["ended"] or last["started"], now)
        if age <= REDTEAM_FRESH_DAYS:
            return "pass", f"{len(good)} successful red-team run(s){' against this agent' if agent else ''}; the last sent {last['requests']} probe(s), {age} day(s) ago.", ""
        return ("warn", f"Out of date: the last successful red-team run was {age} days ago (the limit is {REDTEAM_FRESH_DAYS}).", f"Run the red-team tool again against {who}.")
    if rows:
        last = rows[0]
        diag = RT.diagnose(last["tool"], RT.log_tail(last["id"])) if last["status"] == "failed" else None
        why = diag["short"] if diag else (last["error"] or TRIED.get(last["status"], "did not finish properly"))
        what = TRIED.get(last["status"], "did not finish properly")
        return ("warn", f"Tried but it did not work: the last red-team run {what} ({last['requests'] or 0} probes sent): {why}.",
                "Open Security > Red team and read the run's log, or try another tool (garak and PyRIT do not need an e-mail address).")
    return "warn", f"No red-team run {'against this agent ' if agent else ''}has finished yet.", FIX_REDTEAM.format(who=who)


def evals(s: Any, agent: str | None = None, now: float | None = None) -> tuple[str, str, str]:
    """(status, detail, fix) for "tested regularly": only a finished run in which at least one case was really answered counts."""
    now = time.time() if now is None else now
    since = now - EVAL_FRESH_DAYS * 86400
    rows = s.db.query("SELECT status, summary FROM eval_runs WHERE started>=? AND status!='running'" + (" AND agent=?" if agent else ""), (since, agent) if agent else (since,))

    def answered(r) -> bool:
        try:
            sm = json.loads(r["summary"] or "{}")
        except ValueError:
            return False
        return r["status"] == "done" and sm.get("done", 0) > 0 and sm.get("errors", 0) < sm.get("done", 0)
    n = sum(1 for r in rows if answered(r))
    where = " of this agent" if agent else ""
    if n:
        return "pass", f"{n} evaluation run(s){where} in the last {EVAL_FRESH_DAYS} days.", ""
    if rows:
        return ("warn", f"Evaluation runs{where} in the last {EVAL_FRESH_DAYS} days did not work (every case failed, or the run stopped or was cancelled).",
                "Open Evaluations, read the errors and run the test set again.")
    return ("warn", f"This agent has not been evaluated in the last {EVAL_FRESH_DAYS} days." if agent else f"No evaluation has been run in the last {EVAL_FRESH_DAYS} days.",
            "Create a test set and run it" + (" against this agent." if agent else " (Evaluations)."))
