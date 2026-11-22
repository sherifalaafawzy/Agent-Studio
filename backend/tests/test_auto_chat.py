"""Auto mode inside a chat turn: the route is chosen, streamed, recorded with the answer, and a failed model falls back once.

The offline mock provider answers (no network); its two models ("mock-cheap" flash, "mock-expensive" pro) make the choice visible.
"""
import asyncio
import json

import pytest

from app.agents import AgentDef
from app.auto import AUTO_ID, AutoError


def run(c):
    return asyncio.run(c)


def events_of(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def send(state, message="hello", sid=None, agent=None, emit=None):
    return run(state.chat.send(AUTO_ID, None, message, sid, None, agent, None, emit=emit))


def test_an_auto_turn_chooses_streams_and_records_its_route(state, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    state.providers.update("mock", {"cap_usd": 100.0})
    events = []
    res = send(state, "hello", emit=events.append)
    route = next(e for e in events if e["type"] == "route")
    assert (route["provider"], route["model"], route["tier"], route["engine"]) == ("mock", "mock-cheap", "flash", "rules")
    assert route["reason"].startswith("built-in rules read this as simple") and route["alternates"][0]["model"] == "mock-expensive"
    assert events.index(route) < next(i for i, e in enumerate(events) if e["type"] == "session")      # announced before the work starts
    assert res["provider"] == "mock" and res["route"]["model"] == "mock-cheap" and res["model_calls"][0]["model"] == "mock-cheap"
    assert "You said: hello" in res["reply"]
    sess = state.chat.store.get(res["session_id"])
    assert sess["provider"] == "mock" and sess["model"] == "mock-cheap"                             # the saved chat holds the REAL provider
    bot = state.chat.store.messages(res["session_id"])[-1]
    assert bot["role"] == "bot" and bot["meta"]["route"]["model"] == "mock-cheap" and bot["meta"]["route"]["engine"] == "rules"
    assert res["runs"][0]["route"]["reason"] == route["reason"]                                      # part of the run record too


def test_each_message_in_a_chat_is_routed_on_its_own_and_resuming_keeps_working(state, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    state.providers.update("mock", {"cap_usd": 100.0})                                               # the mock "pro" model is priced to trip caps
    first = send(state, "hello")
    second = send(state, "Analyze the trade-offs step by step and prove which approach is better.", first["session_id"])
    assert first["route"]["tier"] == "flash" and second["route"]["tier"] == "pro" and second["model_calls"][0]["model"] == "mock-expensive"
    assert second["session_id"] == first["session_id"]
    state.chat.sessions = type(state.chat.sessions)()                                                # a restart: the live session is gone
    third = send(state, "and again", first["session_id"])
    assert third["route"]["tier"] == "flash" and "You said: and again" in third["reply"]
    assert [m["role"] for m in state.chat.store.messages(first["session_id"])] == ["user", "bot"] * 3


def test_earlier_messages_are_given_to_the_router_as_context(state, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    seen = []
    real = state.auto.route

    async def spy(message, context=None, agent=False, has_images=False, max_tokens=None, only_provider=None, override=None, history_chars=0):
        seen.append((message, list(context or []), agent, only_provider, history_chars))
        return await real(message, context, agent, has_images, max_tokens, only_provider, override, history_chars)
    state.auto.route = spy
    first = send(state, "my first question")
    send(state, "follow up", first["session_id"])
    assert seen[0] == ("my first question", [], False, None, 0)
    assert seen[1][0] == "follow up" and seen[1][1][0] == "my first question" and "You said: my first question" in seen[1][1][1]
    assert seen[1][4] == sum(len(t) for t in seen[1][1])                                           # the whole chat so far counts towards cost


def test_a_failed_model_falls_back_once_to_the_next_best_and_says_so(state, tmp_path, monkeypatch):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    state.providers.update("mock", {"cap_usd": 100.0})
    real = state.chat._send_attempt
    tried = []

    async def flaky(pid, model, *a, **k):
        tried.append((pid, model))
        if model == "mock-cheap":
            raise RuntimeError("503 service unavailable")
        return await real(pid, model, *a, **k)
    monkeypatch.setattr(state.chat, "_send_attempt", flaky)
    events = []
    res = send(state, "hello", emit=events.append)
    assert tried == [("mock", "mock-cheap"), ("mock", "mock-expensive")] and res["route"]["model"] == "mock-cheap"
    fb = next(e for e in events if e["type"] == "fallback")
    assert fb["from"] == {"provider": "mock", "model": "mock-cheap"} and fb["to"] == {"provider": "mock", "model": "mock-expensive"}
    assert fb["code"] == "unavailable" and res["model_calls"][0]["model"] == "mock-expensive"

    state.auto.set_config({"fallback": False})                                                       # the setting turns it off
    tried.clear()
    with pytest.raises(RuntimeError, match="503"):
        send(state, "hello again")
    assert tried == [("mock", "mock-cheap")]


def test_a_pinned_agent_provider_limits_the_choice_and_agents_count_as_tool_users(state, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    state.providers.update("mock", {"cap_usd": 100.0})
    state.agents.create(AgentDef(id="pinned", name="Pinned", tier="flash", provider="mock", tools=[]))
    seen = []
    real = state.auto.route

    async def spy(message, context=None, agent=False, has_images=False, max_tokens=None, only_provider=None, override=None, history_chars=0):
        seen.append((agent, only_provider))
        return await real(message, context, agent, has_images, max_tokens, only_provider, override, history_chars)
    state.auto.route = spy
    res = send(state, "hello", agent="pinned")
    state.agents.create(AgentDef(id="free", name="Free", tier="flash", tools=[]))
    send(state, "hello", agent="free")
    assert seen == [(True, "mock"), (True, None)] and res["agent"] == "pinned" and res["route"]["signals"]["tools"] is True


def test_when_auto_cannot_route_the_turn_stops_before_anything_is_saved(state, tmp_path):
    state.providers.update("mock", {"enabled": False})
    with pytest.raises(AutoError, match="Auto could not find a model"):
        send(state, "hello", sid="s-none")
    assert not state.chat.store.exists("s-none")
    state.chat.auto = None
    with pytest.raises(ValueError, match="Auto mode is not available"):
        send(state, "hello")


def test_http_chat_reports_an_unroutable_message(client, state):
    state.providers.update("mock", {"enabled": False})
    r = client.post("/api/chat", json={"provider": "auto", "message": "hi"})
    assert r.status_code == 422 and "Auto could not find a model" in r.json()["detail"]
    evs = events_of(client.post("/api/chat/stream", json={"provider": "auto", "message": "hi"}).text)
    err = next(e for e in evs if e["type"] == "error")
    assert err["title"] == "Auto could not choose a model" and err["code"] == "auto_unavailable" and "Auto could not find a model" in err["message"]
    assert err["retryable"] is False and evs[-1]["type"] == "end"


def test_http_stream_carries_the_route_event_and_the_answer(client, state, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    state.providers.update("mock", {"cap_usd": 100.0})
    evs = events_of(client.post("/api/chat/stream", json={"provider": "auto", "message": "hello"}).text)
    route = next(e for e in evs if e["type"] == "route")
    done = next(e for e in evs if e["type"] == "done")
    assert route["model"] == "mock-cheap" and done["route"]["model"] == "mock-cheap" and "You said: hello" in done["reply"]
