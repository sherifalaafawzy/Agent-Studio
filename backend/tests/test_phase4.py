"""Phase 4: persistent chats + resume, streaming, agent-created sub-agents (and their off switches),
runtime visibility of every agent run, and the OpenTelemetry store / OTLP receiver."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.chat import TurnTrace
from app.db import Database
from app.harness.tools import ToolContext
from app.main import State, create_app


@pytest.fixture
def rich(client, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    (tmp_path / "ws").mkdir(exist_ok=True)
    return client


def chat(c, msg, agent=None, **kw):
    body = {"provider": "mock", "message": msg, **kw}
    if agent:
        body["agent"] = agent
    return c.post("/api/chat", json=body)


def sse(c, msg, agent=None, **kw):
    body = {"provider": "mock", "message": msg, **kw}
    if agent:
        body["agent"] = agent
    out = []
    with c.stream("POST", "/api/chat/stream", json=body) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data: "):
                ev = json.loads(line[6:])
                ev.pop("seq", None)
                if ev["type"] not in ("job", "end"):     # background-job framing (covered in test_long_runs_external)
                    out.append(ev)
    return out


def spawn_line(name="helper", task="hello", tools=(), mcp=(), tier="flash"):
    return "!tool spawn_agent " + json.dumps(dict(name=name, instruction="You are a helper.", task=task, tools=list(tools), mcp=list(mcp), tier=tier))


def tool_names(state, agent_id, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    ctx = ToolContext(ws, state.chat.sandbox_for(ws), state.skills, state.mcp)
    agent = asyncio.run(state.chat._build(state.agents.get(agent_id), chat_provider="mock", root_model=None, max_tokens=None,
                                          ctx=ctx, turn=TurnTrace(), toolsets=[]))
    return [getattr(t, "name", None) or getattr(t, "__name__", "") for t in agent.tools]


# ------------------------------------------------------------------ persistence / resume
def test_chats_are_saved_listed_renamed_deleted(rich):
    r = chat(rich, "first question about cats").json()
    sid = r["session_id"]
    lst = rich.get("/api/chat/sessions").json()
    assert lst[0]["id"] == sid and lst[0]["title"].startswith("first question") and lst[0]["messages"] == 2
    got = rich.get(f"/api/chat/sessions/{sid}").json()
    assert [m["role"] for m in got["messages"]] == ["user", "bot"]
    assert got["messages"][1]["meta"]["usage"]["cost_usd"] >= 0 and got["session"]["provider"] == "mock"
    assert rich.put(f"/api/chat/sessions/{sid}", json={"title": "Cats"}).status_code == 200
    assert rich.get("/api/chat/sessions").json()[0]["title"] == "Cats"
    assert rich.delete(f"/api/chat/sessions/{sid}").status_code == 200
    assert rich.get(f"/api/chat/sessions/{sid}").status_code == 404 and rich.get("/api/chat/sessions").json() == []


def test_conversation_survives_a_server_restart(tmp_path):
    db_path = tmp_path / "t.db"
    s1 = State(Database(db_path))
    c1 = TestClient(create_app(s1))
    c1.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    sid = chat(c1, "remember: the codeword is plum").json()["session_id"]
    # "restart": brand-new State / services on the same database file
    s2 = State(Database(db_path))
    c2 = TestClient(create_app(s2))
    assert asyncio.run(s2.chat.sessions.get_session(app_name="agent_studio", user_id="local", session_id=sid)) is None
    r = chat(c2, "what was the codeword?", session_id=sid)
    assert r.status_code == 200 and r.json()["session_id"] == sid
    live = asyncio.run(s2.chat.sessions.get_session(app_name="agent_studio", user_id="local", session_id=sid))
    texts = [p.text for e in live.events for p in (e.content.parts if e.content else []) if p.text]
    assert "remember: the codeword is plum" in texts[0] + " ".join(texts)         # history was replayed into the model's context
    assert len(live.events) == 4
    assert [m["role"] for m in c2.get(f"/api/chat/sessions/{sid}").json()["messages"]] == ["user", "bot", "user", "bot"]


def test_budget_refusal_is_saved_too(rich):
    rich.put("/api/providers/mock", json={"cap_usd": 0.0000001})
    r = chat(rich, "x" * 400)
    if r.status_code == 402:
        sid = rich.get("/api/chat/sessions").json()[0]["id"]
        assert [m["role"] for m in rich.get(f"/api/chat/sessions/{sid}").json()["messages"]] == ["user", "refused"]


# ------------------------------------------------------------------ streaming
def test_stream_events_and_persistence(rich):
    ev = sse(rich, "stream me please")
    types = [e["type"] for e in ev]
    assert types[0] == "agent_start" or "session" in types[:2]
    assert "session" in types and "text" in types and "model_call" in types and types[-1] == "done"
    done = ev[-1]
    assert "stream me please" in done["reply"] and "".join(e["delta"] for e in ev if e["type"] == "text") == done["reply"]
    got = rich.get(f"/api/chat/sessions/{done['session_id']}").json()["messages"]
    assert got[-1]["role"] == "bot" and got[-1]["text"] == done["reply"]


def test_stream_unknown_provider_is_an_error_event(rich):
    with rich.stream("POST", "/api/chat/stream", json={"provider": "nope", "message": "x"}) as r:
        evs = [e for e in (json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")) if e["type"] not in ("job", "end")]
    assert evs[-1]["type"] == "error"


def test_cancel_mid_turn_saves_progress_and_leaks_no_reservation(rich, state):
    (state.chat.workspace_for(None) / "a.txt").write_text("x")

    async def go():
        task = asyncio.current_task()

        def boom(ev):
            if ev["type"] == "tool_call":
                task.cancel()                      # what a client disconnect does to the running turn
        await state.chat.send("mock", None, "!tool list_dir {}", None, agent_id="explorer", emit=boom)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(go())
    assert state.db.query("SELECT * FROM reservations") == []
    sid = state.chat.store.list()[0]["id"]
    bot = state.chat.store.messages(sid)[-1]
    assert bot["role"] == "bot" and bot["meta"]["status"] == "cancelled"


# ------------------------------------------------------------------ agents creating agents
def test_agent_builds_and_runs_a_subagent_and_everything_is_visible(rich, tmp_path):
    (tmp_path / "ws" / "hello.txt").write_text("hi")
    msg = spawn_line("lister", task='!tool list_dir {"path": "."}', tools=["list_dir"])
    ev = sse(rich, msg, agent="engineer")
    done = ev[-1]
    assert done["type"] == "done", done
    starts = [e for e in ev if e["type"] == "agent_start"]
    assert [s["agent"] for s in starts] == ["engineer", "engineer__lister_1"]
    child = starts[1]
    assert child["spawned"] is True and child["parent"] == "root" and child["definition"]["tools"] == ["list_dir"]
    assert child["definition"]["instruction"] == "You are a helper."
    # the spawned agent's own tool call is tagged with its run id, live
    tc = [e for e in ev if e["type"] == "tool_call" and e["tool"] == "list_dir"]
    assert tc and tc[0]["run_id"] == child["run_id"] and tc[0]["agent"] == "engineer__lister_1"
    end = next(e for e in ev if e["type"] == "agent_end" and e["run_id"] == child["run_id"])
    assert end["status"] == "done" and "hello.txt" in end["result"]
    runs = {r["run_id"]: r for r in done["runs"]}
    assert runs[child["run_id"]]["steps"][0]["tool"] == "list_dir" and runs["root"]["status"] == "done"
    # saved with the chat -> visible again later
    saved = rich.get(f"/api/chat/sessions/{done['session_id']}").json()["messages"][-1]["meta"]["runs"]
    assert any(r["spawned"] and r["agent"] == "engineer__lister_1" for r in saved)


def test_static_subagents_are_tracked_too(rich, tmp_path):
    msg = '!agent explorer {"request": "!tool list_dir {\\"path\\": \\".\\"}"}'
    j = chat(rich, msg, agent="engineer").json()
    kinds = [(r["agent"], r["spawned"], r["parent"]) for r in j["runs"]]
    assert kinds[0] == ("engineer", False, None) and ("explorer", False, "root") in kinds


def test_off_switches(rich, state, tmp_path):
    assert "spawn_agent" in tool_names(state, "engineer", tmp_path)             # seeded engineer starts enabled
    assert "spawn_agent" not in tool_names(state, "coder", tmp_path)            # everything else is opt-in
    rich.put("/api/agents/engineer", json={"can_spawn": False})
    assert "spawn_agent" not in tool_names(state, "engineer", tmp_path)         # per-agent switch
    rich.put("/api/agents/engineer", json={"can_spawn": True})
    assert "spawn_agent" in tool_names(state, "engineer", tmp_path)
    r = rich.put("/api/settings", json={"agent_spawning_enabled": False}).json()
    assert r["agent_spawning_enabled"] is False
    assert "spawn_agent" not in tool_names(state, "engineer", tmp_path)         # global kill switch beats the per-agent flag


def test_spawn_cannot_escalate_privileges_or_exceed_the_limit(rich, tmp_path):
    rich.post("/api/agents", json={"id": "narrow", "name": "Narrow", "tools": ["list_dir", "read_file"], "can_spawn": True})
    j = chat(rich, spawn_line("sneaky", tools=["write_file"]), agent="narrow").json()
    res = [s for s in j["steps"] if s["type"] == "result" and s["tool"] == "spawn_agent"][0]["result"]
    assert "only grant" in res and not any(r["spawned"] for r in j["runs"])
    j = chat(rich, spawn_line("tools_ok", tools=["list_dir"], mcp=["fetch"]), agent="narrow").json()
    assert "only grant MCP" in [s for s in j["steps"] if s["type"] == "result"][0]["result"]
    rich.put("/api/settings", json={"max_spawns_per_turn": 1})
    j = chat(rich, spawn_line("one") + "\n" + spawn_line("two"), agent="engineer").json()
    results = [s["result"] for s in j["steps"] if s["type"] == "result" and s["tool"] == "spawn_agent"]
    assert sum(r["spawned"] for r in j["runs"]) == 1 and "spawn limit" in results[1]


def test_spawned_agent_runs_are_guarded_by_the_budget(rich):
    j = chat(rich, spawn_line("h", task="hi there"), agent="engineer").json()
    assert {c["agent"] for c in j["model_calls"]} >= {"engineer", "engineer__h_1"}
    assert all("run_id" in c for c in j["model_calls"])


# ------------------------------------------------------------------ OpenTelemetry
def otlp_trace(source="claude"):
    return {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": source}}]},
                               "scopeSpans": [{"spans": [{"traceId": "a" * 32, "spanId": "b" * 16, "name": "ext.span", "startTimeUnixNano": "1000000000",
                                                          "endTimeUnixNano": "2500000000", "attributes": [{"key": "k", "value": {"intValue": "7"}}],
                                                          "status": {"code": 1}}]}]}]}


def test_receiver_stores_traces_metrics_logs(client):
    assert client.post("/v1/traces", json=otlp_trace()).json()["stored"] == 1
    m = {"resourceMetrics": [{"resource": {"attributes": [{"key": "agent_studio.agent", "value": {"stringValue": "claude"}}]}, "scopeMetrics": [{"metrics": [
        {"name": "claude_code.token.usage", "unit": "tokens", "sum": {"dataPoints": [{"asInt": "42", "timeUnixNano": "5", "attributes": [{"key": "type", "value": {"stringValue": "input"}}]}]}}]}]}]}
    assert client.post("/v1/metrics", json=m).json()["stored"] == 1
    lg = {"resourceLogs": [{"resource": {"attributes": []}, "scopeLogs": [{"logRecords": [
        {"timeUnixNano": "9", "severityText": "INFO", "body": {"stringValue": "hello"}, "attributes": [{"key": "event.name", "value": {"stringValue": "api_request"}}]}]}]}]}
    assert client.post("/v1/logs", json=lg).json()["stored"] == 1
    s = client.get("/api/otel/summary").json()
    assert s["total"] == 3 and {(x["signal"], x["source"]) for x in s["by_source"]} == {("trace", "claude"), ("metric", "claude"), ("log", "unknown")}
    tr = client.get("/api/otel/traces").json()[0]
    assert tr["root"] == "ext.span" and tr["duration_ms"] == 1500.0
    spans = client.get(f"/api/otel/traces/{'a' * 32}").json()
    assert spans[0]["attrs"] == {"k": 7} and spans[0]["status"] == "ok"
    met = client.get("/api/otel/records?signal=metric").json()[0]
    assert met["name"] == "claude_code.token.usage" and met["body"]["value"] == "42" and met["attrs"] == {"type": "input"}
    assert client.get("/api/otel/records?signal=log").json()[0]["name"] == "api_request"


def test_receiver_rules(state):
    c = TestClient(create_app(state))
    assert c.post("/v1/traces", content=b"\x0a\x00", headers={"content-type": "application/x-protobuf"}).status_code == 415
    assert c.post("/v1/nope", json={}).status_code == 404
    assert c.post("/v1/traces", content=b"not json", headers={"content-type": "application/json"}).status_code == 400
    remote = TestClient(create_app(state), client=("203.0.113.9", 5555))
    assert remote.post("/v1/traces", json=otlp_trace()).status_code == 403          # loopback only
    c.put("/api/otel/settings", json={"enabled": False})
    assert c.post("/v1/traces", json=otlp_trace()).json()["stored"] == 1             # accepted...
    assert c.get("/api/otel/summary").json()["total"] == 0                          # ...but nothing is stored while disabled


def test_agents_own_spans_are_stored_as_a_trace(rich, tmp_path):
    (tmp_path / "ws" / "f.txt").write_text("x")
    j = chat(rich, spawn_line("lister", task='!tool list_dir {"path": "."}', tools=["list_dir"]), agent="engineer").json()
    spans = rich.get(f"/api/otel/traces/{j['trace_id']}").json()
    names = [s["name"] for s in spans]
    assert "agent.engineer" in names and "agent.engineer__lister_1" in names and "llm.call" in names and "tool.list_dir" in names
    by_id = {s["span_id"]: s for s in spans}
    child = next(s for s in spans if s["name"] == "agent.engineer__lister_1")
    assert by_id[child["parent_id"]]["name"] == "agent.engineer"                      # the tree is reconstructable
    assert child["attrs"]["spawned"] is True and {s["source"] for s in spans} >= {"engineer", "engineer__lister_1"}
    assert rich.get("/api/otel/traces?source=engineer").json()[0]["trace_id"] == j["trace_id"]


def test_settings_roundtrip_hide_header_values_and_validate(client):
    r = client.put("/api/otel/settings", json={"forward_url": "http://collector:4318/", "forward_headers": {"Authorization": "Bearer SECRET"},
                                               "retention_days": 7}).json()
    assert r["forward_url"] == "http://collector:4318" and r["forward_header_names"] == ["Authorization"] and r["retention_days"] == 7
    assert "SECRET" not in json.dumps(client.get("/api/otel/settings").json())
    assert client.put("/api/otel/settings", json={"forward_url": "ftp://x"}).status_code == 422
    assert client.delete("/api/otel/records").json() == {"deleted": 0}


def test_external_agents_get_otel_env_and_a_turn_span(state, monkeypatch):
    import sys
    from pathlib import Path
    state.db.set_setting("workspace_dir", str(Path(state.db.path).parent / "ws"))
    state.acp.store.create("fake", "Fake", sys.executable, [str(Path(__file__).parent / "fake_acp_agent.py")])
    env = state.tel.agent_env("claude", "s1")
    assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/json" and env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert "agent_studio.agent=claude" in env["OTEL_RESOURCE_ATTRIBUTES"] and "OTEL_LOG_USER_PROMPTS" not in env   # no prompt capture
    state.tel.update({"inject_env": False})
    assert state.tel.agent_env("claude") == {}
    state.tel.update({"inject_env": True})

    async def main():
        s = await state.acp.get_or_open("fake", None, None)
        try:
            ev = []
            await state.acp.prompt(s, "!env OTEL_EXPORTER_OTLP_PROTOCOL", ev.append)
            assert "".join(e["text"] for e in ev if e["type"] == "text") == "env:OTEL_EXPORTER_OTLP_PROTOCOL=present"
            await state.acp.prompt(s, "!write notes/a.txt|hello", lambda e: asyncio.get_running_loop().call_soon(
                lambda: state.acp.respond(e["request_id"], {"option_id": "allow-once"})) if e["type"] == "permission" else None)
        finally:
            await state.acp.close_all()
    asyncio.run(main())
    spans = state.tel.records("trace", "fake")
    turn_span = next(s for s in spans if s["name"] == "acp.turn.fake" and s["attrs"]["turn"] == 2)
    assert turn_span["attrs"]["stop_reason"] == "end_turn"
    assert any(s["name"].startswith("tool.") and s["parent_id"] == turn_span["span_id"] for s in spans)      # tool calls hang off the turn


def test_runs_carry_timing_and_instructions_for_the_timeline(rich, tmp_path):
    (tmp_path / "ws" / "f.txt").write_text("x")
    j = chat(rich, spawn_line("lister", task='!tool list_dir {"path": "."}', tools=["list_dir"]), agent="engineer").json()
    root, child = j["runs"][0], next(r for r in j["runs"] if r["spawned"])
    assert "lead engineer" in root["instruction"].lower() and child["instruction"].startswith("You are a helper.")
    assert root["started"] <= child["started"] <= child["ended"] <= root["ended"]
    for c in root["calls"] + child["calls"]:
        assert c["t0"] <= c["t1"] and "usage_reported" in c
    res = [s for s in child["steps"] if s["type"] == "result"][0]
    call = [s for s in child["steps"] if s["type"] == "call"][0]
    assert call["ts"] <= res["ts"] and res["ms"] >= 0 and res["ok"] is True
