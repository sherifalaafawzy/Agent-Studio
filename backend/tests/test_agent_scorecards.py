"""Per-agent security scorecards: the same OWASP checks judged on one agent's own configuration, the ranking, the exports, the API
and the AI-BOM / ARD summary. Fakes only."""
from __future__ import annotations

import json
import time

import pytest

from app.agents import AgentDef
from app.aibom import builder as B
from app.apiaccess.api import KEY as ACCESS_KEY
from app.harness.policy import set_policy
from app.security import agentcard as AC
from app.security import scorecard as SC

GUARD_IDS = ("guard-input", "guard-model", "guard-output", "guard-leaks")


def make(state, aid="bot", **kw):
    return state.agents.create(AgentDef(id=aid, name=aid.title(), **kw))


def card(state, aid):
    return AC.card(state, state.agents.get(aid))


def chk(c, cid):
    return next(x for x in c["checks"] if x["id"] == cid)


def st(state, aid, cid):
    return chk(card(state, aid), cid)["status"]


def deployed(state, aid, where="agentcore"):
    state.agents.update(aid, {"runtime": where})
    state.runtimes.live = lambda i: {"id": "rel"} if i == aid else None


# ------------------------------------------------------------------------------------------------------------ shape
def test_an_agent_card_has_the_same_checks_as_the_overall_card_plus_agent_only_ones(state):
    make(state)
    c = card(state, "bot")
    overall = SC.build(state)
    ids = [x["id"] for x in c["checks"]]
    assert ids == [cid for cid, _ in AC.CHECKS] and ids[:26] == [x["id"] for x in overall["checks"]]
    assert [x["title"] for x in c["checks"][:26]] == [x["title"] for x in overall["checks"]]               # same wording: a card reads like the overall one
    assert [x["maps"] for x in c["checks"][:26]] == [x["maps"] for x in overall["checks"]]
    assert ids[26:] == ["permission-mode", "agent-data", "agent-exposure", "agent-runtime", "agent-framework", "agent-remote"]
    assert [f["id"] for f in c["frameworks"]] == ["llm2025", "asi2026"] and all(f["covered"] == 10 and len(f["risks"]) == 10 for f in c["frameworks"])
    allowed = {"pass", "warn", "fail", "na"}
    assert all(x["status"] in allowed and x["detail"] and x["scope"] in ("agent", "shared") for x in c["checks"])
    assert c["scope"] == "agent" and c["agent"]["id"] == "bot" and c["band"] in ("good", "middling", "low") and 0 <= c["percent"] <= 100
    assert sum(c["counts"].values()) == len(c["checks"])
    live = [x for x in c["checks"] if x["status"] != "na"]
    assert c["percent"] == round(100 * sum({"pass": 1, "warn": 0.5, "fail": 0}[x["status"]] for x in live) / len(live))
    assert {x["id"] for x in c["checks"] if x["scope"] == "shared"} == AC.SHARED
    risks = {r["id"]: r["status"] for f in c["frameworks"] for r in f["risks"]}
    assert set(risks.values()) <= allowed and "none" not in risks.values()


def test_not_applicable_checks_say_why_and_do_not_count(state):
    make(state)                                                       # no tools, no memory, no MCP, no knowledge bases
    c = card(state, "bot")
    na = {x["id"]: x["detail"] for x in c["checks"] if x["status"] == "na"}
    assert {"guard-tool", "sandbox-network", "sandbox-only", "spawn-rules", "external-agents", "mcp-pinned", "mcp-writes", "memory-pii", "memory-review",
            "retrieved-text", "vector-stores", "inter-agent", "permission-mode", "agent-data", "agent-exposure", "agent-remote"} == set(na)
    assert "cannot run commands" in na["sandbox-network"] and "no tools" in na["guard-tool"] and c["counts"]["na"] == len(na)
    assert chk(c, "agent-runtime")["status"] == "pass" and chk(c, "agent-framework")["status"] == "pass"


def test_an_agent_that_keeps_every_default_matches_the_overall_card_on_the_app_wide_checks(state):
    make(state, "plain")
    ids = (*GUARD_IDS, "aibom", "telemetry", "ocsf", "identity", "transparency", "secrets-write-only")

    def same(label):
        overall, mine = SC.build(state), card(state, "plain")
        for cid in ids:
            assert chk(mine, cid)["status"] == chk(overall, cid)["status"], (label, cid)
    same("defaults")
    state.guardrails.set_enabled(True)
    state.guardrails.save({"id": "watch", "name": "Watch", "mode": "monitor", "default": True, "params": {"detect": ["injection"]}})
    same("monitor")
    state.guardrails.save({"id": "watch", "mode": "block", "params": {"detect": ["pii", "secrets"]}})
    state.guardrails.save({"id": "model1", "name": "Llama", "kind": "model", "mode": "block", "default": True})
    same("block")
    state.guardrails.set_enabled(False)
    same("off again")
    state.db.set_setting("otel_enabled", "0")
    state.identity.set_config({"enforce": True})
    same("telemetry off, identity enforced")


# ----------------------------------------------------------------------------------------------------------- guardrails
def test_guardrail_checks_follow_the_agents_own_choice(state):
    make(state, "plain")
    make(state, "bare", guardrails=["none"])
    make(state, "picky", guardrails=["strict"])
    g = state.guardrails
    g.set_enabled(True)
    g.save({"id": "basic", "name": "Basic", "mode": "block", "default": True})
    g.save({"id": "strict", "name": "Strict", "kind": "model", "mode": "monitor", "default": False})
    assert st(state, "plain", "guard-input") == "pass"
    bare = chk(card(state, "bare"), "guard-input")
    assert bare["status"] == "fail" and "switched off for this agent" in bare["detail"] and bare["link"] == "/agents/?agent=bare#ag-guardrails"
    assert st(state, "picky", "guard-input") == "warn" and "only recorded" in chk(card(state, "picky"), "guard-input")["detail"]
    assert st(state, "picky", "guard-model") == "pass" and st(state, "plain", "guard-model") == "warn"
    g.set_enabled(False)
    assert "switched off." in chk(card(state, "bare"), "guard-input")["detail"]                  # the whole feature is off: say that, not 'this agent'
    assert chk(card(state, "plain"), "guard-input")["link"] == "/agents/?agent=plain#ag-guardrails"


def test_tool_and_retrieval_checks_apply_only_to_agents_that_use_them(state):
    make(state, "plain")
    make(state, "worker", tools=["read_file"], mcp=["fetch"])
    assert st(state, "plain", "guard-tool") == "na" and st(state, "plain", "retrieved-text") == "na"
    assert st(state, "worker", "guard-tool") == "warn" and st(state, "worker", "retrieved-text") == "warn"
    state.guardrails.set_enabled(True)
    state.guardrails.save({"id": "basic", "name": "Basic", "mode": "block", "default": True})
    assert st(state, "worker", "guard-tool") == "pass" and st(state, "worker", "retrieved-text") == "pass"
    assert chk(card(state, "worker"), "guard-tool")["link"].endswith("#ag-guardrails")


def test_a_deployed_agent_has_no_guardrails_or_tools_there(state):
    make(state, "cloud", tools=["read_file"])
    deployed(state, "cloud")
    c = card(state, "cloud")
    assert [chk(c, i)["status"] for i in GUARD_IDS] == ["fail", "warn", "fail", "warn"]
    assert "deployment on agentcore" in chk(c, "guard-input")["detail"] and chk(c, "guard-input")["link"].endswith("#ag-runtime")
    assert chk(c, "guard-tool")["status"] == "na" and chk(c, "retrieved-text")["status"] == "na"
    assert chk(c, "agent-runtime")["status"] == "warn" and "leaves this PC" in chk(c, "agent-runtime")["detail"]
    assert chk(c, "limits")["status"] == "warn" and "outside the spend caps" in chk(c, "limits")["detail"]


# ---------------------------------------------------------------------------------------- commands, tools, permissions
def test_sandbox_network_and_design_checks_need_a_shell(state):
    make(state, "shelly", tools=["run_shell"])
    assert st(state, "shelly", "sandbox-network") == "pass" and st(state, "shelly", "sandbox-only") == "pass"
    state.db.set_setting("sandbox_network", "1")
    assert st(state, "shelly", "sandbox-network") == "warn"
    state.agents.update("shelly", {"sandbox": "docker-microvm"})
    c = chk(card(state, "shelly"), "sandbox-network")
    assert c["status"] == "warn" and "micro VM" in c["detail"] and "sbx policy ls" in c["fix"]


def test_least_privilege_and_the_permission_mode(state):
    make(state, "reader", tools=["read_file"])
    make(state, "editor", tools=["write_file"])
    make(state, "shelly", tools=["run_shell", "web_fetch"])
    assert st(state, "reader", "least-privilege") == "pass" and st(state, "reader", "permission-mode") == "na"
    lp = chk(card(state, "editor"), "least-privilege")
    assert lp["status"] == "warn" and "write_file" in lp["detail"] and lp["link"] == "/agents/?agent=editor#ag-tools"
    full = chk(card(state, "editor"), "permission-mode")
    assert full["status"] == "warn" and "'full'" in full["detail"] and full["link"] == "/settings/#harness"
    set_policy(state.db, {"permission_mode": "accept_edits"})
    assert chk(card(state, "editor"), "permission-mode")["detail"] == "The permission mode is 'accept_edits'."          # editing is allowed there
    shell = chk(card(state, "shelly"), "permission-mode")
    assert shell["status"] == "pass" and "refuses run_shell" in shell["detail"]
    set_policy(state.db, {"permission_mode": "plan"})
    assert "refuses write_file" in chk(card(state, "editor"), "permission-mode")["detail"]


def test_spawn_inter_agent_and_external_agent_checks(state):
    make(state, "plain")
    make(state, "boss", can_spawn=True, sub_agents=["plain"])
    assert st(state, "plain", "spawn-rules") == "na" and st(state, "plain", "inter-agent") == "na"
    assert st(state, "boss", "spawn-rules") == "pass" and st(state, "boss", "inter-agent") == "pass"
    make(state, "caller", acp_agents=["claude"])
    assert st(state, "plain", "external-agents") == "na"
    assert st(state, "caller", "external-agents") == "pass" and "waits for you" in chk(card(state, "caller"), "external-agents")["detail"]
    state.agents.update("caller", {"acp_policy": "readonly"})
    assert "unless it only reads" in chk(card(state, "caller"), "external-agents")["detail"]
    state.agents.update("caller", {"acp_policy": "reads_auto"})
    c = chk(card(state, "caller"), "external-agents")
    assert c["status"] == "warn" and "claude" in c["detail"] and c["link"].endswith("#ag-acp")
    state.db.set_setting("agents_can_call_external", "0")
    assert chk(card(state, "caller"), "external-agents")["status"] == "pass" and "switched off" in chk(card(state, "caller"), "external-agents")["detail"]


def test_limits_look_at_the_agents_provider_framework_and_spawning(state):
    p = next(x for x in state.providers.list() if x.kind not in ("ollama", "mock"))
    make(state, "plain")
    make(state, "picky", provider=p.id)
    make(state, "boss", can_spawn=True)
    assert st(state, "plain", "limits") == "pass"
    state.db.execute("UPDATE providers SET enabled=1, cap_usd=5000 WHERE id=?", (p.id,))
    assert chk(card(state, "picky"), "limits")["status"] == "warn" and "5000" in chk(card(state, "picky"), "limits")["detail"]
    assert st(state, "plain", "limits") == "warn"                                                         # inherits the chat's provider: any high cap counts
    state.db.execute("UPDATE providers SET cap_usd=5 WHERE id=?", (p.id,))
    assert st(state, "picky", "limits") == "pass"
    state.db.set_setting("max_spawns_per_turn", "20")
    assert st(state, "plain", "limits") == "pass" and st(state, "boss", "limits") == "warn" and "created agents" in chk(card(state, "boss"), "limits")["detail"]
    state.db.set_setting("max_spawns_per_turn", "4")
    state.db.set_setting("max_llm_calls_per_turn", "500")
    assert st(state, "plain", "limits") == "warn"
    state.db.set_setting("max_llm_calls_per_turn", "40")
    state.agents.update("plain", {"framework": "claude_sdk"})
    c = chk(card(state, "plain"), "limits")
    assert c["status"] == "warn" and "outside Agent Studio's spend caps" in c["detail"]


def test_remote_a2a_agents_it_may_call(state):
    make(state, "plain")
    make(state, "caller", tools=["call_remote_agent"])
    assert st(state, "plain", "agent-remote") == "na"
    assert st(state, "caller", "agent-remote") == "pass" and "none is connected" in chk(card(state, "caller"), "agent-remote")["detail"]
    state.a2a_remotes.add("near", "Near", "http://127.0.0.1:9000/")
    c = chk(card(state, "caller"), "agent-remote")
    assert c["status"] == "pass" and "1 remote agent" in c["detail"]
    state.a2a_remotes.add("far", "Far Away", "https://agents.example.com/")
    c = chk(card(state, "caller"), "agent-remote")
    assert c["status"] == "warn" and "Far Away" in c["detail"] and "internet" in c["detail"]
    state.a2a_remotes.set_delegates("far", ["someone_else"])                                              # not allowed for this agent: it drops out
    assert chk(card(state, "caller"), "agent-remote")["status"] == "pass"


# ------------------------------------------------------------------------------------------------ MCP, memory, knowledge
def test_mcp_checks_only_look_at_the_servers_the_agent_uses(state):
    state.mcp.add("loose", "Loose", "", "stdio", "npx", ["-y", "somepkg"], None, {}, enabled=True)
    state.mcp.add("tight", "Tight", "", "stdio", "uvx", ["pkg==1.2.3"], None, {}, enabled=True)
    state.mcp.add("off", "Off", "", "stdio", "npx", ["-y", "other"], None, {}, enabled=False)
    make(state, "plain")
    make(state, "careful", mcp=["tight", "off"])
    make(state, "sloppy", mcp=["tight", "loose"])
    assert st(state, "plain", "mcp-pinned") == "na" and st(state, "plain", "mcp-writes") == "na"
    assert st(state, "careful", "mcp-pinned") == "pass"
    c = chk(card(state, "sloppy"), "mcp-pinned")
    assert c["status"] == "warn" and "loose" in c["detail"] and "tight" not in c["detail"] and c["link"] == "/mcp/"
    assert st(state, "sloppy", "mcp-writes") == "pass"
    state.db.set_setting("mcp_catalog_installed", json.dumps({"loose": {"allow_writes": True}}))
    assert chk(card(state, "sloppy"), "mcp-writes")["status"] == "warn" and st(state, "careful", "mcp-writes") == "pass"


def test_memory_checks_apply_to_agents_that_remember(state):
    make(state, "plain")
    mem = {"types": ["semantic"], "read_scopes": ["agent"], "write_scopes": ["agent", "global"], "auto_recall": True, "auto_save": "auto"}
    make(state, "rememberer", memory=mem)
    assert st(state, "plain", "memory-pii") == "na" and st(state, "plain", "memory-review") == "na"
    assert st(state, "rememberer", "memory-pii") == "pass"
    state.memory.set_config({"redact_pii": False, "review": False})
    assert chk(card(state, "rememberer"), "memory-pii")["status"] == "warn" and chk(card(state, "rememberer"), "memory-pii")["link"] == "/settings/#memory"
    c = chk(card(state, "rememberer"), "memory-review")
    assert c["status"] == "warn" and "every agent shares" in c["detail"] and c["link"].endswith("#ag-memory")
    state.agents.update("rememberer", {"memory": {**mem, "write_scopes": ["agent"]}})
    assert "every agent shares" not in chk(card(state, "rememberer"), "memory-review")["detail"]
    state.memory.set_config({"review": True})
    assert st(state, "rememberer", "memory-review") == "pass"
    state.memory.set_config({"review": False})
    state.agents.update("rememberer", {"memory": {**mem, "auto_save": "review"}})
    assert st(state, "rememberer", "memory-review") == "pass"


def test_vector_store_check_looks_at_the_knowledge_bases_the_agent_reads(state):
    state.rag.add_store("qremote", "Qdrant", "qdrant", {"url": "http://qdrant.example.com:6333"}, {})
    state.rag.add_store("qlocal", "Local", "qdrant", {"url": "http://localhost:6333"}, {})
    make(state, "plain")
    make(state, "local_only", rag=["qlocal"])
    make(state, "remote", rag=["qremote"])
    make(state, "everything", rag=["*"])
    assert st(state, "plain", "vector-stores") == "na" and st(state, "local_only", "vector-stores") == "pass"
    assert "qremote" in chk(card(state, "remote"), "vector-stores")["detail"] and chk(card(state, "remote"), "vector-stores")["status"] == "warn"
    assert st(state, "everything", "vector-stores") == "warn"


def test_data_source_check(state, tmp_path):
    state.data.set_source({"id": "shop", "kind": "sqlite", "config": {"path": str(tmp_path / "x.db")}, "redact": True})
    state.data.set_source({"id": "raw", "kind": "sqlite", "config": {"path": str(tmp_path / "y.db")}, "redact": False})
    state.data.set_product({"id": "orders", "name": "Orders", "source": "shop", "classification": "restricted"})
    state.data.set_product({"id": "faq", "name": "FAQ", "source": "raw", "classification": "public"})
    make(state, "plain")
    make(state, "narrow", data=["shop"])
    make(state, "leaky", data=["raw"])
    make(state, "broad", data=["*"])
    assert st(state, "plain", "agent-data") == "na"
    c = chk(card(state, "narrow"), "agent-data")
    assert c["status"] == "pass" and "restricted data product" in c["detail"] and c["link"] == "/agents/?agent=narrow#ag-data"
    assert "not masked for raw" in chk(card(state, "leaky"), "agent-data")["detail"] and st(state, "leaky", "agent-data") == "warn"
    assert "every data source" in chk(card(state, "broad"), "agent-data")["detail"] and st(state, "broad", "agent-data") == "warn"
    state.data.set_source({"id": "raw", "kind": "sqlite", "redact": True})
    state.data.set_product({"id": "faq", "name": "FAQ", "source": "raw", "classification": "public"})
    state.agents.update("leaky", {"data": ["raw"]})
    c = chk(card(state, "leaky"), "agent-data")
    assert c["status"] == "pass" and "1 source(s): read-only, row-limited and masked" in c["detail"]


# ------------------------------------------------------------------------------------------ identity, access, runtime
def test_identity_check_follows_the_overall_one_and_flags_scopes_with_no_tool_behind_them(state):
    make(state, "plain")
    make(state, "greedy", identity={"scopes": ["files:write", "shell:run", "mcp:call", "agents:delegate"]})
    make(state, "fitting", tools=["write_file"], mcp=["fetch"], sub_agents=["plain"], identity={"scopes": ["files:write", "mcp:call", "agents:delegate", "files:read"]})
    assert st(state, "plain", "identity") == SC.build(state)["checks"][[c["id"] for c in SC.build(state)["checks"]].index("identity")]["status"]
    g = chk(card(state, "greedy"), "identity")
    assert g["status"] == "warn" and "files:write, shell:run, mcp:call, agents:delegate" in g["detail"] and g["link"].endswith("#ag-identity")
    assert "no tool for" not in chk(card(state, "fitting"), "identity")["detail"]
    state.identity.set_config({"enforce": True})
    assert st(state, "plain", "identity") == "pass" and st(state, "greedy", "identity") == "warn"
    assert st(state, "fitting", "identity") == "pass"


def test_exposure_check_needs_the_switch_the_keys_and_the_agents_tools(state):
    make(state, "plain")
    make(state, "open_bot", api_access=True)
    make(state, "shell_bot", a2a=True, tools=["run_shell"])
    assert st(state, "plain", "agent-exposure") == "na"
    c = chk(card(state, "open_bot"), "agent-exposure")
    assert c["status"] == "pass" and "switch is off" in c["detail"] and c["link"].endswith("#ag-access")
    state.db.set_setting(ACCESS_KEY, json.dumps({"api_enabled": True, "a2a_enabled": True}))
    assert "No key lets anyone call it yet" in chk(card(state, "open_bot"), "agent-exposure")["detail"]
    narrow = state.apikeys.create("narrow", ["open_bot"])
    assert st(state, "open_bot", "agent-exposure") == "pass" and "1 key(s)" in chk(card(state, "open_bot"), "agent-exposure")["detail"]
    broad = state.apikeys.create("everything", ["*"])
    c = chk(card(state, "open_bot"), "agent-exposure")
    assert c["status"] == "warn" and "everything" in c["detail"] and c["link"] == "/settings/#access"
    c = chk(card(state, "shell_bot"), "agent-exposure")
    assert c["status"] == "warn" and "run_shell" in c["detail"]
    state.apikeys.update(broad["id"], {"revoked": True})
    assert st(state, "open_bot", "agent-exposure") == "pass"
    state.db.execute("UPDATE api_keys SET expires=? WHERE id=?", (time.time() - 5, narrow["id"]))
    assert "No key lets anyone call it yet" in chk(card(state, "open_bot"), "agent-exposure")["detail"]
    state.db.set_setting(ACCESS_KEY, json.dumps({"api_enabled": False, "a2a_enabled": True}))
    assert "switch is off" in chk(card(state, "open_bot"), "agent-exposure")["detail"]


def test_runtime_and_framework_checks(state):
    make(state, "plain")
    make(state, "pending", runtime="vertex")
    make(state, "box", runtime="docker")
    assert "runs on this PC." in chk(card(state, "plain"), "agent-runtime")["detail"]
    assert "not deployed" in chk(card(state, "pending"), "agent-runtime")["detail"] and st(state, "pending", "agent-runtime") == "pass"
    deployed(state, "box", "docker")
    c = chk(card(state, "box"), "agent-runtime")
    assert c["status"] == "pass" and "container on this PC" in c["detail"]
    assert st(state, "plain", "agent-framework") == "pass" and "built-in engine" in chk(card(state, "plain"), "agent-framework")["detail"]
    state.agents.update("plain", {"framework": "langgraph"})
    c = chk(card(state, "plain"), "agent-framework")
    assert c["status"] == "pass" and "LangGraph" in c["detail"] and "still apply" in c["detail"]
    state.agents.update("plain", {"framework": "claude_sdk"})
    c = chk(card(state, "plain"), "agent-framework")
    assert c["status"] == "warn" and "only partly" in c["detail"] and "spend caps" in c["detail"] and c["link"].endswith("#ag-access")


def test_evaluation_and_red_team_checks_are_about_this_agent(state):
    make(state, "tested")
    make(state, "untested")
    state.db.execute("INSERT INTO eval_runs(id,set_id,agent,started,status,summary) VALUES('e1','s','tested',?, 'done', ?)", (time.time(), '{"done": 2, "errors": 0}'))
    state.db.execute("INSERT INTO eval_runs(id,set_id,agent,started,status,summary) VALUES('e2','s','untested',?, 'done', ?)", (time.time() - 90 * 86400, '{"done": 2, "errors": 0}'))
    state.db.execute("INSERT INTO redteam_runs(id,tool,agent,started,status,requests) VALUES('r1','garak','tested',?, 'done', 4)", (time.time(),))
    assert st(state, "tested", "evals") == "pass" and st(state, "tested", "redteam") == "pass"
    assert st(state, "untested", "evals") == "warn" and "not been evaluated" in chk(card(state, "untested"), "evals")["detail"]
    assert st(state, "untested", "redteam") == "warn"


# ---------------------------------------------------------------------------------------------- ranking, summary, exports
def test_ranking_is_worst_first_with_the_top_issue(state):
    make(state, "plain")
    make(state, "risky", tools=["run_shell", "write_file"], guardrails=["none"])
    state.guardrails.set_enabled(True)
    rows = AC.ranking(state)
    assert [r["id"] for r in rows][0] == "risky" and [r["percent"] for r in rows] == sorted(r["percent"] for r in rows)
    assert {r["id"] for r in rows} == {a.id for a in state.agents.list()}
    top = rows[0]["top_issue"]
    assert top["status"] == "fail" and top["id"] == "guard-input" and set(top) == {"id", "title", "status", "detail", "fix", "link"}
    assert rows[0]["counts"]["fail"] >= 1 and rows[0]["tools"] == 2 and rows[0]["band"] in ("good", "middling", "low")
    assert AC.band(80) == "good" and AC.band(79) == "middling" and AC.band(50) == "middling" and AC.band(49) == "low" and AC.band(0) == "low"


def test_a_perfect_agent_has_no_top_issue(state, monkeypatch):
    make(state, "plain")
    ok = SC.Check("x", "X", ("LLM01",), "pass", "fine")
    monkeypatch.setattr(AC, "CHECKS", (("x", lambda s, a, x: ok),))
    rows = AC.ranking(state)
    assert all(r["top_issue"] is None and r["percent"] == 100 for r in rows)


def test_summary_counts_the_bands_and_names_the_weakest(state):
    make(state, "risky", tools=["run_shell"], guardrails=["none"])
    s = AC.summary(state)
    assert s["count"] == len(state.agents.list()) and s["lowest"]["id"] == AC.ranking(state)[0]["id"] and sum(s["bands"].values()) == s["count"]
    assert 0 <= s["average"] <= 100
    assert AC.summary(state, []) == {"count": 0, "average": None, "bands": {"good": 0, "middling": 0, "low": 0}, "lowest": None}
    assert SC.build(state)["agents"] == s                                                                  # the overall card carries the same summary


def test_brief_has_only_numbers(state):
    make(state, "plain")
    b = AC.brief(state, state.agents.get("plain"))
    assert set(b) == {"percent", "band", "fail", "warn"} and all(isinstance(v, (int, str)) for v in b.values())


def test_markdown_and_json_exports(state):
    make(state, "risky", tools=["run_shell"], guardrails=["none"])
    state.guardrails.set_enabled(True)
    c = card(state, "risky")
    md = AC.card_markdown(c)
    assert md.startswith("# Security scorecard: Risky (risky)") and "not applicable" in md and "### LLM01 Prompt Injection" in md and "(agent)" in md and "(shared)" in md and "Fix:" in md
    bare = {**c, "frameworks": [{"id": "x", "name": "X", "url": "u", "risks": [{"id": "R1", "name": "R", "status": "none", "checks": []}]}]}
    assert "No control in Agent Studio is mapped to this risk yet." in AC.card_markdown(bare) and "NOT COVERED" in AC.card_markdown(bare)
    assert json.loads(AC.to_json(c))["agent"]["id"] == "risky"
    rows = AC.ranking(state)
    cards = [card(state, r["id"]) for r in rows]
    report = AC.report_markdown(rows, cards, SC.build(state))
    assert report.startswith("# Agent Studio security report: all agents") and "Overall (app settings) score:" in report and "| Agent | Score |" in report
    assert "Nothing to fix." not in AC.report_markdown(rows[:1], cards[:1]) and "Overall" not in AC.report_markdown(rows[:1], cards[:1])
    clean = {**cards[0], "checks": [x for x in cards[0]["checks"] if x["status"] in ("pass", "na")]}
    assert "- Nothing to fix." in AC.report_markdown([{**rows[0], "top_issue": None}], [clean])
    assert "| none |" in AC.report_markdown([{**rows[0], "top_issue": None}], [clean])


# ------------------------------------------------------------------------------------------------------------------ API
def test_api_lists_agents_and_serves_each_card(state, client):
    make(state, "risky", tools=["run_shell"], guardrails=["none"])
    r = client.get("/api/security/agents").json()
    assert r["summary"]["count"] == len(r["agents"]) and r["agents"][0]["percent"] <= r["agents"][-1]["percent"] and r["agents"][0]["top_issue"]
    one = client.get("/api/security/agents/risky")
    assert one.status_code == 200 and one.json()["agent"]["id"] == "risky" and one.json()["checks"][0]["id"] == "guard-input"
    md = client.get("/api/security/agents/risky?format=md")
    assert md.status_code == 200 and "attachment" in md.headers["content-disposition"] and "agent-studio-security-risky.md" in md.headers["content-disposition"] and md.text.startswith("# Security scorecard: Risky")
    saved = client.get("/api/security/agents/risky?download=1")
    assert saved.status_code == 200 and "agent-studio-security-risky.json" in saved.headers["content-disposition"] and json.loads(saved.text)["agent"]["id"] == "risky"
    assert client.get("/api/security/agents/ghost").status_code == 404 and client.get("/api/security/agents/risky?format=pdf").status_code == 422
    allmd = client.get("/api/security/agents?format=md")
    assert allmd.status_code == 200 and allmd.text.startswith("# Agent Studio security report") and "agent-studio-agent-scorecards.md" in allmd.headers["content-disposition"]
    assert allmd.text.index("## Risky") < allmd.text.index("## Assistant")                                  # worst first
    full = client.get("/api/security/agents?format=full")
    assert full.status_code == 200 and "attachment" in full.headers["content-disposition"] and len(json.loads(full.text)["agents"]) == len(r["agents"])
    assert client.get("/api/security/agents?format=xml").status_code == 422
    assert "agents" in client.get("/api/security/scorecard").json()


# ----------------------------------------------------------------------------------------------------------- AI-BOM / ARD
def props(bom, ref):
    sv = next(s for s in bom["services"] if s["bom-ref"] == ref)
    return {p["name"].split(":", 1)[1]: p["value"] for p in sv["properties"] if p["name"].startswith("agentstudio:")}


def test_the_bom_carries_a_score_only_from_internal_up_and_never_the_details(state):
    make(state, "risky", tools=["run_shell"], guardrails=["none"])
    internal = props(B.build_full(state, "internal"), "agent:risky")
    assert 0 <= int(internal["security-score"]) <= 100 and int(internal["security-checks-failing"]) >= 1
    assert {k for k in internal if k.startswith("security-")} <= {"security-score", "security-checks-failing", "security-checks-partly"}      # numbers only
    public = props(B.build_full(state, "public"), "agent:risky")
    assert not any(k.startswith("security-") for k in public)


def test_the_discovery_catalog_shows_the_score_at_internal(state):
    from app.ard import site
    make(state, "risky", tools=["run_shell"], guardrails=["none"])
    bom = B.build_full(state, "internal")
    p = site.pmap(next(s for s in bom["services"] if s["bom-ref"] == "agent:risky"))
    assert int(p["security-score"]) == AC.brief(state, state.agents.get("risky"))["percent"]
