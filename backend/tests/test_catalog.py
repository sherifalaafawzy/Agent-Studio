"""Catalog: domain skills, the vetted public MCP list (install / read-only / confirm), cloud-ML account settings, AI-BOM."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import catalog_api as CA
from app import mcp_catalog as MC
from app.skills import SkillRegistry, parse_skill

SKILLS = Path(__file__).resolve().parents[2] / "skills"
DOMAIN_GROUPS = list(CA.GROUP_DOMAIN)
BUILTIN_MCP = {"filesystem", "fetch", "git", "memory", "elevenlabs", "github", "playwright", "brave", "code-sandbox", "d1", "timeseries",
               "trainer", "cloudml", "semgrep", "eslint", "snyk", "context7"}


# ---------------------------------------------------------------------------------------------------------------- skills
def test_every_builtin_skill_file_parses():
    """A SKILL.md with broken front matter is silently dropped by the registry - this makes it loud."""
    for p in sorted(SKILLS.glob("*/SKILL.md")):
        s = parse_skill(p.read_text(encoding="utf-8"), p.parent.name)
        assert s.name == p.parent.name and len(s.description) > 40 and len(s.body) > 200, p.parent.name
    assert len(SkillRegistry().list()) == len(list(SKILLS.glob("*/SKILL.md")))


def test_each_domain_has_skills_with_a_when_to_use_and_safety_note():
    by_group: dict[str, list] = {}
    for s in SkillRegistry().list():
        by_group.setdefault(s.group, []).append(s)
    for g in DOMAIN_GROUPS:
        assert len(by_group.get(g, [])) >= 3, f"{g} needs at least three skills"
    for g in DOMAIN_GROUPS:
        for s in by_group[g]:
            if s.name in ("decision-model", "time-series-forecasting", "ai-bom-and-discovery"):
                continue
            assert "When to use" in s.body and "Safety" in s.body, f"{s.name}: needs a 'When to use' and a 'Safety' paragraph"


def test_skill_mcp_references_point_at_real_servers():
    known = {e["id"] for e in MC.CATALOG} | BUILTIN_MCP
    for s in SkillRegistry().list():
        assert set(s.mcp) <= known, f"{s.name} references unknown MCP server(s): {sorted(set(s.mcp) - known)}"


def test_catalog_skills_api_lists_domain_skills_and_who_uses_them(client):
    rows = client.get("/api/catalog/skills").json()
    names = {r["name"]: r for r in rows}
    assert "bank-reconciliation" in names and names["bank-reconciliation"]["domain"] == "accounting"
    assert all(r["domain"] in {d["id"] for d in MC.DOMAINS} for r in rows)
    assert "code-review" not in names                                          # a Coding skill is not part of the domain catalog
    agent = client.get("/api/agents").json()[0]["id"]
    client.put(f"/api/agents/{agent}", json={"skills": ["bank-reconciliation", "group:sales"]})
    rows = {r["name"]: r for r in client.get("/api/catalog/skills").json()}
    assert agent in [a["id"] for a in rows["bank-reconciliation"]["used_by"]]
    assert agent in [a["id"] for a in rows["pipeline-review"]["used_by"]]      # attached through its group


def test_domains_endpoint(client):
    assert [d["id"] for d in client.get("/api/catalog/domains").json()] == MC.DOMAIN_IDS


# ----------------------------------------------------------------------------------------------------------- the MCP list
def test_catalog_is_well_formed_and_pinned():
    assert MC.problems() == []
    assert len(MC.CATALOG) >= 30
    covered = {d for e in MC.CATALOG for d in e["domains"]}
    assert covered == set(MC.DOMAIN_IDS)
    for e in MC.CATALOG:
        if e["transport"] == "stdio":
            assert e["command"] in ("npx", "uvx")


def test_problem_checker_catches_each_kind_of_mistake():
    bad = dict(MC.get("postgres"))
    cases = [
        (dict(bad, id="Bad Id"), "duplicate or invalid id"),
        (dict(bad, read_only="maybe"), "bad read_only"),
        (dict(bad, domains=["nope"]), "bad domains"),
        (dict(bad, args=["x@latest"]), "unpinned"),
        (dict(MC.get("hubspot"), args=["-y", "@hubspot/mcp-server"]), "npm package not pinned"),
        (dict(bad, args=["postgres-mcp"]), "PyPI package not pinned"),
        (dict(MC.get("huggingface"), url="http://x"), "must use https"),
        (dict(bad, fields=bad["fields"] * 2), "duplicate field"),
        (dict(bad, args=bad["args"] + ["{MISSING}"]), "unresolved placeholder"),
        (dict(bad, fields=[MC.F("A", "a", where="elsewhere")]), "bad field"),
        (dict(bad, ro_args=[], ro_env={}, rw_args=[], rw_env={}), "needs a read-only switch"),
        (dict(bad, writes=""), "missing a description field"),
    ]
    for entry, expect in cases:
        assert any(expect in p for p in MC.problems([entry])), expect
    twice = MC.problems([MC.get("fred"), MC.get("fred")])
    assert any("duplicate or invalid id" in p for p in twice)


def test_every_entry_says_what_it_can_change_and_where_data_goes():
    for e in MC.CATALOG:
        assert len(e["writes"]) > 5 and len(e["data_leaves"]) > 5 and e["verified"] and e["docs"].startswith("https://"), e["id"]
        assert (e["read_only"] == "always") == (not MC.needs_confirm(e, False)) or e["read_only"] == "enforced"


def test_needs_confirm_rules():
    assert MC.needs_confirm(MC.get("tavily"), False) is False                      # always read-only
    assert MC.needs_confirm(MC.get("postgres"), False) is False                    # enforced read-only: staying read-only is free
    assert MC.needs_confirm(MC.get("postgres"), True) is True                      # turning writes on needs the tick
    for cid in ("xero", "hubspot", "kubernetes"):
        assert MC.needs_confirm(MC.get(cid), False) is True


def test_public_view_hides_launch_internals():
    d = MC.public(MC.get("postgres"), {"allow_writes": False}, True)
    assert d["installed"] and d["ready"] and d["can_be_read_only"] and not d["allow_writes"]
    assert "ro_args" not in d and d["command_line"].startswith("uvx postgres-mcp==0.3.0")
    assert MC.public(MC.get("huggingface"))["command_line"] == "https://huggingface.co/mcp"
    assert MC.public(MC.get("xero"))["installed"] is False


# -------------------------------------------------------------------------------------------------------------- build()
def test_build_validates_values():
    e = MC.get("postgres")
    with pytest.raises(ValueError, match="unknown setting"):
        MC.build(e, {"NOPE": "x"}, False)
    with pytest.raises(ValueError, match="fill in: Connection string"):
        MC.build(e, {}, False)
    ok = MC.build(e, {"DATABASE_URI": " postgresql://a/b "}, False)
    assert ok["args"] == ["postgres-mcp==0.3.0", "--access-mode=restricted"] and ok["env"] == {"DATABASE_URI": "postgresql://a/b"} and ok["requires_env"] == ["DATABASE_URI"]
    rw = MC.build(e, {"DATABASE_URI": "postgresql://a/b"}, True)
    assert rw["args"][-1] == "--access-mode=unrestricted"


def test_build_requires_the_confirm_for_servers_that_can_change_things():
    with pytest.raises(ValueError, match="can change things"):
        MC.build(MC.get("xero"), {"XERO_CLIENT_ID": "a", "XERO_CLIENT_SECRET": "b"}, False)
    assert MC.build(MC.get("xero"), {"XERO_CLIENT_ID": "a", "XERO_CLIENT_SECRET": "b"}, True)["env"]["XERO_CLIENT_ID"] == "a"
    safe = MC.build(MC.get("tavily"), {"TAVILY_API_KEY": "k"}, True)             # allow_writes is ignored for read-only-always servers
    assert safe["env"] == {"TAVILY_API_KEY": "k"}


def test_build_places_values_in_args_env_and_headers_and_applies_defaults():
    duck = MC.build(MC.get("duckdb"), {"DB_PATH": "C:/data/x.duckdb"}, False)
    assert duck["args"] == ["mcp-server-motherduck==1.1.0", "--db-path", "C:/data/x.duckdb"] and duck["requires_env"] == []
    assert MC.build(MC.get("duckdb"), {"DB_PATH": ":memory:"}, True)["args"][-1] == "--read-write"
    with pytest.raises(ValueError, match="single line"):
        MC.build(MC.get("duckdb"), {"DB_PATH": "a\nb"}, False)
    ch = MC.build(MC.get("clickhouse"), {"CLICKHOUSE_HOST": "h"}, False)
    assert ch["env"]["CLICKHOUSE_USER"] == "default" and ch["env"]["CLICKHOUSE_ALLOW_WRITE_ACCESS"] == "false" and "CLICKHOUSE_PASSWORD" not in ch["env"]
    assert MC.build(MC.get("clickhouse"), {"CLICKHOUSE_HOST": "h"}, True)["env"]["CLICKHOUSE_ALLOW_WRITE_ACCESS"] == "true"
    hf = MC.build(MC.get("huggingface"), {"HF_TOKEN": "hf_x"}, True)
    assert hf["env"] == {"HEADER_AUTHORIZATION": "Bearer hf_x"} and hf["requires_env"] == []
    assert MC.build(MC.get("huggingface"), {}, True)["env"] == {}
    aws = MC.build(MC.get("aws-api"), {}, False)
    assert aws["env"]["READ_OPERATIONS_ONLY"] == "true" and aws["env"]["AWS_REGION"] == "us-east-1"
    assert MC.build(MC.get("aws-api"), {}, True)["env"]["READ_OPERATIONS_ONLY"] == "false"


# ---------------------------------------------------------------------------------------------------- install / mode / API
def test_install_stores_the_server_and_keeps_secrets_write_only(client, state):
    r = client.post("/api/catalog/mcp/postgres/install", json={"values": {"DATABASE_URI": "postgresql://u:secret@h/db"}})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "postgres" and body["enabled"] and body["ready"] and body["env_set"] == {"DATABASE_URI": True}
    assert "secret" not in json.dumps(body) and "secret" not in json.dumps(client.get("/api/mcp").json())
    listed = {m["id"]: m for m in client.get("/api/catalog/mcp").json()}
    assert listed["postgres"]["installed"] and listed["postgres"]["ready"] and not listed["postgres"]["allow_writes"]
    assert not listed["xero"]["installed"]
    assert {m["id"]: m for m in client.get("/api/mcp").json()}["postgres"]["category"] == "Catalog"
    # reinstalling replaces it (e.g. new password) and keeps one entry
    client.post("/api/catalog/mcp/postgres/install", json={"values": {"DATABASE_URI": "postgresql://u:new@h/db"}})
    assert len([m for m in client.get("/api/mcp").json() if m["id"] == "postgres"]) == 1
    assert json.loads(state.mcp.get("postgres").env["DATABASE_URI"] and json.dumps(state.mcp.get("postgres").env))["DATABASE_URI"].endswith("new@h/db")


def test_install_errors(client, state):
    assert client.post("/api/catalog/mcp/nope/install", json={}).status_code == 404
    assert client.post("/api/catalog/mcp/xero/install", json={"values": {"XERO_CLIENT_ID": "a", "XERO_CLIENT_SECRET": "b"}}).status_code == 422     # no confirm
    assert client.post("/api/catalog/mcp/postgres/install", json={"values": {}}).status_code == 422
    state.mcp.add("fred", "My fred", "mine", "stdio", "echo", [], None, {})                                                   # a user server with the same id
    r = client.post("/api/catalog/mcp/fred/install", json={"values": {"FRED_API_KEY": "k"}})
    assert r.status_code == 422 and "was not added from the catalog" in r.json()["detail"]


def test_confirmed_install_of_a_writing_server_is_recorded(client, state):
    r = client.post("/api/catalog/mcp/xero/install", json={"values": {"XERO_CLIENT_ID": "a", "XERO_CLIENT_SECRET": "b"}, "allow_writes": True})
    assert r.status_code == 200
    assert {m["id"]: m for m in client.get("/api/catalog/mcp").json()}["xero"]["allow_writes"] is True


def test_mode_switch_between_read_only_and_writes(client, state):
    client.post("/api/catalog/mcp/clickhouse/install", json={"values": {"CLICKHOUSE_HOST": "h", "CLICKHOUSE_PASSWORD": "pw"}})
    r = client.put("/api/catalog/mcp/clickhouse/mode", json={"allow_writes": True})
    assert r.status_code == 200 and state.mcp.get("clickhouse").env["CLICKHOUSE_ALLOW_WRITE_ACCESS"] == "true" and state.mcp.get("clickhouse").env["CLICKHOUSE_PASSWORD"] == "pw"
    client.put("/api/catalog/mcp/clickhouse/mode", json={"allow_writes": False})
    assert state.mcp.get("clickhouse").env["CLICKHOUSE_ALLOW_WRITE_ACCESS"] == "false"
    # args-based switches (postgres) swap the flag
    client.post("/api/catalog/mcp/postgres/install", json={"values": {"DATABASE_URI": "postgresql://a/b"}})
    client.put("/api/catalog/mcp/postgres/mode", json={"allow_writes": True})
    assert state.mcp.get("postgres").args == ["postgres-mcp==0.3.0", "--access-mode=unrestricted"]
    client.put("/api/catalog/mcp/postgres/mode", json={"allow_writes": False})
    assert state.mcp.get("postgres").args == ["postgres-mcp==0.3.0", "--access-mode=restricted"]
    assert {m["id"]: m for m in client.get("/api/catalog/mcp").json()}["postgres"]["allow_writes"] is False


def test_mode_switch_refusals(client):
    assert client.put("/api/catalog/mcp/postgres/mode", json={"allow_writes": True}).status_code == 404          # not installed
    assert client.put("/api/catalog/mcp/nope/mode", json={"allow_writes": True}).status_code == 404
    client.post("/api/catalog/mcp/tavily/install", json={"values": {"TAVILY_API_KEY": "k"}})
    assert "cannot change anything" in client.put("/api/catalog/mcp/tavily/mode", json={"allow_writes": True}).json()["detail"]
    client.post("/api/catalog/mcp/hubspot/install", json={"values": {"PRIVATE_APP_ACCESS_TOKEN": "t"}, "allow_writes": True})
    assert "cannot be limited by a setting" in client.put("/api/catalog/mcp/hubspot/mode", json={"allow_writes": False}).json()["detail"]


def test_deleting_an_installed_server_removes_it_from_the_catalog_state(client):
    client.post("/api/catalog/mcp/fred/install", json={"values": {"FRED_API_KEY": "k"}})
    assert client.delete("/api/mcp/fred").status_code == 200
    assert not {m["id"]: m for m in client.get("/api/catalog/mcp").json()}["fred"]["installed"]


def test_remote_server_install_builds_a_header(client, state):
    r = client.post("/api/catalog/mcp/kaggle/install", json={"values": {"KAGGLE_API_TOKEN": "tok"}, "allow_writes": True})
    assert r.status_code == 200 and state.mcp.get("kaggle").env == {"HEADER_AUTHORIZATION": "Bearer tok"} and state.mcp.get("kaggle").url == "https://www.kaggle.com/mcp"


# ------------------------------------------------------------------------------------------------------ cloud accounts
def test_cloud_settings_are_write_only_and_validated(client):
    d = client.get("/api/catalog/cloud").json()
    assert {p["id"] for p in d["providers"]} == {"aws", "gcp", "azure", "digitalocean", "runpod", "oci"} and d["allow_writes"] is False and d["enabled"] is False
    assert all(not p["configured"] for p in d["providers"])
    r = client.put("/api/catalog/cloud", json={"values": {"RUNPOD_API_KEY": "rp-secret-key"}})
    got = r.json()
    assert "rp-secret-key" not in json.dumps(got) and {p["id"]: p for p in got["providers"]}["runpod"]["configured"]
    assert {f["name"]: f["set"] for f in {p["id"]: p for p in got["providers"]}["runpod"]["fields"]} == {"RUNPOD_API_KEY": True}
    assert "rp-secret-key" not in json.dumps(client.get("/api/mcp").json())
    assert client.put("/api/catalog/cloud", json={"allow_writes": True}).json()["allow_writes"] is True
    assert client.put("/api/catalog/cloud", json={"allow_writes": False}).json()["allow_writes"] is False
    cleared = client.put("/api/catalog/cloud", json={"values": {"RUNPOD_API_KEY": ""}}).json()
    assert not {p["id"]: p for p in cleared["providers"]}["runpod"]["configured"]
    assert client.put("/api/catalog/cloud", json={"values": {"EVIL": "x"}}).status_code == 422
    assert client.put("/api/catalog/cloud", json={"values": {"RUNPOD_API_KEY": "a\nb"}}).status_code == 422


# -------------------------------------------------------------------------------------------------------------------- BOM
def test_aibom_marks_catalog_servers_without_secrets(client, state):
    from app.aibom import builder
    client.post("/api/catalog/mcp/postgres/install", json={"values": {"DATABASE_URI": "postgresql://u:topsecret@h/db"}})
    bom = builder.build_full(state, "internal")
    text = json.dumps(bom)
    assert "topsecret" not in text
    svc = next(s for s in bom["services"] if s["bom-ref"] == "mcp:postgres")
    props = {p["name"].split(":")[-1].split("/")[-1]: p["value"] for p in svc["properties"]}
    assert props["from-catalog"] == "true" and props["read-only-kind"] == "enforced" and props["writes-allowed"] == "false" and props["publisher"].startswith("Crystal")
    assert "mcp:trainer" in {s["bom-ref"] for s in bom["services"]} and "mcp:cloudml" in {s["bom-ref"] for s in bom["services"]}
