"""REST API for the OCSF security export: /api/ocsf/*. Pydantic-free on purpose (main.py uses postponed annotations)."""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request

from .service import OcsfError


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/ocsf", tags=["ocsf"])

    def svc(request: Request):
        return request.app.state.s.ocsf

    @r.get("/config")
    def get_config(request: Request):
        return svc(request).public_config()

    @r.put("/config")
    def put_config(body: dict, request: Request):
        try:
            return svc(request).set_config(body)
        except OcsfError as e:
            raise HTTPException(422, str(e))

    @r.get("/preview")
    def preview(request: Request, level: str | None = None, limit: int = 10):
        try:
            return svc(request).preview(level, limit)
        except OcsfError as e:
            raise HTTPException(422, str(e))

    @r.post("/sinks/{sid}/test")
    def test(sid: str, request: Request):
        try:
            return svc(request).test_sink(sid)
        except OcsfError as e:
            raise HTTPException(404, str(e))

    @r.get("/stats")
    def stats(request: Request):
        return svc(request).stats()

    app.include_router(r)
