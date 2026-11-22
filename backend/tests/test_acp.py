"""ACP client: run a scripted fake agent over real stdio JSON-RPC (no model, no network, no spend).
Covers streaming, permission flow, path-jailed fs delegation, Cursor extensions, auth retry, cancel,
crash handling, env hygiene, policies, turn limits, usage ledger, and the HTTP/SSE API."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.acp_agents import AcpError
from app.db import Database
from app.main import State, create_app

FAKE = str(Path(__file__).parent / "fake_acp_agent.py")


@pytest.fixture
def st(tmp_path):
    s = State(Database(tmp_path / "t.db"))
    s.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    s.acp.store.create("fake", "Fake", sys.executable, [FAKE])
    return s


def run(coro):
    return asyncio.run(coro)


async def turn(st, sess, text, answer=None, policy="ask"):
    """Run one prompt, collecting events; `answer(ev)` may respond to interactive events."""
    events = []

    def emit(ev):
        events.append(ev)
        if answer and ev["type"] in ("permission", "question", "plan_approval"):
            asyncio.get_running_loop().call_soon(lambda: answer(ev))

    res = await st.acp.prompt(sess, text, emit, policy=policy)
    return events, res


def texts(events):
    return "".join(e["text"] for e in events if e["type"] == "text")


def test_stream_usage_and_ledger(st):
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            assert s.info["title"] == "Fake Agent" and s.modes["current"] == "default"
            ev, res = await turn(st, s, "hello world")
            assert texts(ev) == "echo: hello world"
            assert res["stop_reason"] == "end_turn" and res["usage"]["total_tokens"] == 30
            assert res["reported_cost"] == pytest.approx(0.0042) and res["context"]["used"] == 120
            ev2, res2 = await turn(st, s, "again")           # same live session, same process
            assert res2["turns"] == 2
        finally:
            await st.acp.close_all()
    run(main())
    u = st.acp.store.usage()[0]
    assert u["agent"] == "fake" and u["turns"] == 2 and u["total_tokens"] == 60 and u["capped"] is False


def test_permission_allow_writes_inside_workspace(st, tmp_path):
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            ans = lambda ev: st.acp.respond(ev["request_id"], {"option_id": "allow-once"})
            ev, _ = await turn(st, s, "!write notes/a.txt|hello", answer=ans)
            assert any(e["type"] == "permission" and e["kind"] == "edit" for e in ev)
            assert "wrote notes/a.txt" in texts(ev)
            assert any(e["type"] == "fs_write" for e in ev)
        finally:
            await st.acp.close_all()
    run(main())
    assert (tmp_path / "ws" / "notes" / "a.txt").read_text() == "hello"


def test_permission_reject_writes_nothing(st, tmp_path):
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            ev, _ = await turn(st, s, "!write b.txt|nope", answer=lambda e: st.acp.respond(e["request_id"], {"option_id": "reject-once"}))
            assert "write-denied" in texts(ev)
        finally:
            await st.acp.close_all()
    run(main())
    assert not (tmp_path / "ws" / "b.txt").exists()


def test_fs_delegation_is_path_jailed(st, tmp_path):
    (tmp_path / "secret.txt").write_text("outside")
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            allow = lambda e: st.acp.respond(e["request_id"], {"option_id": "allow-once"})
            ev, _ = await turn(st, s, "!write ../escape.txt|x", answer=allow)
            assert "escapes the workspace" in texts(ev)
            ev, _ = await turn(st, s, "!read ../secret.txt")
            assert "escapes the workspace" in texts(ev) and "outside" not in texts(ev)
            ev, _ = await turn(st, s, "!write .git/config|x", answer=allow)
            assert ".git" in texts(ev)
        finally:
            await st.acp.close_all()
    run(main())
    assert not (tmp_path / "escape.txt").exists()


def test_read_through_client(st, tmp_path):
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "r.txt").write_text("line1\nline2\n")
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            ev, _ = await turn(st, s, "!read r.txt")
            assert "content:line1\nline2" in texts(ev)
        finally:
            await st.acp.close_all()
    run(main())


def test_policies(st, tmp_path):
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            # readonly: read-kind auto-allowed, everything else auto-rejected, never reaches the UI
            ev, _ = await turn(st, s, "!perm read|look", policy="readonly")
            assert "perm:allowed" in texts(ev) and not any(e["type"] == "permission" for e in ev)
            ev, _ = await turn(st, s, "!perm execute|rm -rf", policy="readonly")
            assert "perm:denied" in texts(ev) and not any(e["type"] == "permission" for e in ev)
            ev, _ = await turn(st, s, "!write w.txt|x", policy="readonly")
            assert "write-denied" in texts(ev)
            # reads_auto: reads skip the prompt, execute still asks
            ev, _ = await turn(st, s, "!perm search|grep", policy="reads_auto")
            assert "perm:allowed" in texts(ev) and not any(e["type"] == "permission" for e in ev)
            ev, _ = await turn(st, s, "!perm execute|ls", policy="reads_auto",
                               answer=lambda e: st.acp.respond(e["request_id"], {"option_id": "allow-once"}))
            assert any(e["type"] == "permission" for e in ev) and "perm:allowed" in texts(ev)
            # fetch (network) is never auto-allowed
            ev, _ = await turn(st, s, "!perm fetch|curl", policy="reads_auto",
                               answer=lambda e: st.acp.respond(e["request_id"], {"option_id": "reject-once"}))
            assert any(e["type"] == "permission" for e in ev) and "perm:denied" in texts(ev)
        finally:
            await st.acp.close_all()
    run(main())
    assert not (tmp_path / "ws" / "w.txt").exists()


def test_cancelled_by_user_counts_as_denied(st):
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            ev, _ = await turn(st, s, "!perm execute|x", answer=lambda e: st.acp.respond(e["request_id"], {"cancel": True}))
            assert "perm:denied:cancelled" in texts(ev)
        finally:
            await st.acp.close_all()
    run(main())


def test_cursor_extensions_question_and_plan(st):
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            ev, _ = await turn(st, s, "!ask", answer=lambda e: st.acp.respond(e["request_id"], {"answers": [{"questionId": "q1", "selectedOptionIds": ["b"]}]}))
            assert "answered" in texts(ev) and "'b'" in texts(ev)
            ev, _ = await turn(st, s, "!plan", answer=lambda e: st.acp.respond(e["request_id"], {"accepted": False, "reason": "no"}))
            assert "rejected" in texts(ev) and any(e["type"] == "plan_approval" for e in ev)
        finally:
            await st.acp.close_all()
    run(main())


def test_env_hygiene_secrets_not_inherited(st, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak-check")
    monkeypatch.setenv("AS_PROVIDER_SECRET", "x")
    st.acp.store.update("fake", {"env": {"CUSTOM_KEY": "abc"}})
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            for name, want in (("OPENAI_API_KEY", "absent"), ("AS_PROVIDER_SECRET", "absent"), ("CUSTOM_KEY", "present")):
                ev, _ = await turn(st, s, f"!env {name}")
                assert texts(ev) == f"env:{name}={want}"
        finally:
            await st.acp.close_all()
    run(main())
    assert "abc" not in json.dumps(st.acp.store.get("fake"))     # stored secrets never leave the store


def test_auth_required_then_authenticate_retry(st, monkeypatch):
    monkeypatch.setenv("FAKE_AUTH", "1")
    st.acp.store.update("fake", {"env": {"FAKE_AUTH": "1"}})
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            assert s.info["auth_methods"][0]["id"] == "fake_login"
            ev, _ = await turn(st, s, "hi")
            assert texts(ev) == "echo: hi"
            ev, _ = await turn(st, s, "!authmethod")
            assert texts(ev) == "auth:fake_login"            # no key stored -> the agent's login method
        finally:
            await st.acp.close_all()
    run(main())


def test_stored_api_key_selects_the_agents_api_key_sign_in(st):
    st.acp.store.update("fake", {"env": {"FAKE_AUTH": "1", "GEMINI_API_KEY": "k-123"}})
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            ev, _ = await turn(st, s, "!authmethod")
            assert texts(ev) == "auth:gemini-api-key"
        finally:
            await st.acp.close_all()
    run(main())
    assert "k-123" not in json.dumps(st.acp.store.get("fake"))


def test_cancel_running_turn(st):
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            task = asyncio.create_task(turn(st, s, "!slow"))
            await asyncio.sleep(1.0)
            await st.acp.cancel(s)
            ev, res = await asyncio.wait_for(task, 10)
            assert res["stop_reason"] == "cancelled"
        finally:
            await st.acp.close_all()
    run(main())


def test_busy_session_rejects_second_prompt(st):
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            task = asyncio.create_task(turn(st, s, "!slow"))
            await asyncio.sleep(0.8)
            with pytest.raises(AcpError, match="busy"):
                await st.acp.prompt(s, "x", lambda e: None)
            await st.acp.cancel(s)
            await asyncio.wait_for(task, 10)
        finally:
            await st.acp.close_all()
    run(main())


def test_agent_crash_is_reported_and_session_dropped(st):
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            with pytest.raises(Exception):
                await turn(st, s, "!die")
            await asyncio.sleep(0.5)
            with pytest.raises(AcpError, match="exited"):
                await st.acp.get_or_open("fake", s.id, None)
            assert s.id not in st.acp.sessions
        finally:
            await st.acp.close_all()
    run(main())


def test_turn_limit_per_session(st):
    st.acp.store.update("fake", {"max_turns": 2})
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            await turn(st, s, "1"); await turn(st, s, "2")
            with pytest.raises(AcpError, match="turn limit"):
                await turn(st, s, "3")
        finally:
            await st.acp.close_all()
    run(main())


def test_mode_switch_and_unknown_session(st):
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            await st.acp.set_mode(s, "plan")
            assert s.modes["current"] == "plan"
            with pytest.raises(AcpError):
                await st.acp.set_mode(s, "yolo")
            with pytest.raises(AcpError, match="unknown or expired"):
                await st.acp.get_or_open("fake", "nope", None)
        finally:
            await st.acp.close_all()
    run(main())


def test_not_installed_and_disabled_give_clear_errors(st):
    st.acp.store.update("cursor", {"command": "definitely-not-installed-xyz"})
    async def main():
        with pytest.raises(AcpError, match="not found on PATH"):
            await st.acp.get_or_open("cursor", None, None)
        st.acp.store.update("fake", {"enabled": False})
        with pytest.raises(AcpError, match="disabled"):
            await st.acp.get_or_open("fake", None, None)
    run(main())


def test_probe_reports_and_cleans_up(st):
    r = run(st.acp.probe("fake"))
    assert r["ok"] and r["title"] == "Fake Agent" and r["modes"]["available"][0]["id"] == "default"
    assert st.acp.sessions == {}


def test_store_rules(st):
    s = st.acp.store
    with pytest.raises(AcpError):
        s.delete("claude")                                    # built-ins can be disabled, not deleted
    with pytest.raises(AcpError):
        s.create("Bad Id!", "x", "cmd", [])
    s.delete("fake")
    assert {a["id"] for a in s.list()} >= {"claude", "cursor", "antigravity", "grok"}
    claude = s.get("claude")
    assert claude["command"] == "npx" and "claude-agent-acp@" in " ".join(claude["args"])


# ------------------------------------------------------------------ HTTP / SSE API
def parse_sse(text):
    return [json.loads(l[6:]) for l in text.splitlines() if l.startswith("data: ")]


def test_api_end_to_end(st, tmp_path):
    app = create_app(st)
    with TestClient(app) as c:                                 # one event loop for the whole test (live subprocess)
        lst = c.get("/api/acp/agents").json()
        assert {a["id"] for a in lst} >= {"fake", "claude", "cursor", "antigravity", "grok"}
        assert "env" not in lst[0]
        assert c.post("/api/acp/agents/fake/probe").json()["ok"] is True

        r = c.post("/api/acp/chat", json={"agent": "fake", "message": "ping"})
        evs = parse_sse(r.text)
        assert evs[0]["type"] == "session" and evs[-1]["type"] == "done"
        assert "".join(e["text"] for e in evs if e["type"] == "text") == "echo: ping"
        sid = evs[0]["session_id"]
        assert evs[-1]["stop_reason"] == "end_turn" and "not covered" in evs[-1]["billing_note"]

        # continue the same session; readonly policy auto-rejects an execute request
        r = c.post("/api/acp/chat", json={"agent": "fake", "session_id": sid, "message": "!perm execute|danger", "policy": "readonly"})
        evs = parse_sse(r.text)
        assert any(e["type"] == "auto" and e["decision"] == "rejected" for e in evs)

        # unknown session -> error event, not a crash
        evs = parse_sse(c.post("/api/acp/chat", json={"agent": "fake", "session_id": "zzz", "message": "x"}).text)
        assert evs[-1]["type"] == "error" and "expired" in evs[-1]["message"]

        assert any(s["session_id"] == sid for s in c.get("/api/acp/sessions").json())
        assert c.delete(f"/api/acp/sessions/{sid}").json()["closed"] is True
        u = c.get("/api/acp/usage").json()
        assert u[0]["agent"] == "fake" and u[0]["capped"] is False
        assert c.post("/api/acp/respond/nope", json={"cancel": True}).status_code == 404
    assert st.acp.sessions == {}                               # shutdown closed everything
