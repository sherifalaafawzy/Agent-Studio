from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .budget import BudgetExceeded, BudgetGuard
from .acp_agents import AcpError, AcpManager, AcpStore
from .agents import AgentDef, AgentStore
from .chat import ChatService
from .db import Database
from .pricing import known_models, price_for
from .harness import sandbox as sandbox_mod
from .harness.tools import ALL_TOOLS, TOOL_INFO
from .mcp_registry import McpStore
from .providers import CATALOG, ProviderStore
from .skills import SkillRegistry

ALLOWED_ORIGINS = [
    "http://localhost:3100", "http://127.0.0.1:3100",  # Next dev / static server
    "app://agent-studio", "null",                       # Electron packaged (file:// / custom scheme)
]


class State:
    def __init__(self, db: Database | None = None):
        self.db = db or Database()
        self.providers = ProviderStore(self.db)
        self.guard = BudgetGuard(self.db, self.providers)
        self.agents = AgentStore(self.db)
        self.mcp = McpStore(self.db)
        self.skills = SkillRegistry()
        self.chat = ChatService(self.guard, self.providers, self.agents, self.mcp, self.skills, self.db)
        self.acp = AcpManager(self.db, AcpStore(self.db), self.chat.workspace_for)   # external ACP agents (Phase 3)


class ProviderPatch(BaseModel):
    enabled: bool | None = None
    api_key: str | None = None  # None = unchanged, "" = clear
    api_base: str | None = None
    vertex_project: str | None = None
    vertex_location: str | None = None
    vertex_credentials_path: str | None = None
    default_model: str | None = None
    pro_model: str | None = None
    flash_model: str | None = None
    cap_usd: float | None = Field(default=None, ge=0)
    cap_period: str | None = None
    max_output_tokens: int | None = None


class ChatIn(BaseModel):
    provider: str
    message: str = Field(min_length=1, max_length=100_000)
    model: str | None = None
    session_id: str | None = None
    max_tokens: int | None = Field(default=None, ge=16)
    agent: str | None = None       # None = plain chat (no tools)
    workspace: str | None = None


class AgentIn(BaseModel):
    id: str | None = None
    name: str | None = None
    description: str | None = None
    instruction: str | None = None
    provider: str | None = None
    tier: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    mcp: list[str] | None = None
    skills: list[str] | None = None
    sub_agents: list[str] | None = None


class McpPatch(BaseModel):
    enabled: bool | None = None
    env: dict[str, str] | None = None


class AcpChatIn(BaseModel):
    agent: str
    message: str = Field(min_length=1, max_length=200_000)
    session_id: str | None = None
    workspace: str | None = None
    policy: str = "ask"            # ask | reads_auto | readonly
    mode: str | None = None        # one of the modes the agent advertises (e.g. plan / ask)
    timeout_s: int = Field(default=900, ge=10, le=3600)


class AcpAgentPatch(BaseModel):
    label: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None   # "" clears a key
    enabled: bool | None = None
    max_turns: int | None = Field(default=None, ge=1, le=1000)
    notes: str | None = None


class AcpAgentCreate(BaseModel):
    id: str
    label: str = ""
    command: str
    args: list[str] = []
    notes: str = ""


class SettingsPatch(BaseModel):
    workspace_dir: str | None = None
    sandbox_network: bool | None = None
    max_llm_calls_per_turn: int | None = Field(default=None, ge=1, le=500)


def create_app(state: State | None = None) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(a: FastAPI):
        yield
        await a.state.s.acp.close_all()     # never leave agent subprocesses behind
        a.state.s.acp.stop()

    app = FastAPI(title="Agent Studio", version="0.3.0", lifespan=lifespan)
    app.state.s = state or State()
    # Local-only server: reject foreign Host headers (DNS-rebinding) and foreign origins.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"])

    def S(request: Request) -> State:
        return request.app.state.s

    @app.exception_handler(BudgetExceeded)
    async def _budget(_: Request, e: BudgetExceeded):
        return JSONResponse(status_code=402, content={"detail": e.detail})

    @app.get("/api/health")
    def health():
        import google.adk, litellm  # noqa
        return {"ok": True, "adk": google.adk.__version__, "litellm": getattr(litellm, "__version__", "?")}

    # ---- settings: providers ----
    @app.get("/api/providers")
    def list_providers(s: State = Depends(S)):
        return [dict(p.public(), catalog=dict(CATALOG[p.kind])) for p in s.providers.list()]

    @app.put("/api/providers/{pid}")
    def update_provider(pid: str, patch: ProviderPatch, s: State = Depends(S)):
        try:
            p = s.providers.update(pid, patch.model_dump(exclude_unset=True))
        except KeyError:
            raise HTTPException(404, "unknown provider")
        except ValueError as e:
            raise HTTPException(422, str(e))
        return p.public()

    @app.get("/api/providers/{pid}/models")
    def provider_models(pid: str, s: State = Depends(S)):
        p = s.providers.get(pid)
        if not p:
            raise HTTPException(404, "unknown provider")
        return known_models(p.kind)

    # ---- billing ----
    @app.get("/api/billing/summary")
    def billing_summary(s: State = Depends(S)):
        rows = [s.guard.status(p) for p in s.providers.list()]
        return {"providers": rows, "by_model": s.guard.by_model(),
                "total_spent_usd": sum(r["spent_usd"] for r in rows)}

    @app.get("/api/billing/ledger")
    def billing_ledger(limit: int = 100, provider: str | None = None, s: State = Depends(S)):
        return s.guard.ledger(min(limit, 1000), provider)

    @app.post("/api/billing/preflight")
    def billing_preflight(body: ChatIn, s: State = Depends(S)):
        """Dry-run the guard (no model call, nothing reserved): would this be allowed?"""
        p = s.providers.get(body.provider)
        if not p:
            raise HTTPException(404, "unknown provider")
        from .budget import estimate_tokens
        from .providers import litellm_target
        model = body.model or p.default_model
        try:
            lm, _ = litellm_target(p, model)
        except ValueError:
            lm = f"{CATALOG[p.kind]['prefix']}{model}"
        price = price_for(p.kind, model, lm)
        mo = min(body.max_tokens or p.max_output_tokens, p.max_output_tokens)
        from .pricing import cost_usd
        proj = cost_usd(price, estimate_tokens(len(body.message), 2), mo)
        st = s.guard.status(p)
        return dict(projected_usd=proj, remaining_usd=st["remaining_usd"], would_allow=p.enabled and proj <= st["remaining_usd"] + 1e-12,
                    price_known=price.known, price_source=price.source)

    # ---- chat ----
    @app.post("/api/chat")
    async def chat(body: ChatIn, s: State = Depends(S)):
        try:
            return await s.chat.send(body.provider, body.model, body.message, body.session_id, body.max_tokens,
                                     body.agent, body.workspace)
        except KeyError:
            raise HTTPException(404, "unknown provider")
        except ValueError as e:
            raise HTTPException(422, str(e))
        except BudgetExceeded:
            raise  # -> 402 via handler
        except Exception as e:  # provider/network errors
            raise HTTPException(502, f"{type(e).__name__}: {e}")

    # ---- agents ----
    @app.get("/api/agents")
    def list_agents(s: State = Depends(S)):
        return [a.__dict__ for a in s.agents.list()]

    @app.post("/api/agents")
    def create_agent(body: AgentIn, s: State = Depends(S)):
        try:
            d = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
            return s.agents.create(AgentDef(id=d.pop("id", ""), name=d.pop("name", ""), **d)).__dict__
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.put("/api/agents/{aid}")
    def update_agent(aid: str, body: AgentIn, s: State = Depends(S)):
        try:
            return s.agents.update(aid, body.model_dump(exclude_unset=True)).__dict__
        except KeyError:
            raise HTTPException(404, "unknown agent")
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/agents/{aid}")
    def delete_agent(aid: str, s: State = Depends(S)):
        try:
            s.agents.delete(aid)
        except KeyError:
            raise HTTPException(404, "unknown agent")
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"ok": True}

    @app.get("/api/tools")
    def list_tools():
        return [dict(name=n, description=d, read_only=ro) for n, (d, ro) in TOOL_INFO.items()]

    # ---- MCP ----
    @app.get("/api/mcp")
    def list_mcp(s: State = Depends(S)):
        return [m.public() for m in s.mcp.list()]

    @app.put("/api/mcp/{sid}")
    def update_mcp(sid: str, body: McpPatch, s: State = Depends(S)):
        try:
            return s.mcp.update(sid, body.enabled, body.env).public()
        except KeyError:
            raise HTTPException(404, "unknown MCP server")

    @app.delete("/api/mcp/{sid}")
    def delete_mcp(sid: str, s: State = Depends(S)):
        try:
            s.mcp.delete(sid)
        except KeyError:
            raise HTTPException(404, "unknown MCP server")
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"ok": True}

    # ---- skills ----
    @app.get("/api/skills")
    def list_skills(s: State = Depends(S)):
        return [k.public() for k in s.skills.list()]

    @app.get("/api/skills/{name}")
    def get_skill(name: str, s: State = Depends(S)):
        k = s.skills.get(name)
        if not k:
            raise HTTPException(404, "unknown skill")
        return k.public(with_body=True)

    @app.delete("/api/skills/{name}")
    def delete_skill(name: str, s: State = Depends(S)):
        try:
            s.skills.delete(name)
        except KeyError:
            raise HTTPException(404, "unknown skill")
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"ok": True}

    # ---- harness settings / sandbox ----
    def _settings(s: State) -> dict:
        return dict(workspace_dir=s.chat.workspace_for(None).as_posix(), sandbox_network=s.db.get_setting("sandbox_network", "0") == "1",
                    max_llm_calls_per_turn=int(s.db.get_setting("max_llm_calls_per_turn", "40")))

    @app.get("/api/settings")
    def get_settings(s: State = Depends(S)):
        return _settings(s)

    @app.put("/api/settings")
    def put_settings(body: SettingsPatch, s: State = Depends(S)):
        if body.workspace_dir is not None:
            from pathlib import Path
            if not Path(body.workspace_dir).expanduser().is_absolute():
                raise HTTPException(422, "workspace_dir must be an absolute path")
            s.db.set_setting("workspace_dir", body.workspace_dir)
        if body.sandbox_network is not None:
            s.db.set_setting("sandbox_network", "1" if body.sandbox_network else "0")
        if body.max_llm_calls_per_turn is not None:
            s.db.set_setting("max_llm_calls_per_turn", str(body.max_llm_calls_per_turn))
        return _settings(s)

    # ---- external agents over ACP (Phase 3) ----
    def _acp(fn):
        try:
            return fn()
        except AcpError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/acp/agents")
    def acp_agents(s: State = Depends(S)):
        return s.acp.store.list()

    @app.post("/api/acp/agents")
    def acp_create(body: AcpAgentCreate, s: State = Depends(S)):
        return _acp(lambda: s.acp.store.create(body.id, body.label, body.command, body.args, body.notes))

    @app.put("/api/acp/agents/{aid}")
    def acp_update(aid: str, body: AcpAgentPatch, s: State = Depends(S)):
        return _acp(lambda: s.acp.store.update(aid, body.model_dump(exclude_unset=True)))

    @app.delete("/api/acp/agents/{aid}")
    def acp_delete(aid: str, s: State = Depends(S)):
        _acp(lambda: s.acp.store.delete(aid))
        return {"ok": True}

    @app.post("/api/acp/agents/{aid}/probe")
    async def acp_probe(aid: str, s: State = Depends(S)):
        try:
            return await s.acp.probe(aid)
        except AcpError as e:
            return {"ok": False, "error": str(e)}

    @app.get("/api/acp/sessions")
    def acp_sessions(s: State = Depends(S)):
        return [x.public() for x in s.acp.sessions.values()]

    @app.delete("/api/acp/sessions/{sid}")
    async def acp_close(sid: str, s: State = Depends(S)):
        return {"closed": await s.acp.close(sid)}

    @app.post("/api/acp/sessions/{sid}/cancel")
    async def acp_cancel(sid: str, s: State = Depends(S)):
        sess = s.acp.sessions.get(sid)
        if not sess:
            raise HTTPException(404, "no such session")
        await s.acp.cancel(sess)
        return {"ok": True}

    @app.post("/api/acp/respond/{rid}")
    def acp_respond(rid: str, body: dict, s: State = Depends(S)):
        try:
            return s.acp.respond(rid, body)
        except AcpError as e:
            raise HTTPException(404 if "no such pending" in str(e) else 422, str(e))

    @app.get("/api/acp/usage")
    def acp_usage(s: State = Depends(S)):
        return s.acp.store.usage()

    @app.post("/api/acp/chat")
    async def acp_chat(body: AcpChatIn, request: Request, s: State = Depends(S)):
        """Server-sent events: session | text | thought | tool_call | tool_update | plan | permission | question |
        plan_approval | resolved | auto | fs_write | usage | mode | ext | done | error."""
        q: asyncio.Queue = asyncio.Queue()
        holder: dict = {}

        async def work():
            try:
                sess = holder["sess"] = await s.acp.get_or_open(body.agent, body.session_id, body.workspace)
                if body.mode:
                    await s.acp.set_mode(sess, body.mode)
                q.put_nowait({"type": "session", **sess.public()})
                res = await s.acp.prompt(sess, body.message, q.put_nowait, policy=body.policy, timeout=body.timeout_s)
                q.put_nowait({"type": "done", **res})
            except AcpError as e:
                q.put_nowait({"type": "error", "message": str(e)})
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - surface anything else as a stream error, not a dropped connection
                q.put_nowait({"type": "error", "message": f"{type(e).__name__}: {e}"})
            finally:
                q.put_nowait(None)

        task = asyncio.create_task(work())

        async def stream():
            try:
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), 15)
                    except asyncio.TimeoutError:
                        yield ": keepalive" + chr(10) * 2
                        continue
                    if ev is None:
                        break
                    yield "data: " + json.dumps(ev, default=str) + chr(10) * 2
            finally:                                    # client went away mid-turn -> stop the agent
                if not task.done():
                    if holder.get("sess"):
                        await s.acp.cancel(holder["sess"])
                    task.cancel()

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/sandbox/status")
    def sandbox_status():
        return sandbox_mod.status()

    return app

