import asyncio
import json
import shutil

import pytest

from app.agents import AgentDef
from app.harness import files as F
from app.harness import sandbox as sbx
from app.harness.sandbox import Sandbox
from app.harness.tools import ALL_TOOLS, ToolContext, build_tools
from app.mcp_registry import McpStore
from app.skills import SkillRegistry

DOCKER = sbx.status()
needs_docker = pytest.mark.skipif(not (DOCKER["docker"] and DOCKER["image"]), reason="docker sandbox image not available")


@pytest.fixture
def env(tmp_path, state):
    ws = tmp_path / "ws"
    ws.mkdir()
    skills = SkillRegistry(user_dir=tmp_path / "uskills")
    ctx = ToolContext(ws, Sandbox(ws), skills, state.mcp)
    tools = {f.__name__: f for f in build_tools(ctx, ALL_TOOLS)}
    return ws, ctx, tools


def run(coro):
    return asyncio.run(coro)


# ---------- path jail ----------
def test_jail_blocks_escapes(tmp_path):
    ws = tmp_path / "w"; ws.mkdir()
    for bad in ["../x", "a/../../x", str(tmp_path / "outside.txt"), "", ".git/config"]:
        with pytest.raises(F.ToolError):
            F.jail(ws, bad)
    assert F.jail(ws, "sub/ok.txt").is_relative_to(ws.resolve())


def test_tools_refuse_escape(env):
    ws, ctx, t = env
    assert run(t["read_file"]("../../etc/passwd"))["ok"] is False
    assert run(t["write_file"]("../evil.py", "x=1"))["ok"] is False
    assert not (ws.parent / "evil.py").exists()


# ---------- edit_file + mandatory verify ----------
def test_edit_requires_read_then_applies_with_diff(env):
    ws, ctx, t = env
    assert run(t["write_file"]("a.py", "def f():\n    return 1\n"))["ok"]
    ctx.read_files.clear()
    r = run(t["edit_file"]("a.py", "return 1", "return 2"))
    assert not r["ok"] and "read the file" in r["error"]
    run(t["read_file"]("a.py"))
    r = run(t["edit_file"]("a.py", "return 1", "return 2"))
    assert r["ok"] and "-    return 1" in r["diff"] and "+    return 2" in r["diff"]
    assert (ws / "a.py").read_text() == "def f():\n    return 2\n"
    assert any(c["check"] == "python-syntax" and c["status"] == "pass" for c in r["verify"])


def test_edit_syntax_error_is_rolled_back(env):
    ws, ctx, t = env
    run(t["write_file"]("a.py", "x = 1\n"))
    run(t["read_file"]("a.py"))
    r = run(t["edit_file"]("a.py", "x = 1", "x = (1"))
    assert not r["ok"] and r["rolled_back"] is True
    assert (ws / "a.py").read_text() == "x = 1\n"  # original restored


def test_write_new_invalid_file_removed(env):
    ws, ctx, t = env
    r = run(t["write_file"]("bad.json", "{nope"))
    assert not r["ok"] and r["rolled_back"] and not (ws / "bad.json").exists()


def test_unique_match_required_and_hints(env):
    ws, ctx, t = env
    (ws / "d.txt").write_text("a\nb\na\n")
    run(t["read_file"]("d.txt"))
    r = run(t["edit_file"]("d.txt", "a", "z"))
    assert not r["ok"] and "matches 2 places" in r["error"]
    assert run(t["edit_file"]("d.txt", "a", "z", replace_all=True))["ok"]
    r = run(t["edit_file"]("d.txt", "totally missing", "q"))
    assert not r["ok"] and "not found" in r["error"]


def test_whitespace_tolerant_match_and_crlf_preserved(env):
    ws, ctx, t = env
    (ws / "c.txt").write_bytes(b"  hello   world\r\nsecond\r\n")
    run(t["read_file"]("c.txt"))
    r = run(t["edit_file"]("c.txt", "hello world", "HELLO"))
    assert r["ok"] and r["match"] == "whitespace-tolerant"
    assert (ws / "c.txt").read_bytes() == b"HELLO\r\nsecond\r\n"


def test_unverifiable_type_reports_skipped_not_silent(env):
    ws, ctx, t = env
    r = run(t["write_file"]("notes.md", "# hi"))
    assert r["ok"] and r["verified"] is False and r["verify"][0]["status"] == "skipped"


# ---------- docker sandbox ----------
@needs_docker
def test_sandbox_runs_isolated(env):
    ws, ctx, t = env
    r = run(t["run_shell"]("echo hi && id -u && pwd"))
    assert r["ok"] and "hi" in r["stdout"] and "1000" in r["stdout"] and "/workspace" in r["stdout"]
    # no network by default
    r = run(t["run_shell"]("python -c \"import urllib.request;urllib.request.urlopen('http://1.1.1.1',timeout=3)\"", 15))
    assert not r["ok"]
    # files written inside appear on the host workspace; host files outside the mount are invisible
    run(t["run_shell"]("echo data > out.txt"))
    assert (ws / "out.txt").read_text().strip() == "data"
    assert not run(t["run_shell"]("ls /h /c 2>/dev/null"))["stdout"].strip()
    ctx.sandbox.stop()


@needs_docker
def test_timeout_kills(env):
    ws, ctx, t = env
    r = run(t["run_shell"]("sleep 30", 2))
    assert not r["ok"] and r["timed_out"]
    ctx.sandbox.stop()


@needs_docker
def test_ruff_catches_undefined_name_and_rolls_back(env):
    ws, ctx, t = env
    r = run(t["write_file"]("m.py", "def f():\n    return undefined_thing\n"))
    assert not r["ok"] and r["rolled_back"]
    assert any(c["check"] == "ruff" and c["status"] == "fail" and "F821" in c["detail"] for c in r["verify"])
    assert not (ws / "m.py").exists()
    ctx.sandbox.stop()


def test_fail_closed_when_docker_missing(env, monkeypatch):
    ws, ctx, t = env
    monkeypatch.setattr(shutil, "which", lambda *_: None)
    r = run(t["run_shell"]("echo SHOULD-NOT-RUN > leaked.txt"))
    assert not r["ok"] and "SANDBOX UNAVAILABLE" in r["error"]
    assert not (ws / "leaked.txt").exists()
    # file editing still works (syntax check is host-side, no execution) but lint is reported skipped, not faked
    r = run(t["write_file"]("ok.py", "x = 1\n"))
    assert r["ok"] and any(c["check"] == "ruff" and c["status"] == "skipped" for c in r["verify"])


# ---------- skills / MCP / agents ----------
def test_builtin_skills_and_creation(tmp_path):
    reg = SkillRegistry(user_dir=tmp_path / "u")
    names = {s.name for s in reg.list()}
    assert {"skill-creator", "code-review", "debugging", "git-workflow"} <= names
    assert reg.get("git-workflow").mcp == ["git"]
    s = reg.create("my-skill", "Does X when Y.", "1. do it", ["fetch"])
    assert s.source == "user" and reg.get("my-skill").mcp == ["fetch"]
    with pytest.raises(ValueError):
        reg.create("skill-creator", "x", "y")          # cannot shadow built-in
    with pytest.raises(ValueError):
        reg.create("Bad Name", "x", "y")
    assert "my-skill" in reg.index_text(["*"]) and reg.index_text(["debugging"]).count("- ") == 1


def test_create_skill_tool_validates_mcp_and_proposal_needs_approval(env, state):
    ws, ctx, t = env
    assert not run(t["create_skill"]("s1", "d", "b", ["nonexistent"]))["ok"]
    assert run(t["create_skill"]("s1", "d", "b", ["git"]))["ok"]
    r = run(t["propose_mcp_server"]("mything", "My thing", "why", "npx", ["-y", "pkg@1.0.0"]))
    assert r["ok"] and r["status"] == "pending_approval"
    m = state.mcp.get("mything")
    assert not m.enabled and not m.ready()
    m = state.mcp.update("mything", enabled=True)   # human approval
    assert m.status == "active" and m.ready()


def test_mcp_secrets_never_exposed(client):
    client.put("/api/mcp/github", json={"enabled": True, "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_SECRET123"}})
    body = client.get("/api/mcp").text
    assert "ghp_SECRET123" not in body
    g = next(m for m in client.get("/api/mcp").json() if m["id"] == "github")
    assert g["env_set"]["GITHUB_PERSONAL_ACCESS_TOKEN"] is True and g["ready"] is True
    assert {m["id"] for m in client.get("/api/mcp").json() if m["default_on"]} == {"filesystem", "fetch", "git", "memory"}


def test_agent_validation(state):
    a = state.agents
    assert {x.id for x in a.list()} >= {"engineer", "explorer", "coder", "reviewer", "assistant"}
    with pytest.raises(ValueError):
        a.update("explorer", {"sub_agents": ["engineer"]})   # engineer -> explorer -> engineer cycle
    with pytest.raises(ValueError):
        a.create(AgentDef(id="x1", name="x", tools=["rm_rf"]))
    with pytest.raises(ValueError):
        a.delete("engineer")
    a.create(AgentDef(id="mine", name="Mine", tier="pro", sub_agents=["explorer"]))
    with pytest.raises(ValueError):
        a.delete("explorer")  # builtin
