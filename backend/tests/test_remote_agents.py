"""Connecting a remote A2A agent: reading its card, saving it, refreshing, testing, choosing who may call it."""
from __future__ import annotations

import asyncio
import ssl

import httpx
import pytest
from a2a.client import AgentCardResolutionError

from app.apiaccess import a2a as A
from app.apiaccess.a2a import RemoteAgents, RemoteError
from tests.test_apiaccess import BASE, asgi, new_key, rich  # noqa: F401  (the fixture is shared)


def run(c):
    return asyncio.run(c)


# ------------------------------------------------------------------------------------------------ pure helpers
def test_card_base_accepts_the_base_or_a_link_to_the_card():
    assert A.card_base("https://a.example.com/travel") == "https://a.example.com/travel/"
    assert A.card_base(" https://a.example.com/travel/.well-known/agent-card.json ") == "https://a.example.com/travel/"
    assert A.card_base("http://10.0.0.4:9000/.well-known/AGENT.json/") == "http://10.0.0.4:9000/"
    for bad in ("", "ftp://x", "a.example.com", "http://", "https://" + "a" * 400):
        with pytest.raises(RemoteError, match="http:// or https://"):
            A.card_base(bad)


def test_auth_headers_and_checks():
    assert A.auth_headers("", "bearer", "") == {} and A.auth_headers("t", "bearer", "") == {"Authorization": "Bearer t"}
    assert A.auth_headers("k", "header", "X-Key") == {"X-Key": "k"}
    A._check_auth("bearer", "")
    A._check_auth("header", "X-API-Key")
    with pytest.raises(RemoteError, match="bearer token or an API key"):
        A._check_auth("basic", "")
    for bad in ("", "has space", "x" * 61):
        with pytest.raises(RemoteError, match="name of the header"):
            A._check_auth("header", bad)


def test_security_schemes_in_plain_words():
    assert A._scheme_text({"httpAuthSecurityScheme": {"scheme": "Bearer"}}) == "bearer token"
    assert A._scheme_text({"type": "http", "scheme": "basic"}) == "HTTP basic"
    assert A._scheme_text({"httpAuthSecurityScheme": {}}) == "HTTP auth"
    assert A._scheme_text({"apiKeySecurityScheme": {"name": "X-Key", "location": "header"}}) == "API key (header X-Key)"
    assert A._scheme_text({"type": "apiKey", "name": "k", "in": "query"}) == "API key (query k)"
    assert A._scheme_text({"apiKeySecurityScheme": {}}) == "API key (header)"
    assert A._scheme_text({"oauth2SecurityScheme": {}}) == "OAuth 2" and A._scheme_text({"type": "oauth2"}) == "OAuth 2"
    assert A._scheme_text({"openIdConnectSecurityScheme": {}}) == "OpenID Connect" and A._scheme_text({"type": "openIdConnect"}) == "OpenID Connect"
    assert A._scheme_text({"mtlsSecurityScheme": {}}) == "mutual TLS" and A._scheme_text({"type": "mutualTLS"}) == "mutual TLS"
    assert A._scheme_text({"weird": 1}) == "unknown sign-in" and A._scheme_text("x") == "unknown sign-in"


def test_card_summary_handles_both_card_shapes():
    s = A.summarize_card({"name": "N", "description": "d" * 700, "version": "2", "provider": {"organization": "Acme"}, "capabilities": {"streaming": True},
                          "supportedInterfaces": [{"url": "https://x/a2a/"}, {"nourl": 1}, "junk"], "url": "https://x/old", "defaultInputModes": ["text/plain"],
                          "securitySchemes": {"a": {"type": "http", "scheme": "bearer"}, "b": {"httpAuthSecurityScheme": {"scheme": "bearer"}}},
                          "skills": [{"id": "s1", "name": "One", "description": "x" * 400, "tags": ["t"] * 12}, "junk", {"id": "s2"}]})
    assert s["name"] == "N" and len(s["description"]) == 600 and s["provider"] == "Acme" and s["streaming"] is True
    assert s["interfaces"] == ["https://x/a2a/", "https://x/old"] and s["auth"] == ["bearer token"]
    assert [k["id"] for k in s["skills"]] == ["s1", "s2"] and len(s["skills"][0]["description"]) == 300 and len(s["skills"][0]["tags"]) == 8 and s["skills"][1]["name"] == ""
    assert s["input_modes"] == ["text/plain"] and s["output_modes"] == []
    bare = A.summarize_card({"provider": "Plain Co"})
    assert bare["provider"] == "Plain Co" and bare["skills"] == [] and bare["auth"] == [] and bare["streaming"] is None
    dup = A.summarize_card({"url": "https://x/a/", "supportedInterfaces": [{"url": "https://x/a/"}]})
    assert dup["interfaces"] == ["https://x/a/"]


def test_every_card_failure_is_explained():
    def err(status=None, cause=None, cls=AgentCardResolutionError):
        e = cls("m", status_code=status) if cls is AgentCardResolutionError else cls("m")
        e.__cause__ = cause
        return A.explain_fetch_error(e, "https://agents.example.com/travel/")
    assert err(401)["kind"] == "auth" and err(403)["kind"] == "auth"
    assert err(404)["kind"] == "address" and "agents.example.com" in err(404)["detail"]
    assert err(500)["kind"] == "server" and "HTTP 500" in err(500)["detail"]
    assert err(cause=ssl.SSLError("x"))["kind"] == "tls"
    assert err(cause=httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] bad"))["kind"] == "tls"
    assert err(cause=httpx.ConnectTimeout("t"))["kind"] == "timeout" and err(cause=TimeoutError())["kind"] == "timeout"
    assert err(cause=httpx.ConnectError("refused"))["kind"] == "unreachable"
    assert err()["kind"] == "card"
    assert err(cls=ValueError)["kind"] == "other"


# ------------------------------------------------------------------------------------------------ reading cards
def legacy_card_transport(first_status=404, cards=True):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/agent-card.json":
            return httpx.Response(first_status, text="nope")
        if request.url.path == "/.well-known/agent.json" and cards:
            return httpx.Response(200, json={"name": "Old school", "description": "An older card", "url": "https://old.example.com/rpc", "version": "0.3",
                                             "protocolVersion": "0.3.0", "preferredTransport": "JSONRPC", "capabilities": {"streaming": False},
                                             "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
                                             "skills": [{"id": "s", "name": "S", "description": "d", "tags": ["t"]}],
                                             "securitySchemes": {"k": {"type": "apiKey", "name": "X-Key", "in": "header"}}})
        return httpx.Response(404, text="nope")
    return httpx.MockTransport(handler)


def test_preview_reads_a_live_card_with_the_credentials_given(rich, state):
    k = new_key(rich)
    state.a2a_remotes.transport = asgi(rich)
    pv = run(state.a2a_remotes.preview(f"{BASE}/a2a/helper/.well-known/agent-card.json", k["key"]))
    assert pv["ok"] and pv["base_url"] == f"{BASE}/a2a/helper/" and pv["card"]["name"] == "Helper" and pv["card"]["auth"] == ["bearer token"]
    assert pv["scope"] == "this_pc" and pv["scope_label"] == "on this PC" and pv["raw_card"]["name"] == "Helper" and pv["card"]["streaming"] is True
    hdr = run(state.a2a_remotes.preview(f"{BASE}/a2a/helper/", "Bearer " + k["key"], "header", "Authorization"))      # an API key sent in a header of your choice
    assert hdr["ok"]
    assert run(state.a2a_remotes.preview(f"{BASE}/a2a/helper/", "asx_deadbeef_" + "a" * 30))["kind"] == "auth"
    assert run(state.a2a_remotes.preview(f"{BASE}/a2a/nope/", k["key"]))["kind"] == "address"
    assert run(state.a2a_remotes.preview("nope"))["kind"] == "config" and run(state.a2a_remotes.preview(f"{BASE}/a2a/helper/", "t", "header", ""))["kind"] == "config"


def test_preview_falls_back_to_the_older_card_path_and_stops_on_other_errors(state):
    rem = RemoteAgents(state.db, transport=legacy_card_transport())
    pv = run(rem.preview("https://old.example.com/", "tok"))
    assert pv["ok"] and pv["card"]["name"] == "Old school" and pv["card"]["auth"] == ["API key (header X-Key)"] and pv["scope"] == "internet"
    assert run(RemoteAgents(state.db, transport=legacy_card_transport(first_status=500)).preview("https://old.example.com/"))["kind"] == "server"
    assert run(RemoteAgents(state.db, transport=legacy_card_transport(cards=False)).preview("https://old.example.com/"))["kind"] == "address"

    def junk(request):
        return httpx.Response(200, text="<html>hello</html>")
    assert run(RemoteAgents(state.db, transport=httpx.MockTransport(junk)).preview("https://x.example.com/"))["kind"] == "card"

    def down(request):
        raise httpx.ConnectError("refused")
    assert run(RemoteAgents(state.db, transport=httpx.MockTransport(down)).preview("https://x.example.com/"))["kind"] == "unreachable"


# ------------------------------------------------------------------------------------------------ the registry
def test_connect_saves_the_card_and_picks_a_free_id(rich, state):
    k = new_key(rich)
    rem = state.a2a_remotes
    rem.transport = asgi(rich)
    a = run(rem.connect(f"{BASE}/a2a/helper/", "", k["key"]))
    b = run(rem.connect(f"{BASE}/a2a/helper/", "Helper", k["key"]))
    c = run(rem.connect(f"{BASE}/a2a/helper/", "9 lives!", k["key"]))
    d = run(rem.connect(f"{BASE}/a2a/helper/", "***", k["key"]))
    assert (a["id"], b["id"], c["id"], d["id"]) == ("helper", "helper_2", "agent_9_lives", "agent_")
    assert a["label"] == "Helper" and a["has_token"] and a["card"]["name"] == "Helper" and a["network_scope"] == "this_pc" and a["delegates"] == [] and "token" not in a
    with pytest.raises(RemoteError, match="The agent refused the credentials"):
        run(rem.connect(f"{BASE}/a2a/helper/", "x", "asx_deadbeef_" + "a" * 30))
    rem.transport = None
    for i in range(10):                                                    # an id clash with a long name still ends up unique and valid
        rem.add(f"long_name_agent_number_{i}", "l", "http://x")
    rem.transport = asgi(rich)
    long = run(rem.connect(f"{BASE}/a2a/helper/", "Long Name Agent Number", k["key"]))
    assert A.REMOTE_ID_RE.match(long["id"])


def test_update_changes_address_and_sign_in_with_checks(state):
    rem = state.a2a_remotes
    rem.add("r1", "R", "http://a.example.com/x", "tok")
    u = rem.update("r1", {"url": "http://b.example.com/y/.well-known/agent-card.json", "auth_kind": "header", "auth_header": "X-Key", "label": "Renamed"})
    assert u["url"] == "http://b.example.com/y/" and u["auth_kind"] == "header" and u["auth_header"] == "X-Key" and u["label"] == "Renamed" and u["has_token"]
    assert rem.update("r1", {"auth_kind": "bearer"})["auth_header"] == ""                         # the header name goes away with the header kind
    with pytest.raises(RemoteError, match="http://"):
        rem.update("r1", {"url": "nope"})
    with pytest.raises(RemoteError, match="name of the header"):
        rem.update("r1", {"auth_kind": "header", "auth_header": ""})
    with pytest.raises(RemoteError, match="bearer token or an API key"):
        rem.add("r2", "R", "http://x", "t", "basic")


def test_delegates_decide_who_may_call_a_remote(state):
    rem = state.a2a_remotes
    rem.add("open_one", "Open", "http://a.example.com/")
    rem.add("picky", "Picky", "http://b.example.com/")
    assert rem.enabled_ids("anyone") == ["open_one", "picky"] and rem.enabled_ids() == ["open_one", "picky"]
    assert rem.set_delegates("picky", ["writer", "writer", "helper"])["delegates"] == ["helper", "writer"]
    assert rem.enabled_ids("helper") == ["open_one", "picky"] and rem.enabled_ids("engineer") == ["open_one"] and rem.enabled_ids("") == ["open_one"]
    assert rem.enabled_ids() == ["open_one", "picky"]
    with pytest.raises(KeyError):
        rem.set_delegates("ghost", [])


def test_the_call_tool_shows_only_the_remotes_this_agent_may_call(state, tmp_path):
    from app.harness.sandbox import Sandbox
    from app.harness.tools import ToolContext, build_tools
    rem = state.a2a_remotes
    rem.add("picky", "Picky", "http://b.example.com/")
    rem.set_delegates("picky", ["writer"])
    ctx = lambda aid: ToolContext(tmp_path, Sandbox(tmp_path), state.skills, state.mcp, remotes=rem, agent_id=aid)       # noqa: E731
    assert build_tools(ctx("writer"), ["call_remote_agent"])[0].__name__ == "call_remote_agent"
    assert build_tools(ctx("writer"), ["call_remote_agent"])[0].__doc__.count("picky") == 1
    assert build_tools(ctx("engineer"), ["call_remote_agent"]) == [] and build_tools(ctx(""), ["call_remote_agent"]) == []


# ------------------------------------------------------------------------------------------------ the routes
def test_routes_connect_list_refresh_test_and_remove(rich, state):
    k = new_key(rich)
    state.a2a_remotes.transport = asgi(rich)
    pv = rich.post("/api/access/remotes/preview", json={"url": f"{BASE}/a2a/helper/", "token": k["key"]}).json()
    assert pv["ok"] and "raw_card" not in pv and pv["card"]["name"] == "Helper"
    assert rich.post("/api/access/remotes/preview", json={"url": "nope"}).json()["kind"] == "config"
    assert rich.post("/api/access/remotes/connect", json={"url": f"{BASE}/a2a/helper/", "token": "asx_deadbeef_" + "a" * 30}).status_code == 422
    c = rich.post("/api/access/remotes/connect", json={"url": f"{BASE}/a2a/helper/", "token": k["key"], "label": "My helper"})
    assert c.status_code == 200 and c.json()["id"] == "my_helper" and c.json()["card"]["skills"][0]["id"] == "helper"
    assert [r["id"] for r in rich.get("/api/access/remotes").json()] == ["my_helper"]
    assert rich.get("/api/access/remotes/my_helper").json()["label"] == "My helper" and rich.get("/api/access/remotes/ghost").status_code == 404
    ref = rich.post("/api/access/remotes/my_helper/refresh").json()
    assert ref["ok"] and ref["card"]["name"] == "Helper" and rich.post("/api/access/remotes/ghost/refresh").status_code == 404
    rich.put("/api/access/remotes/my_helper", json={"token": "asx_deadbeef_" + "a" * 30})
    bad = rich.post("/api/access/remotes/my_helper/refresh").json()
    assert not bad["ok"] and bad["kind"] == "auth"
    assert rich.post("/api/access/remotes/my_helper/test").json()["ok"] is False
    assert rich.put("/api/access/remotes/my_helper", json={"url": "nope"}).status_code == 422
    assert rich.put("/api/access/remotes/my_helper", json={"auth_kind": "header"}).status_code == 422
    assert rich.put("/api/access/remotes/my_helper", json={"url": f"{BASE}/a2a/helper/", "token": k["key"], "label": "Renamed"}).json()["label"] == "Renamed"
    assert rich.post("/api/access/remotes/my_helper/test").json() == {"ok": True, "name": "Helper", "description": "Answers questions"}
    assert rich.delete("/api/access/remotes/my_helper").status_code == 200
    assert rich.get("/api/access/remotes").json() == []


def test_routes_send_a_test_message(rich, state):
    k = new_key(rich)
    state.a2a_remotes.transport = asgi(rich)
    rid = rich.post("/api/access/remotes/connect", json={"url": f"{BASE}/a2a/helper/", "token": k["key"]}).json()["id"]
    ok = rich.post(f"/api/access/remotes/{rid}/ping", json={"message": "hello"}).json()
    assert ok["ok"] and ok["title"] == "Answered" and ok["ms"] >= 0 and ok["reply"]
    assert rich.post(f"/api/access/remotes/{rid}/ping", json={"message": "  "}).status_code == 422
    assert rich.post("/api/access/remotes/ghost/ping", json={"message": "hi"}).status_code == 404
    rich.put(f"/api/access/remotes/{rid}", json={"enabled": False})
    off = rich.post(f"/api/access/remotes/{rid}/ping", json={"message": "hi"}).json()
    assert not off["ok"] and off["title"] == "The agent did not answer" and "no enabled remote agent" in off["detail"]


def test_routes_choose_who_may_call_a_remote_and_grant_the_tool(rich, state):
    state.a2a_remotes.add("peer", "Peer", "http://a.example.com/")
    assert "call_remote_agent" not in state.agents.get("helper").tools
    r = rich.put("/api/access/remotes/peer/delegates", json={"agents": ["helper"]})
    assert r.status_code == 200 and r.json()["delegates"] == ["helper"]
    assert "call_remote_agent" in state.agents.get("helper").tools
    again = rich.put("/api/access/remotes/peer/delegates", json={"agents": ["helper"]})                # already has the tool: nothing doubled
    assert again.status_code == 200 and state.agents.get("helper").tools.count("call_remote_agent") == 1
    assert rich.put("/api/access/remotes/peer/delegates", json={"agents": ["nobody"]}).status_code == 422
    assert rich.put("/api/access/remotes/ghost/delegates", json={"agents": []}).status_code == 404
    assert rich.put("/api/access/remotes/peer/delegates", json={}).json()["delegates"] == []


def test_an_older_database_gets_the_new_remote_columns(tmp_path):
    import sqlite3
    from app.db import Database
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE a2a_remotes(id TEXT PRIMARY KEY, label TEXT NOT NULL, url TEXT NOT NULL, token TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, card TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL)")
    con.execute("INSERT INTO a2a_remotes(id,label,url,token,created) VALUES('old','Old','http://a.example.com/','t',1.0)")
    con.commit()
    con.close()
    db = Database(path)
    r = RemoteAgents(db).get("old")
    assert r["auth_kind"] == "bearer" and r["auth_header"] == "" and r["delegates"] == [] and r["has_token"] and r["card"] is None
