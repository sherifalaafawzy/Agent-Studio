"""/api/live with the Gemini Live engine (a fake Google server; nothing real is contacted)."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve

from app import live_api as API
from app import voice_spec as VS
from tests.live_fakes import FakeGoogle, speak_script
from tests.test_live_api import ORIGIN, api, build, drain  # noqa: F401  (api is a fixture)


def vertex(state, project="my-proj", location="us-central1"):
    state.providers.update("vertex_ai", {"vertex_project": project, "vertex_location": location})


@pytest.fixture
def google(state, monkeypatch):
    """The app with the Gemini engine pointed at a fake Google server running in its own thread."""
    g = FakeGoogle(script=speak_script)
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    holder: dict = {}

    async def main():
        holder["stop"] = asyncio.Event()
        async with serve(g.handler, "127.0.0.1", 0) as srv:
            g.url = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"
            ready.set()
            await holder["stop"].wait()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(5)
    vertex(state)
    app, eng = build(state, monkeypatch=monkeypatch)

    async def tok():
        return "T"
    monkeypatch.setitem(API.GEMINI, "token_source", lambda p: tok)
    state.db.set_setting("live_gemini_endpoint", g.url)
    yield g, TestClient(app), app
    loop.call_soon_threadsafe(holder["stop"].set)
    t.join(5)
    eng.close()


def test_gemini_status_names_what_is_missing_and_never_shows_credentials(state, monkeypatch):
    app, _ = build(state, monkeypatch=monkeypatch)
    c = TestClient(app)
    d = c.get("/api/live/gemini/status").json()
    assert d["configured"] is False and any("project" in p for p in d["problems"]) and d["model"] == "gemini-3.8-live" and len(d["voices"]) == 30
    assert d["avatar_examples"] == ["Ben"] and d["prices"]["out"]["AUDIO"] == 12.0 and any("write-only" in n for n in d["notes"]) and "api_key" not in json.dumps(d)
    vertex(state)
    ok = c.get("/api/live/gemini/status").json()
    assert ok["configured"] is True and ok["project"] == "my-proj" and ok["location"] == "us-central1" and ok["endpoint"].startswith("wss://us-central1-aiplatform.googleapis.com/ws/")
    vertex(state, location="global")
    assert c.get("/api/live/gemini/status").json()["location"] == "us-central1"          # Live is served from regions
    c.put("/api/live/settings", json={"live_gemini_provider": "nope"})
    assert any("does not exist" in p for p in c.get("/api/live/gemini/status").json()["problems"])


def test_gemini_settings_round_trip_and_validation(api):
    c, _ = api
    s = c.put("/api/live/settings", json=dict(live_engine="gemini", live_gemini_voice="Aoede", live_gemini_avatar_name="Ben", live_gemini_max_minutes=5, live_gemini_cost_cap_usd=0.5,
                                              live_gemini_video_bitrate=2000000, live_gemini_endpoint="ws://127.0.0.1:9")).json()
    assert s["live_engine"] == "gemini" and s["live_gemini_voice"] == "Aoede" and s["live_gemini_max_minutes"] == 5.0 and s["live_gemini_video_bitrate"] == 2000000
    for bad in ({"live_engine": "other"}, {"live_gemini_max_minutes": 500}, {"live_gemini_cost_cap_usd": 0}, {"live_gemini_endpoint": "http://x"}):
        assert c.put("/api/live/settings", json=bad).status_code == 422


def test_gemini_connection_test(google):
    g, c, _ = google
    r = c.post("/api/live/gemini/test")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["session"]["model"] == "gemini-3.8-live" and "authorization" not in g.headers
    c.put("/api/live/settings", json={"live_gemini_voice": "Nobody"})
    r2 = c.post("/api/live/gemini/test")
    assert r2.status_code == 502 and "prebuilt voices" in r2.json()["detail"]
    c.put("/api/live/settings", json={"live_gemini_voice": "Puck", "live_gemini_provider": "nope"})
    assert c.post("/api/live/gemini/test").status_code == 502


def test_a_gemini_conversation_over_the_socket_with_the_avatar_and_no_local_install(google, monkeypatch):
    g, c, app = google
    monkeypatch.setattr(VS, "is_ready", lambda h=None: False)                 # the local engines are NOT needed for Gemini
    c.put("/api/live/settings", json={"live_gemini_avatar_name": "Ben"})
    with c.websocket_connect("/api/live/ws", headers={"origin": ORIGIN}) as ws:
        ws.send_text(json.dumps({"type": "start", "engine": "gemini", "avatar": True, "voice_name": "Aoede"}))
        ev, _ = drain(ws, "ready")
        assert ev[-1]["cloud"] is True and ev[-1]["avatar"] is True and ev[-1]["engine"]["voice"] == "Aoede"
        ws.send_text(json.dumps({"type": "say", "text": "tell me a fact"}))
        ev2, audio = drain(ws, "metrics")
        assert audio > 0 and [e for e in ev2 if e.get("final")][0]["text"] == "Hello there."
        ws.send_text(json.dumps({"type": "stop"}))
    assert g.setup["generation_config"]["response_modalities"] == ["VIDEO"] and g.setup["avatar_config"] == {"avatar_name": "Ben"}
    assert g.setup["generation_config"]["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Aoede"


def test_a_gemini_session_without_a_provider_or_with_missing_settings_explains(state, monkeypatch):
    app, _ = build(state, monkeypatch=monkeypatch)
    c = TestClient(app)
    c.put("/api/live/settings", json={"live_gemini_provider": "nope"})
    with c.websocket_connect("/api/live/ws") as ws:
        ws.send_text(json.dumps({"type": "start", "engine": "gemini"}))
        assert "does not exist" in ws.receive_json()["message"]
    c.put("/api/live/settings", json={"live_gemini_provider": "vertex_ai"})
    with c.websocket_connect("/api/live/ws") as ws:
        ws.send_text(json.dumps({"type": "start", "engine": "gemini"}))
        assert "project" in ws.receive_json()["message"]


@pytest.mark.asyncio
async def test_the_ask_agent_tool_runs_a_normal_agent_turn_in_one_chat(state, monkeypatch):
    calls = []

    async def send(provider, model, message, sid, max_tokens, agent_id, workspace):
        calls.append((message, sid, agent_id))
        return {"reply": "done: " + message}
    monkeypatch.setattr(state.chat, "send", send)
    aid = state.agents.list()[0].id
    run = API.agent_runner(state, aid)
    assert await run("first") == "done: first" and await run("second") == "done: second"
    assert calls[0][1] == calls[1][1] and calls[0][2] == aid                  # the same chat, the chosen agent
    assert API.agent_runner(state, "") is None and API.agent_runner(state, "nope") is None
