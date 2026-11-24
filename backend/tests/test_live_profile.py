"""An agent's Live profile: validation, storage with the agent, the badge, and how it overrides the Live settings."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import live_profile as LP
from app.agents import AgentDef


def test_no_profile_is_an_empty_object_and_a_full_one_is_checked():
    assert LP.normalise(None) == {} and LP.normalise({}) == {}
    p = LP.normalise({"enabled": True, "engine": "gemini", "mode": "avatar", "avatar_name": "Ben", "voice": "Aoede", "language": "de-DE", "budget_usd": 2})
    assert p["enabled"] is True and p["engine"] == "gemini" and p["budget_usd"] == 2.0 and p["agent_turns"] is False and p["provider"] == ""
    local = LP.normalise({"enabled": True})
    assert local["engine"] == "local" and local["mode"] == "voice"


@pytest.mark.parametrize("bad,msg", [
    ("x", "must be an object"), ({"colour": 1}, "unknown Live setting"), ({"enabled": "yes"}, "live.enabled must be true"), ({"agent_turns": 1}, "live.agent_turns"),
    ({"engine": "cloud"}, "live.engine"), ({"mode": "video"}, "live.mode"), ({"voice": 5}, "live.voice must be text"), ({"model": "x" * 200}, "at most 120"),
    ({"budget_usd": -1}, "live.budget_usd"), ({"budget_usd": True}, "live.budget_usd"), ({"budget_usd": "5"}, "live.budget_usd"),
    ({"engine": "gemini", "voice": "Nobody"}, "30 prebuilt voices"), ({"engine": "gemini", "language": "xx-XX"}, "supports"), ({"engine": "gemini", "mode": "avatar"}, "avatar name"),
])
def test_bad_profiles_are_refused_with_a_plain_message(bad, msg):
    with pytest.raises(ValueError, match=msg):
        LP.normalise(bad)


def test_the_stored_profile_is_read_tolerantly_and_gets_a_badge():
    assert LP.profile("garbage") == {} and LP.profile({"engine": "nope"}) == {}
    assert LP.badge(SimpleNamespace(live={})) is None and LP.badge(SimpleNamespace()) is None
    assert LP.badge(SimpleNamespace(live={"enabled": False})) == "Live off"
    assert LP.badge(SimpleNamespace(live={"enabled": True, "engine": "gemini", "mode": "avatar", "avatar_name": "Ben"})) == "Live · Gemini · avatar"
    assert LP.badge(SimpleNamespace(live={"enabled": True})) == "Live · local · voice"
    assert LP.of(SimpleNamespace(live={"enabled": True}))["engine"] == "local"


SETTINGS = {"live_engine": "local", "live_avatar": False, "live_voice": "af_heart", "live_provider": "ollama", "live_model": "qwen3:8b", "live_gemini_voice": "Puck",
            "live_gemini_language": "en-US", "live_gemini_avatar_name": ""}


def test_an_enabled_profile_overrides_the_live_settings_and_an_empty_field_keeps_the_global_value():
    g = SimpleNamespace(live={"enabled": True, "engine": "gemini", "mode": "avatar", "avatar_name": "Ben", "voice": "Aoede", "language": "de-DE"})
    s, p = LP.apply(SETTINGS, g)
    assert s["live_engine"] == "gemini" and s["live_avatar"] is True and s["live_gemini_voice"] == "Aoede" and s["live_gemini_language"] == "de-DE" and s["live_gemini_avatar_name"] == "Ben"
    assert s["live_voice"] == "af_heart" and p["enabled"] and SETTINGS["live_engine"] == "local"            # the global dict itself is not touched
    keep, _ = LP.apply(SETTINGS, SimpleNamespace(live={"enabled": True, "engine": "gemini", "mode": "voice"}))
    assert keep["live_gemini_voice"] == "Puck" and keep["live_avatar"] is False
    loc, _ = LP.apply(SETTINGS, SimpleNamespace(live={"enabled": True, "voice": "am_adam", "provider": "openai", "model": "gpt-5-mini"}))
    assert loc["live_voice"] == "am_adam" and loc["live_provider"] == "openai" and loc["live_model"] == "gpt-5-mini" and loc["live_engine"] == "local"
    model_only, _ = LP.apply(SETTINGS, SimpleNamespace(live={"enabled": True, "model": "llama3.2"}))
    assert model_only["live_model"] == "llama3.2" and model_only["live_provider"] == "ollama"
    off, p2 = LP.apply(SETTINGS, SimpleNamespace(live={"enabled": False, "engine": "gemini"}))
    assert off == SETTINGS and p2["enabled"] is False
    none, p3 = LP.apply(SETTINGS, SimpleNamespace(live={}))
    assert none == SETTINGS and p3 == {}


def test_the_profile_is_saved_with_the_agent_and_changes_with_it(state):
    made = state.agents.create(AgentDef(id="speaker", name="Speaker", live={"enabled": True, "engine": "gemini", "mode": "avatar", "avatar_name": "Ben", "budget_usd": 0.5}))
    assert made.live["avatar_name"] == "Ben"
    got = state.agents.get("speaker")
    assert got.live["engine"] == "gemini" and got.live["budget_usd"] == 0.5 and got.live["agent_turns"] is False
    state.agents.update("speaker", {"live": {**got.live, "mode": "voice", "agent_turns": True}})
    assert state.agents.get("speaker").live["mode"] == "voice" and state.agents.get("speaker").live["agent_turns"] is True
    plain = state.agents.create(AgentDef(id="plain", name="Plain"))
    assert plain.live == {} and state.agents.get("plain").live == {}
    with pytest.raises(ValueError, match="live.engine"):
        state.agents.create(AgentDef(id="bad", name="Bad", live={"engine": "x"}))


def test_the_agents_api_accepts_returns_and_refuses_profiles(client):
    body = {"id": "avataragent", "name": "Avatar agent", "live": {"enabled": True, "engine": "gemini", "mode": "avatar", "avatar_name": "Ben", "voice": "Puck"}}
    r = client.post("/api/agents", json=body)
    assert r.status_code == 200 and r.json()["live"]["avatar_name"] == "Ben"
    listed = {a["id"]: a for a in client.get("/api/agents").json()}
    assert listed["avataragent"]["live"]["engine"] == "gemini" and all(a["live"] == {} for k, a in listed.items() if k != "avataragent")
    bad = client.post("/api/agents", json={"id": "badlive", "name": "Bad", "live": {"engine": "gemini", "voice": "Nobody"}})
    assert bad.status_code == 422 and "30 prebuilt voices" in bad.text
    upd = client.put("/api/agents/avataragent", json={"live": {"enabled": False}})
    assert upd.status_code == 200 and upd.json()["live"]["enabled"] is False
