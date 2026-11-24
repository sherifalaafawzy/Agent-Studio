"""/api/live/*: settings, install, warm-up, the self-test, and the live WebSocket (voice engine and model are fakes)."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import live_api as API
from app import live_engine as LE
from app import voice_local as VL
from app import voice_spec as VS
from app.live_llm import LlmError
from tests.live_fakes import FakeEngines, FakeProc
from tests.test_live import FLlm
from tests.test_voice_local import GPU, good_runner

ORIGIN = "http://localhost:3100"


def build(state, ready=True, loud=False, installer=None, llm=None, monkeypatch=None, engine_text="what is the moon", llm_error=None):
    engines = FakeEngines(text=engine_text, loud=loud)
    eng = LE.VoiceEngine(spawn=lambda: FakeProc(engines))
    app = FastAPI()
    app.state.s = state
    API.register(app, [ORIGIN], engine=eng, installer=installer or VL.Installer(runner=good_runner(), hardware=lambda: GPU))
    if monkeypatch is not None:
        monkeypatch.setattr(VS, "is_ready", lambda h=None: ready)
        the_llm = llm or FLlm(pieces=("Sure, here we go. ", "All done now."))

        def fake_build(st, provider, model):
            if llm_error:
                raise LlmError(llm_error)
            fake_build.seen.append((provider, model))
            return the_llm
        fake_build.seen = []
        monkeypatch.setattr(API, "build_llm", fake_build)
        app.state.fake_build, app.state.llm, app.state.engines = fake_build, the_llm, engines
    return app, eng


@pytest.fixture
def api(state, monkeypatch):
    app, eng = build(state, monkeypatch=monkeypatch)
    yield TestClient(app), app
    eng.close()


# ---------------------------------------------------------------- settings and status
def test_status_lists_the_local_install_the_engine_and_the_settings(api):
    c, _ = api
    d = c.get("/api/live/status").json()
    assert d["local"]["state"] in ("not_installed", "ready") and d["engine"]["state"] == "idle"
    expected = dict(live_provider="ollama", live_model="", live_voice="af_heart", live_speed=1.0, live_hangover_ms=350, live_barge_in=True, live_avatar=False, live_agent="", live_engine="local")
    assert {k: d["settings"][k] for k in expected} == expected
    assert d["settings"]["live_gemini_model"] == "gemini-3.8-live" and d["settings"]["live_gemini_voice"] == "Puck" and d["settings"]["live_gemini_provider"] == "vertex_ai"


def test_settings_round_trip_with_types_and_validation(api):
    c, _ = api
    s = c.put("/api/live/settings", json=dict(live_model="qwen3:8b", live_speed=1.2, live_hangover_ms=500, live_barge_in=False, live_avatar=True, live_voice="am_adam", live_agent="a1")).json()
    assert s["live_model"] == "qwen3:8b" and s["live_speed"] == 1.2 and s["live_hangover_ms"] == 500 and s["live_barge_in"] is False and s["live_avatar"] is True
    assert c.get("/api/live/status").json()["settings"]["live_voice"] == "am_adam"
    assert c.put("/api/live/settings", json=dict(live_speed=9)).status_code == 422
    assert c.put("/api/live/settings", json=dict(live_hangover_ms=10)).status_code == 422
    assert c.put("/api/live/settings", json={}).json()["live_model"] == "qwen3:8b"
    assert c.get("/api/live/voices").json()["default"] == "af_heart"


def test_session_config_uses_the_saved_settings():
    cfg = API.session_config(dict(live_voice="am_adam", live_speed=1.1, live_barge_in=False, live_hangover_ms=450), "Be brief.")
    assert (cfg.voice, cfg.speed, cfg.barge_in, cfg.vad.hangover_ms, cfg.instruction) == ("am_adam", 1.1, False, 450, "Be brief.")


def test_the_agent_instruction_and_model_helpers(state):
    aid = state.agents.list()[0].id
    assert API.agent_instruction(state, aid) == (state.agents.get(aid).instruction or "") and API.agent_instruction(state, "") == "" and API.agent_instruction(state, "nope") == ""
    with pytest.raises(LlmError, match="choose a model"):
        API.build_llm(state, "", "")
    assert API.build_llm(state, "ollama", "qwen3:8b").model == "qwen3:8b"
    assert API.summary([{"ttfa_ms": 100}, {"ttfa_ms": 300}, {"ttfa_ms": "x"}]) == {"ttfa_ms": 200}


# ---------------------------------------------------------------- install
def test_install_endpoints(state, monkeypatch, tmp_path):
    inst = VL.Installer(runner=good_runner(), hardware=lambda: GPU)
    app, eng = build(state, installer=inst)                                  # no patching: the real readiness check, in the test's own data folder
    assert str(VS.home()).startswith(str(tmp_path))
    c = TestClient(app)
    assert c.post("/api/live/install", json={"accept_licenses": False}).status_code == 422
    assert c.post("/api/live/install", json={"accept_licenses": True, "device": "tpu"}).status_code == 422
    r = c.post("/api/live/install", json={"accept_licenses": True})
    assert r.status_code == 200 and r.json()["state"] == "installing"
    inst.thread.join(timeout=10)
    p = c.get("/api/live/install/progress").json()
    assert p["state"] == "ready" and p["percent"] == 100 and set(p) == {"state", "ready", "error", "steps", "percent", "progress_text", "log_tail"}
    assert c.post("/api/live/install", json={"accept_licenses": True}).status_code == 409
    assert c.post("/api/live/uninstall").json()["removed"] is True and eng.state == "idle"
    monkeypatch.setattr(inst, "uninstall", lambda: (_ for _ in ()).throw(VL.SetupConflict("busy")))
    assert c.post("/api/live/uninstall").status_code == 409


# ---------------------------------------------------------------- warm-up
def test_warm_needs_the_install_then_loads_engines_and_model(state, monkeypatch):
    app, eng = build(state, ready=False, monkeypatch=monkeypatch)
    c = TestClient(app)
    assert c.post("/api/live/warm").status_code == 412 and "not installed" in c.post("/api/live/warm").json()["detail"]
    app2, eng2 = build(state, ready=True, monkeypatch=monkeypatch)
    c2 = TestClient(app2)
    r = c2.post("/api/live/warm?model=qwen3:8b&provider=ollama")
    assert r.status_code == 200 and r.json()["ready"] is True and r.json()["engine"]["device"] == "cpu"
    assert app2.state.fake_build.seen == [("ollama", "qwen3:8b")] and app2.state.llm.warmed == 1
    eng.close(); eng2.close()


def test_warm_reports_engine_and_model_problems_as_502(state, monkeypatch):
    app, eng = build(state, llm_error="Ollama is not reachable", monkeypatch=monkeypatch)
    r = TestClient(app).post("/api/live/warm")
    assert r.status_code == 502 and "not reachable" in r.json()["detail"]
    app2, eng2 = build(state, monkeypatch=monkeypatch)
    eng2._spawn = lambda: (_ for _ in ()).throw(OSError("worker will not start"))
    r2 = TestClient(app2).post("/api/live/warm")
    assert r2.status_code == 502 and "worker will not start" in r2.json()["detail"]


# ---------------------------------------------------------------- self-test
def test_selftest_plays_the_voice_back_as_the_microphone_and_times_every_stage(state, monkeypatch):
    app, eng = build(state, loud=True, llm=FLlm(pieces=("Sure, here we go. ", "All done now.")), monkeypatch=monkeypatch)
    c = TestClient(app)
    d = c.post("/api/live/selftest", json={"runs": 3, "text": "Hello there"}).json()
    assert d["said"] == "Hello there" and len(d["turns"]) == 3 and d["local_model"] is True
    t = d["turns"][0]
    assert t["heard"] == "what is the moon" and t["reply"] == "Sure, here we go. All done now." and t["reply_audio_s"] > 0 and "error" not in t
    for k in ("stt_ms", "llm_first_token_ms", "tts_first_ms", "ttfa_ms", "user_perceived_ms"):
        assert isinstance(t[k], int)
    assert d["median"]["endpoint_ms"] == 350 and d["first"]["ttfa_ms"] == t["ttfa_ms"] and "server-side" in d["note"]
    one = c.post("/api/live/selftest", json={"runs": 1}).json()
    assert len(one["turns"]) == 1 and one["median"] == one["first"]
    eng.close()


def test_selftest_surfaces_model_errors_and_missing_installs(state, monkeypatch):
    app, eng = build(state, loud=True, llm=FLlm(error="model exploded"), monkeypatch=monkeypatch)
    d = TestClient(app).post("/api/live/selftest", json={"runs": 1}).json()
    assert d["turns"][0]["error"] == "model exploded"
    app2, _ = build(state, ready=False, monkeypatch=monkeypatch)
    assert TestClient(app2).post("/api/live/selftest", json={}).status_code == 412
    app3, _ = build(state, llm_error="no model chosen", monkeypatch=monkeypatch)
    assert TestClient(app3).post("/api/live/selftest", json={}).status_code == 502
    assert TestClient(app3).post("/api/live/selftest", json={"runs": 9}).status_code == 422
    eng.close()


# ---------------------------------------------------------------- the live conversation (WebSocket)
def drain(ws, until: str, limit: int = 400):
    """Collect JSON events (binary audio counted) until one of type `until` arrives."""
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
    raise AssertionError(f"never saw {until}: {[e['type'] for e in events]}")


def test_a_typed_message_gets_a_spoken_answer_over_the_socket(api):
    c, app = api
    with c.websocket_connect("/api/live/ws", headers={"origin": ORIGIN}) as ws:
        ws.send_bytes(bytes(640))                                          # audio before "start" is ignored
        ws.send_text("not json")
        ws.send_text(json.dumps({"type": "say", "text": "too early"}))      # so is anything else before start
        ws.send_text(json.dumps({"type": "start", "provider": "ollama", "model": "qwen3:8b", "voice": "am_adam", "speed": 1.2, "hangover_ms": 450, "barge_in": False}))
        ws.send_text(json.dumps({"type": "start", "provider": "other"}))    # a second start is ignored
        ev, _ = drain(ws, "ready")
        assert [e["state"] for e in ev if e["type"] == "state"] == ["loading", "listening"] and ev[-1]["local_model"] is True
        ws.send_text(json.dumps({"type": "say", "text": "  tell me something  "}))
        ev2, audio = drain(ws, "metrics")
        t = [e["type"] for e in ev2]
        assert t.index("transcript") < t.index("assistant") < t.index("metrics") and audio > 0
        assert [e for e in ev2 if e["type"] == "transcript"][0]["text"] == "tell me something"
        assert [e for e in ev2 if e.get("final")][0]["text"] == "Sure, here we go. All done now."
        ws.send_text(json.dumps({"type": "interrupt"}))
        assert drain(ws, "clear")[0][-1]["type"] == "clear"
        ws.send_text(json.dumps({"type": "commit"}))
        ws.send_text(json.dumps({"type": "say", "text": 5}))                # not text: ignored
        ws.send_text(json.dumps([1, 2]))                                    # not an object: ignored
        ws.send_text(json.dumps({"type": "stop"}))
    assert app.state.fake_build.seen == [("ollama", "qwen3:8b")]
    assert app.state.engines.tts_calls[0][1:] == ("am_adam", 1.2)


def test_a_chosen_agents_instructions_shape_the_conversation(api):
    c, app = api
    st = app.state.s
    a = st.agents.list()[0]
    with c.websocket_connect("/api/live/ws") as ws:                         # no Origin header: a non-browser client
        ws.send_text(json.dumps({"type": "start", "agent": a.id}))
        drain(ws, "ready")
        ws.send_text(json.dumps({"type": "say", "text": "hi"}))
        drain(ws, "metrics")
    system = app.state.llm.calls[0][0]["content"]
    assert (a.instruction or "").strip()[:30] in system and "by voice" in system


def test_microphone_audio_over_the_socket_is_heard_and_answered(api):
    c, app = api
    from tests.test_live import quiet, tone
    with c.websocket_connect("/api/live/ws", headers={"origin": ORIGIN}) as ws:
        ws.send_text(json.dumps({"type": "start"}))
        drain(ws, "ready")
        data = quiet(10) + tone(30) + quiet(25)
        for i in range(0, len(data), 640):
            ws.send_bytes(data[i:i + 640])
        ev, audio = drain(ws, "metrics")
        assert [e for e in ev if e["type"] == "transcript"][0]["text"] == "what is the moon" and audio > 0


def test_a_page_from_another_origin_is_refused(api):
    c, _ = api
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/api/live/ws", headers={"origin": "http://evil.example"}):
            pass
    assert e.value.code == 1008


def test_without_the_install_the_socket_explains_and_stays_open(state, monkeypatch):
    app, _ = build(state, ready=False, monkeypatch=monkeypatch)
    with TestClient(app).websocket_connect("/api/live/ws") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        m = ws.receive_json()
        assert m["type"] == "error" and "not installed" in m["message"]


def test_a_model_that_cannot_be_reached_is_reported_and_the_socket_stays_open(state, monkeypatch):
    app, _ = build(state, llm_error="unknown provider 'nope'", monkeypatch=monkeypatch)
    with TestClient(app).websocket_connect("/api/live/ws") as ws:
        ws.send_text(json.dumps({"type": "start", "provider": "nope"}))
        assert ws.receive_json() == {"type": "error", "message": "unknown provider 'nope'"}


def test_a_worker_that_will_not_start_is_reported_on_the_socket(state, monkeypatch):
    app, eng = build(state, monkeypatch=monkeypatch)
    eng._spawn = lambda: (_ for _ in ()).throw(OSError("worker will not start"))
    with TestClient(app).websocket_connect("/api/live/ws") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        errs = []
        while not errs:
            m = ws.receive_json()
            if m["type"] == "error":
                errs.append(m)
        assert "worker will not start" in errs[0]["message"]
    eng2_app, eng2 = build(state, monkeypatch=monkeypatch)
    eng2._spawn = lambda: (_ for _ in ()).throw(ValueError("odd failure"))     # anything else is reported with its type
    with TestClient(eng2_app).websocket_connect("/api/live/ws") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        while True:
            m = ws.receive_json()
            if m["type"] == "error":
                assert "ValueError" in m["message"] or "odd failure" in m["message"]
                break


def test_a_client_that_vanishes_while_the_models_are_still_loading_is_cleaned_up(api):
    import asyncio
    from starlette.websockets import WebSocketDisconnect as Gone
    _, app = api
    handler = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/live/ws")

    class Stub:
        headers: dict = {}

        def __init__(self):
            self.msgs = [{"type": "websocket.receive", "text": json.dumps({"type": "start"})}]
            self.sent = []

        async def accept(self): pass
        async def send_text(self, t): self.sent.append(t)
        async def send_bytes(self, b): pass
        async def close(self, code=1000): pass

        async def receive(self):
            if self.msgs:
                return self.msgs.pop(0)
            raise Gone(1001)

    asyncio.run(handler(Stub()))                                               # disconnect right after "start": the loading task is cancelled, nothing raises
