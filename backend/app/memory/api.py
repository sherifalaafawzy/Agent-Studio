"""REST API for memory: /api/memories/*. (The older /api/memory/* endpoints manage the legacy knowledge-graph file.)

Pydantic models are module-level (main.py uses postponed annotations).
"""
from __future__ import annotations

import re

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ..budget import BudgetExceeded
from ..errors import classify
from .model import SCOPES, STORED_TYPES, scope_id
from .service import MemoryError_
from .store import MemoryNotFound


class MemIn(BaseModel):
    type: str
    text: str = Field(max_length=4000)
    scope: str = "user"
    agent: str = ""                       # for scope "agent": which agent owns it
    workspace: str = ""                   # for scope "workspace": the folder (empty = the default workspace)
    key: str = Field(default="", max_length=80)
    confidence: float = Field(default=0.9, ge=0, le=1)
    ttl_days: int | None = Field(default=None, ge=0, le=3650)


class MemPatch(BaseModel):
    text: str | None = Field(default=None, max_length=4000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    ttl_days: int | None = Field(default=None, ge=0, le=3650)


class ApproveIn(BaseModel):
    text: str | None = Field(default=None, max_length=4000)


class ForgetIn(BaseModel):
    memory_id: str = ""
    query: str = Field(default="", max_length=500)
    dry_run: bool = True


class WipeIn(BaseModel):
    confirm: str = ""
    scope: str | None = None


class NotesIn(BaseModel):
    text: str = Field(max_length=200_000)
    workspace: str = ""


def _svc(request: Request):
    return request.app.state.s.memory


async def _call(coro):
    try:
        return await coro
    except MemoryNotFound as e:
        raise HTTPException(404, str(e))
    except MemoryError_ as e:
        raise HTTPException(422, str(e))
    except BudgetExceeded:
        raise
    except Exception as e:  # noqa: BLE001 - provider and network failures are explained, not shown as traces
        info = classify(e, None)
        raise HTTPException(502, f"{info['title']}. {info.get('hint') or ''}".strip())


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/memories", tags=["memory"])

    @r.get("/config")
    def get_config(request: Request):
        return _svc(request).public_config()

    @r.put("/config")
    def put_config(body: dict, request: Request):
        try:
            return _svc(request).set_config(body)
        except MemoryError_ as e:
            raise HTTPException(422, str(e))

    @r.get("/kinds")
    def kinds(request: Request):
        return _svc(request).kinds()

    @r.get("/health")
    async def health(request: Request):
        return await _svc(request).health()

    @r.get("/stats")
    def stats(request: Request):
        return _svc(request).stats()

    @r.get("")
    def list_(request: Request, type: str | None = None, scope: str | None = None, status: str = "active,pending", q: str = "", limit: int = 200, offset: int = 0):
        if type is not None and type not in STORED_TYPES:
            raise HTTPException(422, f"type must be one of: {', '.join(STORED_TYPES)}")
        if scope is not None and scope not in SCOPES:
            raise HTTPException(422, f"scope must be one of: {', '.join(SCOPES)}")
        return _svc(request).list(type=type, scope=scope, status=[x for x in status.split(",") if x], q=q, limit=max(1, min(limit, 1000)), offset=max(0, offset))

    @r.post("")
    async def create(body: MemIn, request: Request):
        s = request.app.state.s
        if body.scope == "agent" and not body.agent:
            raise HTTPException(422, "say which agent owns an agent-scoped memory")
        if body.scope not in SCOPES:
            raise HTTPException(422, f"scope must be one of: {', '.join(SCOPES)}")
        ws = str(s.chat.workspace_for(body.workspace or None))
        return await _call(_svc(request).add(type=body.type, text=body.text, scope=body.scope, scope_id=scope_id(body.scope, body.agent, ws), agent=body.agent,
                                             origin="user", key=body.key, confidence=body.confidence, ttl_days=body.ttl_days))

    @r.get("/export")
    def export(request: Request):
        return _svc(request).export()

    @r.post("/import")
    async def import_(body: dict, request: Request):
        return await _call(_svc(request).import_(body))

    @r.post("/maintain")
    async def maintain(request: Request):
        return await _call(_svc(request).maintain())

    @r.post("/forget")
    async def forget(body: ForgetIn, request: Request):
        return await _call(_svc(request).forget(memory_id=body.memory_id, query=body.query, scopes=None if body.memory_id else _all_scopes(request), dry_run=body.dry_run))

    @r.post("/wipe")
    async def wipe(body: WipeIn, request: Request):
        if body.confirm != "DELETE ALL MEMORY":
            raise HTTPException(422, 'send {"confirm": "DELETE ALL MEMORY"} to erase everything')
        if body.scope is not None and body.scope not in SCOPES:
            raise HTTPException(422, f"scope must be one of: {', '.join(SCOPES)}")
        return {"deleted": await _call(_svc(request).wipe(body.scope))}

    @r.get("/notes")
    def get_notes(request: Request, workspace: str = ""):
        s = request.app.state.s
        ws = s.chat.workspace_for(workspace or None)
        return {"file": s.memory.config()["notes_file"], "path": (ws / s.memory.config()["notes_file"]).as_posix(), "text": s.memory.read_notes(ws)}

    @r.put("/notes")
    def put_notes(body: NotesIn, request: Request):
        s = request.app.state.s
        try:
            n = s.memory.write_notes(s.chat.workspace_for(body.workspace or None), body.text)
        except MemoryError_ as e:
            raise HTTPException(422, str(e))
        return {"chars": n}

    @r.get("/{mid}")
    def get_one(mid: str, request: Request):
        try:
            return _svc(request).get(mid)
        except MemoryNotFound as e:
            raise HTTPException(404, str(e))

    @r.put("/{mid}")
    async def update(mid: str, body: MemPatch, request: Request):
        return await _call(_svc(request).update(mid, text=body.text, confidence=body.confidence, ttl_days=body.ttl_days))

    @r.post("/{mid}/approve")
    async def approve(mid: str, body: ApproveIn, request: Request):
        return await _call(_svc(request).approve(mid, body.text))

    @r.post("/{mid}/reject")
    async def reject(mid: str, request: Request):
        return await _call(_svc(request).reject(mid))

    @r.post("/{mid}/promote")
    def promote(mid: str, request: Request):
        """Turn a how-to memory into a Skill agents can load."""
        s = request.app.state.s
        try:
            rec = s.memory.get(mid)
        except MemoryNotFound as e:
            raise HTTPException(404, str(e))
        if rec["type"] != "procedural" or rec["status"] != "active":
            raise HTTPException(422, "only an approved how-to memory can become a skill")
        name = re.sub(r"[^a-z0-9]+", "-", rec["text"].lower())[:40].strip("-") or "how-to"
        try:
            sk = s.skills.create(name, rec["text"][:160], rec["text"], [], False, "Learned")
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"skill": sk.public()}

    @r.delete("/{mid}")
    async def delete(mid: str, request: Request):
        s = _svc(request)
        warnings: list[str] = []
        ok = await _call(s.delete(mid, warnings))
        if not ok:
            raise HTTPException(404, f"no memory with id '{mid}'")
        return {"ok": True, "warnings": warnings}

    app.include_router(r)


def _all_scopes(request: Request) -> list[tuple[str, str]]:
    """Every (scope, scope_id) that holds memories: the manager sees everything, unlike an agent."""
    rows = request.app.state.s.db.query("SELECT DISTINCT scope, scope_id FROM memories WHERE status='active'")
    return [(x["scope"], x["scope_id"]) for x in rows]
