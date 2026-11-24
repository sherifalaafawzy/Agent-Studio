"""Stand-ins for the voice worker's engines and process, so the live pipeline is tested end to end without any model."""
from __future__ import annotations

import io
import queue
import threading
from typing import Any

from app.voice_worker import Worker


def _tone() -> bytes:
    """150 ms of a loud 24 kHz tone: enough for the voice-activity detector to hear 'speech' when the self-test plays it back."""
    import array
    import math
    return array.array("h", (int(9000 * math.sin(i * 0.2)) for i in range(3600))).tobytes()


class FakeEngines:
    """What `RealEngines` provides: load / voices / stt / tts (a generator of 24 kHz int16 chunks)."""

    def __init__(self, chunks: int = 3, text: str = "hello there", gate: threading.Event | None = None, loud: bool = False):
        self.n, self.text, self.gate, self.loud = chunks, text, gate, loud
        self.loads = 0
        self.closed = 0
        self.tts_calls: list[tuple] = []

    def load(self) -> dict:
        self.loads += 1
        return {"device": "cpu", "compute_type": "int8", "stt_model": "base.en", "tts_device": "cpu"}

    def voices(self) -> list[str]:
        return ["af_heart", "am_adam"]

    def stt(self, pcm: bytes) -> str:
        if pcm == b"bad":
            raise RuntimeError("cannot decode")
        return self.text

    def tts(self, text: str, voice: str, speed: float):
        self.tts_calls.append((text, voice, speed))
        if text == "explode":
            raise RuntimeError("synthesis failed")
        try:
            for i in range(self.n):
                if self.gate is not None and i == 1:
                    self.gate.wait(5)
                yield (_tone() if self.loud else bytes([i + 1]) * 480)
        finally:
            self.closed += 1


class Pipe:
    """A line pipe: `write` feeds it, iterating it yields lines until `end()`."""

    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()

    def write(self, s: str) -> int:
        for ln in s.splitlines(True):
            self.q.put(ln)
        return len(s)

    def flush(self) -> None:
        pass

    def end(self) -> None:
        self.q.put(None)

    def __iter__(self):
        while True:
            ln = self.q.get()
            if ln is None:
                return
            yield ln


class FakeProc:
    """A `subprocess.Popen` look-alike that runs the real `Worker` protocol in this process."""

    def __init__(self, engines: Any, stderr_lines: tuple[str, ...] = (), swallow: bool = False, stdout_noise: tuple[str, ...] = ()):
        self.out, self.err = Pipe(), Pipe()
        for ln in stderr_lines:
            self.err.write(ln + "\n")
        self.err.end()
        for ln in stdout_noise:
            self.out.write(ln + "\n")
        self.worker = Worker(engines, self.out)
        self.swallow, self.killed, self.stdin_closed, self.fail_writes = swallow, False, False, False
        self.stdout, self.stderr, self.stdin = self.out, self.err, self

    def write(self, s: str) -> int:
        if self.fail_writes:
            raise OSError("broken pipe")
        if not self.swallow:
            for ln in s.splitlines():
                self.worker.handle(ln)
        return len(s)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.stdin_closed = True
        self.out.end()

    def kill(self) -> None:
        self.killed = True
        self.out.end()

    def die(self) -> None:
        self.out.end()


def capture() -> tuple[io.StringIO, Worker]:
    buf = io.StringIO()
    return buf, buf  # type: ignore[return-value]


# ---------------------------------------------------------------- a fake Google Gemini Live server (follows the documented messages)
import asyncio
import base64
import contextlib
import json as _json


class FakeGoogle:
    """Records what the proxy sends and plays a script. `script(ws, msg, google)` runs for every client message after the setup."""

    def __init__(self, script=None, setup_reply="ok", reply_delay: float = 0.0):
        self.script, self.setup_reply, self.reply_delay = script, setup_reply, reply_delay
        self.path = ""
        self.headers: dict = {}
        self.setup: dict = {}
        self.received: list[dict] = []
        self.audio_chunks = 0
        self.conns = 0
        self.url = ""

    async def handler(self, ws):
        self.conns += 1
        self.path, self.headers = ws.request.path, {k.lower(): v for k, v in ws.request.headers.items()}
        first = _json.loads(await ws.recv())
        self.setup = first.get("setup", {})
        if self.setup_reply == "deny":
            await ws.close(code=1008, reason="Permission denied on resource project")
            return
        if self.setup_reply == "wrong":
            await ws.send(_json.dumps({"hello": 1}))
            return
        if self.setup_reply == "silent":
            await ws.wait_closed()
            return
        await ws.send(_json.dumps({"setupComplete": {"sessionId": "sess-1"}}))
        try:
            async for raw in ws:
                msg = _json.loads(raw)
                self.received.append(msg)
                rt = msg.get("realtimeInput") or {}
                if "audio" in rt:
                    self.audio_chunks += 1
                if self.script:
                    await self.script(ws, msg, self)
        except Exception:  # noqa: BLE001 - the client left
            pass


@contextlib.asynccontextmanager
async def fake_google(**kw):
    from websockets.asyncio.server import serve
    g = FakeGoogle(**kw)
    async with serve(g.handler, "127.0.0.1", 0) as server:
        g.url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        yield g


def pcm_b64(n: int = 480) -> str:
    return base64.b64encode(bytes([7, 0]) * n).decode()


async def speak_script(ws, msg, g):
    """Answer a typed message (or an audio-stream end) the way Gemini Live does: every message a BINARY frame holding JSON (as seen from the real service),
    transcriptions, audio, then turnComplete with usageMetadata in the same message."""
    rt = msg.get("realtimeInput") or {}
    if "text" not in rt and not rt.get("audioStreamEnd"):
        return

    async def send(o):
        await ws.send(_json.dumps(o).encode())
    await send({"serverContent": {"inputTranscription": {"text": "tell me "}}})
    await send({"serverContent": {"inputTranscription": {"text": "a fact"}}})
    await send({"serverContent": {"outputTranscription": {"text": "Hello "}}})
    await send({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": pcm_b64()}}]}}})
    await send({"serverContent": {"outputTranscription": {"text": "there."}}})
    await send({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": pcm_b64()}}]}}})
    await send({"serverContent": {"turnComplete": True}, "usageMetadata": {"promptTokenCount": 92, "promptTokensDetails": [{"modality": "AUDIO", "tokenCount": 1000}],
                                                                           "candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 500}]}})
