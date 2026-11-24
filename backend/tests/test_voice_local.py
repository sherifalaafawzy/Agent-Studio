"""Local live voice: pins, paths, the installer (with a fake command runner) and the fetch step."""
from __future__ import annotations

import json
import threading

import pytest

from app import d1_setup, voice_fetch, voice_local as VL, voice_spec as VS

GPU = dict(nvidia=True, gpu="NVIDIA GeForce RTX 5080", vram_mb=16303, driver="610.88", cuda=[13, 3])
CPU = dict(nvidia=False, gpu=None, vram_mb=None, driver=None, cuda=None)
CHECK = json.dumps({"faster-whisper": "1.2.1", "kokoro-onnx": "0.6.1", "onnxruntime-gpu": "1.22.0", "device": "cuda", "tts_device": "cuda"})


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_HOME", str(tmp_path / "voice"))


def good_runner(calls=None, check_line=CHECK, fail_at=None):
    def run(cmd, env, on_line):
        if calls is not None:
            calls.append((cmd, env))
        if "--clear" in cmd:                                               # `python -m venv --clear`: the environment's python now exists
            VS.env_python().parent.mkdir(parents=True, exist_ok=True)
            VS.env_python().write_text("x")
        if fail_at and fail_at in " ".join(cmd):
            on_line("boom one")
            on_line("boom two")
            return 3
        if "--check" in cmd:
            on_line("\x00\x1b[0;93mW:onnxruntime: some warning\x00")      # onnxruntime writes UTF-16 text; our JSON line follows with a stray NUL
            on_line("\x00" + check_line)
        return 0
    return run


def wait(inst):
    inst.thread.join(timeout=10)
    assert not inst.running()


# ---------------------------------------------------------------- spec
def test_paths_and_state_round_trip(tmp_path):
    h = VS.home()
    assert h == tmp_path / "voice"
    assert VS.env_python(h).name in ("python.exe", "python") and VS.models_dir(h) == h / "models" and VS.hf_home(h) == h / "hf"
    assert VS.read_state(h) == {}
    VS.write_state({"state": "ready", "pins": VS._pins()}, h)
    assert VS.read_state(h)["state"] == "ready"
    assert not VS.is_ready(h)                                      # the environment's python does not exist
    VS.env_python(h).parent.mkdir(parents=True)
    VS.env_python(h).write_text("x")
    assert VS.is_ready(h)
    VS.write_state({"state": "ready", "pins": {"stale": 1}}, h)
    assert not VS.is_ready(h)                                      # pins changed: reinstall needed
    (h / "state.json").write_text("[1]")
    assert VS.read_state(h) == {}
    (h / "state.json").write_text("{not json")
    assert VS.read_state(h) == {}


def test_home_falls_back_to_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("VOICE_HOME")
    assert VS.home() == tmp_path / "home" / "envs" / "voice"


def test_device_and_model_choices_and_plan():
    assert VS.choose_device(GPU) == "cuda" and VS.choose_device(GPU, "cpu") == "cpu" and VS.choose_device(CPU) == "cpu"
    assert VS.choose_device({"nvidia": True, "cuda": [11, 8]}) == "cpu" and VS.choose_device({"nvidia": True}) == "cpu"
    assert VS.default_stt("cuda") == "small.en" and VS.default_stt("cpu") == "base.en"
    gpu, cpu = VS.plan("cuda", "small.en"), VS.plan("cpu", "base.en")
    assert [s["id"] for s in gpu] == ["venv", "pip", "packages", "cuda", "models", "verify"]
    assert [s["id"] for s in cpu] == ["venv", "pip", "packages", "models", "verify"]
    assert sum(s["size_mb"] for s in gpu) > sum(s["size_mb"] for s in cpu)
    assert "compiles kernels" in gpu[-1]["label"] and "compiles kernels" not in cpu[-1]["label"]
    assert all(len(v["sha256"]) == 64 for v in {**VS.TTS_FILES, **VS.TTS_GPU_FILES}.values())
    assert all(len(v["revision"]) == 40 for v in VS.STT_MODELS.values())
    assert VS.DEFAULT_VOICE in VS.VOICES


def test_the_expectation_text_names_the_mode():
    assert "GPU speech recognition" in VL.expectation("cuda", "small.en") and "CPU speech recognition" in VL.expectation("cpu", "base.en")


def test_child_env_drops_python_and_token_variables(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "x")
    monkeypatch.setenv("HF_TOKEN", "secret")
    env = VL._child_env(VS.home(), {"A": "b"})
    assert "VIRTUAL_ENV" not in env and "HF_TOKEN" not in env and env["A"] == "b" and env["VOICE_HOME"] == str(VS.home())


# ---------------------------------------------------------------- installer
def test_start_validates_before_doing_anything():
    inst = VL.Installer(runner=good_runner(), hardware=lambda: GPU)
    with pytest.raises(ValueError, match="accept the licences"):
        inst.start(False)
    with pytest.raises(ValueError, match="device"):
        inst.start(True, "tpu")
    with pytest.raises(ValueError, match="unknown speech model"):
        inst.start(True, "auto", "huge.en")


def test_not_enough_disk_space_is_refused(monkeypatch):
    monkeypatch.setattr(VL, "free_disk_gb", lambda p: 0.5)
    with pytest.raises(ValueError, match="not enough free disk space"):
        VL.Installer(runner=good_runner(), hardware=lambda: GPU).start(True)
    monkeypatch.setattr(VL, "free_disk_gb", lambda p: None)          # unknown free space does not block
    inst = VL.Installer(runner=good_runner(), hardware=lambda: CPU)
    inst.start(True)
    wait(inst)


def test_a_gpu_install_runs_every_step_in_order_and_ends_ready():
    calls: list = []
    inst = VL.Installer(runner=good_runner(calls), hardware=lambda: GPU)
    st = inst.start(True)
    assert st["state"] == "installing" and st["device"] == "cuda" and st["stt_model"] == "small.en"
    wait(inst)
    cmds = [" ".join(c[0]) for c in calls]
    assert "-m venv --clear" in cmds[0] and "pip==26.2.2" in cmds[1]
    assert "--only-binary=:all:" in cmds[2] and "--uploaded-prior-to 2026-11-09T00:00:00Z" in cmds[2] and "faster-whisper==1.2.1" in cmds[2] and "kokoro-onnx==0.6.1" in cmds[2]
    assert "pip uninstall --yes onnxruntime" in cmds[3] and "onnxruntime-gpu==1.22.0" in cmds[4] and "nvidia-cudnn-cu12" in cmds[4]
    assert "app.voice_fetch" in cmds[5] and "app.voice_worker --check" in cmds[6]
    fetch_env = calls[5][1]
    assert fetch_env["VOICE_DEVICE"] == "cuda" and fetch_env["VOICE_STT_MODEL"] == "small.en" and fetch_env["PYTHONPATH"]
    done = inst.status()
    assert done["state"] == "ready" and done["percent"] == 100 and done["versions"]["onnxruntime-gpu"] == "1.22.0"
    assert all(s["status"] == "done" for s in done["steps"])
    with pytest.raises(VL.SetupConflict, match="already installed"):
        inst.start(True)


def test_a_cpu_install_skips_the_gpu_steps(monkeypatch):
    calls: list = []
    monkeypatch.setattr(VS, "is_ready", lambda h=None: False)
    inst = VL.Installer(runner=good_runner(calls), hardware=lambda: GPU)
    inst.start(True, "cpu")
    wait(inst)
    text = " ".join(" ".join(c[0]) for c in calls)
    assert "onnxruntime-gpu" not in text and "uninstall" not in text
    assert inst.status()["device"] == "cpu" and inst.status()["stt_model"] == "base.en"


def test_a_failed_step_is_explained_and_can_be_retried():
    inst = VL.Installer(runner=good_runner(fail_at="onnxruntime-gpu"), hardware=lambda: GPU)
    inst.start(True)
    wait(inst)
    st = inst.status()
    assert st["state"] == "failed" and "GPU build of onnxruntime" in st["error"] and "boom one | boom two" in st["error"]
    assert [s["status"] for s in st["steps"]][:4] == ["done", "done", "done", "failed"]
    assert st["log_tail"]
    inst2 = VL.Installer(runner=good_runner(), hardware=lambda: GPU)      # a new process: the failure is read back from disk
    assert inst2.status()["state"] == "failed" and "boom" in inst2.status()["error"]
    inst.runner = good_runner()
    inst.start(True)                                                   # retry
    wait(inst)
    assert inst.status()["state"] == "ready"


def test_unreadable_check_output_fails_the_install():
    inst = VL.Installer(runner=good_runner(check_line="no json here"), hardware=lambda: GPU)
    inst.start(True)
    wait(inst)
    assert inst.status()["state"] == "failed" and "printed nothing readable" in inst.status()["error"]
    assert VL.Installer._parse_versions(["noise", '{"a": 1}', "[1, 2] trailing"]) == {"a": 1}


def test_a_second_start_while_running_conflicts_and_an_interrupted_install_is_reported():
    gate = threading.Event()

    def slow(cmd, env, on_line):
        gate.wait(5)
        return 0
    inst = VL.Installer(runner=slow, hardware=lambda: GPU)
    inst.start(True)
    with pytest.raises(VL.SetupConflict, match="already running"):
        inst.start(True)
    with pytest.raises(VL.SetupConflict, match="install is running"):
        inst.uninstall()
    assert inst.status()["state"] == "installing" and inst.status()["steps"][0]["status"] == "running"
    gate.set()
    inst.thread.join(timeout=10)
    VS.write_state({"state": "installing"})
    assert VL.Installer(hardware=lambda: GPU).status()["error"].startswith("the install was interrupted")


def test_progress_lines_and_uninstall():
    inst = VL.Installer(runner=good_runner(), hardware=lambda: CPU)
    inst._on_line("   ")
    inst._on_line("Downloading something")
    assert inst.progress_text == "Downloading something"
    assert inst.status()["state"] == "not_installed"
    assert inst.uninstall()["removed"] is False
    VS.models_dir().mkdir(parents=True)
    (VS.models_dir() / "x.bin").write_text("x")
    assert inst.uninstall()["removed"] is True and not VS.home().exists()


def test_write_failure_while_recording_failure_is_survivable(monkeypatch):
    inst = VL.Installer(runner=good_runner(fail_at="venv"), hardware=lambda: CPU)
    real = VL.write_state

    def flaky(data, h=None):
        if data.get("state") == "failed":
            raise OSError("disk full")
        real(data, h)
    monkeypatch.setattr(VL, "write_state", flaky)
    inst.start(True)
    wait(inst)
    assert "Create a private Python environment" in inst.error


# ---------------------------------------------------------------- fetch (runs inside the voice environment)
def test_download_verifies_size_and_hash(tmp_path):
    import hashlib
    data = b"x" * 3000
    sha = hashlib.sha256(data).hexdigest()
    said: list[str] = []

    class R:
        def __init__(self, b): self.b = b; self.i = 0
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n):
            chunk, self.b = self.b[:n], self.b[n:]
            return chunk
    dest = tmp_path / "m" / "f.bin"
    voice_fetch.download("http://x/f", dest, len(data), sha, said.append, lambda u: R(data))
    assert dest.read_bytes() == data and said[-1] == "f.bin: verified"
    said.clear()
    voice_fetch.download("http://x/f", dest, len(data), sha, said.append, lambda u: pytest.fail("must not download again"))
    assert "already downloaded" in said[0]
    bad = tmp_path / "m" / "g.bin"
    with pytest.raises(SystemExit, match="does not match the pinned"):
        voice_fetch.download("http://x/g", bad, len(data), sha, said.append, lambda u: R(b"y" * 3000))
    assert not bad.exists() and not bad.with_suffix(".bin.part").exists()
    assert not voice_fetch.matches(tmp_path / "nope", 1, "x")


def test_fetch_main_downloads_everything_for_the_chosen_device(monkeypatch, tmp_path):
    got: list = []
    monkeypatch.setattr(voice_fetch, "download", lambda url, dest, size, sha, say, opener=None: got.append(dest.name))
    snaps: list = []
    said: list[str] = []
    monkeypatch.setenv("VOICE_STT_MODEL", "small.en")
    monkeypatch.setenv("VOICE_DEVICE", "cuda")
    assert voice_fetch.main(say=said.append, snap=lambda r, rev, t: snaps.append((r, rev, t.name))) == 0
    assert got == ["kokoro-v1.0.int8.onnx", "voices-v1.0.bin", "kokoro-v1.0.onnx"]
    assert snaps == [("Systran/faster-whisper-small.en", VS.STT_MODELS["small.en"]["revision"], "whisper-small.en")] and said[-1] == "all models in place"
    got.clear()
    monkeypatch.setenv("VOICE_DEVICE", "cpu")
    monkeypatch.delenv("VOICE_STT_MODEL")
    voice_fetch.main(say=said.append, snap=lambda r, rev, t: snaps.append((r, rev, t.name)))
    assert got == ["kokoro-v1.0.int8.onnx", "voices-v1.0.bin"] and snaps[-1][2] == "whisper-base.en"
    monkeypatch.setenv("VOICE_STT_MODEL", "nope")
    assert voice_fetch.main(say=said.append) == 2
    printed: list = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(a))
    monkeypatch.setenv("VOICE_STT_MODEL", "nope")
    voice_fetch.main()
    assert printed


def test_the_hardware_probe_reads_newer_driver_wording(monkeypatch):
    outs = ["| NVIDIA-SMI 610.88   KMD Version: 610.88   CUDA UMD Version: 13.3 |", "NVIDIA GeForce RTX 5080, 16303, 610.88\n"]
    import subprocess

    class P:
        def __init__(self, out): self.stdout = out
    monkeypatch.setattr(d1_setup.shutil, "which", lambda n: "nvidia-smi")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: P(outs.pop(0)))
    hw = d1_setup.probe_hardware()
    assert hw["nvidia"] is True and hw["cuda"] == [13, 3] and hw["gpu"] == "NVIDIA GeForce RTX 5080"


def test_uninstall_clears_read_only_files_that_block_deletion(monkeypatch):
    import os
    import stat
    inst = VL.Installer(runner=good_runner(), hardware=lambda: CPU)
    VS.models_dir().mkdir(parents=True)
    ro = VS.models_dir() / "locked.bin"
    ro.write_text("x")
    os.chmod(ro, stat.S_IREAD)
    seen = []
    monkeypatch.setattr(VL.shutil, "rmtree", lambda path, onerror: (seen.append(path), onerror(os.remove, str(ro), None)))
    assert inst.uninstall()["removed"] is True
    assert not ro.exists() and seen
