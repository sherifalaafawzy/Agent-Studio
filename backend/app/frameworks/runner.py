"""Running an agent turn on a framework: the factory, the ADK entry and the worker-process adapter.

`FrameworkFactory.get("langgraph")` returns an `AgentFramework`. ADK is the built-in engine (the chat service drives it
directly: `native`). Every other framework is an `ExternalFramework`: it starts that framework's worker in its isolated
environment, hands it the job, and turns the worker's output lines into Agent Studio's normal chat events. Starting the process
is injectable (`launcher`) so the conformance tests run every adapter against a scripted fake worker.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from .base import AgentFramework, EventSink, FrameworkError, FrameworkInfo, RunJob, RunOutcome
from .catalog import FRAMEWORKS
from .envs import Envs, env_python

WORKER = Path(__file__).with_name("worker.py")
PROTOCOL = 1
Launcher = Callable[[str, dict, dict], AsyncIterator[str]]        # (framework id, job, environment) -> the worker's output lines
_ENV_KEEP = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "COMSPEC", "LANG")


def gateway_base() -> str:
    return os.environ.get("AGENT_STUDIO_URL") or "http://127.0.0.1:8765"


def worker_env(extra: dict | None = None) -> dict:
    """The worker's whole environment: no provider keys, no Agent Studio settings - only what a process needs to start."""
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    env.update(PYTHONUTF8="1", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost", PYTHONDONTWRITEBYTECODE="1")
    env.update(extra or {})
    return env


async def subprocess_launcher(fid: str, job: dict, env: dict) -> AsyncIterator[str]:  # pragma: no cover - starts a real process
    py = env_python(fid)
    proc = await asyncio.create_subprocess_exec(
        str(py), str(WORKER), fid, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        limit=1 << 22, creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0))
    assert proc.stdin and proc.stdout and proc.stderr
    err: list[bytes] = []

    async def drain_err() -> None:
        async for ln in proc.stderr:
            err.append(ln)
            del err[:-20]

    t = asyncio.ensure_future(drain_err())
    try:
        proc.stdin.write((json.dumps(job) + "\n").encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        async for ln in proc.stdout:
            yield ln.decode("utf-8", "replace")
        code = await proc.wait()
        if code not in (0, None):
            tail = b"".join(err)[-600:].decode("utf-8", "replace").strip()
            yield json.dumps({"t": "error", "message": f"the worker stopped with exit code {code}" + (f": {tail}" if tail else "")})
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        t.cancel()


class AdkFramework(AgentFramework):
    """Google ADK: the built-in engine. The chat service runs it directly (it is where sub-agents, MCP and spawning live)."""
    info = FRAMEWORKS["adk"]

    def ready(self) -> bool:
        return True

    async def run(self, job: RunJob, session: Any, sink: EventSink, extra_env: dict | None = None) -> RunOutcome:
        raise FrameworkError("Google ADK runs inside the chat service itself; it is not started as a worker")


class ExternalFramework(AgentFramework):
    def __init__(self, info: FrameworkInfo, launcher: Launcher | None = None, ready: Callable[[str], bool] | None = None):
        self.info = info
        self.launcher = launcher or subprocess_launcher
        from .envs import is_ready
        self._ready = ready or is_ready

    def ready(self) -> bool:
        return self._ready(self.info.id)

    def wire(self, job: RunJob, session: Any) -> dict:
        """The one JSON object the worker receives (nothing secret: its model and tools live behind the run's private address)."""
        return dict(protocol=PROTOCOL, gateway=f"{gateway_base()}/internal/fw/{session.token}", model=job.model, instructions=job.instructions,
                    message=job.message, history=job.history, tools=job.tools, max_steps=job.max_steps, workspace=job.workspace)

    async def run(self, job: RunJob, session: Any, sink: EventSink, extra_env: dict | None = None) -> RunOutcome:
        if not self.ready():
            raise FrameworkError(f"{self.info.label} is not installed. Install it in Settings > Agent frameworks, or switch this agent back to Google ADK.")
        out, texts = RunOutcome(), []
        try:
            async with asyncio.timeout(job.timeout_s):
                async for raw in self.launcher(self.info.id, self.wire(job, session), worker_env(extra_env)):
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        continue                                   # the framework printed something of its own: not part of the protocol
                    if not isinstance(ev, dict):
                        continue
                    t = ev.get("t")
                    if t == "text" and isinstance(ev.get("delta"), str):
                        texts.append(ev["delta"])
                        sink({"type": "text", "delta": ev["delta"]})
                    elif t == "final":
                        out.text = str(ev.get("text") or "")
                    elif t == "error":
                        out.status, out.error = "error", str(ev.get("message") or "the worker failed")[:800]
        except TimeoutError:
            out.status, out.error = "error", f"{self.info.label} did not finish within {int(job.timeout_s)} seconds"
        if not out.text:
            out.text = "".join(texts)
        return out


class FrameworkFactory:
    """The abstract factory: framework id -> adapter. Adding a framework = one catalog entry + one worker function."""

    def __init__(self, envs: Envs | None = None, launcher: Launcher | None = None):
        self.envs = envs or Envs()
        self._adapters: dict[str, AgentFramework] = {"adk": AdkFramework()}
        for fid, info in FRAMEWORKS.items():
            if not info.native:
                self._adapters[fid] = ExternalFramework(info, launcher)

    def ids(self) -> list[str]:
        return list(self._adapters)

    def get(self, fid: str) -> AgentFramework:
        fw = self._adapters.get(fid or "adk")
        if fw is None:
            raise FrameworkError(f"unknown agent framework '{fid}' (choose from {', '.join(self._adapters)})")
        return fw

    def matrix(self) -> dict:
        from .base import CAPS, LEVELS
        st = self.envs.status()
        return dict(columns=[dict(id=k, label=v[0], means=v[1]) for k, v in CAPS.items()], levels=list(LEVELS),
                    frameworks=[dict(fw.info.public(), **st["states"][fid]) for fid, fw in self._adapters.items()], install=st["install"],
                    python=sys.version.split()[0])
