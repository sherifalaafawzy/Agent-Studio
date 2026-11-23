"""Guard models that fetch themselves: a missing Ollama model is downloaded in the background (Test button, saving a profile, live chat),
and the decision model is warmed and waited for when a person presses Try it. Fakes only - no Ollama, no download, no real model."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app import auto, d1_client
from app.d1_client import WorkerLoading, WorkerUnavailable
from app.easy import ollama_local
from app.guardrails import modelguard, pulls
from app.guardrails.base import WAIT_FOR_LOAD, Env, GuardError, GuardNotInstalled, GuardPreparing
from app.guardrails.pulls import GuardPuller, norm, plain_error
from app.guardrails.service import GuardrailError

BASE = "http://127.0.0.1:11434"
MODEL = "llama-guard3:8b"


def run(c):
    return asyncio.run(c)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def script(*events, hold=None, clock=None, step=0.0):
    """A fake ollama_local.pull: yields the events; `hold` (an asyncio.Event) pauses it before the last one; `step` seconds pass on `clock` between events."""
    calls = []

    async def fake(name, base, headers=None):
        calls.append((name, base, headers))
        for i, ev in enumerate(events):
            if hold is not None and i == len(events) - 1:
                await hold.wait()
            if i and clock is not None:
                clock.t += step
            yield ev
    fake.calls = calls
    return fake


def progress(done, total):
    return dict(type="progress", status="pulling", completed=done, total=total, percent=round(done * 100 / total, 1))


def puller(*events, have=(), free=100.0, hold=None, clock=None, step=0.0):
    clock = clock or Clock()
    fake = script(*events, hold=hold, clock=clock, step=step)
    p = GuardPuller(list_models=lambda base, headers: list(have), pull=fake, free_gb=lambda: free, clock=clock)
    p.fake = fake
    return p


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


# ------------------------------------------------------------------------------------------------- names and errors
def test_names_and_where():
    assert norm("llama-guard3") == "llama-guard3:latest" and norm(" a/b:1 ") == "a/b:1" and norm("host:5000/m") == "host:5000/m:latest"
    assert pulls.is_local("http://127.0.0.1:11434") and pulls.is_local("http://localhost:1") and not pulls.is_local("http://gpu-box:11434")


@pytest.mark.parametrize("msg,kind", [
    ("Ollama isn't reachable (ConnectError). Start it first.", "ollama_down"), ("write: no space left on device", "disk"),
    ("pull model manifest: file does not exist", "unknown_model"), ("dial tcp: lookup registry.ollama.ai: no such host", "network"),
    ("something odd", "other")])
def test_plain_errors_are_sentences_a_person_can_act_on(msg, kind):
    k, text = plain_error(msg, "m:1")
    assert k == kind and text.endswith(".") and "Traceback" not in text
    assert ("Start Ollama" in text) == (kind == "ollama_down") and ("'m:1'" in text) == (kind == "unknown_model")


# ------------------------------------------------------------------------------------------------- the puller
def test_a_missing_model_is_downloaded_once_with_progress_then_done():
    async def main():
        clock = Clock()
        hold = asyncio.Event()
        p = puller(progress(1_000_000, 4_000_000), progress(2_000_000, 4_000_000), dict(type="done", model=MODEL), hold=hold, clock=clock, step=2.0)
        a = await p.ensure(BASE, MODEL, licence="Llama licence", size_gb=4.9)
        b = await p.ensure(BASE, MODEL)                                      # a second chat asks at the same time: no second download
        await settle()
        assert a["status"] == "downloading" and a["where"] == "on this PC" and a["licence"] == "Llama licence" and a["size_hint_gb"] == 4.9
        assert b["key"] == a["key"] and len(p.fake.calls) == 1 and p.fake.calls[0][:2] == (MODEL, BASE)
        st = p.state(BASE, MODEL)
        assert st["status"] == "downloading" and st["completed"] == 2_000_000 and st["total"] == 4_000_000 and st["percent"] == 50.0
        assert st["speed_bps"] == 1_000_000 and st["eta_s"] == 2
        hold.set()
        await settle()
        st = p.state(BASE, MODEL)
        assert st["status"] == "done" and st["percent"] == 100.0 and [s["model"] for s in p.states()] == [MODEL]
        assert (await p.ensure(BASE, MODEL, check=False))["status"] == "done"      # chat saw a 404 right after: it may retry once
        assert p.cancel(BASE, MODEL) is False                                       # nothing is running
    run(main())


def test_a_model_that_is_already_there_is_not_pulled():
    async def main():
        p = puller(have=["llama-guard3:8b"])
        assert (await p.ensure(BASE, MODEL))["status"] == "ready" and p.fake.calls == [] and p.states() == []
        assert await p.has(BASE, "llama-guard3:8b") and not await p.has(BASE, "other")
    run(main())


def test_ollama_not_running_is_explained():
    async def main():
        def down(base, headers):
            raise ollama_local.LocalError("Ollama isn't reachable (ConnectError). Start it first.")
        p = GuardPuller(list_models=down, pull=script(), free_gb=lambda: 100.0)
        st = await p.ensure(BASE, MODEL)
        assert st["status"] == "error" and st["error_kind"] == "ollama_down" and "Start Ollama" in st["message"]
    run(main())


def test_not_enough_disk_space_refuses_before_and_during_the_download():
    async def main():
        p = puller(free=3.0)
        st = await p.ensure(BASE, MODEL, size_gb=4.9)                       # needs 5.6 GB, 3 free
        assert st["status"] == "error" and st["error_kind"] == "disk" and "5.6 GB" in st["message"] and "3.0 GB is free" in st["message"] and p.fake.calls == []
        st = await puller(free=1.0).ensure(BASE, "m:1")                      # size unknown: needs the 2 GB minimum
        assert st["error_kind"] == "disk" and "2.0 GB" in st["message"]
        assert (await puller(free=None).ensure(BASE, "m:1"))["status"] == "downloading"      # free space unreadable: carry on
        remote = await puller(progress(1, 2), free=0.1).ensure("http://gpu-box:11434", MODEL, size_gb=4.9)
        assert remote["status"] == "downloading" and remote["where"] == "on this network"   # another computer's disk is not ours to read
        # the real size shows up once Ollama starts: 10 GB needed, 5 GB free -> stop and say so
        q = puller(progress(1_000_000_000, 10_000_000_000), dict(type="done", model=MODEL), free=5.0)
        await q.ensure(BASE, "m:1", size_gb=None)
        await settle()
        st = q.state(BASE, "m:1")
        assert st["status"] == "error" and st["error_kind"] == "disk" and "9.0 GB more" in st["message"]
    run(main())


def test_failures_are_translated_and_a_retry_waits_unless_forced():
    async def main():
        clock = Clock()
        p = puller(dict(type="error", message="pull model manifest: file does not exist"), clock=clock)
        await p.ensure(BASE, "nope:1")
        await settle()
        st = p.state(BASE, "nope:1")
        assert st["status"] == "error" and st["error_kind"] == "unknown_model" and "'nope:1'" in st["message"]
        assert (await p.ensure(BASE, "nope:1", check=False))["status"] == "error" and len(p.fake.calls) == 1     # a chat does not hammer it
        await p.ensure(BASE, "nope:1", check=False, force=True)                                                    # "try again" does
        await settle()
        assert len(p.fake.calls) == 2
        await settle()
        clock.t += pulls.RETRY_AFTER_S + 1
        await p.ensure(BASE, "nope:1", check=False)
        await settle()
        assert len(p.fake.calls) == 3
        await settle()
        clock.t += pulls.KEEP_S + 1
        assert p.states() == []                                                                                     # old results are dropped
    run(main())


def test_a_stream_that_ends_early_and_a_cancelled_download_are_reported():
    async def main():
        p = puller(progress(1, 4))
        await p.ensure(BASE, "m:1", size_gb=1)
        await settle()
        assert p.state(BASE, "m:1")["status"] == "error" and "ended early" in p.state(BASE, "m:1")["message"]
        hold = asyncio.Event()
        q = puller(progress(1, 4), dict(type="done", model="m:1"), hold=hold)
        await q.ensure(BASE, "m:1", size_gb=1)
        await settle()
        assert q.cancel(BASE, "m:1") is True
        await settle()
        st = q.state(BASE, "m:1")
        assert st["status"] == "cancelled" and "resumes" in st["message"]
        assert q.cancel(BASE, "zzz:1") is False
        assert q.state(BASE, "zzz:1") is None
    run(main())


def test_the_default_puller_uses_the_phase_1_ollama_helpers(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"models": [{"name": "llama-guard3:8b", "size": 1}]})
    monkeypatch.setattr(ollama_local, "TRANSPORT", httpx.MockTransport(handler))
    p = GuardPuller()
    assert run(p.has(BASE, MODEL, {"Authorization": "Bearer k"})) and seen["auth"] == "Bearer k"
    assert p._pull is ollama_local.pull and isinstance(p._free(), (float, type(None)))


# ------------------------------------------------------------------------------------------------- the model guard
def guard_env(p, handler):
    return Env(http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)), pulls=p)


def test_check_on_a_missing_model_starts_the_download_then_works_once_it_arrived():
    async def main():
        hold = asyncio.Event()
        p = puller(progress(1_000_000, 2_000_000), dict(type="done", model=MODEL), hold=hold)
        have = {"ready": False}

        def handler(req: httpx.Request):
            if not have["ready"]:
                return httpx.Response(404, json={"error": "model not found"})
            return httpx.Response(200, json={"message": {"content": "safe"}})
        g = modelguard._make({"family": "llama_guard"}, {"api_key": "k"}, guard_env(p, handler))
        with pytest.raises(GuardPreparing) as e:
            await g.check("hello", "input")
        await settle()
        assert e.value.what == "downloading" and e.value.notice == "Guard model llama-guard3:8b is downloading" and e.value.state["size_hint_gb"] == 4.9
        assert p.fake.calls[0][2] == {"Authorization": "Bearer k"} and "Llama" in e.value.state["licence"]
        await settle()
        with pytest.raises(GuardPreparing, match=r"downloading, 50%"):
            await g.check("hello", "input")
        assert len(p.fake.calls) == 1                                                    # no second download
        hold.set()
        await settle()
        have["ready"] = True
        assert (await g.check("hello", "input")).flagged is False                          # a normal check once it is there
    run(main())


def test_check_right_after_the_download_finished_asks_again_and_gives_up_after_one_retry():
    async def main():
        p = puller(dict(type="done", model=MODEL))
        await p.ensure(BASE, MODEL)
        await settle()
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(404, json={})
        g = modelguard._make({}, {}, guard_env(p, handler))
        with pytest.raises(GuardError, match="not on that Ollama server"):
            await g.check("x", "input")
        assert len(calls) == 2                                                             # asked, saw done, asked once more
    run(main())


def test_check_reports_a_failed_download_plainly():
    async def main():
        p = puller(dict(type="error", message="pull model manifest: file does not exist"))
        g = modelguard._make({"model": "nope:1"}, {}, guard_env(p, lambda r: httpx.Response(404, json={})))
        with pytest.raises(GuardPreparing):
            await g.check("x", "input")
        await settle()
        with pytest.raises(GuardError, match="does not know a model called 'nope:1'") as e:
            await g.check("x", "input")
        assert not isinstance(e.value, GuardPreparing)
    run(main())


def test_ollama_target_for_saved_profiles():
    assert modelguard.ollama_target({}, {}) == dict(base=BASE, model=MODEL, headers=None, licence=modelguard.FAMILIES["llama_guard"]["licence"], size_gb=4.9)
    t = modelguard.ollama_target({"family": "shieldgemma", "base_url": "http://gpu:11434/", "model": "mine:1"}, {"api_key": "k"})
    assert t["base"] == "http://gpu:11434" and t["model"] == "mine:1" and t["headers"] == {"Authorization": "Bearer k"} and t["size_gb"] is None
    assert modelguard.ollama_target({"api": "openai"}, {}) is None and modelguard.ollama_target({"family": "generic"}, {}) is None


# ------------------------------------------------------------------------------------------------- the service and API
@pytest.fixture
def svc(state):
    return state.guardrails


def use(svc, p, handler=None):
    svc.pulls = p
    svc.env = guard_env(p, handler or (lambda r: httpx.Response(404, json={})))
    svc._cache.clear()


def model_profile(**kw):
    return {"id": "lg", "name": "Llama Guard", "kind": "model", "mode": "block", "fail": "closed", "params": {"family": "llama_guard"}, **kw}


def test_try_it_starts_the_download_and_reports_progress(svc):
    async def main():
        p = puller(progress(2_000_000, 4_000_000), dict(type="done", model=MODEL), hold=asyncio.Event())
        use(svc, p)
        r = await svc.test({"kind": "model", "id": "draft", "name": "D", "params": {}}, "hello", "input")
        assert r["ok"] is False and r["preparing"]["what"] == "downloading" and r["preparing"]["model"] == MODEL and r["error"].startswith("Guard model")
        assert r["preparing"]["licence"] and r["preparing"]["size_hint_gb"] == 4.9
        assert WAIT_FOR_LOAD.get() is False                                               # the flag never leaks out of a test call
    run(main())


def test_evaluate_while_a_model_downloads_follows_the_fail_setting_and_does_not_wait(svc):
    async def main():
        p = puller(progress(1_000_000, 4_000_000), dict(type="done", model=MODEL), hold=asyncio.Event())
        use(svc, p)
        svc.set_enabled(True)
        svc.save(model_profile(default=True))
        d = await svc.evaluate("input", "hello there", "engineer", None)
        assert d.blocked and "Guard model llama-guard3:8b is downloading" in d.events[0]["reason"] and "blocked because this profile stops" in d.events[0]["reason"]
        assert d.events[0]["category"] == "guard_preparing" and d.events[0]["action"] == "blocked"
        svc.save({"id": "lg", "fail": "open"})
        d = await svc.evaluate("input", "hello there", "engineer", None)
        assert not d.blocked and d.events[0]["action"] == "error" and "this check was skipped" in d.events[0]["reason"]
        await settle()
        assert len(p.fake.calls) == 1                                                      # one download for all those chats
    run(main())


def test_saving_a_profile_or_switching_guardrails_on_gets_the_model(svc):
    async def main():
        p = puller(dict(type="done", model=MODEL))
        use(svc, p)
        svc.save(model_profile())
        assert await svc.prepare("lg") == []                                               # master switch off: nothing yet...
        out = await svc.prepare("lg", require_enabled=False)                               # ...unless the profile was just saved
        assert [s["model"] for s in out] == [MODEL]
        svc.set_enabled(True)
        svc.save({"id": "off1", "name": "Off", "kind": "model", "mode": "off"})
        svc.save({"id": "oai", "name": "Remote", "kind": "model", "params": {"api": "openai", "model": "x"}})
        out = await svc.prepare()
        assert [s["model"] for s in out] == [MODEL]                                        # only the Ollama profile that is on
        assert [x["profile"] for x in svc.pull_states()] == ["lg"] and svc.cancel_pull(BASE, MODEL) in (True, False)
        await p.ensure("http://other:1", "zz:1", size_gb=1)                                  # a download no profile asked for (e.g. since deleted)
        assert {x["profile"] for x in svc.pull_states()} == {"lg", ""}
        svc.pulls = None
        assert await svc.prepare() == [] and svc.pull_states() == [] and svc.cancel_pull(BASE, MODEL) is False
        with pytest.raises(GuardrailError, match="nothing to download"):
            await svc.retry_pull("lg")
    run(main())


def test_retry_pull_and_its_refusals(svc):
    async def main():
        p = puller(dict(type="error", message="dial tcp: no such host"))
        use(svc, p)
        svc.save(model_profile())
        await svc.prepare("lg", require_enabled=False)
        await settle()
        assert p.state(BASE, MODEL)["error_kind"] == "network"
        st = await svc.retry_pull("lg")
        await settle()
        assert st["status"] == "downloading" and len(p.fake.calls) == 2
        for pid in ("basic", "zzz"):
            with pytest.raises(GuardrailError, match="nothing to download"):
                await svc.retry_pull(pid)
    run(main())


def test_the_api_exposes_downloads_and_starts_them_on_save(state):
    from fastapi.testclient import TestClient

    from app.main import create_app
    with TestClient(create_app(state)) as client:                 # one event loop for every request, like the real server (background downloads survive)
        api_flow(client, state)


def api_flow(client, state):
    p = puller(progress(1_000_000, 2_000_000), dict(type="done", model=MODEL), hold=asyncio.Event())
    use(state.guardrails, p)
    assert client.get("/api/guardrails/pulls").json() == {"pulls": []}
    assert client.put("/api/guardrails/profiles", json=model_profile()).status_code == 200      # saving starts the download even with the switch off
    pulls_now = client.get("/api/guardrails/pulls").json()["pulls"]
    assert [x["model"] for x in pulls_now] == [MODEL] and pulls_now[0]["status"] == "downloading" and pulls_now[0]["profile"] == "lg"
    assert client.post("/api/guardrails/pulls/cancel", json={"base": BASE, "model": MODEL}).json() == {"cancelled": True}
    assert client.post("/api/guardrails/pulls/cancel", json={}).json() == {"cancelled": False}
    r = client.post("/api/guardrails/pulls/retry", json={"profile": "lg"})
    assert r.status_code == 200 and r.json()["status"] == "downloading"
    assert client.post("/api/guardrails/pulls/retry", json={"profile": "basic"}).status_code == 422
    assert client.put("/api/guardrails/enabled", json={"enabled": True}).json()["enabled"] is True
    assert client.put("/api/guardrails/enabled", json={"enabled": False}).json()["enabled"] is False


# ------------------------------------------------------------------------------------------------- the decision model
class W:
    """A fake d1 worker: records how decide was called."""

    def __init__(self, mode="ready"):
        self.mode, self.calls, self.warmed = mode, [], 0

    def decide(self, state, questions, timeout, wait_for_load=False, load_wait=None):
        self.calls.append((wait_for_load, load_wait))
        if self.mode == "loading" and not wait_for_load:
            raise WorkerLoading("the decision model is still loading")
        if self.mode == "still":
            raise WorkerLoading("the decision model is still loading")
        if self.mode == "failed":
            raise WorkerUnavailable("the decision model could not be loaded: weights are missing")
        return {"ok": True, "answers": {k: {"noul": 0.1} for k in ("injection", "harm", "exfiltration", "destructive")}}

    def warm(self):
        self.warmed += 1

    def public(self):
        return {"model": "d1-2b", "state": "loading", "device": "cpu", "load_seconds": 41.0, "loading_for_s": 3.0}


def d1_profile(**kw):
    return {"id": "gate", "name": "Gate", "kind": "d1", "mode": "block", "fail": "open", "default": True, **kw}


def wire(svc, worker, ready=True):
    svc.d1_ready, svc.d1_worker = (lambda m: ready), (lambda m: worker)
    svc.env.decide = svc._d1_decide
    svc._cache.clear()


def test_try_it_on_the_decision_model_waits_for_the_load_but_chat_does_not(svc):
    async def main():
        svc.set_enabled(True)
        svc.save(d1_profile())
        w = W("loading")
        wire(svc, w)
        r = await svc.test({"id": "gate"}, "hello", "input")                              # a person pressed Try it: asks the worker to wait
        assert r["ok"] is True and w.calls == [(True, 25.0)]
        d = await svc.evaluate("input", "hello", "a", None)                               # live chat: fails fast, the fail setting decides
        assert not d.blocked and w.calls[-1] == (False, None)
        assert "Decision model is loading" in d.events[0]["reason"] and "skipped" in d.events[0]["reason"] and d.events[0]["category"] == "guard_preparing"
        svc.save({"id": "gate", "fail": "closed"})
        d = await svc.evaluate("input", "hello", "a", None)
        assert d.blocked and "Decision model is loading: this check is blocked" in d.events[0]["reason"]
    run(main())


def test_try_it_reports_a_model_that_is_still_loading_a_failure_and_a_missing_install(svc):
    async def main():
        svc.save(d1_profile())
        wire(svc, W("still"))
        r = await svc.test({"id": "gate"}, "hello", "input")
        assert r["ok"] is False and r["preparing"]["what"] == "loading" and r["preparing"]["load_seconds"] == 41.0 and r["error"] == "Decision model is loading"
        wire(svc, W("failed"))
        r = await svc.test({"id": "gate"}, "hello", "input")
        assert r["ok"] is False and "could not answer: the decision model could not be loaded" in r["error"] and "preparing" not in r
        wire(svc, W(), ready=False)
        r = await svc.test({"id": "gate"}, "hello", "input")
        assert r["ok"] is False and r["install"] == "d1" and "not installed yet" in r["error"] and "MCP servers" in r["error"]
        assert not isinstance(GuardNotInstalled("m", "d1"), GuardPreparing)
    run(main())


def test_the_decision_model_is_warmed_ahead_when_a_profile_that_uses_it_is_on(svc):
    w = W()
    wire(svc, w)
    svc.warm_d1()
    assert w.warmed == 0                                                                    # master switch off
    svc.set_enabled(True)
    svc.save(d1_profile())
    svc.save({"id": "off2", "name": "Off", "kind": "d1", "mode": "off"})
    svc.warm_d1()
    assert w.warmed == 1                                                                    # only the profile that is on
    wire(svc, w, ready=False)
    svc.warm_d1()
    assert w.warmed == 1                                                                    # not installed: nothing to warm
    svc.d1_ready, svc.d1_worker = (lambda m: True), (lambda m: None)
    svc.warm_d1()                                                                           # no worker object yet: no crash
    assert run(svc.prepare()) == [] and w.warmed == 1


# ------------------------------------------------------------------------------------------------- the d1 client itself
def spawn_never():
    raise AssertionError("not used")


class StubWorker(d1_client.D1Worker):
    """A D1Worker whose load is driven by the test (no subprocess)."""

    def __init__(self):
        super().__init__("d1-2b", spawn_never)
        self.started = 0

    def warm(self):
        self.started += 1
        if self.state not in ("loading", "ready"):
            self.state, self.error, self._loading_since = "loading", "", self._clock()


def settle_to(w, state, error="", delay=0.05):
    import threading
    import time

    def later():
        time.sleep(delay)
        with w._cv:
            w.state, w.error = state, error
            w._cv.notify_all()
    threading.Thread(target=later, daemon=True).start()


def test_decide_fails_fast_while_loading_but_can_wait_when_asked():
    w = StubWorker()
    with pytest.raises(WorkerLoading, match="still loading"):
        w.decide({}, {}, 1)
    assert w.started == 1 and isinstance(WorkerLoading("x"), WorkerUnavailable)
    assert w.public()["loading_for_s"] is not None
    with pytest.raises(WorkerLoading):
        w.decide({}, {}, 1, wait_for_load=True, load_wait=0.3)                              # still loading after the wait
    w.request = lambda payload, timeout: {"ok": True, "answers": {}}                        # the load finishes while we wait
    settle_to(w, "ready")
    assert w.decide({}, {}, 1, wait_for_load=True, load_wait=5) == {"ok": True, "answers": {}}
    assert w.public()["loading_for_s"] is None


def test_waiting_for_a_load_that_fails_or_a_worker_that_was_stopped():
    w = StubWorker()
    w.warm()
    settle_to(w, "failed", "weights are missing")
    with pytest.raises(WorkerUnavailable, match="could not be loaded: weights are missing"):
        w.wait_ready(5)
    w2 = StubWorker()
    w2.warm()
    settle_to(w2, "idle")
    with pytest.raises(WorkerUnavailable, match="was stopped"):
        w2.wait_ready(5)
    w3 = d1_client.D1Worker("d1-2b", spawn_never)
    w3.state, w3.error, w3._failed_at = "failed", "boom", w3._clock()
    with pytest.raises(WorkerUnavailable, match="could not be loaded: boom"):                  # failed just now: not retried, not waited for
        w3.decide({}, {}, 1)


def test_the_load_thread_wakes_waiters_whether_it_works_or_fails():
    class Boot(d1_client.D1Worker):
        def __init__(self, ok):
            super().__init__("d1-2b", spawn_never)
            self.ok = ok

        def _start_process(self):
            self._proc = object()
            return self._proc

        def request(self, payload, timeout):
            return {"ok": True, "device": "cpu", "load_seconds": 1.5} if self.ok else {"ok": False, "error": "no gpu"}
    good = Boot(True)
    good.warm()
    good.wait_ready(5)
    assert good.state == "ready" and good.device == "cpu"
    bad = Boot(False)
    bad.warm()
    with pytest.raises(WorkerUnavailable, match="no gpu"):
        bad.wait_ready(5)

    class Dead(Boot):
        def _start_process(self):
            raise OSError("cannot spawn")
    dead = Dead(True)
    dead.warm()
    with pytest.raises(WorkerUnavailable, match="cannot spawn"):
        dead.wait_ready(5)


# ------------------------------------------------------------------------------------------------- Auto "Try it"
def test_auto_try_it_waits_for_the_load_but_live_routing_says_it_is_loading(state):
    seen = []

    class AW:
        def __init__(self, model):
            self.model = model

        def warm(self):
            pass

        def close(self):
            pass

        def decide(self, state_, questions, timeout, wait_for_load=False, load_wait=None):
            seen.append((wait_for_load, load_wait))
            if not wait_for_load:
                raise WorkerLoading("the decision model is still loading")
            return {"ok": True, "answers": {k: {"noul": 0.1} for k in questions}}
    router = auto.AutoRouter(state.db, state.providers, state.guard, d1_ready=lambda m: True, worker_factory=AW)
    router.set_config({"engine": "d1-2b"})
    r = asyncio.run(router.route("hello"))
    assert r.engine == "rules" and r.engine_note == "d1-2B is still loading into memory, so the built-in rules decided this time." and seen == [(False, None)]
    asyncio.run(router.route("hello", wait_for_load=True))
    assert seen[-1] == (True, auto.TRY_LOAD_WAIT_S)
