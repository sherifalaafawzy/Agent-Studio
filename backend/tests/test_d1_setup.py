"""Install / status / uninstall of the local decision model, its pinned plan, and its place in the MCP catalogue.

Nothing real is downloaded or installed: the Installer's command runner is replaced by a fake that records every command
(so the pinned plan is asserted) and fabricates the files a real install would leave behind.
"""
import asyncio
import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import d1_setup, d1_spec
from app import mcp_registry as R
from app.d1_setup import Installer, SetupConflict

CUDA_HW = dict(nvidia=True, gpu="NVIDIA GeForce RTX 5080", vram_mb=16303, driver="610.88", cuda=[13, 3])
NO_HW = dict(nvidia=False, gpu=None, vram_mb=None, driver=None, cuda=None)
STEPS = ["venv", "pip", "torch", "libs", "weights", "verify"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "d1"
    monkeypatch.setenv("D1_HOME", str(h))
    return h


class FakeRun:
    """Stands in for pip/venv/download subprocesses."""

    def __init__(self, home, fail_at=None, hold=None, verify=None):
        self.home, self.fail_at, self.hold, self.verify = home, fail_at, hold, verify
        self.cmds, self.envs = [], []
        self.reached = threading.Event()

    def __call__(self, cmd, env, on_line):
        i = len(self.cmds)
        self.cmds.append(cmd)
        self.envs.append(env)
        on_line("   ")                                                       # blank lines are ignored
        on_line(f"step {STEPS[i]}")
        if i == self.fail_at:
            on_line("ERROR: No matching distribution found")
            return 1
        if STEPS[i] == "venv":
            py = d1_spec.env_python(self.home)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("")
        if STEPS[i] == "weights":
            on_line("PROGRESS 1500000000 3866417920")
            self.reached.set()
            if self.hold:
                self.hold.wait(10)
        if STEPS[i] == "verify":
            for ln in (self.verify if self.verify is not None else [json.dumps(dict(torch="2.15.0+cu132", transformers="5.22.2", cuda=True, gpu="RTX"))]):
                on_line(ln)
        return 0


def make(home, runner=None, hw=CUDA_HW, platform="win32"):
    return Installer(runner=runner or FakeRun(home), hardware=lambda: hw, platform=platform)


def finish(inst):
    inst.thread.join(15)
    assert not inst.thread.is_alive()


# ---------------------------------------------------------------- the pinned facts

def test_model_revision_is_a_pinned_commit_never_main():
    assert d1_spec.REVISION_RE.match(d1_spec.REVISION) and d1_spec.REVISION != "main"
    assert d1_spec.REVISION in d1_spec.LICENSE_URL                     # the licence shown is the one at the reviewed commit
    assert "10M" in " ".join(d1_spec.LICENSE_SUMMARY)
    assert any("NOT licensed" in t for t in d1_spec.LICENSE_SUMMARY)
    assert d1_spec.REVIEW_NOTES and d1_spec.REVISION in d1_spec.REVIEW_NOTES[0]


def test_every_pip_pin_is_exact_and_binary_only():
    pins = d1_spec.LIB_PINS + d1_spec.TORCH_CUDA["packages"] + d1_spec.TORCH_CPU["packages"]
    assert all("==" in p for p in pins)
    assert "transformers==5.22.2" in pins                              # model card needs transformers >= 5.19
    assert d1_spec.PIP_CUTOFF < "2026-11-08"                           # nothing newer than 14 days before 2026-11-21


def test_state_roundtrip_and_readiness(home):
    assert d1_spec.read_state() == {} and not d1_spec.is_ready()
    d1_spec.write_state({"state": "ready", "revision": d1_spec.REVISION})
    assert d1_spec.read_state()["state"] == "ready" and "ts" in d1_spec.read_state()
    assert not d1_spec.is_ready()                                      # no environment python yet
    py = d1_spec.env_python()
    py.parent.mkdir(parents=True)
    py.write_text("")
    assert d1_spec.is_ready()
    d1_spec.write_state({"state": "ready", "revision": "0" * 40})
    assert not d1_spec.is_ready()                                      # installed for a different revision
    d1_spec.state_file().write_text("{not json")
    assert d1_spec.read_state() == {}
    d1_spec.state_file().write_text("[1]")
    assert d1_spec.read_state() == {}


def test_home_defaults_into_the_data_dir_and_env_python_follows_the_platform(monkeypatch):
    monkeypatch.delenv("D1_HOME", raising=False)
    from app.db import data_dir
    assert d1_spec.home() == data_dir() / "envs" / "d1"
    monkeypatch.setattr(d1_spec.sys, "platform", "win32")
    assert d1_spec.env_python().parts[-2:] == ("Scripts", "python.exe")
    monkeypatch.setattr(d1_spec.sys, "platform", "linux")
    assert d1_spec.env_python().parts[-2:] == ("bin", "python")


@pytest.mark.parametrize("cuda,platform,variant", [
    ((13, 3), "win32", "cuda"), ((13, 2), "linux", "cuda"), ((12, 9), "win32", "cpu"), (None, "linux", "cpu"),
    ((13, 3), "darwin", "default"), (None, "darwin", "default")])
def test_torch_build_follows_the_driver(cuda, platform, variant):
    assert d1_spec.choose_torch(cuda, platform)["variant"] == variant


def test_plan_lists_sized_steps():
    steps = d1_spec.plan(d1_spec.TORCH_CUDA)
    assert [s["id"] for s in steps] == STEPS
    assert next(s for s in steps if s["id"] == "weights")["size_mb"] == 3836


# ---------------------------------------------------------------- the MCP catalogue entry

def test_catalog_entry_is_off_and_not_ready_until_installed(state, home):
    s = state.mcp.get("d1")
    assert s.enabled is False and s.builtin and s.command == "{d1_python}" and s.args == ["-m", "app.mcp_d1"]
    assert s.public()["ready"] is False
    state.mcp.update("d1", True)
    s = state.mcp.get("d1")
    assert s.enabled and s.setup_missing() and not s.ready()           # enabling does not make it ready


def test_unready_d1_is_skipped_with_an_install_hint(state, home, tmp_path):
    state.mcp.update("d1", True)
    warns = []
    assert asyncio.run(R.build_toolsets(state.mcp, ["d1"], str(tmp_path), warns)) == []
    assert len(warns) == 1 and "not installed" in warns[0] and "Install" in warns[0]


def test_ready_d1_starts_the_isolated_environments_python(state, home, tmp_path, monkeypatch):
    from google.adk.tools.mcp_tool import mcp_toolset as mod
    made = []

    class FakeToolset:
        def __init__(self, connection_params, tool_name_prefix):
            made.append((connection_params, tool_name_prefix))

        async def get_tools(self):
            return []

    monkeypatch.setattr(mod, "McpToolset", FakeToolset)
    py = d1_spec.env_python(home)
    py.parent.mkdir(parents=True)
    py.write_text("")
    d1_spec.write_state({"state": "ready", "revision": d1_spec.REVISION}, home)
    state.mcp.update("d1", True)
    assert state.mcp.get("d1").ready()
    warns = []
    assert len(asyncio.run(R.build_toolsets(state.mcp, ["d1"], str(tmp_path), warns))) == 1 and warns == []
    params, prefix = made[0]
    sp = params.server_params
    assert Path(sp.command).resolve() == py.resolve()                  # the env's python, NOT the backend venv's
    assert Path(sp.command).resolve() != Path(sys.executable).resolve()
    assert sp.args == ["-m", "app.mcp_d1"] and prefix == "d1_"
    assert Path(sp.env["PYTHONPATH"]) == Path(R.__file__).resolve().parents[1]
    assert Path(sp.env["D1_HOME"]) == home                             # heavy deps + weights stay under data/envs/d1


def test_default_home_is_consistent_between_catalogue_and_server(monkeypatch):
    monkeypatch.delenv("D1_HOME", raising=False)
    from app.db import data_dir
    assert R._subst("{d1_home}", "") == str(data_dir() / "envs" / "d1") == str(d1_spec.home())


def test_install_for_another_revision_is_not_ready(state, home):
    d1_spec.write_state({"state": "ready", "revision": "1" * 40}, home)
    py = d1_spec.env_python(home)
    py.parent.mkdir(parents=True)
    py.write_text("")
    state.mcp.update("d1", True)
    assert state.mcp.get("d1").ready() is False


# ---------------------------------------------------------------- hardware + expectations + helpers

class Out:
    def __init__(self, stdout):
        self.stdout = stdout


def test_probe_hardware_parses_nvidia_smi(monkeypatch):
    outs = ["| NVIDIA-SMI 610.88  Driver Version: 610.88  CUDA Version: 13.3 |", "NVIDIA GeForce RTX 5080, 16303, 610.88\n"]
    monkeypatch.setattr(d1_setup.shutil, "which", lambda n: "nvidia-smi")
    monkeypatch.setattr(d1_setup.subprocess, "run", lambda *a, **k: Out(outs.pop(0)))
    assert d1_setup.probe_hardware() == CUDA_HW
    outs[:] = ["CUDA Version: 12.4", "Tesla, [N/A], 550.1"]
    hw = d1_setup.probe_hardware()
    assert hw["vram_mb"] is None and hw["cuda"] == [12, 4]


@pytest.mark.parametrize("plain,query", [("no cuda text here", "GPU, 1, 2"), ("CUDA Version: 13.3", ""), ("CUDA Version: 13.3", "only,two")])
def test_probe_hardware_treats_unparseable_output_as_no_gpu(monkeypatch, plain, query):
    outs = [plain, query]
    monkeypatch.setattr(d1_setup.shutil, "which", lambda n: "nvidia-smi")
    monkeypatch.setattr(d1_setup.subprocess, "run", lambda *a, **k: Out(outs.pop(0)))
    assert d1_setup.probe_hardware()["nvidia"] is False


def test_probe_hardware_without_nvidia_smi_or_when_it_fails(monkeypatch):
    monkeypatch.setattr(d1_setup.shutil, "which", lambda n: None)
    assert d1_setup.probe_hardware()["nvidia"] is False
    monkeypatch.setattr(d1_setup.shutil, "which", lambda n: "nvidia-smi")
    for exc in (OSError("gone"), subprocess.TimeoutExpired("nvidia-smi", 15)):
        def boom(*a, _e=exc, **k):
            raise _e
        monkeypatch.setattr(d1_setup.subprocess, "run", boom)
        assert d1_setup.probe_hardware()["nvidia"] is False


def test_expectations_are_honest_about_what_was_measured():
    cuda = d1_setup.expectation(CUDA_HW, d1_spec.TORCH_CUDA)
    assert "RTX 5080" in cuda and "16 GB" in cuda and "not measured" in cuda
    assert "unmeasured" in d1_setup.expectation(NO_HW, d1_spec.TORCH_CPU) and "no usable NVIDIA GPU" in d1_setup.expectation(NO_HW, d1_spec.TORCH_CPU)
    assert "driver is too old" in d1_setup.expectation(CUDA_HW, d1_spec.TORCH_CPU)
    assert "MPS" in d1_setup.expectation(NO_HW, d1_spec.TORCH_DEFAULT)


def test_free_disk_walks_up_to_an_existing_folder(tmp_path, monkeypatch):
    assert d1_setup.free_disk_gb(tmp_path / "a" / "b" / "c") > 0
    monkeypatch.setattr(d1_setup.shutil, "disk_usage", lambda p: (_ for _ in ()).throw(OSError("no such drive")))
    assert d1_setup.free_disk_gb(tmp_path) is None


def test_run_streaming_feeds_lines_and_reports_exit_codes():
    lines = []
    assert d1_setup.run_streaming([sys.executable, "-c", "print('a'); print('b')"], dict(os.environ), lines.append) == 0
    assert lines == ["a", "b"]
    assert d1_setup.run_streaming([sys.executable, "-c", "import sys; sys.exit(3)"], dict(os.environ), lines.append) == 3
    lines.clear()
    assert d1_setup.run_streaming([str(Path(sys.executable).parent / "definitely-not-a-program")], {}, lines.append) == 127
    assert "cannot start" in lines[0]


def test_child_environment_drops_tokens_and_points_at_the_isolated_home(home, monkeypatch):
    for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
        monkeypatch.setenv(k, "secret")
    env = d1_setup._child_env(home, {"PYTHONPATH": "/backend"})
    assert not {"HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "VIRTUAL_ENV", "PYTHONHOME"} & set(env)
    assert env["PYTHONPATH"] == "/backend" and env["D1_HOME"] == str(home) and env["HF_HOME"] == str(home / "hf")
    assert d1_setup._child_env(home)["PIP_DISABLE_PIP_VERSION_CHECK"] == "1" and "PYTHONPATH" not in d1_setup._child_env(home)


# ---------------------------------------------------------------- the install flow

def test_fresh_status_is_not_installed_and_lists_the_plan(home):
    s = make(home).status()
    assert s["state"] == "not_installed" and s["ready"] is False and s["error"] is None and s["step"] is None
    assert [x["id"] for x in s["steps"]] == STEPS and all(x["status"] == "pending" for x in s["steps"])
    assert s["percent"] == 0 and s["progress_text"] == "" and s["log_tail"] == []
    assert s["hardware"]["variant"] == "cuda" and "cu132" in s["hardware"]["torch_build"] and s["download_mb"] == 6559
    assert s["disk_gb_needed"] == 9.6 and s["free_gb"] > 0
    assert s["revision"] == d1_spec.REVISION and s["repo"] == "LiquidAI/d1-2B" and s["home"] == str(home)
    assert s["license"]["name"] == "LFM Open License v1.0" and s["review_notes"] and "RTX 5080" in s["expect"]
    assert s["versions"] is None


def test_install_runs_the_pinned_plan_and_ends_ready(home, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    run = FakeRun(home)
    inst = make(home, run)
    first = inst.start(True)
    assert first["state"] in ("installing", "ready")
    finish(inst)
    s = inst.status()
    assert s["state"] == "ready" and s["ready"] and s["percent"] == 100 and all(x["status"] == "done" for x in s["steps"])
    assert s["versions"]["transformers"] == "5.22.2" and d1_spec.is_ready(home)
    assert s["progress_text"] == "" and s["log_tail"] == []
    assert d1_spec.read_state(home)["torch"] == "cuda"

    venv, pip, torch, libs, weights, verify = run.cmds
    assert venv[:4] == [sys.executable, "-m", "venv", "--clear"] and venv[4] == str(home / "venv")
    py = str(d1_spec.env_python(home))
    assert pip == [py, "-m", "pip", "install", "--no-input", "pip==26.2.2"]
    assert torch[0] == py and "--only-binary=:all:" in torch
    assert torch[torch.index("--index-url") + 1] == "https://download.pytorch.org/whl/cu132"
    assert torch[-2:] == ["torch==2.15.0", "torchvision==0.29.2"]
    assert "--only-binary=:all:" in libs and libs[libs.index("--uploaded-prior-to") + 1] == "2026-11-07T00:00:00Z"
    assert {"transformers==5.22.2", "huggingface_hub==2.4.0", "mcp==2.3.0", "pillow==12.3.0"} <= set(libs)
    assert weights == [py, "-m", "app.d1_fetch"] and verify[:2] == [py, "-c"]
    assert Path(run.envs[4]["PYTHONPATH"]) == d1_setup.BACKEND_DIR and "PYTHONPATH" not in run.envs[2]
    assert all("HF_TOKEN" not in e for e in run.envs)                  # no credentials reach the install subprocesses


def test_cpu_device_and_cpu_only_machines_pick_the_small_build(home):
    for hw, device in ((CUDA_HW, "cpu"), (NO_HW, "auto")):
        run = FakeRun(home)
        inst = make(home, run, hw=hw)
        inst.start(True, device)
        finish(inst)
        assert run.cmds[2][run.cmds[2].index("--index-url") + 1] == "https://download.pytorch.org/whl/cpu"
        inst.uninstall()


def test_default_pypi_torch_build_has_no_index_flag(home):
    run = FakeRun(home)
    inst = make(home, run, hw=NO_HW, platform="darwin")
    inst.start(True)
    finish(inst)
    assert "--index-url" not in run.cmds[2]


def test_status_while_installing_reports_step_progress(home):
    hold = threading.Event()
    run = FakeRun(home, hold=hold)
    inst = make(home, run)
    inst.start(True)
    assert run.reached.wait(10)
    s = inst.status()
    assert s["state"] == "installing" and s["step"] == "weights" and s["ready"] is False
    assert [x["status"] for x in s["steps"]] == ["done"] * 4 + ["running", "pending"]
    assert s["progress_text"] == "Downloading model weights: 1.50 of 3.87 GB" and s["percent"] == 64
    assert "== Download the model weights" in " ".join(s["log_tail"]) and "step weights" in s["log_tail"]
    p = inst.progress()
    assert set(p) == {"state", "ready", "error", "step", "steps", "percent", "progress_text", "log_tail"} and p["step"] == "weights"
    with pytest.raises(SetupConflict, match="already running"):
        inst.start(True)
    with pytest.raises(SetupConflict, match="running"):
        inst.uninstall()
    hold.set()
    finish(inst)
    assert inst.status()["state"] == "ready"


def test_a_failed_step_is_reported_persisted_and_can_be_retried(home):
    inst = make(home, FakeRun(home, fail_at=2))
    inst.start(True)
    finish(inst)
    s = inst.status()
    assert s["state"] == "failed" and s["ready"] is False
    assert "PyTorch" in s["error"] and "exit code 1" in s["error"] and "No matching distribution" in s["error"]
    assert [x["status"] for x in s["steps"]] == ["done", "done", "failed", "pending", "pending", "pending"]
    assert "ERROR: No matching distribution found" in s["log_tail"]
    assert not d1_spec.is_ready(home) and d1_spec.read_state(home)["state"] == "failed"

    restarted = make(home)                                           # the app restarted: the failure is still shown
    r = restarted.status()
    assert r["state"] == "failed" and "No matching distribution" in r["error"] and all(x["status"] == "pending" for x in r["steps"])

    inst.runner = FakeRun(home)                                      # retry with a working network
    inst.start(True)
    finish(inst)
    assert inst.status()["state"] == "ready" and inst.error is None


def test_an_interrupted_install_shows_as_failed(home):
    d1_spec.write_state({"state": "installing", "revision": d1_spec.REVISION}, home)
    s = make(home).status()
    assert s["state"] == "failed" and "interrupted" in s["error"]


def test_unreadable_environment_check_fails_the_install(home):
    inst = make(home, FakeRun(home, verify=["warning: something", "[1]"]))
    inst.start(True)
    finish(inst)
    s = inst.status()
    assert s["state"] == "failed" and "environment check" in s["error"]
    assert s["steps"][5]["status"] == "failed"


def test_a_crash_while_writing_state_is_contained(home, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(d1_spec, "write_state", boom)
    inst = make(home)
    inst.start(True)
    finish(inst)
    assert "disk full" in inst.error and inst.failed_step == 0         # the thread ended cleanly; nothing was left half-ready
    assert not d1_spec.is_ready(home)


def test_start_refuses_without_consent_or_with_bad_input(home, monkeypatch):
    inst = make(home)
    with pytest.raises(ValueError, match="accept the LFM Open License"):
        inst.start(False)
    with pytest.raises(ValueError, match="device"):
        inst.start(True, "tpu")
    monkeypatch.setattr(d1_setup, "free_disk_gb", lambda p: 1.5)
    with pytest.raises(ValueError, match="not enough free disk space: need about 9.6 GB, 1.5 GB free"):
        inst.start(True)
    assert inst.thread is None and not home.exists()                    # nothing was touched
    monkeypatch.setattr(d1_setup, "free_disk_gb", lambda p: None)       # free space unknown: do not block
    inst.start(True)
    finish(inst)
    assert inst.status()["ready"]


def test_start_refuses_when_already_installed(home):
    inst = make(home)
    inst.start(True)
    finish(inst)
    with pytest.raises(SetupConflict, match="already installed"):
        inst.start(True)


def test_uninstall_removes_only_our_environment(home, tmp_path):
    inst = make(home)
    assert inst.uninstall() == {"removed": False, "path": str(home)}      # nothing there
    inst.start(True)
    finish(inst)
    locked = home / "hf" / "readonly.bin"
    locked.parent.mkdir(parents=True, exist_ok=True)
    locked.write_bytes(b"x")
    os.chmod(locked, stat.S_IREAD)                                      # caches contain read-only files on Windows
    r = inst.uninstall()
    assert r["removed"] is True and not home.exists() and inst.status()["state"] == "not_installed"

    home.mkdir()
    (home / "keep.txt").write_text("someone else's file")                 # a folder that is not ours is never deleted
    assert inst.uninstall()["removed"] is False and (home / "keep.txt").exists()


# ---------------------------------------------------------------- HTTP API

@pytest.fixture
def api(client, home):
    inst = client.app.state.d1
    inst._probe, inst._hw, inst.platform = (lambda: CUDA_HW), None, "win32"
    return client, inst


def test_http_install_lifecycle_and_mcp_readiness(api, home):
    client, inst = api
    s = client.get("/api/d1/status").json()
    assert s["state"] == "not_installed" and s["license"]["summary"] and s["hardware"]["variant"] == "cuda"
    m = next(x for x in client.get("/api/mcp").json() if x["id"] == "d1")
    assert m["category"] == "Local models" and m["ready"] is False and m["enabled"] is False

    assert client.post("/api/d1/install", json={}).status_code == 422                      # consent is required
    assert "accept the LFM Open License" in client.post("/api/d1/install", json={}).json()["detail"]
    assert client.post("/api/d1/install", json={"accept_license": True, "device": "tpu"}).status_code == 422

    hold = threading.Event()
    inst.runner = run = FakeRun(home, hold=hold)
    r = client.post("/api/d1/install", json={"accept_license": True})
    assert r.status_code == 200 and r.json()["state"] == "installing"
    assert run.reached.wait(10)
    assert client.post("/api/d1/install", json={"accept_license": True}).status_code == 409
    assert client.post("/api/d1/uninstall").status_code == 409
    p = client.get("/api/d1/install/progress").json()
    assert p["state"] == "installing" and p["step"] == "weights" and 0 < p["percent"] < 100
    hold.set()
    finish(inst)
    assert client.get("/api/d1/status").json()["state"] == "ready"
    assert client.post("/api/d1/install", json={"accept_license": True}).status_code == 409   # already installed

    m = next(x for x in client.get("/api/mcp").json() if x["id"] == "d1")
    assert m["ready"] is False                                          # installed, but the user has not switched it on
    m = client.put("/api/mcp/d1", json={"enabled": True}).json()
    assert m["ready"] is True

    assert client.post("/api/d1/uninstall").json()["removed"] is True
    assert client.get("/api/d1/status").json()["state"] == "not_installed"
    assert next(x for x in client.get("/api/mcp").json() if x["id"] == "d1")["ready"] is False


def test_register_accepts_an_explicit_installer(home):
    app = FastAPI()
    inst = make(home)
    assert d1_setup.register(app, inst) is inst and app.state.d1 is inst
    c = TestClient(app)
    assert c.get("/api/d1/status").json()["state"] == "not_installed"
