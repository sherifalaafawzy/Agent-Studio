"""The trainer MCP server (app/mcp_trainer.py): tool behaviour over a Trainer with fake processes, and the real stdio process."""
from __future__ import annotations

import asyncio
import json
import os
import runpy
import sys

import pytest

from app import mcp_trainer as M
from app import trainer as T
from tests.test_trainer import Fakes, jtrainer, mark_ready


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAINER_HOME", str(tmp_path / "envs"))
    monkeypatch.setenv("TRAINER_JOBS", str(tmp_path / "jobs"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "data.csv").write_text("age,plan,churned\n30,a,1\n40,b,0\n", encoding="utf-8")
    monkeypatch.setenv("TRAINER_WORKSPACE", str(ws))
    f = Fakes()
    monkeypatch.setattr(M, "_trainer", jtrainer(f))
    return f


def test_status_lists_frameworks_and_recent_jobs():
    s = M.status()
    assert s["ok"] and [f["id"] for f in s["frameworks"]] == ["ludwig", "pytorch", "keras"] and all(f["state"] == "not_installed" for f in s["frameworks"])
    assert s["recent_jobs"] == [] and s["jobs_running"] == 0


def test_templates_explain_the_inputs():
    t = M.templates()
    assert t["ok"] and set(t["templates"]) == {"classification", "regression", "ludwig_config"} and any("workspace" in n for n in t["notes"])


def test_train_job_evaluate_cancel_flow(dirs):
    r = M.train("pytorch", "data.csv", "churned")
    assert not r["ok"] and "not installed" in r["error"]
    mark_ready("pytorch")
    r = M.train("pytorch", "data.csv", "churned", epochs=3, hidden=[4], name="demo")
    assert r["ok"] and r["job"]["state"] == "starting" and r["job"]["epochs"] == 3
    jid = r["job"]["id"]
    assert dirs.spawned and M.job(jid, tail=1)["job"]["log"] == ["epoch 1/2 loss=0.5"]
    assert M.jobs()["jobs"][0]["id"] == jid and M.status()["recent_jobs"][0]["name"] == "demo"
    assert not M.evaluate(jid, "data.csv")["ok"]                                         # not finished yet
    T._write_json(T.jobs_dir() / jid / "status.json", dict(state="done", pid=1))
    ev = M.evaluate(jid, "data.csv")
    assert ev["ok"] and ev["job"]["mode"] == "evaluate"
    run = M.train("pytorch", "data.csv", "churned")["job"]["id"]
    assert M.cancel(run)["job"]["state"] == "cancelled"
    assert not M.cancel(run)["ok"]


def test_bad_input_becomes_an_error_not_a_crash():
    mark_ready("pytorch")
    assert "bad input" in M.train("pytorch", "data.csv", "churned", epochs="many")["error"]
    r = M.train("pytorch", "../x.csv", "churned")
    assert not r["ok"] and "inside the workspace" in r["error"]
    assert not M.job("zzzzzzzzzz")["ok"]
    r = M._guard(lambda: (_ for _ in ()).throw(ValueError("nope")))
    assert r == {"ok": False, "error": "bad input: nope"}
    assert M.jobs(limit=0)["ok"]


def test_workspace_and_default_trainer(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAINER_WORKSPACE", " ")
    assert M.workspace() is None
    monkeypatch.setenv("TRAINER_WORKSPACE", str(tmp_path))
    assert M.workspace() == tmp_path
    monkeypatch.setattr(M, "_trainer", None)
    t = M.trainer()
    assert isinstance(t, T.Trainer) and M.trainer() is t


def test_main_and_module_entrypoint_run_the_stdio_server(monkeypatch):
    from mcp.server.mcpserver import MCPServer
    ran = []
    monkeypatch.setattr(M.server, "run", lambda t: ran.append(t))
    M.main()
    monkeypatch.setattr(MCPServer, "run", lambda self, t: ran.append(t))
    runpy.run_module("app.mcp_trainer", run_name="__main__")
    assert ran == ["stdio", "stdio"]


def test_real_stdio_server_lists_tools_and_reports_nothing_installed(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    async def go():
        env = {**os.environ, "PYTHONPATH": backend, "TRAINER_HOME": str(tmp_path / "e"), "TRAINER_JOBS": str(tmp_path / "j"), "TRAINER_WORKSPACE": str(tmp_path)}
        async with stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_trainer"], env=env)) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = [t.name for t in (await s.list_tools()).tools]
                st = json.loads((await s.call_tool("status", {})).content[0].text)
                tr = json.loads((await s.call_tool("train", {"framework": "ludwig", "dataset": "x.csv", "target": "y"})).content[0].text)
                return names, st, tr
    names, st, tr = asyncio.run(asyncio.wait_for(go(), 60))
    assert names == ["status", "templates", "train", "job", "jobs", "cancel", "evaluate"]
    assert st["ok"] and all(f["state"] == "not_installed" for f in st["frameworks"])
    assert tr["ok"] is False and "not installed" in tr["error"]
