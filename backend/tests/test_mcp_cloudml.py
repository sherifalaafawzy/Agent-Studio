"""The cloud-ML MCP server (app/mcp_cloudml.py): read-only by default, preview before any change, double gate (switch + confirm)."""
from __future__ import annotations

import asyncio
import json
import os
import runpy
import sys

import pytest

from app import cloudml as C
from app import mcp_cloudml as M


class FakeProvider(C.Provider):
    id, label, cost_note = "fake", "Fake Cloud", "costs $1/hour"
    fields = []
    deletes = ("endpoints",)

    def __init__(self, env=None):
        super().__init__(env or {})
        self.did = []

    def list(self, kind):
        self.check_kind(kind)
        return [C.item("e1", "ep", "InService", kind)]

    def describe(self, kind, rid):
        return {"id": rid}

    def deploy(self, spec):
        self.did.append(("deploy", spec))
        return {"created": "x"}

    def delete(self, kind, rid):
        self.did.append(("delete", kind, rid))
        return {"deleted": rid}


@pytest.fixture
def fake(monkeypatch):
    p = FakeProvider()
    monkeypatch.setattr(M, "_factory", lambda name, env: p if name == "fake" else C.make(name, env))
    monkeypatch.delenv("CLOUDML_ALLOW_WRITE", raising=False)
    return p


def test_providers_reports_what_is_configured_and_whether_writes_are_on(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    r = M.providers()
    assert r["ok"] and r["writes_enabled"] is False and {p["id"]: p["configured"] for p in r["providers"]}["runpod"] is True
    monkeypatch.setenv("CLOUDML_ALLOW_WRITE", "1")
    assert M.providers()["writes_enabled"] is True
    monkeypatch.setenv("CLOUDML_ALLOW_WRITE", "true")
    assert M.writes_allowed() is False                              # only the exact value 1 turns it on


def test_reading_works_and_unknown_or_unconfigured_providers_are_errors(fake):
    r = M.list_resources("fake", "endpoints")
    assert r["ok"] and r["items"][0]["state"] == "InService"
    assert M.describe("fake", "endpoints", "e1")["resource"] == {"id": "e1"}
    assert not M.list_resources("fake", "nope")["ok"]
    assert "unknown provider" in M.list_resources("zzz", "models")["error"]
    assert "not configured" in M.list_resources("aws", "models")["error"]


def test_deploy_is_a_preview_unless_confirmed_and_allowed(fake, monkeypatch):
    r = M.deploy("fake", {"size": "big"})
    assert r["ok"] and r["confirmed"] is False and "Preview only" in r["note"] and r["cost_warning"] == "costs $1/hour" and fake.did == []
    r = M.deploy("fake", {"size": "big"}, confirm=True)
    assert not r["ok"] and "switched off" in r["error"] and fake.did == []                      # confirm alone is not enough
    monkeypatch.setenv("CLOUDML_ALLOW_WRITE", "1")
    r = M.deploy("fake", {"size": "big"}, confirm=True)
    assert r["ok"] and r["confirmed"] and r["result"] == {"created": "x"} and fake.did == [("deploy", {"size": "big"})]
    assert not M.deploy("zzz", {}, confirm=True)["ok"]


def test_delete_has_the_same_double_gate_and_checks_the_kind(fake, monkeypatch):
    r = M.delete("fake", "endpoints", "e1")
    assert r["ok"] and r["confirmed"] is False and fake.did == []
    assert "switched off" in M.delete("fake", "endpoints", "e1", confirm=True)["error"]
    monkeypatch.setenv("CLOUDML_ALLOW_WRITE", "1")
    assert M.delete("fake", "endpoints", "e1", confirm=True)["result"] == {"deleted": "e1"} and fake.did == [("delete", "endpoints", "e1")]
    assert "cannot delete 'jobs' here" in M.delete("fake", "jobs", "j", confirm=True)["error"]
    assert "kind must be" in M.delete("fake", "nope", "j")["error"]


def test_the_environment_comes_from_the_process(monkeypatch):
    monkeypatch.setenv("SOME_CLOUD_VAR", "v")
    assert M._env()["SOME_CLOUD_VAR"] == "v"


def test_main_and_module_entrypoint_run_the_stdio_server(monkeypatch):
    from mcp.server.mcpserver import MCPServer
    ran = []
    monkeypatch.setattr(M.server, "run", lambda t: ran.append(t))
    M.main()
    monkeypatch.setattr(MCPServer, "run", lambda self, t: ran.append(t))
    runpy.run_module("app.mcp_cloudml", run_name="__main__")
    assert ran == ["stdio", "stdio"]


def test_real_stdio_server_refuses_writes_by_default(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    async def go():
        env = {**os.environ, "PYTHONPATH": backend}
        for k in list(env):
            if k.startswith(("AWS_", "AZURE_", "GCP_", "RUNPOD_", "DIGITALOCEAN_", "OCI_", "CLOUDML_")):
                env.pop(k)
        async with stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_cloudml"], env=env)) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = [t.name for t in (await s.list_tools()).tools]
                prov = json.loads((await s.call_tool("providers", {})).content[0].text)
                dep = json.loads((await s.call_tool("deploy", {"provider": "runpod", "spec": {"name": "x", "image": "y"}, "confirm": True})).content[0].text)
                return names, prov, dep
    names, prov, dep = asyncio.run(asyncio.wait_for(go(), 60))
    assert names == ["providers", "list_resources", "describe", "deploy", "delete"]
    assert prov["writes_enabled"] is False and not any(p["configured"] for p in prov["providers"])
    assert dep["ok"] is False and "not configured" in dep["error"]
