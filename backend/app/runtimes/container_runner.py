"""The small server that runs INSIDE a deployed agent's container (Docker, Vertex AI Agent Runtime, Azure Foundry, AWS AgentCore).

It is deliberately tiny and self-contained (FastAPI + LiteLLM only - this file is copied into the bundle as `runner.py`):
it reads the agent's frozen definition from `agent.json` and answers with a plain model loop. It has no files, no shell, no
MCP servers and no sub-agents - the compatibility report in Agent Studio says so before anything is deployed.

Contract (one port, `PORT`, default 8080):
  GET  /ping, /health, /readiness -> {"status": "Healthy"}      (AgentCore /ping, Foundry /readiness; also used by Docker)
  POST /invocations     -> {"prompt": "..."} or {"messages": [...]}  ->  {"response": "...", "model": "..."}
  POST /responses       -> OpenAI Responses shape: {"input": "..." | [...]} -> {"output_text": "...", "output": [...]}
If `RUNTIME_TOKEN` is set every request except /ping must carry `Authorization: Bearer <token>` (used for the local Docker target).
Model credentials arrive as the environment variables LiteLLM already reads (OPENAI_API_KEY, ANTHROPIC_API_KEY...).
"""
from __future__ import annotations

import hmac
import json
import os
import time
import uuid
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request

MAX_INPUT_CHARS = 100_000


def load_spec(path: str | None = None) -> dict:
    with open(path or os.environ.get("AGENT_SPEC", "agent.json"), encoding="utf-8") as f:
        return json.load(f)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):                        # Responses/Chat content parts
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content or "")


def messages_from(body: dict, system: str) -> list[dict]:
    """The model messages for a request body (a prompt, a chat `messages` list or a Responses `input`)."""
    raw = body.get("messages") if body.get("messages") is not None else body.get("input")
    if raw is None:
        raw = body.get("prompt") if body.get("prompt") is not None else body.get("message")
    if isinstance(raw, str):
        turns = [{"role": "user", "content": raw}]
    elif isinstance(raw, list) and raw:
        turns = [{"role": m.get("role", "user") if m.get("role") in ("user", "assistant") else "user", "content": _text(m.get("content"))}
                 for m in raw if isinstance(m, dict)]
    else:
        raise HTTPException(422, "send a 'prompt', 'messages' or 'input'")
    if sum(len(t["content"]) for t in turns) > MAX_INPUT_CHARS:
        raise HTTPException(413, "the request is too large")
    return [{"role": "system", "content": system}, *turns] if system else turns


def build_app(spec: dict | None = None, completion: Callable[..., Any] | None = None) -> FastAPI:
    spec = spec if spec is not None else load_spec()
    app = FastAPI(title=f"agent {spec.get('id', '')}", docs_url=None, redoc_url=None, openapi_url=None)

    def call(messages: list[dict]) -> str:
        fn = completion
        if fn is None:
            import litellm
            fn = litellm.completion
        extra = {"api_base": os.environ["AGENT_API_BASE"]} if os.environ.get("AGENT_API_BASE") else {}
        r = fn(model=spec["model"], messages=messages, max_tokens=int(spec.get("max_tokens", 2048)), **extra)
        return (r.choices[0].message.content or "") if hasattr(r, "choices") else str(r["choices"][0]["message"]["content"])

    def guard(request: Request) -> None:
        token = os.environ.get("RUNTIME_TOKEN", "")
        if token and not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {token}"):
            raise HTTPException(401, "missing or wrong token")

    @app.get("/ping")
    @app.get("/health")
    @app.get("/readiness")                                 # Azure Foundry's platform health check
    def ping() -> dict:
        return {"status": "Healthy", "agent": spec.get("id"), "time_of_last_update": int(time.time())}

    @app.post("/invocations")
    async def invocations(request: Request) -> dict:
        guard(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "the body must be a JSON object")
        return {"response": call(messages_from(body, spec.get("instruction", ""))), "model": spec["model"]}

    @app.post("/responses")
    async def responses(request: Request) -> dict:
        guard(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "the body must be a JSON object")
        text = call(messages_from(body, spec.get("instruction", "")))
        return {"id": f"resp_{uuid.uuid4().hex[:16]}", "object": "response", "model": spec["model"], "status": "completed", "output_text": text,
                "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]}

    return app


def main() -> None:                                       # pragma: no cover - the process entry point inside the container
    import uvicorn
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":                                # pragma: no cover
    main()
