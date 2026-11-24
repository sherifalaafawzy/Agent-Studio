"""Live sessions that use an agent's Live profile, the running cost meter, the budget, the history and the price endpoints (fake Google, fake engines)."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import live_api as API
from app import live_cost as LC
from app import voice_spec as VS
from app.agents import AgentDef
from tests.test_live_api import ORIGIN, api, build, drain  # noqa: F401  (api is a fixture)
from tests.test_live_api_gemini import google, vertex  # noqa: F401  (google is a fixture)
from tests.test_live_cost import PAGE


@pytest.fixture
def fast_watch(monkeypatch):
    orig = LC.watch
    monkeypatch.setattr(LC, "watch", lambda s, interval=0.05: orig(s, interval))


def make_agent(state, aid="speaker", **live):
    return state.agents.create(AgentDef(id=aid, name="Speaker", instruction="You are Pirate Pete. Be brief.", live=live))


def history(c, n: int = 1):
    """The records once the server has finished closing the session (it writes them after the socket closes)."""
    import time
    for _ in range(100):
        rows = c.get("/api/live/history").json()
        if len(rows) >= n:
            return rows
        time.sleep(0.05)
    return rows


def collect(ws, until: str, limit: int = 600):
    events, audio = [], 0
    for _ in range(limit):
        m = ws.receive()
        if m.get("bytes") is not None:
            audio += len(m["bytes"])
            continue
        e = json.loads(m["text"])
        events.append(e)
        if e["type"] == until:
            return events, audio
    raise AssertionError(f"never saw {until}: {[e['type'] for e in events][-10:]}")


# ---------------------------------------------------------------- resolve_start
def test_resolve_start_lets_an_enabled_profile_decide_and_refuses_unknown_or_disabled_agents(state):
    cfg = API.read_settings(state.db)
    make_agent(state, "a1", enabled=True, engine="gemini", mode="avatar", avatar_name="Ben", voice="Aoede", budget_usd=0.4)
    s, prof, agent, avatar = API.resolve_start(state, cfg, {"agent": "a1", "engine": "local", "avatar": False})
    assert s["live_engine"] == "gemini" and avatar is True and prof["budget_usd"] == 0.4 and agent.id == "a1" and s["live_gemini_voice"] == "Aoede"        # the profile beats the page
    make_agent(state, "off", enabled=False)
    with pytest.raises(ValueError, match="switched off for the agent 'Speaker'"):
        API.resolve_start(state, cfg, {"agent": "off"})
    with pytest.raises(ValueError, match="does not exist"):
        API.resolve_start(state, cfg, {"agent": "ghost"})
    state.agents.create(AgentDef(id="noprofile", name="Plain"))
    s2, p2, ag2, av2 = API.resolve_start(state, cfg, {"agent": "noprofile", "engine": "gemini", "avatar": True})
    assert s2["live_engine"] == "gemini" and p2 == {} and av2 is True and ag2.id == "noprofile"                        # no profile: the page's choice
    s3, p3, ag3, av3 = API.resolve_start(state, cfg, {})
    assert s3["live_engine"] == "local" and ag3 is None and av3 is False
    s4, *_ = API.resolve_start(state, cfg, {"engine": "bogus"})
    assert s4["live_engine"] == "local"


# ---------------------------------------------------------------- Gemini with an agent profile
def test_a_gemini_avatar_agent_runs_its_persona_and_budget_streams_usage_and_leaves_a_record(google, fast_watch, state):
    g, c, app = google
    make_agent(state, enabled=True, engine="gemini", mode="avatar", avatar_name="Ben", voice="Aoede", language="de-DE", budget_usd=0.5)
    state.db.set_setting("live_engine", "local")                                  # the global settings say local: the agent's profile must win
    with c.websocket_connect("/api/live/ws", headers={"origin": ORIGIN}) as ws:
        ws.send_text(json.dumps({"type": "start", "agent": "speaker", "engine": "local", "avatar": False}))
        ev, _ = collect(ws, "ready")
        assert ev[-1]["cloud"] is True and ev[-1]["avatar"] is True
        ws.send_text(json.dumps({"type": "say", "text": "tell me a fact"}))
        ev2, audio = collect(ws, "metrics")
        assert [e for e in ev2 if e["type"] == "metrics"][0]["cost_source"] == "reported"
        ev3, _ = collect(ws, "usage")                                                  # the meter ticks while the session is open
        usage = ev3[-1]
        assert usage["budget_usd"] == 0.5 and usage["cloud"] is True and usage["prices_as_of"] == "2026-11-23" and usage["source"] == "reported" and usage["cost_usd"] > 0 and usage["rows"]
        ws.send_text(json.dumps({"type": "stop"}))
    setup = g.setup
    assert setup["generation_config"]["response_modalities"] == ["VIDEO"] and setup["avatar_config"] == {"avatar_name": "Ben"}
    assert setup["generation_config"]["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Aoede" and setup["generation_config"]["speech_config"]["language_code"] == "de-DE"
    assert "You are Pirate Pete" in setup["system_instruction"]["parts"][0]["text"] and "ask_agent" in json.dumps(setup["tools"])
    rows = history(c)
    assert len(rows) == 1 and rows[0]["agent"] == "speaker" and rows[0]["engine"] == "gemini" and rows[0]["mode"] == "avatar" and rows[0]["budget_usd"] == 0.5
    assert rows[0]["cost_source"] == "reported" and rows[0]["cost_usd"] == pytest.approx(1000 * 3 / 1e6 + 500 * 12 / 1e6) and rows[0]["ended_by"] == "user"
    import time
    for _ in range(100):                                                              # the ledger row follows the history row by a moment
        ledger = state.db.query("SELECT * FROM ledger WHERE note LIKE 'live_session:%'")
        if ledger:
            break
        time.sleep(0.05)
    assert len(ledger) == 1 and ledger[0]["provider"] == "vertex_ai" and ledger[0]["model"] == "gemini-3.8-live"


def test_the_agents_budget_stops_a_gemini_session_with_a_clear_message(google, fast_watch, state):
    g, c, app = google
    make_agent(state, enabled=True, engine="gemini", mode="voice", budget_usd=0.005)                  # the fake answer costs $0.009
    with c.websocket_connect("/api/live/ws") as ws:
        ws.send_text(json.dumps({"type": "start", "agent": "speaker"}))
        collect(ws, "ready")
        ws.send_text(json.dumps({"type": "say", "text": "go"}))
        ev, _ = collect(ws, "error")
        stop = [e for e in ev if e["type"] == "budget"][-1]
        assert stop["level"] == "stop" and "reached its $0.01 budget" in stop["message"] and "reported by Google" in stop["message"] and "raise the budget" in stop["message"]
    rows = history(c)
    assert rows[0]["ended_by"] == "budget" and rows[0]["budget_usd"] == 0.005


def test_a_disabled_agent_is_refused_and_the_global_settings_still_work_for_other_agents(google, state):
    g, c, app = google
    make_agent(state, "offagent", enabled=False)
    with c.websocket_connect("/api/live/ws") as ws:
        ws.send_text(json.dumps({"type": "start", "agent": "offagent"}))
        assert "switched off for the agent" in ws.receive_json()["message"]
        ws.send_text(json.dumps({"type": "start", "agent": "ghost"}))
        assert "does not exist" in ws.receive_json()["message"]
        ws.send_text(json.dumps({"type": "start", "engine": "gemini"}))
        collect(ws, "ready")


# ---------------------------------------------------------------- the local engine with an agent profile
def test_a_local_agent_with_agent_turns_speaks_a_full_agent_answer_and_counts_its_usage(state, monkeypatch, fast_watch):
    calls = []

    async def send(provider, model, message, sid, max_tokens, agent_id, workspace):
        calls.append((message, agent_id))
        return {"reply": "The tool says the moon is far away today.", "usage": {"cost_usd": 0.0021, "input_tokens": 90, "output_tokens": 12}}
    monkeypatch.setattr(state.chat, "send", send)
    make_agent(state, enabled=True, engine="local", mode="voice", agent_turns=True, voice="am_adam", budget_usd=0.01)
    app, eng = build(state, loud=False, monkeypatch=monkeypatch)
    c = TestClient(app)
    with c.websocket_connect("/api/live/ws") as ws:
        ws.send_text(json.dumps({"type": "start", "agent": "speaker"}))
        collect(ws, "ready")
        ws.send_text(json.dumps({"type": "say", "text": "how far is the moon"}))
        ev, audio = collect(ws, "metrics")
        assert audio > 0 and [e for e in ev if e.get("final")][0]["text"].startswith("The tool says the moon")
        ws.send_text(json.dumps({"type": "stop"}))
    assert calls == [("how far is the moon", "speaker")] and app.state.engines.tts_calls[0][1] == "am_adam"
    row = history(c)[0]
    assert row["engine"] == "local" and row["provider"] == "agent" and row["model"] == "speaker" and row["cost_usd"] == pytest.approx(0.0021) and row["cost_source"] == "reported"
    assert row["budget_usd"] == 0.01 and row["detail"]["tokens"]["in"] == 90 and row["detail"]["tokens"]["out"] == 12
    assert not state.db.query("SELECT * FROM ledger WHERE note LIKE 'live_session:%'")                    # local sessions are not Google spend; the agent's own turn is in the ledger already
    eng.close()


def test_a_plain_local_session_shows_usage_with_zero_cloud_cost(state, monkeypatch, fast_watch):
    app, eng = build(state, monkeypatch=monkeypatch)
    c = TestClient(app)
    with c.websocket_connect("/api/live/ws") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        collect(ws, "ready")
        ws.send_text(json.dumps({"type": "say", "text": "hello"}))
        ev, _ = collect(ws, "metrics")
        ev2, _ = collect(ws, "usage")
        usage = ev2[-1]
        assert usage["cloud"] is False and usage["cost_usd"] == 0.0 and usage["source"] == "local" and usage["seconds"]["audio_out"] > 0 and usage["budget_usd"] is None
        ws.send_text(json.dumps({"type": "stop"}))
    row = history(c)[0]
    assert row["engine"] == "local" and row["cost_usd"] == 0.0 and row["cost_source"] == "local" and row["detail"]["seconds"]["audio_out"] > 0
    eng.close()


def test_a_session_that_never_started_leaves_no_record(state, monkeypatch):
    app, eng = build(state, ready=False, monkeypatch=monkeypatch)
    c = TestClient(app)
    with c.websocket_connect("/api/live/ws") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        assert "not installed" in ws.receive_json()["message"]
    assert c.get("/api/live/history").json() == []


# ---------------------------------------------------------------- prices and history endpoints
def test_the_price_table_can_be_read_edited_checked_and_refreshed_from_googles_page(api):
    c, _ = api
    d = c.get("/api/live/prices").json()
    assert d["as_of"] == "2026-11-23" and d["in"]["AUDIO"] == 3.0 and d["source"].startswith("https://cloud.google.com/")
    ok = c.put("/api/live/prices", json={"as_of": "2027-01-01", "out": {"AUDIO": 13.0}, "rates": {"video_out_tokens_per_s": 3000}}).json()
    assert ok["out"]["AUDIO"] == 13.0 and ok["in"]["AUDIO"] == 3.0 and ok["as_of"] == "2027-01-01" and c.get("/api/live/prices").json()["rates"]["video_out_tokens_per_s"] == 3000.0
    bad = c.put("/api/live/prices", json={"in": {"AUDIO": -5}})
    assert bad.status_code == 422 and "0 to 1000" in bad.json()["detail"]
    API.PRICE_TRANSPORT["transport"] = httpx.MockTransport(lambda r: httpx.Response(200, text=PAGE))
    try:
        r = c.post("/api/live/prices/refresh")
        assert r.status_code == 200 and r.json()["out"]["AUDIO"] == 12.0 and r.json()["as_of"] != "2027-01-01"
        API.PRICE_TRANSPORT["transport"] = httpx.MockTransport(lambda r: httpx.Response(500))
        failed = c.post("/api/live/prices/refresh")
        assert failed.status_code == 502 and "answered 500" in failed.json()["detail"]
    finally:
        API.PRICE_TRANSPORT["transport"] = None
    assert c.get("/api/live/gemini/status").json()["prices"]["out"]["AUDIO"] == 12.0


def test_the_history_lists_newest_first_and_respects_the_limit(api):
    c, app = api
    st = app.state.s
    for i in range(3):
        LC.record_session(st.db, f"h{i}", 1000.0 + i, dict(engine="local", mode="voice"), {"elapsed_s": 1, "cost_usd": 0, "source": "local", "budget_usd": None, "tokens": {}, "seconds": {}, "rows": [], "prices_as_of": "x", "reports": 0}, "user")
    assert [r["id"] for r in c.get("/api/live/history").json()] == ["h2", "h1", "h0"]
    assert [r["id"] for r in c.get("/api/live/history?limit=2").json()] == ["h2", "h1"]


def test_the_price_table_travels_in_the_settings_export_and_import(client):
    client.put("/api/live/settings", json={"live_gemini_model": "gemini-3.8-live"})
    client.put("/api/live/prices", json={"out": {"AUDIO": 13.5}, "as_of": "2027-02-02"})
    exp = client.get("/api/settings/export").json()
    assert exp["settings"]["live_gemini_prices"]["out"]["AUDIO"] == 13.5
    client.post("/api/settings/reset", json={"all": True})
    done = client.post("/api/settings/import", json={"data": exp, "dry_run": False}).json()
    assert done["applied"] is True and not done["errors"] and client.get("/api/live/prices").json()["out"]["AUDIO"] == 13.5
    bad = {**exp, "settings": {"live_gemini_prices": {"in": {"AUDIO": -1}}}}
    res = client.post("/api/settings/import", json={"data": bad, "dry_run": False}).json()
    assert res["errors"] and "0 to 1000" in res["errors"][0]["error"]
