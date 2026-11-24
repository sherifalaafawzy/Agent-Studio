"""Routes of the "Deploy to Claude Code" dialog: preview a deployment, then write it after confirmation. See export.py and apply.py."""
from __future__ import annotations

import shutil

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import mcp_endpoint
from .apply import PlanChanged, apply_plan
from .export import build_plan, parse_opts


def _detail(status: int, text: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": text})


def register(app: FastAPI) -> None:
    mcp_endpoint.register(app)

    @app.get("/api/claude-code/status")
    def status():
        exe = shutil.which("claude")
        return dict(claude_found=bool(exe), live_path="/agent-mcp/{agent}")

    @app.post("/api/agents/{aid}/claude-code/plan")
    def plan(aid: str, body: dict, request: Request):
        try:
            return build_plan(request.app.state.s, aid, parse_opts(body))
        except KeyError:
            return _detail(404, "unknown agent")
        except ValueError as e:
            return _detail(422, str(e))

    @app.post("/api/agents/{aid}/claude-code/apply")
    def apply(aid: str, body: dict, request: Request):
        s = request.app.state.s
        try:
            out = apply_plan(s, aid, parse_opts(body), body)
        except KeyError:
            return _detail(404, "unknown agent")
        except PlanChanged as e:
            return _detail(409, str(e))
        except ValueError as e:
            return _detail(422, str(e))
        s.tel.emit("entity", activity_id=3, kind="agent", name=aid, detail={"fields": f"deployed to Claude Code ({body.get('mode', 'export')}, {body.get('scope', 'project')})"})
        return out
