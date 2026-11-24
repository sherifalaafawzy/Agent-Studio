"""Gemini Live (gemini-3.8-live, Google Cloud / Vertex) as the cloud alternative to the local voice pipeline in live mode.

Built from Google's documentation (read 2026-11-23: the model page, the Live API overview and reference, the Gen AI SDK and WebSocket tutorials,
"Configure live avatars", "Configure language and voice", and the Gen AI SDK's own type definitions). What is certain, and what is not:

* Transport: ONE stateful WebSocket to `wss://{location}-aiplatform.googleapis.com/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent`,
  authenticated with an OAuth 2.0 bearer token (service account or application-default credentials - an API key is not accepted there).
  Google's own guidance: clients must not connect directly; an intermediate server holds the credential. That is what this module is - the
  browser talks to Agent Studio, Agent Studio talks to Google, and the credential never leaves the backend.
* The first message is `setup` (model path, `generation_config.response_modalities`, `speech_config`, `avatar_config`, tools, transcription); the
  server answers `setupComplete`. Then `realtimeInput` (16 kHz 16-bit PCM audio, text, `audioStreamEnd`) goes up, and `serverContent` /
  `toolCall` / `usageMetadata` / `goAway` come down. Audio comes back as 24 kHz 16-bit PCM in `modelTurn.parts[].inlineData`.
* Live Avatar = `response_modalities: ["VIDEO"]` plus `avatar_config.avatar_name` (a prebuilt avatar; Google's only documented example is "Ben")
  or a customised avatar (only for allow-listed customers: NOT offered here).
* NOT documented in anything I could read: how the avatar's video (24 FPS MP4) is framed inside the messages, and the list of prebuilt avatar
  names. So video parts are forwarded to the browser exactly as received (with their MIME type) and the avatar name is a free text setting.
  This whole provider has therefore NOT been verified against Google; it is tested against a fake server that follows the documented messages.

The session exposes the same methods as the local `LiveSession` (`start`, `audio`, `interrupt`, `commit`, `say`, `close`) and sends the same
events to the browser, plus `{"type": "video", "mime", "data"}` for avatar video, so the page treats both engines alike.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import live_cost as LC
from .live import FRAME, Vad, VadConfig

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
PATH = "/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent"
DEFAULT_MODEL = "gemini-3.8-live"
DEFAULT_LOCATION = "us-central1"
SETUP_TIMEOUT_S = 20.0

# The 30 prebuilt voices of Gemini Live (Google's "Configure language and voice").
VOICES = ["Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
          "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
          "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat"]
LANGUAGES = ["ar-EG", "bn-BD", "nl-NL", "en-IN", "en-US", "fr-FR", "de-DE", "hi-IN", "id-ID", "it-IT", "ja-JP", "ko-KR", "mr-IN", "pl-PL", "pt-BR", "ro-RO",
             "ru-RU", "es-US", "ta-IN", "te-IN", "th-TH", "tr-TR", "uk-UA", "vi-VN"]
AGENT_TOOL = "ask_agent"
INSTRUCTION = ("You are talking with a person by voice, in real time. Keep answers short and natural. When a request needs information or actions you do not have, "
               f"call the {AGENT_TOOL} tool with the request in plain words: briefly say you are checking, then report what it returns. If a tool returns no results, "
               "say so instead of retrying with variations. Never call more than two tools in a row without speaking.")


class GeminiError(Exception):
    """The cloud session could not be set up or broke. The message is safe to show."""


@dataclass
class GeminiConfig:
    project: str = ""
    location: str = DEFAULT_LOCATION
    model: str = DEFAULT_MODEL
    voice: str = "Puck"
    language: str = "en-US"
    avatar: bool = False                  # Live Avatar: response_modalities VIDEO
    avatar_name: str = ""
    video_bitrate_bps: int = 0            # AvatarConfig.video_bitrate_bps / audio_bitrate_bps exist in Google's SDK types; 0 = leave to Google
    audio_bitrate_bps: int = 0
    endpoint: str = ""                    # override of the host, for a proxy or a test server (e.g. ws://127.0.0.1:9000)
    max_minutes: float = 10.0
    cost_cap_usd: float = 1.0
    instruction: str = ""
    vad: VadConfig = field(default_factory=VadConfig)

    def model_path(self) -> str:
        return f"projects/{self.project}/locations/{self.location}/publishers/google/models/{self.model}"

    def url(self) -> str:
        if self.endpoint:
            return self.endpoint.rstrip("/") + PATH
        return f"wss://{self.location}-aiplatform.googleapis.com{PATH}"


def problems(cfg: GeminiConfig) -> list[str]:
    """What is missing before a session can start, in words the person can act on."""
    out = []
    if not cfg.project:
        out.append("Set the Google Cloud project: open Providers > Google Vertex AI and fill in the project.")
    if cfg.location in ("", "global"):
        out.append("Gemini Live needs a regional location such as us-central1 (the 'global' location does not serve it).")
    if cfg.avatar and not cfg.avatar_name:
        out.append("Choose an avatar name (Google documents prebuilt avatars; its example is 'Ben').")
    if cfg.voice not in VOICES:
        out.append(f"'{cfg.voice}' is not one of Gemini Live's 30 prebuilt voices.")
    return out


def build_setup(cfg: GeminiConfig, tools: bool) -> dict:
    """The first message of the session, as documented (snake_case keys, as in Google's own samples)."""
    gen: dict[str, Any] = {"response_modalities": ["VIDEO" if cfg.avatar else "AUDIO"],
                           "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": cfg.voice}}, "language_code": cfg.language}}
    setup: dict[str, Any] = {"model": cfg.model_path(), "generation_config": gen,
                             "system_instruction": {"parts": [{"text": ((cfg.instruction + "\n\n") if cfg.instruction else "") + INSTRUCTION}]},
                             "input_audio_transcription": {}, "output_audio_transcription": {}}
    if cfg.avatar:
        av: dict[str, Any] = {"avatar_name": cfg.avatar_name}
        if cfg.video_bitrate_bps:
            av["video_bitrate_bps"] = cfg.video_bitrate_bps
        if cfg.audio_bitrate_bps:
            av["audio_bitrate_bps"] = cfg.audio_bitrate_bps
        setup["avatar_config"] = av
    if tools:
        setup["tools"] = [{"function_declarations": [{
            "name": AGENT_TOOL, "description": "Ask the user's Agent Studio agent to do something or look something up, and get its answer as text.",
            "parameters": {"type": "OBJECT", "properties": {"request": {"type": "STRING", "description": "What to do or find out, in plain words."}}, "required": ["request"]}}]}]
    return setup


def pick(d: Any, *names: str) -> Any:
    """Google's JSON may use camelCase or snake_case for the same field; take whichever is present."""
    if isinstance(d, dict):
        for n in names:
            if n in d:
                return d[n]
    return None


def make_token_source(provider: Any) -> Callable[[], Awaitable[str]]:  # pragma: no cover - needs real Google credentials; every test injects its own token source
    """An OAuth access token for the configured Vertex provider (service-account JSON, service-account file, or application-default credentials)."""
    def fetch() -> str:  # pragma: no cover - needs real Google credentials; the token source is injected everywhere it is tested
        import google.auth
        import google.auth.transport.requests as tr
        from google.oauth2 import service_account
        mode = getattr(provider, "auth_mode", "adc")
        if mode == "api_key":
            raise GeminiError("Gemini Live on Google Cloud needs OAuth credentials (a service account or application-default login), not an API key.")
        if mode == "service_account_json" and provider.vertex_credentials_json:
            creds = service_account.Credentials.from_service_account_info(json.loads(provider.vertex_credentials_json), scopes=SCOPES)
        elif mode == "service_account_file" and provider.vertex_credentials_path:
            creds = service_account.Credentials.from_service_account_file(provider.vertex_credentials_path, scopes=SCOPES)
        else:
            creds, _ = google.auth.default(scopes=SCOPES)
        creds.refresh(tr.Request())
        return creds.token

    async def token() -> str:  # pragma: no cover
        try:
            return await asyncio.to_thread(fetch)
        except GeminiError:
            raise
        except Exception as e:
            raise GeminiError(f"Could not get Google credentials: {type(e).__name__}: {str(e)[:200]}") from e
    return token


async def default_connect(url: str, token: str):
    import websockets
    return await websockets.connect(url, additional_headers={"Authorization": f"Bearer {token}"} if token else None, max_size=None, open_timeout=15)


class GeminiLiveSession:
    def __init__(self, cfg: GeminiConfig, send_json: Callable[[dict], Awaitable[None]], send_audio: Callable[[bytes], Awaitable[None]],
                 token: Callable[[], Awaitable[str]], connect: Callable[[str, str], Awaitable[Any]] = default_connect,
                 run_agent: Callable[[str], Awaitable[str]] | None = None, clock: Callable[[], float] = time.perf_counter, meter: LC.CostMeter | None = None):
        self.cfg, self.send_json, self.send_audio, self.token, self.connect, self.run_agent, self.clock = cfg, send_json, send_audio, token, connect, run_agent, clock
        self.meter = meter or LC.CostMeter(LC.DEFAULT_PRICES, "cloud", cfg.cost_cap_usd, clock, cfg.avatar)
        self.ready = False
        self.ws: Any = None
        self.vad = Vad(cfg.vad)                       # only for the "hearing" light and the timings: Google decides when the person finished
        self.state = "idle"
        self.metrics: list[dict] = []
        self.turn_no = 0
        self.closed = False
        self._reader: asyncio.Task | None = None
        self._limit: asyncio.Task | None = None
        self._tools: dict[str, asyncio.Task] = {}
        self._t_end: float | None = None
        self._m: dict[str, Any] = {}
        self._user, self._reply, self._typed = "", "", ""
        self._muted = False                           # after "Interrupt": drop the rest of this answer
        self._cut = False                             # the turn that is about to complete was ended by an interruption
        self._produced = False                        # this turn has produced a reply (text or voice): Google sometimes ends a turn with none
        self._active = False                          # an answer is being given (the avatar's idle video must not flip the screen to "speaking")
        self._watch: asyncio.Task | None = None
        self._noticed = False

    # ------------------------------------------------------------ helpers
    async def _state(self, s: str) -> None:
        if s != self.state:
            self.state = s
            await self.send_json({"type": "state", "state": s})

    async def _put(self, obj: dict) -> None:
        if self.ws is None or self.closed:
            return
        await self.ws.send(json.dumps(obj, separators=(",", ":")))

    # ------------------------------------------------------------ session
    async def start(self) -> dict:
        bad = problems(self.cfg)
        if bad:
            raise GeminiError(" ".join(bad))
        await self._state("loading")
        tok = "" if self.cfg.url().startswith("ws://") else await self.token()       # an OAuth token is never sent in plaintext: ws:// (a local test server) gets none
        try:
            self.ws = await self.connect(self.cfg.url(), tok)
        except Exception as e:  # noqa: BLE001
            raise GeminiError(f"Could not connect to Gemini Live: {type(e).__name__}: {str(e)[:200]}") from e
        await self._put({"setup": build_setup(self.cfg, self.run_agent is not None)})
        try:
            first = await asyncio.wait_for(self.ws.recv(), SETUP_TIMEOUT_S)
        except asyncio.TimeoutError as e:
            raise GeminiError("Gemini Live did not answer the setup message in time.") from e
        except Exception as e:  # noqa: BLE001 - the server closes with a reason (permission, quota, unknown model ...)
            raise GeminiError(f"Gemini Live refused the session: {_why(e)}") from e
        msg = _decode(first)
        if msg is None or pick(msg, "setupComplete", "setup_complete") is None:
            raise GeminiError("Gemini Live answered with something other than setupComplete: " + str(first)[:200])
        self._reader = asyncio.ensure_future(self._read())
        self._limit = asyncio.ensure_future(self._limit_minutes())
        self._watch = asyncio.ensure_future(LC.watch(self))                 # the live usage meter (once a second) and the budget
        self.ready = True
        await self._state("listening")
        info = {"type": "ready", "engine": {"provider": "gemini", "model": self.cfg.model, "avatar": self.cfg.avatar, "voice": self.cfg.voice,
                                            "session_id": pick(pick(msg, "setupComplete", "setup_complete"), "sessionId", "session_id")},
                "voice": self.cfg.voice, "local_model": False, "cloud": True, "avatar": self.cfg.avatar}
        await self.send_json(info)
        return info

    async def _limit_minutes(self) -> None:
        await asyncio.sleep(self.cfg.max_minutes * 60)
        await self.send_json({"type": "error", "message": f"The Gemini Live session reached your {self.cfg.max_minutes:g}-minute limit and was ended."})
        await self.close()

    # ------------------------------------------------------------ from the browser
    async def audio(self, pcm: bytes) -> None:
        if self.ws is None or self.state in ("idle", "loading") or self.closed:
            return
        for ev in self.vad.feed(pcm):
            if ev[0] == "start":
                self._muted = False                                               # the person is speaking: whatever Google answers next is a new answer
                await self._state("hearing")
            elif ev[0] == "discard":
                await self._state("listening")
            else:
                self._t_end, self._m, self._active, self._muted, self._produced = self.clock(), dict(endpoint_ms=self.cfg.vad.hangover_ms), True, False, False
                await self._state("thinking")
        self.meter.add_audio_in(len(pcm) / 32000)                                  # 16 kHz, 16-bit, mono
        await self._put({"realtimeInput": {"audio": {"data": base64.b64encode(pcm).decode("ascii"), "mimeType": "audio/pcm;rate=16000"}}})

    async def say(self, text: str) -> None:
        self._t_end, self._m, self._muted, self._typed, self._active, self._produced = self.clock(), dict(endpoint_ms=0), False, text.strip(), True, False
        await self.send_json({"type": "transcript", "role": "user", "text": text})
        await self._state("thinking")
        await self._put({"realtimeInput": {"text": text}})

    async def commit(self) -> None:
        """Push-to-talk released: tell Google the audio stream ended."""
        await self._put({"realtimeInput": {"audioStreamEnd": True}})

    async def interrupt(self) -> None:
        """The Interrupt button: stop playing this answer now (Google has no 'stop' message; talking is what interrupts it on its side).
        The rest of the answer's AUDIO is dropped, but only while an answer is actually in progress: a flag set with nothing to drop used to stay set and silently
        dropped the NEXT answer (the text still arrived; the voice did not). The avatar's video is never dropped - it is one continuous stream."""
        self._muted = self._active
        await self.send_json({"type": "clear"})
        await self._state("listening")

    async def wait(self) -> None:
        if self._reader:
            try:
                await self._reader
            except asyncio.CancelledError:
                pass

    async def close(self) -> None:
        self.closed = True
        for t in [self._limit, self._watch, *self._tools.values()]:
            if t and not t.done() and t is not asyncio.current_task():
                t.cancel()
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001 - already closed
                pass
        if self._reader and not self._reader.done() and self._reader is not asyncio.current_task():
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # ------------------------------------------------------------ from Google
    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                await self._on(raw)
        except Exception as e:  # noqa: BLE001 - the connection broke
            if not self.closed:
                await self.send_json({"type": "error", "message": f"The Gemini Live connection ended: {_why(e)}"})
        else:
            if not self.closed:
                await self.send_json({"type": "error", "message": "The Gemini Live connection was closed by Google."})
        await self._state("listening")

    async def _on(self, raw: Any) -> None:
        msg = _decode(raw)
        if msg is None:
            if not self._noticed:
                self._noticed = True
                await self.send_json({"type": "notice", "message": "Gemini Live sent data that is not JSON; Agent Studio cannot decode it (how the avatar video is framed is not documented)."})
            return
        usage = pick(msg, "usageMetadata", "usage_metadata")
        if usage is not None:
            self.meter.report(usage)                                                # Google's own numbers replace the estimate for what they cover
            await LC.enforce(self)
            if self.closed:
                return
        sc = pick(msg, "serverContent", "server_content")
        if sc is not None:
            await self._content(sc)
        tc = pick(msg, "toolCall", "tool_call")
        if tc is not None:
            for call in pick(tc, "functionCalls", "function_calls") or []:
                self._tools[str(call.get("id"))] = asyncio.ensure_future(self._tool(call))
        cancel = pick(msg, "toolCallCancellation", "tool_call_cancellation")
        if cancel is not None:
            for i in pick(cancel, "ids") or []:
                t = self._tools.pop(str(i), None)
                if t:
                    t.cancel()
        if pick(msg, "goAway", "go_away") is not None:
            await self.send_json({"type": "error", "message": "Google will end this Gemini Live session soon (its session-length limit)."})

    async def _content(self, sc: dict) -> None:
        if pick(sc, "interrupted"):
            self._muted, self._active, self._produced, self._cut = False, False, False, True
            await self.send_json({"type": "clear"})
            await self._state("hearing")
            self._reply = ""
            return
        it = pick(sc, "inputTranscription", "input_transcription")
        if it and it.get("text"):
            self._user += it["text"]
        ot = pick(sc, "outputTranscription", "output_transcription")
        if ot and ot.get("text"):
            self._active = self._produced = True
            await self._flush_user()
            self._reply += ot["text"]
            await self.send_json({"type": "assistant", "delta": ot["text"]})
        turn = pick(sc, "modelTurn", "model_turn")
        for part in (turn or {}).get("parts", []):
            blob = pick(part, "inlineData", "inline_data")
            if blob:
                await self._media(blob)
            elif part.get("text") and not ot:
                self._produced = True
                await self._flush_user()
                self._reply += part["text"]
                await self.send_json({"type": "assistant", "delta": part["text"]})
        if pick(sc, "turnComplete", "turn_complete"):
            await self._flush_user()
            cut, self._cut = self._cut, False
            if self._t_end is not None and not self._produced and not cut:
                await self.send_json({"type": "notice", "message": "Gemini ended that turn without replying (it sometimes does this, most often right after an interruption). Say it again."})
            self.turn_no += 1
            if self._reply:
                await self.send_json({"type": "assistant", "final": True, "text": self._reply})
            m = {"turn": self.turn_no, **self._m, "total_ms": round((self.clock() - self._t_end) * 1000) if self._t_end else None, "reply_chars": len(self._reply), "cloud": True}
            firsts = [m[k] for k in ("ttfa_ms", "first_video_ms") if m.get(k) is not None]
            if firsts:                                     # the avatar's voice is inside its video, so the first picture counts as the first sound too
                m["user_perceived_ms"] = min(firsts) + m.get("endpoint_ms", 0)
            snap = self.meter.snapshot()
            m["cost_usd"], m["cost_source"] = snap["cost_usd"], snap["source"]
            self.metrics.append(m)
            await self.send_json({"type": "metrics", **m})
            self._reply, self._muted, self._m, self._t_end, self._active = "", False, {}, None, False
            await self._state("listening")

    async def _flush_user(self) -> None:
        said = self._user.strip()
        if said and said != self._typed:                   # a typed message was already shown when it was sent; do not show Google's echo of it as well
            await self.send_json({"type": "transcript", "role": "user", "text": said})
        self._user, self._typed = "", ""

    async def _media(self, blob: dict) -> None:
        mime = str(pick(blob, "mimeType", "mime_type") or "")
        data = pick(blob, "data") or ""
        if mime.startswith("video/"):
            self.meter.video_started()
            if self._t_end is not None and "first_video_ms" not in self._m:
                self._m["first_video_ms"] = round((self.clock() - self._t_end) * 1000)
            if self._active:                                       # idle video between answers is forwarded (the picture keeps moving) but is not "speaking"
                await self._state("speaking")
            await self.send_json({"type": "video", "mime": mime, "data": data})   # never dropped, even after Interrupt: it is ONE continuous stream (its init segment comes once)
            return
        if self._muted or not mime.startswith("audio/"):
            return
        if "ttfa_ms" not in self._m and self._t_end is not None:
            self._m["ttfa_ms"] = round((self.clock() - self._t_end) * 1000)
        self._active = self._produced = True
        await self._state("speaking")
        pcm = base64.b64decode(data)
        self.meter.add_audio_out(len(pcm) / 48000)                              # 24 kHz, 16-bit, mono
        await self.send_audio(pcm)

    async def _tool(self, call: dict) -> None:
        cid, name, args = call.get("id"), call.get("name"), call.get("args") or {}
        if name == AGENT_TOOL and self.run_agent is not None and isinstance(args.get("request"), str) and args["request"].strip():
            try:
                result = {"status": "ok", "retryable": False, "result": (await self.run_agent(args["request"]))[:4000]}
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                result = {"status": "error", "retryable": False, "message": f"The agent could not do that: {str(e)[:300]}"}
        else:
            result = {"status": "invalid_argument", "retryable": False, "message": f"Unknown tool or missing 'request' for {name!r}."}
        await self._put({"toolResponse": {"functionResponses": [{"id": cid, "name": name, "response": result}]}})


def _decode(raw: Any) -> dict | None:
    try:
        d = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return d if isinstance(d, dict) else None


def _why(e: Exception) -> str:
    """The reason Google gave when it closed the connection, else the exception text."""
    rcvd = getattr(e, "rcvd", None)
    if rcvd is not None and getattr(rcvd, "reason", ""):
        return f"{rcvd.reason} (code {rcvd.code})"
    return f"{type(e).__name__}: {str(e)[:200]}"


__all__ = ["GeminiConfig", "GeminiError", "GeminiLiveSession", "FRAME", "VOICES", "LANGUAGES", "build_setup", "problems", "make_token_source"]
