"""OpenAPI documents become MCP servers (read-only by default); MCP failures raise issues; debug runs start from them."""
import asyncio
import json

import httpx
import pytest

from app import mcp_openapi as M
from app.mcp_registry import build_toolsets

SPEC = {
    "openapi": "3.0.3", "info": {"title": "Items API", "version": "1"},
    "servers": [{"url": "https://api.example.test/v1"}],
    "paths": {
        "/items": {
            "get": {"operationId": "listItems", "summary": "List items",
                    "parameters": [{"name": "limit", "in": "query", "required": False}]},
            "post": {"operationId": "createItem", "summary": "Create an item",
                     "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"name": {"type": "string"}}}}}}},
        },
        "/items/{id}": {"get": {"operationId": "getItem", "summary": "One item", "parameters": [{"name": "id", "in": "path", "required": True}]}},
    },
}


@pytest.fixture
def api(tmp_path, monkeypatch):
    spec = tmp_path / "items.json"
    spec.write_text(json.dumps(SPEC), encoding="utf-8")
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, str(req.url), req.headers.get("authorization")))
        return httpx.Response(200, json={"ok": True, "path": req.url.path})
    monkeypatch.setattr(M, "TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(M, "_allow_write", False)
    monkeypatch.setenv("HEADER_AUTHORIZATION", "Bearer test-token")
    M.configure(str(spec))
    yield seen
    M.TRANSPORT = None


def test_operations_are_listed_and_described(api):
    ops = M.list_operations()
    assert ops["api"] == "Items API" and ops["base_url"] == "https://api.example.test/v1"
    assert {o["operation_id"] for o in ops["operations"]} == {"listitems", "createitem", "getitem"}
    d = M.describe_operation("createitem")
    assert d["ok"] and d["allowed"] is False and d["body"] == ["name"]
    assert M.describe_operation("nope")["ok"] is False


def test_read_calls_work_with_path_query_and_auth(api):
    r = M.call_operation("getitem", json.dumps({"id": 7}))
    assert r["ok"] and r["status"] == 200 and api[-1][1] == "https://api.example.test/v1/items/7" and api[-1][2] == "Bearer test-token"
    M.call_operation("listitems", json.dumps({"limit": 5}))
    assert api[-1][1].endswith("/v1/items?limit=5")


def test_writes_are_refused_unless_allowed(api, monkeypatch):
    r = M.call_operation("createitem", json.dumps({"name": "x"}))
    assert r["ok"] is False and "write operation" in r["error"] and not any(m == "POST" for m, _, _ in api)
    monkeypatch.setattr(M, "_allow_write", True)
    assert M.call_operation("createitem", json.dumps({"name": "x"}))["ok"] is True


def test_bad_arguments_are_reported(api):
    assert "missing path parameter" in M.call_operation("getitem", "{}")["error"]
    assert "JSON object" in M.call_operation("getitem", "[1]")["error"]


def test_endpoint_registers_a_spec_file_and_rejects_junk(client, tmp_path):
    spec = tmp_path / "items.json"
    spec.write_text(json.dumps(SPEC), encoding="utf-8")
    r = client.post("/api/mcp/openapi", json={"id": "items-api", "label": "Items API", "spec": str(spec)})
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["operations"] == 3 and m["enabled"] and m["id"] == "items-api" and "read-only" in m["description"]
    junk = tmp_path / "junk.json"
    junk.write_text('{"hello": 1}', encoding="utf-8")
    assert client.post("/api/mcp/openapi", json={"id": "junk-api", "label": "J", "spec": str(junk)}).status_code == 422
    assert client.post("/api/mcp/openapi", json={"id": "rel-api", "label": "R", "spec": "relative/spec.yaml"}).status_code == 422


def test_a_failed_mcp_server_raises_an_issue_with_a_debug_action(state, tmp_path):
    state.mcp.add("broken-tool", "Broken tool", "", "stdio", "definitely-not-a-real-command-xyz", [], None, {}, enabled=True)
    warns = []
    asyncio.run(build_toolsets(state.mcp, ["broken-tool"], str(tmp_path / "ws"), warns))
    issues = [i for i in state.issues.list() if i["kind"] == "mcp_failed"]
    assert issues and issues[0]["source"] == "broken-tool"
    assert issues[0]["action"] == {"type": "debug_mcp", "mcp": "broken-tool"} and "Test" in issues[0]["suggestion"]
    # the same problem again is not raised twice
    asyncio.run(build_toolsets(state.mcp, ["broken-tool"], str(tmp_path / "ws"), []))
    assert len([i for i in state.issues.list() if i["source"] == "broken-tool"]) == 1


def test_debug_run_starts_and_is_reused(state, client, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 64})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    state.mcp.add("broken-two", "Broken two", "", "stdio", "definitely-not-a-real-command-xyz", [], None, {}, enabled=True)
    asyncio.run(build_toolsets(state.mcp, ["broken-two"], str(tmp_path / "ws"), []))
    issue = next(i for i in state.issues.list() if i["source"] == "broken-two")
    from app.main import start_debug_run

    async def go():
        j1 = start_debug_run(state, issue)
        j2 = start_debug_run(state, state.issues.get(issue["id"]))           # already debugging: same run
        return j1, j2
    j1, j2 = asyncio.run(go())
    assert j1 and j1 == j2
    assert client.post(f"/api/issues/{issue['id']}/resolve").status_code == 200
    assert client.post("/api/issues/nope/debug").status_code == 404


def test_an_agent_can_propose_an_openapi_server_but_it_waits_for_approval(state, tmp_path):
    import asyncio as _a
    from app.harness.tools import ToolContext, build_tools
    from app.harness.sandbox import Sandbox
    spec = tmp_path / "items.json"
    spec.write_text(json.dumps(SPEC), encoding="utf-8")
    ctx = ToolContext(tmp_path, Sandbox(tmp_path), state.skills, state.mcp)
    tool = build_tools(ctx, ["propose_openapi_mcp"])[0]
    r = _a.run(tool(id="items-proposed", label="Items", description="test items", spec=str(spec), base_url=""))
    assert r["ok"] and r["status"] == "pending_approval" and r["operations"] == 3
    m = state.mcp.get("items-proposed")
    assert m.status == "pending_approval" and not m.enabled and m.requires_env == ["HEADER_AUTHORIZATION"]
    assert "OPENAPI_ALLOW_WRITE" in m.env and m.env["OPENAPI_ALLOW_WRITE"] == "0"       # agents never get write access
    bad = _a.run(tool(id="bad-proposed", label="Bad", description="x", spec="relative/spec.yaml", base_url=""))
    assert bad["ok"] is False and "absolute" in bad["error"]
    junk = tmp_path / "junk.json"
    junk.write_text('{"nope": true}', encoding="utf-8")
    assert _a.run(tool(id="junk-proposed", label="J", description="x", spec=str(junk), base_url=""))["ok"] is False
