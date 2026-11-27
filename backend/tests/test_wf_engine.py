"""Workflow engine v2: expressions, node catalog, ports and branches, settings (retry/timeout/pin/disable), sub-workflows, saved runs, drafts, builder, REST."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.workflows import builder as B
from app.workflows import core as W
from app.workflows import engine as E
from app.workflows import expr as X
from app.workflows import nodes as N
from tests.test_workflows import finish, rich, save  # noqa: F401  (rich is a fixture)

run = asyncio.run


def nd(i, t, cfg=None, label=None, **kw):
    return dict(id=i, type=t, label=label or i, config=cfg or {}, position=dict(x=0, y=0), **kw)


def D(nodes, edges, wid="wf_a", autonomy="auto", **kw):
    es = [dict(source=e[0], target=e[1], **({"source_port": e[2]} if len(e) > 2 else {})) for e in edges]
    return dict(id=wid, name=wid, nodes=nodes, edges=es, autonomy=autonomy, **kw)


def go(c, wid, task="go", **kw):
    r = c.post(f"/api/workflows/{wid}/run", json={"task": task, **kw})
    assert r.status_code == 200, r.text
    return finish(c, r.json()["id"])


# ---------------------------------------------------------------------------------------------------- expressions
SC = {"$input": "task", "$prev": "p", "$json": {"a": [1, 2, {"b": "x"}], "n": 5, "s": "7"}, "$node": {"A": {"text": "t", "json": {"k": 1}}}, "$item": None, "$legacy": False, "box": "boxed", "arr": [3, 1, 2]}


def test_expressions_evaluate_safely():
    ev = lambda s: X.evaluate(s, SC)        # noqa: E731
    assert ev("1 + 2 * 3") == 7 and ev("(1 + 2) * 3") == 9 and ev("10 / 4") == 2.5 and ev("10 % 4") == 2 and ev("-3 + 5") == 2 and ev("2.5 * 2") == 5.0
    assert ev('"a" + 1') == "a1" and ev('"x" == "x"') is True and ev("1 != 2") is True and ev("$json.n == 5") is True and ev('$json.s == 7') is True and ev('7 == $json.s') is True
    assert ev("$json.n > 3 && $json.n < 9") is True and ev("false || 'z'") == "z" and ev("0 && 1") == 0 and ev("!true") is False and ev("3 >= 3") is True and ev("2 <= 1") is False
    assert ev('"b" > "a"') is True and ev('"10" > "9"') is True
    assert ev("$json.a[2].b") == "x" and ev("$json.a[-1].b") == "x" and ev("$json.a[9]") is None and ev("$json.nope.deep") is None and ev("$node['A'].json.k") == 1 and ev('$node["A"].text') == "t"
    assert ev("$json['n']") == 5 and ev("$input") == "task" and ev("box") == "boxed" and ev("5 > 3 ? 'big' : 'small'") == "big" and ev("[1, 2, 3]") == [1, 2, 3] and ev("[]") == []
    assert ev("len($json.a)") == 3 and ev("len(5)") == 0 and ev("upper('a') + lower('B') + trim(' c ')") == "Abc" and ev("contains('hello', 'ell')") is True and ev("contains(arr, 1)") is True
    assert ev("startsWith('abc', 'a')") and ev("endsWith('abc', 'c')") and ev("replace('aXb', 'X', '-')") == "a-b" and ev("split('a,b')") == ["a", "b"] and ev("split('a b', ' ')") == ["a", "b"]
    assert ev("join(['a', 'b'], '-')") == "a-b" and ev("join('a')") == "a" and ev("first(arr)") == 3 and ev("last(arr)") == 2 and ev("first(1)") is None and ev("first([])") is None
    assert ev("round(2.567, 1)") == 2.6 and ev("round(2.5)") == 2 and ev("int('4')") == 4 and ev("float(2)") == 2 and ev("str(5)") == "5" and ev("str(null)") == "" and ev("json($json.a[0])") == "1"
    assert ev("parse('{\"q\": 1}').q") == 1 and ev("default('', 'd')") == "d" and ev("default('v', 'd')") == "v" and ev("keys($json)")[:1] == ["a"] and ev("keys(1)") == []
    assert ev("not_empty('')") is False and ev("now()").endswith("+00:00") and ev("format_date('2026-01-05T10:00:00Z')") == "2026-01-05" and ev("format_date('2026-01-05', '%d/%m')") == "05/01"
    assert ev("add_days('2026-01-05T00:00:00Z', 2)").startswith("2026-01-07") and ev("min(arr)") == 1 and ev("max(4, 9, 2)") == 9 and ev("sum(arr)") == 6 and ev("sum(1)") == 0
    assert ev("pick($json, 'n')") == {"n": 5} and ev("pick(1, 'n')") == {} and ev("str($json)").startswith("{") and ev("'a\\nb'") == "a\nb" and ev("true") is True and ev("null") is None
    assert ev("str(1.5)") == "1.5" and X._Eval.truthy("false") is False and X._Eval.truthy("yes") is True and X._Eval.truthy([]) is False and X._Eval.truthy(2) is True and X._Eval.truthy(None) is False
    assert ev("$item") is None and ev("true ? 1 : 2") == 1 and ev("5 - 2") == 3 and ev("true + 1") == 2 and ev("parse($json.a)") == [1, 2, {"b": "x"}]


def test_expression_errors_say_what_is_wrong():
    for bad, msg in [("", "empty"), ("1 +", "ends too early"), ("1 2", "unexpected"), ("@", "unexpected"), ("$nope", "unknown variable"), ("nope", "unknown name"), ("zzz(1)", "unknown function"),
                     ("1 / 0", "division by zero"), ("'a' - 1", "not a number"), ("(1", "expected"), ("$json.", "ends too early"), ("$json.5", "a name must follow"), ("len(1,2)", "len()"),
                     ("parse('{bad')", "not valid JSON"), ("int('x')", "not a number"), ("a" * 700, "longer than"), ("1 ? 2", "expected ':'"), (")", r"unexpected '\)'"), ("round()", "round()"), ("[1,", "ends too early")]:
        with pytest.raises(X.ExprError, match=msg):
            X.evaluate(bad, SC)
    X.MAX_STEPS, old = 5, X.MAX_STEPS
    try:
        with pytest.raises(X.ExprError, match="too complex"):
            X.evaluate("1 + 2 + 3 + 4 + 5", SC)
    finally:
        X.MAX_STEPS = old
    X.check("")
    X.check("1 + 2")
    with pytest.raises(X.ExprError):
        X.check("1 +")
    assert X.template_errors("ok {{ 1 + }} and {{ 2 }}")[0].startswith("{{1 +}}") and X.template_errors("plain") == []
    assert X.render("a {{ $input }} b {{ 1 + 1 }}", SC) == "a task b 2" and X.render("", SC) == ""
    with pytest.raises(X.ExprError, match=r"\{\{\$nope\}\}"):
        X.render("{{ $nope }}", SC)
    assert X.render_value("{{ $json.a }}", SC) == [1, 2, {"b": "x"}] and X.render_value("x {{ 1 }}", SC) == "x 1"
    with pytest.raises(X.ExprError):
        X.render_value("{{ nope }}", SC)
    assert X.evaluate("ghost", {"$legacy": True}) == "" and X.render("{{ghost}}-{{ input }}", {"$legacy": True, "input": "I"}) == "-I"
    assert X.render("{{ 'x' }}" * 3, {}) == "xxx" and len(X.render("{{ 'x' }}" + "y" * 50_000, {})) == 40_000


# ---------------------------------------------------------------------------------------------------- the catalog
def test_catalog_and_validation_of_new_nodes():
    cat = N.catalog()
    types = {n["type"] for n in cat["nodes"]}
    assert {"if", "switch", "merge", "set", "llm", "classifier", "approval", "sub_workflow", "for_each", "note"} <= types and {g["id"] for g in cat["groups"]} >= {"trigger", "ai", "flow", "data"}
    assert N.ports(nd("i", "if")) == ["true", "false"] and N.ports(nd("c", "classifier", dict(labels=["a", "b"]))) == ["a", "b", "other"] and N.ports(nd("s", "switch", dict(cases=["x"]))) == ["x", "other"]
    assert N.ports(nd("a", "agent", settings=dict(on_error="branch"))) == ["out", "error"] and N.ports(nd("o", "output")) == [] and N.ports(nd("z", "zzz")) == ["out"]
    ok = D([nd("start", "input"), nd("l", "llm", dict(prompt="Hi {{ $input }}", json=True)), nd("k", "classifier", dict(labels="a\nb")), nd("sw", "switch", dict(cases=["x", "y"])), nd("m", "merge", dict(mode="json_array")),
            nd("w", "wait", dict(seconds=0)), nd("ap", "approval", dict(message="ok?")), nd("st", "stop", dict(message="no")), nd("su", "sub_workflow", dict(workflow="wf_b")),
            nd("fe", "for_each", dict(workflow="wf_b", concurrency=2)), nd("se", "set", dict(fields=[dict(name="a", value="{{ 1 }}")])), nd("t", "template", dict(text="x")),
            nd("pj", "parse_json"), nd("no", "note", dict(text="hi")), nd("if", "if", dict(condition="{{ 1 == 1 }}")), nd("out", "output", dict(value="{{ $prev }}"))], [])
    c = W.clean(ok)
    assert {n["id"]: n["config"] for n in c["nodes"]}["k"]["labels"] == ["a", "b"] and {n["id"]: n["config"] for n in c["nodes"]}["pj"] == {"from": "{{ $prev }}"} and c["nodes"][-1]["config"] == {"value": "{{ $prev }}"}
    bad = [(nd("x", "classifier", dict(labels=["only"])), "at least 2 labels"), (nd("x", "classifier", dict(labels=["a", "other"])), "at least 2 labels"), (nd("x", "classifier", dict(labels=["a"] * 2)), "no repeats"),
           (nd("x", "switch", dict(cases=[])), "at least 1 case"), (nd("x", "if", dict(condition="{{ 1 + }}")), "condition"), (nd("x", "merge", dict(mode="zip")), "merge as"), (nd("x", "wait", dict(seconds=999)), "wait 0 to"),
           (nd("x", "for_each", dict(concurrency=9)), "1 to 5"), (nd("x", "set", dict(fields=[])), "set 1 to"), (nd("x", "set", dict(fields=[dict(name="bad name", value="")])), "needs a name"),
           (nd("x", "set", dict(fields="no")), "set 1 to"), (nd("x", "output", dict(value="{{ ( }}")), "result"), (nd("x", "agent", settings=dict(on_error="explode")), "on error"),
           (nd("x", "agent", settings=dict(retries=9)), "retries must be"), (nd("x", "agent", settings=dict(timeout_s=True)), "time limit")]
    for n, msg in bad:
        with pytest.raises(W.WorkflowError, match=msg):
            W.clean(D([nd("start", "input"), n], []))
    s = W.clean(D([nd("start", "input"), nd("a", "tool", dict(tool="read_file"), settings=dict(retries=2, retry_wait_s=0, timeout_s=5, pin="p", notes="n", color="red"))], []))["nodes"][1]["settings"]
    assert s["retries"] == 2 and s["pin"] == "p" and "settings" not in W.clean(D([nd("start", "input"), nd("a", "agent", settings=dict(on_error="stop"))], []))["nodes"][1]
    with pytest.raises(W.WorkflowError, match="output it does not have"):
        W.clean(D([nd("start", "input"), nd("i", "if", dict(condition="{{ 1 }}")), nd("t", "template")], [("start", "i"), ("i", "t")]))
    with pytest.raises(W.WorkflowError, match="nothing can follow it"):
        W.clean(D([nd("start", "input"), nd("o", "output"), nd("t", "template")], [("start", "o"), ("o", "t")]))
    e = W.clean(D([nd("start", "input"), nd("i", "if", dict(condition="{{ 1 }}")), nd("t", "template"), nd("u", "template")], [("start", "i"), ("i", "t", "true"), ("i", "t", "true"), ("i", "u", "false")]))["edges"]
    assert [x.get("source_port") for x in e] == [None, "true", "false"] and e[1]["id"] == "i-t-true"


def test_problems_for_new_nodes():
    d = W.clean(D([nd("start", "input"), nd("su", "sub_workflow", dict(workflow="ghost")), nd("fe", "for_each", dict(workflow="wf_a")), nd("t", "template", dict(text="{{ $node['Nope'].text }}")),
                   nd("a", "agent", dict(agent="x"), settings=dict(on_error="branch")), nd("ap", "approval", dict(message=""))],
                  [("start", "su"), ("start", "fe"), ("start", "t"), ("start", "a"), ("start", "ap")]))
    msgs = [p["message"] for p in W.problems(d, {"x"}, {}, set(), {"wf_a"}, {})]
    assert any("'ghost' does not exist" in m for m in msgs) and any("running this workflow again" in m for m in msgs) and any("'Nope'" in m for m in msgs) and any("error output" in m for m in msgs)
    assert any("question is empty" in m for m in msgs)
    assert not any("does not exist" in m for m in [p["message"] for p in W.problems(d, {"x"}, {}, set(), None)] if "workflow" in m)
    assert any("again" in p["message"] for p in W.problems(d, {"x"}, {}, set(), {"wf_a", "ghost", "wf_b"}, {"ghost": {"wf_a"}}))


# ---------------------------------------------------------------------------------------------------- branches
def test_if_branches_prune_the_other_side_and_merge_waits_only_for_live_branches(rich):
    save(rich, D([nd("start", "input"), nd("set", "set", dict(fields=[dict(name="n", value="{{ len($input) }}")])), nd("c", "if", dict(condition="{{ $json.n > 3 }}")),
                  nd("big", "template", dict(text="big:{{ $json.n }}")), nd("small", "template", dict(text="small")), nd("m", "merge"), nd("end", "output", dict(value="[{{ $prev }}]"))],
                 [("start", "set"), ("set", "c"), ("c", "big", "true"), ("c", "small", "false"), ("big", "m"), ("small", "m"), ("m", "end")]))
    r = go(rich, "wf_a", "long task")
    assert r["status"] == "done" and r["final"] == "[big:9]" and r["nodes"]["small"]["status"] == "skipped" and r["nodes"]["big"]["status"] == "done"
    assert r["nodes"]["c"]["ports"] == ["true"] and r["nodes"]["c"]["decision"] == "true" and r["nodes"]["set"]["output"] == '{"n": 9}'
    r2 = go(rich, "wf_a", "abc")
    assert r2["final"] == "[small]" and r2["nodes"]["big"]["status"] == "skipped"
    saved = rich.get("/api/workflows/wf_a/runs").json()["runs"]
    assert [x["status"] for x in saved] == ["done", "done"] and rich.get(f"/api/workflows/runs/{saved[0]['id']}").json()["nodes"]["c"]["decision"] == "false"


def test_switch_classifier_and_merge_modes(rich, state, monkeypatch):
    async def llm(self_run, prompt):
        return "  \"Complaint.\"  " if "complaint" in prompt.split("Text:" + chr(10))[-1] else "banana"
    monkeypatch.setattr(state.wf_runner, "_llm", lambda run_, prompt: llm(run_, prompt))
    save(rich, D([nd("start", "input"), nd("k", "classifier", dict(text="{{ $input }}", labels=["question", "complaint"], hint="be careful")), nd("q", "template", dict(text="Q")), nd("c", "template", dict(text="C")),
                  nd("o", "template", dict(text="O")), nd("m", "merge", dict(mode="json_array"))],
                 [("start", "k"), ("k", "q", "question"), ("k", "c", "complaint"), ("k", "o", "other"), ("q", "m"), ("c", "m"), ("o", "m")]))
    r = go(rich, "wf_a", "this is a complaint")
    assert r["nodes"]["k"]["decision"] == "complaint" and r["final"] == '["C"]' and r["nodes"]["q"]["status"] == "skipped"
    r = go(rich, "wf_a", "hello")
    assert r["nodes"]["k"]["decision"] == "other" and r["final"] == '["O"]'
    save(rich, D([nd("start", "input"), nd("s", "switch", dict(value="{{ $input }}", cases=["red", "5"])), nd("r", "template", dict(text="R")), nd("f", "template", dict(text="F")), nd("d", "template", dict(text="D"))],
                 [("start", "s"), ("s", "r", "red"), ("s", "f", "5"), ("s", "d", "other")], wid="wf_s"))
    assert go(rich, "wf_s", "RED")["final"] == "R" and go(rich, "wf_s", "5")["final"] == "F" and go(rich, "wf_s", "blue")["final"] == "D"
    monkeypatch.setattr(state.wf_runner, "_llm", lambda run_, prompt: asyncio.sleep(0, result="the complaint category"))
    assert go(rich, "wf_a", "x")["nodes"]["k"]["decision"] == "complaint"                 # a label inside a longer answer still counts


def test_approval_node_asks_in_any_mode_and_branches(rich):
    save(rich, D([nd("start", "input"), nd("ask", "approval", dict(message="Send {{ $input }}?")), nd("yes", "template", dict(text="sent")), nd("no", "template", dict(text="held"))],
                 [("start", "ask"), ("ask", "yes", "approved"), ("ask", "no", "rejected")]))
    for decision, final in (("approve", "sent"), ("skip", "held")):
        rid = rich.post("/api/workflows/wf_a/run", json={"task": "mail"}).json()["id"]
        for _ in range(200):
            if rich.get(f"/api/workflows/runs/{rid}").json()["nodes"]["ask"]["status"] == "waiting_approval":
                break
            time.sleep(0.02)
        assert rich.get(f"/api/workflows/runs/{rid}").json()["nodes"]["ask"]["message"] == "Send mail?"
        assert rich.post(f"/api/workflows/runs/{rid}/decide", json={"node": "ask", "decision": decision}).status_code == 200
        assert finish(rich, rid)["final"] == final
    rid = rich.post("/api/workflows/wf_a/run", json={"task": "mail"}).json()["id"]
    for _ in range(200):
        if rich.get(f"/api/workflows/runs/{rid}").json()["nodes"]["ask"]["status"] == "waiting_approval":
            break
        time.sleep(0.02)
    rich.post(f"/api/workflows/runs/{rid}/decide", json={"node": "ask", "decision": "stop"})
    assert finish(rich, rid)["status"] == "stopped"


def test_approve_autonomy_asks_for_work_boxes_but_not_for_plumbing(rich, state, monkeypatch):
    monkeypatch.setattr(state.wf_runner, "_llm", lambda run_, prompt: asyncio.sleep(0, result="AI"))
    save(rich, D([nd("start", "input"), nd("t", "template", dict(text="T")), nd("l", "llm", dict(prompt="x")), nd("end", "output")], [("start", "t"), ("t", "l"), ("l", "end")], autonomy="approve"))
    rid = rich.post("/api/workflows/wf_a/run", json={"task": "x"}).json()["id"]
    for _ in range(200):
        r = rich.get(f"/api/workflows/runs/{rid}").json()
        if r["nodes"]["l"]["status"] == "waiting_approval":
            break
        time.sleep(0.02)
    assert r["nodes"]["t"]["status"] == "done" and "Run 'l'" in r["nodes"]["l"]["message"]
    rich.post(f"/api/workflows/runs/{rid}/decide", json={"node": "l", "decision": "approve"})
    assert finish(rich, rid)["final"] == "AI"
    rid = rich.post("/api/workflows/wf_a/run", json={"task": "x"}).json()["id"]
    for _ in range(200):
        if rich.get(f"/api/workflows/runs/{rid}").json()["nodes"]["l"]["status"] == "waiting_approval":
            break
        time.sleep(0.02)
    rich.post(f"/api/workflows/runs/{rid}/decide", json={"node": "l", "decision": "skip"})
    r = finish(rich, rid)
    assert r["nodes"]["l"]["status"] == "skipped" and r["status"] == "done"


# ---------------------------------------------------------------------------------------------------- settings: errors, retries, pins
def test_error_handling_retries_timeout_pin_disable(rich, state, monkeypatch):
    calls = {"n": 0}

    async def flaky(run_, prompt):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("flaked")
        return "ok after retries"

    async def slow(run_, prompt):
        await asyncio.sleep(5)
    monkeypatch.setattr(state.wf_runner, "_llm", flaky)
    save(rich, D([nd("start", "input"), nd("l", "llm", dict(prompt="x"), settings=dict(retries=2, retry_wait_s=0)), nd("end", "output")], [("start", "l"), ("l", "end")], wid="wf_r"))
    r = go(rich, "wf_r")
    assert r["final"] == "ok after retries" and r["nodes"]["l"]["attempts"] == 3
    calls["n"] = -99
    save(rich, D([nd("start", "input"), nd("l", "llm", dict(prompt="x"), settings=dict(retries=1, retry_wait_s=0)), nd("end", "output")], [("start", "l"), ("l", "end")], wid="wf_f"))
    r = go(rich, "wf_f")
    assert r["status"] == "error" and "flaked" in r["error"] and r["nodes"]["l"]["status"] == "error"
    save(rich, D([nd("start", "input"), nd("l", "llm", dict(prompt="x"), settings=dict(on_error="continue")), nd("t", "template", dict(text="after [{{ $prev }}]"))], [("start", "l"), ("l", "t")], wid="wf_c"))
    r = go(rich, "wf_c")
    assert r["status"] == "done" and r["final"] == "after []" and "flaked" in r["nodes"]["l"]["error"]
    save(rich, D([nd("start", "input"), nd("l", "llm", dict(prompt="x"), settings=dict(on_error="branch")), nd("ok", "template", dict(text="fine")), nd("bad", "template", dict(text="handled: {{ $prev }}"))],
                 [("start", "l"), ("l", "ok"), ("l", "bad", "error")], wid="wf_b"))
    r = go(rich, "wf_b")
    assert r["final"] == "handled: RuntimeError: flaked" and r["nodes"]["ok"]["status"] == "skipped" and r["nodes"]["l"]["ports"] == ["error"]
    monkeypatch.setattr(state.wf_runner, "_llm", slow)
    save(rich, D([nd("start", "input"), nd("l", "llm", dict(prompt="x"), settings=dict(timeout_s=0.05))], [("start", "l")], wid="wf_t"))
    assert "took longer than 0.05 s" in go(rich, "wf_t")["error"]
    save(rich, D([nd("start", "input"), nd("l", "llm", dict(prompt="x"), settings=dict(pin="PINNED")), nd("c", "if", dict(condition="{{ 1 }}"), settings=dict(pin="yes")), nd("t", "template", dict(text="{{ $prev }}!"))],
                 [("start", "l"), ("l", "c"), ("c", "t", "true")], wid="wf_p"))
    r = go(rich, "wf_p")
    assert r["nodes"]["l"]["pinned"] and r["final"] == "yes!"
    monkeypatch.setattr(state.wf_runner, "_llm", lambda run_, prompt: asyncio.sleep(0, result="never"))
    save(rich, D([nd("start", "input"), nd("l", "llm", dict(prompt="x"), settings=dict(disabled=True)), nd("end", "output")], [("start", "l"), ("l", "end")], wid="wf_d"))
    r = go(rich, "wf_d", "pass me")
    assert r["nodes"]["l"]["status"] == "skipped" and r["final"] == "pass me"


def test_data_nodes_wait_stop_and_expression_failures(rich):
    save(rich, D([nd("start", "input"), nd("w", "wait", dict(seconds=0)), nd("pj", "parse_json", {"from": "{{ $input }}"}), nd("t", "template", dict(text="{{ $node['pj'].json.a.b }}|{{ $json.a.b }}")),
                  nd("o", "output", dict(value="{{ $prev }}!"))], [("start", "w"), ("w", "pj"), ("pj", "t"), ("t", "o")]))
    assert go(rich, "wf_a", '{"a": {"b": 5}}')["final"] == "5|5!"
    r = go(rich, "wf_a", "not json")
    assert r["status"] == "error" and "not valid JSON" in r["error"]
    save(rich, D([nd("start", "input"), nd("x", "stop", dict(message="halt: {{ $input }}"))], [("start", "x")], wid="wf_stop"))
    assert "halt: now" in go(rich, "wf_stop", "now")["error"]
    save(rich, D([nd("start", "input"), nd("t", "template", dict(text="{{ 1 / 0 }}"))], [("start", "t")], wid="wf_div"))
    assert "division by zero" in go(rich, "wf_div")["error"]
    save(rich, D([nd("start", "input"), nd("o", "output", dict(value="{{ 1 / 0 }}"))], [("start", "o")], wid="wf_od"))
    assert "division by zero" in go(rich, "wf_od")["error"]
    save(rich, D([nd("start", "input"), nd("a", "template", dict(text="one")), nd("b", "template", dict(text="2")), nd("m", "merge", dict(separator=" + "))], [("start", "a"), ("start", "b"), ("a", "m"), ("b", "m")], wid="wf_m"))
    assert go(rich, "wf_m")["final"] == "one + 2"
    save(rich, D([nd("start", "input"), nd("t", "template", dict(text="{{ legacy }} {{ gone }}|{{prev}}|{{input}}"))], [("start", "t")], wid="wf_leg"))
    assert go(rich, "wf_leg", "I")["final"] == " |I|I"


# ---------------------------------------------------------------------------------------------------- workflows inside workflows
def test_sub_workflow_for_each_and_limits(rich):
    save(rich, D([nd("start", "input"), nd("t", "template", dict(text="<{{ $input }}>")), nd("o", "output")], [("start", "t"), ("t", "o")], wid="wf_leaf"))
    save(rich, D([nd("start", "input"), nd("s", "sub_workflow", dict(workflow="wf_leaf", task="x{{ $input }}"))], [("start", "s")], wid="wf_one"))
    r = go(rich, "wf_one", "A")
    assert r["final"] == "<xA>" and r["nodes"]["s"]["status"] == "done"
    save(rich, D([nd("start", "input"), nd("p", "parse_json", {"from": "{{ $input }}"}), nd("f", "for_each", dict(items="{{ $json }}", workflow="wf_leaf", task="{{ $index }}:{{ $item }}", concurrency=2))],
                 [("start", "p"), ("p", "f")], wid="wf_many"))
    assert json.loads(go(rich, "wf_many", '["a", "b", "c"]')["final"]) == ["<0:a>", "<1:b>", "<2:c>"]
    save(rich, D([nd("start", "input"), nd("f", "for_each", dict(items="{{ $input }}", workflow="wf_leaf", task="{{ $item }}"))], [("start", "f")], wid="wf_lines"))
    assert json.loads(go(rich, "wf_lines", "x\ny\n")["final"]) == ["<x>", "<y>"]
    save(rich, D([nd("start", "input"), nd("f", "for_each", dict(items="{{ 5 }}", workflow="wf_leaf", task="{{ $item }}"))], [("start", "f")], wid="wf_scalar"))
    assert json.loads(go(rich, "wf_scalar")["final"]) == ["<5>"]
    save(rich, D([nd("start", "input"), nd("x", "stop", dict(message="child failed"))], [("start", "x")], wid="wf_bad"))
    save(rich, D([nd("start", "input"), nd("s", "sub_workflow", dict(workflow="wf_bad"))], [("start", "s")], wid="wf_parent"))
    assert "workflow 'wf_bad' error" in go(rich, "wf_parent")["error"]
    assert rich.put("/api/workflows/wf_leaf", json=D([nd("start", "input"), nd("s", "sub_workflow", dict(workflow="wf_one"))], [("start", "s")], wid="wf_leaf")).status_code == 200
    r = rich.post("/api/workflows/wf_one/run", json={"task": "x"})
    assert r.status_code == 422 and "running this workflow again" in r.text
    assert set(rich.get("/api/workflows/wf_leaf/used-by").json()["used_by"]) == {"wf_one", "wf_many", "wf_lines", "wf_scalar"}
    state_runner = rich.app.state.s.wf_runner
    with pytest.raises(W.WorkflowError, match="levels deep"):
        state_runner.start("wf_bad", "x", "auto", depth=4)


def test_stopping_a_parent_stops_the_child(rich):
    save(rich, D([nd("start", "input"), nd("w", "wait", dict(seconds=30))], [("start", "w")], wid="wf_slow"))
    save(rich, D([nd("start", "input"), nd("s", "sub_workflow", dict(workflow="wf_slow"))], [("start", "s")], wid="wf_outer"))
    rid = rich.post("/api/workflows/wf_outer/run", json={"task": "x"}).json()["id"]
    runner = rich.app.state.s.wf_runner
    for _ in range(200):
        if any(r.parent == rid and r.nodes["w"]["status"] == "running" for r in runner.runs.values()):
            break
        time.sleep(0.02)
    assert rich.post(f"/api/workflows/runs/{rid}/stop").json()["id"] == rid
    fin = finish(rich, rid)
    assert fin["status"] == "stopped", fin
    for _ in range(200):
        if all(r.status != "running" for r in runner.runs.values() if r.parent == rid):
            break
        time.sleep(0.02)
    assert [r.status for r in runner.runs.values() if r.parent == rid] == ["stopped"]


# ---------------------------------------------------------------------------------------------------- store, drafts, runs, REST
def test_store_duplicate_drafts_versions_and_listing(rich):
    d = D([nd("start", "input"), nd("t", "template", dict(text="x"), settings=dict(pin="secret test data")), nd("o", "output")], [("start", "t"), ("t", "o")], wid="wf_src")
    save(rich, d)
    assert rich.put("/api/workflows/wf_src", json={**d, "label": "Milestone", "note": "first"}).json()["version"] == 2
    vs = rich.get("/api/workflows/wf_src/versions").json()["versions"]
    assert vs[0]["label"] == "Milestone" and vs[0]["note"] == "first" and vs[1]["label"] == ""
    assert rich.get("/api/workflows/wf_src/versions/1").json()["id"] == "wf_src" and rich.get("/api/workflows/wf_src/versions/9").status_code == 404
    assert rich.post("/api/workflows/wf_src/restore", json={"version": 1}).json()["version"] == 3 and rich.get("/api/workflows/wf_src/versions").json()["versions"][0]["label"] == "Restored v1"
    cp = rich.post("/api/workflows/wf_src/duplicate", json={}).json()
    assert cp["id"] == "wf_src_copy" and cp["name"] == "wf_src copy" and "pin" not in json.dumps(cp["nodes"][1].get("settings", {})) or cp["nodes"][1]["settings"]["pin"] is None
    assert rich.post("/api/workflows/wf_src/duplicate", json={"id": "wf_src_copy", "name": "Named"}).json()["id"] == "wf_src_copy_2" and rich.post("/api/workflows/ghost/duplicate", json={}).status_code == 404
    assert rich.get("/api/workflows/wf_src/draft").json() == {"doc": None}
    assert rich.put("/api/workflows/wf_src/draft", json={**d, "name": "Draft name"}).json()["doc"]["name"] == "Draft name"
    assert rich.get("/api/workflows/wf_src/draft").json()["doc"]["name"] == "Draft name" and rich.get("/api/workflows").json()["workflows"][0]["has_draft"] in (True, False)
    assert any(w["id"] == "wf_src" and w["has_draft"] for w in rich.get("/api/workflows").json()["workflows"])
    assert rich.put("/api/workflows/ghost/draft", json=d).status_code == 404 and rich.put("/api/workflows/wf_src/draft", json={"nodes": []}).status_code == 422
    assert rich.delete("/api/workflows/wf_src/draft").status_code == 200 and rich.get("/api/workflows/wf_src/draft").json() == {"doc": None}
    rich.put("/api/workflows/wf_src/draft", json=d)
    rich.put("/api/workflows/wf_src", json=d)
    assert rich.get("/api/workflows/wf_src/draft").json() == {"doc": None}                          # publishing a version clears the draft
    go(rich, "wf_src")
    row = next(w for w in rich.get("/api/workflows").json()["workflows"] if w["id"] == "wf_src")
    assert row["last_run"]["status"] == "done" and row["nodes"] == 3 and row["uses"] == []
    assert rich.get("/api/workflows/wf_src/runs?limit=500").json()["runs"][0]["status"] == "done"
    assert rich.delete("/api/workflows/wf_src").status_code == 200 and rich.app.state.s.db.query("SELECT id FROM workflow_runs WHERE wf_id='wf_src'") == []


def test_import_modes_and_delete_protection(rich):
    d = D([nd("start", "input")], [], wid="wf_imp")
    assert rich.post("/api/workflows/import", json={"workflow": d}).json()["id"] == "wf_imp"
    assert rich.post("/api/workflows/import", json={"workflow": d, "on_conflict": "cancel"}).status_code == 409
    r = rich.post("/api/workflows/import", json={"workflow": {**d, "name": "Replaced"}, "on_conflict": "replace"}).json()
    assert r["id"] == "wf_imp" and r["name"] == "Replaced" and r["version"] == 2
    assert rich.post("/api/workflows/import", json={"workflow": d, "on_conflict": "keep"}).status_code == 422
    assert rich.post("/api/workflows/import", json={"workflow": d}).json()["id"] == "wf_imp_2"
    save(rich, D([nd("start", "input"), nd("s", "sub_workflow", dict(workflow="wf_imp"))], [("start", "s")], wid="wf_user"))
    blocked = rich.delete("/api/workflows/wf_imp")
    assert blocked.status_code == 409 and blocked.json()["used_by"] == ["wf_user"] and rich.delete("/api/workflows/wf_imp?force=true").status_code == 200
    row = next(w for w in rich.get("/api/workflows").json()["workflows"] if w["id"] == "wf_user")
    assert row["uses"] == ["wf_imp"]


def test_catalog_and_templates_endpoints_and_every_template_runs(rich, state, monkeypatch):
    assert rich.get("/api/workflows/nodes").json()["nodes"][0]["type"] == "input"
    ts = rich.get("/api/workflows/templates").json()["templates"]
    assert {t["id"] for t in ts} == {"research", "approve", "route", "report", "science-debate"}

    async def llm(run_, prompt):
        return "question" if "Classify" in prompt else "AI text"
    monkeypatch.setattr(state.wf_runner, "_llm", llm)
    for t in ts:
        doc = {**t["doc"], "id": f"tpl_{t['id'].replace('-', '_')}"}
        saved = save(rich, doc)
        assert saved["problems"] == [] or all(p["severity"] == "warning" for p in saved["problems"]), saved["problems"]
    assert go(rich, "tpl_research", "bees")["final"] == "AI text" and go(rich, "tpl_route", "why?")["final"] == "AI text"
    assert "Daily" not in go(rich, "tpl_report", "notes")["final"] and go(rich, "tpl_science_debate", "Does X cause Y?")["final"] == "AI text"
    rid = rich.post("/api/workflows/tpl_approve/run", json={"task": "hi"}).json()["id"]
    for _ in range(200):
        if rich.get(f"/api/workflows/runs/{rid}").json()["nodes"]["ask"]["status"] == "waiting_approval":
            break
        time.sleep(0.02)
    rich.post(f"/api/workflows/runs/{rid}/decide", json={"node": "ask", "decision": "approve"})
    assert finish(rich, rid)["final"] == "AI text"


# ---------------------------------------------------------------------------------------------------- the builder
def test_apply_ops_validates_and_never_grants_confirmation():
    base = W.clean(D([nd("start", "input")], [], wid="draft"))
    ops = [dict(op="add_node", type="llm", id="draft", label="Draft", config=dict(prompt="Write {{ $input }}"), after="start"),
           dict(op="add_node", type="tool", id="rm", config=dict(tool="delete_file", args={}, confirmed=True), after="draft"),
           dict(op="add_node", type="sequential", id="seq", config=dict(steps=[dict(kind="tool", tool="write_file", confirmed=True)]), settings=dict(pin="no", retries=1), after="draft", port="out"),
           dict(op="add_node", type="if", id="gate", config=dict(condition="{{ 1 }}"), after="seq"),
           dict(op="add_node", type="output", label="Done", after="gate", port="true"),
           dict(op="add_node", type="input"), dict(op="add_node", type="mystery"), dict(op="update_node", id="start", label="x"), dict(op="remove_node", id="start"), dict(op="remove_node", id="ghost"),
           dict(op="connect", source="gate", target="rm", source_port="false"), dict(op="connect", source="gate", target="rm", source_port="maybe"), dict(op="disconnect", source="gate", target="rm"),
           dict(op="update_node", id="draft", label="Better", config=dict(prompt="p2"), settings=dict(pin="x", notes="n")), dict(op="set_meta", name="Renamed", description="d", autonomy="suggest"),
           dict(op="set_meta", autonomy="bogus"), dict(op="explode"), "nope", dict(op="add_node", type="llm", config=dict(prompt="{{ 1 + }}"))]
    new, applied, refused = B.apply_ops(base, ops)
    by = {n["id"]: n for n in new["nodes"]}
    assert by["rm"]["config"]["confirmed"] is False and by["seq"]["config"]["steps"][0]["confirmed"] is False and by["seq"]["settings"]["pin"] is None and by["seq"]["settings"]["retries"] == 1 and by["draft"]["label"] == "Better"
    assert new["name"] == "Renamed" and new["autonomy"] == "suggest" and any(e.get("source_port") == "true" for e in new["edges"]) and len(applied) >= 8 and len(refused) >= 7
    assert any("only a task input" in x or "not a box" in x for x in refused) and any("cannot be removed" in x for x in refused) and any("there is no box 'ghost'" in x for x in refused)
    d = B.diff(base, new)
    assert "draft" in d["added"] and d["edges_added"] >= 3 and d["meta"] is True and B.diff(new, new) == dict(added=[], removed=[], changed=[], edges_added=0, edges_removed=0, meta=False)
    assert B.apply_ops(base, "nope")[2] == ["the builder did not send a list of edits"] and "only the first 40" in B.apply_ops(base, [dict(op="set_meta", name="x")] * 41)[2][0]
    assert B.apply_ops(new, [dict(op="remove_node", id="draft")])[1] == ["removed 'Better'"]
    assert "disconnected" in B.apply_ops(new, [dict(op="disconnect", source="start", target="draft")])[1][0]
    assert B.catalog_text().count("\n") > 10 and "input" not in B.catalog_text().split("\n")[0]
    assert B.parse_reply('noise {"reply": "Done", "ops": [{"op": "set_meta"}]} tail') == ("Done", [{"op": "set_meta"}]) and B.parse_reply('{"reply": "x", "ops": 5}') == ("x", [])
    for bad, msg in [("no json", "did not answer with edits"), ("{not json}", "not valid JSON")]:
        with pytest.raises(W.WorkflowError, match=msg):
            B.parse_reply(bad)
    ops2 = B.template_ops("do it", ["helper"], [dict(source="pets", operation="get_pet")])
    t, _, ref = B.apply_ops(base, ops2)
    assert not ref and [n["type"] for n in t["nodes"]] == ["input", "openapi", "agent", "output"]


def test_builder_chat_endpoint(rich, monkeypatch):
    from app.workflows import api as WA
    seen = {}

    async def ask(s, prompt):
        seen["p"] = prompt
        return json.dumps({"reply": "Added a draft step.", "ops": [dict(op="add_node", type="llm", id="draft", label="Draft", config=dict(prompt="{{ $input }}"), after="start"),
                                                                    dict(op="add_node", type="output", id="output", after="draft")]})
    monkeypatch.setattr(WA, "_ask", ask)
    r = rich.post("/api/workflows/builder/chat", json={"messages": [{"role": "user", "content": "draft a reply"}], "autonomy": "approve", "systems": {"agents": ["helper", "ghost"]}}).json()
    assert r["reply"] == "Added a draft step." and r["drafted_by"] == "model" and r["diff"]["added"] == ["draft", "output"] and r["applied"] and r["refused"] == [] and r["workflow"]["nodes"][1]["type"] == "llm"
    assert "helper: Helps" in seen["p"] and "ghost" not in seen["p"] and "ask before each step" in seen["p"]
    cur = r["workflow"]
    r2 = rich.post("/api/workflows/builder/chat", json={"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "more"}], "doc": cur}).json()
    assert "Draft" in seen["p"] and r2["workflow"]["nodes"][1]["id"] == "draft"
    for bad, code in [({"messages": []}, 422), ({"messages": [{"role": "assistant", "content": "x"}]}, 422), ({"messages": [{"role": "user", "content": " "}]}, 422),
                      ({"messages": [{"role": "user", "content": "x"}], "autonomy": "wild"}, 422), ({"messages": [{"role": "user", "content": "x"}], "doc": {"nodes": [{"id": "a", "type": "agent"}]}}, 422)]:
        assert rich.post("/api/workflows/builder/chat", json=bad).status_code == code

    async def broken(s, prompt):
        raise RuntimeError("no model")
    monkeypatch.setattr(WA, "_ask", broken)
    rich.post("/api/workflows/apis", json={"id": "pets", "name": "Pets", "src": "https://x/openapi.json"})
    t = rich.post("/api/workflows/builder/chat", json={"messages": [{"role": "user", "content": "chain my agents"}], "systems": {"agents": ["helper"]}}).json()
    assert t["drafted_by"] == "template" and "no model" in t["note"] and [n["type"] for n in t["workflow"]["nodes"]][:2] == ["input", "openapi"] and t["diff"]["added"]
    again = rich.post("/api/workflows/builder/chat", json={"messages": [{"role": "user", "content": "more"}], "doc": t["workflow"]}).json()
    assert again["drafted_by"] == "template" and again["diff"]["added"] == []


def test_gaps_json_answers_real_llm_persist_failures_and_bad_duplicate(rich, state, monkeypatch):
    seen = []
    real_llm = state.wf_runner._llm
    monkeypatch.setattr(state.wf_runner, "_llm", lambda run_, prompt: seen.append(prompt) or asyncio.sleep(0, result="{}"))
    save(rich, D([nd("start", "input"), nd("l", "llm", dict(prompt="Give {{ $input }}", system="Be terse", json=True))], [("start", "l")], wid="wf_j"))
    assert go(rich, "wf_j", "data")["final"] == "{}" and seen[0].splitlines()[:2] == ["Be terse", "Answer with one valid JSON value and nothing else."] and seen[0].endswith("Give data")
    save(rich, D([nd("start", "input"), nd("f", "for_each", dict(items="{{ $input }}", workflow="wf_leaf", task="{{ $item }}"))], [("start", "f")], wid="wf_jl"))
    save(rich, D([nd("start", "input"), nd("t", "template", dict(text="<{{ $input }}>"))], [("start", "t")], wid="wf_leaf"))
    assert json.loads(go(rich, "wf_jl", '["a", "b"]')["final"]) == ["<a>", "<b>"]
    monkeypatch.setattr(state.wf_runner, "_llm", real_llm)
    save(rich, D([nd("start", "input"), nd("l", "llm", dict(prompt="hello"))], [("start", "l")], wid="wf_real"))
    assert go(rich, "wf_real")["final"].startswith("[mock]")                          # the real guarded model call (the offline mock provider)
    monkeypatch.setattr(state.wf_runner, "_persist", lambda run_: (_ for _ in ()).throw(RuntimeError("disk full")))
    assert go(rich, "wf_real")["status"] == "done"                                   # a run that cannot be saved still finishes
    assert rich.post("/api/workflows/wf_real/duplicate", json={"id": "Bad Id"}).status_code == 422
