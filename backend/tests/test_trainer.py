"""Model trainer (app/trainer.py): isolated-environment install flow, job lifecycle (start / poll / cancel / evaluate), dataset safety, API."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app import trainer as T

TODAY = date(2026, 11, 22)


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAINER_HOME", str(tmp_path / "envs"))
    monkeypatch.setenv("TRAINER_JOBS", str(tmp_path / "jobs"))
    monkeypatch.setattr(T, "free_disk_gb", lambda p: 500.0)


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    (w / "data.csv").write_text("age,plan,churned\n30,a,1\n40,b,0\n", encoding="utf-8")
    return w


class Runner:
    """Stands in for pip/venv: records every command, creates the env's python on the venv step, prints a VERSION line on the check."""

    def __init__(self, fail_step: int | None = None, version: bool = True, gate: threading.Event | None = None):
        self.cmds, self.fail_step, self.version, self.gate = [], fail_step, version, gate

    def __call__(self, cmd, env, on_line):
        if self.gate is not None:
            self.gate.wait(5)
        i = len(self.cmds)
        self.cmds.append((cmd, env))
        if i == self.fail_step:
            on_line("ERROR: no matching distribution")
            return 1
        if "venv" in cmd:
            fw = Path(cmd[-1]).parent.name
            py = T.env_python(fw)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("", encoding="utf-8")
        if i == 3 and self.version:
            on_line("VERSION 2.15.0")
        return 0


def trainer(**kw) -> T.Trainer:
    kw.setdefault("runner", Runner())
    kw.setdefault("today", lambda: TODAY)
    return T.Trainer(**kw)


def install(t: T.Trainer, fw="pytorch", **kw):
    t.start_install(fw, kw.pop("accepted", True), **kw)
    t.thread.join(10)


def mark_ready(fw: str) -> None:
    py = T.env_python(fw)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="utf-8")
    st = T.read_state()
    st.setdefault("frameworks", {})[fw] = dict(state="ready", version="1.0")
    T._write_json(T.home() / "state.json", st)


# ----------------------------------------------------------------------------------------------------------- the install
def test_plan_describes_size_licence_and_cpu_build():
    p = trainer().plan("ludwig")
    assert p["label"] == "Ludwig" and p["download_mb"] == 3500 and "Apache" in p["licence"] and p["packages"][0] == "ludwig==0.17.10"
    assert trainer(platform="linux").plan("pytorch", "cpu")["cpu_build"] is True
    assert trainer(platform="win32").plan("pytorch", "cpu")["cpu_build"] is False
    assert trainer().plan("keras")["disk_gb_needed"] == 2.0
    with pytest.raises(T.TrainerError, match="unknown framework"):
        trainer().plan("jax")


def test_frameworks_are_pinned_exactly():
    for fw in T.FRAMEWORKS.values():
        assert all("==" in p for p in fw["packages"]), fw["label"]


def test_install_refuses_without_licence_and_with_bad_device_or_little_disk(monkeypatch):
    t = trainer()
    with pytest.raises(T.TrainerError, match="Accept the PyTorch licence"):
        t.start_install("pytorch", False)
    with pytest.raises(T.TrainerError, match="device must be"):
        t.start_install("pytorch", True, "gpu")
    monkeypatch.setattr(T, "free_disk_gb", lambda p: 0.2)
    with pytest.raises(T.TrainerError, match="not enough free disk space"):
        t.start_install("pytorch", True)
    assert not T.is_ready("pytorch")


def test_install_runs_the_four_steps_with_the_14_day_cutoff():
    r = Runner()
    t = trainer(runner=r, platform="linux")
    install(t, "pytorch", device="cpu")
    labels = [c[0] for c in r.cmds]
    assert labels[0][1:3] == ["-m", "venv"] and "--clear" in labels[0]
    assert labels[1][-1].startswith("pip==")
    pip = labels[2]
    assert pip[1:4] == ["-m", "pip", "install"]
    assert "--only-binary=:all:" in pip and pip[pip.index("--uploaded-prior-to") + 1] == "2026-11-08T00:00:00Z"
    assert "--extra-index-url" in pip and "torch==2.15.0" in pip
    assert labels[3][1] == "-c" and "import torch" in labels[3][2]
    env = r.cmds[2][1]
    assert env["PIP_NO_INPUT"] == "1" and "HF_TOKEN" not in env and "PYTHONPATH" not in env
    st = t.status()
    fw = {f["id"]: f for f in st["frameworks"]}["pytorch"]
    assert fw["state"] == "ready" and fw["version"] == "2.15.0" and T.is_ready("pytorch") and T.ready_frameworks() == ["pytorch"]
    assert st["install"]["running"] is False and any("Install PyTorch" in ln for ln in st["install"]["log"])
    with pytest.raises(T.TrainerError, match="already installed"):
        t.start_install("pytorch", True)


def test_install_cpu_build_is_only_for_linux_pytorch():
    r = Runner()
    install(trainer(runner=r, platform="win32"), "pytorch", device="cpu")
    assert all("--extra-index-url" not in c[0] for c in r.cmds)


def test_failed_install_is_reported_and_can_be_retried():
    t = trainer(runner=Runner(fail_step=2))
    install(t, "keras")
    fw = {f["id"]: f for f in t.status()["frameworks"]}["keras"]
    assert fw["state"] == "failed" and "exit code 1" in fw["error"] and "no matching distribution" in fw["error"]
    assert not T.is_ready("keras")
    t2 = trainer(runner=Runner())
    install(t2, "keras")
    assert T.is_ready("keras")


def test_install_without_a_readable_version_fails():
    t = trainer(runner=Runner(version=False))
    install(t, "ludwig")
    assert "version could not be read" in {f["id"]: f for f in t.status()["frameworks"]}["ludwig"]["error"]


def test_only_one_install_at_a_time_and_status_shows_installing():
    gate = threading.Event()
    t = trainer(runner=Runner(gate=gate))
    t.start_install("pytorch", True)
    st = t.status()
    assert {f["id"]: f for f in st["frameworks"]}["pytorch"]["state"] == "installing" and st["install"]["running"] and st["install"]["framework"] == "pytorch"
    with pytest.raises(T.TrainerError, match="already running"):
        t.start_install("keras", True)
    with pytest.raises(T.TrainerError, match="being installed right now"):
        t.uninstall("pytorch")
    gate.set()
    t.thread.join(10)


def test_an_interrupted_install_shows_as_failed_after_a_restart():
    T._write_json(T.home() / "state.json", {"frameworks": {"keras": {"state": "installing"}}})
    fw = {f["id"]: f for f in trainer().status()["frameworks"]}["keras"]
    assert fw["state"] == "failed"


def test_uninstall():
    mark_ready("keras")
    st = trainer().uninstall("keras")
    assert {f["id"]: f for f in st["frameworks"]}["keras"]["state"] == "not_installed"
    assert not T.is_ready("keras") and not (T.home() / "keras").exists()
    with pytest.raises(T.TrainerError, match="unknown framework"):
        trainer().uninstall("jax")


# ---------------------------------------------------------------------------------------------------------------- datasets
def test_dataset_must_be_a_csv_inside_the_workspace(ws, tmp_path):
    p, cols = T.Trainer.check_dataset("data.csv", ws)
    assert p == (ws / "data.csv").resolve() and cols == ["age", "plan", "churned"]
    assert T.Trainer.check_dataset(str(ws / "data.csv"), ws)[1] == cols
    outside = tmp_path / "elsewhere.csv"
    outside.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(T.TrainerError, match="inside the workspace"):
        T.Trainer.check_dataset(str(outside), ws)
    with pytest.raises(T.TrainerError, match="inside the workspace"):
        T.Trainer.check_dataset("../elsewhere.csv", ws)
    with pytest.raises(T.TrainerError, match="not found or not a .csv"):
        T.Trainer.check_dataset("missing.csv", ws)
    (ws / "t.txt").write_text("a,b", encoding="utf-8")
    with pytest.raises(T.TrainerError, match="not a .csv"):
        T.Trainer.check_dataset("t.txt", ws)
    (ws / "one.csv").write_text("only\n1\n", encoding="utf-8")
    with pytest.raises(T.TrainerError, match="at least two columns"):
        T.Trainer.check_dataset("one.csv", ws)
    (ws / "bin.csv").write_bytes(b"\xff\xfe\x00bad,\xff\n")
    with pytest.raises(T.TrainerError, match="cannot read the dataset"):
        T.Trainer.check_dataset("bin.csv", ws)
    assert T.Trainer.check_dataset(str(outside), None)[1] == ["a", "b"]               # no workspace configured: no containment check


# ---------------------------------------------------------------------------------------------------------------- the jobs
def spec(**kw):
    return dict(framework="pytorch", dataset="data.csv", target="churned", **kw)


def test_train_validation_messages(ws):
    t = trainer()
    with pytest.raises(T.TrainerError, match="framework must be one of"):
        t.validate_train(dict(spec(), framework="jax"), ws)
    with pytest.raises(T.TrainerError, match="PyTorch is not installed"):
        t.validate_train(spec(), ws)
    mark_ready("pytorch")
    for bad, msg in [(dict(task="clustering"), "task must be"), (dict(target="nope"), "target column 'nope' is not in the CSV"),
                     (dict(epochs=0), "epochs must be"), (dict(epochs=10_000), "epochs must be"), (dict(max_minutes=0.1), "max_minutes"),
                     (dict(hidden=[1] * 9), "hidden must be"), (dict(hidden=[0]), "hidden must be"),
                     (dict(config={"a": 1}), "only for framework=ludwig")]:
        with pytest.raises(T.TrainerError, match=msg):
            t.validate_train({**spec(), **bad}, ws)
    ok = t.validate_train(spec(test_size=0.9, name="x" * 99), ws)
    assert ok["test_size"] == 0.5 and ok["epochs"] == 20 and ok["hidden"] == [64, 32] and len(ok["name"]) == 60 and ok["dataset"].endswith("data.csv")
    mark_ready("ludwig")
    cfg = t.validate_train(dict(spec(), framework="ludwig", config={"input_features": []}), ws)
    assert cfg["config"] == {"input_features": []}
    with pytest.raises(T.TrainerError, match="must be an object"):
        t.validate_train(dict(spec(), framework="ludwig", config=[1]), ws)
    ok2 = trainer().validate_train(dict(spec(), test_size=0.01), ws)
    assert ok2["test_size"] == 0.05


class Fakes:
    def __init__(self):
        self.spawned, self.killed, self.live = [], [], True

    def spawn(self, cmd, env, log):
        self.spawned.append((cmd, env, log))
        log.write_text("starting\nepoch 1/2 loss=0.5\n", encoding="utf-8")
        return 4242

    def kill(self, pid):
        self.killed.append(pid)

    def alive(self, pid):
        return self.live


def jtrainer(f: Fakes) -> T.Trainer:
    return trainer(spawn=f.spawn, kill=f.kill, alive=f.alive)


def test_start_train_spawns_the_worker_in_the_framework_environment(ws):
    mark_ready("pytorch")
    f = Fakes()
    t = jtrainer(f)
    job = t.start_train(spec(epochs=5, name="churn"), ws)
    cmd, env, log = f.spawned[0]
    assert cmd[0] == str(T.env_python("pytorch")) and cmd[1:3] == ["-m", "app.trainer_worker"] and Path(cmd[3]).name == job["id"]
    assert env["PYTHONPATH"] == str(T.BACKEND_DIR) and "HF_TOKEN" not in env
    d = T.jobs_dir() / job["id"]
    assert json.loads((d / "spec.json").read_text())["epochs"] == 5 and json.loads((d / "status.json").read_text())["pid"] == 4242
    assert job["state"] == "starting" and job["mode"] == "train" and job["framework"] == "pytorch" and job["name"] == "churn" and job["epochs"] == 5
    assert t.job(job["id"], tail=1)["log"] == ["epoch 1/2 loss=0.5"]
    assert t.job(job["id"])["state"] == "starting" and "log" not in t.job(job["id"])


def test_job_reflects_the_workers_status_and_metrics(ws):
    mark_ready("pytorch")
    f = Fakes()
    t = jtrainer(f)
    jid = t.start_train(spec(), ws)["id"]
    d = T.jobs_dir() / jid
    T._write_json(d / "status.json", dict(state="running", pid=4242, epoch=3, progress=0.3))
    j = t.job(jid)
    assert j["state"] == "running" and j["epoch"] == 3 and j["progress"] == 0.3 and j["metrics"] is None
    T._write_json(d / "status.json", dict(state="done", pid=4242, artifact="model.pt"))
    T._write_json(d / "metrics.json", dict(accuracy=0.9))
    j = t.job(jid)
    assert j["state"] == "done" and j["metrics"] == {"accuracy": 0.9} and j["artifact"] == "model.pt" and j["folder"].endswith(jid)
    assert f.killed == []


def test_a_dead_worker_marks_the_job_failed(ws):
    mark_ready("pytorch")
    f = Fakes()
    t = jtrainer(f)
    jid = t.start_train(spec(), ws)["id"]
    f.live = False
    j = t.job(jid)
    assert j["state"] == "failed" and "worker stopped unexpectedly" in j["error"]
    assert t.job(jid)["state"] == "failed"                                                   # persisted


def test_a_job_over_its_time_limit_is_killed(ws):
    mark_ready("pytorch")
    f = Fakes()
    t = jtrainer(f)
    jid = t.start_train(spec(max_minutes=1), ws)["id"]
    d = T.jobs_dir() / jid
    s = json.loads((d / "spec.json").read_text())
    s["created"] = time.time() - 3600
    T._write_json(d / "spec.json", s)
    j = t.job(jid)
    assert j["state"] == "failed" and "minute limit" in j["error"] and f.killed == [4242]


def test_cancel(ws):
    mark_ready("pytorch")
    f = Fakes()
    t = jtrainer(f)
    jid = t.start_train(spec(), ws)["id"]
    j = t.cancel(jid)
    assert j["state"] == "cancelled" and f.killed == [4242]
    with pytest.raises(T.TrainerError, match="is not running"):
        t.cancel(jid)
    jid2 = t.start_train(spec(), ws)["id"]                                                     # a job without a pid yet
    T._write_json(T.jobs_dir() / jid2 / "status.json", dict(state="running"))
    assert t.cancel(jid2)["state"] == "cancelled" and f.killed == [4242]


def test_unknown_and_malformed_job_ids_are_refused():
    t = trainer()
    for bad in ("", "../../etc", "zzzzzzzzzz", "short"):
        with pytest.raises(T.TrainerError):
            t.job(bad)
    assert t.jobs() == []


def test_jobs_are_listed_newest_first_and_uninstall_is_blocked_while_one_runs(ws):
    mark_ready("pytorch")
    f = Fakes()
    t = jtrainer(f)
    a = t.start_train(spec(name="first"), ws)["id"]
    time.sleep(0.02)
    b = t.start_train(spec(name="second"), ws)["id"]
    (T.jobs_dir() / "not-a-job").mkdir()
    assert [j["id"] for j in t.jobs()] == [b, a] and [j["id"] for j in t.jobs(limit=1)] == [b]
    assert t.status()["jobs_running"] == 2
    T._write_json(T.jobs_dir() / a / "status.json", dict(state="running", pid=1))
    with pytest.raises(T.TrainerError, match="still running"):
        t.uninstall("pytorch")
    for j in (a, b):
        T._write_json(T.jobs_dir() / j / "status.json", dict(state="done"))
    assert t.uninstall("pytorch")["jobs_running"] == 0 and not T.is_ready("pytorch")


def test_evaluate_needs_a_finished_training_job_and_the_target_column(ws):
    mark_ready("pytorch")
    f = Fakes()
    t = jtrainer(f)
    jid = t.start_train(spec(), ws)["id"]
    with pytest.raises(T.TrainerError, match="finished training job"):
        t.start_evaluate(jid, "data.csv", ws)
    T._write_json(T.jobs_dir() / jid / "status.json", dict(state="done", pid=1))
    (ws / "other.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    with pytest.raises(T.TrainerError, match="needs the target column 'churned'"):
        t.start_evaluate(jid, "other.csv", ws)
    ev = t.start_evaluate(jid, "data.csv", ws)
    assert ev["mode"] == "evaluate" and ev["framework"] == "pytorch" and ev["name"] == f"evaluate {jid}"
    s = json.loads((T.jobs_dir() / ev["id"] / "spec.json").read_text())
    assert s["model_job"] == jid and s["target"] == "churned"
    with pytest.raises(T.TrainerError, match="finished training job"):
        t.start_evaluate(ev["id"], "data.csv", ws)                                              # an evaluate job is not a model


# ------------------------------------------------------------------------------------------------- real process helpers
def test_real_spawn_alive_and_kill(tmp_path):
    log = tmp_path / "log.txt"
    pid = T._spawn([sys.executable, "-c", "import time; print('hi', flush=True); time.sleep(60)"], dict(os.environ), log)
    assert pid > 0 and T._alive(pid)
    T._kill(pid)
    for _ in range(50):
        if not T._alive(pid):
            break
        time.sleep(0.1)
    assert not T._alive(pid)
    assert T._alive(2_000_000_000) is False


def test_posix_branches_of_the_process_helpers(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    seen = {}
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.update(kw) or NS(pid=77))
    assert T._spawn(["x"], {}, tmp_path / "l") == 77 and seen["start_new_session"] is True
    calls = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append(("kill", pid, sig)), raising=False)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid + 1, raising=False)
    monkeypatch.setattr(os, "killpg", lambda pg, sig: calls.append(("killpg", pg, sig)), raising=False)
    assert T._alive(5) is True
    T._kill(5)
    assert calls == [("kill", 5, 0), ("killpg", 6, T.signal.SIGTERM)]

    def gone(pid, sig):
        raise OSError("no such process")
    monkeypatch.setattr(os, "kill", gone, raising=False)
    monkeypatch.setattr(os, "killpg", gone, raising=False)
    assert T._alive(5) is False
    T._kill(5)                                                                                   # already gone: no error


def test_json_helpers_tolerate_missing_and_broken_files(tmp_path):
    assert T._read_json(tmp_path / "nope.json") == {}
    (tmp_path / "bad.json").write_text("[1, 2]", encoding="utf-8")
    assert T._read_json(tmp_path / "bad.json") == {}
    (tmp_path / "bad2.json").write_text("{oops", encoding="utf-8")
    assert T._read_json(tmp_path / "bad2.json") == {}


# ---------------------------------------------------------------------------------------------------------------------- API
def test_api_status_plan_install_and_jobs(client, ws):
    r = client.get("/api/trainer/status").json()
    assert [f["id"] for f in r["frameworks"]] == ["ludwig", "pytorch", "keras"] and all(f["state"] == "not_installed" for f in r["frameworks"])
    assert client.get("/api/trainer/plan/ludwig").json()["label"] == "Ludwig"
    assert client.get("/api/trainer/plan/jax").status_code == 422
    assert client.post("/api/trainer/install", json={"framework": "pytorch"}).status_code == 422                  # licence not accepted
    assert client.post("/api/trainer/uninstall", json={"framework": "jax"}).status_code == 422
    assert client.get("/api/trainer/jobs").json() == []
    assert client.get("/api/trainer/jobs/zzzzzzzzzz").status_code == 422
    assert client.post("/api/trainer/jobs/zzzzzzzzzz/cancel").status_code == 422


def test_api_job_detail_cancel_and_uninstall(client, ws):
    mark_ready("pytorch")
    t: T.Trainer = client.app.state.trainer
    f = Fakes()
    t._spawn, t._kill, t._alive = f.spawn, f.kill, f.alive
    jid = t.start_train(spec(), ws)["id"]
    d = client.get(f"/api/trainer/jobs/{jid}?tail=5").json()
    assert d["id"] == jid and d["log"] == ["starting", "epoch 1/2 loss=0.5"]
    assert "log" not in client.get(f"/api/trainer/jobs/{jid}?tail=-3").json()
    (T.jobs_dir() / jid / "log.txt").unlink()
    assert client.get(f"/api/trainer/jobs/{jid}").json()["log"] == []
    assert [j["id"] for j in client.get("/api/trainer/jobs").json()] == [jid]
    assert client.post(f"/api/trainer/jobs/{jid}/cancel").json()["state"] == "cancelled"
    assert client.post("/api/trainer/uninstall", json={"framework": "pytorch"}).status_code == 200


def test_api_install_runs_in_the_background(client):
    t: T.Trainer = client.app.state.trainer
    t.runner = Runner()
    t.today = lambda: TODAY
    r = client.post("/api/trainer/install", json={"framework": "keras", "accepted": True})
    assert r.status_code == 200
    t.thread.join(10)
    assert {f["id"]: f for f in client.get("/api/trainer/status").json()["frameworks"]}["keras"]["state"] == "ready"


# ------------------------------------------------------------------------------------------------- the built-in MCP entry
def test_trainer_mcp_entry_resolves_its_folders_and_needs_a_framework(state, tmp_path):
    import asyncio
    from app import mcp_registry as R
    assert R._subst("{trainer_home}", "") == str(T.home()) and R._subst("{trainer_jobs}", "") == str(T.jobs_dir())
    s = state.mcp.get("trainer")
    assert s.env["TRAINER_HOME"] == "{trainer_home}" and s.setup_missing() and not s.ready()
    state.mcp.update("trainer", enabled=True)
    assert not state.mcp.get("trainer").ready()
    warns: list[str] = []
    assert asyncio.run(R.build_toolsets(state.mcp, ["trainer"], str(tmp_path), warns)) == []
    assert "Catalog > ML tools" in warns[0]
    mark_ready("pytorch")
    assert state.mcp.get("trainer").ready()
    cloud = state.mcp.get("cloudml")
    assert not cloud.enabled and cloud.args == ["-m", "app.mcp_cloudml"] and cloud.requires_env == []
