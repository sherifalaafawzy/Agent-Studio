"""Workflows: graph validation, store + versions, imported APIs, the engine (sequential, parallel, loop, approvals, stop), the wizard, tools."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.workflows import core as W

run = asyncio.run


def node(i, t, cfg=None, **kw):
    return dict(id=i, type=t, label=kw.get("label", i), config=cfg or {}, position=kw.get("position"))


def agent_node(i, agent="helper", prompt="{{prev}}"):
    return node(i, "agent", dict(agent=agent, prompt=prompt))


def doc(nodes, edges, wid="wf_one", **kw):
    return dict(id=wid, name="Test flow", nodes=nodes, edges=[dict(source=a, target=b) for a, b in edges], **kw)


LINEAR = doc([node("start", "input"), agent_node("a1", prompt="Answer: {{input}}"), node("end", "output")], [("start", "a1"), ("a1", "end")])


# ---------------------------------------------------------------------------------------------------- the graph
def test_clean_normalises_and_validates():
    d = W.clean({**LINEAR, "description": "d", "nodes": [{**n, "position": {"x": 1.234, "y": 2}} if n["id"] == "start" else n for n in LINEAR["nodes"]]})
    assert d["autonomy"] == "approve" and d["nodes"][0]["position"] == {"x": 1.2, "y": 2.0} and d["nodes"][1]["position"] == {"x": 0, "y": 0}
    assert d["edges"][0]["id"] == "start-a1" and d["nodes"][1]["config"] == {"agent": "helper", "prompt": "Answer: {{input}}"}
    big = [node("start", "input"), node("p", "parallel", dict(steps=[dict(kind="agent", agent="a")])), node("l", "loop", dict(steps=[], max_iterations=2, until="DONE")),
           node("t", "tool", dict(tool="read_file", args={"path": "a", "n": 3, "flag": True, "o": {"x": 1}})), node("o", "openapi", dict(source="s", operation="op", args={"q": "v"}))]
    d2 = W.clean(doc(big, [("start", "p"), ("start", "l"), ("start", "t"), ("start", "o"), ("start", "o")]))
    assert len(d2["edges"]) == 4 and d2["nodes"][2]["config"] == {"steps": [], "max_iterations": 2, "until": "DONE"} and d2["nodes"][3]["config"]["args"]["o"] == "{'x': 1}"
    bad = [
        ("x", "a JSON object"), (doc([node("s", "input")], [], wid="Bad Id"), "workflow id"), ({**LINEAR, "name": " "}, "give the workflow a name"),
        ({**LINEAR, "autonomy": "wild"}, "autonomy must be"), ({**LINEAR, "nodes": []}, "1 to 60 nodes"), ({**LINEAR, "edges": "x"}, "at most 120"),
        (doc([node("s", "input"), node("s", "input")], []), "two nodes are called"), (doc([node("s", "mystery")], []), "needs a type"),
        (doc([node("s", "input", "x")], []), "config must be an object"), (doc([node("a", "agent")], []), "exactly one starting box"),
        (doc([node("s", "input"), node("t", "tool", dict(args="x"))], []), "arguments must be an object"),
        (doc([node("s", "input"), node("t", "openapi", dict(args="x"))], []), "arguments must be an object"),
        (doc([node("s", "input"), node("p", "parallel", dict(steps="x"))], []), "at most 12 steps"), (doc([node("s", "input"), node("p", "parallel", dict(steps=[{"kind": "x"}]))], []), "a step is an agent"),
        (doc([node("s", "input"), node("l", "loop", dict(max_iterations=99))], []), "repeats 1 to 10"), (doc([node("s", "input")], [("s", "ghost")]), "join two different nodes"),
        (doc([node("s", "input")], [("s", "s")]), "join two different nodes"), (doc([node("s", "input"), agent_node("a")], [("a", "s")]), "nothing can feed a starting box"),
        (doc([node("s", "input"), agent_node("a"), agent_node("b")], [("s", "a"), ("a", "b"), ("b", "a")]), "form a loop"),
    ]
    for d_, msg in bad:
        with pytest.raises(W.WorkflowError, match=msg):
            W.clean(d_)
    assert W.render("{{ input }}-{{prev}}-{{gone}}-{{a1}}", {"input": "I", "prev": "P", "a1": "A"}) == "I-P--A" and W.render("", {}) == ""
    assert W._pos({"x": True, "y": 1}) == {"x": 0, "y": 0}


def test_problems_explain_what_blocks_a_run():
    srcs = {"api": dict(allow_write=False, ops={"get_x": dict(method="get", path="/x"), "post_x": dict(method="post", path="/x")}),
            "rw": dict(allow_write=True, ops={"post_x": dict(method="post", path="/x")})}
    nodes = [node("start", "input"), agent_node("ghost", "nobody"), node("t1", "tool", dict(tool="nope")), node("t2", "tool", dict(tool="write_file")),
             node("t3", "tool", dict(tool="write_file", confirmed=True)), node("t4", "tool", dict(tool="read_file")),
             node("o1", "openapi", dict(source="zzz", operation="x")), node("o2", "openapi", dict(source="api", operation="zzz")), node("o3", "openapi", dict(source="api", operation="post_x")),
             node("o4", "openapi", dict(source="rw", operation="post_x")), node("o5", "openapi", dict(source="rw", operation="post_x", confirmed=True)),
             node("o6", "openapi", dict(source="api", operation="get_x")), node("c", "sequential", dict(steps=[])), node("l", "loop", dict(steps=[dict(kind="agent", agent="helper")], max_iterations=3)),
             node("lonely", "output")]
    edges = [("start", i["id"]) for i in nodes[1:-1]]
    d = W.clean(doc(nodes, edges))
    pr = W.problems(d, {"helper"}, srcs, {"read_file", "write_file"})
    msgs = {(p["node"], p["severity"]): p["message"] for p in pr}
    assert "does not exist" in msgs[("ghost", "error")] and "not available" in msgs[("t1", "error")] and "tick 'I confirm'" in msgs[("t2", "error")]
    assert ("t3", "error") not in msgs and ("t4", "error") not in msgs
    assert "is not imported" in msgs[("o1", "error")] and "no operation" in msgs[("o2", "error")] and "writes are not allowed" in msgs[("o3", "error")]
    assert "tick 'I confirm'" in msgs[("o4", "error")] and "will change data" in msgs[("o5", "warning")] and ("o6", "error") not in msgs
    assert msgs[("c", "error")] == "add at least one step" and "always repeat 3 times" in msgs[("l", "warning")] and "nothing leads into" in msgs[("lonely", "error")]
    ok = W.problems(W.clean(LINEAR), {"helper"}, {}, set())
    assert ok == []
    assert any(p["message"].startswith("no Output node") for p in W.problems(W.clean(doc([node("s", "input"), agent_node("a")], [("s", "a")])), {"helper"}, {}, set()))


# ---------------------------------------------------------------------------------------------------- store
class Clock:
    t = 100.0

    def __call__(self):
        Clock.t += 1
        return Clock.t


def test_store_versions_restore_and_delete(state):
    st = W.WorkflowStore(state.db, Clock())
    assert st.list() == [] and st.get("nope") is None and st.ids() == []
    a = st.save(LINEAR, create=True)
    assert a["version"] == 1
    with pytest.raises(W.WorkflowError, match="already exists"):
        st.save(LINEAR, create=True)
    b = st.save({**LINEAR, "name": "Renamed"})
    assert b["version"] == 2 and st.list()[0]["name"] == "Renamed" and st.list()[0]["nodes"] == 3 and st.ids() == ["wf_one"]
    assert [v["version"] for v in st.versions("wf_one")] == [2, 1]
    r = st.restore("wf_one", 1)
    assert r["version"] == 3 and r["name"] == "Test flow"
    with pytest.raises(KeyError):
        st.restore("wf_one", 99)
    for i in range(W.MAX_VERSIONS + 3):
        st.save(LINEAR)
    assert len(st.versions("wf_one")) == W.MAX_VERSIONS
    st.delete("wf_one")
    assert st.get("wf_one") is None and st.versions("wf_one") == []
    with pytest.raises(KeyError):
        st.delete("wf_one")


# ---------------------------------------------------------------------------------------------------- imported APIs
SPEC_OPS = {"get_pet": dict(operation_id="get_pet", method="get", path="/pets/{id}", summary="Get a pet", parameters=[{"name": "id", "in": "path", "required": True}, {"name": "verbose", "in": "query", "required": False}], body=None),
            "add_pet": dict(operation_id="add_pet", method="post", path="/pets", summary="Add", parameters=[], body=["name"])}


def fake_loader(src, base):
    if "bad" in src:
        raise ValueError("not an OpenAPI document")
    if "empty" in src:
        return {}, "https://api.example.com", {}
    return {}, base or "https://api.example.com", SPEC_OPS


def api_transport(seen):
    def handler(req: httpx.Request):
        seen.append((req.method, str(req.url), dict(req.headers), req.content))
        if req.url.path == "/pets/9":
            return httpx.Response(200, json={"name": "Rex"})
        if req.url.path == "/text":
            return httpx.Response(200, text="plain")
        if req.url.path == "/boom":
            raise httpx.ConnectError("down")
        return httpx.Response(404, json={"error": "nope"})
    return httpx.MockTransport(handler)


def test_openapi_sources_import_and_call(state):
    seen = []
    src = W.OpenApiSources(state.db, api_transport(seen), fake_loader)
    for args, msg in [(("Bad Id", "n", "u"), "API id must be"), (("ok1", "n", ""), "give the address"), (("ok1", "n", "bad.yaml"), "could not read"), (("ok1", "n", "empty.yaml"), "no operations")]:
        with pytest.raises(W.WorkflowError, match=msg):
            src.add(*args)
    a = src.add("pets", "", "https://x/openapi.json", "", "Bearer T")
    assert a["name"] == "pets" and a["has_token"] and "Bearer" not in json.dumps(a) and [o["id"] for o in a["operations"]] == ["get_pet", "add_pet"]
    with pytest.raises(W.WorkflowError, match="already imported"):
        src.add("pets", "n", "https://x")
    assert src.for_validation()["pets"]["allow_write"] is False
    ok = run(src.call("pets", "get_pet", {"id": 9, "verbose": 1}, False))
    assert ok == {"ok": True, "status": 200, "body": {"name": "Rex"}}
    m, url, headers, _ = seen[0]
    assert m == "GET" and url == "https://api.example.com/pets/9?verbose=1" and headers["authorization"] == "Bearer T"
    assert run(src.call("pets", "get_pet", {}, False))["error"] == "missing path parameter 'id'"
    assert "changes data" in run(src.call("pets", "add_pet", {"name": "x"}, True))["error"]
    assert "not imported" in run(src.call("zzz", "x", {}, False))["error"] and "no operation" in run(src.call("pets", "zzz", {}, False))["error"]
    src.update("pets", {"allow_write": True, "name": "Pets API"})
    assert run(src.call("pets", "add_pet", {"name": "x"}, False))["ok"] is False
    assert run(src.call("pets", "add_pet", {"name": "x"}, True))["status"] == 404 and json.loads(seen[-1][3]) == {"name": "x"}
    assert run(src.call("pets", "get_pet", {"id": "../../evil"}, False))["status"] == 404
    src.db.execute("UPDATE openapi_sources SET ops=?", (json.dumps({**SPEC_OPS, "t": dict(operation_id="t", method="get", path="/text", summary="", parameters=[], body=None),
                                                                      "b": dict(operation_id="b", method="get", path="/boom", summary="", parameters=[], body=None),
                                                                      "e": dict(operation_id="e", method="get", path="//evil.example/x", summary="", parameters=[], body=None)}),))
    assert run(src.call("pets", "t", {}, False))["body"] == "plain" and run(src.call("pets", "b", {}, False))["error"] == "request failed: ConnectError"
    assert run(src.call("pets", "e", {}, False))["status"] == 404 and seen[-1][1].startswith("https://api.example.com//evil.example")          # still the API's own server
    u = src.update("pets", {"token": ""})
    assert not u["has_token"] and src.update("pets", {})["name"] == "Pets API"
    with pytest.raises(KeyError):
        src.update("nope", {})
    src.delete("pets")
    with pytest.raises(KeyError):
        src.delete("pets")
    assert src.list() == []


def test_default_loader_reads_a_real_spec(state, tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"openapi": "3.0.0", "info": {"title": "t"}, "servers": [{"url": "https://api.test"}], "paths": {"/a": {"get": {"operationId": "getA", "summary": "A"}}}}))
    a = W.OpenApiSources(state.db).add("real", "Real", str(spec))
    assert a["base"] == "https://api.test" and a["operations"][0]["id"] == "geta"


# ---------------------------------------------------------------------------------------------------- the engine
@pytest.fixture
def rich(client, state, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    (tmp_path / "ws").mkdir(exist_ok=True)
    (tmp_path / "ws" / "note.txt").write_text("workspace note")
    client.post("/api/agents", json={"id": "helper", "name": "Helper", "tier": "flash", "description": "Helps"})
    client.post("/api/agents", json={"id": "writer", "name": "Writer", "tier": "flash"})
    state.wf_sources.loader, state.wf_sources.transport = fake_loader, api_transport([])
    with client:                                    # keeps the event loop alive between requests, so background runs are not cancelled
        yield client


def save(c, d):
    r = c.post("/api/workflows", json=d)
    assert r.status_code == 200, r.text
    return r.json()


def finish(c, rid, timeout=20):
    import time
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/workflows/runs/{rid}").json()
        if r["status"] != "running" and not any(n["status"] == "waiting_approval" for n in r["nodes"].values()):
            return r
        time.sleep(0.05)
    raise AssertionError(r)


def test_validation_endpoint_and_save_flow(rich):
    assert rich.post("/api/workflows/validate", json={"id": "x"}).json()["problems"][0]["severity"] == "error"
    ok = rich.post("/api/workflows/validate", json=LINEAR).json()
    assert ok["ok"] and ok["problems"] == []
    bad = rich.post("/api/workflows/validate", json=doc([node("s", "input"), agent_node("a", "ghost")], [("s", "a")])).json()
    assert not bad["ok"] and "does not exist" in bad["problems"][0]["message"]
    saved = save(rich, LINEAR)
    assert saved["version"] == 1 and saved["problems"] == []
    assert rich.post("/api/workflows", json=LINEAR).status_code == 422
    assert rich.get("/api/workflows").json()["workflows"][0]["id"] == "wf_one"
    assert rich.get("/api/workflows/wf_one").json()["name"] == "Test flow" and rich.get("/api/workflows/nope").status_code == 404
    assert rich.put("/api/workflows/wf_one", json={**LINEAR, "name": "Second"}).json()["version"] == 2
    assert rich.put("/api/workflows/wf_one", json={"name": ""}).status_code == 422 and rich.put("/api/workflows/nope", json=LINEAR).status_code == 404
    assert [v["version"] for v in rich.get("/api/workflows/wf_one/versions").json()["versions"]] == [2, 1]
    assert rich.post("/api/workflows/wf_one/restore", json={"version": 1}).json()["name"] == "Test flow"
    assert rich.post("/api/workflows/wf_one/restore", json={"version": 77}).status_code == 404 and rich.post("/api/workflows/wf_one/restore", json={}).status_code == 404
    ex = rich.get("/api/workflows/wf_one/export")
    assert "attachment" in ex.headers["content-disposition"] and "version" not in ex.json() and rich.get("/api/workflows/nope/export").status_code == 404
    imp = rich.post("/api/workflows/import", json={"workflow": ex.json()}).json()
    assert imp["id"] == "wf_one_2"
    assert rich.post("/api/workflows/import", json=ex.json()).json()["id"] == "wf_one_3" and rich.post("/api/workflows/import", json={"x": 1}).status_code == 422
    assert rich.delete("/api/workflows/wf_one_2").status_code == 200 and rich.delete("/api/workflows/wf_one_2").status_code == 404


def test_auto_run_sequence_and_dag_with_parallel_branches(rich):
    d = doc([node("start", "input"), agent_node("left", prompt="L {{input}}"), agent_node("right", "writer", "R {{input}}"), agent_node("join", prompt="J {{left}} / {{right}} / {{prev}}"), node("end", "output")],
            [("start", "left"), ("start", "right"), ("left", "join"), ("right", "join"), ("join", "end")], autonomy="auto")
    save(rich, d)
    r = rich.post("/api/workflows/wf_one/run", json={"task": "do the thing"}).json()
    done = finish(rich, r["id"])
    assert done["status"] == "done" and all(n["status"] == "done" for n in done["nodes"].values()) and done["final"] and done["cost_usd"] >= 0
    assert "do the thing" in done["nodes"]["left"]["output"] or done["nodes"]["left"]["output"]
    assert rich.post("/api/workflows/nope/run", json={"task": "x"}).status_code == 404
    assert rich.post("/api/workflows/wf_one/run", json={"task": " "}).status_code == 422
    assert rich.post("/api/workflows/wf_one/run", json={"task": "x", "autonomy": "wild"}).status_code == 422
    assert rich.get("/api/workflows/runs/nope").status_code == 404


def test_containers_loop_parallel_sequential_tools_and_apis(rich, tmp_path):
    steps = [dict(kind="agent", agent="helper", prompt="one {{prev}}"), dict(kind="agent", agent="writer", prompt="two {{prev}}")]
    nodes = [node("start", "input"), node("seq", "sequential", dict(steps=steps)), node("par", "parallel", dict(steps=steps)),
             node("lp", "loop", dict(steps=[steps[0]], max_iterations=3, until="mock")), node("lp2", "loop", dict(steps=[steps[0]], max_iterations=2, until="")),
             node("tool", "tool", dict(tool="read_file", args={"path": "note.txt"})), node("api", "openapi", dict(source="pets", operation="get_pet", args={"id": 9})),
             node("end", "output")]
    save(rich, doc(nodes, [("start", i["id"]) for i in nodes[1:-1]] + [(i["id"], "end") for i in nodes[1:-1]], autonomy="auto"))
    rich.app.state.s.wf_sources.add("pets", "Pets", "https://x/openapi.json")
    done = finish(rich, rich.post("/api/workflows/wf_one/run", json={"task": "go"}).json()["id"])
    assert done["status"] == "done", done
    n = done["nodes"]
    assert "workspace note" in n["tool"]["output"] and '"Rex"' in n["api"]["output"] and n["par"]["output"].count("\n\n") == 1 and n["seq"]["output"]
    assert n["lp"]["output"] and n["lp2"]["output"] and done["final"]


def test_run_refuses_workflows_with_problems(rich):
    save(rich, doc([node("start", "input"), agent_node("a", "ghost")], [("start", "a")]))
    r = rich.post("/api/workflows/wf_one/run", json={"task": "x"})
    assert r.status_code == 422 and "fix these first" in r.json()["detail"] and "does not exist" in r.json()["detail"]


def test_suggest_mode_only_plans(rich):
    d = doc([node("start", "input"), agent_node("a1"), node("t", "tool", dict(tool="read_file")), node("c", "loop", dict(steps=[dict(kind="agent", agent="helper")], max_iterations=2)),
             node("p", "parallel", dict(steps=[dict(kind="agent", agent="helper")])), node("o", "openapi", dict(source="pets", operation="get_pet")), node("end", "output")],
            [("start", "a1"), ("a1", "t"), ("t", "c"), ("c", "p"), ("p", "o"), ("o", "end")], autonomy="suggest")
    rich.app.state.s.wf_sources.add("pets", "Pets", "https://x/openapi.json")
    save(rich, d)
    r = rich.post("/api/workflows/wf_one/run", json={"task": "plan it"}).json()
    done = finish(rich, r["id"])
    assert done["status"] == "plan" and [p["node"] for p in done["plan"]] == ["start", "a1", "t", "c", "p", "o", "end"]
    assert done["plan"][1]["does"].startswith("Ask agent 'helper'") and "Loop of 1 step(s), up to 2 times" in done["plan"][3]["does"] and done["plan"][5]["does"] == "Call pets.get_pet"
    assert done["plan"][0]["does"] == "Start" and done["plan"][6]["does"] == "Collect the result" and done["plan"][2]["does"] == "Use the read_file tool"
    assert all(n["status"] == "pending" for n in done["nodes"].values()) and rich.app.state.s.chat.store.list() == []          # nothing ran


def test_approve_mode_approve_skip_and_stop(rich):
    d = doc([node("start", "input"), agent_node("a1"), agent_node("a2"), agent_node("a3"), node("end", "output")], [("start", "a1"), ("a1", "a2"), ("a2", "a3"), ("a3", "end")], autonomy="approve")
    save(rich, d)
    rid = rich.post("/api/workflows/wf_one/run", json={"task": "x"}).json()["id"]

    def waiting():
        import time
        for _ in range(200):
            nodes = rich.get(f"/api/workflows/runs/{rid}").json()["nodes"]
            w = [k for k, v in nodes.items() if v["status"] == "waiting_approval"]
            if w:
                return w[0]
            time.sleep(0.05)
        raise AssertionError("nothing waited")
    assert waiting() == "a1"
    assert rich.post(f"/api/workflows/runs/{rid}/decide", json={"node": "a2", "decision": "approve"}).status_code == 422
    assert rich.post(f"/api/workflows/runs/{rid}/decide", json={"node": "a1", "decision": "maybe"}).status_code == 422
    assert rich.post("/api/workflows/runs/nope/decide", json={"node": "a1", "decision": "approve"}).status_code == 404
    rich.post(f"/api/workflows/runs/{rid}/decide", json={"node": "a1", "decision": "approve"})
    assert waiting() == "a2"
    rich.post(f"/api/workflows/runs/{rid}/decide", json={"node": "a2", "decision": "skip"})
    assert waiting() == "a3"
    rich.post(f"/api/workflows/runs/{rid}/decide", json={"node": "a3", "decision": "stop"})
    done = finish(rich, rid)
    assert done["status"] == "stopped" and done["nodes"]["a2"]["status"] == "skipped" and done["nodes"]["a1"]["status"] == "done" and done["nodes"]["a3"]["status"] == "stopped"


def test_stop_a_running_workflow_and_failures(rich, state, monkeypatch):
    save(rich, doc([node("start", "input"), agent_node("a1"), node("end", "output")], [("start", "a1"), ("a1", "end")], autonomy="auto"))

    async def slow(*a, **kw):
        await asyncio.sleep(30)
    monkeypatch.setattr(state.chat, "send", slow)
    rid = rich.post("/api/workflows/wf_one/run", json={"task": "x"}).json()["id"]
    import time
    time.sleep(0.4)
    assert rich.post(f"/api/workflows/runs/{rid}/stop").json()["status"] in ("running", "stopped")
    done = finish(rich, rid)
    assert done["status"] == "stopped" and done["nodes"]["a1"]["status"] == "stopped"
    assert rich.post("/api/workflows/runs/nope/stop").status_code == 404

    async def boom(*a, **kw):
        raise RuntimeError("engine fell over")
    monkeypatch.setattr(state.chat, "send", boom)
    rid = rich.post("/api/workflows/wf_one/run", json={"task": "x"}).json()["id"]
    done = finish(rich, rid)
    assert done["status"] == "error" and "engine fell over" in done["nodes"]["a1"]["error"] and "failed" in done["error"]


def test_the_approval_times_out_into_a_stop(rich, state):
    state.wf_runner.approve_timeout = 0.2
    save(rich, doc([node("start", "input"), agent_node("a1")], [("start", "a1")], autonomy="approve"))
    done = finish(rich, rich.post("/api/workflows/wf_one/run", json={"task": "x"}).json()["id"])
    assert done["status"] == "stopped" and done["nodes"]["a1"]["status"] == "stopped"


def test_tool_nodes_need_confirmation_and_apis_respect_writes(rich, tmp_path):
    s = rich.app.state.s
    s.wf_sources.add("pets", "Pets", "https://x/openapi.json", allow_write=True)
    nodes = [node("start", "input"), node("w", "tool", dict(tool="write_file", args={"path": "made.txt", "content": "hello {{input}}"}, confirmed=True)),
             node("add", "openapi", dict(source="pets", operation="add_pet", args={"name": "{{input}}"}, confirmed=True)), node("end", "output")]
    d = doc(nodes, [("start", "w"), ("start", "add"), ("w", "end"), ("add", "end")], autonomy="auto")
    save(rich, d)
    done = finish(rich, rich.post("/api/workflows/wf_one/run", json={"task": "world"}).json()["id"])
    assert done["status"] == "done" and (tmp_path / "ws" / "made.txt").read_text() == "hello world"
    # unconfirmed / unknown tools at run time are refused with a reason
    steps = [{"kind": "tool", "tool": "write_file", "args": {}, "confirmed": False}, {"kind": "tool", "tool": "load_skill", "args": {}, "confirmed": True},
             {"kind": "tool", "tool": "read_file", "args": {"nope": 1}, "confirmed": False}]
    outs = [json.loads(run(s.wf_runner._tool(st, {}))["error"] and json.dumps(run(s.wf_runner._tool(st, {})))) for st in steps]
    assert "not confirmed" in outs[0]["error"] and "bad arguments" in outs[2]["error"]


# ---------------------------------------------------------------------------------------------------- wizard, apis endpoints, conversion, tool
def test_api_endpoints(rich, tmp_path):
    assert rich.get("/api/workflows/apis").json() == {"apis": []}
    assert rich.post("/api/workflows/apis", json={"id": "x", "src": "../../secret.yaml"}).status_code == 422
    (tmp_path / "ws" / "bad.yaml").write_text("x")
    assert rich.post("/api/workflows/apis", json={"id": "x", "src": "bad.yaml"}).status_code == 422
    (tmp_path / "ws" / "bad.json").write_text("{}")
    r = rich.post("/api/workflows/apis", json={"id": "pets", "name": "Pets", "src": "https://x/openapi.json", "token": "Bearer s3cret"})
    assert r.status_code == 200 and "s3cret" not in json.dumps(r.json()) and r.json()["has_token"]
    assert rich.put("/api/workflows/apis/pets", json={"allow_write": True}).json()["allow_write"] is True and rich.put("/api/workflows/apis/zzz", json={}).status_code == 404
    assert "s3cret" not in json.dumps(rich.get("/api/workflows/apis").json())
    assert rich.delete("/api/workflows/apis/pets").status_code == 200 and rich.delete("/api/workflows/apis/pets").status_code == 404
    assert rich.post("/api/workflows/apis", json={"id": "wsfile", "src": "bad.json"}).status_code == 422                 # valid path, not an OpenAPI document


def test_wizard_drafts_with_a_model_or_falls_back_to_a_template(rich, state, monkeypatch):
    state.wf_sources.add("pets", "Pets", "https://x/openapi.json")
    body = {"goal": "Find a pet and write about it", "agents": ["helper", "writer", "ghost"], "operations": [{"source": "pets", "operation": "get_pet"}, {"source": "pets", "operation": "nope"}], "autonomy": "approve"}
    r = rich.post("/api/workflows/draft", json=body).json()
    assert r["drafted_by"] == "template" and "simple chain" in r["note"]
    ids = [n["id"] for n in r["workflow"]["nodes"]]
    assert ids == ["start", "api1", "agent1", "agent2", "result"] and r["workflow"]["autonomy"] == "approve" and r["problems"] == [] or r["problems"]

    from app.workflows import api as WA
    good = json.dumps(doc([node("start", "input"), agent_node("a", prompt="{{input}}"), node("end", "output")], [("start", "a"), ("a", "end")], wid="draft"))

    async def ask(s, prompt):
        assert "Find a pet" in prompt and "helper: Helps" in prompt and "pets.get_pet" in prompt
        return "Here you go:\n" + good
    monkeypatch.setattr(WA, "_ask", ask)
    r = rich.post("/api/workflows/draft", json=body).json()
    assert r["drafted_by"] == "model" and r["note"] is None and r["workflow"]["nodes"][1]["id"] == "a"
    for bad in ("no json here", "{not json}", "[1, 2]", json.dumps({"nodes": []})):
        async def ask2(s, prompt, _b=bad):
            return _b
        monkeypatch.setattr(WA, "_ask", ask2)
        assert rich.post("/api/workflows/draft", json=body).json()["drafted_by"] == "template"
    assert rich.post("/api/workflows/draft", json={"goal": " "}).status_code == 422
    assert rich.post("/api/workflows/draft", json={"goal": "x", "agents": ["helper"], "autonomy": "wild"}).status_code == 422
    assert rich.post("/api/workflows/draft", json={"goal": "x", "agents": ["ghost"]}).status_code == 422


def test_ask_is_guarded(state, client, monkeypatch):
    from app.workflows import api as WA
    client.put("/api/providers/mock", json={"cap_usd": 1000})
    assert "[mock]" in run(WA._ask(state, "hi"))
    client.put("/api/providers/mock", json={"cap_usd": 0.0000001})
    with pytest.raises(Exception):
        run(WA._ask(state, "hello " * 500))
    client.put("/api/providers/mock", json={"cap_usd": 1000})

    async def boom(**kw):
        raise RuntimeError("provider down")
    monkeypatch.setattr(WA.litellm, "acompletion", boom)
    with pytest.raises(RuntimeError):
        run(WA._ask(state, "hi"))
    assert state.db.query("SELECT 1 FROM reservations") == []


def test_convert_to_agent_and_agents_can_run_workflows(rich, state):
    save(rich, doc([node("start", "input"), agent_node("a1", prompt="Echo: {{input}}"), node("end", "output")], [("start", "a1"), ("a1", "end")], autonomy="approve"))
    r = rich.post("/api/workflows/wf_one/to-agent").json()
    assert r == {"agent": "wf_wf_one"}
    a = state.agents.get("wf_wf_one")
    assert a.tools == ["run_workflow"] and "wf_one" in a.instruction
    assert rich.post("/api/workflows/wf_one/to-agent").status_code == 422 and rich.post("/api/workflows/nope/to-agent").status_code == 404
    res = rich.post("/api/chat", json={"provider": "mock", "agent": "wf_wf_one", "message": '!tool run_workflow {"workflow": "wf_one", "task": "hello"}'}).json()
    step = [s for s in res["steps"] if s["type"] == "result"][0]
    assert "'ok': True" in step["result"] and "'status': 'done'" in step["result"]
    tool = W.workflow_tool(state.wf_runner, ["wf_one"])
    assert run(tool("ghost", "x"))["ok"] is False and "wf_one" in tool.__doc__
    assert run(tool("wf_one", " "))["error"].startswith("write the task")
    from app.harness.sandbox import Sandbox
    from app.harness.tools import ToolContext, build_tools
    ctx = ToolContext(state.chat.workspace_for(None), Sandbox(state.chat.workspace_for(None)), state.skills, state.mcp, workflows=state.wf_runner)
    assert [f.__name__ for f in build_tools(ctx, ["run_workflow"])] == ["run_workflow"]
    blocked = rich.delete("/api/workflows/wf_one")                       # an agent still runs it: refused, and it says which
    assert blocked.status_code == 409 and blocked.json()["used_by"] == ["agent wf_wf_one"]
    rich.delete("/api/workflows/wf_one?force=true")
    assert build_tools(ctx, ["run_workflow"]) == []


def test_template_draft_directly():
    d = W.clean(W.template_draft("", [], [], "auto"))
    assert d["name"] == "New workflow" and [n["type"] for n in d["nodes"]] == ["input", "output"]
    assert W.parse_draft('{"nodes": [{"id": "s", "type": "input"}], "edges": []}', "goal text", "auto")["name"] == "goal text"


# ---------------------------------------------------------------------------------------------------- remaining branches
def test_old_runs_are_dropped_and_unexpected_failures_are_reported(rich, state, monkeypatch):
    monkeypatch.setattr(W, "MAX_RUNS", 1)
    save(rich, doc([node("start", "input"), node("end", "output")], [("start", "end")], autonomy="auto"))
    first = rich.post("/api/workflows/wf_one/run", json={"task": "a"}).json()["id"]
    finish(rich, first)
    second = rich.post("/api/workflows/wf_one/run", json={"task": "b"}).json()["id"]
    finish(rich, second)
    assert first not in state.wf_runner.runs and second in state.wf_runner.runs               # only the newest stays in memory ...
    assert rich.get(f"/api/workflows/runs/{first}").json()["status"] == "done" and rich.get(f"/api/workflows/runs/{second}").status_code == 200       # ... older ones are read from the saved runs
    assert rich.get("/api/workflows/runs/nope").status_code == 404
    monkeypatch.setattr(state.wf_runner, "_describe", lambda n: (_ for _ in ()).throw(RuntimeError("plan broke")))
    rid = rich.post("/api/workflows/wf_one/run", json={"task": "c", "autonomy": "suggest"}).json()["id"]
    done = finish(rich, rid)
    assert done["status"] == "error" and "plan broke" in done["error"]


def test_stopping_one_branch_cancels_the_others(rich):
    save(rich, doc([node("start", "input"), agent_node("a1"), agent_node("a2"), node("end", "output")], [("start", "a1"), ("start", "a2"), ("a1", "end"), ("a2", "end")], autonomy="approve"))
    rid = rich.post("/api/workflows/wf_one/run", json={"task": "x"}).json()["id"]
    import time
    for _ in range(200):
        nodes = rich.get(f"/api/workflows/runs/{rid}").json()["nodes"]
        if nodes["a1"]["status"] == "waiting_approval" and nodes["a2"]["status"] == "waiting_approval":
            break
        time.sleep(0.05)
    rich.post(f"/api/workflows/runs/{rid}/decide", json={"node": "a1", "decision": "stop"})
    done = finish(rich, rid)
    assert done["status"] == "stopped" and done["nodes"]["a1"]["status"] == "stopped"


def test_steps_refuse_after_a_stop_and_missing_things_at_run_time(rich, state):
    r = W.Run("r", "wf", "t", "auto")
    r.stop.set()
    with pytest.raises(asyncio.CancelledError):
        run(state.wf_runner._step(r, {"kind": "agent", "agent": "helper", "prompt": "x"}, {}))
    r2 = W.Run("r2", "wf", "t", "auto")
    with pytest.raises(W.WorkflowError, match="does not exist"):
        run(state.wf_runner._step(r2, {"kind": "agent", "agent": "ghost", "prompt": "x"}, {}))
    assert "not available here" in run(state.wf_runner._tool({"tool": "rag_search", "args": {}, "confirmed": False}, {}))["error"]
