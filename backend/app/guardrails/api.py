"""REST API for guardrails: /api/guardrails/*. Pydantic-free on purpose (main.py uses postponed annotations)."""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request

from .service import GuardrailError


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/guardrails", tags=["guardrails"])

    def svc(request: Request):
        return request.app.state.s.guardrails

    @r.get("")
    def get_all(request: Request):
        return svc(request).public()

    @r.put("/enabled")
    def put_enabled(body: dict, request: Request):
        if not isinstance(body.get("enabled"), bool):
            raise HTTPException(422, "enabled must be true or false")
        svc(request).set_enabled(body["enabled"])
        return svc(request).public()

    @r.put("/profiles")
    def put_profile(body: dict, request: Request):
        try:
            return svc(request).save(body)
        except GuardrailError as e:
            raise HTTPException(422, str(e))

    @r.delete("/profiles/{pid}")
    def delete_profile(pid: str, request: Request):
        try:
            return svc(request).delete(pid)
        except KeyError:
            raise HTTPException(404, "unknown profile (the built-in one cannot be deleted)")

    @r.post("/test")
    async def test(body: dict, request: Request):
        try:
            return await svc(request).test(body.get("profile") or {}, str(body.get("text") or ""), body.get("stage") or "input")
        except GuardrailError as e:
            raise HTTPException(422, str(e))

    @r.get("/events")
    def events(request: Request, limit: int = 100, stage: str | None = None, action: str | None = None):
        return {"events": svc(request).events(limit, stage, action)}

    @r.get("/stats")
    def stats(request: Request, days: int = 7):
        return svc(request).stats(max(1, min(days, 365)))

    app.include_router(r)
