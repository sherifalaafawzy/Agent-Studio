"""Full agent trees on the keyless mock model: delegation, tiering, shared guard, halting, loop limit."""
import json

import pytest

from app.harness import sandbox as sbx

DOCKER = sbx.status()
needs_docker = pytest.mark.skipif(not (DOCKER["docker"] and DOCKER["image"]), reason="docker sandbox image not available")


@pytest.fixture
def calls_spy(monkeypatch):
    import litellm
    calls = []
    orig = litellm.acompletion

    async def spy(*a, **k):
        calls.append(k.get("model"))
        return await orig(*a, **k)

    monkeypatch.setattr(litellm, "acompletion", spy)
    return calls


@pytest.fixture
def rich(client, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    return client


def chat(c, msg, agent="engineer", **kw):
    return c.post("/api/chat", json={"provider": "mock", "agent": agent, "message": msg, **kw})


def test_orchestrator_delegates_to_subagent_with_pro_flash_tiering(rich, tmp_path):
    (tmp_path / "ws").mkdir(exist_ok=True)
    (tmp_path / "ws" / "hello.txt").write_text("hi")
    msg = '!agent explorer {"request": "!tool list_dir {\\"path\\": \\".\\"}"}'
    r = chat(rich, msg)
    assert r.status_code == 200, r.text
    j = r.json()
    # explorer (isolated sub-agent) ran list_dir inside its own context
    tools = [(s["agent"], s["tool"]) for s in j["steps"] if s["type"] == "call"]
    assert ("engineer", "explorer") in tools and ("explorer", "list_dir") in tools
    assert "hello.txt" in json.dumps(j["steps"])
    # tiering: orchestrator on the 'pro' model, sub-agent on the 'flash' model, each settled separately
    by = {(c["agent"], c["model"]) for c in j["model_calls"]}
    assert ("engineer", "mock-expensive") in by and ("explorer", "mock-cheap") in by
    assert j["usage"]["cost_usd"] > 0


def test_coder_edit_flow_with_verification(rich, tmp_path):
    ws = tmp_path / "ws"; ws.mkdir(exist_ok=True)
    (ws / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    script = ('!tool read_file {"path": "calc.py"}\n'
              '!tool edit_file {"path": "calc.py", "old_text": "return a - b", "new_text": "return a + b"}')
    j = chat(rich, script, agent="coder").json()
    res = [s for s in j["steps"] if s["type"] == "result" and s["tool"] == "edit_file"][0]
    assert "'ok': True" in res["result"] and "python-syntax" in res["result"]
    assert (ws / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"


def test_broken_edit_rolled_back_via_agent(rich, tmp_path):
    ws = tmp_path / "ws"; ws.mkdir(exist_ok=True)
    (ws / "calc.py").write_text("x = 1\n")
    script = ('!tool read_file {"path": "calc.py"}\n'
              '!tool edit_file {"path": "calc.py", "old_text": "x = 1", "new_text": "x = ("}')
    j = chat(rich, script, agent="coder").json()
    assert "rolled_back" in json.dumps(j["steps"])
    assert (ws / "calc.py").read_text() == "x = 1\n"


def test_guard_halts_whole_turn_but_returns_partial_progress(rich, calls_spy):
    # Root runs on 'mock' (funded). Its sub-agent runs on 'openai' whose cap is tiny -> the guard refuses the
    # sub-agent's call BEFORE it is sent; the whole turn halts; partial progress is returned with the reason.
    rich.put("/api/providers/openai", json={"enabled": True, "api_key": "sk-never-used", "cap_usd": 0.0001})
    rich.post("/api/agents", json={"id": "pricey", "name": "Pricey", "provider": "openai", "tier": "pro", "tools": ["list_dir"]})
    rich.post("/api/agents", json={"id": "boss", "name": "Boss", "tier": "flash", "sub_agents": ["pricey"]})
    r = chat(rich, '!agent pricey {"request": "hello"}', agent="boss")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["halted"]["error"] == "budget_exceeded" and j["halted"]["provider"] == "openai"
    assert len(j["model_calls"]) == 1 and j["model_calls"][0]["agent"] == "boss"   # root's first call settled, nothing else
    assert calls_spy == [], "no provider call may leave the app after the refusal"
    summary = {p["provider"]: p for p in rich.get("/api/billing/summary").json()["providers"]}
    assert summary["openai"]["spent_usd"] == 0 and summary["openai"]["refused"] >= 1


def test_refusal_before_any_call_is_402_for_agents_too(rich):
    rich.put("/api/providers/mock", json={"cap_usd": 0.0001})
    r = chat(rich, "hello")
    assert r.status_code == 402 and r.json()["detail"]["error"] == "budget_exceeded"


def test_turn_call_limit_stops_runaway_loops(rich):
    rich.put("/api/settings", json={"max_llm_calls_per_turn": 2})
    # 4 scripted tool steps would need 5 model calls; limit is 2
    script = "\n".join('!tool list_dir {}' for _ in range(4))
    j = chat(rich, script, agent="explorer").json()
    assert j["halted"]["error"] == "turn_limit"
    assert len(j["model_calls"]) == 2


def test_agent_crud_api(rich):
    r = rich.post("/api/agents", json={"id": "scout", "name": "Scout", "tier": "flash", "tools": ["read_file"], "sub_agents": []})
    assert r.status_code == 200
    assert rich.put("/api/agents/scout", json={"tier": "pro"}).json()["tier"] == "pro"
    assert rich.post("/api/agents", json={"id": "scout", "name": "dup"}).status_code == 422
    assert chat(rich, "hello there", agent="scout").status_code == 200
    assert rich.delete("/api/agents/scout").status_code == 200
    assert rich.delete("/api/agents/engineer").status_code == 422


@needs_docker
def test_shell_tool_through_agent_runs_in_container(rich):
    j = chat(rich, '!tool run_shell {"command": "id -u"}', agent="coder").json()
    res = [s for s in j["steps"] if s["type"] == "result"][0]
    assert "1000" in res["result"]
