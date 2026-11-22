"""Sandbox kinds: the policy, the Docker container options, the experimental micro VM (sbx) against a fake CLI, the factory, status and API."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.agents import AgentDef
from app.harness import sandbox as H
from app.sandboxes import base as SB
from app.sandboxes import factory as SF
from app.sandboxes import microvm as MV
from app.sandboxes.base import SandboxUnavailable


def cp(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


# ------------------------------------------------------------------------------------------------------------------- policy
def test_policy_defaults_and_validation():
    assert SB.clean_policy(None) == {"kind": "docker-container", "memory": "1g", "cpus": "2", "pids": 256, "read_only_root": False, "microvm_enabled": False}
    ok = SB.clean_policy({"kind": "docker-microvm", "memory": "512m", "cpus": 0.5, "pids": 64, "read_only_root": True, "microvm_enabled": True})
    assert ok == {"kind": "docker-microvm", "memory": "512m", "cpus": "0.5", "pids": 64, "read_only_root": True, "microvm_enabled": True}
    for bad, msg in [({"kind": "vm"}, "kind must be"), ({"memory": "lots"}, "memory must look"), ({"memory": 5}, "memory must look"), ({"cpus": "many"}, "cpus must be a number"),
                     ({"cpus": 0.1}, "between 0.25 and 32"), ({"cpus": 64}, "between 0.25 and 32"), ({"pids": 5}, "between 32 and 4096"), ({"pids": True}, "between 32 and 4096"),
                     ({"pids": "9"}, "between 32 and 4096"), ({"read_only_root": 1}, "true or false"), ({"microvm_enabled": "yes"}, "true or false")]:
        with pytest.raises(ValueError, match=msg):
            SB.clean_policy(bad)
    assert set(SB.INFO) == set(SB.SANDBOX_KINDS) and SB.INFO["docker-microvm"]["experimental"] and "proprietary" in SB.INFO["docker-microvm"]["notice"]


def test_docker_sandbox_applies_the_policy(monkeypatch, tmp_path):
    calls: list = []

    def fake(args, timeout=30):
        calls.append(args)
        if args[:2] == ["inspect", "-f"]:
            return cp(1)
        return cp(0, "id")
    monkeypatch.setattr(H, "_docker", fake)
    monkeypatch.setattr(H, "status", lambda: {"docker": True, "image": True, "detail": "ready"})
    plain = H.Sandbox(tmp_path, memory="2g", cpus="1.5", pids=100)
    plain.ensure()
    run = next(a for a in calls if a[0] == "run")
    assert run[run.index("--pids-limit") + 1] == "100" and run[run.index("--memory") + 1] == "2g" and run[run.index("--cpus") + 1] == "1.5" and "--read-only" not in run
    assert "--cap-drop" in run and "no-new-privileges" in run and run[run.index("--network") + 1] == "none" and plain.kind == "docker-container"
    calls.clear()
    ro = H.Sandbox(tmp_path, network=True, read_only=True)
    ro.ensure()
    run = next(a for a in calls if a[0] == "run")
    assert "--read-only" in run and "/tmp:rw,exec,size=256m" in run and run[run.index("--network") + 1] == "bridge"
    assert ro.name != plain.name                                         # different limits are a different container


# ------------------------------------------------------------------------------------------------------------------ micro VM
class FakeSbx:
    def __init__(self, ws: Path):
        self.ws, self.calls, self.sandboxes, self.fail, self.sees_workspace = ws, [], [], {}, True

    def __call__(self, args, timeout):
        self.calls.append(args)
        cmd = args[0]
        if cmd in self.fail:
            f = self.fail[cmd]
            if isinstance(f, Exception):
                raise f
            return f
        if cmd == "version":
            return cp(0, "sbx 0.46.0\n")
        if cmd == "ls":
            return cp(0, json.dumps(self.sandboxes))
        if cmd == "create":
            self.sandboxes.append({"name": args[args.index("--name") + 1]})
            return cp(0)
        if cmd == "exec" and args[2:] == ["ls", "-A"]:
            return cp(0, "\n".join(p.name for p in self.ws.iterdir()) if self.sees_workspace else "")
        if cmd == "exec":
            return cp(0, "hello\n", "warn\n")
        return cp(0)


@pytest.fixture
def sbx(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    f = FakeSbx(ws)
    monkeypatch.setattr(MV, "SBX_RUN", f)
    f.root = ws
    return f


def test_microvm_status_reports_what_is_missing(sbx):
    assert MV.status() == {"available": True, "version": "sbx 0.46.0", "detail": "ready"}
    sbx.fail["ls"] = cp(1, err="daemon not running")
    st = MV.status()
    assert not st["available"] and "daemon not running" in st["detail"] and "HypervisorPlatform" in st["detail"]
    sbx.fail["version"] = cp(1, err="broken install")
    assert "installed but not working: broken install" in MV.status()["detail"]
    sbx.fail["version"] = SandboxUnavailable("the sbx command was not found")
    assert MV.status() == {"available": False, "detail": "the sbx command was not found"}


def test_microvm_creates_proves_the_workspace_is_visible_then_runs(sbx):
    vm = MV.MicroVmSandbox(sbx.root, cpus="2", enabled=True)
    r = vm.exec("echo hello")
    assert r == {"exit_code": 0, "stdout": "hello\n", "stderr": "warn\n", "timed_out": False, "truncated": False}
    create = next(c for c in sbx.calls if c[0] == "create")
    assert create[1:3] == ["shell", str(sbx.root.resolve())] and create[create.index("--name") + 1] == vm.name and create[create.index("--cpus") + 1] == "2"
    run = sbx.calls[-1]
    assert run[:2] == ["exec", vm.name] and run[2:6] == ["timeout", "-s", "KILL", "60"] and run[-1] == "echo hello"
    assert not list(sbx.root.glob(".agent-studio-probe-*"))              # the marker never stays behind
    n = len([c for c in sbx.calls if c[0] == "create"])
    vm.exec("again", timeout_s=99999)
    assert len([c for c in sbx.calls if c[0] == "create"]) == n and sbx.calls[-1][5] == "600"          # exists: not created again; time is capped


def test_microvm_refuses_when_it_cannot_be_trusted(sbx):
    with pytest.raises(SandboxUnavailable, match="switched off"):
        MV.MicroVmSandbox(sbx.root).exec("x")
    vm = MV.MicroVmSandbox(sbx.root, enabled=True)
    sbx.sees_workspace = False
    with pytest.raises(SandboxUnavailable, match="cannot see the workspace folder"):
        vm.exec("x")
    assert not list(sbx.root.glob(".agent-studio-probe-*"))
    sbx.sees_workspace = True
    sbx.fail["create"] = cp(1, err="quota")
    sbx.sandboxes.clear()
    with pytest.raises(SandboxUnavailable, match="could not create the sandbox: quota"):
        vm.exec("x")
    del sbx.fail["create"]
    sbx.fail["ls"] = cp(1, err="no daemon")
    with pytest.raises(SandboxUnavailable, match="cannot list sandboxes: no daemon"):
        vm.exec("x")
    sbx.fail["ls"] = cp(0, "not json")
    with pytest.raises(SandboxUnavailable, match="cannot read"):
        vm.exec("x")
    sbx.fail["ls"] = cp(0, json.dumps({"sandboxes": [{"NAME": vm.name}]}))        # the dict form and the upper-case key are understood
    assert vm._exists()
    sbx.fail["ls"] = cp(0, "")
    assert not vm._exists()


def test_microvm_timeout_and_stop(sbx):
    vm = MV.MicroVmSandbox(sbx.root, enabled=True)
    vm.exec("warm up")
    orig = sbx.__call__

    def slow(args, timeout):
        if args[:2] == ["exec", vm.name] and "bash" in args:
            raise SandboxUnavailable("sbx did not answer in time")
        return orig(args, timeout)
    MV.SBX_RUN = slow
    assert vm.exec("sleep 99", 1)["timed_out"] is True
    MV.SBX_RUN = orig
    vm.stop()
    assert sbx.calls[-1] == ["rm", vm.name]
    sbx.fail["rm"] = SandboxUnavailable("gone")
    vm.stop()                                                            # a failing remove is not an error


def test_microvm_output_is_clipped_and_kills_are_reported(sbx):
    vm = MV.MicroVmSandbox(sbx.root, enabled=True)
    big = "y" * (H.MAX_OUT * 2)
    orig = sbx.__call__
    MV.SBX_RUN = lambda args, timeout: cp(137, big, "") if "bash" in args else orig(args, timeout)
    r = vm.exec("yes")
    MV.SBX_RUN = orig
    assert r["timed_out"] and r["truncated"] and len(r["stdout"]) < len(big)


def test_the_real_sbx_helper(monkeypatch):
    monkeypatch.setattr(MV, "SBX_RUN", None)
    monkeypatch.setattr(MV.shutil, "which", lambda n: None)
    with pytest.raises(SandboxUnavailable, match="sbx command was not found"):
        MV.sbx(["version"])
    monkeypatch.setattr(MV.shutil, "which", lambda n: "/bin/sbx")
    monkeypatch.setattr(MV.subprocess, "run", lambda *a, **k: cp(0, "v1"))
    assert MV.sbx(["version"]).stdout == "v1"

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("sbx", 1)
    monkeypatch.setattr(MV.subprocess, "run", slow)
    with pytest.raises(SandboxUnavailable, match="did not answer in time"):
        MV.sbx(["version"])


# ------------------------------------------------------------------------------------------------------------------ factory
def test_factory_policy_persists_and_rejects_bad_values(state):
    f = SF.SandboxFactory(state.db)
    assert f.policy() == SB.DEFAULTS
    assert f.set_policy({"memory": "2g", "pids": 128})["memory"] == "2g" and f.policy()["pids"] == 128
    for bad, msg in [("x", "must be an object"), ({"colour": 1}, "unknown sandbox setting"), ({"pids": 1}, "between 32 and 4096")]:
        with pytest.raises(ValueError, match=msg):
            f.set_policy(bad)
    assert f.policy()["pids"] == 128
    state.db.set_setting(SF.CONFIG_KEY, "{broken")
    assert f.policy() == SB.DEFAULTS
    state.db.set_setting(SF.CONFIG_KEY, json.dumps({"memory": "nonsense"}))
    assert f.policy() == SB.DEFAULTS


def test_factory_makes_the_kind_the_agent_or_the_default_asks_for(state, tmp_path):
    f = SF.SandboxFactory(state.db)
    box = f.make(tmp_path)
    assert isinstance(box, H.Sandbox) and (box.memory, box.cpus, box.pids, box.read_only, box.network) == ("1g", "2", 256, False, False)
    f.set_policy({"memory": "512m", "cpus": 1, "pids": 64, "read_only_root": True})
    state.db.set_setting("sandbox_network", "1")
    box = f.make(tmp_path, "docker-container")
    assert (box.memory, box.cpus, box.pids, box.read_only, box.network) == ("512m", "1", 64, True, True)
    vm = f.make(tmp_path, "docker-microvm")
    assert isinstance(vm, MV.MicroVmSandbox) and vm.kind == "docker-microvm" and not vm.enabled and vm.network
    f.set_policy({"kind": "docker-microvm", "microvm_enabled": True})
    assert isinstance(f.make(tmp_path), MV.MicroVmSandbox) and f.make(tmp_path).enabled
    with pytest.raises(SandboxUnavailable, match="unknown sandbox kind"):
        f.make(tmp_path, "vm")


def test_the_chat_picks_the_sandbox_from_the_lead_agent(state, tmp_path):
    ws = tmp_path / "w"
    assert isinstance(state.chat.sandbox_for(ws), H.Sandbox)
    assert isinstance(state.chat.sandbox_for(ws, AgentDef(id="a", name="a")), H.Sandbox)
    assert isinstance(state.chat.sandbox_for(ws, AgentDef(id="a", name="a", sandbox="docker-microvm")), MV.MicroVmSandbox)


def test_status_lists_both_kinds(state, monkeypatch):
    f = SF.SandboxFactory(state.db)
    monkeypatch.setattr(SF, "docker_status", lambda: {"docker": True, "image": False, "detail": "image missing: docker build"})
    monkeypatch.setattr(SF.microvm, "status", lambda: {"available": True, "version": "v", "detail": "ready"})
    st = f.status()
    k = {x["id"]: x for x in st["kinds"]}
    assert st["default"] == "docker-container" and not st["network"] and not k["docker-container"]["available"] and "image missing" in k["docker-container"]["detail"]
    assert not k["docker-microvm"]["available"] and "switched off" in k["docker-microvm"]["detail"] and k["docker-microvm"]["experimental"]
    f.set_policy({"microvm_enabled": True})
    monkeypatch.setattr(SF, "docker_status", lambda: {"docker": True, "image": True, "detail": "ready"})
    k = {x["id"]: x for x in f.status()["kinds"]}
    assert k["docker-container"]["available"] and k["docker-microvm"]["available"] and k["docker-microvm"]["detail"] == "ready"
    monkeypatch.setattr(SF.microvm, "status", lambda: {"available": False, "detail": "no hypervisor"})
    assert not {x["id"]: x for x in f.status()["kinds"]}["docker-microvm"]["available"]


def test_the_test_button_runs_a_command_or_explains(state, monkeypatch):
    f = SF.SandboxFactory(state.db)
    assert f.test("vm") == {"ok": False, "detail": "unknown sandbox kind 'vm'"}

    class Box:
        def __init__(self, out):
            self.out = out

        def exec(self, command, timeout_s):
            if isinstance(self.out, Exception):
                raise self.out
            return self.out
    monkeypatch.setattr(f, "make", lambda ws, kind: Box({"exit_code": 0, "stdout": "agent-studio-ok\n", "stderr": ""}))
    r = f.test("docker-container")
    assert r["ok"] and "ran a command" in r["detail"] and "ms" in r
    monkeypatch.setattr(f, "make", lambda ws, kind: Box({"exit_code": 1, "stdout": "", "stderr": "boom"}))
    assert f.test("docker-container")["detail"] == "the command failed: boom"
    monkeypatch.setattr(f, "make", lambda ws, kind: Box(SandboxUnavailable("docker is not running")))
    assert f.test("docker-container") == {"ok": False, "detail": "docker is not running"}


def test_api(client, monkeypatch):
    monkeypatch.setattr(SF, "docker_status", lambda: {"docker": False, "image": False, "detail": "Docker daemon not running"})
    monkeypatch.setattr(SF.microvm, "status", lambda: {"available": False, "detail": "no sbx"})
    st = client.get("/api/sandboxes").json()
    assert [k["id"] for k in st["kinds"]] == ["docker-container", "docker-microvm"] and st["policy"]["memory"] == "1g"
    out = client.put("/api/sandboxes/config", json={"memory": "4g", "microvm_enabled": True}).json()
    assert out["policy"]["memory"] == "4g" and out["policy"]["microvm_enabled"]
    assert client.put("/api/sandboxes/config", json={"memory": "huge"}).status_code == 422
    assert client.post("/api/sandboxes/vm/test").json()["ok"] is False
    assert client.put("/api/agents/engineer", json={"sandbox": "docker-microvm"}).json()["sandbox"] == "docker-microvm"
    assert client.put("/api/agents/engineer", json={"sandbox": "vm"}).status_code == 422
    assert client.put("/api/agents/engineer", json={"sandbox": ""}).json()["sandbox"] == ""
