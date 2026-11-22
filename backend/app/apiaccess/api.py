"""Authenticated API access to Agent Studio's agents.

* `/v1/models` and `/v1/chat/completions`: an OpenAI-compatible API where each agent that has "API access" switched on is a model.
  Use it from any OpenAI client library, tool or app: `base_url = http://127.0.0.1:8765/v1`, `api_key = asx_...`.
* `/a2a/{agent}/...`: the same agents over the A2A protocol (see a2a_server.py).
* `/api/access/*`: the settings screen's API - keys, remote A2A agents, switches.

Everything is OFF until switched on in Settings, every call needs a key, keys are scoped to agents, limited per minute and revocable,
agents opt in one by one, and every call runs through the normal chat engine, so spend caps, guardrails, memory and the run record
all apply exactly as in the app. Pydantic-free on purpose (main.py uses postponed annotations).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..budget import BudgetExceeded
from ..providers import mock_enabled
from .keys import ApiKeys, KeyError_


class OaiError(Exception):
    def __init__(self, status: int, code: str, message: str, kind: str = "invalid_request_error", headers: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.kind, self.headers = status, code, message, kind, headers or {}

    def response(self) -> JSONResponse:
        return JSONResponse(status_code=self.status, headers=self.headers,
                            content={"error": {"message": self.message, "type": self.kind, "code": self.code}})


KEY = "access_config"
DEFAULTS = dict(api_enabled=False, a2a_enabled=False, default_provider="")


def access_config(db) -> dict:
    try:
        v = json.loads(db.get_setting(KEY, "{}") or "{}")
    except ValueError:
        v = {}
    return {**DEFAULTS, **(v if isinstance(v, dict) else {})}


def set_access_config(db, patch: dict, providers=None) -> dict:
    """Validate and save the API / A2A switches; raises ValueError with a plain reason."""
    cur = access_config(db)
    for k, v in (patch or {}).items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting '{k}'")
        if k != "default_provider" and not isinstance(v, bool):
            raise ValueError(f"{k} must be true or false")
        if k == "default_provider" and (not isinstance(v, str) or (v and providers is not None and providers.get(v) is None)):
            raise ValueError("choose one of your providers, or leave it empty for the first enabled one")
        cur[k] = v
    db.set_setting(KEY, json.dumps(cur))
    return cur


def api_on(s) -> bool:
    return access_config(s.db)["api_enabled"]


def a2a_on(s) -> bool:
    return access_config(s.db)["a2a_enabled"]


def authenticate(s, request: Request, what: str = "api") -> dict:
    """The live key behind the request's Bearer token (raises OaiError 403/401/429)."""
    if not (api_on(s) if what == "api" else a2a_on(s)):
        raise OaiError(403, "api_disabled", "This API is switched off. Turn it on in Settings > API & A2A.", "permission_error")
    auth = request.headers.get("authorization", "")
    key = s.apikeys.verify(auth[7:].strip() if auth[:7].lower() == "bearer " else None)
    if key is None:
        raise OaiError(401, "invalid_api_key", "Missing, wrong, revoked or expired API key. Send it as `Authorization: Bearer asx_...`.", "authentication_error")
    wait = s.apikeys.hit(key)
    if wait is not None:
        raise OaiError(429, "rate_limit_exceeded", f"Rate limit reached for this key. Try again in {wait} seconds.", "rate_limit_error", {"Retry-After": str(int(wait + 0.5))})
    return key


def agent_for(s, key: dict, agent_id: str, flag: str = "api_access"):
    a = s.agents.get(agent_id) if isinstance(agent_id, str) else None
    if a is None or not getattr(a, flag) or not s.apikeys.allows(key, a.id):
        raise OaiError(404, "model_not_found", f"No agent named '{agent_id}' is available to this key. Turn on API access for the agent and allow it for the key.")
    return a


def pick_provider(s, agent) -> str:
    if agent.provider:
        return agent.provider
    want = access_config(s.db)["default_provider"]
    if want and s.providers.get(want):
        return want
    for p in s.providers.list():
        if p.enabled and (p.kind != "mock" or mock_enabled()):
            return p.id
    raise OaiError(503, "no_provider", "No model provider is enabled. Add one in Settings > Providers.", "server_error")


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in ("text", "input_text"))
    return ""


def prompt_from(messages) -> str:
    """OpenAI clients send the whole conversation each time: earlier turns become context, the last user message is the request."""
    if not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict) or messages[-1].get("role") != "user":
        raise OaiError(400, "invalid_messages", "`messages` must be a list that ends with a user message.")
    last = text_of(messages[-1].get("content")).strip()
    if not last:
        raise OaiError(400, "invalid_messages", "The last user message is empty.")
    before = [m for m in messages[:-1] if isinstance(m, dict) and m.get("role") in ("system", "developer", "user", "assistant") and text_of(m.get("content")).strip()]
    if not before:
        return last
    lines = "\n".join(f"{'Instructions' if m['role'] in ('system', 'developer') else m['role'].capitalize()}: {text_of(m['content']).strip()}" for m in before)
    return f"[Conversation so far, sent by the API caller]\n{lines}\n\n[Latest message]\n{last}"


def usage_of(res: dict) -> dict:
    u = res.get("usage") or {}
    i, o = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
    return {"prompt_tokens": i, "completion_tokens": o, "total_tokens": i + o}


def _completion(model: str, cid: str, res: dict) -> dict:
    return {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "finish_reason": "length" if res.get("halted") else "stop", "message": {"role": "assistant", "content": res.get("reply") or ""}}],
            "usage": usage_of(res), "x_agent_studio": {"cost_usd": (res.get("usage") or {}).get("cost_usd"), "trace_id": res.get("trace_id"), "warnings": res.get("warnings") or []}}


def _fail(e: BaseException) -> OaiError:
    if isinstance(e, BudgetExceeded):
        return OaiError(402, "budget_exceeded", e.detail["message"], "insufficient_quota")
    if isinstance(e, KeyError):
        return OaiError(404, "not_found", "unknown provider or agent")
    from ..errors import as_event
    info = as_event(e, None)
    return OaiError(502, info.get("code") or "upstream_error", f"{info.get('title')}: {info.get('hint') or info.get('raw') or ''}".strip(": "), "server_error")


def register(app: FastAPI) -> None:
    v1 = APIRouter(prefix="/v1", tags=["openai-compatible"])
    adm = APIRouter(prefix="/api/access", tags=["access"])

    def S(request: Request):
        return request.app.state.s

    @app.exception_handler(OaiError)
    async def _oai(_: Request, e: OaiError):
        return e.response()

    # ------------------------------------------------------------------ the OpenAI-compatible API
    @v1.get("/models")
    def models(request: Request):
        s = S(request)
        key = authenticate(s, request)
        return {"object": "list", "data": [{"id": a.id, "object": "model", "created": 0, "owned_by": "agent-studio", "description": a.description or a.name}
                                           for a in s.agents.list() if a.api_access and s.apikeys.allows(key, a.id)]}

    @v1.post("/chat/completions")
    async def completions(body: dict, request: Request):
        s = S(request)
        key = authenticate(s, request)
        agent = agent_for(s, key, body.get("model"))
        if body.get("tools") or body.get("functions"):
            raise OaiError(400, "tools_not_supported", "Agents use their own tools; remove `tools`/`functions` from the request.")
        prompt = prompt_from(body.get("messages"))
        provider = pick_provider(s, agent)
        cap = body.get("max_completion_tokens") or body.get("max_tokens")
        max_tokens = cap if isinstance(cap, int) and cap >= 16 else None
        cid, model = "chatcmpl-" + uuid.uuid4().hex[:24], agent.id
        if not body.get("stream"):
            try:
                res = await s.chat.send(provider, None, prompt, None, max_tokens, agent.id, None)
            except Exception as e:  # noqa: BLE001
                raise _fail(e)
            return _completion(model, cid, res)
        q: asyncio.Queue = asyncio.Queue()

        async def run():
            try:
                res = await s.chat.send(provider, None, prompt, None, max_tokens, agent.id, None, emit=lambda ev: q.put_nowait(("ev", ev)))
                q.put_nowait(("done", res))
            except BaseException as e:  # noqa: BLE001
                q.put_nowait(("err", e))
        task = asyncio.ensure_future(run())

        def chunk(delta: dict, finish=None, usage=None) -> str:
            c = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            if usage is not None:
                c["usage"] = usage
            return f"data: {json.dumps(c)}\n\n"

        async def sse():
            sent = False
            try:
                yield chunk({"role": "assistant", "content": ""})
                while True:
                    kind, val = await q.get()
                    if kind == "ev":
                        if val.get("type") == "text" and val.get("delta"):
                            sent = True
                            yield chunk({"content": val["delta"]})
                    elif kind == "err":
                        e = _fail(val)
                        yield "data: " + json.dumps({"error": {"message": e.message, "type": e.kind, "code": e.code}}) + "\n\n"
                        break
                    else:
                        if not sent and val.get("reply"):
                            yield chunk({"content": val["reply"]})
                        yield chunk({}, "length" if val.get("halted") else "stop", usage_of(val) if (body.get("stream_options") or {}).get("include_usage") else None)
                        break
                yield "data: [DONE]\n\n"
            finally:
                if not task.done():  # pragma: no cover - only when the caller disconnects mid-stream
                    task.cancel()                                       # the caller went away: stop the run (its reservations are settled by the engine)
        return StreamingResponse(sse(), media_type="text/event-stream")

    # ------------------------------------------------------------------ settings-screen API
    @adm.get("/config")
    def config(request: Request):
        s = S(request)
        return dict(api_enabled=api_on(s), a2a_enabled=a2a_on(s), api_default_provider=access_config(s.db)["default_provider"],
                    base_url="http://127.0.0.1:8765/v1", a2a_base="http://127.0.0.1:8765/a2a",
                    agents=[dict(id=a.id, name=a.name, api_access=a.api_access, a2a=a.a2a) for a in s.agents.list()],
                    keys=s.apikeys.list(), remotes=s.a2a_remotes.list())

    @adm.put("/config")
    def put_config(body: dict, request: Request):
        s = S(request)
        mapped = {"api_enabled": body.get("api_enabled"), "a2a_enabled": body.get("a2a_enabled"), "default_provider": body.get("api_default_provider")}
        try:
            cfg = set_access_config(s.db, {k: v for k, v in mapped.items() if v is not None}, s.providers)
        except ValueError as e:
            return JSONResponse(status_code=422, content={"detail": str(e)})
        return dict(api_enabled=cfg["api_enabled"], a2a_enabled=cfg["a2a_enabled"], api_default_provider=cfg["default_provider"])

    @adm.post("/keys")
    def create_key(body: dict, request: Request):
        try:
            return S(request).apikeys.create(body.get("name") or "", body.get("agents"), body.get("rate_per_min", 60), body.get("expires_days"))
        except KeyError_ as e:
            return JSONResponse(status_code=422, content={"detail": str(e)})

    @adm.put("/keys/{kid}")
    def update_key(kid: str, body: dict, request: Request):
        try:
            return S(request).apikeys.update(kid, body)
        except KeyError:
            return JSONResponse(status_code=404, content={"detail": "unknown key"})
        except KeyError_ as e:
            return JSONResponse(status_code=422, content={"detail": str(e)})

    @adm.post("/keys/{kid}/rotate")
    def rotate_key(kid: str, request: Request):
        try:
            return S(request).apikeys.rotate(kid)
        except KeyError:
            return JSONResponse(status_code=404, content={"detail": "unknown key"})

    @adm.delete("/keys/{kid}")
    def delete_key(kid: str, request: Request):
        try:
            S(request).apikeys.delete(kid)
        except KeyError:
            return JSONResponse(status_code=404, content={"detail": "unknown key"})
        return {"ok": True}

    app.include_router(v1)
    app.include_router(adm)
