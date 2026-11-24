"""The backend's client for the voice worker, talking to the real worker protocol through an in-process fake process."""
from __future__ import annotations

import asyncio
import threading

import pytest

from app import live_engine as LE
from app import voice_spec as VS
from tests.live_fakes import FakeEngines, FakeProc


def make(engines=None, **kw):
    proc = FakeProc(engines or FakeEngines(), **kw)
    return LE.VoiceEngine(spawn=lambda: proc), proc


@pytest.mark.asyncio
async def test_warm_starts_the_worker_once_and_shares_one_load():
    eng, proc = make()
    a, b = await asyncio.gather(eng.warm(), eng.warm())
    assert a == b and eng.state == "ready" and a["device"] == "cpu" and a["tts_device"] == "cpu" and "warm_s" in a
    assert proc.worker.engines.loads == 1
    assert await eng.warm() is eng.info                                  # already warm
    assert eng.status() == dict(state="ready", error="", **eng.info)
    eng.close()


@pytest.mark.asyncio
async def test_stt_and_voices_wait_for_the_warm_up_themselves():
    eng, _ = make(FakeEngines(text="what time is it"))
    r = await eng.stt(bytes(32000))
    assert r == {"text": "what time is it", "ms": r["ms"], "audio_ms": 1000}
    assert await eng.voices() == ["af_heart", "am_adam"]
    with pytest.raises(LE.EngineError, match="cannot decode"):
        await eng.stt(b"bad")
    eng.close()


@pytest.mark.asyncio
async def test_tts_streams_chunks_and_reports_what_it_cost():
    eng, proc = make()
    stream = await eng.tts("Hello there", "af_heart", 1.1)
    chunks = [c async for c in stream]
    assert chunks == [bytes([1]) * 480, bytes([2]) * 480, bytes([3]) * 480]
    assert stream.finished and stream.stats["chunks"] == 3 and stream.stats["first_ms"] is not None
    assert proc.worker.engines.tts_calls == [("Hello there", "af_heart", 1.1)]
    assert not eng._queues                                                # the reply queue is released
    bad = await eng.tts("explode")
    with pytest.raises(LE.EngineError, match="synthesis failed"):
        [c async for c in bad]
    eng.close()


@pytest.mark.asyncio
async def test_cancelling_a_phrase_stops_the_engine():
    gate = threading.Event()
    engines = FakeEngines(chunks=5, gate=gate)
    eng, _ = make(engines)
    stream = await eng.tts("a long phrase")
    got = []

    async def consume():
        async for c in stream:
            got.append(c)
    task = asyncio.ensure_future(consume())
    for _ in range(300):
        if got:
            break
        await asyncio.sleep(0.01)
    assert got
    stream.cancel()
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.wait_for(task, 5)
    assert stream.stats.get("cancelled") is True and len(got) < 5
    stream.cancel()                                                       # cancelling a finished phrase is a no-op
    stream.close()
    eng.close()


@pytest.mark.asyncio
async def test_closing_a_stream_early_drops_its_queue():
    gate = threading.Event()
    eng, _ = make(FakeEngines(chunks=4, gate=gate))
    stream = await eng.tts("x")
    assert stream.rid in eng._queues
    stream.close()
    assert stream.rid not in eng._queues
    gate.set()
    await asyncio.sleep(0.05)                                             # late replies for a forgotten id are ignored
    eng.close()


@pytest.mark.asyncio
async def test_a_worker_that_will_not_start_is_reported_and_retried():
    calls = []

    def spawn():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("no such file")
        return FakeProc(FakeEngines())
    eng = LE.VoiceEngine(spawn=spawn)
    with pytest.raises(LE.EngineError, match="no such file"):
        await eng.warm()
    assert eng.state == "failed" and "no such file" in eng.error
    assert (await eng.warm())["device"] == "cpu" and eng.state == "ready"
    eng.close()


@pytest.mark.asyncio
async def test_a_worker_that_dies_fails_everything_waiting_and_says_why():
    eng, proc = make(stderr_lines=("Traceback", "ImportError: no module named kokoro"))
    await eng.warm()
    await asyncio.sleep(0.05)                                             # let the stderr reader collect
    proc.swallow = True                                                   # the worker stops answering ...
    stream = await eng.tts("x")
    proc.die()                                                            # ... and then exits
    await asyncio.sleep(0.05)
    assert eng.state == "failed" and "ImportError" in eng.error
    with pytest.raises(LE.EngineError, match="ImportError"):
        await asyncio.wait_for(_drain(stream), 3)
    with pytest.raises(LE.EngineError, match="not running"):
        eng._send_nowait({"op": "ping"})


async def _drain(stream):
    async for _ in stream:
        pass


@pytest.mark.asyncio
async def test_a_worker_that_exits_before_it_ever_answered_fails_the_warm_up():
    eng, proc = make(swallow=True)
    task = asyncio.ensure_future(eng.warm())
    await asyncio.sleep(0.05)
    proc.die()
    with pytest.raises(LE.EngineError, match="exited"):
        await asyncio.wait_for(task, 3)
    assert eng.state == "failed"


@pytest.mark.asyncio
async def test_a_silent_worker_times_out_and_a_broken_pipe_is_reported(monkeypatch):
    eng, proc = make()
    await eng.warm()
    proc.swallow = True
    monkeypatch.setattr(LE, "REPLY_TIMEOUT_S", 0.05)
    with pytest.raises(LE.EngineError, match="did not answer in time"):
        await eng._reply({"op": "ping"}, timeout=0.05)
    stream = await eng.tts("x")
    with pytest.raises(asyncio.TimeoutError):
        await _drain(stream)
    proc.swallow, proc.fail_writes = False, True
    with pytest.raises(LE.EngineError, match="not accepting requests"):
        await eng.stt(b"x")
    assert not eng._queues
    eng.close()


@pytest.mark.asyncio
async def test_noise_on_the_workers_stdout_is_ignored():
    eng, _ = make(stdout_noise=("library banner", "@@v {not json"))
    assert (await eng.warm())["device"] == "cpu"
    eng.close()


@pytest.mark.asyncio
async def test_close_is_safe_when_idle_or_when_the_pipes_are_gone():
    eng, proc = make()
    eng.close()                                                           # never started
    await eng.warm()
    proc.stdin_close = proc.close

    def boom():
        raise OSError("already closed")
    proc.close = boom
    proc.kill = boom
    eng.close()
    assert eng.state == "idle"


def test_default_spawn_runs_the_worker_in_the_isolated_environment(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setenv("VOICE_HOME", str(tmp_path / "v"))
    VS.write_state({"state": "ready", "device": "cuda", "stt_model": "small.en"})
    monkeypatch.setattr(LE.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, **kw) or "proc")
    assert LE.default_spawn()() == "proc"
    assert seen["cmd"][1:] == ["-u", "-m", "app.voice_worker"] and "venv" in seen["cmd"][0]
    assert seen["env"]["VOICE_DEVICE"] == "cuda" and seen["env"]["VOICE_STT_MODEL"] == "small.en" and seen["env"]["PYTHONPATH"]
    assert "HF_TOKEN" not in seen["env"] and seen["cwd"].endswith("backend")
    (tmp_path / "v" / "state.json").write_text("{}")
    LE.default_spawn()()
    assert seen["env"]["VOICE_DEVICE"] == "cpu" and seen["env"]["VOICE_STT_MODEL"] == "base.en"


def test_a_reply_for_an_event_loop_that_already_closed_is_dropped_quietly():
    loop = asyncio.new_event_loop()
    q: asyncio.Queue = asyncio.Queue()
    loop.close()
    LE.VoiceEngine._post((loop, q), {"ok": True})                          # must not raise (the reader thread would die)
    assert q.empty()
