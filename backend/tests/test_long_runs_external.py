"""Long runs (background jobs that survive the client) + native agents calling external ACP agents."""
import json
import sys
import time
from pathlib import Path

import pytest

FAKE = str(Path(__file__).parent / "fake_acp_agent.py")


@pytest.fixture
def lead(state, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(state)) as client:      # one persistent event loop, like the real server (jobs outlive requests)
        client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
        client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
        state.acp.store.create("fake", "Fake", sys.executable, [FAKE])
        r = client.post("/api/agents", json={"id": "lead", "name": "Lead", "acp_agents": ["fake"], "acp_policy": "readonly"})
        assert r.status_code == 200, r.text
        yield client


def call(task, agent="fake"):
    return "!tool call_external_agent " + json.dumps({"agent": agent, "task": task})


def stream(c, msg, agent="lead", stop_after=None, **kw):
    out = []
    with c.stream("POST", "/api/chat/stream", json={"provider": "mock", "agent": agent, "message": msg, **kw}) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
                if stop_after and stop_after(out[-1]):
                    break                                   # client goes away mid-turn
    return out


def wait_job(c, jid, timeout=40):
    t = time.time()
    while time.time() - t < timeout:
        j = next((x for x in c.get("/api/chat/jobs").json() if x["job_id"] == jid), None)
        if j and j["ended"]:
            return j
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_agent_calls_an_external_agent_and_it_is_visible(lead):
    ev = stream(lead, call("hello there"))
    types = [e["type"] for e in ev]
    assert types[0] == "job" and types[-1] == "end" and "done" in types
    done = next(e for e in ev if e["type"] == "done")
    ext = next(r for r in done["runs"] if r.get("external"))
    assert ext["agent"] == "fake" and ext["parent"] == "root" and ext["status"] == "done" and ext["definition"]["policy"] == "readonly"
    assert ext["result"] == "echo: hello there" and ext["external_cost"] == pytest.approx(0.0042)
    assert any(e["type"] == "external_text" and e["run_id"] == ext["run_id"] for e in ev)          # streamed live
    res = next(s for s in done["steps"] if s["type"] == "result" and s["tool"] == "call_external_agent")["result"]
    assert "echo: hello there" in res and "not covered" in res
    ev2 = stream(lead, call("again"), session_id=done["session_id"])                              # same ACP session reused
    assert next(e for e in ev2 if e["type"] == "done")["session_id"] == done["session_id"]


def test_permission_requests_reach_the_chat_and_can_be_answered(lead, tmp_path):
    lead.put("/api/agents/lead", json={"acp_policy": "ask"})
    answered = []

    def on(e):
        if e["type"] == "external_ask" and not answered:
            answered.append(lead.post(f"/api/acp/respond/{e['request_id']}", json={"option_id": "allow-once"}).status_code)
        return False
    ev = stream(lead, call("!write notes/x.txt|hi"), stop_after=on)
    assert answered == [200] and any(e["type"] == "external_resolved" for e in ev)
    assert (tmp_path / "ws" / "notes" / "x.txt").read_text() == "hi"


def test_readonly_policy_blocks_writes(lead, tmp_path):
    ev = stream(lead, call("!write notes/y.txt|no"))
    assert not (tmp_path / "ws" / "notes" / "y.txt").exists()
    assert any(e["type"] == "external_note" and e["event"]["type"] == "auto" for e in ev)


def test_turn_keeps_running_after_the_client_disconnects_and_can_be_reattached(lead):
    ev = stream(lead, call("!slow"), stop_after=lambda e: e["type"] == "agent_start" and e.get("external"))
    jid = ev[0]["job_id"]
    sid = next(e["session_id"] for e in ev if e["type"] == "session")
    assert lead.get(f"/api/chat/sessions/{sid}").json()["active_job"]["job_id"] == jid       # still running
    assert next(r for r in lead.get("/api/chat/sessions").json() if r["id"] == sid)["running"] is True
    assert lead.post("/api/chat/stream", json={"provider": "mock", "agent": "lead", "message": "x", "session_id": sid}).status_code == 409
    after = ev[-1]["seq"]
    rest = []
    with lead.stream("GET", f"/api/chat/jobs/{jid}/events", params={"after": after}) as r:     # re-attach
        for line in r.iter_lines():
            if line.startswith("data: "):
                rest.append(json.loads(line[6:]))
    assert rest[0]["seq"] == after + 1 and rest[-1] == {"type": "end", "status": "done", "seq": rest[-1]["seq"]}
    assert "slow-done" in next(e for e in rest if e["type"] == "done")["runs"][1]["result"]
    msgs = lead.get(f"/api/chat/sessions/{sid}").json()
    assert msgs["active_job"] is None and msgs["messages"][-1]["role"] == "bot"


def test_stop_button_cancels_job_and_external_agent(lead, state):
    ev = stream(lead, call("!slow"), stop_after=lambda e: e["type"] == "agent_start" and e.get("external"))
    jid = ev[0]["job_id"]
    assert lead.post(f"/api/chat/jobs/{jid}/cancel").status_code == 200
    j = wait_job(lead, jid, 15)
    assert j["status"] == "cancelled" and j["elapsed_s"] < 15
    sid = j["session_id"]
    bot = lead.get(f"/api/chat/sessions/{sid}").json()["messages"][-1]
    assert bot["meta"]["status"] == "cancelled" and any(r.get("external") and r["status"] == "cancelled" for r in bot["meta"]["runs"])
    assert state.db.query("SELECT * FROM reservations") == []
    assert lead.post(f"/api/chat/jobs/{jid}/cancel").status_code == 404


def test_wall_clock_limit(lead):
    lead.put("/api/settings", json={"max_turn_minutes": 0.02})            # ~1.2 s
    ev = stream(lead, call("!slow"))
    assert ev[-1]["type"] == "end" and ev[-1]["status"] == "timeout"
    assert any(e["type"] == "error" and "minute limit" in e["message"] for e in ev)


def test_switches_and_limits(lead, state, tmp_path):
    from app.chat import TurnTrace
    from app.harness.tools import ToolContext
    import asyncio

    def names(aid):
        ws = tmp_path / "ws"
        ws.mkdir(exist_ok=True)
        ag = asyncio.run(state.chat._build(state.agents.get(aid), chat_provider="mock", root_model=None, max_tokens=None,
                                           ctx=ToolContext(ws, state.chat.sandbox_for(ws), state.skills, state.mcp), turn=TurnTrace(), toolsets=[]))
        return [getattr(t, "name", None) or getattr(t, "__name__", "") for t in ag.tools]
    assert "call_external_agent" in names("lead") and "call_external_agent" not in names("coder")
    lead.put("/api/settings", json={"agents_can_call_external": False})
    assert "call_external_agent" not in names("lead")
    lead.put("/api/settings", json={"agents_can_call_external": True, "max_external_calls_per_turn": 1})
    ev = stream(lead, call("one") + "\n" + call("two"))
    results = [e["result"] for e in ev if e["type"] == "tool_result" and e["tool"] == "call_external_agent"]
    assert "echo: one" in results[0] and "limit reached" in results[1]
    ev = stream(lead, call("x", agent="claude"))
    assert "must be one of" in next(e["result"] for e in ev if e["type"] == "tool_result")
    assert lead.put("/api/agents/lead", json={"acp_policy": "yolo"}).status_code == 422
