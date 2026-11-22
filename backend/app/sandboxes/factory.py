"""SandboxFactory: the one place that decides which kind of sandbox a command runs in and how it is limited.

The default comes from the `sandbox_config` setting; an agent may pick its own kind (`agent.sandbox`, the lead agent of a turn
decides for the whole turn). Network on/off stays the existing `sandbox_network` setting. Nothing falls back to the host.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..db import data_dir
from ..harness.sandbox import Sandbox, SandboxUnavailable, status as docker_status
from . import microvm
from .base import DEFAULTS, INFO, SANDBOX_KINDS, clean_policy

CONFIG_KEY = "sandbox_config"


class SandboxFactory:
    def __init__(self, db: Any):
        self.db = db

    def policy(self) -> dict:
        try:
            return clean_policy(json.loads(self.db.get_setting(CONFIG_KEY) or "{}"))
        except (ValueError, TypeError):
            return dict(DEFAULTS)

    def set_policy(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("the sandbox settings must be an object")
        unknown = sorted(set(patch) - set(DEFAULTS))
        if unknown:
            raise ValueError(f"unknown sandbox setting: {', '.join(unknown)}")
        new = clean_policy({**self.policy(), **patch})
        self.db.set_setting(CONFIG_KEY, json.dumps(new))
        return new

    def network(self) -> bool:
        return self.db.get_setting("sandbox_network", "0") == "1"

    def make(self, workspace: Path, kind: str = "") -> Any:
        """A sandbox for this workspace (not started yet: it starts on first use and refuses to run if it cannot)."""
        pol = self.policy()
        kind = kind or pol["kind"]
        if kind not in SANDBOX_KINDS:
            raise SandboxUnavailable(f"unknown sandbox kind '{kind}'")
        if kind == "docker-microvm":
            return microvm.MicroVmSandbox(workspace, self.network(), pol["cpus"], pol["microvm_enabled"])
        return Sandbox(workspace, network=self.network(), memory=pol["memory"], cpus=pol["cpus"], pids=pol["pids"], read_only=pol["read_only_root"])

    def status(self) -> dict:
        pol = self.policy()
        d = docker_status()
        kinds = []
        for k in SANDBOX_KINDS:
            if k == "docker-container":
                avail, detail = bool(d["docker"] and d["image"]), d["detail"]
            else:
                m = microvm.status()
                avail, detail = bool(pol["microvm_enabled"] and m["available"]), (m["detail"] if pol["microvm_enabled"] else "switched off (experimental): turn it on below to use it")
            kinds.append(dict(id=k, available=avail, detail=detail, **INFO[k]))
        return dict(policy=pol, network=self.network(), kinds=kinds, default=pol["kind"])

    def test(self, kind: str) -> dict:
        """Run `echo` in a scratch folder to prove the kind works (needs Docker / sbx; says what is missing otherwise)."""
        if kind not in SANDBOX_KINDS:
            return dict(ok=False, detail=f"unknown sandbox kind '{kind}'")
        ws = data_dir() / "sandbox-test"
        ws.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        try:
            r = self.make(ws, kind).exec("echo agent-studio-ok", 30)
        except SandboxUnavailable as e:
            return dict(ok=False, detail=str(e))
        ok = r["exit_code"] == 0 and "agent-studio-ok" in r["stdout"]
        return dict(ok=ok, detail="the sandbox ran a command" if ok else f"the command failed: {(r['stderr'] or r['stdout'])[:200]}", ms=int((time.time() - t0) * 1000))
