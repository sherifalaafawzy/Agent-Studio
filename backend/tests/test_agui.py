"""AG-UI: our chat events translated into protocol events (validated by the official SDK models), the /api/agui endpoint, and the interrupt/resume round trip."""
from __future__ import annotations

import asyncio
import json

import pytest
from ag_ui.core import RunAgentInput
from fastapi.testclient import TestClient

from app.agui import AguiTranslator, interrupt_schema, last_user_text
from app.main import create_app


def dump(events):
    return [json.loads(e.model_dump_json(by_alias=True, exclude_none=True)) for e in events]


def types(events):
    return [e["type"] for e in dump(events)]


def feed(tr, *evs):
    out = []
    for e in evs:
        out += tr.feed(e)
    return out


# ------------------------------------------------------------------------------------------------ the translation
def test_a_run_starts_with_its_state():
    tr = AguiTranslator("th", "r1", "parent")
    first = dump(tr.start())
    assert [e["type"] for e in first] == ["RUN_STARTED", "STATE_SNAPSHOT"] and first[0]["threadId"] == "th" and first[0]["parentRunId"] == "parent"
    assert first[1]["snapshot"]["status"] == "running" and first[1]["snapshot"]["totals"] == {"cost_usd": 0.0, "model_calls": 0, "tool_calls": 0}


def test_text_thinking_and_tools_become_messages_and_tool_calls():
    tr = AguiTranslator("th", "r1")
    out = feed(tr, {"type": "job", "job_id": "j"}, {"type": "session", "session_id": "s1", "agent": "eng", "provider": "mock", "trace_id": "tr"},
               {"type": "thinking", "delta": "hmm"}, {"type": "thinking", "delta": ""}, {"type": "thinking", "delta": " ok"}, {"type": "text", "delta": "Hel"}, {"type": "text", "delta": ""}, {"type": "text", "delta": "lo"},
               {"type": "tool_call", "run_id": "root", "tool": "read_file", "args": {"path": "a.py"}},
               {"type": "tool_result", "run_id": "root", "tool": "read_file", "result": {"ok": True}}, {"type": "tool_result", "run_id": "root", "tool": "orphan", "result": "x" * 5000})
    t = types(out)
    assert t == ["STATE_DELTA", "CUSTOM", "REASONING_START", "REASONING_MESSAGE_START", "REASONING_MESSAGE_CONTENT", "REASONING_MESSAGE_CONTENT",
                 "REASONING_MESSAGE_END", "REASONING_END", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END",
                 "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "STATE_DELTA", "TOOL_CALL_RESULT", "TOOL_CALL_RESULT"]
    e = dump(out)
    start = next(x for x in e if x["type"] == "TOOL_CALL_START")
    assert next(x for x in e if x["type"] == "TOOL_CALL_ARGS")["delta"] == '{"path": "a.py"}'
    results = [x for x in e if x["type"] == "TOOL_CALL_RESULT"]
    assert results[0]["toolCallId"] == start["toolCallId"] and results[1]["toolCallId"] != start["toolCallId"] and results[1]["content"].endswith("…") and tr.state["totals"]["tool_calls"] == 1
    assert next(x for x in e if x["type"] == "CUSTOM")["name"] == "agent_studio.session" and tr.state["session_id"] == "s1"
    assert [x["delta"] for x in e if x["type"] == "TEXT_MESSAGE_CONTENT"] == ["Hel", "lo"]
    assert types(tr.feed({"type": "tool_call", "tool": "t"})) == ["TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "STATE_DELTA"]        # no args at all
    assert dump(tr.feed({"type": "tool_call", "tool": "x", "args": None}))[1]["delta"] == "{}"


def test_open_messages_are_closed_when_the_stream_ends_early():
    tr = AguiTranslator("th", "r1")
    feed(tr, {"type": "thinking", "delta": "a"})
    assert types(tr.close()) == ["REASONING_MESSAGE_END", "REASONING_END"]
    feed(tr, {"type": "text", "delta": "a"}, {"type": "external_text", "run_id": "r2", "delta": "b"})
    assert types(tr.close()) == ["TEXT_MESSAGE_END", "TEXT_MESSAGE_END"] and tr.close() == []


def test_sub_agents_are_tagged_with_their_run():
    tr = AguiTranslator("th", "r1")
    out = dump(feed(tr, {"type": "agent_start", "run_id": "root", "agent": "eng", "parent": None, "task": "do"},
                    {"type": "agent_start", "run_id": "r2", "agent": "helper", "parent": "root", "task": "t" * 400},
                    {"type": "agent_start", "run_id": "r3", "agent": "deep", "parent": "r2", "task": ""},
                    {"type": "tool_call", "run_id": "r2", "tool": "read_file", "args": {}}, {"type": "tool_result", "run_id": "r2", "tool": "read_file", "result": "ok"},
                    {"type": "external_text", "run_id": "r2", "delta": "hello"}, {"type": "text", "delta": "root text"},
                    {"type": "agent_end", "run_id": "r3", "status": "done", "result": "fine"}, {"type": "agent_end", "run_id": "r2", "status": "error", "result": None},
                    {"type": "agent_end", "run_id": "root", "agent": "eng", "status": "done"}))
    kinds = [e["type"] for e in out]
    assert kinds.count("SUBAGENT_STARTED") == 2 and "STEP_STARTED" in kinds and "STEP_FINISHED" in kinds and "SUBAGENT_FINISHED" in kinds and "SUBAGENT_ERROR" in kinds
    started = [e for e in out if e["type"] == "SUBAGENT_STARTED"]
    assert started[0]["subagentRunId"] == "r2" and "parentSubagentRunId" not in started[0] and started[1]["parentSubagentRunId"] == "r2" and len(started[0]["description"]) <= 301
    assert all(e.get("subagentRunId") == "r2" for e in out if e["type"] in ("TOOL_CALL_START", "TOOL_CALL_RESULT")) and next(e for e in out if e["type"] == "SUBAGENT_ERROR")["message"] == "the sub-agent error"
    texts = [e for e in out if e["type"] == "TEXT_MESSAGE_START"]
    assert texts[0]["subagentRunId"] == "r2" and "subagentRunId" not in texts[1]
    assert tr.state["agents"]["r2"]["status"] == "error" and tr.state["agents"]["root"]["status"] == "done"
    assert types(tr.feed({"type": "agent_end", "run_id": "r9", "status": "cancelled", "result": "stopped"})) == ["SUBAGENT_ERROR", "STATE_DELTA"]


def test_cost_route_artifacts_memory_and_guardrails_become_state_and_activity():
    tr = AguiTranslator("th", "r1")
    out = dump(feed(tr, {"type": "model_call", "turn_cost_usd": 0.0123456789}, {"type": "model_call"},
                    {"type": "route", "provider": "openai", "model": "m", "tier": "flash", "reason": "easy", "engine": "rules"},
                    {"type": "artifact", "artifact": {"id": "a1", "name": "r.md", "type": "document", "size": 5, "mime": "text/markdown", "preview": "markdown", "sha256": "ab", "path": "r.md", "version": 1}},
                    {"type": "memory", "kind": "saved", "text": "likes tea", "seq": 4}, {"type": "guardrail", "stage": "output", "action": "flag", "kind": "rules"},
                    {"type": "fallback", "from": {"provider": "a"}, "to": {"provider": "b"}}, {"type": "note_queued", "text": "hi"}, {"type": "mystery"}, {"type": "end", "status": "done"}))
    assert tr.state["totals"]["model_calls"] == 2
    deltas = [e["delta"] for e in out if e["type"] == "STATE_DELTA"]
    assert deltas[0] == [{"op": "replace", "path": "/totals/model_calls", "value": 1}, {"op": "replace", "path": "/totals/cost_usd", "value": 0.012346}]
    assert {"op": "add", "path": "/artifacts/-", "value": {"id": "a1", "name": "r.md", "type": "document", "size": 5, "mime": "text/markdown", "preview": "markdown"}} in [d for ds in deltas for d in ds]
    acts = [e for e in out if e["type"] == "ACTIVITY_SNAPSHOT"]
    assert [a["activityType"] for a in acts] == ["artifact", "memory", "guardrail"] and acts[0]["content"]["sha256"] == "ab" and "seq" not in acts[1]["content"] and acts[1]["content"]["text"] == "likes tea"
    customs = [e for e in out if e["type"] == "CUSTOM"]
    assert [c["name"] for c in customs] == ["agent_studio.route", "agent_studio.fallback", "agent_studio.note_queued"] and customs[0]["value"]["engine"] == "rules"
    assert tr.state["route"] == {"provider": "openai", "model": "m", "tier": "flash", "reason": "easy"}


def test_the_run_ends_with_a_result_an_error_or_a_refusal():
    tr = AguiTranslator("th", "r1")
    out = dump(feed(tr, {"type": "text", "delta": "x"}, {"type": "done", "session_id": "s1", "reply": "x", "trace_id": "t", "usage": {"cost_usd": 0.1}, "steps": ["big"]}))
    fin = out[-1]
    assert fin["type"] == "RUN_FINISHED" and fin["outcome"] == {"type": "success"} and fin["result"] == {"session_id": "s1", "reply": "x", "trace_id": "t", "usage": {"cost_usd": 0.1}} and out[-2]["type"] == "TEXT_MESSAGE_END"
    assert tr.finished and tr.feed({"type": "text", "delta": "late"}) == []
    e = dump(feed(AguiTranslator("t", "r"), {"type": "error", "title": "Rate limit", "hint": "wait a minute", "code": "rate_limited"}))
    assert e == [{"type": "RUN_ERROR", "message": "Rate limit. wait a minute", "code": "rate_limited"}]
    e = dump(feed(AguiTranslator("t", "r"), {"type": "error", "message": "boom"}))
    assert e[0]["message"] == "Error. boom" and e[0]["code"] == "error"
    e = dump(feed(AguiTranslator("t", "r"), {"type": "refused", "detail": {"message": "cap reached"}}, ))
    assert e == [{"type": "RUN_ERROR", "message": "cap reached", "code": "budget_refused"}]
    assert dump(feed(AguiTranslator("t", "r"), {"type": "refused"}))[0]["message"] == "A spend cap stopped this run"


def test_an_external_agent_asking_pauses_the_run_with_an_interrupt():
    tr = AguiTranslator("th", "r1")
    perm = {"type": "external_ask", "ask": "permission", "request_id": "rq1", "title": "Run npm test?", "run_id": "r2", "agent": "claude", "label": "Claude Code",
            "options": [{"id": "allow", "name": "Allow"}, {"id": "reject", "name": "Reject"}], "input": "npm test"}
    out = dump(feed(tr, {"type": "text", "delta": "working"}, perm))
    fin = out[-1]
    assert fin["type"] == "RUN_FINISHED" and fin["outcome"]["type"] == "interrupt" and out[-2]["type"] == "TEXT_MESSAGE_END" and tr.paused
    it = fin["outcome"]["interrupts"][0]
    assert it["id"] == "rq1" and it["reason"] == "permission" and it["message"] == "Run npm test?" and it["subagentRunId"] == "r2" and it["responseSchema"]["properties"]["option_id"]["enum"] == ["allow", "reject"]
    assert it["metadata"]["label"] == "Claude Code" and tr.feed({"type": "text", "delta": "x"}) == []
    assert interrupt_schema({"ask": "question"})["properties"]["answers"]["type"] == "array" and interrupt_schema({"ask": "plan_approval"})["required"] == ["accepted"]
    bare = dump(feed(AguiTranslator("t", "r"), {"type": "external_ask", "request_id": "q"}))
    assert bare[0]["outcome"]["interrupts"][0]["message"] == "The agent is asking you something" and bare[0]["outcome"]["interrupts"][0]["reason"] == "input_required"


def test_last_user_text():
    def inp(messages):
        return RunAgentInput.model_validate({"threadId": "t", "runId": "r", "state": {}, "messages": messages, "tools": [], "context": [], "forwardedProps": {}})
    m = inp([{"id": "1", "role": "user", "content": "first"}, {"id": "2", "role": "assistant", "content": "ok"}, {"id": "3", "role": "user", "content": "  second  "}]).messages
    assert last_user_text(m) == "second"
    parts = inp([{"id": "1", "role": "user", "content": [{"type": "text", "text": "a"}, {"type": "image", "source": {"type": "url", "value": "http://x/y.png", "mimeType": "image/png"}}, {"type": "text", "text": "b"}]}]).messages
    assert last_user_text(parts) == "a\nb"
    assert last_user_text(inp([{"id": "1", "role": "assistant", "content": "hello"}]).messages) == "" and last_user_text([]) == ""


# ------------------------------------------------------------------------------------------------ the endpoint
def body(message="hello", thread="th1", run="run1", props=None, **extra):
    return {"threadId": thread, "runId": run, "state": {}, "messages": [{"id": "m1", "role": "user", "content": message}], "tools": [], "context": [], "forwardedProps": props or {}, **extra}


def events(r):
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def web(state, tmp_path):
    with TestClient(create_app(state)) as c:
        c.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128, "enabled": True})
        c.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
        yield c


def test_a_full_run_streams_agui_events_and_continues_the_same_chat(web, state):
    msg = '!tool write_file {"path": "r.md", "content": "# R"}'
    r = web.post("/api/agui", json=body(msg, props={"provider": "mock", "agent": "engineer"}))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = events(r)
    kinds = [e["type"] for e in ev]
    assert kinds[:2] == ["RUN_STARTED", "STATE_SNAPSHOT"] and kinds[-1] == "RUN_FINISHED" and ev[-1]["outcome"] == {"type": "success"}
    assert {"TOOL_CALL_START", "TOOL_CALL_RESULT", "TEXT_MESSAGE_CONTENT", "STATE_DELTA", "ACTIVITY_SNAPSHOT", "STEP_STARTED"} <= set(kinds)
    art = next(e for e in ev if e["type"] == "ACTIVITY_SNAPSHOT" and e["activityType"] == "artifact")
    assert art["content"]["name"] == "r.md" and next(e for e in ev if e["type"] == "TOOL_CALL_START")["toolCallName"] == "write_file"
    sid = state.agui_threads["th1"]
    assert state.chat.store.get(sid) is not None
    r2 = web.post("/api/agui", json=body("!tool read_file {\"path\": \"r.md\"}", run="run2", props={"provider": "mock", "agent": "engineer"}))
    assert events(r2)[-1]["type"] == "RUN_FINISHED" and len(state.chat.store.list(10)) == 1 and state.agui_threads["th1"] == sid
    assert web.post("/api/agui", json=body("hi again", thread=sid, run="run3", props={"provider": "mock"})).status_code == 200      # a chat id also works as a thread id
    assert len(state.chat.store.list(10)) == 1


def test_the_default_provider_is_the_first_one_switched_on(web):
    r = web.post("/api/agui", json=body("hello"))
    assert events(r)[-1]["type"] == "RUN_FINISHED"


def test_a_failed_run_is_an_error_event(web):
    r = web.post("/api/agui", json=body("hello", props={"provider": "nope"}))
    ev = events(r)
    assert ev[-1]["type"] == "RUN_ERROR" and ev[-1]["message"]


@pytest.mark.parametrize("payload,status,msg", [("not json", 422, "must be JSON"), ({"hello": 1}, 422, "not a valid AG-UI run"),
                                                (body("x", props={"max_tokens": 1}), 422, "forwardedProps is not valid"),
                                                ({**body("x"), "messages": [{"id": "m", "role": "assistant", "content": "hi"}]}, 422, "needs a user message")])
def test_bad_requests_are_refused_with_a_reason(web, payload, status, msg):
    r = web.post("/api/agui", content=payload if isinstance(payload, str) else json.dumps(payload), headers={"content-type": "application/json"})
    assert r.status_code == status and msg in r.json()["detail"]


def test_no_provider_switched_on(state):
    with TestClient(create_app(state)) as c:
        for p in state.providers.list():
            c.put(f"/api/providers/{p.id}", json={"enabled": False})
        r = c.post("/api/agui", json=body("hi"))
        assert r.status_code == 422 and "no model provider" in r.json()["detail"]


def test_a_busy_chat_is_refused_and_resume_needs_something_waiting(web, state):
    from app.jobs import Job
    state.agui_threads["th1"] = "sess1"
    resume = {"resume": [{"interruptId": "rq", "status": "resolved", "payload": {"option_id": "allow"}}]}
    assert web.post("/api/agui", json=body("x", **resume)).status_code == 409                 # nothing is waiting on this thread
    state.jobs.jobs["busy"] = Job("busy", session_id="sess1")
    r = web.post("/api/agui", json=body("again", props={"provider": "mock"}))
    assert r.status_code == 409 and "still running" in r.json()["detail"]


def test_an_interrupt_is_answered_by_a_resume_that_carries_on_with_the_same_run(web, state, monkeypatch):
    import app.agui_api as A
    gate: dict = {}
    asked = {"permission": {"type": "external_ask", "ask": "permission", "request_id": "rq1", "title": "Run it?", "run_id": "r2", "agent": "claude", "label": "Claude Code",
                            "options": [{"id": "allow", "name": "Allow"}]}}

    def scripted(s, b):
        async def work(emit, inbox):
            emit({"type": "session", "session_id": "sess1"})
            emit({"type": "text", "delta": "before "})
            emit(asked["permission"])
            gate["e"] = asyncio.Event()
            await gate["e"].wait()
            emit({"type": "text", "delta": "after"})
            emit({"type": "done", "session_id": "sess1", "reply": "before after"})
        return work
    monkeypatch.setattr(A, "make_chat_work", scripted)
    state.chat.store.ensure("sess1", "mock", None, None, None, "hi")
    first = events(web.post("/api/agui", json=body("start", props={"provider": "mock"})))
    assert first[-1]["type"] == "RUN_FINISHED" and first[-1]["outcome"]["type"] == "interrupt" and first[-1]["outcome"]["interrupts"][0]["id"] == "rq1"
    assert [e["delta"] for e in first if e["type"] == "TEXT_MESSAGE_CONTENT"] == ["before "] and state.agui_threads["th1"] == "sess1"
    answered = []
    monkeypatch.setattr(state.acp, "respond", lambda rid, b: (answered.append((rid, b)), gate["e"].set())[0])
    second = events(web.post("/api/agui", json=body("", run="run2", **{"resume": [{"interruptId": "rq1", "status": "resolved", "payload": {"option_id": "allow"}}]})))
    assert answered == [("rq1", {"option_id": "allow"})]
    assert [e["type"] for e in second][:2] == ["RUN_STARTED", "STATE_SNAPSHOT"] and second[-1]["type"] == "RUN_FINISHED" and second[-1]["outcome"] == {"type": "success"}
    deltas = [e["delta"] for e in second if e["type"] == "TEXT_MESSAGE_CONTENT"]
    assert deltas == ["after"]                                                              # only what happened after the answer is streamed again


def test_resume_can_cancel_or_fail(web, state, monkeypatch):
    import app.agui_api as A
    from app.acp_agents import AcpError
    gate: dict = {}

    def scripted(s, b):
        async def work(emit, inbox):
            emit({"type": "session", "session_id": "sess1"})
            emit({"type": "external_ask", "ask": "question", "request_id": "rq9", "title": "Which?", "run_id": "r2"})
            gate["e"] = asyncio.Event()
            await gate["e"].wait()
            emit({"type": "done", "session_id": "sess1", "reply": ""})
        return work
    monkeypatch.setattr(A, "make_chat_work", scripted)
    state.chat.store.ensure("sess1", "mock", None, None, None, "hi")
    web.post("/api/agui", json=body("go", props={"provider": "mock"}))
    resume = lambda status, payload=None: body("", run="r2", **{"resume": [{"interruptId": "rq9", "status": status, **({"payload": payload} if payload is not None else {})}]})
    monkeypatch.setattr(state.acp, "respond", lambda rid, b: (_ for _ in ()).throw(AcpError("no such pending request")))
    bad = web.post("/api/agui", json=resume("resolved", {"answers": ["a"]}))
    assert bad.status_code == 409 and "no such pending" in bad.json()["detail"]
    seen = []
    monkeypatch.setattr(state.acp, "respond", lambda rid, b: (seen.append(b), gate["e"].set())[0])
    ok = events(web.post("/api/agui", json=resume("cancelled")))
    assert seen == [{"cancel": True}] and ok[-1]["type"] == "RUN_FINISHED"


def test_keepalives_pass_through_and_a_stream_that_just_ends_is_closed_cleanly(web, state, monkeypatch):
    async def follow(job, after=-1):
        yield None
        yield {"seq": 0, "type": "session", "session_id": "sx"}
        yield {"seq": 1, "type": "text", "delta": "partial"}
    monkeypatch.setattr(state.jobs, "follow", follow)
    r = web.post("/api/agui", json=body("hi", props={"provider": "mock"}))
    assert ": keepalive" in r.text
    ev = events(r)
    assert [e["type"] for e in ev][-2:] == ["TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"] and state.agui_threads["th1"] == "sx"
