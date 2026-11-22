"""API access: keys, the OpenAI-compatible API, A2A in both directions (the real a2a-sdk client against our server)."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.apiaccess import api as A
from a2a.helpers import new_text_message
from app.apiaccess.a2a import RemoteAgents, RemoteError, make_card, remote_tool
from app.apiaccess.keys import ApiKeys, KeyError_

BASE = "http://127.0.0.1:8765"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def rich(client, state, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    client.put("/api/access/config", json={"api_enabled": True, "a2a_enabled": True})
    r = client.post("/api/agents", json={"id": "helper", "name": "Helper", "description": "Answers questions", "tier": "flash", "api_access": True, "a2a": True})
    assert r.status_code == 200, r.text
    return client


def new_key(c, **kw):
    r = c.post("/api/access/keys", json={"name": "test", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def auth(key):
    return {"Authorization": f"Bearer {key['key']}"}


# ---------------------------------------------------------------------------------------------------- keys
def test_key_lifecycle(state):
    clock = Clock()
    ks = ApiKeys(state.db, clock)
    k = ks.create("ci", ["a"], 2, 1)
    assert k["key"].startswith("asx_") and k["prefix"] == k["key"][:17] and "hash" not in json.dumps(ks.list()) and k["key"] not in json.dumps(ks.list())
    assert ks.verify(k["key"])["id"] == k["id"] and ks.verify(k["key"] + "x") is None and ks.verify(None) is None and ks.verify("asx_zzzzzzzz_" + "a" * 30) is None
    assert ks.verify("asx_%s_%s" % (k["id"], "a" * 30)) is None
    live = ks.verify(k["key"])
    assert ks.allows(live, "a") and not ks.allows(live, "b") and ks.allows({"agents": ["*"]}, "zzz")
    assert ks.hit(live) is None and ks.hit(live) is None and ks.hit(live) >= 1
    clock.t += 61
    assert ks.hit(live) is None and ks.list()[0]["uses"] == 3
    clock.t += 2 * 86400
    assert ks.verify(k["key"]) is None                                    # expired
    clock.t -= 2 * 86400
    r = ks.rotate(k["id"])
    assert r["key"] != k["key"] and ks.verify(k["key"]) is None and ks.verify(r["key"]) is not None and r["id"] == k["id"] and r["expires"] is not None
    u = ks.update(k["id"], {"name": "renamed", "rate_per_min": 9, "agents": ["x"], "revoked": True})
    assert u["name"] == "renamed" and u["revoked"] and ks.verify(r["key"]) is None
    assert ks.rotate(k["id"])["revoked"] is False
    ks.delete(k["id"])
    for fn, a in ((ks.delete, ("nope",)), (ks.rotate, ("nope",)), (ks.update, ("nope", {}))):
        with pytest.raises(KeyError):
            fn(*a)
    assert ks.list() == []


def test_key_validation(state):
    ks = ApiKeys(state.db)
    for kw, msg in [(dict(name=" "), "give the key a name"), (dict(name="x" * 61), "give the key a name"), (dict(name="a", rate_per_min=0), "requests per minute"),
                    (dict(name="a", expires_days=0), "expiry"), (dict(name="a", expires_days=9999), "expiry"), (dict(name="a", agents=[""]), "choose which agents")]:
        with pytest.raises(KeyError_, match=msg):
            ks.create(**kw)
    for i in range(50):
        ks.create(f"k{i}")
    with pytest.raises(KeyError_, match="at most 50"):
        ks.create("one more")
    k = ks.list()[0]
    for bad in ({"name": ""}, {"rate_per_min": 0}, {"rate_per_min": "x"}, {"agents": []}, {"agents": [1]}):
        with pytest.raises(KeyError_):
            ks.update(k["id"], bad)


def test_admin_endpoints(client):
    cfg = client.get("/api/access/config").json()
    assert not cfg["api_enabled"] and cfg["base_url"].endswith("/v1") and cfg["keys"] == [] and cfg["remotes"] == [] and any(a["id"] == "engineer" for a in cfg["agents"])
    assert client.put("/api/access/config", json={"api_default_provider": "ghost"}).status_code == 422
    assert client.put("/api/access/config", json={"api_enabled": "yes"}).status_code == 422
    assert client.put("/api/access/config", json={"api_enabled": True, "api_default_provider": "mock"}).json() == dict(api_enabled=True, a2a_enabled=False, api_default_provider="mock")
    k = new_key(client)
    assert client.post("/api/access/keys", json={"name": ""}).status_code == 422
    assert client.put(f"/api/access/keys/{k['id']}", json={"name": "n2"}).json()["name"] == "n2"
    assert client.put(f"/api/access/keys/{k['id']}", json={"name": ""}).status_code == 422
    assert client.put("/api/access/keys/nope", json={}).status_code == 404
    assert client.post(f"/api/access/keys/{k['id']}/rotate").json()["key"] != k["key"]
    assert client.post("/api/access/keys/nope/rotate").status_code == 404
    assert client.delete(f"/api/access/keys/{k['id']}").status_code == 200 and client.delete(f"/api/access/keys/{k['id']}").status_code == 404
    assert A.access_config(client.app.state.s.db)["default_provider"] == "mock"
    client.app.state.s.db.set_setting("access_config", "not json")
    assert A.access_config(client.app.state.s.db)["api_enabled"] is False
    with pytest.raises(ValueError, match="unknown setting"):
        A.set_access_config(client.app.state.s.db, {"nope": 1})


# ---------------------------------------------------------------------------------------------------- OpenAI-compatible API
def test_the_api_is_off_and_needs_a_key(client):
    r = client.get("/v1/models")
    assert r.status_code == 403 and r.json()["error"]["code"] == "api_disabled"
    client.put("/api/access/config", json={"api_enabled": True})
    for h in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}):
        r = client.get("/v1/models", headers=h)
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"


def test_models_are_the_agents_that_opted_in_and_the_key_may_call(rich):
    rich.post("/api/agents", json={"id": "other", "name": "Other", "api_access": True})
    k_all, k_one = new_key(rich), new_key(rich, agents=["other"])
    assert sorted(m["id"] for m in rich.get("/v1/models", headers=auth(k_all)).json()["data"]) == ["helper", "other"]
    assert [m["id"] for m in rich.get("/v1/models", headers=auth(k_one)).json()["data"]] == ["other"]
    assert rich.get("/v1/models", headers=auth(k_all)).json()["data"][0]["object"] == "model"


def chat(c, key, **body):
    return c.post("/v1/chat/completions", headers=auth(key), json={"model": "helper", "messages": [{"role": "user", "content": "ping"}], **body})


def test_chat_completion(rich):
    k = new_key(rich)
    r = chat(rich, k)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["object"] == "chat.completion" and j["model"] == "helper" and j["choices"][0]["message"]["content"] and j["usage"]["total_tokens"] > 0
    assert j["x_agent_studio"]["trace_id"] and j["choices"][0]["finish_reason"] == "stop"
    # earlier turns become context; system messages are labelled instructions
    r = chat(rich, k, messages=[{"role": "system", "content": "Be terse"}, {"role": "user", "content": "one"}, {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
                                {"role": "user", "content": [{"type": "text", "text": "three"}]}])
    assert r.status_code == 200
    sess = rich.app.state.s.chat.store.list()[0]
    first_user = [m for m in rich.app.state.s.chat.store.messages(sess["id"]) if m["role"] == "user"][0]["text"]
    assert "Instructions: Be terse" in first_user and "Assistant: two" in first_user and first_user.endswith("three")


def test_chat_completion_errors(rich):
    k = new_key(rich, agents=["helper"])
    cases = [(dict(model="engineer"), 404, "model_not_found"), (dict(model="nope"), 404, "model_not_found"), (dict(model=5), 404, "model_not_found"),
             (dict(tools=[{"type": "function"}]), 400, "tools_not_supported"), (dict(functions=[{}]), 400, "tools_not_supported"),
             (dict(messages=[]), 400, "invalid_messages"), (dict(messages=[{"role": "assistant", "content": "x"}]), 400, "invalid_messages"),
             (dict(messages=[{"role": "user", "content": "  "}]), 400, "invalid_messages"), (dict(messages="x"), 400, "invalid_messages")]
    for body, status, code in cases:
        r = chat(rich, k, **body)
        assert r.status_code == status and r.json()["error"]["code"] == code, (body, r.text)
    rich.put("/api/agents/helper", json={"api_access": False})
    assert chat(rich, k).status_code == 404
    rich.put("/api/agents/helper", json={"api_access": True})
    rich.put("/api/providers/mock", json={"cap_usd": 0.0000001})
    r = chat(rich, k)
    assert r.status_code == 402 and r.json()["error"]["type"] == "insufficient_quota"


def test_rate_limit(rich):
    k = new_key(rich, rate_per_min=1)
    assert chat(rich, k).status_code == 200
    r = chat(rich, k)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limit_exceeded" and int(r.headers["retry-after"]) >= 1


def test_streaming(rich):
    k = new_key(rich)
    r = chat(rich, k, stream=True, stream_options={"include_usage": True})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(x[6:]) for x in r.text.split("\n\n") if x.startswith("data: {")]
    assert events[0]["choices"][0]["delta"]["role"] == "assistant" and r.text.rstrip().endswith("data: [DONE]")
    text = "".join(e["choices"][0]["delta"].get("content") or "" for e in events if e["choices"])
    assert text and events[-1]["choices"][0]["finish_reason"] == "stop" and events[-1]["usage"]["total_tokens"] > 0
    r = chat(rich, k, stream=True)
    assert "usage" not in [json.loads(x[6:]) for x in r.text.split("\n\n") if x.startswith("data: {")][-1]


def test_streaming_errors_and_no_provider(rich, state, monkeypatch):
    k = new_key(rich)
    rich.put("/api/providers/mock", json={"cap_usd": 0.0000001})
    r = chat(rich, k, stream=True)
    assert "insufficient_quota" in r.text and r.text.rstrip().endswith("[DONE]")
    rich.put("/api/providers/mock", json={"cap_usd": 1000, "enabled": False})
    assert chat(rich, k).status_code == 503

    async def boom(*a, **kw):
        raise RuntimeError("model fell over")
    rich.put("/api/providers/mock", json={"enabled": True})
    monkeypatch.setattr(state.chat, "send", boom)
    r = chat(rich, k)
    assert r.status_code == 502 and r.json()["error"]["type"] == "server_error"
    assert "server_error" in chat(rich, k, stream=True).text
    async def gone(*a, **kw):
        raise KeyError("x")
    monkeypatch.setattr(state.chat, "send", gone)
    assert chat(rich, k).status_code == 404


def test_streaming_without_deltas_sends_the_whole_reply(rich, state, monkeypatch):
    k = new_key(rich)

    async def send(*a, emit=None, **kw):
        return dict(reply="whole answer", usage={"input_tokens": 1, "output_tokens": 2}, halted={"error": "x"}, trace_id="t", warnings=[])
    monkeypatch.setattr(state.chat, "send", send)
    r = chat(rich, k, stream=True)
    assert "whole answer" in r.text and '"finish_reason": "length"' in r.text
    j = chat(rich, k).json()
    assert j["choices"][0]["finish_reason"] == "length"


def test_default_provider_and_agent_provider(rich, state):
    rich.put("/api/access/config", json={"api_default_provider": "mock"})
    a = state.agents.get("helper")
    assert A.pick_provider(state, a) == "mock"
    a.provider = "openai"
    assert A.pick_provider(state, a) == "openai"
    assert A.text_of(5) == "" and A.text_of([{"type": "image"}, "x"]) == ""


# ---------------------------------------------------------------------------------------------------- A2A
def asgi(client):
    return httpx.ASGITransport(app=client.app)


def test_a2a_needs_switch_key_and_agent_opt_in(rich):
    k = new_key(rich)
    url = "/a2a/helper/.well-known/agent-card.json"
    assert rich.get(url).status_code == 401
    assert rich.get(url, headers={"Authorization": "Bearer asx_deadbeef_" + "a" * 30}).status_code == 401
    ok = rich.get(url, headers=auth(k))
    assert ok.status_code == 200
    card = ok.json()
    assert card["name"] == "Helper" and card["supportedInterfaces"][0]["url"].endswith("/a2a/helper/") and "bearer" in card["securitySchemes"]
    assert rich.get("/a2a/engineer/.well-known/agent-card.json", headers=auth(k)).status_code == 404
    assert rich.get("/a2a/", headers=auth(k)).status_code == 404
    rich.put("/api/access/config", json={"a2a_enabled": False})
    assert rich.get(url, headers=auth(k)).status_code == 403
    rich.put("/api/access/config", json={"a2a_enabled": True})
    rich.put("/api/agents/helper", json={"description": "Changed"})
    assert rich.get(url, headers=auth(k)).json()["description"] == "Changed"          # the card follows the agent


def register_self(rich, key, rid="peer"):
    rich.app.state.s.a2a_remotes.transport = asgi(rich)
    r = rich.post("/api/access/remotes", json={"id": rid, "label": "Peer", "url": f"{BASE}/a2a/helper/", "token": key["key"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_a2a_round_trip_with_the_official_client(rich, state):
    k = new_key(rich)
    r = register_self(rich, k)
    assert r["has_token"] and "token" not in r and r["url"].endswith("/a2a/helper/")
    t = rich.post("/api/access/remotes/peer/test").json()
    assert t["ok"] and t["name"] == "Helper"
    out = asyncio.run(state.a2a_remotes.ask("peer", "hello over a2a"))
    assert out["ok"] and "mock" in out["reply"].lower() or out["reply"]
    assert any(s["id"].startswith("a2a-") for s in state.chat.store.list())                  # it ran as a chat, visible in the app
    tool = remote_tool(state.a2a_remotes, ["peer"])
    res = asyncio.run(tool("peer", "again"))
    assert res["ok"] and "peer" in tool.__doc__
    assert asyncio.run(tool("ghost", "x"))["ok"] is False


def test_a2a_client_failures(rich, state):
    k = new_key(rich)
    state.a2a_remotes.transport = asgi(rich)
    state.a2a_remotes.add("badtoken", "Bad", f"{BASE}/a2a/helper/", "asx_deadbeef_" + "a" * 30)
    assert rich.post("/api/access/remotes/badtoken/test").json()["ok"] is False
    with pytest.raises(RemoteError, match="the remote agent failed"):
        asyncio.run(state.a2a_remotes.ask("badtoken", "x"))
    state.a2a_remotes.add("notoken", "None", f"{BASE}/a2a/helper/")
    assert rich.post("/api/access/remotes/notoken/test").json()["ok"] is False
    with pytest.raises(RemoteError, match="no enabled remote agent 'ghost'"):
        asyncio.run(state.a2a_remotes.ask("ghost", "x"))
    assert rich.post("/api/access/remotes/ghost/test").status_code == 404


def test_a2a_agent_turned_off_or_failing(rich, state, monkeypatch):
    k = new_key(rich)
    register_self(rich, k)
    rich.put("/api/providers/mock", json={"cap_usd": 0.0000001})
    with pytest.raises(RemoteError):
        asyncio.run(state.a2a_remotes.ask("peer", "capped"))             # the budget stop comes back as a failed task with the reason
    rich.put("/api/providers/mock", json={"cap_usd": 1000})

    async def boom(*a, **kw):
        raise RuntimeError("engine fell over")
    monkeypatch.setattr(state.chat, "send", boom)
    with pytest.raises(RemoteError, match="reported a failure: Something went wrong"):
        asyncio.run(state.a2a_remotes.ask("peer", "boom"))


def test_a2a_cancel_stops_the_run(rich, state, monkeypatch):
    from a2a.server.agent_execution import RequestContext
    from app.apiaccess.a2a import StudioExecutor

    class EventQueue:
        def __init__(self):
            self.events = []

        async def enqueue_event(self, e):
            self.events.append(e)
    ex = StudioExecutor(state, "helper")
    ran = asyncio.Event()

    async def slow(*a, **kw):
        ran.set()
        await asyncio.sleep(30)
    monkeypatch.setattr(state.chat, "send", slow)

    class Ctx:
        task_id, context_id, current_task, message = "t1", "c1", None, new_text_message("go")

        def get_user_input(self):
            return "go"

    async def go():
        q = EventQueue()
        t = asyncio.ensure_future(ex.execute(Ctx(), q))
        await ran.wait()
        await ex.cancel(Ctx(), q)
        await asyncio.wait_for(t, 5)
        assert ex.running == {}
        t2 = asyncio.ensure_future(ex.execute(type("C2", (), {"task_id": "t2", "context_id": "c2", "current_task": object(), "get_user_input": lambda self: "  "})(), EventQueue()))
        await asyncio.wait_for(t2, 5)
        await ex.cancel(type("C3", (), {"task_id": "none", "context_id": "c"})(), EventQueue())            # nothing running: still fine
    asyncio.run(go())


def test_remote_registry_and_tool_exposure(rich, state, tmp_path):
    rem = state.a2a_remotes
    for rid, url, msg in [("X", "http://a", "id must be"), ("ok_id", "ftp://a", "URL must start"), ("ok_id", "http://", "URL must start")]:
        with pytest.raises(RemoteError, match=msg):
            rem.add(rid, "l", url)
    r = rem.add("ok_id", "", "http://remote.example/agent")
    assert r["label"] == "ok_id" and r["url"] == "http://remote.example/agent/" and rem.enabled_ids() == ["ok_id"]
    with pytest.raises(RemoteError, match="already exists"):
        rem.add("ok_id", "l", "http://x")
    u = rem.update("ok_id", {"label": "New", "enabled": False, "token": "t"})
    assert u["label"] == "New" and not u["enabled"] and u["has_token"] and rem.enabled_ids() == []
    assert not rem.update("ok_id", {"token": ""})["has_token"] and rem.update("ok_id", {})["label"] == "New"
    with pytest.raises(KeyError):
        rem.update("nope", {})
    for i in range(29):
        rem.add(f"r{i:02d}", "l", "http://x")
    with pytest.raises(RemoteError, match="at most 30"):
        rem.add("r99", "l", "http://x")
    assert rich.put("/api/access/remotes/nope", json={}).status_code == 404
    assert rich.delete("/api/access/remotes/nope").status_code == 404
    assert rich.delete("/api/access/remotes/ok_id").status_code == 200
    assert rich.post("/api/access/remotes", json={"id": "!"}).status_code == 422
    assert rich.put("/api/access/remotes/r00", json={"label": "z"}).json()["label"] == "z"
    # the tool exists only when a remote agent is enabled
    from app.harness.sandbox import Sandbox
    from app.harness.tools import ToolContext, build_tools
    ctx = ToolContext(tmp_path, Sandbox(tmp_path), state.skills, state.mcp, remotes=rem)
    assert [f.__name__ for f in build_tools(ctx, ["call_remote_agent"])] == ["call_remote_agent"]
    ctx = ToolContext(tmp_path, Sandbox(tmp_path), state.skills, state.mcp, remotes=RemoteAgents(state.db))
    for r_ in rem.list():
        rem.delete(r_["id"])
    assert build_tools(ctx, ["call_remote_agent"]) == []


def test_card_lists_tools_and_framework(state):
    from app.agents import AgentDef
    c = make_card(AgentDef(id="x", name="X", tools=["read_file"], framework="strands"), BASE)
    assert "read_file" in c.skills[0].description and list(c.skills[0].tags) == ["agent-studio", "strands"]
    assert make_card(AgentDef(id="y", name="Y"), BASE).description.startswith("Y, an Agent Studio agent")


# ---------------------------------------------------------------------------------------------------- AI-BOM and discovery
def test_the_ai_bom_lists_frameworks_and_access_without_secrets(rich, state):
    from app.aibom import builder as B
    from app.aibom.validate import validate_bom
    k = new_key(rich, name="SECRET-KEY-NAME-XYZ")
    state.a2a_remotes.add("peer", "Peer", "https://secret-remote.example/agents/", "REMOTE-TOKEN-123")
    state.workflows.save({"id": "wf_x", "name": "WF", "nodes": [{"id": "s", "type": "input"}], "edges": []})
    state.workflows.save({"id": "wf_hook", "name": "Hooked", "nodes": [{"id": "w", "type": "webhook"}], "edges": []})
    hook_secret = state.wf_hooks.create("wf_hook")
    state.wf_config.set_credential({"id": "tok", "value": "CRED-SECRET-VALUE"})
    state.wf_sources.loader = lambda src, base: ({}, "https://api.example.com", {"o": dict(operation_id="o", method="get", path="/o", summary="", parameters=[], body=None)})
    state.wf_sources.add("pets", "Pets", "https://x/openapi.json", token="Bearer API-TOKEN-1")
    rich.put("/api/agents/helper", json={"framework": "strands"})
    rich.put("/api/harness/policy", json={"web_enabled": True, "hooks": [{"event": "pre_tool", "action": "block", "message": "HOOK-MESSAGE-SECRET"}]})
    for level in ("public", "internal", "full"):
        bom = B.build_full(state, level)
        assert validate_bom(bom)["ok"]
        text = json.dumps(bom)
        for secret in (k["key"], "SECRET-KEY-NAME-XYZ", "secret-remote.example", "REMOTE-TOKEN-123", "API-TOKEN-1", "HOOK-MESSAGE-SECRET", hook_secret, "CRED-SECRET-VALUE"):
            assert secret not in text, (level, secret)
        svc = next(s for s in bom["services"] if s["bom-ref"] == "agent-access")
        props = {p["name"].removeprefix("agentstudio:").replace("-", "_"): p["value"] for p in svc["properties"]}
        assert props["api_enabled"] == "true" and props["api_keys"] == "1" and props["workflows"] == "2" and props["workflow_webhook_addresses"] == "1" and props["workflow_http_credentials"] == "1" and props["workflow_webhooks_enabled"] == "false" and props["workflow_triggers"] == '["input", "webhook"]' and props["web_tools"] == "true" and props["hooks"] == "1" and "strands" in props["frameworks_in_use"]
        ag = next(s for s in bom["services"] if s["bom-ref"] == "agent:helper")
        names = {p["name"]: p["value"] for p in ag["properties"]}
        assert names["agentstudio:framework"] == "strands" and names["agentstudio:api-access"] == "true"
    rich.put("/api/agents/helper", json={"framework": "adk"})
