"""REST API for sandboxes: /api/sandboxes/*. Pydantic-free on purpose (main.py uses postponed annotations)."""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/sandboxes", tags=["sandboxes"])

    def fac(request: Request):
        return request.app.state.s.chat.sandboxes

    @r.get("")
    def status(request: Request):
        return fac(request).status()

    @r.put("/config")
    def put_config(body: dict, request: Request):
        try:
            fac(request).set_policy(body)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return fac(request).status()

    @r.post("/{kind}/test")
    def test(kind: str, request: Request):
        return fac(request).test(kind)

    app.include_router(r)
