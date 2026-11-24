"""The live-voice worker: speech-to-text and streaming text-to-speech, kept warm in one process.

Runs INSIDE the isolated voice environment (`<env python> -m app.voice_worker`, env VOICE_HOME / VOICE_DEVICE / VOICE_STT_MODEL / PYTHONPATH),
started by `live_engine.VoiceEngine` - never by the user and never before an install exists. Only the standard library is imported here;
the heavy engines (faster-whisper, kokoro-onnx) are imported by `RealEngines.load()`.

Protocol (one JSON object per line; replies start with "@@v " so library chatter on stdout cannot be mistaken for one):
    in : {"id": 1, "op": "warm"}                                              load both models and run each once
         {"id": 2, "op": "stt", "pcm": <base64 of 16 kHz mono int16>}         -> {"id": 2, "ok": true, "text": "...", "ms": 85}
         {"id": 3, "op": "tts", "text": "...", "voice": "af_heart", "speed": 1.0}
              -> {"id": 3, "chunk": <base64 of 24 kHz mono int16>, "n": 0, "ms": 190}   (one per audio chunk, as it is produced)
                 then {"id": 3, "ok": true, "done": true, "chunks": 2, "first_ms": 190, "ms": 410}   ("cancelled": true if cancelled)
         {"op": "cancel", "id": 3}                                            stop that tts job at the next chunk (barge-in)
         {"id": 4, "op": "ping"} / {"id": 5, "op": "voices"}
Jobs run on a small thread pool so `cancel` is read while a synthesis is still streaming.
"""
from __future__ import annotations

import base64
import json
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, TextIO

PREFIX = "@@v "
TTS_RATE = 24000
STT_RATE = 16000


class Worker:
    def __init__(self, engines: Any, out: TextIO | None = None, clock=time.perf_counter, pool_size: int = 4):
        self.engines = engines
        self.out = out or sys.stdout
        self.clock = clock
        self.lock = threading.Lock()
        self.cancelled: dict[int, threading.Event] = {}
        self.pool = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="voice")

    # ------------------------------------------------------------ output
    def emit(self, msg: dict) -> None:
        with self.lock:
            self.out.write(PREFIX + json.dumps(msg, separators=(",", ":")) + "\n")
            self.out.flush()

    # ------------------------------------------------------------ requests
    def handle(self, line: str) -> None:
        """One request line. Replies come through `emit`, possibly from a pool thread. Never raises."""
        try:
            req = json.loads(line)
        except ValueError:
            return self.emit({"id": None, "ok": False, "error": "the request is not valid JSON"})
        if not isinstance(req, dict):
            return self.emit({"id": None, "ok": False, "error": "the request must be a JSON object"})
        rid, op = req.get("id"), req.get("op")
        if op == "cancel":
            ev = self.cancelled.get(rid)
            if ev:
                ev.set()
            return
        if op == "ping":
            return self.emit({"id": rid, "ok": True})
        if op == "voices":
            return self.emit({"id": rid, "ok": True, "voices": list(self.engines.voices())})
        if op == "warm":
            return self._job(op, rid, req)            # on THIS thread: Windows deadlocks when a model library loads its DLLs on a pool thread
        if op in ("stt", "tts"):                      # while the main thread waits on the pipe
            if op == "tts":
                self.cancelled[rid] = threading.Event()
            self.pool.submit(self._job, op, rid, req)
            return
        self.emit({"id": rid, "ok": False, "error": f"unknown op '{op}'"})

    def _job(self, op: str, rid: Any, req: dict) -> None:
        try:
            getattr(self, "_" + op)(rid, req)
        except Exception as e:  # noqa: BLE001 - reported to the parent, which shows it
            self.emit({"id": rid, "ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"})
        finally:
            self.cancelled.pop(rid, None)

    def _warm(self, rid: Any, req: dict) -> None:
        t = self.clock()
        info = self.engines.load()
        self.emit({"id": rid, "ok": True, **info, "load_ms": round((self.clock() - t) * 1000)})

    def _stt(self, rid: Any, req: dict) -> None:
        pcm = base64.b64decode(req.get("pcm") or "")
        t = self.clock()
        text = self.engines.stt(pcm)
        self.emit({"id": rid, "ok": True, "text": text, "ms": round((self.clock() - t) * 1000), "audio_ms": round(len(pcm) / 2 / STT_RATE * 1000)})

    def _tts(self, rid: Any, req: dict) -> None:
        stop = self.cancelled[rid]
        t = self.clock()
        n, first = 0, None
        chunks: Iterable[bytes] = self.engines.tts(str(req.get("text") or ""), str(req.get("voice") or ""), float(req.get("speed") or 1.0))
        for pcm in chunks:
            if stop.is_set():
                close = getattr(chunks, "close", None)
                if close:
                    close()
                break
            ms = round((self.clock() - t) * 1000)
            first = ms if first is None else first
            self.emit({"id": rid, "chunk": base64.b64encode(pcm).decode("ascii"), "n": n, "ms": ms})
            n += 1
        self.emit({"id": rid, "ok": True, "done": True, "chunks": n, "first_ms": first, "ms": round((self.clock() - t) * 1000),
                   **({"cancelled": True} if stop.is_set() else {})})

    # ------------------------------------------------------------ the loop
    def serve(self, lines: Iterable[str]) -> None:
        """Read requests on a helper thread and handle them here, so this thread is never the one blocked in a pipe read."""
        q: queue.Queue = queue.Queue()

        def read() -> None:
            for ln in lines:
                q.put(ln)
            q.put(None)
        threading.Thread(target=read, name="voice-stdin", daemon=True).start()
        while True:
            line = q.get()
            if line is None:
                break
            if line.strip():
                self.handle(line)
        self.pool.shutdown(wait=True)


# ---------------------------------------------------------------- the real engines
class RealEngines:  # pragma: no cover - needs faster-whisper, kokoro-onnx and the model files; exercised by the live run
    def __init__(self) -> None:
        import os
        from pathlib import Path
        from . import voice_spec as VS
        self.home = VS.home()
        self.stt_name = os.environ.get("VOICE_STT_MODEL") or "base.en"
        self.device = os.environ.get("VOICE_DEVICE") or ("cuda" if VS.read_state(self.home).get("device") == "cuda" else "cpu")
        self.models = Path(VS.models_dir(self.home))
        self.whisper = self.kokoro = None

    def _dll_dirs(self) -> None:
        import glob
        import os
        import site
        for sp in site.getsitepackages():
            for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
                os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

    def load(self) -> dict:
        import numpy as np
        from faster_whisper import WhisperModel
        path = str(self.models / f"whisper-{self.stt_name}")
        device, ctype = self.device, "float16"
        if device == "cuda":
            try:
                self._dll_dirs()
                self.whisper = WhisperModel(path, device="cuda", compute_type="float16")
            except Exception:  # noqa: BLE001 - no usable GPU runtime: fall back to the CPU, and say so
                device, ctype, self.whisper = "cpu", "int8", None
        if self.whisper is None:
            device, ctype = "cpu", "int8"
            self.whisper = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=0)
        tts_device = self._load_tts(device)
        self.stt(np.zeros(STT_RATE, dtype="<i2").tobytes())                       # dummy runs, so the first real one is not slow (on a GPU the very first
        for phrase in ("Ready.", "This is a slightly longer phrase, so every kernel size is prepared."):  # run of each size compiles kernels)
            for _ in self.tts(phrase, "af_heart", 1.0):
                pass
        return {"device": device, "compute_type": ctype, "stt_model": self.stt_name, "tts_device": tts_device}

    def _load_tts(self, device: str) -> str:
        """Kokoro on the GPU (full-precision model, CUDA provider) when it is usable, else the int8 model on the CPU."""
        from kokoro_onnx import Kokoro
        voices = str(self.models / "voices-v1.0.bin")
        full = self.models / "kokoro-v1.0.onnx"
        if device == "cuda" and full.exists():
            try:
                import onnxruntime as ort
                sess = ort.InferenceSession(str(full), providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
                if "CUDAExecutionProvider" in sess.get_providers():
                    self.kokoro = Kokoro.from_session(sess, voices)
                    return "cuda"
            except Exception:  # noqa: BLE001 - the CPU build of onnxruntime, or no usable CUDA: use the int8 model on the CPU
                pass
        self.kokoro = Kokoro(str(self.models / "kokoro-v1.0.int8.onnx"), voices)
        return "cpu"

    def voices(self) -> list[str]:
        return sorted(self.kokoro.get_voices()) if self.kokoro else []

    def stt(self, pcm: bytes) -> str:
        import numpy as np
        x = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0
        segs, _ = self.whisper.transcribe(x, language="en", beam_size=1, vad_filter=False, condition_on_previous_text=False,
                                          without_timestamps=True, temperature=0.0)
        return " ".join(s.text.strip() for s in segs).strip()

    def tts(self, text: str, voice: str, speed: float):
        import asyncio
        import numpy as np
        lang = "en-gb" if voice[:2] in ("bf", "bm") else "en-us"
        loop = asyncio.new_event_loop()
        agen = self.kokoro.create_stream(text, voice=voice or "af_heart", speed=speed, lang=lang)
        try:
            while True:
                try:
                    a, _sr = loop.run_until_complete(agen.__anext__())
                except StopAsyncIteration:
                    break
                yield (np.clip(a, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        finally:
            loop.run_until_complete(agen.aclose())
            loop.close()


def check() -> int:  # pragma: no cover - the installer's last step: load both engines, say a phrase, print what was found
    import importlib.metadata as md
    import faster_whisper, kokoro_onnx, numpy  # noqa: F401, E401
    eng = RealEngines()
    t = time.perf_counter()
    info = eng.load()
    load_s = round(time.perf_counter() - t, 1)
    t = time.perf_counter()
    n = sum(len(c) for c in eng.tts("Everything is ready.", "af_heart", 1.0))
    ort = "onnxruntime-gpu" if info["tts_device"] == "cuda" else "onnxruntime"
    print(json.dumps({"faster-whisper": md.version("faster-whisper"), "kokoro-onnx": md.version("kokoro-onnx"), ort: md.version(ort),
                      "ctranslate2": md.version("ctranslate2"), **info, "load_s": load_s, "tts_ms": round((time.perf_counter() - t) * 1000),
                      "test_audio_s": round(n / 2 / TTS_RATE, 2)}), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    import os
    if "--check" in (argv if argv is not None else sys.argv[1:]):
        return check()
    # Import the engines BEFORE starting to read commands: on Windows the first import of numpy hung forever when it happened while the
    # command pipe was being read (found by stack dump); doing it here also takes the ~1 s import off the first request.
    import faster_whisper, kokoro_onnx, numpy  # noqa: F401, E401
    commands = os.fdopen(os.dup(0), "r", encoding="utf-8", errors="replace")      # a private copy of the command pipe ...
    replies = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    sys.stdin = open(os.devnull, "r")                                              # ... so nothing else can read or lock it, and
    sys.stdout = sys.stderr                                                        # whatever a library prints goes to stderr, not into the replies
    Worker(RealEngines(), replies).serve(commands)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
