"""REST API for the security scorecard (/api/security/*) and the red-team runners (/api/redteam/*). Pydantic-free on purpose."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import agentcard, scorecard
from .redteam import RedTeamError


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def register(app: FastAPI) -> None:
    sec = APIRouter(prefix="/api/security", tags=["security"])
    rt = APIRouter(prefix="/api/redteam", tags=["redteam"])

    def state(request: Request):
        return request.app.state.s

    @sec.get("/scorecard")
    def card(request: Request, format: str = "json"):
        c = scorecard.build(state(request))
        if format == "md":
            return PlainTextResponse(scorecard.to_markdown(c), media_type="text/markdown",
                                     headers={"Content-Disposition": 'attachment; filename="agent-studio-security-scorecard.md"'})
        if format != "json":
            raise HTTPException(422, "format must be json or md")
        return c

    def send(text: str, media: str, name: str):
        return PlainTextResponse(text, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @sec.get("/agents")
    def agents_card(request: Request, format: str = "json"):
        """Every agent with its own score, worst first. format=md is one report for all agents; format=full is every agent's full card as JSON."""
        s = state(request)
        if format not in ("json", "md", "full"):
            raise HTTPException(422, "format must be json, md or full")
        x = agentcard.Ctx(s)
        rows = agentcard.ranking(s, x)
        if format == "json":
            return dict(generated=time.time(), summary=agentcard.summary(s, rows), agents=rows)
        cards = [agentcard.card(s, a, x) for a in x.agents]
        if format == "full":
            return send(agentcard.to_json(dict(generated=time.time(), summary=agentcard.summary(s, rows), agents=cards)), "application/json", "agent-studio-agent-scorecards.json")
        order = {r["id"]: i for i, r in enumerate(rows)}
        return send(agentcard.report_markdown(rows, sorted(cards, key=lambda c: order[c["agent"]["id"]]), scorecard.build(s)), "text/markdown", "agent-studio-agent-scorecards.md")

    @sec.get("/agents/{aid}")
    def agent_card(aid: str, request: Request, format: str = "json", download: bool = False):
        s = state(request)
        a = s.agents.get(aid)
        if a is None:
            raise HTTPException(404, "unknown agent")
        if format not in ("json", "md"):
            raise HTTPException(422, "format must be json or md")
        c = agentcard.card(s, a)
        if format == "md":
            return send(agentcard.card_markdown(c), "text/markdown", f"agent-studio-security-{aid}.md")
        if download:                                                    # the same card as a saved file
            return send(agentcard.to_json(c), "application/json", f"agent-studio-security-{aid}.json")
        return c

    def wrap(fn, *a):
        try:
            return fn(*a)
        except RedTeamError as e:
            raise HTTPException(422, str(e))
        except KeyError:
            raise HTTPException(404, "unknown run")

    @rt.get("/status")
    def status(request: Request):
        return state(request).redteam.status()

    @rt.get("/plan/{tool}")
    def plan(tool: str, request: Request):
        return wrap(state(request).redteam.plan, tool)

    @rt.post("/install")
    def install(body: dict, request: Request):
        return wrap(state(request).redteam.start_install, body.get("tool") or "", body.get("accepted") is True)

    @rt.post("/uninstall")
    def uninstall(body: dict, request: Request):
        return wrap(state(request).redteam.uninstall, body.get("tool") or "")

    @rt.get("/runs")
    def runs(request: Request):
        return {"runs": state(request).redteam.runs()}

    @rt.post("/runs")
    def start(body: dict, request: Request):
        s = state(request)
        return wrap(s.redteam.start, body.get("tool") or "", body.get("agent") or "", body.get("provider") or "", body.get("model"), body.get("options"),
                    body.get("max_requests"), str(body.get("purpose") or ""), str(request.base_url), lambda a: s.agents.get(a) is not None)

    @rt.get("/runs/{rid}")
    def run(rid: str, request: Request, tail: int = 40):
        return wrap(state(request).redteam.run, rid, tail)

    @rt.post("/runs/{rid}/cancel")
    def cancel(rid: str, request: Request):
        return wrap(state(request).redteam.cancel, rid)

    @rt.post("/target/{token}/v1/chat/completions")
    async def target(token: str, body: dict, request: Request):
        """What the red-team tools talk to: an OpenAI-style chat endpoint that hands each probe to the chosen agent."""
        s = state(request)
        try:
            t = s.redteam.target_for(token)
        except RedTeamError as e:
            return JSONResponse({"error": {"message": str(e), "type": "invalid_request_error"}}, status_code=404 if "not active" in str(e) else 429)
        msgs = [m for m in body.get("messages") or [] if isinstance(m, dict) and m.get("role") == "user"]
        message = _text(msgs[-1].get("content")) if msgs else ""
        if not message.strip():
            s.redteam.note_result(t, error=True)
            return JSONResponse({"error": {"message": "send at least one user message", "type": "invalid_request_error"}}, status_code=400)
        try:
            res = await s.chat.send(t["provider"], t["model"], message, None, None, t["agent"], None)
        except Exception as e:  # noqa: BLE001 - a probe that fails must not stop the tool: it is counted and reported
            s.redteam.note_result(t, error=True)
            return JSONResponse({"error": {"message": f"{type(e).__name__}: {str(e)[:200]}", "type": "server_error"}}, status_code=502)
        s.chat.store.delete(res["session_id"])                           # probes are not kept in your chat history
        s.redteam.note_result(t, blocked=any(g.get("action") == "blocked" for g in res.get("guardrails") or []))
        u = res.get("usage") or {}
        return {"id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion", "created": int(time.time()), "model": t["agent"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": res.get("reply") or ""}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0),
                          "total_tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0)}}

    app.include_router(sec)
    app.include_router(rt)
