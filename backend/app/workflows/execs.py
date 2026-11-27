"""What each of the newer boxes does when it runs. The engine calls `execute` with the box, its settings filled in from the expression scope, and gets back the box's text and the
output ports it used. Pure data boxes call transform.py; the integrations (HTTP, MCP, memory, knowledge, data, artifacts, guardrails) call the same services the agents use, so
spend caps, read-only guards, redaction and audit trails apply here too.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..mcp_registry import build_toolsets
from . import expr as X
from . import transform as T
from .core import MAX_TEXT, WorkflowError
from .nodes import READ_METHODS

WRITES = ("http_request", "mcp_tool", "memory", "artifact")          # boxes that can change something outside the run (a "test step" does not run them unless you say so)


def out_text(v: Any) -> str:
    """How a value travels between boxes: text as it is, numbers and booleans as text, everything else as JSON."""
    if isinstance(v, str):
        return v
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return T.to_json(v)


def has_side_effects(n: dict) -> bool:
    """True when running this box could change something: used by 'test this step' to stay hands-off unless the person says otherwise."""
    t, c = n["type"], n["config"]
    if t == "http_request":
        return c["method"] not in READ_METHODS
    if t == "memory":
        return c["operation"] == "save"
    if t in ("mcp_tool", "artifact", "sub_workflow", "for_each", "agent", "sequential", "parallel", "loop"):
        return True
    if t == "tool":
        from .core import SAFE_TOOLS
        return c["tool"] not in SAFE_TOOLS
    if t == "openapi":
        return True
    return False


def _render_obj(d: dict, scope: dict) -> dict:
    return {k: (X.render(v, scope) if isinstance(v, str) else v) for k, v in d.items()}


def _items(c: dict, scope: dict, key: str = "items") -> list:
    return T.items_of(X.render_value(c[key], scope))


async def execute(r, run, doc: dict, n: dict, st: dict, scope: dict, prev: str) -> tuple[str, set[str]]:
    t, c = n["type"], n["config"]
    out = {"out"}
    if t == "compare":
        rules = [dict(left=X.render_value(x["left"], scope), op=x["op"], right=X.render_value(x["right"], scope)) for x in c["conditions"]]
        ok = T.conditions_hold(rules, c["combine"])
        st["decision"] = "true" if ok else "false"
        return prev, {st["decision"]}
    if t == "filter":
        rules = [dict(field=x["field"], op=x["op"], value=X.render_value(x["value"], scope)) for x in c["rules"]]
        kept = T.filter_items(_items(c, scope), rules, c["combine"])
        return T.to_json(kept), out
    if t == "sort":
        return T.to_json(T.sort_items(_items(c, scope), c["field"], c["order"] == "desc", c["numeric"])), out
    if t == "limit":
        return T.to_json(T.limit_items(_items(c, scope), c["count"], c["from"] == "end")), out
    if t == "dedupe":
        return T.to_json(T.dedupe_items(_items(c, scope), c["field"])), out
    if t == "aggregate":
        return out_text(T.aggregate(_items(c, scope), c["op"], c["field"], c["group_by"], c["separator"])), out
    if t == "split_out":
        return T.to_json(T.split_out(X.render_value(c["from"], scope), c["mode"], c["separator"], c["field"])), out
    if t == "join":
        return T.join_items(_items(c, scope), c["field"], c["separator"]), out
    if t == "merge_by_key":
        return T.to_json(T.merge_by_key(_items(c, scope, "left"), _items(c, scope, "right"), c["key"], c["right_key"], c["mode"])), out
    if t == "date_time":
        value = X.render_value(c["value"], scope) if c["operation"] != "now" else ""
        other = X.render_value(c["other"], scope) if c["operation"] == "diff_days" else ""
        return T.date_time(c["operation"], value, c["amount"], c["unit"], c["format"], other), out
    if t == "regex":
        return out_text(T.regex_op(c["mode"], X.render_value(c["text"], scope), c["pattern"], c["replacement"], c["ignore_case"])), out
    if t == "respond_webhook":
        body = X.render(c["body"], scope)
        run.response = dict(status=c["status"], content_type=c["content_type"], body=body[:MAX_TEXT])
        return prev, out
    if t == "http_request":
        return await _http(r, c, scope), out
    if t == "mcp_tool":
        return await _mcp(r, c, _render_obj(c["args"], scope)), out
    if t == "memory":
        return await _memory(r, run, c, scope), out
    if t == "knowledge":
        return await _knowledge(r, c, scope), out
    if t == "data_query":
        return await _data(r, run, c, scope), out
    if t == "artifact":
        return _artifact(r, run, doc, c, scope), out
    if t == "guardrail":
        return await _guardrail(r, run, st, c, scope)
    if t == "notify":
        return _notify(r, run, doc, c, scope), out
    raise WorkflowError(f"the engine does not run '{t}' boxes")      # pragma: no cover - every catalog type is handled above


# -------------------------------------------------------------------------------------------------- integrations
def _notify(r, run, doc: dict, c: dict, scope: dict) -> str:
    """Put a message in the notification center. Passes on what it showed."""
    title, message = X.render(c["title"], scope).strip()[:200] or doc["name"], X.render(c["message"], scope)[:4000]
    r.s.notifications.add(title, message, level=c["level"], source="workflow", link=f"/workflows/?tab=runs&run={run.id}", desktop=c["desktop"])
    return message


async def _http(r, c: dict, scope: dict) -> str:
    if c["method"] not in READ_METHODS and not c["confirmed"]:
        raise WorkflowError(f"{c['method']} changes data: tick 'I confirm' on the box to allow it")
    cfg = r.s.wf_config
    conf = cfg.config()
    cred = cfg.header_for(c["credential"]) if c["credential"] else None
    res = await r.http.request(c["method"], X.render(c["url"], scope), _render_obj(c["query"], scope), _render_obj(c["headers"], scope), X.render(c["body"], scope), cred,
                               float(conf["http_timeout_s"]), conf["http_max_kb"] * 1024)
    if not res["ok"] and c["fail_on_error"]:
        raise WorkflowError(f"{c['method']} answered {res['status']}: {res['text'][:200]}")
    if c["fail_on_error"]:
        return res["text"][:MAX_TEXT]
    return T.to_json(dict(status=res["status"], ok=res["ok"], headers=res["headers"], body=res["text"][:MAX_TEXT]))


def mcp_result_text(res: Any) -> str:
    if getattr(res, "isError", False):
        raise WorkflowError("the tool reported an error: " + _mcp_text(res)[:300])
    structured = getattr(res, "structuredContent", None)
    return T.to_json(structured) if structured else _mcp_text(res)


def _mcp_text(res: Any) -> str:
    parts = [getattr(x, "text", None) for x in (getattr(res, "content", None) or [])]
    return "\n".join(p for p in parts if p)


async def _mcp(r, c: dict, args: dict) -> str:
    s = r.s
    warns: list[str] = []
    try:
        sets = await asyncio.wait_for(build_toolsets(s.mcp, [c["server"]], str(s.chat.workspace_for(None)), warns), 150)
    except asyncio.TimeoutError:
        raise WorkflowError(f"the MCP server '{c['server']}' took too long to start")
    if not sets:
        raise WorkflowError("; ".join(warns) or f"the MCP server '{c['server']}' did not start")
    ts = sets[0]
    try:
        tools = await ts.get_tools()
        tool = next((x for x in tools if x.raw_mcp_tool.name == c["tool"]), None)
        if tool is None:
            raise WorkflowError(f"'{c['server']}' has no tool called '{c['tool']}' (it has: {', '.join(x.raw_mcp_tool.name for x in tools)[:200] or 'none'})")
        read_only = bool(getattr(getattr(tool.raw_mcp_tool, "annotations", None), "readOnlyHint", False))
        if not read_only and not c["confirmed"]:
            raise WorkflowError(f"'{c['tool']}' is not marked read-only by its server: tick 'I confirm' on the box to allow it")
        session = await ts._mcp_session_manager.create_session()
        return mcp_result_text(await session.call_tool(c["tool"], arguments=args))[:MAX_TEXT]
    finally:
        try:
            await ts.close()
        except Exception:  # noqa: BLE001 - closing a server that is already gone is fine
            pass


async def _memory(r, run, c: dict, scope: dict) -> str:
    from ..memory.model import scope_id
    svc = r.s.memory
    cfg = svc.config()
    if not cfg["enabled"]:
        raise WorkflowError("memory is switched off (Memory page > Settings)")
    text = X.render(c["text"], scope)
    ws = str(r.s.chat.workspace_for(None))
    me = f"workflow:{run.workflow}"
    sid = scope_id(c["scope"], me, ws)
    if c["operation"] == "search":
        found = await svc.search(text, scopes=[(c["scope"], sid)], types=[c["kind"]], k=c["k"])
        return T.to_json([dict(id=m["id"], kind=m["type"], text=m["text"], score=m["score"]) for m in found])
    rec = await svc.add(type=c["kind"], text=text, scope=c["scope"], scope_id=sid, agent=me, session=run.id, run=run.id, origin="workflow", key=c["key"], status="pending" if cfg["review"] else "active")
    return T.to_json(dict(id=rec["id"], status=rec["status"], duplicate=bool(rec.get("duplicate")), saved=rec["text"][:200]))


async def _knowledge(r, c: dict, scope: dict) -> str:
    found = await r.s.rag.search(X.render(c["query"], scope), [c["kb"]], c["k"])
    return T.to_json(found)


async def _data(r, run, c: dict, scope: dict) -> str:
    from ..datasources.base import DataError
    try:
        res = await asyncio.to_thread(r.s.data.query, c["source"], X.render(c["sql"], scope), c["max_rows"], f"workflow:{run.workflow}")
    except DataError as e:
        raise WorkflowError(str(e))
    return T.to_json(res)[:MAX_TEXT]


def _artifact(r, run, doc: dict, c: dict, scope: dict) -> str:
    from ..artifacts.service import ArtifactError
    tags = [x.strip() for x in c["tags"].split(",") if x.strip()][:10]
    try:
        rec = r.s.artifacts.save_text(X.render(c["name"], scope), X.render(c["content"], scope), agent=f"workflow:{doc['id']}", session_id=run.id, workspace=str(r.s.chat.workspace_for(None)),
                                      source="workflow", tags=tags)
    except ArtifactError as e:
        raise WorkflowError(str(e))
    return T.to_json(dict(id=rec["id"], name=rec["name"], size=rec.get("size")))


async def _guardrail(r, run, st: dict, c: dict, scope: dict) -> tuple[str, set[str]]:
    g = r.s.guardrails
    if not g.enabled():
        raise WorkflowError("guardrails are switched off (Settings > Security): a check that cannot run must not pass")
    text = X.render(c["text"], scope)
    d = await g.evaluate(c["stage"], text, f"workflow:{run.workflow}", [c["profile"]] if c["profile"] else None)
    st["flagged"] = len(d.events)
    st["decision"] = "blocked" if d.blocked else "passed"
    return (d.message if d.blocked else text), {st["decision"]}
