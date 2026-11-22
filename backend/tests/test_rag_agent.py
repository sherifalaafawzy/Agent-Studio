"""Agents and RAG: attaching knowledge bases, the rag_search / rag_list tools, and a real chat turn that uses them."""
import asyncio
import json

import pytest

from app import mock_llm
from app.budget import BudgetExceeded
from app.db import Database
from app.harness.tools import ToolContext, build_tools
from app.rag.service import RagService
from tests.rag_fakes import FakeEmbedder

run = asyncio.run


@pytest.fixture
def rich(client, state, tmp_path):
    state.rag.embedder = FakeEmbedder()
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    return client


def kb_with_docs(client, kb_id="docs"):
    assert client.post("/api/rag/kbs", json={"id": kb_id, "label": "Company docs", "provider": "mock", "model": "embed-a",
                                             "description": "policies"}).status_code == 200
    client.post(f"/api/rag/kbs/{kb_id}/text", json={"title": "Billing policy", "text": "Refunds and invoices are handled by the billing team."})
    client.post(f"/api/rag/kbs/{kb_id}/text", json={"title": "Shipping policy", "text": "Parcels are tracked until delivery."})


def make_agent(client, agent_id="kbagent", **kw):
    body = {"id": agent_id, "name": "KB agent", "tier": "flash", "tools": [], **kw}
    r = client.post("/api/agents", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------ attaching them
def test_attaching_a_knowledge_base_switches_the_search_tools_on(rich):
    a = make_agent(rich, rag=["docs"], tools=["read_file"])
    assert a["rag"] == ["docs"] and a["tools"] == ["read_file", "rag_search", "rag_list"]
    assert next(x for x in rich.get("/api/agents").json() if x["id"] == "kbagent")["rag"] == ["docs"]
    b = rich.put("/api/agents/kbagent", json={"rag": ["*"]}).json()
    assert b["rag"] == ["*"] and b["tools"].count("rag_search") == 1             # not added twice
    assert rich.put("/api/agents/kbagent", json={"rag": []}).json()["rag"] == []


def test_agents_without_knowledge_bases_have_none_by_default(rich):
    assert all(a["rag"] == [] for a in rich.get("/api/agents").json())
    assert make_agent(rich, "plain")["tools"] == []


def test_bad_knowledge_base_references_are_refused(rich):
    r = rich.post("/api/agents", json={"id": "bad", "name": "Bad", "rag": ["Not A Kb"]})
    assert r.status_code == 422 and "bad knowledge base reference" in r.json()["detail"]


def test_attached_knowledge_bases_survive_reopening_the_database(tmp_path):
    from app.agents import AgentStore
    path = tmp_path / "again.db"
    AgentStore(Database(path)).update("engineer", {"rag": ["docs"]})
    assert AgentStore(Database(path)).get("engineer").rag == ["docs"]


# ---------------------------------------------------------------------- tools
@pytest.fixture
def tool_env(state, tmp_path):
    svc = RagService(state.db, state.providers, state.guard, home=tmp_path / "home", embedder=FakeEmbedder())
    for kb_id, text in (("docs", "Refunds and invoices are handled by billing."), ("other", "Parcels are tracked until delivery.")):
        run(svc.create_kb(kb_id, kb_id.title(), "local", "mock", "embed-a", description=f"{kb_id} docs"))
        run(svc.ingest_text(kb_id, f"{kb_id} note", text))

    def tools(kbs, rag=svc):
        ctx = ToolContext(tmp_path, None, None, None, rag=rag, rag_kbs=tuple(kbs))
        return {f.__name__: f for f in build_tools(ctx, ["rag_search", "rag_list", "list_dir"])}
    return svc, tools


def test_the_tools_only_exist_when_knowledge_bases_are_attached(tool_env):
    svc, tools = tool_env
    assert set(tools(["docs"])) == {"rag_search", "rag_list", "list_dir"}
    assert set(tools([])) == {"list_dir"}                                      # nothing attached
    assert set(tools(["docs"], rag=None)) == {"list_dir"}                      # no RAG service at all


def test_rag_list_shows_only_attached_knowledge_bases(tool_env):
    _, tools = tool_env
    listed = run(tools(["docs"])["rag_list"]())["knowledge_bases"]
    assert [k["id"] for k in listed] == ["docs"] and listed[0]["documents"] == 1 and listed[0]["description"] == "docs docs"
    assert [k["id"] for k in run(tools(["*"])["rag_list"]())["knowledge_bases"]] == ["docs", "other"]
    assert run(tools(["ghost"])["rag_list"]())["knowledge_bases"] == []        # attached but deleted


def test_rag_search_is_limited_to_attached_knowledge_bases(tool_env):
    _, tools = tool_env
    search = tools(["docs"])["rag_search"]
    r = run(search("refunds and invoices"))
    assert r["ok"] and r["results"][0]["source"] == "docs note" and {h["kb"] for h in r["results"]} == {"docs"} and "cosine" in r["note"]
    assert {h["kb"] for h in run(tools(["*"])["rag_search"]("parcels tracked", k=5))["results"]} == {"docs", "other"}
    only = run(tools(["*"])["rag_search"]("parcels", knowledge_base="other"))
    assert {h["kb"] for h in only["results"]} == {"other"}
    denied = run(search("parcels", knowledge_base="other"))
    assert denied["ok"] is False and "not one of your knowledge bases (docs)" in denied["error"]
    assert "(none)" in run(tools(["ghost"])["rag_search"]("x", knowledge_base="other"))["error"]


def test_rag_search_clamps_k_and_reports_every_failure_as_text(tool_env):
    svc, tools = tool_env
    search = tools(["*"])["rag_search"]
    assert len(run(search("note", k=99))["results"]) <= 10
    assert len(run(search("note", k=0))["results"]) == 1
    assert "invalid literal" in run(search("note", k="many"))["error"]
    assert "empty" in run(search("   "))["error"]

    class Capped:
        async def embed(self, *a):
            raise BudgetExceeded({"message": "cap reached for mock"})
    svc.embedder = Capped()
    assert run(search("note")) == {"ok": False, "error": "cap reached for mock"}


# ---------------------------------------------------- a real chat turn (mock LLM)
@pytest.fixture
def instructions(monkeypatch):
    """The system prompt every mock-model call receives."""
    seen = []
    orig = mock_llm.MockLlm.generate_content_async

    async def spy(self, llm_request, stream=False):
        seen.append(str(llm_request.config.system_instruction))
        async for item in orig(self, llm_request, stream):
            yield item
    monkeypatch.setattr(mock_llm.MockLlm, "generate_content_async", spy)
    return seen


def chat(c, msg, agent):
    return c.post("/api/chat", json={"provider": "mock", "agent": agent, "message": msg})


def test_an_agent_answers_from_its_knowledge_base(rich, instructions):
    kb_with_docs(rich)
    make_agent(rich, rag=["docs"])
    j = chat(rich, '!tool rag_search {"query": "refunds and invoices", "k": 2}', "kbagent").json()
    call = next(s for s in j["steps"] if s["type"] == "result" and s["tool"] == "rag_search")
    assert "Billing policy" in json.dumps(call) and "Shipping policy" in json.dumps(call)       # k=2 returns both passages
    assert "## Knowledge bases" in instructions[0] and "rag_search" in instructions[0]
    assert j["usage"]["cost_usd"] > 0


def test_an_agent_without_knowledge_bases_gets_no_hint_and_no_tool(rich, instructions):
    kb_with_docs(rich)
    make_agent(rich, "plain", tools=["rag_search"])                             # tool listed, but nothing attached
    chat(rich, "hello there", "plain")
    assert "Knowledge bases" not in instructions[0]


def test_a_sub_agent_searches_its_own_knowledge_bases(rich):
    kb_with_docs(rich)
    make_agent(rich, "researcher", rag=["docs"])
    make_agent(rich, "lead", sub_agents=["researcher"])
    j = chat(rich, '!agent researcher {"request": "!tool rag_search {\\"query\\": \\"refunds invoices\\"}"}', "lead").json()
    steps = [(s["agent"], s["tool"]) for s in j["steps"] if s["type"] == "call"]
    assert ("lead", "researcher") in steps and ("researcher", "rag_search") in steps
    assert "Billing policy" in json.dumps(j["steps"])
