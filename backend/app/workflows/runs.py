"""Runs of every workflow in one place: the list behind Workflows -> Runs, one run's detail (secrets removed), deleting and re-running.

Finished runs live in the database (the latest N per workflow, see the workflow settings' run retention); runs still working live in memory. The list
merges both: a run that is in memory is described from memory (it is the freshest copy), everything else comes from the database. Nothing in a
response carries a credential: text that looks like one is replaced, in the task, the result, every box's input and output, and approval answers.
"""
from __future__ import annotations

import time
from typing import Any

from ..aibom.redact import SECRET_VALUE
from . import core as C
from .core import WorkflowError

ORIGINS = ("manual", "webhook", "agent", "run_from")
STATUSES = ("running", "done", "error", "stopped", "plan")
SORTS = ("started", "duration", "cost", "status")
MAX_WINDOW = 5000                                   # offset + limit: deeper than this means "narrow the filters"
GONE = "[secret removed]"


def scrub(v: Any) -> Any:
    """The same value with anything that looks like a credential replaced (strings, lists and dicts, recursively)."""
    if isinstance(v, str):
        return SECRET_VALUE.sub(GONE, v)
    if isinstance(v, list):
        return [scrub(x) for x in v]
    if isinstance(v, dict):
        return {k: scrub(x) for k, x in v.items()}
    return v


def node_counts(nodes: dict) -> dict:
    c = dict(total=len(nodes), done=0, error=0, skipped=0, running=0, waiting=0, pending=0, stopped=0)
    for n in nodes.values():
        k = "waiting" if n.get("status") == "waiting_approval" else n.get("status")
        c[k if k in c else "pending"] += 1
    return c


def _row(d: dict, name: str, now: float) -> dict:
    """One line of the list (no box text: that is in the detail)."""
    counts = node_counts(d["nodes"])
    end = d.get("ended")
    return dict(id=d["id"], workflow=d["workflow"], name=name, status=d["status"], origin=d.get("origin") or "manual", autonomy=d["autonomy"], version=d["version"], started=d["started"], ended=end,
                duration=round((end if end else now) - d["started"], 2), task=scrub((d.get("task") or "")[:200]), error=scrub((d.get("error") or "")[:200]) or None, cost_usd=round(d.get("cost_usd") or 0, 6),
                tokens=d.get("tokens") or 0, from_node=d.get("from_node") or None, counts=counts, needs_you=d["status"] == "running" and counts["waiting"] > 0)


def _db_dict(r) -> dict:
    import json
    return dict(id=r["id"], workflow=r["wf_id"], status=r["status"], autonomy=r["autonomy"], task=r["task"], started=r["started"], ended=r["ended"], cost_usd=r["cost_usd"], tokens=r["tokens"],
                origin=r["origin"], version=r["wf_version"], error=r["error"], from_node=r["from_node"], nodes=json.loads(r["nodes"]))


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _key(d: dict, sort: str, now: float):
    if sort == "duration":
        return (d["ended"] or now) - d["started"]
    return {"started": d["started"], "cost": d.get("cost_usd") or 0, "status": d["status"]}[sort]


def list_runs(runner, *, workflow: str = "", status: str = "", origin: str = "", since: float | None = None, until: float | None = None, q: str = "", needs_you: bool = False,
              sort: str = "started", order: str = "desc", limit: int = 25, offset: int = 0) -> dict:
    if status and status not in STATUSES or origin and origin not in ORIGINS or sort not in SORTS or order not in ("asc", "desc"):
        raise WorkflowError("unknown filter or sort")
    limit, offset = max(1, min(limit, 100)), max(0, offset)
    if offset + limit > MAX_WINDOW:
        raise WorkflowError(f"that is deeper than {MAX_WINDOW} runs: narrow the filters instead")
    db, now = runner.store.db, time.time()
    names = {r["id"]: r["name"] for r in db.query("SELECT id,name FROM workflows")}
    live = [r.public() for r in runner.runs.values() if not r.parent]           # runs inside other runs are part of their parent
    live_ids = [d["id"] for d in live]
    ql = q.strip().lower()

    def hit(d: dict, with_status: bool, with_need: bool) -> bool:
        return ((not workflow or d["workflow"] == workflow) and (not with_status or not status or d["status"] == status) and (not origin or d["origin"] == origin)
                and (since is None or d["started"] >= since) and (until is None or d["started"] <= until)
                and (not ql or ql in f"{d['id']} {d.get('task') or ''} {d.get('error') or ''} {d['workflow']} {names.get(d['workflow'], '')}".lower())
                and (not with_need or not needs_you or any(n.get("status") == "waiting_approval" for n in d["nodes"].values())))

    def where(with_status: bool, with_need: bool) -> tuple[str, list]:
        w, p = ["1=1"], []
        if live_ids:
            w.append(f"id NOT IN ({','.join('?' * len(live_ids))})")
            p += live_ids
        for col, val, on in (("wf_id", workflow, bool(workflow)), ("status", status, with_status and bool(status)), ("origin", origin, bool(origin))):
            if on:
                w.append(f"{col}=?")
                p.append(val)
        if since is not None:
            w.append("started>=?")
            p.append(since)
        if until is not None:
            w.append("started<=?")
            p.append(until)
        if ql:
            by_name = [i for i, n in names.items() if ql in n.lower()]
            w.append("(id LIKE ? ESCAPE '\\' OR task LIKE ? ESCAPE '\\' OR error LIKE ? ESCAPE '\\' OR wf_id LIKE ? ESCAPE '\\'" + (f" OR wf_id IN ({','.join('?' * len(by_name))})" if by_name else "") + ")")
            p += [_like(ql)] * 4 + by_name
        if needs_you and with_need:
            w.append("0=1")                                                        # a finished run is never waiting for a person
        return " AND ".join(w), p

    # counts for the status chips: every filter except the status itself
    base_w, base_p = where(False, False)
    by_status = {s: 0 for s in STATUSES}
    for r in db.query(f"SELECT status, COUNT(*) AS c FROM workflow_runs WHERE {base_w} GROUP BY status", tuple(base_p)):
        by_status[r["status"]] = by_status.get(r["status"], 0) + r["c"]
    for d in live:
        if hit(d, False, False):
            by_status[d["status"]] = by_status.get(d["status"], 0) + 1
    waiting = sum(1 for d in live if d["status"] == "running" and hit(d, True, False) and any(n.get("status") == "waiting_approval" for n in d["nodes"].values()))
    # the page: the top (offset + limit) of each source, merged
    w, p = where(True, True)
    sql_key = {"started": "started", "duration": "(COALESCE(ended, started) - started)", "cost": "cost_usd", "status": "status"}[sort]
    d_ = "DESC" if order == "desc" else "ASC"
    stored = [_db_dict(r) for r in db.query(f"SELECT * FROM workflow_runs WHERE {w} ORDER BY {sql_key} {d_}, started DESC, id LIMIT ?", (*p, offset + limit))]
    total_db = db.one(f"SELECT COUNT(*) AS c FROM workflow_runs WHERE {w}", tuple(p))["c"]
    mine = [d for d in live if hit(d, True, True)]
    merged = sorted(stored + mine, key=lambda d: (_key(d, sort, now), d["started"], d["id"]), reverse=order == "desc")
    page = merged[offset:offset + limit]
    return dict(runs=[_row(d, names.get(d["workflow"], d["workflow"]), now) for d in page], total=total_db + len(mine), counts=by_status, needs_you=waiting,
                retention=C.LIMITS["run_retention"], limit=limit, offset=offset)


def detail(runner, rid: str) -> dict | None:
    """One run with everything in it, secrets removed. None if there is no such run."""
    live = runner.runs.get(rid)
    d = live.public() if live is not None else runner.saved_run(rid)
    if d is None:
        return None
    d = {**d, "origin": d.get("origin") or "manual", "tokens": d.get("tokens") or 0}
    row = runner.store.db.one("SELECT name FROM workflows WHERE id=?", (d["workflow"],))
    d["name"] = row["name"] if row else d["workflow"]
    d["counts"] = node_counts(d["nodes"])
    d["needs_you"] = d["status"] == "running" and d["counts"]["waiting"] > 0
    return scrub(d)


def delete_runs(runner, ids: list[str]) -> dict:
    """Delete finished runs. A run that is still working is skipped (stop it first)."""
    db, deleted, skipped = runner.store.db, [], []
    for rid in dict.fromkeys(ids):
        live = runner.runs.get(rid)
        if live is not None and live.ended is None:
            skipped.append(dict(id=rid, reason="still working: stop it first"))
            continue
        gone = db.one("SELECT id FROM workflow_runs WHERE id=?", (rid,)) is not None or live is not None
        if not gone:
            skipped.append(dict(id=rid, reason="no such run"))
            continue
        db.execute("DELETE FROM workflow_runs WHERE id=?", (rid,))
        runner.runs.pop(rid, None)
        deleted.append(rid)
    return dict(deleted=deleted, skipped=skipped)


def rerun(runner, rid: str, from_node: str | None = None):
    """Start the same task again on the current version of the workflow, or (from_node set) from one box, reusing this run's earlier outputs.
    `from_node="failed"` means the first box that failed."""
    live = runner.runs.get(rid)
    if live is not None:
        wid, task, autonomy, status, nodes = live.workflow, live.task, live.autonomy, live.status if live.ended else "running", live.public()["nodes"]
    else:
        r = runner.store.db.one("SELECT * FROM workflow_runs WHERE id=?", (rid,))
        if r is None:
            raise KeyError(rid)
        d = _db_dict(r)
        wid, task, autonomy, status, nodes = d["workflow"], d["task"], d["autonomy"], d["status"], d["nodes"]
    if status == "running":
        raise WorkflowError("this run is still working: stop it or wait for it to finish")
    if from_node == "failed":
        bad = sorted((n for n in nodes.items() if n[1].get("status") == "error"), key=lambda kv: kv[1].get("started") or 0)
        if not bad:
            raise WorkflowError("this run has no failed box to start from")
        from_node = bad[0][0]
    return runner.start(wid, task, autonomy, from_node=from_node or None, base=rid if from_node else None)
