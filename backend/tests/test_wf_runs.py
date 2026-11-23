"""Workflows -> Runs: the list across workflows (saved and live), filters, sort, paging, status counts, a run's detail with secrets removed, delete, re-run, origins."""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

from app.db import Database
from app.workflows import runs as R
from tests.test_wf_boxes_run import asked, hook_flow
from tests.test_wf_engine import D, nd
from tests.test_workflows import finish, rich, save  # noqa: F401  (rich is a fixture)

KEY = "sk-" "abcdefghijklmnop1234"


def flow(c, wid, text="hi"):
    save(c, D([nd("s", "input"), nd("t", "template", dict(text=text)), nd("o", "output")], [("s", "t"), ("t", "o")], wid=wid))


def failing(c, wid="fail"):
    save(c, D([nd("s", "input"), nd("a", "template", dict(text="ok {{ $input }}")), nd("boom", "stop", dict(message="nope"))], [("s", "a"), ("a", "boom")], wid=wid))


def asking(c, wid="ask"):
    save(c, D([nd("s", "input"), nd("ask", "approval", dict(message="Go on?")), nd("o", "output")], [("s", "ask"), ("ask", "o", "approved")], wid=wid))


def put(state, rid, wid, status="done", started=1000.0, ended=1002.0, origin="manual", cost=0.0, tokens=0, task="t", error=None, nodes=None, final=""):
    nodes = nodes if nodes is not None else {"s": dict(status="done", output="x", error=None, label="Task", type="input"), "t": dict(status=status if status == "error" else "done", output="y", error=error, label="T", type="template")}
    state.db.execute("INSERT INTO workflow_runs(id,wf_id,wf_version,status,autonomy,task,started,ended,cost_usd,error,final,nodes,origin,tokens,from_node) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, wid, 1, status, "auto", task, started, ended, cost, error, final, json.dumps(nodes), origin, tokens, ""))


def ls(c, **q):
    r = c.get("/api/workflows/runs", params=q)
    assert r.status_code == 200, r.text
    return r.json()


def ids(c, **q):
    return [x["id"] for x in ls(c, **q)["runs"]]


@pytest.fixture
def seeded(rich, state):
    flow(rich, "wf_a")
    flow(rich, "wf_b")
    put(state, "r1", "wf_a", "done", 1000, 1002, cost=0.5, tokens=10, task="first")
    put(state, "r2", "wf_a", "error", 2000, 2010, origin="webhook", error=f"boom {KEY}", task=f"payload {KEY}")
    put(state, "r3", "wf_b", "stopped", 3000, 3001, origin="agent", cost=0.1)
    put(state, "r4", "wf_b", "plan", 4000, 4000.5)
    return rich


def test_the_list_orders_filters_counts_and_pages(seeded):
    out = ls(seeded)
    assert [x["id"] for x in out["runs"]] == ["r4", "r3", "r2", "r1"] and out["total"] == 4 and out["retention"] == 100
    assert out["counts"] == {"running": 0, "done": 1, "error": 1, "stopped": 1, "plan": 1} and out["needs_you"] == 0
    r2 = next(x for x in out["runs"] if x["id"] == "r2")
    assert r2["name"] == "wf_a" and r2["origin"] == "webhook" and r2["duration"] == 10 and r2["counts"]["total"] == 2 and r2["counts"]["error"] == 1 and r2["needs_you"] is False
    assert KEY not in json.dumps(out) and "[secret removed]" in r2["error"] and "[secret removed]" in r2["task"]           # a credential never reaches the list
    assert ids(seeded, workflow="wf_a") == ["r2", "r1"] and ids(seeded, status="error") == ["r2"] and ids(seeded, origin="agent") == ["r3"]
    assert ls(seeded, status="error")["counts"] == out["counts"]                                  # the chips count everything the other filters allow, whatever the status filter says
    assert ls(seeded, workflow="wf_b")["counts"]["done"] == 0 and ls(seeded, workflow="wf_b")["counts"]["plan"] == 1
    assert ids(seeded, since=2000, until=3000) == ["r3", "r2"] and ids(seeded, since=5000) == []
    assert ids(seeded, q="first") == ["r1"] and ids(seeded, q="r3") == ["r3"] and ids(seeded, q="WF_B") == ["r4", "r3"] and ids(seeded, q="boom") == ["r2"] and ids(seeded, q="%") == []
    assert ids(seeded, sort="cost")[:2] == ["r1", "r3"] and ids(seeded, sort="cost", order="asc")[-2:] == ["r3", "r1"]
    assert ids(seeded, sort="duration")[0] == "r2" and ids(seeded, sort="duration", order="asc")[0] == "r4" and ids(seeded, sort="status", order="asc")[0] == "r1"
    assert ids(seeded, limit=2) == ["r4", "r3"] and ids(seeded, limit=2, offset=2) == ["r2", "r1"] and ids(seeded, limit=2, offset=9) == []
    assert ids(seeded, needs_you="true") == []
    for bad in (dict(status="nope"), dict(origin="nope"), dict(sort="nope"), dict(order="sideways"), dict(offset=5000, limit=5)):
        r = seeded.get("/api/workflows/runs", params=bad)
        assert r.status_code == 422, bad
    assert len(ids(seeded, limit=1000)) == 4                                                         # the page size is capped, not refused


def test_live_runs_are_listed_with_what_they_wait_for(seeded, state):
    asking(seeded)
    rid = seeded.post("/api/workflows/ask/run", json={"task": "go", "autonomy": "auto"}).json()["id"]
    asked(seeded, rid, "ask")
    out = ls(seeded)
    live = out["runs"][0]
    assert live["id"] == rid and live["status"] == "running" and live["needs_you"] is True and live["counts"]["waiting"] == 1 and live["ended"] is None and live["duration"] >= 0
    assert out["counts"]["running"] == 1 and out["needs_you"] == 1 and out["total"] == 5
    assert ids(seeded, needs_you="true") == [rid] and ids(seeded, status="running") == [rid] and ids(seeded, status="done") == ["r1"]
    assert ids(seeded, workflow="wf_a", needs_you="true") == [] and ids(seeded, q="Go on") == []                        # q looks in the task and the error, not in a box's question
    assert ids(seeded, sort="duration")[0] in (rid, "r2") and ids(seeded, sort="started", order="asc")[-1] == rid
    seeded.post(f"/api/workflows/runs/{rid}/stop")
    done = finish(seeded, rid)
    assert done["status"] == "stopped"
    again = ls(seeded)
    assert [x["id"] for x in again["runs"]].count(rid) == 1 and again["total"] == 5 and again["needs_you"] == 0            # saved and in memory, listed once
    nested = state.wf_runner.runs[rid]
    assert nested.ended is not None


def test_a_run_inside_another_run_is_not_listed_on_its_own(rich, state):
    flow(rich, "inner")
    save(rich, D([nd("s", "input"), nd("sub", "sub_workflow", dict(workflow="inner")), nd("o", "output")], [("s", "sub"), ("sub", "o")], wid="outer"))
    rid = rich.post("/api/workflows/outer/run", json={"task": "x"}).json()["id"]
    assert finish(rich, rid)["status"] == "done"
    assert len(state.wf_runner.runs) == 2 and ids(rich) == [rid] and ls(rich)["total"] == 1


def test_detail_has_names_counts_and_no_secrets(seeded, state):
    nodes = {"s": dict(status="done", output=f"token {KEY}", error=None, label="Task", type="input", input=f"in {KEY}"),
             "ask": dict(status="waiting_approval", output="", error=None, label="Ask", type="approval", form=[{"name": "k", "label": "Key", "type": "text", "options": [], "required": False}], answers={"k": KEY}),
             "z": dict(status="pending", output="", error=None, label="Z", type="template"), "u": dict(status="weird", output="", error=None, label="U", type="template")}
    put(state, "rd", "wf_a", "running", 5000, None, task=f"use {KEY}", nodes=nodes, final=f"done {KEY}")
    d = seeded.get("/api/workflows/runs/rd").json()
    assert KEY not in json.dumps(d) and d["name"] == "wf_a" and d["origin"] == "manual" and d["tokens"] == 0 and d["counts"] == dict(total=4, done=1, error=0, skipped=0, running=0, waiting=1, pending=2, stopped=0)
    assert d["needs_you"] is True and d["nodes"]["ask"]["answers"] == {"k": "[secret removed]"} and d["task"] == "use [secret removed]" and d["final"] == "done [secret removed]"
    assert seeded.get("/api/workflows/runs/ghost").status_code == 404
    put(state, "orphan", "gone_wf", "done", 6000, 6001)
    assert seeded.get("/api/workflows/runs/orphan").json()["name"] == "gone_wf"                                  # a workflow that no longer exists is still named by its id
    assert R.scrub(["a", {"b": KEY}, 3]) == ["a", {"b": "[secret removed]"}, 3]


def test_delete_one_or_many_but_never_a_run_that_is_still_working(seeded, state):
    assert seeded.delete("/api/workflows/runs/r1").json() == {"ok": True} and seeded.get("/api/workflows/runs/r1").status_code == 404
    assert seeded.delete("/api/workflows/runs/r1").status_code == 404
    asking(seeded)
    rid = seeded.post("/api/workflows/ask/run", json={"task": "go", "autonomy": "auto"}).json()["id"]
    asked(seeded, rid, "ask")
    assert seeded.delete(f"/api/workflows/runs/{rid}").status_code == 409
    out = seeded.post("/api/workflows/runs/delete", json={"ids": ["r2", "r2", "ghost", rid]}).json()
    assert out["deleted"] == ["r2"] and {s["id"]: s["reason"] for s in out["skipped"]} == {"ghost": "no such run", rid: "still working: stop it first"}
    assert sorted(ids(seeded)) == sorted([rid, "r3", "r4"])
    for bad in ({}, {"ids": []}, {"ids": "r3"}, {"ids": [1]}, {"ids": ["x"] * 501}):
        assert seeded.post("/api/workflows/runs/delete", json=bad).status_code == 422
    seeded.post(f"/api/workflows/runs/{rid}/stop")
    finish(seeded, rid)
    state.db.execute("DELETE FROM workflow_runs WHERE id=?", (rid,))                                          # saved copy lost: the run is still in memory
    assert seeded.delete(f"/api/workflows/runs/{rid}").json() == {"ok": True} and rid not in state.wf_runner.runs


def test_rerun_the_same_task_or_from_the_box_that_failed(rich, state):
    failing(rich)
    first = rich.post("/api/workflows/fail/run", json={"task": "hello"}).json()["id"]
    assert finish(rich, first)["status"] == "error"
    again = rich.post(f"/api/workflows/runs/{first}/rerun", json={}).json()
    assert again["id"] != first and again["origin"] == "manual" and again["workflow"] == "fail"
    done = finish(rich, again["id"])
    assert done["task"] == "hello" and done["status"] == "error"
    resumed = rich.post(f"/api/workflows/runs/{first}/rerun", json={"from_node": "failed"}).json()
    assert resumed["origin"] == "run_from" and resumed["from_node"] == "boom"
    got = finish(rich, resumed["id"])
    assert got["nodes"]["a"].get("reused") is True and got["nodes"]["boom"]["status"] == "error"
    state.wf_runner.runs.clear()                                                                              # only the saved copy is left
    assert ls(rich, origin="run_from")["total"] == 1 and ls(rich)["runs"][0]["from_node"] == "boom"
    saved = rich.post(f"/api/workflows/runs/{first}/rerun", json={"from_node": "boom"}).json()
    assert saved["origin"] == "run_from" and finish(rich, saved["id"])["nodes"]["a"].get("reused") is True
    assert rich.get(f"/api/workflows/runs/{saved['id']}").json()["from_node"] == "boom"
    flow(rich, "fine")
    ok = rich.post("/api/workflows/fine/run", json={"task": "x"}).json()["id"]
    finish(rich, ok)
    assert "no failed box" in rich.post(f"/api/workflows/runs/{ok}/rerun", json={"from_node": "failed"}).json()["detail"]
    assert rich.post("/api/workflows/runs/ghost/rerun", json={}).status_code == 404
    assert rich.post(f"/api/workflows/runs/{ok}/rerun", json={"from_node": "ghost"}).status_code == 422


def test_cannot_rerun_a_run_that_is_still_working(rich, state):
    asking(rich)
    rid = rich.post("/api/workflows/ask/run", json={"task": "go", "autonomy": "auto"}).json()["id"]
    asked(rich, rid, "ask")
    r = rich.post(f"/api/workflows/runs/{rid}/rerun", json={})
    assert r.status_code == 422 and "still working" in r.json()["detail"]
    rich.post(f"/api/workflows/runs/{rid}/stop")
    finish(rich, rid)


def test_what_started_each_run_is_recorded(rich, state):
    flow(rich, "plain")
    manual = rich.post("/api/workflows/plain/run", json={"task": "x"}).json()
    assert manual["origin"] == "manual" and manual["tokens"] == 0
    finish(rich, manual["id"])
    rich.put("/api/workflows/settings", json=dict(webhooks_enabled=True))
    path = hook_flow(rich)
    hooked = rich.post(path, content=f"hello {KEY}").json()["run"]
    done = finish(rich, hooked)
    assert done["origin"] == "webhook" and KEY not in json.dumps(done)                                   # the caller's payload is redacted in what the Tab shows
    assert state.wf_runner.runs[hooked].task.endswith(KEY)                                                 # ... but kept as it came, so the run can be repeated
    save_tool = rich.post("/api/workflows/plain/to-agent").json()["agent"]
    res = rich.post("/api/chat", json={"provider": "mock", "agent": save_tool, "message": '!tool run_workflow {"workflow": "plain", "task": "hello"}'}).json()
    assert any(s["type"] == "result" and "'status': 'done'" in s["result"] for s in res["steps"])
    assert ls(rich, origin="agent")["total"] == 1 and ls(rich, origin="webhook")["total"] == 1 and ls(rich, origin="manual")["total"] == 1


def test_tokens_add_up_from_agent_steps_and_child_workflows(rich, state):
    save(rich, D([nd("s", "input"), nd("c", "sequential", dict(steps=[dict(kind="agent", agent="helper", prompt="say hi")])), nd("o", "output")], [("s", "c"), ("c", "o")], wid="tok"))
    rid = rich.post("/api/workflows/tok/run", json={"task": "x"}).json()["id"]
    got = finish(rich, rid)
    assert got["status"] == "done" and got["tokens"] > 0 and ls(rich)["runs"][0]["tokens"] == got["tokens"]
    save(rich, D([nd("s", "input"), nd("sub", "sub_workflow", dict(workflow="tok")), nd("o", "output")], [("s", "sub"), ("sub", "o")], wid="parent"))
    p = finish(rich, rich.post("/api/workflows/parent/run", json={"task": "x"}).json()["id"])
    assert p["tokens"] >= got["tokens"] > 0


def test_old_databases_gain_the_new_run_columns(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE workflow_runs(id TEXT PRIMARY KEY, wf_id TEXT NOT NULL, wf_version INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, autonomy TEXT NOT NULL DEFAULT 'auto', "
                "task TEXT NOT NULL DEFAULT '', started REAL NOT NULL, ended REAL, cost_usd REAL NOT NULL DEFAULT 0, error TEXT, final TEXT NOT NULL DEFAULT '', nodes TEXT NOT NULL DEFAULT '{}')")
    con.execute("INSERT INTO workflow_runs(id,wf_id,status,started) VALUES('old','w','done',1)")
    con.commit()
    con.close()
    db = Database(path)
    r = db.one("SELECT origin, tokens, from_node FROM workflow_runs WHERE id='old'")
    assert (r["origin"], r["tokens"], r["from_node"]) == ("manual", 0, "")


def test_the_list_is_quick_with_many_runs(rich, state):
    flow(rich, "many")
    for i in range(300):
        put(state, f"m{i:03}", "many", "done", 1000 + i, 1001 + i)
    t = time.time()
    out = ls(rich, limit=50, offset=100)
    assert out["total"] == 300 and len(out["runs"]) == 50 and out["runs"][0]["id"] == "m199" and time.time() - t < 3


def test_deleting_a_workflow_removes_its_finished_runs_from_the_list(rich, state):
    flow(rich, "gone")
    flow(rich, "stays")
    a = rich.post("/api/workflows/gone/run", json={"task": "x"}).json()["id"]
    b = rich.post("/api/workflows/stays/run", json={"task": "x"}).json()["id"]
    finish(rich, a)
    finish(rich, b)
    assert sorted(ids(rich)) == sorted([a, b])
    assert rich.delete("/api/workflows/gone").json() == {"ok": True}
    assert ids(rich) == [b] and a not in state.wf_runner.runs and rich.get(f"/api/workflows/runs/{a}").status_code == 404
