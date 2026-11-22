"""Skill groups, group attachment on agents, duplicate / import / export, and the 'used by' list."""
import pytest

from app.skills import SkillRegistry, clean_group, covers, parse_skill, render_skill

BUILTIN_GROUPS = {
    "code-quality": "Coding", "code-review": "Coding", "debugging": "Coding", "repo-orientation": "Coding",
    "test-writing": "Coding", "git-workflow": "Ops", "security-audit": "Security", "research-and-summarize": "Research",
    "plan-and-delegate": "General", "delegate-to-external-agents": "General", "skill-creator": "General",
    "time-series-forecasting": "Data", "ai-bom-and-discovery": "Security",
}


def reg(tmp_path, builtin=None):
    return SkillRegistry(builtin_dir=builtin or tmp_path / "b", user_dir=tmp_path / "u")


# ---------- model ----------
def test_clean_group_normalises_and_validates():
    assert clean_group(None) == "General" and clean_group("   ") == "General"
    assert clean_group("  Dev   Ops ") == "Dev Ops"
    for bad in ("1abc", "has:colon", "x" * 25, "semi;colon"):
        with pytest.raises(ValueError):
            clean_group(bad)


def test_group_defaults_and_roundtrips_through_frontmatter():
    assert parse_skill("---\nname: a-skill\ndescription: d\n---\nbody", "x").group == "General"
    assert parse_skill("---\nname: a-skill\ndescription: d\ngroup: Security\n---\nbody", "x").group == "Security"
    assert parse_skill("---\nname: a-skill\ndescription: d\ngroup: '9 bad'\n---\nbody", "x").group == "General"   # bad label never hides it
    s = parse_skill("---\nname: a-skill\ndescription: d\ngroup: Data\nmcp: [git]\n---\nbody", "x")
    assert parse_skill(render_skill(s), "x") == s
    assert "group: Data" in render_skill(s)


def test_builtin_groups_are_assigned(tmp_path):
    got = {s.name: s.group for s in SkillRegistry(user_dir=tmp_path / "u").list() if s.source == "builtin"}
    from tests.test_catalog import DOMAIN_GROUPS
    assert {k: v for k, v in got.items() if v not in DOMAIN_GROUPS or k in BUILTIN_GROUPS} == BUILTIN_GROUPS      # the domain skills are covered by test_catalog


def test_create_validates_group_and_defaults(tmp_path):
    r = reg(tmp_path)
    assert r.create("plain-skill", "d", "b").group == "General"
    assert r.create("sec-skill", "d", "b", group=" Security ").group == "Security"
    with pytest.raises(ValueError):
        r.create("bad-group", "d", "b", group="no:way")


def test_groups_listing_counts_and_case_insensitive_merge(tmp_path):
    b = tmp_path / "b"
    (b / "core").mkdir(parents=True)
    (b / "core" / "SKILL.md").write_text("---\nname: core\ndescription: c\ngroup: Security\n---\nx", "utf-8")
    r = reg(tmp_path, b)
    r.create("mine-a", "d", "b", group="security")
    r.create("mine-b", "d", "b", group="Finance")
    r.create("mine-c", "d", "b", group="finance")
    g = {x["name"]: x for x in r.groups()}
    assert g["Security"] == dict(name="Security", count=2, builtin=1, user=1)
    assert g["Finance"]["count"] == 2 and g["Coding"]["count"] == 0          # suggested groups always listed
    assert [x["name"] for x in r.groups()][-1] == "General"


# ---------- resolution ----------
def test_covers_and_resolve_by_group_name_and_star(tmp_path):
    r = reg(tmp_path)
    r.create("sec-one", "d", "b", ["semgrep"], group="Security")
    r.create("sec-two", "d", "b", ["snyk"], group="Security")
    r.create("other", "d", "b", ["git"], group="Coding")
    names = lambda al: [s.name for s in r.resolve(al)]
    assert names(["group:Security"]) == ["sec-one", "sec-two"]
    assert names(["GROUP:security"]) == ["sec-one", "sec-two"]
    assert names(["group:Security", "other"]) == ["other", "sec-one", "sec-two"]
    assert names(["*"]) == ["other", "sec-one", "sec-two"] and names([]) == [] and names(["group:Nothing"]) == []
    # a skill added to the group later is picked up with no change to the agent
    r.create("sec-three", "d", "b", group="Security")
    assert names(["group:Security"]) == ["sec-one", "sec-three", "sec-two"]
    assert covers(["sec-one"], r.get("sec-one")) and not covers(["sec-one"], r.get("other"))


def test_index_text_and_linked_mcp_follow_groups(tmp_path):
    r = reg(tmp_path)
    r.create("sec-one", "Scan stuff.", "b", ["semgrep"], group="Security")
    r.create("other", "Other stuff.", "b", ["git"], group="Coding")
    assert "- sec-one: Scan stuff." in r.index_text(["group:Security"]) and "other" not in r.index_text(["group:Security"])
    assert r.linked_mcp(["group:Security"]) == ["semgrep"]
    assert r.linked_mcp(["*"]) == []                                         # "*" alone never auto-links MCP servers
    assert r.linked_mcp(["*", "other"]) == ["git"]


# ---------- duplicate / import ----------
def test_duplicate_builtin_into_editable_copy(tmp_path):
    r = SkillRegistry(user_dir=tmp_path / "u")
    c = r.duplicate("security-audit")
    assert c.name == "security-audit-custom" and c.source == "user" and c.group == "Security"
    assert c.body == r.get("security-audit").body and c.mcp == ["semgrep", "snyk"]
    assert r.duplicate("security-audit", "audit-v2").name == "audit-v2"
    with pytest.raises(ValueError):                                          # the copy already exists
        r.duplicate("security-audit")
    with pytest.raises(KeyError):
        r.duplicate("no-such-skill")
    long = r.duplicate("delegate-to-external-agents")                        # name is clipped to a legal length
    assert len(long.name) <= 41 and r.get(long.name)


def test_import_text_variants(tmp_path):
    r = reg(tmp_path)
    text = "---\nname: imported-one\ndescription: Use when importing.\ngroup: Data\nmcp: [fetch]\n---\n\n# Body\n"
    s = r.import_text(text)
    assert (s.name, s.group, s.mcp, s.source) == ("imported-one", "Data", ["fetch"], "user")
    with pytest.raises(ValueError):
        r.import_text(text)                                                  # exists
    assert r.import_text(text.replace("Body", "New"), overwrite=True).body == "# New"
    assert r.import_text(text, name="renamed-one").name == "renamed-one"
    with pytest.raises(ValueError):
        r.import_text("no frontmatter")
    with pytest.raises(ValueError):
        r.import_text("---\ndescription: nameless\n---\nbody")               # no name anywhere
    with pytest.raises(ValueError):
        r.import_text("---\nname: huge-one\ndescription: d\n---\n" + "x" * 40_000)


# ---------- agent validation ----------
def test_agent_skills_group_reference_is_validated(client):
    ok = client.post("/api/agents", json={"id": "grp_agent", "name": "G", "skills": ["group:Security", "debugging"]})
    assert ok.status_code == 200 and ok.json()["skills"] == ["group:Security", "debugging"]
    for bad in ("group:", "group:9x", "group:a:b"):
        assert client.post("/api/agents", json={"id": "grp_bad", "name": "G", "skills": [bad]}).status_code == 422


# ---------- API ----------
def test_api_group_field_listing_and_validation(client):
    body = {"name": "pen-notes", "description": "when pentesting", "body": "## x", "group": "Security"}
    r = client.post("/api/skills", json=body)
    assert r.status_code == 200 and r.json()["group"] == "Security" and r.json()["used_by"] == ["engineer", "super_engineer"]
    assert client.post("/api/skills", json={**body, "name": "pen-notes-2", "group": "bad:group"}).status_code == 422
    assert client.post("/api/skills", json={k: v for k, v in body.items() if k != "group"} | {"name": "no-group"}).json()["group"] == "General"
    listing = client.get("/api/skills").json()
    assert next(s for s in listing if s["name"] == "debugging")["group"] == "Coding"
    g = {x["name"]: x for x in client.get("/api/skill-groups").json()}
    assert g["Security"]["count"] == 3 and g["Security"]["builtin"] == 2 and g["Security"]["user"] == 1
    assert client.get("/api/skills/pen-notes").json()["group"] == "Security"


def test_api_used_by_lists_agents_by_name_group_and_star(client):
    client.post("/api/agents", json={"id": "by_name", "name": "N", "skills": ["pen-notes"]})
    client.post("/api/agents", json={"id": "by_group", "name": "G", "skills": ["group:security"]})
    client.post("/api/agents", json={"id": "by_none", "name": "X", "skills": ["debugging"]})
    client.post("/api/skills", json={"name": "pen-notes", "description": "d", "body": "b", "group": "Security"})
    used = client.get("/api/skills/pen-notes").json()["used_by"]
    assert {"by_name", "by_group", "engineer"} <= set(used) and "by_none" not in used          # engineer has "*"
    assert next(s for s in client.get("/api/skills").json() if s["name"] == "pen-notes")["used_by"] == used
    # builtin security-audit is reached by the security agent's explicit list and by the group agent
    assert "by_group" in client.get("/api/skills/security-audit").json()["used_by"]


def test_api_export_import_roundtrip(client):
    client.post("/api/skills", json={"name": "to-share", "description": "Use when sharing.", "body": "## Steps\n1. go",
                                     "mcp": ["git"], "group": "Writing"})
    r = client.get("/api/skills/to-share/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert 'filename="to-share-SKILL.md"' in r.headers["content-disposition"]
    assert "group: Writing" in r.text and "## Steps" in r.text
    client.delete("/api/skills/to-share")
    back = client.post("/api/skills/import", json={"text": r.text})
    assert back.status_code == 200 and back.json()["group"] == "Writing" and back.json()["mcp"] == ["git"]
    assert client.post("/api/skills/import", json={"text": r.text}).status_code == 422            # exists
    assert client.post("/api/skills/import", json={"text": r.text, "overwrite": True}).status_code == 200
    assert client.post("/api/skills/import", json={"text": r.text, "name": "copy-of-it"}).json()["name"] == "copy-of-it"
    assert client.post("/api/skills/import", json={"text": "garbage"}).status_code == 422
    assert client.get("/api/skills/ghost/export").status_code == 404
    assert "name: debugging" in client.get("/api/skills/debugging/export").text                   # builtins export too


def test_api_duplicate(client):
    r = client.post("/api/skills/code-review/duplicate")
    assert r.status_code == 200 and r.json()["name"] == "code-review-custom" and r.json()["source"] == "user"
    assert r.json()["group"] == "Coding" and r.json()["body"]
    assert client.post("/api/skills/code-review/duplicate").status_code == 422
    assert client.post("/api/skills/code-review/duplicate", json={"name": "my-review"}).json()["name"] == "my-review"
    assert client.post("/api/skills/ghost/duplicate").status_code == 404
    assert client.post("/api/skills/code-review/duplicate", json={"name": "Bad Name"}).status_code == 422


def test_create_skill_tool_accepts_group(state, tmp_path):
    import asyncio
    from app.harness.sandbox import Sandbox
    from app.harness.tools import ToolContext, build_tools
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(ws, Sandbox(ws), SkillRegistry(user_dir=tmp_path / "us"), state.mcp)
    t = {f.__name__: f for f in build_tools(ctx, ["create_skill"])}
    r = asyncio.run(t["create_skill"]("grouped-skill", "d", "b", None, False, "Research"))
    assert r["ok"] and r["skill"]["group"] == "Research"
    assert not asyncio.run(t["create_skill"]("grouped-bad", "d", "b", None, False, "no:pe"))["ok"]
