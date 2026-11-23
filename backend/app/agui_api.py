"""POST /api/agui: run a chat turn and stream it as AG-UI events (see agui.py for the mapping).

Request: a RunAgentInput. `forwardedProps` may name { provider, model, agent, workspace, max_tokens } (defaults: the first enabled provider, no agent).
The thread id is the chat: a thread that is already a chat id continues that chat, otherwise a new chat is made and remembered for the thread.
An answer to an interrupt is a RunAgentInput with `resume` (interrupt id + payload); it carries on with the same run.
"""
from __future__ import annotations

from types import SimpleNamespace

from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import agui_flags
from .acp_agents import AcpError
from .agui import AguiTranslator, last_user_text
from .chat_job import make_chat_work
from .sidebot import parse_run_input, sse_headers


class Props(BaseModel):
    provider: str | None = None
    model: str | None = None
    agent: str | None = None
    workspace: str | None = None
    max_tokens: int | None = Field(default=None, ge=16)


def register(app: FastAPI) -> None:
    r = APIRouter(tags=["agui"])

    @r.post("/api/agui")
    async def agui(request: Request):
        s = request.app.state.s
        try:
            raw = await request.json()
        except ValueError:
            raise HTTPException(422, "the body must be JSON")
        try:
            inp = parse_run_input(raw)
        except ValueError as e:
            raise HTTPException(422, str(e))
        sid = s.agui_threads.get(inp.thread_id) or (inp.thread_id if s.chat.store.get(inp.thread_id) else None)
        if inp.resume:
            job = s.jobs.active_for(sid) if sid else None
            if job is None:
                raise HTTPException(409, "nothing is waiting for an answer on this thread")
            for entry in inp.resume:
                body = {"cancel": True} if entry.status == "cancelled" else (entry.payload if isinstance(entry.payload, dict) else {})
                try:
                    s.acp.respond(entry.interrupt_id, body)
                except AcpError as e:
                    raise HTTPException(409, str(e))
            agent = s.agents.get(((s.chat.store.get(sid) or {}).get("agent") or "")) if sid else None
            after = s.agui_cursors.get(job.id, -1)
        else:
            try:
                props = Props.model_validate(inp.forwarded_props if isinstance(inp.forwarded_props, dict) else {})
            except ValueError as e:
                raise HTTPException(422, f"forwardedProps is not valid: {str(e)[:200]}")
            agent_id = props.agent or ((s.chat.store.get(sid) or {}).get("agent") if sid else None)
            agent = s.agents.get(agent_id) if agent_id else None
            if agent is not None and not agui_flags.endpoint_on(agent):
                raise HTTPException(403, f"AG-UI is switched off for the agent '{agent.id}'. Switch it on in the agent's settings (AG-UI) to use this endpoint.")
            message = last_user_text(inp.messages)
            if not message:
                raise HTTPException(422, "the run needs a user message")
            provider = props.provider or next((p.id for p in s.providers.list() if p.enabled), None)
            if not provider:
                raise HTTPException(422, "no model provider is switched on (Settings > Model providers)")
            if sid and (busy := s.jobs.active_for(sid)):
                raise HTTPException(409, f"this chat is still running (job {busy.id}); wait for it or stop it first")
            body = SimpleNamespace(provider=provider, model=props.model, message=message[:100_000], session_id=sid, max_tokens=props.max_tokens, agent=props.agent, workspace=props.workspace)
            job = s.jobs.start(make_chat_work(s, body), float(s.db.get_setting("max_turn_minutes", "0") or 0))
            after = -1
        tr = AguiTranslator(inp.thread_id, inp.run_id, inp.parent_run_id, components=agent is None or agui_flags.components_on(agent))
        enc = EventEncoder()

        async def stream():
            for e in tr.start():
                yield enc.encode(e)
            last = after
            async for ev in s.jobs.follow(job, after):
                if ev is None:
                    yield ": keepalive\n\n"
                    continue
                last = ev["seq"]
                if ev.get("type") == "session":
                    s.agui_threads[inp.thread_id] = ev.get("session_id")
                for e in tr.feed(ev):
                    yield enc.encode(e)
                if tr.paused or tr.finished:
                    break
            if tr.paused:
                s.agui_cursors[job.id] = last
            for e in tr.close():
                yield enc.encode(e)
        return StreamingResponse(stream(), media_type="text/event-stream", headers=sse_headers())

    app.include_router(r)
