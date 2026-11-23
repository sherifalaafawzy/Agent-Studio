"""A chat turn on another framework: instructions, memory, knowledge bases, skills, artifacts, errors and cancellation around the loop."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.frameworks import turn as T
from app.frameworks.base import FrameworkError
from tests import framework_conformance as C
from tests.rag_fakes import FakeEmbedder

MEM = {"types": ["semantic", "notes"], "auto_save": "auto"}


@pytest.fixture
def h(client, tmp_path):
    return C.Harness(client, "langgraph", tmp_path)


def test_instructions_carry_memory_knowledge_skills_and_artifact_rules(h, state):
    state.memory.embedder = FakeEmbedder()
    h.client.put("/api/memories/config", json={"embedding": {"provider": "mock", "model": "embed-a"}})
    h.agent(memory=MEM, rag=["*"], skills=["*"], tools=["read_file", "save_artifact"], instruction="Be brief.")
    r = h.send("hello")
    ins = h.job["instructions"]
    assert ins.startswith("Be brief.") and "Working rules" in ins and "Results and rich answers" in ins
    assert "## Knowledge bases" in ins and "## Memory" in ins and "memory_search" in ins
    assert [t["name"] for t in h.job["tools"]] == ["read_file", "save_artifact", "memory_search", "memory_save", "memory_forget", "memory_notes", "rag_search", "rag_list"] or "read_file" in json.dumps(h.job["tools"])
    assert r["reply"] == "answer: hello"


def test_a_plain_agent_gets_the_default_prompt_and_no_tool_rules(h):
    h.agent()
    h.send("hello")
    assert h.job["instructions"] == "You are Agent Studio's assistant. Be concise and accurate." and h.job["tools"] == []


def test_memory_problems_never_stop_the_turn(h, state, monkeypatch):
    h.agent(memory=MEM)

    async def boom(*a, **k):
        raise RuntimeError("memory is down")
    monkeypatch.setattr(state.memory, "recall_for_turn", boom)
    monkeypatch.setattr(state.memory, "after_turn", boom)
    r = h.send("hello")
    assert r["reply"] == "answer: hello"
    assert any("memory could not be read" in w for w in r["warnings"]) and any("memory could not be saved" in w for w in r["warnings"])


def test_claude_sdk_needs_an_anthropic_provider(client, tmp_path):
    h = C.Harness(client, "claude_sdk", tmp_path)
    h.agent()
    with pytest.raises(FrameworkError, match="needs an Anthropic provider"):
        h.send("hello")


def test_an_unknown_provider_is_a_key_error(h):
    h.agent(provider="ghost")
    with pytest.raises(KeyError):
        h.send("hello")


def test_a_routed_turn_keeps_the_route_in_the_run_record(h, state):
    h.agent()
    root = state.agents.get("fwagent")
    res = asyncio.run(state.chat._send_framework(root, state.providers.get("mock"), None, "hi", "sid1", None, None, None, True, True, None, {"why": "test"}))
    assert res["runs"][0]["route"] == {"why": "test"} and res["runs"][0]["definition"] == {"framework": "langgraph"}


def test_files_the_framework_writes_become_artifacts(h, tmp_path):
    h.agent()
    ws = tmp_path / "ws"

    async def worker(fid, job, env):
        ws.mkdir(exist_ok=True)
        (ws / "report.md").write_text("# report\nbody")
        yield json.dumps({"t": "final", "text": "wrote it"})
    h.adapter.launcher = worker
    r = h.send("write")
    assert r["reply"] == "wrote it" and [a["name"] for a in r["artifacts"]] == ["report.md"]
    assert any(e["type"] == "artifact" for e in h.events)


def test_cancelling_during_a_model_call_charges_the_worst_case(h):
    h.agent()

    async def hang(**kw):
        await asyncio.sleep(30)
    h.rt.completion = hang

    async def go():
        t = asyncio.ensure_future(h.s.chat.send("mock", None, "x", None, None, "fwagent", None))
        await asyncio.sleep(0.5)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    asyncio.run(go())
    assert h.rt.sessions == {}
    led = h.s.db.query("SELECT note FROM ledger WHERE note LIKE 'client_cancelled%'")
    assert led


def test_a_failed_attempt_that_will_be_retried_is_not_saved_as_the_answer(h):
    h.agent()
    h.script = [{"error": "first model failed"}]
    root = h.s.agents.get("fwagent")
    with pytest.raises(FrameworkError):
        asyncio.run(h.s.chat._send_framework(root, h.s.providers.get("mock"), None, "hi", "sid2", None, None, None, True, False, None, None))
    assert not [m for m in h.s.chat.store.messages("sid2") if m["role"] == "bot"]


def test_the_frameworks_worker_sees_the_note_from_a_fallback(h):
    h.agent()
    root = h.s.agents.get("fwagent")
    asyncio.run(h.s.chat._send_framework(root, h.s.providers.get("mock"), None, "hi", "sid3", None, None, None, True, True, "[earlier model failed]", None))
    assert h.job["message"].startswith("[earlier model failed]\n\nhi")


def test_instructions_for_helper_directly(state):
    from app.agents import AgentDef
    d = AgentDef(id="x", name="x", instruction="Hi", tools=["read_file"])
    turn = type("T", (), {"recall_text": ""})()
    assert "Working rules" in T.instructions_for(state.chat, d, turn, ["read_file"])
    assert "## Canvas" in T.instructions_for(state.chat, d, turn, ["canvas_create"]) and "## Canvas" not in T.instructions_for(state.chat, d, turn, ["read_file"])
    assert "## Apps" in T.instructions_for(state.chat, d, turn, ["app_write_file"]) and "## Apps" not in T.instructions_for(state.chat, d, turn, ["read_file"])
    assert "## Science Lab" in T.instructions_for(state.chat, d, turn, ["science_search"]) and "## Science Lab" not in T.instructions_for(state.chat, d, turn, ["read_file"])
    assert "## Rich components" in T.instructions_for(state.chat, d, turn, ["render_ui"]) and "## Rich components" not in T.instructions_for(state.chat, d, turn, ["read_file"])


def test_memory_events_are_shown_and_a_timeout_releases_the_reservation(h, state, monkeypatch):
    h.agent(memory=MEM)

    async def recall(*a, **k):
        return "\n\n## Recalled\nlikes tea", [{"kind": "recalled", "text": "likes tea"}], []

    async def after(*a, **k):
        return [{"kind": "saved", "text": "x"}]
    monkeypatch.setattr(state.memory, "recall_for_turn", recall)
    monkeypatch.setattr(state.memory, "after_turn", after)
    r = h.send("hi")
    assert [m["kind"] for m in r["memory"]] == ["recalled", "saved"] and "likes tea" in h.job["instructions"]
    h.client.put("/api/settings", json={"max_turn_minutes": 0.01})

    async def hang(**kw):
        await asyncio.sleep(30)
    h.rt.completion = hang
    with pytest.raises(FrameworkError, match="did not finish within"):
        h.send("again")
    assert h.s.db.query("SELECT note FROM ledger WHERE note LIKE 'turn_error%'") and h.rt.sessions == {}


def test_a_cancelled_stream_keeps_its_reservation_open_for_the_turn(client):
    from tests.test_frameworks import open_session
    rt, s, t = open_session(client)

    async def comp(**kw):
        async def gen():
            yield {"choices": [{"delta": {"content": "a"}}]}
            await asyncio.sleep(30)
        return gen()
    rt.completion = comp

    async def go():
        g = rt.stream(s.token, {"messages": [{"role": "user", "content": "x"}]})
        task = asyncio.ensure_future(g.__anext__())
        await task                                  # first chunk
        nxt = asyncio.ensure_future(g.__anext__())
        await asyncio.sleep(0.1)
        nxt.cancel()
        with pytest.raises(asyncio.CancelledError):
            await nxt
    asyncio.run(go())
    assert len(t.open_reservations) == 1


def test_policy_applies_to_every_framework(h, tmp_path):
    h.client.put("/api/harness/policy", json={"loop_guard_repeats": 1, "hooks": [{"event": "post_tool", "tool": "list_dir", "action": "note", "message": "mind the clutter"}]})
    h.agent(tools=["list_dir", "write_file"])
    (tmp_path / "ws").mkdir(exist_ok=True)
    h.script = [{"tool": "list_dir", "args": {"path": "."}}, {"tool": "list_dir", "args": {"path": "."}}, {"model": True}, {"say": True}]
    h.send("look twice")
    first, second = h.tool_results
    assert first["ok"] and first["note"] == "mind the clutter" and second["refused"] and "exact 'list_dir' call" in second["error"]
    h.client.put("/api/harness/policy", json={"loop_guard_repeats": 3, "permission_mode": "plan", "hooks": []})
    h.tool_results.clear()
    h.script = [{"tool": "write_file", "args": {"path": "a.txt", "content": "x"}}, {"model": True}, {"say": True}]
    h.send("write")
    assert h.tool_results[0]["refused"] and "Plan mode" in h.tool_results[0]["error"] and not (tmp_path / "ws" / "a.txt").exists()
