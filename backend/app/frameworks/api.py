"""REST API for agent frameworks: /api/frameworks/* (matrix, install, compatibility) and the per-run private gateway used by
framework workers: /internal/fw/{token}/... (see gateway.py). Pydantic-free on purpose (main.py uses postponed annotations)."""
from __future__ import annotations

import json

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .base import FrameworkError
from .catalog import FRAMEWORKS
from .gateway import GatewayError


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/frameworks", tags=["frameworks"])
    internal = APIRouter(prefix="/internal/fw", tags=["frameworks-internal"], include_in_schema=False)

    def chat(request: Request):
        return request.app.state.s.chat

    @r.get("")
    def matrix(request: Request):
        return chat(request).frameworks.matrix()

    @r.get("/{fid}/plan")
    def plan(fid: str, request: Request):
        try:
            return chat(request).frameworks.envs.plan(fid)
        except FrameworkError as e:
            raise HTTPException(422, str(e))

    @r.post("/{fid}/install")
    def install(fid: str, body: dict, request: Request):
        try:
            return chat(request).frameworks.envs.start_install(fid, bool(body.get("accepted")))
        except FrameworkError as e:
            raise HTTPException(422, str(e))

    @r.delete("/{fid}")
    def uninstall(fid: str, request: Request):
        try:
            return chat(request).frameworks.envs.uninstall(fid)
        except FrameworkError as e:
            raise HTTPException(422, str(e))

    @r.post("/compat")
    def compat(body: dict, request: Request):
        """What an agent (a draft from the editor) would lose on a framework: plain-language warnings."""
        from ..agents import AgentDef
        fid = body.get("framework") or "adk"
        if fid not in FRAMEWORKS:
            raise HTTPException(422, f"unknown framework '{fid}'")
        keep = ("tools", "mcp", "skills", "sub_agents", "acp_agents", "rag")
        a = AgentDef(id="draft", name="draft", can_spawn=bool(body.get("can_spawn")), **{k: list(body.get(k) or []) for k in keep})
        fw = chat(request).frameworks.get(fid)
        return dict(framework=fid, ready=fw.ready(), warnings=fw.compat_warnings(a))

    # ---- the private address of ONE run (a worker holds only this) ----
    def err(e: GatewayError) -> JSONResponse:
        return JSONResponse(status_code=e.status, content=e.body())

    @internal.post("/{token}/v1/chat/completions")
    async def completions(token: str, body: dict, request: Request):
        rt = chat(request).fw
        try:
            if body.get("stream"):
                s = rt.get(token)                                           # fail before the stream starts
                first = rt.stream(s.token, body)

                async def sse():
                    try:
                        async for c in first:
                            yield f"data: {json.dumps(c)}\n\n"
                    except GatewayError as e:
                        yield f"data: {json.dumps(e.body())}\n\n"
                    yield "data: [DONE]\n\n"
                return StreamingResponse(sse(), media_type="text/event-stream")
            return await rt.complete(token, body)
        except GatewayError as e:
            return err(e)
        except Exception as e:  # noqa: BLE001 - the framework shows this to the model/user
            return JSONResponse(status_code=502, content={"error": {"message": f"{type(e).__name__}: {str(e)[:400]}", "type": "upstream_error"}})

    @internal.post("/{token}/tool")
    async def tool(token: str, body: dict, request: Request):
        try:
            return await chat(request).fw.call_tool(token, str(body.get("name") or ""), body.get("args") or {})
        except GatewayError as e:
            return err(e)

    app.include_router(r)
    app.include_router(internal)
