"""Route-level coverage for app/main.py: health + lifespan, providers (Ollama pull, credits, billing preflight),
chat error mapping and background-job routes, notes, memory, issues and debug runs, agents/MCP/skills/settings,
the side agent (AG-UI), dashboard, folder picker, voice, OpenTelemetry receiver and the ACP routes."""
import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import fsbrowse
from app import memory_store as MS
from app import voice as V
from app.acp_agents import AcpError
from app.budget import BudgetExceeded
from app.harness import sandbox as sandbox_mod
from app.jobs import Job, NoteInbox
from app.main import AcpChatIn, create_app, start_debug_run


def events_of(text: str) -> list[dict]:
    """Parse a text/event-stream body into its JSON events (keepalive comments are skipped)."""
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def wait_job(state, jid: str, timeout: float = 20) -> Job:
    job = state.jobs.jobs[jid]
    for _ in range(int(timeout / 0.05)):
        if job.ended is not None:
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("background job did not finish in time")


# ------------------------------------------------------------------ health + lifespan
def test_health_reports_versions(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True and isinstance(r.json()["adk"], str)


def test_lifespan_shutdown_stops_agents_and_jobs(state, monkeypatch):
    calls = []

    async def close_all():
        calls.append("close_all")
    monkeypatch.setattr(state.acp, "close_all", close_all)
    monkeypatch.setattr(state.acp, "stop", lambda: calls.append("stop"))
    with TestClient(create_app(state)) as c:
        assert c.get("/api/health").status_code == 200
        assert calls == []                      # nothing happens until the server stops
    assert calls == ["close_all", "stop"]       # leaving the context = server shutdown


# ------------------------------------------------------------------ providers: list, Ollama, credits, models, update
def test_provider_list_and_ollama_suggestions(client):
    rows = client.get("/api/providers").json()
    mock = next(p for p in rows if p["id"] == "mock")
    assert mock["enabled"] is True and "catalog" in mock and "credits" in mock
    sug = client.get("/api/providers/ollama/ollama/suggestions").json()
    assert sug and all("id" in s for s in sug)


@pytest.fixture
def ollama_http(monkeypatch):
    """Fake Ollama HTTP API: `script["resp"]` builds the response for each request; `seen` records request bodies."""
    script, seen = {}, []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return script["resp"](req)

    real = httpx.AsyncClient

    def factory(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return script, seen


def test_ollama_pull_streams_progress_and_done(client, ollama_http):
    script, seen = ollama_http
    script["resp"] = lambda req: httpx.Response(200, content=(
        b'{"status":"pulling manifest"}\n{"status":"downloading","completed":5,"total":10}\n\n{"status":"success"}\n'))
    evs = events_of(client.post("/api/providers/ollama/pull", json={"model": "llama3.2:1b"}).text)
    assert [e["type"] for e in evs] == ["progress", "progress", "progress", "done"]
    assert evs[1] == {"type": "progress", "status": "downloading", "completed": 5, "total": 10}
    assert evs[-1] == {"type": "done", "model": "llama3.2:1b"}
    assert seen[0] == {"model": "llama3.2:1b", "name": "llama3.2:1b", "stream": True}


def test_ollama_pull_error_paths(client, ollama_http):
    script, _ = ollama_http
    assert client.post("/api/providers/mock/pull", json={"model": "x1"}).status_code == 404      # not an Ollama provider
    assert client.post("/api/providers/ollama/pull", json={"model": "bad name!"}).status_code == 422
    script["resp"] = lambda req: httpx.Response(500, content=b"boom")
    evs = events_of(client.post("/api/providers/ollama/pull", json={"model": "llama3.2"}).text)
    assert evs == [{"type": "error", "message": "Ollama 500: boom"}]
    script["resp"] = lambda req: httpx.Response(200, content=b'{"error":"pull model manifest: file does not exist"}\n')
    evs = events_of(client.post("/api/providers/ollama/pull", json={"model": "llama3.2"}).text)
    assert evs == [{"type": "error", "message": "pull model manifest: file does not exist"}]

    def down(req):
        raise httpx.ConnectError("refused", request=req)
    script["resp"] = down
    evs = events_of(client.post("/api/providers/ollama/pull", json={"model": "llama3.2"}).text)
    assert len(evs) == 1 and evs[0]["type"] == "error" and "isn't reachable" in evs[0]["message"]


def test_provider_credits_endpoints(client):
    assert client.get("/api/providers/nope/credits").status_code == 404
    assert client.get("/api/providers/mock/credits").json()["mode"] == "off"
    assert client.put("/api/providers/nope/credits", json={"mode": "manual"}).status_code == 404
    assert client.put("/api/providers/mock/credits", json={"mode": "bogus"}).status_code == 422
    r = client.put("/api/providers/mock/credits", json={"mode": "manual", "balance_usd": 25})
    assert r.status_code == 200 and r.json()["mode"] == "manual" and r.json()["balance_usd"] == 25.0
    assert client.post("/api/providers/nope/credits/refresh").status_code == 404
    assert client.post("/api/providers/mock/credits/refresh").json()["balance_usd"] == 25.0   # non-auto kinds keep the balance


def test_provider_models_and_update_errors(client):
    assert client.get("/api/providers/nope/models").status_code == 404
    assert client.get("/api/providers/mock/models").status_code == 200
    assert client.put("/api/providers/nope", json={"enabled": True}).status_code == 404
    assert client.put("/api/providers/mock", json={"cap_period": "weekly"}).status_code == 422


# ------------------------------------------------------------------ billing
def test_billing_summary_ledger_and_preflight(client, state):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    summary = client.get("/api/billing/summary").json()
    assert any(p["provider"] == "mock" or p.get("id") == "mock" for p in summary["providers"]) and "total_spent_usd" in summary
    assert isinstance(client.get("/api/billing/ledger", params={"limit": 5000, "provider": "mock"}).json(), list)
    ok = client.post("/api/billing/preflight", json={"provider": "mock", "message": "hello there"}).json()
    assert ok["would_allow"] is True and ok["projected_usd"] >= 0 and ok["remaining_usd"] >= ok["projected_usd"]
    assert client.post("/api/billing/preflight", json={"provider": "nope", "message": "x"}).status_code == 404
    client.put("/api/providers/mock", json={"enabled": False})
    assert client.post("/api/billing/preflight", json={"provider": "mock", "message": "hi"}).json()["would_allow"] is False


def test_preflight_survives_a_misconfigured_provider(client):
    # vertex_ai without a GCP project: litellm_target() raises, preflight falls back to the plain model string
    r = client.post("/api/billing/preflight", json={"provider": "vertex_ai", "message": "hi", "model": "gemini-x"})
    assert r.status_code == 200 and r.json()["would_allow"] is False      # not enabled, so never allowed


# ------------------------------------------------------------------ chat error mapping + background jobs
def test_chat_error_mapping(client, state, monkeypatch):
    assert client.post("/api/chat", json={"provider": "nope", "message": "x"}).status_code == 404

    async def value_error(*a, **k):
        raise ValueError("bad request shape")

    monkeypatch.setattr(state.chat, "send", value_error)
    r = client.post("/api/chat", json={"provider": "mock", "message": "x"})
    assert r.status_code == 422 and r.json()["detail"] == "bad request shape"

    async def crash(*a, **k):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(state.chat, "send", crash)
    r = client.post("/api/chat", json={"provider": "mock", "message": "x"})
    assert r.status_code == 502 and r.json()["detail"] == "RuntimeError: upstream exploded"


def test_chat_stream_refusals_and_errors_become_events(client, state, monkeypatch):
    async def refused(*a, **k):
        raise BudgetExceeded({"reason": "cap", "message": "over the cap"})

    monkeypatch.setattr(state.chat, "send", refused)
    evs = events_of(client.post("/api/chat/stream", json={"provider": "mock", "message": "x"}).text)
    assert evs[0]["type"] == "job" and any(e == {"type": "refused", "detail": {"reason": "cap", "message": "over the cap"}, "seq": e["seq"]} for e in evs)
    assert evs[-1]["type"] == "end"

    async def unknown(*a, **k):
        raise KeyError("nope")

    monkeypatch.setattr(state.chat, "send", unknown)
    evs = events_of(client.post("/api/chat/stream", json={"provider": "mock", "message": "x"}).text)
    assert any(e["type"] == "error" and e["message"] == "unknown provider or agent" for e in evs)

    async def crash(*a, **k):
        raise RuntimeError("socket closed")

    monkeypatch.setattr(state.chat, "send", crash)
    evs = events_of(client.post("/api/chat/stream", json={"provider": "mock", "message": "x"}).text)
    err = next(e for e in evs if e["type"] == "error")
    assert "socket closed" in json.dumps(err)


def test_chat_stream_is_refused_while_the_chat_runs(client, state):
    state.jobs.jobs["busy-1"] = Job("busy-1", session_id="sess-busy")
    r = client.post("/api/chat/stream", json={"provider": "mock", "message": "x", "session_id": "sess-busy"})
    assert r.status_code == 409 and "still running" in r.json()["detail"]
    del state.jobs.jobs["busy-1"]


def test_job_list_events_and_cancel(client, state):
    evs = events_of(client.post("/api/chat/stream", json={"provider": "mock", "message": "hello job"}).text)
    jid = evs[0]["job_id"]
    assert evs[-1] == {"type": "end", "status": "done", "seq": evs[-1]["seq"]}
    assert any(j["job_id"] == jid and j["status"] == "done" for j in client.get("/api/chat/jobs").json())
    replay = events_of(client.get(f"/api/chat/jobs/{jid}/events", params={"after": -1}).text)
    assert [e["type"] for e in replay][-2:] == ["done", "end"]
    later = events_of(client.get(f"/api/chat/jobs/{jid}/events", params={"after": evs[1]["seq"]}).text)
    assert later and all(e["seq"] > evs[1]["seq"] for e in later)
    assert client.get("/api/chat/jobs/nope/events").status_code == 404
    assert client.post("/api/chat/jobs/nope/cancel").status_code == 404
    finished = client.post(f"/api/chat/jobs/{jid}/cancel")
    assert finished.status_code == 404                                   # finished jobs cannot be cancelled

    class FakeTask:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True
    task = FakeTask()
    state.jobs.jobs["live-1"] = Job("live-1", task=task)
    assert client.post("/api/chat/jobs/live-1/cancel").json() == {"ok": True} and task.cancelled
    del state.jobs.jobs["live-1"]


def test_job_notes_queue_steer_retract(client, state):
    emitted = []
    job = Job("note-job", inbox=NoteInbox(lambda ev: emitted.append(ev)))
    state.jobs.jobs["note-job"] = job
    assert client.post("/api/chat/jobs/nope/notes", json={"text": "hi"}).status_code == 409
    r = client.post("/api/chat/jobs/note-job/notes", json={"text": "  use the blue file  ", "mode": "queue"})
    assert r.status_code == 200 and r.json()["text"] == "use the blue file" and r.json()["mode"] == "queue"
    nid = r.json()["id"]
    assert client.post("/api/chat/jobs/note-job/notes", json={"text": "   "}).status_code == 422       # empty after trimming
    assert client.post("/api/chat/jobs/note-job/notes", json={"text": "x", "mode": "bogus"}).status_code == 422
    assert client.delete("/api/chat/jobs/note-job/notes/nope").status_code == 404
    assert client.delete(f"/api/chat/jobs/note-job/notes/{nid}").json() == {"ok": True}
    assert job.inbox.items == [] and emitted[-1]["type"] == "note_retracted"
    assert client.delete(f"/api/chat/jobs/note-job/notes/{nid}").status_code == 404   # already retracted
    job.ended = time.time()
    assert client.post("/api/chat/jobs/note-job/notes", json={"text": "late"}).status_code == 409


# ------------------------------------------------------------------ chat sessions: rename/delete/context/summarize
def test_session_rename_delete_context_and_summarize(client, state, monkeypatch):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    sid = client.post("/api/chat", json={"provider": "mock", "message": "hello there"}).json()["session_id"]
    assert client.put("/api/chat/sessions/nope", json={"title": "x"}).status_code == 404
    assert client.delete("/api/chat/sessions/nope").status_code == 404
    assert client.get("/api/chat/sessions/nope/context").status_code == 404
    ctx = client.get(f"/api/chat/sessions/{sid}/context")
    assert ctx.status_code == 200 and isinstance(ctx.json(), dict)
    assert client.post("/api/chat/sessions/nope/summarize").status_code == 404

    assert [s["id"] for s in client.get("/api/chat/sessions").json()] == [sid]
    assert client.get("/api/chat/sessions").json()[0]["running"] is False
    busy = Job("sum-busy", session_id=sid)
    state.jobs.jobs["sum-busy"] = busy
    assert client.post(f"/api/chat/sessions/{sid}/summarize").status_code == 409
    assert client.get("/api/chat/sessions").json()[0]["running"] is True
    assert client.get(f"/api/chat/sessions/{sid}").json()["active_job"]["job_id"] == "sum-busy"
    del state.jobs.jobs["sum-busy"]
    got = client.get(f"/api/chat/sessions/{sid}").json()
    assert got["active_job"] is None and [m["role"] for m in got["messages"]] == ["user", "bot"]
    assert client.put(f"/api/chat/sessions/{sid}", json={"title": "Renamed chat"}).json() == {"ok": True}
    assert client.get(f"/api/chat/sessions/{sid}").json()["session"]["title"] == "Renamed chat"

    async def nothing(*a, **k):
        raise ValueError("nothing new to summarise since the last summary")

    monkeypatch.setattr(state.chat, "summarize", nothing)
    r = client.post(f"/api/chat/sessions/{sid}/summarize")
    assert r.status_code == 422 and "nothing new" in r.json()["detail"]

    async def over_cap(*a, **k):
        raise BudgetExceeded({"reason": "cap", "message": "over"})

    monkeypatch.setattr(state.chat, "summarize", over_cap)
    assert client.post(f"/api/chat/sessions/{sid}/summarize").status_code == 402

    async def crash(*a, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr(state.chat, "summarize", crash)
    r = client.post(f"/api/chat/sessions/{sid}/summarize")
    assert r.status_code == 502 and r.json()["detail"] and "model down" not in r.json()["detail"]   # friendly text, no raw error
    monkeypatch.undo()
    assert client.delete(f"/api/chat/sessions/{sid}").json() == {"ok": True}
    assert client.get(f"/api/chat/sessions/{sid}").status_code == 404


# ------------------------------------------------------------------ memory
def test_memory_entity_routes(client):
    MS._write([{"name": "Ada", "entityType": "person", "observations": ["likes math", "lives in London"]},
               {"name": "Bob", "entityType": "person", "observations": ["a", "b"]}], [])
    assert client.put("/api/memory/entities/Nobody", json={"observations": ["x"]}).status_code == 404
    r = client.put("/api/memory/entities/Ada", json={"observations": ["likes maths"], "entityType": "scientist"})
    assert r.status_code == 200 and r.json()["observations"] == ["likes maths"] and r.json()["entityType"] == "scientist"
    assert client.delete("/api/memory/entities/Nobody").status_code == 404
    assert client.delete("/api/memory/entities/Nobody/observations/0").status_code == 404
    assert client.delete("/api/memory/entities/Ada/observations/5").status_code == 404
    assert client.delete("/api/memory/entities/Bob/observations/0").json()["observations"] == ["b"]
    assert client.delete("/api/memory/entities/Bob").json() == {"deleted": "Bob", "relations_removed": 0}
    assert [e["name"] for e in client.get("/api/memory/entities").json()] == ["Ada"]


def test_memory_clear_needs_the_confirmation_phrase(client):
    MS._write([{"name": "Ada", "entityType": "person", "observations": ["x"]}], [])
    assert client.post("/api/memory/clear", json={}).status_code == 422
    assert client.post("/api/memory/clear", json={"confirm": "yes please"}).status_code == 422
    assert client.post("/api/memory/clear", json={"confirm": "DELETE ALL MEMORY"}).json() == {"cleared": 1, "relations": 0}
    assert client.get("/api/memory/entities").json() == []


# ------------------------------------------------------------------ issues and debug runs
def test_issue_routes(client, state):
    client.put("/api/providers/mock", json={"enabled": False})            # no model provider: a debug run cannot start
    i1 = state.issues.add("other", "issue-src", "Something odd", detail="details here")
    assert any(i["id"] == i1["id"] for i in client.get("/api/issues").json())
    assert client.get("/api/issues", params={"status": "all"}).status_code == 200
    assert client.post(f"/api/issues/{i1['id']}/debug").status_code == 409
    assert client.post("/api/issues/nope/debug").status_code == 404
    assert client.post("/api/issues/nope/resolve").status_code == 404
    assert client.post(f"/api/issues/{i1['id']}/resolve").json() == {"ok": True}
    assert all(i["id"] != i1["id"] for i in client.get("/api/issues").json())

    client.put("/api/providers/mock", json={"enabled": True, "cap_usd": 1000})


def test_issue_debug_over_http_starts_a_run(client, state):
    client.put("/api/providers/mock", json={"enabled": True, "cap_usd": 1000})
    i2 = state.issues.add("other", "issue-two", "Another problem")
    r = client.post(f"/api/issues/{i2['id']}/debug")
    assert r.status_code == 200 and r.json()["job_id"]
    assert state.issues.get(i2["id"])["debug_job"] == r.json()["job_id"]


def test_issue_debug_route_body_starts_a_run_inside_a_loop(client, state):
    """Same route body called on a running event loop (where the job can be scheduled)."""
    endpoint = next(r.endpoint for r in client.app.routes if getattr(r, "path", "") == "/api/issues/{iid}/debug")

    async def go():
        issue = state.issues.add("other", "issue-direct", "Direct debug")
        out = await endpoint(issue["id"], state)
        job = await wait_job(state, out["job_id"])
        return issue, out, job
    issue, out, job = asyncio.run(go())
    assert out["job_id"] and state.issues.get(issue["id"])["debug_job"] == out["job_id"]
    assert job.ended is not None


def test_debug_run_completes_and_reports_crashes(state, tmp_path, monkeypatch):
    state.providers.update("mock", {"cap_usd": 1000, "max_output_tokens": 64})
    (tmp_path / "ws").mkdir(exist_ok=True)
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))

    async def ok_case():
        issue = state.issues.add("mcp_failed", "dbg-ok", "MCP server 'dbg-ok' failed to start", detail="no such file")
        jid = start_debug_run(state, issue)
        again = start_debug_run(state, state.issues.get(issue["id"]))     # still running: the same run is reused
        return issue, jid, again, await wait_job(state, jid)
    issue, jid, again, job = asyncio.run(ok_case())
    assert job.status == "done" and any(e["type"] == "done" for e in job.events)
    assert again == jid and state.issues.get(issue["id"])["debug_job"] == job.id

    async def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(state.chat, "send", boom)

    async def crash_case():
        issue = state.issues.add("mcp_failed", "dbg-crash", "MCP server 'dbg-crash' failed", detail="x")
        return await wait_job(state, start_debug_run(state, issue))
    job = asyncio.run(crash_case())
    errs = [e["message"] for e in job.events if e["type"] == "error"]
    assert errs == ["Debug run failed: RuntimeError: kaboom"]


def test_debug_run_needs_an_enabled_provider(state):
    state.providers.update("mock", {"enabled": False})
    issue = state.issues.add("mcp_failed", "no-provider", "MCP server 'no-provider' failed")
    assert start_debug_run(state, issue) is None


def test_auto_debug_only_for_mcp_failures_and_only_when_enabled(state, tmp_path):
    state.providers.update("mock", {"cap_usd": 1000, "max_output_tokens": 64})
    (tmp_path / "ws").mkdir(exist_ok=True)
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))

    async def go():
        off = state.issues.add("mcp_failed", "auto-off", "MCP server 'auto-off' failed")
        state.db.set_setting("auto_debug_mcp", "1")
        on = state.issues.add("mcp_failed", "auto-on", "MCP server 'auto-on' failed")
        other = state.issues.add("other", "auto-other", "unrelated problem")
        await wait_job(state, state.issues.get(on["id"])["debug_job"])
        return off, on, other
    off, on, other = asyncio.run(go())
    assert state.issues.get(off["id"])["debug_job"] is None
    assert state.issues.get(on["id"])["debug_job"] is not None
    assert state.issues.get(other["id"])["debug_job"] is None


# ------------------------------------------------------------------ agents, tools, skills
def test_agent_and_tool_routes(client):
    tools = client.get("/api/tools").json()
    assert tools and all({"name", "description", "read_only"} <= set(t) for t in tools)
    assert client.put("/api/agents/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/agents/nope").status_code == 404
    assert client.post("/api/agents", json={"id": "Bad Id", "name": "x"}).status_code == 422
    created = client.post("/api/agents", json={"id": "helper_bot", "name": "Helper Bot", "instruction": "Be brief."})
    assert created.status_code == 200 and created.json()["id"] == "helper_bot"
    assert any(a["id"] == "helper_bot" for a in client.get("/api/agents").json())
    assert client.post("/api/agents", json={"id": "helper_bot", "name": "Again"}).status_code == 422          # duplicate
    assert client.put("/api/agents/helper_bot", json={"tier": "ultra"}).status_code == 422
    assert client.put("/api/agents/helper_bot", json={"description": "does things"}).json()["description"] == "does things"
    assert client.delete("/api/agents/helper_bot").json() == {"ok": True}
    builtin = next(a["id"] for a in client.get("/api/agents").json() if a.get("builtin"))
    assert client.delete(f"/api/agents/{builtin}").status_code == 422


def test_skill_routes_not_found_and_builtin_protection(client):
    assert client.get("/api/skills/nope").status_code == 404
    assert client.delete("/api/skills/nope").status_code == 404
    builtin = next(s["name"] for s in client.get("/api/skills").json() if s["source"] == "builtin")
    assert client.delete(f"/api/skills/{builtin}").status_code == 422
    assert client.get(f"/api/skills/{builtin}").json()["name"] == builtin


# ------------------------------------------------------------------ MCP servers
def test_mcp_update_delete_and_browser_settings(client, state):
    assert client.put("/api/mcp/nope", json={"enabled": False}).status_code == 404
    assert client.delete("/api/mcp/nope").status_code == 404
    builtin = next(m.id for m in state.mcp.list() if m.builtin)
    assert client.delete(f"/api/mcp/{builtin}").status_code == 422
    client.post("/api/mcp", json={"id": "scratch-srv", "command": "npx", "args": []})
    assert client.put("/api/mcp/scratch-srv", json={"enabled": False}).json()["enabled"] is False
    assert client.delete("/api/mcp/scratch-srv").json() == {"ok": True}
    assert client.get("/api/browser/settings").status_code == 200


def test_mcp_test_endpoint_outcomes(client, state, monkeypatch):
    assert client.post("/api/mcp/nope/test").status_code == 404
    client.post("/api/mcp", json={"id": "slow-srv", "command": "npx", "args": []})
    client.post("/api/mcp", json={"id": "fast-fail", "command": "npx", "args": []})
    client.post("/api/mcp", json={"id": "good-srv", "command": "npx", "args": []})
    import app.main as M

    async def timed_out(store, ids, ws, warns):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(M, "build_toolsets", timed_out)
    r = client.post("/api/mcp/slow-srv/test").json()
    assert r["ok"] is False and "timed out" in r["error"]
    issue = next(i for i in state.issues.list() if i["source"] == "slow-srv")
    assert issue["kind"] == "mcp_failed" and issue["action"] == {"type": "debug_mcp", "mcp": "slow-srv"}

    async def warned(store, ids, ws, warns):
        warns.append("command not found")
        return []
    monkeypatch.setattr(M, "build_toolsets", warned)
    r = client.post("/api/mcp/fast-fail/test").json()
    assert r == {"ok": False, "error": "command not found"}
    assert state.mcp_tests["fast-fail"]["ok"] is False

    closed = []

    class FakeToolset:
        async def get_tools(self):
            return [SimpleNamespace(name="read_file", description="Read a file from disk")]

        async def close(self):
            closed.append(True)

    async def working(store, ids, ws, warns):
        return [FakeToolset()]
    monkeypatch.setattr(M, "build_toolsets", working)
    r = client.post("/api/mcp/good-srv/test").json()
    assert r["ok"] is True and r["tools"][0]["name"] == "read_file"
    assert state.mcp_tests["good-srv"]["tools"] == 1 and state.mcp_tests["good-srv"]["names"] == ["read_file"]
    assert closed == [True]


def test_openapi_server_headers_and_duplicates(client, tmp_path):
    spec = tmp_path / "pets.json"
    spec.write_text(json.dumps({"openapi": "3.0.3", "info": {"title": "Pets", "version": "1"},
                                "servers": [{"url": "https://pets.example.test"}],
                                "paths": {"/pets": {"get": {"operationId": "listPets", "summary": "List pets"}}}}), encoding="utf-8")
    assert client.post("/api/mcp/openapi", json={"id": "pets-api", "label": "Pets", "spec": "http://remote.example.com/x.json"}).status_code == 422
    r = client.post("/api/mcp/openapi", json={"id": "pets-api", "label": "Pets", "spec": str(spec), "allow_write": True,
                                              "headers": {"X-Api-Key": "k-123", "Authorization": "Bearer t"}})
    assert r.status_code == 200 and r.json()["operations"] == 1 and "read & write" in r.json()["description"]
    server = next(m for m in client.get("/api/mcp").json() if m["id"] == "pets-api")
    assert server["env_set"]["HEADER_X_API_KEY"] is True and "k-123" not in json.dumps(server)
    assert client.post("/api/mcp/openapi", json={"id": "pets-api", "label": "Pets", "spec": str(spec)}).status_code == 422


# ------------------------------------------------------------------ settings and the side agent
def test_settings_get_and_put(client, tmp_path):
    base = client.get("/api/settings").json()
    assert "sidebot_resolved" in base and base["thinking_level"] in ("off", "low", "medium", "high")
    assert client.put("/api/settings", json={"workspace_dir": "relative/ws"}).status_code == 422
    assert client.put("/api/settings", json={"thinking_level": "ultra"}).status_code == 422
    assert client.put("/api/settings", json={"sidebot_provider": "nope"}).status_code == 422
    r = client.put("/api/settings", json={
        "workspace_dir": str(tmp_path / "ws"), "sandbox_network": True, "max_llm_calls_per_turn": 7,
        "agent_spawning_enabled": False, "max_spawns_per_turn": 2, "max_turn_minutes": 3,
        "max_external_calls_per_turn": 4, "external_timeout_min": 9, "auto_debug_mcp": True,
        "model_fallbacks": [{"provider": "mock", "model": "m1"}, {"provider": "", "model": "x"}, {"model": "orphan"}, {"provider": "ollama"}],
        "thinking_level": "high", "agents_can_call_external": False, "sidebot_enabled": False,
        "sidebot_provider": "", "sidebot_model": "  gpt-x  "})
    assert r.status_code == 200
    body = r.json()
    assert body["sandbox_network"] is True and body["max_llm_calls_per_turn"] == 7 and body["agent_spawning_enabled"] is False
    assert body["max_spawns_per_turn"] == 2 and body["max_turn_minutes"] == 3.0 and body["auto_debug_mcp"] is True
    assert body["model_fallbacks"] == [{"provider": "mock", "model": "m1"}, {"provider": "ollama", "model": None}]
    assert body["thinking_level"] == "high" and body["agents_can_call_external"] is False
    assert body["sidebot_enabled"] is False and body["sidebot_model"] == "gpt-x"
    assert body["max_external_calls_per_turn"] == 4 and body["external_timeout_min"] == 9.0


def _run_input(text="hi"):
    return {"thread_id": "t1", "run_id": "r1", "state": {}, "tools": [], "context": [], "forwarded_props": {},
            "messages": [{"id": "m1", "role": "user", "content": text}]}


def test_sidebot_endpoint_paths(client, state, monkeypatch):
    assert client.post("/api/sidebot", json=["not", "an", "object"]).status_code == 422
    assert client.post("/api/sidebot", json={"nope": 1}).status_code == 422
    client.put("/api/settings", json={"sidebot_enabled": False})
    assert client.post("/api/sidebot", json=_run_input()).status_code == 409
    client.put("/api/settings", json={"sidebot_enabled": True, "sidebot_provider": ""})
    client.put("/api/providers/mock", json={"enabled": False})
    assert client.post("/api/sidebot", json=_run_input()).status_code == 422             # no provider to use
    client.put("/api/providers/vertex_ai", json={"enabled": True, "vertex_project": ""})
    client.put("/api/settings", json={"sidebot_provider": "vertex_ai"})
    assert client.post("/api/sidebot", json=_run_input()).status_code == 422             # misconfigured provider
    client.put("/api/providers/vertex_ai", json={"enabled": False})
    client.put("/api/providers/mock", json={"enabled": True, "cap_usd": 1000})
    client.put("/api/settings", json={"sidebot_provider": "mock", "sidebot_model": ""})

    import litellm

    chunks = [
        {"choices": [{"delta": {"content": "Hello"}}]},
        {"choices": [{"delta": {"content": " there"}}]},
        {"choices": []},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "get_weather", "arguments": "{\"city\":"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"Paris\"}"}}]}}]},
    ]
    seen = {}

    async def fake_ok(**kw):
        seen.update(kw)

        async def gen():
            for c in chunks:
                yield c
        return gen()
    monkeypatch.setattr(litellm, "acompletion", fake_ok)
    r = client.post("/api/sidebot", json=_run_input("say hello"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = events_of(r.text)
    types = [e["type"] for e in evs]
    assert types[0] == "RUN_STARTED" and types[-1] == "RUN_FINISHED"
    assert "".join(e["delta"] for e in evs if e["type"] == "TEXT_MESSAGE_CONTENT") == "Hello there"
    assert any(e["type"] == "TOOL_CALL_START" and e["toolCallName"] == "get_weather" for e in evs)
    assert any(e["type"] == "TOOL_CALL_ARGS" and e["delta"] == '{"city":"Paris"}' for e in evs)
    assert seen["stream"] is True and seen["messages"][-1]["content"] == "say hello"

    async def broken(**kw):
        raise RuntimeError("network unreachable")
    monkeypatch.setattr(litellm, "acompletion", broken)
    evs = events_of(client.post("/api/sidebot", json=_run_input()).text)
    assert [e["type"] for e in evs] == ["RUN_STARTED", "RUN_ERROR"]


# ------------------------------------------------------------------ dashboard
def test_dashboard_counts_calls_and_refusals(client, state):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    assert client.post("/api/chat", json={"provider": "mock", "message": "count me"}).status_code == 200
    client.put("/api/providers/mock", json={"cap_usd": 0.0000001})
    assert client.post("/api/chat", json={"provider": "mock", "message": "x" * 400}).status_code == 402
    d = client.get("/api/dashboard", params={"days": 2}).json()
    assert len(d["daily"]) == 7                                       # clamped to the 7-day minimum
    assert d["kpis"]["month_calls"] >= 1 and d["kpis"]["month_refused"] >= 1 and d["kpis"]["turns_7d"] >= 1
    assert sum(x["calls"] for x in d["daily"]) >= 1
    assert any(p["provider"] == "mock" and p["calls"] >= 1 for p in d["providers"]) and d["recent"]
    assert {"providers_ready", "providers_enabled", "mcp_total", "agents", "skills"} <= set(d["health"])
    assert len(client.get("/api/dashboard", params={"days": 9999}).json()["daily"]) == 365


# ------------------------------------------------------------------ folder picking and folder listing
def test_folder_picker_and_listing(client, monkeypatch, tmp_path):
    (tmp_path / "child-folder").mkdir()
    monkeypatch.setattr(fsbrowse, "pick_folder", lambda *a, **k: str(tmp_path))
    assert client.post("/api/fs/pick-folder", json={}).json() == {"path": str(tmp_path), "cancelled": False}
    monkeypatch.setattr(fsbrowse, "pick_folder", lambda *a, **k: None)
    assert client.post("/api/fs/pick-folder", json={"initial": str(tmp_path)}).json() == {"path": None, "cancelled": True}

    def no_gui(*a, **k):
        raise RuntimeError("no display")
    monkeypatch.setattr(fsbrowse, "pick_folder", no_gui)
    assert client.post("/api/fs/pick-folder", json={}).status_code == 501

    root = client.get("/api/fs/dirs").json()
    assert root["path"] is None and any(d["name"] == "Home" for d in root["dirs"])
    listing = client.get("/api/fs/dirs", params={"path": str(tmp_path)}).json()
    assert any(d["name"] == "child-folder" for d in listing["dirs"])
    assert client.get("/api/fs/dirs", params={"path": "relative/folder"}).status_code == 422


# ------------------------------------------------------------------ voice (ElevenLabs, fully mocked)
@pytest.fixture
def eleven(monkeypatch):
    flags = {"down": False}

    def handler(req: httpx.Request) -> httpx.Response:
        if flags["down"]:
            raise httpx.ConnectError("unreachable", request=req)
        if req.headers.get("xi-api-key") != "el-key-123456789":
            return httpx.Response(401, json={"detail": {"message": "invalid api key"}})
        p = req.url.path
        if p == "/v1/voices":
            return httpx.Response(200, json={"voices": [{"voice_id": "v1", "name": "Rachel", "category": "premade"}]})
        if p == "/v1/models":
            return httpx.Response(200, json=[{"model_id": "eleven_flash_v2_5", "name": "Flash", "can_do_text_to_speech": True}])
        if p == "/v1/user/subscription":
            return httpx.Response(200, json={"tier": "creator", "character_count": 10, "character_limit": 100000})
        if p.startswith("/v1/text-to-speech/"):
            return httpx.Response(200, content=b"ID3voice", headers={"content-type": "audio/mpeg"})
        if p == "/v1/speech-to-text":
            return httpx.Response(200, json={"text": "hello world", "language_code": "en"})
        return httpx.Response(404, json={"detail": "nope"})

    real = httpx.AsyncClient

    def factory(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)
    monkeypatch.setattr(V.httpx, "AsyncClient", factory)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return flags


def test_voice_routes_success_and_upstream_errors(client, state, eleven):
    if state.mcp.get("elevenlabs") is None:
        state.mcp.add("elevenlabs", "ElevenLabs", "", "stdio", "npx", ["-y", "elevenlabs-mcp"], None, {}, enabled=False)
    assert client.put("/api/voice/settings", json={"api_key": "el-key-123456789", "voice_id": "v1"}).status_code == 200
    assert state.mcp.get("elevenlabs").env.get("ELEVENLABS_API_KEY") == "el-key-123456789"    # shared with the MCP server
    assert client.get("/api/voice/voices").json()[0]["name"] == "Rachel"
    assert client.get("/api/voice/models").json()[0]["model_id"] == "eleven_flash_v2_5"
    assert client.get("/api/voice/subscription").json()["tier"] == "creator"
    r = client.post("/api/voice/tts", json={"text": "Hello there", "voice_id": "v1"})
    assert r.status_code == 200 and r.content == b"ID3voice" and r.headers["X-Characters"] == str(len("Hello there"))
    assert r.headers["Cache-Control"] == "no-store"
    r = client.post("/api/voice/stt", files={"file": ("a.webm", b"RIFFfake", "audio/webm")})
    assert r.json() == {"text": "hello world", "language": "en"}
    eleven["down"] = True
    r = client.get("/api/voice/voices")
    assert r.status_code == 502 and "could not reach ElevenLabs" in r.json()["detail"]
    r = client.post("/api/voice/tts", json={"text": "again", "voice_id": "v1"})
    assert r.status_code == 502


# ------------------------------------------------------------------ OpenTelemetry (OTLP receiver)
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"


def _otlp_trace():
    return {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "ext-agent"}}]},
                               "scopeSpans": [{"spans": [{"traceId": TRACE_ID, "spanId": "b7ad6b7169203331", "name": "step",
                                                          "startTimeUnixNano": "1000000000", "endTimeUnixNano": "2000000000"}]}]}]}


def test_otel_settings_summary_and_records(client, monkeypatch):
    assert client.get("/api/otel/settings").json()["enabled"] is True
    assert client.put("/api/otel/settings", json={"retention_days": 9999}).status_code == 422
    assert client.put("/api/otel/settings", json={"forward_url": "ftp://collector.example"}).status_code == 422
    assert client.put("/api/otel/settings", json={"forward_headers": {"X-Team": "a"}, "retention_days": 7}).json()["retention_days"] == 7
    assert "summary" in str(client.get("/api/otel/summary").json()).lower() or client.get("/api/otel/summary").status_code == 200
    assert client.get("/api/otel/records", params={"signal": "bogus"}).status_code == 422
    assert client.get("/api/otel/records", params={"signal": "log"}).json() == []

    stored = client.post("/v1/traces", json=_otlp_trace()).json()
    assert stored["stored"] >= 1 and stored["partialSuccess"] == {}
    traces = client.get("/api/otel/traces").json()
    assert TRACE_ID in json.dumps(traces)
    spans = client.get(f"/api/otel/traces/{TRACE_ID}").json()
    assert spans and TRACE_ID in json.dumps(spans)
    assert client.get("/api/otel/traces/deadbeef").status_code == 404
    assert client.get("/api/otel/records", params={"signal": "trace"}).json()
    assert client.delete("/api/otel/records", params={"source": "nobody"}).json() == {"deleted": 0}
    assert client.delete("/api/otel/records").json()["deleted"] >= 1


def test_otlp_receiver_rejects_bad_bodies(client, monkeypatch):
    assert client.post("/v1/traces", content=b"[]", headers={"content-type": "application/json"}).status_code == 400
    monkeypatch.setattr("app.main.MAX_BODY", 10)
    assert client.post("/v1/metrics", content=b'{"resourceMetrics": []}', headers={"content-type": "application/json"}).status_code == 413


# ------------------------------------------------------------------ ACP external agents (routes)
class FakeAcpSession:
    def __init__(self, sid="s1"):
        self.sid = sid

    def public(self):
        return {"id": self.sid, "agent": "fake"}


def test_acp_agent_store_routes(client, state):
    assert isinstance(client.get("/api/acp/agents").json(), list)
    assert client.post("/api/acp/agents", json={"id": "Bad Id", "command": "tool"}).status_code == 422
    r = client.post("/api/acp/agents", json={"id": "my-agent", "label": "My agent", "command": "my-tool", "args": ["--x"], "notes": "n"})
    assert r.status_code == 200 and r.json()["id"] == "my-agent"
    r = client.put("/api/acp/agents/my-agent", json={"label": "Renamed", "max_turns": 5, "month_turn_cap": 3, "enabled": False})
    assert r.status_code == 200 and r.json()["label"] == "Renamed"
    assert client.put("/api/acp/agents/nope", json={"label": "x"}).status_code == 422
    assert client.delete("/api/acp/agents/nope").status_code == 422
    assert client.delete("/api/acp/agents/my-agent").json() == {"ok": True}
    builtin = next((a["id"] for a in client.get("/api/acp/agents").json() if a.get("builtin")), None)
    if builtin:
        assert client.delete(f"/api/acp/agents/{builtin}").status_code == 422
    assert isinstance(client.get("/api/acp/usage").json(), list)


def test_acp_probe_sessions_cancel_respond(client, state, monkeypatch):
    async def probe_ok(aid):
        return {"ok": True, "agent": aid}

    async def probe_fail(aid):
        raise AcpError("not installed")
    monkeypatch.setattr(state.acp, "probe", probe_ok)
    assert client.post("/api/acp/agents/x/probe").json() == {"ok": True, "agent": "x"}
    monkeypatch.setattr(state.acp, "probe", probe_fail)
    assert client.post("/api/acp/agents/x/probe").json() == {"ok": False, "error": "not installed"}

    state.acp.sessions["s1"] = FakeAcpSession("s1")
    assert client.get("/api/acp/sessions").json() == [{"id": "s1", "agent": "fake"}]
    closed, cancelled = [], []

    async def close(sid):
        closed.append(sid)
        return True

    async def cancel(sess):
        cancelled.append(sess.sid)
    monkeypatch.setattr(state.acp, "close", close)
    monkeypatch.setattr(state.acp, "cancel", cancel)
    assert client.delete("/api/acp/sessions/s1").json() == {"closed": True} and closed == ["s1"]
    assert client.post("/api/acp/sessions/nope/cancel").status_code == 404
    assert client.post("/api/acp/sessions/s1/cancel").json() == {"ok": True} and cancelled == ["s1"]

    def respond_missing(rid, body):
        raise AcpError("no such pending request")

    def respond_bad(rid, body):
        raise AcpError("answer must be yes or no")
    monkeypatch.setattr(state.acp, "respond", respond_missing)
    assert client.post("/api/acp/respond/r1", json={"ok": True}).status_code == 404
    monkeypatch.setattr(state.acp, "respond", respond_bad)
    assert client.post("/api/acp/respond/r1", json={"ok": True}).status_code == 422
    monkeypatch.setattr(state.acp, "respond", lambda rid, body: {"resolved": rid, "with": body})
    assert client.post("/api/acp/respond/r1", json={"ok": True}).json() == {"resolved": "r1", "with": {"ok": True}}


def test_acp_chat_stream_events(client, state, monkeypatch):
    modes, calls = [], []

    async def get_or_open(agent, sid, ws):
        calls.append(agent)
        return FakeAcpSession("s-acp")

    async def set_mode(sess, mode):
        modes.append(mode)

    async def prompt_ok(sess, text, emit, policy="ask", timeout=900):
        emit({"type": "text", "delta": "hi from agent"})
        return {"stop_reason": "end_turn"}
    monkeypatch.setattr(state.acp, "get_or_open", get_or_open)
    monkeypatch.setattr(state.acp, "set_mode", set_mode)
    monkeypatch.setattr(state.acp, "prompt", prompt_ok)
    evs = events_of(client.post("/api/acp/chat", json={"agent": "fake", "message": "hello", "mode": "plan"}).text)
    assert [e["type"] for e in evs] == ["session", "text", "done"]
    assert evs[0]["id"] == "s-acp" and evs[1]["delta"] == "hi from agent" and evs[2]["stop_reason"] == "end_turn"
    assert modes == ["plan"] and calls == ["fake"]

    async def prompt_acp_error(sess, text, emit, policy="ask", timeout=900):
        raise AcpError("agent refused the turn")
    monkeypatch.setattr(state.acp, "prompt", prompt_acp_error)
    evs = events_of(client.post("/api/acp/chat", json={"agent": "fake", "message": "hello"}).text)
    assert evs[-1] == {"type": "error", "message": "agent refused the turn"}

    async def prompt_crash(sess, text, emit, policy="ask", timeout=900):
        raise RuntimeError("pipe broke")
    monkeypatch.setattr(state.acp, "prompt", prompt_crash)
    evs = events_of(client.post("/api/acp/chat", json={"agent": "fake", "message": "hello"}).text)
    assert evs[-1] == {"type": "error", "message": "RuntimeError: pipe broke"}

    async def open_fails(agent, sid, ws):
        raise AcpError("agent binary not found")
    monkeypatch.setattr(state.acp, "get_or_open", open_fails)
    evs = events_of(client.post("/api/acp/chat", json={"agent": "fake", "message": "hello"}).text)
    assert evs == [{"type": "error", "message": "agent binary not found"}]


def test_acp_chat_keepalive_and_client_disconnect(state, client, monkeypatch):
    """Drive the endpoint generator directly: one idle keepalive tick, then the client goes away mid-turn."""
    cancelled = []

    async def get_or_open(agent, sid, ws):
        return FakeAcpSession("s-live")

    async def prompt_forever(sess, text, emit, policy="ask", timeout=900):
        await asyncio.Event().wait()                    # never finishes: the turn is still running
        return {}

    async def cancel(sess):
        cancelled.append(sess.sid)
    monkeypatch.setattr(state.acp, "get_or_open", get_or_open)
    monkeypatch.setattr(state.acp, "prompt", prompt_forever)
    monkeypatch.setattr(state.acp, "cancel", cancel)

    real_wait_for = asyncio.wait_for
    timed_out = []

    async def wait_for_once(aw, timeout):
        # first idle wait on the event queue times out at once (stands in for 15 s of silence)
        if not timed_out and getattr(aw, "__qualname__", "") == "Queue.get":
            timed_out.append(True)
            aw.close()
            raise asyncio.TimeoutError()
        return await real_wait_for(aw, timeout)
    monkeypatch.setattr(asyncio, "wait_for", wait_for_once)

    endpoint = next(r.endpoint for r in client.app.routes if getattr(r, "path", "") == "/api/acp/chat")

    async def run():
        resp = await endpoint(AcpChatIn(agent="fake", message="hello"), None, state)
        it = resp.body_iterator
        keepalive = await it.__anext__()
        session = await it.__anext__()
        await it.aclose()                               # the browser closed the stream
        return keepalive, session
    keepalive, session = asyncio.run(run())
    assert keepalive == ": keepalive\n\n"
    assert json.loads(session[len("data: "):]) == {"type": "session", "id": "s-live", "agent": "fake"}
    assert cancelled == ["s-live"]


def test_sandbox_status_route(client, monkeypatch):
    monkeypatch.setattr(sandbox_mod, "status", lambda: {"docker": True, "image": False, "detail": "image missing"})
    assert client.get("/api/sandbox/status").json() == {"docker": True, "image": False, "detail": "image missing"}


# ------------------------------------------------------------------ remaining branches
def test_memory_summary_route(client):
    MS._write([{"name": "Ada", "entityType": "person", "observations": ["x"]}], [])
    assert isinstance(client.get("/api/memory").json(), dict)


def test_mcp_duplicate_browser_settings_and_openapi_failures(client, tmp_path):
    client.post("/api/mcp", json={"id": "dup-srv", "command": "npx", "args": []})
    assert client.post("/api/mcp", json={"id": "dup-srv", "command": "npx", "args": []}).status_code == 422
    ok = client.put("/api/browser/settings", json={"browser": "chromium", "headless": True, "isolated": True, "viewport": "1280x720",
                                                   "allowed_origins": [], "blocked_origins": []})
    assert ok.status_code == 200 and ok.json()["viewport"] == "1280x720"
    assert client.get("/api/browser/settings").json()["viewport"] == "1280x720"
    assert client.put("/api/browser/settings", json={"viewport": "huge"}).status_code == 422
    assert client.post("/api/mcp/openapi", json={"id": "rel-api", "label": "R", "spec": "relative/spec.json"}).status_code == 422
    junk = tmp_path / "junk-spec.json"
    junk.write_text('{"hello": 1}', encoding="utf-8")
    r = client.post("/api/mcp/openapi", json={"id": "junk-spec", "label": "J", "spec": str(junk)})
    assert r.status_code == 422 and "could not load that OpenAPI document" in r.json()["detail"]


def test_skill_create_invalid_and_delete_user_skill(client):
    body = {"name": "tmp-skill-x", "description": "a temporary skill", "body": "## Steps\n1. do it"}
    assert client.post("/api/skills", json={**body, "name": "Bad Name"}).status_code == 422
    assert client.post("/api/skills", json=body).status_code == 200
    assert client.delete("/api/skills/tmp-skill-x").json() == {"ok": True}
    assert client.get("/api/skills/tmp-skill-x").status_code == 404


def test_voice_settings_and_unconfigured_error(client, eleven):
    assert client.get("/api/voice/settings").json()["configured"] is False
    r = client.get("/api/voice/voices")
    assert r.status_code == 412 and "ElevenLabs is not set up" in r.json()["detail"]


def test_otlp_receiver_routing_and_content_rules(client, state):
    assert client.post("/v1/bogus", json={}).status_code == 404
    assert client.post("/v1/traces", content=b"x", headers={"content-type": "text/plain"}).status_code == 415
    remote = TestClient(client.app, client=("203.0.113.9", 5555))
    assert remote.post("/v1/traces", json=_otlp_trace()).status_code == 403
    assert state.tel.summary()["total"] == 0                       # refused before anything was stored
