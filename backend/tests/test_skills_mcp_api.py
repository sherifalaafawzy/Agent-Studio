"""Skills + MCP servers as first-class pages: create/edit skills, add/test servers."""
import json


def test_create_edit_delete_skill(client):
    body = {"name": "release-checklist", "description": "the user asks to cut a release", "body": "## Steps\n1. bump", "mcp": ["git"]}
    r = client.post("/api/skills", json=body)
    assert r.status_code == 200 and r.json()["body"].startswith("## Steps") and r.json()["mcp"] == ["git"]
    assert client.post("/api/skills", json=body).status_code == 422                         # exists -> needs overwrite
    r = client.post("/api/skills", json={**body, "body": "## Steps\n1. tag", "overwrite": True})
    assert r.status_code == 200 and "tag" in client.get("/api/skills/release-checklist").json()["body"]
    assert next(s for s in client.get("/api/skills").json() if s["name"] == "release-checklist")["source"] == "user"
    assert client.post("/api/skills", json={**body, "name": "Bad Name"}).status_code == 422
    assert client.post("/api/skills", json={**body, "name": "x2", "description": " "}).status_code == 422
    builtin = next(s["name"] for s in client.get("/api/skills").json() if s["source"] == "builtin")
    assert client.post("/api/skills", json={**body, "name": builtin, "overwrite": True}).status_code == 422   # built-ins are protected
    assert client.delete("/api/skills/release-checklist").status_code == 200


def test_add_mcp_servers_and_rules(client, state):
    r = client.post("/api/mcp", json={"id": "my-tool", "label": "My tool", "description": "does x", "command": "npx",
                                      "args": ["-y", "pkg@1.0.0"], "env": {"API_TOKEN": "secret-1"}})
    assert r.status_code == 200
    m = r.json()
    assert m["enabled"] and m["ready"] and m["status"] == "active" and m["requires_env"] == ["API_TOKEN"] and m["env_set"] == {"API_TOKEN": True}
    assert "secret-1" not in json.dumps(client.get("/api/mcp").json())                    # values never returned
    assert client.post("/api/mcp", json={"id": "my-tool", "command": "npx"}).status_code == 422
    assert client.post("/api/mcp", json={"id": "x1", "transport": "stdio"}).status_code == 422          # no command
    assert client.post("/api/mcp", json={"id": "x2", "transport": "http", "url": "ftp://x"}).status_code == 422
    assert client.post("/api/mcp", json={"id": "Bad Id", "command": "npx"}).status_code == 422
    r = client.post("/api/mcp", json={"id": "remote", "transport": "http", "url": "https://example.invalid/mcp",
                                      "env": {"HEADER_AUTHORIZATION": "Bearer t", "HEADER_X_API_KEY": "k"}})
    assert r.status_code == 200 and r.json()["transport"] == "http"
    assert client.delete("/api/mcp/my-tool").status_code == 200


def test_mcp_test_endpoint_reports_failures(client):
    client.post("/api/mcp", json={"id": "broken", "command": "definitely-not-a-real-binary-xyz", "args": []})
    r = client.post("/api/mcp/broken/test").json()
    assert r["ok"] is False and "broken" in r["error"]
    client.put("/api/mcp/broken", json={"enabled": False})
    assert "disabled" in client.post("/api/mcp/broken/test").json()["error"]
    assert client.post("/api/mcp/nope/test").status_code == 404


def test_http_headers_are_built_from_header_env(state, monkeypatch):
    import asyncio
    from app import mcp_registry
    seen = {}

    class FakeParams:
        def __init__(self, url, headers, timeout):
            seen["headers"] = headers

    class FakeToolset:
        def __init__(self, **kw): pass
        async def get_tools(self): return []
        async def close(self): pass

    import google.adk.tools.mcp_tool.mcp_session_manager as sm
    import google.adk.tools.mcp_tool.mcp_toolset as mt
    monkeypatch.setattr(sm, "StreamableHTTPConnectionParams", FakeParams)
    monkeypatch.setattr(mt, "McpToolset", FakeToolset)
    state.mcp.add("remote", "Remote", "", "http", None, [], "https://example.invalid/mcp",
                  {"HEADER_AUTHORIZATION": "Bearer t", "HEADER_X_API_KEY": "k"})
    asyncio.run(mcp_registry.build_toolsets(state.mcp, ["remote"], ".", []))
    assert seen["headers"] == {"Authorization": "Bearer t", "X-Api-Key": "k"}


def test_builtin_servers_are_refreshed_from_the_catalog_keeping_secrets(tmp_path):
    from app.db import Database
    from app.mcp_registry import McpStore
    db = Database(tmp_path / "t.db")
    st = McpStore(db)
    st.update("snyk", True, {"SNYK_TOKEN": "tok-1"})
    db.execute("UPDATE mcp_servers SET args='[\"old-package==0.1\"]' WHERE id='semgrep'")
    st = McpStore(db)                                                   # restart
    assert st.get("semgrep").args[:3] == ["--from", "semgrep==1.186.0", "semgrep"]
    sn = st.get("snyk")
    assert sn.env["SNYK_TOKEN"] == "tok-1" and sn.enabled
