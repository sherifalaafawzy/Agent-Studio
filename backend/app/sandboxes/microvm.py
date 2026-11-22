"""Docker Sandboxes (`sbx`) as a sandbox kind - EXPERIMENTAL. One micro VM per workspace, created with the built-in `shell` agent.

Commands used (all from Docker's CLI reference, checked 2026-11-22): `sbx version`, `sbx ls --format json`,
`sbx create shell <workspace> --name <name> --cpus N`, `sbx exec <name> <command...>` (starts a stopped sandbox), `sbx rm <name>`.
NOT verified on a real machine: the folder the workspace appears at inside the VM. So `ensure` proves it first: it drops a marker file
into the workspace and refuses to run anything unless the VM can see it.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Callable

from ..harness.sandbox import SandboxUnavailable, _clip

SBX_RUN: Callable[[list[str], float], subprocess.CompletedProcess] | None = None     # tests inject a fake sbx
WINDOWS_HELP = ("On Windows turn on the Windows Hypervisor Platform (elevated PowerShell: Enable-WindowsOptionalFeature -Online "
                "-FeatureName HypervisorPlatform -All) and run `sbx login`.")


def sbx(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    if SBX_RUN is not None:
        return SBX_RUN(args, timeout)
    exe = shutil.which("sbx")
    if not exe:
        raise SandboxUnavailable("the sbx command was not found (install Docker Sandboxes: winget install -h Docker.sbx)")
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise SandboxUnavailable("sbx did not answer in time")


def status() -> dict:
    try:
        v = sbx(["version"], 8)
    except SandboxUnavailable as e:
        return dict(available=False, detail=str(e))
    if v.returncode != 0:
        return dict(available=False, detail=f"sbx is installed but not working: {(v.stderr.strip() or v.stdout.strip())[:200]}")
    ls = sbx(["ls", "--format", "json"], 15)
    if ls.returncode != 0:
        return dict(available=False, version=v.stdout.strip()[:80], detail=f"sbx cannot reach its service: {ls.stderr.strip()[:200]}. {WINDOWS_HELP}")
    return dict(available=True, version=v.stdout.strip()[:80], detail="ready")


class MicroVmSandbox:
    kind = "docker-microvm"
    _lock = threading.Lock()

    def __init__(self, workspace: Path, network: bool = False, cpus: str = "2", enabled: bool = False):
        self.workspace = Path(workspace).resolve()
        self.network = network
        self.cpus, self.enabled = cpus, enabled
        self.name = "agent-studio-" + hashlib.sha1(str(self.workspace).encode()).hexdigest()[:10]

    def _exists(self) -> bool:
        r = sbx(["ls", "--format", "json"], 15)
        if r.returncode != 0:
            raise SandboxUnavailable(f"sbx cannot list sandboxes: {r.stderr.strip()[:200]}. {WINDOWS_HELP}")
        try:
            data = json.loads(r.stdout or "[]")
        except ValueError:
            raise SandboxUnavailable("sbx returned a list this version of Agent Studio cannot read")
        rows = data if isinstance(data, list) else data.get("sandboxes", [])
        return any(isinstance(x, dict) and (x.get("name") or x.get("NAME")) == self.name for x in rows)

    def ensure(self) -> None:
        if not self.enabled:
            raise SandboxUnavailable("the micro VM sandbox is switched off (Settings > Agents & sandbox > Sandbox)")
        with MicroVmSandbox._lock:
            if not self._exists():
                self.workspace.mkdir(parents=True, exist_ok=True)
                r = sbx(["create", "shell", str(self.workspace), "--name", self.name, "--cpus", str(max(1, int(float(self.cpus))))], 300)
                if r.returncode != 0:
                    raise SandboxUnavailable(f"sbx could not create the sandbox: {r.stderr.strip()[:300]}")
            marker = self.workspace / f".agent-studio-probe-{uuid.uuid4().hex[:8]}"
            marker.write_text("probe", encoding="utf-8")
            try:
                r = sbx(["exec", self.name, "ls", "-A"], 60)
            finally:
                marker.unlink(missing_ok=True)
            if r.returncode != 0 or marker.name not in r.stdout:
                raise SandboxUnavailable("the micro VM cannot see the workspace folder, so it was not used (this is the part of sbx that is not verified on every system)")

    def exec(self, command: str, timeout_s: int = 60) -> dict:
        timeout_s = max(1, min(int(timeout_s), 600))
        self.ensure()
        try:
            r = sbx(["exec", self.name, "timeout", "-s", "KILL", str(timeout_s), "bash", "-c", command], timeout_s + 15)
        except SandboxUnavailable:
            return dict(exit_code=-1, stdout="", stderr="", timed_out=True, truncated=False)
        out, t1 = _clip(r.stdout)
        err, t2 = _clip(r.stderr)
        return dict(exit_code=r.returncode, stdout=out, stderr=err, timed_out=r.returncode == 137, truncated=t1 or t2)

    def stop(self) -> None:
        try:
            sbx(["rm", self.name], 60)
        except SandboxUnavailable:
            pass
