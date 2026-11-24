"""The voice worker's line protocol (engines are fakes)."""
from __future__ import annotations

import base64
import io
import json
import threading

from app.voice_worker import PREFIX, Worker
from tests.live_fakes import FakeEngines


def run(lines: list[str], engines=None, finish=True):
    eng = engines or FakeEngines()
    out = io.StringIO()
    w = Worker(eng, out)
    for ln in lines:
        w.handle(ln)
    if finish:
        w.pool.shutdown(wait=True)
    msgs = [json.loads(ln[len(PREFIX):]) for ln in out.getvalue().splitlines() if ln.startswith(PREFIX)]
    return eng, w, msgs


def test_ping_voices_and_bad_requests():
    _, _, msgs = run([json.dumps({"id": 1, "op": "ping"}), json.dumps({"id": 2, "op": "voices"}), "not json", "[1]", json.dumps({"id": 3, "op": "dance"})])
    assert msgs[0] == {"id": 1, "ok": True} and msgs[1]["voices"] == ["af_heart", "am_adam"]
    assert msgs[2]["error"] == "the request is not valid JSON" and msgs[3]["error"] == "the request must be a JSON object"
    assert msgs[4]["ok"] is False and "unknown op 'dance'" in msgs[4]["error"]


def test_warm_runs_on_the_calling_thread_and_reports_the_engines():
    seen = []
    eng = FakeEngines()
    orig = eng.load
    eng.load = lambda: (seen.append(threading.current_thread() is threading.main_thread()), orig())[1]
    _, _, msgs = run([json.dumps({"id": 1, "op": "warm"})], eng)
    assert seen == [True] and msgs[0]["ok"] and msgs[0]["device"] == "cpu" and msgs[0]["tts_device"] == "cpu" and "load_ms" in msgs[0]


def test_stt_returns_text_and_timings_and_reports_errors():
    pcm = base64.b64encode(bytes(32000)).decode()
    _, _, msgs = run([json.dumps({"id": 5, "op": "stt", "pcm": pcm}), json.dumps({"id": 6, "op": "stt", "pcm": base64.b64encode(b"bad").decode()}),
                      json.dumps({"id": 7, "op": "stt"})])
    by = {m["id"]: m for m in msgs}
    assert by[5]["text"] == "hello there" and by[5]["audio_ms"] == 1000 and "ms" in by[5]
    assert by[6]["ok"] is False and "RuntimeError: cannot decode" in by[6]["error"]
    assert by[7]["ok"] is True                                             # no audio is just empty audio


def test_tts_streams_chunks_then_a_done_message():
    eng, _, msgs = run([json.dumps({"id": 9, "op": "tts", "text": "Hi there", "voice": "am_adam", "speed": 1.2})])
    chunks = [m for m in msgs if "chunk" in m]
    assert [m["n"] for m in chunks] == [0, 1, 2] and base64.b64decode(chunks[0]["chunk"]) == bytes([1]) * 480
    done = msgs[-1]
    assert done["done"] and done["chunks"] == 3 and done["first_ms"] is not None and "cancelled" not in done
    assert eng.tts_calls == [("Hi there", "am_adam", 1.2)]
    _, _, m2 = run([json.dumps({"id": 1, "op": "tts", "text": "x"})])
    assert m2[-1]["done"]
    _, _, m3 = run([json.dumps({"id": 2, "op": "tts", "text": "explode"})])
    assert m3[-1]["ok"] is False and "synthesis failed" in m3[-1]["error"]


def test_cancel_stops_a_synthesis_that_is_still_streaming():
    gate = threading.Event()
    eng = FakeEngines(chunks=5, gate=gate)
    out = io.StringIO()
    w = Worker(eng, out)
    w.handle(json.dumps({"id": 4, "op": "tts", "text": "a long phrase"}))
    for _ in range(200):                                                   # wait until the first chunk was sent (the worker is now parked at the gate)
        if '"n":0' in out.getvalue():
            break
        threading.Event().wait(0.01)
    w.handle(json.dumps({"op": "cancel", "id": 4}))
    w.handle(json.dumps({"op": "cancel", "id": 999}))                      # cancelling something unknown is harmless
    gate.set()
    w.pool.shutdown(wait=True)
    msgs = [json.loads(ln[len(PREFIX):]) for ln in out.getvalue().splitlines()]
    assert msgs[-1]["done"] and msgs[-1]["cancelled"] is True and msgs[-1]["chunks"] < 5
    assert eng.closed == 1                                                  # the generator was closed, so the engine stopped


def test_serve_reads_requests_on_a_helper_thread_until_the_input_ends():
    out = io.StringIO()
    w = Worker(FakeEngines(), out)
    w.serve(iter([json.dumps({"id": 1, "op": "ping"}) + "\n", "\n", json.dumps({"id": 2, "op": "tts", "text": "x"}) + "\n"]))
    msgs = [json.loads(ln[len(PREFIX):]) for ln in out.getvalue().splitlines()]
    assert {m["id"] for m in msgs} == {1, 2} and any(m.get("done") for m in msgs)


def test_default_output_is_stdout(capsys):
    w = Worker(FakeEngines())
    w.handle(json.dumps({"id": 1, "op": "ping"}))
    assert capsys.readouterr().out.startswith(PREFIX)
