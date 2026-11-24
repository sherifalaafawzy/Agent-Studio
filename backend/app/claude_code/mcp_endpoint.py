"""`/agent-mcp/{agent}`: one Agent Studio agent as an MCP server, so Claude Code (or any MCP client) can call the RUNNING agent.

A minimal stateless Streamable-HTTP MCP server (JSON replies, no SSE stream): `initialize`, `ping`, `tools/list` and `tools/call` with one tool, `ask`.
It uses the same Bearer keys as the OpenAI-compatible API (`asx_...`, scoped to agents), needs the API switched on and the agent's "API access" box
ticked, and runs the request through the normal chat engine, so spend caps, guardrails, memory and the run record all apply.
Requests from a browser page (an `Origin` that is not this PC) are refused, so a web page cannot use the endpoint.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from ..apiaccess.api import OaiError, _fail, agent_for, authenticate, pick_provider

LATEST = "2025-06-18"
MAX_MESSAGE = 20_000
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _rpc(id_, result=None, error=None, status: int = 200) -> JSONResponse:
    body: dict = {"jsonrpc": "2.0", "id": id_}
    body.update({"error": error} if error else {"result": result})
    return JSONResponse(status_code=status, content=body)


def _tool(agent) -> dict:
    return dict(name="ask", description=(f"Ask the Agent Studio agent \"{agent.name}\". " + (agent.description or "")).strip()[:900],
                inputSchema={"type": "object", "properties": {"message": {"type": "string", "description": "The full request, with all the context the agent needs: it cannot see your files."}},
                             "required": ["message"], "additionalProperties": False})


def register(app: FastAPI) -> None:
    @app.post("/agent-mcp/{agent_id}")
    async def agent_mcp(agent_id: str, request: Request):
        s = request.app.state.s
        origin = request.headers.get("origin")
        if origin and (urlsplit(origin).hostname or "") not in LOCAL_HOSTS:
            return JSONResponse(status_code=403, content={"error": "this endpoint does not accept requests from web pages"})
        try:
            key = authenticate(s, request)
            agent = agent_for(s, key, agent_id)
        except OaiError as e:
            return e.response()
        try:
            msg = await request.json()
        except ValueError:
            return _rpc(None, error=dict(code=-32700, message="parse error"), status=400)
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _rpc(None, error=dict(code=-32600, message="send one JSON-RPC 2.0 message per request"), status=400)
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if not isinstance(method, str) or "id" not in msg:
            return Response(status_code=202)                       # a notification (e.g. notifications/initialized) or a reply: nothing to answer
        if method == "initialize":
            want = params.get("protocolVersion") if isinstance(params, dict) else None
            ver = want if isinstance(want, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", want) else LATEST
            return _rpc(mid, dict(protocolVersion=ver, capabilities={"tools": {}}, serverInfo=dict(name=f"agent-studio-{agent.id}", version="1.0"),
                                  instructions=f"Call `ask` to send a request to the Agent Studio agent {agent.name}."))
        if method == "ping":
            return _rpc(mid, {})
        if method == "tools/list":
            return _rpc(mid, dict(tools=[_tool(agent)]))
        if method != "tools/call":
            return _rpc(mid, error=dict(code=-32601, message=f"unknown method {method[:60]}"))
        args = params.get("arguments") if isinstance(params, dict) else None
        text = args.get("message") if isinstance(args, dict) else None
        if not isinstance(params, dict) or params.get("name") != "ask" or not isinstance(text, str) or not text.strip() or len(text) > MAX_MESSAGE:
            return _rpc(mid, error=dict(code=-32602, message=f"call the `ask` tool with a `message` of up to {MAX_MESSAGE} characters"))
        try:
            res = await s.chat.send(pick_provider(s, agent), None, text, None, None, agent.id, None)
        except Exception as e:  # noqa: BLE001
            return _rpc(mid, dict(content=[dict(type="text", text=_fail(e).message)], isError=True))
        reply = res.get("reply") or ""
        if res.get("halted"):
            reply += "\n\n(The agent stopped before it finished: a limit was reached.)"
        return _rpc(mid, dict(content=[dict(type="text", text=reply or "(no answer)")], isError=False))
