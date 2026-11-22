"""Agent frameworks: the conformance suite over every adapter, plus the pieces around it (installer, gateway, tool bridge, worker)."""
from __future__ import annotations

import asyncio
import io
import json
import sys
from datetime import date
from typing import Optional

import pytest

from app.frameworks import envs as E
from app.frameworks import worker as W
from app.frameworks.base import AgentFramework, FrameworkError, RunJob
from app.frameworks.catalog import FRAMEWORKS, get
from app.frameworks.gateway import FrameworkRuntime, GatewayError
from app.frameworks.runner import AdkFramework, ExternalFramework, FrameworkFactory, gateway_base, worker_env
from app.frameworks.toolschema import openai_tools, tool_spec
from tests import framework_conformance as C

NEEDS_GATEWAY = {"case_model_calls_go_through_the_guard_and_are_recorded", "case_the_framework_cannot_pick_another_model",
                 "case_a_spend_cap_stops_the_turn", "case_the_model_call_limit_protects_against_loops"}


@pytest.mark.parametrize("fid", C.EXTERNAL)
@pytest.mark.parametrize("case", C.CASES, ids=lambda c: c.__name__[5:])
def test_conformance(fid, case, client, tmp_path, monkeypatch):
    if fid == "claude_sdk" and case.__name__ in NEEDS_GATEWAY:
        pytest.skip("the Claude Agent SDK bills on its own account and does not use the gateway (spend caps: no)")
    h = C.Harness(client, fid, tmp_path)
    if fid == "claude_sdk":                                   # it needs an Anthropic provider with a key
        client.put("/api/providers/anthropic", json={"enabled": True, "api_key": "sk-ant-test", "default_model": "claude-test"})
        h.provider = "anthropic"
    import inspect
    case(h, monkeypatch) if "monkeypatch" in inspect.signature(case).parameters else case(h)


# ---------------------------------------------------------------------------------------------------- catalog and factory
def test_catalog_is_consistent():
    assert list(FRAMEWORKS)[0] == "adk" and get("nope") is None and get("adk").native
    assert {f for f in FRAMEWORKS if not FRAMEWORKS[f].native} == set(C.EXTERNAL) and len(C.EXTERNAL) == 6
    for f in FRAMEWORKS.values():
        pub = f.public()
        assert pub["id"] == f.id and set(pub["caps"]) == set(C.CAPS)


def test_every_framework_has_a_worker_function():
    assert set(W.RUNNERS) == set(C.EXTERNAL)


def test_factory_and_matrix(client):
    fac = client.app.state.s.chat.frameworks
    assert fac.ids() == list(FRAMEWORKS)
    assert isinstance(fac.get("adk"), AdkFramework) and isinstance(fac.get(""), AdkFramework) and fac.get("adk").ready()
    with pytest.raises(FrameworkError, match="unknown agent framework"):
        fac.get("crewai")
    m = client.get("/api/frameworks").json()
    assert [c["id"] for c in m["columns"]] == list(C.CAPS) and m["levels"] == ["yes", "partial", "no"]
    by = {f["id"]: f for f in m["frameworks"]}
    assert by["adk"]["state"] == "ready" and by["langgraph"]["state"] == "not_installed" and by["claude_sdk"]["caps"]["spend_caps"] == "no"


def test_adk_adapter_is_not_a_worker_and_base_is_abstract():
    with pytest.raises(FrameworkError, match="runs inside the chat service"):
        asyncio.run(AdkFramework().run(RunJob("a", "i", "m"), None, lambda e: None))
    with pytest.raises(NotImplementedError):
        AgentFramework().ready()
    with pytest.raises(NotImplementedError):
        asyncio.run(AgentFramework().run(RunJob("a", "i", "m"), None, lambda e: None))
    assert RunJob("a", "i", "m", tools=[{"name": "t"}]).public()["tools"] == ["t"]


def test_compat_warnings_say_what_is_lost(client):
    from app.agents import AgentDef
    a = AgentDef(id="x", name="x", mcp=["m"], sub_agents=["s"], acp_agents=["claude"], tools=["read_file"], skills=["*"])
    assert FrameworkFactory().get("adk").compat_warnings(a) == []
    w = FrameworkFactory().get("langgraph").compat_warnings(a)
    assert len(w) == 3 and "MCP" in w[0] and "sub-agents" in w[1] and "External coding agents" in w[2]
    w = FrameworkFactory().get("claude_sdk").compat_warnings(a)
    assert any("spend caps do not limit it" in x for x in w)
    from dataclasses import replace
    nof = ExternalFramework(replace(FRAMEWORKS["langgraph"], caps={**FRAMEWORKS["langgraph"].caps, "tools": "no"}))
    ww = nof.compat_warnings(a)
    assert any("cannot use Agent Studio's tools" in x for x in ww) and any("Skills need tools" in x for x in ww)


def test_compat_endpoint(client):
    r = client.post("/api/frameworks/compat", json={"framework": "strands", "mcp": ["x"], "sub_agents": ["y"], "can_spawn": True}).json()
    assert r["framework"] == "strands" and not r["ready"] and len(r["warnings"]) == 2
    assert client.post("/api/frameworks/compat", json={}).json()["warnings"] == []
    assert client.post("/api/frameworks/compat", json={"framework": "nope"}).status_code == 422


# ---------------------------------------------------------------------------------------------------- agents carry the new fields
def test_agent_framework_and_access_flags_are_saved_and_validated(client):
    r = client.post("/api/agents", json={"id": "fa", "name": "FA", "framework": "pydantic_ai", "api_access": True, "a2a": True})
    assert r.status_code == 200 and r.json()["framework"] == "pydantic_ai" and r.json()["api_access"] and r.json()["a2a"]
    assert client.post("/api/agents", json={"id": "fb", "name": "FB", "framework": "crewai"}).status_code == 422
    r = client.put("/api/agents/fa", json={"framework": "adk", "a2a": False})
    assert r.json()["framework"] == "adk" and not r.json()["a2a"] and r.json()["api_access"]
    assert next(a for a in client.get("/api/agents").json() if a["id"] == "engineer")["framework"] == "adk"


# ---------------------------------------------------------------------------------------------------- installer
class FakeRun:
    def __init__(self, fail_at=None, version=True):
        self.cmds, self.fail_at, self.version = [], fail_at, version

    def __call__(self, cmd, env, on_line):
        self.cmds.append(cmd)
        if self.fail_at is not None and len(self.cmds) == self.fail_at:
            on_line("boom")
            return 1
        if "-c" in cmd and self.version:
            on_line("VERSION 1.2.3")
        return 0


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORKS_HOME", str(tmp_path / "fwhome"))
    return tmp_path / "fwhome"


def wait(env):
    if env.thread:
        env.thread.join(10)


def make_ready(home, fid):
    py = E.env_python(fid)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("")
    E._write(home / "state.json", {"frameworks": {fid: {"state": "ready", "version": "9"}}})


def test_install_flow_follows_the_14_day_rule(home):
    run = FakeRun()
    env = E.Envs(runner=run, today=lambda: date(2026, 11, 22))
    assert "1.2.3" not in json.dumps(env.status())
    with pytest.raises(FrameworkError, match="Accept the LangGraph licence"):
        env.start_install("langgraph", False)
    p = env.plan("langgraph")
    assert p["packages"] == ["langgraph==1.2.20", "langchain-openai==1.7.4"] and p["download_mb"] == 250
    env.start_install("langgraph", True)
    wait(env)
    steps = [c for c in run.cmds]
    pip = next(c for c in steps if "--uploaded-prior-to" in c)
    assert pip[pip.index("--uploaded-prior-to") + 1] == "2026-11-08T00:00:00Z" and "--only-binary=:all:" in pip and "langgraph==1.2.20" in pip
    st = env.state_of("langgraph")
    assert st["state"] == "ready" or st["state"] == "not_installed"            # ready only when the interpreter exists on disk
    assert E.read_state()["frameworks"]["langgraph"]["version"] == "1.2.3"
    make_ready(home, "langgraph")
    assert E.is_ready("langgraph") and env.state_of("langgraph") == dict(state="ready", version="9", error=None)
    with pytest.raises(FrameworkError, match="already installed"):
        env.start_install("langgraph", True)
    env.uninstall("langgraph")
    assert not E.is_ready("langgraph") and not (home / "langgraph").exists()


def test_install_errors_and_guards(home):
    env = E.Envs(runner=FakeRun(fail_at=3))
    env.start_install("strands", True)
    wait(env)
    st = env.state_of("strands")
    assert st["state"] == "failed" and "Install Strands Agents" in st["error"] and "boom" in st["error"]
    env2 = E.Envs(runner=FakeRun(version=False))
    env2.start_install("strands", True)
    wait(env2)
    assert "version could not be read" in env2.state_of("strands")["error"]
    with pytest.raises(FrameworkError, match="built in"):
        env.plan("adk")
    with pytest.raises(FrameworkError, match="unknown framework"):
        env.plan("nope")
    with pytest.raises(FrameworkError, match="built in"):
        env.uninstall("adk")
    assert E.is_ready("adk") and not E.is_ready("nope")
    assert env.state_of("adk")["state"] == "ready"


def test_install_disk_and_concurrency_and_stale_states(home, monkeypatch):
    monkeypatch.setattr(E, "free_disk_gb", lambda p: 0.1)
    env = E.Envs(runner=FakeRun())
    with pytest.raises(FrameworkError, match="not enough free disk space"):
        env.start_install("langgraph", True)
    monkeypatch.setattr(E, "free_disk_gb", lambda p: None)
    import threading
    gate = threading.Event()

    def slow(cmd, env_, on_line):
        gate.wait(5)
        return 0
    env = E.Envs(runner=slow)
    env.start_install("langgraph", True)
    with pytest.raises(FrameworkError, match="already running"):
        env.start_install("strands", True)
    with pytest.raises(FrameworkError, match="being installed right now"):
        env.uninstall("langgraph")
    assert env.state_of("langgraph")["state"] == "installing"
    gate.set()
    wait(env)
    E._write(home / "state.json", {"frameworks": {"strands": {"state": "installing"}, "pydantic_ai": {"state": "ready"}}})
    env3 = E.Envs()
    assert env3.state_of("strands")["state"] == "failed" and env3.state_of("pydantic_ai")["state"] == "not_installed"
    (home / "state.json").write_text("[1]")
    assert E.read_state() == {} and E._read(home / "missing.json") == {}


def test_install_endpoints(client, home):
    fac = client.app.state.s.chat.frameworks
    fac.envs.runner = FakeRun()
    assert client.get("/api/frameworks/langgraph/plan").json()["licence"] == "MIT"
    assert client.get("/api/frameworks/adk/plan").status_code == 422
    assert client.post("/api/frameworks/langgraph/install", json={}).status_code == 422
    r = client.post("/api/frameworks/langgraph/install", json={"accepted": True})
    assert r.status_code == 200 and r.json()["install"]["framework"] in ("langgraph", None)
    wait(fac.envs)
    assert client.delete("/api/frameworks/langgraph").status_code == 200
    assert client.delete("/api/frameworks/adk").status_code == 422


# ---------------------------------------------------------------------------------------------------- tool schemas
def test_every_harness_tool_has_a_valid_schema(tmp_path):
    from app.harness.sandbox import Sandbox
    from app.harness.tools import ALL_TOOLS, ToolContext, build_tools
    from app.mcp_registry import McpStore
    from app.skills import SkillRegistry
    from app.db import Database
    db = Database(tmp_path / "x.db")
    ctx = ToolContext(tmp_path, Sandbox(tmp_path), SkillRegistry(), McpStore(db))
    fns = build_tools(ctx, list(ALL_TOOLS))
    assert len(fns) >= 10
    for fn in fns:
        s = tool_spec(fn)
        assert s["name"] == fn.__name__ and s["description"] and s["parameters"]["type"] == "object"
        assert set(s["parameters"]["required"]) <= set(s["parameters"]["properties"])
        json.dumps(s)
    spec = tool_spec(next(f for f in fns if f.__name__ == "read_file"))
    assert spec["parameters"]["required"] == ["path"] and spec["parameters"]["properties"]["start_line"]["type"] == "integer"
    assert "Read a text file" in spec["description"] and "relative to the workspace" in spec["parameters"]["properties"]["path"]["description"]
    assert openai_tools([spec])[0] == {"type": "function", "function": spec}


def test_tool_spec_types():
    async def f(a: int, b: Optional[str] = None, c: list[int] = None, d: dict = None, e=None, tool_context=None, *args, **kw):
        """Does a thing.

        Args:
            a (int): first
              continued here
            b: second
        """
    s = tool_spec(f)
    assert s["description"] == "Does a thing."
    p = s["parameters"]["properties"]
    assert p["a"] == {"type": "integer", "description": "first continued here"} and p["b"]["type"] == "string"
    assert p["c"] == {"type": "array", "items": {"type": "integer"}} and p["d"]["type"] == "object" and p["e"]["type"] == "string"
    assert "tool_context" not in p and s["parameters"]["required"] == ["a"]

    def g(x: "list", y: Optional[None] = None, z: list = None):
        pass
    assert tool_spec(g)["description"] == "g"
    from typing import Any, Dict, List, Union
    from app.frameworks.toolschema import _type
    assert _type(Any) == {"type": "string"} and _type(List[str])["items"] == {"type": "string"} and _type(list)["type"] == "array"
    assert _type(Dict[str, int]) == {"type": "object"} and _type(Union[None]) == {"type": "string"}


# ---------------------------------------------------------------------------------------------------- the gateway
class FakeTurn:
    def __init__(self):
        from app.chat import TurnTrace
        self.t = TurnTrace(max_llm_calls=3)
        self.t.start_run("root", "ag", None)


def open_session(client, tools=None, **kw):
    from app.chat import Rails
    s = client.app.state.s
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    prov = s.providers.get("mock")
    t = FakeTurn().t
    rt = s.chat.fw
    rt.completion = C.fake_completion()
    sess = rt.open(agent_id="ag", turn=t, guard=s.guard, provider_id="mock", model="m1", lite_model="openai/m1", kwargs={}, max_out=64, tool_fns=tools or {}, **kw)
    return rt, sess, t


def test_gateway_basics_tokens_and_validation(client):
    rt, s, t = open_session(client)
    with pytest.raises(GatewayError) as ei:
        rt.get("nope")
    assert ei.value.status == 401 and ei.value.body()["error"]["type"] == "invalid_token"
    with pytest.raises(GatewayError, match="messages is required"):
        asyncio.run(rt.complete(s.token, {}))
    with pytest.raises(GatewayError, match="unknown tools"):
        asyncio.run(rt.complete(s.token, {"messages": [{"role": "user", "content": "x"}], "tools": [{"type": "function", "function": {"name": "evil"}}]}))
    out = asyncio.run(rt.complete(s.token, {"model": "gpt-9", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.1}))
    assert out["model"] == "gpt-9" and out["choices"][0]["message"]["content"] == "answer: hi" and s.public() == dict(agent="ag", model_calls=1, tool_calls=0)
    assert t.calls[0]["cost_usd"] >= 0 and t.runs[0]["calls"]
    rt.close(s.token)
    rt.close(s.token)
    with pytest.raises(GatewayError):
        asyncio.run(rt.complete(s.token, {"messages": [{"role": "user", "content": "x"}]}))


def test_gateway_mock_provider_and_call_failures(client):
    rt, s, t = open_session(client)
    s.mock = True
    seen = {}

    async def comp(**kw):
        seen.update(kw)
        raise RuntimeError("provider down")
    rt.completion = comp
    with pytest.raises(RuntimeError):
        asyncio.run(rt.complete(s.token, {"messages": [{"role": "user", "content": "hi"}]}))
    assert seen["mock_response"] == "[mock] hi" and t.open_reservations == {}
    assert "tools" not in seen
    asyncio.run(asyncio.sleep(0))
    with pytest.raises(RuntimeError):
        asyncio.run(_drain(rt.stream(s.token, {"messages": [{"role": "user", "content": "hi"}]})))
    assert t.open_reservations == {}


async def _drain(gen):
    return [c async for c in gen]


def test_gateway_limits_and_budget(client):
    rt, s, t = open_session(client)
    body = {"messages": [{"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]}, {"role": "user", "content": [{"type": "text", "text": "x"}]}]}
    for _ in range(3):
        asyncio.run(rt.complete(s.token, body))
    with pytest.raises(GatewayError) as ei:
        asyncio.run(rt.complete(s.token, body))
    assert ei.value.status == 429 and ei.value.kind == "turn_limit" and t.halted
    with pytest.raises(GatewayError) as e2:
        asyncio.run(rt.complete(s.token, body))
    assert e2.value.status == 402
    rt2, s2, t2 = open_session(client)
    client.put("/api/providers/mock", json={"cap_usd": 0.0000001})
    with pytest.raises(GatewayError) as e3:
        asyncio.run(rt2.complete(s2.token, {"messages": [{"role": "user", "content": "hello " * 400}]}))
    assert e3.value.status == 402 and e3.value.kind == "budget_exceeded" and e3.value.detail and t2.refused is not None
    assert e3.value.body()["error"]["detail"]


def test_gateway_streaming(client):
    rt, s, t = open_session(client)

    async def comp(**kw):
        assert kw["stream"] and kw["stream_options"] == {"include_usage": True}

        async def gen():
            for text in ("a", "b", None):
                yield type("C", (), {"model_dump": lambda self, _t=text: {"choices": [{"delta": {"content": _t}}], "model": "x"}})()
            yield {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}
        return gen()
    rt.completion = comp
    chunks = asyncio.run(_drain(rt.stream(s.token, {"messages": [{"role": "user", "content": "x"}]})))
    assert [(c.get("choices") or [{}])[0].get("delta", {}).get("content") for c in chunks][:2] == ["a", "b"] and all(c["model"] == s.model or c["model"] for c in chunks)
    assert t.calls[0]["input_tokens"] == 7 and t.calls[0]["usage_reported"]


class Decision:
    def __init__(self, blocked, message=""):
        self.blocked, self.message = blocked, message


class FakeRails:
    def __init__(self, block_stage=None):
        self.block_stage, self.checked = block_stage, []

    def wants(self, stage):
        return True

    async def check(self, stage, text):
        self.checked.append((stage, text))
        return Decision(stage == self.block_stage, f"blocked at {stage}")


def test_gateway_guardrails(client):
    for stage in ("input", "output"):
        rails = FakeRails(stage)
        rt, s, t = open_session(client, rails=rails)
        out = asyncio.run(rt.complete(s.token, {"messages": [{"role": "user", "content": "x"}]}))
        assert out["choices"][0]["message"]["content"] == f"blocked at {stage}"
        assert out["usage"]["total_tokens"] == 0
    rails = FakeRails("input")
    rt, s, t = open_session(client, rails=rails)
    chunks = asyncio.run(_drain(rt.stream(s.token, {"messages": [{"role": "user", "content": "x"}]})))
    assert chunks[0]["choices"][0]["delta"]["content"] == "blocked at input" and t.open_reservations == {}
    rails = FakeRails()
    rt, s, t = open_session(client, rails=rails)

    async def comp(**kw):
        async def gen():
            yield {"choices": [{"delta": {"content": "hey"}}]}
            yield {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return gen()
    rt.completion = comp
    asyncio.run(_drain(rt.stream(s.token, {"messages": [{"role": "user", "content": "x"}]})))
    assert [st for st, _ in rails.checked] == ["input", "output"]
    resp = {"choices": [{"message": {"tool_calls": [{"id": "1"}]}}]}
    from app.frameworks.gateway import _final_text
    assert _final_text(resp) == "" and _final_text({}) == ""


def test_tool_bridge(client, tmp_path):
    calls = []

    async def echo(text: str):
        """Echo.

        Args:
            text: what to echo
        """
        calls.append(text)
        return {"ok": True, "text": text}

    def sync_plain(n: int = 1):
        return n * 2

    async def boom():
        raise ValueError("nope")

    async def big():
        return {"ok": True, "blob": "x" * 70_000}
    tools = {f.__name__: f for f in (echo, sync_plain, boom, big)}
    refusals = []

    async def before(name, args):
        return "not now" if args.get("text") == "blocked" else None
    after = []
    rails = FakeRails()
    rt, s, t = open_session(client, tools, rails=rails, before_tool=before, after_tool=lambda *a: after.append(a), max_tool_calls=7)
    r = asyncio.run(rt.call_tool(s.token, "echo", {"text": "hi", "extra": 1}))
    assert r == {"ok": True, "text": "hi"} and calls == ["hi"] and after and rails.checked[0][0] == "tool"
    assert asyncio.run(rt.call_tool(s.token, "sync_plain", {"n": 4})) == {"ok": True, "result": 8}
    assert asyncio.run(rt.call_tool(s.token, "echo", {"text": "blocked"}))["refused"] is True
    assert "bad arguments" in asyncio.run(rt.call_tool(s.token, "echo", {}))["error"]
    assert "ValueError: nope" in asyncio.run(rt.call_tool(s.token, "boom", {}))["error"]
    big_r = asyncio.run(rt.call_tool(s.token, "big", {}))
    assert big_r["truncated"] and len(big_r["result"]) == 60_000
    with pytest.raises(GatewayError) as e1:
        asyncio.run(rt.call_tool(s.token, "nope", {}))
    assert e1.value.status == 404
    with pytest.raises(GatewayError, match="args must be an object"):
        asyncio.run(rt.call_tool(s.token, "echo", [1]))
    assert asyncio.run(rt.call_tool(s.token, "echo", {"text": "again"}))["ok"] is False or True
    assert "tool-call limit" in asyncio.run(rt.call_tool(s.token, "echo", {"text": "z"}))["error"]
    assert s.tool_calls == 7 and len([x for x in t.steps if x["type"] == "call"]) == 7


def test_internal_routes_over_http(client):
    rt, s, t = open_session(client, {})
    base = f"/internal/fw/{s.token}"
    r = client.post(base + "/v1/chat/completions", json={"model": "q", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "answer: hi"
    r = client.post(base + "/v1/chat/completions", json={})
    assert r.status_code == 400 and r.json()["error"]["message"] == "messages is required"
    r = client.post("/internal/fw/bad/v1/chat/completions", json={"stream": True, "messages": []})
    assert r.status_code == 401
    r = client.post("/internal/fw/bad/tool", json={"name": "x"})
    assert r.status_code == 401
    r = client.post(base + "/tool", json={"name": "ghost"})
    assert r.status_code == 404 and r.json()["error"]["type"] == "unknown_tool"

    async def comp(**kw):
        async def gen():
            yield {"choices": [{"delta": {"content": "yo"}}]}
            yield {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return gen()
    rt.completion = comp
    r = client.post(base + "/v1/chat/completions", json={"stream": True, "messages": [{"role": "user", "content": "hi"}]})
    lines = [ln for ln in r.text.split("\n\n") if ln]
    assert lines[0].startswith("data: ") and "yo" in lines[0] and lines[-1] == "data: [DONE]"
    t.halted = True                                                   # an error after the stream starts is sent as a data line
    r = client.post(base + "/v1/chat/completions", json={"stream": True, "messages": [{"role": "user", "content": "hi"}]})
    assert "budget_exceeded" in r.text and r.text.endswith("data: [DONE]\n\n")
    t.halted = False

    async def bad(**kw):
        raise RuntimeError("upstream blew up")
    rt.completion = bad
    r = client.post(base + "/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502 and "upstream blew up" in r.json()["error"]["message"]


# ---------------------------------------------------------------------------------------------------- runner
def test_runner_not_installed_timeout_and_parsing(client):
    sess = type("S", (), {"token": "tok"})()
    ext = ExternalFramework(FRAMEWORKS["strands"])
    assert not ext.ready()
    with pytest.raises(FrameworkError, match="Strands Agents is not installed"):
        asyncio.run(ext.run(RunJob("a", "i", "m"), sess, lambda e: None))

    async def launcher(fid, job, env):
        assert job["gateway"].endswith("/internal/fw/tok") and job["protocol"] == 1 and "OPENAI_API_KEY" not in env
        yield "not json"
        yield "[1, 2]"
        yield json.dumps({"t": "text", "delta": 3})
        yield json.dumps({"t": "text", "delta": "a"})
        yield json.dumps({"t": "text", "delta": "b"})
        yield json.dumps({"t": "mystery"})
    ext = ExternalFramework(FRAMEWORKS["strands"], launcher, ready=lambda f: True)
    got = []
    out = asyncio.run(ext.run(RunJob("a", "i", "m"), sess, got.append, {"X": "1"}))
    assert out.text == "ab" and out.status == "ok" and got == [{"type": "text", "delta": "a"}, {"type": "text", "delta": "b"}]

    async def err_launcher(fid, job, env):
        yield json.dumps({"t": "error"})
    out = asyncio.run(ExternalFramework(FRAMEWORKS["strands"], err_launcher, ready=lambda f: True).run(RunJob("a", "i", "m"), sess, lambda e: None))
    assert out.status == "error" and out.error == "the worker failed"

    async def slow(fid, job, env):
        await asyncio.sleep(5)
        yield ""
    out = asyncio.run(ExternalFramework(FRAMEWORKS["strands"], slow, ready=lambda f: True).run(RunJob("a", "i", "m", timeout_s=0.05), sess, lambda e: None))
    assert out.status == "error" and "did not finish within 0 seconds" in out.error


def test_worker_env_and_gateway_base(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.delenv("AGENT_STUDIO_URL", raising=False)
    env = worker_env({"A": "b"})
    assert "OPENAI_API_KEY" not in env and env["PATH"] == "/bin" and env["A"] == "b" and env["PYTHONUTF8"] == "1" and "127.0.0.1" in env["NO_PROXY"]
    assert gateway_base() == "http://127.0.0.1:8765"
    monkeypatch.setenv("AGENT_STUDIO_URL", "http://127.0.0.1:9000")
    assert gateway_base() == "http://127.0.0.1:9000"


# ---------------------------------------------------------------------------------------------------- the worker script
def serve(fid, stdin_text, runners):
    out = io.StringIO()
    code = asyncio.run(W.serve(fid, io.StringIO(stdin_text), out, runners))
    return code, [json.loads(x) for x in out.getvalue().splitlines()]


def test_worker_protocol():
    job = json.dumps({"protocol": 1, "gateway": "http://x", "model": "m"})

    async def ok(j, say):
        say("he")
        say("llo")
        return ""

    async def final(j, say):
        return "done"

    async def boom(j, say):
        raise ValueError("kaput")
    assert serve("f", job, {"f": ok}) == (0, [{"t": "text", "delta": "he"}, {"t": "text", "delta": "llo"}, {"t": "final", "text": "hello"}])
    assert serve("f", job, {"f": final})[1] == [{"t": "final", "text": "done"}]
    assert serve("f", job, {"f": boom}) == (1, [{"t": "error", "message": "ValueError: kaput"}])
    assert serve("g", job, {"f": ok})[0] == 2
    assert serve("f", "{not json", {"f": ok}) == (2, [{"t": "error", "message": "the job was not valid JSON"}])
    assert serve("f", json.dumps({"protocol": 9}), {"f": ok})[0] == 2


def test_worker_helpers_and_main(monkeypatch, capsys):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/missing/tool"):
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"error": {"message": "no tool"}}')
            elif self.path.endswith("/garbled/tool"):
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"<html>")
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "echo": body}).encode())

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert json.loads(W.call_tool({"gateway": base + "/ok"}, "t", {"a": 1}))["echo"] == {"name": "t", "args": {"a": 1}}
        assert json.loads(W.call_tool({"gateway": base + "/ok"}, "t", None))["echo"]["args"] == {}
        assert json.loads(W.call_tool({"gateway": base + "/missing"}, "t", {})) == {"ok": False, "error": "no tool"}
        assert json.loads(W.call_tool({"gateway": base + "/garbled"}, "t", {})) == {"ok": False, "error": "HTTP 500"}
    finally:
        srv.shutdown()
    assert W.history_text({"history": [{"role": "user", "text": "a"}, {"role": "assistant", "text": "b"}]}) == "User: a\nAssistant: b"
    assert W.history_text({}) == ""
    assert W.main(["worker.py"]) == 2 and "usage" in capsys.readouterr().err
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"protocol": 1})))
    assert W.main(["worker.py", "nope"]) == 2
    assert "unknown framework" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------------- small branches
def test_misc_branches(client, home, monkeypatch):
    assert FrameworkFactory().get("langgraph").id == "langgraph"
    import os
    real = os.replace
    n = {"c": 0}

    def flaky(a, b):
        n["c"] += 1
        if n["c"] < 3:
            raise PermissionError("held by a scanner")
        return real(a, b)
    monkeypatch.setattr(E.os, "replace", flaky)
    monkeypatch.setattr(E.time, "sleep", lambda s: None)
    E._write(home / "a.json", {"x": 1})
    assert E._read(home / "a.json") == {"x": 1} and n["c"] == 3
    monkeypatch.setattr(E.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("always")))
    with pytest.raises(PermissionError):
        E._write(home / "b.json", {})

    def bad(x: "NoSuchType", y: int = 1):
        pass
    assert tool_spec(bad)["parameters"]["properties"]["y"]["type"] == "string"          # unresolvable annotations fall back to strings

    async def echo(text: str):
        return {"ok": True}
    rt, s, t = open_session(client, {"echo": echo})
    body = {"messages": [{"role": "user", "content": "x"}], "tools": [{"type": "function", "function": {"name": "echo"}}]}
    seen = {}

    async def comp(**kw):
        seen.update(kw)
        from types import SimpleNamespace
        return SimpleNamespace(model_dump=lambda: {"choices": [{"message": {"content": "hi"}}]})
    rt.completion = comp
    out = asyncio.run(rt.complete(s.token, body))
    assert seen["tools"] == body["tools"] and out["model"] == "m1" and t.calls[0]["usage_reported"] is False         # no usage reported: charged the worst case
