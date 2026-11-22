"""Install / status / uninstall of the local time-series forecasting models (`/api/ts/*`), plus a one-shot forecast for the UI.

Nothing here runs unless the user clicks Install. The first install builds ONE ISOLATED Python environment under
`<data>/envs/timeseries/` (torch and the model libraries never touch the backend venv). Each model then adds its own pinned
package(s), its pinned weights (Hugging Face cache inside that folder) and, for the two libraries that are not on PyPI, its
pinned GitHub source. All models share the environment: `pip` refuses an incompatible set, in which case that install fails
with pip's message and the models already installed are untouched.

The 14-day rule: pip runs with `--uploaded-prior-to <today - 14 days>`, direct packages are pinned to exact versions, and a
model whose newest package or source commit is younger than 14 days is refused until it is old enough.

The command runner is injectable, so tests exercise the whole flow without any real download.
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
from collections.abc import Callable
from datetime import date
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from . import ts_spec
from .d1_setup import free_disk_gb, probe_hardware, run_streaming

BACKEND_DIR = Path(__file__).resolve().parents[1]
FORECAST_TIMEOUT = 900                  # a cold start loads multi-GB weights; a try-it forecast may take minutes on CPU


class TsInstallIn(BaseModel):
    models: list[str]
    accepted: list[str] = []            # model ids whose licence the user ticked
    device: str = "auto"                # "auto" (CUDA build when an NVIDIA GPU is usable) or "cpu" (smaller download)


class TsForecastIn(BaseModel):
    series: list[float]
    horizon: int
    models: list[str] | str = "all"
    quantiles: list[float] | None = None
    backtest: bool = False
    season: int = 1


class SetupConflict(Exception):
    """An install is already running, or the thing asked for is already done."""


class InstallError(Exception):
    """A step failed; the message is shown to the user."""


def _child_env(h: Path, extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")}
    env.update(PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1", PYTHONUTF8="1", TS_HOME=str(h), HF_HOME=str(ts_spec.hf_home(h)))
    env.update(extra or {})
    return env


def expectation(hw: dict, choice: dict) -> str:
    """Plain-language performance expectation for this machine. Nothing here was measured on it."""
    if choice["variant"] == "cuda":
        return (f"{hw.get('gpu')} with the CUDA build: every model fits easily and runs in well under a second per forecast once loaded. "
                "Not measured on this machine; the first call of each model also loads its weights (seconds).")
    if choice["variant"] == "default":
        return "Apple GPU (MPS) is not used by these adapters: models run on the CPU. Not measured on this machine."
    why = "no usable NVIDIA GPU was found" if not hw.get("nvidia") else "CPU was chosen or the NVIDIA driver is too old for CUDA 13.2"
    return (f"CPU ({why}). The small models (Toto 4M, Timestral 5M/28M) forecast in about a second or less; the 120M to 330M models "
            "take a few seconds per series and need 1-4 GB of RAM while loaded. Not measured on this machine.")


class Installer:
    def __init__(self, runner: Callable | None = None, hardware: Callable[[], dict] | None = None, platform: str | None = None,
                 today: Callable[[], date] | None = None, python: tuple[int, int] | None = None):
        self.runner = runner or run_streaming
        self._probe = hardware or probe_hardware
        self.platform = platform or sys.platform
        self.today = today or date.today
        self.python = python or sys.version_info[:2]
        self._hw: dict | None = None
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.reset()

    def reset(self) -> None:
        self.steps: list[dict] = []
        self.step_index = -1
        self.step_fraction = 0.0
        self.progress_text = ""
        self.error: str | None = None
        self.queue: list[str] = []
        self.log: collections.deque[str] = collections.deque(maxlen=60)

    # -- helpers
    def hw(self) -> dict:
        if self._hw is None:
            self._hw = self._probe()
        return self._hw

    def choice(self, device: str = "auto") -> dict:
        st = ts_spec.read_state().get("env") or {}
        if ts_spec.env_ready() and st.get("torch"):                       # the environment already has its torch
            return {**ts_spec.TORCH_CPU, "variant": st["torch"], "label": st.get("torch_label") or "PyTorch (installed)", "size_mb": 0}
        hw = self.hw()
        cuda = tuple(hw["cuda"]) if hw.get("nvidia") and hw.get("cuda") and device == "auto" else None
        return ts_spec.choose_torch(cuda, self.platform)

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def blocked(self, m: ts_spec.Model) -> str:
        return ts_spec.blocked_reason(m, self.today(), self.python)

    def _on_line(self, line: str) -> None:
        line = line.strip()[:300]
        if not line:
            return
        m = re.match(r"PROGRESS (\d+) (\d+)$", line)
        if m:
            done, total = int(m.group(1)), max(int(m.group(2)), 1)
            self.step_fraction = min(done / total, 1.0)
            self.progress_text = f"Downloading: {done / 1e6:.0f} of {total / 1e6:.0f} MB"
            return
        self.log.append(line)
        self.progress_text = line

    # -- the plan
    def plan(self, ids: list[str], device: str = "auto") -> list[dict]:
        """The ordered install steps for these models (the shared environment first, only if it is not there yet)."""
        choice = self.choice(device)
        steps: list[dict] = []
        if not ts_spec.env_ready():
            steps += [dict(id="venv", label="Create the isolated Python environment", size_mb=ts_spec.VENV_MB, model=None),
                      dict(id="pip", label=f"Update pip to {ts_spec.PIP_VERSION}", size_mb=3, model=None),
                      dict(id="torch", label=f"Install {choice['label']}", size_mb=choice["size_mb"], model=None),
                      dict(id="libs", label="Install numpy, pandas, safetensors, huggingface_hub, einops, tqdm, mcp", size_mb=ts_spec.BASE_LIBS_MB, model=None)]
        for mid in ids:
            m = ts_spec.model(mid)
            if m.pip:
                steps.append(dict(id=f"{mid}:pkgs", label=f"{m.label}: install {', '.join(p.split('==')[0] for p in m.pip)}", size_mb=m.pip_mb, model=mid))
            steps.append(dict(id=f"{mid}:weights", label=f"{m.label}: download {'weights and source' if m.source else 'weights'} ({m.repo} @ {m.revision[:8]})",
                              size_mb=round(m.download_bytes / 1e6), model=mid))
            steps.append(dict(id=f"{mid}:verify", label=f"{m.label}: check that it imports", size_mb=0, model=mid))
        return steps

    @staticmethod
    def disk_gb(steps: list[dict], choice: dict) -> float:
        extra = 3000 if choice["variant"] == "cuda" and any(s["id"] == "torch" for s in steps) else 300 if steps else 0   # unpacked > downloaded
        return round((sum(s["size_mb"] for s in steps) + extra) / 1000, 1)

    def describe_plan(self, ids: list[str], device: str = "auto") -> dict:
        self._validate_ids(ids)
        steps = self.plan([i for i in ids if not ts_spec.is_ready(i)], device)
        choice = self.choice(device)
        h = ts_spec.home()
        return dict(models=ids, steps=[{**s, "status": "pending"} for s in steps], download_mb=sum(s["size_mb"] for s in steps),
                    disk_gb_needed=self.disk_gb(steps, choice), free_gb=free_disk_gb(h), torch_build=choice["label"], variant=choice["variant"],
                    env_needed=not ts_spec.env_ready(), expect=expectation(self.hw(), choice))

    @staticmethod
    def _validate_ids(ids: list[str]) -> None:
        if not ids:
            raise ValueError("choose at least one model")
        bad = [i for i in ids if i not in ts_spec.MODELS]
        if bad:
            raise ValueError(f"unknown model(s): {', '.join(bad)}")

    # -- status
    def _model_state(self, m: ts_spec.Model, persisted: dict, running: bool) -> tuple[str, str | None]:
        if running and m.id in self.queue:
            cur = self.steps[self.step_index]["model"] if 0 <= self.step_index < len(self.steps) else None
            done = self._model_done(m.id)
            return ("ready" if done else "installing" if cur == m.id else "queued"), None
        if ts_spec.is_ready(m.id):
            return "ready", None
        rec = (persisted.get("models") or {}).get(m.id) or {}
        if rec.get("state") == "failed":
            return "failed", rec.get("error") or "the last install failed"
        if rec.get("state") == "installing":
            return "failed", "the install was interrupted (the app was closed or crashed); click Install to retry"
        why = self.blocked(m)
        return ("blocked", why) if why else ("not_installed", None)

    def _model_done(self, mid: str) -> bool:
        return any(s["id"] == f"{mid}:verify" and s["status"] == "done" for s in self.steps)

    def status(self) -> dict:
        h = ts_spec.home()
        hw = self.hw()
        persisted = ts_spec.read_state(h)
        running = self.running()
        choice = self.choice()
        steps = [dict(s) for s in self.steps]
        total = sum(s["size_mb"] for s in steps) or 1
        done = sum(s["size_mb"] for s in steps if s["status"] == "done")
        if running and 0 <= self.step_index < len(steps):
            done += steps[self.step_index]["size_mb"] * self.step_fraction
        models = []
        for m in ts_spec.MODELS.values():
            state, err = self._model_state(m, persisted, running)
            rec = (persisted.get("models") or {}).get(m.id) or {}
            models.append({**ts_spec.public(m, self.today(), self.python), "state": state, "error": err if state in ("failed", "blocked") else None,
                           "versions": rec.get("versions") if state == "ready" else None,
                           "usable_commercially": m.commercial})
        install_state = "installing" if running else "failed" if self.error else "idle"
        return dict(
            env=dict(ready=ts_spec.env_ready(h), home=str(h), torch=(persisted.get("env") or {}).get("torch"), python=(persisted.get("env") or {}).get("python"),
                     free_gb=free_disk_gb(h)),
            hardware={**hw, "torch_build": choice["label"], "variant": choice["variant"]}, expect=expectation(hw, choice),
            install=dict(state=install_state, step=steps[self.step_index]["id"] if running and 0 <= self.step_index < len(steps) else None, steps=steps,
                         percent=round(100 * done / total) if steps else 0, progress_text=self.progress_text if running else "",
                         log_tail=list(self.log)[-12:] if running or self.error else [], error=self.error, queue=list(self.queue)),
            models=models)

    def progress(self) -> dict:
        s = self.status()
        return {**s["install"], "models": {m["id"]: m["state"] for m in s["models"]}}

    # -- start / run
    def start(self, ids: list[str], accepted: list[str], device: str = "auto") -> dict:
        ids = list(dict.fromkeys(ids))
        self._validate_ids(ids)
        if device not in ("auto", "cpu"):
            raise ValueError("device must be 'auto' or 'cpu'")
        missing = [i for i in ids if i not in accepted]
        if missing:
            raise ValueError("You must accept the licence of every model you install; not accepted: " + ", ".join(ts_spec.MODELS[i].label for i in missing))
        for i in ids:
            why = self.blocked(ts_spec.MODELS[i])
            if why:
                raise ValueError(f"{ts_spec.MODELS[i].label} cannot be installed yet: {why}")
        with self.lock:
            if self.running():
                raise SetupConflict("an install is already running")
            todo = [i for i in ids if not ts_spec.is_ready(i)]
            if not todo:
                raise SetupConflict("those models are already installed")
            choice = self.choice(device)
            steps = self.plan(todo, device)
            need, free = self.disk_gb(steps, choice), free_disk_gb(ts_spec.home())
            if free is not None and free < need:
                raise ValueError(f"not enough free disk space: need about {need} GB, {free} GB free")
            self.reset()
            self.queue = todo
            self.steps = [{**s, "status": "pending"} for s in steps]
            self.step_index = 0
            self.thread = threading.Thread(target=self._run, args=(choice, todo), name="ts-install", daemon=True)
            self.thread.start()
        return self.status()

    def _command(self, step: dict, choice: dict, h: Path, cutoff: str) -> tuple[list[str], dict]:
        py = str(ts_spec.env_python(h))
        pip = [py, "-m", "pip", "install", "--no-input", "--only-binary=:all:"]
        sid = step["id"]
        if sid == "venv":
            return [sys.executable, "-m", "venv", "--clear", str(h / "venv")], {}
        if sid == "pip":
            return [py, "-m", "pip", "install", "--no-input", f"pip=={ts_spec.PIP_VERSION}"], {}
        if sid == "torch":
            return [*pip, *(["--index-url", choice["index"]] if choice["index"] else []), *choice["packages"]], {}
        if sid == "libs":
            return [*pip, "--uploaded-prior-to", cutoff, *ts_spec.BASE_PINS], {}
        mid, kind = sid.split(":")
        m = ts_spec.MODELS[mid]
        if kind == "pkgs":
            return [*pip, "--uploaded-prior-to", cutoff, *m.pip], {}
        if kind == "weights":
            return [py, "-m", "app.ts_fetch", mid], {"PYTHONPATH": str(BACKEND_DIR)}
        return [py, "-m", "app.ts_runners", "verify", mid], {"PYTHONPATH": str(BACKEND_DIR)}

    def _record(self, h: Path, mid: str | None, **rec) -> None:
        st = ts_spec.read_state(h)
        if mid is None:
            st["env"] = {**(st.get("env") or {}), **rec}
        else:
            st.setdefault("models", {})[mid] = rec
        ts_spec.write_state(st, h)

    def _run(self, choice: dict, todo: list[str]) -> None:
        h = ts_spec.home()
        cutoff = ts_spec.cutoff(self.today())
        current: str | None = None
        try:
            h.mkdir(parents=True, exist_ok=True)
            for mid in todo:
                self._record(h, mid, state="installing", revision=ts_spec.MODELS[mid].revision)
            captured: list[str] = []
            for i, step in enumerate(self.steps):
                self.step_index, self.step_fraction = i, 0.0
                step["status"] = "running"
                current = step["model"]
                self.progress_text = step["label"] + "..."
                self.log.append(f"== {step['label']}")
                cmd, extra = self._command(step, choice, h, cutoff)
                captured = []
                rc = self.runner(cmd, _child_env(h, extra), lambda ln: (captured.append(ln), self._on_line(ln)))
                if rc != 0:
                    step["status"] = "failed"
                    tail = " | ".join(c for c in captured[-3:] if c)
                    raise InstallError(f"Step '{step['label']}' failed (exit code {rc}). {tail}"[:600])
                if step["id"] == "libs":
                    self._record(h, None, state="ready", torch=choice["variant"], torch_label=choice["label"],
                                 python=".".join(map(str, sys.version_info[:3])), installed_at=time.time())
                elif step["id"].endswith(":verify"):
                    self._record(h, step["model"], state="ready", revision=ts_spec.MODELS[step["model"]].revision,
                                 versions=self._parse_versions(captured), installed_at=time.time())      # raises if unreadable: not "done"
                step["status"] = "done"
            self.step_index = len(self.steps)
        except Exception as e:  # noqa: BLE001 - recorded and shown; the user can retry
            self.error = str(e)[:600]
            for step in self.steps:
                if step["status"] == "running":
                    step["status"] = "failed"
            for mid in (m for m in todo if not self._model_done(m)):
                msg = self.error if current is None or mid == current else "not started: an earlier install step failed"
                try:
                    self._record(h, mid, state="failed", revision=ts_spec.MODELS[mid].revision, error=msg)
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
        raise InstallError("the check printed nothing readable - the model library may not import")

    # -- uninstall
    def uninstall(self, model_id: str) -> dict:
        """Remove one model's weights, source and record. Removing the last model removes the whole environment."""
        m = ts_spec.model(model_id)
        with self.lock:
            if self.running():
                raise SetupConflict("an install is running; wait for it to finish")
            h = ts_spec.home()

            def writable(func, path, _exc):           # Windows: read-only files inside caches
                os.chmod(path, stat.S_IWRITE)
                func(path)

            def rm(path: Path) -> None:
                if path.exists():
                    shutil.rmtree(path, onerror=writable)

            st = ts_spec.read_state(h)
            had = m.id in (st.get("models") or {})
            left = [i for i in ts_spec.MODELS if i != m.id and ts_spec.is_ready(i, h)]
            removed_env = not left and h.is_dir()
            if removed_env:
                rm(h)
            else:
                for repo, _rev, _files in m.repos:
                    rm(ts_spec.hf_home(h) / "hub" / ts_spec.model_dir_name(repo))
                shared = any(ts_spec.MODELS[i].source and ts_spec.MODELS[i].source.dest == (m.source.dest if m.source else None) for i in left)
                if m.source and not shared:
                    rm(ts_spec.src_dir(h) / m.source.dest)
                if had:
                    st["models"].pop(m.id)
                    ts_spec.write_state(st, h)
            self.reset()
        return dict(removed=had or removed_env, model=model_id, removed_environment=removed_env, path=str(h))


# ---------------------------------------------------------------- one-shot forecast (for the UI's try-it box)

def run_cli(payload: dict, timeout: int = FORECAST_TIMEOUT) -> dict:
    """Run one forecast in the isolated environment (`python -m app.ts_cli`): JSON in on stdin, JSON out on stdout."""
    py = ts_spec.env_python()
    if not py.exists():
        return {"ok": False, "error": "no time-series model is installed yet: install one first"}
    try:
        p = subprocess.run([str(py), "-m", "app.ts_cli"], input=json.dumps(payload), capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                           errors="replace", env=_child_env(ts_spec.home(), {"PYTHONPATH": str(BACKEND_DIR)}),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"the forecast took longer than {timeout} seconds and was stopped"}
    except OSError as e:
        return {"ok": False, "error": f"cannot start the model environment: {e}"}
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-1:] or ["no output"]
        return {"ok": False, "error": f"the model environment failed (exit code {p.returncode}): {tail[0][:300]}"}
    return out if isinstance(out, dict) else {"ok": False, "error": "unreadable answer from the model environment"}


# ---------------------------------------------------------------- HTTP

def register(app: FastAPI, installer: Installer | None = None, forecaster: Callable[[dict], dict] | None = None) -> Installer:
    """Mount /api/ts/* on the app. One Installer per app (kept on app.state.ts)."""
    inst = installer or Installer()
    app.state.ts = inst
    router = APIRouter(prefix="/api/ts", tags=["timeseries"])

    @router.get("/status")
    def ts_status():
        return inst.status()

    @router.get("/plan")
    def ts_plan(models: str, device: str = "auto"):
        try:
            return inst.describe_plan([m for m in models.split(",") if m], device)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.get("/install/progress")
    def ts_progress():
        return inst.progress()

    @router.post("/install")
    def ts_install(body: TsInstallIn):
        try:
            return inst.start(body.models, body.accepted, body.device)
        except SetupConflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.post("/uninstall/{model_id}")
    def ts_uninstall(model_id: str):
        try:
            return inst.uninstall(model_id)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0]))
        except SetupConflict as e:
            raise HTTPException(409, str(e))

    @router.post("/forecast")
    def ts_forecast(body: TsForecastIn):
        """Try-it box: one forecast in the model environment (the models load fresh each time, so the first call is slow)."""
        run = forecaster or run_cli
        return run({"tool": "forecast", "args": body.model_dump()})

    app.include_router(router)
    return inst
