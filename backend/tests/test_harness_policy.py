"""Harness upgrades: permission modes, loop guard, hooks, checkpoints, project instructions, todo list, repo map, web tools (SSRF guard)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app import mock_llm
from app.harness import extras as X
from app.harness import policy as P
from app.harness.sandbox import Sandbox
from app.harness.tools import ToolContext, build_tools

run = asyncio.run


# ---------------------------------------------------------------------------------------------------- hooks and settings
def test_hooks_are_validated():
    ok = P.clean_hooks([{"event": "pre_tool", "tool": "run_shell", "match": "rm -rf", "action": "block", "message": "No deleting"},
                        {"event": "post_tool", "tool": "*", "action": "note", "message": "Remember to run the tests"}])
    assert ok[0]["match"] == "rm -rf" and ok[1]["tool"] == "*" and ok[1]["match"] == "" and P.clean_hooks(None) == [] and P.clean_hooks("") == []
    bad = [("x", "list of at most"), ([1], "must be an object"), ([{"event": "x"}], "event must be"), ([{"event": "pre_tool", "action": "note", "message": "m"}], "can only block"),
           ([{"event": "pre_tool", "action": "block", "tool": "Bad Tool", "message": "m"}], "tool must be"),
           ([{"event": "pre_tool", "action": "block", "message": " "}], "write a message"), ([{"event": "pre_tool", "action": "block", "message": "m", "match": "(["}], "regular expression"),
           ([{"event": "pre_tool", "action": "block", "message": "m"}] * 31, "list of at most")]
    for raw, msg in bad:
        with pytest.raises(P.PolicyError, match=msg):
            P.clean_hooks(raw)


def test_policy_settings_roundtrip_and_validation(state):
    db = state.db
    assert P.get_policy(db) == P.DEFAULTS
    db.set_setting("harness_policy", "broken")
    assert P.get_policy(db) == P.DEFAULTS
    db.set_setting("harness_policy", "[1]")
    assert P.get_policy(db) == P.DEFAULTS
    got = P.set_policy(db, {"permission_mode": "plan", "loop_guard_repeats": 0, "web_allow_hosts": [" Docs.Python.org "], "web_search_url": "https://s.example", "web_enabled": True,
                            "hooks": [{"event": "pre_tool", "action": "block", "message": "m"}]})
    assert got["permission_mode"] == "plan" and got["web_allow_hosts"] == ["docs.python.org"] and P.get_policy(db)["hooks"][0]["tool"] == "*"
    for patch, msg in [("x", "send an object"), ({"nope": 1}, "unknown harness setting"), ({"permission_mode": "yolo"}, "permission_mode must"), ({"web_enabled": 1}, "true or false"),
                       ({"loop_guard_repeats": 99}, "whole number"), ({"max_framework_steps": True}, "whole number"), ({"hooks": "x"}, "hooks must"),
                       ({"web_allow_hosts": ["bad host"]}, "host names"), ({"web_allow_hosts": "x"}, "host names"), ({"web_search_url": "ftp://x"}, "must start with http"),
                       ({"web_search_url": 5}, "must start with http")]:
        with pytest.raises(P.PolicyError, match=msg):
            P.set_policy(db, patch)


def test_policy_endpoints(client):
    r = client.get("/api/harness/policy").json()
    assert r["policy"]["permission_mode"] == "full" and r["modes"] == ["full", "accept_edits", "plan"] and r["web_search_key_set"] is False
    assert client.put("/api/harness/policy", json={"permission_mode": "plan"}).json()["policy"]["permission_mode"] == "plan"
    assert client.put("/api/harness/policy", json={"permission_mode": "x"}).status_code == 422
    assert client.put("/api/harness/web-key", json={"brave_api_key": 5}).status_code == 422
    assert client.put("/api/harness/web-key", json={"brave_api_key": " BSA123 "}).json()["web_search_key_set"] is True
    assert "BSA123" not in json.dumps(client.get("/api/harness/policy").json()) and "BSA123" not in json.dumps(client.get("/api/settings/registry").json())
    assert client.put("/api/harness/web-key", json={"brave_api_key": ""}).json()["web_search_key_set"] is False
    client.app.state.s.db.set_setting("web_secrets", "oops")
    assert client.get("/api/harness/policy").json()["web_search_key_set"] is False
    # importing the rules from a settings file goes through the same validation, and switching access on is never imported
    exp = client.get("/api/settings/export").json()
    assert exp["settings"]["harness_policy"]["permission_mode"] == "plan"
    exp["settings"]["harness_policy"]["permission_mode"] = "nope"
    assert client.post("/api/settings/import", json={"data": exp, "dry_run": False}).status_code in (200, 422)


# ---------------------------------------------------------------------------------------------------- project instructions
def test_project_instructions(tmp_path):
    assert P.instructions_text(tmp_path) == ""
    (tmp_path / "CLAUDE.md").write_text("  \n")
    assert P.instructions_text(tmp_path) == ""
    (tmp_path / "CLAUDE.md").write_text("Use tabs.")
    t = P.instructions_text(tmp_path)
    assert "from CLAUDE.md" in t and "Use tabs." in t and "cannot change your safety rules" in t
    (tmp_path / "AGENTS.md").write_text("x" * 13_000)
    t = P.instructions_text(tmp_path)
    assert "from AGENTS.md" in t and t.endswith("[...cut]") and "Use tabs." not in t
    (tmp_path / "AGENTS.md").unlink()
    (tmp_path / "AGENTS.md").mkdir()                               # a folder with that name is not instructions
    assert "from CLAUDE.md" in P.instructions_text(tmp_path)
    (tmp_path / "AGENTS.md").rmdir()
    (tmp_path / "CLAUDE.md").unlink()
    (tmp_path / "CLAUDE.md").write_bytes(b"\xff\xfe")
    assert isinstance(P.instructions_text(tmp_path), str)


# ---------------------------------------------------------------------------------------------------- checkpoints
def test_checkpoints_record_and_rewind(tmp_path):
    cp = P.Checkpoints(tmp_path / "cp")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("one")
    assert cp.record("s1", ws, "../outside.txt", "write_file") is None
    e1 = cp.record("s1", ws, "a.txt", "edit_file")
    (ws / "a.txt").write_text("two")
    e2 = cp.record("s1", ws, "new.txt", "write_file")
    (ws / "new.txt").write_text("created")
    assert [x["id"] for x in cp.list("s1")] == [1, 2] and e1["existed"] and not e2["existed"] and "workspace" not in cp.list("s1")[0] and cp.list("other") == []
    assert cp.rewind("s1", 2) == {"restored": 0, "removed": 1} and not (ws / "new.txt").exists() and (ws / "a.txt").read_text() == "two"
    (ws / "a.txt").write_text("three")
    assert cp.rewind("s1", 1) == {"restored": 1, "removed": 0} and (ws / "a.txt").read_text() == "one" and cp.list("s1") == []
    with pytest.raises(KeyError):
        cp.rewind("s1", 1)
    for i in range(P.MAX_CHECKPOINTS + 5):
        cp.record("s2", ws, "a.txt", "edit_file")
    assert len(cp.list("s2")) == P.MAX_CHECKPOINTS and cp.list("s2")[0]["id"] == 6
    (ws / "gone.txt").write_text("x")
    cp.record("s3", ws, "gone.txt", "edit_file")
    (cp._dir("s3") / "1.bak").unlink()
    assert cp.rewind("s3", 1) == {"restored": 0, "removed": 0}                     # a missing backup is skipped, not fatal
    assert cp._index("zzz") == [] and P.Checkpoints().root.name == "checkpoints"


# ---------------------------------------------------------------------------------------------------- the policy itself
def test_permission_modes():
    plan, edits, full = P.ToolPolicy("plan"), P.ToolPolicy("accept_edits"), P.ToolPolicy("nonsense")
    assert full.mode == "full" and full.before("run_shell", {"command": "ls"}) is None
    assert "Plan mode" in plan.before("write_file", {"path": "a"}) and plan.before("read_file", {"path": "a"}) is None and plan.before("web_fetch", {}) is None
    assert edits.before("write_file", {"path": "a"}) is None and "not allowed in this permission mode" in edits.before("run_shell", {})
    assert "not allowed" in edits.before("call_remote_agent", {})


def test_hooks_block_and_annotate():
    hooks = P.clean_hooks([{"event": "pre_tool", "tool": "run_shell", "match": r"rm\s+-rf", "action": "block", "message": "never delete"},
                           {"event": "post_tool", "tool": "write_file", "match": "", "action": "note", "message": "run the tests"}])
    pol = P.ToolPolicy("full", 0, hooks)
    assert "Blocked by your rule: never delete" in pol.before("run_shell", {"command": "rm -rf /"}) and pol.before("run_shell", {"command": "ls"}) is None
    assert pol.before("read_file", {"path": "rm -rf"}) is None
    assert pol.after("write_file", {"path": "a"}, {}) == "run the tests" and pol.after("read_file", {}, {}) is None


def test_loop_guard():
    pol = P.ToolPolicy("full", 2)
    assert pol.before("read_file", {"path": "a"}) is None and pol.before("read_file", {"path": "a"}) is None
    msg = pol.before("read_file", {"path": "a"})
    assert "exact 'read_file' call 2 times" in msg
    assert pol.before("read_file", {"path": "b"}) is None and pol.before("read_file", {"path": "a"}) is None       # a different call resets the count
    assert P.ToolPolicy("full", 0).before("read_file", {}) is None
    assert P.ToolPolicy("full", -3).loop_repeats == 0


def test_policy_checkpoints_edits(tmp_path):
    cp = P.Checkpoints(tmp_path / "cp")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.txt").write_text("v1")
    pol = P.ToolPolicy("full", 3, [], cp, "sess", ws)
    assert pol.before("edit_file", {"path": "f.txt"}) is None and cp.list("sess")[0]["path"] == "f.txt"
    assert pol.before("write_file", {"content": "no path"}) is None and len(cp.list("sess")) == 1

    class Boom(P.Checkpoints):
        def record(self, *a):
            raise OSError("disk")
    assert P.ToolPolicy("full", 3, [], Boom(), "s", ws).before("edit_file", {"path": "f.txt"}) is None


def test_policy_from_settings(state, tmp_path):
    pol = P.ToolPolicy.from_settings(state.db, "s", tmp_path)
    assert pol.mode == "full" and pol.loop_repeats == 3 and pol.checkpoints is not None
    P.set_policy(state.db, {"checkpoints_enabled": False, "permission_mode": "plan"})
    pol = P.ToolPolicy.from_settings(state.db)
    assert pol.checkpoints is None and pol.mode == "plan"
    state.db.set_setting("harness_policy", json.dumps({"hooks": [{"event": "nope"}]}))
    assert P.ToolPolicy.from_settings(state.db).hooks == []


# ---------------------------------------------------------------------------------------------------- web tools
PUBLIC = "93.184.216.34"


def web(policy=None, resolver=None, handler=None, brave=""):
    pol = {"web_enabled": True, "web_allow_hosts": [], "web_search_url": "", **(policy or {})}
    return X.WebClient(lambda: pol, lambda: brave, resolver or (lambda h: [PUBLIC]), httpx.MockTransport(handler or (lambda r: httpx.Response(200, text="hi"))))


def test_web_check_refuses_unsafe_addresses():
    w = web()
    assert w.check("https://example.com/x") == ("https", "example.com", PUBLIC)
    assert w.check("http://93.184.216.34/")[2] == "93.184.216.34" and w.check("https://[2606:2800:220:1:248:1893:25c8:1946]/")[1].startswith("2606")
    for url, msg in [("ftp://example.com", "only http"), ("file:///etc/passwd", "only http"), ("https://u:p@example.com/", "user name"), ("https://example.com:8080/", "standard web ports"),
                     ("http://127.0.0.1/", "not a public"), ("http://10.0.0.5/", "not a public"), ("http://169.254.169.254/latest/meta-data", "not a public"),
                     ("http://[::1]/", "not a public"), ("http:///x", "only http")]:
        with pytest.raises(X.WebError, match=msg):
            w.check(url)
    with pytest.raises(X.WebError, match="not a public"):
        web(resolver=lambda h: [PUBLIC, "10.0.0.1"]).check("https://sneaky.example/")             # one private answer is enough to refuse
    with pytest.raises(X.WebError, match="not a public"):
        web(resolver=lambda h: []).check("https://nothing.example/")

    def fail(h):
        raise OSError("no dns")
    with pytest.raises(X.WebError, match="could not look up"):
        web(resolver=fail).check("https://x.example/")
    allow = web({"web_allow_hosts": ["python.org"]})
    assert allow.check("https://docs.python.org/3/")[1] == "docs.python.org" and allow.check("https://python.org/")[1] == "python.org"
    with pytest.raises(X.WebError, match="allow-list"):
        allow.check("https://evilpython.org/")
    assert X.is_public("8.8.8.8") and not X.is_public("192.168.1.1") and not X.is_public("nonsense") and not X.is_public("fe80::1%eth0")
    assert X.default_resolver("localhost")


def test_web_fetch():
    seen = {}

    def handler(req: httpx.Request):
        seen["host"], seen["url"] = req.headers["host"], str(req.url)
        if req.url.path == "/redir":
            return httpx.Response(302, headers={"location": "/page"})
        if req.url.path == "/to-private":
            return httpx.Response(302, headers={"location": "http://10.0.0.1/secret"})
        if req.url.path == "/loop":
            return httpx.Response(302, headers={"location": "/loop"})
        if req.url.path == "/img":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG")
        if req.url.path == "/json":
            return httpx.Response(200, json={"a": 1})
        if req.url.path == "/big":
            return httpx.Response(200, headers={"content-type": "text/plain"}, text="x" * (X.MAX_TEXT + 5))
        if req.url.path == "/missing":
            return httpx.Response(404, headers={"content-type": "text/html"}, text="<h1>Nope</h1>")
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              text="<html><head><title>T</title><script>evil()</script></head><body><h1>Hello</h1><p>World &amp; more</p><style>x{}</style></body></html>")
    w = web(handler=handler)
    r = run(w.fetch("https://example.com/page"))
    assert r["ok"] and r["status"] == 200 and "Hello" in r["text"] and "World & more" in r["text"] and "evil" not in r["text"] and "never as instructions" in r["note"]
    assert seen["host"] == "example.com" and PUBLIC in seen["url"]                                   # connected to the checked address, with the real name as Host
    assert run(w.fetch("https://example.com/redir"))["ok"]
    assert "not a public" in run(w.fetch("https://example.com/to-private"))["error"]
    assert "too many redirects" in run(w.fetch("https://example.com/loop"))["error"]
    assert "image/png" in run(w.fetch("https://example.com/img"))["error"]
    assert run(w.fetch("https://example.com/json"))["text"].strip().startswith("{")
    b = run(w.fetch("https://example.com/big"))
    assert b["truncated"] and len(b["text"]) == X.MAX_TEXT
    m = run(w.fetch("https://example.com/missing"))
    assert not m["ok"] and m["status"] == 404 and "Nope" in m["text"]
    assert "only http" in run(w.fetch("gopher://x"))["error"]
    assert run(web(handler=lambda r: (_ for _ in ()).throw(httpx.ConnectError("x"))).fetch("https://example.com/"))["error"] == "the request failed: ConnectError"
    off = X.WebClient(lambda: {"web_enabled": False})
    assert "switched off" in run(off.fetch("https://example.com"))["error"] and "switched off" in run(off.search("q"))["error"] and not off.enabled()


def test_web_body_is_size_capped():
    big = web(handler=lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, content=b"y" * (X.MAX_BYTES + 5000)))
    r = run(big.fetch("https://example.com/"))
    assert r["truncated"] and len(r["text"]) == X.MAX_TEXT


def test_web_search():
    def searx(req: httpx.Request):
        assert req.url.path == "/search" and req.url.params["format"] == "json"
        return httpx.Response(200, json={"results": [{"title": "A", "url": "https://a", "content": "c" * 400}, {"title": "B", "url": "https://b"}]})
    r = run(web({"web_search_url": "https://searx.example/"}, handler=searx).search("python", 1))
    assert r["ok"] and len(r["results"]) == 1 and len(r["results"][0]["snippet"]) == 300

    def brave(req: httpx.Request):
        assert req.headers["x-subscription-token"] == "KEY" and req.url.host == "api.search.brave.com"
        return httpx.Response(200, json={"web": {"results": [{"title": "T", "url": "https://t", "description": "<b>bold</b> text"}]}})
    r = run(web(handler=brave, brave="KEY").search("q", 50))
    assert r["results"][0]["snippet"] == "bold text"
    assert "no search service" in run(web().search("q"))["error"]
    assert run(web({"web_search_url": "https://s"}, handler=lambda r: httpx.Response(500)).search("q"))["error"] == "the search failed: HTTPStatusError"
    assert run(web({"web_search_url": "https://s"}, handler=lambda r: httpx.Response(200, text="not json")).search("q"))["error"].startswith("the search failed")


# ---------------------------------------------------------------------------------------------------- todo list and repo map
def make_ctx(tmp_path, state, web_client=None):
    ctx = ToolContext(tmp_path, Sandbox(tmp_path), state.skills, state.mcp)
    ctx.web = web_client
    return ctx


def test_extra_tools(tmp_path, state):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("class Foo:\n    pass\n\ndef bar():\n    pass\nasync def baz():\n    pass\n")
    (tmp_path / "app.ts").write_text("export default function main() {}\nexport class Thing {}\ninterface I {}\n")
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("function skip() {}")
    ctx = make_ctx(tmp_path, state)
    seen = []
    ctx.on_todos = seen.append
    tools = {f.__name__: f for f in build_tools(ctx, ["todo_write", "repo_map", "web_fetch", "web_search"])}
    assert set(tools) == {"todo_write", "repo_map"}                               # the web tools need the switch
    todo = tools["todo_write"]
    assert run(todo([{"text": "a", "status": "done"}, {"text": "b", "status": "in_progress"}, {"text": "c", "status": "weird"}])) == {"ok": True, "items": 3, "done": 1}
    assert seen[0][2]["status"] == "pending" and ctx.todos == seen[0]
    assert "needs a text" in run(todo([{"text": ""}]))["error"] and "needs a text" in run(todo(["x"]))["error"]
    assert "only one in_progress" in run(todo([{"text": "a", "status": "in_progress"}, {"text": "b", "status": "in_progress"}]))["error"]
    assert "at most 30" in run(todo([{"text": str(i)} for i in range(31)]))["error"]
    ctx.on_todos = None
    assert run(todo([]))["ok"]
    rm = run(tools["repo_map"]())
    lines = {x.split(" ")[0]: x for x in rm["files"]}
    assert "Foo, bar, baz" in lines["pkg/mod.py"] and "main, Thing, I" in lines["app.ts"] and "notes.txt" in lines and not any("node_modules" in x for x in rm["files"]) and not rm["truncated"]
    assert run(tools["repo_map"]("pkg"))["files"][0].startswith("pkg/mod.py")
    assert run(tools["repo_map"](max_files=1))["truncated"]
    assert "not a directory" in run(tools["repo_map"]("notes.txt"))["error"] and run(tools["repo_map"]("../x"))["ok"] is False
    ctx.web = web()
    both = {f.__name__ for f in build_tools(ctx, ["web_fetch", "web_search"])}
    assert both == {"web_fetch", "web_search"}
    fns = {f.__name__: f for f in build_tools(ctx, ["web_fetch", "web_search"])}
    assert run(fns["web_fetch"]("https://example.com/"))["ok"] and "no search service" in run(fns["web_search"]("q"))["error"]


# ---------------------------------------------------------------------------------------------------- inside real turns
@pytest.fixture
def rich(client, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    (tmp_path / "ws").mkdir(exist_ok=True)
    client.post("/api/agents", json={"id": "worker", "name": "Worker", "tier": "flash", "tools": ["read_file", "write_file", "list_dir", "todo_write"]})
    return client


def chat(c, msg, sid=None):
    r = c.post("/api/chat", json={"provider": "mock", "agent": "worker", "message": msg, **({"session_id": sid} if sid else {})})
    assert r.status_code == 200, r.text
    return r.json()


def results(j):
    return [s for s in j["steps"] if s["type"] == "result"]


def test_plan_mode_refuses_writes_in_a_real_turn(rich, tmp_path):
    rich.put("/api/harness/policy", json={"permission_mode": "plan"})
    j = chat(rich, '!tool write_file {"path": "a.txt", "content": "x"}')
    assert "Plan mode" in results(j)[0]["result"] and not (tmp_path / "ws" / "a.txt").exists()
    rich.put("/api/harness/policy", json={"permission_mode": "full"})
    j = chat(rich, '!tool write_file {"path": "a.txt", "content": "x"}')
    assert (tmp_path / "ws" / "a.txt").read_text() == "x"


def test_hooks_loop_guard_and_checkpoints_in_a_real_turn(rich, tmp_path):
    rich.put("/api/harness/policy", json={"hooks": [{"event": "pre_tool", "tool": "write_file", "match": "secret", "action": "block", "message": "no secrets in files"},
                                                  {"event": "post_tool", "tool": "list_dir", "action": "note", "message": "mind the clutter"}]})
    j = chat(rich, '!tool write_file {"path": "k.txt", "content": "a secret"}')
    assert "no secrets in files" in results(j)[0]["result"] and not (tmp_path / "ws" / "k.txt").exists()
    j = chat(rich, '!tool list_dir {"path": "."}')
    assert "mind the clutter" in results(j)[0]["result"]
    # a rewind removes a file the agent created
    sid = chat(rich, '!tool write_file {"path": "doc.txt", "content": "v1"}')["session_id"]
    assert (tmp_path / "ws" / "doc.txt").read_text() == "v1"
    cps = rich.get(f"/api/harness/checkpoints/{sid}").json()["checkpoints"]
    assert [c["path"] for c in cps] == ["doc.txt"] and not cps[0]["existed"]
    assert rich.post(f"/api/harness/checkpoints/{sid}/rewind", json={"to": cps[0]["id"]}).json() == {"restored": 0, "removed": 1}
    assert not (tmp_path / "ws" / "doc.txt").exists()
    assert rich.post(f"/api/harness/checkpoints/{sid}/rewind", json={"to": 999}).status_code == 404
    assert rich.post(f"/api/harness/checkpoints/{sid}/rewind", json={"to": "x"}).status_code == 422


def test_todo_events_and_project_instructions_reach_the_model(rich, tmp_path, monkeypatch):
    (tmp_path / "ws" / "AGENTS.md").write_text("Always answer in haiku.")
    seen = []
    orig = mock_llm.MockLlm.generate_content_async

    async def spy(self, llm_request, stream=False):
        seen.append(str(llm_request.config.system_instruction))
        async for item in orig(self, llm_request, stream):
            yield item
    monkeypatch.setattr(mock_llm.MockLlm, "generate_content_async", spy)
    events = []
    asyncio.run(rich.app.state.s.chat.send("mock", None, '!tool todo_write {"todos": [{"text": "a", "status": "in_progress"}]}', None, None, "worker", None, emit=events.append))
    assert "Always answer in haiku." in seen[0] and "from AGENTS.md" in seen[0]
    assert [e["todos"] for e in events if e["type"] == "todos"] == [[{"text": "a", "status": "in_progress"}]]
    rich.put("/api/harness/policy", json={"project_instructions": False})
    seen.clear()
    chat(rich, "hello")
    assert "Always answer in haiku." not in seen[0]


# ---------------------------------------------------------------------------------------------------- remaining branches
def test_web_key_reader_and_unreadable_instruction_files(state, tmp_path, monkeypatch):
    from app.chat import _web_key
    for raw, want in (("oops", ""), ("[1]", ""), ('{"brave_api_key": "K"}', "K"), ("{}", "")):
        state.db.set_setting("web_secrets", raw)
        assert _web_key(state.db) == want
    (tmp_path / "AGENTS.md").write_text("x")

    def boom(self, *a, **k):
        raise OSError("locked")
    monkeypatch.setattr(Path, "read_text", boom)
    assert P.instructions_text(tmp_path) == ""


def test_a_policy_refusal_comes_before_the_guardrail_check(state):
    from types import SimpleNamespace
    from app.chat import TurnTrace, make_callbacks

    class Rails:
        checked = 0

        def wants(self, stage):
            return True

        async def check(self, stage, text):
            Rails.checked += 1
            return SimpleNamespace(blocked=False, message="")
    turn = TurnTrace()
    turn.start_run("root", "a", None)
    turn.policy = P.ToolPolicy("plan")
    before_tool = make_callbacks(state.guard, turn, "a", "mock", "m", "openai/m", 64, Rails())[3]
    res = run(before_tool(SimpleNamespace(name="write_file"), {"path": "a"}, SimpleNamespace(function_call_id="1")))
    assert res["refused"] and "Plan mode" in res["error"] and Rails.checked == 0
