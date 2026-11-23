"""Per-agent AG-UI switches: rich components and the AG-UI stream endpoint, each on or off per agent (see app/agui_flags.py)."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import agui_flags as F
from app.agents import ARTIFACT_KEEP, ARTIFACT_RULES, AgentDef, artifact_rules
from app.agui import AguiTranslator
from app.chat import TurnTrace
from app.db import Database
from app.harness.tools import ToolContext
from app.main import State, create_app
from app.uikit.tools import UI_RULES, uikit_tools

TOOLS = ["read_file", "save_artifact", "render_ui", "ui_catalog"]


def run(c):
    return asyncio.run(c)


def agent(aid="shower", agui=None, **kw):
    return AgentDef(id=aid, name=aid.title(), tools=list(TOOLS), **({"agui": agui} if agui is not None else {}), **kw)


# ------------------------------------------------------------------------------------------------ the helper
def test_flags_default_to_on_and_ignore_bad_stored_values():
    assert F.flags(None) == F.flags({}) == F.flags("junk") == {"components": True, "endpoint": True}
    assert F.flags({"components": False, "endpoint": "no", "other": 1}) == {"components": False, "endpoint": True}


def test_normalise_checks_requests():
    assert F.normalise(None) == F.normalise({}) == {"components": True, "endpoint": True}
    assert F.normalise({"endpoint": False}) == {"components": True, "endpoint": False}
    with pytest.raises(ValueError, match="must be an object"):
        F.normalise("on")
    with pytest.raises(ValueError, match="unknown AG-UI setting 'speed'"):
        F.normalise({"speed": True})
    with pytest.raises(ValueError, match="components must be true or false"):
        F.normalise({"components": "yes"})


def test_tools_for_drops_only_the_component_tools_when_they_are_off():
    assert F.tools_for(agent()) == TOOLS
    off = F.tools_for(agent(agui={"components": False, "endpoint": True}))
    assert off == ["read_file", "save_artifact"]
    assert F.components_on(agent()) and F.endpoint_on(agent())
    assert not F.components_on(agent(agui={"components": False})) and not F.endpoint_on(agent(agui={"endpoint": False}))


def test_artifact_rules_keep_saving_but_drop_the_rich_blocks_when_components_are_off():
    assert artifact_rules(True) == ARTIFACT_RULES and artifact_rules() == ARTIFACT_RULES
    off = artifact_rules(False)
    assert off == ARTIFACT_KEEP and "save_artifact" in off and "```chart" not in off and "html-preview" not in off and "## Results" in off


# ------------------------------------------------------------------------------------------------ storage and the API
def test_old_agents_get_both_switches_on_and_values_round_trip(tmp_path):
    db = Database(tmp_path / "old.db")
    db.execute("UPDATE agents SET agui='{}'") if db.query("SELECT 1 FROM agents LIMIT 1") else None
    s = State(db)
    assert all(a.agui == {"components": True, "endpoint": True} for a in s.agents.list())           # seeded and migrated agents behave as before
    made = s.agents.create(agent("rt", agui={"components": False}))
    assert made.agui == {"components": False, "endpoint": True}
    assert s.agents.update("rt", {"agui": {"endpoint": False}}).agui == {"components": True, "endpoint": False}
    with pytest.raises(ValueError, match="unknown AG-UI setting"):
        s.agents.update("rt", {"agui": {"bogus": True}})


def test_a_database_without_the_column_is_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "m.db"
    State(Database(path))
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE agents DROP COLUMN agui")
    con.commit()
    con.close()
    s = State(Database(path))                                                                        # opening it adds the column back, both switches on
    assert s.agents.get("engineer").agui == {"components": True, "endpoint": True}


def test_the_api_reads_and_writes_the_switches(client, state):
    assert all(a["agui"] == {"components": True, "endpoint": True} for a in client.get("/api/agents").json())
    r = client.post("/api/agents", json={"id": "plain", "name": "Plain", "agui": {"components": False}})
    assert r.status_code == 200 and r.json()["agui"] == {"components": False, "endpoint": True}
    assert client.put("/api/agents/plain", json={"agui": {"components": True, "endpoint": False}}).json()["agui"] == {"components": True, "endpoint": False}
    assert client.put("/api/agents/plain", json={"agui": {"nope": True}}).status_code == 422
    assert client.put("/api/agents/plain", json={"agui": {"endpoint": 3}}).status_code == 422


def test_new_agents_start_with_the_default_chosen_in_settings(client, state):
    assert client.post("/api/agents", json={"id": "n1", "name": "N1"}).json()["agui"] == {"components": True, "endpoint": True}
    cfg = client.put("/api/uikit/config", json={"new_agents": {"components": False, "endpoint": False}})
    assert cfg.status_code == 200 and cfg.json()["new_agents"] == {"components": False, "endpoint": False}
    assert client.post("/api/agents", json={"id": "n2", "name": "N2"}).json()["agui"] == {"components": False, "endpoint": False}
    assert client.post("/api/agents", json={"id": "n3", "name": "N3", "agui": {"components": True, "endpoint": True}}).json()["agui"] == {"components": True, "endpoint": True}
    assert client.put("/api/uikit/config", json={"new_agents": {"components": "x"}}).status_code == 400
    assert client.get("/api/uikit/config").json()["new_agents"] == {"components": False, "endpoint": False}


# ------------------------------------------------------------------------------------------------ the tools and instructions
def _ctx(state, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    c = ToolContext(ws, state.chat.sandbox_for(ws), state.skills, state.mcp, uikit=state.uikit, on_ui=lambda spec: True)
    return c


def built(state, tmp_path, d):
    state.chat.agents.create(d) if state.chat.agents.get(d.id) is None else None
    return run(state.chat._build(state.chat.agents.get(d.id), chat_provider="mock", root_model=None, max_tokens=None, ctx=_ctx(state, tmp_path), turn=TurnTrace(), toolsets=[]))


def names(a):
    return {getattr(t, "__name__", getattr(t, "name", "")) for t in a.tools}


def test_components_on_gives_the_tools_and_the_teaching(state, tmp_path):
    a = built(state, tmp_path, agent("on1"))
    assert {"render_ui", "ui_catalog"} <= names(a) and UI_RULES in a.instruction and "```chart" in a.instruction


def test_components_off_removes_the_tools_the_teaching_and_the_rich_block_lines(state, tmp_path):
    a = built(state, tmp_path, agent("off1", agui={"components": False}))
    assert "render_ui" not in names(a) and "ui_catalog" not in names(a) and "read_file" in names(a) and "save_artifact" in names(a)
    assert UI_RULES not in a.instruction and "```chart" not in a.instruction and "html-preview" not in a.instruction
    assert "save_artifact" in a.instruction                                                          # keeping results is not part of AG-UI


def test_the_endpoint_switch_alone_leaves_the_components_alone(state, tmp_path):
    a = built(state, tmp_path, agent("ep1", agui={"endpoint": False}))
    assert "render_ui" in names(a) and UI_RULES in a.instruction


def test_render_ui_refuses_when_called_for_an_agent_with_components_off(state):
    ctx = ToolContext(None, None, None, None, agent_id="a", uikit=state.uikit, on_ui=lambda spec: True)
    ctx.agui_components = False
    t = uikit_tools(ctx)
    for r in (run(t["render_ui"]({"type": "chart.bar", "x": ["a"], "series": [{"name": "s", "values": [1]}]})), run(t["ui_catalog"]())):
        assert r["ok"] is False and "switched off" in r["errors"][0]


@pytest.mark.parametrize("agui", [{"components": False, "endpoint": True}, {"components": True, "endpoint": False}, {"components": True, "endpoint": True}])
def test_a_helper_an_agent_creates_inherits_its_switches(state, tmp_path, agui):
    seen: dict = {}

    async def stop(d, **kw):                                                                         # look at the helper's definition, then stop before it runs
        seen["helper"] = d
        raise KeyError("stop here")
    state.chat._build = stop                                                                         # type: ignore[method-assign]
    boss = AgentDef(id="boss", name="Boss", tier="flash", can_spawn=True, tools=list(TOOLS), agui=agui)
    spawn = state.chat._spawn_tool(boss, _ctx(state, tmp_path), TurnTrace(), [], "mock", None, 0, (), F.tools_for(boss), [])
    res = run(spawn(name="helper", instruction="do", task="t", tools=["read_file"], mcp=[], tier="flash", tool_context=None))
    assert res["ok"] is False and seen["helper"].agui == agui


def test_the_tools_an_agent_may_grant_a_helper_leave_out_the_component_tools_when_they_are_off(state, tmp_path):
    off = built(state, tmp_path, agent("boss1", agui={"components": False}, can_spawn=True))
    on = built(state, tmp_path, agent("boss2", can_spawn=True))
    assert "You may grant it only: tools" in off.instruction and "render_ui" not in off.instruction.split("You may grant it only: tools")[1].split("MCP servers")[0]
    assert "render_ui" in on.instruction.split("You may grant it only: tools")[1].split("MCP servers")[0]


def test_framework_instructions_follow_the_switch(state):
    from app.frameworks import turn as T
    turn = type("T", (), {"recall_text": ""})()
    on, off = agent("fw1"), agent("fw2", agui={"components": False})
    assert "```chart" in T.instructions_for(state.chat, on, turn, ["save_artifact", "render_ui"])
    t = T.instructions_for(state.chat, off, turn, ["save_artifact"])
    assert "```chart" not in t and "save_artifact" in t


# ------------------------------------------------------------------------------------------------ the AG-UI stream
def test_rich_blocks_and_components_become_activity_only_when_components_are_on():
    def feed(tr):
        out = []
        for ev in ({"type": "session", "session_id": "s1"}, {"type": "token", "text": "```chart\n{\"type\": \"bar\", \"data\": [{\"label\": \"a\", \"value\": 1}]}\n```\n", "run": "root"},
                   {"type": "ui", "spec": {"type": "chart.bar"}}, {"type": "done", "reply": "x", "session_id": "s1"}):
            out += [e.model_dump(by_alias=True) if hasattr(e, "model_dump") else e for e in tr.feed(ev)]
        out += [e.model_dump(by_alias=True) for e in tr.close()]
        return [e for e in out if e.get("type") == "ACTIVITY" or e.get("type") == "ACTIVITY_SNAPSHOT"]
    on = feed(AguiTranslator("t", "r"))
    off = feed(AguiTranslator("t", "r", components=False))
    on_types = {e["activityType"] for e in on}
    assert "ui.component" in on_types
    assert not [e for e in off if str(e["activityType"]).startswith(("rich.", "ui."))]


@pytest.fixture
def web(state, tmp_path):
    with TestClient(create_app(state)) as c:
        c.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128, "enabled": True})
        c.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
        yield c


def post(web, agent_id, thread="th1", run_id="r1", message="hello"):
    return web.post("/api/agui", json={"threadId": thread, "runId": run_id, "state": {}, "messages": [{"id": "m", "role": "user", "content": message}],
                                       "tools": [], "context": [], "forwardedProps": {"provider": "mock", "agent": agent_id}})


def test_the_endpoint_refuses_an_agent_with_it_switched_off(web, state):
    web.post("/api/agents", json={"id": "closed", "name": "Closed", "agui": {"endpoint": False}})
    r = post(web, "closed")
    assert r.status_code == 403 and "AG-UI is switched off for the agent 'closed'" in r.json()["detail"]
    assert state.agui_threads == {} and state.chat.store.list(10) == []                                # nothing ran
    web.put("/api/agents/closed", json={"agui": {"endpoint": True}})
    assert post(web, "closed", thread="th2").status_code == 200


def test_the_endpoint_applies_to_a_chat_that_already_belongs_to_the_agent(web, state):
    web.post("/api/agents", json={"id": "later", "name": "Later"})
    assert post(web, "later", thread="th3").status_code == 200
    sid = state.agui_threads["th3"]
    web.put("/api/agents/later", json={"agui": {"endpoint": False}})
    r = web.post("/api/agui", json={"threadId": sid, "runId": "r2", "state": {}, "messages": [{"id": "m", "role": "user", "content": "again"}], "tools": [], "context": [],
                                    "forwardedProps": {"provider": "mock"}})
    assert r.status_code == 403                                                                      # continuing the chat names no agent: its stored agent is checked


def test_an_agent_with_components_off_still_streams_plain_text(web):
    web.post("/api/agents", json={"id": "quiet", "name": "Quiet", "agui": {"components": False}})
    r = post(web, "quiet", thread="th4")
    kinds = [json.loads(line[6:])["type"] for line in r.text.splitlines() if line.startswith("data: ")]
    assert r.status_code == 200 and kinds[-1] == "RUN_FINISHED"


def test_a_resumed_run_looks_up_the_agent_of_its_chat(web, state, monkeypatch):
    web.post("/api/agents", json={"id": "asker", "name": "Asker"})
    assert post(web, "asker", thread="th5").status_code == 200
    r = web.post("/api/agui", json={"threadId": "th5", "runId": "r9", "state": {}, "messages": [], "tools": [], "context": [], "forwardedProps": {},
                                    "resume": [{"interruptId": "i1", "status": "resolved", "payload": {}}]})
    assert r.status_code == 409                                                                      # nothing is waiting; the lookup itself did not fail


# ------------------------------------------------------------------------------------------------ discovery and the BOM
def test_the_bom_shows_a_switched_off_agent_and_says_nothing_about_the_others(web, state):
    web.post("/api/agents", json={"id": "mute", "name": "Mute", "agui": {"components": False, "endpoint": False}})
    from app.aibom.builder import build_bom
    bom = build_bom(state, "internal")

    def props(ref):
        sv = next(x for x in bom["services"] if x["bom-ref"] == ref)
        return {p["name"].split(":", 1)[-1]: p["value"] for p in sv["properties"]}
    assert props("agent:mute")["agui-components-off"] == "true" and props("agent:mute")["agui-endpoint-off"] == "true"
    assert "agui-components-off" not in props("agent:engineer") and "agui-endpoint-off" not in props("agent:engineer")


def test_the_catalog_descriptor_carries_the_flags():
    from app.ard import site
    src = open(site.__file__, encoding="utf-8").read()
    assert '"agui": {"components": p.get("agui-components-off") != "true"' in src
