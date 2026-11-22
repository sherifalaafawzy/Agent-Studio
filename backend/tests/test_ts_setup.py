"""Install / status / uninstall of the local time-series models, the one-shot UI forecast, the CLI, and the MCP catalogue entry.

Nothing real is downloaded or installed: the Installer's command runner is replaced by a fake that records every command
(so the pinned plan is asserted) and fabricates the files a real install would leave behind.
"""
import asyncio
import json
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import mcp_registry as R, ts_cli, ts_setup, ts_spec as T
from app.ts_setup import Installer, SetupConflict
from tests.test_ts_spec_fetch import mark_ready

CUDA_HW = dict(nvidia=True, gpu="NVIDIA GeForce RTX 5080", vram_mb=16303, driver="610.88", cuda=[13, 3])
NO_HW = dict(nvidia=False, gpu=None, vram_mb=None, driver=None, cuda=None)
TODAY = date(2026, 11, 21)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "ts"
    monkeypatch.setenv("TS_HOME", str(h))
    return h


class FakeRun:
    """Stands in for venv / pip / the fetcher / the import check."""

    def __init__(self, home, fail_on=None, hold_at=None, hold=None, verify=None):
        self.home, self.fail_on, self.hold_at, self.hold, self.verify = home, fail_on, hold_at, hold, verify
        self.cmds, self.envs = [], []
        self.reached = threading.Event()

    def __call__(self, cmd, env, on_line):
        self.cmds.append(cmd)
        self.envs.append(env)
        text = " ".join(cmd)
        on_line("   ")                                                       # blank lines are ignored
        on_line("line of output " + text[-30:])
        if self.fail_on and self.fail_on in text:
            on_line("ERROR: No matching distribution found")
            return 1
        if "-m venv" in text:
            py = T.env_python(self.home)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("")
        if "app.ts_fetch" in text:
            on_line("PROGRESS 1500000 3000000")
        if self.hold_at and self.hold_at in text:
            self.reached.set()
            if self.hold:
                self.hold.wait(10)
        if "app.ts_runners" in text:
            mid = cmd[-1]
            for ln in (self.verify if self.verify is not None else [json.dumps({"model": mid, "torch": "2.15.0", "library": "1.0", "cuda": False, "gpu": None})]):
                on_line(ln)
        return 0


def make(home, runner=None, hw=CUDA_HW, platform="win32", today=TODAY, python=(3, 12)):
    return Installer(runner=runner or FakeRun(home), hardware=lambda: hw, platform=platform, today=lambda: today, python=python)


def finish(inst):
    inst.thread.join(15)
    assert not inst.thread.is_alive()


def cmd_text(cmds):
    return [" ".join(c) for c in cmds]


# ---------------------------------------------------------------- planning

def test_plan_for_a_fresh_environment_lists_environment_steps_then_each_model(home):
    inst = make(home)
    p = inst.plan(["chronos-2", "timestral-5m"])
    assert [s["id"] for s in p] == ["venv", "pip", "torch", "libs", "chronos-2:pkgs", "chronos-2:weights", "chronos-2:verify",
                                    "timestral-5m:weights", "timestral-5m:verify"]                                   # Timestral has no PyPI package
    sizes = {s["id"]: s["size_mb"] for s in p}
    assert sizes["torch"] == 2400 and sizes["chronos-2:weights"] == 478 and sizes["timestral-5m:weights"] == 21 and sizes["chronos-2:verify"] == 0
    assert "weights and source" in next(s["label"] for s in p if s["id"] == "timestral-5m:weights")
    assert "download weights (" in next(s["label"] for s in p if s["id"] == "chronos-2:weights")
    assert next(s["label"] for s in p if s["id"] == "chronos-2:pkgs") == "Chronos-2 (Amazon): install chronos-forecasting, transformers, accelerate"
    assert all(s["model"] is None for s in p[:4]) and p[4]["model"] == "chronos-2"


def test_plan_skips_the_environment_once_it_exists(home):
    mark_ready(home)
    inst = make(home)
    assert [s["id"] for s in inst.plan(["toto-2-4m"])] == ["toto-2-4m:pkgs", "toto-2-4m:weights", "toto-2-4m:verify"]


def test_describe_plan_shows_download_disk_and_expectation_before_anything_happens(home, monkeypatch):
    monkeypatch.setattr(ts_setup, "free_disk_gb", lambda p: 123.4)
    d = make(home).describe_plan(["chronos-2", "timesfm-3", "chronos-2"])
    assert d["models"] == ["chronos-2", "timesfm-3", "chronos-2"] and d["env_needed"] is True and d["free_gb"] == 123.4 and d["variant"] == "cuda"
    assert d["download_mb"] == sum(s["size_mb"] for s in d["steps"]) and d["download_mb"] > 4000 and d["disk_gb_needed"] > d["download_mb"] / 1000
    assert all(s["status"] == "pending" for s in d["steps"]) and "RTX 5080" in d["expect"] and "CUDA 13.2" in d["torch_build"]
    cpu = make(home, hw=NO_HW).describe_plan(["chronos-2"])
    assert cpu["variant"] == "cpu" and "CPU" in cpu["expect"] and cpu["disk_gb_needed"] < d["disk_gb_needed"]
    with pytest.raises(ValueError, match="choose at least one model"):
        make(home).describe_plan([])
    with pytest.raises(ValueError, match="unknown model\\(s\\): nope"):
        make(home).describe_plan(["nope"])


def test_already_installed_models_are_not_planned_again(home):
    mark_ready(home, "chronos-2")
    d = make(home).describe_plan(["chronos-2"])
    assert d["steps"] == [] and d["download_mb"] == 0 and d["disk_gb_needed"] == 0 and d["env_needed"] is False


def test_the_expectation_text_covers_every_build(home):
    mac = make(home, platform="darwin")
    assert "not used by these adapters" in mac.status()["expect"] and mac.status()["hardware"]["variant"] == "default"
    old = make(home, hw={**CUDA_HW, "cuda": [12, 4]})
    assert "too old for CUDA 13.2" in old.status()["expect"]
    forced = make(home, hw=CUDA_HW)
    assert forced.choice("cpu")["variant"] == "cpu"


# ---------------------------------------------------------------- start() checks

def test_start_requires_every_licence_to_be_accepted(home):
    inst = make(home)
    with pytest.raises(ValueError, match="not accepted: TimesFM 3.0 \\(Google\\)"):
        inst.start(["chronos-2", "timesfm-3"], ["chronos-2"])
    assert not inst.running()
    with pytest.raises(ValueError, match="choose at least one"):
        inst.start([], [])
    with pytest.raises(ValueError, match="device must be"):
        inst.start(["chronos-2"], ["chronos-2"], "tpu")
    with pytest.raises(ValueError, match="unknown model"):
        inst.start(["nope"], ["nope"])


def test_start_refuses_blocked_models_with_the_reason(home):
    with pytest.raises(ValueError, match="ci-proto .* cannot be installed yet: its newest package is under 14 days old; Agent Studio installs it from 2026-11-26"):
        make(home).start(["ci-proto"], ["ci-proto"])
    with pytest.raises(ValueError, match="needs Python 3.12\\+, the backend runs 3.11"):
        make(home, python=(3, 11)).start(["toto-2-4m"], ["toto-2-4m"])
    run = FakeRun(home)
    inst = make(home, runner=run, today=date(2026, 11, 26))                              # five days later it is allowed
    inst.start(["ci-proto"], ["ci-proto"])
    finish(inst)
    assert T.is_ready("ci-proto")


def test_start_refuses_when_the_disk_is_too_small_and_when_everything_is_installed(home, monkeypatch):
    inst = make(home)
    monkeypatch.setattr(ts_setup, "free_disk_gb", lambda p: 0.5)
    with pytest.raises(ValueError, match="not enough free disk space: need about .* GB, 0.5 GB free"):
        inst.start(["chronos-2"], ["chronos-2"])
    assert not inst.running()
    monkeypatch.setattr(ts_setup, "free_disk_gb", lambda p: None)                        # unknown free space does not block
    mark_ready(home, "chronos-2")
    with pytest.raises(SetupConflict, match="already installed"):
        inst.start(["chronos-2"], ["chronos-2"])


# ---------------------------------------------------------------- a full install

def test_installing_two_models_builds_the_environment_once_with_pinned_commands(home, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "secret-token")
    monkeypatch.setenv("PYTHONPATH", "/elsewhere")
    run = FakeRun(home)
    inst = make(home, runner=run)
    st = inst.start(["chronos-2", "timestral-5m"], ["chronos-2", "timestral-5m"])
    assert st["install"]["state"] == "installing"
    finish(inst)
    cmds = cmd_text(run.cmds)
    py = str(T.env_python(home))
    assert cmds[0].endswith("-m venv --clear " + str(home / "venv")) and run.cmds[0][0] == sys.executable
    assert cmds[1] == f"{py} -m pip install --no-input pip==26.2.2"
    assert cmds[2] == f"{py} -m pip install --no-input --only-binary=:all: --index-url https://download.pytorch.org/whl/cu132 torch==2.15.0"
    assert cmds[3] == (f"{py} -m pip install --no-input --only-binary=:all: --uploaded-prior-to 2026-11-07T00:00:00Z " + " ".join(T.BASE_PINS))
    assert cmds[4] == (f"{py} -m pip install --no-input --only-binary=:all: --uploaded-prior-to 2026-11-07T00:00:00Z "
                       "chronos-forecasting==2.3.2 transformers==5.22.2 accelerate==1.15.0")
    assert cmds[5] == f"{py} -m app.ts_fetch chronos-2" and cmds[6] == f"{py} -m app.ts_runners verify chronos-2"
    assert cmds[7] == f"{py} -m app.ts_fetch timestral-5m" and cmds[8] == f"{py} -m app.ts_runners verify timestral-5m" and len(cmds) == 9
    assert run.envs[5]["PYTHONPATH"] == str(ts_setup.BACKEND_DIR) and run.envs[0]["TS_HOME"] == str(home) and run.envs[0]["HF_HOME"] == str(home / "hf")
    assert all("HF_TOKEN" not in e and "VIRTUAL_ENV" not in e for e in run.envs) and "PYTHONPATH" not in run.envs[0]
    assert run.envs[0]["PIP_NO_INPUT"] == "1" and run.envs[0]["PYTHONUTF8"] == "1"
    state = T.read_state(home)
    assert state["env"]["state"] == "ready" and state["env"]["torch"] == "cuda" and state["env"]["python"].startswith("3.")
    assert state["models"]["chronos-2"] == {**state["models"]["chronos-2"], "state": "ready", "revision": T.MODELS["chronos-2"].revision,
                                              "versions": {"model": "chronos-2", "torch": "2.15.0", "library": "1.0", "cuda": False, "gpu": None}}
    s = inst.status()
    assert s["install"]["state"] == "idle" and s["env"]["ready"] and s["env"]["torch"] == "cuda"
    assert {m["id"]: m["state"] for m in s["models"] if m["state"] == "ready"} == {"chronos-2": "ready", "timestral-5m": "ready"}
    assert next(m for m in s["models"] if m["id"] == "chronos-2")["versions"]["library"] == "1.0"
    assert s["install"]["percent"] == 100 and all(x["status"] == "done" for x in s["install"]["steps"])


def test_a_later_install_reuses_the_environment_and_its_torch_build(home):
    run = FakeRun(home)
    inst = make(home, runner=run)
    inst.start(["toto-2-4m"], ["toto-2-4m"], "cpu")
    finish(inst)
    assert "https://download.pytorch.org/whl/cpu" in cmd_text(run.cmds)[2]
    run2 = FakeRun(home)
    inst2 = make(home, runner=run2, hw=CUDA_HW)                                           # a GPU appeared, but the environment keeps its build
    inst2.start(["chronos-2"], ["chronos-2"])
    finish(inst2)
    texts = cmd_text(run2.cmds)
    assert len(texts) == 3 and "-m pip install" in texts[0] and "chronos-forecasting" in texts[0] and "app.ts_fetch chronos-2" in texts[1] and "app.ts_runners verify" in texts[2]
    assert inst2.status()["hardware"]["variant"] == "cpu" and inst2.status()["hardware"]["torch_build"] == T.TORCH_CPU["label"]
    assert T.read_state(home)["models"]["toto-2-4m"]["state"] == "ready" and T.is_ready("chronos-2")


def test_progress_lines_drive_the_percentage_and_the_text(home):
    hold = threading.Event()
    run = FakeRun(home, hold_at="app.ts_fetch", hold=hold)
    inst = make(home, runner=run)
    inst.start(["chronos-2"], ["chronos-2"])
    assert run.reached.wait(10)
    p = inst.progress()
    assert p["state"] == "installing" and p["step"] == "chronos-2:weights" and 0 < p["percent"] < 100 and p["queue"] == ["chronos-2"]
    assert p["progress_text"] == "Downloading: 2 of 3 MB" and p["models"]["chronos-2"] == "installing" and p["log_tail"]
    assert [s["status"] for s in p["steps"]][:5] == ["done"] * 5 and p["steps"][5]["status"] == "running"
    with pytest.raises(SetupConflict, match="already running"):
        inst.start(["toto-2-4m"], ["toto-2-4m"])
    with pytest.raises(SetupConflict, match="install is running"):
        inst.uninstall("chronos-2")
    hold.set()
    finish(inst)
    assert inst.status()["install"]["progress_text"] == "" and inst.status()["install"]["step"] is None


def test_queued_installing_and_finished_models_show_their_own_state_during_a_run(home):
    hold = threading.Event()
    run = FakeRun(home, hold_at="app.ts_fetch timestral-5m", hold=hold)
    inst = make(home, runner=run)
    inst.start(["chronos-2", "timestral-5m", "toto-2-4m"], ["chronos-2", "timestral-5m", "toto-2-4m"])
    assert run.reached.wait(10)
    states = {m["id"]: m["state"] for m in inst.status()["models"]}
    assert states["chronos-2"] == "ready" and states["timestral-5m"] == "installing" and states["toto-2-4m"] == "queued" and states["timesfm-3"] == "not_installed"
    hold.set()
    finish(inst)
    assert {m["id"]: m["state"] for m in inst.status()["models"]}["toto-2-4m"] == "ready"


# ---------------------------------------------------------------- failures

def test_a_failing_environment_step_marks_every_requested_model_failed_and_can_be_retried(home):
    run = FakeRun(home, fail_on="--uploaded-prior-to 2026-11-07T00:00:00Z numpy")
    inst = make(home, runner=run)
    inst.start(["chronos-2", "toto-2-4m"], ["chronos-2", "toto-2-4m"])
    finish(inst)
    s = inst.status()
    assert s["install"]["state"] == "failed" and "Step 'Install numpy" in s["install"]["error"] and "exit code 1" in s["install"]["error"]
    assert "No matching distribution found" in s["install"]["error"] and not s["env"]["ready"]
    by = {m["id"]: m for m in s["models"]}
    assert by["chronos-2"]["state"] == "failed" and "Step 'Install numpy" in by["chronos-2"]["error"] and by["toto-2-4m"]["state"] == "failed"
    assert [x["status"] for x in s["install"]["steps"]][:4] == ["done", "done", "done", "failed"] and s["install"]["log_tail"]
    inst2 = make(home, runner=FakeRun(home))                                              # a fresh app: the failure was persisted
    assert {m["id"]: m["state"] for m in inst2.status()["models"]}["chronos-2"] == "failed"
    inst2.start(["chronos-2"], ["chronos-2"])                                             # retry works and clears the failure
    finish(inst2)
    assert inst2.status()["install"]["state"] == "idle" and T.is_ready("chronos-2")


def test_a_failing_model_leaves_the_models_before_it_installed(home):
    run = FakeRun(home, fail_on="app.ts_fetch toto-2-4m")
    inst = make(home, runner=run)
    inst.start(["chronos-2", "toto-2-4m", "timestral-5m"], ["chronos-2", "toto-2-4m", "timestral-5m"])
    finish(inst)
    by = {m["id"]: m for m in inst.status()["models"]}
    assert by["chronos-2"]["state"] == "ready" and T.is_ready("chronos-2")
    assert by["toto-2-4m"]["state"] == "failed" and "download weights" in by["toto-2-4m"]["error"]
    assert by["timestral-5m"]["state"] == "failed" and by["timestral-5m"]["error"] == "not started: an earlier install step failed"


def test_an_unreadable_import_check_fails_the_model(home):
    inst = make(home, runner=FakeRun(home, verify=["not json", "[1, 2]", ""]))
    inst.start(["chronos-2"], ["chronos-2"])
    finish(inst)
    assert "printed nothing readable" in inst.status()["install"]["error"]
    assert inst.status()["install"]["steps"][-1]["status"] == "failed" and {m["id"]: m["state"] for m in inst.status()["models"]}["chronos-2"] == "failed"
    inst2 = make(home, runner=FakeRun(home, verify=["warning: something", json.dumps({"model": "chronos-2", "library": "9"})]))   # noise before the JSON is fine
    inst2.start(["chronos-2"], ["chronos-2"])
    finish(inst2)
    assert T.read_state(home)["models"]["chronos-2"]["versions"]["library"] == "9"


def test_a_write_error_while_recording_a_failure_is_swallowed(home, monkeypatch):
    run = FakeRun(home, fail_on="-m venv")
    inst = make(home, runner=run)
    real = T.write_state
    calls = []

    def flaky(data, h=None):
        calls.append(1)
        if len(calls) > 1:
            raise OSError("disk gone")
        real(data, h)
    monkeypatch.setattr(T, "write_state", flaky)
    inst.start(["chronos-2"], ["chronos-2"])
    finish(inst)
    assert inst.error and "venv" in inst.error


def test_an_interrupted_install_is_reported_as_failed(home):
    mark_ready(home)
    st = T.read_state(home)
    st["models"] = {"chronos-2": {"state": "installing", "revision": T.MODELS["chronos-2"].revision}}
    T.write_state(st, home)
    m = {x["id"]: x for x in make(home).status()["models"]}["chronos-2"]
    assert m["state"] == "failed" and "was interrupted" in m["error"]


def test_blocked_models_show_why_and_other_python_versions_block_toto(home):
    by = {m["id"]: m for m in make(home).status()["models"]}
    assert by["ci-proto"]["state"] == "blocked" and "2026-11-26" in by["ci-proto"]["error"] and by["ci-proto"]["available_from"] == "2026-11-26"
    by = {m["id"]: m for m in make(home, python=(3, 11)).status()["models"]}
    assert by["toto-2-4m"]["state"] == "blocked" and "needs Python 3.12+" in by["toto-2-4m"]["error"] and by["chronos-2"]["state"] == "not_installed"
    assert by["chronos-2"]["error"] is None and by["chronos-2"]["usable_commercially"] == "yes" and by["timesfm-3"]["usable_commercially"] == "no"


# ---------------------------------------------------------------- uninstall

def hf_dir(home, repo):
    d = home / "hf" / "hub" / T.model_dir_name(repo)
    d.mkdir(parents=True, exist_ok=True)
    (d / "blob").write_text("weights")
    return d


def test_uninstalling_one_model_keeps_the_others_and_the_environment(home):
    mark_ready(home, "chronos-2", "toto-2-4m")
    a, b = hf_dir(home, "amazon/chronos-2"), hf_dir(home, "Datadog/Toto-2.0-4m")
    (a / "blob").chmod(0o444)                                                             # read-only caches must not stop the removal (Windows)
    inst = make(home)
    r = inst.uninstall("chronos-2")
    assert r == {"removed": True, "model": "chronos-2", "removed_environment": False, "path": str(home)}
    assert not a.exists() and b.exists() and T.is_ready("toto-2-4m") and not T.is_ready("chronos-2") and T.env_ready()


def test_models_sharing_a_source_keep_it_until_the_last_one_goes(home):
    mark_ready(home, "timestral-5m", "timestral-28m", "chronos-2")
    src = home / "src" / "timestral" / "src"
    src.mkdir(parents=True)
    (src / "x.py").write_text("x")
    hf_dir(home, "mistralai/Timestral-5M")
    hf_dir(home, "mistralai/Timestral-28M")
    inst = make(home)
    inst.uninstall("timestral-5m")
    assert src.exists() and not (home / "hf" / "hub" / "models--mistralai--Timestral-5M").exists()
    inst.uninstall("timestral-28m")
    assert not (home / "src" / "timestral").exists() and T.is_ready("chronos-2")
    kro = home / "src" / "kronos" / "model"
    kro.mkdir(parents=True)
    st = T.read_state(home)
    st["models"]["kronos-base"] = {"state": "ready", "revision": T.MODELS["kronos-base"].revision}
    T.write_state(st, home)
    hf_dir(home, "NeoQuasar/Kronos-base")
    hf_dir(home, "NeoQuasar/Kronos-Tokenizer-base")
    inst.uninstall("kronos-base")
    assert not (home / "src" / "kronos").exists() and not (home / "hf" / "hub" / "models--NeoQuasar--Kronos-Tokenizer-base").exists()


def test_uninstalling_the_last_model_removes_the_whole_environment(home):
    mark_ready(home, "chronos-2")
    (home / "venv" / "Lib").mkdir(parents=True)
    r = make(home).uninstall("chronos-2")
    assert r["removed"] is True and r["removed_environment"] is True and not home.exists()


def test_uninstall_of_something_that_was_never_installed_changes_nothing(home):
    mark_ready(home, "chronos-2")
    r = make(home).uninstall("toto-2-4m")
    assert r["removed"] is False and r["removed_environment"] is False and T.is_ready("chronos-2")
    nothing = make(home.parent / "elsewhere")
    assert make(home).uninstall("timesfm-3")["removed"] is False
    assert nothing is not None
    with pytest.raises(KeyError, match="unknown model"):
        make(home).uninstall("nope")


def test_uninstall_with_no_environment_at_all_is_a_no_op(home, monkeypatch):
    assert make(home).uninstall("chronos-2") == {"removed": False, "model": "chronos-2", "removed_environment": False, "path": str(home)}


# ---------------------------------------------------------------- HTTP API

@pytest.fixture
def api(client, home):
    inst = client.app.state.ts
    inst._probe, inst._hw, inst.platform, inst.today, inst.python = (lambda: CUDA_HW), None, "win32", (lambda: TODAY), (3, 12)
    return client, inst


def test_http_install_lifecycle(api, home):
    client, inst = api
    s = client.get("/api/ts/status").json()
    assert len(s["models"]) == 8 and s["install"]["state"] == "idle" and s["hardware"]["variant"] == "cuda" and not s["env"]["ready"]
    plan = client.get("/api/ts/plan", params={"models": "chronos-2,timestral-5m"}).json()
    assert plan["env_needed"] and plan["steps"][0]["id"] == "venv" and plan["download_mb"] > 3000
    assert client.get("/api/ts/plan", params={"models": "nope"}).status_code == 422
    assert client.get("/api/ts/plan", params={"models": "chronos-2", "device": "cpu"}).json()["variant"] == "cpu"

    bad = client.post("/api/ts/install", json={"models": ["chronos-2"]})                    # no licence accepted
    assert bad.status_code == 422 and "accept the licence" in bad.json()["detail"]
    assert client.post("/api/ts/install", json={"models": ["ci-proto"], "accepted": ["ci-proto"]}).status_code == 422

    hold = threading.Event()
    inst.runner = run = FakeRun(home, hold_at="app.ts_fetch", hold=hold)
    ok = client.post("/api/ts/install", json={"models": ["chronos-2"], "accepted": ["chronos-2"]})
    assert ok.status_code == 200 and ok.json()["install"]["state"] == "installing"
    assert run.reached.wait(10)
    assert client.post("/api/ts/install", json={"models": ["toto-2-4m"], "accepted": ["toto-2-4m"]}).status_code == 409
    assert client.post("/api/ts/uninstall/chronos-2").status_code == 409
    p = client.get("/api/ts/install/progress").json()
    assert p["state"] == "installing" and p["models"]["chronos-2"] == "installing" and p["step"] == "chronos-2:weights"
    hold.set()
    finish(inst)
    assert {m["id"]: m["state"] for m in client.get("/api/ts/status").json()["models"]}["chronos-2"] == "ready"
    assert client.post("/api/ts/install", json={"models": ["chronos-2"], "accepted": ["chronos-2"]}).status_code == 409   # already installed

    assert client.post("/api/ts/uninstall/nope").status_code == 404
    assert client.post("/api/ts/uninstall/chronos-2").json()["removed_environment"] is True
    assert client.get("/api/ts/status").json()["env"]["ready"] is False


def test_mcp_catalogue_entry_follows_the_install(api, home):
    client, inst = api
    m = next(x for x in client.get("/api/mcp").json() if x["id"] == "timeseries")
    assert m["category"] == "Local models" and m["ready"] is False and m["enabled"] is False and m["builtin"]
    assert m["command"] == "{ts_python}" and m["args"] == ["-m", "app.mcp_ts"]
    assert client.put("/api/mcp/timeseries", json={"enabled": True}).json()["ready"] is False        # on, but nothing installed
    mark_ready(home, "toto-2-4m")
    assert next(x for x in client.get("/api/mcp").json() if x["id"] == "timeseries")["ready"] is True
    mark_ready(home)
    assert next(x for x in client.get("/api/mcp").json() if x["id"] == "timeseries")["ready"] is False


def test_unready_timeseries_server_is_skipped_with_an_install_hint(state, home, tmp_path):
    state.mcp.update("timeseries", True)
    warns = []
    assert asyncio.run(R.build_toolsets(state.mcp, ["timeseries"], str(tmp_path), warns)) == []
    assert len(warns) == 1 and "not installed" in warns[0] and "Time-series forecasting" in warns[0] and "Decision" not in warns[0]
    state.mcp.update("d1", True)
    warns = []
    asyncio.run(R.build_toolsets(state.mcp, ["d1"], str(tmp_path), warns))
    assert "Decision model" in warns[0]                                                              # the d1 hint is unchanged


def test_ready_timeseries_server_starts_the_environments_python(state, home, tmp_path, monkeypatch):
    from google.adk.tools.mcp_tool import mcp_toolset as mod
    made = []

    class FakeToolset:
        def __init__(self, connection_params, tool_name_prefix):
            made.append((connection_params, tool_name_prefix))

        async def get_tools(self):
            return []
    monkeypatch.setattr(mod, "McpToolset", FakeToolset)
    mark_ready(home, "chronos-2")
    state.mcp.update("timeseries", True)
    assert state.mcp.get("timeseries").ready()
    warns = []
    assert len(asyncio.run(R.build_toolsets(state.mcp, ["timeseries"], str(tmp_path), warns))) == 1 and warns == []
    params, prefix = made[0]
    sp = params.server_params
    assert Path(sp.command).resolve() == T.env_python(home).resolve() and Path(sp.command).resolve() != Path(sys.executable).resolve()
    assert sp.args == ["-m", "app.mcp_ts"] and prefix == "timeseries_"
    assert Path(sp.env["PYTHONPATH"]) == Path(R.__file__).resolve().parents[1] and Path(sp.env["TS_HOME"]) == home and Path(sp.env["TS_WORKSPACE"]) == tmp_path


def test_default_home_is_consistent_between_catalogue_and_server(monkeypatch):
    monkeypatch.delenv("TS_HOME", raising=False)
    from app.db import data_dir
    assert R._subst("{ts_home}", "") == str(data_dir() / "envs" / "timeseries") == str(T.home())


def test_register_accepts_an_explicit_installer_and_forecaster(home):
    app = FastAPI()
    inst = make(home)
    seen = []
    ts_setup.register(app, inst, lambda payload: (seen.append(payload), {"ok": True, "echo": 1})[1])
    assert app.state.ts is inst
    c = TestClient(app)
    r = c.post("/api/ts/forecast", json={"series": [1.0, 2.0], "horizon": 3, "models": ["chronos-2"], "quantiles": [0.5], "backtest": True, "season": 7})
    assert r.json() == {"ok": True, "echo": 1}
    assert seen == [{"tool": "forecast", "args": {"series": [1.0, 2.0], "horizon": 3, "models": ["chronos-2"], "quantiles": [0.5], "backtest": True, "season": 7}}]
    c.post("/api/ts/forecast", json={"series": [1.0], "horizon": 1})
    assert seen[-1]["args"]["models"] == "all" and seen[-1]["args"]["backtest"] is False


# ---------------------------------------------------------------- the one-shot run

class Done:
    def __init__(self, stdout="", stderr="", code=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, code


def test_run_cli_without_an_environment(home):
    assert ts_setup.run_cli({"tool": "forecast", "args": {}}) == {"ok": False, "error": "no time-series model is installed yet: install one first"}


def test_run_cli_runs_the_environments_python_and_reads_the_last_json_line(home, monkeypatch):
    mark_ready(home, "chronos-2")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return Done("warming up\n" + json.dumps({"ok": True, "models": {}}) + "\n")
    monkeypatch.setattr(ts_setup.subprocess, "run", fake_run)
    out = ts_setup.run_cli({"tool": "forecast", "args": {"horizon": 2}}, timeout=77)
    assert out == {"ok": True, "models": {}} and seen["cmd"] == [str(T.env_python(home)), "-m", "app.ts_cli"] and seen["timeout"] == 77
    assert json.loads(seen["input"]) == {"tool": "forecast", "args": {"horizon": 2}}
    assert seen["env"]["TS_HOME"] == str(home) and seen["env"]["PYTHONPATH"] == str(ts_setup.BACKEND_DIR)


def test_run_cli_explains_every_failure(home, monkeypatch):
    mark_ready(home, "chronos-2")

    def raising(exc):
        def run(cmd, **kw):
            raise exc
        return run
    monkeypatch.setattr(ts_setup.subprocess, "run", raising(subprocess.TimeoutExpired("x", 5)))
    assert ts_setup.run_cli({}, timeout=5) == {"ok": False, "error": "the forecast took longer than 5 seconds and was stopped"}
    monkeypatch.setattr(ts_setup.subprocess, "run", raising(OSError("cannot spawn")))
    assert "cannot start the model environment: cannot spawn" in ts_setup.run_cli({})["error"]
    monkeypatch.setattr(ts_setup.subprocess, "run", lambda cmd, **kw: Done("", "Traceback...\nImportError: torch\n", 1))
    assert ts_setup.run_cli({})["error"] == "the model environment failed (exit code 1): ImportError: torch"
    monkeypatch.setattr(ts_setup.subprocess, "run", lambda cmd, **kw: Done("not json at all", "", 3))
    assert "(exit code 3): not json at all" in ts_setup.run_cli({})["error"]
    monkeypatch.setattr(ts_setup.subprocess, "run", lambda cmd, **kw: Done("", "", 0))
    assert "(exit code 0): no output" in ts_setup.run_cli({})["error"]
    monkeypatch.setattr(ts_setup.subprocess, "run", lambda cmd, **kw: Done("[1, 2]", "", 0))
    assert ts_setup.run_cli({}) == {"ok": False, "error": "unreadable answer from the model environment"}


def test_the_default_http_forecaster_is_run_cli(api, home, monkeypatch):
    client, _ = api
    monkeypatch.setattr(ts_setup, "run_cli", lambda payload: {"ok": True, "payload": payload["tool"]})
    assert client.post("/api/ts/forecast", json={"series": [1.0] * 10, "horizon": 2}).json() == {"ok": True, "payload": "forecast"}
    assert client.post("/api/ts/forecast", json={"series": "x", "horizon": 2}).status_code == 422


# ---------------------------------------------------------------- app.ts_cli (runs inside the environment)

def test_cli_dispatches_forecast_and_compare_and_reports_bad_requests(monkeypatch, capsys):
    monkeypatch.setattr(ts_cli.mcp_ts, "run_forecast", lambda **kw: {"ok": True, "tool": "forecast", "kw": kw})
    monkeypatch.setattr(ts_cli.mcp_ts, "compare", lambda **kw: {"ok": True, "tool": "compare"})
    monkeypatch.setitem(ts_cli.TOOLS, "forecast", ts_cli.mcp_ts.run_forecast)
    monkeypatch.setitem(ts_cli.TOOLS, "compare", ts_cli.mcp_ts.compare)
    assert ts_cli.main(json.dumps({"tool": "forecast", "args": {"horizon": 3}})) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "tool": "forecast", "kw": {"horizon": 3}}
    assert ts_cli.main(json.dumps({"tool": "compare", "args": {}})) == 0
    assert json.loads(capsys.readouterr().out)["tool"] == "compare"
    for raw in ("not json", json.dumps({"tool": "nope", "args": {}}), json.dumps({"tool": "forecast", "args": 5}), json.dumps({"args": {}})):
        assert ts_cli.main(raw) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is False and out["error"].startswith("bad request:")


def test_cli_reads_stdin_and_runs_as_a_module(monkeypatch, capsys):
    import io
    import runpy
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool": "compare", "args": {"series": [1.0], "horizon": 1}})))
    assert ts_cli.main() == 0
    assert json.loads(capsys.readouterr().out)["ok"] is False                      # the real compare: too little data, but it ran
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    with pytest.raises(SystemExit) as e:
        runpy.run_module("app.ts_cli", run_name="__main__")
    assert e.value.code == 0
