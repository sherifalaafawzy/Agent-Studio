"""Gemini Live provider: the setup message, the session against a fake Google server, tool calls, limits and failures.

The fake server follows the messages Google documents; none of this has been run against Google itself."""
from __future__ import annotations

import asyncio
import base64
import json

import pytest

from app import gemini_live as GL
from app import live_cost as LC
from tests.live_fakes import FakeGoogle, fake_google, pcm_b64, speak_script
from tests.test_live import quiet, tone


def cfg(g=None, **kw) -> GL.GeminiConfig:
    base = dict(project="my-proj", location="us-central1", endpoint=g.url if g else "", cost_cap_usd=1.0)
    base.update(kw)
    return GL.GeminiConfig(**base)


async def token() -> str:
    return "TOKEN-123"


class Sink:
    def __init__(self):
        self.events: list[dict] = []
        self.audio: list[bytes] = []

    async def send_json(self, m):
        self.events.append(m)

    async def send_audio(self, b):
        self.audio.append(b)

    def of(self, t):
        return [e for e in self.events if e["type"] == t]

    def states(self):
        return [e["state"] for e in self.events if e["type"] == "state"]


def session(c, sink=None, run_agent=None, tok=token):
    sink = sink or Sink()
    return GL.GeminiLiveSession(c, sink.send_json, sink.send_audio, tok, GL.default_connect, run_agent), sink


async def until(cond, timeout=5.0):
    end = asyncio.get_event_loop().time() + timeout
    while not cond():
        assert asyncio.get_event_loop().time() < end, "timed out waiting"
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------- pure helpers
def test_the_setup_message_follows_the_documented_shape():
    s = GL.build_setup(GL.GeminiConfig(project="p", location="eu", voice="Aoede", language="de-DE", instruction="Be Pirate Pete."), tools=False)
    assert s["model"] == "projects/p/locations/eu/publishers/google/models/gemini-3.8-live"
    assert s["generation_config"]["response_modalities"] == ["AUDIO"]
    assert s["generation_config"]["speech_config"] == {"voice_config": {"prebuilt_voice_config": {"voice_name": "Aoede"}}, "language_code": "de-DE"}
    assert s["system_instruction"]["parts"][0]["text"].startswith("Be Pirate Pete.\n\n") and "Never call more than two tools" in s["system_instruction"]["parts"][0]["text"]
    assert s["input_audio_transcription"] == {} and s["output_audio_transcription"] == {} and "avatar_config" not in s and "tools" not in s


def test_avatar_and_tools_and_bitrates_are_added_only_when_asked():
    s = GL.build_setup(GL.GeminiConfig(project="p", avatar=True, avatar_name="Ben", video_bitrate_bps=2_000_000, audio_bitrate_bps=64_000), tools=True)
    assert s["generation_config"]["response_modalities"] == ["VIDEO"]
    assert s["avatar_config"] == {"avatar_name": "Ben", "video_bitrate_bps": 2_000_000, "audio_bitrate_bps": 64_000}
    decl = s["tools"][0]["function_declarations"][0]
    assert decl["name"] == "ask_agent" and decl["parameters"]["required"] == ["request"] and decl["parameters"]["type"] == "OBJECT"
    plain = GL.build_setup(GL.GeminiConfig(project="p", avatar=True, avatar_name="Ben"), tools=False)
    assert plain["avatar_config"] == {"avatar_name": "Ben"}


def test_what_is_missing_is_named_and_the_address_is_regional():
    assert GL.problems(GL.GeminiConfig()) and "project" in GL.problems(GL.GeminiConfig())[0]
    assert any("regional" in x for x in GL.problems(GL.GeminiConfig(project="p", location="global")))
    assert any("avatar name" in x for x in GL.problems(GL.GeminiConfig(project="p", avatar=True)))
    assert any("prebuilt voices" in x for x in GL.problems(GL.GeminiConfig(project="p", voice="Nobody")))
    assert GL.problems(GL.GeminiConfig(project="p", avatar=True, avatar_name="Ben")) == []
    c = GL.GeminiConfig(project="p", location="europe-west4")
    assert c.url() == "wss://europe-west4-aiplatform.googleapis.com/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent"
    assert GL.GeminiConfig(endpoint="ws://127.0.0.1:9/").url() == "ws://127.0.0.1:9" + GL.PATH
    assert len(GL.VOICES) == 30 and "Puck" in GL.VOICES and "en-US" in GL.LANGUAGES


def test_pick_takes_whichever_spelling_is_present():
    assert GL.pick({"a": 1}, "b", "a") == 1 and GL.pick(None, "a") is None and GL.pick({}, "a") is None


# ---------------------------------------------------------------- a conversation
@pytest.mark.asyncio
async def test_start_authenticates_sends_setup_and_reports_ready():
    async with fake_google() as g:
        s, sink = session(cfg(g, voice="Aoede"))
        info = await s.start()
        assert "authorization" not in g.headers and g.path == GL.PATH                # a plaintext ws:// test server never receives the token
        assert g.setup["model"].endswith("/models/gemini-3.8-live") and g.setup["generation_config"]["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Aoede"
        assert info["engine"] == {"provider": "gemini", "model": "gemini-3.8-live", "avatar": False, "voice": "Aoede", "session_id": "sess-1"} and info["cloud"] is True
        assert sink.states() == ["loading", "listening"] and sink.of("ready")
        await s.close()
        await s.close()


@pytest.mark.asyncio
async def test_a_typed_message_gets_transcripts_audio_and_timings():
    async with fake_google(script=speak_script) as g:
        s, sink = session(cfg(g))
        await s.start()
        await s.say("tell me a fact")
        await until(lambda: sink.of("metrics"))
        await s.close()
        assert {"realtimeInput": {"text": "tell me a fact"}} in g.received
        assert [e["text"] for e in sink.of("transcript")] == ["tell me a fact"]          # the typed message is shown once; Google's echo of it is not repeated
        assert "".join(e["delta"] for e in sink.of("assistant") if "delta" in e) == "Hello there."
        assert [e for e in sink.of("assistant") if e.get("final")][0]["text"] == "Hello there."
        assert len(sink.audio) == 2 and sink.audio[0] == bytes([7, 0]) * 480
        m = sink.of("metrics")[0]
        assert m["turn"] == 1 and m["cloud"] is True and m["endpoint_ms"] == 0 and isinstance(m["ttfa_ms"], int) and m["user_perceived_ms"] == m["ttfa_ms"]
        assert m["cost_usd"] == pytest.approx(1000 * 3 / 1e6 + 500 * 12 / 1e6)
        assert sink.states()[-1] == "listening" and "speaking" in sink.states()


@pytest.mark.asyncio
async def test_microphone_audio_goes_up_as_16k_pcm_and_the_light_follows_the_speaker():
    async with fake_google(script=speak_script) as g:
        s, sink = session(cfg(g))
        await s.audio(tone(5))                                                   # before start: ignored
        await s.start()
        data = quiet(10) + tone(30) + quiet(25)
        for i in range(0, len(data), 640):
            await s.audio(data[i:i + 640])
        await until(lambda: g.audio_chunks >= 65)
        first = next(m for m in g.received if "audio" in (m.get("realtimeInput") or {}))["realtimeInput"]["audio"]
        assert first["mimeType"] == "audio/pcm;rate=16000" and len(base64.b64decode(first["data"])) == 640
        assert sink.states()[:4] == ["loading", "listening", "hearing", "thinking"]
        await s.commit()                                                          # push-to-talk release
        await until(lambda: sink.of("metrics"))
        assert {"realtimeInput": {"audioStreamEnd": True}} in g.received
        assert sink.of("metrics")[0]["endpoint_ms"] == 350 and sink.of("metrics")[0]["user_perceived_ms"] == sink.of("metrics")[0]["ttfa_ms"] + 350
        await s.close()


@pytest.mark.asyncio
async def test_a_cough_returns_to_listening():
    async with fake_google() as g:
        s, sink = session(cfg(g))
        await s.start()
        await s.audio(quiet(10) + tone(6) + quiet(30))
        assert sink.states()[-2:] == ["hearing", "listening"]
        await s.close()


@pytest.mark.asyncio
async def test_the_avatar_asks_for_video_and_video_parts_are_forwarded_untouched():
    async def script(ws, msg, g):
        if "text" in (msg.get("realtimeInput") or {}):
            await ws.send(json.dumps({"server_content": {"model_turn": {"parts": [{"inline_data": {"mime_type": "video/mp4", "data": "QUJD"}},
                                                                                     {"inline_data": {"mime_type": "audio/pcm;rate=24000", "data": pcm_b64(10)}}]}}}))
            await ws.send(json.dumps({"server_content": {"turn_complete": True}}))
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g, avatar=True, avatar_name="Ben"))
        info = await s.start()
        assert g.setup["generation_config"]["response_modalities"] == ["VIDEO"] and g.setup["avatar_config"] == {"avatar_name": "Ben"} and info["avatar"] is True
        await s.say("hi")
        await until(lambda: sink.of("metrics"))
        assert sink.of("video") == [{"type": "video", "mime": "video/mp4", "data": "QUJD"}]
        m = sink.of("metrics")[0]
        assert len(sink.audio) == 1 and isinstance(m["first_video_ms"], int) and m["user_perceived_ms"] == min(m["first_video_ms"], m["ttfa_ms"])
        await s.close()


@pytest.mark.asyncio
async def test_an_interruption_clears_playback_and_the_interrupt_button_drops_the_rest_of_the_answer():
    async def script(ws, msg, g):
        rt = msg.get("realtimeInput") or {}
        if rt.get("text") == "again":
            await ws.send(json.dumps({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": pcm_b64()}}]}}}))
            await ws.send(json.dumps({"serverContent": {"interrupted": True}}))
        elif rt.get("text") == "talk":
            await ws.send(json.dumps({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": pcm_b64()}}]}}}))
            await asyncio.sleep(0.1)
            await ws.send(json.dumps({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": pcm_b64()}}]}}}))
            await ws.send(json.dumps({"serverContent": {"turnComplete": True}}))
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g))
        await s.start()
        await s.say("again")
        await until(lambda: sink.of("clear"))
        assert sink.states()[-1] == "hearing" and len(sink.audio) == 1
        await s.say("talk")
        await until(lambda: len(sink.audio) == 2)
        await s.interrupt()                                                      # the person pressed Interrupt: the rest of this answer is dropped
        await until(lambda: sink.of("metrics"))
        assert len(sink.audio) == 2 and sink.states()[-1] == "listening"
        await s.close()


@pytest.mark.asyncio
async def test_tool_calls_run_through_the_agent_and_the_answer_goes_back_with_the_same_id():
    asked: list[str] = []

    async def agent(request: str) -> str:
        asked.append(request)
        if request == "boom":
            raise RuntimeError("the agent crashed")
        return "It is sunny in Cairo."

    async def script(ws, msg, g):
        rt = msg.get("realtimeInput") or {}
        if rt.get("text") == "weather":
            await ws.send(json.dumps({"toolCall": {"functionCalls": [{"id": "c1", "name": "ask_agent", "args": {"request": "weather in Cairo"}},
                                                                       {"id": "c2", "name": "ask_agent", "args": {"request": "boom"}},
                                                                       {"id": "c3", "name": "ask_agent", "args": {}},
                                                                       {"id": "c4", "name": "other", "args": {"x": 1}}]}}))
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g), run_agent=agent)
        await s.start()
        assert g.setup["tools"][0]["function_declarations"][0]["name"] == "ask_agent"
        await s.say("weather")
        await until(lambda: sum(1 for m in g.received if "toolResponse" in m) == 4)
        by_id = {r["id"]: r for m in g.received if "toolResponse" in m for r in m["toolResponse"]["functionResponses"]}
        assert asked == ["weather in Cairo", "boom"]
        assert by_id["c1"]["response"] == {"status": "ok", "retryable": False, "result": "It is sunny in Cairo."} and by_id["c1"]["name"] == "ask_agent"
        assert by_id["c2"]["response"]["status"] == "error" and "the agent crashed" in by_id["c2"]["response"]["message"]
        assert by_id["c3"]["response"]["status"] == "invalid_argument" and by_id["c4"]["response"]["status"] == "invalid_argument"
        await s.close()


@pytest.mark.asyncio
async def test_a_tool_call_that_google_cancels_is_stopped_and_no_agent_means_no_tool_declared():
    gate = asyncio.Event()
    cancelled = []

    async def slow(request: str) -> str:
        try:
            await gate.wait()
        except asyncio.CancelledError:
            cancelled.append(1)
            raise
        return "late"

    async def script(ws, msg, g):
        rt = msg.get("realtimeInput") or {}
        if rt.get("text") == "go":
            await ws.send(json.dumps({"toolCall": {"function_calls": [{"id": "x1", "name": "ask_agent", "args": {"request": "slow thing"}}]}}))
            await asyncio.sleep(0.1)
            await ws.send(json.dumps({"tool_call_cancellation": {"ids": ["x1", "unknown"]}}))
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g), run_agent=slow)
        await s.start()
        await s.say("go")
        await until(lambda: cancelled)
        await s.close()
    async with fake_google() as g2:
        s2, _ = session(cfg(g2))
        await s2.start()
        assert "tools" not in g2.setup
        await s2.close()


@pytest.mark.asyncio
async def test_the_cost_limit_ends_the_session_and_says_why():
    async def script(ws, msg, g):
        if "text" in (msg.get("realtimeInput") or {}):
            await ws.send(json.dumps({"usageMetadata": {"promptTokensDetails": [{"modality": "AUDIO", "tokenCount": 1_000_000}]}}))
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g, cost_cap_usd=1.0))
        await s.start()
        await s.say("expensive")
        await until(lambda: sink.of("error"))
        assert "reached its $1.00 budget" in sink.of("error")[0]["message"] and "as reported by Google" in sink.of("error")[0]["message"] and s.closed
        assert s.meter.snapshot()["cost_usd"] == pytest.approx(3.0) and s.meter.level == "stop" and sink.of("budget")[-1]["level"] == "stop"


@pytest.mark.asyncio
async def test_the_session_length_limit_and_googles_goodbye_are_reported():
    async def script(ws, msg, g):
        if "text" in (msg.get("realtimeInput") or {}):
            await ws.send(json.dumps({"goAway": {"timeLeft": "30s"}}))
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g))
        await s.start()
        await s.say("hello")
        await until(lambda: sink.of("error"))
        assert "end this Gemini Live session soon" in sink.of("error")[0]["message"]
        await s.close()
    async with fake_google() as g2:
        s2, sink2 = session(cfg(g2, max_minutes=0.0005))
        await s2.start()
        await until(lambda: s2.closed)
        assert "minute limit" in sink2.of("error")[0]["message"]


@pytest.mark.asyncio
async def test_data_that_is_not_json_is_noted_once_and_a_closed_connection_is_reported():
    async def script(ws, msg, g):
        if "text" in (msg.get("realtimeInput") or {}):
            await ws.send(b"\x00\x01 binary video bytes")
            await ws.send(b"\x00\x02 more")
            await ws.send(json.dumps([1, 2]))
            await ws.send(b"\xff\xfe")
            await ws.close()
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g))
        await s.start()
        await s.say("hello")
        await until(lambda: sink.of("error"))
        assert len(sink.of("notice")) == 1 and "not JSON" in sink.of("notice")[0]["message"]
        assert "closed" in sink.of("error")[0]["message"] or "ended" in sink.of("error")[0]["message"]
        assert sink.states()[-1] == "listening"
        await s.close()


# ---------------------------------------------------------------- failures at the start
@pytest.mark.asyncio
async def test_a_refused_session_carries_googles_reason():
    async with fake_google(setup_reply="deny") as g:
        s, _ = session(cfg(g))
        with pytest.raises(GL.GeminiError, match="Permission denied on resource project"):
            await s.start()


@pytest.mark.asyncio
async def test_an_unexpected_first_answer_and_a_silent_server_are_reported(monkeypatch):
    async with fake_google(setup_reply="wrong") as g:
        s, _ = session(cfg(g))
        with pytest.raises(GL.GeminiError, match="other than setupComplete"):
            await s.start()
    monkeypatch.setattr(GL, "SETUP_TIMEOUT_S", 0.1)
    async with fake_google(setup_reply="silent") as g2:
        s2, _ = session(cfg(g2))
        with pytest.raises(GL.GeminiError, match="did not answer the setup"):
            await s2.start()
        await s2.close()


@pytest.mark.asyncio
async def test_connection_credential_and_configuration_failures_are_plain_errors():
    s, _ = session(cfg(None, endpoint="ws://127.0.0.1:1"))
    with pytest.raises(GL.GeminiError, match="Could not connect to Gemini Live"):
        await s.start()

    async def no_token() -> str:
        raise GL.GeminiError("Could not get Google credentials: DefaultCredentialsError")
    s2, _ = session(cfg(None), tok=no_token)                                      # the secure default address needs a token
    with pytest.raises(GL.GeminiError, match="credentials"):
        await s2.start()
    s3, _ = session(GL.GeminiConfig())
    with pytest.raises(GL.GeminiError, match="project"):
        await s3.start()
    await s3.close()


def test_the_reason_for_a_close_is_extracted():
    class Rcvd:
        reason, code = "Quota exceeded", 1011

    class Err(Exception):
        rcvd = Rcvd()
    assert GL._why(Err()) == "Quota exceeded (code 1011)"
    assert GL._why(ValueError("plain")) == "ValueError: plain"
    assert GL._decode("not json") is None and GL._decode(b"\xff") is None and GL._decode("[1]") is None and GL._decode(b'{"a": 1}') == {"a": 1}


@pytest.mark.asyncio
async def test_text_parts_without_a_transcription_become_the_reply_and_wait_and_close_are_safe():
    async def script(ws, msg, g):
        if "text" in (msg.get("realtimeInput") or {}):
            await ws.send(json.dumps({"serverContent": {"modelTurn": {"parts": [{"text": "Plain text answer."}]}}}))
            await ws.send(json.dumps({"serverContent": {"turnComplete": True}}))
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g))
        await s.wait()                                                           # nothing started yet
        await s.start()
        await s.say("q")
        await until(lambda: sink.of("metrics"))
        assert [e for e in sink.of("assistant") if e.get("final")][0]["text"] == "Plain text answer." and sink.of("metrics")[0]["total_ms"] is not None
        s.ws.close = _boom                                                        # closing a connection that is already gone must not raise
        await s.close()
        await s.wait()


async def _boom(*a, **k):
    raise OSError("already closed")


@pytest.mark.asyncio
async def test_an_abruptly_dropped_connection_is_reported():
    async def script(ws, msg, g):
        if "text" in (msg.get("realtimeInput") or {}):
            ws.transport.abort()
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g))
        await s.start()
        await s.say("hi")
        await until(lambda: sink.of("error"))
        assert "connection ended" in sink.of("error")[0]["message"] or "closed" in sink.of("error")[0]["message"]
        await s.close()


@pytest.mark.asyncio
async def test_closing_from_inside_the_reader_does_not_wait_for_itself():
    async def script(ws, msg, g):
        if "text" in (msg.get("realtimeInput") or {}):
            await ws.send(json.dumps({"usageMetadata": {"promptTokensDetails": [{"modality": "AUDIO", "tokenCount": 9_000_000}]}}))
            await asyncio.sleep(0.2)
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g, cost_cap_usd=0.5))
        await s.start()
        await s.say("x")
        await until(lambda: s.closed)
        await s.wait()


@pytest.mark.asyncio
async def test_nothing_is_sent_before_the_start():
    s, sink = session(cfg(None))
    await s.say("too early")                                                     # no connection yet: dropped quietly
    await s.commit()
    assert sink.of("transcript")[0]["text"] == "too early"


@pytest.mark.asyncio
async def test_the_oauth_token_is_sent_as_a_bearer_header_to_a_secure_address():
    seen = []
    async with fake_google() as g:
        async def spy(url, tok):
            seen.append((url, tok))
            return await GL.default_connect(g.url + GL.PATH, tok)                 # the test server speaks plain ws; the address the session asked for was wss
        sink = Sink()
        s = GL.GeminiLiveSession(GL.GeminiConfig(project="my-proj", location="europe-west4"), sink.send_json, sink.send_audio, token, spy)
        await s.start()
        assert seen == [("wss://europe-west4-aiplatform.googleapis.com" + GL.PATH, "TOKEN-123")]
        assert g.headers["authorization"] == "Bearer TOKEN-123"
        await s.close()


@pytest.mark.asyncio
async def test_idle_avatar_video_after_an_answer_is_forwarded_but_never_marked_as_speaking():
    async def script(ws, msg, g):
        if "text" in (msg.get("realtimeInput") or {}):
            await ws.send(json.dumps({"serverContent": {"outputTranscription": {"text": "Hi."}, "modelTurn": {"parts": [{"inlineData": {"mimeType": "video/mp4", "data": "QUJD"}}]}}}))
            await ws.send(json.dumps({"serverContent": {"turnComplete": True}}))
            await asyncio.sleep(0.05)
            await ws.send(json.dumps({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "video/mp4", "data": "REVG"}}]}}}))   # the avatar keeps moving while idle
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g, avatar=True, avatar_name="Ben"))
        await s.start()
        await s.say("hi")
        await until(lambda: len(sink.of("video")) == 2)
        assert sink.states()[-1] == "listening" and sink.states().count("speaking") == 1
        await s.close()


@pytest.mark.asyncio
async def test_an_idle_avatar_session_is_ended_by_the_streamed_video_cost():
    prices = json.loads(json.dumps(LC.DEFAULT_PRICES))
    prices["rates"]["video_out_tokens_per_s"] = 1_000_000.0                      # make one second of video exceed the budget
    async def script(ws, msg, g):
        if "text" in (msg.get("realtimeInput") or {}):
            await ws.send(json.dumps({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "video/mp4", "data": "QUJD"}}]}}}))
    async with fake_google(script=script) as g:
        c = cfg(g, avatar=True, avatar_name="Ben", cost_cap_usd=0.05)
        sink = Sink()
        s = GL.GeminiLiveSession(c, sink.send_json, sink.send_audio, token, GL.default_connect, None, meter=LC.CostMeter(prices, "cloud", 0.05, avatar=True))
        await s.start()
        assert s.meter.snapshot()["cost_usd"] == 0.0
        await s.say("hi")
        await until(lambda: s.closed, timeout=6)
        err = sink.of("error")[0]["message"]
        assert "reached its $0.05 budget" in err and "estimated" in err




# ---------------------------------------------------------------- multi-turn with an interruption in the middle (the barge-in bug)
def _frame(o):
    return json.dumps(o).encode()                                                   # Google sends every message as a binary frame holding JSON


def _audio(n=1):
    return [_frame({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": pcm_b64()}}]}}})] * n


async def barge_script(ws, msg, g):
    """Replays what the real service sent around a barge-in (logged 2026-11-23): the answer's audio flows, then `voiceActivity` + `interrupted` and IMMEDIATELY
    `turnComplete` with usage (before the person has finished speaking), then the new answer, then a typed third turn."""
    t = (msg.get("realtimeInput") or {}).get("text")
    if t in ("q1", "q2", "q3"):
        for f in _audio(2):
            await ws.send(f)
        if t == "q1":
            return
        await ws.send(_frame({"serverContent": {"outputTranscription": {"text": f"answer to {t}. "}}}))
        await ws.send(_frame({"serverContent": {"turnComplete": True}, "usageMetadata": {"promptTokensDetails": [{"modality": "TEXT", "tokenCount": 10}], "candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 40}]}}))
    elif t == "interrupt-me":
        await ws.send(_frame({"voiceActivity": {"type": "ACTIVITY_START", "audioOffset": "0.2s"}}))
        await ws.send(_frame({"serverContent": {"interrupted": True}}))
        await ws.send(_frame({"serverContent": {"turnComplete": True}, "usageMetadata": {"promptTokensDetails": [{"modality": "TEXT", "tokenCount": 5}]}}))


@pytest.mark.asyncio
async def test_voice_and_video_keep_flowing_for_the_turns_after_an_interruption():
    async with fake_google(script=barge_script) as g:
        s, sink = session(cfg(g, avatar=True, avatar_name="Ben"))
        await s.start()
        await s.say("q1")                                                            # turn 1: audio flows
        await until(lambda: len(sink.audio) == 2)
        await s.say("interrupt-me")                                                  # the person talks over it: interrupted, then turnComplete straight away
        await until(lambda: len(sink.of("clear")) == 1 and len(sink.of("metrics")) == 1)
        before = len(sink.audio)
        await s.say("q2")                                                            # turn 2 must be heard and seen
        await until(lambda: len(sink.of("metrics")) == 2)
        assert len(sink.audio) == before + 2 and s._muted is False and sink.states()[-1] == "listening"
        await s.say("q3")                                                            # turn 3, typed
        await until(lambda: len(sink.of("metrics")) == 3)
        assert len(sink.audio) == before + 4 and len(sink.of("clear")) == 1          # one clear, for the one interruption
        assert [e["text"] for e in sink.of("assistant") if e.get("final")] == ["answer to q2. ", "answer to q3. "]
        assert [m["turn"] for m in sink.of("metrics")] == [1, 2, 3] and "speaking" in sink.states()[-6:]
        await s.close()


@pytest.mark.asyncio
async def test_pressing_interrupt_with_no_answer_in_progress_does_not_silence_the_next_answer():
    async with fake_google(script=barge_script) as g:
        s, sink = session(cfg(g))
        await s.start()
        await s.say("q2")                                                            # a complete answer, already delivered
        await until(lambda: len(sink.of("metrics")) == 1)
        await s.interrupt()                                                          # the Interrupt button while the browser is still playing it out
        assert s._muted is False                                                     # nothing was in progress on the server: nothing to drop
        sent = len(sink.audio)
        await s.say("q3")
        await until(lambda: len(sink.of("metrics")) == 2)
        assert len(sink.audio) == sent + 2                                           # (this answer's voice used to be dropped; only its text arrived)
        await s.close()


@pytest.mark.asyncio
async def test_interrupt_mid_answer_drops_the_rest_of_that_answer_but_the_next_one_plays_even_without_googles_interrupted():
    from tests.test_live import quiet, tone
    gate = asyncio.Event()

    async def script(ws, msg, g):
        rt = msg.get("realtimeInput") or {}
        if rt.get("text") == "long":
            for f in _audio(1):
                await ws.send(f)
            await gate.wait()
            for f in _audio(2):                                                      # still generating after the button was pressed
                await ws.send(f)
            await ws.send(_frame({"serverContent": {"turnComplete": True}}))
        elif "audio" in rt and not getattr(g, "sent2", False) and g.audio_chunks > 3:  # the person's next utterance is answered with NO `interrupted` event first
            g.sent2 = True
            for f in _audio(2):
                await ws.send(f)
            await ws.send(_frame({"serverContent": {"turnComplete": True}}))
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g))
        await s.start()
        await s.say("long")
        await until(lambda: len(sink.audio) == 1)
        await s.interrupt()                                                          # button pressed mid-answer: the rest of this answer is dropped
        assert s._muted is True
        gate.set()
        await asyncio.sleep(0.2)
        assert len(sink.audio) == 1                                                  # its remaining audio never reached the browser
        data = quiet(10) + tone(30) + quiet(25)
        for i in range(0, len(data), 640):
            await s.audio(data[i:i + 640])
        await until(lambda: len(sink.audio) == 3, timeout=6)                         # the new utterance's answer is heard: speaking again cleared the mute
        assert s._muted is False
        await s.close()


@pytest.mark.asyncio
async def test_avatar_video_is_never_dropped_by_the_interrupt_button():
    async def script(ws, msg, g):
        if (msg.get("realtimeInput") or {}).get("text") == "go":
            for i in range(6):
                await ws.send(_frame({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "video/mp4", "data": f"QUJD{i}"}}]}}}))
                await asyncio.sleep(0.03)
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g, avatar=True, avatar_name="Ben"))
        await s.start()
        await s.say("go")
        await until(lambda: len(sink.of("video")) >= 2)
        await s.interrupt()                                                          # the continuous stream must keep every fragment (its init segment came once)
        await until(lambda: len(sink.of("video")) == 6)
        assert [v["data"] for v in sink.of("video")] == [f"QUJD{i}" for i in range(6)]
        await s.close()


@pytest.mark.asyncio
async def test_a_turn_that_google_ends_without_replying_is_reported_so_it_does_not_look_broken():
    """Seen on the real service after a barge-in: the new utterance is transcribed and the turn completes with no text and no audio."""
    async def script(ws, msg, g):
        t = (msg.get("realtimeInput") or {}).get("text")
        if t == "empty":
            await ws.send(_frame({"serverContent": {"inputTranscription": {"text": "hello how is the weather"}}}))
            await ws.send(_frame({"serverContent": {"turnComplete": True}}))
        elif t == "answered":
            for f in _audio(1):
                await ws.send(f)
            await ws.send(_frame({"serverContent": {"turnComplete": True}}))
        elif t == "text only":
            await ws.send(_frame({"serverContent": {"modelTurn": {"parts": [{"text": "plain words"}]}}}))
            await ws.send(_frame({"serverContent": {"turnComplete": True}}))
        elif t == "cut":
            await ws.send(_frame({"serverContent": {"interrupted": True}}))
            await ws.send(_frame({"serverContent": {"turnComplete": True}}))
    async with fake_google(script=script) as g:
        s, sink = session(cfg(g))
        await s.start()
        await s.say("empty")
        await until(lambda: len(sink.of("metrics")) == 1)
        assert len(sink.of("notice")) == 1 and "without replying" in sink.of("notice")[0]["message"] and sink.states()[-1] == "listening"
        await s.say("answered")                                                      # a real answer: no notice
        await until(lambda: len(sink.of("metrics")) == 2)
        await s.say("text only")                                                     # a text-only reply counts too
        await until(lambda: len(sink.of("metrics")) == 3)
        await s.say("cut")                                                           # the turn that an interruption ends is not an empty reply
        await until(lambda: len(sink.of("metrics")) == 4)
        assert len(sink.of("notice")) == 1
        await s.close()
