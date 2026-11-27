"""The workflow engine: runs a graph of boxes, follows branches, keeps a record of every run.

Boxes run as soon as every box before them has finished *or been left out*. A box with ports (If, Switch, Classifier, Approval, and any box set to
"on error: branch") sends its result down one port; connections from the other ports are pruned, and boxes that only hang off a pruned branch are
marked "skipped". Merge waits for the branches that actually ran. Values are text (a box's JSON result is also readable as `.json` in expressions).

Every agent / AI step runs through the normal chat engine or the guarded model call, so spend caps, guardrails, memory and the run record all apply.
Autonomy decides how much the person stays in the loop: `suggest` (plan only), `approve` (each working box waits for a yes) or `auto`; an "Ask a
person" box always waits, in any mode. Finished runs are saved so they can be listed and reopened.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import core as C
from . import execs as EX
from . import expr as X
from . import nodes as NODES
from . import transform as T
from .core import AUTONOMY, CONTAINERS, MAX_TEXT, SAFE_TOOLS, STEP_KINDS, WorkflowError, order
from .http import HttpClient

MAX_DEPTH, MAX_ITEMS = 3, 100
TEST_TIMEOUT_S = 180
BRANCHING = ("if", "switch", "classifier", "approval", "compare", "guardrail")


@dataclass
class Run:
    id: str
    workflow: str
    task: str
    autonomy: str
    status: str = "running"                  # running | done | error | stopped | plan
    nodes: dict = field(default_factory=dict)
    final: str = ""
    error: str | None = None
    plan: list = field(default_factory=list)
    started: float = field(default_factory=time.time)
    ended: float | None = None
    cost_usd: float = 0.0
    tokens: int = 0
    origin: str = "manual"                   # manual | webhook | schedule | agent | run_from: what started this run
    decisions: dict = field(default_factory=dict)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task_ref: Any = None
    version: int = 0
    depth: int = 0
    parent: str | None = None
    name: str = ""
    response: dict | None = None             # what a "Respond to webhook" box said to send back
    failed: dict | None = None               # the box that ended the run: {id, label, error} (for the On error box)
    seed: tuple | None = None                # (outputs, produced, boxes to run) when a run starts from one box and reuses an earlier run
    from_node: str | None = None

    def public(self) -> dict:
        return dict(id=self.id, workflow=self.workflow, task=self.task, autonomy=self.autonomy, status=self.status, final=self.final, error=self.error, plan=self.plan, started=self.started,
                    ended=self.ended, cost_usd=round(self.cost_usd, 6), tokens=self.tokens, origin=self.origin, version=self.version, parent=self.parent, from_node=self.from_node,
                    nodes={k: dict(v) for k, v in self.nodes.items()})


def _json_or_none(text: str) -> Any:
    t = (text or "").lstrip()
    if t[:1] in "{[":
        try:
            return json.loads(text)
        except ValueError:
            return None
    return None


def _items(v: Any) -> list:
    if isinstance(v, list):
        return v[:MAX_ITEMS]
    if isinstance(v, str):
        j = _json_or_none(v)
        if isinstance(j, list):
            return j[:MAX_ITEMS]
        return [x for x in v.splitlines() if x.strip()][:MAX_ITEMS]
    return [] if v is None else [v]


class Runner:
    def __init__(self, s, store, sources, approve_timeout: float = 3600.0):
        self.s, self.store, self.sources, self.approve_timeout = s, store, sources, approve_timeout
        self.runs: dict[str, Run] = {}
        self._http: HttpClient | None = None

    @property
    def http(self) -> HttpClient:
        if self._http is None:
            self._http = HttpClient(lambda: self.s.wf_config.config())
        return self._http

    @http.setter
    def http(self, client: HttpClient) -> None:                  # tests (and anything that needs a proxy) swap the client
        self._http = client

    # -- checks
    def check(self, doc: dict) -> list[dict]:
        from ..harness.tools import ALL_TOOLS
        ids = set(self.store.ids())
        reach: dict[str, set[str]] = {}
        for wid in ids:                                          # which workflows each one ends up calling (to refuse a workflow that would run itself)
            d = self.store.get(wid)
            reach[wid] = {n["config"]["workflow"] for n in (d["nodes"] if d else []) if n["type"] in ("sub_workflow", "for_each")}
        for _ in range(MAX_DEPTH + 2):
            for k, v in reach.items():
                reach[k] = v | {y for x in v for y in reach.get(x, set())}
        own = {n["config"]["workflow"] for n in doc["nodes"] if n["type"] in ("sub_workflow", "for_each")}
        reach[doc["id"]] = own | {y for x in own for y in reach.get(x, set())}
        return C.problems(doc, {a.id for a in self.s.agents.list()}, self.sources.for_validation(), set(ALL_TOOLS), ids | {doc["id"]}, {k: v for k, v in reach.items() if k != doc["id"]}, self.refs())

    def refs(self) -> dict:
        """What boxes may point at: connected MCP servers, knowledge bases, data sources, guardrail profiles and credentials."""
        from .. import mcp_catalog
        s = self.s
        mcp = {}
        for m in s.mcp.list():
            entry = mcp_catalog.get(m.id) or {}
            why = "switched off" if not m.enabled else "waiting for your approval" if m.status == "pending_approval" else "not set up yet"
            mcp[m.id] = dict(ready=m.ready(), why=why, read_only=entry.get("read_only", "none"))
        return dict(mcp=mcp, knowledge={k["id"] for k in s.rag.list_kbs()}, data_source={x["id"] for x in s.data.public()}, guardrail={p.id for p in s.guardrails.profiles()}, credential=s.wf_config.ids())

    # -- starting and steering
    def start(self, wid: str, task: str, autonomy: str | None = None, *, depth: int = 0, parent: str | None = None, from_node: str | None = None, base: str | None = None,
              origin: str = "manual") -> Run:
        doc = self.store.get(wid)
        if doc is None:
            raise KeyError(wid)
        autonomy = autonomy or doc["autonomy"]
        if autonomy not in AUTONOMY:
            raise WorkflowError(f"autonomy must be one of {', '.join(AUTONOMY)}")
        if not (task or "").strip() or len(task) > 20_000:
            raise WorkflowError("write the task for the workflow (up to 20,000 characters)")
        if depth > MAX_DEPTH:
            raise WorkflowError(f"workflows inside workflows go at most {MAX_DEPTH} levels deep")
        errs = [p for p in self.check(doc) if p["severity"] == "error"]
        if errs:
            raise WorkflowError("fix these first: " + "; ".join(f"{e['node'] or 'workflow'}: {e['message']}" for e in errs[:4]))
        run = Run(uuid.uuid4().hex[:10], wid, task.strip(), autonomy, version=doc.get("version", 0), depth=depth, parent=parent, name=doc["name"], origin="run_from" if from_node else origin)
        run.nodes = {n["id"]: dict(status="pending", output="", error=None, started=None, ended=None, label=n["label"], type=n["type"], ports=[], attempts=0, input="")
                     for n in doc["nodes"] if n["type"] != "note"}
        if from_node:
            run.from_node, run.seed = from_node, self._seed(run, doc, from_node, base)
        self.runs[run.id] = run
        for old in list(self.runs)[:-C.MAX_RUNS]:
            self.runs.pop(old, None)
        run.task_ref = asyncio.ensure_future(self._drive(run, doc))
        return run

    def _seed(self, run: Run, doc: dict, from_node: str, base: str | None) -> tuple:
        """Run only `from_node` and what follows it, taking every earlier box's output from an earlier run of this workflow."""
        by_id = {n["id"]: n for n in doc["nodes"] if n["type"] != "note"}
        if from_node not in by_id or by_id[from_node]["type"] in NODES.ROOTS:
            raise WorkflowError("choose a box after the start to run from")
        prior = self.runs.get(base or "")
        prior_nodes = prior.public()["nodes"] if prior is not None else (self.saved_run(base or "") or {}).get("nodes")
        if prior_nodes is None or (prior is not None and prior.workflow != doc["id"]) or (prior is None and (self.saved_run(base or "") or {}).get("workflow") != doc["id"]):
            raise WorkflowError("run the whole workflow once first, then you can run from a box and reuse that run")
        todo, closure = [from_node], {from_node}
        while todo:
            cur = todo.pop()
            for e in doc["edges"]:
                if e["source"] == cur and e["target"] in by_id and e["target"] not in closure:
                    closure.add(e["target"])
                    todo.append(e["target"])
        handler = {n["id"] for n in by_id.values() if n["type"] == "error_handler"}
        outputs: dict[str, str] = {}
        produced: dict[str, set[str]] = {}
        for nid, n in by_id.items():
            if nid in closure or nid in handler:
                continue
            old = prior_nodes.get(nid)
            if old is None or old["status"] not in ("done", "skipped"):
                raise WorkflowError(f"'{n['label']}' has no earlier output to reuse: run the whole workflow first")
            ports = set(old.get("ports") or ["out"]) if old["status"] == "done" else set()
            outputs[nid], produced[nid] = old.get("output", ""), ports
            run.nodes[nid].update(status=old["status"], output=old.get("output", ""), ports=sorted(ports), reused=True)
        return outputs, produced, closure

    def decide(self, rid: str, node: str, decision: str, values: dict | None = None) -> None:
        run = self.runs.get(rid)
        if run is None:
            raise KeyError(rid)
        if decision not in ("approve", "skip", "stop"):
            raise WorkflowError("decide approve, skip or stop")
        if run.nodes.get(node, {}).get("status") != "waiting_approval":
            raise WorkflowError("that step is not waiting for an answer")
        st = run.nodes[node]
        if decision == "approve" and st.get("form"):
            st["answers"] = self._answers(st["form"], values or {})
        fut = run.decisions.get(node)
        if fut is not None and not fut.done():
            fut.set_result(decision)

    @staticmethod
    def _answers(form: list[dict], values: dict) -> dict:
        """The details a person filled in, checked against the questions the box asked."""
        out: dict[str, Any] = {}
        for f in form:
            v = values.get(f["name"])
            if v in (None, ""):
                if f["required"]:
                    raise WorkflowError(f"'{f['label']}' is needed")
                out[f["name"]] = False if f["type"] == "bool" else ""                 # an optional detail left empty
                continue
            if f["type"] == "number":
                n = T.number(v)
                if n is None:
                    raise WorkflowError(f"'{f['label']}' must be a number")
                v = int(n) if n.is_integer() else n
            elif f["type"] == "bool":
                v = bool(v)
            elif f["type"] == "select" and v not in f["options"]:
                raise WorkflowError(f"'{f['label']}' must be one of {', '.join(f['options'])}")
            elif f["type"] in ("text", "select"):
                v = str(v)[:2000]
            out[f["name"]] = v
        return out

    def stop(self, rid: str) -> Run:
        run = self.runs.get(rid)
        if run is None:
            raise KeyError(rid)
        run.stop.set()
        if run.task_ref is not None and not run.task_ref.done():
            run.task_ref.cancel()
        return run

    # -- saved runs
    def forget(self, wid: str) -> None:
        """A deleted workflow's finished runs leave the list of runs kept in memory too (the ones still working finish on their own)."""
        for rid in [i for i, r in self.runs.items() if r.workflow == wid and r.ended is not None]:
            self.runs.pop(rid, None)

    def _persist(self, run: Run) -> None:
        if run.parent:
            return
        db = self.store.db
        db.execute("INSERT OR REPLACE INTO workflow_runs(id,wf_id,wf_version,status,autonomy,task,started,ended,cost_usd,error,final,nodes,origin,tokens,from_node) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (run.id, run.workflow, run.version, run.status, run.autonomy, run.task[:20_000], run.started, run.ended, run.cost_usd, run.error, run.final, json.dumps(run.public()["nodes"], default=str),
                    run.origin, run.tokens, run.from_node or ""))
        db.execute("DELETE FROM workflow_runs WHERE wf_id=? AND id NOT IN (SELECT id FROM workflow_runs WHERE wf_id=? ORDER BY started DESC LIMIT ?)", (run.workflow, run.workflow, C.LIMITS["run_retention"]))

    def saved_runs(self, wid: str, limit: int = 20) -> list[dict]:
        return [dict(id=r["id"], status=r["status"], autonomy=r["autonomy"], task=r["task"][:200], started=r["started"], ended=r["ended"], cost_usd=r["cost_usd"], version=r["wf_version"], error=r["error"], origin=r["origin"])
                for r in self.store.db.query("SELECT * FROM workflow_runs WHERE wf_id=? ORDER BY started DESC LIMIT ?", (wid, limit))]

    def saved_run(self, rid: str) -> dict | None:
        r = self.store.db.one("SELECT * FROM workflow_runs WHERE id=?", (rid,))
        return None if r is None else dict(id=r["id"], workflow=r["wf_id"], task=r["task"], autonomy=r["autonomy"], status=r["status"], final=r["final"], error=r["error"], plan=[],
                                           started=r["started"], ended=r["ended"], cost_usd=r["cost_usd"], tokens=r["tokens"], origin=r["origin"], version=r["wf_version"], parent=None, from_node=r["from_node"] or None,
                                           nodes=json.loads(r["nodes"]))

    # -- one run
    @staticmethod
    def _reach(doc: dict, starts: list[str]) -> set[str]:
        seen, todo = set(starts), list(starts)
        while todo:
            cur = todo.pop()
            for e in doc["edges"]:
                if e["source"] == cur and e["target"] not in seen:
                    seen.add(e["target"])
                    todo.append(e["target"])
        return seen

    async def _loop(self, run: Run, doc: dict, by_id: dict, edges: list, pending: set, running: dict, outputs: dict, produced: dict) -> bool:
        """Run the boxes of `by_id` until none is left (or one fails / is stopped). True when the run was cut short."""
        inc = {i: [e for e in edges if e["target"] == i] for i in by_id}
        stopped = False
        while (pending or running) and not stopped:
            progressed = True
            while progressed:                                                       # prune boxes that no live connection reaches, until nothing changes
                progressed = False
                for nid in sorted(pending):
                    if not all(e["source"] in produced for e in inc[nid]):
                        continue
                    live = [e for e in inc[nid] if e.get("source_port", "out") in produced[e["source"]]]
                    pending.discard(nid)
                    progressed = True
                    if by_id[nid]["type"] not in NODES.ROOTS and not live:
                        run.nodes[nid].update(status="skipped", output="", ended=time.time())
                        produced[nid] = set()
                    else:
                        running[asyncio.ensure_future(self._node(run, doc, by_id[nid], outputs, [e["source"] for e in live]))] = nid
            if not running:  # pragma: no cover - cannot happen for a valid graph (every box's inputs are produced by boxes before it)
                break
            done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                nid = running.pop(t)
                res = t.result()
                if res is None:
                    stopped = True
                else:
                    outputs[nid], produced[nid] = res
        return stopped

    async def _drive(self, run: Run, doc: dict) -> None:
        every = {n["id"]: n for n in doc["nodes"] if n["type"] != "note"}
        handlers = [i for i, n in every.items() if n["type"] == "error_handler"]
        after_error = self._reach(doc, handlers) if handlers else set()          # the On error box and what follows it: kept out of the main flow
        by_id = {i: n for i, n in every.items() if i not in after_error}
        edges = [e for e in doc["edges"] if e["source"] in by_id and e["target"] in by_id]
        running: dict = {}
        outputs: dict[str, str] = {}
        produced: dict[str, set[str]] = {}
        try:
            if run.autonomy == "suggest":
                run.plan = [dict(node=i, type=by_id[i]["type"], label=by_id[i]["label"], does=self._describe(by_id[i])) for i in order(list(by_id.values()), edges)]
                run.status = "plan"
                return
            pending = set(by_id)
            if run.seed is not None:
                outputs, produced, pending = dict(run.seed[0]), dict(run.seed[1]), set(run.seed[2]) - set(after_error)
            stopped = await self._loop(run, doc, by_id, edges, pending, running, outputs, produced)
            for t in running:
                t.cancel()
            for n in run.nodes.values():                               # branches cut off by a stop are shown as stopped, not left waiting
                if n["status"] in ("running", "waiting_approval"):
                    n["status"] = "stopped"
            run.final = self._final(by_id, edges, outputs)
            if run.status != "error":
                run.status = "stopped" if stopped or run.stop.is_set() else "done"
            elif handlers and run.failed:
                await self._on_error(run, doc, every, after_error, outputs, produced, running)
            for i in after_error:                                      # an On error path that never ran is shown as skipped, not left waiting
                if run.nodes[i]["status"] == "pending":
                    run.nodes[i]["status"] = "skipped"
        except asyncio.CancelledError:
            run.status = "stopped"
            for t in running:                                                    # stopping the run stops every box still working (and any workflow it started)
                t.cancel()
            for n in run.nodes.values():
                if n["status"] in ("running", "waiting_approval"):
                    n["status"] = "stopped"
        except Exception as e:  # noqa: BLE001
            run.status, run.error = "error", f"{type(e).__name__}: {str(e)[:300]}"
        finally:
            run.ended = time.time()
            try:
                self._persist(run)
            except Exception:  # noqa: BLE001 - a run that cannot be saved is still a finished run
                pass

    @staticmethod
    def _final(by_id: dict, edges: list, outputs: dict) -> str:
        sinks = [i for i in by_id if not any(e["source"] == i for e in edges) and i in outputs and by_id[i]["type"] not in NODES.ENTRY]
        outs = [i for i in sinks if by_id[i]["type"] == "output"] or sinks
        return "\n\n".join(outputs[i] for i in outs)[:MAX_TEXT]

    async def _on_error(self, run: Run, doc: dict, every: dict, after_error: set, outputs: dict, produced: dict, running: dict) -> None:
        """The run failed and the workflow has an On error box: run that path (with what failed as its input). The run still ends as failed."""
        sub = {i: every[i] for i in after_error}
        edges = [e for e in doc["edges"] if e["source"] in sub and e["target"] in sub]
        run.stop.clear()                                                       # the failed box set it to end the main flow; the clean-up path must be allowed to run
        await self._loop(run, doc, sub, edges, set(sub), running, outputs, produced)
        for t in running:                                                      # a clean-up box that failed leaves its siblings behind: stop them too
            t.cancel()
        for i in sub:
            if run.nodes[i]["status"] in ("running", "waiting_approval"):
                run.nodes[i]["status"] = "stopped"
        run.stop.set()
        handled = self._final(sub, edges, outputs)
        if handled:
            run.final = handled

    def _describe(self, n: dict) -> str:
        c = n["config"]
        t = n["type"]
        if t == "agent":
            return f"Ask agent '{c['agent']}': {c['prompt'][:120]}"
        if t == "tool":
            return f"Use the {c['tool']} tool"
        if t == "openapi":
            return f"Call {c['source']}.{c['operation']}"
        if t in CONTAINERS:
            return f"{t.capitalize()} of {len(c['steps'])} step(s)" + (f", up to {c['max_iterations']} times" if t == "loop" else "")
        return {"input": "Start", "output": "Collect the result", "llm": f"Ask the model: {c.get('prompt', '')[:100]}", "classifier": f"Choose one of: {', '.join(c.get('labels', []))}",
                "if": f"Branch on {c.get('condition', '')[:80]}", "switch": f"Branch by {c.get('value', '')[:60]}", "merge": "Combine the branches that ran", "wait": f"Wait {c.get('seconds', 0)} s",
                "approval": "Ask you before continuing", "stop": "Stop with a message", "sub_workflow": f"Run workflow '{c.get('workflow', '')}'", "for_each": f"Run '{c.get('workflow', '')}' for each item",
                "set": f"Set {len(c.get('fields', []))} field(s)", "template": "Fill in a text", "parse_json": "Read JSON",
                "compare": f"Check {len(c.get('conditions', []))} condition(s)", "error_handler": "Runs only if something fails", "webhook": "Start when the secret address is called", "schedule": "Start on a schedule", "notify": f"Notify: {str(c.get('title', ''))[:60]}",
                "respond_webhook": f"Answer the caller with status {c.get('status')}", "http_request": f"{c.get('method')} {str(c.get('url', ''))[:80]}",
                "mcp_tool": f"Call {c.get('server')}.{c.get('tool')}", "memory": f"{c.get('operation', '').capitalize()} memory", "knowledge": f"Search the knowledge base '{c.get('kb')}'",
                "data_query": f"Read from the data source '{c.get('source')}'", "artifact": f"Keep a file named {str(c.get('name', ''))[:40]}", "guardrail": f"Check the {c.get('stage')} with your guardrails",
                "filter": "Keep the items that pass the rules", "sort": f"Sort by {c.get('field') or 'the item'}", "limit": f"Keep {c.get('count')} item(s)", "dedupe": "Remove repeats",
                "aggregate": f"{c.get('op')} the items", "split_out": f"Split by {c.get('mode')}", "join": "Join the items into text", "merge_by_key": f"Merge two lists on {c.get('key')}",
                "date_time": f"Date and time: {c.get('operation')}", "regex": f"Text pattern: {c.get('mode')}"}.get(t, t)

    # -- values for expressions
    def _scope(self, run: Run, doc: dict, outputs: dict[str, str], prev: str, item: Any = None, index: int = 0, error: str | None = None) -> dict:
        by_label: dict[str, dict] = {}
        for n in doc["nodes"]:
            if n["id"] in outputs:
                v = dict(text=outputs[n["id"]], json=_json_or_none(outputs[n["id"]]))
                by_label[n["id"]] = by_label[n["label"]] = v
        pj = _json_or_none(prev)
        scope: dict[str, Any] = {"$input": run.task, "$prev": prev, "$json": pj, "$node": by_label, "$item": item, "$index": index, "$error": error, "$legacy": True,
                                 "$run": dict(id=run.id), "$workflow": dict(name=doc["name"], id=doc["id"]), "$vars": {}, "$now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "input": run.task, "prev": prev}
        scope.update(outputs)
        return scope

    @staticmethod
    def _legacy_scope(vars_: dict) -> dict:
        if "$input" in vars_:
            return vars_
        return {"$input": vars_.get("input", ""), "$prev": vars_.get("prev", ""), "$json": _json_or_none(vars_.get("prev", "")), "$node": {k: dict(text=v, json=None) for k, v in vars_.items()},
                "$item": None, "$index": 0, "$error": None, "$legacy": True, "$run": {}, "$workflow": {}, "$vars": {}, "$now": "", **vars_}

    # -- one box
    async def _node(self, run: Run, doc: dict, n: dict, outputs: dict[str, str], live: list[str]) -> tuple[str, set[str]] | None:
        st = run.nodes[n["id"]]
        prev = "\n\n".join(outputs[p] for p in live)
        scope = self._scope(run, doc, outputs, prev)
        s = n.get("settings") or {}
        t = n["type"]
        if t in NODES.ENTRY:
            st.update(status="done", output=run.task, ended=time.time())
            return run.task, {"out"}
        if t == "error_handler":
            info = T.to_json(dict(message=run.error or "", node=(run.failed or {}).get("id", ""), label=(run.failed or {}).get("label", ""), error=(run.failed or {}).get("error", "")))
            st.update(status="done", output=info, ended=time.time())
            return info, {"out"}
        if s.get("pin") is not None and t != "output":
            st.update(status="done", output=s["pin"][:MAX_TEXT], pinned=True, ended=time.time())
            ps = NODES.ports(n)
            return s["pin"], ({ps[0]} if t in BRANCHING else set(ps))               # a pinned branching box takes its first branch
        if s.get("disabled"):
            st.update(status="skipped", output=prev[:MAX_TEXT], ended=time.time())
            return prev, {"out"}
        if t == "output":
            try:
                val = X.render(n["config"]["value"], scope) if n["config"].get("value") else prev
            except X.ExprError as e:
                st.update(status="error", error=str(e), ended=time.time())
                run.status, run.error = "error", f"step '{n['label']}' failed: {str(e)[:200]}"
                run.failed = dict(id=n["id"], label=n["label"], error=str(e))
                run.stop.set()
                return None
            st.update(status="done", output=val[:MAX_TEXT], ended=time.time())
            return val, {"out"}
        if run.autonomy == "approve" and t in NODES.ASKS and not await self._ask_person(run, n, st, f"Run '{n['label']}'?"):
            if st["status"] == "skipped":
                return "", {"out"}
            return None
        st.update(status="running", started=time.time(), input=prev[:2000])
        attempts = 1 + int(s.get("retries", 0))
        err: Exception | None = None
        for attempt in range(1, attempts + 1):
            st["attempts"] = attempt
            try:
                coro = self._exec(run, doc, n, st, scope, outputs, live, prev)
                res = await (asyncio.wait_for(coro, s["timeout_s"]) if s.get("timeout_s") else coro)
                if res is None:
                    return None
                out, ports_ = res
                st.update(status="done", output=out[:MAX_TEXT], ended=time.time(), ports=sorted(ports_))
                return out, ports_
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                err = TimeoutError(f"took longer than {s['timeout_s']:g} s")
            except Exception as e:  # noqa: BLE001 - one failed step is reported on that node
                err = e
            if attempt < attempts:
                await asyncio.sleep(float(s.get("retry_wait_s", 0)))
        msg = f"{type(err).__name__}: {str(err)[:300]}"
        if s.get("on_error") == "continue":
            st.update(status="done", output="", error=msg, ended=time.time(), ports=["out"])
            return "", {"out"}
        if s.get("on_error") == "branch":
            st.update(status="error", output=msg, error=msg, ended=time.time(), ports=["error"])
            return msg, {"error"}
        st.update(status="error", error=msg, ended=time.time())
        run.status, run.error = "error", f"step '{n['label']}' failed: {str(err)[:200]}"
        run.failed = dict(id=n["id"], label=n["label"], error=msg)
        run.stop.set()
        return None

    async def _ask_person(self, run: Run, n: dict, st: dict, message: str) -> bool:
        """Wait for approve / skip / stop. True = go ahead. On skip the node is marked skipped; on stop the run stops."""
        st.update(status="waiting_approval", message=message[:500], form=n["config"].get("form") or [] if n["type"] == "approval" else [])
        fut = run.decisions[n["id"]] = asyncio.get_event_loop().create_future()
        try:
            decision = await asyncio.wait_for(fut, self.approve_timeout)
        except asyncio.TimeoutError:
            decision = "stop"
        if decision == "stop":
            st.update(status="stopped")
            run.stop.set()
            return False
        if decision == "skip":
            st.update(status="skipped", output="")
            return False
        return True

    async def _exec(self, run: Run, doc: dict, n: dict, st: dict, scope: dict, outputs: dict[str, str], live: list[str], prev: str) -> tuple[str, set[str]] | None:
        t, c = n["type"], n["config"]
        out = {"out"}
        if t in STEP_KINDS:
            return await self._step(run, {"kind": t, **c}, scope), out
        if t in CONTAINERS:
            return await self._container(run, n, scope), out
        if t == "llm":
            prompt = X.render(c["prompt"], scope)
            system = X.render(c["system"], scope) if c["system"] else ""
            if c["json"]:
                system = (system + "\nAnswer with one valid JSON value and nothing else.").strip()
            return await self._llm(run, (f"{system}\n\n" if system else "") + prompt), out
        if t == "classifier":
            text, labels = X.render(c["text"], scope), c["labels"]
            ans = (await self._llm(run, f"Classify the text into exactly one of these labels: {', '.join(labels)}.\n{c['hint'] + chr(10) if c['hint'] else ''}"
                                         f"Reply with the label only.\n\nText:\n{text}")).strip().strip(".\"'").lower()
            label = next((x for x in labels if x.lower() == ans), None) or next((x for x in labels if x.lower() in ans), None)
            st["decision"] = label or "other"
            return prev, {label or "other"}
        if t == "if":
            ok = X._Eval.truthy(X.render_value(c["condition"], scope))
            st["decision"] = "true" if ok else "false"
            return prev, {"true" if ok else "false"}
        if t == "switch":
            v = X._text(X.render_value(c["value"], scope)).strip().lower()
            case = next((x for x in c["cases"] if x.lower() == v), None)
            st["decision"] = case or "other"
            return prev, {case or "other"}
        if t == "merge":
            parts = [outputs[p] for p in live]
            return (json.dumps([_json_or_none(p) if _json_or_none(p) is not None else p for p in parts], ensure_ascii=False) if c["mode"] == "json_array" else c["separator"].join(parts)), out
        if t == "wait":
            await asyncio.sleep(c["seconds"])
            return prev, out
        if t == "approval":
            ok = await self._ask_person(run, n, st, X.render(c["message"], scope) or "Continue?")
            if run.stop.is_set():
                return None
            st.update(status="running")
            return (T.to_json(st["answers"]) if ok and st.get("answers") else prev), {"approved" if ok else "rejected"}
        if t == "stop":
            raise WorkflowError(X.render(c["message"], scope) or "stopped")
        if t == "sub_workflow":
            return await self._sub(run, c["workflow"], X.render(c["task"], scope)), out
        if t == "for_each":
            return await self._for_each(run, doc, c, scope, outputs, prev), out
        if t == "set":
            return json.dumps({f["name"]: X.render_value(f["value"], scope) for f in c["fields"]}, ensure_ascii=False, default=str), out
        if t == "template":
            return X.render(c["text"], scope), out
        if t == "parse_json":
            v = X.render_value(c["from"], scope)
            try:
                return json.dumps(json.loads(v) if isinstance(v, str) else v, ensure_ascii=False, indent=2), out
            except ValueError:
                raise WorkflowError("that text is not valid JSON")
        return await EX.execute(self, run, doc, n, st, scope, prev)

    async def test_node(self, doc: dict, nid: str, input_text: str, task: str, allow_side_effects: bool = False) -> dict:
        """Try ONE box with the text you give it as its input, nothing else running. A box that could change something is not run unless `allow_side_effects` says so."""
        n = next((x for x in doc["nodes"] if x["id"] == nid and x["type"] != "note"), None)
        if n is None:
            raise KeyError(nid)
        if n["type"] == "approval":
            return dict(status="not_run", message="This box waits for a person, so it can only be tried in a real run.", side_effects=False)
        side = EX.has_side_effects(n)
        if side and not allow_side_effects:
            return dict(status="not_run", message="This box could change something or spend money, so it was not run. Tick 'allow side effects' to try it for real.", side_effects=True)
        probe = {**n, "settings": {**(n.get("settings") or {}), "pin": None, "disabled": False}}
        run = Run(uuid.uuid4().hex[:10], doc["id"], (task or input_text or "test").strip(), "auto", name=doc["name"])
        run.nodes = {nid: dict(status="pending", output="", error=None, started=None, ended=None, label=n["label"], type=n["type"], ports=[], attempts=0, input="")}
        try:
            await asyncio.wait_for(self._node(run, {**doc, "nodes": [probe]}, probe, {"__test__": input_text}, ["__test__"]), TEST_TIMEOUT_S)
        except asyncio.TimeoutError:
            run.nodes[nid].update(status="error", error=f"took longer than {TEST_TIMEOUT_S:g} s")
        st = run.nodes[nid]
        return dict(status=st["status"], output=st["output"], error=st["error"], ports=st.get("ports") or [], decision=st.get("decision"), side_effects=side, cost_usd=round(run.cost_usd, 6))

    async def _sub(self, run: Run, wid: str, task: str) -> str:
        child = self.start(wid, task or "(no task)", "auto", depth=run.depth + 1, parent=run.id)
        await child.task_ref
        run.cost_usd += child.cost_usd
        run.tokens += child.tokens
        if run.stop.is_set():                                                    # the child ended because this run was stopped (a stopped run swallows the cancellation)
            raise asyncio.CancelledError()
        if child.status != "done":
            raise WorkflowError(f"workflow '{wid}' {child.status}: {child.error or 'did not finish'}")
        return child.final

    async def _for_each(self, run: Run, doc: dict, c: dict, scope: dict, outputs: dict[str, str], prev: str) -> str:
        items = _items(X.render_value(c["items"], scope))
        sem = asyncio.Semaphore(c["concurrency"])

        async def one(i: int, it: Any) -> Any:
            async with sem:
                sc = {**scope, "$item": it, "$index": i}
                r = await self._sub(run, c["workflow"], X.render(c["task"], sc))
                j = _json_or_none(r)
                return j if j is not None else r
        return json.dumps(await asyncio.gather(*[one(i, it) for i, it in enumerate(items)]), ensure_ascii=False, default=str)

    async def _llm(self, run: Run, prompt: str) -> str:
        from .api import _ask
        return await _ask(self.s, prompt)

    async def _container(self, run: Run, n: dict, vars_: dict) -> str:
        steps, kind = n["config"]["steps"], n["type"]
        sc = self._legacy_scope(vars_)
        if kind == "parallel":
            return "\n\n".join(await asyncio.gather(*[self._step(run, s, sc) for s in steps]))
        rounds = n["config"]["max_iterations"] if kind == "loop" else 1
        until, out = (n["config"].get("until") or "").lower(), sc["$prev"]
        for _ in range(rounds):
            for s in steps:
                out = await self._step(run, s, {**sc, "$prev": out, "prev": out})
            if kind == "loop" and until and until in out.lower():
                break
        return out

    async def _step(self, run: Run, step: dict, vars_: dict) -> str:
        if run.stop.is_set():
            raise asyncio.CancelledError()
        k = step["kind"]
        sc = self._legacy_scope(vars_)
        if k == "agent":
            from ..apiaccess.api import pick_provider
            agent = self.s.agents.get(step["agent"])
            if agent is None:
                raise WorkflowError(f"agent '{step['agent']}' does not exist")
            res = await self.s.chat.send(pick_provider(self.s, agent), None, X.render(step["prompt"], sc), None, None, agent.id, None)
            usage = res.get("usage") or {}
            run.cost_usd += float(usage.get("cost_usd") or 0)
            run.tokens += int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
            return res.get("reply") or ""
        if k == "tool":
            return json.dumps(await self._tool(step, sc), default=str)[:MAX_TEXT]
        return json.dumps(await self.sources.call(step["source"], step["operation"], {a: (X.render(v, sc) if isinstance(v, str) else v) for a, v in step["args"].items()},
                                                  step["confirmed"]), default=str)[:MAX_TEXT]

    async def _tool(self, step: dict, vars_: dict) -> dict:
        import inspect
        from ..harness.tools import ToolContext, build_tools
        if step["tool"] not in SAFE_TOOLS and not step["confirmed"]:
            return {"ok": False, "error": f"'{step['tool']}' changes things and is not confirmed"}
        ws = self.s.chat.workspace_for(None)
        ctx = ToolContext(ws, self.s.chat.sandbox_for(ws), self.s.skills, self.s.mcp, rag=self.s.rag, web=self.s.chat.web, remotes=self.s.chat.remotes)
        fns = {f.__name__: f for f in build_tools(ctx, [step["tool"]])}
        fn = fns.get(step["tool"])
        if fn is None:
            return {"ok": False, "error": f"the {step['tool']} tool is not available here"}
        sc = self._legacy_scope(vars_)
        args = {k: (X.render(v, sc) if isinstance(v, str) else v) for k, v in step["args"].items()}
        try:
            r = fn(**{k: v for k, v in args.items() if k in inspect.signature(fn).parameters})
            return await r if inspect.isawaitable(r) else r
        except TypeError as e:
            return {"ok": False, "error": f"bad arguments: {e}"}


def workflow_tool(runner: Runner, ids: list[str]):
    """`run_workflow` for agents: run a saved workflow in auto mode and return its result."""
    async def run_workflow(workflow: str, task: str) -> dict:
        try:
            run = runner.start(workflow, task, "auto", origin="agent")
        except (KeyError, WorkflowError) as e:
            return {"ok": False, "error": f"{e}. Workflows: {ids}"}
        await run.task_ref
        return {"ok": run.status == "done", "status": run.status, "result": run.final, "error": run.error}
    run_workflow.__doc__ = ("Run a saved workflow (a drawn graph of agents, tools and API calls) and return its result.\n\nArgs:\n"
                            f"    workflow: which workflow, one of {ids}.\n    task: the complete task to give it.")
    return run_workflow
