"""Rich components: the kind table, validation and cleaning, settings, the API, the agent tools, a real turn, AG-UI events and the generated samples."""
from __future__ import annotations

import asyncio
import json
import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import rich
from app.agui import AguiTranslator
from app.harness.tools import ALL_TOOLS, ToolContext, build_tools
from app.uikit import kinds as K
from app.uikit.samples import SAMPLES
from app.uikit.service import UiKitError
from app.uikit.tools import MAX_PER_TURN, UI_RULES, fallback_table, uikit_tools
from app.uikit.validate import DEFAULT_LIMITS, clean_limits, validate

REPO = Path(__file__).resolve().parents[2]
AID = "0123456789ab"


def run(c):
    return asyncio.run(c)


def sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


# ------------------------------------------------------------------------------------------------ the table of kinds
def test_every_kind_has_a_sample_and_the_counts_match_the_plan():
    assert set(K.KINDS) == set(SAMPLES)
    count = lambda g: sum(1 for k in K.KINDS.values() if k.group == g)
    assert count("chart") >= 24 and count("card") >= 40 and count("layout") == 5 and count("map") == 1 and count("timeseries") == 1
    assert {k.group for k in K.KINDS.values()} == set(K.GROUPS)


def test_every_sample_validates_cleanly_and_keeps_its_type():
    for key, sample in SAMPLES.items():
        clean, errors, warnings = validate(sample)
        assert clean is not None and not errors and not warnings, (key, errors, warnings)
        assert clean["type"] == key and clean["version"] == 1


def test_the_generated_typescript_samples_are_up_to_date():
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "gen_uikit_samples.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, "run: python scripts/gen_uikit_samples.py"


def test_the_generator_writes_the_file(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_uikit", REPO / "scripts" / "gen_uikit_samples.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "s.ts"
    monkeypatch.setattr(mod, "OUT", out)
    monkeypatch.setattr(sys, "argv", ["x", "--check"])
    with pytest.raises(SystemExit) as e:
        runpy_main(mod)
    assert e.value.code == 1                                   # nothing written yet
    out.write_text(mod.render(), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--check"])
    with pytest.raises(SystemExit) as e:
        runpy_main(mod)
    assert e.value.code == 0
    monkeypatch.setattr(sys, "argv", ["x"])
    out.unlink()
    runpy_main(mod, expect_exit=False)
    assert out.exists() and "export const SAMPLES" in out.read_text(encoding="utf-8")


def runpy_main(mod, expect_exit=True):
    """Run the module's `if __name__ == "__main__"` block against the patched argv and OUT."""
    src = (REPO / "scripts" / "gen_uikit_samples.py").read_text(encoding="utf-8")
    tail = src[src.index('if __name__ == "__main__":'):].replace('if __name__ == "__main__":', "if True:")
    exec(compile(tail, "gen_main", "exec"), vars(mod))


def test_canonical_names_and_suggestions():
    assert K.canonical("chart.line") == "chart.line" and K.canonical(" Chart.Line ") == "chart.line" and K.canonical("chart/line") == "chart.line"
    assert K.canonical("chart", {"kind": "pie"}) == "chart.pie" and K.canonical("card", {"kind": "nope"}) is None and K.canonical(None) is None
    assert "chart.line" in K.suggestions("chart.lin") and K.suggestions("card.zzzzzzzz")[0].startswith("card.")
    assert K.suggestions("zzzzzzzz") == []


# ------------------------------------------------------------------------------------------------ validation
def test_a_spec_must_be_an_object_or_json_text():
    assert validate([1])[1] == ["a component spec is a JSON object with a 'type'"]
    assert validate("not json")[1] == ["the spec is not valid JSON"]
    clean, errors, _ = validate(json.dumps({"type": "card.alert", "text": "hi"}))
    assert clean["text"] == "hi" and not errors
    loop: list = []
    loop.append(loop)
    assert validate({"type": "json", "value": loop})[1] == ["the spec cannot be turned into JSON"]
    assert validate({"type": "json", "value": {1, 2}})[0]["value"] in (["1", "2"], ["2", "1"]) or isinstance(validate({"type": "json", "value": {1, 2}})[0]["value"], str)
    assert validate({"type": "json", "value": {"a": float("nan")}})[0]["value"] == {"a": None}


def test_unknown_and_incomplete_specs_are_refused_with_a_hint():
    _, e, _ = validate({"type": "chart.lin"})
    assert "unknown component type 'chart.lin'" in e[0] and "chart.line" in e[0] and "ui_catalog" in e[0]
    assert "did you mean" not in validate({"type": "zzzzzzzzzz"})[1][0]
    _, e, _ = validate({"type": "chart.bar", "title": "x"})
    assert "chart.bar needs 'x'+'series' or 'data'" in e[0] and "ui_catalog" in e[0]
    assert validate({"type": "chart", "kind": "pie", "data": [{"label": "a", "value": 1}]})[0]["type"] == "chart.pie"


def test_big_specs_are_refused_with_the_limit_named():
    huge = {"type": "table", "rows": [["x" * 100] * 10] * 200}
    clean, errors, _ = validate(huge, {"max_kb": 20})
    assert clean is None and "limit is 20 KB" in errors[0]
    assert validate(huge)[0] is not None


def test_strings_numbers_lists_and_depth_are_limited_and_cleaned():
    deep: object = "bottom"
    for _ in range(14):
        deep = {"n": deep}
    clean, _, warns = validate({"type": "json", "value": deep})
    assert any("nesting" in w for w in warns)
    clean, _, warns = validate({"type": "card.definition", "term": "a\x00b\x07c", "meaning": "m" * 25_000})
    assert clean["term"] == "abc" and len(clean["meaning"]) == 20_000 and any("cut to 20000" in w for w in warns)
    clean, _, warns = validate({"type": "chart.histogram", "values": [1.0, float("inf"), float("nan"), 3]})
    assert clean["values"] == [1.0, None, None, 3]
    clean, _, warns = validate({"type": "table", "rows": [[i] for i in range(50)]}, {"max_rows": 10, "max_items": 5})
    assert len(clean["rows"]) == 10 and any("'rows' was cut to 10" in w for w in warns)
    clean, _, warns = validate({"type": "chart.histogram", "values": list(range(50))}, {"max_items": 5})
    assert clean["values"] == [0, 1, 2, 3, 4] and any("'values' was cut" in w for w in warns)
    clean, _, warns = validate({"type": "json", "value": [list(range(9)) for _ in range(1)], "other": {"k" * 200: 1}})
    assert max(len(k) for k in clean["other"]) == 80
    assert validate({"type": "json", "value": (1, 2)})[0]["value"].startswith("(1")
    clean, _, _ = validate({"type": "json", "value": {"obj": object()}})
    assert clean["value"]["obj"].startswith("<object")


def test_keys_that_could_carry_behaviour_are_dropped_and_html_only_survives_in_the_html_type():
    clean, _, warns = validate({"type": "card.alert", "text": "x", "onclick": "alert(1)", "style": "x", "className": "y", "__proto__": {}, "dangerouslySetInnerHTML": "z", "html": "<b>"})
    assert set(clean) == {"type", "version", "text"} and sum("is not allowed" in w for w in warns) == 6
    clean, _, _ = validate({"type": "html", "html": "<h1>hi</h1><script>alert(1)</script>"})
    assert clean["html"].startswith("<h1>")                       # kept as text: the browser draws it only inside the empty-sandbox frame
    clean, _, warns = validate({"type": "html", "html": "x" * 250_000})
    assert len(clean["html"]) == 200_000


def test_links_may_only_be_https_mailto_tel_artifact_or_a_page_of_this_app():
    spec = {"type": "card.person", "name": "A", "links": [{"label": "ok", "url": "https://example.com"}, {"label": "mail", "url": "mailto:a@b.co"}, {"label": "tel", "url": "tel:+1"}, {"label": "art", "url": "artifact:abc"},
                                                          {"label": "app", "url": "/runs/"}, {"label": "hash", "url": "#top"}, {"label": "js", "url": "javascript:alert(1)"}, {"label": "http", "url": "http://x.co"},
                                                          {"label": "proto", "url": "//evil.example"}, {"label": "data", "url": "data:text/html,<script>"}], "image": "data:image/png;base64,AAAA"}
    clean, _, warns = validate(spec)
    urls = [x["url"] for x in clean["links"]]
    assert urls[:6] == ["https://example.com", "mailto:a@b.co", "tel:+1", "artifact:abc", "/runs/", "#top"] and urls[6:] == [None, None, None, None]
    assert clean["image"] == "data:image/png;base64,AAAA" and any("link in 'url' was removed" in w for w in warns)
    clean, _, _ = validate({"type": "card.person", "name": "A", "image": AID})
    assert clean["image"] == AID
    clean, _, _ = validate({"type": "card.person", "name": "A", "image": "https://x.co/a.png", "avatar": "javascript:1"})
    assert clean["image"] == "https://x.co/a.png" and clean["avatar"] is None
    clean, _, _ = validate({"type": "card.person", "name": "A", "url": "https://x.co"})
    assert clean["url"] == "https://x.co"


def test_secrets_are_removed_and_api_card_headers_are_masked():
    clean, _, warns = validate({"type": "card.definition", "term": "k", "meaning": "use AK" "IAIOSFODNN7EXAMPLE now"})
    assert "AKIA" not in clean["meaning"] and any("looked like a secret" in w for w in warns)
    clean, _, _ = validate({"type": "card.request", "method": "GET", "url": "https://x.co", "headers": {"Authorization": "Bearer abc", "X-Api-Key": "k", "Cookie": "c", "Accept": "json", "Set-Token": "t"},
                            "response_headers": {"set-cookie": "s", "content-type": "x"}})
    assert clean["headers"] == {"Authorization": "••••", "X-Api-Key": "••••", "Cookie": "••••", "Accept": "json", "Set-Token": "••••"}
    assert clean["response_headers"] == {"set-cookie": "••••", "content-type": "x"}
    other, _, _ = validate({"type": "json", "value": {"headers": {"Authorization": "Bearer abc"}}})
    assert other["value"]["headers"]["Authorization"] == "Bearer abc"                     # only the API card knows to mask header names


def test_layouts_validate_their_children_and_rebuild_them():
    ok = SAMPLES["layout.grid"]
    clean, errors, _ = validate(ok)
    assert [c["type"] for c in clean["children"]] == ["card.kpi", "card.kpi", "chart.sparkline", "card.alert"] and all(c["version"] == 1 for c in clean["children"])
    clean, _, _ = validate({"type": "layout.stack", "children": [{"type": "chart", "kind": "line", "data": [{"label": "a", "value": 1}]}] * 30})
    assert len(clean["children"]) == 24
    clean, _, _ = validate(SAMPLES["layout.tabs"])
    assert [t["child"]["type"] for t in clean["tabs"]] == ["chart.bar", "table"]
    clean, _, _ = validate({"type": "layout.tabs", "tabs": [{"label": "a" * 90, "child": {"type": "card.alert", "text": "x"}}] * 14})
    assert len(clean["tabs"]) == 12 and len(clean["tabs"][0]["label"]) == 60
    clean, _, _ = validate(SAMPLES["layout.split"])
    assert clean["left"]["type"] == "chart.pie" and clean["right"]["type"] == "card.kpi"
    assert "item 2 must be a component spec" in validate({"type": "layout.stack", "children": [SAMPLES["card.alert"], "nope"]})[1][0]
    assert "item 1: unknown component type" in validate({"type": "layout.stack", "children": [{"type": "nonsense"}]})[1][0]
    assert "item 1" in validate({"type": "layout.tabs", "tabs": [{"label": "t", "child": "x"}]})[1][0]
    assert "needs 'children'" in validate({"type": "layout.stack", "children": []})[1][0]
    _, _, warns = validate({"type": "layout.stack", "children": [{"type": "card.alert", "text": "x", "onclick": "y"}]})
    assert any("not allowed" in w for w in warns)


def test_limits_are_cleaned():
    assert clean_limits({"max_kb": 5, "max_rows": True, "x": 1, "max_items": -2}) == {"max_kb": 5}
    assert clean_limits(None) == {} and DEFAULT_LIMITS["max_rows"] == 5000


# ------------------------------------------------------------------------------------------------ settings
def test_settings_defaults_and_validation(state):
    s = state.uikit
    cfg = s.config()
    assert cfg["enabled"] and cfg["max_kb"] == 300 and cfg["basemap"]["kind"] == "none" and s.limits() == {"max_kb": 300, "max_rows": 5000}
    assert s.set_config({"max_kb": 400, "max_rows": 9000, "enabled": False})["max_kb"] == 400 and not s.config()["enabled"]
    for bad in ("x", {"nope": 1}, {"enabled": "yes"}, {"max_kb": 5}, {"max_rows": True}, {"max_kb": 99999}, {"basemap": "x"}, {"basemap": {"kind": "web"}},
                {"basemap": {"kind": "tiles", "tile_url": "http://a/{z}/{x}/{y}", "attribution": "o"}}, {"basemap": {"kind": "tiles", "tile_url": "https://a/{z}/{x}", "attribution": "o"}},
                {"basemap": {"kind": "tiles", "tile_url": "https://a/{z}/{x}/{y}.png", "attribution": ""}}, {"basemap": {"kind": "pmtiles", "pmtiles": "zz"}}):
        with pytest.raises(UiKitError):
            s.set_config(bad)
    ok = s.set_config({"basemap": {"kind": "tiles", "tile_url": "https://t.example/{z}/{x}/{y}.png", "attribution": "© Example"}})
    assert ok["basemap"]["kind"] == "tiles" and s.config()["basemap"]["attribution"] == "© Example"
    assert s.set_config({"basemap": {"kind": "pmtiles", "pmtiles": AID}})["basemap"]["pmtiles"] == AID
    state.db.set_setting("uikit_config", "{bad")
    assert s.config()["max_kb"] == 300
    state.db.set_setting("uikit_config", json.dumps([1]))
    assert s.config()["enabled"]
    state.db.set_setting("uikit_config", json.dumps({"basemap": "oops", "max_kb": 55}))
    assert s.config()["basemap"]["kind"] == "none" and s.config()["max_kb"] == 55


def test_the_api(client):
    assert client.get("/api/uikit/config").json()["enabled"]
    assert client.put("/api/uikit/config", json={"max_kb": 123}).json()["max_kb"] == 123
    assert client.put("/api/uikit/config", json={"max_kb": 1}).status_code == 400
    cat = client.get("/api/uikit/catalog").json()
    assert cat["groups"] == K.GROUPS and len(cat["kinds"]) == len(K.KINDS) and cat["kinds"][0]["shape"]
    one = client.get("/api/uikit/catalog/chart.line").json()
    assert one["example"]["type"] == "chart.line" and one["group"] == "chart"
    assert client.get("/api/uikit/catalog/nope").status_code == 404
    ok = client.post("/api/uikit/validate", json=SAMPLES["card.kpi"]).json()
    assert ok["ok"] and ok["spec"]["type"] == "card.kpi" and not ok["errors"]
    bad = client.post("/api/uikit/validate", json={"type": "chart.bar"}).json()
    assert not bad["ok"] and bad["spec"] is None and "needs" in bad["errors"][0]
    client.put("/api/uikit/config", json={"max_kb": 20})
    big = client.post("/api/uikit/validate", json={"type": "table", "rows": [["x" * 100] * 10] * 200}).json()
    assert not big["ok"] and "20 KB" in big["errors"][0]


def test_the_settings_page_can_export_and_import_it(client, state):
    client.put("/api/uikit/config", json={"max_rows": 7000})
    exported = client.get("/api/settings/export").json()
    assert exported["settings"]["uikit_config"]["max_rows"] == 7000
    client.put("/api/uikit/config", json={"max_rows": 100})
    r = client.post("/api/settings/import", json={"data": exported, "dry_run": False}).json()
    assert not [e for e in r["errors"] if e["key"] == "uikit_config"] and state.uikit.config()["max_rows"] == 7000
    bad = {"format": "agent-studio-settings", "settings": {"uikit_config": {"max_rows": 1}}}
    assert client.post("/api/settings/import", json={"data": bad, "dry_run": False}).json()["errors"][0]["key"] == "uikit_config"


# ------------------------------------------------------------------------------------------------ the tools
def tctx(state, **kw):
    shown: list = []
    ctx = ToolContext(None, None, None, None, agent_id="a", uikit=state.uikit, on_ui=lambda spec: (shown.append(spec) or True), **kw)
    return ctx, uikit_tools(ctx), shown


def test_the_tools_exist_only_while_the_kit_is_on(state, tmp_path):
    names = {"render_ui", "ui_catalog"}
    assert names <= set(ALL_TOOLS)
    off = ToolContext(tmp_path, None, None, None)
    assert not names & {t.__name__ for t in build_tools(off, list(ALL_TOOLS))}
    on = ToolContext(tmp_path, None, None, None, uikit=state.uikit)
    assert names <= {t.__name__ for t in build_tools(on, list(ALL_TOOLS))}
    state.uikit.set_config({"enabled": False})
    assert not names & {t.__name__ for t in build_tools(on, list(ALL_TOOLS))}
    assert "ui_catalog" in UI_RULES and "chart" in UI_RULES and "card" in UI_RULES


def test_render_ui_shows_a_valid_component(state):
    ctx, t, shown = tctx(state)
    r = run(t["render_ui"](SAMPLES["chart.bar"]))
    assert r["ok"] and r["type"] == "chart.bar" and "Do not repeat" in r["note"] and shown[0]["type"] == "chart.bar" and "warnings" not in r
    r = run(t["render_ui"]({"type": "card.alert", "text": "x", "onclick": "y"}))
    assert r["ok"] and r["warnings"] and shown[-1] == {"type": "card.alert", "version": 1, "text": "x"}


def test_render_ui_refuses_what_it_cannot_show_and_falls_back_to_a_table(state):
    ctx, t, shown = tctx(state)
    r = run(t["render_ui"]({"type": "chart.nope"}))
    assert not r["ok"] and "unknown component type" in r["errors"][0] and "ui_catalog" in r["hint"] and not shown
    r = run(t["render_ui"]({"type": "chart.bar", "title": "T", "rows": [["a", 1]]}))                # a chart that is missing its data but carries rows
    assert not r["ok"] and r["shown_as"] == "a plain table" and shown[-1]["type"] == "table" and "plain table" in r["note"]
    r = run(t["render_ui"]({"type": "chart.scatter", "x": ["a", "b"], "series": [{"name": "s", "values": [1, 2]}]}))     # needs points: shown as a table of x and series
    assert r["shown_as"] == "a plain table" and shown[-1]["columns"] == ["", "s"]
    assert not run(t["render_ui"]("not json"))["ok"]


def test_render_ui_stops_at_the_limit_and_without_a_chat(state):
    ctx, t, shown = tctx(state)
    ctx.on_ui = lambda spec: False
    r = run(t["render_ui"](SAMPLES["card.kpi"]))
    assert not r["ok"] and str(MAX_PER_TURN) in r["errors"][0]
    ctx.on_ui = None
    assert not run(t["render_ui"](SAMPLES["card.kpi"]))["ok"]


def test_ui_catalog_lists_describes_and_suggests(state):
    ctx, t, _ = tctx(state)
    cat = run(t["ui_catalog"]())
    assert cat["ok"] and set(cat["groups"]) == set(K.GROUPS) and cat["limits"]["max_kb"] == 300 and sum(len(v) for v in cat["groups"].values()) == len(K.KINDS)
    one = run(t["ui_catalog"]("Chart.Line"))
    assert one["ok"] and one["example"]["type"] == "chart.line" and one["required"] and "x" in one["shape"]
    grp = run(t["ui_catalog"]("card"))
    assert grp["ok"] and len(grp["types"]) >= 40 and grp["group"] == "card"
    pre = run(t["ui_catalog"]("chart.b"))
    assert not pre["ok"] and "chart.bar" in pre["did_you_mean"]
    bad = run(t["ui_catalog"]("zzzz"))
    assert not bad["ok"] and "unknown type" in bad["error"]
    assert run(t["ui_catalog"]("layout"))["group"] == "layout"


def test_fallback_table_shapes():
    assert fallback_table("x") is None and fallback_table({"type": "chart.bar"}) is None and fallback_table({"rows": []}) is None
    t = fallback_table({"title": "T", "columns": ["a"], "rows": [[1]]})
    assert t == {"type": "table", "title": "T", "rows": [[1]], "columns": ["a"]}
    assert fallback_table({"rows": [{"a": 1}]}) == {"type": "table", "title": "", "rows": [{"a": 1}]}
    assert fallback_table({"data": [{"label": "a", "value": 1}]})["rows"] == [{"label": "a", "value": 1}]
    assert fallback_table({"data": [1, 2]}) is None
    t = fallback_table({"x": ["a", "b", "c"], "series": [{"name": "s1", "values": [1, 2, 3]}, {"values": [4]}]})
    assert t["columns"] == ["", "s1", "Series 2"] and t["rows"] == [["a", 1, 4], ["b", 2, None], ["c", 3, None]]
    assert fallback_table({"x": ["a"], "series": [5]}) is None


# ------------------------------------------------------------------------------------------------ a real turn, rich fences and AG-UI
def test_a_real_turn_shows_a_component_stores_it_and_teaches_the_agent(client, state, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    spec = json.dumps({"type": "card.kpi", "label": "Users", "value": 12})
    r = client.post("/api/chat/stream", json={"provider": "mock", "agent": "engineer", "message": "!tool render_ui " + json.dumps({"spec": json.loads(spec)})})
    events = sse(r.text)
    uis = [e for e in events if e.get("type") == "ui"]
    assert len(uis) == 1 and uis[0]["spec"]["type"] == "card.kpi" and uis[0]["spec"]["value"] == 12
    done = next(e for e in events if e.get("type") == "done")
    assert done["uis"][0]["label"] == "Users"
    hist = client.get(f"/api/chat/sessions/{done['session_id']}").json()["messages"]
    assert next(m for m in hist if m["role"] == "bot")["meta"]["uis"][0]["type"] == "card.kpi"
    plain = client.post("/api/chat", json={"provider": "mock", "agent": "engineer", "message": "hello"})
    assert "## Rich components" in plain.json()["runs"][0]["instruction"]
    assert plain.json()["uis"] == []


def test_turns_stop_showing_components_after_the_limit():
    from app.chat import TurnTrace
    t = TurnTrace.__new__(TurnTrace)
    t.uis, t.emit = [], lambda ev: None
    assert all(t.ui_event({"type": "card.alert"}) for _ in range(30)) and t.ui_event({"type": "card.alert"}) is False and len(t.uis) == 30


def test_ui_fences_become_validated_blocks():
    good = rich.block_content("ui", json.dumps(SAMPLES["card.kpi"]))
    assert good["type"] == "card.kpi" and rich.block_content("ui", "{not json") is None and rich.block_content("ui", json.dumps({"type": "chart.bar"})) is None
    text = f"x\n```ui\n{json.dumps(SAMPLES['chart.pie'])}\n```\n```ui\nbroken\n```"
    blocks = rich.extract_blocks(text)
    assert [k for k, _ in blocks] == ["ui"] and blocks[0][1]["type"] == "chart.pie"


def test_components_reach_ag_ui_clients_as_activity_events():
    tr = AguiTranslator("th", "r1")
    out = tr.feed({"type": "ui", "spec": {"type": "card.kpi", "version": 1, "label": "L", "value": 1}, "seq": 2})
    act = next(e for e in out if str(e.type).endswith("ACTIVITY_SNAPSHOT"))
    assert act.activity_type == "ui.component" and act.content["type"] == "card.kpi"
    tr2 = AguiTranslator("th", "r2")
    tr2.feed({"type": "text", "delta": "Look:\n```ui\n" + json.dumps(SAMPLES["chart.pie"]) + "\n```\n", "run_id": "root"})
    closing = tr2.close()
    acts = [e for e in closing if str(e.type).endswith("ACTIVITY_SNAPSHOT")]
    assert acts and acts[0].activity_type == "ui.component" and acts[0].content["type"] == "chart.pie"
    assert tr.feed({"type": "ui"})[-1].content == {}


def test_the_agent_gets_plain_language_labels(state):
    from app.easy.agent_templates import TOOL_PLAIN as TOOL_TEXT
    assert TOOL_TEXT["render_ui"][0] and TOOL_TEXT["ui_catalog"][2] == "safe"
