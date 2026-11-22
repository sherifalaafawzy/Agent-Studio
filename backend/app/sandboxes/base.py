"""What a sandbox is for Agent Studio: a place where an agent's shell commands run, never on the host. Two kinds:

  docker-container  the default: one long-lived hardened Docker container per workspace (non-root, all capabilities dropped,
                    no-new-privileges, memory / CPU / process limits, optional read-only root file system, only the workspace mounted).
  docker-microvm    EXPERIMENTAL: a Docker Sandbox (`sbx`) - a micro VM with its own kernel and Docker daemon. Proprietary Docker
                    software, needs `sbx login` and, on Windows, the Windows Hypervisor Platform. Off until switched on.
Both fail closed: when the sandbox cannot start, commands are refused - there is no fallback to the host.
"""
from __future__ import annotations

import re

from ..harness.sandbox import SandboxUnavailable  # noqa: F401 - the one error every kind raises

SANDBOX_KINDS = ("docker-container", "docker-microvm")
DEFAULTS = {"kind": "docker-container", "memory": "1g", "cpus": "2", "pids": 256, "read_only_root": False, "microvm_enabled": False}
MEM = re.compile(r"^[1-9]\d{0,4}[mg]$")
INFO = {
    "docker-container": dict(label="Docker container", experimental=False,
                             notice="Uses Docker on this PC. Hardened: non-root, no capabilities, resource limits, only the workspace is visible."),
    "docker-microvm": dict(label="Docker Sandbox (micro VM)", experimental=True,
                           notice="Experimental. Docker Sandboxes (sbx) is proprietary Docker software under Docker's own terms; it needs `sbx login` and, on Windows, "
                                  "the Windows Hypervisor Platform. Its network rules come from sbx's own policy (`sbx policy ls`), not from the network setting here."),
}


def clean_policy(raw: object) -> dict:
    """The sandbox settings with every value checked (ValueError says what is wrong)."""
    d = {**DEFAULTS, **(raw if isinstance(raw, dict) else {})}
    if d["kind"] not in SANDBOX_KINDS:
        raise ValueError(f"the sandbox kind must be one of: {', '.join(SANDBOX_KINDS)}")
    if not isinstance(d["memory"], str) or not MEM.match(d["memory"]):
        raise ValueError("memory must look like 512m or 2g")
    try:
        cpus = float(d["cpus"])
    except (TypeError, ValueError):
        raise ValueError("cpus must be a number")
    if not 0.25 <= cpus <= 32:
        raise ValueError("cpus must be between 0.25 and 32")
    if isinstance(d["pids"], bool) or not isinstance(d["pids"], int) or not 32 <= d["pids"] <= 4096:
        raise ValueError("processes must be a whole number between 32 and 4096")
    for k in ("read_only_root", "microvm_enabled"):
        if not isinstance(d[k], bool):
            raise ValueError(f"{k} must be true or false")
    return {"kind": d["kind"], "memory": d["memory"], "cpus": f"{cpus:g}", "pids": d["pids"], "read_only_root": d["read_only_root"], "microvm_enabled": d["microvm_enabled"]}
