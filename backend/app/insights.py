"""Telemetry insights: what happened, what it cost, did anything go wrong, what is slow. Computed on the server from the stored spans.

A "run" is one trace (one chat turn and everything under it). Nothing here calls a model: the explanations are rules over the recorded spans.
"""
from __future__ import annotations

import csv
import io
import json
import statistics
import time
from collections import Counter, defaultdict

MAX_SPANS = 60_000
DAY = 86400
CAUSE = {"llm.call": "A model call failed: the provider returned an error, a spend cap stopped it, or the model timed out.",
         "tool": "A tool call failed: it was refused, could not run, or returned an error to the agent.",
         "agent": "An agent run ended with an error or was stopped.", "acp": "An outside agent's turn ended with an error.", "memory": "A memory action failed."}


def _cause(name: str) -> str:
    key = "llm.call" if name == "llm.call" else name.split(".")[0] if name.split(".")[0] in CAUSE else "tool"
    return CAUSE[key]


def _spans(db, since: float) -> list[dict]:
    rows = db.query("SELECT * FROM otel_records WHERE signal='trace' AND ts >= ? ORDER BY start_ns LIMIT ?", (since, MAX_SPANS))
    out = []
    for r in rows:
        a = json.loads(r["attrs"] or "{}")
        out.append(dict(trace=r["trace_id"], span=r["span_id"], parent=r["parent_id"], name=r["name"], source=r["source"], start=r["start_ns"] / 1e9, end=r["end_ns"] / 1e9,
                        ms=max(0.0, (r["end_ns"] - r["start_ns"]) / 1e6), status=r["status"], attrs=a, ts=r["ts"]))
    return out


def _runs(spans: list[dict]) -> dict[str, dict]:
    runs: dict[str, dict] = {}
    for s in spans:
        r = runs.setdefault(s["trace"], dict(trace_id=s["trace"], agents=set(), start=s["start"], end=s["end"], spans=0, errors=0, cost_usd=0.0, tokens_in=0, tokens_out=0,
                                             model_calls=0, tool_calls=0, models=set(), tools=Counter(), first_error=None, last=s["ts"]))
        r["agents"].add(s["source"])
        r["start"], r["end"], r["spans"], r["last"] = min(r["start"], s["start"]), max(r["end"], s["end"]), r["spans"] + 1, max(r["last"], s["ts"])
        if s["status"] == "error":
            r["errors"] += 1
            r["first_error"] = r["first_error"] or s["name"]
        if s["name"] == "llm.call":
            a = s["attrs"]
            r["model_calls"] += 1
            r["cost_usd"] += float(a.get("cost_usd") or 0)
            r["tokens_in"] += int(a.get("gen_ai.usage.input_tokens") or 0)
            r["tokens_out"] += int(a.get("gen_ai.usage.output_tokens") or 0)
            r["models"].add(str(a.get("gen_ai.request.model") or "unknown"))
        elif s["name"].startswith("tool."):
            r["tool_calls"] += 1
            r["tools"][s["name"][5:]] += 1
    for r in runs.values():
        r["duration_ms"] = round((r["end"] - r["start"]) * 1000, 1)
        r["status"] = "error" if r["errors"] else "ok"
        r["agent"] = sorted(r["agents"])[0] if r["agents"] else ""
    return runs


def _keep(r: dict, agent: str, model: str, status: str, tool: str) -> bool:
    return (not agent or agent in r["agents"]) and (not model or model in r["models"]) and (not status or r["status"] == status) and (not tool or tool in r["tools"])


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    v = sorted(vals)
    return round(v[min(len(v) - 1, int(round(p * (len(v) - 1))))], 1)


def run_row(r: dict) -> dict:
    return dict(trace_id=r["trace_id"], agent=r["agent"], agents=sorted(r["agents"]), started=r["start"], duration_ms=r["duration_ms"], status=r["status"], errors=r["errors"],
                cost_usd=round(r["cost_usd"], 6), tokens_in=r["tokens_in"], tokens_out=r["tokens_out"], model_calls=r["model_calls"], tool_calls=r["tool_calls"],
                models=sorted(r["models"]), tools=dict(r["tools"]))


def build(db, days: int = 7, agent: str = "", model: str = "", status: str = "", tool: str = "", limit: int = 100) -> dict:
    days = max(1, min(int(days), 365))
    now = time.time()
    spans = _spans(db, now - days * DAY)
    runs = _runs(spans)
    chosen = {k: r for k, r in runs.items() if _keep(r, agent, model, status, tool)}
    keep = set(chosen)
    mine = [s for s in spans if s["trace"] in keep]
    durations = [r["duration_ms"] for r in chosen.values()]
    errors = sum(1 for r in chosen.values() if r["status"] == "error")
    kpis = dict(runs=len(chosen), errors=errors, error_rate=round(errors / len(chosen), 3) if chosen else 0.0, cost_usd=round(sum(r["cost_usd"] for r in chosen.values()), 6),
                tokens_in=sum(r["tokens_in"] for r in chosen.values()), tokens_out=sum(r["tokens_out"] for r in chosen.values()), model_calls=sum(r["model_calls"] for r in chosen.values()),
                tool_calls=sum(r["tool_calls"] for r in chosen.values()), p50_ms=_pct(durations, 0.5), p95_ms=_pct(durations, 0.95))
    perday: dict[str, dict] = {}
    for i in range(days):
        d = time.strftime("%Y-%m-%d", time.gmtime(now - (days - 1 - i) * DAY))
        perday[d] = dict(day=d, runs=0, errors=0, cost_usd=0.0)
    for r in chosen.values():
        d = time.strftime("%Y-%m-%d", time.gmtime(r["last"]))
        if d in perday:
            perday[d]["runs"] += 1
            perday[d]["errors"] += 1 if r["status"] == "error" else 0
            perday[d]["cost_usd"] = round(perday[d]["cost_usd"] + r["cost_usd"], 6)
    agents: dict[str, dict] = defaultdict(lambda: dict(runs=0, errors=0, cost_usd=0.0))
    for r in chosen.values():
        for a in r["agents"]:
            agents[a]["runs"] += 1
            agents[a]["errors"] += 1 if r["status"] == "error" else 0
    models: dict[str, dict] = defaultdict(lambda: dict(calls=0, cost_usd=0.0, tokens=0))
    tools: dict[str, dict] = defaultdict(lambda: dict(calls=0, errors=0, total_ms=0.0))
    for s in mine:
        if s["name"] == "llm.call":
            m = models[str(s["attrs"].get("gen_ai.request.model") or "unknown")]
            m["calls"] += 1
            m["cost_usd"] = round(m["cost_usd"] + float(s["attrs"].get("cost_usd") or 0), 6)
            m["tokens"] += int(s["attrs"].get("gen_ai.usage.input_tokens") or 0) + int(s["attrs"].get("gen_ai.usage.output_tokens") or 0)
            agents[s["source"]]["cost_usd"] = round(agents[s["source"]]["cost_usd"] + float(s["attrs"].get("cost_usd") or 0), 6)
        elif s["name"].startswith("tool."):
            t = tools[s["name"][5:]]
            t["calls"] += 1
            t["errors"] += 1 if s["status"] == "error" else 0
            t["total_ms"] += s["ms"]
    top = dict(agents=sorted(({"name": k, **v} for k, v in agents.items()), key=lambda x: (-x["cost_usd"], -x["runs"]))[:8],
               models=sorted(({"name": k, **v} for k, v in models.items()), key=lambda x: (-x["cost_usd"], -x["calls"]))[:8],
               tools=sorted(({"name": k, "calls": v["calls"], "errors": v["errors"], "avg_ms": round(v["total_ms"] / v["calls"], 1)} for k, v in tools.items()), key=lambda x: -x["calls"])[:8])
    errs = [dict(trace_id=s["trace"], agent=s["source"], what=s["name"], cause=_cause(s["name"]), at=s["ts"]) for s in mine if s["status"] == "error"]
    errs.sort(key=lambda e: -e["at"])
    return dict(days=days, filters=dict(agent=agent, model=model, status=status, tool=tool), kpis=kpis, trend=list(perday.values()), top=top, errors=errs[:25],
                anomalies=anomalies(chosen, mine), runs=[run_row(r) for r in sorted(chosen.values(), key=lambda r: -r["last"])[:max(1, min(limit, 500))]])


def anomalies(runs: dict[str, dict], spans: list[dict]) -> list[dict]:
    out: list[dict] = []
    costs = [r["cost_usd"] for r in runs.values() if r["cost_usd"] > 0]
    if len(costs) >= 5:
        med = statistics.median(costs)
        for r in sorted(runs.values(), key=lambda r: -r["cost_usd"])[:3]:
            if r["cost_usd"] > max(med * 3, 0.01):
                out.append(dict(kind="cost_spike", severity="warn", trace_id=r["trace_id"], title="A run cost much more than usual",
                                detail=f"${r['cost_usd']:.4f} against a typical ${med:.4f} (agent {r['agent']})."))
    for r in runs.values():
        for name, n in r["tools"].items():
            if n >= 8:
                out.append(dict(kind="loop", severity="warn", trace_id=r["trace_id"], title="An agent may be going in circles",
                                detail=f"{r['agent']} used {name} {n} times in one run."))
    by_tool: dict[str, list[float]] = defaultdict(list)
    for s in spans:
        if s["name"].startswith("tool."):
            by_tool[s["name"][5:]].append(s["ms"])
    for s in spans:
        if s["name"].startswith("tool."):
            vals = by_tool[s["name"][5:]]
            if len(vals) >= 5 and s["ms"] > 5000 and s["ms"] > 3 * statistics.median(vals):
                out.append(dict(kind="slow_tool", severity="info", trace_id=s["trace"], title="A tool was unusually slow",
                                detail=f"{s['name'][5:]} took {s['ms'] / 1000:.1f}s; it usually takes {statistics.median(vals) / 1000:.1f}s."))
    fails = Counter((s["source"], s["name"]) for s in spans if s["status"] == "error")
    for (src, name), n in fails.items():
        if n >= 3:
            out.append(dict(kind="repeated_failure", severity="bad", trace_id=None, title="The same thing keeps failing",
                            detail=f"{name} failed {n} times for {src}. {_cause(name)}"))
    seen, uniq = set(), []
    for a in out:
        k = (a["kind"], a["trace_id"], a["detail"])
        if k not in seen:
            seen.add(k)
            uniq.append(a)
    return uniq[:20]


def explain(db, trace_id: str) -> dict | None:
    """A plain-language account of one run, from its spans. No model is used."""
    spans = [s for s in _spans(db, 0) if s["trace"] == trace_id]
    if not spans:
        return None
    r = _runs(spans)[trace_id]
    lines = [f"This run took {r['duration_ms'] / 1000:.1f} seconds and ended {'with an error' if r['errors'] else 'normally'}.",
             f"{', '.join(sorted(r['agents']))} worked on it." if r["agents"] else ""]
    if r["model_calls"]:
        lines.append(f"{r['model_calls']} model call{'s' if r['model_calls'] != 1 else ''} ({', '.join(sorted(r['models']))}) used {r['tokens_in']:,} tokens in and {r['tokens_out']:,} out, costing ${r['cost_usd']:.4f}.")
    if r["tool_calls"]:
        top = ", ".join(f"{n} × {c}" for n, c in r["tools"].most_common(4))
        lines.append(f"It used tools {r['tool_calls']} times ({top}).")
    slow = max((s for s in spans if s["name"] != "llm.call" and not s["name"].startswith("agent.")), key=lambda s: s["ms"], default=None)
    if slow is not None and slow["ms"] >= 1000:
        lines.append(f"The slowest step was {slow['name']} at {slow['ms'] / 1000:.1f} seconds.")
    for s in [s for s in spans if s["status"] == "error"][:5]:
        lines.append(f"Problem: {_cause(s['name'])} ({s['name']}, by {s['source']}).")
    if not r["errors"]:
        lines.append("Nothing went wrong.")
    return dict(trace_id=trace_id, summary=[ln for ln in lines if ln], run=run_row(r))


def export(db, fmt: str = "json", **filters) -> tuple[str, str]:
    """(content type, body) of the filtered runs as CSV or JSON."""
    runs = build(db, limit=500, **filters)["runs"]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["trace_id", "agent", "started", "duration_ms", "status", "errors", "cost_usd", "tokens_in", "tokens_out", "model_calls", "tool_calls", "models"])
        for r in runs:
            w.writerow([r["trace_id"], r["agent"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["started"])), r["duration_ms"], r["status"], r["errors"], r["cost_usd"],
                        r["tokens_in"], r["tokens_out"], r["model_calls"], r["tool_calls"], " ".join(r["models"])])
        return "text/csv", buf.getvalue()
    return "application/json", json.dumps(runs, indent=2)
