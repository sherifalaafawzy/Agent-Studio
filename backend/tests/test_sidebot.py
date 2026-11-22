"""The side agent (AG-UI): event stream, message/tool conversion, settings, and the endpoint."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from ag_ui.core import RunAgentInput

from app import sidebot


def _run(**over):
    base = {"threadId": "t1", "runId": "r1", "state": {}, "messages": [{"id": "m1", "role": "user", "content": "hi"}],
            "tools": [], "context": [], "forwardedProps": {}}
    base.update(over)
    return RunAgentInput.model_validate(base)


def _chunk(content=None, tool_calls=None):
    return {"choices": [{"delta": {"content": content, "tool_calls": tool_calls}}]}


def _fake(chunks=None, exc=None):
    seen = {}

    async def completion(**kw):
        seen.update(kw)
        if exc:
            raise exc

        async def gen():
            for c in chunks or []:
                yield c
        return gen()
    completion.seen = seen
    return completion


def _collect(agen):
    async def go():
        return [e async for e in agen]
    return asyncio.run(go())


def test_messages_and_tools_convert_to_openai_shape():
    msgs = sidebot.to_litellm_messages([
        NS(role="user", content="go"),
        NS(role="assistant", content="", tool_calls=[NS(id="c1", function=NS(name="navigate", arguments='{"to":"/x"}'))]),
        NS(role="tool", content="done", tool_call_id="c1"),
        NS(role="developer", content="ignored"),
        NS(role="assistant", content="plain"),
    ])
    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "user", "content": "go"}
    assert msgs[2]["tool_calls"][0]["function"] == {"name": "navigate", "arguments": '{"to":"/x"}'}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "done"}
    assert msgs[4] == {"role": "assistant", "content": "plain"}      # unknown roles are dropped, no tool_calls key
    assert sidebot.to_litellm_messages(None)[0]["role"] == "system"


def test_declared_tools_become_function_tools():
    assert sidebot.to_openai_tools([]) is None
    tools = sidebot.to_openai_tools([NS(name="navigate", description="go", parameters=None),
                                     NS(name="highlight", description="", parameters={"type": "object", "properties": {"sel": {}}})])
    assert tools[0]["function"]["parameters"] == {"type": "object", "properties": {}}
    assert tools[1]["function"]["parameters"]["properties"] == {"sel": {}}


def test_text_streams_as_message_events_then_finishes():
    comp = _fake([_chunk(content="Hel"), _chunk(content="lo"), _chunk(content=None), {"choices": []}, {"choices": [{"delta": None}]}])
    evs = _collect(sidebot.stream_events(_run(), "openai/x", {"api_key": "k"}, comp))
    types = [e.type.value if hasattr(e.type, "value") else e.type for e in evs]
    assert types == ["RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "RUN_FINISHED"]
    assert "".join(e.delta for e in evs if getattr(e, "delta", None)) == "Hello"
    assert comp.seen["model"] == "openai/x" and comp.seen["stream"] is True and comp.seen["api_key"] == "k"


def test_tool_calls_are_assembled_from_fragments():
    chunks = [
        _chunk(content="Going there."),
        _chunk(tool_calls=[{"index": 0, "id": "call_1", "function": {"name": "navigate", "arguments": '{"to":'}}]),
        _chunk(tool_calls=[{"index": 0, "function": {"arguments": '"/mcp/"}'}}]),
        _chunk(tool_calls=[{"index": 1, "id": "", "function": {"name": "start_tour"}}]),
    ]
    evs = _collect(sidebot.stream_events(_run(tools=[{"name": "navigate", "description": "", "parameters": None}]), "m", {}, _fake(chunks)))
    starts = [e for e in evs if e.type.value == "TOOL_CALL_START"]
    args = [e.delta for e in evs if e.type.value == "TOOL_CALL_ARGS"]
    assert [s.tool_call_name for s in starts] == ["navigate", "start_tour"]
    assert starts[0].tool_call_id == "call_1" and starts[0].parent_message_id is not None
    assert args == ['{"to":"/mcp/"}', "{}"]                        # no args at all -> empty object
    assert starts[1].tool_call_id and starts[1].parent_message_id is not None   # generated id, same message
    assert evs[-1].type.value == "RUN_FINISHED"


def test_tool_only_turn_has_no_parent_message():
    chunks = [_chunk(tool_calls=[{"index": 0, "id": "c", "function": {"name": "n", "arguments": "{}"}}])]
    evs = _collect(sidebot.stream_events(_run(), "m", {}, _fake(chunks)))
    assert [e.type.value for e in evs] == ["RUN_STARTED", "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "RUN_FINISHED"]
    assert evs[1].parent_message_id is None


def test_object_chunks_work_like_dicts():
    """LiteLLM returns objects, not dicts - the same events come out."""
    delta = NS(content="yo", tool_calls=None)
    chunk = NS(choices=[NS(delta=delta)])

    async def completion(**kw):
        async def gen():
            yield chunk
        return gen()
    evs = _collect(sidebot.stream_events(_run(), "m", {}, completion))
    assert [e.type.value for e in evs][:3] == ["RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT"]


def test_model_failure_becomes_run_error():
    evs = _collect(sidebot.stream_events(_run(), "m", {}, _fake(exc=RuntimeError("boom"))))
    assert [e.type.value for e in evs] == ["RUN_STARTED", "RUN_ERROR"]
    assert evs[1].message and evs[1].code


def test_encode_produces_sse_frames():
    async def src():
        yield sidebot.RunStartedEvent(thread_id="t", run_id="r")

    async def collect():
        return [f async for f in sidebot.encode(src())]
    frames = asyncio.run(collect())
    assert frames[0].startswith("data: ") and frames[0].endswith("\n\n")
    assert json.loads(frames[0][6:])["type"] == "RUN_STARTED"


def test_parse_run_input_rejects_bad_bodies():
    with pytest.raises(ValueError, match="JSON object"):
        sidebot.parse_run_input(["not", "an", "object"])
    with pytest.raises(ValueError, match="valid AG-UI run"):
        sidebot.parse_run_input({"threadId": "t"})          # no runId
    assert sidebot.parse_run_input({"threadId": "t", "runId": "r", "messages": []}).thread_id == "t"


# ---- settings and the endpoint -------------------------------------------------

def test_settings_round_trip_and_validation(client):
    s = client.get("/api/settings").json()
    assert s["sidebot_enabled"] is True and s["sidebot_resolved"]["provider"] is None     # no real provider yet
    r = client.put("/api/settings", json={"sidebot_enabled": False, "sidebot_model": " m1 "})
    assert r.status_code == 200 and r.json()["sidebot_enabled"] is False and r.json()["sidebot_model"] == "m1"
    assert client.put("/api/settings", json={"sidebot_provider": "nope"}).status_code == 422
    client.put("/api/settings", json={"sidebot_provider": "mock", "sidebot_enabled": True})
    assert client.get("/api/settings").json()["sidebot_resolved"]["provider"] == "mock"


def test_endpoint_refuses_bad_or_disabled_requests(client):
    assert client.post("/api/sidebot", json={"threadId": "t"}).status_code == 422
    client.put("/api/settings", json={"sidebot_enabled": False})
    assert client.post("/api/sidebot", json=_run_json()).status_code == 409
    client.put("/api/settings", json={"sidebot_enabled": True})
    assert client.post("/api/sidebot", json=_run_json()).status_code == 422       # no provider configured


def test_endpoint_streams_ag_ui_events(client, monkeypatch):
    import litellm
    client.put("/api/settings", json={"sidebot_provider": "mock", "sidebot_model": "demo"})
    monkeypatch.setattr(litellm, "acompletion", _fake([_chunk(content="Hi there")]))
    r = client.post("/api/sidebot", json=_run_json())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    frames = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert [f["type"] for f in frames] == ["RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "RUN_FINISHED"]
    assert frames[2]["delta"] == "Hi there"


def test_endpoint_reports_model_errors_as_run_error(client, monkeypatch):
    import litellm
    client.put("/api/settings", json={"sidebot_provider": "mock"})
    monkeypatch.setattr(litellm, "acompletion", _fake(exc=RuntimeError("rate limit exceeded")))
    frames = [json.loads(line[6:]) for line in client.post("/api/sidebot", json=_run_json()).text.splitlines() if line.startswith("data: ")]
    assert frames[-1]["type"] == "RUN_ERROR"


def _run_json():
    return {"threadId": "t1", "runId": "r1", "messages": [{"id": "m1", "role": "user", "content": "hi"}], "tools": [], "context": [], "state": {}, "forwardedProps": {}}


# ---- guidance and switched-off tools (Settings -> Assistant) ------------------------------------

def test_the_safety_rules_always_come_first_and_guidance_is_only_added():
    assert sidebot.SYSTEM_PROMPT == sidebot.compose_prompt(None) and sidebot.compose_prompt("  ").endswith(sidebot.DEFAULT_GUIDANCE)
    custom = sidebot.compose_prompt("Always answer in French.")
    assert custom.startswith(sidebot.CORE_RULES) and custom.endswith("Always answer in French.") and sidebot.DEFAULT_GUIDANCE not in custom
    assert "never override them" in custom and "Never ask the user to paste API keys" in sidebot.CORE_RULES
    msgs = sidebot.to_litellm_messages([NS(role="user", content="hi")], custom)
    assert msgs[0] == {"role": "system", "content": custom}


def test_switched_off_tools_are_not_offered_and_are_refused_if_called():
    tools = [NS(name="navigate", description="", parameters=None), NS(name="explain", description="", parameters=None)]
    assert [t["function"]["name"] for t in sidebot.to_openai_tools(tools, frozenset({"navigate"}))] == ["explain"]
    assert sidebot.to_openai_tools(tools, frozenset({"navigate", "explain"})) is None
    chunks = [_chunk(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "navigate", "arguments": '{"page":"/"}'}}]),
              _chunk(tool_calls=[{"index": 1, "id": "c2", "function": {"name": "explain", "arguments": "{}"}}])]
    comp = _fake(chunks)
    evs = _collect(sidebot.stream_events(_run(tools=[{"name": "navigate", "description": "", "parameters": None}]), "m", {}, comp, system="SYS", off=frozenset({"navigate"})))
    kinds = [e.type.value for e in evs]
    assert kinds.count("TOOL_CALL_START") == 1 and evs[[e.type.value for e in evs].index("TOOL_CALL_START")].tool_call_name == "explain"
    refusal = "".join(getattr(e, "delta", "") for e in evs if e.type.value == "TEXT_MESSAGE_CONTENT")
    assert "switched off in Settings (navigate)" in refusal
    assert comp.seen["messages"][0]["content"] == "SYS" and comp.seen["tools"] is None          # the model was never told about the tool


def test_settings_carry_guidance_and_tool_switches(client):
    s = client.get("/api/settings").json()
    assert s["sidebot_guidance"] == "" and s["sidebot_prompt"]["effective"] == sidebot.SYSTEM_PROMPT and s["sidebot_prompt"]["core"] == sidebot.CORE_RULES
    assert [t["name"] for t in s["sidebot_tools"]] == list(sidebot.SIDEBOT_TOOLS) and all(t["enabled"] for t in s["sidebot_tools"])
    r = client.put("/api/settings", json={"sidebot_guidance": "  Be brief.  ", "sidebot_tools_off": ["explain", "explain"]}).json()
    assert r["sidebot_guidance"] == "Be brief." and r["sidebot_prompt"]["effective"].endswith("Be brief.")
    assert {t["name"]: t["enabled"] for t in r["sidebot_tools"]} == {"navigate": True, "start_tour": True, "explain": False}
    assert client.put("/api/settings", json={"sidebot_tools_off": ["rm_rf"]}).status_code == 422
    assert client.put("/api/settings", json={"sidebot_guidance": "x" * 4001}).status_code == 422
    assert client.put("/api/settings", json={"sidebot_guidance": ""}).json()["sidebot_prompt"]["effective"] == sidebot.SYSTEM_PROMPT        # empty = back to the default
    client.app.state.s.db.set_setting("sidebot_tools_off", "{not json")
    assert all(t["enabled"] for t in client.get("/api/settings").json()["sidebot_tools"])                                                    # a damaged value switches nothing off


def test_endpoint_sends_the_users_guidance_and_hides_switched_off_tools(client, monkeypatch):
    import litellm
    client.put("/api/settings", json={"sidebot_provider": "mock", "sidebot_model": "demo", "sidebot_guidance": "Speak like a pirate.", "sidebot_tools_off": ["navigate"]})
    comp = _fake([_chunk(content="Arr")])
    monkeypatch.setattr(litellm, "acompletion", comp)
    body = _run_json() | {"tools": [{"name": "navigate", "description": "go", "parameters": {"type": "object", "properties": {}}},
                                    {"name": "explain", "description": "x", "parameters": {"type": "object", "properties": {}}}]}
    assert client.post("/api/sidebot", json=body).status_code == 200
    assert comp.seen["messages"][0]["content"].endswith("Speak like a pirate.") and comp.seen["messages"][0]["content"].startswith(sidebot.CORE_RULES)
    assert [t["function"]["name"] for t in comp.seen["tools"]] == ["explain"]
