"""REST API for Auto mode: /api/auto (settings + availability), /api/auto/route (a dry run, no LLM call), /api/auto/warm.

Pydantic models live at module level (main.py uses postponed annotations; models built inside a function make FastAPI treat the
body as a query parameter).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .auto import AutoError, AutoRouter, public_config
from . import d1_spec


class AutoPatch(BaseModel):
    enabled: bool | None = None
    engine: str | None = None                      # rules | d1-2b | d1-3b
    preference: str | None = None                  # cost | balanced | quality
    allow: list[str] | None = None                 # "provider" or "provider/model"; empty = everything
    deny: list[str] | None = None
    fallback: bool | None = None                   # on a provider error, retry once on the next-best model
    timeout_s: float | None = None
    tier_models: dict[str, str] | None = None      # {"flash": "provider/model", "pro": "provider/model"}; "" clears


class RouteTry(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    agent: bool = False                            # pretend an agent (tools) is selected
    engine: str | None = None                      # try a different engine / preference without saving it
    preference: str | None = None


def _auto(request: Request) -> AutoRouter:
    return request.app.state.s.auto


def _view(r: AutoRouter) -> dict:
    return {**public_config(r.config()), **r.availability(), "worker": r.d1_states()["active"],
            "engine_ready": {m: r.d1_ready(m) for m in d1_spec.MODELS}}


def register(app: FastAPI) -> None:
    router = APIRouter(prefix="/api/auto", tags=["auto"])

    @router.get("")
    def get_auto(r: AutoRouter = Depends(_auto)):
        return _view(r)

    @router.put("")
    def put_auto(body: AutoPatch, r: AutoRouter = Depends(_auto)):
        try:
            r.set_config(body.model_dump(exclude_none=True))
        except AutoError as e:
            raise HTTPException(422, str(e))
        return _view(r)

    @router.post("/route")
    async def try_route(body: RouteTry, r: AutoRouter = Depends(_auto)):
        """What would Auto pick for this message? Nothing is sent to any provider (a d1 engine runs locally, for free)."""
        override = {k: v for k, v in (("engine", body.engine), ("preference", body.preference)) if v}
        try:
            return (await r.route(body.message, [], body.agent, False, None, None, override)).public()
        except AutoError as e:
            raise HTTPException(422, str(e))

    @router.post("/warm")
    def warm(r: AutoRouter = Depends(_auto)):
        """Start loading the chosen decision model now (it keeps loading in the background)."""
        cfg = r.config()
        if cfg["engine"] not in d1_spec.MODELS:
            raise HTTPException(422, "the rules engine has nothing to load")
        if not r.d1_ready(cfg["engine"]):
            raise HTTPException(409, f"{d1_spec.spec(cfg['engine']).label} is not installed yet")
        r._worker_for(cfg["engine"]).warm()
        return r.d1_states()["active"]

    app.include_router(router)
