"""Deploy an agent to Claude Code: the plan (files, diffs, MCP servers, what is not carried over), writing it safely, and a live agent as an MCP server."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from app.agents import AgentDef
from app.claude_code import apply as A
from app.claude_code import export as E
from app.claude_code import mapping as M
from app.mcp_registry import McpServer


def srv(**kw) -> McpServer:
    base = dict(id="x", label="X", description="", transport="stdio", command="npx", args=["-y", "pkg@1"], url=None, env={}, requires_env=[], enabled=True, default_on=False,
                status="active", builtin=False)
    base.update(kw)
    return McpServer(**base)


def ag(**kw) -> AgentDef:
    base = dict(id="bot_one", name="Bot One", description="Does things", instruction="Be useful.", tools=["read_file"])
    base.update(kw)
    return AgentDef(**base)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "userhome"
    h.mkdir()
    return h


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def plan(state, aid, proj, home, **body):
    return E.build_plan(state, aid, E.parse_opts({"project_path": str(proj), **body}), home=home, claude_exe=body.pop("exe", None))


def file_named(p, suffix):
    return next(f for f in p["files"] if f["path"].replace("\\", "/").endswith(suffix))


# ---------------------------------------------------------------------------------------------------- mapping
def test_tool_translation_and_reasons():
    tools, lost = M.translate_tools(["read_file", "list_dir", "glob_files", "search_text", "edit_file", "load_skill", "rag_search", "repo_map", "save_artifact", "science_calc", "zzz"])
    assert tools == ["Read", "Glob", "Grep", "Edit"]
    assert [x["item"] for x in lost] == ["rag_search", "repo_map", "save_artifact", "science_calc", "zzz"]
    assert "RAG" in lost[0]["why"] and "project map" in lost[1]["why"] and "Artifacts" in lost[2]["why"] and "Science" in lost[3]["why"] and lost[4]["why"] == "No Claude Code tool does this."


def test_feature_losses_name_what_the_agent_uses():
    plain = ag(guardrails=["none"], agui={"components": False, "endpoint": True})
    assert M.feature_losses(plain) == []
    rich = ag(memory={"types": ["fact"]}, rag=["kb"], data=["db"], acp_agents=["codex"], can_spawn=True, identity={"scopes": ["x"]}, live={"enabled": True},
              agui={"components": True, "endpoint": True}, runtime="docker", framework="langgraph", guardrails=[])
    items = [x["item"] for x in M.feature_losses(rich)]
    assert items == ["guardrails", "memory", "knowledge bases", "data sources", "external agents (ACP)", "creating sub-agents at run time", "agent identity and scopes",
                     "Live profile", "AG-UI rich components", "runtime (docker)", "framework (langgraph)"]


# ---------------------------------------------------------------------------------------------------- options
def test_options_are_checked():
    ok = E.parse_opts({})
    assert (ok.mode, ok.scope, ok.mcp_placement, ok.include_sub_agents, ok.base_url) == ("export", "project", "config", True, "http://127.0.0.1:8765")
    for bad, msg in [({"mode": "x"}, "mode must"), ({"scope": "x"}, "scope must"), ({"mcp_placement": "x"}, "mcp_placement"), ({"include_skills": "yes"}, "true or false"),
                     ({"project_path": 5}, "folder path"), ({"project_path": "a" * 1001}, "folder path"), ({"base_url": "ftp://x"}, "base_url"), ({"base_url": 5}, "base_url"),
                     ({"base_url": "http://u:p@127.0.0.1:8765"}, "base_url"), ({"base_url": "http://127.0.0.1:8765/x"}, "base_url"), ({"base_url": "http://127.0.0.1?x=1"}, "base_url"),
                     ({"base_url": "http:///"}, "base_url")]:
        with pytest.raises(ValueError, match=msg):
            E.parse_opts(bad)
    assert E.parse_opts({"base_url": "https://studio.example.com:9000/"}).base_url == "https://studio.example.com:9000"
    assert E.parse_opts({"project_path": "  /x  "}).project_path == "/x"


def test_project_folder_is_validated(tmp_path, home):
    with pytest.raises(ValueError, match="choose the project folder"):
        E.resolve_root(E.parse_opts({}), home)
    with pytest.raises(ValueError, match="full path"):
        E.resolve_root(E.parse_opts({"project_path": "relative/dir"}), home)
    with pytest.raises(ValueError, match="does not exist"):
        E.resolve_root(E.parse_opts({"project_path": str(tmp_path / "nope")}), home)
    with pytest.raises(ValueError, match="root of a drive"):
        E.resolve_root(E.parse_opts({"project_path": Path(tmp_path.anchor).as_posix()}), home)
    assert E.resolve_root(E.parse_opts({"scope": "user"}), home) == home
    assert E.resolve_root(E.parse_opts({"project_path": str(tmp_path)}), home) == tmp_path.resolve()


def test_text_helpers():
    warns: list[str] = []
    assert E.clean_text("plain", "x", warns) == "plain" and warns == []
    out = E.clean_text("use " + "sk-" + "abcdefghijklmnopqrstu" + " now", "The instruction of A", warns)
    assert "sk-abc" not in out and E.MASK in out and "looks like a credential" in warns[0]
    assert E.one_line("a\n  b\t c", 3) == "a b"
    big = E._diff("", "x\n" * 30000, "f")
    assert big.endswith("(diff shortened)")
    assert E.front_matter({"name": "a", "description": "x: y # z"}, "body\n").startswith("---\nname: a\ndescription: 'x: y # z'\n---\n\nbody")
    assert E.claude_name("my_agent") == "my-agent"


# ---------------------------------------------------------------------------------------------------- MCP server entries
def test_http_servers_never_copy_credentials():
    e, env, why = E.server_entry(srv(id="github", transport="http", url="https://api.example.com/mcp/", command=None, args=[],
                                     env={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_realsecret", "HEADER_X_API_KEY": "k", "HEADER_EMPTY": "", "OTHER": "v"}), "project")
    assert e == {"type": "http", "url": "https://api.example.com/mcp/", "headers": {"Authorization": "Bearer ${AGENT_STUDIO_GITHUB_PERSONAL_ACCESS_TOKEN}", "X-Api-Key": "${AGENT_STUDIO_HEADER_X_API_KEY}"}}
    assert [v["name"] for v in env] == ["AGENT_STUDIO_GITHUB_PERSONAL_ACCESS_TOKEN", "AGENT_STUDIO_HEADER_X_API_KEY"] and why == ""
    assert "ghp_realsecret" not in json.dumps([e, env])
    e2, env2, _ = E.server_entry(srv(transport="http", url="https://api.example.com/mcp", command=None, args=[]), "project")
    assert e2 == {"type": "http", "url": "https://api.example.com/mcp"} and env2 == []
    for url in ["", "ftp://x/y", "https:///x", "https://u:p@h.example.com/x", "https://h.example.com/x?api_key=abc", "https://h.example.com/sk-" + "abcdefghijklmnopqrstuv"]:
        assert E.server_entry(srv(transport="http", url=url, command=None, args=[]), "project")[0] is None
    assert E.server_entry(srv(id="bad name", transport="http", url="https://h.example.com"), "project")[0] is None


def test_stdio_servers_become_portable_or_are_left_out():
    e, env, _ = E.server_entry(srv(id="fs", args=["-y", "fs@2", "{workspace}"]), "project")
    assert e == {"type": "stdio", "command": "npx", "args": ["-y", "fs@2", "${CLAUDE_PROJECT_DIR:-.}"]} and env == []
    assert E.server_entry(srv(args=["{workspace}"]), "user")[0]["args"] == ["."]
    e, env, _ = E.server_entry(srv(id="s", env={"API_TOKEN": "supersecretvalue", "MEMORY_FILE_PATH": "{data}/m.json", "REGION": "eu", "ROOT": "{workspace}/x", "N": "sk-" + "abcdefghijklmnopqrstuv"},
                                   requires_env=["SNYK_TOKEN"], label="Snyk"), "project")
    assert e["env"] == {"API_TOKEN": "${API_TOKEN}", "REGION": "eu", "ROOT": "${CLAUDE_PROJECT_DIR:-.}/x", "N": "${N}", "SNYK_TOKEN": "${SNYK_TOKEN}"}
    assert {v["name"] for v in env} == {"API_TOKEN", "N", "SNYK_TOKEN"} and "supersecretvalue" not in json.dumps([e, env])
    e, env, _ = E.server_entry(srv(env={"SNYK_TOKEN": "stored"}, requires_env=["SNYK_TOKEN"]), "project")
    assert e["env"] == {"SNYK_TOKEN": "${SNYK_TOKEN}"} and len(env) == 1
    for bad in [srv(command=None), srv(command="{python}"), srv(args=["{backend}"]), srv(env={"P": "{trainer_home}/x"})]:
        e, env, why = E.server_entry(bad, "project")
        assert e is None and why


def test_user_servers_are_read_only(home):
    assert E.user_servers(home) == ({}, "")
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"a": {"type": "http"}}}), "utf-8")
    assert E.user_servers(home) == ({"a": {"type": "http"}}, "")
    (home / ".claude.json").write_text(json.dumps({"mcpServers": 3}), "utf-8")
    assert E.user_servers(home) == ({}, "")
    (home / ".claude.json").write_text("[1]", "utf-8")
    assert E.user_servers(home) == ({}, "")
    (home / ".claude.json").write_text("{not json", "utf-8")
    assert "Could not read" in E.user_servers(home)[1]


# ---------------------------------------------------------------------------------------------------- the export plan
def test_export_writes_an_agent_file_with_the_right_frontmatter(state, proj, home):
    state.skills.create("note-style", "How to write notes", "Write short notes.\nKey: " + "ghp_" + "a" * 30, group="Writing")
    state.mcp.update("fetch", enabled=True)
    state.agents.create(ag(skills=["note-style"], mcp=["fetch", "gone", "d1"], tools=["read_file", "edit_file", "rag_search"], description="Does\nthings", provider="anthropic", tier="pro"))
    p = plan(state, "bot_one", proj, home)
    agent = file_named(p, ".claude/agents/bot-one.md")
    meta = yaml.safe_load(agent["content"].split("---")[1])
    assert meta == {"name": "bot-one", "description": "Does things", "tools": "Read, Edit, mcp__fetch__*", "model": "opus", "skills": ["note-style"]}
    assert agent["content"].rstrip().endswith("(agent: bot_one). Edit the agent in Agent Studio and deploy again to update this file. -->")
    skill = file_named(p, ".claude/skills/note-style/SKILL.md")
    assert skill["content"].startswith("---\nname: note-style\ndescription: How to write notes\n---") and "ghp_" not in skill["content"] and "group:" not in skill["content"]
    mcp = json.loads(file_named(p, ".mcp.json")["content"])
    assert mcp["mcpServers"]["fetch"] == {"type": "stdio", "command": "uvx", "args": ["mcp-server-fetch==2026.11.18"]} and "d1" not in mcp["mcpServers"]
    items = {x["item"]: x["why"] for x in p["not_carried"]}
    assert "Bot One: rag_search" in items and "MCP server gone" in items and items["MCP server d1"].startswith("Not copied") and "Bot One: guardrails" in items
    assert any("credential" in w for w in p["warnings"]) and p["claude_found"] is False
    assert len(p["hash"]) == 64 and p["root"].endswith(".claude")


def test_model_mapping(state, proj, home):
    state.agents.create(ag(id="a_pro", name="A", provider="anthropic", tier="pro"))
    state.agents.create(ag(id="a_flash", name="B", provider="anthropic", tier="flash"))
    state.agents.create(ag(id="a_custom", name="C", provider="anthropic", tier="custom", model="claude-opus-9"))
    state.agents.create(ag(id="a_other", name="D", provider="anthropic", tier="custom", model="gpt-x"))
    state.agents.create(ag(id="a_ollama", name="E", provider="ollama"))
    state.agents.create(ag(id="a_none", name="F"))
    out = {}
    for aid in ["a_pro", "a_flash", "a_custom", "a_other", "a_ollama", "a_none"]:
        p = plan(state, aid, proj, home, include_skills=False)
        out[aid] = yaml.safe_load(file_named(p, f"{aid.replace('_', '-')}.md")["content"].split("---")[1])["model"]
    assert out == {"a_pro": "opus", "a_flash": "sonnet", "a_custom": "claude-opus-9", "a_other": "sonnet", "a_ollama": "inherit", "a_none": "inherit"}
    p = plan(state, "a_ollama", proj, home)
    assert any("E (its provider" in w and "'inherit'" in w for w in p["warnings"])


def test_agent_without_usable_tools_gets_the_read_only_trio_and_all_skills_are_not_expanded(state, proj, home):
    state.agents.create(ag(id="a_empty", name="Empty", tools=["rag_search"], skills=["*"], instruction="", description=""))
    p = plan(state, "a_empty", proj, home)
    f = file_named(p, "a-empty.md")
    meta = yaml.safe_load(f["content"].split("---")[1])
    assert meta["tools"] == "Read, Glob, Grep" and meta["description"] == "Empty (exported from Agent Studio)" and "skills" not in meta
    assert "You are Empty." in f["content"] and not any(x["kind"] == "skill" for x in p["files"])
    assert any("read-only file tools" in w for w in p["warnings"]) and any("every Agent Studio skill" in w for w in p["warnings"])
    off = plan(state, "a_empty", proj, home, include_skills=False)
    assert not any("every Agent Studio skill" in w for w in off["warnings"])


def test_many_skills_are_copied_but_not_preloaded_and_unusable_names_are_left_out(state, proj, home):
    state.agents.create(ag(id="a_many", name="Many", skills=["group:Coding", "group:Data"]))
    p = plan(state, "a_many", proj, home)
    skills = [x for x in p["files"] if x["kind"] == "skill"]
    meta = yaml.safe_load(file_named(p, "a-many.md")["content"].split("---")[1])
    assert len(skills) > E.PRELOAD_LIMIT and "skills" not in meta and "Skill" in meta["tools"].split(", ")
    assert any("copied but not preloaded" in w for w in p["warnings"])
    d = Path(state.skills.user_dir) / "weird"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: Bad Name!\ndescription: x\ngroup: Odd\n---\nbody\n", "utf-8")
    state.agents.create(ag(id="a_weird", name="Weird", skills=["Bad Name!"]))
    p = plan(state, "a_weird", proj, home)
    assert any("unusable name" in w for w in p["warnings"]) and not any(x["kind"] == "skill" for x in p["files"])


def test_sub_agents_are_exported_together(state, proj, home, monkeypatch):
    state.agents.create(ag(id="leaf", name="Leaf", description="Leaf work", tools=["read_file"]))
    state.agents.create(ag(id="mid", name="Mid", description="", sub_agents=["leaf"], tools=["search_text"]))
    state.agents.create(ag(id="top", name="Top", description="Boss", sub_agents=["mid", "leaf"], tools=["read_file"]))
    real = state.agents.get

    def get(aid):
        a = real(aid)
        if a is not None and aid == "mid":
            a.sub_agents = ["leaf", "ghost", "top"]                  # a deleted agent and a loop back to the parent
        return a
    monkeypatch.setattr(state.agents, "get", get)
    p = plan(state, "top", proj, home, include_skills=False)
    names = [Path(f["path"]).name for f in p["files"] if f["kind"] == "agent"]
    assert names == ["top.md", "mid.md", "leaf.md"]
    top = file_named(p, "top.md")["content"]
    assert "## Sub-agents" in top and "- `mid`: Mid" in top and "- `leaf`: Leaf work" in top and yaml.safe_load(top.split("---")[1])["tools"] == "Read, Agent"
    mid = file_named(p, "mid.md")["content"]
    assert "- `leaf`: Leaf work" in mid and "`top`" not in mid
    solo = plan(state, "top", proj, home, include_skills=False, include_sub_agents=False)
    assert [Path(f["path"]).name for f in solo["files"] if f["kind"] == "agent"] == ["top.md"]
    assert any(x["item"].startswith("sub-agents (mid, leaf)") for x in solo["not_carried"])
    assert "## Sub-agents" not in file_named(solo, "top.md")["content"]


def test_only_the_first_twelve_sub_agents_are_included(state, proj, home):
    ids = [f"s{i:02d}" for i in range(14)]
    for i in ids:
        state.agents.create(ag(id=i, name=i, tools=["read_file"]))
    state.agents.create(ag(id="boss", name="Boss", sub_agents=ids))
    p = plan(state, "boss", proj, home, include_skills=False)
    assert len([f for f in p["files"] if f["kind"] == "agent"]) == 13 and any("first 12" in w for w in p["warnings"])


def test_mcp_servers_can_live_in_the_agent_file_instead(state, proj, home):
    state.agents.create(ag(mcp=["fetch"], tools=[]))
    p = plan(state, "bot_one", proj, home, mcp_placement="agent")
    meta = yaml.safe_load(file_named(p, "bot-one.md")["content"].split("---")[1])
    assert meta["mcpServers"] == [{"fetch": {"type": "stdio", "command": "uvx", "args": ["mcp-server-fetch==2026.11.18"]}}]
    assert not any(f["kind"] == "mcp-config" for f in p["files"]) and not p["commands"]


def test_filesystem_and_git_servers_are_limited_to_the_start_folder_in_user_scope(state, home):
    state.agents.create(ag(mcp=["filesystem"], tools=[]))
    p = E.build_plan(state, "bot_one", E.parse_opts({"scope": "user"}), home=home, claude_exe="C:/bin/claude.exe")
    assert any("limited to the folder Claude Code starts in" in n for n in p["notes"])
    cmd = p["commands"][0]
    assert cmd["action"] == "create" and cmd["argvs"][0][:4] == ["C:/bin/claude.exe", "mcp", "add-json", "filesystem"] and cmd["argvs"][0][-2:] == ["--scope", "user"]
    assert cmd["display"].startswith("claude mcp add-json filesystem '") and p["claude_found"] is True
    assert str(home / ".claude" / "agents" / "bot-one.md") in [f["path"] for f in p["files"]]


def test_user_scope_compares_with_existing_servers(state, home):
    state.agents.create(ag(mcp=["fetch", "memory"], tools=[]))
    same = {"type": "stdio", "command": "uvx", "args": ["mcp-server-fetch==2026.11.18"]}
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"fetch": same, "memory": {"type": "stdio", "command": "other"}}}), "utf-8")
    p = E.build_plan(state, "bot_one", E.parse_opts({"scope": "user"}), home=home, claude_exe=None)
    by = {c["name"]: c for c in p["commands"]}
    assert by["fetch"]["action"] == "unchanged" and by["fetch"]["argvs"] == [] and by["fetch"]["diff"] == ""
    assert by["memory"]["action"] == "update" and by["memory"]["argvs"][0][1:3] == ["mcp", "remove"] and by["memory"]["display"].startswith("claude mcp remove memory")
    assert by["memory"]["argvs"][0][0] == "claude" and "other" in by["memory"]["diff"]
    (home / ".claude.json").write_text("{bad", "utf-8")
    assert any("Could not read" in w for w in E.build_plan(state, "bot_one", E.parse_opts({"scope": "user"}), home=home, claude_exe=None)["warnings"])


def test_existing_files_are_never_replaced_silently(state, proj, home):
    state.agents.create(ag())
    target = proj / ".claude" / "agents" / "bot-one.md"
    target.parent.mkdir(parents=True)
    target.write_text("my own edits\n", "utf-8")
    p = plan(state, "bot_one", proj, home, include_skills=False)
    f = file_named(p, "bot-one.md")
    assert f["action"] == "update" and "my own edits" in f["diff"] and "-my own edits" in f["diff"]
    target.write_text(f["content"], "utf-8")
    assert file_named(plan(state, "bot_one", proj, home, include_skills=False), "bot-one.md")["action"] == "unchanged"
    target.write_bytes(b"\xff\xfe\x00bad")
    blocked = file_named(plan(state, "bot_one", proj, home, include_skills=False), "bot-one.md")
    assert blocked["action"] == "blocked" and "cannot be read" in blocked["note"]
    target.unlink()
    target.mkdir()
    assert file_named(plan(state, "bot_one", proj, home, include_skills=False), "bot-one.md")["action"] == "blocked"


def make_link(link: Path, target: Path) -> bool:
    """A symlink, or on Windows a junction (which needs no privilege)."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        return r.returncode == 0
    return False


def test_a_path_that_leaves_the_folder_is_blocked(state, proj, home, tmp_path):
    state.agents.create(ag())
    outside = tmp_path / "outside"
    outside.mkdir()
    link = proj / ".claude"
    if not make_link(link, outside):
        pytest.skip("neither symlinks nor junctions are allowed here")
    p = plan(state, "bot_one", proj, home, include_skills=False)
    assert file_named(p, "bot-one.md")["action"] == "blocked" and "outside" in file_named(p, "bot-one.md")["note"]


def test_mcp_json_is_merged_not_replaced(state, proj, home):
    state.agents.create(ag(mcp=["fetch"], tools=[]))
    cfg = proj / ".mcp.json"
    cfg.write_text(json.dumps({"other": 1, "mcpServers": {"mine": {"type": "http", "url": "https://x.example.com"}}}), "utf-8")
    f = file_named(plan(state, "bot_one", proj, home, include_skills=False), ".mcp.json")
    data = json.loads(f["content"])
    assert f["action"] == "update" and data["other"] == 1 and set(data["mcpServers"]) == {"mine", "fetch"} and "adds server entries" in f["note"]
    cfg.write_text(f["content"], "utf-8")
    assert file_named(plan(state, "bot_one", proj, home, include_skills=False), ".mcp.json")["action"] == "unchanged"
    data["mcpServers"]["fetch"]["args"] = ["old"]
    cfg.write_text(json.dumps(data), "utf-8")
    f = file_named(plan(state, "bot_one", proj, home, include_skills=False), ".mcp.json")
    assert f["action"] == "update" and "changes the existing server entry fetch" in f["note"]
    cfg.write_text("   ", "utf-8")
    assert file_named(plan(state, "bot_one", proj, home, include_skills=False), ".mcp.json")["action"] == "update"
    for bad in ["{oops", "[1]", json.dumps({"mcpServers": 4})]:
        cfg.write_text(bad, "utf-8")
        b = file_named(plan(state, "bot_one", proj, home, include_skills=False), ".mcp.json")
        assert b["action"] == "blocked" and "not a valid MCP configuration" in b["note"]


def test_unknown_agent_and_default_lookup(state, proj, home, monkeypatch):
    with pytest.raises(KeyError):
        E.build_plan(state, "nobody", E.parse_opts({"project_path": str(proj)}), home=home)
    state.agents.create(ag(tools=[]))
    monkeypatch.setattr(E.shutil, "which", lambda n: "C:/found/claude.exe")
    p = E.build_plan(state, "bot_one", E.parse_opts({"project_path": str(proj)}), home=home)
    assert p["claude_found"] is True


# ---------------------------------------------------------------------------------------------------- live mode plan
def test_live_plan_points_claude_code_at_the_running_agent(state, proj, home, client):
    client.put("/api/access/config", json={"api_enabled": True})
    state.agents.create(ag(id="live_one", name="Live One", description="Runs here", api_access=True))
    p = plan(state, "live_one", proj, home, mode="live")
    pre = {x["id"]: x["ok"] for x in p["prerequisites"]}
    assert pre["api"] is True and pre["agent"] is True
    f = file_named(p, "live-one-live.md")
    meta = yaml.safe_load(f["content"].split("---")[1])
    assert meta["name"] == "live-one-live" and meta["tools"] == "mcp__agent-studio-live-one__*" and meta["model"] == "inherit" and "Relay to the live Agent Studio agent" in meta["description"]
    entry = json.loads(file_named(p, ".mcp.json")["content"])["mcpServers"]["agent-studio-live-one"]
    assert entry == {"type": "http", "url": "http://127.0.0.1:8765/agent-mcp/live_one", "headers": {"Authorization": "Bearer ${AGENT_STUDIO_KEY}"}}
    assert [v["name"] for v in p["env_vars"]] == ["AGENT_STUDIO_KEY"] and "asx_" not in json.dumps(p) and len(p["notes"]) == 2
    off = plan(state, "live_one", proj, home, mode="live", mcp_placement="agent", base_url="https://studio.example.com")
    m2 = yaml.safe_load(file_named(off, "live-one-live.md")["content"].split("---")[1])
    assert m2["mcpServers"][0]["agent-studio-live-one"]["url"] == "https://studio.example.com/agent-mcp/live_one" and not off["commands"]
    state.agents.create(ag(id="closed", name="Closed"))
    cfg = client.put("/api/access/config", json={"api_enabled": False})
    assert cfg.status_code == 200
    pc = plan(state, "closed", proj, home, mode="live")
    assert {x["id"]: x["ok"] for x in pc["prerequisites"]}["api"] is False and {x["id"]: x["ok"] for x in pc["prerequisites"]}["agent"] is False
    us = E.build_plan(state, "live_one", E.parse_opts({"mode": "live", "scope": "user"}), home=home, claude_exe="claude")
    assert us["commands"][0]["name"] == "agent-studio-live-one" and "${AGENT_STUDIO_KEY}" in us["commands"][0]["display"]


# ---------------------------------------------------------------------------------------------------- writing
def apply_body(p, **kw):
    return {"confirm": True, "plan_hash": p["hash"], **kw}


def test_nothing_is_written_without_confirmation_or_with_a_stale_preview(state, proj, home):
    state.agents.create(ag(tools=[]))
    o = E.parse_opts({"project_path": str(proj)})
    p = E.build_plan(state, "bot_one", o, home=home, claude_exe=None)
    with pytest.raises(ValueError, match="confirm the preview"):
        A.apply_plan(state, "bot_one", o, {"plan_hash": p["hash"]}, home=home, claude_exe=None)
    with pytest.raises(A.PlanChanged):
        A.apply_plan(state, "bot_one", o, {"confirm": True, "plan_hash": "old"}, home=home, claude_exe=None)
    assert not (proj / ".claude").exists()
    state.agents.update("bot_one", {"instruction": "changed"})
    with pytest.raises(A.PlanChanged):
        A.apply_plan(state, "bot_one", o, apply_body(p), home=home, claude_exe=None)


def test_apply_creates_files_then_keeps_the_old_copy_when_asked_to_overwrite(state, proj, home):
    state.agents.create(ag(mcp=["fetch"], tools=["read_file"]))
    o = E.parse_opts({"project_path": str(proj), "include_skills": False})
    p = E.build_plan(state, "bot_one", o, home=home, claude_exe=None)
    r = A.apply_plan(state, "bot_one", o, apply_body(p), home=home, claude_exe=None)
    assert r["ok"] and [x["status"] for x in r["results"]] == ["written", "written"]
    target = proj / ".claude" / "agents" / "bot-one.md"
    assert target.read_text("utf-8") == file_named(p, "bot-one.md")["content"] and json.loads((proj / ".mcp.json").read_text("utf-8"))["mcpServers"]["fetch"]["command"] == "uvx"
    again = A.apply_plan(state, "bot_one", o, apply_body(E.build_plan(state, "bot_one", o, home=home, claude_exe=None)), home=home, claude_exe=None)
    assert {x["status"] for x in again["results"]} == {"unchanged"}
    state.agents.update("bot_one", {"instruction": "New words."})
    p2 = E.build_plan(state, "bot_one", o, home=home, claude_exe=None)
    skipped = A.apply_plan(state, "bot_one", o, apply_body(p2), home=home, claude_exe=None)
    assert [x["status"] for x in skipped["results"]] == ["skipped", "unchanged"] and "overwrite" in skipped["results"][0]["detail"] and "Be useful." in target.read_text("utf-8")
    done = A.apply_plan(state, "bot_one", o, apply_body(p2, overwrite=[file_named(p2, "bot-one.md")["path"]]), home=home, claude_exe=None, now=lambda: 1_800_000_000)
    assert done["results"][0]["status"] == "overwritten" and "New words." in target.read_text("utf-8")
    backup = Path(done["results"][0]["backup"])
    assert backup.is_file() and "Be useful." in backup.read_text("utf-8") and "claude_code_backups" in str(backup)
    assert not list((proj / ".claude" / "agents").glob("*.tmp"))


def test_blocked_files_are_reported_and_left_alone(state, proj, home):
    state.agents.create(ag(tools=[]))
    (proj / ".mcp.json").write_text("{bad", "utf-8")
    state.agents.update("bot_one", {"mcp": ["fetch"]})
    o = E.parse_opts({"project_path": str(proj)})
    p = E.build_plan(state, "bot_one", o, home=home, claude_exe=None)
    r = A.apply_plan(state, "bot_one", o, apply_body(p), home=home, claude_exe=None)
    assert {x["status"] for x in r["results"]} == {"written", "skipped"} and (proj / ".mcp.json").read_text("utf-8") == "{bad"


def test_write_refuses_paths_outside_the_folder_and_does_not_overwrite_a_new_file(tmp_path, proj):
    outside = tmp_path / "elsewhere" / "f.md"
    assert A._write(outside, "x", proj, False, 0)["status"] == "skipped" and not outside.exists()
    ok = proj / "a" / "f.md"
    assert A._write(ok, "x", proj, False, 0)["status"] == "written"
    raced = A._write(ok, "y", proj, False, 0)
    assert raced["status"] == "skipped" and "appeared" in raced["detail"] and ok.read_text("utf-8") == "x"
    link = proj / "ln"
    try:
        link.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    except (OSError, NotImplementedError):
        return
    assert A._write(link / "g.md", "x", proj, False, 0)["status"] == "skipped"
    (tmp_path / "elsewhere").mkdir(exist_ok=True)
    flink = proj / "flink.md"
    try:
        flink.symlink_to(tmp_path / "elsewhere" / "t.md")
    except (OSError, NotImplementedError):
        return
    assert A._write(flink, "x", proj, False, 0)["status"] == "skipped"


def test_a_parent_that_resolves_outside_is_refused_after_it_is_created(tmp_path, proj, monkeypatch):
    calls = {"n": 0}

    def inside(root, path):
        calls["n"] += 1
        return calls["n"] == 1                      # the file path looks fine, its parent does not
    monkeypatch.setattr(A, "inside", inside)
    r = A._write(proj / "x" / "f.md", "z", proj, False, 0)
    assert r["status"] == "skipped" and "outside" in r["detail"]


class Result:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_user_scope_commands_run_only_when_asked_and_only_for_approved_changes(state, home):
    state.agents.create(ag(mcp=["fetch", "memory"], tools=[]))
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"memory": {"type": "stdio", "command": "other"}}}), "utf-8")
    o = E.parse_opts({"scope": "user"})
    exe = "C:/bin/claude.exe"
    seen: list[list[str]] = []

    def fresh():
        return E.build_plan(state, "bot_one", o, home=home, claude_exe=exe)

    def run(argv, **kw):
        seen.append(argv)
        assert kw["capture_output"] and kw["timeout"] == A.TIMEOUT and not kw.get("shell")
        return Result(0, "Added")
    not_run = A.apply_plan(state, "bot_one", o, apply_body(fresh()), home=home, claude_exe=exe, run=run)
    assert [x["status"] for x in not_run["results"] if "command" in x] == ["skipped", "skipped"] and not seen
    ran = A.apply_plan(state, "bot_one", o, apply_body(fresh(), run_commands=True), home=home, claude_exe=exe, run=run)
    cmds = {x["command"]: x for x in ran["results"] if "command" in x}
    assert cmds["fetch"]["status"] == "done" and cmds["memory"]["status"] == "skipped" and "overwrite" in cmds["memory"]["detail"] and len(seen) == 1 and seen[0][1:3] == ["mcp", "add-json"]
    seen.clear()
    ran = A.apply_plan(state, "bot_one", o, apply_body(fresh(), run_commands=True, overwrite=["memory"]), home=home, claude_exe=exe, run=run)
    assert [a[2] for a in seen if a[1] == "mcp"][:3] == ["add-json", "remove", "add-json"]
    nofound = A.apply_plan(state, "bot_one", o, apply_body(E.build_plan(state, "bot_one", o, home=home, claude_exe=None), run_commands=True), home=home, claude_exe=None, run=run)
    assert any("not found" in x["detail"] for x in nofound["results"])


def test_command_failures_are_reported(state, home):
    state.agents.create(ag(mcp=["fetch"], tools=[]))
    o = E.parse_opts({"scope": "user"})
    for res, want in [(Result(1, "", "boom"), "boom"), (Result(2, "", ""), "exit code 2"), (OSError("gone"), "OSError"), (subprocess.TimeoutExpired("c", 1), "TimeoutExpired")]:
        def run(argv, **kw):
            if isinstance(res, Exception):
                raise res
            return res
        p = E.build_plan(state, "bot_one", o, home=home, claude_exe="claude")
        out = A.apply_plan(state, "bot_one", o, apply_body(p, run_commands=True), home=home, claude_exe="claude", run=run)
        failed = next(x for x in out["results"] if x.get("command") == "fetch")
        assert out["ok"] is False and failed["status"] == "failed" and want in failed["detail"]


def test_unchanged_servers_are_not_run(state, home):
    state.agents.create(ag(mcp=["fetch"], tools=[]))
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"fetch": {"type": "stdio", "command": "uvx", "args": ["mcp-server-fetch==2026.11.18"]}}}), "utf-8")
    o = E.parse_opts({"scope": "user"})
    p = E.build_plan(state, "bot_one", o, home=home, claude_exe="claude")
    out = A.apply_plan(state, "bot_one", o, apply_body(p, run_commands=True), home=home, claude_exe="claude", run=lambda *a, **k: pytest.fail("must not run"))
    assert next(x for x in out["results"] if x.get("command") == "fetch")["status"] == "unchanged"


# ---------------------------------------------------------------------------------------------------- HTTP routes
def test_routes_plan_and_apply(client, proj, tmp_path, monkeypatch):
    monkeypatch.setattr(E.Path, "home", staticmethod(lambda: tmp_path / "userhome"))
    st = client.get("/api/claude-code/status").json()
    assert set(st) == {"claude_found", "live_path"}
    client.post("/api/agents", json={"id": "web_bot", "name": "Web Bot", "description": "d", "tier": "flash", "tools": ["read_file"]})
    body = {"project_path": str(proj), "include_skills": False}
    assert client.post("/api/agents/nobody/claude-code/plan", json=body).status_code == 404
    assert client.post("/api/agents/web_bot/claude-code/plan", json={"mode": "bad"}).status_code == 422
    p = client.post("/api/agents/web_bot/claude-code/plan", json=body)
    assert p.status_code == 200 and p.json()["files"][0]["action"] == "create"
    assert client.post("/api/agents/nobody/claude-code/apply", json=body).status_code == 404
    assert client.post("/api/agents/web_bot/claude-code/apply", json={**body, "mode": "bad"}).status_code == 422
    assert client.post("/api/agents/web_bot/claude-code/apply", json={**body, "confirm": True, "plan_hash": "stale"}).status_code == 409
    assert client.post("/api/agents/web_bot/claude-code/apply", json=body).status_code == 422
    ok = client.post("/api/agents/web_bot/claude-code/apply", json={**body, "confirm": True, "plan_hash": p.json()["hash"]})
    assert ok.status_code == 200 and ok.json()["ok"] and (proj / ".claude" / "agents" / "web-bot.md").is_file()


# ---------------------------------------------------------------------------------------------------- the live agent as an MCP server
@pytest.fixture
def live(client, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    client.put("/api/access/config", json={"api_enabled": True})
    r = client.post("/api/agents", json={"id": "relay", "name": "Relay", "description": "Echoes", "tier": "flash", "provider": "mock", "api_access": True})
    assert r.status_code == 200, r.text
    key = client.post("/api/access/keys", json={"name": "cc", "agents": ["relay"]}).json()["key"]
    return client, {"Authorization": f"Bearer {key}"}


def rpc(c, headers, method, params=None, mid=1, agent="relay", **kw):
    body = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {}), **({} if mid is None else {"id": mid})}
    return c.post(f"/agent-mcp/{agent}", json=body, headers=headers, **kw)


def test_mcp_endpoint_needs_a_key_a_local_caller_and_an_opted_in_agent(live, client):
    c, h = live
    assert c.post("/agent-mcp/relay", json={}).status_code == 401
    assert c.post("/agent-mcp/relay", json={}, headers={**h, "Origin": "https://evil.example"}).status_code == 403
    assert rpc(c, {**h, "Origin": "http://127.0.0.1:3100"}, "ping").status_code == 200
    assert rpc(c, h, "ping", agent="other").status_code == 404
    c.put("/api/access/config", json={"api_enabled": False})
    assert rpc(c, h, "ping").status_code == 403


def test_mcp_endpoint_speaks_json_rpc(live):
    c, h = live
    init = rpc(c, h, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}).json()["result"]
    assert init["protocolVersion"] == "2025-03-26" and init["capabilities"] == {"tools": {}} and init["serverInfo"]["name"] == "agent-studio-relay"
    assert rpc(c, h, "initialize", {"protocolVersion": "junk"}).json()["result"]["protocolVersion"] == E_LATEST()
    assert rpc(c, h, "initialize", []).json()["result"]["protocolVersion"] == E_LATEST()
    assert rpc(c, h, "notifications/initialized", mid=None).status_code == 202
    assert c.post("/agent-mcp/relay", json={"jsonrpc": "2.0", "id": 4, "result": {}}, headers=h).status_code == 202
    assert rpc(c, h, "ping").json()["result"] == {}
    tool = rpc(c, h, "tools/list").json()["result"]["tools"]
    assert [t["name"] for t in tool] == ["ask"] and "Echoes" in tool[0]["description"] and tool[0]["inputSchema"]["required"] == ["message"]
    assert rpc(c, h, "resources/list").json()["error"]["code"] == -32601
    bad = c.post("/agent-mcp/relay", content="not json", headers=h)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == -32700
    for body in ([1], {"id": 1, "method": "ping"}):
        r = c.post("/agent-mcp/relay", json=body, headers=h)
        assert r.status_code == 400 and r.json()["error"]["code"] == -32600
    assert c.get("/agent-mcp/relay", headers=h).status_code == 405


def E_LATEST():
    from app.claude_code import mcp_endpoint
    return mcp_endpoint.LATEST


def test_mcp_endpoint_asks_the_agent(live, monkeypatch):
    c, h = live
    ok = rpc(c, h, "tools/call", {"name": "ask", "arguments": {"message": "hello there"}}).json()["result"]
    assert ok["isError"] is False and "hello there" in ok["content"][0]["text"]
    for params in (None, {"name": "other", "arguments": {"message": "x"}}, {"name": "ask", "arguments": {}}, {"name": "ask", "arguments": {"message": "  "}},
                   {"name": "ask", "arguments": {"message": "x" * 20_001}}, {"name": "ask", "arguments": "nope"}, []):
        r = rpc(c, h, "tools/call", params)
        assert r.json()["error"]["code"] == -32602

    async def halted(*a, **k):
        return {"reply": "", "halted": True}
    c.app.state.s.chat.send = halted
    r = rpc(c, h, "tools/call", {"name": "ask", "arguments": {"message": "hi"}}).json()["result"]
    assert r["isError"] is False and "(The agent stopped" in r["content"][0]["text"]

    async def boom(*a, **k):
        raise RuntimeError("model down")
    c.app.state.s.chat.send = boom
    r = rpc(c, h, "tools/call", {"name": "ask", "arguments": {"message": "hi"}}).json()["result"]
    assert r["isError"] is True and r["content"][0]["text"]


def test_a_shared_skill_and_a_shared_secret_are_written_or_listed_once(state, proj, home):
    state.skills.create("shared-skill", "Shared", "Body.")
    state.mcp.update("snyk", enabled=True, env={"SNYK_TOKEN": "real-stored-secret-value"})
    state.agents.create(ag(id="kid", name="Kid", skills=["shared-skill"], mcp=["snyk"], tools=[]))
    state.agents.create(ag(id="parent", name="Parent", skills=["shared-skill"], mcp=["snyk"], sub_agents=["kid"], tools=[]))
    p = plan(state, "parent", proj, home)
    assert len([f for f in p["files"] if f["kind"] == "skill"]) == 1
    assert [v["name"] for v in p["env_vars"]] == ["SNYK_TOKEN"] and "real-stored-secret-value" not in json.dumps(p)
    assert json.loads(file_named(p, ".mcp.json")["content"])["mcpServers"]["snyk"]["env"] == {"SNYK_TOKEN": "${SNYK_TOKEN}"}
