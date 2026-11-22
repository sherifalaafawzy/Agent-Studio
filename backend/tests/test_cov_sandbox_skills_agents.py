"""Coverage for app/mcp_sandbox.py, app/mock_llm.py, app/skills.py and app/agents.py edge branches.
The Docker sandbox is replaced by a fake object; no container is started."""
import runpy
import sys

import pytest

from app import mcp_sandbox as MS
from app import mock_llm
from app.agents import AgentDef
from app.harness.sandbox import Sandbox, SandboxUnavailable
from app.skills import SkillRegistry, parse_skill


class FakeBox:
    def __init__(self, workspace, reply=None):
        self.workspace = workspace
        self.network = False
        self.reply = reply or dict(exit_code=0, stdout="ran\n", stderr="", timed_out=False, truncated=False)
        self.commands = []

    def exec(self, command, timeout_s=120):
        self.commands.append((command, timeout_s))
        return dict(self.reply)


# ---------- mcp_sandbox ----------

def test_tools_report_unavailable_when_not_configured(monkeypatch):
    monkeypatch.setattr(MS, "_sb", None)
    r = MS.run_command("ls")
    assert r["ok"] is False and r["error"].startswith("sandbox unavailable: sandbox not configured")
    r = MS.run_python("print(1)")
    assert r["ok"] is False and "not configured" in r["error"]


def test_run_command_maps_exit_and_timeout(monkeypatch, tmp_path):
    box = FakeBox(tmp_path)
    monkeypatch.setattr(MS, "_sb", box)
    ok = MS.run_command("echo hi", 7)
    assert ok["ok"] is True and ok["stdout"] == "ran\n" and box.commands == [("echo hi", 7)]
    box.reply = dict(exit_code=0, stdout="", stderr="", timed_out=True, truncated=False)
    assert MS.run_command("sleep 9")["ok"] is False                   # timed out counts as failure
    box.reply = dict(exit_code=1, stdout="", stderr="bad", timed_out=False, truncated=False)
    assert MS.run_command("false")["ok"] is False


def test_run_python_writes_snippet_and_executes_it(monkeypatch, tmp_path):
    box = FakeBox(tmp_path)
    monkeypatch.setattr(MS, "_sb", box)
    r = MS.run_python("print('ran')", 30)
    assert r["ok"] is True
    cmd, timeout = box.commands[-1]
    assert cmd.startswith("python3 .sandbox-runs/run-") and cmd.endswith(".py") and timeout == 30
    saved = tmp_path / cmd.split(" ", 1)[1]
    assert saved.read_text(encoding="utf-8") == "print('ran')"


def test_run_python_reports_sandbox_failure(monkeypatch, tmp_path):
    class Down(FakeBox):
        def exec(self, command, timeout_s=120):
            raise SandboxUnavailable("docker CLI not found")

    monkeypatch.setattr(MS, "_sb", Down(tmp_path))
    r = MS.run_python("x = 1")
    assert r == {"ok": False, "error": "sandbox unavailable: docker CLI not found"}


def test_sandbox_status_describes_configured_box(monkeypatch, tmp_path):
    monkeypatch.setattr(MS, "status", lambda: dict(docker=True, image=True, detail="ready"))
    box = Sandbox(tmp_path, network=True)
    monkeypatch.setattr(MS, "_sb", box)
    st = MS.sandbox_status()
    assert st["docker"] is True and st["network"] is True and st["workspace"] == str(tmp_path.resolve())


def test_mcp_sandbox_main_configures_box_and_runs_server(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        MS.main([])
    assert e.value.code == 2 and "usage:" in capsys.readouterr().err

    ran = []
    monkeypatch.setattr(MS.server, "run", lambda transport: ran.append(transport))
    monkeypatch.setattr(MS, "_sb", None)
    MS.main([str(tmp_path), "--network"])
    try:
        assert ran == ["stdio"]
        assert MS._sb.network is True and MS._sb.workspace == tmp_path.resolve()
    finally:
        monkeypatch.setattr(MS, "_sb", None)


def test_mcp_sandbox_module_entrypoint(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["app.mcp_sandbox"])
    with pytest.raises(SystemExit) as e:
        runpy.run_module("app.mcp_sandbox", run_name="__main__")
    assert e.value.code == 2


# ---------- mock_llm ----------

def test_script_parses_tool_and_agent_steps():
    text = "\n".join([
        "please do this",
        '!tool read_file {"path": "a.py"}',
        "  !agent explorer {\"request\": \"list\"}  ",
        "!tool list_dir",                                # no args -> empty dict
        "!tool edit_file {not json",                     # bad json kept as _bad_json
        "!bogus thing {}",                               # unknown kind ignored
        "!tool",                                         # too short ignored
    ])
    steps = mock_llm._script(text)
    assert steps == [
        ("read_file", {"path": "a.py"}),
        ("explorer", {"request": "list"}),
        ("list_dir", {}),
        ("edit_file", {"_bad_json": "{not json"}),
    ]


def test_script_returns_none_for_plain_chat():
    assert mock_llm._script("hello there") is None
    assert mock_llm._script("!nothing useful") is None


def test_usage_metadata_is_never_zero():
    u = mock_llm._usage(0, 0)
    assert u.prompt_token_count == 1 and u.candidates_token_count == 1 and u.total_token_count == 2


# ---------- skills ----------

def test_parse_skill_requires_frontmatter():
    with pytest.raises(ValueError, match="YAML frontmatter"):
        parse_skill("no frontmatter here", "x")


def test_parse_skill_accepts_comma_separated_mcp_string():
    s = parse_skill("---\nname: combo\ndescription: Does combo.\nmcp: git, fetch ,\n---\nbody", "fallback")
    assert s.name == "combo" and s.mcp == ["git", "fetch"] and s.body == "body" and s.description == "Does combo."


def test_registry_skips_malformed_and_missing_dirs(tmp_path):
    builtin = tmp_path / "builtin"
    (builtin / "good").mkdir(parents=True)
    (builtin / "good" / "SKILL.md").write_text("---\ndescription: ok\n---\nbody", encoding="utf-8")
    (builtin / "broken").mkdir()
    (builtin / "broken" / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    (builtin / "stray.txt").write_text("not a folder", encoding="utf-8")
    reg = SkillRegistry(builtin_dir=builtin, user_dir=tmp_path / "user")
    assert [s.name for s in reg.list()] == ["good"]
    absent = SkillRegistry(builtin_dir=tmp_path / "nope", user_dir=tmp_path / "user2")
    assert absent.list() == []
    assert absent.index_text(["*"]) == ""


def test_create_rejects_oversize_body_and_duplicate_user_skill(tmp_path):
    reg = SkillRegistry(builtin_dir=tmp_path / "b", user_dir=tmp_path / "u")
    with pytest.raises(ValueError, match="too large"):
        reg.create("big-skill", "desc", "x" * 30_001)
    reg.create("mine-skill", "desc", "body")
    with pytest.raises(ValueError, match="already exists"):
        reg.create("mine-skill", "desc", "body2")
    assert reg.create("mine-skill", "desc", "body2", overwrite=True).body == "body2"


def test_delete_user_skill_and_guard_rails(tmp_path):
    reg = SkillRegistry(builtin_dir=tmp_path / "b", user_dir=tmp_path / "u")
    with pytest.raises(KeyError):
        reg.delete("ghost-skill")
    reg.create("temp-skill", "desc", "body")
    reg.delete("temp-skill")
    assert reg.get("temp-skill") is None and not (tmp_path / "u" / "temp-skill").exists()

    builtin = tmp_path / "bi"
    (builtin / "core-skill").mkdir(parents=True)
    (builtin / "core-skill" / "SKILL.md").write_text("---\ndescription: core\n---\nbody", encoding="utf-8")
    reg2 = SkillRegistry(builtin_dir=builtin, user_dir=tmp_path / "u2")
    with pytest.raises(ValueError, match="cannot be deleted"):
        reg2.delete("core-skill")


def test_index_text_for_wildcard_and_explicit_lists(tmp_path):
    reg = SkillRegistry(builtin_dir=tmp_path / "b", user_dir=tmp_path / "u")
    reg.create("alpha-skill", "Alpha does a.", "a")
    reg.create("beta-skill", "Beta does b.", "b")
    assert reg.index_text(["beta-skill"]).count("- ") == 1
    assert "- alpha-skill: Alpha does a." in reg.index_text(["*"])
    assert reg.index_text(["nothing-matches"]) == ""


# ---------- agents ----------

def _store(state):
    return state.agents


def test_agent_id_name_tier_and_model_validation(state):
    s = _store(state)
    with pytest.raises(ValueError, match="id must be"):
        s.create(AgentDef(id="Bad-Id", name="x"))
    with pytest.raises(ValueError, match="name is required"):
        s.create(AgentDef(id="okname", name="   "))
    with pytest.raises(ValueError, match="tier must be"):
        s.create(AgentDef(id="okname", name="x", tier="gold"))
    with pytest.raises(ValueError, match="needs a model"):
        s.create(AgentDef(id="okname", name="x", tier="custom"))
    s.create(AgentDef(id="custom_ok", name="x", tier="custom", model="openai/gpt-x"))
    assert s.get("custom_ok").model == "openai/gpt-x"


def test_agent_sub_agent_rules(state):
    s = _store(state)
    with pytest.raises(ValueError, match="own sub-agent"):
        s.create(AgentDef(id="selfish", name="S", sub_agents=["selfish"]))
    with pytest.raises(ValueError, match="unknown sub-agent"):
        s.create(AgentDef(id="parent1", name="P", sub_agents=["ghost_agent"]))


def test_cycle_detection_handles_shared_children(state):
    s = _store(state)
    s.create(AgentDef(id="leafz", name="Leaf"))
    s.create(AgentDef(id="midx", name="X", sub_agents=["leafz"]))
    s.create(AgentDef(id="midy", name="Y", sub_agents=["leafz"]))
    # diamond: leafz is reachable twice; validation must still accept it (no cycle)
    top = s.create(AgentDef(id="topdia", name="Top", sub_agents=["midx", "midy"]))
    assert top.sub_agents == ["midx", "midy"]
    # closing the loop leafz -> topdia is a cycle through the diamond
    with pytest.raises(ValueError, match="cycle"):
        s.update("leafz", {"sub_agents": ["topdia"]})


def test_update_and_delete_unknown_agents(state):
    s = _store(state)
    with pytest.raises(KeyError):
        s.update("no_such_agent", {"name": "x"})
    with pytest.raises(KeyError):
        s.delete("no_such_agent")


def test_cannot_delete_agent_still_used_as_sub_agent(state):
    s = _store(state)
    s.create(AgentDef(id="helper1", name="Helper"))
    s.create(AgentDef(id="boss1", name="Boss", sub_agents=["helper1"]))
    with pytest.raises(ValueError, match="still used as a sub-agent"):
        s.delete("helper1")
    s.delete("boss1")
    s.delete("helper1")
    assert s.get("helper1") is None
