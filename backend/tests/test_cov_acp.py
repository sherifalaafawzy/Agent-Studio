"""Coverage for app/acp_agents.py: the branches tests/test_acp.py does not reach.

Three ways of driving the code, none of which launches a real agent CLI:
  * the scripted stdio agent (tests/fake_acp_agent.py) where the real protocol matters;
  * a fake SDK connection (acp.spawn_agent_process replaced) where one specific error must be injected;
  * direct calls on _Client / AcpManager / AcpStore with plain objects where the path is pure bookkeeping.
"""
import asyncio
import contextlib
import os
import sys
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import acp
import pytest
from acp import schema as S

from app import acp_agents
from app.acp_agents import AcpError, AcpManager, AcpSession, Pending, _Client, _content_text, _noop, _refresh_path
from app.db import Database
from app.main import State

FAKE = str(Path(__file__).parent / "fake_acp_agent.py")


@pytest.fixture
def st(tmp_path):
    s = State(Database(tmp_path / "t.db"))
    s.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    s.acp.store.create("fake", "Fake", sys.executable, [FAKE])
    return s


# ------------------------------------------------------------------ helpers
def make_session(st, ws: Path, sid: str = "s1") -> AcpSession:
    ws.mkdir(parents=True, exist_ok=True)
    sess = AcpSession(sid, st.acp.store.get("fake"), ws)
    sess.events = []
    sess.emit = sess.events.append
    return sess


def add_usage(st, *, cost=None, duration_s=0.0, agent="fake"):
    st.db.execute("INSERT INTO acp_usage(ts,agent,session,stop_reason,input_tokens,output_tokens,total_tokens,cost,cost_currency,duration_s) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (time.time(), agent, "x", "end_turn", None, None, None, cost, "USD" if cost is not None else None, duration_s))


async def turn(st, sess, text, answer=None, policy="ask"):
    """Run one prompt on the live stdio agent, collecting events; `answer(ev)` may respond to interactive events."""
    events = []

    def emit(ev):
        events.append(ev)
        if answer and ev["type"] in ("permission", "question", "plan_approval"):
            asyncio.get_running_loop().call_soon(lambda: answer(ev))

    res = await st.acp.prompt(sess, text, emit, policy=policy)
    return events, res


def texts(events):
    return "".join(e["text"] for e in events if e["type"] == "text")


class FakeProc:
    """Stands in for asyncio's subprocess handle. pid=None keeps the Windows taskkill branch out of the way."""

    def __init__(self, stderr=None):
        self.pid = None
        self.returncode = None
        self.stderr = stderr

    async def wait(self):
        return self.returncode if self.returncode is not None else 0


class FakeConn:
    """Stands in for the SDK's client-side connection. Each failure mode is switched on per test."""

    def __init__(self):
        self.auth_methods = []
        self.init_error = None
        self.new_errors = []          # raised by successive new_session calls, in order
        self.auth_error = None
        self.prompt_exc = None
        self.prompt_hang = False
        self.stderr_text = ""
        self.calls = []

    async def initialize(self, protocol_version, client_capabilities=None, client_info=None, **kw):
        if self.init_error is not None:
            await asyncio.sleep(0.05)          # lets the stderr drain task run before the failure surfaces
            raise self.init_error
        return S.InitializeResponse(protocol_version=protocol_version, auth_methods=self.auth_methods,
                                    agent_info=S.Implementation(name="fake", title="Fake Agent", version="1.0"))

    async def new_session(self, cwd, mcp_servers=None, **kw):
        if self.new_errors:
            raise self.new_errors.pop(0)
        return S.NewSessionResponse(session_id="acp-1")

    async def authenticate(self, method_id, **kw):
        self.calls.append(("authenticate", method_id))
        if self.auth_error is not None:
            raise self.auth_error
        return S.AuthenticateResponse()

    async def cancel(self, session_id, **kw):
        self.calls.append(("cancel", session_id))

    async def prompt(self, session_id, prompt, **kw):
        if self.prompt_exc is not None:
            raise self.prompt_exc
        if self.prompt_hang:
            await asyncio.sleep(3600)
        return S.PromptResponse(stop_reason="end_turn")


def use_fake_conn(monkeypatch, conn: FakeConn) -> None:
    @contextlib.asynccontextmanager
    async def spawn(client, command, *args, **kw):
        reader = asyncio.StreamReader()
        reader.feed_data(conn.stderr_text.encode())
        reader.feed_eof()
        yield conn, FakeProc(stderr=reader)

    monkeypatch.setattr(acp, "spawn_agent_process", spawn)


# ------------------------------------------------------------------ module helpers
def test_noop_sink_ignores_events():
    assert _noop({"type": "text", "text": "x"}) is None


def test_content_text_handles_every_block_shape():
    blocks = [
        SimpleNamespace(type="content", content=SimpleNamespace(type="text", text="hi")),
        SimpleNamespace(type="diff", path="a.py"),
        SimpleNamespace(type="terminal", terminal_id="t1"),
        SimpleNamespace(type="image"),
        SimpleNamespace(type="text", text=""),
    ]
    assert _content_text(None) == ""
    assert _content_text(blocks) == "hi\nedit a.py\nterminal t1\n[image]"
    assert _content_text(SimpleNamespace()) == ""
    assert _content_text(SimpleNamespace(type="text", text="plain")) == "plain"


def test_refresh_path_is_a_noop_off_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setenv("PATH", "/usr/bin")
    _refresh_path()
    assert os.environ["PATH"] == "/usr/bin"


def test_refresh_path_adds_installer_entries_from_registry(monkeypatch, tmp_path):
    winreg = pytest.importorskip("winreg")
    new_dir = str(tmp_path / "freshly-installed-bin")
    monkeypatch.setenv("PATH", r"C:\Windows")
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (new_dir, winreg.REG_SZ))
    _refresh_path()
    assert new_dir in os.environ["PATH"].split(";")


def test_refresh_path_survives_registry_failure(monkeypatch):
    winreg = pytest.importorskip("winreg")

    def broken(key, name):
        raise RuntimeError("registry unavailable")

    monkeypatch.setenv("PATH", r"C:\Windows")
    monkeypatch.setattr(winreg, "QueryValueEx", broken)
    _refresh_path()                                   # must not raise
    assert os.environ["PATH"] == r"C:\Windows"


# ------------------------------------------------------------------ configuration store
def test_store_lookup_and_create_rules(st):
    s = st.acp.store
    with pytest.raises(AcpError, match="unknown external agent 'nope'"):
        s.get("nope")
    with pytest.raises(AcpError, match="already exists"):
        s.create("fake", "Again", "cmd", [])
    with pytest.raises(AcpError, match="command is required"):
        s.create("blank-cmd", "Blank", "   ", [])
    made = s.create("tool-2", "", "  mytool  ", ["--x"])
    assert made["label"] == "tool-2"                  # empty label falls back to the id
    assert made["command"] == "mytool" and made["args"] == ["--x"] and made["builtin"] is False


def test_store_update_validation_and_env_removal(st):
    s = st.acp.store
    with pytest.raises(AcpError, match="unknown external agent"):
        s.update("ghost", {"label": "x"})
    with pytest.raises(AcpError, match="command cannot be empty"):
        s.update("fake", {"command": "  "})
    with pytest.raises(AcpError, match="month_cost_cap must be >= 0"):
        s.update("fake", {"month_cost_cap": -1})
    with pytest.raises(AcpError, match="month_minutes_cap must be >= 0"):
        s.update("fake", {"month_minutes_cap": -0.5})
    out = s.update("fake", {"args": [1, "two"], "month_turn_cap": 7.9, "month_cost_cap": "2.5",
                            "max_turns": 5000, "env": {"TOKEN_X": "abc"}})
    assert out["args"] == ["1", "two"]
    assert out["month_turn_cap"] == 7 and out["month_cost_cap"] == 2.5
    assert out["max_turns"] == 1000                   # clamped to the ceiling
    assert s.env_values("fake") == {"TOKEN_X": "abc"}
    s.update("fake", {"env": {"TOKEN_X": ""}})        # an empty value removes the key
    assert s.env_values("fake") == {}


def test_store_delete_unknown_agent(st):
    with pytest.raises(AcpError, match="unknown external agent"):
        st.acp.store.delete("ghost")


def test_check_limits_turn_and_minute_caps(st):
    s = st.acp.store
    s.update("fake", {"month_turn_cap": 2})
    s.check_limits("fake")                            # nothing used yet
    add_usage(st)
    s.check_limits("fake")                            # 1 of 2 is still allowed
    add_usage(st)
    with pytest.raises(AcpError, match=r"monthly turn limit reached \(2/2\)"):
        s.check_limits("fake")

    st.db.execute("DELETE FROM acp_usage")
    s.update("fake", {"month_turn_cap": 0, "month_minutes_cap": 1})
    add_usage(st, duration_s=120)                     # two minutes used against a one-minute cap
    with pytest.raises(AcpError, match=r"monthly time limit reached \(2/1 min\)"):
        s.check_limits("fake")


def test_check_limits_cost_cap_needs_reported_cost(st):
    s = st.acp.store
    s.update("fake", {"month_cost_cap": 5})
    add_usage(st, cost=None)                          # the agent ran but never reported a cost
    with pytest.raises(AcpError, match="doesn't report cost"):
        s.check_limits("fake")

    st.db.execute("DELETE FROM acp_usage")
    add_usage(st, cost=4.0)
    s.check_limits("fake")                            # reported 4.00 of 5.00: allowed
    add_usage(st, cost=2.0)
    with pytest.raises(AcpError, match=r"reported \$6\.00 of \$5\.00"):
        s.check_limits("fake")


# ------------------------------------------------------------------ _Client: streaming updates
def test_session_update_thought_plan_and_mode_events(st, tmp_path):
    sess = make_session(st, tmp_path / "ws")
    client = _Client(st.acp, sess)

    async def main():
        await client.session_update("acp-1", SimpleNamespace(
            session_update="agent_thought_chunk", content=SimpleNamespace(type="text", text="pondering")))
        await client.session_update("acp-1", SimpleNamespace(session_update="plan", entries=[
            SimpleNamespace(content="read code", status="completed", priority="high"),
            SimpleNamespace(content="write tests", status="pending")]))
        await client.session_update("acp-1", SimpleNamespace(session_update="current_mode_update", current_mode_id="plan"))
        modes_without_state = sess.modes
        sess.modes = {"current": "default", "available": []}
        await client.session_update("acp-1", SimpleNamespace(session_update="current_mode_update", current_mode_id="edit"))
        return modes_without_state

    modes_without_state = asyncio.run(main())
    assert sess.events[0] == {"type": "thought", "text": "pondering"}
    assert sess.events[1] == {"type": "plan", "entries": [
        {"content": "read code", "status": "completed", "priority": "high"},
        {"content": "write tests", "status": "pending", "priority": None}]}
    assert sess.events[2] == {"type": "mode", "mode": "plan"} and modes_without_state is None   # no mode state -> nothing to update
    assert sess.events[3] == {"type": "mode", "mode": "edit"} and sess.modes["current"] == "edit"


# ------------------------------------------------------------------ _Client: permissions
def test_readonly_permission_without_matching_options(st, tmp_path):
    sess = make_session(st, tmp_path / "ws")
    sess.policy = "readonly"
    client = _Client(st.acp, sess)

    async def main():
        # a read with no allow option and no reject option on offer -> denied outright
        denied = await client.request_permission("acp-1", SimpleNamespace(kind="read", title="look"), [])
        # an edit when the agent offers only a reject option -> that option is the answer it gets
        reject = SimpleNamespace(option_id="no-thanks", name="No", kind="reject_once")
        rejected = await client.request_permission("acp-1", SimpleNamespace(kind="edit", title="edit x"), [reject])
        return denied, rejected

    denied, rejected = asyncio.run(main())
    assert isinstance(denied.outcome, S.DeniedOutcome)
    assert rejected.outcome.option_id == "no-thanks"
    assert [e["decision"] for e in sess.events] == ["rejected", "rejected"]


def test_unanswered_interaction_times_out_as_denied(st, tmp_path, monkeypatch):
    monkeypatch.setattr(acp_agents, "INTERACT_TIMEOUT_S", 0.05)
    sess = make_session(st, tmp_path / "ws")

    async def main():
        return await st.acp._wait("rid-timeout", "permission", sess, ["allow-once"],
                                  {"type": "permission", "request_id": "rid-timeout"})

    assert asyncio.run(main()) is None                # timed out = denied
    assert sess.events == [{"type": "permission", "request_id": "rid-timeout"}]
    assert "rid-timeout" not in st.acp.pending        # the pending entry is always cleaned up


def test_respond_validates_and_needs_a_running_runtime(st):
    async def main():
        fut = asyncio.get_running_loop().create_future()
        st.acp.pending["p-perm"] = Pending("p-perm", "permission", "s1", fut, ["allow-once"])
        with pytest.raises(AcpError, match="option_id must be one of"):
            st.acp.respond("p-perm", {"option_id": "nope"})
        # the ACP runtime loop was never started here, so a valid answer cannot be delivered
        with pytest.raises(AcpError, match="not running"):
            st.acp.respond("p-perm", {"option_id": "allow-once"})
        with pytest.raises(AcpError, match="no such pending"):
            st.acp.respond("gone", {"cancel": True})

    asyncio.run(main())


def test_cancel_pending_only_touches_that_session(st):
    async def main():
        loop = asyncio.get_running_loop()
        mine, other = loop.create_future(), loop.create_future()
        st.acp.pending["a"] = Pending("a", "question", "s1", mine)
        st.acp.pending["b"] = Pending("b", "plan", "s2", other)
        st.acp._cancel_pending("s1")
        assert mine.done() and mine.result() is None
        assert not other.done()
        other.cancel()

    asyncio.run(main())


# ------------------------------------------------------------------ _Client: file delegation
def test_readonly_run_refuses_writes_and_missing_files_are_not_found(st, tmp_path):
    ws = tmp_path / "ws"
    sess = make_session(st, ws)
    client = _Client(st.acp, sess)
    (ws / "keep.txt").write_text("original")

    async def main():
        sess.policy = "readonly"
        with pytest.raises(acp.RequestError) as refused:
            await client.write_text_file("acp-1", "keep.txt", "changed")
        assert refused.value.code == -32602
        sess.policy = "ask"
        with pytest.raises(acp.RequestError) as missing:
            await client.read_text_file("acp-1", "missing.txt")
        assert missing.value.code == -32002

    asyncio.run(main())
    assert (ws / "keep.txt").read_text() == "original"
    assert sess.events == []                          # a refused write emits nothing


def test_fs_read_honours_line_and_limit(st, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "n.txt").write_text("a\nb\nc\nd\n")
    client = _Client(st.acp, make_session(st, ws))

    async def main():
        return [(await client.read_text_file("acp-1", "n.txt", **kw)).content for kw in (
            {}, {"line": 2, "limit": 2}, {"line": 3}, {"limit": 1})]

    whole, window, tail, head = asyncio.run(main())
    assert whole == "a\nb\nc\nd\n"
    assert window == "b\nc"
    assert tail == "c\nd\n"
    assert head == "a"


# ------------------------------------------------------------------ _Client: extensions
def test_unknown_extension_is_method_not_found_and_notifications_forwarded(st, tmp_path):
    sess = make_session(st, tmp_path / "ws")
    client = _Client(st.acp, sess)

    async def main():
        with pytest.raises(acp.RequestError) as e:
            await client.ext_method("_cursor/unknown_thing", {})
        assert e.value.code == -32601
        await client.ext_notification("_cursor/progress", {"step": 2})

    asyncio.run(main())
    assert sess.events == [{"type": "ext", "method": "cursor/progress", "data": '{"step": 2}'}]


def test_cursor_question_skip_and_plan_cancel_or_accept(st):
    async def main():
        s = await st.acp.get_or_open("fake", None, None)
        try:
            ev, _ = await turn(st, s, "!ask", answer=lambda e: st.acp.respond(e["request_id"], {"skip": True}))
            assert "skipped" in texts(ev)
            assert any(e["type"] == "resolved" and e["decision"] == "skipped" for e in ev)

            ev, _ = await turn(st, s, "!plan", answer=lambda e: st.acp.respond(e["request_id"], {"accepted": True}))
            assert "accepted" in texts(ev)

            ev, _ = await turn(st, s, "!plan", answer=lambda e: st.acp.respond(e["request_id"], {"cancel": True}))
            assert "cancelled" in texts(ev)
            assert any(e["type"] == "resolved" and e["decision"] == "cancelled" for e in ev)
        finally:
            await st.acp.close_all()

    asyncio.run(main())


# ------------------------------------------------------------------ lifecycle helpers
def test_stderr_drain_keeps_trimmed_lines(tmp_path):
    async def main():
        reader = asyncio.StreamReader()
        reader.feed_data(b"first line\r\nsecond\n" + b"x" * 500 + b"\n")
        reader.feed_eof()
        buf = deque(maxlen=40)
        await AcpManager._drain(reader, buf)
        await AcpManager._drain(None, buf)            # no stream: returns at once
        return list(buf)

    assert asyncio.run(main()) == ["first line", "second", "x" * 400]


def test_closing_a_session_twice_tears_down_once(st, tmp_path):
    waits = []
    proc = FakeProc()

    async def counting_wait():
        waits.append(1)
        return 0

    proc.wait = counting_wait
    sess = make_session(st, tmp_path / "ws")
    sess.proc = proc

    async def main():
        await st.acp._close_session(sess)
        await st.acp._close_session(sess)

    asyncio.run(main())
    assert sess.closed and len(waits) == 1


def test_session_cap_refuses_new_agents(st, tmp_path):
    agent = st.acp.store.get("fake")
    for i in range(acp_agents.MAX_LIVE_SESSIONS):
        st.acp.sessions[f"busy{i}"] = AcpSession(f"busy{i}", agent, tmp_path / "ws")

    async def main():
        with pytest.raises(AcpError, match="too many live agent sessions"):
            await st.acp.get_or_open("fake", None, None)

    try:
        asyncio.run(main())
    finally:
        st.acp.sessions.clear()


def test_idle_session_is_reaped_when_a_new_one_opens(st, tmp_path, monkeypatch):
    conn = FakeConn()
    use_fake_conn(monkeypatch, conn)
    stale = AcpSession("stale", st.acp.store.get("fake"), tmp_path / "ws")
    stale.last_used = time.time() - acp_agents.IDLE_REAP_S - 60
    st.acp.sessions["stale"] = stale

    async def main():
        fresh = await st.acp.get_or_open("fake", None, None)
        try:
            return fresh.acp_id, "stale" in st.acp.sessions
        finally:
            await st.acp.close_all()

    acp_id, stale_still_open = asyncio.run(main())
    assert acp_id == "acp-1" and stale_still_open is False
    assert st.acp.sessions == {}


# ------------------------------------------------------------------ session setup failures (fake SDK connection)
@pytest.mark.parametrize("code, methods, reason", [
    (-32603, [S.AuthMethodAgent(id="fake_login", name="Fake login")], "internal boom"),   # not an auth failure
    (-32000, [], "no sign-in offered"),                                                   # auth failure, nothing to sign in with
])
def test_unrecoverable_session_setup_errors_are_reported(st, monkeypatch, code, methods, reason):
    conn = FakeConn()
    conn.auth_methods = methods
    conn.new_errors = [acp.RequestError(code, reason)]
    use_fake_conn(monkeypatch, conn)

    async def main():
        await st.acp.get_or_open("fake", None, None)

    with pytest.raises(AcpError, match=f"could not start Fake: RequestError: {reason}"):
        asyncio.run(main())
    assert conn.calls == [] and st.acp.sessions == {}


def test_sign_in_retry_that_still_fails_asks_the_user_to_log_in(st, monkeypatch):
    conn = FakeConn()
    conn.auth_methods = [S.AuthMethodAgent(id="fake_login", name="Fake login")]
    conn.new_errors = [acp.RequestError.auth_required(), acp.RequestError.auth_required()]
    use_fake_conn(monkeypatch, conn)

    async def main():
        await st.acp.get_or_open("fake", None, None)

    with pytest.raises(AcpError, match="needs you to sign in first") as ex:
        asyncio.run(main())
    assert conn.calls == [("authenticate", "fake_login")]
    assert not str(ex.value).startswith("could not start")     # the AcpError passes through unwrapped
    assert st.acp.sessions == {}


def test_startup_crash_reports_exception_and_agent_stderr(st, monkeypatch):
    conn = FakeConn()
    conn.init_error = RuntimeError("boom")
    conn.stderr_text = "fatal: missing api key\n"
    use_fake_conn(monkeypatch, conn)

    async def main():
        await st.acp.get_or_open("fake", None, None)

    with pytest.raises(AcpError) as ex:
        asyncio.run(main())
    assert str(ex.value) == "could not start Fake: RuntimeError: boom. stderr: fatal: missing api key"
    assert st.acp.sessions == {}


def test_startup_error_without_message_names_the_exception(st, monkeypatch):
    conn = FakeConn()
    conn.init_error = RuntimeError()
    use_fake_conn(monkeypatch, conn)

    async def main():
        await st.acp.get_or_open("fake", None, None)

    with pytest.raises(AcpError) as ex:
        asyncio.run(main())
    assert str(ex.value) == "could not start Fake: RuntimeError."


def test_cancelled_startup_propagates_cancellation(st, monkeypatch):
    conn = FakeConn()
    conn.init_error = asyncio.CancelledError()
    use_fake_conn(monkeypatch, conn)

    async def main():
        await st.acp.get_or_open("fake", None, None)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main())
    assert st.acp.sessions == {}


# ------------------------------------------------------------------ turn-level guards (fake SDK connection)
def test_prompt_rejects_bad_policy_and_a_dead_agent_process(st, monkeypatch):
    conn = FakeConn()
    use_fake_conn(monkeypatch, conn)

    async def main():
        sess = await st.acp.get_or_open("fake", None, None)
        try:
            with pytest.raises(AcpError, match="policy must be"):
                await st.acp.prompt(sess, "hi", lambda e: None, policy="yolo")
            sess.proc.returncode = 3                  # the agent process died since its last turn
            with pytest.raises(AcpError, match=r"agent process exited \(code 3\)"):
                await st.acp.prompt(sess, "hi", lambda e: None)
            return sess
        finally:
            await st.acp.close_all()

    sess = asyncio.run(main())
    assert sess.turns == 0 and conn.calls == []


def test_turn_timeout_cancels_the_agent_and_reports_it(st, monkeypatch):
    conn = FakeConn()
    conn.prompt_hang = True
    use_fake_conn(monkeypatch, conn)

    async def main():
        sess = await st.acp.get_or_open("fake", None, None)
        try:
            with pytest.raises(AcpError, match="exceeded 1s and was cancelled"):
                await st.acp.prompt(sess, "hi", lambda e: None, timeout=1)
            return sess
        finally:
            await st.acp.close_all()

    sess = asyncio.run(main())
    assert ("cancel", "acp-1") in conn.calls
    assert sess.turns == 0


def test_agent_protocol_error_during_a_turn_is_surfaced(st, monkeypatch):
    conn = FakeConn()
    conn.prompt_exc = acp.RequestError(-32603, "model unavailable")
    use_fake_conn(monkeypatch, conn)

    async def main():
        sess = await st.acp.get_or_open("fake", None, None)
        try:
            with pytest.raises(AcpError, match="agent error -32603: model unavailable"):
                await st.acp.prompt(sess, "hi", lambda e: None)
            return sess
        finally:
            await st.acp.close_all()

    sess = asyncio.run(main())
    assert sess.turns == 0
    assert st.acp.store.usage() == []                 # a failed turn is not billed to the ledger


# ------------------------------------------------------------------ misc
def test_probe_of_missing_agent_fails_cleanly(st):
    st.acp.store.update("fake", {"command": "definitely-not-installed-xyz"})
    with pytest.raises(AcpError, match="not found on PATH"):
        asyncio.run(st.acp.probe("fake"))
    assert st.acp.sessions == {}
