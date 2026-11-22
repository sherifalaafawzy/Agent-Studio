"""Workflow data boxes (transform.py), the validation of every newer box (nodes.py), workflow settings and credentials (config.py), webhook secrets (hooks.py)."""
from __future__ import annotations

import datetime as dt

import pytest

from app.workflows import config as CFG
from app.workflows import core as W
from app.workflows import hooks as H
from app.workflows import nodes as N
from app.workflows import transform as T
from app.workflows.core import WorkflowError


# ---------------------------------------------------------------------------------------------------- transform: helpers
def test_text_json_and_items():
    assert T.text("a") == "a" and T.text(None) == "" and T.text({"a": 1}) == '{"a": 1}' and T.text(3) == "3" and T.to_json({"é": 1}) == '{"é": 1}'
    assert T.items_of([1, 2]) == [1, 2] and T.items_of('[1, 2]') == [1, 2] and T.items_of("a\n\nb\n") == ["a", "b"] and T.items_of("[bad") == ["[bad"] and T.items_of('["x"] trailing') == ['["x"] trailing']
    assert T.items_of('{"a": 1}') == ['{"a": 1}'] and T.items_of(None) == [] and T.items_of(5) == [5] and len(T.items_of(list(range(5000)))) == T.MAX_ITEMS


def test_paths_and_numbers():
    obj = {"a": {"b": [{"c": 1}, {"c": 2}]}, "n": 5}
    assert T.path_get(obj, "a.b.1.c") == 2 and T.path_get(obj, "a.b.-1.c") == 2 and T.path_get(obj, "a.b.9") is None and T.path_get(obj, "a.x.y") is None and T.path_get(obj, "n.deeper") is None
    assert T.path_get(obj, "") is obj and T.path_get(obj, "a.b.first") is None
    assert T.number(True) is None and T.number(3) == 3.0 and T.number(" 4.5 ") == 4.5 and T.number("x") is None and T.number(None) is None


def test_regex_safety():
    assert T.safe_regex("a+b").search("aab") and T.safe_regex("ABC", True).search("abc")
    for bad, msg in [("", "write a pattern"), ("a" * 201, "write a pattern"), ("(a+)+", "repeats a repeat"), ("(x*)*y", "repeats a repeat"), ("(", "not valid")]:
        with pytest.raises(WorkflowError, match=msg):
            T.safe_regex(bad)


# ---------------------------------------------------------------------------------------------------- transform: rules
def test_rule_matching():
    m = T.rule_match
    assert m("5", "equals", 5) and m("a", "equals", "a") and not m("a", "equals", "b") and m("a", "not_equals", "b") and not m(1, "not_equals", "1.0")
    assert m("hello", "contains", "ell") and m(["a", "b"], "contains", "b") and not m("hello", "contains", "z") and m("hello", "not_contains", "z") and not m(["a"], "not_contains", "a")
    assert m("hello", "starts_with", "he") and m("hello", "ends_with", "lo") and not m("hello", "ends_with", "he")
    assert m("10", "gt", "9") and m(3, "gte", 3) and m(2, "lt", 3) and m("2", "lte", 2) and not m(1, "gt", 2) and m("b", "gt", "a") and not m("b", "lt", "a")
    assert m("", "is_empty", "") and m(None, "is_empty", "") and m([], "is_empty", "") and m("x", "not_empty", "") and not m("x", "is_empty", "")
    assert m("order 66", "regex", r"\d+") and not m("none", "regex", r"\d+")
    with pytest.raises(WorkflowError, match="unknown comparison"):
        m(1, "almost", 1)
    assert T.combine([], "and") and T.combine([True, False], "or") and not T.combine([True, False], "and") and T.combine([True, True], "and")


def test_filter_and_conditions():
    items = [{"n": 3, "t": "a"}, {"n": 1, "t": "b"}, {"n": 2, "t": "a"}]
    assert T.filter_items(items, [dict(field="n", op="gte", value=2)], "and") == [items[0], items[2]]
    assert T.filter_items(items, [dict(field="n", op="gt", value=2), dict(field="t", op="equals", value="b")], "or") == [items[0], items[1]]
    assert T.filter_items(items, [dict(field="t", op="equals", value="a"), dict(field="n", op="lt", value=3)], "and") == [items[2]]
    assert T.conditions_hold([dict(left="a", op="equals", right="a"), dict(left=5, op="gt", right=3)], "and") and not T.conditions_hold([dict(left="a", op="equals", right="b")], "and")


# ---------------------------------------------------------------------------------------------------- transform: lists
def test_sort_limit_dedupe():
    items = [{"n": "10", "t": "b"}, {"n": "9", "t": "a"}, {"n": None, "t": "c"}, {"n": "x", "t": "A"}]
    assert [i["t"] for i in T.sort_items(items, "n", False, True)] == ["a", "b", "c", "A"]                       # numbers first, whatever is not a number last
    assert [i["t"] for i in T.sort_items(items, "n", True, True)] == ["c", "A", "b", "a"]
    assert [i["t"] for i in T.sort_items(items, "t", False, False)] == ["a", "A", "b", "c"]                       # text order ignores case
    assert T.sort_items(["b", "a"], "", False, False) == ["a", "b"] and T.sort_items(["b", "a"], "", True, False) == ["b", "a"] and T.sort_items([{"x": 1}, {}], "x", False, False)[0] == {"x": 1}
    assert T.limit_items([1, 2, 3, 4], 2, False) == [1, 2] and T.limit_items([1, 2, 3, 4], 2, True) == [3, 4]
    assert T.dedupe_items([{"t": "a"}, {"t": "b"}, {"t": "a"}], "t") == [{"t": "a"}, {"t": "b"}] and T.dedupe_items([1, 2, 1, "1"], "") == [1, 2]


def test_aggregate():
    items = [{"n": 3, "g": "a"}, {"n": 1, "g": "b"}, {"n": 2, "g": "a"}]
    A = T.aggregate
    assert A(items, "count", "", "", "") == 3 and A(items, "sum", "n", "", "") == 6 and A(items, "avg", "n", "", "") == 2 and A(items, "min", "n", "", "") == 1 and A(items, "max", "n", "", "") == 3
    assert A([{"n": 1}, {"n": 2}], "avg", "n", "", "") == 1.5 and A(items, "first", "", "", "") == items[0] and A(items, "last", "", "", "") == items[2] and A([], "first", "", "", "") is None and A([], "last", "", "", "") is None
    assert A(items, "collect", "n", "", "") == [3, 1, 2] and A(items, "join", "g", "", "+") == "a+b+a" and A(items, "sum", "n", "g", "") == {"a": 5, "b": 1} and A(items, "count", "", "g", "") == {"a": 2, "b": 1}
    with pytest.raises(WorkflowError, match="no numbers"):
        A(items, "sum", "g", "", "")
    with pytest.raises(WorkflowError, match="summarise with"):
        A(items, "median", "n", "", "")


def test_split_and_join_and_merge():
    assert T.split_out("a\n\nb", "lines", "", "") == ["a", "b"] and T.split_out("a, b ,,c", "separator", ",", "") == ["a", "b", "c"] and T.split_out("a;b", "separator", "", "") == ["a;b"]
    assert T.split_out({"rows": [1, 2]}, "field", "", "rows") == [1, 2] and T.split_out('{"rows": [3]}', "field", "", "rows") == [3]
    with pytest.raises(WorkflowError, match="not a list"):
        T.split_out({"rows": 1}, "field", "", "rows")
    with pytest.raises(WorkflowError, match="not valid JSON"):
        T.split_out("{bad", "field", "", "rows")
    assert T.join_items([{"n": 1}, {"n": 2}], "n", "-") == "1-2" and T.join_items(["a", "b"], "", ", ") == "a, b"
    left, right = [{"id": 1, "a": "x"}, {"id": 2, "a": "y"}, "raw"], [{"k": 1, "b": "p"}, {"k": 1, "b": "q"}, "plain"]
    assert T.merge_by_key(left[:2], right[:2], "id", "k", "inner") == [{"id": 1, "a": "x", "k": 1, "b": "p"}, {"id": 1, "a": "x", "k": 1, "b": "q"}]
    assert T.merge_by_key(left[:2], right[:2], "id", "k", "left")[-1] == {"id": 2, "a": "y"} and T.merge_by_key([{"id": 1}], [{"id": 1, "z": 1}], "id", "", "inner") == [{"id": 1, "z": 1}]
    assert T.merge_by_key(["a"], ["a"], "", "", "inner") == [{"left": "a", "right": "a"}]


# ---------------------------------------------------------------------------------------------------- transform: dates and patterns
def test_dates():
    D = T.date_time
    fixed = dt.datetime(2026, 3, 1, 9, 30, tzinfo=dt.timezone.utc)
    assert D("now", "", 0, "days", "", "", now=fixed) == "2026-03-01T09:30:00+00:00" and D("now", "", 0, "days", "", "").endswith("+00:00")
    assert D("parse", "2026-03-31", 0, "days", "", "") == "2026-03-31T00:00:00+00:00" and D("parse", "2026-03-31T09:30:00Z", 0, "days", "", "") == "2026-03-31T09:30:00+00:00"
    assert D("format", "2026-03-31", 0, "days", "%d/%m/%Y", "") == "31/03/2026" and D("format", "2026-03-31", 0, "days", "", "") == "2026-03-31"
    assert D("add", "2026-03-31", 2, "days", "", "").startswith("2026-04-02") and D("subtract", "2026-03-31", 1, "weeks", "", "").startswith("2026-03-24") and D("add", "2026-03-31T00:00:00Z", 90, "minutes", "", "").endswith("01:30:00+00:00")
    assert D("diff_days", "2026-03-31", 0, "days", "", "2026-03-01") == "30.0"
    for args, msg in [(("add", "2026-03-31", 1, "years", "", ""), "unit must be"), (("format", "yesterday", 0, "days", "", ""), "not a date"), (("fly", "2026-03-31", 0, "days", "", ""), "choose now")]:
        with pytest.raises(WorkflowError, match=msg):
            D(*args)


def test_regex_modes():
    R = T.regex_op
    assert R("test", "order 66", r"\d+", "", False) is True and R("test", "none", r"\d+", "", False) is False and R("first", "a1 b22", r"\d+", "", False) == "1" and R("first", "none", r"\d+", "", False) == ""
    assert R("all", "a1 b22", r"\d+", "", False) == ["1", "22"] and R("groups", "key=val", r"(\w+)=(?P<v>\w+)", "", False) == {"1": "key", "2": "val", "v": "val"} and R("groups", "zzz", r"(\d)", "", False) == {}
    assert R("replace", "a1b2", r"\d", "#", False) == "a#b#" and R("first", "ABC", "b", "", True) == "B"
    with pytest.raises(WorkflowError, match="replacement is not valid"):
        R("replace", "a1", r"\d", r"\9", False)
    with pytest.raises(WorkflowError, match="choose test"):
        R("explode", "a", "a", "", False)


# ---------------------------------------------------------------------------------------------------- the validation of the newer boxes
def cc(t, cfg=None):
    return N.clean_config(t, cfg or {}, "node x")


def rejects(t, cfg, msg):
    with pytest.raises(ValueError, match=msg):
        cc(t, cfg)


def test_every_newer_box_has_a_validator_and_defaults_are_valid():
    for spec in N.SPECS:
        t = spec["type"]
        if t in N.NEW_TYPES and t != "note" and t not in ("filter", "compare", "merge_by_key", "split_out", "regex", "data_query", "artifact", "http_request", "mcp_tool", "classifier", "switch", "set"):
            assert isinstance(cc(t, {f["name"]: f["default"] for f in spec["fields"] if "default" in f}), dict), t
    assert cc("error_handler") == {} and cc("approval", {"message": "ok"}) == {"message": "ok", "form": []} and cc("stop", {"message": "no"}) == {"message": "no"}


def test_rules_and_forms_are_checked():
    ok = cc("filter", dict(rules=[dict(field="a.b", op="gte", value="{{ 2 }}")]))
    assert ok["rules"] == [dict(field="a.b", op="gte", value="{{ 2 }}")] and ok["combine"] == "and"
    rejects("filter", {}, "give 1 to 12 rules")
    rejects("filter", dict(rules=[dict(field="a", op="equals", value=1)] * 13), "give 1 to 12 rules")
    rejects("filter", dict(rules=["x"]), "each rule needs")
    rejects("filter", dict(rules=[dict(field="a", op="nope", value=1)]), "a comparison must be one of")
    rejects("filter", dict(rules=[dict(field="a b", op="equals", value=1)]), "the field is a name")
    rejects("filter", dict(rules=[dict(field="a", op="equals", value="{{ 1 + }}")]), "the value")
    rejects("filter", dict(rules=[dict(field="a", op="equals", value=1)], combine="xor"), "combine must be")
    c = cc("compare", dict(conditions=[dict(left="{{ $prev }}", op="contains", right="x")], combine="or"))
    assert c["conditions"][0]["left"] == "{{ $prev }}" and c["combine"] == "or"
    rejects("compare", dict(conditions=[dict(left="{{ 1 + }}", op="equals", right="x")]), "the left side")
    form = [dict(name="who", label="Who", type="text", required=True), dict(name="size", type="select", options=["s", "m"]), dict(name="n", type="number"), dict(name="ok", type="bool")]
    assert cc("approval", dict(message="m", form=form))["form"][1] == dict(name="size", label="size", type="select", options=["s", "m"], required=False)
    rejects("approval", dict(form="x"), "at most 10 details")
    rejects("approval", dict(form=[dict(name="1bad")]), "short name")
    rejects("approval", dict(form=[dict(name="a"), dict(name="a")]), "short name")
    rejects("approval", dict(form=[dict(name="a", type="file")]), "a detail's type must be")
    rejects("approval", dict(form=[dict(name="a", type="select", options=["only"])]), "at least two options")


def test_integration_boxes_are_checked():
    h = cc("http_request", dict(method="post", url="https://x.test/a", query={"q": "{{ $prev }}", "n": 1}, headers={"X-A": "b"}, body="{}", credential="tok"))
    assert h["method"] == "POST" and h["credential"] == "tok" and h["confirmed"] is False and h["fail_on_error"] is True and cc("http_request", dict(url="{{ $prev }}"))["url"] == "{{ $prev }}"
    rejects("http_request", dict(url="ftp://x"), "must start with https")
    rejects("http_request", dict(url="https://x", method="TRACE"), "the method must be")
    rejects("http_request", dict(url="https://x", query=[]), "the query must be an object")
    rejects("http_request", dict(url="https://x", headers={str(i): "v" for i in range(21)}), "at most 20")
    m = cc("mcp_tool", dict(server="s", tool="search", args={"q": "{{ $prev }}"}))
    assert m == dict(server="s", tool="search", args={"q": "{{ $prev }}"}, confirmed=False)
    rejects("mcp_tool", dict(server="s", tool="bad tool!"), "give the tool's name")
    assert cc("memory", dict(operation="save", kind="procedural", text="how", key="k", scope="workspace", k=3))["scope"] == "workspace"
    rejects("memory", dict(operation="save", kind="entity"), "saving people and things")
    rejects("memory", dict(k=11), "how many results")
    rejects("memory", dict(k=True), "how many results")
    rejects("memory", dict(scope="agent"), "where must be")
    assert cc("knowledge", dict(kb="docs"))["k"] == 5
    rejects("knowledge", dict(kb="docs", k=0), "how many passages")
    assert cc("data_query", dict(source="db", sql="select 1"))["max_rows"] == 50
    rejects("data_query", dict(source="db", sql=" "), "write the query")
    assert cc("artifact", dict(name="r.md", tags="a,b"))["tags"] == "a,b"
    rejects("artifact", dict(name=" "), "give the file a name")
    assert cc("guardrail", dict(stage="output", profile="p"))["profile"] == "p"
    rejects("guardrail", dict(stage="never"), "the stage must be")
    assert cc("webhook", dict(payload="all", respond="node")) == dict(payload="all", respond="node")
    rejects("webhook", dict(payload="xml"), "what becomes the task")
    assert cc("respond_webhook", dict(status=201, content_type="application/json", body="{}"))["status"] == 201
    rejects("respond_webhook", dict(status=99), "the status code")
    rejects("respond_webhook", dict(content_type="text/html"), "the content type")


def test_data_boxes_are_checked():
    assert cc("sort", dict(field="a.b", order="desc", numeric=True))["numeric"] is True and cc("limit", dict(count=3, **{"from": "end"}))["from"] == "end" and cc("dedupe", dict(field="id"))["field"] == "id"
    rejects("sort", dict(field="a b"), "the field is a name")
    rejects("sort", dict(order="up"), "the order must be")
    rejects("limit", dict(count=0), "how many must be")
    rejects("limit", dict(count=5, **{"from": "middle"}), "from where")
    assert cc("aggregate", dict(op="sum", field="n", group_by="g", separator="; "))["separator"] == "; " and cc("aggregate")["op"] == "count"
    rejects("aggregate", dict(op="median"), "what to do must be")
    assert cc("split_out", dict(mode="field", field="rows"))["field"] == "rows" and cc("join", dict(field="n", separator="-"))["separator"] == "-"
    rejects("split_out", dict(mode="field"), "name the field")
    rejects("split_out", dict(mode="chars"), "how to split")
    assert cc("merge_by_key", dict(key="id", right_key="k", mode="left"))["mode"] == "left"
    rejects("merge_by_key", {}, "name the key")
    assert cc("date_time", dict(operation="add", amount=2, unit="hours"))["unit"] == "hours"
    rejects("date_time", dict(amount=-1), "the amount must be")
    rejects("date_time", dict(amount=True), "the amount must be")
    rejects("date_time", dict(operation="teleport"), "what to do must be")
    assert cc("regex", dict(pattern="a+", mode="all", ignore_case=True))["ignore_case"] is True
    rejects("regex", dict(pattern=" "), "write a pattern")
    rejects("regex", dict(pattern="a" * 201), "write a pattern")


# ---------------------------------------------------------------------------------------------------- graph rules for the new starting boxes
def node(i, t, cfg=None):
    return dict(id=i, type=t, label=i, config=cfg or {}, position=dict(x=0, y=0))


def wf(nodes, edges=(), **kw):
    return dict(id="wf_x", name="X", nodes=nodes, edges=[dict(source=a, target=b, **({"source_port": c[0]} if c else {})) for a, b, *c in edges], **kw)


def test_starting_boxes_and_the_error_box():
    d = W.clean(wf([node("w", "webhook"), node("a", "template", dict(text="x")), node("e", "error_handler"), node("n", "template", dict(text="y"))], [("w", "a"), ("e", "n")]))
    assert [n["type"] for n in d["nodes"]] == ["webhook", "template", "error_handler", "template"] and d["nodes"][0]["config"] == dict(payload="body", respond="immediately")
    with pytest.raises(WorkflowError, match="exactly one starting box"):
        W.clean(wf([node("s", "input"), node("w", "webhook")]))
    with pytest.raises(WorkflowError, match="exactly one starting box"):
        W.clean(wf([node("a", "template", dict(text="x"))]))
    with pytest.raises(WorkflowError, match="only one On error box"):
        W.clean(wf([node("s", "input"), node("e", "error_handler"), node("f", "error_handler")]))
    with pytest.raises(WorkflowError, match="nothing can feed a starting box"):
        W.clean(wf([node("s", "input"), node("e", "error_handler"), node("a", "template", dict(text="x"))], [("s", "e")]))
    clean_settings = W.clean({**wf([node("s", "input"), node("e", "error_handler")]), "nodes": [node("s", "input"), {**node("e", "error_handler"), "settings": {"retries": 2}}, ]})
    assert "settings" not in next(n for n in clean_settings["nodes"] if n["id"] == "e")


def refs(**kw):
    return dict(mcp={"m": dict(ready=True, why="", read_only="always"), "w": dict(ready=True, why="", read_only="none"), "off": dict(ready=False, why="switched off", read_only="none")},
                knowledge={"kb"}, data_source={"db"}, guardrail={"gp"}, credential={"cr"}, **kw)


def problems(nodes, edges, refs_=None):
    d = W.clean(wf(nodes, edges))
    return [(p["node"], p["severity"], p["message"]) for p in W.problems(d, set(), {}, set(), None, None, refs_ if refs_ is not None else refs())]


def test_problems_of_the_newer_boxes():
    S = node("s", "input")
    p = problems([S, node("h", "http_request", dict(url="https://x", method="POST")), node("h2", "http_request", dict(url="https://x", method="PUT", confirmed=True, credential="ghost")), node("g", "http_request", dict(url="https://x", credential="cr"))],
                 [("s", "h"), ("s", "h2"), ("s", "g")])
    assert ("h", "error", "POST changes data: tick 'I confirm' on the box to allow it") in p and any(x[0] == "h2" and "will change data" in x[2] for x in p) and any(x[0] == "h2" and "credential 'ghost'" in x[2] for x in p)
    assert not [x for x in p if x[0] == "g" and x[1] == "error"]
    p = problems([S, node("a", "mcp_tool", dict(server="ghost", tool="t")), node("b", "mcp_tool", dict(server="off", tool="t")), node("c", "mcp_tool", dict(server="w", tool="t")), node("d", "mcp_tool", dict(server="w", tool="t", confirmed=True)),
                  node("e", "mcp_tool", dict(server="m", tool="t"))], [("s", x) for x in "abcde"])
    msgs = {x[0]: x[2] for x in p}
    assert "does not exist" in msgs["a"] and "not ready (switched off)" in msgs["b"] and "can change things" in msgs["c"] and "may change things when it runs" in msgs["d"] and "e" not in msgs
    p = problems([S, node("k", "knowledge", dict(kb="nope")), node("d", "data_query", dict(source="nope", sql="select 1")), node("g", "guardrail", dict(profile="nope")), node("g2", "guardrail", dict(profile="gp")), node("g3", "guardrail"),
                  node("m", "memory", dict(operation="save")), node("m2", "memory"), node("f", "artifact", dict(name="a"))], [("s", x) for x in ("k", "d", "g", "g2", "g3", "m", "m2", "f")])
    msgs = {x[0]: x[2] for x in p}
    assert "knowledge base 'nope'" in msgs["k"] and "data source 'nope'" in msgs["d"] and "guardrail profile 'nope'" in msgs["g"] and "g2" not in msgs and "g3" not in msgs and "saves to memory" in msgs["m"] and "m2" not in msgs and "keeps a file" in msgs["f"]
    d = W.clean(wf([S, node("k", "knowledge", dict(kb="nope"))], [("s", "k")]))
    assert not [x for x in W.problems(d, set(), {}, set()) if x["node"] == "k"]                       # no refs given: nothing about existence is checked


def test_the_error_box_and_webhook_graph_checks():
    S = node("s", "input")
    p = problems([S, node("a", "template", dict(text="x")), node("e", "error_handler"), node("shared", "template", dict(text="y"))], [("s", "a"), ("a", "shared"), ("e", "shared")])
    assert ("shared", "error", "this box follows both the main flow and the On error box: keep the error path separate") in p
    p = problems([node("w", "webhook", dict(respond="node")), node("a", "template", dict(text="x"))], [("w", "a")])
    assert any("there is none" in x[2] for x in p)
    p = problems([node("w", "webhook", dict(respond="node")), node("r", "respond_webhook")], [("w", "r")])
    assert not any("there is none" in x[2] for x in p)


# ---------------------------------------------------------------------------------------------------- workflow settings and credentials
def test_workflow_settings(state):
    cfg = CFG.WorkflowSettings(state.db)
    assert cfg.config() == CFG.DEFAULTS and W.LIMITS["max_nodes"] == 60
    out = cfg.set_config(dict(webhooks_enabled=True, max_nodes=20, run_retention=7, http_allow_hosts=["API.Internal.Example", " ", ".corp.test"], ignored=1, hook_rate_per_min=None))
    assert out["webhooks_enabled"] is True and out["http_allow_hosts"] == ["api.internal.example", "corp.test"] and W.LIMITS == dict(max_nodes=20, run_retention=7)
    assert CFG.WorkflowSettings(state.db).config()["max_nodes"] == 20                         # remembered, and pushed to the limits when the app starts
    for bad, msg in [(dict(webhooks_enabled="yes"), "true or false"), (dict(http_allow_hosts="x"), "at most 30"), (dict(http_allow_hosts=["a b"]), "a host is a name"), (dict(max_nodes=5), "10 to 200"),
                     (dict(max_nodes=True), "10 to 200"), (dict(hook_rate_per_min=1.5), "1 to 600")]:
        with pytest.raises(WorkflowError, match=msg):
            cfg.set_config(bad)
    state.db.set_setting(CFG.CONFIG_KEY, "{bad")
    assert cfg.config() == CFG.DEFAULTS
    cfg.set_config(dict(max_nodes=60, run_retention=100))


def test_credentials(state):
    cfg = CFG.WorkflowSettings(state.db)
    assert cfg.credentials() == [] and cfg.ids() == set()
    assert cfg.set_credential(dict(id="tok", label="Token", kind="bearer", value="s3cret")) == dict(id="tok", label="Token", kind="bearer", header="", set=True)
    cfg.set_credential(dict(id="key", kind="header", header="X-Api-Key", value="k1"))
    cfg.set_credential(dict(id="basic", kind="basic", value="user:pass"))
    assert cfg.header_for("tok") == ("Authorization", "Bearer s3cret") and cfg.header_for("key") == ("X-Api-Key", "k1") and cfg.header_for("basic") == ("Authorization", "Basic dXNlcjpwYXNz")
    assert [c["id"] for c in cfg.credentials()] == ["basic", "key", "tok"] and "s3cret" not in str(cfg.credentials()) and cfg.ids() == {"basic", "key", "tok"}
    assert cfg.set_credential(dict(id="tok", label="Renamed"))["label"] == "Renamed" and cfg.header_for("tok")[1] == "Bearer s3cret"                 # no new value: the old one stays
    for bad, msg in [(dict(id="Bad"), "credential id"), (dict(id="x1", kind="cookie", value="v"), "kind must be"), (dict(id="x1", kind="header", header="bad header", value="v"), "name the header"),
                     (dict(id="x1", kind="header", header="Host", value="v"), "cannot be used"), (dict(id="x1", kind="bearer"), "give the secret value"),
                     (dict(id="x1", kind="basic", value="nocolon"), "user:password"), (dict(id="x1", kind="bearer", value="v" * 4001), "too long")]:
        with pytest.raises(WorkflowError, match=msg):
            cfg.set_credential(bad)
    with pytest.raises(WorkflowError, match="does not exist"):
        cfg.header_for("ghost")
    with pytest.raises(WorkflowError, match="used by wf_a"):
        cfg.delete_credential("tok", ["wf_a"])
    cfg.delete_credential("tok", [])
    with pytest.raises(KeyError):
        cfg.delete_credential("tok", [])
    for i in range(CFG.MAX_CREDENTIALS):
        try:
            cfg.set_credential(dict(id=f"c{i:02d}", value="v"))
        except WorkflowError:
            break
    with pytest.raises(WorkflowError, match="at most 20"):
        cfg.set_credential(dict(id="onetoomany", value="v"))
    state.db.set_setting(CFG.SECRETS_KEY, "[1]")
    assert cfg.credentials() == []
    state.db.set_setting(CFG.SECRETS_KEY, "{bad")
    assert cfg.credentials() == []


def test_webhook_secrets_and_the_rate_limit(state):
    hooks = H.HookStore(state.db, clock=lambda: 100.0)
    assert hooks.status("wf_a") == dict(exists=False, created=None, last_used=None, calls=0) and hooks.verify("wf_a", "x") is False
    token = hooks.create("wf_a")
    assert len(token) >= 40 and hooks.verify("wf_a", token) and not hooks.verify("wf_a", token + "x") and not hooks.verify("wf_a", "") and not hooks.verify("wf_b", token)
    assert token not in str(state.db.query("SELECT * FROM workflow_hooks")[0][1])                       # only a hash is kept
    hooks.touch("wf_a")
    hooks.touch("wf_a")
    assert hooks.status("wf_a") == dict(exists=True, created=100.0, last_used=100.0, calls=2)
    token2 = hooks.create("wf_a")
    assert token2 != token and not hooks.verify("wf_a", token) and hooks.verify("wf_a", token2) and hooks.status("wf_a")["calls"] == 0
    hooks.delete("wf_a")
    assert not hooks.verify("wf_a", token2)
    now = [0.0]
    lim = H.RateLimiter(clock=lambda: now[0])
    assert [lim.allow("a", 2) for _ in range(3)] == [True, True, False] and lim.allow("b", 2)
    now[0] = 61
    assert lim.allow("a", 2)
