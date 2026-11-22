"""REST API for the harness rules: /api/harness/* (policy, web search key, checkpoints). Pydantic-free on purpose."""
from __future__ import annotations

import json

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .policy import DEFAULTS, MODES, Checkpoints, PolicyError, get_policy, set_policy


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/harness", tags=["harness"])

    def S(request: Request):
        return request.app.state.s

    def view(s) -> dict:
        try:
            has_key = bool(json.loads(s.db.get_setting("web_secrets", "{}") or "{}").get("brave_api_key"))
        except (ValueError, AttributeError):
            has_key = False
        return dict(policy=get_policy(s.db), defaults=DEFAULTS, modes=list(MODES), web_search_key_set=has_key)

    @r.get("/policy")
    def policy(request: Request):
        return view(S(request))

    @r.put("/policy")
    def put_policy(body: dict, request: Request):
        s = S(request)
        try:
            set_policy(s.db, body)
        except PolicyError as e:
            return JSONResponse(status_code=422, content={"detail": str(e)})
        return view(s)

    @r.put("/web-key")
    def web_key(body: dict, request: Request):
        """Write-only: the Brave Search key. An empty string clears it."""
        s = S(request)
        key = body.get("brave_api_key")
        if not isinstance(key, str) or len(key) > 200:
            return JSONResponse(status_code=422, content={"detail": "send brave_api_key as text (empty to clear it)"})
        s.db.set_setting("web_secrets", json.dumps({"brave_api_key": key.strip()} if key.strip() else {}))
        return view(s)

    @r.get("/checkpoints/{session}")
    def checkpoints(session: str):
        return {"checkpoints": Checkpoints().list(session)}

    @r.post("/checkpoints/{session}/rewind")
    def rewind(session: str, body: dict):
        to = body.get("to")
        if isinstance(to, bool) or not isinstance(to, int):
            return JSONResponse(status_code=422, content={"detail": "`to` is the checkpoint id to rewind to"})
        try:
            return Checkpoints().rewind(session, to)
        except KeyError:
            return JSONResponse(status_code=404, content={"detail": "no such checkpoint"})

    app.include_router(r)
