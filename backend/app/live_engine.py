"""Backend side of the live-voice worker: starts `app.voice_worker` in the isolated environment, keeps it warm, and gives the live
session three async calls - `stt`, `tts` (a stream that can be cancelled) and `warm`.

State: idle -> loading -> ready | failed. The process dies with the backend (its stdin pipe closes). A plain thread reads the worker's
output (not asyncio subprocess pipes, which need a particular event loop on Windows) and hands each reply to the waiting coroutine.
"""
from __future__ import annotations

import asyncio
import base64
import collections
import itertools
import json
import subprocess
import threading
import time
from typing import Any, AsyncIterator, Callable

from . import voice_spec as VS
from .voice_worker import PREFIX

REPLY_TIMEOUT_S = 120.0


class EngineError(Exception):
    """The voice engines are not usable right now. The message is safe to show."""


def default_spawn() -> Callable[[], subprocess.Popen]:
    def spawn() -> subprocess.Popen:
        from .voice_local import BACKEND_DIR, _child_env
        h = VS.home()
        st = VS.read_state(h)
        env = _child_env(h, {"PYTHONPATH": str(BACKEND_DIR), "VOICE_STT_MODEL": st.get("stt_model") or "base.en", "VOICE_DEVICE": st.get("device") or "cpu"})
        return subprocess.Popen([str(VS.env_python(h)), "-u", "-m", "app.voice_worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1, env=env, cwd=str(BACKEND_DIR),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return spawn


class TtsStream:
    """The audio of one spoken phrase: `async for pcm in stream` yields 24 kHz mono int16 chunks as the worker makes them."""

    def __init__(self, engine: "VoiceEngine", rid: int, queue: asyncio.Queue):
        self.engine, self.rid, self.queue = engine, rid, queue
        self.stats: dict = {}
        self.finished = False

    def cancel(self) -> None:
        if not self.finished:
            self.engine._send_nowait({"op": "cancel", "id": self.rid})

    def close(self) -> None:
        """Stop this phrase (if it is still being made) and drop its reply queue."""
        self.cancel()
        self.engine._forget(self.rid)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            while True:
                msg = await asyncio.wait_for(self.queue.get(), REPLY_TIMEOUT_S)
                if "chunk" in msg:
                    yield base64.b64decode(msg["chunk"])
                    continue
                self.finished = True
                self.stats = {k: msg[k] for k in ("first_ms", "ms", "chunks", "cancelled") if k in msg}
                if not msg.get("ok"):
                    raise EngineError(msg.get("error") or "the voice worker failed")
                return
        finally:
            self.engine._forget(self.rid)


class VoiceEngine:
    def __init__(self, spawn: Callable[[], Any] | None = None):
        self._spawn = spawn or default_spawn()
        self.state, self.error = "idle", ""
        self.info: dict = {}
        self._proc: Any = None
        self._send_lock = threading.Lock()
        self._queues: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}
        self._ids = itertools.count(1)
        self._stderr: collections.deque[str] = collections.deque(maxlen=8)
        self._warming: asyncio.Task | None = None

    # ------------------------------------------------------------ process plumbing
    def _read(self, proc: Any) -> None:
        for line in proc.stdout:
            if not line.startswith(PREFIX):
                continue
            try:
                msg = json.loads(line[len(PREFIX):])
            except ValueError:
                continue
            entry = self._queues.get(msg.get("id"))
            if entry is not None:
                self._post(entry, msg)
        self._exited(proc)

    @staticmethod
    def _post(entry: tuple, msg: dict) -> None:
        loop, q = entry                                   # each request's reply goes to the event loop that is waiting for it
        try:
            loop.call_soon_threadsafe(q.put_nowait, msg)
        except RuntimeError:                              # that loop has already closed: nobody is waiting any more
            pass

    def _drain(self, proc: Any) -> None:
        for line in proc.stderr:
            if line.strip():
                self._stderr.append(line.strip()[:200])

    def _exited(self, proc: Any) -> None:
        if self._proc is not proc:
            return
        self._proc = None
        if self.state in ("loading", "ready"):
            self.state = "failed"
            self.error = "the voice worker exited" + (f": {' | '.join(self._stderr)}" if self._stderr else "")
        for entry in list(self._queues.values()):
            self._post(entry, {"ok": False, "error": self.error or "the voice worker exited"})

    def _start_process(self) -> Any:
        proc = self._spawn()
        self._proc = proc
        threading.Thread(target=self._read, args=(proc,), name="voice-read", daemon=True).start()
        threading.Thread(target=self._drain, args=(proc,), name="voice-err", daemon=True).start()
        return proc

    def _send_nowait(self, payload: dict) -> None:
        proc = self._proc
        if proc is None:
            raise EngineError("the voice worker is not running")
        with self._send_lock:
            try:
                proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as e:
                raise EngineError(f"the voice worker is not accepting requests: {e}") from e

    def _forget(self, rid: int) -> None:
        self._queues.pop(rid, None)

    async def _request(self, payload: dict) -> tuple[int, asyncio.Queue]:
        rid = next(self._ids)
        q: asyncio.Queue = asyncio.Queue()
        self._queues[rid] = (asyncio.get_running_loop(), q)
        try:
            await asyncio.to_thread(self._send_nowait, {**payload, "id": rid})
        except BaseException:
            self._forget(rid)
            raise
        return rid, q

    async def _reply(self, payload: dict, timeout: float = REPLY_TIMEOUT_S) -> dict:
        rid, q = await self._request(payload)
        try:
            msg = await asyncio.wait_for(q.get(), timeout)
        except asyncio.TimeoutError as e:
            raise EngineError("the voice worker did not answer in time") from e
        finally:
            self._forget(rid)
        if not msg.get("ok"):
            raise EngineError(msg.get("error") or "the voice worker failed")
        return msg

    # ------------------------------------------------------------ public calls
    async def warm(self) -> dict:
        """Start the worker if needed and load both models. Safe to call again; concurrent callers share one load."""
        if self.state == "ready":
            return self.info
        if self._warming is None or self._warming.done():
            self._warming = asyncio.ensure_future(self._warm())
        return await asyncio.shield(self._warming)

    async def _warm(self) -> dict:
        self.state, self.error = "loading", ""
        t = time.perf_counter()
        try:
            if self._proc is None:
                self._start_process()
            msg = await self._reply({"op": "warm"}, timeout=600)
        except (EngineError, OSError) as e:
            self.state, self.error = "failed", str(e)
            raise EngineError(str(e)) from e
        self.state = "ready"
        self.info = {k: msg[k] for k in ("device", "compute_type", "stt_model", "tts_device", "load_ms") if k in msg}
        self.info["warm_s"] = round(time.perf_counter() - t, 2)
        return self.info

    async def stt(self, pcm: bytes) -> dict:
        """Transcribe 16 kHz mono int16 audio -> {"text", "ms", "audio_ms"}."""
        await self.warm()
        msg = await self._reply({"op": "stt", "pcm": base64.b64encode(pcm).decode("ascii")})
        return {"text": msg.get("text", ""), "ms": msg.get("ms"), "audio_ms": msg.get("audio_ms")}

    async def tts(self, text: str, voice: str = "", speed: float = 1.0) -> TtsStream:
        await self.warm()
        rid, q = await self._request({"op": "tts", "text": text, "voice": voice, "speed": speed})
        return TtsStream(self, rid, q)

    async def voices(self) -> list[str]:
        await self.warm()
        return list((await self._reply({"op": "voices"})).get("voices") or [])

    def close(self) -> None:
        proc, self._proc = self._proc, None
        self.state = "idle"
        if proc is not None:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                proc.kill()
            except OSError:
                pass

    def status(self) -> dict:
        return dict(state=self.state, error=self.error, **self.info)
