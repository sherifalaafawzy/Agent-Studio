"""The newer workflow boxes as they run: data boxes, branching, HTTP, MCP, memory, knowledge, data, artifacts, guardrails, the On error path, webhooks, run-from-a-box,
test-one-box, approval forms, bulk export/import and the REST for settings and credentials."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from app.workflows import engine as E
from app.workflows import execs as EX
from app.workflows.http import HttpClient
from tests.test_workflows import finish, rich, save  # noqa: F401  (rich is a fixture)
from tests.test_wf_engine import D, go, nd


def probe(c, node, input_="", wid="probe", **kw):
    d = D([nd("s", "input"), node], [("s", node["id"])], wid=wid)
    r = c.post(f"/api/workflows/{wid}/nodes/{node['id']}/test", json={"doc": d, "input": input_, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def asked(c, rid, node, timeout=20):
    """Wait until a run is waiting for a person at `node`."""
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/workflows/runs/{rid}").json()
        if r["nodes"][node]["status"] == "waiting_approval":
            return r
        time.sleep(0.05)
    raise AssertionError(r)


def out(res):
    assert res["status"] == "done", res
    return res["output"]


LIST = json.dumps([{"n": 3, "t": "a", "g": "x"}, {"n": 1, "t": "b", "g": "y"}, {"n": 2, "t": "a", "g": "x"}, {"n": 2, "t": "a", "g": "x"}])


# ---------------------------------------------------------------------------------------------------- the data boxes, run for real
def test_the_data_boxes_run(rich):
    p = lambda t, cfg, inp=LIST: probe(rich, nd("b", t, cfg), inp)    # noqa: E731
    assert [x["n"] for x in json.loads(out(p("filter", dict(rules=[dict(field="n", op="gte", value="2")]))))] == [3, 2, 2]
    assert [x["n"] for x in json.loads(out(p("sort", dict(field="n", order="desc", numeric=True))))] == [3, 2, 2, 1]
    assert len(json.loads(out(p("limit", dict(count=2))))) == 2 and json.loads(out(p("limit", dict(count=1, **{"from": "end"}))))[0]["n"] == 2
    assert len(json.loads(out(p("dedupe", dict(field="t"))))) == 2
    assert out(p("aggregate", dict(op="sum", field="n"))) == "8" and json.loads(out(p("aggregate", dict(op="count", group_by="g")))) == {"x": 3, "y": 1} and out(p("aggregate", dict(op="join", field="t", separator="+"))) == "a+b+a+a"
    assert json.loads(out(p("split_out", dict(mode="lines"), "a\nb"))) == ["a", "b"] and out(p("join", dict(items='["a", "b"]', separator="-"), "")) == "a-b"
    assert json.loads(out(p("merge_by_key", dict(left='[{"id": 1, "a": 1}]', right='[{"id": 1, "b": 2}]', key="id"), ""))) == [{"id": 1, "a": 1, "b": 2}]
    assert out(p("date_time", dict(operation="format", value="2026-03-31", format="%d/%m/%Y"), "")) == "31/03/2026" and out(p("date_time", dict(operation="now"), "")).endswith("+00:00")
    assert out(p("date_time", dict(operation="diff_days", value="2026-03-31", other="2026-03-01"), "")) == "30.0"
    assert out(p("regex", dict(pattern=r"\d+", mode="test"), "a1")) == "true" and out(p("regex", dict(pattern=r"(\w+)=(\w+)", mode="groups"), "k=v")) == '{"1": "k", "2": "v"}' and out(p("regex", dict(pattern="a", mode="first"), "none")) == ""
    bad = p("regex", dict(pattern="a", mode="replace", replacement="\\9"), "a")
    assert bad["status"] == "error" and "replacement is not valid" in bad["error"]
    assert p("aggregate", dict(op="sum", field="zzz"))["status"] == "error"
    assert p("filter", dict(rules=[dict(field="n", op="regex", value="(a+)+")]))["error"].startswith("WorkflowError: that pattern repeats")


def test_compare_and_guardrail_branch_and_a_pinned_branch_takes_its_first_path(rich, state, monkeypatch):
    nodes = [nd("s", "input"), nd("c", "compare", dict(conditions=[dict(left="{{ $input }}", op="gt", right="3")])), nd("yes", "template", dict(text="big")), nd("no", "template", dict(text="small")),
             nd("m", "merge"), nd("o", "output")]
    edges = [("s", "c"), ("c", "yes", "true"), ("c", "no", "false"), ("yes", "m"), ("no", "m"), ("m", "o")]
    save(rich, D(nodes, edges, wid="cmp"))
    big, small = go(rich, "cmp", "9"), go(rich, "cmp", "1")
    assert big["final"] == "big" and big["nodes"]["c"]["decision"] == "true" and big["nodes"]["no"]["status"] == "skipped" and small["final"] == "small"
    pinned = [{**n, "settings": {"pin": "x"}} if n["id"] == "c" else n for n in nodes]
    rich.put("/api/workflows/cmp", json=D(pinned, edges, wid="cmp"))
    assert go(rich, "cmp", "1")["final"] == "big"
    # a guardrail check goes down 'passed' or 'blocked', and refuses to pass when guardrails are off
    g = nd("g", "guardrail", dict(stage="input", profile=""))
    off = probe(rich, g, "hello")
    assert off["status"] == "error" and "guardrails are switched off" in off["error"]
    from app.guardrails.service import Decision
    seen = []

    async def evaluate(stage, text, agent, rails):
        seen.append((stage, agent, rails))
        d = Decision()
        if "bad" in text:
            d.blocked, d.message, d.events = True, "stopped: bad", [dict(action="blocked")]
        return d
    monkeypatch.setattr(state.guardrails, "enabled", lambda: True)
    monkeypatch.setattr(state.guardrails, "evaluate", evaluate)
    ok, blocked = probe(rich, g, "hello"), probe(rich, nd("g", "guardrail", dict(stage="output", profile="p1")), "so bad")
    assert ok["output"] == "hello" and ok["ports"] == ["passed"] and blocked["output"] == "stopped: bad" and blocked["ports"] == ["blocked"] and blocked["decision"] == "blocked"
    assert seen == [("input", "workflow:probe", None), ("output", "workflow:probe", ["p1"])]


# ---------------------------------------------------------------------------------------------------- HTTP request
def http_client(state, handler, resolver=None):
    state.wf_runner.http = HttpClient(lambda: state.wf_config.config(), resolver=resolver or (lambda h: ["93.184.216.34"]), transport=httpx.MockTransport(handler))


def test_http_box(rich, state):
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path == "/boom":
            return httpx.Response(500, text="server broke")
        return httpx.Response(200, json={"ok": True, "echo": req.content.decode()}, headers={"x-request-id": "r1", "set-cookie": "no"})
    http_client(state, handler)
    state.wf_config.set_credential(dict(id="tok", kind="bearer", value="s3cret"))
    state.wf_config.set_credential(dict(id="hdr", kind="header", header="X-Key", value="k"))
    get = probe(rich, nd("h", "http_request", dict(url="https://api.test/items", query={"q": "{{ $prev }}"}, headers={"X-A": "b"}, credential="tok")), "cats")
    assert json.loads(out(get)) == {"ok": True, "echo": ""} and seen[0].url.params["q"] == "cats" and seen[0].headers["authorization"] == "Bearer s3cret" and seen[0].headers["x-a"] == "b" and seen[0].method == "GET"
    unconfirmed = probe(rich, nd("h", "http_request", dict(url="https://api.test/items", method="POST", body='{"a": 1}')), "", allow_side_effects=True)
    assert unconfirmed["status"] == "error" and "tick 'I confirm'" in unconfirmed["error"]
    post = probe(rich, nd("h", "http_request", dict(url="https://api.test/items", method="POST", body='{"a": "{{ $prev }}"}', confirmed=True, credential="hdr")), "z", allow_side_effects=True)
    assert json.loads(out(post))["echo"] == '{"a": "z"}' and seen[-1].headers["x-key"] == "k" and seen[-1].headers["content-type"] == "application/json"
    fail = probe(rich, nd("h", "http_request", dict(url="https://api.test/boom")))
    assert fail["status"] == "error" and "answered 500: server broke" in fail["error"]
    soft = json.loads(out(probe(rich, nd("h", "http_request", dict(url="https://api.test/boom", fail_on_error=False)))))
    assert soft["status"] == 500 and soft["ok"] is False and soft["body"] == "server broke" and soft["headers"]["content-type"].startswith("text/plain") and "set-cookie" not in soft["headers"]
    http_client(state, handler, resolver=lambda h: ["10.0.0.5"])
    private = probe(rich, nd("h", "http_request", dict(url="https://internal.test/x")))
    assert private["status"] == "error" and "not a public internet address" in private["error"]
    rich.put("/api/workflows/settings", json=dict(http_allow_hosts=["internal.test"]))
    assert probe(rich, nd("h", "http_request", dict(url="https://internal.test/x")))["status"] == "done"
    gone = probe(rich, nd("h", "http_request", dict(url="https://api.test/x", credential="ghost")))
    assert gone["status"] == "error" and "does not exist" in gone["error"]


def test_http_client_property_builds_its_own_client(state):
    state.wf_runner._http = None
    c = state.wf_runner.http
    assert isinstance(c, HttpClient) and state.wf_runner.http is c and c.config() == state.wf_config.config()


# ---------------------------------------------------------------------------------------------------- MCP tool
class FakeSet:
    def __init__(self, tools, result, close_fails=False):
        self.tools, self.closed, self.calls, self.close_fails = tools, False, [], close_fails
        outer = self

        class Manager:
            async def create_session(self):
                class S:
                    async def call_tool(self, name, arguments):
                        outer.calls.append((name, arguments))
                        return result
                return S()
        self._mcp_session_manager = Manager()

    async def get_tools(self):
        return self.tools

    async def close(self):
        self.closed = True
        if self.close_fails:
            raise RuntimeError("already gone")


def tool(name, read_only):
    return SimpleNamespace(raw_mcp_tool=SimpleNamespace(name=name, annotations=None if read_only is None else SimpleNamespace(readOnlyHint=read_only)))


def reply(text="hello", error=False, structured=None):
    return SimpleNamespace(isError=error, structuredContent=structured, content=[SimpleNamespace(text=text), SimpleNamespace(text=None)])


def test_mcp_box(rich, monkeypatch):
    fake = {}

    async def build(store, ids, workspace, warns):
        if fake.get("timeout"):
            raise asyncio.TimeoutError()
        warns.extend(fake.get("warns", []))
        return [fake["set"]] if fake.get("set") else []
    monkeypatch.setattr(EX, "build_toolsets", build)
    box = lambda **kw: nd("m", "mcp_tool", dict(server="srv", tool="search", args={"q": "{{ $prev }}"}, **kw))    # noqa: E731
    call = lambda b, inp="cats": probe(rich, b, inp, allow_side_effects=True)    # noqa: E731
    fake["timeout"] = True
    assert "took too long to start" in call(box())["error"]
    fake["timeout"] = False
    fake["warns"] = ["MCP 'srv' skipped: disabled"]
    assert "skipped: disabled" in call(box())["error"]
    fake["warns"] = []
    assert "did not start" in call(box())["error"]
    fake["set"] = FakeSet([tool("search", True), tool("other", None)], reply("found cats"))
    ok = call(box())
    assert out(ok) == "found cats" and fake["set"].calls == [("search", {"q": "cats"})] and fake["set"].closed
    assert "has no tool called 'nope'" in call(nd("m", "mcp_tool", dict(server="srv", tool="nope")))["error"] and "search, other" in call(nd("m", "mcp_tool", dict(server="srv", tool="nope")))["error"]
    assert "not marked read-only" in call(nd("m", "mcp_tool", dict(server="srv", tool="other")))["error"]
    fake["set"] = FakeSet([tool("other", False)], reply('{"x": 1}', structured={"x": 1}), close_fails=True)
    assert out(call(nd("m", "mcp_tool", dict(server="srv", tool="other", confirmed=True)))) == '{"x": 1}'
    fake["set"] = FakeSet([tool("search", True)], reply("it broke", error=True))
    assert "the tool reported an error: it broke" in call(box())["error"]
    assert probe(rich, box(), "cats")["status"] == "not_run"                                                      # a test step does not call a server unless you say so


# ---------------------------------------------------------------------------------------------------- memory, knowledge, data, artifacts
def test_memory_box(rich, state):
    save_ = nd("m", "memory", dict(operation="save", kind="semantic", text="The user prefers metric units", scope="user", key="units"))
    res = probe(rich, save_, "", allow_side_effects=True)
    saved = json.loads(out(res))
    assert saved["status"] == "active" and saved["duplicate"] is False and saved["saved"] == "The user prefers metric units"
    assert json.loads(out(probe(rich, save_, "", allow_side_effects=True)))["duplicate"] is True
    found = json.loads(out(probe(rich, nd("m", "memory", dict(operation="search", kind="semantic", text="metric units", scope="user", k=3)), "")))
    assert found and found[0]["text"] == "The user prefers metric units" and found[0]["kind"] == "semantic"
    assert probe(rich, save_, "")["status"] == "not_run"                                                          # saving is a side effect: not run by a test step
    state.memory.set_config({"review": True})
    pending = json.loads(out(probe(rich, nd("m", "memory", dict(operation="save", text="another fact", scope="workspace")), "", allow_side_effects=True)))
    assert pending["status"] == "pending"
    state.memory.set_config({"enabled": False})
    off = probe(rich, nd("m", "memory", dict(operation="search", text="x")), "")
    assert off["status"] == "error" and "memory is switched off" in off["error"]


def test_knowledge_and_data_boxes(rich, state, monkeypatch):
    asked = []

    async def search(query, kbs, k):
        asked.append((query, kbs, k))
        return [{"text": "passage", "source": "a.md", "score": 0.9}]
    monkeypatch.setattr(state.rag, "search", search)
    assert json.loads(out(probe(rich, nd("k", "knowledge", dict(kb="docs", query="what is {{ $prev }}?", k=2)), "x")))[0]["source"] == "a.md" and asked == [("what is x?", ["docs"], 2)]
    from app.datasources.base import DataError
    calls = []

    def query(source, sql, max_rows, agent):
        calls.append((source, sql, max_rows, agent))
        if "drop" in sql.lower():
            raise DataError("only SELECT is allowed")
        return dict(columns=["a"], rows=[[1]], truncated=False)
    monkeypatch.setattr(state.data, "query", query)
    assert json.loads(out(probe(rich, nd("d", "data_query", dict(source="db", sql="select {{ $prev }} as a", max_rows=5)), "1")))["rows"] == [[1]] and calls == [("db", "select 1 as a", 5, "workflow:probe")]
    refused = probe(rich, nd("d", "data_query", dict(source="db", sql="drop table x")), "")
    assert refused["status"] == "error" and "only SELECT is allowed" in refused["error"]


def test_artifact_box(rich, state):
    res = probe(rich, nd("a", "artifact", dict(name="report-{{ $prev }}.md", content="# Report\n{{ $prev }}", tags="a, b")), "q1", allow_side_effects=True)
    saved = json.loads(out(res))
    assert saved["name"] == "report-q1.md" and saved["id"] and state.artifacts.get(saved["id"])["tags"] == ["a", "b"]
    state.artifacts.set_config({"enabled": False})
    off = probe(rich, nd("a", "artifact", dict(name="r.md")), "q1", allow_side_effects=True)
    assert off["status"] == "error" and "switched off" in off["error"]


# ---------------------------------------------------------------------------------------------------- the On error path
def test_the_on_error_box(rich):
    nodes = [nd("s", "input"), nd("boom", "parse_json"), nd("after", "template", dict(text="never")), nd("e", "error_handler"),
             nd("tidy", "template", dict(text="cleaning up after {{ $node['e'].json.label }}: {{ $node['e'].json.error }}")), nd("o", "output")]
    save(rich, D(nodes, [("s", "boom"), ("boom", "after"), ("e", "tidy"), ("tidy", "o")], wid="oops"))
    r = go(rich, "oops", "now")
    assert r["status"] == "error" and "step 'boom' failed" in r["error"] and r["nodes"]["e"]["status"] == "done" and r["nodes"]["tidy"]["status"] == "done" and r["nodes"]["o"]["status"] == "done"
    assert r["final"].startswith("cleaning up after boom: WorkflowError: that text is not valid JSON") and json.loads(r["nodes"]["e"]["output"])["node"] == "boom"
    ok = [nd("s", "input"), nd("fine", "template", dict(text="fine")), nd("e", "error_handler"), nd("tidy", "template", dict(text="x"))]
    save(rich, D(ok, [("s", "fine"), ("e", "tidy")], wid="fine"))
    r = go(rich, "fine")
    assert r["status"] == "done" and r["final"] == "fine" and r["nodes"]["e"]["status"] == "skipped" and r["nodes"]["tidy"]["status"] == "skipped"
    both = [nd("s", "input"), nd("a", "template", dict(text="x")), nd("e", "error_handler"), nd("shared", "template", dict(text="y"))]
    r = rich.post("/api/workflows/validate", json=D(both, [("s", "a"), ("a", "shared"), ("e", "shared")], wid="both")).json()
    assert not r["ok"] and any("keep the error path separate" in p["message"] for p in r["problems"])
    again = [nd("s", "input"), nd("boom", "stop", dict(message="x")), nd("e", "error_handler"), nd("tidy", "stop", dict(message="the cleanup failed too")), nd("slow", "wait", dict(seconds=30))]
    save(rich, D(again, [("s", "boom"), ("e", "tidy"), ("e", "slow")], wid="twice"))
    started = time.time()
    r = go(rich, "twice")
    assert r["status"] == "error" and r["nodes"]["tidy"]["status"] == "error" and r["nodes"]["slow"]["status"] == "stopped" and time.time() - started < 10


# ---------------------------------------------------------------------------------------------------- webhooks
def hook_flow(rich, wid="hooked", respond="immediately", payload="body", extra=(), edges=None):
    nodes = [nd("w", "webhook", dict(payload=payload, respond=respond)), nd("t", "template", dict(text="got: {{ $input }}")), *extra, nd("o", "output")]
    save(rich, D(nodes, edges or [("w", "t"), ("t", "o")], wid=wid))
    tok = rich.post(f"/api/workflows/{wid}/hook").json()
    return tok["path"]


def test_webhook_address_lifecycle_and_security(rich):
    save(rich, D([nd("s", "input"), nd("o", "output")], [("s", "o")], wid="plain"))
    assert rich.post("/api/workflows/plain/hook").status_code == 422 and rich.post("/api/workflows/ghost/hook").status_code == 404 and rich.get("/api/workflows/ghost/hook").status_code == 404
    path = hook_flow(rich)
    assert rich.get("/api/workflows/hooked/hook").json()["exists"] is True
    assert rich.post(path, content="hello").status_code == 404                                                    # webhooks are off until switched on
    assert rich.put("/api/workflows/settings", json=dict(webhooks_enabled=True)).json()["settings"]["webhooks_enabled"] is True
    for wrong in ("/api/hooks/hooked/nope", "/api/hooks/ghost/nope"):
        assert rich.post(wrong, content="x").status_code == 404
    r = rich.post(path, content="hello")
    assert r.status_code == 202 and r.json()["status"] == "accepted"
    run = finish(rich, r.json()["run"])
    assert run["final"] == "got: hello" and rich.get("/api/workflows/hooked/hook").json()["calls"] == 1
    listed = {w["id"]: w for w in rich.get("/api/workflows").json()["workflows"]}
    assert listed["hooked"]["trigger"] == "webhook" and listed["hooked"]["has_hook"] is True and listed["plain"]["trigger"] == "input" and listed["plain"]["has_hook"] is False
    second = rich.post("/api/workflows/hooked/hook").json()["path"]
    assert second != path and rich.post(path, content="old").status_code == 404 and rich.post(second, content="new").status_code == 202                          # a new secret replaces the old
    assert rich.delete("/api/workflows/hooked/hook").json() == {"ok": True} and rich.post(second, content="x").status_code == 404
    path = rich.post("/api/workflows/hooked/hook").json()["path"]
    rich.put("/api/workflows/settings", json=dict(hook_max_body_kb=1))
    assert rich.post(path, content="x" * 2000).status_code == 413
    rich.put("/api/workflows/settings", json=dict(hook_rate_per_min=2, hook_max_body_kb=256))
    limited = hook_flow(rich, "rl")
    assert [rich.post(limited, content="x").status_code for _ in range(3)] == [202, 202, 429] and rich.post(limited, content="x").headers["retry-after"] == "60"
    rich.put("/api/workflows/settings", json=dict(hook_rate_per_min=600))
    rich.put("/api/workflows/hooked", json=D([nd("s", "input"), nd("o", "output")], [("s", "o")], wid="hooked"))                                   # the workflow no longer starts with a Webhook box
    assert rich.post(path, content="x").status_code == 409


def test_webhook_payload_modes_and_answers(rich):
    rich.put("/api/workflows/settings", json=dict(webhooks_enabled=True, hook_rate_per_min=600))
    get = lambda wid, path, **kw: finish(rich, rich.post(path, **kw).json()["run"])    # noqa: E731
    path = hook_flow(rich, "wj", payload="json")
    assert get("wj", path, content='{bad')["final"] == "got: {bad" and get("wj", path, content='{"a":1}')["final"] == 'got: {\n  "a": 1\n}' and get("wj", path, content="not json")["final"] == "got: not json" and get("wj", path, content="")["final"] == "got: (empty)"
    path = hook_flow(rich, "wall", payload="all")
    final = json.loads(get("wall", path + "?x=1", content='{"a": 1}', headers={"content-type": "application/json", "authorization": "secret"})["final"][len("got: "):])
    assert final["body"] == {"a": 1} and final["query"] == {"x": "1"} and final["headers"]["content-type"] == "application/json" and "authorization" not in final["headers"]
    path = hook_flow(rich, "wdone", respond="when_done")
    r = rich.post(path, content="hi")
    assert r.status_code == 200 and r.json()["result"] == "got: hi" and r.json()["status"] == "done"
    save(rich, D([nd("w", "webhook", dict(respond="when_done")), nd("x", "stop", dict(message="nope"))], [("w", "x")], wid="wb"))
    bad = rich.post("/api/workflows/wb/hook").json()["path"]
    r = rich.post(bad, content="hi")
    assert r.status_code == 500 and r.json()["status"] == "error" and "nope" in r.json()["error"]
    nodes = [nd("w", "webhook", dict(respond="node")), nd("t", "template", dict(text="made: {{ $input }}")), nd("r", "respond_webhook", dict(status=201, content_type="application/json", body='{"made": "{{ $prev }}"}')),
             nd("slow", "wait", dict(seconds=1)), nd("o", "output")]
    save(rich, D(nodes, [("w", "t"), ("t", "r"), ("r", "slow"), ("slow", "o")], wid="rn"))
    path = rich.post("/api/workflows/rn/hook").json()["path"]
    started = time.time()
    r = rich.post(path, content="x")
    assert r.status_code == 201 and r.headers["content-type"].startswith("application/json") and r.json() == {"made": "made: x"} and time.time() - started < 0.9        # it answers before the slow box finishes
    save(rich, D([nd("w", "webhook", dict(respond="node")), nd("t", "template", dict(text="no responder"))], [("w", "t")], wid="rn2"))
    path = rich.post("/api/workflows/rn2/hook").json()["path"]
    r = rich.post(path, content="x")
    assert r.status_code == 200 and r.json()["result"] == "no responder"
    rich.put("/api/workflows/settings", json=dict(hook_wait_max_s=0))
    r = rich.post(rich.post("/api/workflows/wdone/hook").json()["path"], content="x")
    assert r.status_code == 202 and r.json()["status"] in ("running", "done")
    save(rich, D([nd("w", "webhook"), nd("a", "agent", dict(agent="ghost", prompt="x"))], [("w", "a")], wid="broken"))
    r = rich.post(rich.post("/api/workflows/broken/hook").json()["path"], content="x")
    assert r.status_code == 422 and "fix these first" in r.json()["detail"]


# ---------------------------------------------------------------------------------------------------- run from one box, test one box
def test_run_from_a_box(rich, state):
    nodes = [nd("s", "input"), nd("a", "template", dict(text="A saw {{ $input }}")), nd("b", "template", dict(text="B got [{{ $prev }}] and {{ $input }}")), nd("o", "output")]
    save(rich, D(nodes, [("s", "a"), ("a", "b"), ("b", "o")], wid="rf"))
    save(rich, D([nd("s", "input"), nd("z", "template", dict(text="z")), nd("o", "output")], [("s", "z"), ("z", "o")], wid="other"))
    base = go(rich, "rf", "first")
    assert base["final"] == "B got [A saw first] and first"
    again = go(rich, "rf", "second", from_node="b", base=base["id"])
    assert again["final"] == "B got [A saw first] and second" and again["nodes"]["a"]["status"] == "done" and again["nodes"]["a"].get("reused") is True and again["nodes"]["b"].get("reused") is None and again["from_node"] == "b"
    bad = lambda **kw: rich.post("/api/workflows/rf/run", json={"task": "x", **kw})    # noqa: E731
    other = go(rich, "other")
    assert "run the whole workflow once first" in bad(from_node="b", base=other["id"]).json()["detail"]                                # a run of another workflow, still in memory
    state.wf_runner.runs.clear()                                                                                  # after a restart the earlier run comes from the saved ones
    assert go(rich, "rf", "third", from_node="b", base=base["id"])["final"] == "B got [A saw first] and third"
    assert "run the whole workflow once first" in bad(from_node="b").json()["detail"] and "run the whole workflow once first" in bad(from_node="b", base="nope").json()["detail"]
    assert "choose a box after the start" in bad(from_node="s", base=base["id"]).json()["detail"] and "choose a box after the start" in bad(from_node="ghost", base=base["id"]).json()["detail"]
    assert "run the whole workflow once first" in bad(from_node="b", base=other["id"]).json()["detail"]                                # a saved run of another workflow
    # a box with no output to reuse stops the run before it starts
    skipped = [nd("s", "input"), nd("c", "compare", dict(conditions=[dict(left="a", op="equals", right="a")])), nd("t", "template", dict(text="t")), nd("f", "template", dict(text="f")), nd("m", "merge"), nd("o", "output")]
    save(rich, D(skipped, [("s", "c"), ("c", "t", "true"), ("c", "f", "false"), ("t", "m"), ("f", "m"), ("m", "o")], wid="holes"))
    first = go(rich, "holes")
    assert first["nodes"]["f"]["status"] == "skipped"
    assert go(rich, "holes", "again", from_node="m", base=first["id"])["status"] == "done"
    r = rich.post("/api/workflows/holes/run", json={"task": "x", "from_node": "f", "base": first["id"]})
    assert r.status_code == 200
    state.workflows.db.execute("UPDATE workflow_runs SET nodes=? WHERE id=?", (json.dumps({**first["nodes"], "t": {**first["nodes"]["t"], "status": "error"}}), first["id"]))
    state.wf_runner.runs.clear()
    assert "has no earlier output to reuse" in rich.post("/api/workflows/holes/run", json={"task": "x", "from_node": "o", "base": first["id"]}).json()["detail"]


def test_try_one_box(rich, monkeypatch):
    ok = probe(rich, nd("t", "template", dict(text="Hi {{ $prev }} / {{ $input }}")), "there", task="the task")
    assert ok == dict(status="done", output="Hi there / the task", error=None, ports=["out"], decision=None, side_effects=False, cost_usd=0)
    assert probe(rich, nd("t", "template", dict(text="x")), "p", **{"task": ""})["output"] == "x"
    pinned = probe(rich, nd("t", "template", dict(text="real"), settings={"pin": "pinned", "disabled": True}), "")
    assert pinned["output"] == "real"                                                                              # a test step runs the box itself, ignoring its pin
    held = probe(rich, nd("a", "approval", dict(message="ok?")), "")
    assert held["status"] == "not_run" and "waits for a person" in held["message"]
    risky = probe(rich, nd("p", "http_request", dict(url="https://x.test", method="DELETE", confirmed=True)), "")
    assert risky["status"] == "not_run" and risky["side_effects"] is True
    br = probe(rich, nd("c", "compare", dict(conditions=[dict(left="a", op="equals", right="a")])), "keep")
    assert br["ports"] == ["true"] and br["decision"] == "true" and br["output"] == "keep"
    assert rich.post("/api/workflows/probe/nodes/ghost/test", json={"doc": D([nd("s", "input")], [], wid="probe")}).status_code == 404
    assert rich.post("/api/workflows/probe/nodes/s/test", json={"doc": {"id": "x"}}).status_code == 422
    assert rich.post("/api/workflows/nope/nodes/s/test", json={}).status_code == 404
    save(rich, D([nd("s", "input"), nd("t", "template", dict(text="saved {{ $prev }}"))], [("s", "t")], wid="stored"))
    assert rich.post("/api/workflows/stored/nodes/t/test", json={"input": "doc"}).json()["output"] == "saved doc"
    monkeypatch.setattr(E, "TEST_TIMEOUT_S", 0.05)
    slow = probe(rich, nd("w", "wait", dict(seconds=2)), "")
    assert slow["status"] == "error" and "took longer than" in slow["error"]


def test_values_travel_between_boxes_as_text():
    assert [EX.out_text(v) for v in ("a", None, True, False, 3, 2.5, [1], {"a": "é"})] == ["a", "", "true", "false", "3", "2.5", "[1]", '{"a": "é"}']


def test_side_effects_are_known_for_every_kind_of_box():
    se = EX.has_side_effects
    cfg = lambda t, **c: dict(type=t, config=c)    # noqa: E731
    assert se(cfg("http_request", method="POST")) and not se(cfg("http_request", method="GET")) and se(cfg("memory", operation="save")) and not se(cfg("memory", operation="search"))
    assert all(se(cfg(t)) for t in ("mcp_tool", "artifact", "sub_workflow", "for_each", "agent", "sequential", "parallel", "loop", "openapi"))
    assert se(cfg("tool", tool="write_file")) and not se(cfg("tool", tool="read_file")) and not se(cfg("filter")) and not se(cfg("data_query")) and not se(cfg("knowledge"))


# ---------------------------------------------------------------------------------------------------- approval with details
def test_an_approval_can_ask_for_details(rich):
    form = [dict(name="who", label="Who", type="text", required=True), dict(name="size", label="Size", type="select", options=["s", "m"]), dict(name="n", label="How many", type="number"), dict(name="ok", label="Sure", type="bool")]
    nodes = [nd("s", "input"), nd("ask", "approval", dict(message="Details?", form=form)), nd("done", "template", dict(text="for {{ $node['ask'].json.who }} x{{ $node['ask'].json.n }}")), nd("no", "stop", dict(message="declined"))]
    save(rich, D(nodes, [("s", "ask"), ("ask", "done", "approved"), ("ask", "no", "rejected")], wid="ask"))
    rid = rich.post("/api/workflows/ask/run", json={"task": "go"}).json()["id"]
    waiting = asked(rich, rid, "ask")
    assert waiting["nodes"]["ask"]["form"][0]["name"] == "who"
    decide = lambda **kw: rich.post(f"/api/workflows/runs/{rid}/decide", json=dict(node="ask", decision="approve", **kw))    # noqa: E731
    for values, msg in [({}, "'Who' is needed"), ({"who": "Ann", "n": "lots"}, "must be a number"), ({"who": "Ann", "size": "xl"}, "must be one of s, m")]:
        r = decide(values=values)
        assert r.status_code == 422 and msg in r.json()["detail"], r.text
    assert decide(values={"who": "Ann", "size": "m", "n": "3", "ok": 1}).status_code == 200
    done = finish(rich, rid)
    answers = json.loads(done["nodes"]["ask"]["output"])
    assert answers == dict(who="Ann", size="m", n=3, ok=True) and done["final"] == "for Ann x3"
    rid2 = rich.post("/api/workflows/ask/run", json={"task": "go"}).json()["id"]
    asked(rich, rid2, "ask")
    r = rich.post(f"/api/workflows/runs/{rid2}/decide", json=dict(node="ask", decision="approve", values={"who": "Bo", "n": 2.5}))
    assert r.status_code == 200 and json.loads(finish(rich, rid2)["nodes"]["ask"]["output"]) == dict(who="Bo", size="", n=2.5, ok=False)


# ---------------------------------------------------------------------------------------------------- bulk export and import
def test_bulk_export_and_import(rich):
    for wid in ("one", "two"):
        save(rich, D([nd("s", "input"), nd("o", "output")], [("s", "o")], wid=wid))
    everything = rich.post("/api/workflows/export", json={})
    assert everything.status_code == 200 and "workflows.bundle.json" in everything.headers["content-disposition"] and {w["id"] for w in everything.json()["workflows"]} == {"one", "two"}
    only = rich.post("/api/workflows/export", json={"ids": ["two", "ghost", 5]}).json()
    assert [w["id"] for w in only["workflows"]] == ["two"] and only["format"] == "agent-studio-workflows" and "version" not in only["workflows"][0]
    assert rich.post("/api/workflows/export", json={"ids": ["ghost"]}).status_code == 404
    bundle = everything.json()
    first = bundle["workflows"][0]["id"]
    r = rich.post("/api/workflows/import", json={**bundle, "on_conflict": "rename"}).json()
    assert sorted(r["imported"]) == ["one_2", "two_2"] and r["skipped"] == [] and r["errors"] == []
    r = rich.post("/api/workflows/import", json={**bundle, "on_conflict": "cancel"}).json()
    assert r["imported"] == [] and sorted(r["skipped"]) == ["one", "two"]
    r = rich.post("/api/workflows/import", json={**bundle, "on_conflict": "replace"}).json()
    assert sorted(r["imported"]) == ["one", "two"] and rich.get(f"/api/workflows/{first}").json()["version"] == 2
    r = rich.post("/api/workflows/import", json={"workflows": [{"id": "x"}, "junk", bundle["workflows"][0]], "on_conflict": "cancel"}).json()
    assert len(r["errors"]) == 2 and r["skipped"] == [first]
    assert rich.post("/api/workflows/import", json={"workflows": []}).status_code == 422 and rich.post("/api/workflows/import", json={"format": "agent-studio-workflows"}).status_code == 422
    assert rich.post("/api/workflows/import", json={"workflows": [bundle["workflows"][0]] * 51}).status_code == 422
    assert rich.post("/api/workflows/import", json={**bundle, "on_conflict": "maybe"}).status_code == 422
    single = rich.post("/api/workflows/import", json={"workflow": bundle["workflows"][0], "on_conflict": "cancel"})
    assert single.status_code == 409 and single.json()["id"] == first
    assert rich.post("/api/workflows/import", json={"workflow": bundle["workflows"][0], "on_conflict": "replace"}).json()["version"] == 3
    assert rich.post("/api/workflows/import", json={"workflow": {"id": "x"}}).status_code == 422
    assert rich.post("/api/workflows/import", json=bundle["workflows"][0]).json()["id"] == f"{first}_3"


# ---------------------------------------------------------------------------------------------------- settings and credentials over REST
def test_settings_credentials_and_pickers(rich, state):
    got = rich.get("/api/workflows/settings").json()
    assert got["settings"]["webhooks_enabled"] is False and got["credentials"] == []
    assert rich.put("/api/workflows/settings", json=dict(max_nodes=3)).status_code == 422
    assert rich.put("/api/workflows/settings", json=dict(max_nodes=40, run_retention=10)).json()["settings"]["max_nodes"] == 40
    made = rich.post("/api/workflows/credentials", json=dict(id="tok", label="Token", kind="bearer", value="s3cret")).json()
    assert made == dict(id="tok", label="Token", kind="bearer", header="", set=True) and "s3cret" not in rich.get("/api/workflows/settings").text
    assert rich.post("/api/workflows/credentials", json=dict(id="Bad")).status_code == 422
    save(rich, D([nd("s", "input"), nd("h", "http_request", dict(url="https://x.test", credential="tok"))], [("s", "h")], wid="uses_cred"))
    used = rich.delete("/api/workflows/credentials/tok")
    assert used.status_code == 409 and used.json()["used_by"] == ["uses_cred"]
    rich.delete("/api/workflows/uses_cred?force=true")
    assert rich.delete("/api/workflows/credentials/tok").json() == {"ok": True} and rich.delete("/api/workflows/credentials/tok").status_code == 404
    refs = rich.get("/api/workflows/refs").json()
    assert set(refs) == {"mcp", "knowledge", "data_source", "guardrail", "credential"} and refs["credential"] == [] and all({"id", "label", "ready", "read_only"} <= set(m) for m in refs["mcp"]) and refs["mcp"]
    real = state.wf_runner.refs()
    assert set(real) == {"mcp", "knowledge", "data_source", "guardrail", "credential"} and all({"ready", "why", "read_only"} <= set(v) for v in real["mcp"].values())
    from app import settings_api as A
    A._blob_apply(state, "workflow_config", dict(webhooks_enabled=True, max_nodes=30, hook_rate_per_min=5))
    assert state.wf_config.config()["webhooks_enabled"] is False and state.wf_config.config()["max_nodes"] == 30                      # switching webhooks on is never imported
    with pytest.raises(A.SettingError, match="10 to 200"):
        A._blob_apply(state, "workflow_config", dict(max_nodes=1))
    state.wf_config.set_config(dict(max_nodes=60, run_retention=100))


def test_run_retention_follows_the_setting(rich, state):
    rich.put("/api/workflows/settings", json=dict(run_retention=5))
    save(rich, D([nd("s", "input"), nd("o", "output")], [("s", "o")], wid="keep"))
    for _ in range(7):
        go(rich, "keep")
    assert len(rich.get("/api/workflows/keep/runs?limit=100").json()["runs"]) == 5
    rich.put("/api/workflows/settings", json=dict(run_retention=100))


# ---------------------------------------------------------------------------------------------------- the builder and the plan view
def test_builder_cannot_add_a_start_or_a_credential():
    from app.workflows import builder as B
    from app.workflows import core as W
    base = W.clean(D([nd("s", "input")], [], wid="draft"))
    d, applied, refused = B.apply_ops(base, [dict(op="add_node", type="webhook"), dict(op="add_node", id="h", type="http_request", config=dict(url="https://x.test", method="POST", credential="tok", confirmed=True), after="s")])
    assert any("not a box you can add" in r for r in refused) and applied == ["added HTTP request 'HTTP request'"]
    cfg = next(n for n in d["nodes"] if n["id"] == "h")["config"]
    assert cfg["credential"] == "" and cfg["confirmed"] is False
    d2, _, refused2 = B.apply_ops(W.clean(D([nd("w", "webhook"), nd("t", "template", dict(text="x"))], [("w", "t")], wid="draft")), [dict(op="update_node", id="w", label="x"), dict(op="remove_node", id="w")])
    assert [r.split(": ", 1)[1] for r in refused2] == ["the starting box cannot be changed", "the starting box cannot be removed"] and "- webhook:" not in B.catalog_text()


def test_the_plan_describes_every_newer_box(rich, state, monkeypatch):
    from tests.test_wf_data_boxes import refs as known
    monkeypatch.setattr(state.wf_runner, "refs", lambda: known())
    boxes = [("compare", dict(conditions=[dict(left="a", op="equals", right="a")])), ("http_request", dict(url="https://x.test")), ("mcp_tool", dict(server="m", tool="t")), ("memory", dict(operation="search")),
             ("knowledge", dict(kb="kb")), ("data_query", dict(source="db", sql="select 1")), ("artifact", dict(name="a.md")), ("guardrail", dict(stage="input")), ("filter", dict(rules=[dict(field="a", op="equals", value="1")])),
             ("sort", {}), ("limit", {}), ("dedupe", {}), ("aggregate", {}), ("split_out", {}), ("join", {}), ("merge_by_key", dict(key="id")), ("date_time", {}), ("regex", dict(pattern="a")), ("respond_webhook", {})]
    nodes = [nd("w", "webhook")] + [nd(f"b{i}", t, c) for i, (t, c) in enumerate(boxes)] + [nd("e", "error_handler")]
    edges = [("w", f"b{i}") for i in range(len(boxes))]
    save(rich, D(nodes, edges, wid="plan", autonomy="suggest"))
    r = rich.post("/api/workflows/plan/run", json={"task": "x"}).json()
    plan = finish(rich, r["id"])["plan"]
    text = " | ".join(p["does"] for p in plan)
    for needle in ("Start when the secret address is called", "Check 1 condition(s)", "GET https://x.test", "Call m.t", "Search memory", "knowledge base 'kb'", "data source 'db'", "named a.md", "Check the input", "Keep the items",
                   "Sort by the item", "Keep 10 item(s)", "Remove repeats", "count the items", "Split by lines", "Join the items", "Merge two lists on id", "Date and time: now", "Text pattern: first", "status 200"):
        assert needle in text, needle
    assert len(plan) == len(boxes) + 1


def test_the_catalog_lists_the_newer_boxes(rich):
    cat = rich.get("/api/workflows/nodes").json()
    types = {n["type"] for n in cat["nodes"]}
    assert {"http_request", "mcp_tool", "memory", "knowledge", "data_query", "artifact", "guardrail", "filter", "compare", "webhook", "respond_webhook", "error_handler", "regex"} <= types and len(types - {"note"}) >= 40
    refs = {f["name"]: f.get("ref") for n in cat["nodes"] for f in n["fields"] if f["kind"] == "ref"}
    assert refs == {"credential": "credential", "server": "mcp", "kb": "knowledge", "source": "data_source", "profile": "guardrail"}
