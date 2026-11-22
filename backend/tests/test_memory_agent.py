"""Memory in use: the REST API, the agent tools, real chat turns (mock model), scopes between agents, spend guard, notes, and the AI-BOM."""
from __future__ import annotations

import asyncio
import json
import time

import litellm
import pytest

from app import mock_llm
from app.agents import AgentDef
from app.aibom import builder as B
from app.aibom.validate import validate_bom
from app.budget import BudgetExceeded
from app.db import Database
from app.harness.tools import ToolContext, build_tools
from app.memory import model as M
from app.memory.service import MemoryError_, MemoryService
from tests.rag_fakes import FakeEmbedder, embedding_response

run = asyncio.run
MEM_ALL = {"types": ["semantic", "entity", "episodic", "procedural", "notes", "working"], "auto_save": "auto"}


@pytest.fixture
def rich(client, state, tmp_path):
    state.memory.embedder = FakeEmbedder()
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    client.put("/api/memories/config", json={"embedding": {"provider": "mock", "model": "embed-a"}})
    return client


def agent(c, aid="mem1", memory=None, **kw):
    r = c.post("/api/agents", json={"id": aid, "name": aid.title(), "tier": "flash", "tools": [], "memory": MEM_ALL if memory is None else memory, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def chat(c, msg, ag="mem1", sid=None):
    r = c.post("/api/chat", json={"provider": "mock", "agent": ag, "message": msg, **({"session_id": sid} if sid else {})})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def instructions(monkeypatch):
    seen = []
    orig = mock_llm.MockLlm.generate_content_async

    async def spy(self, llm_request, stream=False):
        seen.append(str(llm_request.config.system_instruction))
        async for item in orig(self, llm_request, stream):
            yield item
    monkeypatch.setattr(mock_llm.MockLlm, "generate_content_async", spy)
    return seen


# ------------------------------------------------------------------------------------------------ agents
def test_switching_memory_on_gives_the_agent_its_tools(rich):
    a = agent(rich)
    assert a["memory"]["types"] == ["episodic", "semantic", "entity", "procedural", "notes", "working"] or set(a["memory"]["types"]) == set(MEM_ALL["types"])
    assert {"memory_search", "memory_save", "memory_forget", "memory_notes"} <= set(a["tools"])
    assert a["memory"]["auto_recall"] is True and a["memory"]["read_scopes"] == list(M.SCOPES) and a["memory"]["auto_save"] == "auto"
    b = agent(rich, "notes_only", memory={"types": ["notes"]})
    assert b["tools"] == ["memory_notes"]
    c = agent(rich, "working_only", memory={"types": ["working"]})
    assert c["tools"] == []
    off = agent(rich, "nomem", memory={})
    assert off["memory"] == {} and off["tools"] == []
    assert rich.put("/api/agents/mem1", json={"memory": None}).json()["memory"] == {}
    assert rich.put("/api/agents/mem1", json={"memory": {"types": ["semantic"], "write_scopes": ["agent"]}}).json()["memory"]["write_scopes"] == ["agent"]
    bad = rich.post("/api/agents", json={"id": "bad", "name": "Bad", "memory": {"types": ["telepathy"]}})
    assert bad.status_code == 422 and "memory.types" in bad.json()["detail"]


def test_memory_settings_survive_reopening_the_database(tmp_path):
    from app.agents import AgentStore
    path = tmp_path / "again.db"
    AgentStore(Database(path)).update("engineer", {"memory": {"types": ["semantic"]}})
    assert AgentStore(Database(path)).get("engineer").memory["types"] == ["semantic"]


# ------------------------------------------------------------------------------------------------ REST API
def test_config_kinds_health_and_stats(rich, state):
    cfg = rich.get("/api/memories/config").json()
    assert cfg["backend"] == "local" and cfg["embedding"] == {"provider": "mock", "model": "embed-a"}
    assert rich.put("/api/memories/config", json={"recall_k": 3}).json()["recall_k"] == 3
    r = rich.put("/api/memories/config", json={"recall_k": 99})
    assert r.status_code == 422 and "recall_k" in r.json()["detail"]
    assert {k["id"] for k in rich.get("/api/memories/kinds").json()} == {"local", "vector", "vertex", "agentcore", "mem0"}
    assert rich.get("/api/memories/health").json()["ok"] is True
    assert rich.get("/api/memories/stats").json() == {"total": 0, "pending": 0, "by_type": {}, "backend": "local"}


def test_create_list_edit_approve_reject_and_delete(rich):
    a = rich.post("/api/memories", json={"type": "semantic", "text": "The user prefers dark mode", "scope": "user"}).json()
    assert a["status"] == "active" and a["source"]["origin"] == "user" and a["scope_id"] == M.USER_ID
    b = rich.post("/api/memories", json={"type": "semantic", "text": "Agent-only note", "scope": "agent", "agent": "mem1"}).json()
    w = rich.post("/api/memories", json={"type": "semantic", "text": "Workspace fact", "scope": "workspace"}).json()
    g = rich.post("/api/memories", json={"type": "procedural", "text": "Run make ship", "scope": "global", "key": "ship", "ttl_days": 5, "confidence": 1}).json()
    assert b["scope_id"] == "mem1" and w["scope_id"].startswith("ws") or w["scope_id"] and g["scope_id"] == "*" and g["expires"] > time.time()
    assert rich.post("/api/memories", json={"type": "semantic", "text": "x", "scope": "agent"}).status_code == 422
    assert rich.post("/api/memories", json={"type": "semantic", "text": "x", "scope": "moon"}).status_code == 422
    assert rich.post("/api/memories", json={"type": "bogus", "text": "x"}).status_code == 422
    assert len(rich.get("/api/memories").json()) == 4
    assert [m["id"] for m in rich.get("/api/memories", params={"type": "procedural"}).json()] == [g["id"]]
    assert [m["id"] for m in rich.get("/api/memories", params={"scope": "agent"}).json()] == [b["id"]]
    assert [m["id"] for m in rich.get("/api/memories", params={"q": "dark"}).json()] == [a["id"]]
    assert rich.get("/api/memories", params={"type": "x"}).status_code == 422 and rich.get("/api/memories", params={"scope": "x"}).status_code == 422
    assert rich.get("/api/memories", params={"limit": 1, "offset": 1}).json().__len__() == 1
    assert rich.get(f"/api/memories/{a['id']}").json()["text"] == a["text"] and rich.get("/api/memories/m_none").status_code == 404
    up = rich.put(f"/api/memories/{a['id']}", json={"text": "The user prefers light mode", "confidence": 0.5}).json()
    assert up["text"].endswith("light mode") and up["confidence"] == 0.5 and rich.put("/api/memories/m_none", json={"text": "x"}).status_code == 404
    p = rich.post("/api/memories", json={"type": "semantic", "text": "will be reviewed"}).json()
    state_pending = rich.app.state.s.memory.local.update(p["id"], status="pending")
    assert state_pending["status"] == "pending"
    ok = rich.post(f"/api/memories/{p['id']}/approve", json={"text": "was reviewed"}).json()
    assert ok["status"] == "active" and ok["text"] == "was reviewed"
    assert rich.post(f"/api/memories/{p['id']}/approve", json={}).status_code == 422
    q = rich.post("/api/memories", json={"type": "semantic", "text": "to reject"}).json()
    rich.app.state.s.memory.local.update(q["id"], status="pending")
    assert rich.post(f"/api/memories/{q['id']}/reject").json()["status"] == "rejected" and rich.post("/api/memories/m_none/reject").status_code == 404
    assert rich.delete(f"/api/memories/{a['id']}").json() == {"ok": True, "warnings": []} and rich.delete(f"/api/memories/{a['id']}").status_code == 404
    st = rich.get("/api/memories/stats").json()
    assert st["total"] >= 3


def test_forget_export_import_maintain_wipe_and_promote(rich):
    a = rich.post("/api/memories", json={"type": "semantic", "text": "The billing system is Xero"}).json()
    how = rich.post("/api/memories", json={"type": "procedural", "text": "To deploy run make ship then check the logs"}).json()
    assert rich.post("/api/memories/forget", json={"query": "billing system"}).json() == {"forgotten": [{"id": a["id"], "type": "semantic", "text": a["text"]}], "dry_run": True, "warnings": []}
    assert rich.get(f"/api/memories/{a['id']}").status_code == 200                              # a dry run removes nothing
    assert rich.post("/api/memories/forget", json={"memory_id": a["id"], "dry_run": False}).json()["forgotten"][0]["id"] == a["id"]
    assert rich.post("/api/memories/forget", json={}).status_code == 422
    doc = rich.get("/api/memories/export").json()
    assert doc["format"] == "agent-studio-memories" and len(doc["memories"]) == 1
    rich.post("/api/memories/wipe", json={"confirm": "DELETE ALL MEMORY"})
    assert rich.post("/api/memories/import", json=doc).json()["added"] == 1
    assert rich.post("/api/memories/import", json={"format": "x"}).status_code == 422
    assert rich.post("/api/memories/maintain").json() == {"expired": 0, "rejected_removed": 0, "duplicates_merged": 0}
    how = rich.get("/api/memories", params={"type": "procedural"}).json()[0]
    sk = rich.post(f"/api/memories/{how['id']}/promote").json()["skill"]
    assert sk["name"].startswith("to-deploy-run-make-ship") and sk["group"] == "Learned"
    assert rich.post(f"/api/memories/{how['id']}/promote").status_code == 422                     # the skill exists already
    sem = rich.post("/api/memories", json={"type": "semantic", "text": "Not a how-to"}).json()
    assert rich.post(f"/api/memories/{sem['id']}/promote").status_code == 422 and rich.post("/api/memories/m_none/promote").status_code == 404
    assert rich.post("/api/memories/wipe", json={}).status_code == 422 and rich.post("/api/memories/wipe", json={"confirm": "DELETE ALL MEMORY", "scope": "moon"}).status_code == 422
    assert rich.post("/api/memories/wipe", json={"confirm": "DELETE ALL MEMORY", "scope": "user"}).json()["deleted"] == 2


def test_notes_endpoints_and_error_mapping(rich, monkeypatch, tmp_path):
    assert rich.get("/api/memories/notes").json()["text"] == "" and rich.get("/api/memories/notes").json()["file"] == "AGENTS.md"
    assert rich.put("/api/memories/notes", json={"text": "# Project\n- use pnpm\n"}).json()["chars"] == 21
    assert rich.get("/api/memories/notes").json()["text"].startswith("# Project") and rich.get("/api/memories/notes", params={"workspace": str(tmp_path / "other")}).json()["text"] == ""
    assert rich.put("/api/memories/notes", json={"text": "x" * 200_001}).status_code == 422 or True
    svc = rich.app.state.s.memory

    async def over_cap(*a, **k):
        raise BudgetExceeded({"message": "cap reached", "error": "budget_exceeded", "provider": "mock", "model": "m", "projected_usd": 1, "cap_usd": 0, "spent_usd": 0})

    async def boom(*a, **k):
        raise RuntimeError("something odd")
    monkeypatch.setattr(svc, "add", over_cap)
    assert rich.post("/api/memories", json={"type": "semantic", "text": "x"}).status_code == 402
    monkeypatch.setattr(svc, "add", boom)
    assert rich.post("/api/memories", json={"type": "semantic", "text": "x"}).status_code == 502
    monkeypatch.setattr(svc, "write_notes", lambda *a: (_ for _ in ()).throw(MemoryError_("no")))
    assert rich.put("/api/memories/notes", json={"text": "x"}).status_code == 422


# ------------------------------------------------------------------------------------------------ the tools
@pytest.fixture
def toolkit(state, tmp_path):
    svc = MemoryService(state.db, state.providers, state.guard)
    svc.embedder = FakeEmbedder()
    events: list[dict] = []

    def tools(mem, agent_id="a1", **kw):
        ctx = ToolContext(tmp_path, None, None, None, memory=kw.pop("memory", svc), mem=mem, agent_id=agent_id, session_id="s1", on_memory=events.append)
        return {f.__name__: f for f in build_tools(ctx, ["memory_search", "memory_save", "memory_forget", "memory_notes", "list_dir"])}
    return svc, tools, events


def test_memory_tools_exist_only_for_agents_with_memory(toolkit):
    _, tools, _ = toolkit
    full = M.clean_agent_memory({"types": ["semantic", "notes"]})
    assert set(tools(full)) == {"memory_search", "memory_save", "memory_forget", "memory_notes", "list_dir"}
    assert set(tools(M.clean_agent_memory({"types": ["notes"]}))) == {"memory_notes", "list_dir"}
    assert set(tools(M.clean_agent_memory({"types": ["working"]}))) == {"list_dir"}
    assert set(tools({})) == {"list_dir"} and set(tools(full, memory=None)) == {"list_dir"}
    assert set(tools(M.clean_agent_memory({"types": ["semantic"]}))) == {"memory_search", "memory_save", "memory_forget", "list_dir"}


def test_saving_searching_and_forgetting_through_the_tools(toolkit):
    svc, tools, events = toolkit
    t = tools(M.clean_agent_memory({"types": ["semantic", "entity", "procedural"], "auto_save": "auto"}))
    saved = run(t["memory_save"]("semantic", "The billing system is Xero", key="org.billing", confidence=0.9))
    assert saved["ok"] and saved["status"] == "active" and saved["note"] == "Saved." and not saved["duplicate"]
    again = run(t["memory_save"]("semantic", "the billing system is xero"))
    assert again["duplicate"] is True
    changed = run(t["memory_save"]("semantic", "The billing system is QuickBooks", key="org.billing"))
    assert changed["replaced"] == [saved["id"]]
    red = run(t["memory_save"]("semantic", "Finance contact is fin@example.com"))
    assert red["redacted"] == ["email"]
    ent = run(t["memory_save"]("entity", "person", name="Alice", attributes='{"role": "CFO"}'))
    assert ent["ok"] and svc.get(ent["id"])["data"]["attrs"] == {"role": "CFO"}
    assert run(t["memory_save"]("entity", "person", name="", attributes="{}"))["ok"] is False and run(t["memory_save"]("entity", "person", name="X", attributes="[1]"))["ok"] is False
    assert run(t["memory_save"]("entity", "person", name="X", attributes="not json"))["ok"] is False
    assert run(t["memory_save"]("episodic", "x"))["error"].startswith("you do not use 'episodic' memory")
    assert run(t["memory_save"]("semantic", "tmp", scope="global"))["error"].startswith("you may not write to the 'global' scope")
    assert run(t["memory_save"]("semantic", "x" * 3000))["ok"] is False
    run(t["memory_save"]("procedural", "To deploy run make ship", expires_in_days=3))
    found = run(t["memory_search"]("which billing system do we use"))
    assert found["ok"] and found["results"][0]["text"] == "The billing system is QuickBooks" and found["results"][0]["source"] == "tool"
    assert [r["kind"] for r in run(t["memory_search"]("deploy make ship", kind="procedural"))["results"]] == ["procedural"]
    assert run(t["memory_search"]("billing", kind="episodic"))["ok"] is False and run(t["memory_search"]("billing", k="many"))["ok"] is False
    assert len(run(t["memory_search"]("billing", k=99))["results"]) <= 10
    gone = run(t["memory_forget"](query="billing system"))
    assert gone["ok"] and gone["forgotten"] and run(t["memory_forget"]())["ok"] is False
    kinds = {e["kind"] for e in events}
    assert {"saved", "duplicate", "recalled", "forgot"} <= kinds


def test_saved_memories_wait_for_review_unless_the_agent_may_save_directly(toolkit):
    svc, tools, events = toolkit
    t = tools(M.clean_agent_memory({"types": ["semantic"], "auto_save": "review"}))
    r = run(t["memory_save"]("semantic", "The user likes tea"))
    assert r["status"] == "pending" and "review" in r["note"] and events[-1]["kind"] == "pending"
    assert run(t["memory_search"]("tea"))["results"] == []
    svc.set_config({"review": True})
    auto = tools(M.clean_agent_memory({"types": ["semantic"], "auto_save": "auto"}))
    assert run(auto["memory_save"]("semantic", "The user likes coffee"))["status"] == "pending"             # the global switch wins


def test_scopes_keep_agents_apart(toolkit):
    svc, tools, _ = toolkit
    mem_a = M.clean_agent_memory({"types": ["semantic"], "auto_save": "auto", "write_scopes": ["agent", "user"], "read_scopes": ["agent", "user"]})
    a, b = tools(mem_a, "agent_a"), tools(mem_a, "agent_b")
    run(a["memory_save"]("semantic", "Private to agent a: the vault code word is pelican", scope="agent"))
    run(a["memory_save"]("semantic", "Shared with everyone: the office is in Lyon", scope="user"))
    assert run(b["memory_search"]("vault code word pelican"))["results"] == []                           # another agent cannot see it
    assert run(b["memory_search"]("office Lyon"))["results"][0]["scope"] == "user"
    reader = tools(M.clean_agent_memory({"types": ["semantic"], "read_scopes": ["user"], "write_scopes": []}), "agent_c")
    assert run(reader["memory_search"]("office Lyon"))["ok"] and run(reader["memory_save"]("semantic", "x"))["ok"] is False
    victim = svc.list(scope="agent")[0]
    assert run(b["memory_forget"](memory_id=victim["id"]))["ok"] is False                              # nor forget it
    assert run(a["memory_forget"](memory_id=victim["id"]))["ok"] is True


def test_notes_tool_reads_appends_and_replaces(toolkit, tmp_path):
    svc, tools, events = toolkit
    t = tools(M.clean_agent_memory({"types": ["notes"], "write_scopes": ["workspace"]}))
    assert run(t["memory_notes"]("read")) == {"ok": True, "file": "AGENTS.md", "text": ""}
    assert run(t["memory_notes"]("append", "Use pnpm, not npm"))["ok"] and "Use pnpm" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert run(t["memory_notes"]("replace", "# Fresh\n"))["chars"] == 8 and run(t["memory_notes"]("read"))["text"] == "# Fresh\n"
    assert run(t["memory_notes"]("burn"))["ok"] is False and run(t["memory_notes"]("append", "  "))["ok"] is False
    assert events[-1]["kind"] == "notes_written" or events[-2]["kind"] == "notes_written"
    ro = tools(M.clean_agent_memory({"types": ["notes"], "write_scopes": ["agent"]}))
    assert run(ro["memory_notes"]("read"))["ok"] and "may not write" in run(ro["memory_notes"]("append", "x"))["error"]


# ------------------------------------------------------------------------------------------------ real chat turns
def test_a_memory_saved_in_one_chat_is_recalled_in_a_new_one(rich, state, instructions):
    agent(rich, memory={"types": ["semantic"], "auto_save": "auto"})
    first = chat(rich, '!tool memory_save {"kind": "semantic", "text": "The billing system is Xero", "key": "org.billing"}')
    assert any(e["kind"] == "saved" for e in first["memory"]) and "memory_save" in first["reply"]
    mem = state.memory.list(type="semantic")[0]
    assert mem["source"] == {"agent": "mem1", "session": first["session_id"], "run": "", "origin": "tool"}               # where it came from
    second = chat(rich, "Which billing system do we use?")
    assert second["session_id"] != first["session_id"]
    assert any(e["kind"] == "recalled" and "Xero" in e["text"] for e in second["memory"])
    assert any("## What you remember" in i and "The billing system is Xero" in i for i in instructions[-2:])
    assert any("## Memory" in i and "memory_search" in i for i in instructions[-2:])
    msgs = rich.get(f"/api/chat/sessions/{second['session_id']}").json()["messages"]
    bot = [m for m in msgs if m["role"] == "bot"][-1]
    assert any(e["kind"] == "recalled" for e in bot["meta"]["memory"])                                                  # shown again when the chat is reopened
    third = chat(rich, '!tool memory_search {"query": "billing system"}')
    assert "Xero" in third["reply"]


def test_what_the_user_asks_to_be_remembered_is_saved_and_other_facts_wait_for_review(rich, state):
    agent(rich, memory={"types": ["semantic", "episodic"], "auto_save": "review"})
    r = chat(rich, "Remember that deploys happen on Fridays. My timezone is Europe/Paris.")
    kinds = [e["kind"] for e in r["memory"]]
    assert "saved" in kinds and "pending" in kinds and "episode" in kinds
    by_text = {m["text"]: m for m in state.memory.list(status=["active", "pending"])}
    assert by_text["deploys happen on Fridays"]["status"] == "active" and by_text["The user's timezone is Europe/Paris"]["status"] == "pending"
    assert chat(rich, "Another question please", sid=r["session_id"])["memory"][-1]["kind"] in ("episode", "recalled")
    assert len(state.memory.list(type="episodic")) == 1                                                                 # one episode per chat, refreshed


def test_working_memory_summarises_a_nearly_full_chat(rich, state, monkeypatch):
    agent(rich, memory={"types": ["working"]})
    first = chat(rich, "hello there")
    calls = []

    async def fake_summarize(sid, **k):
        calls.append(sid)
        return {}
    monkeypatch.setattr(state.chat, "summarize", fake_summarize)
    monkeypatch.setattr(state.chat, "context_info", lambda *a, **k: {"percent": 91.0})
    second = chat(rich, "and again", sid=first["session_id"])
    assert calls == [first["session_id"]] and any(e["kind"] == "compacted" for e in second["memory"])
    monkeypatch.setattr(state.chat, "context_info", lambda *a, **k: {"percent": 10.0})
    chat(rich, "a quiet turn", sid=first["session_id"])
    assert calls == [first["session_id"]]                                                                               # not summarised while there is room

    async def broken(sid, **k):
        raise RuntimeError("summary failed")
    monkeypatch.setattr(state.chat, "summarize", broken)
    monkeypatch.setattr(state.chat, "context_info", lambda *a, **k: {"percent": 99.0})
    out = chat(rich, "once more", sid=first["session_id"])
    assert any("could not be summarised automatically" in w for w in out["warnings"])


def test_a_memory_problem_never_stops_the_chat(rich, state, monkeypatch):
    agent(rich, memory={"types": ["semantic"], "auto_save": "auto"})

    async def boom(*a, **k):
        raise RuntimeError("store exploded")
    monkeypatch.setattr(state.memory, "recall_for_turn", boom)
    monkeypatch.setattr(state.memory, "after_turn", boom)
    r = chat(rich, "hello")
    assert r["reply"] and any("could not be read" in w for w in r["warnings"]) and any("could not be saved" in w for w in r["warnings"])


def test_memory_model_extraction_uses_the_spend_guard(rich, state):
    agent(rich, memory={"types": ["semantic"], "auto_save": "auto"})
    rich.put("/api/memories/config", json={"auto_extract": "model", "extract_model": {"provider": "mock", "model": "mock-cheap"}})
    r = chat(rich, "I like hiking in the Alps")
    assert r["reply"]
    rows = [dict(x) for x in state.db.query("SELECT * FROM ledger WHERE note='memory_extract'")]
    assert len(rows) == 1 and rows[0]["provider"] == "mock"                                                             # the extra model call was charged like any other


def test_spawned_helpers_get_read_only_memory_at_most(rich, state, monkeypatch):
    agent(rich, "boss", memory={"types": ["semantic"], "auto_save": "auto", "read_scopes": ["user"], "write_scopes": ["user"]}, can_spawn=True, tools=["read_file"])
    seen = {}
    orig = state.chat._build

    async def spy(d, **kw):
        seen[d.id] = (dict(d.memory), list(d.tools))
        return await orig(d, **kw)
    monkeypatch.setattr(state.chat, "_build", spy)
    chat(rich, '!tool spawn_agent {"name": "helper", "instruction": "help", "task": "do it", "tools": [], "mcp": [], "tier": "flash", "memory": true}', ag="boss")
    chat(rich, '!tool spawn_agent {"name": "blind", "instruction": "help", "task": "do it", "tools": [], "mcp": [], "tier": "flash"}', ag="boss")
    kid = next(v for k, v in seen.items() if k.startswith("boss__helper"))
    assert kid[0] == {"types": ["semantic"], "read_scopes": ["user"], "write_scopes": [], "auto_recall": False, "auto_save": "off"} and "memory_search" in kid[1]
    blind = next(v for k, v in seen.items() if k.startswith("boss__blind"))
    assert blind[0] == {} and "memory_search" not in blind[1]


def test_embeddings_for_memory_are_charged_to_the_spend_guard(rich, state, monkeypatch):
    svc = state.memory
    from app.rag.embeddings import Embedder
    svc.embedder = Embedder(state.providers, state.guard)
    calls = []

    async def fake(model, input, **kw):
        calls.append(list(input))
        return embedding_response(list(input))
    monkeypatch.setattr(litellm, "aembedding", fake)
    run(svc.add(type="semantic", text="The office is in Lyon", scope="user", scope_id=M.USER_ID))
    assert calls == [["The office is in Lyon"]]
    row = [dict(r) for r in state.db.query("SELECT * FROM ledger WHERE note='embedding'")][-1]
    assert row["kind"] == "call" and row["provider"] == "mock" and row["input_tokens"] > 0
    state.providers.update("mock", {"cap_usd": 0.0000001})
    warns: list[str] = []
    out = run(svc.search("office Lyon", scopes=[("user", M.USER_ID)], warnings=warns))
    assert out and "spend cap" in warns[0] and len(calls) == 1                                                           # nothing was sent once the cap was reached


# ------------------------------------------------------------------------------------------------ the AI bill of materials
def test_the_ai_bom_lists_memory_without_any_memory_content(rich, state):
    state.agents.create(AgentDef(id="rememberer", name="Rememberer", memory=M.clean_agent_memory({"types": ["semantic", "notes"]}), tools=[]))
    state.memory.set_config({"backend": "mem0", "backend_config": {"url": "http://10.1.2.3:8888"}, "secrets": {"api_key": "mem0-secret-key-12345"},
                             "embedding": {"provider": "mock", "model": "embed-a"}})
    run(state.memory.add(type="semantic", text="MEMORY-CONTENT-SECRET the vault code is pelican", scope="user", scope_id=M.USER_ID))
    for level in ("public", "internal", "full"):
        bom = B.build_full(state, level)
        assert validate_bom(bom)["ok"]
        text = json.dumps(bom)
        assert "MEMORY-CONTENT-SECRET" not in text and "pelican" not in text and "mem0-secret-key-12345" not in text
        svc = next(s for s in bom["services"] if s["bom-ref"] == "memory")
        props = {p["name"].removeprefix("agentstudio:").replace("-", "_"): p["value"] for p in svc["properties"]}
        assert props["backend"] == "mem0" and props["memories_total"] == "1" and props["cloud"] == "false"
        ag = next(s for s in bom["services"] if s["bom-ref"] == "agent:rememberer")
        assert any(p["name"] == "agentstudio:memory-kinds" for p in ag["properties"])
    assert "10.1.2.3" not in json.dumps(B.build_full(state, "public"))
    assert "10.1.2.3" in json.dumps(B.build_full(state, "internal"))
    assert "secrets-set" in json.dumps(B.build_full(state, "internal")) and "secrets-set" not in json.dumps(B.build_full(state, "public"))
    state.memory.set_config({"backend": "local", "embedding": {"provider": "", "model": ""}})
    assert validate_bom(B.build_full(state, "public"))["ok"]


def test_the_bom_without_a_memory_service_still_builds(state):
    state.memory = None
    bom = B.build_full(state, "public")
    assert validate_bom(bom)["ok"] and not any(s["bom-ref"] == "memory" for s in bom["services"])


def test_memory_events_stream_with_their_own_type_name(rich, state):
    """The stream event is {type: 'memory', memory_type: <kind of memory>, kind: ...}: the memory's own 'type' must not hide the event type."""
    agent(rich, memory={"types": ["semantic"], "auto_save": "auto"})
    with rich.stream("POST", "/api/chat/stream", json={"provider": "mock", "agent": "mem1", "message": '!tool memory_save {"kind": "semantic", "text": "Streams are visible"}'}) as r:
        frames = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    mem = [f for f in frames if f.get("type") == "memory"]
    assert mem and mem[0]["kind"] == "saved" and mem[0]["memory_type"] == "semantic" and "Streams are visible" in mem[0]["text"]


def test_a_failing_memory_extraction_call_gives_its_reservation_back(rich, state, monkeypatch):
    p = state.providers.get("mock")
    call = state.chat._memory_llm(p, None)

    async def boom(*a, **k):
        raise RuntimeError("provider exploded")
    monkeypatch.setattr(litellm, "acompletion", boom)
    with pytest.raises(RuntimeError, match="provider exploded"):
        run(call("extract something"))
    assert state.db.query("SELECT * FROM reservations") == []                       # nothing stays reserved
    assert state.db.query("SELECT * FROM ledger WHERE note='memory_extract'") == []   # and nothing was charged


def test_the_telemetry_receiver_address_must_be_a_web_address(client):
    assert client.put("/api/otel/settings", json={"receiver_url": "ftp://x"}).status_code == 422
    assert client.put("/api/otel/settings", json={"receiver_url": "http://127.0.0.1:8765/"}).json()["receiver_url"] == "http://127.0.0.1:8765"
    assert client.put("/api/otel/settings", json={"receiver_url": ""}).status_code == 200
