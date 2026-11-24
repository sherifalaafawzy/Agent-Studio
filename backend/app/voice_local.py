"""Local live voice: the pinned engines, where they live, and the one-click install / status / uninstall.

Live mode needs a speech-to-text engine and a text-to-speech engine that run ON THIS PC (nothing is sent anywhere):

* **speech to text** - faster-whisper (MIT) with the Systran conversions of OpenAI's Whisper (MIT): `small.en` on an NVIDIA GPU,
  `base.en` on the CPU.
* **text to speech** - Kokoro 82M through kokoro-onnx (MIT code, Apache-2.0 weights), int8 build, on the CPU.

They are installed into an ISOLATED environment (`<data>/envs/voice/venv`) so their numpy / onnxruntime / ctranslate2 never touch the
backend's packages; the live worker (`app.voice_worker`) runs inside it and keeps both models warm. The install follows the shared
installer rules (`pip_install`): ready-made wheels only, nothing younger than 14 days, exact versions. Model files are pinned too
(a Hugging Face commit for Whisper; a SHA-256 for each Kokoro file) and checked after download.

Piper (the other popular local voice) is NOT used: its current Python package is GPL-3.0 and Agent Studio does not ship GPL code.
The command runner is injectable, so the tests exercise the whole flow without a download.
"""
from __future__ import annotations

import collections
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .d1_setup import free_disk_gb, probe_hardware, run_streaming
from .voice_spec import (CUDA_PACKAGES, ORT_GPU, LICENSES, PACKAGES, PIP_CUTOFF, PIP_VERSION, STT_MODELS, TTS_FILES, VOICES, choose_device, default_stt, env_python, hf_home, home, is_ready, plan, read_state, write_state, _pins)

BACKEND_DIR = Path(__file__).resolve().parents[1]


class SetupConflict(Exception):
    """An install is already running / done."""


class InstallError(Exception):
    """A step failed; the message is shown to the user."""


def _child_env(h: Path, extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")}
    env.update(PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1", PYTHONUTF8="1", VOICE_HOME=str(h), HF_HOME=str(hf_home(h)),
               HF_HUB_DISABLE_SYMLINKS_WARNING="1")
    env.update(extra or {})
    return env


class Installer:
    def __init__(self, runner: Callable | None = None, hardware: Callable[[], dict] | None = None):
        self.runner = runner or run_streaming
        self._probe = hardware or probe_hardware
        self._hw: dict | None = None
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.reset()

    def reset(self) -> None:
        self.step_index, self.failed_step = -1, None
        self.progress_text, self.error = "", None
        self.log: collections.deque[str] = collections.deque(maxlen=60)
        self.device, self.stt_model = "cpu", "base.en"

    def hw(self) -> dict:
        if self._hw is None:
            self._hw = self._probe()
        return self._hw

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _on_line(self, line: str) -> None:
        line = line.strip()[:300]
        if line:
            self.log.append(line)
            self.progress_text = line

    # -- status
    def status(self) -> dict:
        h, hw = home(), self.hw()
        st = read_state(h)
        auto = choose_device(hw)
        device = self.device if self.step_index >= 0 else (st.get("device") or auto)
        stt = self.stt_model if self.step_index >= 0 else (st.get("stt_model") or default_stt(device))
        steps = plan(device, stt)
        running = self.running()
        if running:
            state, error = "installing", None
        elif is_ready(h):
            state, error = "ready", None
        elif st.get("state") == "failed":
            state, error = "failed", st.get("error") or self.error or "the last install failed"
        elif st.get("state") == "installing":
            state, error = "failed", "the install was interrupted (the app was closed or crashed); click Install to retry"
        else:
            state, error = "not_installed", None
        for i, s in enumerate(steps):
            if state == "ready" or (state in ("installing", "failed") and i < self.step_index):
                s["status"] = "done"
            elif state == "installing" and i == self.step_index:
                s["status"] = "running"
            elif state == "failed" and i == (self.failed_step if self.failed_step is not None else -1):
                s["status"] = "failed"
            else:
                s["status"] = "pending"
        total = sum(s["size_mb"] for s in steps) or 1
        done = sum(s["size_mb"] for s in steps if s["status"] == "done")
        percent = 100 if state == "ready" else round(100 * done / total)
        return dict(state=state, ready=state == "ready", error=error, steps=steps, percent=percent, device=device, stt_model=stt,
                    auto_device=auto, progress_text=self.progress_text if state == "installing" else "",
                    log_tail=list(self.log)[-12:] if state in ("installing", "failed") else [],
                    hardware=hw, versions=st.get("versions") if state == "ready" else None, home=str(h), free_gb=free_disk_gb(h),
                    download_mb=sum(s["size_mb"] for s in steps), licences=LICENSES, voices=VOICES, stt_models=STT_MODELS,
                    expect=expectation(device, stt))

    # -- start / run
    def start(self, accept_licenses: bool, device: str = "auto", stt_model: str | None = None) -> dict:
        if not accept_licenses:
            raise ValueError("Please tick that you accept the licences listed in the panel to install the voice engines.")
        if device not in ("auto", "cpu"):
            raise ValueError("device must be 'auto' or 'cpu'")
        with self.lock:
            if self.running():
                raise SetupConflict("an install is already running")
            if is_ready(home()):
                raise SetupConflict("the local voice engines are already installed")
            dev = choose_device(self.hw(), device)
            stt = stt_model or default_stt(dev)
            if stt not in STT_MODELS:
                raise ValueError(f"unknown speech model '{stt}' (choose {', '.join(STT_MODELS)})")
            steps = plan(dev, stt)
            need = round(sum(s["size_mb"] for s in steps) * 2 / 1000, 1)
            free = free_disk_gb(home())
            if free is not None and free < need:
                raise ValueError(f"not enough free disk space: need about {need} GB, {free} GB free")
            self.reset()
            self.device, self.stt_model, self.step_index = dev, stt, 0
            self.thread = threading.Thread(target=self._run, args=(dev, stt), name="voice-install", daemon=True)
            self.thread.start()
        return self.status()

    def commands(self, device: str, stt: str, h: Path) -> list[tuple[str, list[str], dict]]:
        """(step id, command, extra environment) in order; one step may run several commands."""
        py = str(env_python(h))
        pip = [py, "-m", "pip", "install", "--no-input", "--only-binary=:all:", "--uploaded-prior-to", PIP_CUTOFF]
        cmds: list[tuple[str, list[str], dict]] = [
            ("venv", [sys.executable, "-m", "venv", "--clear", str(h / "venv")], {}),
            ("pip", [py, "-m", "pip", "install", "--no-input", f"pip=={PIP_VERSION}"], {}),
            ("packages", [*pip, *PACKAGES], {})]
        if device == "cuda":      # kokoro-onnx pulled in the CPU onnxruntime; both packages own the same module, so remove it before the GPU build goes in
            cmds += [("cuda", [py, "-m", "pip", "uninstall", "--yes", "onnxruntime"], {}), ("cuda", [*pip, ORT_GPU, *CUDA_PACKAGES], {})]
        run_env = {"PYTHONPATH": str(BACKEND_DIR), "VOICE_STT_MODEL": stt, "VOICE_DEVICE": device}
        cmds += [("models", [py, "-m", "app.voice_fetch"], run_env), ("verify", [py, "-m", "app.voice_worker", "--check"], run_env)]
        return cmds

    def _run(self, device: str, stt: str) -> None:
        h = home()
        steps = plan(device, stt)
        ids = [st["id"] for st in steps]
        try:
            h.mkdir(parents=True, exist_ok=True)
            write_state(dict(state="installing"), h)
            versions: dict = {}
            for sid, cmd, extra in self.commands(device, stt, h):
                i = ids.index(sid)
                if i != self.step_index:
                    self.log.append(f"== {steps[i]['label']}")
                self.step_index = i
                self.progress_text = steps[i]["label"] + "..."
                captured: list[str] = []
                rc = self.runner(cmd, _child_env(h, extra), lambda ln: (captured.append(ln), self._on_line(ln)))
                if rc != 0:
                    self.failed_step = i
                    tail = " | ".join(c for c in captured[-3:] if c)
                    raise InstallError(f"Step '{steps[i]['label']}' failed (exit code {rc}). {tail}"[:600])
                if sid == "verify":
                    versions = self._parse_versions(captured)
            self.step_index = len(steps)
            write_state(dict(state="ready", pins=_pins(), device=device, stt_model=stt, versions=versions, installed_at=time.time()), h)
        except Exception as e:  # noqa: BLE001 - recorded and shown; the user can retry
            self.error = str(e)[:600]
            if self.failed_step is None:
                self.failed_step = max(self.step_index, 0)
            try:
                write_state(dict(state="failed", error=self.error), h)
            except OSError:
                pass

    @staticmethod
    def _parse_versions(lines: list[str]) -> dict:
        for ln in reversed(lines):
            ln = ln.replace("\x00", "")                  # onnxruntime writes UTF-16 text to the console, which leaves NULs around our line
            try:
                d = json.loads(ln[ln.index("{"):])
            except ValueError:                           # no "{" (index) or not JSON
                continue
            if isinstance(d, dict):
                return d
        raise InstallError("the environment check printed nothing readable - the engines may not import")

    def uninstall(self) -> dict:
        with self.lock:
            if self.running():
                raise SetupConflict("an install is running; wait for it to finish")
            h = home()
            ours = h.is_dir() and any((h / n).exists() for n in ("state.json", "venv", "models", "hf"))
            if ours:
                def _writable(func, path, _exc):      # Windows: read-only files inside caches
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                shutil.rmtree(h, onerror=_writable)
            self.reset()
        return dict(removed=ours, path=str(h))


def expectation(device: str, stt: str) -> str:
    if device == "cuda":
        return (f"GPU speech recognition ({stt}) measured about 0.1 s per sentence on an RTX 5080; the voice runs on the CPU "
                "(about 0.4 s for a three-second sentence, less for the short first phrase). Numbers differ on other machines.")
    return (f"CPU speech recognition ({stt}): about 0.4 s for a four-second sentence on a modern CPU (measured on a desktop CPU); the voice also runs "
            "on the CPU. An NVIDIA GPU makes recognition several times faster.")

