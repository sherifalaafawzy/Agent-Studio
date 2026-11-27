"""Drag-and-drop agent workflows: the graph model, validation, the store (with versions), OpenAPI components and the engine.

A workflow is a graph the user draws (or an agent drafts): an **input** node, work nodes, an optional **output** node, and edges.
Work nodes are the building blocks ADK is made of -
  agent        an agent from the Agents page (an "LLM agent"), run with a prompt template
  tool         one of Agent Studio's tools (read-only ones unless you confirm otherwise)
  openapi      one operation of an imported API (GET unless writes are allowed AND the node is confirmed)
  sequential   a list of steps run one after another, each seeing the previous result ({{prev}})   (ADK SequentialAgent)
  parallel     a list of steps run at the same time, results joined                                (ADK ParallelAgent)
  loop         a list of steps repeated until the result contains a phrase or a maximum is reached (ADK LoopAgent)
Edges say who runs after whom; nodes with no path between them run in parallel. Text flows through `{{input}}` (the task),
`{{prev}}` (what the nodes just before produced) and `{{node_id}}` (any earlier node's result).

Every agent step runs through the normal chat engine, so spend caps, guardrails, memory and the run record all apply.
Autonomy decides how much the person stays in the loop: `suggest` (plan only, nothing runs), `approve` (each step waits for a yes)
or `auto`.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from ..db import Database

WF_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
NODE_ID = re.compile(r"^[A-Za-z][\w-]{0,30}$")
SRC_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
from . import expr as X
from . import nodes as NODES
from .nodes import CONTAINERS, NODE_TYPES, STEP_KINDS

DEFAULT_SETTINGS = dict(on_error="stop", retries=0, retry_wait_s=1.0, timeout_s=0.0, disabled=False, notes="", color="", pin=None)
AUTONOMY = ("suggest", "approve", "auto")
MAX_NODES, MAX_EDGES, MAX_STEPS, MAX_LOOP, MAX_VERSIONS, MAX_RUNS = 60, 120, 12, 10, 40, 50
LIMITS = dict(max_nodes=MAX_NODES, run_retention=100)         # the workflow settings (config.py) write here; clean() and the engine read them
SAFE_TOOLS = ("read_file", "list_dir", "glob_files", "search_text", "repo_map", "rag_search", "memory_search", "web_fetch", "web_search")
VAR = re.compile(r"\{\{\s*([\w-]+)\s*\}\}")
NODE_REF = re.compile(r"\$node\[\s*[\"']([^\"']+)[\"']\s*\]")
MAX_TEXT = 40_000


class WorkflowError(ValueError):
    """A workflow, component or run request that cannot be accepted; the message says why."""


def __getattr__(name: str):                       # OpenApiSources lives in sources.py (which imports this module); resolved lazily to avoid a cycle
    if name in ("OpenApiSources", "InUse"):
        from . import sources
        return getattr(sources, name)
    if name in ("Runner", "Run", "workflow_tool"):                # the engine imports this module, so it is resolved lazily too
        from . import engine
        return getattr(engine, name)
    raise AttributeError(name)


def render(template: str, vars_: dict[str, str]) -> str:
    return VAR.sub(lambda m: vars_.get(m.group(1), ""), template or "")


# ---------------------------------------------------------------------------------------------------- the graph
def _clean_step(s: Any, where: str) -> dict:
    if not isinstance(s, dict) or s.get("kind") not in STEP_KINDS:
        raise WorkflowError(f"{where}: a step is an agent, a tool or an API operation")
    out: dict = {"kind": s["kind"]}
    if s["kind"] == "agent":
        out.update(agent=str(s.get("agent") or "")[:40], prompt=str(s.get("prompt") or "{{prev}}")[:4000])
    elif s["kind"] == "tool":
        args = s.get("args") or {}
        if not isinstance(args, dict):
            raise WorkflowError(f"{where}: tool arguments must be an object")
        out.update(tool=str(s.get("tool") or "")[:40], args={str(k)[:40]: v if isinstance(v, (int, float, bool)) else str(v)[:2000] for k, v in list(args.items())[:20]},
                   confirmed=bool(s.get("confirmed")))
    else:
        args = s.get("args") or {}
        if not isinstance(args, dict):
            raise WorkflowError(f"{where}: operation arguments must be an object")
        out.update(source=str(s.get("source") or "")[:30], operation=str(s.get("operation") or "")[:80], confirmed=bool(s.get("confirmed")),
                   args={str(k)[:60]: v if isinstance(v, (int, float, bool)) else str(v)[:2000] for k, v in list(args.items())[:30]})
    return out


def clean(doc: Any) -> dict:
    """Normalise a workflow document; raises WorkflowError for anything that cannot even be saved (bad shape, cycles, dangling edges)."""
    if not isinstance(doc, dict):
        raise WorkflowError("a workflow is a JSON object")
    wid, name = str(doc.get("id") or ""), str(doc.get("name") or "").strip()
    if not WF_ID.match(wid):
        raise WorkflowError("the workflow id must be lowercase letters, digits and underscores, starting with a letter (2-41 characters)")
    if not name or len(name) > 80:
        raise WorkflowError("give the workflow a name (up to 80 characters)")
    autonomy = doc.get("autonomy", "approve")
    if autonomy not in AUTONOMY:
        raise WorkflowError(f"autonomy must be one of {', '.join(AUTONOMY)}")
    raw_nodes, raw_edges = doc.get("nodes"), doc.get("edges") or []
    if not isinstance(raw_nodes, list) or not raw_nodes or len(raw_nodes) > LIMITS["max_nodes"]:
        raise WorkflowError(f"a workflow has 1 to {LIMITS['max_nodes']} nodes")
    if not isinstance(raw_edges, list) or len(raw_edges) > MAX_EDGES:
        raise WorkflowError(f"at most {MAX_EDGES} connections")
    nodes, ids = [], set()
    for n in raw_nodes:
        if not isinstance(n, dict) or n.get("type") not in NODE_TYPES or not NODE_ID.match(str(n.get("id") or "")):
            raise WorkflowError("every node needs a type and a short id (letters, digits, - and _)")
        if n["id"] in ids:
            raise WorkflowError(f"two nodes are called '{n['id']}'")
        ids.add(n["id"])
        cfg = n.get("config") or {}
        if not isinstance(cfg, dict):
            raise WorkflowError(f"node {n['id']}: config must be an object")
        node = dict(id=n["id"], type=n["type"], label=str(n.get("label") or n["type"])[:60], position=_pos(n.get("position")))
        if n["type"] in STEP_KINDS:
            node["config"] = _clean_step({**cfg, "kind": n["type"]}, f"node {n['id']}")
            node["config"].pop("kind")
        elif n["type"] in CONTAINERS:
            steps = cfg.get("steps") or []
            if not isinstance(steps, list) or len(steps) > MAX_STEPS:
                raise WorkflowError(f"node {n['id']}: at most {MAX_STEPS} steps")
            c: dict = {"steps": [_clean_step(s, f"node {n['id']}") for s in steps]}
            if n["type"] == "loop":
                mi = cfg.get("max_iterations", 3)
                if isinstance(mi, bool) or not isinstance(mi, int) or not 1 <= mi <= MAX_LOOP:
                    raise WorkflowError(f"node {n['id']}: a loop repeats 1 to {MAX_LOOP} times")
                c.update(max_iterations=mi, until=str(cfg.get("until") or "")[:200])
            node["config"] = c
        elif n["type"] in NODES.NEW_TYPES:
            try:
                node["config"] = NODES.clean_config(n["type"], cfg, f"node {n['id']}")
            except ValueError as e:
                raise WorkflowError(str(e))
        elif n["type"] == "output" and str(cfg.get("value") or "").strip():
            try:
                node["config"] = NODES.clean_output(cfg, f"node {n['id']}")
            except ValueError as e:
                raise WorkflowError(str(e))
        else:
            node["config"] = {}
        if isinstance(n.get("settings"), dict) and n["type"] not in NODES.ROOTS + ("note",):
            try:
                st = NODES.clean_settings(n["settings"], f"node {n['id']}")
            except ValueError as e:
                raise WorkflowError(str(e))
            if st != DEFAULT_SETTINGS:
                node["settings"] = st
        nodes.append(node)
    if sum(1 for n in nodes if n["type"] in NODES.ENTRY) != 1:
        raise WorkflowError("a workflow needs exactly one starting box: Task input or Webhook (where the task comes in)")
    if sum(1 for n in nodes if n["type"] == "error_handler") > 1:
        raise WorkflowError("a workflow can have only one On error box")
    edges, seen = [], set()
    for e in raw_edges:
        if not isinstance(e, dict) or e.get("source") not in ids or e.get("target") not in ids or e["source"] == e["target"]:
            raise WorkflowError("every connection must join two different nodes of this workflow")
        sp = str(e.get("source_port") or "out")
        src_node = next(n for n in nodes if n["id"] == e["source"])
        if sp not in NODES.ports(src_node):
            raise WorkflowError(f"a connection leaves '{e['source']}' from an output it does not have ('{sp}'): " + (f"choose one of {', '.join(NODES.ports(src_node))}" if NODES.ports(src_node) else "nothing can follow it"))
        if (e["source"], e["target"], sp) in seen:
            continue
        seen.add((e["source"], e["target"], sp))
        edge = dict(id=str(e.get("id") or f"{e['source']}-{e['target']}" + ("" if sp == "out" else f"-{sp}"))[:70], source=e["source"], target=e["target"])
        if sp != "out":
            edge["source_port"] = sp
        edges.append(edge)
    roots = {n["id"] for n in nodes if n["type"] in NODES.ROOTS}
    if any(e["target"] in roots for e in edges):
        raise WorkflowError("nothing can feed a starting box (Task input, Webhook or On error)")
    order(nodes, edges)                                                     # raises on a cycle
    return dict(id=wid, name=name, description=str(doc.get("description") or "")[:500], autonomy=autonomy, nodes=nodes, edges=edges)


def _pos(p: Any) -> dict:
    if isinstance(p, dict) and all(isinstance(p.get(k), (int, float)) and not isinstance(p.get(k), bool) for k in ("x", "y")):
        return dict(x=round(float(p["x"]), 1), y=round(float(p["y"]), 1))
    return dict(x=0, y=0)


def order(nodes: list[dict], edges: list[dict]) -> list[str]:
    """Node ids in a runnable order (Kahn); raises WorkflowError when the connections form a loop."""
    indeg = {n["id"]: 0 for n in nodes}
    for e in edges:
        indeg[e["target"]] += 1
    ready = [i for i, d in indeg.items() if d == 0]
    out: list[str] = []
    while ready:
        cur = ready.pop(0)
        out.append(cur)
        for e in edges:
            if e["source"] == cur:
                indeg[e["target"]] -= 1
                if indeg[e["target"]] == 0:
                    ready.append(e["target"])
    if len(out) != len(nodes):
        raise WorkflowError("the connections form a loop: use a Loop node to repeat steps instead")
    return out


def problems(doc: dict, agents: set[str], sources: dict[str, dict], tools: set[str], workflows: set[str] | None = None, reach: dict[str, set[str]] | None = None,
             refs: dict[str, Any] | None = None) -> list[dict]:
    """What would stop this workflow from running, as plain sentences (severity 'error') or things to double-check ('warning')."""
    out: list[dict] = []

    def add(node: str, sev: str, msg: str) -> None:
        out.append(dict(node=node, severity=sev, message=msg))

    def step(nid: str, s: dict) -> None:
        if s["kind"] == "agent" and s["agent"] not in agents:
            add(nid, "error", f"agent '{s['agent'] or '(none chosen)'}' does not exist")
        elif s["kind"] == "tool":
            if s["tool"] not in tools:
                add(nid, "error", f"tool '{s['tool'] or '(none chosen)'}' is not available")
            elif s["tool"] not in SAFE_TOOLS and not s["confirmed"]:
                add(nid, "error", f"'{s['tool']}' changes things: tick 'I confirm' on the node to allow it")
        elif s["kind"] == "openapi":
            src = sources.get(s["source"])
            op = (src or {}).get("ops", {}).get(s["operation"])
            if src is None:
                add(nid, "error", f"API '{s['source'] or '(none chosen)'}' is not imported")
            elif op is None:
                add(nid, "error", f"the API has no operation '{s['operation'] or '(none chosen)'}'")
            elif op.get("hidden") or not op.get("enabled", True):
                add(nid, "error", f"the endpoint '{s['operation']}' is switched off in the API settings")
            elif op["method"] not in ("get", "head", "options"):
                if not src["allow_write"]:
                    add(nid, "error", f"{op['method'].upper()} {op['path']} changes data and writes are not allowed for this API")
                elif not s["confirmed"]:
                    add(nid, "error", f"{op['method'].upper()} {op['path']} changes data: tick 'I confirm' on the node to allow it")
                else:
                    add(nid, "warning", f"{op['method'].upper()} {op['path']} will change data when it runs")
    incoming = {e["target"] for e in doc["edges"]}
    for n in doc["nodes"]:
        if n["type"] in STEP_KINDS:
            step(n["id"], {"kind": n["type"], **n["config"]})
        elif n["type"] in CONTAINERS:
            if not n["config"]["steps"]:
                add(n["id"], "error", "add at least one step")
            for s in n["config"]["steps"]:
                step(n["id"], s)
            if n["type"] == "loop" and not n["config"]["until"]:
                add(n["id"], "warning", f"no stop phrase: it will always repeat {n['config']['max_iterations']} times")
        if n["type"] in ("sub_workflow", "for_each") and workflows is not None:
            wf = n["config"]["workflow"]
            if wf not in workflows:
                add(n["id"], "error", f"workflow '{wf or '(none chosen)'}' does not exist")
            elif wf == doc["id"] or (reach and doc["id"] in reach.get(wf, set())):
                add(n["id"], "error", f"workflow '{wf}' would end up running this workflow again")
        if n["type"] == "approval" and doc["autonomy"] == "auto" and not n["config"]["message"].strip():
            add(n["id"], "warning", "the question is empty")
        if refs is not None:
            _ref_problems(n, refs, add)
        if n["type"] not in NODES.ROOTS + ("note",) and n["id"] not in incoming:
            add(n["id"], "error", "nothing leads into this node, so it would never get anything to work on")
        labels = {x["id"] for x in doc["nodes"]} | {x["label"] for x in doc["nodes"]}
        for ref in sorted({m for v in _strings(n["config"]) for m in NODE_REF.findall(v)} - labels):
            add(n["id"], "warning", f"it refers to a box called '{ref}' that does not exist")
        if (n.get("settings") or {}).get("on_error") == "branch" and not any(e["source"] == n["id"] and e.get("source_port") == "error" for e in doc["edges"]):
            add(n["id"], "warning", "'on error: branch' is set but nothing is connected to its error output, so a failure ends the run")
    if not any(n["type"] == "output" for n in doc["nodes"]):
        add("", "warning", "no Output node: the answer is whatever the last nodes produce")
    handler = next((n for n in doc["nodes"] if n["type"] == "error_handler"), None)
    if handler is not None:
        shared = _reachable(doc, handler["id"]) & _reachable(doc, next(n["id"] for n in doc["nodes"] if n["type"] in NODES.ENTRY))
        for nid in sorted(shared):
            add(nid, "error", "this box follows both the main flow and the On error box: keep the error path separate")
    entry = next(n for n in doc["nodes"] if n["type"] in NODES.ENTRY)
    if entry["type"] == "webhook" and entry["config"]["respond"] == "node" and not any(n["type"] == "respond_webhook" for n in doc["nodes"]):
        add(entry["id"], "warning", "the Webhook answers with a 'Respond to webhook' box but there is none: the caller gets a plain 'done'")
    return out


def _reachable(doc: dict, start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        cur = todo.pop()
        for e in doc["edges"]:
            if e["source"] == cur and e["target"] not in seen:
                seen.add(e["target"])
                todo.append(e["target"])
    return seen


def _ref_problems(n: dict, refs: dict[str, Any], add) -> None:
    """Checks that need to know what exists in the app: MCP servers, knowledge bases, data sources, credentials, guardrail profiles."""
    t, c, nid = n["type"], n["config"], n["id"]
    if t == "http_request":
        if c["method"] not in NODES.READ_METHODS and not c["confirmed"]:
            add(nid, "error", f"{c['method']} changes data: tick 'I confirm' on the box to allow it")
        elif c["method"] not in NODES.READ_METHODS:
            add(nid, "warning", f"{c['method']} {c['url'][:60]} will change data when it runs")
        if c["credential"] and c["credential"] not in refs.get("credential", set()):
            add(nid, "error", f"the credential '{c['credential']}' does not exist (workflow settings)")
    elif t == "mcp_tool":
        srv = refs.get("mcp", {}).get(c["server"])
        if srv is None:
            add(nid, "error", f"MCP server '{c['server'] or '(none chosen)'}' does not exist")
        elif not srv["ready"]:
            add(nid, "error", f"MCP server '{c['server']}' is not ready ({srv['why']})")
        elif srv["read_only"] != "always" and not c["confirmed"]:
            add(nid, "warning", "this server can change things: a tool that is not marked read-only will be refused unless you tick 'I confirm'")
        elif c["confirmed"]:
            add(nid, "warning", f"{c['tool']} may change things when it runs")
    elif t == "knowledge" and c["kb"] not in refs.get("knowledge", set()):
        add(nid, "error", f"knowledge base '{c['kb'] or '(none chosen)'}' does not exist")
    elif t == "data_query" and c["source"] not in refs.get("data_source", set()):
        add(nid, "error", f"data source '{c['source'] or '(none chosen)'}' does not exist")
    elif t == "guardrail" and c["profile"] and c["profile"] not in refs.get("guardrail", set()):
        add(nid, "error", f"guardrail profile '{c['profile']}' does not exist")
    elif t == "memory" and c["operation"] == "save":
        add(nid, "warning", "this box saves to memory every time the workflow runs")
    elif t == "artifact":
        add(nid, "warning", "this box keeps a file every time the workflow runs")


def _strings(v) -> list[str]:
    """Every text inside a config (to look for `$node["x"]` references)."""
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [t for x in v.values() for t in _strings(x)]
    if isinstance(v, list):
        return [t for x in v for t in _strings(x)]
    return []


# ---------------------------------------------------------------------------------------------------- store
class WorkflowStore:
    def __init__(self, db: Database, clock=time.time):
        self.db, self.clock = db, clock

    def list(self) -> list[dict]:
        out = []
        last = {r["wf_id"]: r for r in self.db.query("SELECT wf_id,status,started,cost_usd FROM workflow_runs WHERE rowid IN (SELECT MAX(rowid) FROM workflow_runs GROUP BY wf_id)")}
        drafts = {r["wf_id"] for r in self.db.query("SELECT wf_id FROM workflow_drafts")}
        hooks = {r["wf_id"] for r in self.db.query("SELECT wf_id FROM workflow_hooks")}
        sched = {r["wf_id"]: r for r in self.db.query("SELECT wf_id,enabled,next_run FROM workflow_schedules")}
        for r in self.db.query("SELECT id,name,doc,version,updated FROM workflows ORDER BY updated DESC"):
            d = json.loads(r["doc"])
            lr = last.get(r["id"])
            out.append(dict(id=r["id"], name=r["name"], description=d.get("description", ""), version=r["version"], updated=r["updated"], nodes=sum(1 for n in d["nodes"] if n["type"] != "note"),
                            autonomy=d["autonomy"], last_run=dict(status=lr["status"], started=lr["started"], cost_usd=lr["cost_usd"]) if lr else None, has_draft=r["id"] in drafts,
                            trigger=next((n["type"] for n in d["nodes"] if n["type"] in NODES.ENTRY), "input"), has_hook=r["id"] in hooks,
                            schedule=dict(enabled=bool(sched[r["id"]]["enabled"]), next_run=sched[r["id"]]["next_run"] if sched[r["id"]]["enabled"] else None) if r["id"] in sched else None,
                            uses=sorted({n["config"]["workflow"] for n in d["nodes"] if n["type"] in ("sub_workflow", "for_each")})))
        return out

    def ids(self) -> list[str]:
        return [r["id"] for r in self.db.query("SELECT id FROM workflows ORDER BY updated DESC")]

    def get(self, wid: str) -> dict | None:
        r = self.db.one("SELECT doc,version,updated FROM workflows WHERE id=?", (wid,))
        return None if r is None else {**json.loads(r["doc"]), "version": r["version"], "updated": r["updated"]}

    def save(self, doc: Any, create: bool = False, label: str = "", note: str = "") -> dict:
        d = clean(doc)
        cur = self.db.one("SELECT version FROM workflows WHERE id=?", (d["id"],))
        if create and cur is not None:
            raise WorkflowError(f"a workflow '{d['id']}' already exists")
        v = (cur["version"] + 1) if cur else 1
        now = self.clock()
        if cur:
            self.db.execute("UPDATE workflows SET name=?, doc=?, version=?, updated=? WHERE id=?", (d["name"], json.dumps(d), v, now, d["id"]))
        else:
            self.db.execute("INSERT INTO workflows(id,name,doc,version,updated) VALUES(?,?,?,?,?)", (d["id"], d["name"], json.dumps(d), v, now))
        self.db.execute("INSERT INTO workflow_versions(wf_id,version,doc,ts,label,note) VALUES(?,?,?,?,?,?)", (d["id"], v, json.dumps(d), now, str(label or "")[:60], str(note or "")[:500]))
        self.db.execute("DELETE FROM workflow_versions WHERE wf_id=? AND version <= ?", (d["id"], v - MAX_VERSIONS))
        self.db.execute("DELETE FROM workflow_drafts WHERE wf_id=?", (d["id"],))                 # publishing a version replaces the autosaved draft
        return self.get(d["id"])  # type: ignore[return-value]

    def duplicate(self, wid: str, new_id: str = "", name: str = "") -> dict:
        d = self.get(wid)
        if d is None:
            raise KeyError(wid)
        base = new_id or f"{wid}_copy"
        nid, n = base, 2
        while self.get(nid) is not None:
            nid, n = f"{base}_{n}", n + 1
        d = {k: v for k, v in d.items() if k not in ("version", "updated")}
        d["nodes"] = [{**x, "settings": {**x["settings"], "pin": None}} if x.get("settings") else x for x in d["nodes"]]            # a pinned output is test data: not copied
        return self.save({**d, "id": nid, "name": (name or f"{d['name']} copy")[:80]}, create=True)

    def users(self, wid: str) -> list[str]:
        """Workflows that run this one (as a sub-workflow or a For each)."""
        return sorted(r["id"] for r in self.db.query("SELECT id,doc FROM workflows") if r["id"] != wid and
                      any(n["type"] in ("sub_workflow", "for_each") and n["config"]["workflow"] == wid for n in json.loads(r["doc"])["nodes"]))

    def uses(self, sid: str, op: str | None = None) -> list[str]:
        """Ids of the workflows with a node (or container step) that calls API `sid` (and endpoint `op`, when given)."""
        out = []
        for r in self.db.query("SELECT id,doc FROM workflows ORDER BY id"):
            for n in json.loads(r["doc"])["nodes"]:
                steps = [{"kind": n["type"], **n["config"]}] if n["type"] in STEP_KINDS else n["config"].get("steps", []) if n["type"] in CONTAINERS else []
                if any(x.get("kind") == "openapi" and x.get("source") == sid and (op is None or x.get("operation") == op) for x in steps):
                    out.append(r["id"])
                    break
        return out

    def credential_users(self, cid: str) -> list[str]:
        """Ids of the workflows with an HTTP request box that signs in with credential `cid`."""
        return sorted(r["id"] for r in self.db.query("SELECT id,doc FROM workflows")
                      if any(n["type"] == "http_request" and n["config"]["credential"] == cid for n in json.loads(r["doc"])["nodes"]))

    def delete(self, wid: str) -> None:
        if self.db.one("SELECT id FROM workflows WHERE id=?", (wid,)) is None:
            raise KeyError(wid)
        for t in ("workflows", "workflow_versions", "workflow_drafts", "workflow_hooks", "workflow_schedules"):
            self.db.execute(f"DELETE FROM {t} WHERE {'id' if t == 'workflows' else 'wf_id'}=?", (wid,))
        self.db.execute("DELETE FROM workflow_runs WHERE wf_id=?", (wid,))

    def versions(self, wid: str) -> list[dict]:
        return [dict(version=r["version"], ts=r["ts"], nodes=len(json.loads(r["doc"])["nodes"]), label=r["label"] or "", note=r["note"] or "")
                for r in self.db.query("SELECT version,ts,doc,label,note FROM workflow_versions WHERE wf_id=? ORDER BY version DESC", (wid,))]

    def version(self, wid: str, version: int) -> dict:
        r = self.db.one("SELECT doc FROM workflow_versions WHERE wf_id=? AND version=?", (wid, version))
        if r is None:
            raise KeyError(version)
        return json.loads(r["doc"])

    def restore(self, wid: str, version: int) -> dict:
        r = self.db.one("SELECT doc FROM workflow_versions WHERE wf_id=? AND version=?", (wid, version))
        if r is None:
            raise KeyError(version)
        return self.save(json.loads(r["doc"]), label=f"Restored v{version}")                       # restoring is a new version, so nothing is lost

    # -- the autosaved draft (an unpublished edit; "Save version" publishes it)
    def get_draft(self, wid: str) -> dict | None:
        r = self.db.one("SELECT doc,updated FROM workflow_drafts WHERE wf_id=?", (wid,))
        return None if r is None else dict(doc=json.loads(r["doc"]), updated=r["updated"])

    def put_draft(self, wid: str, doc: Any) -> dict:
        if self.get(wid) is None:
            raise KeyError(wid)
        d = clean({**doc, "id": wid} if isinstance(doc, dict) else doc)
        self.db.execute("INSERT INTO workflow_drafts(wf_id,doc,updated) VALUES(?,?,?) ON CONFLICT(wf_id) DO UPDATE SET doc=excluded.doc, updated=excluded.updated", (wid, json.dumps(d), self.clock()))
        return dict(doc=d, updated=self.clock())

    def delete_draft(self, wid: str) -> None:
        self.db.execute("DELETE FROM workflow_drafts WHERE wf_id=?", (wid,))


# ---------------------------------------------------------------------------------------------------- the wizard
def template_draft(goal: str, agents: list[str], operations: list[dict], autonomy: str) -> dict:
    """A plain chain when no model can draft it: input -> (API calls) -> agents in order -> output."""
    nodes = [dict(id="start", type="input", label="Task", config={}, position=dict(x=0, y=0))]
    edges: list[dict] = []
    last, x = "start", 260
    for i, op in enumerate(operations[:6], 1):
        nid = f"api{i}"
        nodes.append(dict(id=nid, type="openapi", label=op["operation"], position=dict(x=x, y=0),
                          config=dict(source=op["source"], operation=op["operation"], args={}, confirmed=False)))
        edges.append(dict(source=last, target=nid))
        last, x = nid, x + 260
    for i, a in enumerate(agents[:6], 1):
        nid = f"agent{i}"
        nodes.append(dict(id=nid, type="agent", label=a, position=dict(x=x, y=0), config=dict(agent=a, prompt="Task: {{input}}\n\nWhat we have so far:\n{{prev}}\n\nDo your part.")))
        edges.append(dict(source=last, target=nid))
        last, x = nid, x + 260
    nodes.append(dict(id="result", type="output", label="Result", config={}, position=dict(x=x, y=0)))
    edges.append(dict(source=last, target="result"))
    return dict(id="draft", name=(goal.strip()[:60] or "New workflow"), description=goal.strip()[:500], autonomy=autonomy, nodes=nodes, edges=edges)


DRAFT_PROMPT = """You design workflows for Agent Studio. Reply with ONE JSON object only (no prose, no code fences).
Goal: {goal}
Agents you may use (id: what it does): {agents}
API operations you may use (source.operation: what it does): {ops}
Schema: {{"name": str, "description": str, "nodes": [{{"id": str, "type": "input"|"agent"|"openapi"|"parallel"|"loop"|"output", "label": str,
 "config": for agent {{"agent": <id>, "prompt": str using {{{{input}}}} and {{{{prev}}}}}}; for openapi {{"source": str, "operation": str, "args": {{}}}};
 for parallel/loop {{"steps": [{{"kind": "agent", "agent": <id>, "prompt": str}}], "max_iterations": 1-10, "until": str}}}}],
 "edges": [{{"source": id, "target": id}}]}}
Rules: exactly one input node, no cycles, end with an output node, only use the listed agents and operations, keep it small."""


def parse_draft(text: str, goal: str, autonomy: str) -> dict:
    """Pull the JSON object out of a model's reply and make it a valid workflow (raises WorkflowError when it cannot)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise WorkflowError("the model did not return a workflow")
    try:
        d = json.loads(m.group(0))
    except ValueError:
        raise WorkflowError("the model's workflow was not valid JSON")
    d.update(id="draft", autonomy=autonomy)
    d.setdefault("name", goal[:60] or "New workflow")
    return clean(d)
