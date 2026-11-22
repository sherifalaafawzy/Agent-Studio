"""Evaluations: assertions, test sets, runs against an agent (mock provider), a judge model, cancellation, spend stop, exports, API."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from app.budget import BudgetExceeded
from app.evals import assertions as A
from app.evals import service as S
from app.evals.service import EvalError
from app.main import create_app


def run(c):
    return asyncio.run(c)


@pytest.fixture
def ev(state):
    return state.evals


@pytest.fixture
def mock_ready(client):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    return client


CASES = [{"id": "greet", "prompt": "say hello", "expect": [{"type": "min_chars", "value": 1}, {"type": "not_contains", "value": "zzzz-never"}]},
         {"id": "fail", "prompt": "say something", "expect": [{"type": "contains", "value": "zzzz-never"}]}]


# ------------------------------------------------------------------------------------------------ assertions
def test_assertions_are_validated():
    assert A.clean({"type": "contains", "value": "x", "junk": 1}) == {"type": "contains", "value": "x"}
    assert A.clean({"type": "json_valid", "value": "ignored"}) == {"type": "json_valid"}
    assert A.clean({"type": "max_chars", "value": 5}) == {"type": "max_chars", "value": 5}
    for bad, msg in (("x", "needs a type"), ({"type": "nope"}, "needs a type"), ({"type": "contains"}, "needs some text"), ({"type": "contains", "value": " "}, "needs some text"),
                     ({"type": "contains", "value": "x" * 2001}, "2000"), ({"type": "max_chars", "value": "5"}, "whole number"), ({"type": "min_chars", "value": 0}, "whole number"),
                     ({"type": "max_chars", "value": True}, "whole number"), ({"type": "regex", "value": "("}, "not valid")):
        with pytest.raises(A.AssertionError_, match=msg):
            A.clean(bad)


def test_exact_assertions():
    ck = A.check_exact
    assert ck({"type": "contains", "value": "HELLO"}, "oh hello") == (True, "contains 'HELLO'")
    assert ck({"type": "not_contains", "value": "x"}, "abc")[0] and not ck({"type": "not_contains", "value": "a"}, "abc")[0]
    assert ck({"type": "equals", "value": " yes "}, "yes\n")[0] and not ck({"type": "equals", "value": "yes"}, "no")[0]
    assert ck({"type": "regex", "value": r"\d+"}, "a1")[0] and not ck({"type": "regex", "value": r"\d+"}, "a")[0]
    assert ck({"type": "not_regex", "value": r"\d+"}, "a")[0] and not ck({"type": "not_regex", "value": r"\d"}, "1")[0]
    assert ck({"type": "max_chars", "value": 3}, "abc")[0] and not ck({"type": "max_chars", "value": 2}, "abc")[0]
    assert ck({"type": "min_chars", "value": 3}, "abc")[0] and not ck({"type": "min_chars", "value": 4}, "abc")[0]
    assert ck({"type": "json_valid"}, '{"a": 1}')[0] and ck({"type": "json_valid"}, "nope") == (False, "is valid JSON")


def test_judge_answer_parsing():
    assert A.parse_judge("PASS the answer is right") == (True, "the answer is right")
    assert A.parse_judge("fail: missing the date\nmore") == (False, "missing the date")
    assert A.parse_judge("PASS") == (True, "")
    for bad in ("maybe", "", None):
        with pytest.raises(A.AssertionError_, match="PASS or FAIL"):
            A.parse_judge(bad)


# ---------------------------------------------------------------------------------------------------- sets
def test_set_crud_and_validation(ev):
    s = ev.save_set({"id": "smoke", "name": "Smoke", "description": "d", "cases": CASES})
    assert s["case_count"] == 2 and s["cases"][0]["expect"][0] == {"type": "min_chars", "value": 1}
    auto = ev.save_set({"name": "Auto id", "cases": [{"prompt": "hi"}]})
    assert auto["id"].startswith("set-") and auto["cases"] == [{"id": "c1", "prompt": "hi", "expect": []}]
    ev.save_set({"id": "smoke", "name": "Renamed"})                                      # a partial update keeps the cases
    assert ev.get_set("smoke")["name"] == "Renamed" and ev.get_set("smoke")["case_count"] == 2
    assert {x["id"] for x in ev.sets()} == {"smoke", auto["id"]} and all(x["last_run"] is None for x in ev.sets())
    for body, msg in (({"id": "A"}, "the id must"), ({"id": "okid", "name": " ", "cases": CASES}, "give the test set a name"),
                      ({"id": "okid", "cases": []}, "between 1 and 200"), ({"id": "okid", "cases": "x"}, "between 1 and 200"),
                      ({"id": "okid", "cases": [{"prompt": " "}]}, "case 1: write a prompt"), ({"id": "okid", "cases": ["x"]}, "case 1: write a prompt"),
                      ({"id": "okid", "cases": [{"prompt": "a", "id": "bad id"}]}, "unique"),
                      ({"id": "okid", "cases": [{"prompt": "a", "id": "x"}, {"prompt": "b", "id": "x"}]}, "unique"),
                      ({"id": "okid", "cases": [{"prompt": "a", "expect": "x"}]}, "at most 10"),
                      ({"id": "okid", "cases": [{"prompt": "a", "expect": [{"type": "x"}]}]}, "case 1: each assertion"),
                      ({"id": "okid", "cases": [{"prompt": "a", "expect": [{"type": "contains", "value": "x"}] * 11}]}, "at most 10")):
        with pytest.raises(EvalError, match=msg):
            ev.save_set(body)
    with pytest.raises(KeyError):
        ev.get_set("ghost")
    ev.delete_set("smoke")
    with pytest.raises(KeyError):
        ev.delete_set("smoke")


# ---------------------------------------------------------------------------------------------------- runs
def start(ev, **kw):
    a = dict(set_id="smoke", agent="assistant", provider="mock", model=None, judge=None)
    a.update(kw)
    return ev.begin(**a)


def test_a_run_checks_every_case_and_keeps_no_chat(ev, mock_ready, state):
    ev.save_set({"id": "smoke", "name": "Smoke", "cases": CASES})
    rid = start(ev)
    with pytest.raises(EvalError, match="still running"):
        start(ev)
    r = run(ev.execute(rid))
    assert r["status"] == "done" and r["total"] == 2 and r["done"] == 2 and r["passed"] == 1 and r["failed"] == 1 and r["errors"] == 0 and r["pass_rate"] == 50
    first, second = r["results"]
    assert first["ok"] and first["reply"] and first["checks"][0]["ok"] and not second["ok"] and second["checks"][0]["type"] == "contains"
    assert r["cost_usd"] >= 0 and r["avg_ms"] >= 0 and r["blocked_by_guardrails"] == 0
    assert state.db.one("SELECT COUNT(*) n FROM chat_sessions")["n"] == 0
    assert ev.sets()[0]["last_run"]["pass_rate"] == 50
    assert [x["id"] for x in ev.runs("smoke")] == [rid] and "results" not in ev.runs()[0]
    with pytest.raises(KeyError):
        ev.run("ghost")


def test_begin_refuses_bad_requests(ev, mock_ready):
    ev.save_set({"id": "smoke", "name": "Smoke", "cases": CASES})
    for kw, exc, msg in (({"set_id": "ghost"}, KeyError, ""), ({"agent": "ghost"}, EvalError, "unknown agent"), ({"provider": "ghost"}, EvalError, "unknown provider"),
                         ({"judge": {"provider": "ghost"}}, EvalError, "judge needs a known provider"), ({"judge": "x"}, EvalError, "judge needs a known provider")):
        with pytest.raises(exc, match=msg):
            start(ev, **kw)


def test_guardrail_blocks_and_errors_are_recorded_per_case(ev, mock_ready, state, monkeypatch):
    state.guardrails.set_enabled(True)
    state.guardrails.save({"id": "strict", "name": "S", "mode": "block", "default": True, "params": {"blocklist": "forbidden"}})
    ev.save_set({"id": "grd", "name": "G", "cases": [{"id": "b", "prompt": "say forbidden", "expect": [{"type": "contains", "value": "I stopped here"}]},
                                                   {"id": "ok", "prompt": "hello", "expect": []}]})
    r = run(ev.execute(start(ev, set_id="grd")))
    assert r["results"][0]["blocked"] is True and r["results"][0]["ok"] and r["blocked_by_guardrails"] == 1
    assert r["results"][1]["ok"] is True                                                  # no checks, but a real reply

    async def boom(*a, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(state.chat, "send", boom)
    r = run(ev.execute(start(ev, set_id="grd")))
    assert r["errors"] == 2 and r["passed"] == 0 and "RuntimeError: provider down" in r["results"][0]["error"] and r["status"] == "done"


def test_an_empty_reply_with_no_checks_does_not_pass(ev, mock_ready, state, monkeypatch):
    async def empty(*a, **k):
        return {"session_id": "s1", "reply": "", "usage": {}}
    monkeypatch.setattr(state.chat, "send", empty)
    ev.save_set({"id": "emp", "name": "E", "cases": [{"prompt": "hi"}]})
    assert run(ev.execute(start(ev, set_id="emp")))["results"][0]["ok"] is False


def test_a_spend_limit_stops_the_run(ev, client, state):
    client.put("/api/providers/mock", json={"cap_usd": 0})
    ev.save_set({"id": "smoke", "name": "Smoke", "cases": CASES})
    r = run(ev.execute(start(ev)))
    assert r["status"] == "error" and r["done"] == 1 and r["stopped"] and r["results"][0]["stop"] is True


def test_cancel_between_cases(ev, mock_ready, state, monkeypatch):
    ev.save_set({"id": "smoke", "name": "Smoke", "cases": CASES})
    rid = start(ev)
    orig = state.chat.send

    async def slow(*a, **k):
        ev.cancel(rid)
        return await orig(*a, **k)
    monkeypatch.setattr(state.chat, "send", slow)
    r = run(ev.execute(rid))
    assert r["status"] == "cancelled" and r["done"] == 1 and rid not in ev.cancelled
    with pytest.raises(EvalError, match="not running"):
        ev.cancel(rid)
    with pytest.raises(KeyError):
        ev.cancel("ghost")


def test_runs_left_running_by_a_restart_are_marked_interrupted(state):
    state.db.execute("INSERT INTO eval_runs(id,set_id,agent,started,status) VALUES('old','s','a',?, 'running')", (time.time(),))
    S.EvalService(state.db, state.chat)
    assert state.db.one("SELECT status FROM eval_runs WHERE id='old'")["status"] == "interrupted"


def test_judge_assertions(ev, mock_ready, state):
    ev.save_set({"id": "jdg", "name": "J", "cases": [{"id": "a", "prompt": "hello", "expect": [{"type": "llm_judge", "value": "is friendly"}]}]})
    r = run(ev.execute(start(ev, set_id="jdg")))
    assert r["results"][0]["checks"][0] == {"type": "llm_judge", "ok": False, "detail": "no judge model was chosen for this run"}
    answers = iter(["PASS looks friendly", "FAIL too curt", "dunno"])

    async def judge(prompt):
        assert "is friendly" in prompt and "hello" in prompt
        return next(answers)
    ev.judge_factory = lambda cfg: judge
    for expected in (True, False):
        r = run(ev.execute(start(ev, set_id="jdg", judge={"provider": "mock"}), None, {"provider": "mock"}))
        assert r["results"][0]["checks"][0]["ok"] is expected
    r = run(ev.execute(start(ev, set_id="jdg", judge={"provider": "mock"}), None, {"provider": "mock"}))
    assert "PASS or FAIL" in r["results"][0]["checks"][0]["detail"]

    async def broke(prompt):
        raise BudgetExceeded(dict(message="judge spend cap"))
    ev.judge_factory = lambda cfg: broke
    r = run(ev.execute(start(ev, set_id="jdg", judge={"provider": "mock"}), None, {"provider": "mock"}))
    assert r["results"][0]["checks"][0]["detail"] == "judge spend cap"


def test_the_default_judge_is_a_guarded_model_call(mock_ready, state, monkeypatch):
    call = S.default_judge(state.chat, "mock", None)
    assert run(call("grade this")).startswith("PASS")
    assert state.db.one("SELECT COUNT(*) n FROM ledger WHERE note='eval_judge'")["n"] == 1
    with pytest.raises(EvalError, match="unknown judge provider"):
        S.default_judge(state.chat, "ghost", None)

    async def fails(**kw):
        raise RuntimeError("down")
    monkeypatch.setattr(S.litellm, "acompletion", fails)
    with pytest.raises(RuntimeError):
        run(call("x"))
    assert state.db.one("SELECT COUNT(*) n FROM ledger WHERE note LIKE 'eval_judge_failed%'")["n"] == 1

    async def no_usage(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))], usage=None)
    monkeypatch.setattr(S.litellm, "acompletion", no_usage)
    assert run(call("x")) == ""
    fac = S.EvalService(state.db, state.chat).judge_factory
    assert callable(fac({"provider": "mock"}))


# -------------------------------------------------------------------------------------------------- exports
@pytest.fixture
def finished(ev, mock_ready):
    ev.save_set({"id": "smoke", "name": "Smoke", "cases": [*CASES, {"id": "len", "prompt": "short", "expect": [{"type": "max_chars", "value": 9999}, {"type": "json_valid"}, {"type": "equals", "value": "x"},
                                                                          {"type": "regex", "value": "a"}, {"type": "not_regex", "value": "q"}, {"type": "llm_judge", "value": "ok"}]}]})
    return run(ev.execute(start(ev)))["id"]


def test_exports_for_other_frameworks(ev, finished):
    name, media, text = ev.export("set", "smoke", "promptfoo")
    cfg = yaml.safe_load(text.split("\n", 1)[1])
    assert name == "smoke-promptfoo.yaml" and media == "application/x-yaml" and cfg["prompts"] == ["{{prompt}}"] and len(cfg["tests"]) == 3
    kinds = [a["type"] for a in cfg["tests"][2]["assert"]]
    assert kinds == ["javascript", "is-json", "equals", "regex", "not-regex", "llm-rubric"] and cfg["tests"][0]["assert"][0] == {"type": "javascript", "value": "output.length >= 1"}
    assert cfg["tests"][1]["assert"][0] == {"type": "icontains", "value": "zzzz-never"} and cfg["tests"][0]["assert"][1]["type"] == "not-icontains"
    _, media, py = ev.export("set", "smoke", "inspect")
    compile(py, "task.py", "exec")
    assert "zzzz-never" in py and media == "text/x-python"
    lines = ev.export("set", "smoke", "jsonl")[2].strip().splitlines()
    assert json.loads(lines[0])["id"] == "greet" and len(lines) == 3
    name, media, text = ev.export("run", finished, "csv")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == "id" and len(rows) == 4 and rows[1][1] == "True" and "min_chars:ok" in rows[1][4] and name.endswith("-csv.csv")
    de = ev.export("run", finished, "deepeval")[2]
    compile(de, "t.py", "exec")
    assert "LLMTestCase" in de and "say hello" in de
    rg = json.loads(ev.export("run", finished, "ragas")[2])
    assert len(rg["rows"]) == 3 and rg["rows"][2]["reference"] == "x" and "reference" not in rg["rows"][0]
    assert len(ev.export("run", finished, "jsonl")[2].strip().splitlines()) == 3


def test_exports_skip_errored_cases_and_refuse_bad_requests(ev, finished, state):
    state.db.execute("UPDATE eval_runs SET results=? WHERE id=?", (json.dumps([{"id": "greet", "prompt": "p", "reply": "", "ok": False, "checks": [], "cost_usd": 0, "ms": 1, "error": "boom"}]), finished))
    assert json.loads(ev.export("run", finished, "ragas")[2])["rows"] == [] and "boom" in ev.export("run", finished, "csv")[2]
    for args, msg in ((("set", "smoke", "zip"), "format must be"), (("run", finished, "promptfoo"), "from a test set"), (("set", "smoke", "deepeval"), "from a finished run"),
                      (("thing", "smoke", "jsonl"), "from a")):
        with pytest.raises(EvalError, match=msg):
            ev.export(*args)
    with pytest.raises(KeyError):
        ev.export("set", "ghost", "promptfoo")


# ---------------------------------------------------------------------------------------------------------- API
def test_api(state):
    with TestClient(create_app(state)) as c:
        c.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
        assert c.get("/api/evals/sets").json()["sets"] == []
        assert "promptfoo" in c.get("/api/evals/sets").json()["exports"]
        assert c.put("/api/evals/sets", json={"id": "x"}).status_code == 422
        r = c.put("/api/evals/sets", json={"id": "smoke", "name": "Smoke", "cases": CASES})
        assert r.status_code == 200 and c.get("/api/evals/sets/smoke").json()["case_count"] == 2 and c.get("/api/evals/sets/ghost").status_code == 404
        assert c.post("/api/evals/runs", json={"set_id": "smoke", "agent": "ghost", "provider": "mock"}).status_code == 422
        r = c.post("/api/evals/runs", json={"set_id": "smoke", "agent": "assistant", "provider": "mock"})
        assert r.status_code == 200 and r.json()["status"] == "running"
        rid = r.json()["id"]
        for _ in range(200):
            got = c.get(f"/api/evals/runs/{rid}").json()
            if got["status"] != "running":
                break
            time.sleep(0.05)
        assert got["status"] == "done" and got["done"] == 2 and len(got["results"]) == 2
        assert c.get("/api/evals/runs").json()["runs"][0]["id"] == rid and c.get(f"/api/evals/runs?set_id=smoke").json()["runs"]
        assert c.get("/api/evals/runs/ghost").status_code == 404
        assert c.post(f"/api/evals/runs/{rid}/cancel").status_code == 422 and c.post("/api/evals/runs/ghost/cancel").status_code == 404
        ex = c.get(f"/api/evals/export?kind=run&id={rid}&format=csv")
        assert ex.status_code == 200 and "attachment" in ex.headers["content-disposition"] and ex.text.startswith("id,passed")
        assert c.get("/api/evals/export?kind=set&id=smoke&format=nope").status_code == 422 and c.get("/api/evals/export?kind=set&id=ghost&format=jsonl").status_code == 404
        assert c.delete("/api/evals/sets/smoke").json() == {"ok": True} and c.delete("/api/evals/sets/smoke").status_code == 404
