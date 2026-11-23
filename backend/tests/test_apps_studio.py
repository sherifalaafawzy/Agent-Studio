"""The smarter builder: lint, themes and components, the best-model choice, and the store's plan / verify / undo / connect features."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import types

import pytest

from app.agents import APP_BUILDER_TOOLS, NEW_APP_BUILDER, OLD_APP_BUILDER, AgentStore
from app.apiaccess.keys import ApiKeys
from app.apps import choose, design, lint as L, studio
from app.apps import manifest as M
from app.apps import templates as T
from app.apps.errors import AppError
from app.apps.store import AppStore
from app.apps.tools import REWRITE_MIN_LINES, app_tools, rewrote_most
from app.datasources.service import DataService
from app.db import Database
from app.harness.tools import ToolContext


def run(c):
    return asyncio.run(c)


class Agents:
    def __init__(self, *items):
        self.items = {a.id: a for a in items}

    def get(self, i):
        return self.items.get(i)

    def list(self):
        return list(self.items.values())

    def update(self, i, patch):
        self.items[i].__dict__.update(patch)


def agent(i, api=True):
    return types.SimpleNamespace(id=i, name=i.title(), description=f"the {i} agent", api_access=api, runtime="local")


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.db")


@pytest.fixture
def store(db, tmp_path):
    s = AppStore(db, home=tmp_path / "home", keys=ApiKeys(db), agents=Agents(agent("coder"), agent("quiet", api=False), agent("app_builder")))
    s.data = DataService(db)
    return s


@pytest.fixture
def app(store):
    return store.create("Studio", "static-dashboard", "d", "coder")


@pytest.fixture
def sqlite_file(tmp_path):
    f = tmp_path / "sales.db"
    c = sqlite3.connect(f)
    c.execute("CREATE TABLE orders(region TEXT, total INTEGER)")
    c.executemany("INSERT INTO orders VALUES(?,?)", [("n", 1), ("s", 2)])
    c.commit()
    c.close()
    return f


# ------------------------------------------------------------------------------------------------ lint
def page(body="", head=""):
    return f"<!doctype html><html><head>{head}</head><body>{body}</body></html>".encode()


def messages(files, manifest=None):
    return [(p["level"], p["text"]) for p in L.lint(files, manifest)]


def test_a_clean_template_has_no_lint_problems():
    for tid in T.TEMPLATES:
        if T.TEMPLATES[tid]["type"] != "static":
            continue
        files = {k: v.encode() for k, v in T.render(tid, "X", "", "coder").items()}
        man = M.clean_manifest(json.loads(files["app.json"]))
        assert L.lint(files, man) == [], tid


def test_missing_references_inline_code_and_duplicate_ids_are_found():
    files = {"public/index.html": page('<img src="logo.png"><a href="about.html">x</a><a href="#top">t</a><a href="https://x.com">e</a><a href="mailto:a@b.c">m</a>'
                                       '<div id="a"></div><div id="a"></div><button onclick="go()">go</button><script>var x=1</script><script type="application/json">{}</script>',
                                       '<script src="app.js"></script><link rel="stylesheet" href="s.css?v=1"><link rel="icon" href="data:,"><link rel="preload" href="nope.css">'
                                       '<script src="//cdn.x.com/a.js"></script>')}
    got = messages(files)
    text = " | ".join(t for _, t in got)
    assert "script 'app.js' points at a file that does not exist (public/app.js)" in text and "link 's.css?v=1'" in text and "img 'logo.png'" in text and "link to 'about.html'" in text
    assert "inline <script>" in text and "inline event handlers (<button onclick=" in text and "the id 'a' is used more than once" in text
    assert "https://x.com" not in text and "mailto" not in text and "cdn.x.com" not in text and "nope.css" not in text
    ok = {"public/index.html": page('<a href="/about.html">a</a><a href="sub/">s</a><a href="x.html">x</a>'), "public/about.html": b"x", "public/sub/index.html": b"x", "public/x.html": b"x"}
    assert messages(ok) == []
    assert messages({"public/index.html": b"  \n"}) == [("error", "the page is empty")]


def test_unbalanced_tags_and_self_closing_tags():
    assert any("unbalanced" in t for _, t in messages({"public/index.html": page("<div><span></div>")}))
    assert any("unbalanced" in t for _, t in messages({"public/index.html": page("<div>")}))
    assert any("unbalanced" in t for _, t in messages({"public/index.html": page("</p>")}))
    assert messages({"public/index.html": page('<svg><path d="M0"/></svg><br><input type="text"><p>a</p>')}) == []


def test_html_that_cannot_be_read_is_reported_not_raised(monkeypatch):
    def boom(self, data):
        raise RuntimeError("bad")
    monkeypatch.setattr(L._Page, "feed", boom)
    assert messages({"public/index.html": page("x")}) == [("warn", "could not read the page structure: bad")]


@pytest.mark.parametrize("code,bad", [
    ("function a(){ return [1,2].map(x => ({v: x})); }", None),
    ("var s = `a ${ b({c: 1}) } d ${e}`; var t = 'it\\'s'; var u = \"x\";", None),
    ("var r = /[)}]\\//g.test(s); var q = a / b / c; if (x) { y = (1) / 2 }", None),
    ("/* ( */ // ) {\nvar z = 1;", None),
    ("var t = `unterminated ${ x }", None),
    ("return /re/.test(x); typeof /x/", None),
    ("a(", "never closed"), ("}", "unexpected '}'"), ("if (x) { a(]", "unexpected ']'"), ("var a = { b: [1, 2 };", "unexpected '}'"), ("/* open", None),
    ("var s = `x ${ a(`inner ${ b }`) } y`;", None), ("var s = 'a\nb(';", "never closed"),
])
def test_bracket_scan(code, bad):
    got = L.bracket_problem(code)
    assert (got is None) if bad is None else (bad in got)


def test_css_and_code_checks_in_files():
    man = {"permissions": {"network": "none", "allow": []}, "agents": [], "data": []}
    files = {"public/app.js": b"fetch('https://api.example.com/x'); AgentClient.ask('a','b'); DataClient.query('s','select 1'); a(", "public/style.css": b"a { color: red; /* } */ content: '}'; ",
             "public/agent-client.js": b"AgentClient = 1; (", "public/ui.js": b"(", "public/index.html": page("<p>x</p>")}
    text = " | ".join(t for _, t in messages(files, man))
    assert "it calls https://api.example.com, which the preview blocks" in text and "lists no agents" in text and "lists none" in text and "may be a syntax error" in text
    assert "stylesheet looks unbalanced: 1 '{' never closed" in text and text.count("may be a syntax error") == 3
    allow = {**man, "permissions": {"network": "allowlist", "allow": ["https://api.example.com"]}, "agents": [{"id": "a"}], "data": [{"id": "s"}]}
    assert messages({"public/app.js": b"fetch('https://api.example.com/x'); AgentClient.ask(); DataClient.query()"}, allow) == []
    assert any("calls https://other.com" in t or "https://other.com" in t for _, t in messages({"public/app.js": b"fetch('https://other.com/x')"}, allow))
    assert L.css_problem("a { }") is None and "extra '}'" in L.css_problem("a { } }")
    assert L.local_target("public/index.html", "https://a.b") is None and L.local_target("public/sub/p.html", "../x.js") == "public/x.js" and L.local_target("public/index.html", "") is None


# ------------------------------------------------------------------------------------------------ themes and components
def test_themes_blocks_and_current():
    for k in design.THEMES:
        css = design.apply_theme("body{}", k, "serif", "round")
        assert design.current_theme(css) == dict(theme=k, font="serif", radius="round") and L.css_problem(css) is None
    one = design.apply_theme("a{}", "ocean")
    two = design.apply_theme(one, "forest", "mono", "sharp")
    assert two.count(design.MARK_START) == 1 and "forest" in two and "ocean" not in two and two.startswith("a{}")
    assert design.current_theme("a{}") == dict(theme="", font="", radius="") and design.current_theme(None) == dict(theme="", font="", radius="")
    for kw, msg in (({"theme": "x"}, "theme must"), ({"font": "x"}, "font must"), ({"radius": "x"}, "radius must")):
        with pytest.raises(ValueError, match=msg):
            design.theme_block(**kw)
    c = design.catalog()
    assert len(c["themes"]) == len(design.THEMES) and {f["id"] for f in c["fonts"]} == set(design.FONTS) and c["radii"] == list(design.RADII)


def test_components_bundle_merge_and_wiring():
    assert [c["id"] for c in design.component_list()] == list(design.COMPONENTS) and all(c["usage"] for c in design.component_list())
    css, js = design.bundle(["chart", "stats"])
    assert ".ui-chart" in css and ".ui-stats" in css and ".ui-table" not in css and "UI.chart" in js and "UI.table" not in js and "(window)" in js
    assert design.merge_names("", ["table"]) == ["table", "toast"]
    assert design.merge_names(css, ["tabs"]) == ["stats", "chart", "tabs"]
    assert design.merge_names("not ours", ["hero"]) == ["hero"]
    with pytest.raises(ValueError, match="unknown component 'nope'"):
        design.bundle(["nope"])
    html = '<html><head><title>x</title></head><body><p>a</p></body></html>'
    w = design.wire_html(html)
    assert w.count("ui.css") == 1 and w.count("ui.js") == 1 and w.index("ui.css") < w.index("</head>") and w.index("ui.js") < w.index("</body>") and design.wire_html(w) == w
    bare = design.wire_html("<p>x</p>")
    assert bare.startswith('<link rel="stylesheet" href="ui.css">') and bare.endswith('<script src="ui.js"></script>\n')


# ------------------------------------------------------------------------------------------------ choosing the model
def prov(i, kind, enabled=True, ready=True, pro="m-pro"):
    return types.SimpleNamespace(id=i, label=i.title(), kind=kind, enabled=enabled, pro_model=pro, public=lambda: {"auth_ready": ready})


def tm(p, tier, custom=None):
    return p.pro_model or "default"


def test_best_is_the_first_ready_provider_in_the_ranking():
    provs = [prov("local", "ollama"), prov("oa", "openai", ready=False), prov("an", "anthropic", enabled=False), prov("vx", "vertex_ai"), prov("odd", "weird")]
    opts = choose.options(provs, tm)
    assert [o["provider"] for o in opts] == ["oa", "vx", "local", "odd"] and not opts[0]["ready"]
    assert choose.best(provs, tm)["provider"] == "vx" and choose.best([prov("x", "openai", ready=False)], tm) is None
    assert choose.effective({"mode": "best"}, provs, tm) == dict(provider="vx", label="Vx", kind="vertex_ai", model="m-pro", source="best")
    custom = choose.effective({"mode": "custom", "provider": "local", "model": "llama"}, provs, tm)
    assert custom["provider"] == "local" and custom["model"] == "llama" and custom["source"] == "custom"
    assert choose.effective({"mode": "custom", "provider": "local", "model": ""}, provs, tm)["model"] == "m-pro"
    fall = choose.effective({"mode": "custom", "provider": "oa", "model": "x"}, provs, tm)
    assert fall["provider"] == "vx" and "not available" in fall["source"]
    assert choose.effective({"mode": "custom", "provider": "gone"}, provs, tm)["provider"] == "vx"
    assert choose.effective({"mode": "best"}, [], tm) == dict(provider="", label="", kind="", model="", source="none")


# ------------------------------------------------------------------------------------------------ plan, model, meta
GOOD_PLAN = {"title": "Sales board", "summary": "Shows sales.", "steps": ["Pick template", {"title": "Build it", "done": True}, "Check"], "pages": ["index.html"], "features": ["chart"],
             "data": ["data.json"], "agents": ["coder"]}


def test_plan_is_saved_cleaned_and_ticked_off(store, app):
    a = app["id"]
    assert store.meta(a) == {"plan": None, "model": {"mode": "best", "provider": "", "model": ""}, "fix_round": 0}
    plan = store.set_plan(a, GOOD_PLAN)
    assert [s["done"] for s in plan["steps"]] == [False, True, False] and plan["pages"] == ["index.html"] and store.meta(a)["plan"]["title"] == "Sales board"
    assert store.plan_done(a, [1, 3])["steps"][0]["done"] is True
    for bad, msg in (([1, 2, 3, 4], "step numbers from 1 to 3"), ([0], "step numbers"), ([True], "step numbers"), ([], "step numbers"), ("x", "step numbers")):
        with pytest.raises(AppError, match=msg):
            store.plan_done(a, bad)
    for patch, msg in (({"steps": []}, "1 to 20 steps"), ({"steps": "x"}, "1 to 20 steps"), ({"title": ""}, "title must be text and not empty"), ({"title": 3}, "title"),
                       ({"summary": 3}, "summary"), ({"pages": "x"}, "pages must be a list"), ({"pages": [3]}, "each of pages"), ({"steps": [""]}, "each step"),
                       ({"features": ["x"] * 21}, "at most 20")):
        with pytest.raises(AppError, match=msg):
            store.set_plan(a, {**GOOD_PLAN, **patch})
    with pytest.raises(AppError, match="must be an object"):
        store.set_plan(a, [])
    minimal = store.set_plan(a, {"title": "T", "steps": ["a"]})
    assert minimal["pages"] == [] and minimal["summary"] == ""
    assert store.clear_plan(a) == {"plan": None} and store.meta(a)["plan"] is None
    with pytest.raises(AppError, match="no plan yet"):
        store.plan_done(a, [1])
    with pytest.raises(AppError, match="no such app"):
        store.set_plan("0" * 12, GOOD_PLAN)


def test_model_choice_and_forgetting(store, app, db):
    a = app["id"]
    assert store.set_model(a, "custom", "oa", " gpt ") == {"mode": "custom", "provider": "oa", "model": "gpt"} and store.get_model(a)["provider"] == "oa"
    assert store.set_model(a, "best", "ignored", "x") == {"mode": "best", "provider": "", "model": ""}
    for args, msg in ((("nope",), "best or custom"), (("custom", ""), "choose a provider"), (("custom", 3), "provider must be text"), (("custom", "p" * 61), None)):
        if msg:
            with pytest.raises(AppError, match=msg):
                store.set_model(a, *args)
    assert len(store.set_model(a, "custom", "p" * 61)["provider"]) == 60
    store.set_plan(a, GOOD_PLAN)
    store.add_console(a, "error", "x")
    store.delete(a)
    assert a not in json.loads(db.get_setting(studio.META_KEY)) and a not in store._console
    db.set_setting(studio.META_KEY, "not json")
    assert store._metas() == {}
    db.set_setting(studio.META_KEY, "[1]")
    assert store._metas() == {}


# ------------------------------------------------------------------------------------------------ what the preview reported
def test_console_reports_are_kept_deduplicated_limited_and_cleared_by_a_new_version(store, app):
    a = app["id"]
    assert store.console(a) == {"loaded": False, "errors": []}
    ev = []
    store._emit = lambda aid, e: ev.append(e)
    store.add_console(a, "ready", "page loaded")
    store.add_console(a, "error", "boom", "index.html", "app.js", 7)
    store.add_console(a, "error", "boom")
    store.add_console(a, "console.warn", "careful")
    c = store.console(a)
    assert c["loaded"] is True and [(e["kind"], e["count"]) for e in c["errors"]] == [("error", 2), ("console.warn", 1)] and c["errors"][0]["line"] == 7
    assert ev[0] == {"type": "console", "loaded": True, "errors": 0} and len(ev) == 3
    for i in range(60):
        store.add_console(a, "error", f"e{i}")
    assert len(store.console(a)["errors"]) == studio.MAX_CONSOLE
    store.snapshot(a, "again", "user")
    assert store.console(a) == {"loaded": False, "errors": []}
    with pytest.raises(AppError, match="no such app"):
        store.add_console("0" * 12, "error", "x")


def test_verify_reads_files_and_the_preview_and_counts_failed_rounds(store, app):
    a = app["id"]
    v = store.verify(a)
    assert v["ok"] is True and v["round"] == 0 and v["preview_loaded"] is False and "has not loaded" in v["report"] and v["give_up"] is False and v["max_rounds"] == 3
    store.add_console(a, "ready", "ok")
    clean = store.verify(a, count=True)
    assert clean["ok"] and clean["report"] == "No problems found." and clean["round"] == 0
    store.add_console(a, "console.warn", "just a warning")
    assert store.verify(a)["ok"]
    store.add_console(a, "error", "x is not defined", "index.html", "app.js", 3)
    store.add_console(a, "error", "x is not defined")
    rounds = [store.verify(a, count=True) for _ in range(5)]
    assert [r["ok"] for r in rounds] == [False] * 5 and [r["round"] for r in rounds] == [1, 2, 3, 4, 5] and [r["give_up"] for r in rounds] == [False, False, False, True, True]
    assert "(error, app.js:3): x is not defined (x2)" in rounds[0]["report"] and store.meta(a)["fix_round"] == 5
    assert store.reset_round(a) == {"round": 0} and store.meta(a)["fix_round"] == 0
    store.verify(a, count=True)
    assert store.meta(a)["fix_round"] == 1
    store.write(a, "public/app.js", "// the user fixed it")                                    # a save by hand is not a version...
    assert store.meta(a)["fix_round"] == 1
    store.snapshot(a, "mine", "agent:coder")
    assert store.meta(a)["fix_round"] == 1
    store.snapshot(a, "mine", "user")                                                          # ...but a version by the user starts the count again
    assert store.meta(a)["fix_round"] == 0


def test_verify_reports_lint_check_and_a_broken_manifest(store, app):
    a = app["id"]
    store.write(a, "public/index.html", page('<script src="gone.js"></script>').decode())
    store.write(a, "public/app.js", "function(")
    v = store.verify(a)
    assert not v["ok"] and any(p["level"] == "error" and "gone.js" in p["text"] for p in v["problems"]) and "- error in public/index.html" in v["report"] and "- warning in public/app.js" in v["report"]
    (store.root(a) / "app.json").write_text("{", encoding="utf-8")
    broken = store.verify(a)
    assert not broken["ok"] and "app.json is missing or not valid" in broken["report"]


# ------------------------------------------------------------------------------------------------ undo and compare
def test_undo_goes_back_and_undoing_an_undo_goes_forward(store, app):
    a = app["id"]
    assert store.get(a)["version"] == 1
    with pytest.raises(AppError, match="no earlier version"):
        store.undo(a)
    store.write(a, "public/app.js", "// two")
    store.snapshot(a, "two", "agent:coder")
    store.write(a, "public/app.js", "// three")
    store.snapshot(a, "three", "agent:coder")
    r = store.undo(a)
    assert store.read(a, "public/app.js")["content"] == "// two" and r["version"] == 4 and "(was v3)" in store.versions(a)[0]["note"]
    store.undo(a)
    assert store.read(a, "public/app.js")["content"] == "// three"
    c = store.compare(a, 2, 3)
    assert [(f["path"], f["status"]) for f in c] == [("public/app.js", "changed")] and c[0]["old"] == "// two" and c[0]["new"] == "// three"
    store.write(a, "public/new.bin", "x")
    store.snapshot(a, "added", "user")
    store.delete_file(a, "public/data.json")
    store.snapshot(a, "gone", "user")
    kinds = {f["path"]: f["status"] for f in store.compare(a, 3, store.get(a)["version"])}
    assert kinds["public/new.bin"] == "added" and kinds["public/data.json"] == "removed"
    (store.root(a) / "public" / "blob.dat").write_bytes(b"\x00\x01")
    store.snapshot(a, "blob", "user")
    n = store.get(a)["version"]
    assert next(f for f in store.compare(a, n - 1, n) if f["path"] == "public/blob.dat")["new"] is None
    with pytest.raises(AppError, match="not kept"):
        store.compare(a, 1, 999)


def test_undo_with_pruned_history_says_so(store, app):
    a = app["id"]
    store.set_config({"keep_versions": 5})
    for i in range(7):
        store.write(a, "public/app.js", f"// {i}")
        store.snapshot(a, str(i), "user")
    with pytest.raises(AppError, match="no earlier version"):
        store.db.execute("DELETE FROM app_versions WHERE app_id=? AND version<>?", (a, store.get(a)["version"]))
        store.undo(a)
    with pytest.raises(AppError, match="nothing to undo"):
        store.db.execute("DELETE FROM app_versions WHERE app_id=?", (a,))
        store.undo(a)


# ------------------------------------------------------------------------------------------------ themes and components
def test_theme_and_components_each_make_one_version(store, app):
    a = app["id"]
    info = store.theme_info(a)
    assert info["theme"] == "warm" and info["font"] == "system" and len(info["themes"]) == len(design.THEMES)
    v0 = store.get(a)["version"]
    r = store.set_theme(a, "ocean", "serif", "round")
    assert r["theme"] == "ocean" and r["font"] == "serif" and r["version"] == v0 + 1 and "--accent: #0b6fa4" in store.read(a, "public/style.css")["content"]
    assert store.versions(a)[0]["note"] == "theme: ocean, serif, round corners"
    with pytest.raises(AppError, match="theme must be one of"):
        store.set_theme(a, "nope")
    chat = store.create("Chat", "static-chat", "", "coder")["id"]
    out = store.add_components(chat, ["stats", "table"])
    assert set(out["usage"]) == {"stats", "table"} and "ui.css" in store.read(chat, "public/index.html")["content"] and "UI.table" in store.read(chat, "public/ui.js")["content"]
    again = store.add_components(chat, ["chart"])
    css = store.read(chat, "public/ui.css")["content"]
    assert ".ui-stats" in css and ".ui-chart" in css and ".ui-table" in css and again["version"] == out["version"] + 1
    for bad, msg in ((None, "list of component names"), ([], "list of component names"), ([3], "list of component names"), (["zzz"], "unknown component")):
        with pytest.raises(AppError, match=msg):
            store.add_components(chat, bad)
    no_index = store.create("Srv", "node-api")["id"]
    store.delete_file(no_index, "public/index.html")
    ch = store.component_changes(no_index, ["hero"])
    assert [c["path"] for c in ch] == ["public/ui.css", "public/ui.js"]


# ------------------------------------------------------------------------------------------------ connections
def test_connect_and_disconnect_an_agent(store):
    a = store.create("Chat", "static-chat")["id"]
    r = store.connect_agent(a, "coder", "answers")
    assert r["agent"] == "coder" and r["api_access"] is True and r["enabled_now"] is False
    assert store.manifest(a)["agents"] == [{"id": "coder", "purpose": "answers"}] and 'data-agent="coder"' in store.read(a, "public/index.html")["content"]
    quiet = store.connect_agent(a, "quiet")
    assert quiet["api_access"] is False and store.manifest(a)["agents"][1]["purpose"] == "The agent this app talks to" and store.agents.get("quiet").api_access is False
    on = store.connect_agent(a, "quiet", "", enable_api=True)
    assert on["enabled_now"] is True and store.agents.get("quiet").api_access is True
    assert [x["id"] for x in store.manifest(a)["agents"]] == ["coder", "quiet"]
    c = store.connections(a)
    assert {x["id"]: x["connected"] for x in c["agents"]} == {"coder": True, "quiet": True} and "app_builder" not in {x["id"] for x in c["agents"]}
    store.disconnect_agent(a, "quiet")
    assert [x["id"] for x in store.manifest(a)["agents"]] == ["coder"]
    with pytest.raises(AppError, match="does not use the agent"):
        store.disconnect_agent(a, "quiet")
    with pytest.raises(AppError, match="no such agent"):
        store.connect_agent(a, "ghost")
    store.agents = None
    with pytest.raises(AppError, match="no such agent"):
        store.connect_agent(a, "coder")
    assert store.connections(a)["agents"] == []


def test_connect_a_data_source(store, sqlite_file):
    a = store.create("Dash", "static-dashboard", "", "coder")["id"]
    assert store.connections(a)["data"] == []
    store.data.set_source({"id": "sales", "kind": "sqlite", "config": {"path": str(sqlite_file)}, "description": "orders"})
    store.data.set_source({"id": "off", "kind": "sqlite", "config": {"path": str(sqlite_file)}, "enabled": False})
    c = store.connections(a)["data"]
    assert {d["id"]: d["connected"] for d in c} == {"sales": False, "off": False}
    r = store.connect_data(a, "sales", "revenue by region")
    assert r["source"] == "sales" and store.manifest(a)["data"] == [{"id": "sales", "purpose": "revenue by region"}]
    assert "data-client.js" in store.read(a, "public/index.html")["content"]
    store.connect_data(a, "sales")
    assert store.manifest(a)["data"] == [{"id": "sales", "purpose": "orders"}]
    chat = store.create("Chat", "static-chat")["id"]
    store.connect_data(chat, "sales")
    html = store.read(chat, "public/index.html")["content"]
    assert html.index("data-client.js") < html.index("app.js") and store.read(chat, "public/data-client.js")["content"] == T.DATA_CLIENT_JS
    store.write(chat, "public/index.html", "<p>no scripts</p>")
    store.connect_data(chat, "sales")
    assert store.read(chat, "public/index.html")["content"].endswith('<script src="data-client.js"></script>\n')
    store.disconnect_data(chat, "sales")
    assert store.manifest(chat)["data"] == []
    with pytest.raises(AppError, match="does not use the data source"):
        store.disconnect_data(chat, "sales")
    with pytest.raises(AppError, match="switched off"):
        store.connect_data(a, "off")
    with pytest.raises(AppError, match="no data source called"):
        store.connect_data(a, "ghost")
    store.data = None
    with pytest.raises(AppError, match="not available"):
        store.connect_data(a, "sales")
    assert store.connections(a)["data"] == []


# ------------------------------------------------------------------------------------------------ the tools
def tctx(store, **kw):
    events = []
    ctx = ToolContext(None, None, None, None, agent_id="app_builder", session_id="s1", apps=store, on_app=events.append, **kw)
    return app_tools(ctx), events


def test_the_builder_plans_scaffolds_verifies_and_stops_when_it_cannot_fix(store):
    t, events = tctx(store)
    aid = run(t["app_create"]("Board", "static-dashboard", "sales", "coder"))["id"]
    assert run(t["app_plan"]("Board", "Shows sales.", ["scaffold", "check"], pages=["index.html"]))["steps"] == ["scaffold", "check"] and events[-1]["action"] == "plan"
    assert run(t["app_plan"]("Board", "x", []))["ok"] is False
    assert run(t["app_plan_done"]([1]))["done"] == 1 and run(t["app_plan_done"]([9]))["ok"] is False
    v = run(t["app_verify"]())
    assert v["ok"] and v["clean"] and "Clean" in v["next"] and v["round"] == 0
    store.write(aid, "public/index.html", page('<script src="gone.js"></script>').decode())
    nxt = [run(t["app_verify"]()) for _ in range(4)]
    assert [n["clean"] for n in nxt] == [False] * 4 and [n["give_up"] for n in nxt] == [False, False, False, True]
    assert "Fix these" in nxt[0]["next"] and "Stop fixing" in nxt[3]["next"]
    assert run(t["app_verify"]("0" * 12))["ok"] is False


def test_the_builder_adds_components_themes_and_connections_by_proposal(store, sqlite_file):
    t, events = tctx(store)
    aid = run(t["app_create"]("Chat", "static-chat", "", ""))["id"]
    listing = run(t["app_components"]())
    assert listing["ok"] and len(listing["components"]) == 10
    done = run(t["app_components"](["stats", "chart"]))
    assert done["status"] == "applied" and set(done["usage"]) == {"stats", "chart"} and "ui.js" in store.read(aid, "public/index.html")["content"]
    assert run(t["app_components"](["nope"]))["ok"] is False
    th = run(t["app_theme"]())
    assert th["ok"] and "ocean" in [x["id"] for x in th["themes"]] and th["radii"] == ["sharp", "soft", "round"]
    assert run(t["app_theme"]("forest", "mono", "sharp"))["status"] == "applied" and store.theme_info(aid)["theme"] == "forest"
    assert run(t["app_theme"]("zzz"))["ok"] is False
    conn = run(t["app_connect_agent"]("quiet", "asks"))
    assert conn["status"] == "applied" and "API access switched off" in conn["warning"] and store.agents.get("quiet").api_access is False
    assert "warning" not in run(t["app_connect_agent"]("coder"))
    assert run(t["app_connect_agent"]("ghost"))["ok"] is False
    store.data.set_source({"id": "sales", "kind": "sqlite", "config": {"path": str(sqlite_file)}})
    ds = run(t["app_data_sources"]())
    assert ds["ok"] and [d["id"] for d in ds["data_sources"]] == ["sales"]
    assert run(t["app_connect_data"]("sales", "totals"))["status"] == "applied" and store.manifest(aid)["data"][0]["id"] == "sales"
    assert run(t["app_connect_data"]("ghost"))["ok"] is False
    assert run(t["app_data_sources"]("0" * 12))["ok"] is False and run(t["app_components"]([], "0" * 12))["ok"] is True
    assert run(t["app_components"](["hero"], "0" * 12))["ok"] is False


def test_undo_by_the_builder_only_while_changes_apply_at_once(store):
    t, events = tctx(store)
    aid = run(t["app_create"]("U", "static-chat"))["id"]
    run(t["app_write_file"]("public/a.txt", "one"))
    undone = run(t["app_undo"]())
    assert undone["ok"] and undone["version"] == 3 and "public/a.txt" not in {f["path"] for f in store.tree(aid)} and events[-1]["action"] == "applied"
    store.update(aid, mode="advanced")
    blocked = run(t["app_undo"]())
    assert blocked["ok"] is False and "Versions tab" in blocked["error"]
    assert run(t["app_undo"]("0" * 12))["ok"] is False


def test_a_big_rewrite_gets_a_hint_but_a_new_or_changed_file_does_not(store):
    t, _ = tctx(store)
    aid = run(t["app_create"]("R", "static-chat"))["id"]
    body = "\n".join(f"line {i}" for i in range(REWRITE_MIN_LINES + 10))
    assert "hint" not in run(t["app_write_file"]("public/long.js", body))
    again = run(t["app_write_file"]("public/long.js", body + "\nextra"))
    assert "app_patch_file" in again["hint"]
    assert "hint" not in run(t["app_write_file"]("public/long.js", "totally\ndifferent\n" * 20))
    assert "hint" not in run(t["app_write_file"]("public/short.js", "a\nb"))
    assert not rewrote_most("a\nb", "a\nc") and not rewrote_most(body, body) and rewrote_most(body, body + "\nx") and not rewrote_most(body, "x\n" * 40)
    bad = run(t["app_write_file"]("../x", "x", aid))
    assert bad["ok"] is False and "hint" not in bad


# ------------------------------------------------------------------------------------------------ the built-in agent is upgraded in old databases
def test_an_old_app_builder_gets_the_new_tools_and_instruction_once(db):
    reg = AgentStore(db)
    row = lambda: db.one("SELECT instruction, tools FROM agents WHERE id='app_builder'")        # noqa: E731
    assert set(APP_BUILDER_TOOLS) <= set(json.loads(row()["tools"])) and row()["instruction"] == NEW_APP_BUILDER
    old_tools = [t for t in APP_BUILDER_TOOLS if t not in ("app_plan", "app_plan_done", "app_verify", "app_components", "app_theme", "app_connect_agent", "app_data_sources", "app_connect_data", "app_undo")]
    db.execute("UPDATE agents SET tools=?, instruction=? WHERE id='app_builder'", (json.dumps(old_tools), OLD_APP_BUILDER))
    AgentStore(db)
    assert set(json.loads(row()["tools"])) == set(APP_BUILDER_TOOLS) and row()["instruction"] == NEW_APP_BUILDER
    db.execute("UPDATE agents SET tools=?, instruction=? WHERE id='app_builder'", (json.dumps(["app_plan", "app_check"]), "my own words"))
    AgentStore(db)
    assert json.loads(row()["tools"]) == ["app_plan", "app_check"] and row()["instruction"] == "my own words"
    db.execute("DELETE FROM agents WHERE id='app_builder'")
    reg._upgrade_app_builder()                                                                  # a deleted built-in is simply skipped
    assert db.one("SELECT id FROM agents WHERE id='app_builder'") is None


# ------------------------------------------------------------------------------------------------ the new templates
def test_dashboard_and_table_templates(tmp_path):
    for tid in ("static-dashboard", "static-table"):
        f = T.render(tid, "Sales <x>", "d", "coder")
        assert {"public/ui.css", "public/ui.js", "public/data.json", "public/data-client.js", "public/agent-client.js"} <= set(f)
        data = json.loads(f["public/data.json"])
        assert data["columns"] and data["rows"] and (("stats" in data) == (tid == "static-dashboard"))
        assert "UI.table" in f["public/ui.js"] and ("UI.chart" in f["public/ui.js"]) == (tid == "static-dashboard")
        assert design.MARK_START in f["public/style.css"] and "Sales &lt;x&gt;" in f["public/index.html"]
        for rel in ("public/ui.js", "public/app.js", "public/data-client.js"):
            assert L.bracket_problem(f[rel]) is None
    assert T.render("static-chat", "x")["public/style.css"].count(design.MARK_START) == 1
    assert M.clean_manifest(json.loads(T.render("static-table", "x")["app.json"]))["data"] == []


@pytest.mark.parametrize("patch,msg", [
    ({"data": "x"}, "at most 10 data"), ({"data": [{}]}, "needs an id"), ({"data": [{"id": "Bad"}]}, "needs an id"), ({"data": [{"id": "a1"}, {"id": "a1"}]}, "twice"),
    ({"data": [{"id": "a1", "purpose": "x" * 201}]}, "purpose"), ({"data": [{"id": f"s{i}"} for i in range(11)]}, "at most 10 data"),
])
def test_manifest_data_sources_are_checked(patch, msg):
    with pytest.raises(M.ManifestError, match=msg):
        M.clean_manifest({**M.default_manifest("A"), **patch})
    assert M.clean_manifest({**M.default_manifest("A"), "data": [{"id": "ab", "purpose": " p "}]})["data"] == [{"id": "ab", "purpose": "p"}]


def test_remaining_lint_and_connection_edges(store):
    assert messages({"public/index.html": page("<br></br><p>a</p>")}) == []
    assert L.bracket_problem("var s = `a \` b\nc ${ d } e`; f(g)") is None
    a = store.create("Many", "static-chat")["id"]
    for i in range(M.MAX_AGENTS):
        store.agents.items[f"x{i}"] = agent(f"x{i}")
        store.connect_agent(a, f"x{i}")
    store.agents.items["extra"] = agent("extra")
    with pytest.raises(AppError, match="at most 10 agents"):
        store.connect_agent(a, "extra")


def test_the_new_tools_are_in_the_catalogue_and_classified_for_plan_mode():
    from app.easy.agent_templates import TOOL_PLAIN
    from app.harness import policy as P
    from app.harness.tools import TOOL_INFO
    new = {"app_plan", "app_plan_done", "app_verify", "app_components", "app_theme", "app_connect_agent", "app_data_sources", "app_connect_data", "app_undo"}
    assert new <= set(TOOL_INFO) and new <= set(TOOL_PLAIN) and set(APP_BUILDER_TOOLS) - {"load_skill"} <= set(TOOL_INFO)
    assert {"app_plan", "app_plan_done", "app_verify", "app_data_sources"} <= P.READ_ONLY
    assert {"app_components", "app_theme", "app_connect_agent", "app_connect_data"} <= P.PROPOSE and "app_undo" in P.WRITES and not new & {"run_shell"}
