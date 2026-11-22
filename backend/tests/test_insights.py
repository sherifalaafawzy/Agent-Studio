"""Telemetry insights: KPIs, trend, top lists, anomalies, plain-language errors, explain-a-run and export (all computed from stored spans)."""
from __future__ import annotations

import csv
import io
import json
import time

import pytest

from app import insights as I

T = time.time()


def seed(state, trace, agent="engineer", model="claude-x", cost=0.01, tools=(), error=None, age=0.0, tool_ms=200, n_llm=1):
    t0 = T - age
    state.tel.span(agent, trace, "a" * 16, None, f"agent.{agent}", t0, t0 + 3, {"run_id": "root"}, "error" if error == "agent" else "ok")
    for i in range(n_llm):
        state.tel.span(agent, trace, f"{i:016x}", "a" * 16, "llm.call", t0, t0 + 1, {"gen_ai.request.model": model, "gen_ai.provider": "p", "gen_ai.usage.input_tokens": 100,
                                                                                       "gen_ai.usage.output_tokens": 20, "cost_usd": cost / n_llm}, "error" if error == "llm" else "ok")
    for j, name in enumerate(tools):
        state.tel.span(agent, trace, f"{j + 100:016x}", "a" * 16, f"tool.{name}", t0 + 1, t0 + 1 + tool_ms / 1000, {"tool": name}, "error" if error == name else "ok")


@pytest.fixture
def rich(state):
    for i in range(6):
        seed(state, f"{i:032x}", cost=0.01, tools=("read_file", "write_file"), age=i * 3600)
    seed(state, "e" * 32, agent="reviewer", model="gpt-y", cost=0.5, tools=("read_file",) * 9, age=100)               # expensive, looping
    for i in range(3):
        seed(state, f"f{i}" + "0" * 30, agent="reviewer", model="gpt-y", cost=0.01, tools=("run_shell",), error="run_shell", age=200 + i)
    seed(state, "d" * 32, tools=("read_file",) * 5, tool_ms=9000, age=300)
    seed(state, "c" * 32, error="llm", age=50)
    seed(state, "b" * 32, error="agent", age=60)
    for i in range(5):
        seed(state, f"9{i}" + "0" * 30, tools=("search_text",), tool_ms=100, age=400 + i)
    seed(state, "8" * 32, tools=("search_text",), tool_ms=9000, age=500)                                                  # one slow call among many fast ones
    seed(state, "7" * 32, age=3 * 86400)                                                                                  # three days old
    return state


def test_the_overview_answers_what_happened_what_it_cost_and_what_failed(rich, client):
    out = client.get("/api/telemetry/insights", params={"days": 7}).json()
    k = out["kpis"]
    assert k["runs"] == 20 and k["errors"] == 5 and k["error_rate"] == 0.25 and k["model_calls"] == 20 and k["tool_calls"] > 30
    assert k["cost_usd"] == pytest.approx(6 * 0.01 + 0.5 + 3 * 0.01 + 0.01 + 0.01 + 0.01 + 5 * 0.01 + 0.01 + 0.01) and k["tokens_in"] == 2000 and k["p95_ms"] >= k["p50_ms"] > 0
    assert len(out["trend"]) == 7 and sum(d["runs"] for d in out["trend"]) == 20 and out["trend"][-1]["day"] == time.strftime("%Y-%m-%d", time.gmtime())
    top = out["top"]
    assert top["agents"][0]["name"] == "reviewer" and top["models"][0]["name"] == "gpt-y" and top["models"][0]["calls"] == 4 and {t["name"] for t in top["tools"]} >= {"read_file", "run_shell"}
    assert top["tools"][0]["name"] == "read_file" and next(t for t in top["tools"] if t["name"] == "run_shell")["errors"] == 3
    assert {e["what"] for e in out["errors"]} == {"tool.run_shell", "llm.call", "agent.engineer", "agent.reviewer"} or {"tool.run_shell", "llm.call", "agent.engineer"} <= {e["what"] for e in out["errors"]}
    assert all(e["cause"] for e in out["errors"]) and "tool call failed" in next(e for e in out["errors"] if e["what"] == "tool.run_shell")["cause"] and len(out["runs"]) == 20
    kinds = {a["kind"] for a in out["anomalies"]}
    assert kinds == {"cost_spike", "loop", "slow_tool", "repeated_failure"}
    assert next(a for a in out["anomalies"] if a["kind"] == "cost_spike")["trace_id"] == "e" * 32 and "keeps failing" in next(a for a in out["anomalies"] if a["kind"] == "repeated_failure")["title"]


def test_filters_narrow_everything(rich, client):
    q = lambda **p: client.get("/api/telemetry/insights", params=p).json()               # noqa: E731
    assert q(agent="reviewer")["kpis"]["runs"] == 4 and q(model="gpt-y")["kpis"]["runs"] == 4 and q(status="error")["kpis"]["runs"] == 5 and q(tool="run_shell")["kpis"]["runs"] == 3
    assert q(agent="reviewer", status="ok")["kpis"]["runs"] == 1 and q(agent="nobody")["kpis"] == {**q(agent="nobody")["kpis"], "runs": 0, "error_rate": 0.0, "p50_ms": 0.0}
    assert q(days=1)["kpis"]["runs"] == 19 and q(days=9999)["days"] == 365 and q(days=0)["days"] == 1 and len(q(limit=3)["runs"]) == 3 and len(q(limit=0)["runs"]) == 1
    assert client.get("/api/telemetry/insights", params={"status": "weird"}).status_code == 422
    assert client.get("/api/telemetry/insights").json()["days"] == 7


def test_an_empty_system_has_an_empty_but_valid_overview(client):
    out = client.get("/api/telemetry/insights").json()
    assert out["kpis"]["runs"] == 0 and out["runs"] == [] and out["anomalies"] == [] and len(out["trend"]) == 7 and out["errors"] == []


def test_explain_a_run_in_plain_words(rich, client):
    good = client.get(f"/api/telemetry/runs/{'0' * 32}/explain").json()
    text = " ".join(good["summary"])
    assert "took 3.0 seconds and ended normally" in text and "engineer worked on it" in text and "1 model call (claude-x) used 100 tokens in and 20 out, costing $0.0100" in text
    assert "It used tools 2 times" in text and "Nothing went wrong." in text and good["run"]["tool_calls"] == 2
    bad = " ".join(client.get(f"/api/telemetry/runs/{'f0' + '0' * 30}/explain").json()["summary"])
    assert "ended with an error" in bad and "Problem: A tool call failed" in bad and "Nothing went wrong" not in bad
    slow = " ".join(client.get(f"/api/telemetry/runs/{'d' * 32}/explain").json()["summary"])
    assert "slowest step was tool.read_file at 9.0 seconds" in slow
    assert client.get("/api/telemetry/runs/nope/explain").status_code == 404


def test_export_as_csv_and_json(rich, client):
    r = client.get("/api/telemetry/export", params={"format": "csv", "agent": "reviewer"})
    rows = list(csv.reader(io.StringIO(r.text)))
    assert r.headers["content-type"].startswith("text/csv") and "agent-studio-runs.csv" in r.headers["content-disposition"] and rows[0][:3] == ["trace_id", "agent", "started"] and len(rows) == 5
    j = client.get("/api/telemetry/export", params={"format": "json", "status": "error"})
    assert len(json.loads(j.text)) == 5 and j.headers["content-type"].startswith("application/json")
    assert client.get("/api/telemetry/export", params={"format": "xml"}).status_code == 422


def test_helpers():
    assert I._pct([], 0.5) == 0.0 and I._pct([5.0], 0.95) == 5.0 and I._pct([1, 2, 3, 4], 0.5) in (2, 3)
    assert "model call" in I._cause("llm.call") and "agent run" in I._cause("agent.x") and "outside agent" in I._cause("acp.turn.claude") and "memory" in I._cause("memory.saved") and "tool call" in I._cause("tool.x") and "tool call" in I._cause("weird")
