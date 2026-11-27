"""The workflow builder agent and the starter templates.

The builder only **proposes**: a model reads the conversation and the current graph and answers with a short reply plus a list of small edits
(add/update/remove a box, connect/disconnect, rename). The server validates every edit against the real workflow rules, applies the valid ones to a *copy*
of the graph and returns it with a diff. Nothing is saved or run, and the builder can never tick "I confirm" on a box that changes things, create
credentials or pin data: the person does that in the editor.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from . import nodes as NODES
from .core import AUTONOMY, WorkflowError, clean

MAX_OPS = 40
OP_NAMES = ("add_node", "update_node", "remove_node", "connect", "disconnect", "set_meta")
ID = re.compile(r"[^A-Za-z0-9_-]+")


def _n(i, t, label, cfg=None, x=0, y=0, **kw):
    return dict(id=i, type=t, label=label, config=cfg or {}, position=dict(x=x, y=y), **kw)


TEMPLATES = [
    dict(id="research", name="Research and summarise", description="Look at a topic from a few angles, then write one short summary.",
         doc=dict(name="Research and summarise", autonomy="auto", nodes=[
             _n("start", "input", "Task", x=0, y=120), _n("angles", "llm", "List the angles", dict(prompt="List 3 short angles to research for this task:\n{{ $input }}"), 260, 120),
             _n("summary", "llm", "Write the summary", dict(prompt="Task: {{ $input }}\n\nAngles:\n{{ $prev }}\n\nWrite a clear 5-sentence summary."), 520, 120), _n("result", "output", "Result", x=780, y=120)],
             edges=[dict(source="start", target="angles"), dict(source="angles", target="summary"), dict(source="summary", target="result")])),
    dict(id="approve", name="Draft, approve, then send", description="An AI drafts something; nothing goes on until you say yes.",
         doc=dict(name="Draft, approve, then send", autonomy="auto", nodes=[
             _n("start", "input", "Task", x=0, y=120), _n("draft", "llm", "Write the draft", dict(prompt="Write a draft for this request:\n{{ $input }}"), 260, 120),
             _n("ask", "approval", "Do you approve?", dict(message="Use this draft?\n\n{{ $prev }}"), 520, 120), _n("done", "output", "Approved draft", x=780, y=60),
             _n("no", "stop", "Not approved", dict(message="You did not approve the draft."), 780, 200)],
             edges=[dict(source="start", target="draft"), dict(source="draft", target="ask"), dict(source="ask", target="done", source_port="approved"), dict(source="ask", target="no", source_port="rejected")])),
    dict(id="route", name="Classify and route", description="Decide what kind of request this is and handle each kind differently.",
         doc=dict(name="Classify and route", autonomy="auto", nodes=[
             _n("start", "input", "Task", x=0, y=140), _n("kind", "classifier", "What kind is it?", dict(text="{{ $input }}", labels=["question", "complaint", "other request"]), 260, 140),
             _n("q", "llm", "Answer the question", dict(prompt="Answer briefly:\n{{ $input }}"), 560, 20), _n("c", "llm", "Reply to the complaint", dict(prompt="Write a calm, apologetic reply to:\n{{ $input }}"), 560, 140),
             _n("o", "llm", "Handle the request", dict(prompt="Say how you would handle:\n{{ $input }}"), 560, 260), _n("join", "merge", "Whichever ran", x=840, y=140), _n("result", "output", "Result", x=1100, y=140)],
             edges=[dict(source="start", target="kind"), dict(source="kind", target="q", source_port="question"), dict(source="kind", target="c", source_port="complaint"),
                    dict(source="kind", target="o", source_port="other request"), dict(source="q", target="join"), dict(source="c", target="join"), dict(source="o", target="join"),
                    dict(source="join", target="result")])),
    dict(id="report", name="Daily report", description="Turn a few notes into a tidy report with a header.",
         doc=dict(name="Daily report", autonomy="auto", nodes=[
             _n("start", "input", "Notes", x=0, y=120), _n("head", "set", "Report header", dict(fields=[dict(name="date", value="{{ format_date(now()) }}"), dict(name="notes", value="{{ $input }}")]), 260, 120),
             _n("body", "llm", "Write the report", dict(prompt="Turn these notes into a short report with headings:\n{{ $prev }}"), 520, 120), _n("result", "output", "Report", x=780, y=120)],
             edges=[dict(source="start", target="head"), dict(source="head", target="body"), dict(source="body", target="result")])),
    dict(id="science-debate", name="Science debate (propose, critique, judge)", description="Three roles argue a research question: a proposer, a sceptic and a judge who labels every claim. Swap the AI boxes for your Science Lab agents for real data-source calls.",
         doc=dict(name="Science debate", autonomy="auto", description="A proposer argues a hypothesis, a sceptic attacks it, the proposer answers, a judge weighs both and labels each claim.", nodes=[
             _n("start", "input", "Research question", x=0, y=140),
             _n("propose", "llm", "Proposer", dict(system="You are the proposer in a scientific debate. Be specific and cite sources you are sure exist (DOI, PMID or arXiv id); never invent one. Say 'no source' when you have none.",
                                                   prompt="Question: {{ $input }}\n\nPropose the most likely answer as a testable hypothesis, with the evidence for it and how it could be tested."), 260, 140),
             _n("critique", "llm", "Sceptic", dict(system="You are the sceptic in a scientific debate. Attack weak evidence, missing controls, confounders and any citation that looks invented. Do not be polite at the cost of accuracy.",
                                                   prompt="Question: {{ $input }}\n\nProposal:\n{{ $prev }}\n\nList the strongest objections, most serious first, and what evidence would settle each."), 520, 140),
             _n("rebut", "llm", "Proposer answers", dict(prompt="Question: {{ $input }}\n\nYour proposal:\n{{ $node[\"Proposer\"].text }}\n\nObjections:\n{{ $prev }}\n\nAnswer each objection: concede, narrow the claim, or defend it with evidence."), 780, 140),
             _n("judge", "llm", "Judge", dict(system="You judge a scientific debate. Label every claim as one of: established (2+ independent sources), single source, computed, inference, conflicting, unsupported. Never upgrade a claim without a source.",
                                              prompt="Question: {{ $input }}\n\nProposal:\n{{ $node[\"Proposer\"].text }}\n\nObjections:\n{{ $node[\"Sceptic\"].text }}\n\nAnswers:\n{{ $prev }}\n\nGive: the verdict, a table of claims with labels, the open questions and the next experiment."), 1040, 140),
             _n("result", "output", "Verdict", x=1300, y=140)],
             edges=[dict(source="start", target="propose"), dict(source="propose", target="critique"), dict(source="critique", target="rebut"), dict(source="rebut", target="judge"), dict(source="judge", target="result")])),
]


def _clean_cfg(cfg: Any) -> dict:
    """Whatever the builder sent for a box's settings: 'I confirm' is never granted, here or inside steps."""
    cfg = copy.deepcopy(cfg) if isinstance(cfg, dict) else {}
    cfg.pop("credential", None)                                                  # signing in is the person's choice, never the builder's
    if "confirmed" in cfg:
        cfg["confirmed"] = False
    for s in cfg.get("steps") or []:
        if isinstance(s, dict) and "confirmed" in s:
            s["confirmed"] = False
    return cfg


def _slot(d: dict, after: str | None) -> dict:
    nodes = d["nodes"]
    a = next((n for n in nodes if n["id"] == after), None)
    if a:
        k = sum(1 for e in d["edges"] if e["source"] == a["id"])
        return dict(x=a["position"]["x"] + 270, y=a["position"]["y"] + 130 * k)
    return dict(x=max((n["position"]["x"] for n in nodes), default=0) + 270, y=max((n["position"]["y"] for n in nodes), default=0))


def apply_ops(doc: dict, ops: Any) -> tuple[dict, list[str], list[str]]:
    """Apply the valid edits to a copy of the graph. Returns (new graph, what was done, what was refused and why)."""
    d, applied, refused = copy.deepcopy({k: v for k, v in doc.items() if k not in ("version", "updated", "problems")}), [], []
    if not isinstance(ops, list):
        return d, applied, ["the builder did not send a list of edits"]
    for op in ops[:MAX_OPS]:
        cand = copy.deepcopy(d)
        try:
            what = _apply_one(cand, op)
            d = clean(cand)
            applied.append(what)
        except (WorkflowError, ValueError, KeyError, TypeError, StopIteration) as e:
            refused.append(f"{(op or {}).get('op', '?') if isinstance(op, dict) else '?'}: {str(e)[:160] or 'not allowed'}")
    if len(ops) > MAX_OPS:
        refused.append(f"only the first {MAX_OPS} edits were considered")
    return d, applied, refused


def _node(d: dict, nid: Any) -> dict:
    n = next((x for x in d["nodes"] if x["id"] == nid), None)
    if n is None:
        raise ValueError(f"there is no box '{nid}'")
    return n


def _apply_one(d: dict, op: Any) -> str:
    if not isinstance(op, dict) or op.get("op") not in OP_NAMES:
        raise ValueError(f"an edit is one of {', '.join(OP_NAMES)}")
    k = op["op"]
    if k == "add_node":
        t = op.get("type")
        if t not in NODES.NODE_TYPES or t in NODES.ENTRY:
            raise ValueError(f"'{t}' is not a box you can add")
        nid = ID.sub("", str(op.get("id") or t))[:24] or t
        base, i = nid, 2
        while any(n["id"] == nid for n in d["nodes"]):
            nid, i = f"{base}{i}", i + 1
        node: dict = dict(id=nid, type=t, label=str(op.get("label") or NODES.BY_TYPE[t]["label"])[:60], config=_clean_cfg(op.get("config")), position=_slot(d, op.get("after")))
        if isinstance(op.get("settings"), dict):
            node["settings"] = {k2: v for k2, v in op["settings"].items() if k2 != "pin"}
        d["nodes"].append(node)
        if op.get("after"):
            _node(d, op["after"])
            d["edges"].append(dict(source=op["after"], target=nid, **({"source_port": op["port"]} if op.get("port") else {})))
        return f"added {NODES.BY_TYPE[t]['label']} '{node['label']}'"
    if k == "update_node":
        n = _node(d, op.get("id"))
        if n["type"] in NODES.ENTRY:
            raise ValueError("the starting box cannot be changed")
        if op.get("label"):
            n["label"] = str(op["label"])[:60]
        if isinstance(op.get("config"), dict):
            n["config"] = {**n["config"], **_clean_cfg(op["config"])}
        if isinstance(op.get("settings"), dict):
            n["settings"] = {**(n.get("settings") or {}), **{k2: v for k2, v in op["settings"].items() if k2 != "pin"}}
        return f"changed '{n['label']}'"
    if k == "remove_node":
        n = _node(d, op.get("id"))
        if n["type"] in NODES.ENTRY:
            raise ValueError("the starting box cannot be removed")
        d["nodes"] = [x for x in d["nodes"] if x["id"] != n["id"]]
        d["edges"] = [e for e in d["edges"] if n["id"] not in (e["source"], e["target"])]
        return f"removed '{n['label']}'"
    if k in ("connect", "disconnect"):
        s_, t_ = _node(d, op.get("source")), _node(d, op.get("target"))
        port = str(op.get("source_port") or op.get("port") or "out")
        if k == "connect":
            d["edges"].append(dict(source=s_["id"], target=t_["id"], **({"source_port": port} if port != "out" else {})))
            return f"connected '{s_['label']}' to '{t_['label']}'"
        d["edges"] = [e for e in d["edges"] if not (e["source"] == s_["id"] and e["target"] == t_["id"])]
        return f"disconnected '{s_['label']}' from '{t_['label']}'"
    if isinstance(op.get("name"), str) and op["name"].strip():
        d["name"] = op["name"].strip()[:80]
    if isinstance(op.get("description"), str):
        d["description"] = op["description"][:500]
    if op.get("autonomy") in AUTONOMY:
        d["autonomy"] = op["autonomy"]
    return "updated the workflow's name or settings"


def diff(old: dict, new: dict) -> dict:
    o, n = {x["id"]: x for x in old["nodes"]}, {x["id"]: x for x in new["nodes"]}
    oe = {(e["source"], e["target"], e.get("source_port", "out")) for e in old["edges"]}
    ne = {(e["source"], e["target"], e.get("source_port", "out")) for e in new["edges"]}
    return dict(added=sorted(set(n) - set(o)), removed=sorted(set(o) - set(n)), changed=sorted(i for i in set(o) & set(n) if json.dumps(o[i], sort_keys=True) != json.dumps(n[i], sort_keys=True)),
                edges_added=len(ne - oe), edges_removed=len(oe - ne), meta=any(old.get(k) != new.get(k) for k in ("name", "description", "autonomy")))


def catalog_text() -> str:
    lines = []
    for s in NODES.SPECS:
        if s["type"] in NODES.ENTRY:
            continue
        fields = ", ".join(f["name"] for f in s["fields"])
        out = s["outputs"] if isinstance(s["outputs"], list) else "one per label/case + other"
        lines.append(f"- {s['type']}: {s['desc']} config: {fields or '(none)'}; output ports: {out}")
    return "\n".join(lines)


BUILDER_PROMPT = """You help a person build a workflow in Agent Studio. A workflow is a graph of boxes. Reply with ONE JSON object only (no prose, no code fences):
{{"reply": "<one or two friendly sentences saying what you changed or asking ONE question>", "ops": [<edits>]}}
Edits (use only these): {{"op":"add_node","type":..,"label":..,"config":{{..}},"after":"<id of the box it follows>","port":"<output port of that box, default out>"}}, {{"op":"update_node","id":..,"label":..,"config":{{..}}}},
{{"op":"remove_node","id":..}}, {{"op":"connect","source":..,"target":..,"source_port":..}}, {{"op":"disconnect","source":..,"target":..}}, {{"op":"set_meta","name":..,"autonomy":"suggest|approve|auto"}}.
Boxes you may add:
{catalog}
Texts in config may use {{{{ $input }}}} (the task) and {{{{ $prev }}}} (what came just before). Agents you may use (id: what it does): {agents}
API endpoints you may use (source.endpoint): {ops}. Tools: {tools}.
Rules: never add a task input box (it exists); keep graphs small; connect every new box; never claim to confirm or run anything - the person ticks "I confirm" and runs it.
How much the person wants to be asked: {autonomy}. Systems the person allowed: {systems}.
Current workflow: {doc}
Conversation so far:
{chat}
"""


def parse_reply(text: str) -> tuple[str, list]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise WorkflowError("the model did not answer with edits")
    try:
        j = json.loads(m.group(0))
    except ValueError:
        raise WorkflowError("the model's answer was not valid JSON")
    ops = j.get("ops")
    return str(j.get("reply") or "")[:600], ops if isinstance(ops, list) else []


def template_ops(goal: str, agents: list[str], operations: list[dict]) -> list[dict]:
    """When no model can help: a plain chain of the chosen agents/endpoints, as edits."""
    from .core import template_draft
    t = template_draft(goal, agents, operations, "approve")
    ops: list[dict] = []
    prev = "start"
    for n in t["nodes"]:
        if n["type"] in ("input", "output"):
            continue
        ops.append(dict(op="add_node", id=n["id"], type=n["type"], label=n["label"], config=n["config"], after=prev))
        prev = n["id"]
    ops.append(dict(op="add_node", id="result", type="output", label="Result", after=prev))
    return ops
