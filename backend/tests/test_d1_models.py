"""Choosing between the two local decision models (d1-2B and d1-3B): facts, per-model install state, a shared environment,
the install / uninstall flows and API, the MCP server's per-model behaviour and the weight fetcher.

Nothing real is installed or downloaded; every command runs through a fake runner and the model is a fake object.
"""
import json
import os
import re
import sys
import types
from pathlib import Path

import pytest

from app import d1_fetch, d1_setup, d1_spec
from app import mcp_d1 as M
from app import mcp_registry as R
from app.d1_setup import Installer, SetupConflict
from tests.test_d1_setup import CUDA_HW, NO_HW, FakeRun, finish, home, make    # noqa: F401  (home is a fixture)


class Runner:
    """Fabricates what each install step leaves behind; works for the 6-step and the 2-step (shared environment) plans."""

    def __init__(self, home, fail_on=None):
        self.home, self.fail_on = home, fail_on
        self.cmds, self.envs = [], []

    def __call__(self, cmd, env, on_line):
        self.cmds.append(cmd)
        self.envs.append(env)
        if cmd[1:3] == ["-m", "venv"]:
            py = d1_spec.env_python(self.home)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("")
        if self.fail_on and self.fail_on in cmd:
            on_line("ERROR: boom")
            return 1
        if cmd[-2:] == ["-m", "app.d1_fetch"]:
            on_line("PROGRESS 1000000 6000000")
        if cmd[-2] == "-c":
            on_line(json.dumps(dict(torch="2.15.0", transformers="5.22.2", cuda=False, gpu=None)))
        return 0


def installer(home, **kw):
    return make(home, runner=Runner(home, **kw), hw=NO_HW, platform="linux")


# ------------------------------------------------------------------------------------------------------ the facts
def test_the_two_models_are_pinned_reviewed_and_described_honestly():
    two, three = d1_spec.MODELS["d1-2b"], d1_spec.MODELS["d1-3b"]
    assert (two.repo, two.revision, two.weights_bytes) == (d1_spec.REPO_ID, d1_spec.REVISION, d1_spec.WEIGHTS_BYTES)     # the 2B facts did not move
    assert three.repo == "LiquidAI/d1-3B" and three.revision == "05b80faf55536582ad5ffb2da7456f6a653acf71" and three.weights_bytes == 6_247_065_504
    assert d1_spec.REVISION_RE.match(three.revision) and three.revision in three.license_url and three.revision in three.review_notes[0]
    assert three.state_name != two.state_name and two.state_name == "state.json"                  # the 2B install record did not move
    assert three.types == ("noul", "choice", "score") and set(two.types) == set(d1_spec.ALL_TYPES)
    assert (two.image_kw, two.set_size, three.image_kw, three.set_size) == ("media", True, "images", False)
    assert "older" in three.note and "newer" in two.note and "not verified" in three.note.lower()
    notes = " ".join(three.review_notes)
    assert "Nothing found" in notes and "does NOT monkey-patch" in notes and "WITHOUT a revision" in notes and "NOT verified" in notes


def test_spec_and_active_model_fall_back_safely(monkeypatch):
    assert d1_spec.spec().id == "d1-2b" and d1_spec.spec("d1-3b").label == "d1-3B"
    with pytest.raises(ValueError, match="unknown decision model 'd1-9b' \\(use one of: d1-2b, d1-3b\\)"):
        d1_spec.spec("d1-9b")
    monkeypatch.delenv("D1_MODEL", raising=False)
    assert d1_spec.active_model() == "d1-2b"
    monkeypatch.setenv("D1_MODEL", "d1-3b")
    assert d1_spec.active_model() == "d1-3b"
    monkeypatch.setenv("D1_MODEL", "who-knows")
    assert d1_spec.active_model() == "d1-2b"


def test_each_model_has_its_own_install_record_and_shares_the_environment(home):
    assert d1_spec.ready_models() == [] and d1_spec.preferred_model() == "d1-2b" and not d1_spec.env_usable(None, "d1-2b")
    py = d1_spec.env_python()
    py.parent.mkdir(parents=True)
    py.write_text("")
    d1_spec.write_state({"state": "ready", "revision": d1_spec.spec("d1-3b").revision}, None, "d1-3b")
    assert d1_spec.read_state(None, "d1-3b")["state"] == "ready" and d1_spec.read_state() == {}
    assert d1_spec.ready_models() == ["d1-3b"] and d1_spec.preferred_model() == "d1-3b"           # only the 3B is installed: use it
    assert d1_spec.env_usable(None, "d1-2b") and not d1_spec.env_usable(None, "d1-3b")            # the 2B could reuse the 3B's environment
    d1_spec.write_state({"state": "ready", "revision": d1_spec.REVISION})
    assert d1_spec.ready_models() == ["d1-2b", "d1-3b"] and d1_spec.preferred_model() == "d1-2b"
    d1_spec.write_state({"state": "ready", "revision": "0" * 40}, None, "d1-3b")
    assert d1_spec.ready_models() == ["d1-2b"]                                                    # a record for another commit does not count
    assert d1_spec.repo_cache(home, "d1-3b") == home / "hf" / "hub" / "models--LiquidAI--d1-3B"
    assert d1_spec.repo_cache(home) == home / "hf" / "hub" / "models--LiquidAI--d1-2B"


def test_a_shared_environment_plan_only_downloads_the_weights():
    full = d1_spec.plan(d1_spec.TORCH_CPU, "d1-3b")
    short = d1_spec.plan(d1_spec.TORCH_CPU, "d1-3b", shared_env=True)
    assert [s["id"] for s in full] == ["venv", "pip", "torch", "libs", "weights", "verify"] and [s["id"] for s in short] == ["weights", "verify"]
    assert short[0]["size_mb"] == 6247 and "LiquidAI/d1-3B @ 05b80faf" in short[0]["label"]
    assert d1_spec.plan(d1_spec.TORCH_CPU)[4]["size_mb"] == round(d1_spec.WEIGHTS_BYTES / 1e6)


def test_expectations_for_the_3b_are_scaled_up_and_say_the_timings_are_the_2bs():
    cuda = d1_setup.expectation(CUDA_HW, d1_spec.TORCH_CUDA, "d1-3b")
    assert "about 7 GB of VRAM" in cuda and "timings above are for d1-2B" in cuda
    assert "about 4 GB of VRAM" in d1_setup.expectation(CUDA_HW, d1_spec.TORCH_CUDA)
    cpu = d1_setup.expectation(NO_HW, d1_spec.TORCH_CPU, "d1-3b")
    assert "about 13 GB of RAM" in cpu and "about 8 GB" not in cpu


# -------------------------------------------------------------------------------------------------- the installer
def test_installing_the_3b_first_builds_the_environment_and_records_only_the_3b(home):
    inst = installer(home)
    s = inst.start(True, "cpu", "d1-3b")
    assert s["model"] == "d1-3b" and s["repo"] == "LiquidAI/d1-3B" and s["shared_env"] is False and s["busy_with"] == "d1-3b"
    finish(inst)
    r = inst.runner
    assert [c[1:3] for c in r.cmds[:2]] == [["-m", "venv"], ["-m", "pip"]] and len(r.cmds) == 6
    assert r.envs[4]["D1_MODEL"] == "d1-3b" and r.cmds[4][-2:] == ["-m", "app.d1_fetch"]       # the fetch step is told which model
    assert d1_spec.read_state(home, "d1-3b")["state"] == "ready" and d1_spec.read_state(home) == {}
    three, two = inst.status("d1-3b"), inst.status("d1-2b")
    assert three["ready"] and three["percent"] == 100 and three["weights_gb"] == 6.25 and three["question_types"] == ["noul", "choice", "score"]
    assert two["state"] == "not_installed" and two["shared_env"] is True and [x["id"] for x in two["steps"]] == ["weights", "verify"]
    assert two["download_mb"] == d1_spec.WEIGHTS_BYTES // 1_000_000 and two["disk_gb_needed"] == round(two["download_mb"] / 1000, 1)
    assert three["license"]["url"].endswith("LICENSE") and three["review_notes"] == d1_spec.MODELS["d1-3b"].review_notes and three["busy_with"] is None


def test_the_second_model_reuses_the_environment_and_downloads_only_its_weights(home):
    inst = installer(home)
    inst.start(True, "cpu", "d1-3b")
    finish(inst)
    inst.runner.cmds.clear()
    inst.runner.envs.clear()
    s = inst.start(True, "cpu", "d1-2b")
    assert s["shared_env"] is True and [x["id"] for x in s["steps"]] == ["weights", "verify"]
    finish(inst)
    assert len(inst.runner.cmds) == 2 and inst.runner.cmds[0][-2:] == ["-m", "app.d1_fetch"] and inst.runner.envs[0]["D1_MODEL"] == "d1-2b"
    assert d1_spec.ready_models(home) == ["d1-2b", "d1-3b"]
    with pytest.raises(SetupConflict, match="d1-3B is already installed"):
        inst.start(True, "cpu", "d1-3b")
    with pytest.raises(ValueError, match="unknown decision model"):
        inst.start(True, "cpu", "d1-1b")


def test_while_one_model_installs_the_other_reports_its_own_state(home):
    import threading
    hold = threading.Event()
    run = Runner(home)

    def slow(cmd, env, on_line):
        if cmd[-2:] == ["-m", "app.d1_fetch"]:
            hold.wait(10)
        return run(cmd, env, on_line)
    inst = make(home, runner=slow, hw=NO_HW, platform="linux")
    inst.start(True, "cpu", "d1-3b")
    try:
        while inst.step_index < 4:
            threading.Event().wait(0.02)
        busy = inst.status("d1-3b")
        assert busy["state"] == "installing" and busy["step"] == "weights" and busy["log_tail"]
        other = inst.status("d1-2b")
        assert other["state"] == "not_installed" and other["busy_with"] == "d1-3b" and other["log_tail"] == [] and other["step"] is None
        with pytest.raises(SetupConflict, match="already running"):
            inst.start(True, "cpu", "d1-2b")
        with pytest.raises(SetupConflict, match="install is running"):
            inst.uninstall("d1-2b")
    finally:
        hold.set()
        finish(inst)


def test_a_failed_3b_install_is_recorded_for_the_3b_only(home):
    inst = installer(home, fail_on="-c")
    inst.start(True, "cpu", "d1-3b")
    finish(inst)
    assert inst.status("d1-3b")["state"] == "failed" and d1_spec.read_state(home, "d1-3b")["state"] == "failed"
    two = inst.status("d1-2b")
    assert two["state"] == "not_installed" and two["error"] is None
    inst2 = installer(home)                                                       # a new process: the record is on disk
    assert inst2.status("d1-3b")["state"] == "failed" and "Step" in inst2.status("d1-3b")["error"] and inst2.status("d1-3b")["log_tail"] == []
    d1_spec.write_state({"state": "installing", "revision": d1_spec.spec("d1-3b").revision}, home, "d1-3b")
    assert "interrupted" in installer(home).status("d1-3b")["error"]


def test_uninstalling_one_model_keeps_the_other_and_the_environment(home):
    inst = installer(home)
    inst.start(True, "cpu", "d1-3b")
    finish(inst)
    inst.start(True, "cpu", "d1-2b")
    finish(inst)
    for m in ("d1-2b", "d1-3b"):                                                   # something in each model's cache
        c = d1_spec.repo_cache(home, m)
        c.mkdir(parents=True, exist_ok=True)
        (c / "blob").write_bytes(b"x")
    assert inst.uninstall("d1-3b") == {"removed": True, "path": str(home)}
    assert not d1_spec.repo_cache(home, "d1-3b").exists() and not d1_spec.state_file(home, "d1-3b").exists()
    assert d1_spec.is_ready(home, "d1-2b") and d1_spec.repo_cache(home, "d1-2b").exists() and d1_spec.env_python(home).exists()
    assert inst.uninstall("d1-3b") == {"removed": False, "path": str(home)}        # nothing of the 3B left: a no-op
    assert inst.uninstall("d1-2b")["removed"] is True and not home.exists()         # the last model takes the environment with it


def test_a_3b_only_install_is_removed_whole(home):
    inst = installer(home)
    inst.start(True, "cpu", "d1-3b")
    finish(inst)
    assert inst.uninstall("d1-3b")["removed"] is True and not home.exists()
    home.mkdir()
    d1_spec.state_file(home, "d1-3b").write_text("{}")                              # only its record is there: still ours
    assert inst.uninstall("d1-3b")["removed"] is True and not home.exists()


# --------------------------------------------------------------------------------------------------------- HTTP
@pytest.fixture
def api(client, home):
    inst = client.app.state.d1
    inst._probe, inst._hw, inst.platform = (lambda: NO_HW), None, "linux"
    inst.runner = Runner(home)
    return client, inst


def test_http_models_status_install_and_uninstall_per_model(api, home):
    client, inst = api
    m = client.get("/api/d1/models").json()
    assert m["default"] == "d1-2b" and m["installing"] is None and set(m["models"]) == {"d1-2b", "d1-3b"}
    assert m["models"]["d1-3b"]["label"] == "d1-3B" and m["models"]["d1-2b"]["state"] == "not_installed"
    assert client.get("/api/d1/status").json()["model"] == "d1-2b"                  # the default did not change
    assert client.get("/api/d1/status?model=d1-3b").json()["repo"] == "LiquidAI/d1-3B"
    assert client.post("/api/d1/install", json={"accept_license": False, "model": "d1-3b"}).status_code == 422
    r = client.post("/api/d1/install", json={"accept_license": True, "device": "cpu", "model": "d1-3b"})
    assert r.status_code == 200 and r.json()["model"] == "d1-3b"
    finish(inst)
    assert client.get("/api/d1/models").json()["models"]["d1-3b"]["ready"] is True
    assert client.get("/api/d1/install/progress?model=d1-3b").json()["state"] == "ready"
    assert client.get("/api/d1/install/progress").json()["state"] == "not_installed"
    assert client.post("/api/d1/install", json={"accept_license": True, "model": "d1-3b"}).status_code == 409
    assert client.post("/api/d1/install", json={"accept_license": True, "model": "nope"}).status_code == 422
    for path in ("/api/d1/status?model=nope", "/api/d1/install/progress?model=nope"):
        assert client.get(path).status_code == 422
    assert client.post("/api/d1/uninstall?model=nope").status_code == 422
    assert client.post("/api/d1/uninstall?model=d1-3b").json()["removed"] is True


def test_the_mcp_entry_follows_the_installed_model(state, home):
    inst = installer(home)
    s = state.mcp.get("d1")
    assert s.setup_missing() and R._subst(s.env["D1_MODEL"], "") == "d1-2b"
    inst.start(True, "cpu", "d1-3b")
    finish(inst)
    s = state.mcp.get("d1")
    assert not s.setup_missing() and R._subst(s.env["D1_MODEL"], "") == "d1-3b"       # only the 3B is installed: the tools use it
    inst.start(True, "cpu", "d1-2b")
    finish(inst)
    assert R._subst(state.mcp.get("d1").env["D1_MODEL"], "") == "d1-2b"


# -------------------------------------------------------------------------------------------- the MCP server
class FakeModel3:
    """d1-3B's documented signature: system_one(state, questions, images=None)."""

    def __init__(self):
        self.calls = []

    def system_one(self, state, questions, images=None):
        self.calls.append(dict(state=state, questions=questions, images=images))
        return {"answers": {n: {"type": "noul", "noul": 0.7} for n in questions}, "usage": {"input_tokens": 5, "output_tokens": 0}}


@pytest.fixture
def three(monkeypatch, tmp_path):
    monkeypatch.setenv("D1_MODEL", "d1-3b")
    monkeypatch.setenv("D1_HOME", str(tmp_path / "d1"))
    fm = FakeModel3()
    monkeypatch.setattr(M, "_loaded", None)
    monkeypatch.setattr(M, "_loader", lambda: M.Loaded(fm, "cuda", "bfloat16"))
    return fm


def test_the_3b_calls_the_model_with_images_and_reports_which_model_answered(three):
    r = M.decide("hello", {"q": {"type": "noul", "instructions": "x"}})
    assert r["ok"] and r["answers"]["q"]["noul"] == 0.7 and r["meta"]["model"] == "d1-3B" and r["meta"]["revision"] == "05b80faf55536"[:12]
    assert three.calls[0]["images"] is None and "media" not in three.calls[0]
    for q in ({"type": "choice", "instructions": "x", "criteria": {"a": "a", "b": "b"}}, {"type": "score", "instructions": "x", "criteria": ["lo", "hi"]}):
        assert M.decide("t", {"q": q})["ok"]


def test_the_3b_refuses_question_types_it_cannot_answer_and_set_size(three):
    for t in ("text", "number", "date"):
        r = M.decide("t", {"q": {"type": t, "instructions": "x"}})
        assert r["ok"] is False and f"d1-3B answers only noul, choice, score questions, not '{t}'" in r["error"]
    r = M.extract("Invoice #12", {"id": "the invoice id"})
    assert r["ok"] is False and "answers only noul, choice, score" in r["error"]
    r = M.decide("t", {"q": {"type": "noul", "instructions": "x"}}, set_size=4)
    assert r["ok"] is False and r["error"] == "d1-3B does not support set_size"
    assert three.calls == []                                                          # refused before the model was touched


def test_status_names_the_active_model_and_its_question_types(three, tmp_path):
    s = M.status()
    assert s["model"] == "LiquidAI/d1-3B" and s["revision"] == d1_spec.spec("d1-3b").revision and s["question_types"] == ["noul", "choice", "score"]
    assert s["license"]["url"] == d1_spec.spec("d1-3b").license_url and s["installed"] is False


def test_the_real_loader_loads_the_pinned_3b_revision_offline(tmp_path, monkeypatch):
    from tests.test_d1_server import FakeTorch, _restore_env_after
    home = tmp_path / "d1"
    py = d1_spec.env_python(home)
    py.parent.mkdir(parents=True)
    py.write_text("")
    d1_spec.write_state({"state": "ready", "revision": d1_spec.spec("d1-3b").revision}, home, "d1-3b")
    monkeypatch.setenv("D1_HOME", str(home))
    monkeypatch.setenv("D1_MODEL", "d1-3b")
    monkeypatch.setenv("D1_DEVICE", "")
    _restore_env_after(monkeypatch, "HF_HOME", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    seen = {}

    class Model:
        def to(self, device):
            return self

        def eval(self):
            pass

        def system_one(self, state, questions, images=None):
            return {"answers": {"q": {"noul": 0.5}}, "usage": {}}

    class AutoModel:
        @staticmethod
        def from_pretrained(repo, **kw):
            seen["repo"], seen["kw"] = repo, kw
            return Model()

    tr = types.ModuleType("transformers")
    tr.AutoModel = AutoModel
    monkeypatch.setitem(sys.modules, "torch", FakeTorch(cuda=True))
    monkeypatch.setitem(sys.modules, "transformers", tr)
    monkeypatch.setattr(M, "_loaded", None)
    monkeypatch.setattr(M, "_loader", None)
    r = M.decide("t", {"q": {"type": "noul", "instructions": "x"}})
    assert r["ok"], r
    assert seen["repo"] == "LiquidAI/d1-3B" and seen["kw"]["revision"] == d1_spec.spec("d1-3b").revision and seen["kw"]["trust_remote_code"] is True
    # the 2B being installed does not make the 3B available
    monkeypatch.setattr(M, "_loaded", None)
    d1_spec.state_file(home, "d1-3b").unlink()
    r = M.decide("t", {"q": {"type": "noul", "instructions": "x"}})
    assert r["ok"] is False and "d1-3B is not installed" in r["error"] and "Settings > Auto" in r["error"]


# -------------------------------------------------------------------------------------------------- the fetcher
def test_fetch_downloads_the_3b_at_its_pinned_commit_and_pins_main(tmp_path, monkeypatch, capsys):
    from tests.test_d1_server import _fetch_home
    home = _fetch_home(tmp_path, monkeypatch)
    monkeypatch.setenv("D1_MODEL", "d1-3b")
    monkeypatch.setattr(d1_fetch, "POLL_SECONDS", 0.01)
    seen = {}
    hub = types.ModuleType("huggingface_hub")

    def snapshot_download(**kw):
        seen.update(kw)
        return str(home / "hf" / "hub" / "snap")
    hub.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    (home / "hf" / "hub").mkdir(parents=True)
    assert d1_fetch.main() == 0
    out = capsys.readouterr().out.splitlines()
    sp = d1_spec.spec("d1-3b")
    assert seen["repo_id"] == "LiquidAI/d1-3B" and seen["revision"] == sp.revision
    assert out[-2] == f"PROGRESS {sp.weights_bytes + 30_000_000} {sp.weights_bytes + 30_000_000}"
    ref = home / "hf" / "hub" / "models--LiquidAI--d1-3B" / "refs" / "main"
    assert ref.read_text(encoding="utf-8") == sp.revision                              # offline "main" resolves to the reviewed commit
    assert not (home / "hf" / "hub" / "models--LiquidAI--d1-2B").exists()
