"""/api/uikit: the component settings, the catalog of types and a validator (used by the gallery and the tests)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .kinds import GROUPS, KINDS
from .samples import SAMPLES
from .service import UiKitError
from .validate import DEFAULT_LIMITS, validate


def register(app) -> None:
    r = APIRouter(prefix="/api/uikit", tags=["uikit"])

    def svc(request: Request):
        return request.app.state.s.uikit

    @r.get("/config")
    def get_config(request: Request):
        return svc(request).config()

    @r.put("/config")
    def put_config(body: dict, request: Request):
        try:
            return svc(request).set_config(body)
        except UiKitError as e:
            raise HTTPException(400, str(e))

    @r.get("/catalog")
    def catalog():
        return {"groups": GROUPS, "kinds": [{"type": k.type, "group": k.group, "about": k.about, "shape": k.shape} for k in KINDS.values()]}

    @r.get("/catalog/{type}")
    def one(type: str):
        k = KINDS.get(type)
        if k is None:
            raise HTTPException(404, "no such component type")
        return {"type": k.type, "group": k.group, "about": k.about, "shape": k.shape, "example": SAMPLES.get(k.type)}

    @r.post("/validate")
    def check(body: dict, request: Request):
        clean, errors, warnings = validate(body, {**DEFAULT_LIMITS, **svc(request).limits()})
        return {"ok": clean is not None, "spec": clean, "errors": errors, "warnings": warnings}

    app.include_router(r)
