"""Docker sandbox. Fail-closed: if Docker or the image is unavailable, execution is REFUSED -
there is no fallback to running on the host.

One long-lived container per (workspace, network-mode): the workspace is bind-mounted at /workspace,
the container runs as a non-root user with all capabilities dropped, no-new-privileges, and
memory/CPU/pid limits. Network is OFF by default (--network none).
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import threading
from pathlib import Path

IMAGE = "agent-studio-sandbox:1"
MAX_OUT = 20_000


class SandboxUnavailable(Exception):
    pass


def _docker(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    exe = shutil.which("docker")
    if not exe:
        raise SandboxUnavailable("docker CLI not found (the sandbox is required)")
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise SandboxUnavailable("docker did not respond in time")


def status() -> dict:
    try:
        r = _docker(["version", "--format", "{{.Server.Version}}"], timeout=8)
    except SandboxUnavailable as e:
        return dict(docker=False, image=False, detail=str(e))
    if r.returncode != 0:
        return dict(docker=False, image=False, detail="Docker daemon not running (start Docker Desktop)")
    img = _docker(["image", "inspect", IMAGE, "--format", "{{.Id}}"], timeout=8).returncode == 0
    return dict(docker=True, image=img, server=r.stdout.strip(),
                detail="ready" if img else f"image missing: docker build -t {IMAGE} docker/sandbox")


def _clip(s: str) -> tuple[str, bool]:
    if len(s) <= MAX_OUT:
        return s, False
    h = MAX_OUT // 2
    return s[:h] + f"\n…[{len(s) - MAX_OUT} chars truncated]…\n" + s[-h:], True


class Sandbox:
    _lock = threading.Lock()

    def __init__(self, workspace: Path, network: bool = False, memory: str = "1g", cpus: str = "2"):
        self.workspace = Path(workspace).resolve()
        self.network = network
        self.memory, self.cpus = memory, cpus
        h = hashlib.sha1(f"{self.workspace}|{network}".encode()).hexdigest()[:12]
        self.name = f"agent-studio-{h}"

    def ensure(self) -> None:
        with Sandbox._lock:
            st = status()
            if not st["docker"] or not st["image"]:
                raise SandboxUnavailable(st["detail"])
            r = _docker(["inspect", "-f", "{{.State.Running}}", self.name], timeout=10)
            if r.returncode == 0:
                if r.stdout.strip() == "true":
                    return
                _docker(["rm", "-f", self.name])
            self.workspace.mkdir(parents=True, exist_ok=True)
            args = ["run", "-d", "--name", self.name, "--label", "agent-studio=1",
                    "--network", "bridge" if self.network else "none",
                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--pids-limit", "256", "--memory", self.memory, "--cpus", self.cpus,
                    "--user", "1000:1000", "--mount", f"type=bind,source={self.workspace},target=/workspace",
                    "-w", "/workspace", IMAGE]
            r = _docker(args, timeout=60)
            if r.returncode != 0:
                raise SandboxUnavailable(f"could not start sandbox container: {r.stderr.strip()[:300]}")

    def exec(self, command: str, timeout_s: int = 60) -> dict:
        """Run a shell command inside the container. Returns exit_code/stdout/stderr/timed_out."""
        timeout_s = max(1, min(int(timeout_s), 600))
        self.ensure()
        try:
            r = _docker(["exec", "-w", "/workspace", self.name, "timeout", "-s", "KILL", str(timeout_s), "bash", "-c", command],
                        timeout=timeout_s + 10)
        except SandboxUnavailable:
            return dict(exit_code=-1, stdout="", stderr="", timed_out=True, truncated=False)
        out, t1 = _clip(r.stdout)
        err, t2 = _clip(r.stderr)
        return dict(exit_code=r.returncode, stdout=out, stderr=err, timed_out=r.returncode == 137, truncated=t1 or t2)

    def stop(self) -> None:
        try:
            _docker(["rm", "-f", self.name], timeout=20)
        except SandboxUnavailable:
            pass
