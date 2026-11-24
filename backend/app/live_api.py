"""/api/live/*: local voice install, settings, a latency self-test, and the live conversation itself (a WebSocket).

WebSocket `/api/live/ws` - the browser sends 16 kHz mono 16-bit PCM frames as binary messages and JSON control messages as text:
    {"type": "start", "provider": "ollama", "model": "qwen3:8b", "agent": "", "voice": "af_heart", "speed": 1.0, "hangover_ms": 350, "barge_in": true}
    {"type": "interrupt"} {"type": "commit"} {"type": "say", "text": "..."} {"type": "stop"}
and gets JSON events (state, ready, transcript, assistant, clear, metrics, error) plus 24 kHz mono 16-bit PCM as binary messages.
Only pages from Agent Studio's own origins may open it (a WebSocket is not covered by CORS, so the Origin header is checked here).
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
import uuid
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from . import gemini_live as GL
from . import live_cost as LC
from . import live_profile as LP
from . import voice_spec as VS
from .live import FRAME, IN_RATE, LiveConfig, LiveSession, VadConfig, resample_24k_to_16k
from .live_engine import EngineError, VoiceEngine
from .live_llm import AgentLlm, LlmError, make_llm
from .voice_local import Installer, SetupConflict

DEFAULTS = dict(live_provider="ollama", live_model="", live_voice=VS.DEFAULT_VOICE, live_speed=1.0, live_hangover_ms=350, live_barge_in=True,
                live_avatar=False, live_agent="", live_engine="local", live_gemini_provider="vertex_ai", live_gemini_model=GL.DEFAULT_MODEL, live_gemini_voice="Puck",
                live_gemini_language="en-US", live_gemini_avatar_name="", live_gemini_location="", live_gemini_endpoint="", live_gemini_max_minutes=10.0,
                live_gemini_cost_cap_usd=1.0, live_gemini_video_bitrate=0, live_gemini_audio_bitrate=0)
GEMINI = dict(connect=GL.default_connect, token_source=GL.make_token_source)           # replaced in tests
PRICE_TRANSPORT: dict = {"transport": None}                                                 # a test sets an httpx mock transport here


class InstallIn(BaseModel):
    accept_licenses: bool = False
    device: str = "auto"
    stt_model: str | None = None


class SettingsIn(BaseModel):
    live_provider: str | None = Field(default=None, max_length=80)
    live_model: str | None = Field(default=None, max_length=120)
    live_voice: str | None = Field(default=None, max_length=40)
    live_speed: float | None = Field(default=None, ge=0.6, le=1.6)
    live_hangover_ms: int | None = Field(default=None, ge=150, le=1500)
    live_barge_in: bool | None = None
    live_avatar: bool | None = None
    live_agent: str | None = Field(default=None, max_length=80)
    live_engine: str | None = Field(default=None, pattern="^(local|gemini)$")
    live_gemini_provider: str | None = Field(default=None, max_length=80)
    live_gemini_model: str | None = Field(default=None, max_length=80)
    live_gemini_voice: str | None = Field(default=None, max_length=40)
    live_gemini_language: str | None = Field(default=None, max_length=12)
    live_gemini_avatar_name: str | None = Field(default=None, max_length=80)
    live_gemini_location: str | None = Field(default=None, max_length=40)
    live_gemini_endpoint: str | None = Field(default=None, max_length=200, pattern="^(|wss?://.+)$")
    live_gemini_max_minutes: float | None = Field(default=None, ge=1, le=60)
    live_gemini_cost_cap_usd: float | None = Field(default=None, ge=0.05, le=100)
    live_gemini_video_bitrate: int | None = Field(default=None, ge=0, le=20_000_000)
    live_gemini_audio_bitrate: int | None = Field(default=None, ge=0, le=1_000_000)


class SelfTestIn(BaseModel):
    text: str = Field(default="Hello! Tell me one interesting fact about the moon.", max_length=300)
    provider: str | None = None
    model: str | None = None
    voice: str | None = None
    runs: int = Field(default=2, ge=1, le=5)


def read_settings(db: Any) -> dict:
    out: dict = {}
    for k, d in DEFAULTS.items():
        raw = db.get_setting(k, None)
        if raw is None or raw == "":
            out[k] = d
        elif isinstance(d, bool):
            out[k] = str(raw) in ("1", "true", "True")
        elif isinstance(d, int):
            out[k] = int(float(raw))
        elif isinstance(d, float):
            out[k] = float(raw)
        else:
            out[k] = str(raw)
    return out


def write_settings(db: Any, patch: dict) -> dict:
    for k, v in patch.items():
        if k in DEFAULTS and v is not None:
            db.set_setting(k, ("1" if v else "0") if isinstance(v, bool) else str(v))
    return read_settings(db)


def session_config(s: dict, agent_instruction: str = "") -> LiveConfig:
    return LiveConfig(voice=s["live_voice"], speed=float(s["live_speed"]), instruction=agent_instruction, barge_in=bool(s["live_barge_in"]),
                      vad=VadConfig(hangover_ms=int(s["live_hangover_ms"])))


def build_llm(state: Any, provider: str, model: str):
    """The model for a live session. Provider/model fall back to the saved live settings, then to the provider's default."""
    if not provider:
        raise LlmError("choose a model for live mode first")
    return make_llm(state.providers, state.guard, provider, model or None)


def agent_instruction(state: Any, agent_id: str) -> str:
    if not agent_id:
        return ""
    a = state.agents.get(agent_id)
    return (a.instruction or "") if a else ""


def gemini_config(state: Any, s: dict, avatar: bool, instruction: str = "", budget: float = 0.0) -> tuple[GL.GeminiConfig, Any]:
    """The Gemini Live settings for one session, with the project and location read from the chosen Vertex provider (credentials stay there)."""
    p = state.providers.get(s["live_gemini_provider"])
    location = s["live_gemini_location"] or (getattr(p, "vertex_location", "") if p else "") or GL.DEFAULT_LOCATION
    if location == "global":
        location = GL.DEFAULT_LOCATION                     # Gemini Live is served from regions, not "global"
    cfg = GL.GeminiConfig(project=(getattr(p, "vertex_project", "") or "") if p else "", location=location, model=s["live_gemini_model"], voice=s["live_gemini_voice"],
                          language=s["live_gemini_language"], avatar=avatar, avatar_name=s["live_gemini_avatar_name"], endpoint=s["live_gemini_endpoint"],
                          video_bitrate_bps=int(s["live_gemini_video_bitrate"]), audio_bitrate_bps=int(s["live_gemini_audio_bitrate"]),
                          max_minutes=float(s["live_gemini_max_minutes"]), cost_cap_usd=float(budget or s["live_gemini_cost_cap_usd"]), instruction=instruction,
                          vad=VadConfig(hangover_ms=int(s["live_hangover_ms"])))
    return cfg, p


def agent_turn(state: Any, agent: Any):
    """`run(text)` = one normal Agent Studio chat turn with this agent (its tools, knowledge, memory, guardrails), all in one chat per live session."""
    from .apiaccess.api import pick_provider
    sid = uuid.uuid4().hex

    async def run(request: str) -> dict:
        return await state.chat.send(pick_provider(state, agent), None, request, sid, None, agent.id, None)
    return run


def agent_runner(state: Any, agent_id: str):
    """The ask_agent tool for Gemini: one call runs a normal Agent Studio turn with the chosen agent."""
    a = state.agents.get(agent_id) if agent_id else None
    if a is None:
        return None
    run = agent_turn(state, a)

    async def ask(request: str) -> str:
        return (await run(request)).get("reply") or ""
    return ask


def resolve_start(st: Any, cfg: dict, msg: dict) -> tuple[dict, dict, Any, bool]:
    """What a `start` message means: (the Live settings for this session, the agent's profile, the agent, avatar?). An agent's enabled Live profile decides the engine,
    mode, voice, language, avatar and budget; without one the page's choice (the message) and the global settings apply. Raises ValueError with a plain message."""
    agent_id = str(msg.get("agent") or cfg["live_agent"] or "")
    agent = st.agents.get(agent_id) if agent_id else None
    if agent_id and agent is None:
        raise ValueError(f"The agent '{agent_id}' does not exist.")
    s, prof = LP.apply(cfg, agent) if agent else (dict(cfg), {})
    if prof and not prof["enabled"]:
        raise ValueError(f"Live mode is switched off for the agent '{agent.name}'. Switch it on in the agent's Live section.")
    if prof:
        return s, prof, agent, prof["mode"] == "avatar"
    if msg.get("engine") in ("local", "gemini"):
        s["live_engine"] = msg["engine"]
    return s, {}, agent, bool(msg.get("avatar"))


def summary(turns: list[dict]) -> dict:
    out: dict = {}
    for k in ("endpoint_ms", "stt_ms", "llm_first_token_ms", "first_chunk_ms", "tts_first_ms", "ttfa_ms", "user_perceived_ms", "total_ms"):
        v = [t[k] for t in turns if isinstance(t.get(k), (int, float))]
        if v:
            out[k] = round(statistics.median(v))
    return out


def register(app: FastAPI, allowed_origins: list[str] | None = None, engine: VoiceEngine | None = None,
             installer: Installer | None = None) -> None:
    inst = installer or Installer()
    eng = engine or VoiceEngine()
    app.state.live_installer, app.state.live_engine = inst, eng
    origins = list(allowed_origins or [])
    r = APIRouter(prefix="/api/live", tags=["live"])
    LC.ensure_history(app.state.s.db)

    def need_ready() -> None:
        if not VS.is_ready():
            raise HTTPException(412, "The local voice engines are not installed yet: open Settings > Voice (or the Live page) and click Install.")

    @r.get("/status")
    def status():
        db = app.state.s.db
        return dict(local=inst.status(), engine=eng.status(), settings=read_settings(db))

    @r.put("/settings")
    def put_settings(body: SettingsIn):
        return write_settings(app.state.s.db, body.model_dump(exclude_unset=True))

    @r.post("/install")
    def install(body: InstallIn):
        try:
            return inst.start(body.accept_licenses, body.device, body.stt_model)
        except SetupConflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @r.get("/install/progress")
    def progress():
        s = inst.status()
        return {k: s[k] for k in ("state", "ready", "error", "steps", "percent", "progress_text", "log_tail")}

    @r.post("/uninstall")
    def uninstall():
        try:
            eng.close()
            return inst.uninstall()
        except SetupConflict as e:
            raise HTTPException(409, str(e))

    @r.post("/warm")
    async def warm(model: str = "", provider: str = ""):
        """Load the voice engines (and the chosen model) now, so the first live turn is not a cold start."""
        need_ready()
        st = app.state.s
        cfg = read_settings(st.db)
        try:
            llm = build_llm(st, provider or cfg["live_provider"], model or cfg["live_model"])
            info, _ = await asyncio.gather(eng.warm(), llm.warm())
        except (EngineError, LlmError) as e:
            raise HTTPException(502, str(e))
        return dict(engine=info, ready=True)

    @r.get("/voices")
    async def voices():
        return dict(voices=VS.VOICES, default=VS.DEFAULT_VOICE)

    @r.post("/selftest")
    async def selftest(body: SelfTestIn):
        """Say a sentence with the voice, feed that audio back as the microphone, and time every stage of the reply."""
        need_ready()
        st = app.state.s
        cfg = read_settings(st.db)
        try:
            llm = build_llm(st, body.provider or cfg["live_provider"], body.model if body.model is not None else cfg["live_model"])
            await asyncio.gather(eng.warm(), llm.warm())
            stream = await eng.tts(body.text, body.voice or cfg["live_voice"], 1.0)
            speech = b"".join([c async for c in stream])
        except (EngineError, LlmError) as e:
            raise HTTPException(502, str(e))
        mic = resample_24k_to_16k(speech)
        silence = bytes(2 * IN_RATE * 2)                          # two seconds of quiet end the "utterance"
        turns: list[dict] = []
        sent = dict(json=[], audio=0)

        async def send_json(m: dict) -> None:
            sent["json"].append(m)

        async def send_audio(b: bytes) -> None:
            sent["audio"] += len(b)

        sess = LiveSession(eng, llm, send_json, send_audio, session_config({**cfg, "live_voice": body.voice or cfg["live_voice"]}))
        await sess.start()
        for _ in range(body.runs):
            sent["json"].clear()
            sent["audio"] = 0
            for i in range(0, len(mic + silence), FRAME * 2):
                await sess.audio((mic + silence)[i:i + FRAME * 2])
            await sess.wait()
            heard = next((m["text"] for m in sent["json"] if m.get("type") == "transcript"), "")
            reply = next((m["text"] for m in sent["json"] if m.get("final")), "")
            errors = [m["message"] for m in sent["json"] if m.get("type") == "error"]
            turns.append({**(sess.metrics[-1] if sess.metrics else {}), "heard": heard, "reply": reply, "reply_audio_s": round(sent["audio"] / 2 / 24000, 2),
                          **({"error": errors[0]} if errors else {})})
        await sess.close()
        return dict(said=body.text, turns=turns, median=summary(turns[1:] or turns), first=summary(turns[:1]), engine=eng.status(),
                    local_model=bool(getattr(llm, "local", False)), note="Times are server-side milliseconds; the browser adds its own audio-output delay. "
                    "'ttfa_ms' runs from the detected end of speech to the first audio frame sent; 'user_perceived_ms' adds the end-of-speech wait.")

    @r.get("/prices")
    def get_prices():
        return LC.load_prices(app.state.s.db)

    @r.put("/prices")
    def put_prices(body: dict):
        try:
            return LC.save_prices(app.state.s.db, LC.validate_prices(body))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @r.post("/prices/refresh")
    async def refresh_prices():
        """Read Google's pricing page now and store the Gemini Live prices it lists (with today's date). Nothing changes if the page cannot be read."""
        try:
            return await LC.refresh_prices(app.state.s.db, PRICE_TRANSPORT["transport"])
        except ValueError as e:
            raise HTTPException(502, str(e))

    @r.get("/history")
    def history(limit: int = 50):
        return LC.list_sessions(app.state.s.db, limit)

    @r.get("/gemini/status")
    def gemini_status():
        st = app.state.s
        s = read_settings(st.db)
        cfg, p = gemini_config(st, s, bool(s["live_avatar"]))
        miss = GL.problems(cfg)
        if p is None:
            miss.insert(0, "The provider '" + s["live_gemini_provider"] + "' does not exist.")
        return dict(configured=not miss, problems=miss, provider=s["live_gemini_provider"], project=cfg.project, location=cfg.location, model=cfg.model,
                    auth_mode=getattr(p, "auth_mode", None), voices=GL.VOICES, languages=GL.LANGUAGES, prices=LC.load_prices(st.db), endpoint=cfg.url(),
                    avatar_examples=["Ben"], notes=["Your audio and the answers go to Google Cloud while this engine is on; the local engine keeps everything on this PC.",
                                                    "Billed by Google to your project at its list prices (audio in $3.00, audio out $12.00, avatar video out $1.00 per million tokens); "
                                                    "Agent Studio ends a session at your cost limit using Google's own usage report, which is an estimate.",
                                                    "Credentials live in the Vertex provider's write-only settings; this page never shows them.",
                                                    "With the avatar on, Google streams its video the whole time the session is open, even while nobody is talking: measured at about "
                                                    "$0.15 per minute. Keep sessions short; the cost limit counts that time."])

    @r.post("/gemini/test")
    async def gemini_test():
        """Open a session (setup only, no audio) and close it: tells you whether Google accepts the project, credentials and model."""
        st = app.state.s
        s = read_settings(st.db)
        cfg, p = gemini_config(st, s, False)
        t0 = time.perf_counter()

        async def sj(m): pass

        async def sa(b): pass
        if p is None:
            raise HTTPException(502, "The provider '" + s["live_gemini_provider"] + "' does not exist.")
        sess = GL.GeminiLiveSession(cfg, sj, sa, GEMINI["token_source"](p), GEMINI["connect"])
        try:
            info = await sess.start()
        except GL.GeminiError as e:
            raise HTTPException(502, str(e))
        finally:
            await sess.close()
        return dict(ok=True, ms=round((time.perf_counter() - t0) * 1000), session=info["engine"])

    app.include_router(r)

    @app.websocket("/api/live/ws")
    async def live_ws(ws: WebSocket):
        origin = ws.headers.get("origin")
        if origin is not None and origins and origin not in origins:
            await ws.close(code=1008)                              # a page from another origin: refused before accepting
            return
        await ws.accept()
        st = app.state.s
        sess: Any = None                                           # a LiveSession (local engines) or a GeminiLiveSession (Google)
        starter: asyncio.Task | None = None
        session_id, started_at, info = uuid.uuid4().hex[:12], time.time(), {}

        async def send_json(m: dict) -> None:
            await ws.send_text(json.dumps(m))

        try:
            while True:
                m = await ws.receive()
                if m.get("type") == "websocket.disconnect":
                    break
                if m.get("bytes") is not None:
                    if sess:
                        await sess.audio(m["bytes"])
                    continue
                try:
                    msg = json.loads(m.get("text") or "")
                except ValueError:
                    continue
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "start" and sess is None:
                    cfg = read_settings(st.db)
                    cfg.update({f"live_{k}": msg[k] for k in ("voice", "speed", "hangover_ms", "barge_in") if msg.get(k) is not None})
                    try:
                        s2, prof, agent, avatar = resolve_start(st, cfg, msg)
                    except ValueError as e:
                        await send_json({"type": "error", "message": str(e)})
                        continue
                    prices = LC.load_prices(st.db)
                    instruction = (agent.instruction or "") if agent else ""
                    info = dict(agent=agent.id if agent else "", mode="avatar" if avatar else "voice")
                    if s2["live_engine"] == "gemini":
                        gcfg, prov = gemini_config(st, s2, avatar, instruction, float(prof.get("budget_usd") or 0))
                        if msg.get("voice_name") and not prof:
                            gcfg.voice = str(msg["voice_name"])
                        if prov is None:
                            await send_json({"type": "error", "message": "The provider '" + s2["live_gemini_provider"] + "' does not exist: choose a Vertex provider in the Live settings."})
                            continue
                        info.update(engine="gemini", provider=prov.id, model=gcfg.model, voice=gcfg.voice)
                        meter = LC.CostMeter(prices, "cloud", gcfg.cost_cap_usd, avatar=avatar)
                        sess = GL.GeminiLiveSession(gcfg, send_json, ws.send_bytes, GEMINI["token_source"](prov), GEMINI["connect"], agent_runner(st, agent.id if agent else ""), meter=meter)
                        starter = asyncio.ensure_future(_start(sess, send_json))
                        continue
                    if not VS.is_ready():
                        await send_json({"type": "error", "message": "The local voice engines are not installed yet: open the Live page and click Install."})
                        continue
                    try:
                        if prof.get("agent_turns") and agent is not None:
                            llm = AgentLlm(agent_turn(st, agent))
                            info.update(provider="agent", model=agent.id)
                        else:
                            pid = str(prof.get("provider") or msg.get("provider") or s2["live_provider"])
                            mdl = str(prof.get("model") or msg.get("model") or s2["live_model"]) if prof.get("provider") else str(msg.get("model") or s2["live_model"])
                            llm = build_llm(st, pid, mdl)
                            info.update(provider=pid, model=getattr(llm, "model", mdl))
                    except LlmError as e:
                        await send_json({"type": "error", "message": str(e)})
                        continue
                    lcfg = session_config(s2, instruction)
                    lcfg.budget_usd = float(prof.get("budget_usd") or 0)
                    info.update(engine="local", voice=lcfg.voice)
                    sess = LiveSession(eng, llm, send_json, ws.send_bytes, lcfg, meter=LC.CostMeter(prices, "local", lcfg.budget_usd))
                    starter = asyncio.ensure_future(_start(sess, send_json))
                elif sess is None:
                    continue
                elif kind == "interrupt":
                    await sess.interrupt()
                elif kind == "commit":
                    await sess.commit()
                elif kind == "say" and isinstance(msg.get("text"), str) and msg["text"].strip():
                    await sess.say(msg["text"].strip()[:1000])
                elif kind == "stop":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            if starter and not starter.done():
                starter.cancel()
            if sess:
                if getattr(sess, "ready", False):                    # a session that really started leaves a record (and, for Gemini, a spend-ledger row): written BEFORE closing,
                    snap = sess.meter.snapshot()                     # so a shutdown that cancels the close cannot lose it
                    LC.record_session(st.db, session_id, started_at, info, snap, "budget" if sess.meter.level == "stop" else "user")
                await sess.close()

    async def _start(sess: Any, send_json) -> None:
        try:
            await sess.start()
        except (EngineError, LlmError, GL.GeminiError) as e:
            await send_json({"type": "error", "message": str(e)})
        except Exception as e:  # noqa: BLE001
            await send_json({"type": "error", "message": f"{type(e).__name__}: {str(e)[:200]}"})
