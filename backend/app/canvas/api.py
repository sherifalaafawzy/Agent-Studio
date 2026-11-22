"""REST API for the canvas: /api/canvas/*. Bodies are plain dicts on purpose (main.py uses postponed annotations)."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from .service import CanvasConflict, CanvasError


async def event_stream(svc, cid: str, request, keepalive: float = 15.0):
    """Server-sent events for one canvas: a line each time it changes (a new version, a proposal), and a comment now and then to keep the connection open."""
    q = svc.subscribe(cid)
    try:
        yield ": connected\n\n"
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), keepalive)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    return
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(ev)}\n\n"
    finally:
        svc.unsubscribe(cid, q)


def events_response(svc, cid: str, request) -> StreamingResponse:
    return StreamingResponse(event_stream(svc, cid, request), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/canvas", tags=["canvas"])

    def svc(request: Request):
        return request.app.state.s.canvas

    def guard(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except CanvasConflict as e:
            raise HTTPException(409, {"message": str(e), "current_version": e.current, "conflicts": e.conflicts})
        except CanvasError as e:
            raise HTTPException(404 if str(e).startswith("no such") else 422, str(e))

    @r.get("/config")
    def get_config(request: Request):
        return svc(request).config()

    @r.put("/config")
    def put_config(body: dict, request: Request):
        return guard(svc(request).set_config, body)

    @r.get("")
    def list_canvases(request: Request, session_id: str = "", limit: int = 50):
        return svc(request).list(session_id, limit)

    @r.post("")
    def create(body: dict, request: Request):
        return guard(svc(request).create, str(body.get("title") or ""), str(body.get("type") or "markdown"), str(body.get("content") or ""), str(body.get("language") or ""),
                     session_id=str(body.get("session_id") or ""), agent=str(body.get("agent") or ""))

    @r.get("/{cid}")
    def get_one(cid: str, request: Request):
        return guard(svc(request).get, cid)

    @r.put("/{cid}")
    def update(cid: str, body: dict, request: Request):
        bv = body.get("base_version")
        if bv is not None and (isinstance(bv, bool) or not isinstance(bv, int)):
            raise HTTPException(422, "base_version must be a whole number")
        for k in ("content", "title", "language"):
            if body.get(k) is not None and not isinstance(body[k], str):
                raise HTTPException(422, f"{k} must be text")
        return guard(svc(request).update, cid, content=body.get("content"), title=body.get("title"), language=body.get("language"), base_version=bv)

    @r.delete("/{cid}")
    def delete(cid: str, request: Request):
        return guard(svc(request).delete, cid)

    @r.get("/{cid}/versions")
    def versions(cid: str, request: Request):
        return guard(svc(request).versions, cid)

    @r.get("/{cid}/versions/{n}")
    def version(cid: str, n: int, request: Request):
        return guard(svc(request).version_content, cid, n)

    @r.post("/{cid}/restore")
    def restore(cid: str, body: dict, request: Request):
        n = body.get("version")
        if isinstance(n, bool) or not isinstance(n, int):
            raise HTTPException(422, "send the version number to bring back")
        return guard(svc(request).restore, cid, n)

    @r.post("/{cid}/selection")
    def selection(cid: str, body: dict, request: Request):
        return guard(svc(request).set_selection, cid, body.get("start"), body.get("end"))

    @r.get("/{cid}/proposals")
    def proposals(cid: str, request: Request, status: str = "pending"):
        return guard(svc(request).proposals, cid, status)

    @r.post("/{cid}/proposals/{pid}/accept")
    def accept(cid: str, pid: str, body: dict, request: Request):
        return guard(svc(request).accept, cid, pid, bool(body.get("skip_conflicts")))

    @r.post("/{cid}/proposals/{pid}/reject")
    def reject(cid: str, pid: str, request: Request):
        return guard(svc(request).reject, cid, pid)

    @r.post("/{cid}/accept-all")
    def accept_all(cid: str, body: dict, request: Request):
        return guard(svc(request).accept_all, cid, bool(body.get("skip_conflicts")))

    @r.post("/{cid}/reject-all")
    def reject_all(cid: str, request: Request):
        return guard(svc(request).reject_all, cid)

    @r.post("/{cid}/snapshot")
    def snapshot(cid: str, request: Request):
        return guard(svc(request).snapshot, cid)

    @r.get("/{cid}/events")
    async def events(cid: str, request: Request):
        guard(svc(request).get, cid)
        return events_response(svc(request), cid, request)

    app.include_router(r)
