"""/api/easy/*: system check, onboarding, Ollama install + models + library, coding-agent installs, the agent wizard."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace as NS

import httpx
import pytest

from app.agents import AgentDef
from app.easy import agent_installer as AI
from app.easy import agent_templates as T
from app.easy import api as EAPI
from app.easy import ollama_library as L
from app.easy import ollama_local as OL
from app.easy import ollama_setup as OS
from app.easy import syscheck as SC
from app.easy.agent_installer import Registry
from app.easy.ollama_library import Library
from app.harness.tools import ALL_TOOLS
from tests.test_easy_agents import REG, Box
from tests.test_easy_ollama import PAYLOAD, SEARCH, TAGS, installer, verified


def fake_ollama(models=("qwen3:8b",), running=True):
    """An in-memory local Ollama: the documented endpoints, nothing else."""
    state = {"models": list(models), "deleted": []}

    def handler(req):
        if not running:
            raise httpx.ConnectError("refused", request=req)
        p = req.url.path
        if p == "/api/version":
            return httpx.Response(200, json={"version": "0.40.16"})
        if p == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m, "size": 5_000_000_000, "details": {"family": "qwen3", "parameter_size": "8B", "quantization_level": "Q4"}} for m in state["models"]]})
        if p == "/api/ps":
            return httpx.Response(200, json={"models": []})
        if p == "/api/show":
            name = json.loads(req.content)["model"]
            return httpx.Response(200, json={"capabilities": ["tools"], "details": {}, "model_info": {"x.context_length": 8192}}) if name in state["models"] else httpx.Response(404, json={"error": f"model '{name}' not found"})
        if p == "/api/delete":
            state["deleted"].append(json.loads(req.content)["model"])
            return httpx.Response(200)
        if p == "/api/pull":
            name = json.loads(req.content)["model"]
            if name == "missing":
                return httpx.Response(200, content=(json.dumps({"error": "file does not exist"}) + "\n").encode())
            state["models"].append(name)
            return httpx.Response(200, content=b'{"status":"pulling manifest"}\n{"status":"pulling x","digest":"x","total":10,"completed":10}\n{"status":"success"}\n')
        return httpx.Response(404)
    return state, httpx.MockTransport(handler)


@pytest.fixture
def ollama(monkeypatch):
    state, tr = fake_ollama()
    monkeypatch.setattr(SC, "TRANSPORT", tr)
    monkeypatch.setattr(OL, "TRANSPORT", tr)
    monkeypatch.setattr(OL, "ATRANSPORT", tr)
    monkeypatch.setattr(EAPI, "find_ollama", lambda: "C:/ollama.exe")
    monkeypatch.setattr(OS, "find_ollama", lambda: "C:/ollama.exe")
    monkeypatch.setattr(OS, "TRANSPORT", httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("offline", request=r))))
    return state


@pytest.fixture
def easy(client):
    return client.app.state.easy


# ------------------------------------------------------------------------------------------------ system + onboarding
def test_system_check_reports_the_pc(client, ollama):
    r = client.get("/api/easy/system").json()
    assert [i["id"] for i in r["items"]] == ["python", "docker", "ollama", "node", "disk", "ram", "gpu"]
    assert {i["id"]: i for i in r["items"]}["ollama"]["state"] == "ok" and r["ollama"]["running"] and r["workspace"]


def seed_chat(state):
    state.db.execute("INSERT INTO chat_sessions(id,title,provider,agent,model,workspace,created,updated) VALUES('s1','t','mock',NULL,NULL,NULL,1,1)")


def test_onboarding_is_new_for_new_installs_and_existing_for_people_who_already_chat(client, state, ollama):
    first = client.get("/api/easy/onboarding").json()
    assert first["record"]["status"] == "new" and first["show"] is True and first["resume"] is False and first["steps"][0] == "welcome"
    by = {i["id"]: i for i in first["checklist"]}
    assert by["ollama"]["state"] == "done" and by["first"]["state"] == "todo" and by["workspace"]["state"] == "done" and by["key"]["state"] == "optional"
    seed_chat(state)
    client.put("/api/easy/onboarding/reset")
    state.db.execute("DELETE FROM settings WHERE key='onboarding'")
    again = client.get("/api/easy/onboarding").json()
    assert again["record"]["status"] == "existing" and again["show"] is False
    assert {i["id"]: i for i in again["checklist"]}["first"]["state"] == "done"


def test_onboarding_progress_is_saved_resumable_and_validated(client, ollama):
    r = client.put("/api/easy/onboarding", json={"step": "ollama", "choice": "local", "completed": ["welcome", "power"]}).json()
    assert r["status"] == "in_progress" and r["step"] == "ollama" and r["choice"] == "local" and r["completed"] == ["welcome", "power"]
    client.put("/api/easy/onboarding", json={"completed": ["power"], "skipped": ["key"]})
    got = client.get("/api/easy/onboarding").json()
    assert got["record"]["completed"] == ["welcome", "power"] and got["record"]["skipped"] == ["key"] and got["resume"] is True and got["show"] is False
    assert client.put("/api/easy/onboarding", json={"status": "skipped"}).json()["status"] == "skipped"
    assert client.get("/api/easy/onboarding").json()["resume"] is False
    for bad in ({"status": "bogus"}, {"step": "nope"}, {"choice": "cloud9"}, {"completed": ["nope"]}):
        assert client.put("/api/easy/onboarding", json=bad).status_code == 422
    assert client.put("/api/easy/onboarding", json={"choice": ""}).json()["choice"] == ""
    assert client.post("/api/easy/onboarding/reset").json()["status"] == "in_progress"


def test_a_damaged_saved_record_starts_over(client, state, ollama):
    state.db.set_setting("onboarding", "{broken")
    assert client.get("/api/easy/onboarding").json()["record"]["status"] == "new"
    state.db.set_setting("onboarding", "[1]")
    assert client.get("/api/easy/onboarding").json()["record"]["status"] == "new"


def test_the_checklist_follows_the_real_state(client, state, ollama, monkeypatch):
    client.post("/api/easy/ollama/use", json={"model": "qwen3:8b"})
    by = {i["id"]: i for i in client.get("/api/easy/onboarding").json()["checklist"]}
    assert by["power"]["state"] == "done" and by["model"]["state"] == "done" and "1 local model" in by["model"]["detail"] and by["assistant"]["state"] == "done"
    state.providers.update("openai", dict(enabled=True, api_key="sk-test-key-1234567890"))
    by = {i["id"]: i for i in client.get("/api/easy/onboarding").json()["checklist"]}
    assert by["key"]["state"] == "done" and "OpenAI" in by["key"]["detail"]
    state.db.set_setting("sidebot_enabled", "0")
    assert {i["id"]: i for i in client.get("/api/easy/onboarding").json()["checklist"]}["assistant"]["state"] == "optional"


def test_the_checklist_when_ollama_is_off_or_unreadable(client, monkeypatch):
    state_, tr = fake_ollama(running=False)
    monkeypatch.setattr(SC, "TRANSPORT", tr)
    monkeypatch.setattr(OL, "TRANSPORT", tr)
    monkeypatch.setattr(EAPI, "find_ollama", lambda: None)
    by = {i["id"]: i for i in client.get("/api/easy/onboarding").json()["checklist"]}
    assert by["ollama"]["detail"] == "not installed" and by["ollama"]["state"] == "todo" and by["model"]["detail"] == "no local model yet"
    monkeypatch.setattr(EAPI, "find_ollama", lambda: "C:/ollama.exe")
    assert {i["id"]: i for i in client.get("/api/easy/onboarding").json()["checklist"]}["ollama"]["detail"] == "installed but not running"
    up, tr2 = fake_ollama()
    monkeypatch.setattr(SC, "TRANSPORT", tr2)
    monkeypatch.setattr(OL, "installed", lambda base=None: (_ for _ in ()).throw(OL.LocalError("boom")))
    assert {i["id"]: i for i in client.get("/api/easy/onboarding").json()["checklist"]}["model"]["detail"] == "no local model yet"


# ------------------------------------------------------------------------------------------------ Ollama
def test_ollama_status_plan_and_install_endpoints(client, easy, ollama, tmp_path, monkeypatch):
    st = client.get("/api/easy/ollama/status").json()
    assert st["running"] and st["installed"] and st["install"]["state"] == "idle" and st["path"] == "C:/ollama.exe"
    easy.ollama.platform = "darwin"
    assert client.get("/api/easy/ollama/plan").json()["supported"] is False
    assert client.post("/api/easy/ollama/install", json={}).status_code == 409
    inst, _ = installer(tmp_path, monkeypatch=monkeypatch)
    easy.ollama = inst
    plan = client.get("/api/easy/ollama/plan").json()
    assert plan["supported"] and plan["release"]["verified"]
    assert client.post("/api/easy/ollama/install", json={}).json()["state"] == "downloading"
    inst.join()
    assert client.get("/api/easy/ollama/install/status").json()["state"] == "ready"
    assert client.post("/api/easy/ollama/install/cancel").json()["state"] == "ready"


def test_ollama_start_endpoint(client, easy, ollama, monkeypatch):
    assert client.post("/api/easy/ollama/start").json()["message"] == "Ollama is already running."
    easy.ollama = OS.OllamaInstaller(probe=lambda: {"running": False, "version": None}, platform="win32", sleep=lambda s: None)
    monkeypatch.setattr(OS, "find_ollama", lambda: None)
    r = client.post("/api/easy/ollama/start")
    assert r.status_code == 409 and "not installed" in r.json()["detail"]


def test_installed_models_details_and_delete(client, ollama, state):
    r = client.get("/api/easy/ollama/models").json()
    assert r["ok"] and r["models"][0]["name"] == "qwen3:8b" and r["loaded"] == []
    assert client.get("/api/easy/ollama/models/detail", params={"name": "qwen3:8b"}).json()["context_length"] == 8192
    assert client.get("/api/easy/ollama/models/detail", params={"name": "ghost"}).status_code == 422
    assert client.delete("/api/easy/ollama/models", params={"name": "qwen3:8b"}).json() == {"ok": True} and ollama["deleted"] == ["qwen3:8b"]
    assert client.delete("/api/easy/ollama/models", params={"name": "bad name;"}).status_code == 422


def test_models_endpoint_when_ollama_is_down(client, monkeypatch):
    _, tr = fake_ollama(running=False)
    monkeypatch.setattr(OL, "TRANSPORT", tr)
    r = client.get("/api/easy/ollama/models").json()
    assert r["ok"] is False and "isn't reachable" in r["message"] and r["models"] == []


def test_pulling_streams_one_overall_percentage_and_refreshes_pickers(client, ollama, state):
    from app import models as M
    M._cache["x"] = (1e18, {"provider": "ollama", "models": []})
    with client.stream("POST", "/api/easy/ollama/pull", json={"model": "llama3.2:3b"}) as r:
        evs = [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]
    assert [e["type"] for e in evs] == ["progress", "progress", "progress", "done"] and evs[1]["percent"] == 100.0 and "x" not in M._cache and "llama3.2:3b" in ollama["models"]
    with client.stream("POST", "/api/easy/ollama/pull", json={"model": "missing"}) as r:
        evs = [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]
    assert evs[-1] == {"type": "error", "message": "file does not exist"}


def test_use_switches_the_provider_on(client, ollama, state):
    assert state.providers.get("ollama").enabled is False
    r = client.post("/api/easy/ollama/use", json={"model": " qwen3:8b "}).json()
    assert r == {"ok": True, "provider": "ollama", "model": "qwen3:8b"}
    p = state.providers.get("ollama")
    assert p.enabled and p.default_model == "qwen3:8b"
    state.db.execute("DELETE FROM providers WHERE kind='ollama'")
    assert client.post("/api/easy/ollama/use", json={"model": "x"}).status_code == 404


def test_library_endpoints(client, easy, ollama, tmp_path):
    easy.library = Library(fetch=lambda url: TAGS if url.endswith("/tags") else SEARCH, home=tmp_path, clock=time.time)
    cat = client.get("/api/easy/ollama/library").json()
    names = [m["name"] for m in cat["models"]]
    assert "qwen3" in names and cat["ram_gb"] and {m["name"]: m for m in cat["models"]}["qwen3"]["installed"]
    assert [m["name"] for m in client.get("/api/easy/ollama/library", params={"q": "embed"}).json()["models"]] == ["nomic-embed-text", "mxbai-embed-large", "bge-m3"]
    r = client.post("/api/easy/ollama/library/refresh", json={"query": "", "force": False}).json()
    assert r["ok"] and r["count"] == 2
    assert "qwen3.8" in [m["name"] for m in client.get("/api/easy/ollama/library").json()["models"]]
    assert client.post("/api/easy/ollama/library/refresh", json={"query": "q" * 100}).status_code == 422
    t = client.get("/api/easy/ollama/library/tags", params={"model": "qwen3"}).json()
    assert t["ok"] and t["tags"][0]["tag"] == "latest"


def test_library_works_when_the_local_ollama_is_down(client, easy, tmp_path, monkeypatch):
    _, tr = fake_ollama(running=False)
    monkeypatch.setattr(SC, "TRANSPORT", tr)
    monkeypatch.setattr(OL, "TRANSPORT", tr)
    assert client.get("/api/easy/ollama/library").json()["models"]


# ------------------------------------------------------------------------------------------------ coding agents
def test_agent_catalog_install_status_and_uninstall(client, easy, tmp_path):
    box = Box(tmp_path)
    easy.agents = box.inst
    easy.registry = Registry(fetch=lambda: REG, home=tmp_path)
    cat = client.get("/api/easy/agents").json()
    assert cat["registry"]["source"] == "live" and cat["agents"][0]["id"] == "claude"
    assert client.post("/api/easy/agents/registry/refresh").json() == {"source": "live", "count": len(REG["agents"])}
    bad = client.post("/api/easy/agents/install", json={"agent": "claude", "method": "script"})
    assert bad.status_code == 409 and "confirm the exact command" in bad.json()["detail"]
    ok = client.post("/api/easy/agents/install", json={"agent": "codex", "method": "npm"})
    assert ok.status_code == 200 and ok.json()["agent"] == "codex"
    box.inst.join()
    assert client.get("/api/easy/agents/status").json()["state"] == "done"
    gone = client.post("/api/easy/agents/uninstall", json={"agent": "codex"})
    assert gone.status_code == 409 and "not installed" in gone.json()["detail"]


def test_the_installer_is_built_on_first_use(client, easy):
    assert easy.agents is None
    assert client.get("/api/easy/agents/status").json()["state"] == "idle" and easy.agents is not None


# ------------------------------------------------------------------------------------------------ wizard
def test_every_starter_profile_is_a_valid_agent(state):
    ids = set()
    for t in T.TEMPLATES:
        assert t["id"] not in ids and t["tagline"] and t["color"] and t["risk"] in T.RISK_TEXT
        ids.add(t["id"])
        a = AgentDef(id="tpl_" + t["id"].replace("-", "_"), name=t["agent"]["name"], **{k: v for k, v in t["agent"].items() if k != "name"})
        state.agents.validate(a)
        assert set(a.tools) <= set(ALL_TOOLS)
    assert {"explorer", "coder", "reviewer", "debugger", "architect", "researcher", "minimal", "orchestrator"} == ids
    assert set(T.TOOL_PLAIN) == set(ALL_TOOLS)                                          # every tool has a plain-language label and a risk
    by = {t["id"]: t for t in T.TEMPLATES}
    assert by["explorer"]["risk"] == "safe" and "write_file" not in by["explorer"]["agent"]["tools"] and "run_shell" not in by["explorer"]["agent"]["tools"]
    assert by["researcher"]["agent"]["rag"] == ["*"] and by["minimal"]["agent"]["tools"] == ["run_shell"]


def test_wizard_data(client):
    d = client.get("/api/easy/wizard").json()
    assert len(d["templates"]) == 8 and [t["id"] for t in d["tiers"]] == ["flash", "pro", "custom"]
    tool = {t["name"]: t for t in d["tools"]}
    assert tool["run_shell"]["risk"] == "runs" and tool["read_file"]["risk"] == "safe" and tool["edit_file"]["risk_text"].startswith("Can change files")
    assert set(tool) == set(ALL_TOOLS) and set(d["risk_text"]) == {"safe", "changes", "runs", "saves", "app"}


def fake_completion(text="You are a careful helper.", boom=None, usage=True):
    async def comp(**kw):
        comp.seen = kw
        if boom:
            raise boom
        return NS(choices=[NS(message=NS(content=text))], usage=NS(prompt_tokens=50, completion_tokens=20) if usage else None)
    return comp


def test_draft_instruction_goes_through_the_spend_guard(client, state, monkeypatch):
    import litellm
    assert client.post("/api/easy/wizard/draft", json={"goal": "summarise my notes"}).status_code == 409           # no real provider yet
    state.providers.update("openai", dict(enabled=True, api_key="sk-test-key-1234567890"))
    comp = fake_completion()
    monkeypatch.setattr(litellm, "acompletion", comp)
    r = client.post("/api/easy/wizard/draft", json={"goal": "summarise my notes", "name": "Notes", "tools": ["read_file", "bogus"]}).json()
    assert r["instruction"] == "You are a careful helper." and r["provider"] == "openai" and r["cost_usd"] >= 0
    sys_msg, user_msg = comp.seen["messages"]
    assert sys_msg["content"] == T.DRAFT_SYSTEM and "summarise my notes" in user_msg["content"] and "Read files (read_file)" in user_msg["content"] and "bogus" not in user_msg["content"]
    assert state.db.one("SELECT COUNT(*) n FROM ledger WHERE note='wizard_draft'")["n"] == 1
    monkeypatch.setattr(litellm, "acompletion", fake_completion(usage=False, text="  "))
    assert client.post("/api/easy/wizard/draft", json={"goal": "x y z", "provider": "openai", "model": "gpt-x"}).json()["instruction"] == ""
    assert client.post("/api/easy/wizard/draft", json={"goal": "x"}).status_code == 422                      # too short to draft from
    assert client.post("/api/easy/wizard/draft", json={"goal": "abc", "provider": "anthropic"}).status_code == 409   # that provider is off


def test_draft_refuses_a_misconfigured_provider_and_refunds_a_failed_call(client, state, monkeypatch):
    import litellm
    state.providers.update("openai", dict(enabled=True, api_key="sk-test-key-1234567890"))
    state.providers.update("anthropic", dict(enabled=True))
    assert client.post("/api/easy/wizard/draft", json={"goal": "abc", "provider": "anthropic"}).status_code == 422       # enabled, but no key
    monkeypatch.setattr(litellm, "acompletion", fake_completion(boom=RuntimeError("provider down")))
    with pytest.raises(RuntimeError, match="provider down"):
        client.post("/api/easy/wizard/draft", json={"goal": "abc"})
    assert state.guard.reserved("openai") == 0.0                                                                           # the reservation was released
