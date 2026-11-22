"""Headless-CLI harness adapter (Aider, OpenHands) driven against a scripted fake CLI (tests/fake_cli_agent.py):
success, permission mapping, timeout, cancel, crash, malformed output, output capping, WSL launching, version checks.
No model, no network, no spend, nothing installed."""
import asyncio
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import acp_agents, cli_agents
from app.acp_agents import AcpError, AcpSession
from app.cli_agents import AiderParser, Capped, CliError, JsonlParser
from app.db import Database
from app.main import State, create_app

FAKE = str(Path(__file__).parent / "fake_cli_agent.py")


@pytest.fixture
def st(tmp_path):
    s = State(Database(tmp_path / "t.db"))
    s.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    for aid in ("aider", "openhands"):
        s.acp.store.update(aid, {"command": sys.executable, "args": [FAKE]})
    return s


def run(coro):
    return asyncio.run(coro)


async def turn(st, sess, text, answer="allow", policy="ask", timeout=60):
    events = []

    def emit(ev):
        events.append(ev)
        if answer and ev["type"] == "permission":
            asyncio.get_running_loop().call_soon(lambda: st.acp.respond(ev["request_id"], {"option_id": answer}))

    res = await st.acp.prompt(sess, text, emit, policy=policy, timeout=timeout)
    return events, res


def texts(events):
    return "".join(e["text"] for e in events if e["type"] == "text")


# ------------------------------------------------------------------ catalog / store view
def test_catalog_lists_all_four_harnesses_with_install_text(tmp_path):
    s = State(Database(tmp_path / "t.db"))
    by = {a["id"]: a for a in s.acp.store.list()}
    assert {"claude", "codex", "aider", "openhands"} <= set(by)
    assert by["claude"]["kind"] == "acp" and by["codex"]["kind"] == "acp"
    assert by["aider"]["kind"] == "cli" and by["openhands"]["kind"] == "cli"
    assert "@agentclientprotocol/codex-acp@2.5.0" in by["codex"]["args"][1]
    assert "uv tool install" in by["aider"]["install"] and "SETUP:" in by["aider"]["notes"]
    assert "WSL" in by["openhands"]["install"] and "LLM_API_KEY" in by["openhands"]["env_keys"]
    assert "DEFAULT for large coding tasks" in by["claude"]["notes"]
    assert by["cursor"]["install"] == "" and "not capped" in by["cursor"]["billing"]


def test_custom_agent_is_always_acp(tmp_path):
    s = State(Database(tmp_path / "t.db"))
    s.acp.store.create("mine", "Mine", sys.executable, [FAKE])
    v = s.acp.store.get("mine")
    assert v["kind"] == "acp" and v["installed"] is True


def test_view_installed_via_wsl(tmp_path, monkeypatch):
    s = State(Database(tmp_path / "t.db"))
    s.acp.store.update("openhands", {"command": "oh-not-on-windows"})
    monkeypatch.setattr(cli_agents, "is_installed", lambda c, env=None: True)
    v = s.acp.store.get("openhands")
    assert v["installed"] is True and v["resolved_path"] == "oh-not-on-windows"


# ------------------------------------------------------------------ Aider: success, usage, permission
def test_aider_success_usage_and_ledger(st, tmp_path):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        assert s.kind == "cli" and s.info["protocol"].startswith("n/a")
        ev, res = await turn(st, s, "!aider")
        perm = [e for e in ev if e["type"] == "permission"]
        assert len(perm) == 1 and perm[0]["kind"] == "execute" and "without asking again" in perm[0]["input"]
        assert any(e["type"] == "fs_write" and e["path"] == "src/a.py" for e in ev)
        assert "Applied edit to src/a.py" in texts(ev)
        assert res["stop_reason"] == "end_turn" and res["turns"] == 1
        assert res["usage"] == dict(input_tokens=2000, output_tokens=400, total_tokens=2400)
        assert res["reported_cost"] == pytest.approx(0.05) and res["currency"] == "USD"
        assert "not covered" in res["billing_note"]
        assert s.public()["agent"] == "aider" and s.proc is None
        await st.acp.close_all()
    run(main())
    u = st.acp.store.usage()[0]
    assert u["agent"] == "aider" and u["total_tokens"] == 2400 and u["reported_cost"] == pytest.approx(0.05)


def test_session_reuse_and_unknown_session(st):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        _, r1 = await turn(st, s, "one")
        s2 = await st.acp.get_or_open("aider", s.id, None)      # a live cli session is reused (no process to check)
        assert s2 is s
        _, r2 = await turn(st, s2, "two")
        assert r2["turns"] == 2
        with pytest.raises(AcpError):
            await st.acp.get_or_open("aider", "nope", None)
        await st.acp.close_all()
    run(main())


def test_rejected_run_executes_nothing(st, tmp_path):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        ev, res = await turn(st, s, "!write marker.txt|x", answer="reject")
        assert res["stop_reason"] == "cancelled" and "not approved" in texts(ev)
        assert any(e["type"] == "resolved" and e["decision"] == "reject" for e in ev)
        await st.acp.close_all()
    run(main())
    assert not (tmp_path / "ws" / "marker.txt").exists()


def test_unanswered_approval_times_out_as_denied(st, monkeypatch):
    monkeypatch.setattr(acp_agents, "INTERACT_TIMEOUT_S", 0.2)

    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        ev, res = await turn(st, s, "!aider", answer=None)
        assert res["stop_reason"] == "cancelled"
        assert any(e["type"] == "resolved" and e["decision"] == "cancelled" for e in ev)
        await st.acp.close_all()
    run(main())


def test_reads_auto_still_asks_because_the_run_writes(st):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        ev, res = await turn(st, s, "hi", policy="reads_auto")
        assert any(e["type"] == "permission" for e in ev) and res["stop_reason"] == "end_turn"
        await st.acp.close_all()
    run(main())


def test_readonly_aider_uses_dry_run_without_asking(st):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        ev, res = await turn(st, s, "!args", policy="readonly")
        assert not any(e["type"] == "permission" for e in ev)
        assert "--dry-run" in texts(ev) and "--message-file" in texts(ev) and "--no-git" in texts(ev)
        await st.acp.close_all()
    run(main())


def test_aider_inside_git_repo_does_not_get_no_git(st, tmp_path):
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / ".git").mkdir()

    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        ev, _ = await turn(st, s, "!args")
        assert "--no-git" not in texts(ev)
        await st.acp.close_all()
    run(main())


# ------------------------------------------------------------------ OpenHands: jsonl, readonly refusal
def test_openhands_jsonl_report_cost_and_tool_events(st):
    async def main():
        s = await st.acp.get_or_open("openhands", None, None)
        ev, res = await turn(st, s, "!jsonl")
        assert texts(ev).count("all done") == 1 and "starting up" in texts(ev)
        tools = [e for e in ev if e["type"] == "tool_call"]
        assert [t["title"] for t in tools] == ["terminal", "think"] and tools[0]["id"] == "t1" and tools[1]["input"] is None
        assert res["usage"] == dict(input_tokens=100, output_tokens=25, total_tokens=125)
        assert res["reported_cost"] == pytest.approx(0.07)
        await st.acp.close_all()
    run(main())


def test_openhands_readonly_is_refused(st):
    async def main():
        s = await st.acp.get_or_open("openhands", None, None)
        with pytest.raises(AcpError, match="no read-only mode"):
            await turn(st, s, "hi", policy="readonly")
        await st.acp.close_all()
    run(main())


def test_invalid_policy(st):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        with pytest.raises(AcpError, match="policy must be"):
            await turn(st, s, "hi", policy="yolo")
        await st.acp.close_all()
    run(main())


# ------------------------------------------------------------------ workspace jail, env
def test_cwd_is_the_workspace_and_env_is_passed(st, tmp_path):
    st.acp.store.update("aider", {"env": {"OPENAI_API_KEY": "sk-fake"}})

    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        ev, _ = await turn(st, s, "!cwd")
        assert Path(texts(ev).split("cwd:", 1)[1].strip()).resolve() == (tmp_path / "ws").resolve()
        ev, _ = await turn(st, s, "!env OPENAI_API_KEY")
        assert "OPENAI_API_KEY=present" in texts(ev)
        ev, _ = await turn(st, s, "!env NO_COLOR")
        assert "NO_COLOR=present" in texts(ev)
        ev, _ = await turn(st, s, "!write sub/x.txt|hello")
        assert "wrote sub/x.txt" in texts(ev)
        await st.acp.close_all()
    run(main())
    assert (tmp_path / "ws" / "sub" / "x.txt").read_text() == "hello"


# ------------------------------------------------------------------ failure modes
def test_crash_reports_exit_code_and_stderr(st):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        with pytest.raises(AcpError) as e:
            await turn(st, s, "!die")
        assert "exited with code 3" in str(e.value) and "kaboom" in str(e.value)
        _, res = await turn(st, s, "still alive")        # the session survives a crashed run
        assert res["stop_reason"] == "end_turn"
        await st.acp.close_all()
    run(main())


def test_crash_without_stderr(st, monkeypatch):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        st.acp.store.update("aider", {"args": ["-c", "import sys; sys.exit(4)"]})
        s.agent = st.acp.store.get("aider")
        with pytest.raises(AcpError, match=r"code 4\. \(no stderr\)"):
            await turn(st, s, "x")
        await st.acp.close_all()
    run(main())


def test_malformed_output_is_tolerated(st):
    async def main():
        s = await st.acp.get_or_open("openhands", None, None)
        ev, res = await turn(st, s, "!junk")
        t = texts(ev)
        assert "red text" in t and "\x1b" not in t and "\x00" not in t and "last line without newline" in t
        assert '{"broken":' in t and "[1, 2, 3]" in t
        assert res["usage"] is None and res["reported_cost"] is None       # unknown, not invented
        assert "red text" in "".join(e["text"] for e in ev if e["type"] == "text")
        await st.acp.close_all()
    run(main())


def test_silent_run_has_no_text_and_unknown_usage(st):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        ev, res = await turn(st, s, "!silent")
        assert texts(ev) == "" and res["stop_reason"] == "end_turn" and res["usage"] is None and res["reported_cost"] is None
        await st.acp.close_all()
    run(main())
    u = st.acp.store.usage()[0]
    assert u["turns_without_usage"] == 1


def test_timeout_kills_the_process(st):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        t0 = time.time()
        with pytest.raises(AcpError, match="exceeded 1s"):
            await turn(st, s, "!slow", timeout=1)
        assert time.time() - t0 < 20 and s.proc is None
        await st.acp.close_all()
    run(main())


def test_cancel_a_running_turn(st):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        started = asyncio.Event()
        events = []

        def emit(ev):
            events.append(ev)
            if ev["type"] == "permission":
                asyncio.get_running_loop().call_soon(lambda: st.acp.respond(ev["request_id"], {"option_id": "allow"}))
            if ev["type"] == "text" and "started" in ev["text"]:
                started.set()

        task = asyncio.create_task(st.acp.prompt(s, "!slow", emit, policy="ask", timeout=60))
        await asyncio.wait_for(started.wait(), 20)
        await st.acp.cancel(s)
        res = await asyncio.wait_for(task, 20)
        assert res["stop_reason"] == "cancelled"
        await st.acp.close_all()
    run(main())


def test_cancel_while_waiting_for_approval_and_idle_cancel(st):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        await st.acp.cancel(s)                                   # idle session: nothing to kill, no error
        asked = asyncio.Event()
        task = asyncio.create_task(st.acp.prompt(s, "hi", lambda ev: asked.set() if ev["type"] == "permission" else None, policy="ask"))
        await asyncio.wait_for(asked.wait(), 20)
        await st.acp.cancel(s)
        res = await asyncio.wait_for(task, 20)
        assert res["stop_reason"] == "cancelled"
        await st.acp.close_all()
    run(main())


def test_closing_a_session_mid_run_kills_it(st):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        started = asyncio.Event()

        def emit(ev):
            if ev["type"] == "permission":
                asyncio.get_running_loop().call_soon(lambda: st.acp.respond(ev["request_id"], {"option_id": "allow"}))
            if ev["type"] == "text" and "started" in ev["text"]:
                started.set()

        task = asyncio.create_task(st.acp.prompt(s, "!slow", emit, policy="ask", timeout=60))
        await asyncio.wait_for(started.wait(), 20)
        assert await st.acp.close(s.id) is True
        with pytest.raises(AcpError):
            await asyncio.wait_for(task, 20)                     # killed mid-run -> non-zero exit reported
    run(main())


def test_output_is_capped(st):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        ev, res = await turn(st, s, "!big")
        t = texts(ev)
        assert len(t) > 500_000                                   # the UI stream still saw everything...
        assert any(e["type"] == "note" and "not kept" in e["text"] for e in ev)
        await st.acp.close_all()
        return res
    run(main())


def test_report_text_is_bounded(st):
    parser = AiderParser()
    for i in range(6000):
        parser.feed("z" * 100, lambda e: None)
    text, _, _ = parser.finish()
    assert len(text) < cli_agents.MAX_HEAD_CHARS + cli_agents.MAX_TAIL_CHARS + 200 and "omitted" in text and parser.dropped > 0


def test_missing_binary_is_reported_with_install_hint(tmp_path, monkeypatch):
    s = State(Database(tmp_path / "t.db"))
    s.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    s.acp.store.update("aider", {"command": "definitely-not-installed-xyz"})
    monkeypatch.setattr(cli_agents, "IS_WINDOWS", False)

    async def main():
        with pytest.raises(AcpError, match="not found on PATH.*uv tool install"):
            await s.acp.get_or_open("aider", None, None)
        with pytest.raises(AcpError, match="not found on PATH"):
            await s.acp.probe("aider")
    run(main())


def test_binary_vanishes_after_open(st, monkeypatch):
    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        st.acp.store.update("aider", {"command": "definitely-not-installed-xyz"})
        s.agent = {**s.agent, "command": "definitely-not-installed-xyz"}
        monkeypatch.setattr(cli_agents, "IS_WINDOWS", False)
        with pytest.raises(AcpError, match="was not found on PATH"):
            await turn(st, s, "x")
        await st.acp.close_all()
    run(main())


def test_spawn_oserror_is_reported(st, monkeypatch):
    async def boom(*a, **k):
        raise OSError("exec format error")
    monkeypatch.setattr(cli_agents.asyncio, "create_subprocess_exec", boom)

    async def main():
        s = await st.acp.get_or_open("aider", None, None)
        with pytest.raises(AcpError, match="could not start Aider: exec format error"):
            await turn(st, s, "x")
        await st.acp.close_all()
    run(main())


def test_disabled_agent_refuses(st):
    st.acp.store.update("aider", {"enabled": False})

    async def main():
        with pytest.raises(AcpError, match="disabled"):
            await st.acp.get_or_open("aider", None, None)
    run(main())


# ------------------------------------------------------------------ one-click, free health check
def test_probe_runs_version_check_only(st):
    async def main():
        r = await st.acp.probe("aider")
        assert r["ok"] and r["version"].startswith("Python") and r["protocol"].startswith("n/a") and r["modes"] is None
        assert st.acp.sessions == {}
        return r
    run(main())
    assert st.acp.store.usage() == []                           # nothing was prompted -> nothing spent


def test_probe_version_failure(st, monkeypatch):
    async def bad(aid, ws):
        raise CliError("exited with code 5")
    monkeypatch.setattr(st.acp.store, "version", bad)

    async def main():
        with pytest.raises(AcpError, match="did not pass its version check: exited with code 5"):
            await st.acp.probe("aider")
    run(main())


def test_http_probe_and_listing(st):
    c = TestClient(create_app(st))
    rows = {a["id"]: a for a in c.get("/api/acp/agents").json()}
    assert rows["aider"]["kind"] == "cli" and rows["aider"]["installed"]
    r = c.post("/api/acp/agents/aider/probe")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_store_version_variants(st, monkeypatch):
    async def fake_version(command, args, ws, env):
        return f"{command}|{' '.join(args)}"
    monkeypatch.setattr(cli_agents, "version_of", fake_version)

    async def main(ws):
        v = st.acp.store.version
        assert (await v("aider", ws)).endswith("|--version")                 # cli: the configured command
        with pytest.raises(AcpError):
            await v("nope-agent", ws)                                         # not configured at all
        st.acp.store.create("mine", "Mine", sys.executable, [])
        assert await v("mine", ws) is None                                    # custom agents have no known check
        monkeypatch.setattr(cli_agents, "is_installed", lambda c, env=None: False)
        assert await v("claude", ws) is None                                  # ACP adapter, global CLI not installed
        monkeypatch.setattr(cli_agents, "is_installed", lambda c, env=None: True)
        assert await v("claude", ws) == "claude|--version"                    # informational global CLI version
    run(main(st.acp.workspace_for(None)))


def test_acp_probe_reports_cli_version_and_survives_its_failure(st, monkeypatch):
    fake = str(Path(__file__).parent / "fake_acp_agent.py")
    st.acp.store.create("fake", "Fake", sys.executable, [fake])

    async def main():
        monkeypatch.setattr(st.acp.store, "version", lambda aid, ws: _const("1.2.3"))
        assert (await st.acp.probe("fake"))["cli_version"] == "1.2.3"

        async def bad(aid, ws):
            raise CliError("nope")
        monkeypatch.setattr(st.acp.store, "version", bad)
        assert (await st.acp.probe("fake"))["cli_version"] is None
    run(main())


async def _const(v):
    return v


# ------------------------------------------------------------------ version_of
def test_version_of_ok_and_failures(tmp_path, monkeypatch):
    env = {}

    async def main():
        assert await cli_agents.version_of(sys.executable, [FAKE, "--version"], tmp_path, env) == "fakecli 1.2.3"
        with pytest.raises(CliError, match="exited with code 5.*boom"):
            await cli_agents.version_of(sys.executable, [FAKE, "--bad-version"], tmp_path, env)
        with pytest.raises(CliError, match="printed nothing"):
            await cli_agents.version_of(sys.executable, [FAKE, "--empty-version"], tmp_path, env)
        monkeypatch.setattr(cli_agents, "VERSION_TIMEOUT_S", 1)
        with pytest.raises(CliError, match="did not answer within 1s"):
            await cli_agents.version_of(sys.executable, [FAKE, "--hang-version"], tmp_path, env)

        async def boom(*a, **k):
            raise OSError("denied")
        monkeypatch.setattr(cli_agents.asyncio, "create_subprocess_exec", boom)
        with pytest.raises(CliError, match="could not start.*denied"):
            await cli_agents.version_of(sys.executable, ["x"], tmp_path, env)
    run(main())


# ------------------------------------------------------------------ WSL launching (Windows only; simulated here)
def test_to_wsl_path():
    assert cli_agents.to_wsl_path("H:\\htdocs\\x y\\f.txt") == "/mnt/h/htdocs/x y/f.txt"
    assert cli_agents.to_wsl_path("c:/") == "/mnt/c"
    with pytest.raises(CliError, match="cannot map"):
        cli_agents.to_wsl_path("\\\\server\\share\\x")


def _fake_which(table):
    return lambda name: table.get(name)


def test_launcher_native_wsl_and_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_agents, "IS_WINDOWS", True)
    monkeypatch.setattr(cli_agents.shutil, "which", _fake_which({"oh": "C:/bin/oh.exe"}))
    pre, env, wsl = cli_agents.launcher("oh", tmp_path, {"A": "1"})
    assert pre == ["C:/bin/oh.exe"] and env == {"A": "1"} and wsl is False
    # an existing file path works as the command
    pre, _, wsl = cli_agents.launcher(sys.executable, tmp_path, {})
    assert pre == [sys.executable] and wsl is False

    monkeypatch.setattr(cli_agents.shutil, "which", _fake_which({"wsl": "C:/Windows/wsl.exe"}))
    monkeypatch.setattr(cli_agents, "_wsl_has", lambda c, e: True)
    pre, env, wsl = cli_agents.launcher("openhands", tmp_path, {"LLM_API_KEY": "k", "LLM_MODEL": "m", "WSLENV": "X", "WSL_DISTRO": "Ubuntu-24.04"})
    assert wsl is True
    assert pre[:3] == ["C:/Windows/wsl.exe", "-d", "Ubuntu-24.04"] and "--cd" in pre and str(tmp_path) in pre
    assert pre[-6:] == ["-e", "bash", "-lc", 'exec "$@"', "bash", "openhands"]
    assert env["WSLENV"] == "X:LLM_API_KEY:LLM_MODEL"
    _, env2, _ = cli_agents.launcher("openhands", tmp_path, {})
    assert env2["WSLENV"] == ""

    monkeypatch.setattr(cli_agents, "_wsl_has", lambda c, e: False)
    with pytest.raises(CliError, match="or inside WSL"):
        cli_agents.launcher("openhands", tmp_path, {})
    monkeypatch.setattr(cli_agents.shutil, "which", _fake_which({}))
    with pytest.raises(CliError) as e:
        cli_agents.launcher("openhands", tmp_path, {})
    assert "inside WSL" not in str(e.value)


def test_run_via_wsl_translates_the_task_path(st, tmp_path, monkeypatch):
    mapped = []
    monkeypatch.setattr(cli_agents, "launcher", lambda command, ws, env: ([sys.executable, FAKE], env, True))
    monkeypatch.setattr(cli_agents, "to_wsl_path", lambda p: mapped.append(str(p)) or str(p))     # identity: the fake runs natively
    st.acp.store.update("openhands", {"args": []})

    async def main():
        s = await st.acp.get_or_open("openhands", None, None)
        ev, res = await turn(st, s, "via wsl")
        assert "echo: via wsl" in texts(ev) and res["stop_reason"] == "end_turn"
        await st.acp.close_all()
    run(main())
    assert len(mapped) == 1 and mapped[0].endswith("task.txt")


def test_wsl_has_probe_and_cache(monkeypatch):
    calls = []

    def fake_run(argv, **k):
        calls.append(argv)
        return SimpleNamespace(returncode=0 if argv[-1] == "present" else 1)

    monkeypatch.setattr(cli_agents.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_agents.shutil, "which", _fake_which({"wsl": "wsl.exe"}))
    cli_agents._wsl_cache.clear()
    assert cli_agents._wsl_has("present", {"WSL_DISTRO": "U"}) is True
    assert calls[0][:3] == ["wsl.exe", "-d", "U"]
    assert cli_agents._wsl_has("present", {"WSL_DISTRO": "U"}) is True and len(calls) == 1      # cached
    assert cli_agents._wsl_has("absent", {}) is False
    monkeypatch.setattr(cli_agents.shutil, "which", _fake_which({}))
    cli_agents._wsl_cache.clear()
    assert cli_agents._wsl_has("present", {}) is False                                           # no wsl.exe at all

    def boom(argv, **k):
        raise subprocess.TimeoutExpired(argv, 1)
    monkeypatch.setattr(cli_agents.subprocess, "run", boom)
    monkeypatch.setattr(cli_agents.shutil, "which", _fake_which({"wsl": "wsl.exe"}))
    cli_agents._wsl_cache.clear()
    assert cli_agents._wsl_has("x", {}) is False
    cli_agents._wsl_cache.clear()


def test_is_installed_variants(monkeypatch):
    monkeypatch.setattr(cli_agents, "IS_WINDOWS", True)
    monkeypatch.setattr(cli_agents.shutil, "which", _fake_which({"here": "/x/here"}))
    assert cli_agents.is_installed("here") is True
    assert cli_agents.is_installed(sys.executable) is True
    monkeypatch.setattr(cli_agents, "_wsl_has", lambda c, e: c == "inwsl")
    assert cli_agents.is_installed("inwsl") is False                    # wsl.exe itself missing
    monkeypatch.setattr(cli_agents.shutil, "which", _fake_which({"wsl": "wsl.exe"}))
    assert cli_agents.is_installed("inwsl", {}) is True and cli_agents.is_installed("other") is False
    monkeypatch.setattr(cli_agents, "IS_WINDOWS", False)
    assert cli_agents.is_installed("inwsl") is False


def test_kill_tree_variants(monkeypatch):
    killed = []
    proc = SimpleNamespace(pid=None, returncode=None, kill=lambda: killed.append("k"))
    cli_agents.kill_tree(proc)                                            # no pid
    cli_agents.kill_tree(SimpleNamespace(pid=5, returncode=0, kill=lambda: killed.append("late")))   # already exited
    assert killed == []
    calls = []
    monkeypatch.setattr(cli_agents.subprocess, "run", lambda *a, **k: calls.append(a[0]))
    monkeypatch.setattr(cli_agents, "IS_WINDOWS", True)
    cli_agents.kill_tree(SimpleNamespace(pid=5, returncode=None, kill=lambda: killed.append("k")))
    assert calls[0][:3] == ["taskkill", "/PID", "5"] and killed == ["k"]
    monkeypatch.setattr(cli_agents, "IS_WINDOWS", False)
    cli_agents.kill_tree(SimpleNamespace(pid=5, returncode=None, kill=lambda: (_ for _ in ()).throw(ProcessLookupError())))


# ------------------------------------------------------------------ parsers / bounded buffer
def test_capped_partial_and_whole_chunk_eviction():
    c = Capped()
    big = "a" * (cli_agents.MAX_HEAD_CHARS + 10)
    c.add(big)                                                            # head full, 10 chars spill to tail
    c.add("")                                                             # empty add is a no-op
    c.add("b" * 30_000)
    c.add("c" * 30_000)                                                   # tail now 60_010 > 40_000: oldest partially trimmed
    c.add("d" * 50_000)                                                   # evicts whole chunks
    assert c.tail_len == cli_agents.MAX_TAIL_CHARS and c.dropped == 10 + 30_000 + 30_000 + 50_000 - cli_agents.MAX_TAIL_CHARS
    assert "omitted" in c.text() and c.text().endswith("d" * 100)


def test_parser_edge_cases():
    p = AiderParser()
    ev = []
    p.feed("Tokens: sent and received words but no numbers", ev.append)
    assert p.finish()[1] is None and p.finish()[2] is None
    p.feed("Tokens: 2M sent, 1,500 received.", ev.append)
    assert p.finish()[1] == dict(input_tokens=2_000_000, output_tokens=1500, total_tokens=2_001_500)

    j = JsonlParser()
    assert j._text_of("s") == "s" and j._text_of(["a", {"text": "b"}, 5]) == "a\nb" and j._text_of({"zzz": 1}) == "" and j._text_of(7) == ""
    j.feed('{"source": "agent"}', ev.append)                              # agent event without text: ignored
    j.feed('{"tool_name": "t", "tool_call_id": "c9"}', ev.append)
    assert ev[-1]["id"] == "c9"
    j.feed('{"tool_name": "t2"}', ev.append)
    assert ev[-1]["id"] == "t2"
    j.feed("[1]", ev.append)
    j.feed('{"accumulated_cost": 1, "completion_tokens": 4}', ev.append)
    text, usage, cost = j.finish()
    assert usage == dict(input_tokens=0, output_tokens=4, total_tokens=4) and cost == 1.0 and "tool_name" in text


# ------------------------------------------------------------------ Super Engineer upgrade for existing databases
def test_super_engineer_upgrade_is_additive_and_respects_edits(tmp_path):
    import json
    from app import agents as ag
    db = Database(tmp_path / "t.db")
    store = ag.AgentStore(db)
    old_text = ag.SEEDS[-1].instruction.replace(ag.NEW_SE_STEP, ag.OLD_SE_STEP)
    assert old_text != ag.SEEDS[-1].instruction
    db.execute("UPDATE agents SET instruction=?, acp_agents=? WHERE id='super_engineer'", (old_text, json.dumps(["claude", "cursor"])))
    store = ag.AgentStore(db)                                                       # upgrade on startup
    a = store.get("super_engineer")
    assert a.instruction == ag.SEEDS[-1].instruction
    assert a.acp_agents == ["claude", "cursor", "codex", "aider", "openhands"]
    ag.AgentStore(db)                                                               # idempotent
    assert store.get("super_engineer").acp_agents == ["claude", "cursor", "codex", "aider", "openhands"]

    db.execute("UPDATE agents SET instruction='my own wording', acp_agents='[]' WHERE id='super_engineer'")
    a = ag.AgentStore(db).get("super_engineer")
    assert a.instruction == "my own wording" and a.acp_agents == []                  # opted out + edited: untouched

    db.execute("DELETE FROM agents WHERE id='super_engineer'")
    db.execute("UPDATE agents SET builtin=0 WHERE id='engineer'")
    s2 = ag.AgentStore.__new__(ag.AgentStore)
    s2.db = db
    s2._upgrade_super_engineer()                                                    # no built-in row: nothing to do, no error
