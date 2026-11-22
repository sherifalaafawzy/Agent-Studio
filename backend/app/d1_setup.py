"""Install / status / uninstall of the local d1 decision model (`/api/d1/*`).

Nothing here runs unless the user clicks Install. The install builds an ISOLATED Python environment under
`<data>/envs/d1/` (torch + transformers never touch the backend venv), then downloads the pinned model revision into that
environment's own Hugging Face cache. The MCP catalog entry `d1` is "not ready" until this finishes (`d1_spec.is_ready`).

States: not_installed -> installing -> ready | failed.  The work runs in one background thread; `Installer.status()` is the
single source for the UI.  The command runner is injectable, so tests exercise the whole flow without any real download.
"""
from __future__ import annotations

import collections
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from . import d1_spec

BACKEND_DIR = Path(__file__).resolve().parents[1]
VERIFY_CODE = (
    "import json, torch, transformers;"
    "print(json.dumps({'torch': torch.__version__, 'transformers': transformers.__version__,"
    "'cuda': bool(torch.cuda.is_available()),"
    "'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}))"
)


class D1InstallIn(BaseModel):
    accept_license: bool = False       # the user must acknowledge the LFM Open License terms shown in the panel
    device: str = "auto"               # "auto" (CUDA build when an NVIDIA GPU is usable) or "cpu" (smaller download)
    model: str = d1_spec.DEFAULT_MODEL  # which decision model: "d1-2b" or "d1-3b"


class SetupConflict(Exception):
    """An install is already running / done, or the environment is busy."""


class InstallError(Exception):
    """A step failed; the message is shown to the user."""


# ---------------------------------------------------------------- hardware

def probe_hardware() -> dict:
    """What GPU is available, via nvidia-smi (no torch needed)."""
    none = dict(nvidia=False, gpu=None, vram_mb=None, driver=None, cuda=None)
    exe = shutil.which("nvidia-smi")
    if not exe:
        return none
    try:
        plain = subprocess.run([exe], capture_output=True, text=True, timeout=15).stdout
        q = subprocess.run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=15).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return none
    m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", plain)
    parts = [p.strip() for p in q[0].split(",")] if q else []
    if len(parts) < 3 or not m:
        return none
    return dict(nvidia=True, gpu=parts[0], vram_mb=int(parts[1]) if parts[1].isdigit() else None, driver=parts[2],
                cuda=[int(m.group(1)), int(m.group(2))])


def expectation(hw: dict, choice: dict, model: str | None = None) -> str:
    """Plain-language performance expectation for this machine."""
    text = _expectation(hw, choice)
    if d1_spec.spec(model).id == "d1-3b":     # the figures above are d1-2B's; the 3B has 1.6x the weights
        text = text.replace("about 4 GB", "about 7 GB").replace("about 8 GB", "about 13 GB")
        text += " d1-3B is larger than d1-2B, so expect proportionally slower calls (the timings above are for d1-2B)."
    return text


def _expectation(hw: dict, choice: dict) -> str:
    if choice["variant"] == "cuda":
        return (f"{hw.get('gpu')} ({round((hw.get('vram_mb') or 0) / 1024)} GB) with the CUDA build: the model needs about 4 GB "
                "of VRAM in bf16. Expect roughly 5-30 ms per call once warm (Liquid AI measured 5.7 ms on an H100; not measured "
                "on this machine) and 10-60 s for the first call, which loads the weights.")
    if choice["variant"] == "default":
        return ("Apple GPU (MPS) in bf16: Liquid AI measured 18.6 ms per call on an M5 Pro; the first call loads the weights.")
    why = "no usable NVIDIA GPU was found" if not hw.get("nvidia") else "CPU was chosen or the NVIDIA driver is too old for CUDA 13.2"
    return (f"CPU in float32 ({why}): needs about 8 GB of RAM. Liquid AI publishes no CPU figures; expect roughly a second or "
            "more per call (unmeasured). Fine for routing/triage, slow for bulk work.")


def free_disk_gb(path: Path) -> float | None:
    """Free space (GB) on the drive that holds `path` (which may not exist yet); None when it cannot be read."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return round(shutil.disk_usage(probe).free / 1e9, 1)
    except OSError:
        return None


# ---------------------------------------------------------------- running commands

def run_streaming(cmd: list[str], env: dict, on_line: Callable[[str], None]) -> int:
    """Run a command, feeding each output line to on_line; returns the exit code (127 when it cannot start)."""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        on_line(f"cannot start {Path(cmd[0]).name}: {e}")
        return 127
    assert p.stdout is not None
    for line in p.stdout:
        on_line(line.rstrip())
    return p.wait()


def _child_env(h: Path, extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")}
    env.update(PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1", PYTHONUTF8="1", D1_HOME=str(h), HF_HOME=str(d1_spec.hf_home(h)))
    env.update(extra or {})
    return env


# ---------------------------------------------------------------- the installer

class Installer:
    def __init__(self, runner: Callable | None = None, hardware: Callable[[], dict] | None = None, platform: str | None = None):
        self.runner = runner or run_streaming
        self._probe = hardware or probe_hardware
        self.platform = platform or sys.platform
        self._hw: dict | None = None
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.reset()

    def reset(self) -> None:
        self.step_index = -1
        self.step_fraction = 0.0
        self.failed_step: int | None = None
        self.progress_text = ""
        self.error: str | None = None
        self.log: collections.deque[str] = collections.deque(maxlen=60)
        self.choice: dict | None = None
        self.model: str | None = None        # which model the current / last install was for
        self.shared = False                  # ... and whether it reused an environment that already existed

    # -- helpers
    def hw(self) -> dict:
        if self._hw is None:
            self._hw = self._probe()
        return self._hw

    def _choice(self, device: str = "auto") -> dict:
        hw = self.hw()
        cuda = tuple(hw["cuda"]) if hw.get("nvidia") and hw.get("cuda") and device == "auto" else None
        return d1_spec.choose_torch(cuda, self.platform)

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _on_line(self, line: str) -> None:
        line = line.strip()[:300]
        if not line:
            return
        m = re.match(r"PROGRESS (\d+) (\d+)$", line)
        if m:
            done, total = int(m.group(1)), max(int(m.group(2)), 1)
            self.step_fraction = min(done / total, 1.0)
            self.progress_text = f"Downloading model weights: {done / 1e9:.2f} of {total / 1e9:.2f} GB"
            return
        self.log.append(line)
        self.progress_text = line

    # -- status
    def status(self, model: str | None = None) -> dict:
        sp = d1_spec.spec(model)
        h = d1_spec.home()
        hw = self.hw()
        persisted = d1_spec.read_state(h, sp.id)
        mine = self.model == sp.id                      # this installer's current / last install is for this model
        choice = (self.choice if mine else None) or self._choice()
        shared = self.shared if mine and self.choice else d1_spec.env_usable(h, sp.id)
        steps = d1_spec.plan(choice, sp.id, shared)
        step_index, failed_step = (self.step_index, self.failed_step) if mine else (-1, None)
        running = self.running() and mine
        if running:
            state, error = "installing", None
        elif d1_spec.is_ready(h, sp.id):
            state, error = "ready", None
        elif persisted.get("state") == "failed":
            state, error = "failed", persisted.get("error") or (self.error if mine else None) or "the last install failed"
        elif persisted.get("state") == "installing":
            state, error = "failed", "the install was interrupted (the app was closed or crashed); click Install to retry"
        else:
            state, error = "not_installed", None
        for i, s in enumerate(steps):
            if state == "ready" or (state in ("installing", "failed") and i < step_index):
                s["status"] = "done"
            elif state == "installing" and i == step_index:
                s["status"] = "running"
            elif state == "failed" and i == (failed_step if failed_step is not None else -1):
                s["status"] = "failed"
            else:
                s["status"] = "pending"
        total = sum(s["size_mb"] for s in steps) or 1
        done = sum(s["size_mb"] for s in steps if s["status"] == "done")
        if state == "installing" and 0 <= step_index < len(steps):
            done += steps[step_index]["size_mb"] * self.step_fraction
        percent = 100 if state == "ready" else round(100 * done / total)
        return dict(
            state=state, ready=state == "ready", error=error, mcp_id=d1_spec.MCP_ID, model=sp.id, label=sp.label, note=sp.note,
            question_types=list(sp.types), shared_env=shared, busy_with=self.model if self.running() else None,
            step=steps[step_index]["id"] if state == "installing" and 0 <= step_index < len(steps) else None,
            steps=steps, percent=percent, progress_text=self.progress_text if state == "installing" else "",
            log_tail=list(self.log)[-12:] if state in ("installing", "failed") and mine else [],
            repo=sp.repo, revision=sp.revision, home=str(h), versions=persisted.get("versions") if state == "ready" else None,
            weights_gb=round(sp.weights_bytes / 1e9, 2),
            download_mb=sum(s["size_mb"] for s in steps), disk_gb_needed=self.disk_gb(choice, sp.id, shared), free_gb=free_disk_gb(h),
            hardware={**hw, "torch_build": choice["label"], "variant": choice["variant"]}, expect=expectation(hw, choice, sp.id),
            license=dict(name=d1_spec.LICENSE_NAME, url=sp.license_url, summary=d1_spec.LICENSE_SUMMARY),
            review_notes=sp.review_notes)

    @staticmethod
    def disk_gb(choice: dict, model: str | None = None, shared: bool = False) -> float:
        # unpacked size is larger than the download: torch's CUDA libraries roughly double
        extra = 0 if shared else (3000 if choice["variant"] == "cuda" else 300)
        return round((sum(s["size_mb"] for s in d1_spec.plan(choice, model, shared)) + extra) / 1000, 1)

    def progress(self, model: str | None = None) -> dict:
        s = self.status(model)
        return {k: s[k] for k in ("state", "ready", "error", "step", "steps", "percent", "progress_text", "log_tail")}

    # -- start / run
    def start(self, accept_license: bool, device: str = "auto", model: str | None = None) -> dict:
        sp = d1_spec.spec(model)
        if not accept_license:
            raise ValueError("You must accept the LFM Open License v1.0 terms (summary shown in the panel) to install the model.")
        if device not in ("auto", "cpu"):
            raise ValueError("device must be 'auto' or 'cpu'")
        with self.lock:
            if self.running():
                raise SetupConflict("an install is already running")
            if d1_spec.is_ready(None, sp.id):
                raise SetupConflict(f"the decision model {sp.label} is already installed")
            choice = self._choice(device)
            shared = d1_spec.env_usable(None, sp.id)
            need = self.disk_gb(choice, sp.id, shared)
            free = free_disk_gb(d1_spec.home())
            if free is not None and free < need:
                raise ValueError(f"not enough free disk space: need about {need} GB, {free} GB free")
            self.reset()
            self.choice, self.model, self.shared = choice, sp.id, shared
            self.step_index = 0
            self.thread = threading.Thread(target=self._run, args=(choice, sp.id, shared), name="d1-install", daemon=True)
            self.thread.start()
        return self.status(sp.id)

    def _commands(self, choice: dict, h: Path, model: str | None = None, shared: bool = False) -> list[tuple[list[str], dict]]:
        py = str(d1_spec.env_python(h))
        pip = [py, "-m", "pip", "install", "--no-input", "--only-binary=:all:"]
        torch_cmd = [*pip, *(["--index-url", choice["index"]] if choice["index"] else []), *choice["packages"]]
        full = [
            ([sys.executable, "-m", "venv", "--clear", str(h / "venv")], {}),
            ([py, "-m", "pip", "install", "--no-input", f"pip=={d1_spec.PIP_VERSION}"], {}),
            (torch_cmd, {}),
            ([*pip, "--uploaded-prior-to", d1_spec.PIP_CUTOFF, *d1_spec.LIB_PINS], {}),
            ([py, "-m", "app.d1_fetch"], {"PYTHONPATH": str(BACKEND_DIR), "D1_MODEL": d1_spec.spec(model).id}),
            ([py, "-c", VERIFY_CODE], {}),
        ]
        return full[4:] if shared else full           # a shared environment already has torch and the libraries

    def _run(self, choice: dict, model: str | None = None, shared: bool = False) -> None:
        h = d1_spec.home()
        sp = d1_spec.spec(model)
        steps = d1_spec.plan(choice, sp.id, shared)
        try:
            h.mkdir(parents=True, exist_ok=True)
            d1_spec.write_state(dict(state="installing", revision=sp.revision), h, sp.id)
            versions: dict = {}
            for i, (cmd, extra) in enumerate(self._commands(choice, h, sp.id, shared)):
                self.step_index, self.step_fraction = i, 0.0
                self.progress_text = steps[i]["label"] + "..."
                self.log.append(f"== {steps[i]['label']}")
                captured: list[str] = []
                rc = self.runner(cmd, _child_env(h, extra), lambda ln: (captured.append(ln), self._on_line(ln)))
                if rc != 0:
                    self.failed_step = i
                    tail = " | ".join(c for c in captured[-3:] if c)
                    raise InstallError(f"Step '{steps[i]['label']}' failed (exit code {rc}). {tail}"[:600])
                if steps[i]["id"] == "verify":
                    versions = self._parse_versions(captured)
            self.step_index = len(steps)
            d1_spec.write_state(dict(state="ready", revision=sp.revision, torch=choice["variant"], versions=versions,
                                     installed_at=time.time()), h, sp.id)
        except Exception as e:  # noqa: BLE001 - recorded and shown; the user can retry
            self.error = str(e)[:600]
            if self.failed_step is None:
                self.failed_step = max(self.step_index, 0)
            try:
                d1_spec.write_state(dict(state="failed", revision=sp.revision, error=self.error), h, sp.id)
            except OSError:
                pass

    @staticmethod
    def _parse_versions(lines: list[str]) -> dict:
        for ln in reversed(lines):
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if isinstance(d, dict):
                return d
        raise InstallError("the environment check printed nothing readable - torch/transformers may not import")

    # -- uninstall
    def uninstall(self, model: str | None = None) -> dict:
        sp = d1_spec.spec(model)
        with self.lock:
            if self.running():
                raise SetupConflict("an install is running; wait for it to finish")
            h = d1_spec.home()

            def _writable(func, path, _exc):      # Windows: read-only files inside caches
                os.chmod(path, stat.S_IWRITE)
                func(path)
            if any(d1_spec.state_file(h, m).exists() for m in d1_spec.MODELS if m != sp.id):
                # another model still uses this environment: remove only this model's weights and install record
                cache, st = d1_spec.repo_cache(h, sp.id), d1_spec.state_file(h, sp.id)
                ours = cache.exists() or st.exists()
                if cache.exists():
                    shutil.rmtree(cache, onerror=_writable)
                st.unlink(missing_ok=True)
            else:
                ours = h.is_dir() and any((h / n).exists() for n in ("state.json", "venv", "hf", d1_spec.state_file(h, sp.id).name))
                if ours:
                    shutil.rmtree(h, onerror=_writable)
            self.reset()
        return dict(removed=ours, path=str(h))


# ---------------------------------------------------------------- HTTP

def register(app: FastAPI, installer: Installer | None = None) -> Installer:
    """Mount /api/d1/* on the app. One Installer per app (kept on app.state.d1)."""
    inst = installer or Installer()
    app.state.d1 = inst
    router = APIRouter(prefix="/api/d1", tags=["d1"])

    def _spec(model: str):
        try:
            return d1_spec.spec(model)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.get("/status")
    def d1_status(model: str = d1_spec.DEFAULT_MODEL):
        return inst.status(_spec(model).id)

    @router.get("/models")
    def d1_models():
        """Every decision model with its own install state (the Auto settings and the MCP panel draw from this)."""
        return dict(default=d1_spec.DEFAULT_MODEL, installing=inst.model if inst.running() else None,
                    models={m: inst.status(m) for m in d1_spec.MODELS})

    @router.get("/install/progress")
    def d1_progress(model: str = d1_spec.DEFAULT_MODEL):
        return inst.progress(_spec(model).id)

    @router.post("/install")
    def d1_install(body: D1InstallIn):
        try:
            return inst.start(body.accept_license, body.device, body.model)
        except SetupConflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.post("/uninstall")
    def d1_uninstall(model: str = d1_spec.DEFAULT_MODEL):
        try:
            return inst.uninstall(_spec(model).id)
        except SetupConflict as e:
            raise HTTPException(409, str(e))

    app.include_router(router)
    return inst
