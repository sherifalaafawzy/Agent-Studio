"""Model trainer: build / train / evaluate models with Ludwig, PyTorch or Keras (TensorFlow) from an agent or the UI.

Each framework lives in its OWN isolated Python environment under `<data>/envs/trainer/<framework>/` (never in the backend
venv), installed only when the user clicks Install after seeing the size and licence. Training runs as a detached child
process (`python -m app.trainer_worker <job dir>`) that writes `status.json`, `log.txt`, `metrics.json` and a model artifact into
`<data>/trainer/jobs/<id>/`, so a job survives the MCP server or the app restarting and can be polled and cancelled.

The 14-day rule: direct packages are pinned to exact versions and pip runs with `--uploaded-prior-to <today - 14 days>`.
Spawning, killing, liveness and the command runner are injectable so the tests run the whole flow without installing anything.
"""
from __future__ import annotations

import csv
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from . import pip_install, ts_spec
from .d1_setup import free_disk_gb, run_streaming
from .db import data_dir

BACKEND_DIR = Path(__file__).resolve().parents[1]
TASKS = ("classification", "regression")
MAX_EPOCHS = 500
DEFAULT_MINUTES = 30
JOB_ID_RE = re.compile(r"^[a-z0-9]{10}$")

def _notes(f: dict) -> str:
    """The framework's note, plus a plain sentence when its install builds a small package from source."""
    return " ".join(x for x in (f["notes"], pip_install.source_note(f.get("source_builds", ()))) if x)


FRAMEWORKS: dict[str, dict] = {
    "ludwig": dict(
        label="Ludwig", module="ludwig", licence="Apache-2.0", size_mb=3500,
        blurb="Declarative deep learning: describe inputs and outputs in a small config, Ludwig builds and trains the model. Best for a quick, strong tabular/text baseline.",
        packages=["ludwig==0.17.10", "pyyaml==6.0.3"], source_builds=("gpustat",),
        notes="Installs PyTorch as a dependency, so it is the largest download. Needs Python 3.12 or newer."),
    "pytorch": dict(
        label="PyTorch", module="torch", licence="BSD-3-Clause (PyTorch)", size_mb=900,
        blurb="PyTorch multilayer network for CSV data (classification or regression) with early reporting each epoch.",
        packages=["torch==2.15.0", "numpy==2.5.4", "scikit-learn==1.9.1"],
        notes="Windows and macOS get the CPU build; Linux gets the default build (CUDA, several GB) unless you choose the CPU build."),
    "keras": dict(
        label="Keras / TensorFlow", module="tensorflow", licence="Apache-2.0", size_mb=1000,
        blurb="Keras multilayer network on TensorFlow for CSV data (classification or regression).",
        packages=["tensorflow==2.21.0", "numpy==2.5.4", "scikit-learn==1.9.1"],
        notes="CPU only on native Windows (TensorFlow dropped Windows GPU support); GPU works on Linux/WSL2."),
}

TEMPLATES = {
    "classification": dict(task="classification", target="<column to predict>", epochs=20, hidden=[64, 32], learning_rate=0.001, batch_size=32, test_size=0.2),
    "regression": dict(task="regression", target="<column to predict>", epochs=30, hidden=[64, 32], learning_rate=0.001, batch_size=32, test_size=0.2),
    "ludwig_config": dict(
        note="Optional `config` for framework=ludwig. Leave it out and Agent Studio builds one from the CSV columns.",
        config={"input_features": [{"name": "age", "type": "number"}, {"name": "plan", "type": "category"}],
                "output_features": [{"name": "churned", "type": "binary"}], "trainer": {"epochs": 10}}),
}


class TrainerError(Exception):
    """Something the user or the agent can fix; the message is shown as is."""


# ------------------------------------------------------------------------------------------------------------------ paths
def home() -> Path:
    return Path(os.environ.get("TRAINER_HOME") or (data_dir() / "envs" / "trainer"))


def jobs_dir() -> Path:
    return Path(os.environ.get("TRAINER_JOBS") or (data_dir() / "trainer" / "jobs"))


def env_python(fw: str, h: Path | None = None) -> Path:
    return (h or home()) / fw / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _read_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, p)


def read_state() -> dict:
    return _read_json(home() / "state.json")


def is_ready(fw: str) -> bool:
    return (read_state().get("frameworks") or {}).get(fw, {}).get("state") == "ready" and env_python(fw).exists()


def ready_frameworks() -> list[str]:
    return [f for f in FRAMEWORKS if is_ready(f)]


def _kill(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            pass


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _spawn(cmd: list[str], env: dict, log_path: Path) -> int:
    kw: dict = {}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    with open(log_path, "ab") as log:
        return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, **kw).pid


class Trainer:
    """Install state, jobs and their lifecycle. One instance per process; all durable state is on disk."""

    def __init__(self, runner: Callable | None = None, spawn: Callable | None = None, kill: Callable | None = None,
                 alive: Callable | None = None, today: Callable[[], date] | None = None, platform: str | None = None):
        self.runner = runner or run_streaming
        self._spawn = spawn or _spawn
        self._kill = kill or _kill
        self._alive = alive or _alive
        self.today = today or date.today
        self.platform = platform or sys.platform
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.installing: str | None = None
        self.log: list[str] = []
        self.error: str | None = None

    # ------------------------------------------------------------------------------------------------ install
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def plan(self, fw: str, device: str = "auto") -> dict:
        f = FRAMEWORKS.get(fw)
        if f is None:
            raise TrainerError(f"unknown framework '{fw}' (choose from {', '.join(FRAMEWORKS)})")
        h = home()
        return dict(framework=fw, label=f["label"], packages=f["packages"], download_mb=f["size_mb"], licence=f["licence"], notes=_notes(f),
                    disk_gb_needed=round(f["size_mb"] * 2 / 1000, 1), free_gb=free_disk_gb(h if h.exists() else data_dir()),
                    cpu_build=device == "cpu" and fw == "pytorch" and self.platform not in ("win32", "darwin"))

    def start_install(self, fw: str, accepted: bool, device: str = "auto") -> dict:
        p = self.plan(fw, device)
        if device not in ("auto", "cpu"):
            raise TrainerError("device must be 'auto' or 'cpu'")
        if not accepted:
            raise TrainerError(f"Accept the {p['label']} licence ({p['licence']}) and the download size (about {p['download_mb']} MB) to install it")
        with self.lock:
            if self.running():
                raise TrainerError("an install is already running")
            if is_ready(fw):
                raise TrainerError(f"{p['label']} is already installed")
            free = p["free_gb"]
            if free is not None and free < p["disk_gb_needed"]:
                raise TrainerError(f"not enough free disk space: need about {p['disk_gb_needed']} GB, {free} GB free")
            self.log, self.error, self.installing = [], None, fw
            self._record(fw, state="installing")
            self.thread = threading.Thread(target=self._run_install, args=(fw, device), name="trainer-install", daemon=True)
            self.thread.start()
        return self.status()

    def _record(self, fw: str, **rec) -> None:
        st = read_state()
        st.setdefault("frameworks", {})[fw] = rec
        _write_json(home() / "state.json", st)

    def _env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")}
        env.update(PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1", PYTHONUTF8="1")
        return env

    def _run_install(self, fw: str, device: str) -> None:
        f, h = FRAMEWORKS[fw], home()
        py = str(env_python(fw, h))
        cutoff = ts_spec.cutoff(self.today())
        extra = ["--extra-index-url", "https://download.pytorch.org/whl/cpu"] if device == "cpu" and fw == "pytorch" and self.platform not in ("win32", "darwin") else []
        steps = [
            ("Create the isolated environment", [pip_install.base_python(), "-m", "venv", "--clear", str(h / fw / "venv")]),
            ("Update pip", [py, "-m", "pip", "install", "--no-input", f"pip=={ts_spec.PIP_VERSION}"]),
            (f"Install {f['label']}", pip_install.install_cmd(py, cutoff, f["packages"], f.get("source_builds", ()), extra)),
            ("Check that it imports", [py, "-c", f"import {f['module']}; print('VERSION', {f['module']}.__version__)"]),
        ]
        captured: list[str] = []
        try:
            for label, cmd in steps:
                self.log.append(f"== {label}")
                captured = []
                rc = self.runner(cmd, self._env(), lambda ln: (captured.append(ln), self.log.append(ln[:300])))
                if rc != 0:
                    raise TrainerError(pip_install.explain_failure(label, rc, captured, pip_install.python_label()))
            m = next((re.match(r"VERSION (\S+)", c) for c in reversed(captured) if c.startswith("VERSION ")), None)
            if m is None:
                raise TrainerError("the framework imported but its version could not be read")
            self._record(fw, state="ready", version=m.group(1), installed_at=time.time(), cutoff=cutoff)
        except Exception as e:  # noqa: BLE001 - shown to the user; they can retry
            self.error = str(e)[:700]
            self._record(fw, state="failed", error=self.error)
        finally:
            self.installing = None

    def uninstall(self, fw: str) -> dict:
        if fw not in FRAMEWORKS:
            raise TrainerError(f"unknown framework '{fw}'")
        if self.running() and self.installing == fw:
            raise TrainerError("it is being installed right now")
        if any(j["state"] in ("starting", "running") and j["framework"] == fw for j in self.jobs()):
            raise TrainerError("a training job is still running on it - cancel the job first")
        shutil.rmtree(home() / fw, ignore_errors=True)
        st = read_state()
        (st.get("frameworks") or {}).pop(fw, None)
        _write_json(home() / "state.json", st)
        return self.status()

    def status(self) -> dict:
        st = (read_state().get("frameworks") or {})
        fws = []
        for fid, f in FRAMEWORKS.items():
            rec = st.get(fid) or {}
            state = "installing" if self.running() and self.installing == fid else ("ready" if is_ready(fid) else rec.get("state", "not_installed"))
            if state == "installing" and not (self.running() and self.installing == fid):
                state = "failed"                       # the app was closed mid-install
            fws.append(dict(id=fid, label=f["label"], blurb=f["blurb"], licence=f["licence"], size_mb=f["size_mb"], packages=f["packages"],
                            notes=_notes(f), state=state, version=rec.get("version") if state == "ready" else None,
                            error=(rec.get("error") if state == "failed" else None) or (self.error if state == "failed" else None)))
        return dict(frameworks=fws, install=dict(running=self.running(), framework=self.installing, log=self.log[-12:], error=self.error),
                    jobs_running=sum(1 for j in self.jobs() if j["state"] in ("starting", "running")))

    # ------------------------------------------------------------------------------------------------ jobs
    def _job_dir(self, jid: str) -> Path:
        if not JOB_ID_RE.match(jid or ""):
            raise TrainerError("unknown job id")
        d = jobs_dir() / jid
        if not d.is_dir():
            raise TrainerError(f"no job '{jid}'")
        return d

    @staticmethod
    def check_dataset(path: str, workspace: Path | None) -> tuple[Path, list[str]]:
        """The CSV must exist, be a .csv inside the workspace, and have a header. Returns (path, columns)."""
        p = Path(path).expanduser()
        if workspace is not None and not p.is_absolute():
            p = workspace / p
        p = p.resolve()
        if workspace is not None and workspace.resolve() not in (p, *p.parents):
            raise TrainerError(f"the dataset must be inside the workspace folder ({workspace})")
        if p.suffix.lower() != ".csv" or not p.is_file():
            raise TrainerError(f"dataset not found or not a .csv file: {p}")
        try:
            with open(p, newline="", encoding="utf-8-sig") as fh:
                header = next(csv.reader(fh), [])
        except (OSError, UnicodeDecodeError) as e:
            raise TrainerError(f"cannot read the dataset: {e}")
        header = [c.strip() for c in header]
        if len(header) < 2:
            raise TrainerError("the CSV needs a header row with at least two columns")
        return p, header

    def validate_train(self, spec: dict, workspace: Path | None) -> dict:
        fw = spec.get("framework")
        if fw not in FRAMEWORKS:
            raise TrainerError(f"framework must be one of: {', '.join(FRAMEWORKS)}")
        if not is_ready(fw):
            raise TrainerError(f"{FRAMEWORKS[fw]['label']} is not installed. Install it in Catalog > ML tools (it shows the size and licence first).")
        task = spec.get("task", "classification")
        if task not in TASKS:
            raise TrainerError(f"task must be one of: {', '.join(TASKS)}")
        path, header = self.check_dataset(str(spec.get("dataset", "")), workspace)
        target = str(spec.get("target", "")).strip()
        if target not in header:
            raise TrainerError(f"target column '{target}' is not in the CSV. Columns: {', '.join(header[:30])}")
        epochs, minutes = int(spec.get("epochs", 20)), float(spec.get("max_minutes", DEFAULT_MINUTES))
        if not 1 <= epochs <= MAX_EPOCHS:
            raise TrainerError(f"epochs must be 1-{MAX_EPOCHS}")
        if not 0.5 <= minutes <= 720:
            raise TrainerError("max_minutes must be between 0.5 and 720")
        hidden = [int(x) for x in (spec.get("hidden") or [64, 32])]
        if not hidden or len(hidden) > 8 or any(not 1 <= x <= 4096 for x in hidden):
            raise TrainerError("hidden must be 1-8 layer sizes between 1 and 4096")
        cfg = spec.get("config")
        if cfg is not None and (fw != "ludwig" or not isinstance(cfg, dict)):
            raise TrainerError("`config` is only for framework=ludwig and must be an object")
        return dict(framework=fw, task=task, dataset=str(path), target=target, epochs=epochs, max_minutes=minutes, hidden=hidden,
                    learning_rate=float(spec.get("learning_rate", 0.001)), batch_size=int(spec.get("batch_size", 32)),
                    test_size=min(max(float(spec.get("test_size", 0.2)), 0.05), 0.5), seed=int(spec.get("seed", 42)), config=cfg,
                    name=str(spec.get("name") or "")[:60])

    def _launch(self, spec: dict) -> dict:
        jid = secrets.token_hex(5)
        d = jobs_dir() / jid
        d.mkdir(parents=True, exist_ok=True)
        spec = {**spec, "id": jid, "created": time.time()}
        _write_json(d / "spec.json", spec)
        _write_json(d / "status.json", dict(state="starting", created=spec["created"]))
        env = {**self._env(), "PYTHONPATH": str(BACKEND_DIR)}
        pid = self._spawn([str(env_python(spec["framework"])), "-m", "app.trainer_worker", str(d)], env, d / "log.txt")
        _write_json(d / "status.json", {**_read_json(d / "status.json"), "pid": pid})
        return self.job(jid)

    def start_train(self, spec: dict, workspace: Path | None) -> dict:
        return self._launch({**self.validate_train(spec, workspace), "mode": "train"})

    def start_evaluate(self, job_id: str, dataset: str, workspace: Path | None) -> dict:
        src = self.job(job_id)
        if src["state"] != "done" or src["mode"] != "train":
            raise TrainerError("evaluate needs a finished training job")
        path, header = self.check_dataset(dataset, workspace)
        if src["target"] not in header:
            raise TrainerError(f"the evaluation CSV needs the target column '{src['target']}'")
        s = _read_json(self._job_dir(job_id) / "spec.json")
        return self._launch({**{k: s[k] for k in ("framework", "task", "target") if k in s}, "mode": "evaluate", "model_job": job_id,
                             "dataset": str(path), "max_minutes": 30, "name": f"evaluate {job_id}"})

    def job(self, jid: str, tail: int = 0) -> dict:
        d = self._job_dir(jid)
        spec, st = _read_json(d / "spec.json"), _read_json(d / "status.json")
        state, pid = st.get("state", "unknown"), st.get("pid")
        if state in ("starting", "running") and pid:
            elapsed = time.time() - spec.get("created", time.time())
            if not self._alive(pid):
                state = "failed"
                st = {**st, "error": st.get("error") or "the worker stopped unexpectedly - see the log"}
                _write_json(d / "status.json", {**st, "state": "failed"})
            elif elapsed > float(spec.get("max_minutes", DEFAULT_MINUTES)) * 60 + 60:
                self._kill(pid)
                state = "failed"
                st = {**st, "error": f"stopped after the {spec.get('max_minutes')} minute limit"}
                _write_json(d / "status.json", {**st, "state": "failed"})
        out = dict(id=jid, state=state, mode=spec.get("mode", "train"), framework=spec.get("framework"), task=spec.get("task"), target=spec.get("target"),
                   name=spec.get("name") or "", dataset=spec.get("dataset"), created=spec.get("created"), progress=st.get("progress"),
                   epoch=st.get("epoch"), epochs=spec.get("epochs"), error=st.get("error"), metrics=_read_json(d / "metrics.json") or None,
                   artifact=st.get("artifact"), folder=str(d))
        if tail:
            try:
                lines = (d / "log.txt").read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            out["log"] = lines[-tail:]
        return out

    def jobs(self, limit: int = 50) -> list[dict]:
        root = jobs_dir()
        if not root.is_dir():
            return []
        ids = sorted((p.name for p in root.iterdir() if p.is_dir() and JOB_ID_RE.match(p.name)),
                     key=lambda i: -_read_json(root / i / "spec.json").get("created", 0))[:limit]
        return [self.job(i) for i in ids]

    def cancel(self, jid: str) -> dict:
        d = self._job_dir(jid)
        st = _read_json(d / "status.json")
        if st.get("state") not in ("starting", "running"):
            raise TrainerError(f"job {jid} is not running (state: {st.get('state')})")
        if st.get("pid"):
            self._kill(st["pid"])
        _write_json(d / "status.json", {**st, "state": "cancelled", "error": "cancelled by the user"})
        return self.job(jid)


# --------------------------------------------------------------------------------------------------------------------- API
class InstallIn(BaseModel):
    framework: str
    accepted: bool = False
    device: str = "auto"


class NameIn(BaseModel):
    framework: str


def register(app: FastAPI) -> Trainer:
    t = Trainer()
    app.state.trainer = t
    r = APIRouter(prefix="/api/trainer", tags=["trainer"])

    def wrap(fn, *a):
        try:
            return fn(*a)
        except TrainerError as e:
            raise HTTPException(422, str(e))

    @r.get("/status")
    def status():
        return t.status()

    @r.get("/plan/{fw}")
    def plan(fw: str, device: str = "auto"):
        return wrap(t.plan, fw, device)

    @r.post("/install")
    def install(body: InstallIn):
        return wrap(t.start_install, body.framework, body.accepted, body.device)

    @r.post("/uninstall")
    def uninstall(body: NameIn):
        return wrap(t.uninstall, body.framework)

    @r.get("/jobs")
    def jobs():
        return t.jobs()

    @r.get("/jobs/{jid}")
    def job(jid: str, tail: int = 40):
        return wrap(t.job, jid, min(max(tail, 0), 500))

    @r.post("/jobs/{jid}/cancel")
    def cancel(jid: str):
        return wrap(t.cancel, jid)

    app.include_router(r)
    return t
