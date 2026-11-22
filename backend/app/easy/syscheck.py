"""'Is this PC ready?' - a quick, read-only look at what Agent Studio can use (Docker, Ollama, Node, Python, disk, memory, GPU).

Nothing here installs or changes anything. Every probe is injectable so tests never touch the real machine.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import httpx

OLLAMA_URL = "http://127.0.0.1:11434"
TRANSPORT: httpx.BaseTransport | None = None          # tests swap in a MockTransport


def run_line(cmd: list[str], timeout: float = 6) -> str | None:
    """First line of a command's output, or None if it is missing, fails or is too slow."""
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or r.stderr or "").strip().splitlines()
    return out[0].strip() if r.returncode == 0 and out else None


def ram_gb() -> float | None:
    """Total memory in GB (Windows API, /proc/meminfo or sysconf); None when it cannot be read."""
    try:
        if os.name == "nt":
            import ctypes

            class MemStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("tpage", ctypes.c_ulonglong), ("apage", ctypes.c_ulonglong), ("tvirt", ctypes.c_ulonglong), ("avirt", ctypes.c_ulonglong),
                            ("xvirt", ctypes.c_ulonglong)]
            m = MemStatus()
            m.length = ctypes.sizeof(MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))     # type: ignore[attr-defined]
            return round(m.total / 1e9, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9, 1)
    except (OSError, ValueError, AttributeError):
        return None


def ollama_state(base: str = OLLAMA_URL) -> dict:
    """{running, version} for a local Ollama server (never raises)."""
    try:
        with httpx.Client(transport=TRANSPORT, timeout=2.0) as c:
            r = c.get(f"{base.rstrip('/')}/api/version")
        if r.status_code == 200:
            return {"running": True, "version": r.json().get("version")}
    except (httpx.HTTPError, ValueError):
        pass
    return {"running": False, "version": None}


def free_gb(path: Path) -> float | None:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return round(shutil.disk_usage(probe).free / 1e9, 1)
    except OSError:
        return None


def check(workspace: Path, data: Path, *, line: Callable[[list[str]], str | None] = run_line, ram: Callable[[], float | None] = ram_gb,
          ollama: Callable[[], dict] = ollama_state, gpu: Callable[[], dict] | None = None,
          ollama_path: Callable[[], str | None] | None = None) -> dict:
    """One list of checks, each {id, label, state: ok|missing|warn, detail, why}. 'missing' is not an error: it says what a feature needs."""
    from .ollama_setup import find_ollama
    gpu_info = (gpu or _gpu)()
    docker = line(["docker", "version", "--format", "{{.Server.Version}}"])
    node, npm = line(["node", "--version"]), line(["npm", "--version"])
    oll = ollama()
    found = (ollama_path or find_ollama)()
    disk, mem = free_gb(data), ram()
    items = [
        dict(id="python", label="Python", state="ok", detail=f"{sys.version.split()[0]} (runs the Agent Studio server)", why="Already running: this is what Agent Studio itself uses."),
        dict(id="docker", label="Docker", state="ok" if docker else "missing", detail=f"engine {docker}" if docker else "not running or not installed",
             why="Needed for the safe code sandbox and for the optional vector database stores (Qdrant, pgvector, Chroma). You can skip it for plain chat."),
        dict(id="ollama", label="Ollama (local models)", state="ok" if oll["running"] else ("warn" if found else "missing"),
             detail=(f"running, version {oll['version']}" if oll["running"] else ("installed but not running" if found else "not installed")),
             why="Runs AI models on this PC for free, with nothing sent to the internet. You can skip it if you will use an online provider."),
        dict(id="node", label="Node.js", state="ok" if node and npm else "missing", detail=f"{node} (npm {npm})" if node and npm else "not installed",
             why="Needed to install and run most coding agents (Claude Code, Codex, Gemini CLI). Optional."),
        dict(id="disk", label="Free disk space", state="missing" if disk is None else ("warn" if disk < 15 else "ok"),
             detail="unknown" if disk is None else f"{disk} GB free", why="Local models are large (2-20 GB each). Keep at least 15 GB free if you plan to use them."),
        dict(id="ram", label="Memory (RAM)", state="missing" if mem is None else ("warn" if mem < 8 else "ok"), detail="unknown" if mem is None else f"{mem} GB",
             why="Decides which local models run comfortably (8 GB: small ones; 16 GB or more: most)."),
        dict(id="gpu", label="Graphics card (GPU)", state="ok" if gpu_info.get("nvidia") else "missing",
             detail=(f"{gpu_info['gpu']} ({round((gpu_info.get('vram_mb') or 0) / 1024)} GB)" if gpu_info.get("nvidia") else "no NVIDIA GPU found"),
             why="A GPU makes local models much faster. Not required: models also run on the CPU, just slower."),
    ]
    return dict(items=items, workspace=workspace.as_posix(), ram_gb=mem, vram_gb=round((gpu_info.get("vram_mb") or 0) / 1024, 1) or None,
                ollama=dict(oll, installed=bool(found)))


def _gpu() -> dict:
    from ..d1_setup import probe_hardware
    return probe_hardware()
