"""The d1 worker line protocol (app/d1_worker.py) and its backend client (app/d1_client.py).

The protocol is tested in-process with injected fakes, and the client against a REAL subprocess (tests/fake_d1_worker.py)
over real pipes - no model, torch or download.
"""
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app import d1_client, d1_spec, d1_worker
from app import mcp_d1 as M
from app.d1_client import D1Worker, WorkerTimeout, WorkerUnavailable

FAKE = str(Path(__file__).with_name("fake_d1_worker.py"))


# ------------------------------------------------------------------------------------------------ the worker protocol
def test_handle_covers_every_op_and_bad_input():
    run = lambda state, questions: {"ok": True, "answers": {"echo": [state, questions]}}
    warm = lambda: {"ok": True, "device": "cpu"}
    assert d1_worker.handle(json.dumps({"id": 1, "op": "ping"}), run, warm) == {"id": 1, "ok": True}
    assert d1_worker.handle(json.dumps({"id": 2, "op": "warm"}), run, warm) == {"id": 2, "ok": True, "device": "cpu"}
    assert d1_worker.handle(json.dumps({"id": 3, "state": "s", "questions": {"q": 1}}), run, warm) == {"id": 3, "ok": True, "answers": {"echo": ["s", {"q": 1}]}}
    assert d1_worker.handle(json.dumps({"id": 4, "op": "decide", "state": "x"}), run, warm)["answers"]["echo"] == ["x", None]
    assert d1_worker.handle(json.dumps({"id": 5, "op": "rm -rf"}), run, warm) == {"id": 5, "ok": False, "error": "unknown op 'rm -rf'"}
    assert d1_worker.handle("{nope", run, warm)["error"] == "the request is not valid JSON"
    assert d1_worker.handle("[1]", run, warm)["error"] == "the request must be a JSON object"


def test_serve_answers_each_line_and_stops_at_eof():
    stdin = io.StringIO(json.dumps({"id": 1, "op": "ping"}) + "\n\n" + json.dumps({"id": 2, "op": "warm"}) + "\n")
    out = io.StringIO()
    d1_worker.serve(stdin, out, run=lambda s, q: {"ok": True}, warm=lambda: {"ok": True, "device": "cuda"})
    lines = out.getvalue().splitlines()
    assert [json.loads(l[len(d1_worker.PREFIX):]) for l in lines] == [{"id": 1, "ok": True}, {"id": 2, "ok": True, "device": "cuda"}]
    assert all(l.startswith(d1_worker.PREFIX) for l in lines)


def test_serve_defaults_to_the_real_runner_and_warm(monkeypatch):
    fake = M.Loaded(object(), "cpu", "float32")
    monkeypatch.setattr(M, "_loaded", None)
    monkeypatch.setattr(M, "_loader", lambda: fake)
    out = io.StringIO()
    d1_worker.serve(io.StringIO(json.dumps({"id": 1, "op": "warm"}) + "\n" + json.dumps({"id": 2, "state": None, "questions": None}) + "\n"), out)
    warm, bad = [json.loads(l[len(d1_worker.PREFIX):]) for l in out.getvalue().splitlines()]
    assert warm["ok"] and warm["device"] == "cpu" and warm["loaded_now"] is True
    assert bad["ok"] is False and "state is required" in bad["error"]          # mcp_d1.run's own validation


def test_warm_reports_a_load_failure(monkeypatch):
    monkeypatch.setattr(M, "_loaded", None)
    monkeypatch.setattr(M, "_loader", lambda: (_ for _ in ()).throw(RuntimeError("weights are missing")))
    assert d1_worker._warm() == {"ok": False, "error": "RuntimeError: weights are missing"}


def test_main_keeps_library_output_off_the_protocol_stream(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"id": 1, "op": "ping"}) + "\n"))
    seen = {}
    real_serve = d1_worker.serve
    monkeypatch.setattr(d1_worker, "serve", lambda stdin, proto: (seen.update(stdout_during=sys.stdout), real_serve(stdin, proto)))
    d1_worker.main()
    assert out.getvalue().startswith(d1_worker.PREFIX)
    assert seen["stdout_during"] is sys.stderr                                  # print() inside the model code goes to stderr


def test_module_entrypoint(monkeypatch):
    import runpy
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    runpy.run_module("app.d1_worker", run_name="__main__")


# ------------------------------------------------------------------------------------------------ the client
def spawn_fake(mode="ok"):
    def spawn():
        env = {**os.environ, "FAKE_D1_MODE": mode, "PYTHONIOENCODING": "utf-8"}
        return subprocess.Popen([sys.executable, FAKE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", bufsize=1, env=env)
    return spawn


def wait_state(w, state, seconds=20):
    end = time.time() + seconds
    while time.time() < end and w.state != state:
        time.sleep(0.02)
    return w.state


@pytest.fixture
def worker():
    made = []

    def make(mode="ok", model="d1-2b"):
        w = D1Worker(model, spawn_fake(mode))
        made.append(w)
        return w
    yield make
    for w in made:
        w.close()


def test_a_cold_worker_is_never_waited_for_and_loads_in_the_background(worker):
    w = worker()
    assert w.state == "idle" and w.public()["model"] == "d1-2b"
    with pytest.raises(WorkerUnavailable, match="still loading"):
        w.decide("hi", {"q": {}}, 2)                                           # starts loading, answers immediately
    assert wait_state(w, "ready") == "ready"
    assert w.device == "cuda" and w.load_seconds == 0.5
    r = w.decide({"request": "hello"}, {"q": {}}, 5)
    assert r["ok"] and r["answers"]["reasoning"]["noul"] == 0.9 and r["meta"]["echo"] == {"request": "hello"}
    w.warm()                                                                   # already loaded: nothing happens
    assert w.state == "ready"


def test_a_slow_decision_times_out_and_its_late_answer_is_dropped(worker):
    w = worker("slow")
    w.warm()
    wait_state(w, "ready")
    with pytest.raises(WorkerTimeout, match="no answer within 0.3 s"):
        w.decide("x", {"q": {}}, 0.3)
    time.sleep(1.6)                                                            # the late reply arrives; nobody is waiting
    assert w._replies == {} and w._waiting == set()
    assert w.decide("y", {"q": {}}, 5)["ok"]                                   # and the worker still works


def test_load_failures_are_reported_and_retried_only_after_a_pause(worker):
    t = [0.0]
    w = D1Worker("d1-3b", spawn_fake("warm_fail"), clock=lambda: t[0])
    try:
        w.warm()
        assert wait_state(w, "failed") == "failed" and "weights are missing" in w.error
        with pytest.raises(WorkerUnavailable, match="not available: RuntimeError: weights are missing"):
            w.decide("x", {}, 1)                                               # failed recently: no new attempt yet
        assert w.state == "failed"
        t[0] = d1_client.RETRY_AFTER_S + 1
        w.warm()                                                               # enough time passed: tries again
        assert w.state in ("loading", "failed")
        wait_state(w, "failed")
    finally:
        w.close()


def test_a_worker_that_dies_while_loading_or_deciding_is_reported(worker):
    w = worker("crash_warm")
    w.warm()
    assert wait_state(w, "failed") == "failed" and "exited" in w.error
    w2 = worker("crash_decide")
    w2.warm()
    wait_state(w2, "ready")
    with pytest.raises(WorkerUnavailable, match="cuda out of memory"):
        w2.decide("x", {"q": {}}, 10)
    assert w2.state == "failed" and "cuda out of memory" in w2.error            # the worker's last stderr lines say why
    with pytest.raises(WorkerUnavailable):
        w2.decide("x", {"q": {}}, 1)


def test_protocol_noise_is_ignored(worker):
    w = worker("noise")
    w.warm()
    assert wait_state(w, "ready") == "ready"
    assert w.decide("x", {"q": {}}, 5)["ok"]


def test_request_without_a_process_and_with_a_broken_pipe():
    w = D1Worker("d1-2b", spawn_fake())
    with pytest.raises(WorkerUnavailable, match="not running"):
        w.request({"op": "ping"}, 1)

    class Dead:
        class stdin:
            @staticmethod
            def write(_):
                raise BrokenPipeError()
        stdout = stderr = io.StringIO("")

    w._proc = Dead()
    with pytest.raises(WorkerUnavailable, match="cannot reach the decision-model worker: BrokenPipeError"):
        w.request({"op": "ping"}, 1)


def test_spawn_errors_fail_the_load_and_close_is_safe():
    def spawn():
        raise FileNotFoundError("the d1 environment is gone")
    w = D1Worker("d1-2b", spawn)
    w.warm()
    assert wait_state(w, "failed") == "failed" and "d1 environment is gone" in w.error
    w.close()
    assert w.state == "idle"

    class Stubborn:
        class stdin:
            @staticmethod
            def close():
                raise OSError("pipe")
        stdout = stderr = io.StringIO("")

        @staticmethod
        def terminate():
            raise OSError("already gone")
    w._proc = Stubborn()
    w.close()                                                                  # neither OSError escapes
    assert w._proc is None


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError, match="unknown decision model"):
        D1Worker("d1-9b")


def test_default_spawn_starts_the_environment_python_with_the_chosen_model(tmp_path, monkeypatch):
    monkeypatch.setenv("D1_HOME", str(tmp_path / "d1"))
    monkeypatch.setenv("HF_TOKEN", "secret")
    seen = {}
    monkeypatch.setattr(d1_client.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, **kw) or "proc")
    assert d1_client.default_spawn("d1-3b")() == "proc"
    assert seen["cmd"] == [str(d1_spec.env_python(tmp_path / "d1")), "-m", "app.d1_worker"]
    assert seen["env"]["D1_MODEL"] == "d1-3b" and seen["env"]["D1_HOME"] == str(tmp_path / "d1") and "HF_TOKEN" not in seen["env"]
    assert seen["env"]["PYTHONPATH"].endswith("backend") and seen["stdin"] == subprocess.PIPE
