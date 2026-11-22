"""A2A (Agent2Agent protocol 1.0) for Agent Studio, both directions, on the official a2a-sdk.

Server: every agent with "A2A" switched on is reachable at `/a2a/{agent}/` (JSON-RPC) with its agent card at
`/a2a/{agent}/.well-known/agent-card.json`. The SDK's own request handler speaks the protocol (tasks, streaming, cancel); what is
ours is the part the SDK leaves open - AUTHENTICATION: every request, the card included, needs an API key (see keys.py) that is
allowed to call that agent, and the whole thing is off until switched on in Settings. A call runs through the normal chat engine.

Client: agents can call other agents anywhere over A2A with the `call_remote_agent` tool. Remote agents are registered in
Settings (URL + optional bearer token, write-only) and only registered ones can be called.
"""
from __future__ import annotations

import asyncio
import json
import re
import ssl
import time
import uuid
from typing import Any

import httpx
from a2a.client import A2ACardResolver, AgentCardResolutionError, ClientConfig, create_client
from a2a.helpers import get_message_text, get_stream_response_text, new_task, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (AgentCapabilities, AgentCard, AgentInterface, AgentSkill, HTTPAuthSecurityScheme, Part, SecurityRequirement, SecurityScheme,
                       SendMessageRequest, StringList, TaskState)
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.applications import Starlette

from google.protobuf.json_format import MessageToDict

from ..budget import BudgetExceeded
from ..db import Database
from ..providers import SCOPE_LABEL, network_scope
from .api import OaiError, a2a_on, agent_for, authenticate, pick_provider

REMOTE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
MAX_REMOTES = 30
MAX_REPLY = 20_000
CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")        # the current path, then the older one
HEADER_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,60}$")
AUTH_KINDS = ("bearer", "header")


class RemoteError(ValueError):
    """A remote-agent operation was refused or failed; the message says why."""


# ------------------------------------------------------------------------------------------------ server
def public_base(request_or_none: Any = None) -> str:
    import os
    return os.environ.get("AGENT_STUDIO_URL") or "http://127.0.0.1:8765"


def make_card(agent, base: str) -> AgentCard:
    tools = ", ".join(agent.tools[:12]) or "none"
    return AgentCard(
        name=agent.name, description=agent.description or f"{agent.name}, an Agent Studio agent.", version="1.0.0",
        supported_interfaces=[AgentInterface(url=f"{base}/a2a/{agent.id}/", protocol_binding="JSONRPC", protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text/plain"], default_output_modes=["text/plain"],
        skills=[AgentSkill(id=agent.id, name=agent.name, description=(agent.description or agent.name) + f" Tools: {tools}.",
                           tags=["agent-studio", agent.framework], examples=[], input_modes=["text/plain"], output_modes=["text/plain"])],
        security_schemes={"bearer": SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="bearer", description="An Agent Studio API key (asx_...)"))},
        security_requirements=[SecurityRequirement(schemes={"bearer": StringList(list=[])})])


class StudioExecutor(AgentExecutor):
    """Runs one A2A request through the chat engine; the A2A context id is the chat session, so a conversation continues."""

    def __init__(self, s, agent_id: str):
        self.s, self.agent_id = s, agent_id
        self.running: dict[str, asyncio.Task] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        up = TaskUpdater(event_queue, context.task_id, context.context_id)
        if context.current_task is None:                  # a new task starts with the Task itself, then its status updates
            await event_queue.enqueue_event(new_task(context.task_id, context.context_id, TaskState.TASK_STATE_SUBMITTED, history=[context.message]))
        await up.start_work()
        agent = self.s.agents.get(self.agent_id)
        text = context.get_user_input()
        if agent is None or not agent.a2a or not text.strip():
            await up.failed(up.new_agent_message([Part(text="This agent is not available over A2A, or the message was empty.")]))
            return
        sid = "a2a-" + re.sub(r"[^A-Za-z0-9_-]", "", context.context_id or "")[:40]
        task = asyncio.ensure_future(self.s.chat.send(pick_provider(self.s, agent), None, text, sid, None, agent.id, None))
        self.running[context.task_id] = task
        try:
            res = await task
        except asyncio.CancelledError:
            await up.cancel()
            return
        except BudgetExceeded as e:
            await up.failed(up.new_agent_message([Part(text=e.detail["message"])]))
            return
        except Exception as e:  # noqa: BLE001 - the caller gets the plain reason
            from ..errors import as_event
            info = as_event(e, None)
            await up.failed(up.new_agent_message([Part(text=f"{info.get('title')}: {info.get('hint') or info.get('raw') or ''}")]))
            return
        finally:
            self.running.pop(context.task_id, None)
        await up.add_artifact([Part(text=(res.get("reply") or "")[:MAX_REPLY])], name="answer")
        await up.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        t = self.running.get(context.task_id)
        if t is not None:
            t.cancel()
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def build_app(s, agent, base: str) -> Starlette:
    card = make_card(agent, base)
    handler = DefaultRequestHandler(agent_executor=StudioExecutor(s, agent.id), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, rpc_url="/")])


class A2AMiddleware:
    """Pure ASGI: sends `/a2a/{agent}/...` to that agent's A2A app after checking the API key; everything else passes through."""

    def __init__(self, app):
        self.app = app
        self.cache: dict[str, tuple[tuple, Starlette]] = {}

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/a2a/"):
            return await self.app(scope, receive, send)
        s = scope["app"].state.s
        parts = path.split("/", 3)                       # ['', 'a2a', '<agent>', '<rest>']
        try:
            request = Request(scope, receive)
            key = authenticate(s, request, "a2a")
            agent = agent_for(s, key, parts[2] if len(parts) > 2 else "", "a2a")
        except OaiError as e:
            return await e.response()(scope, receive, send)
        sig = (agent.name, agent.description, tuple(agent.tools), agent.framework)
        hit = self.cache.get(agent.id)
        if hit is None or hit[0] != sig:
            hit = self.cache[agent.id] = (sig, build_app(s, agent, public_base()))
        sub = dict(scope, path="/" + (parts[3] if len(parts) > 3 else ""), root_path=f"/a2a/{agent.id}", raw_path=None)
        await hit[1](sub, receive, send)


# ------------------------------------------------------------------------------------------------ client
def card_base(url: str) -> str:
    """The agent's base address from what was pasted: the base itself, or the link to its agent card."""
    u = (url or "").strip().rstrip("/")
    for suffix in CARD_PATHS:
        if u.lower().endswith(suffix):
            u = u[: -len(suffix)]
            break
    if not re.match(r"^https?://[^\s/]+", u) or len(u) > 300:
        raise RemoteError("the address must start with http:// or https://")
    return u.rstrip("/") + "/"


def auth_headers(token: str, kind: str, header: str) -> dict:
    if not token:
        return {}
    return {header: token} if kind == "header" else {"Authorization": f"Bearer {token}"}


def _check_auth(kind: str, header: str) -> None:
    if kind not in AUTH_KINDS:
        raise RemoteError("sign-in must be a bearer token or an API key in a header")
    if kind == "header" and not HEADER_RE.match(header or ""):
        raise RemoteError("give the name of the header that carries the API key (for example X-API-Key)")


def _scheme_text(sch: Any) -> str:
    """One security scheme of an agent card, in plain words (the card may use the current or the older shape)."""
    if not isinstance(sch, dict):
        return "unknown sign-in"
    if "httpAuthSecurityScheme" in sch or sch.get("type") == "http":
        h = sch.get("httpAuthSecurityScheme") or sch
        scheme = str(h.get("scheme") or "").lower()
        return "bearer token" if scheme == "bearer" else f"HTTP {scheme or 'auth'}"
    if "apiKeySecurityScheme" in sch or sch.get("type") == "apiKey":
        k = sch.get("apiKeySecurityScheme") or sch
        return f"API key ({k.get('location') or k.get('in') or 'header'} {k.get('name') or ''})".replace("  ", " ").replace(" )", ")")
    if "oauth2SecurityScheme" in sch or sch.get("type") == "oauth2":
        return "OAuth 2"
    if "openIdConnectSecurityScheme" in sch or sch.get("type") == "openIdConnect":
        return "OpenID Connect"
    if "mtlsSecurityScheme" in sch or sch.get("type") == "mutualTLS":
        return "mutual TLS"
    return "unknown sign-in"


def summarize_card(card: dict) -> dict:
    """What the UI shows of an agent card: identity, skills, how to sign in, streaming."""
    schemes = card.get("securitySchemes") or {}
    auth = list(dict.fromkeys(_scheme_text(v) for v in schemes.values()))
    ifaces = [i["url"] for i in (card.get("supportedInterfaces") or []) if isinstance(i, dict) and i.get("url")]
    if card.get("url") and card["url"] not in ifaces:                       # the older card shape carries one address
        ifaces.append(card["url"])
    prov = card.get("provider")
    skills = [dict(id=str(sk.get("id") or ""), name=str(sk.get("name") or ""), description=str(sk.get("description") or "")[:300], tags=[str(t) for t in sk.get("tags") or []][:8])
              for sk in (card.get("skills") or []) if isinstance(sk, dict)][:50]
    return dict(name=card.get("name"), description=str(card.get("description") or "")[:600], version=card.get("version"),
                provider=prov.get("organization") if isinstance(prov, dict) else prov, streaming=(card.get("capabilities") or {}).get("streaming"),
                skills=skills, auth=auth, interfaces=ifaces[:5], input_modes=list(card.get("defaultInputModes") or []), output_modes=list(card.get("defaultOutputModes") or []))


def explain_fetch_error(e: BaseException, base: str) -> dict:
    """A plain reason for a failed card fetch: {kind, title, detail}."""
    host = base.split("//")[-1].split("/")[0]
    cause = e.__cause__ or e
    text = f"{type(cause).__name__}: {cause}"
    status = getattr(e, "status_code", None)
    if status in (401, 403):
        return dict(kind="auth", title="The agent refused the credentials", detail=f"HTTP {status}. Check the token or API key, and how it is sent (bearer token or a header).")
    if status == 404:
        return dict(kind="address", title="No agent card there", detail=f"{host} has no agent card at /.well-known/agent-card.json. Check the address; it may need a path such as /travel/.")
    if status:
        return dict(kind="server", title="The agent had an error", detail=f"HTTP {status} when reading its card.")
    if isinstance(cause, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in text or "[SSL" in text:
        return dict(kind="tls", title="Certificate problem", detail=f"The secure connection to {host} failed its certificate check.")
    if isinstance(cause, (httpx.TimeoutException, TimeoutError)):
        return dict(kind="timeout", title="Timed out", detail=f"{host} did not answer in time.")
    if isinstance(cause, httpx.RequestError):
        return dict(kind="unreachable", title="Cannot reach it", detail=f"Nothing answered at {host}. Check the address, and that this PC can reach it.")
    if isinstance(e, AgentCardResolutionError):
        return dict(kind="card", title="Not an agent card", detail="The address answered, but not with a valid A2A agent card.")
    return dict(kind="other", title="The check failed", detail=text[:300])


class RemoteAgents:
    def __init__(self, db: Database, transport: httpx.AsyncBaseTransport | None = None, clock=time.time):
        self.db, self.transport, self.clock = db, transport, clock

    @staticmethod
    def _public(r) -> dict:
        card = json.loads(r["card"] or "{}")
        scope = network_scope(r["url"])
        return dict(id=r["id"], label=r["label"], url=r["url"], enabled=bool(r["enabled"]), has_token=bool(r["token"]), created=r["created"],
                    name=card.get("name"), description=card.get("description"), auth_kind=r["auth_kind"], auth_header=r["auth_header"],
                    delegates=json.loads(r["delegates"] or "[]"), network_scope=scope, scope_label=SCOPE_LABEL[scope],
                    card=summarize_card(card) if card else None)

    def list(self) -> list[dict]:
        return [self._public(r) for r in self.db.query("SELECT * FROM a2a_remotes ORDER BY created")]

    def get(self, rid: str) -> dict:
        r = self.db.one("SELECT * FROM a2a_remotes WHERE id=?", (rid,))
        if r is None:
            raise KeyError(rid)
        return self._public(r)

    def enabled_ids(self, agent_id: str | None = None) -> list[str]:
        """Enabled remote agents. With an agent id: only those it may call (a remote with no delegates listed is open to every agent that has the tool)."""
        out = []
        for r in self.db.query("SELECT id, delegates FROM a2a_remotes WHERE enabled=1 ORDER BY created"):
            d = json.loads(r["delegates"] or "[]")
            if agent_id is None or not d or agent_id in d:
                out.append(r["id"])
        return out

    def add(self, rid: str, label: str, url: str, token: str = "", auth_kind: str = "bearer", auth_header: str = "", card: dict | None = None) -> dict:
        if not REMOTE_ID_RE.match(rid or ""):
            raise RemoteError("id must be lowercase letters, digits and underscores, starting with a letter (2-31 characters)")
        if not re.match(r"^https?://[^\s/]+", url or "") or len(url) > 300:
            raise RemoteError("the URL must start with http:// or https://")
        _check_auth(auth_kind, auth_header)
        if self.db.one("SELECT id FROM a2a_remotes WHERE id=?", (rid,)):
            raise RemoteError(f"a remote agent '{rid}' already exists")
        if len(self.list()) >= MAX_REMOTES:
            raise RemoteError(f"at most {MAX_REMOTES} remote agents")
        self.db.execute("INSERT INTO a2a_remotes(id,label,url,token,created,auth_kind,auth_header,card) VALUES(?,?,?,?,?,?,?,?)",
                        (rid, (label or rid)[:60], url.rstrip("/") + "/", token or "", self.clock(), auth_kind, auth_header if auth_kind == "header" else "",
                         json.dumps(card)[:20000] if card else "{}"))
        return self.get(rid)

    def update(self, rid: str, patch: dict) -> dict:
        r = self.db.one("SELECT * FROM a2a_remotes WHERE id=?", (rid,))
        if r is None:
            raise KeyError(rid)
        token = r["token"] if patch.get("token") is None else patch["token"]                 # None = unchanged, "" = clear
        url = card_base(patch["url"]) if patch.get("url") else r["url"]
        kind = patch.get("auth_kind") or r["auth_kind"]
        header = patch["auth_header"] if "auth_header" in patch else r["auth_header"]
        _check_auth(kind, header)
        self.db.execute("UPDATE a2a_remotes SET label=?, enabled=?, token=?, url=?, auth_kind=?, auth_header=? WHERE id=?",
                        ((patch.get("label") or r["label"])[:60], int(patch.get("enabled", bool(r["enabled"]))), token, url, kind, header if kind == "header" else "", rid))
        return self.get(rid)

    def set_delegates(self, rid: str, agents: list[str]) -> dict:
        if self.db.one("SELECT id FROM a2a_remotes WHERE id=?", (rid,)) is None:
            raise KeyError(rid)
        self.db.execute("UPDATE a2a_remotes SET delegates=? WHERE id=?", (json.dumps(sorted({str(a) for a in agents})), rid))
        return self.get(rid)

    def delete(self, rid: str) -> None:
        if self.db.one("SELECT id FROM a2a_remotes WHERE id=?", (rid,)) is None:
            raise KeyError(rid)
        self.db.execute("DELETE FROM a2a_remotes WHERE id=?", (rid,))

    @staticmethod
    def _headers(r) -> dict:
        return auth_headers(r["token"], r["auth_kind"], r["auth_header"])

    async def _client(self, r):
        headers = self._headers(r)
        http = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(120, connect=10), transport=self.transport)
        return http, await create_client(r["url"], ClientConfig(streaming=False, httpx_client=http), resolver_http_kwargs={"headers": headers})

    async def _fetch_card(self, base: str, headers: dict) -> dict:
        """The agent card as a dict. Tries the current card path, then the older one."""
        http = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(20, connect=10), transport=self.transport)
        try:
            last: AgentCardResolutionError | None = None
            for path in CARD_PATHS:
                try:
                    return MessageToDict(await A2ACardResolver(http, base, agent_card_path=path).get_agent_card())
                except AgentCardResolutionError as e:
                    last = e
                    if e.status_code != 404:
                        raise
            raise last  # type: ignore[misc]
        finally:
            await http.aclose()

    async def preview(self, url: str, token: str = "", auth_kind: str = "bearer", auth_header: str = "") -> dict:
        """Read an agent's card before anything is saved. Never raises: {ok, ...}."""
        try:
            base = card_base(url)
            _check_auth(auth_kind, auth_header)
        except RemoteError as e:
            return dict(ok=False, kind="config", title="Check the address", detail=str(e))
        try:
            card = await self._fetch_card(base, auth_headers(token, auth_kind, auth_header))
        except Exception as e:  # noqa: BLE001
            return dict(ok=False, **explain_fetch_error(e, base))
        scope = network_scope(base)
        return dict(ok=True, base_url=base, card=summarize_card(card), raw_card=card, scope=scope, scope_label=SCOPE_LABEL[scope])

    async def connect(self, url: str, label: str = "", token: str = "", auth_kind: str = "bearer", auth_header: str = "") -> dict:
        pv = await self.preview(url, token, auth_kind, auth_header)
        if not pv["ok"]:
            raise RemoteError(f"{pv['title']}: {pv['detail']}")
        name = (label or pv["card"].get("name") or "remote agent").strip()
        base_id = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24]
        base_id = base_id if re.match(r"^[a-z]", base_id) else "agent_" + base_id
        taken = {r["id"] for r in self.db.query("SELECT id FROM a2a_remotes")}
        rid, n = base_id, 2
        while rid in taken or not REMOTE_ID_RE.match(rid):
            rid, n = f"{base_id[:26]}_{n}", n + 1
        return self.add(rid, name, pv["base_url"], token, auth_kind, auth_header, pv["raw_card"])

    async def refresh(self, rid: str) -> dict:
        """Read the card again with the stored credentials and remember it."""
        r = self.db.one("SELECT * FROM a2a_remotes WHERE id=?", (rid,))
        if r is None:
            raise KeyError(rid)
        try:
            card = await self._fetch_card(r["url"], self._headers(r))
        except Exception as e:  # noqa: BLE001
            return dict(ok=False, **explain_fetch_error(e, r["url"]))
        self.db.execute("UPDATE a2a_remotes SET card=? WHERE id=?", (json.dumps(card)[:20000], rid))
        return dict(ok=True, card=summarize_card(card))

    async def test(self, rid: str) -> dict:
        """Fetch the remote agent's card (and remember it) - proves the URL and token work."""
        res = await self.refresh(rid)
        if not res["ok"]:
            return dict(ok=False, error=f"{res['title']}: {res['detail']}")
        return dict(ok=True, name=res["card"].get("name"), description=res["card"].get("description"))

    async def ping(self, rid: str, text: str) -> dict:
        """One real message to the agent, with the exact reason when it fails."""
        t0 = time.monotonic()
        try:
            res = await self.ask(rid, text)
        except RemoteError as e:
            return dict(ok=False, title="The agent did not answer", detail=str(e), ms=int((time.monotonic() - t0) * 1000))
        ms = int((time.monotonic() - t0) * 1000)
        return dict(ok=True, title="Answered", detail=f"in {ms} ms.", reply=res["reply"][:500], ms=ms)

    async def ask(self, rid: str, text: str) -> dict:
        r = self.db.one("SELECT * FROM a2a_remotes WHERE id=? AND enabled=1", (rid,))
        if r is None:
            raise RemoteError(f"no enabled remote agent '{rid}'. Available: {self.enabled_ids() or 'none (add one in Settings > API & A2A)'}")
        http = None
        try:
            http, client = await self._client(r)
            out: list[str] = []
            failed: str | None = None
            async for resp in client.send_message(SendMessageRequest(message=new_text_message(text))):
                t = get_stream_response_text(resp)
                if t:
                    out.append(t)
                st = resp.status_update.status if resp.HasField("status_update") else (resp.task.status if resp.HasField("task") else None)
                if st is not None and st.state in (TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_REJECTED):
                    failed = (get_message_text(st.message) if st.HasField("message") else "") or "the remote agent could not do it"
            await client.close()
            if failed is not None:
                raise RemoteError(f"the remote agent reported a failure: {failed[:300]}")
        except RemoteError:
            raise
        except Exception as e:  # noqa: BLE001
            raise RemoteError(f"the remote agent failed: {type(e).__name__}: {str(e)[:300]}")
        finally:
            if http is not None:
                await http.aclose()
        return dict(ok=True, agent=rid, reply=(out[-1] if out else "")[:MAX_REPLY])


def remote_tool(remotes: RemoteAgents, ids: list[str]):
    async def call_remote_agent(agent: str, message: str) -> dict:
        try:
            return await remotes.ask(agent, message)
        except RemoteError as e:
            return {"ok": False, "error": str(e)}
    call_remote_agent.__doc__ = ("Send a message to another agent over the A2A protocol and return its answer.\n\nArgs:\n"
                                 f"    agent: which remote agent: one of {ids}.\n    message: the complete, self-contained request.")
    return call_remote_agent


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/access/remotes", tags=["a2a"])

    def rem(request: Request) -> RemoteAgents:
        return request.app.state.s.a2a_remotes

    def bad(e: Exception, code: int = 422):
        return JSONResponse(status_code=code, content={"detail": str(e)})

    @r.get("")
    def list_remotes(request: Request):
        return rem(request).list()

    @r.post("")
    def add(body: dict, request: Request):
        try:
            return rem(request).add(body.get("id") or "", body.get("label") or "", body.get("url") or "", body.get("token") or "",
                                    body.get("auth_kind") or "bearer", body.get("auth_header") or "")
        except RemoteError as e:
            return bad(e)

    @r.post("/preview")
    async def preview(body: dict, request: Request):
        res = await rem(request).preview(body.get("url") or "", body.get("token") or "", body.get("auth_kind") or "bearer", body.get("auth_header") or "")
        res.pop("raw_card", None)
        return res

    @r.post("/connect")
    async def connect(body: dict, request: Request):
        try:
            return await rem(request).connect(body.get("url") or "", body.get("label") or "", body.get("token") or "", body.get("auth_kind") or "bearer",
                                              body.get("auth_header") or "")
        except RemoteError as e:
            return bad(e)

    @r.get("/{rid}")
    def one(rid: str, request: Request):
        try:
            return rem(request).get(rid)
        except KeyError:
            return bad(KeyError("unknown remote agent"), 404)

    @r.put("/{rid}")
    def update(rid: str, body: dict, request: Request):
        try:
            return rem(request).update(rid, body)
        except KeyError:
            return JSONResponse(status_code=404, content={"detail": "unknown remote agent"})
        except RemoteError as e:
            return bad(e)

    @r.put("/{rid}/delegates")
    def delegates(rid: str, body: dict, request: Request):
        """Which agents may call this remote agent. Each ticked agent also gets the call_remote_agent tool."""
        s = request.app.state.s
        ids = [str(a) for a in (body.get("agents") or [])]
        known = {a.id: a for a in s.agents.list()}
        if any(i not in known for i in ids):
            return bad(ValueError("unknown agent: " + ", ".join(i for i in ids if i not in known)))
        try:
            out = rem(request).set_delegates(rid, ids)
        except KeyError:
            return bad(KeyError("unknown remote agent"), 404)
        for i in ids:
            if "call_remote_agent" not in known[i].tools:
                s.agents.update(i, {"tools": [*known[i].tools, "call_remote_agent"]})
        return out

    @r.delete("/{rid}")
    def delete(rid: str, request: Request):
        try:
            rem(request).delete(rid)
        except KeyError:
            return JSONResponse(status_code=404, content={"detail": "unknown remote agent"})
        return {"ok": True}

    @r.post("/{rid}/test")
    async def test(rid: str, request: Request):
        try:
            return await rem(request).test(rid)
        except KeyError:
            return JSONResponse(status_code=404, content={"detail": "unknown remote agent"})

    @r.post("/{rid}/refresh")
    async def refresh(rid: str, request: Request):
        try:
            return await rem(request).refresh(rid)
        except KeyError:
            return bad(KeyError("unknown remote agent"), 404)

    @r.post("/{rid}/ping")
    async def ping(rid: str, body: dict, request: Request):
        text = str(body.get("message") or "").strip()
        if not text:
            return bad(ValueError("write a message to send"))
        try:
            rem(request).get(rid)
        except KeyError:
            return bad(KeyError("unknown remote agent"), 404)
        return await rem(request).ping(rid, text[:2000])

    app.include_router(r)
