"""Agents survive model errors: fallback models, "try another model" suggestions, and handoff between agents."""
import asyncio
import json

import litellm
import pytest
from google.adk.events import Event
from google.genai import types

import app.chat as chat_mod


class FailFirstRunner:
    """The first model used fails with a rate limit; later attempts answer."""
    seen: list[str] = []
    fail_models: set[str] = set()

    def __init__(self, **kw):
        self.agent = kw["agent"]

    async def run_async(self, *, user_id, session_id, new_message, run_config):
        FailFirstRunner.seen.append(new_message.parts[0].text)
        name = getattr(self.agent.model, "model", "") or ""
        if any(name.endswith(f) for f in FailFirstRunner.fail_models):
            raise litellm.RateLimitError("429 RESOURCE_EXHAUSTED", model="x", llm_provider="vertex_ai")
        yield Event(invocation_id="i", author=self.agent.name, content=types.Content(role="model", parts=[types.Part(text="answered")]))


def test_a_failing_model_falls_back_to_the_next_configured_one(state, monkeypatch, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    state.db.set_setting("model_fallbacks", json.dumps([{"provider": "mock", "model": "mock-cheap"}]))
    monkeypatch.setattr(chat_mod, "Runner", FailFirstRunner)
    FailFirstRunner.seen, FailFirstRunner.fail_models = [], set()
    events = []
    # first attempt: a provider that fails. Use a mock provider whose model name we make fail.
    FailFirstRunner.fail_models = {"mock-expensive"}
    res = asyncio.run(state.chat.send("mock", "mock-expensive", "Summarise the repo", None, emit=events.append))
    assert res["reply"] == "answered"
    fb = next(e for e in events if e["type"] == "fallback")
    assert fb["from"] == {"provider": "mock", "model": "mock-expensive"} and fb["to"]["model"] == "mock-cheap"
    assert fb["code"] == "rate_limit"
    assert "previous model" in FailFirstRunner.seen[1] and "Summarise the repo" in FailFirstRunner.seen[1]
    msgs = state.chat.store.messages(res["session_id"])
    assert [m["role"] for m in msgs] == ["user", "bot"] and msgs[0]["text"] == "Summarise the repo"   # saved once, not twice


def test_with_no_fallback_the_error_suggests_other_models(state, monkeypatch, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    state.providers.update("ollama", {"enabled": True})            # another enabled model to suggest
    monkeypatch.setattr(chat_mod, "Runner", FailFirstRunner)
    FailFirstRunner.seen, FailFirstRunner.fail_models = [], {"mock-expensive"}
    with pytest.raises(litellm.RateLimitError) as ei:
        asyncio.run(state.chat.send("mock", "mock-expensive", "hi", None, emit=lambda e: None))
    assert [s["provider"] for s in ei.value.suggestions] == ["ollama"] and ei.value.suggestions[0]["model"]
    sid = state.chat.store.list(1)[0]["id"]
    bot = state.chat.store.messages(sid)[-1]
    assert bot["meta"]["status"] == "error" and bot["meta"]["error"]["suggestions"] == ei.value.suggestions
    assert bot["meta"]["error"]["code"] == "rate_limit"


def test_handoff_between_agents_is_recorded_and_explained(state, monkeypatch, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    monkeypatch.setattr(chat_mod, "Runner", FailFirstRunner)
    FailFirstRunner.seen, FailFirstRunner.fail_models = [], set()
    r1 = asyncio.run(state.chat.send("mock", None, "first, explore", None, agent_id="explorer", emit=lambda e: None))
    r2 = asyncio.run(state.chat.send("mock", None, "now implement it", r1["session_id"], agent_id="coder", emit=lambda e: None))
    msgs = state.chat.store.messages(r1["session_id"])
    assert [m["role"] for m in msgs] == ["user", "bot", "user", "handoff", "bot"]
    assert msgs[3]["text"] == "Handed off to Coder"
    assert "Handoff" in FailFirstRunner.seen[-1] and "now implement it" in FailFirstRunner.seen[-1]
