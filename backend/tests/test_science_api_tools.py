"""Science Lab: the agent tools, the REST API, the optional-component installer, the guardrail kind, the AI-BOM entry and the wiring into chats."""
from __future__ import annotations

import asyncio
import json
import types
from datetime import date

import pytest

from app.db import Database
from app.science import catalog
from app.science.service import ScienceService
from app.science.tools import SCIENCE_RULES, science_tools
from tests.science_fakes import make_fetcher
from tests.test_science_core import FakeSandbox, abstract
from tests.test_science_service import Art


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def lab(tmp_path):
    fx, mode, clock = make_fetcher()
    svc = ScienceService(Database(tmp_path / "t.db"), Art(), None, fx, now=clock.now)
    events = []
    ctx = types.SimpleNamespace(science=svc, agent_id="sci", session_id="sess", sandbox=FakeSandbox(), on_science=events.append)
    return svc, science_tools(ctx), events, ctx


def test_the_tool_set_and_its_rules_mention_the_safety_and_honesty_rules(lab):
    svc, tools, *_ = lab
    assert set(tools) == {"science_sources", "science_start", "science_search", "science_calc", "science_run_python", "science_add_evidence", "science_add_claim", "science_add_hypothesis",
                          "science_compare", "science_check_citations", "science_report"}
    assert all(f.__doc__ for f in tools.values()) and "no data found" in SCIENCE_RULES and "retrosynthesis" in SCIENCE_RULES and "pubmed" in SCIENCE_RULES


def test_an_agent_investigates_end_to_end_with_recorded_evidence(lab):
    svc, t, events, ctx = lab
    assert any(s["id"] == "pubchem" and s["status"] == "ready" for s in run(t["science_sources"]())["sources"])
    started = run(t["science_start"]("Aspirin", "chemistry", "What is aspirin?"))
    iid = started["investigation_id"]
    found = run(t["science_search"]("pubchem", "aspirin", "search", 3))                    # no id given: the chat's investigation is used
    assert found["ok"] and found["records"][0]["fields"]["formula"] == "C9H8O4" and "science_add_evidence" in found["note"]
    ev = run(t["science_add_evidence"]("", "source", found["run_id"], "cid:2244" if False else "2244"))
    assert ev["ok"] and ev["source"] == "pubchem"
    calc = run(t["science_calc"]("formula_mass", "C9H8O4"))
    assert calc["ok"] and calc["molar_mass_g_per_mol"] == pytest.approx(180.159, abs=0.01) and calc["label"] == "computed here"
    py = run(t["science_run_python"]("print(2+2)", "{}", 5))
    assert py["ok"] and py["run_id"].startswith("run_") and py["record"]["seed"] == 5
    comp = run(t["science_add_evidence"](iid, "computed", py["run_id"], value="4"))
    claim = run(t["science_add_claim"]("Aspirin is C9H8O4 with mass 180.16", f"{ev['evidence_id']}, {comp['evidence_id']}"))
    assert claim["ok"] and claim["label"] == "single_source"
    guess = run(t["science_add_claim"]("It probably relaxes muscles", "", False, iid))
    assert guess["label"] == "unsupported"
    h1, h2 = run(t["science_add_hypothesis"]("H1"))["hypothesis_id"], run(t["science_add_hypothesis"]("H2", iid))["hypothesis_id"]
    assert run(t["science_compare"](h1, h2))["ranking"][0]["id"] == h1
    assert run(t["science_check_citations"]())["ok"]
    rep = run(t["science_report"](True))
    assert rep["ok"] and "# Aspirin" in rep["markdown"] and rep["unsupported"] == 1 and len(rep["saved"]["files"]) == 3 and "saved" not in run(t["science_report"]())
    assert [e["action"] for e in events][:3] == ["start", "search", "evidence"] and "report" in [e["action"] for e in events]


def test_tools_report_problems_instead_of_raising(lab):
    svc, t, events, ctx = lab
    no_inv = run(t["science_add_claim"]("x"))
    assert not no_inv["ok"] and "science_start" in no_inv["error"]
    for call in (t["science_add_evidence"]("", "inference"), t["science_add_hypothesis"]("h"), t["science_compare"]("a", "b"), t["science_check_citations"](), t["science_report"]()):
        assert not run(call)["ok"]
    assert not run(t["science_start"]("", "chemistry"))["ok"]
    iid = run(t["science_start"]("T"))["investigation_id"]
    assert run(t["science_search"]("zzz", "x"))["ok"] is False
    assert run(t["science_search"]("ads", "x"))["error_kind"] == "needs_key"
    assert run(t["science_start"]("blocked", "biology", abstract("toxin", "how to make")))["error"].startswith("Blocked")
    assert run(t["science_search"]("pubchem", abstract("explosive", "how to make")))["blocked"]
    assert not run(t["science_calc"]("fly"))["ok"] and not run(t["science_calc"]("calc", "1/0"))["ok"] and not run(t["science_calc"]("calc", "1", "{bad"))["ok"] and not run(t["science_calc"]("calc", "1", "[1]"))["ok"]
    assert run(t["science_calc"]("table", "x", "", 0, 1, "x", "", ""))["ok"]
    assert run(t["science_run_python"]("# " + abstract("pathogen", "synthesize"), "", 0, iid))["blocked"]
    assert not run(t["science_run_python"]("print(1)", "{bad"))["ok"]
    assert run(t["science_run_python"]("print(1)", "", 0, ""))["run_id"].startswith("run_")      # the chat's investigation is used
    fresh = science_tools(types.SimpleNamespace(science=svc, agent_id="", session_id="another", sandbox=FakeSandbox(), on_science=None))
    assert run(fresh["science_run_python"]("print(1)"))["run_id"] == ""                          # a quick run with no investigation is fine
    ctx.sandbox = types.SimpleNamespace(exec=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("docker is not running")), kind="x")
    r = run(science_tools(ctx)["science_run_python"]("print(1)"))
    assert not r["ok"] and "docker is not running" in r["error"]


def test_tools_work_without_a_callback_and_in_a_fresh_chat(lab):
    svc, _, _, ctx = lab
    ctx.on_science = None
    t = science_tools(ctx)
    assert run(t["science_start"]("No callback"))["ok"]


@pytest.fixture
def api(client, state):
    fx, mode, clock = make_fetcher()
    state.science.fx = fx
    state.science.now = clock.now
    return client, state, mode


def test_the_rest_api_end_to_end(api):
    c, state, mode = api
    cfg = c.get("/api/science/config").json()
    assert cfg["enabled"] and cfg["safety_level"] == "standard" and "policy" in cfg and "labels" in cfg
    assert c.put("/api/science/config", json={"safety_level": "strict"}).json()["safety_level"] == "strict"
    assert c.put("/api/science/config", json={"safety_level": "standard"}).json()["safety_level"] == "strict"
    assert c.put("/api/science/config", json={"bogus": 1}).status_code == 422
    assert c.put("/api/science/keys/ads", json={"key": "adskey"}).json()["ok"] and c.put("/api/science/keys/zzz", json={"key": "k"}).status_code == 422
    assert "adskey" not in c.get("/api/science/config").text and "adskey" not in c.get("/api/science/connectors").text
    assert len(c.get("/api/science/connectors").json()) == 13
    assert c.post("/api/science/screen", json={"text": abstract("toxin", "how to make")}).json()["action"] == "block" and c.post("/api/science/screen", json={"text": "hello"}).json()["action"] == "allow"
    assert c.post("/api/science/screen", json={"text": 5}).status_code == 422

    inv = c.post("/api/science/investigations", json={"title": "Aspirin", "domain": "chemistry", "question": "What?"}).json()
    iid = inv["id"]
    assert c.post("/api/science/investigations", json={"title": ""}).status_code == 422 and c.get("/api/science/investigations/inv_x").status_code == 404
    found = c.post("/api/science/search", json={"source": "pubchem", "query": "aspirin", "investigation_id": iid, "limit": 2}).json()
    assert found["ok"] and found["records"][0]["fields"]["cid"] == 2244
    assert c.post("/api/science/search", json={"source": "zzz", "query": "x"}).status_code == 422
    ev = c.post(f"/api/science/investigations/{iid}/evidence", json={"kind": "source", "run_id": found["run_id"], "ref": "2244"}).json()
    assert ev["source"] == "pubchem" and c.post(f"/api/science/investigations/{iid}/evidence", json={"kind": "source"}).status_code == 422
    cl = c.post(f"/api/science/investigations/{iid}/claims", json={"text": "C9H8O4", "evidence_ids": [ev["id"]]}).json()
    assert cl["label"] == "single_source"
    h1 = c.post(f"/api/science/investigations/{iid}/hypotheses", json={"text": "H1"}).json()["id"]
    h2 = c.post(f"/api/science/investigations/{iid}/hypotheses", json={"text": "H2"}).json()["id"]
    assert c.post(f"/api/science/investigations/{iid}/compare", json={"winner": h1, "loser": h2}).json()[0]["id"] == h1
    assert c.post(f"/api/science/investigations/{iid}/compare", json={"winner": h1, "loser": h1}).status_code == 422
    assert c.post(f"/api/science/investigations/{iid}/verify").json() == [] and c.post("/api/science/investigations/inv_x/verify").status_code == 404
    full = c.get(f"/api/science/investigations/{iid}").json()
    assert full["claims_list"][0]["label"] == "single_source" and len(full["evidence_list"]) == 1 and len(full["hypotheses_list"]) == 2 and full["runs_list"][0]["tool"] == "source:pubchem"
    assert "Single source" in c.get(f"/api/science/investigations/{iid}/report").json()["markdown"] and c.get("/api/science/investigations/inv_x/report").status_code == 404
    assert c.put(f"/api/science/investigations/{iid}", json={"status": "done"}).json()["status"] == "done"
    assert c.get("/api/science/investigations", params={"domain": "chemistry"}).json()[0]["id"] == iid
    assert len(c.post(f"/api/science/investigations/{iid}/notebook").json()["files"]) == 3
    assert c.delete(f"/api/science/investigations/{iid}/claims/{cl['id']}").json()["ok"] and c.delete(f"/api/science/investigations/{iid}/claims/{cl['id']}").status_code == 404
    assert c.delete(f"/api/science/investigations/{iid}").json()["ok"] and c.delete(f"/api/science/investigations/{iid}").status_code == 404


def test_the_local_calculator_api(api):
    c, *_ = api
    assert c.post("/api/science/calc", json={"kind": "convert", "expression": "1", "unit_from": "eV", "unit_to": "J"}).json()["result"] == pytest.approx(1.602176634e-19)
    assert c.post("/api/science/calc", json={"kind": "integrate", "expression": "k*x", "variables": {"k": 2}, "a": 0, "b": 1}).json()["result"] == pytest.approx(1.0)
    assert c.post("/api/science/calc", json={"kind": "calc", "expression": "1/0"}).status_code == 422
    assert c.post("/api/science/calc", json={"kind": "calc", "expression": "1", "variables": [1]}).status_code == 422
    assert c.post("/api/science/calc", json={"kind": "calc", "expression": "1", "a": "zzz"}).status_code == 422
    k = c.get("/api/science/constants").json()
    assert "CODATA" in k["source"] and {x["symbol"] for x in k["constants"]} >= {"c", "h", "k_B"}


# ------------------------------------------------------------------ optional components
def fake_runner(fail_at=None, version="1.2.3"):
    calls = []

    def runner(cmd, env, on_line):
        calls.append(cmd)
        step = len(calls)
        if fail_at == step:
            on_line("ERROR: No matching distribution found for fancy-dep")
            return 1
        if step == 4:
            on_line(f"VERSION {version}")
        return 0
    runner.calls = calls
    return runner


def test_plans_explain_what_can_and_cannot_be_installed():
    ids = {k: catalog.plan(v) for k, v in catalog.COMPONENTS.items()}
    assert ids["paperqa"]["can_install"] and ids["tooluniverse"]["can_install"] and "Apache-2.0" == ids["paperqa"]["licence"]
    for blocked in ("biomni", "chemcrow", "ai-scientist-v2", "edison"):
        assert not ids[blocked]["can_install"] and ids[blocked]["reasons"]
    assert any("retrosynthesis" in r for r in ids["chemcrow"]["reasons"]) and any("licence" in r.lower() for r in ids["ai-scientist-v2"]["reasons"]) and any("Hosted" in r for r in ids["edison"]["reasons"])
    assert not catalog.plan(catalog.COMPONENTS["paperqa"], "3.9.1")["can_install"] and "Python 3.11" in " ".join(catalog.plan(catalog.COMPONENTS["paperqa"], "3.9.1")["reasons"])
    assert catalog._ver("3.12.4") > catalog._ver("3.11") and catalog._ver("x") == (0,)
    assert all(c["version"] for c in catalog.COMPONENTS.values() if c["installable"]) and ids["paperqa"]["steps"] and not ids["edison"]["steps"]
    assert not any("claude" in k or "co-scientist" in k or "coscientist" in k for k in catalog.COMPONENTS)        # the lab never connects to those products


def test_install_needs_the_licence_tick_and_runs_in_its_own_environment(tmp_path):
    db = Database(tmp_path / "i.db")
    runner = fake_runner()
    ins = catalog.ComponentInstaller(db, tmp_path / "envs", runner, today=lambda: date(2026, 11, 23))
    with pytest.raises(ValueError, match="tick the box"):
        ins.install("paperqa", False)
    for cid, msg in [("zzz", "unknown"), ("biomni", "cannot be installed"), ("edison", "cannot be installed")]:
        with pytest.raises(ValueError, match=msg):
            ins.install(cid, True)
    st = ins.install("paperqa", True)
    ins.wait()
    comp = {c["id"]: c for c in ins.list()}["paperqa"]
    assert comp["state"] == "ready" and comp["installed_version"] == "1.2.3" and comp["command"].endswith("-m paperqa") and ins.status()["installing"] == ""
    venv_cmd, pip_up, install, check = runner.calls
    assert "venv" in venv_cmd and str(tmp_path / "envs" / "paperqa" / "venv") in venv_cmd
    assert "--only-binary=:all:" in install and "--uploaded-prior-to" in install and "2026-11-09T00:00:00Z" in install and "paper-qa==2026.8.12" in install
    assert json.loads(db.get_setting("science_components"))["paperqa"]["licence_ack"] is True and st["installing"] == "paperqa"
    ins.uninstall("paperqa")
    assert {c["id"]: c for c in ins.list()}["paperqa"]["state"] == "not_installed"
    with pytest.raises(ValueError, match="unknown"):
        ins.uninstall("zzz")


def test_a_failed_install_explains_why_and_a_busy_one_refuses_a_second(tmp_path):
    db = Database(tmp_path / "i.db")
    ins = catalog.ComponentInstaller(db, tmp_path / "envs", fake_runner(fail_at=3))
    ins.install("tooluniverse", True)
    ins.wait()
    comp = {c["id"]: c for c in ins.list()}["tooluniverse"]
    assert comp["state"] == "failed" and "fancy-dep" in comp["error"] and comp["command"] == ""
    gate = __import__("threading").Event()

    def slow(cmd, env, on_line):
        gate.wait(5)
        on_line("VERSION 9")
        return 0
    busy = catalog.ComponentInstaller(db, tmp_path / "e2", slow)
    busy.install("paperqa", True)
    with pytest.raises(ValueError, match="being installed"):
        busy.install("tooluniverse", True)
    with pytest.raises(ValueError, match="right now"):
        busy.uninstall("paperqa")
    gate.set()
    busy.wait()
    bad = catalog.ComponentInstaller(db, tmp_path / "e3", fake_runner(version=""))      # the check step prints nothing useful
    bad.runner = lambda cmd, env, on_line: 0
    bad.install("paperqa", True)
    bad.wait()
    assert {c["id"]: c for c in bad.list()}["paperqa"]["error"].startswith("it installed but its version")
    db.set_setting("science_components", "broken")
    assert bad._state() == {}
    db.set_setting("science_components", "[]")
    assert bad._state() == {}


def test_component_api(api):
    c, state, _ = api
    state.science_components.runner = fake_runner()
    assert {x["id"] for x in c.get("/api/science/components").json()["components"]} >= {"paperqa", "tooluniverse", "edison"}
    assert c.post("/api/science/components/paperqa/install", json={}).status_code == 422
    assert c.post("/api/science/components/biomni/install", json={"licence_ack": True}).status_code == 422
    assert c.post("/api/science/components/paperqa/install", json={"licence_ack": True}).json()["installing"] == "paperqa"
    state.science_components.wait()
    assert c.delete("/api/science/components/paperqa").status_code == 200 and c.delete("/api/science/components/zzz").status_code == 422


# ------------------------------------------------------------------ wiring
def test_the_science_guardrail_kind_blocks_through_a_profile():
    from app.guardrails import science as g
    from app.guardrails.service import KINDS
    assert "science" in KINDS and KINDS["science"] is g.KIND
    guard = g.KIND.make({"level": "standard"}, {}, None)
    v = run(guard.check(abstract("chemical_weapon", "how to make"), "input"))
    assert v.flagged and v.category == "chemical_weapon" and not run(guard.check("hello", "input")).flagged
    assert run(g.KIND.make({}, {}, None).check("write a protocol for the culture medium of anthrax", "input")).flagged is True
    from app.guardrails.base import GuardError
    with pytest.raises(GuardError, match="level"):
        g.KIND.make({"level": "off"}, {}, None)


def test_the_science_tools_are_registered_described_and_allowed_in_plan_mode(state):
    from app.easy.agent_templates import TOOL_PLAIN as TOOL_TEXT
    from app.harness.policy import READ_ONLY
    from app.harness.tools import TOOL_INFO, ToolContext, build_tools
    names = [n for n in TOOL_INFO if n.startswith("science_")]
    assert len(names) == 11 and all(n in TOOL_TEXT for n in names) and {"science_search", "science_calc", "science_report"} <= READ_ONLY and "science_run_python" not in READ_ONLY
    ws = state.chat.workspace_for(None)
    ctx = ToolContext(ws, state.chat.sandbox_for(ws, state.agents.get("science_lab")), state.chat.skills, state.chat.mcp, science=state.science, agent_id="science_lab", session_id="s")
    got = {t.__name__ if hasattr(t, "__name__") else t.name for t in build_tools(ctx, names)}
    assert set(names) <= got
    off = ToolContext(ws, state.chat.sandbox_for(ws, None), state.chat.skills, state.chat.mcp, science=state.science)
    state.science.set_config({"enabled": False})
    assert not ({t.__name__ if hasattr(t, "__name__") else t.name for t in build_tools(off, names)} & set(names))


def test_a_builtin_science_agent_and_skills_exist(state):
    a = state.agents.get("science_lab")
    assert a and a.builtin and "science_search" in a.tools and "science_run_python" in a.tools and all(state.chat.skills.get(s) for s in a.skills)
    for sk in ("science-literature-review", "science-hypothesis-ranking", "science-chemistry", "science-biology", "science-physics", "science-math", "science-safety"):
        s = state.chat.skills.get(sk)
        assert s and s.group == "Science" and "**Safety:**" in s.body and "**When to use:**" in s.body


def test_the_ai_bom_lists_sources_without_questions_or_keys(state):
    from app.aibom import builder as B
    from app.aibom.validate import validate_bom
    state.science.set_key("ads", "super-secret-science-key")
    inv = state.science.create("Private research question title", "biology", "Secret hypothesis about X")
    for level in ("public", "internal", "full"):
        bom = B.build_full(state, level)
        assert validate_bom(bom)["ok"]
        text = json.dumps(bom)
        svc = next(x for x in bom["services"] if x["bom-ref"] == "science")
        assert "super-secret-science-key" not in text and "Private research question" not in text and "Secret hypothesis" not in text and inv["id"] not in text and "pubmed" in text
        assert svc["name"] == "Science Lab"


def test_settings_files_carry_the_lab_settings_but_never_keys_installs_or_a_lower_safety_level(api):
    c, state, _ = api
    c.put("/api/science/keys/ads", json={"key": "adskey"})
    c.put("/api/science/config", json={"safety_level": "strict", "contact_email": "me@example.org"})
    exp = c.get("/api/settings/export").json()
    assert exp["settings"]["science_config"]["safety_level"] == "strict" and "science_components" not in exp["settings"] and "science_secrets" not in exp["settings"] and "adskey" not in json.dumps(exp)
    lowered = {"format": exp["format"], "version": exp["version"], "settings": {"science_config": {"safety_level": "standard", "max_calls_per_investigation": 7}}}
    done = c.post("/api/settings/import", json={"data": lowered, "dry_run": False}).json()
    assert not done["errors"] and state.science.config()["safety_level"] == "strict" and state.science.config()["max_calls_per_investigation"] == 7       # lowering is ignored, the rest applies
    bad = {"format": exp["format"], "version": exp["version"], "settings": {"science_config": {"nonsense": 1}}}
    res = c.post("/api/settings/import", json={"data": bad, "dry_run": False}).json()
    assert any(e["key"] == "science_config" and "unknown science setting" in e["error"] for e in res["errors"])


def test_the_ai_bom_skips_the_lab_when_the_state_has_none(state):
    from app.aibom import builder as B
    state.science = None
    bom = B.build_full(state, "public")
    assert not any(x.get("bom-ref") == "science" for x in bom.get("services", []))
