"""Live mode: one spoken conversation, as a pipeline in which every stage streams.

    microphone frames --VAD--> utterance --Whisper--> text --LLM token stream--> speakable chunks --Kokoro--> audio frames

* **VAD** (`Vad`): an energy detector with an adaptive noise floor. It decides when you started (with a short pre-roll so no word
  onset is clipped) and when you stopped (`hangover_ms` of silence - the one delay a spoken conversation cannot avoid).
* **Chunker** (`Chunker`): cuts the model's tokens into phrases as they arrive, the FIRST one at the earliest natural pause (a comma
  after four words, a sentence end, or simply after six words) so the voice starts while the model is still writing; later ones at sentence ends.
* **Turn** (`LiveSession._turn`): speech-to-text, then the model, with a separate task turning chunks into audio in order, so the next
  chunk is being synthesised while the previous one plays. Audio goes to the client the moment the worker makes it.
* **Barge-in**: speech while the assistant is talking (or thinking) cancels the model call and the synthesis, tells the client to
  flush what it has buffered, and keeps in the history only what was actually spoken.
* **Metrics**: every turn reports the time of each stage and `ttfa_ms` - from the moment the end of your speech was detected to the
  first audio frame sent; `user_perceived_ms` adds the end-of-speech wait. The client adds its own playback time.

All I/O is injected (`send_json`, `send_audio`, the voice engine and the model), so the tests drive the whole pipeline with fakes.
"""
from __future__ import annotations

import array
import asyncio
import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import live_cost as LC
from .voice import speakable

IN_RATE = 16000
OUT_RATE = 24000
FRAME = 320                                    # 20 ms of 16 kHz audio
SYSTEM_PROMPT = ("You are talking with a person by voice, in real time. Reply in one to three short, natural spoken sentences. "
                 "Use plain words only: no markdown, lists, headings, code, emojis or URLs. If the answer is long, give the gist and offer more. "
                 "If you did not catch something, say so briefly.")


# ---------------------------------------------------------------- voice activity detection
def rms_db(frame: bytes) -> float:
    """Level of a 16-bit little-endian frame in dB relative to full scale (-96 for silence)."""
    a = array.array("h")
    a.frombytes(frame[: len(frame) // 2 * 2])
    if not a:
        return -96.0
    mean_sq = sum(v * v for v in a) / len(a)
    return 20 * math.log10(math.sqrt(mean_sq) / 32768.0) if mean_sq > 0 else -96.0


@dataclass
class VadConfig:
    hangover_ms: int = 350        # silence that ends an utterance
    start_ms: int = 100           # speech that starts one
    barge_ms: int = 240           # speech that starts one while the assistant is talking (stricter: its own voice may leak into the mic)
    min_speech_ms: int = 250      # shorter than this is a cough, not a sentence
    preroll_ms: int = 300
    max_ms: int = 30000
    floor_db: float = -50.0       # never treat anything quieter than this as speech
    margin_db: float = 12.0       # speech must be this far above the noise floor


class Vad:
    def __init__(self, cfg: VadConfig | None = None):
        self.cfg = cfg or VadConfig()
        self.busy = False                       # the assistant is talking/thinking: be stricter about what counts as speech
        self.reset()

    def reset(self) -> None:
        self._rest = b""
        self.noise = -60.0
        self.speaking = False
        self.buf = bytearray()
        self.pre: deque[bytes] = deque(maxlen=max(1, self.cfg.preroll_ms // 20))
        self.run_ms = self.sil_ms = self.speech_ms = 0
        self._run: list[bytes] = []

    def feed(self, pcm: bytes) -> list[tuple]:
        """Events for this audio: ("start",), ("end", pcm, speech_ms) or ("discard",)."""
        data = self._rest + pcm
        n = len(data) // (FRAME * 2) * (FRAME * 2)
        self._rest = data[n:]
        out: list[tuple] = []
        for i in range(0, n, FRAME * 2):
            out += self._frame(data[i:i + FRAME * 2])
        return out

    def _frame(self, f: bytes) -> list[tuple]:
        c = self.cfg
        db = rms_db(f)
        margin = c.margin_db + (6.0 if self.busy else 0.0)
        speech = db > max(c.floor_db, self.noise + margin)
        if not self.speaking:
            if not speech:
                self.noise = self.noise * 0.97 + db * 0.03          # learn the room's noise from the quiet frames only
            if speech:
                self.run_ms += 20
                self._run.append(f)
                need = c.barge_ms if self.busy else c.start_ms
                if self.run_ms >= need:
                    self.speaking, self.speech_ms, self.sil_ms = True, self.run_ms, 0
                    self.buf = bytearray(b"".join(self.pre)) + b"".join(self._run)
                    self.pre.clear()
                    self.run_ms, self._run = 0, []
                    return [("start",)]
            else:
                self.run_ms, self._run = 0, []
                self.pre.append(f)
            return []
        self.buf += f
        if speech:
            self.sil_ms, self.speech_ms = 0, self.speech_ms + 20
        else:
            self.sil_ms += 20
        if self.sil_ms >= c.hangover_ms or len(self.buf) >= c.max_ms * IN_RATE * 2 // 1000:
            return self.finish()
        return []

    def finish(self) -> list[tuple]:
        """End the utterance now (silence, the length limit, or the user releasing push-to-talk)."""
        if not self.speaking:
            return []
        pcm, ms = bytes(self.buf), self.speech_ms
        self.speaking, self.buf, self.sil_ms, self.speech_ms = False, bytearray(), 0, 0
        return [("end", pcm, ms)] if ms >= self.cfg.min_speech_ms else [("discard",)]


# ---------------------------------------------------------------- phrases for the voice
_ABBREV = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "vs", "etc", "e.g", "i.e", "st", "no"}
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")
_THINK = re.compile(r"<think>.*?</think>", re.S)


class Chunker:
    """Cuts a stream of model text into phrases worth sending to the voice, as early as it sounds natural."""

    def __init__(self, first_words: int = 4, min_words: int = 3, max_chars: int = 220, first_soft: int = 6):
        self.first_words, self.min_words, self.max_chars, self.first_soft = first_words, min_words, max_chars, first_soft
        self.buf = ""
        self.emitted = 0

    def feed(self, text: str) -> list[str]:
        self.buf += text
        self.buf = _THINK.sub("", self.buf)
        if "<think>" in self.buf:                   # a reasoning block is still open: keep what is before it, hold the rest
            head = self.buf.split("<think>", 1)[0]
            return self._cut(head, hold=self.buf[len(head):])
        return self._cut(self.buf)

    def _cut(self, text: str, hold: str = "") -> list[str]:
        out: list[str] = []
        while True:
            i = self._boundary(text)
            if i is None:
                break
            piece, text = text[:i], text[i:]
            self._emit(piece, out)
        self.buf = text + hold
        if len(text) > self.max_chars:               # no pause in sight: split at the last space
            cut = text.rfind(" ", 0, self.max_chars)
            if cut > 0:
                self._emit(text[:cut], out)
                self.buf = text[cut:] + hold
        return out

    def _emit(self, piece: str, out: list[str]) -> None:
        clean = _EMOJI.sub("", speakable(piece)).strip()
        if re.search(r"\w", clean):
            out.append(clean)
            self.emitted += 1

    def _boundary(self, text: str) -> int | None:
        """Index just after the first place the text may be cut, or None. A cut needs the character after it (so '3.5' and
        'Dr. X' are not cut), except at a newline."""
        words = 0
        for i, ch in enumerate(text):
            if ch.isspace() and i and not text[i - 1].isspace():
                words += 1
            if ch == "\n" and text[:i].strip():
                return i + 1
            if i + 1 >= len(text) or not text[i + 1].isspace():
                continue
            before = text[:i + 1].split()
            if not before:
                continue
            last = before[-1].lower().rstrip(".!?…")
            if ch in ".!?…":
                if last in _ABBREV or (last.isdigit() and len(last) <= 2 and ch == "."):
                    continue
                if self.emitted == 0 or words + 1 >= self.min_words:
                    return i + 1
            elif ch in ",;:—" and self.emitted == 0 and words + 1 >= self.first_words:
                return i + 1                         # the first phrase may stop at a comma, so the voice starts sooner
            elif self.emitted == 0 and words + 1 >= self.first_soft:
                return i + 1                         # ... or after a few words even with no pause: a short first phrase is what makes the voice start fast
        return None

    def flush(self) -> list[str]:
        out: list[str] = []
        rest = _THINK.sub("", self.buf).split("<think>", 1)[0]
        self.buf = ""
        self._emit(rest, out)
        return out


_JUNK = re.compile(r"^[\[\(\*].*[\]\)\*]$")
_FILLER = {"you", "thank you", "thanks", "thanks for watching", "bye", "uh", "um", "hmm", "mm"}


def junk_transcript(text: str, speech_ms: int = 10_000) -> bool:
    """Whisper invents words from noise ('Thank you.', '[BLANK_AUDIO]'); do not answer those."""
    t = text.strip()
    plain = re.sub(r"[^\w\s]", "", t).strip().lower()
    return (not plain) or bool(_JUNK.match(t)) or (plain in _FILLER and speech_ms < 1500)


# ---------------------------------------------------------------- the session
@dataclass
class LiveConfig:
    voice: str = "af_heart"
    speed: float = 1.0
    instruction: str = ""
    barge_in: bool = True
    max_tokens: int = 300
    max_history: int = 16
    budget_usd: float = 0.0                       # 0 = no cap (a local session costs nothing; a cloud model or an agent turn can)
    vad: VadConfig = field(default_factory=VadConfig)


def resample_24k_to_16k(pcm: bytes) -> bytes:
    """Linear 3:2 down-sampling of 16-bit audio (used by the self-test, which plays the voice's own output back as the 'microphone')."""
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    n = len(a) * 2 // 3
    out = array.array("h", bytes(2 * n))
    for j in range(n):
        x = j * 1.5
        i = int(x)
        frac = x - i
        nxt = a[i + 1] if i + 1 < len(a) else a[i]
        out[j] = int(a[i] * (1 - frac) + nxt * frac)
    return out.tobytes()


class LiveSession:
    def __init__(self, engine: Any, llm: Any, send_json: Callable[[dict], Awaitable[None]], send_audio: Callable[[bytes], Awaitable[None]],
                 cfg: LiveConfig | None = None, clock: Callable[[], float] = time.perf_counter, meter: LC.CostMeter | None = None):
        self.engine, self.llm = engine, llm
        self.send_json, self.send_audio = send_json, send_audio
        self.cfg = cfg or LiveConfig()
        self.clock = clock
        self.vad = Vad(self.cfg.vad)
        self.history: list[dict] = []
        self.turn_no = 0
        self.task: asyncio.Task | None = None
        self.state = "idle"
        self.metrics: list[dict] = []
        self.closed = False
        self.ready = False
        self.meter = meter or LC.CostMeter(LC.DEFAULT_PRICES, "local", self.cfg.budget_usd, clock)
        self._watch: asyncio.Task | None = None
        self._seen = (0, 0)

    def _count_llm(self) -> None:
        """Fold the model's usage since the last turn into the meter (Ollama's own token counts; a cloud model's spend)."""
        tin, tout = getattr(self.llm, "tokens_in", 0), getattr(self.llm, "tokens_out", 0)
        self.meter.add_local(tokens_in=tin - self._seen[0], tokens_out=tout - self._seen[1])
        self._seen = (tin, tout)
        if getattr(self.llm, "cost_usd", 0):
            self.meter.set_cloud(self.llm.cost_usd)

    # ------------------------------------------------------------ outgoing
    async def _state(self, s: str) -> None:
        if s != self.state:
            self.state = s
            self.vad.busy = s in ("thinking", "speaking") and self.cfg.barge_in
            await self.send_json({"type": "state", "state": s})

    async def start(self) -> dict:
        """Load the voice engines and the model in parallel; tell the client when both are warm."""
        await self._state("loading")
        info, _ = await asyncio.gather(self.engine.warm(), self.llm.warm())
        await self._state("listening")
        self.ready = True
        self._watch = asyncio.ensure_future(LC.watch(self))                        # the live usage meter (once a second) and the budget
        msg = {"type": "ready", "engine": info, "voice": self.cfg.voice, "local_model": bool(getattr(self.llm, "local", False))}
        await self.send_json(msg)
        return msg

    # ------------------------------------------------------------ incoming
    async def audio(self, pcm: bytes) -> None:
        if self.state in ("idle", "loading") or (self.state in ("thinking", "speaking") and not self.cfg.barge_in):
            return                                   # not ready yet, or barge-in is off and the assistant is busy
        for ev in self.vad.feed(pcm):
            await self._on_vad(ev)

    async def _on_vad(self, ev: tuple) -> None:
        if ev[0] == "start":
            if self.task and not self.task.done():
                await self.interrupt()               # barge-in: the person started talking over the assistant
            await self._state("hearing")
        elif ev[0] == "discard":
            await self._state("listening")
        else:                                        # ("end", pcm, speech_ms)
            self._begin(self._turn(pcm=ev[1], speech_ms=ev[2], t_end=self.clock()))

    async def commit(self) -> None:
        """Push-to-talk released: end the utterance now."""
        for ev in self.vad.finish():
            await self._on_vad(ev)

    async def say(self, text: str) -> None:
        """A typed message: same as speech, without the recogniser."""
        if self.task and not self.task.done():
            await self.interrupt()
        self._begin(self._turn(text=text, t_end=self.clock()))

    def _begin(self, coro) -> None:
        self.task = asyncio.ensure_future(coro)

    async def interrupt(self) -> None:
        t = self.task
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        await self.send_json({"type": "clear"})
        await self._state("listening")

    async def close(self) -> None:
        self.closed = True
        if self._watch and not self._watch.done() and self._watch is not asyncio.current_task():
            self._watch.cancel()
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def wait(self) -> None:
        """Wait for the turn in progress (used by the self-test)."""
        if self.task:
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------ one turn
    async def _turn(self, pcm: bytes | None = None, speech_ms: int = 0, text: str = "", t_end: float = 0.0) -> None:
        self.turn_no += 1
        m: dict[str, Any] = dict(turn=self.turn_no, endpoint_ms=0 if text else self.cfg.vad.hangover_ms)
        spoken: list[str] = []
        queue: asyncio.Queue = asyncio.Queue()
        speaker: asyncio.Task | None = None
        reply_pieces: list[str] = []
        try:
            if pcm is not None:
                await self._state("thinking")
                t0 = self.clock()
                r = await self.engine.stt(pcm)
                m["stt_ms"], m["stt_wall_ms"], m["audio_ms"] = r.get("ms"), round((self.clock() - t0) * 1000), r.get("audio_ms")
                self.meter.add_local(audio_in_s=(r.get("audio_ms") or 0) / 1000)
                text = (r.get("text") or "").strip()
                if junk_transcript(text, speech_ms):
                    await self._state("listening")
                    return
            await self.send_json({"type": "transcript", "role": "user", "text": text})
            await self._state("thinking")
            t_llm = self.clock()
            speaker = asyncio.ensure_future(self._speak(queue, spoken, m, t_end))
            chunker = Chunker()
            messages = [{"role": "system", "content": (self.cfg.instruction + "\n\n" if self.cfg.instruction else "") + SYSTEM_PROMPT},
                        *self.history[-self.cfg.max_history:], {"role": "user", "content": text}]
            async for piece in self.llm.stream(messages, self.cfg.max_tokens):
                if "llm_first_token_ms" not in m:
                    m["llm_first_token_ms"] = round((self.clock() - t_llm) * 1000)
                reply_pieces.append(piece)
                await self.send_json({"type": "assistant", "delta": piece})
                for ch in chunker.feed(piece):
                    if "first_chunk_ms" not in m:
                        m["first_chunk_ms"] = round((self.clock() - t_llm) * 1000)
                    queue.put_nowait(ch)
            for ch in chunker.flush():
                if "first_chunk_ms" not in m:
                    m["first_chunk_ms"] = round((self.clock() - t_llm) * 1000)
                queue.put_nowait(ch)
            queue.put_nowait(None)
            await speaker
            speaker = None
            m["total_ms"] = round((self.clock() - t_end) * 1000)
            m["reply_chars"] = len("".join(reply_pieces))
            self._count_llm()
            self._remember(text, spoken, full="".join(reply_pieces))
            await self.send_json({"type": "assistant", "final": True, "text": "".join(reply_pieces)})
            self.metrics.append(m)
            await self.send_json({"type": "metrics", **m})
            await self._state("listening")
        except asyncio.CancelledError:
            if speaker:
                speaker.cancel()
            self._count_llm()
            self._remember(text, spoken, full="")                  # keep only what was said aloud before the person cut in
            m["interrupted"] = True
            self.metrics.append(m)
            raise
        except Exception as e:  # noqa: BLE001 - shown to the person; the conversation goes on
            if speaker:
                speaker.cancel()
            await self.send_json({"type": "error", "message": str(e)[:300] or type(e).__name__})
            await self._state("listening")

    async def _speak(self, queue: asyncio.Queue, spoken: list[str], m: dict, t_end: float) -> None:
        """Turn phrases into audio, in order, sending each audio chunk the moment it exists."""
        while True:
            chunk = await queue.get()
            if chunk is None:
                return
            t_req = self.clock()
            stream = await self.engine.tts(chunk, self.cfg.voice, self.cfg.speed)
            try:
                first = True
                async for pcm in stream:
                    if first:
                        first = False
                        spoken.append(chunk)
                        if "ttfa_ms" not in m:
                            now = self.clock()
                            m["tts_first_ms"] = round((now - t_req) * 1000)
                            m["ttfa_ms"] = round((now - t_end) * 1000)
                            m["user_perceived_ms"] = m["ttfa_ms"] + m["endpoint_ms"]
                            await self._state("speaking")
                    self.meter.add_local(audio_out_s=len(pcm) / 48000)
                    await self.send_audio(pcm)
            except asyncio.CancelledError:
                stream.close()
                raise

    def _remember(self, user: str, spoken: list[str], full: str) -> None:
        said = " ".join(spoken).strip() or full.strip()
        self.history.append({"role": "user", "content": user})
        if said:
            self.history.append({"role": "assistant", "content": said})
        del self.history[:-self.cfg.max_history]
