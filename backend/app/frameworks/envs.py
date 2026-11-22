"""Isolated environments for the agent frameworks that are not built in.

Each lives in its OWN virtual environment under `<data>/envs/frameworks/<id>/venv` (never in the backend's), is installed only
after the user accepts its size and licence, and follows the 14-day rule: direct packages are pinned to exact versions and pip
runs with `--uploaded-prior-to <today - 14 days>` so nothing newer is pulled in transitively either. Binary wheels only (no
build scripts run). The command runner is injectable so the whole flow is tested without installing anything.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

from .. import pip_install, ts_spec
from ..d1_setup import free_disk_gb, run_streaming
from ..db import data_dir
from .base import FrameworkError
from .catalog import FRAMEWORKS


def home() -> Path:
    return Path(os.environ.get("FRAMEWORKS_HOME") or (data_dir() / "envs" / "frameworks"))


def env_python(fid: str, h: Path | None = None) -> Path:
    return (h or home()) / fid / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    for attempt in range(8):                                  # Windows refuses the swap while a scanner or reader holds the file: try again
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def read_state() -> dict:
    return _read(home() / "state.json")


def is_ready(fid: str) -> bool:
    info = FRAMEWORKS.get(fid)
    if info is None:
        return False
    if info.native:
        return True
    return (read_state().get("frameworks") or {}).get(fid, {}).get("state") == "ready" and env_python(fid).exists()


class Envs:
    def __init__(self, runner: Callable | None = None, today: Callable[[], date] | None = None):
        self.runner = runner or run_streaming
        self.today = today or date.today
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.installing: str | None = None
        self.log: list[str] = []
        self.error: str | None = None

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _info(self, fid: str):
        f = FRAMEWORKS.get(fid)
        if f is None:
            raise FrameworkError(f"unknown framework '{fid}' (choose from {', '.join(FRAMEWORKS)})")
        if f.native:
            raise FrameworkError(f"{f.label} is built in: there is nothing to install")
        return f

    def plan(self, fid: str) -> dict:
        f = self._info(fid)
        h = home()
        return dict(framework=fid, label=f.label, packages=list(f.packages), download_mb=f.size_mb, licence=f.licence, notes=f.notes,
                    disk_gb_needed=round(f.size_mb * 2 / 1000, 1), free_gb=free_disk_gb(h if h.exists() else data_dir()))

    def start_install(self, fid: str, accepted: bool) -> dict:
        p = self.plan(fid)
        if not accepted:
            raise FrameworkError(f"Accept the {p['label']} licence ({p['licence']}) and the download size (about {p['download_mb']} MB) to install it")
        with self.lock:
            if self.running():
                raise FrameworkError("an install is already running")
            if is_ready(fid):
                raise FrameworkError(f"{p['label']} is already installed")
            free = p["free_gb"]
            if free is not None and free < p["disk_gb_needed"]:
                raise FrameworkError(f"not enough free disk space: need about {p['disk_gb_needed']} GB, {free} GB free")
            self.log, self.error, self.installing = [], None, fid
            self._record(fid, state="installing")
            self.thread = threading.Thread(target=self._run_install, args=(fid,), name="framework-install", daemon=True)
            self.thread.start()
        return self.status()

    def _record(self, fid: str, **rec) -> None:
        st = read_state()
        st.setdefault("frameworks", {})[fid] = rec
        _write(home() / "state.json", st)

    def _env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")}
        env.update(PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1", PYTHONUTF8="1")
        return env

    def _run_install(self, fid: str) -> None:
        f, h = FRAMEWORKS[fid], home()
        py = str(env_python(fid, h))
        cutoff = ts_spec.cutoff(self.today())
        check = (f"import importlib.metadata as m, {f.module}; print('VERSION', m.version({f.dist!r}))")
        steps = [
            ("Create the isolated environment", [pip_install.base_python(), "-m", "venv", "--clear", str(h / fid / "venv")]),
            ("Update pip", [py, "-m", "pip", "install", "--no-input", f"pip=={ts_spec.PIP_VERSION}"]),
            (f"Install {f.label}", pip_install.install_cmd(py, cutoff, f.packages)),
            ("Check that it imports", [py, "-c", check]),
        ]
        captured: list[str] = []
        try:
            for label, cmd in steps:
                self.log.append(f"== {label}")
                captured = []
                rc = self.runner(cmd, self._env(), lambda ln: (captured.append(ln), self.log.append(ln[:300])))
                if rc != 0:
                    raise FrameworkError(pip_install.explain_failure(label, rc, captured, pip_install.python_label()))
            ver = next((c.split(" ", 1)[1] for c in reversed(captured) if c.startswith("VERSION ")), None)
            if ver is None:
                raise FrameworkError("it imported but its version could not be read")
            self._record(fid, state="ready", version=ver, installed_at=time.time(), cutoff=cutoff)
        except Exception as e:  # noqa: BLE001 - shown to the user; they can retry
            self.error = str(e)[:700]
            self._record(fid, state="failed", error=self.error)
        finally:
            self.installing = None

    def uninstall(self, fid: str) -> dict:
        self._info(fid)
        if self.running() and self.installing == fid:
            raise FrameworkError("it is being installed right now")
        shutil.rmtree(home() / fid, ignore_errors=True)
        st = read_state()
        (st.get("frameworks") or {}).pop(fid, None)
        _write(home() / "state.json", st)
        return self.status()

    def state_of(self, fid: str) -> dict:
        f = FRAMEWORKS[fid]
        if f.native:
            return dict(state="ready", version=None, error=None)
        rec = (read_state().get("frameworks") or {}).get(fid) or {}
        state = "installing" if self.running() and self.installing == fid else ("ready" if is_ready(fid) else rec.get("state", "not_installed"))
        if state == "installing" and not (self.running() and self.installing == fid):
            state = "failed"                                   # the app was closed mid-install
        if state == "ready" and not is_ready(fid):
            state = "not_installed"
        return dict(state=state, version=rec.get("version") if state == "ready" else None,
                    error=(rec.get("error") or self.error) if state == "failed" else None)

    def status(self) -> dict:
        return dict(install=dict(running=self.running(), framework=self.installing, log=self.log[-12:], error=self.error),
                    states={fid: self.state_of(fid) for fid in FRAMEWORKS})
