"""The Docker-managed vector stores, tested with a fake `docker` command (no containers are started)."""
import subprocess
from dataclasses import replace

import pytest

from app.rag import stack as S


def cp(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


class FakeDocker:
    """Tracks one container's state through docker run/start/stop/rm."""

    def __init__(self, state="missing", up=True):
        self.state, self.up, self.fail, self.calls = state, up, set(), []

    def __call__(self, args, timeout=30):
        self.calls.append(args)
        cmd = args[0]
        if cmd == "version":
            return cp(0 if self.up else 1, "29.0.0\n")
        if cmd == "inspect":
            return cp(1, "", "No such object") if self.state == "missing" else cp(0, "running\n" if self.state == "running" else "exited\n")
        if cmd in self.fail:
            return cp(1, "", "Unable to find image\ndocker: Error response: port is already allocated")
        self.state = {"run": "running", "start": "running", "stop": "exited", "rm": "missing"}.get(cmd, self.state)
        return cp(0, "ok")


@pytest.fixture
def docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(S, "_docker", fake)
    return fake


# ------------------------------------------------------------------ plumbing
def test_docker_runs_the_cli_and_reports_a_missing_cli(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return cp(0, "hi")
    monkeypatch.setattr(S.shutil, "which", lambda name: "C:/docker.exe")
    monkeypatch.setattr(S.subprocess, "run", fake_run)
    assert S._docker(["ps"], timeout=5).stdout == "hi" and seen["cmd"] == ["C:/docker.exe", "ps"] and seen["kw"]["timeout"] == 5
    monkeypatch.setattr(S.shutil, "which", lambda name: None)
    with pytest.raises(S.StackError, match="not installed"):
        S._docker(["ps"])


def test_a_hung_docker_is_reported(monkeypatch):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(S.shutil, "which", lambda name: "docker")
    monkeypatch.setattr(S.subprocess, "run", slow)
    with pytest.raises(S.StackError, match="did not respond"):
        S._docker(["ps"])


def test_why_uses_the_last_line_docker_printed():
    assert S._why(cp(1, "", "first\nthe real reason")) == "the real reason"
    assert S._why(cp(1, "only stdout", "")) == "only stdout"
    assert S._why(cp(1, "", "")) == "docker failed"


def test_unknown_stacks_are_refused(docker):
    for fn in (S.status, S.start, S.stop, S.remove):
        with pytest.raises(S.StackError, match="unknown stack 'nope'"):
            fn("nope")


# -------------------------------------------------------------------- status
def test_status_for_each_container_state(docker):
    assert S.status("qdrant")["state"] == "missing" and "not created" in S.status("qdrant")["detail"]
    docker.state = "running"
    st = S.status("qdrant")
    assert st["state"] == "running" and st["docker"] and st["detail"] == "running on 127.0.0.1:6333" and st["image"] == "qdrant/qdrant:v1.20.0"
    docker.state = "exited"
    assert S.status("qdrant")["state"] == "stopped" and "data is kept" in S.status("qdrant")["detail"]


def test_status_when_docker_is_off_or_missing(docker, monkeypatch):
    docker.up = False
    assert S.status("qdrant") == {**S.status("qdrant"), "docker": False, "state": "unavailable"} and "start Docker Desktop" in S.status("qdrant")["detail"]

    def gone(args, timeout=30):
        raise S.StackError("Docker is not installed or not on PATH")
    monkeypatch.setattr(S, "_docker", gone)
    st = S.status("qdrant")
    assert st["docker"] is False and "not installed" in st["detail"]


def test_available_lists_every_stack(docker):
    assert [s["id"] for s in S.available()] == list(S.STACKS)


# ------------------------------------------------------------- start / stop
def test_first_start_creates_the_container_with_a_named_volume_on_localhost(docker):
    st = S.start("qdrant")
    run = next(c for c in docker.calls if c[0] == "run")
    assert st["state"] == "running"
    assert ["-p", "127.0.0.1:6333:6333"] == run[run.index("-p"):run.index("-p") + 2]
    assert ["-v", "agent-studio-qdrant:/qdrant/storage"] == run[run.index("-v"):run.index("-v") + 2]
    assert "--restart" in run and "agent-studio=rag" in run and run[-1] == "qdrant/qdrant:v1.20.0"


def test_start_passes_env_secrets_and_command(docker, monkeypatch):
    spec = replace(S.STACKS["qdrant"], env={"A": "1"}, secrets={"PASS": "pw"}, cmd=("serve", "--x"))
    monkeypatch.setitem(S.STACKS, "qdrant", spec)
    S.start("qdrant")
    run = next(c for c in docker.calls if c[0] == "run")
    assert run.count("-e") == 2 and "A=1" in run and "PASS=pw" in run and run[-2:] == ["serve", "--x"]


def test_start_restarts_a_stopped_container_and_leaves_a_running_one_alone(docker):
    docker.state = "exited"
    assert S.start("qdrant")["state"] == "running" and ["start", "agent-studio-qdrant"] in docker.calls
    docker.calls.clear()
    S.start("qdrant")
    assert not [c for c in docker.calls if c[0] in ("run", "start")]


def test_start_failures_say_why(docker):
    docker.fail = {"run"}
    with pytest.raises(S.StackError, match="port is already allocated"):
        S.start("qdrant")
    docker.state, docker.fail = "exited", {"start"}
    with pytest.raises(S.StackError, match="port is already allocated"):
        S.start("qdrant")


def test_stop_stops_only_a_running_container(docker):
    docker.state = "running"
    assert S.stop("qdrant")["state"] == "stopped"
    docker.calls.clear()
    S.stop("qdrant")
    assert not [c for c in docker.calls if c[0] == "stop"]
    docker.state, docker.fail = "running", {"stop"}
    with pytest.raises(S.StackError):
        S.stop("qdrant")


def test_remove_keeps_the_data_unless_asked(docker):
    docker.state = "running"
    assert S.remove("qdrant")["state"] == "missing"
    assert ["rm", "-f", "agent-studio-qdrant"] in docker.calls and not [c for c in docker.calls if c[0] == "volume"]
    S.remove("qdrant", delete_data=True)
    assert ["volume", "rm", "-f", "agent-studio-qdrant"] in docker.calls


def test_remove_failure_is_reported(docker):
    docker.state, docker.fail = "running", {"rm"}
    with pytest.raises(S.StackError):
        S.remove("qdrant")
