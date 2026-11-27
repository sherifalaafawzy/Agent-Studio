"""REST API for workflows: /api/workflows/* (graphs, versions, validation, runs, the drafting wizard) and imported APIs
(/api/workflows/apis). Pydantic-free on purpose (main.py uses postponed annotations)."""
from __future__ import annotations

import asyncio
import json
import time

import litellm
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ..budget import BudgetExceeded
from . import builder as B
from . import nodes as NODES
from . import runs as RUNS
from .core import DRAFT_PROMPT, AUTONOMY, WorkflowError, clean, parse_draft, template_draft
from .hooks import RateLimiter
from .sources import InUse

BUNDLE = "agent-studio-workflows"
MAX_BUNDLE = 50


def _hook_task(entry: dict, raw: bytes, query: dict, headers: dict) -> str:
    """What a call to the Webhook address becomes: the task of the run."""
    text = raw.decode("utf-8", errors="replace")
    mode = entry["config"]["payload"]
    parsed = None
    if text.strip()[:1] in ("{", "["):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
    if mode == "json" and parsed is not None:
        text = json.dumps(parsed, ensure_ascii=False, indent=2)
    elif mode == "all":
        keep = {k: v for k, v in headers.items() if k.lower() in ("content-type", "user-agent", "x-request-id")}
        text = json.dumps(dict(body=parsed if parsed is not None else text, query=query, headers=keep), ensure_ascii=False, indent=2)
    return (text.strip() or "(empty)")[:20_000]


async def _wait_for(run, until_response: bool, seconds: float) -> None:
    """Wait for a run to finish (or, for 'Respond to webhook', for that box to speak), for at most `seconds`."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not run.task_ref.done() and not (until_response and run.response):
        await asyncio.sleep(0.05)


def _bad(msg: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": msg})


async def _ask(s, prompt: str) -> str:
    """One guarded model call for the wizard (the first enabled provider's default model); raises on any failure."""
    from ..apiaccess.api import pick_provider
    from ..providers import litellm_target
    p = s.providers.get(pick_provider(s, type("A", (), {"provider": None})()))
    model = p.default_model
    lite, kwargs = litellm_target(p, model)
    max_out = min(p.max_output_tokens, 1800)
    r = s.guard.reserve(p.id, model, len(prompt) // 3 + 50, max_out, lite)
    try:
        resp = await litellm.acompletion(model=lite, messages=[{"role": "user", "content": prompt}], max_tokens=max_out,
                                         **({"mock_response": "[mock] I cannot draft this."} if p.kind == "mock" else {}), **kwargs)
    except BaseException:
        s.guard.release(r, note="workflow_draft_failed")
        raise
    u = getattr(resp, "usage", None)
    s.guard.settle(r, getattr(u, "prompt_tokens", None) if u else None, getattr(u, "completion_tokens", None) if u else None, note="workflow_draft")
    return (resp.choices[0].message.content or "").strip()


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/workflows", tags=["workflows"])

    def S(request: Request):
        return request.app.state.s

    def view(s, doc: dict) -> dict:
        return {**doc, "problems": s.wf_runner.check(clean(doc))}

    # ---- settings, credentials and the lists boxes pick from -- declared first: these words must not be read as a workflow id
    @r.get("/settings")
    def get_settings(request: Request):
        return {"settings": S(request).wf_config.config(), "credentials": S(request).wf_config.credentials()}

    @r.put("/settings")
    def put_settings(body: dict, request: Request):
        s = S(request)
        try:
            return {"settings": s.wf_config.set_config(body)}
        except WorkflowError as e:
            return _bad(str(e))

    @r.post("/credentials")
    def put_credential(body: dict, request: Request):
        try:
            return S(request).wf_config.set_credential(body)
        except WorkflowError as e:
            return _bad(str(e))

    @r.delete("/credentials/{cid}")
    def del_credential(cid: str, request: Request):
        s = S(request)
        try:
            s.wf_config.delete_credential(cid, s.workflows.credential_users(cid))
        except KeyError:
            return _bad("not found", 404)
        except WorkflowError as e:
            return JSONResponse(status_code=409, content={"detail": str(e), "used_by": s.workflows.credential_users(cid)})
        return {"ok": True}

    @r.get("/refs")
    def refs(request: Request):
        """What boxes can point at, with names (the editor's pickers)."""
        from .. import mcp_catalog
        s = S(request)
        return dict(mcp=[dict(id=m.id, label=m.label, ready=m.ready(), read_only=(mcp_catalog.get(m.id) or {}).get("read_only", "none")) for m in s.mcp.list()],
                    knowledge=[dict(id=k["id"], label=k["label"]) for k in s.rag.list_kbs()], data_source=[dict(id=x["id"], label=x["label"], kind=x["kind"]) for x in s.data.public()],
                    guardrail=[dict(id=p.id, label=p.name) for p in s.guardrails.profiles()], credential=s.wf_config.credentials())

    @r.post("/export")
    def export_many(body: dict, request: Request):
        """Several workflows in one file (all of them when no ids are given)."""
        s = S(request)
        ids = [str(i) for i in (body.get("ids") or []) if isinstance(i, str)] or s.workflows.ids()
        docs = [d for d in (s.workflows.get(i) for i in ids[:200]) if d is not None]
        if not docs:
            return _bad("none of those workflows exist", 404)
        bundle = dict(format=BUNDLE, version=1, workflows=[{k: v for k, v in d.items() if k not in ("version", "updated")} for d in docs])
        return JSONResponse(content=bundle, headers={"Content-Disposition": 'attachment; filename="workflows.bundle.json"'})

    # ---- imported APIs (components) -- declared first: "/apis" must not be read as a workflow id
    def jail(s, src: str):
        """A workspace file must stay inside the workspace; an address is used as it is. Returns (src, error response or None)."""
        if not src or src.startswith(("http://", "https://")):
            return src, None
        from ..harness import files as F
        try:
            return str(F.jail(s.chat.workspace_for(None), src)), None
        except F.ToolError as e:
            return src, _bad(str(e))

    def source_args(s, body: dict):
        src, err = jail(s, str(body.get("src") or ""))
        return (src, err, dict(content=str(body.get("content") or ""), filename=str(body.get("filename") or "")[:200]))

    def guarded(fn):
        try:
            return fn()
        except InUse as e:
            return JSONResponse(status_code=409, content={"detail": str(e), "used_by": e.used_by})
        except WorkflowError as e:
            return _bad(str(e))
        except KeyError:
            return _bad("not found", 404)

    @r.get("/apis")
    def apis(request: Request):
        return {"apis": S(request).wf_sources.list()}

    @r.post("/apis/preview")
    def preview_api(body: dict, request: Request):
        s = S(request)
        src, err, extra = source_args(s, body)
        return err or guarded(lambda: s.wf_sources.preview(src, extra["content"], extra["filename"], str(body.get("base") or "")))

    @r.post("/apis")
    def add_api(body: dict, request: Request):
        s = S(request)
        src, err, extra = source_args(s, body)
        return err or guarded(lambda: s.wf_sources.add(body.get("id") or "", body.get("name") or "", src, body.get("base") or "", body.get("token") or "",
                                                         bool(body.get("allow_write")), auth=body.get("auth") if isinstance(body.get("auth"), dict) else None, **extra))

    @r.get("/apis/{sid}")
    def get_api(sid: str, request: Request):
        return guarded(lambda: S(request).wf_sources.get(sid))

    @r.put("/apis/{sid}")
    def put_api(sid: str, body: dict, request: Request):
        return guarded(lambda: S(request).wf_sources.update(sid, body))

    @r.delete("/apis/{sid}")
    def del_api(sid: str, request: Request, force: bool = False):
        def go():
            S(request).wf_sources.delete(sid, force)
            return {"ok": True}
        return guarded(go)

    @r.post("/apis/{sid}/refresh")
    def refresh_api(sid: str, body: dict, request: Request):
        s = S(request)
        src, err, extra = source_args(s, body)
        return err or guarded(lambda: s.wf_sources.refresh(sid, src, extra["content"], extra["filename"], dry_run=body.get("dry_run", True) is not False))

    @r.post("/apis/{sid}/duplicate")
    def duplicate_api(sid: str, body: dict, request: Request):
        return guarded(lambda: S(request).wf_sources.duplicate(sid, str(body.get("id") or ""), str(body.get("name") or "")))

    @r.get("/apis/{sid}/spec")
    def spec_api(sid: str, request: Request):
        def go():
            return Response(S(request).wf_sources.export_spec(sid), media_type="text/plain", headers={"Content-Disposition": f'attachment; filename="{sid}.openapi.txt"'})
        return guarded(go)

    @r.get("/apis/{sid}/operations")
    def list_ops(sid: str, request: Request, q: str = "", tag: str = "", method: str = "", enabled: bool | None = None, hidden: bool = False):
        return guarded(lambda: {"operations": S(request).wf_sources.operations(sid, q, tag, method, enabled, hidden)})

    @r.post("/apis/{sid}/operations")
    def create_op(sid: str, body: dict, request: Request):
        return guarded(lambda: S(request).wf_sources.create_operation(sid, body))

    @r.get("/apis/{sid}/operations/{op}")
    def get_op(sid: str, op: str, request: Request):
        return guarded(lambda: S(request).wf_sources.operation(sid, op))

    @r.put("/apis/{sid}/operations/{op}")
    def put_op(sid: str, op: str, body: dict, request: Request):
        return guarded(lambda: S(request).wf_sources.update_operation(sid, op, body))

    @r.delete("/apis/{sid}/operations/{op}")
    def del_op(sid: str, op: str, request: Request, force: bool = False):
        return guarded(lambda: S(request).wf_sources.delete_operation(sid, op, force))

    @r.post("/apis/{sid}/operations/{op}/test")
    async def test_op(sid: str, op: str, body: dict, request: Request):
        try:
            args = body.get("args") if isinstance(body.get("args"), dict) else {}
            return await S(request).wf_sources.test(sid, op, args, bool(body.get("confirmed")))
        except KeyError:
            return _bad("not found", 404)

    # ---- runs
    @r.get("/nodes")
    def node_catalog():
        return NODES.catalog()

    @r.get("/templates")
    def templates():
        return {"templates": [dict(id=t["id"], name=t["name"], description=t["description"], nodes=len(t["doc"]["nodes"]), doc=t["doc"]) for t in B.TEMPLATES]}

    @r.get("/runs")
    def all_runs(request: Request, workflow: str = "", status: str = "", origin: str = "", since: float | None = None, until: float | None = None, q: str = "", needs_you: bool = False,
                 sort: str = "started", order: str = "desc", limit: int = 25, offset: int = 0):
        """Every run of every workflow (the Runs tab): filters, sort, paging, and the counts for the status chips."""
        try:
            return RUNS.list_runs(S(request).wf_runner, workflow=workflow, status=status, origin=origin, since=since, until=until, q=q, needs_you=needs_you, sort=sort, order=order, limit=limit, offset=offset)
        except WorkflowError as e:
            return _bad(str(e))

    @r.post("/runs/delete")
    def delete_many_runs(body: dict, request: Request):
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(i, str) for i in ids):
            return _bad("give the ids of 1 to 500 runs")
        return RUNS.delete_runs(S(request).wf_runner, ids)

    @r.get("/runs/{rid}")
    def get_run(rid: str, request: Request):
        return RUNS.detail(S(request).wf_runner, rid) or _bad("unknown run", 404)

    @r.delete("/runs/{rid}")
    def delete_run(rid: str, request: Request):
        out = RUNS.delete_runs(S(request).wf_runner, [rid])
        if out["skipped"]:
            reason = out["skipped"][0]["reason"]
            return _bad(reason, 404 if reason == "no such run" else 409)
        return {"ok": True}

    @r.post("/runs/{rid}/rerun")
    async def rerun_run(rid: str, body: dict, request: Request):
        """Run the same task again (on the workflow as it is now), or from one box ("failed" = the first box that failed) reusing this run's earlier outputs."""
        node = body.get("from_node")
        try:
            return RUNS.rerun(S(request).wf_runner, rid, str(node) if node else None).public()
        except KeyError:
            return _bad("unknown run", 404)
        except WorkflowError as e:
            return _bad(str(e))

    @r.post("/runs/{rid}/decide")
    def decide(rid: str, body: dict, request: Request):
        try:
            S(request).wf_runner.decide(rid, str(body.get("node") or ""), str(body.get("decision") or ""), body.get("values") if isinstance(body.get("values"), dict) else None)
        except KeyError:
            return _bad("unknown run", 404)
        except WorkflowError as e:
            return _bad(str(e))
        return {"ok": True}

    @r.post("/runs/{rid}/stop")
    def stop(rid: str, request: Request):
        try:
            return S(request).wf_runner.stop(rid).public()
        except KeyError:
            return _bad("unknown run", 404)

    # ---- the drafting wizard
    @r.post("/draft")
    async def draft(body: dict, request: Request):
        s = S(request)
        goal = str(body.get("goal") or "").strip()
        autonomy = body.get("autonomy", "approve")
        if not goal or len(goal) > 2000:
            return _bad("describe the goal in a sentence or two")
        if autonomy not in AUTONOMY:
            return _bad(f"autonomy must be one of {', '.join(AUTONOMY)}")
        known = {a.id: a for a in s.agents.list()}
        agents = [a for a in (body.get("agents") or []) if a in known]
        ops = []
        srcs = {x["id"]: x for x in s.wf_sources.list()}
        for o in body.get("operations") or []:
            src, op = str(o.get("source")), str(o.get("operation"))
            if src in srcs and any(x["id"] == op for x in srcs[src]["operations"]):
                ops.append(dict(source=src, operation=op))
        if not agents and not ops:
            return _bad("choose at least one agent or API operation to build with")
        prompt = DRAFT_PROMPT.format(goal=goal, agents="; ".join(f"{a}: {known[a].description or known[a].name}" for a in agents) or "(none)",
                                     ops="; ".join(f"{o['source']}.{o['operation']}" for o in ops) or "(none)")
        by, note = "model", None
        try:
            doc = parse_draft(await _ask(s, prompt), goal, autonomy)
        except (WorkflowError, BudgetExceeded, Exception) as e:  # noqa: BLE001 - the wizard always gives you something to start from
            doc, by = clean(template_draft(goal, agents, ops, autonomy)), "template"
            note = f"A model could not draft this ({str(e)[:160]}), so this is a simple chain of your chosen steps to edit."
        return dict(workflow=doc, drafted_by=by, note=note, problems=s.wf_runner.check(doc))

    # ---- the builder agent: proposes edits as a diff; nothing is saved or run
    @r.post("/builder/chat")
    async def builder_chat(body: dict, request: Request):
        s = S(request)
        msgs = [dict(role="assistant" if m.get("role") == "assistant" else "user", content=str(m.get("content") or "")[:3000]) for m in (body.get("messages") or []) if isinstance(m, dict)][-12:]
        if not msgs or msgs[-1]["role"] != "user" or not msgs[-1]["content"].strip():
            return _bad("write what you want the workflow to do")
        autonomy = body.get("autonomy", "approve")
        if autonomy not in AUTONOMY:
            return _bad(f"autonomy must be one of {', '.join(AUTONOMY)}")
        raw = body.get("doc") if isinstance(body.get("doc"), dict) else {}
        try:
            cur = clean({"id": "draft", "name": "New workflow", **raw, "nodes": raw.get("nodes") or [dict(id="start", type="input", label="Task", config={})]})
        except WorkflowError as e:
            return _bad(f"the current workflow has a problem the builder cannot work around: {e}")
        known = {a.id: a for a in s.agents.list()}
        sysm = body.get("systems") if isinstance(body.get("systems"), dict) else {}
        agents = [a for a in (sysm.get("agents") or list(known)) if a in known][:30]
        srcs = {x["id"]: x for x in s.wf_sources.list()}
        ops_ok = [f"{sid}.{o['id']}" for sid, x in srcs.items() for o in x["operations"] if o["enabled"] and (not sysm.get("operations") or f"{sid}.{o['id']}" in sysm["operations"])][:60]
        from ..harness.tools import ALL_TOOLS
        prompt = B.BUILDER_PROMPT.format(catalog=B.catalog_text(), agents="; ".join(f"{a}: {known[a].description or known[a].name}" for a in agents) or "(none)", ops="; ".join(ops_ok) or "(none)",
                                         tools=", ".join(sorted(ALL_TOOLS)[:40]), autonomy={"suggest": "plan only, never run", "approve": "ask before each step", "auto": "run by itself"}[autonomy],
                                         systems=", ".join(agents + ops_ok) or "(none)", doc=json.dumps({k: cur[k] for k in ("name", "nodes", "edges")}, ensure_ascii=False)[:9000],
                                         chat="\n".join(f"{m['role']}: {m['content']}" for m in msgs))
        by, note = "model", None
        try:
            reply, ops = B.parse_reply(await _ask(s, prompt))
        except (WorkflowError, BudgetExceeded, Exception) as e:  # noqa: BLE001 - the builder always answers, even without a model
            by, note = "template", f"No model could help ({str(e)[:140]})."
            reply = "I could not reach a model, so I laid out a simple chain from the systems you allowed. Adjust it on the canvas."
            ops = B.template_ops(msgs[0]["content"], agents[:3], [dict(source=o.split(".")[0], operation=o.split(".", 1)[1]) for o in ops_ok[:2]]) if len(cur["nodes"]) == 1 else []
        new, applied, refused = B.apply_ops(cur, ops)
        return dict(reply=reply, drafted_by=by, note=note, applied=applied, refused=refused, diff=B.diff(cur, new), workflow=new, problems=s.wf_runner.check(new))

    # ---- workflows
    @r.get("")
    def list_all(request: Request):
        return {"workflows": S(request).workflows.list()}

    @r.post("")
    def create(body: dict, request: Request):
        s = S(request)
        try:
            return view(s, s.workflows.save(body, create=True))
        except WorkflowError as e:
            return _bad(str(e))

    def import_one(s, doc: dict, mode: str):
        """Returns (outcome, workflow or message): outcome is 'created', 'replaced' or 'clash'."""
        d = clean({k: v for k, v in doc.items() if k not in ("version", "updated", "problems", "on_conflict")})
        taken = s.workflows.get(d["id"]) is not None
        if taken and mode == "cancel":
            return "clash", d
        if taken and mode == "replace":
            return "replaced", s.workflows.save(d, label="Imported")
        base, n = d["id"], 2
        while s.workflows.get(d["id"]):
            d["id"], n = f"{base}_{n}", n + 1
        return "created", s.workflows.save(d, create=True, label="Imported")

    @r.post("/import")
    def import_(body: dict, request: Request):
        """Import a workflow, or a file of several. If an id is taken: on_conflict = rename (default, keeps both), replace (a new version of the existing one) or cancel."""
        s = S(request)
        wrapped = isinstance(body.get("workflow"), dict) or isinstance(body.get("workflows"), list)
        mode = body.get("on_conflict", "rename") if wrapped else "rename"
        if mode not in ("rename", "replace", "cancel"):
            return _bad("on_conflict is rename, replace or cancel")
        if isinstance(body.get("workflows"), list) or body.get("format") == BUNDLE:
            docs = body.get("workflows") if isinstance(body.get("workflows"), list) else []
            if not docs or len(docs) > MAX_BUNDLE:
                return _bad(f"a file holds 1 to {MAX_BUNDLE} workflows")
            done, skipped, errors = [], [], []
            for d in docs:
                try:
                    kind, res = import_one(s, d, mode)
                except (WorkflowError, AttributeError, TypeError) as e:
                    errors.append(str(e)[:200])
                    continue
                (skipped if kind == "clash" else done).append(res["id"])
            return dict(imported=done, skipped=skipped, errors=errors)
        doc = body["workflow"] if isinstance(body.get("workflow"), dict) else body
        try:
            kind, res = import_one(s, doc, mode)
        except WorkflowError as e:
            return _bad(str(e))
        if kind == "clash":
            return JSONResponse(status_code=409, content={"detail": f"a workflow '{res['id']}' already exists", "id": res["id"]})
        return view(s, res)

    @r.post("/validate")
    def validate(body: dict, request: Request):
        s = S(request)
        try:
            doc = clean(body)
        except WorkflowError as e:
            return {"ok": False, "problems": [dict(node="", severity="error", message=str(e))]}
        pr = s.wf_runner.check(doc)
        return {"ok": not any(p["severity"] == "error" for p in pr), "problems": pr}

    @r.get("/{wid}")
    def get(wid: str, request: Request):
        s = S(request)
        doc = s.workflows.get(wid)
        return view(s, doc) if doc else _bad("unknown workflow", 404)

    @r.put("/{wid}")
    def save(wid: str, body: dict, request: Request):
        s = S(request)
        if s.workflows.get(wid) is None:
            return _bad("unknown workflow", 404)
        try:
            return view(s, s.workflows.save({k: v for k, v in body.items() if k not in ("label", "note")} | {"id": wid}, label=str(body.get("label") or ""), note=str(body.get("note") or "")))
        except WorkflowError as e:
            return _bad(str(e))

    def users_of(s, wid: str) -> list[str]:
        out = s.workflows.users(wid)
        if s.agents.get(f"wf_{wid}"[:31]) is not None:
            out.append(f"agent wf_{wid}"[:36])
        return out

    @r.delete("/{wid}")
    def delete(wid: str, request: Request, force: bool = False):
        s = S(request)
        if s.workflows.get(wid) is None:
            return _bad("unknown workflow", 404)
        used = users_of(s, wid)
        if used and not force:
            return JSONResponse(status_code=409, content={"detail": f"'{wid}' is used by {', '.join(used)}: remove it there first, or delete anyway", "used_by": used})
        s.workflows.delete(wid)
        s.wf_runner.forget(wid)                                                  # its finished runs go with it (the saved ones were deleted with the workflow)
        return {"ok": True}

    @r.post("/{wid}/duplicate")
    def duplicate(wid: str, body: dict, request: Request):
        s = S(request)
        try:
            return view(s, s.workflows.duplicate(wid, str(body.get("id") or ""), str(body.get("name") or "")))
        except KeyError:
            return _bad("unknown workflow", 404)
        except WorkflowError as e:
            return _bad(str(e))

    @r.get("/{wid}/used-by")
    def used_by(wid: str, request: Request):
        return {"used_by": users_of(S(request), wid)}

    @r.get("/{wid}/draft")
    def get_draft(wid: str, request: Request):
        # "no draft" is the usual answer, not an error: a 404 here showed as a red line in the browser console every time an editor opened
        return S(request).workflows.get_draft(wid) or {"doc": None}

    @r.put("/{wid}/draft")
    def put_draft(wid: str, body: dict, request: Request):
        try:
            return S(request).workflows.put_draft(wid, body)
        except KeyError:
            return _bad("unknown workflow", 404)
        except WorkflowError as e:
            return _bad(str(e))

    @r.delete("/{wid}/draft")
    def del_draft(wid: str, request: Request):
        S(request).workflows.delete_draft(wid)
        return {"ok": True}

    @r.get("/{wid}/runs")
    def runs(wid: str, request: Request, limit: int = 20):
        return {"runs": S(request).wf_runner.saved_runs(wid, max(1, min(limit, 100)))}

    @r.get("/{wid}/versions")
    def versions(wid: str, request: Request):
        return {"versions": S(request).workflows.versions(wid)}

    @r.get("/{wid}/versions/{n}")
    def version_doc(wid: str, n: int, request: Request):
        try:
            return S(request).workflows.version(wid, n)
        except KeyError:
            return _bad("that version does not exist", 404)

    @r.post("/{wid}/restore")
    def restore(wid: str, body: dict, request: Request):
        s = S(request)
        try:
            return view(s, s.workflows.restore(wid, int(body.get("version"))))
        except (KeyError, TypeError, ValueError):
            return _bad("that version does not exist", 404)

    @r.get("/{wid}/export")
    def export(wid: str, request: Request):
        doc = S(request).workflows.get(wid)
        if doc is None:
            return _bad("unknown workflow", 404)
        return JSONResponse(content={k: v for k, v in doc.items() if k not in ("version", "updated")},
                            headers={"Content-Disposition": f'attachment; filename="{wid}.workflow.json"'})

    @r.post("/{wid}/nodes/{nid}/test")
    async def test_box(wid: str, nid: str, body: dict, request: Request):
        """Try one box with the text you give it. Boxes that could change something are not run unless allow_side_effects is true."""
        s = S(request)
        try:
            if isinstance(body.get("doc"), dict):
                doc = clean({**body["doc"], "id": wid})
            else:
                doc = s.workflows.get(wid)
                if doc is None:
                    return _bad("unknown workflow", 404)
            return await s.wf_runner.test_node(doc, nid, str(body.get("input") or "")[:20_000], str(body.get("task") or "")[:20_000], body.get("allow_side_effects") is True)
        except KeyError:
            return _bad("that box does not exist", 404)
        except WorkflowError as e:
            return _bad(str(e))

    @r.get("/{wid}/hook")
    def hook_status(wid: str, request: Request):
        s = S(request)
        if s.workflows.get(wid) is None:
            return _bad("unknown workflow", 404)
        return {**s.wf_hooks.status(wid), "path": f"/api/hooks/{wid}/<secret>", "webhooks_enabled": s.wf_config.config()["webhooks_enabled"]}

    @r.post("/{wid}/hook")
    def hook_create(wid: str, request: Request):
        """Make (or replace) the secret address. The secret is shown this once."""
        s = S(request)
        doc = s.workflows.get(wid)
        if doc is None:
            return _bad("unknown workflow", 404)
        if not any(n["type"] == "webhook" for n in doc["nodes"]):
            return _bad("this workflow does not start with a Webhook box: add one first")
        token = s.wf_hooks.create(wid)
        return {"path": f"/api/hooks/{wid}/{token}", "secret": token, "webhooks_enabled": s.wf_config.config()["webhooks_enabled"]}

    @r.delete("/{wid}/hook")
    def hook_delete(wid: str, request: Request):
        S(request).wf_hooks.delete(wid)
        return {"ok": True}

    @r.get("/{wid}/schedule")
    def schedule_status(wid: str, request: Request):
        s = S(request)
        if s.workflows.get(wid) is None:
            return _bad("unknown workflow", 404)
        return s.wf_schedule.status(wid)

    @r.post("/{wid}/schedule")
    def schedule_set(wid: str, body: dict, request: Request):
        """Switch the schedule on (from the saved Schedule box) or off."""
        s = S(request)
        try:
            if body.get("enabled") is True:
                return s.wf_schedule.enable(wid)
            if body.get("enabled") is False:
                return s.wf_schedule.pause(wid)
        except KeyError:
            return _bad("unknown workflow", 404)
        except ValueError as e:
            return _bad(str(e))
        return _bad("send enabled: true or false")

    @r.post("/{wid}/run")
    async def run(wid: str, body: dict, request: Request):
        try:
            return S(request).wf_runner.start(wid, str(body.get("task") or ""), body.get("autonomy"), from_node=str(body["from_node"]) if body.get("from_node") else None,
                                              base=str(body.get("base") or "") or None).public()
        except KeyError:
            return _bad("unknown workflow", 404)
        except WorkflowError as e:
            return _bad(str(e))

    @r.post("/{wid}/to-agent")
    def to_agent(wid: str, request: Request):
        """Turn a workflow into an agent that runs it (so it can be chatted with, called over the API or A2A, or used by other agents)."""
        from ..agents import AgentDef
        s = S(request)
        doc = s.workflows.get(wid)
        if doc is None:
            return _bad("unknown workflow", 404)
        aid = f"wf_{wid}"[:31]
        instruction = (f"You run the workflow '{doc['name']}' for the user. Call `run_workflow` with workflow='{wid}' and the user's request as the task, "
                       "then give them its result, adding nothing the workflow did not produce. If it fails, say why.")
        try:
            a = s.agents.create(AgentDef(id=aid, name=doc["name"][:60], description=(doc.get("description") or f"Runs the '{doc['name']}' workflow.")[:200],
                                         instruction=instruction, tools=["run_workflow"], tier="flash"))
        except ValueError as e:
            return _bad(str(e))
        return {"agent": a.id}

    app.include_router(r)
    limiter = RateLimiter()

    @app.post("/api/hooks/{wid}/{token}", tags=["workflows"])
    async def hook(wid: str, token: str, request: Request):
        """Start a workflow from outside. Any problem with the address looks the same (404), so nobody can learn which workflows exist."""
        s = request.app.state.s
        cfg = s.wf_config.config()
        doc = s.workflows.get(wid) if cfg["webhooks_enabled"] else None
        if doc is None or not s.wf_hooks.verify(wid, token):
            return _bad("not found", 404)
        if not limiter.allow(wid, cfg["hook_rate_per_min"]):
            return JSONResponse(status_code=429, content={"detail": "too many calls: slow down"}, headers={"Retry-After": "60"})
        entry = next((n for n in doc["nodes"] if n["type"] in NODES.ENTRY), None)
        if entry is None or entry["type"] != "webhook":
            return _bad("this workflow does not start with a Webhook box", 409)
        raw = await request.body()
        if len(raw) > cfg["hook_max_body_kb"] * 1024:
            return _bad("that is too large for this workflow's webhook", 413)
        try:
            run = s.wf_runner.start(wid, _hook_task(entry, raw, dict(request.query_params), dict(request.headers)), None, origin="webhook")
        except WorkflowError as e:
            return _bad(str(e))
        s.wf_hooks.touch(wid)
        mode = entry["config"]["respond"]
        if mode != "immediately":
            await _wait_for(run, mode == "node", cfg["hook_wait_max_s"])
        if mode == "node" and run.response:
            resp = run.response
            return Response(content=resp["body"], status_code=resp["status"], media_type=resp["content_type"])
        if mode != "immediately" and run.task_ref.done():
            if run.status == "done":
                return {"run": run.id, "status": "done", "result": run.final}
            return JSONResponse(status_code=500, content={"run": run.id, "status": run.status, "error": run.error})
        return JSONResponse(status_code=202, content={"run": run.id, "status": "accepted" if mode == "immediately" else run.status})
