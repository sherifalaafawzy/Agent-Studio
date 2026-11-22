"""Provider account credits (auto/manual/strict) enforced by the guard + external-agent monthly limits + super agent."""
import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest

from app import credits as C

FAKE = str(Path(__file__).parent / "fake_acp_agent.py")


@pytest.fixture
def mock(client):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 64})
    return client


def chat(c, msg="hello"):
    return c.post("/api/chat", json={"provider": "mock", "message": msg})


def test_manual_balance_blocks_when_credits_would_not_cover_the_call(mock):
    r = mock.put("/api/providers/mock/credits", json={"balance_usd": 0.0001})
    assert r.status_code == 200 and r.json()["mode"] == "manual" and r.json()["remaining_usd"] == pytest.approx(0.0001)
    r = chat(mock)
    assert r.status_code == 402 and r.json()["detail"]["reason"] == "credits_insufficient" and "Credits guard" in r.json()["detail"]["message"]
    mock.put("/api/providers/mock/credits", json={"balance_usd": 100})
    assert chat(mock).status_code == 200
    cr = mock.get("/api/providers/mock/credits").json()
    assert 0 < cr["spent_since_usd"] and cr["remaining_usd"] == pytest.approx(100 - cr["spent_since_usd"])
    st = next(p for p in mock.get("/api/billing/summary").json()["providers"] if p["provider"] == "mock")
    assert st["credits_remaining_usd"] == pytest.approx(cr["remaining_usd"]) and st["spendable_usd"] <= st["credits_remaining_usd"]


def test_strict_mode_blocks_unverifiable_credits(mock):
    mock.put("/api/providers/mock/credits", json={"mode": "off", "strict": True})
    r = chat(mock)
    assert r.status_code == 402 and r.json()["detail"]["reason"] == "credits_unverified"
    mock.put("/api/providers/mock/credits", json={"strict": False})
    assert chat(mock).status_code == 200
    assert mock.put("/api/providers/openai/credits", json={"mode": "auto"}).status_code == 422     # no balance API: says so
    assert "no balance API" in next(p for p in mock.get("/api/providers").json() if p["id"] == "openai")["credits"]["why_not"]


@pytest.fixture
def openrouter(monkeypatch):
    replies = {}

    def handler(req):
        for k, v in replies.items():
            if req.url.path.endswith(k):
                return httpx.Response(v[0], json=v[1])
        return httpx.Response(404, json={})
    real = httpx.AsyncClient
    monkeypatch.setattr(C.httpx, "AsyncClient", lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    return replies


def test_openrouter_credits_are_grabbed_on_connect(client, openrouter):
    openrouter["/credits"] = (200, {"data": {"total_credits": 25.0, "total_usage": 7.5}})
    r = client.put("/api/providers/openrouter", json={"enabled": True, "api_key": "sk-or-v1-test-123456"}).json()
    assert r["credits"]["mode"] == "auto" and r["credits"]["balance_usd"] == pytest.approx(17.5) and r["credits"]["source"] == "OpenRouter account credits"
    openrouter["/credits"] = (403, {"error": "management key required"})
    openrouter["/key"] = (200, {"data": {"limit": 10, "usage": 4, "limit_remaining": 6.0}})
    r = client.post("/api/providers/openrouter/credits/refresh").json()
    assert r["balance_usd"] == pytest.approx(6.0) and r["source"] == "OpenRouter key limit remaining"
    openrouter["/key"] = (200, {"data": {"limit": None, "limit_remaining": None}})
    r = client.post("/api/providers/openrouter/credits/refresh").json()
    assert "management key" in r["error"] and r["balance_usd"] == pytest.approx(6.0)       # keeps the last known value, shows why


def test_external_agent_monthly_limits(state):
    state.db.set_setting("workspace_dir", str(Path(state.db.path).parent / "ws"))
    state.acp.store.create("fake", "Fake", sys.executable, [FAKE])
    from app.acp_agents import AcpError

    async def one(text="hi"):
        s = await state.acp.get_or_open("fake", None, None)
        try:
            return await state.acp.prompt(s, text, lambda e: None)
        finally:
            await state.acp.close_all()

    state.acp.store.update("fake", {"month_turn_cap": 1})
    asyncio.run(one())
    with pytest.raises(AcpError, match="monthly turn limit"):
        asyncio.run(one())
    state.acp.store.update("fake", {"month_turn_cap": 0, "month_cost_cap": 0.004})          # fake reports $0.0042 per turn
    with pytest.raises(AcpError, match="monthly cost limit reached"):
        asyncio.run(one())
    state.db.execute("DELETE FROM acp_usage")
    state.db.execute("INSERT INTO acp_usage(ts,agent,session,stop_reason,cost,duration_s) VALUES(?,?,?,?,?,?)", (time.time(), "fake", "s", "end_turn", None, 90))
    with pytest.raises(AcpError, match="doesn't report cost"):                               # can't verify -> refuse, say why
        asyncio.run(one())
    state.acp.store.update("fake", {"month_cost_cap": 0, "month_minutes_cap": 1})
    with pytest.raises(AcpError, match="monthly time limit"):
        asyncio.run(one())
    m = state.acp.store.get("fake")["month"]
    assert m["turns"] == 1 and m["minutes"] == 1.5


def test_super_engineer_is_seeded_with_security_and_quality_tooling(client):
    a = next(x for x in client.get("/api/agents").json() if x["id"] == "super_engineer")
    assert a["can_spawn"] and set(a["acp_agents"]) == {"claude", "codex", "aider", "openhands", "cursor", "kiro", "antigravity", "grok"} and a["acp_policy"] == "ask"
    assert {"semgrep", "eslint", "snyk"} <= set(a["mcp"]) and "security_auditor" in a["sub_agents"] and a["skills"] == ["*"]
    mcp = {m["id"]: m for m in client.get("/api/mcp").json()}
    assert mcp["semgrep"]["enabled"] and mcp["eslint"]["enabled"] and not mcp["snyk"]["enabled"] and mcp["snyk"]["requires_env"] == ["SNYK_TOKEN"]
    names = {s["name"] for s in client.get("/api/skills").json()}
    assert {"security-audit", "code-quality", "delegate-to-external-agents"} <= names


def test_dashboard_aggregates(mock):
    chat(mock)
    d = mock.get("/api/dashboard").json()
    assert len(d["daily"]) == 30 and d["daily"][-1]["calls"] >= 1 and d["kpis"]["month_calls"] >= 1 and d["kpis"]["month_spent_usd"] > 0
    assert any(p["provider"] == "mock" for p in d["providers"]) and d["recent"][0]["messages"] == 2
    assert d["health"]["agents"] >= 7 and d["health"]["mcp_total"] >= 10 and {"turns", "minutes"} <= set(d["external"][0]["month"])
