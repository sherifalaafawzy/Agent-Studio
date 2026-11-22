"""REST API for runtimes: /api/runtimes/*. Pydantic-free on purpose (main.py uses postponed annotations)."""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request

from .service import RuntimeFault


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/runtimes", tags=["runtimes"])

    def svc(request: Request):
        return request.app.state.s.runtimes

    def guard(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except RuntimeFault as e:
            raise HTTPException(404 if "no such" in str(e) else 422, str(e))

    @r.get("")
    def overview(request: Request):
        return svc(request).public()

    @r.put("/config")
    def put_config(body: dict, request: Request):
        return guard(svc(request).set_config, body)

    @r.post("/targets/{tid}/test")
    def test(tid: str, request: Request):
        return guard(svc(request).test, tid)

    @r.get("/agents/{aid}")
    def agent_state(aid: str, request: Request):
        s = svc(request)
        return {"releases": guard(s.releases, aid), "migration": guard(s.migration, aid)}

    @r.post("/agents/{aid}/plan")
    def plan(aid: str, body: dict, request: Request):
        return guard(svc(request).plan, aid, str(body.get("target") or ""), body.get("image") or None)

    @r.post("/agents/{aid}/deploy")
    def deploy(aid: str, body: dict, request: Request):
        return guard(svc(request).deploy, aid, str(body.get("target") or ""), body.get("image") or None, body.get("confirm") is True)

    @r.post("/agents/{aid}/rollback")
    def rollback(aid: str, body: dict, request: Request):
        return guard(svc(request).rollback, aid, body.get("confirm") is True)

    @r.post("/releases/{rid}/refresh")
    def refresh(rid: str, request: Request):
        return guard(svc(request).refresh, rid)

    @r.post("/releases/{rid}/health")
    def health(rid: str, body: dict, request: Request):
        return guard(svc(request).health, rid, body.get("smoke") is True)

    @r.post("/releases/{rid}/promote")
    def promote(rid: str, body: dict, request: Request):
        return guard(svc(request).promote, rid, body.get("confirm") is True)

    @r.delete("/releases/{rid}")
    def delete(rid: str, request: Request, confirm: bool = False):
        return guard(svc(request).delete_release, rid, confirm)

    app.include_router(r)
