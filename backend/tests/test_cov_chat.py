"""Coverage for app/chat.py: note injection, sub-agent run bookkeeping, reasoning budgets, spawn and external-agent
guards, steering / cancel / error settlement, history rebuilds, summaries, model fallback chains.

Model calls are never real: the ADK Runner is replaced by a scripted fake, litellm.acompletion is monkeypatched,
and MCP toolsets are stubbed. Each test asserts observable behaviour (replies, events, stored messages, spend)."""
import asyncio
import json
from types import SimpleNamespace

import litellm
import pytest
from google.adk.agents import LlmAgent
from google.adk.events import Event
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.base_toolset import BaseToolset
from google.genai import types

import app.chat as chat_mod
from app import context as CX
from app.agents import AgentDef
from app.chat import CUR_RUN, MAX_DEPTH, TrackedAgentTool, TurnTrace, make_callbacks
from app.db import data_dir
from app.harness.tools import ToolContext
from app.jobs import NoteInbox

# ------------------------------------------------------------------ helpers


def _ctx(state, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(ws, state.chat.sandbox_for(ws), state.skills, state.mcp)


def _req(text="hello"):
    """A minimal stand-in for the ADK LlmRequest the model callbacks read and mutate."""
    return SimpleNamespace(config=None, contents=[types.Content(role="user", parts=[types.Part(text=text)])])


def _callbacks(state, turn, agent_id="engineer", provider="mock", model="mock-cheap", max_out=64):
    return make_callbacks(state.guard, turn, agent_id, provider, model, "openai/mock-cheap", max_out)


def _ev(agent, text, *, partial=None, thought=False):
    return Event(invocation_id="inv", author=agent.name, partial=partial,
                 content=types.Content(role="model", parts=[types.Part(text=text, thought=thought)]))


def _reserve(agent):
    """Run the agent's before-model guard, as the ADK would before a real model call."""
    agent.before_model_callback(None, _req())


STEPS: list = []


class FakeRunner:
    """Replaces app.chat.Runner: each run_async call plays the next scripted step (default: a plain answer)."""

    def __init__(self, app_name, agent, session_service, auto_create_session):
        self.agent = agent

    async def run_async(self, *, user_id, session_id, new_message, run_config):
        step = STEPS.pop(0) if STEPS else _answer("default answer")
        async for event in step(self.agent):
            yield event


def _use_steps(monkeypatch, *steps):
    STEPS[:] = list(steps)
    monkeypatch.setattr(chat_mod, "Runner", FakeRunner)


def _answer(text):
    async def step(agent):
        yield _ev(agent, text)
    return step


def _raising(exc):
    async def step(agent):
        raise exc
        yield  # pragma-free: keeps this an async generator
    return step


def _reserving_then_raising(exc):
    async def step(agent):
        _reserve(agent)
        raise exc
        yield
    return step


def _hanging_after_reserve(agent_partial=None):
    async def step(agent):
        _reserve(agent)
        if agent_partial:
            yield _ev(agent, agent_partial, partial=True)
        await asyncio.sleep(3600)
        yield _ev(agent, "never delivered")
    return step


def _steer_after_reserve(box, note="change course"):
    async def step(agent):
        _reserve(agent)
        yield _ev(agent, "half", partial=True)
        box["inbox"].push(note, "steer")          # cancels this model step; the engine resumes with the note
        await asyncio.sleep(3600)
        yield _ev(agent, "never delivered")
    return step


# ------------------------------------------------------------------ model callbacks


def test_queued_notes_are_read_at_the_next_model_step(state):
    state.providers.update("mock", {"cap_usd": 1000})
    turn = TurnTrace()
    turn.inbox = NoteInbox(lambda ev: None)
    before, *_ = _callbacks(state, turn)
    turn.inbox.push("use metric units", "queue")
    req = _req("how far is it?")
    before(None, req)
    last = req.contents[-1]
    assert last.role == "user" and len(last.parts) == 2
    assert "use metric units" in last.parts[1].text and last.parts[0].text == "how far is it?"
    assert turn.inbox.items == []                      # delivered, not left queued


def test_subagent_thoughts_text_and_unmetered_usage(state):
    state.providers.update("mock", {"cap_usd": 1000})
    turn = TurnTrace()
    events = []
    turn.emit = events.append
    turn.start_run("r1", "sub", "root")
    before, after, *_ = _callbacks(state, turn, agent_id="sub")
    tok = CUR_RUN.set("r1")
    try:
        before(None, _req())
        reservation = next(iter(turn.open_reservations.values()))
        after(None, LlmResponse(content=types.Content(role="model", parts=[
            types.Part(text="pondering", thought=True), types.Part(text="the answer")])))
    finally:
        CUR_RUN.reset(tok)
    run = turn.runs_by_id["r1"]
    assert run["thoughts"] == ["pondering"] and run["texts"] == ["the answer"]
    assert any(e["type"] == "agent_thought" and e["text"] == "pondering" for e in events)
    call = run["calls"][0]
    assert call["usage_reported"] is False                         # no usage in the response
    assert call["cost_usd"] == pytest.approx(reservation.projected_usd)   # charged the worst case
    assert turn.open_reservations == {}


def test_subagent_run_records_error_and_cancellation(state, monkeypatch):
    turn = TurnTrace()
    tool = TrackedAgentTool(LlmAgent(name="sub", instruction="sub instruction"), turn)

    async def boom(self, *, args, tool_context):
        raise RuntimeError("sub crashed")

    monkeypatch.setattr(AgentTool, "run_async", boom)
    with pytest.raises(RuntimeError, match="sub crashed"):
        asyncio.run(tool.run_async(args={"request": "go"}, tool_context=None))
    assert turn.runs[-1]["status"] == "error" and "sub crashed" in turn.runs[-1]["result"]

    async def cancelled(self, *, args, tool_context):
        raise asyncio.CancelledError()

    monkeypatch.setattr(AgentTool, "run_async", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(tool.run_async(args={"request": "again"}, tool_context=None))
    assert turn.runs[-1]["status"] == "cancelled"
    assert CUR_RUN.get() == "root"                                  # the run context was restored


# ------------------------------------------------------------------ agent tree construction


def test_relative_workspace_lives_under_the_data_dir(state):
    p = state.chat.workspace_for("proj-rel")
    assert p == (data_dir() / "workspaces" / "proj-rel").resolve() and p.is_dir()


def test_agent_tree_refuses_deep_cyclic_and_unknown_provider(state, tmp_path):
    ctx = _ctx(state, tmp_path)
    d = AgentDef(id="loopy", name="Loopy", tier="flash")

    def build(**kw):
        base = dict(chat_provider="mock", root_model=None, max_tokens=None, ctx=ctx, turn=TurnTrace(), toolsets=[])
        base.update(kw)
        return asyncio.run(state.chat._build(d, **base))

    with pytest.raises(ValueError, match="nesting too deep or cyclic"):
        build(depth=MAX_DEPTH + 1)
    with pytest.raises(ValueError, match="nesting too deep or cyclic"):
        build(trail=("loopy",))
    with pytest.raises(KeyError):
        build(chat_provider="no-such-provider")


@pytest.mark.parametrize("max_tokens, expected", [(None, "high"), (3000, "medium"), (1500, "low"), (1000, None)])
def test_claude_thinking_level_steps_down_to_fit_under_max_tokens(state, tmp_path, max_tokens, expected):
    state.providers.update("anthropic", {"enabled": True, "api_key": "sk-ant-test-1234567"})
    state.db.set_setting("thinking_level", "high")
    d = AgentDef(id="thinker", name="Thinker", provider="anthropic", tier="custom", model="claude-sonnet-5-5")
    agent = asyncio.run(state.chat._build(d, chat_provider="mock", root_model=None, max_tokens=max_tokens,
                                          ctx=_ctx(state, tmp_path), turn=TurnTrace(), toolsets=[]))
    # the thinking budget (1024/2048/4096) must fit under the output cap; otherwise thinking is skipped for the call
    assert agent.model._additional_args.get("reasoning_effort") == expected


def test_other_reasoning_models_keep_the_requested_thinking_level(state, tmp_path):
    state.providers.update("openai", {"enabled": True, "api_key": "sk-test-1234567"})
    state.db.set_setting("thinking_level", "high")
    d = AgentDef(id="o_thinker", name="O", provider="openai", tier="custom", model="o3-mini")
    agent = asyncio.run(state.chat._build(d, chat_provider="mock", root_model=None, max_tokens=1000,
                                          ctx=_ctx(state, tmp_path), turn=TurnTrace(), toolsets=[]))
    assert agent.model._additional_args.get("reasoning_effort") == "high"


# ------------------------------------------------------------------ spawn_agent guard rails


def _spawn(state, tmp_path, chat_provider="mock", turn=None):
    d = AgentDef(id="boss", name="Boss", tier="flash", can_spawn=True)
    turn = turn or TurnTrace()
    return state.chat._spawn_tool(d, _ctx(state, tmp_path), turn, [], chat_provider, None, 0, (), ["list_dir"], []), turn


def test_spawn_refused_when_creating_agents_is_switched_off(state, tmp_path):
    spawn, turn = _spawn(state, tmp_path)
    state.db.set_setting("agent_spawning_enabled", "0")
    res = asyncio.run(spawn(name="x", instruction="do", task="t", tools=[], mcp=[], tier="flash", tool_context=None))
    assert res == {"ok": False, "error": "creating agents is disabled in Settings"} and turn.spawned == 0


def test_spawn_needs_an_instruction_and_a_task(state, tmp_path):
    spawn, turn = _spawn(state, tmp_path)
    res = asyncio.run(spawn(name="x", instruction="   ", task="t", tools=[], mcp=[], tier="flash", tool_context=None))
    assert res == {"ok": False, "error": "instruction and task are required"} and turn.spawned == 0
    res = asyncio.run(spawn(name="x", instruction="do", task="", tools=[], mcp=[], tier="flash", tool_context=None))
    assert res["ok"] is False and turn.spawned == 0


def test_spawn_reports_a_child_that_cannot_be_built(state, tmp_path):
    spawn, turn = _spawn(state, tmp_path, chat_provider="no-such-provider")
    res = asyncio.run(spawn(name="helper", instruction="do", task="t", tools=[], mcp=[], tier="flash", tool_context=None))
    assert res == {"ok": False, "error": "'no-such-provider'"}     # reported to the caller, not raised
    assert turn.spawned == 1                                        # the attempt still counted against the limit


# ------------------------------------------------------------------ external (ACP) agents


class _FakeSess:
    def __init__(self, sid):
        self.id = sid


def _external(state, tmp_path, turn=None):
    d = state.agents.get("engineer")
    turn = turn or TurnTrace()
    fn = state.chat._external_tool(d, _ctx(state, tmp_path), turn, [{"id": "claude", "label": "Claude"}])
    return fn, turn


def test_external_agent_call_refused_when_switched_off(state, tmp_path):
    fn, turn = _external(state, tmp_path)
    state.db.set_setting("agents_can_call_external", "0")
    res = asyncio.run(fn(agent="claude", task="do it", tool_context=None))
    assert res == {"ok": False, "error": "calling external agents is disabled in Settings"}
    assert turn.external_calls == 0


def test_external_agent_call_needs_a_task(state, tmp_path):
    fn, turn = _external(state, tmp_path)
    assert asyncio.run(fn(agent="claude", task="   ", tool_context=None)) == {"ok": False, "error": "task is required"}


def test_external_thoughts_are_streamed_and_kept_on_the_run(state, tmp_path, monkeypatch):
    events = []
    fn, turn = _external(state, tmp_path, TurnTrace(emit=events.append))

    async def fake_get(agent_id, session_id, workspace):
        return _FakeSess("s-new")

    async def fake_prompt(sess, task, on_event, policy="ask", timeout=None):
        on_event({"type": "thought", "text": "plan: "})
        on_event({"type": "thought", "text": "read first"})
        on_event({"type": "text", "text": "Done."})
        return {"stop_reason": "end_turn", "usage": None, "reported_cost": None}

    monkeypatch.setattr(state.acp, "get_or_open", fake_get, raising=False)
    monkeypatch.setattr(state.acp, "prompt", fake_prompt, raising=False)
    res = asyncio.run(fn(agent="claude", task="do it", tool_context=None))
    assert res["ok"] is True and res["report"] == "Done." and res["stop_reason"] == "end_turn"
    run = turn.runs[-1]
    assert run["agent"] == "claude" and run["external"] is True and run["status"] == "done"
    assert run["thoughts"] == ["plan: read first"]
    deltas = [e["delta"] for e in events if e["type"] == "agent_thought"]
    assert deltas == ["plan: ", "read first"]


def test_expired_external_session_is_replaced_by_a_fresh_one(state, tmp_path, monkeypatch):
    fn, turn = _external(state, tmp_path)
    key = (None, "engineer", "claude")
    state.chat._acp_sessions[key] = "old-1"
    opened = []

    async def fake_get(agent_id, session_id, workspace):
        opened.append(session_id)
        if session_id == "old-1":
            from app.acp_agents import AcpError
            raise AcpError("session expired")
        return _FakeSess("s-fresh")

    async def fake_prompt(sess, task, on_event, policy="ask", timeout=None):
        return {"stop_reason": "end_turn", "usage": None, "reported_cost": None}

    monkeypatch.setattr(state.acp, "get_or_open", fake_get, raising=False)
    monkeypatch.setattr(state.acp, "prompt", fake_prompt, raising=False)
    res = asyncio.run(fn(agent="claude", task="continue", tool_context=None))
    assert opened == ["old-1", None]                       # tried the old session, then started fresh
    assert res["ok"] is True and state.chat._acp_sessions[key] == "s-fresh"


def test_external_agent_that_cannot_start_is_reported_not_retried(state, tmp_path, monkeypatch):
    from app.acp_agents import AcpError
    fn, turn = _external(state, tmp_path)
    opened = []

    async def fake_get(agent_id, session_id, workspace):
        opened.append(session_id)
        raise AcpError("not installed")

    monkeypatch.setattr(state.acp, "get_or_open", fake_get, raising=False)
    res = asyncio.run(fn(agent="claude", task="do it", tool_context=None))
    assert res == {"ok": False, "error": "Claude failed: not installed"}
    assert opened == [None]                                # no previous session to fall back from: one attempt only


def test_external_agent_failure_is_reported_to_the_caller(state, tmp_path, monkeypatch):
    fn, turn = _external(state, tmp_path)

    async def fake_get(agent_id, session_id, workspace):
        return _FakeSess("s-1")

    async def fake_prompt(sess, task, on_event, policy="ask", timeout=None):
        raise RuntimeError("bridge crashed")

    monkeypatch.setattr(state.acp, "get_or_open", fake_get, raising=False)
    monkeypatch.setattr(state.acp, "prompt", fake_prompt, raising=False)
    res = asyncio.run(fn(agent="claude", task="do it", tool_context=None))
    assert res == {"ok": False, "error": "Claude failed: bridge crashed"}
    assert turn.runs[-1]["status"] == "error" and "bridge crashed" in turn.runs[-1]["result"]


# ------------------------------------------------------------------ session repair and rebuilds


def test_interrupted_tool_calls_are_closed_before_the_model_resumes(state):
    async def scenario():
        sess_svc = state.chat.sessions
        await sess_svc.create_session(app_name="agent_studio", user_id="local", session_id="rep-1")
        sess = await sess_svc.get_session(app_name="agent_studio", user_id="local", session_id="rep-1")
        call = types.Part(function_call=types.FunctionCall(id="c1", name="read_file", args={"path": "a"}))
        await sess_svc.append_event(sess, Event(invocation_id="i1", author="engineer", content=types.Content(role="model", parts=[call])))
        await state.chat._repair_session("rep-1", "engineer")
        sess = await sess_svc.get_session(app_name="agent_studio", user_id="local", session_id="rep-1")
        last = sess.events[-1]
        resp = last.content.parts[0].function_response
        assert last.invocation_id == "steer" and resp.id == "c1" and resp.name == "read_file"
        assert "Interrupted" in resp.response["error"]

        # a second repair does nothing: the last tool exchange is now complete
        await state.chat._repair_session("rep-1", "engineer")
        sess = await sess_svc.get_session(app_name="agent_studio", user_id="local", session_id="rep-1")
        assert len(sess.events) == 2
    asyncio.run(scenario())


def test_repair_is_a_no_op_without_pending_calls(state):
    async def scenario():
        svc = state.chat.sessions
        await svc.create_session(app_name="agent_studio", user_id="local", session_id="rep-empty")
        await state.chat._repair_session("rep-empty", "engineer")          # empty history
        await state.chat._repair_session("no-such-session", "engineer")    # unknown session
        sess = await svc.get_session(app_name="agent_studio", user_id="local", session_id="rep-empty")
        assert sess.events == []
        await svc.append_event(sess, Event(invocation_id="i", author="user",
                                           content=types.Content(role="user", parts=[types.Part(text="just words")])))
        await state.chat._repair_session("rep-empty", "engineer")          # text only: nothing was called
        sess = await svc.get_session(app_name="agent_studio", user_id="local", session_id="rep-empty")
        assert len(sess.events) == 1
    asyncio.run(scenario())


def test_context_info_for_a_provider_that_is_not_configured(state):
    info = state.chat.context_info("no-such-chat", "gemini", "gemini-2.5-flash")
    assert info["used_tokens"] == CX.usage_for([])["used_tokens"]     # the fixed baseline for an empty chat
    assert info["messages"] == 0 and info["summarized"] is False and info["messages_after_summary"] == 0
    assert info["model"] == "gemini-2.5-flash"


def test_summarize_needs_the_chats_provider_to_exist(state):
    state.chat.store.ensure("sum-ghost", "ghost", None, None, None, "hello there")
    state.chat.store.add("sum-ghost", "user", "hello there")
    with pytest.raises(KeyError):
        asyncio.run(state.chat.summarize("sum-ghost"))


def test_summary_is_written_by_a_real_provider_call_and_budgeted(state, monkeypatch):
    state.providers.update("openai", {"enabled": True, "api_key": "sk-test-1234567", "cap_usd": 10})
    state.chat.store.ensure("sum-oa", "openai", None, "gpt-5-mini", None, "hello")
    state.chat.store.add("sum-oa", "user", "hello")
    state.chat.store.add("sum-oa", "bot", "hi there")
    seen = []

    async def fake_completion(**kw):
        seen.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=" Short summary. "))],
                               usage=SimpleNamespace(prompt_tokens=120, completion_tokens=12))

    monkeypatch.setattr(litellm, "acompletion", fake_completion)
    res = asyncio.run(state.chat.summarize("sum-oa"))
    assert res["text"] == "Short summary." and res["covered_messages"] == 2 and res["session_resumed"] is False
    assert seen[0]["model"] == "openai/gpt-5-mini" and "api_key" in seen[0]
    assert res["cost_usd"] > 0 and state.chat.store.messages("sum-oa")[-1]["role"] == "summary"
    assert state.guard.reserved("openai") == 0                       # the reservation was settled, not left open


def test_failed_summary_call_releases_its_reservation(state, monkeypatch):
    state.providers.update("openai", {"enabled": True, "api_key": "sk-test-1234567", "cap_usd": 10})
    state.chat.store.ensure("sum-err", "openai", None, "gpt-5-mini", None, "hello")
    state.chat.store.add("sum-err", "user", "hello")

    async def down(**kw):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(litellm, "acompletion", down)
    with pytest.raises(RuntimeError, match="upstream unavailable"):
        asyncio.run(state.chat.summarize("sum-err"))
    assert state.guard.reserved("openai") == 0 and state.guard.spent(state.providers.get("openai")) == 0
    assert [m["role"] for m in state.chat.store.messages("sum-err")] == ["user"]   # nothing was saved as a summary


def test_model_fallback_chain_skips_bad_entries(state):
    state.db.set_setting("model_fallbacks", "{not json")
    assert state.chat._model_chain("mock", None) == [("mock", None)]
    state.db.set_setting("model_fallbacks", '{"provider": "mock"}')          # not a list
    assert state.chat._model_chain("mock", None) == [("mock", None)]
    state.db.set_setting("model_fallbacks", json.dumps([
        None,                                                # empty entry
        {"provider": "mock"},                                # repeats the first choice
        {"provider": "mock", "model": ""},                   # the same again, empty model name
        {"provider": "ghost", "model": "x"},                 # unknown provider
        {"provider": "ollama", "model": "llama3.2"},         # disabled provider
    ]))
    assert state.chat._model_chain("mock", None) == [("mock", None)]
    state.providers.update("ollama", {"enabled": True})
    assert state.chat._model_chain("mock", None) == [("mock", None), ("ollama", "llama3.2")]


def test_unknown_agent_is_refused_before_any_work(state):
    with pytest.raises(KeyError):
        asyncio.run(state.chat.send("mock", None, "hi", None, agent_id="no-such-agent"))


# ------------------------------------------------------------------ whole-turn behaviour (streaming, steering, failures)


def _turn_state(state):
    state.providers.update("mock", {"cap_usd": 1000})


def test_thinking_is_streamed_and_kept_with_the_answer(state, monkeypatch):
    _turn_state(state)

    def thinking_then_answer(agent):
        async def step(agent_):
            yield _ev(agent_, "pondering", thought=True)                         # delivered whole, not streamed
            yield _ev(agent_, "more", partial=True, thought=True)                # streamed thought
            yield _ev(agent_, "Hello", partial=True)                             # streamed answer
            yield _ev(agent_, "Hello")
        return step(agent)

    _use_steps(monkeypatch, thinking_then_answer)
    events = []
    res = asyncio.run(state.chat.send("mock", None, "think first", None, emit=events.append))
    assert res["reply"] == "Hello" and res["thinking"] == "ponderingmore"
    deltas = [e["delta"] for e in events if e["type"] == "thinking"]
    assert deltas == ["pondering", "more"]
    assert [e["delta"] for e in events if e["type"] == "text"] == ["Hello"]


def test_steering_keeps_the_partial_answer_and_resumes_with_the_note(state, monkeypatch):
    _turn_state(state)
    box = {}
    _use_steps(monkeypatch, _steer_after_reserve(box), _answer("answer after steer"))
    events = []
    inbox = NoteInbox(events.append)
    box["inbox"] = inbox
    res = asyncio.run(state.chat.send("mock", None, "long task", "steer-1", emit=events.append, inbox=inbox))
    assert res["reply"] == "answer after steer"
    steered = [e for e in events if e["type"] == "steered"]
    assert steered == [{"type": "steered", "partial_chars": 4, "notes": 1}]
    msgs = state.chat.store.messages("steer-1")
    interrupted = [m for m in msgs if m["role"] == "bot" and m["meta"].get("status") == "interrupted"]
    assert interrupted and interrupted[0]["text"] == "half"
    assert state.guard.reserved("mock") == 0                  # the cut-off call was settled, nothing left reserved
    assert state.guard.spent(state.providers.get("mock")) > 0  # ...and charged at its worst case


def test_cancelled_turn_charges_the_open_call_at_worst_case(state, monkeypatch):
    _turn_state(state)
    _use_steps(monkeypatch, _hanging_after_reserve(agent_partial="partly"))

    async def main():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(state.chat.send("mock", None, "hang", "cx-1"), timeout=0.5)

    asyncio.run(main())
    assert state.guard.reserved("mock") == 0
    assert state.guard.spent(state.providers.get("mock")) > 0            # provider usage unknown: charged worst case
    bot = state.chat.store.messages("cx-1")[-1]
    assert bot["role"] == "bot" and bot["meta"]["status"] == "cancelled"


def test_failed_turn_releases_its_reservation(state, monkeypatch):
    _turn_state(state)
    _use_steps(monkeypatch, _reserving_then_raising(RuntimeError("provider exploded")))
    with pytest.raises(RuntimeError, match="provider exploded"):
        asyncio.run(state.chat.send("mock", None, "boom", "err-1"))
    assert state.guard.reserved("mock") == 0
    assert state.guard.spent(state.providers.get("mock")) == 0            # nothing was billed
    assert state.chat.store.messages("err-1")[-1]["meta"]["status"] == "error"


def test_rejected_reasoning_history_is_rebuilt_and_retried(state, monkeypatch):
    _turn_state(state)
    _use_steps(monkeypatch, _answer("first answer"),
               _raising(RuntimeError("invalid thinking signature from another conversation")),
               _answer("second answer"))

    async def main():
        await state.chat.send("mock", None, "first question", "rb-1")
        res = await state.chat.send("mock", None, "second question", "rb-1")
        return res, await state.chat.sessions.get_session(app_name="agent_studio", user_id="local", session_id="rb-1")

    res, sess = asyncio.run(main())
    assert res["reply"] == "second answer"
    # the session was rebuilt from the saved text: only the earlier exchange, as plain text
    assert [e.content.parts[0].text for e in sess.events] == ["first question", "first answer"]


def test_mcp_toolsets_that_fail_to_close_do_not_fail_the_turn(state, monkeypatch):
    _turn_state(state)

    class _BadClose(BaseToolset):
        def __init__(self):
            super().__init__()

        async def get_tools(self, readonly_context=None):
            return []

        async def close(self):
            raise RuntimeError("close failed")

    async def fake_build(store, ids, workspace, warnings, *identity):
        return [_BadClose()]

    monkeypatch.setattr(chat_mod, "build_toolsets", fake_build)
    _use_steps(monkeypatch, _answer("ok"))
    state.agents.create(AgentDef(id="mcp_agent", name="MCP agent", tier="flash", mcp=["fetch"]))
    res = asyncio.run(state.chat.send("mock", None, "hi", None, agent_id="mcp_agent"))
    assert res["reply"] == "ok"
