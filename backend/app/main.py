from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import re

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .budget import BudgetExceeded, BudgetGuard
from .acp_agents import AcpError, AcpManager, AcpStore
from .agents import AgentDef, AgentStore
from .telemetry import MAX_BODY, LOOPBACK, SIGNALS, Telemetry
from .chat import ChatService
from .chat_job import make_chat_work
from .agui_api import register as register_agui
from .live_api import register as register_live
from .artifacts.api import register as register_artifacts
from .artifacts.service import ArtifactService
from .apps.api import register as register_apps
from .apps.runner import AppRunner
from .apps.server import AppsServer
from .apps.store import AppStore
from .canvas.api import register as register_canvas
from .canvas.service import CanvasService
from .science.api import register as register_science
from .science.catalog import ComponentInstaller
from .science.service import ScienceService
from .uikit.api import register as register_uikit
from .uikit.service import UiKitService
from .desktop import mount_web, own_origins
from .db import Database, data_dir
from .pricing import known_models
from .harness import sandbox as sandbox_mod
from .harness.tools import ALL_TOOLS, TOOL_INFO
from .mcp_registry import McpStore, build_toolsets
from .d1_setup import register as register_d1
from .ts_setup import register as register_ts
from .rag.api import register as register_rag
from .ard.api import register as register_ard, shutdown as shutdown_ard
from .auto import AutoError, AutoRouter
from .auto_api import register as register_auto
from .settings_api import register as register_settings
from .memory.api import register as register_memory
from .memory.service import MemoryService
from .guardrails.api import register as register_guardrails
from .guardrails.service import GuardrailService
from .evals.api import register as register_evals
from .evals.service import EvalService
from .security.api import register as register_security
from .security.redteam import RedTeam
from .identity.api import register as register_identity
from .identity.service import IdentityService
from .ocsf.api import register as register_ocsf
from .ocsf.service import OcsfService
from .easy.api import register as register_easy
from .frameworks.api import register as register_frameworks
from .apiaccess.a2a import A2AMiddleware, RemoteAgents, register as register_a2a
from .apiaccess.api import register as register_access
from .claude_code.api import register as register_claude_code
from .apiaccess.keys import ApiKeys
from .datasources.api import register as register_data
from .datasources.service import DataService
from .runtimes.api import register as register_runtimes
from .sandboxes.api import register as register_sandboxes
from .runtimes.service import RuntimeService, model_resolver
from .workflows.api import register as register_workflows
from .workflows.config import WorkflowSettings
from .workflows.core import WorkflowStore
from .workflows.hooks import HookStore
from .workflows.schedule import Scheduler
from .notifications import NotificationStore, register as register_notifications
from .workflows.engine import Runner
from .workflows.sources import OpenApiSources
from .harness.policy_api import register as register_harness
from . import catalog_api, mcp_catalog, trainer as trainer_mod
from .rag.service import RagService
from . import fsbrowse
from .credits import Credits
from .issues import IssueStore
from .jobs import JobManager
from .voice import Voice, VoiceError
from .models import OLLAMA_SUGGESTIONS, forget as forget_models, list_models
from .custom_providers import discover as discover_endpoint, test_endpoint, transient as transient_endpoint
from .providers import CATALOG, ProviderStore, price_of
from .skills import SkillRegistry, covers, render_skill


def allowed_origins(env: "os._Environ[str] | dict[str, str]") -> list[str]:
    """CORS origins for the local API. The "null" origin is what file:// pages AND every sandboxed iframe (on any website) send;
    allowing it would let any web page read this local, unauthenticated API (settings, chats, keys), so it is off unless the
    packaged file:// build opts in with AGENT_STUDIO_ALLOW_NULL_ORIGIN=1."""
    out = [
        "http://localhost:3100", "http://127.0.0.1:3100",  # Next dev / static server
        "app://agent-studio",                               # Electron packaged (custom scheme)
    ] + [o.strip() for o in env.get("AGENT_STUDIO_EXTRA_ORIGINS", "").split(",") if o.strip()]   # e.g. a second dev UI port
    out += own_origins(env)                                   # desktop build: the UI is served by this server
    if env.get("AGENT_STUDIO_ALLOW_NULL_ORIGIN", "").strip().lower() in ("1", "true", "yes"):
        out.append("null")
    return out


ALLOWED_ORIGINS = allowed_origins(os.environ)


class State:
    def __init__(self, db: Database | None = None):
        self.db = db or Database()
        self.providers = ProviderStore(self.db)
        self.credits = Credits(self.db)                    # provider account balances (auto / manual / strict)
        self.guard = BudgetGuard(self.db, self.providers, self.credits)
        self.agents = AgentStore(self.db)
        self.mcp = McpStore(self.db)
        self.issues = IssueStore(self.db)                 # problems the harness raises itself
        self.mcp.issues = self.issues
        self.issues.on_new = lambda issue: _maybe_auto_debug(self, issue)
        self.skills = SkillRegistry()
        self.tel = Telemetry(self.db)                     # OpenTelemetry store + OTLP receiver (Phase 4)
        self.chat = ChatService(self.guard, self.providers, self.agents, self.mcp, self.skills, self.db, self.tel)
        self.acp = AcpManager(self.db, AcpStore(self.db), self.chat.workspace_for, self.tel)   # external ACP agents (Phase 3)
        self.chat.acp = self.acp                           # lets native agents call external agents
        self.rag = RagService(self.db, self.providers, self.guard)     # knowledge bases (RAG): stores, embeddings, search
        self.chat.rag = self.rag
        self.memory = MemoryService(self.db, self.providers, self.guard, self.rag)     # agent memory: kinds, scopes, backends, review queue
        self.chat.memory = self.memory
        self.ocsf = OcsfService(self.db)                   # security export: Agent Studio's events as OCSF to files, webhooks, S3 and syslog
        self.tel.span_listener = self.ocsf.on_span
        self.tel.event_listener = lambda event, kw: getattr(self.ocsf, event)(**kw)
        self.db.change_hook = self.ocsf.on_setting
        self.auto = AutoRouter(self.db, self.providers, self.guard)      # Auto mode: picks the provider and model per message
        self.chat.auto = self.auto
        self.guardrails = GuardrailService(self.db, self.tel)          # checks on inputs, outputs and tool calls (open-source models, cloud services, rules)
        self.guardrails.d1_ready, self.guardrails.d1_worker = self.auto.d1_ready, self.auto._worker_for      # the d1 gate shares Auto's decision-model worker
        self.chat.guardrails = self.guardrails
        self.guardrails.warm_d1()                                      # start loading the decision model now if an enabled profile uses it
        self.evals = EvalService(self.db, self.chat)                    # test sets, runs against an agent, exports for other evaluation frameworks
        self.redteam = RedTeam(self.db)                                 # opt-in red-team tools (garak, PyRIT, promptfoo) aimed at one agent
        self.identity = IdentityService(self.db, self.agents, self.tel)    # agent identities (SPIFFE-style ids, short-lived tokens, RFC 8693 delegation)
        self.chat.identity = self.identity
        self.artifacts = ArtifactService(self.db)          # files and outputs agents produce: stored in one or more places, searchable
        self.chat.artifacts = self.artifacts
        self.artifacts.resume()                            # copies that were waiting when the app last stopped
        self.canvas = CanvasService(self.db, self.artifacts)     # the document you and an agent edit together (versions, proposals, a copy as an artifact)
        self.chat.canvas = self.canvas
        self.uikit = UiKitService(self.db)                 # rich components agents can show (charts, time series, maps, cards): limits and the optional map basemap
        self.chat.uikit = self.uikit
        self.agui_threads: dict[str, str] = {}             # AG-UI thread id -> chat session id
        self.agui_cursors: dict[str, int] = {}             # chat job id -> last event an AG-UI client has seen (for resuming after an interrupt)
        self.apikeys = ApiKeys(self.db)                    # keys for the OpenAI-compatible API and A2A (only hashes are stored)
        self.apps = AppStore(self.db, keys=self.apikeys, agents=self.agents)    # apps agents build with you: files, versions, proposals and each app's own scoped API key
        self.chat.apps = self.apps
        self.app_runner = AppRunner(self.apps, policy=self.chat.sandboxes.policy, frame_ancestors=[o for o in ALLOWED_ORIGINS if o.startswith("http")])
        self.science = ScienceService(self.db, self.artifacts, self.tel)       # the Science Lab: open data sources, evidence, claims and reports (read-only, safety-screened)
        self.chat.science = self.science
        self.science_components = ComponentInstaller(self.db)          # optional science components (PaperQA2, ToolUniverse...) in their own isolated environments
        self.apps_server = AppsServer(self.app_runner)     # the separate loopback server that shows static apps (never the same origin as this API)
        self.a2a_remotes = RemoteAgents(self.db)           # other A2A agents our agents may call
        self.chat.remotes = self.a2a_remotes
        self.workflows = WorkflowStore(self.db)            # drag-and-drop workflows (versions, validation)
        self.wf_config = WorkflowSettings(self.db)         # workflow limits, webhook rules and the credentials workflow boxes sign in with
        self.wf_hooks = HookStore(self.db)                 # the secret addresses that start workflows from outside
        self.wf_sources = OpenApiSources(self.db, usage=self.workflows.uses)          # APIs imported as workflow components
        self.wf_runner = Runner(self, self.workflows, self.wf_sources)
        self.chat.workflows = self.wf_runner
        self.wf_schedule = Scheduler(self)                 # the Schedule trigger's clock (started with the server, see lifespan)
        self.notifications = NotificationStore(self.db)    # the bell: messages from Notify boxes and the scheduler (nothing leaves the computer)
        self.data = DataService(self.db)                    # read-only data sources (databases, warehouses, Salesforce), data products, audit
        self.chat.data = self.data
        self.apps.data = self.app_runner.data = self.data     # apps may be connected to data sources (read-only, audited, only the ones their manifest lists)
        self.runtimes = RuntimeService(self.db, self.agents, model_resolver(self.providers), data_dir())    # where agents run: local, Docker, AgentCore, Vertex, Foundry
        self.chat.runtimes = self.runtimes
        self.jobs = JobManager()                           # background turns (long runs survive closed tabs)
        self.chat.store.mark_interrupted()                 # turns cut off by a previous restart are shown as interrupted
        self.voice = Voice(self.db)                        # ElevenLabs text-to-speech / speech-to-text
        self.mcp_tests: dict[str, dict] = {}               # last "Test" result per MCP server (shown on its card)


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
    max_output_tokens: int | None = Field(default=None, ge=16, le=1_000_000)
    auth_mode: str | None = None
    api_key_env: str | None = None
    vertex_credentials_json: str | None = None   # None = unchanged, "" = clear (write-only)
    extra_headers: dict[str, str] | None = None  # {} clears (values write-only)
    organization: str | None = None
    aws_region: str | None = None
    aws_access_key_id: str | None = None        # None = unchanged, "" = clear (write-only)
    aws_secret_access_key: str | None = None    # write-only
    aws_session_token: str | None = None        # write-only
    aws_profile: str | None = None
    context_window: int | None = None           # Ollama: tokens of context (num_ctx)
    label: str | None = None                    # custom endpoints only
    model_prices: dict | None = None            # custom endpoints: {"*" or model: {input_per_m, output_per_m, context}}
    manual_models: list[str] | None = None      # custom endpoints: model names typed by hand


class CustomProviderIn(BaseModel):
    label: str
    api_base: str
    default_model: str = ""
    auth_mode: str = "api_key"
    api_key: str | None = None                  # write-only
    api_key_env: str | None = None
    headers: dict[str, str] | None = None       # write-only values
    organization: str | None = None
    cap_usd: float = Field(default=1.0, ge=0)
    cap_period: str = "total"
    max_output_tokens: int = Field(default=4096, ge=16, le=1_000_000)
    model_prices: dict | None = None
    manual_models: list[str] | None = None
    enabled: bool = True


class EndpointProbeIn(BaseModel):               # an address that is not saved yet (the "Find models" button of the add form)
    api_base: str
    auth_mode: str = "none"
    api_key: str | None = None
    api_key_env: str | None = None
    headers: dict[str, str] | None = None
    organization: str | None = None


class EndpointTestIn(BaseModel):
    model: str | None = None


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
    can_spawn: bool | None = None
    acp_agents: list[str] | None = None
    acp_policy: str | None = None
    rag: list[str] | None = None                    # knowledge base ids this agent may search; ["*"] = all
    memory: dict | None = None                      # {types, read_scopes, write_scopes, auto_recall, auto_save}; {} = memory off
    guardrails: list[str] | None = None             # guardrail profile ids; [] = the default profiles, ["none"] = no guardrails for this agent
    identity: dict | None = None                    # {scopes, delegates}; {} = the defaults
    framework: str | None = None                    # adk | langgraph | strands | pydantic_ai | openai_agents | ms_agent | claude_sdk
    api_access: bool | None = None                  # may be called through the OpenAI-compatible API (/v1)
    a2a: bool | None = None                         # may be called by other agents over A2A
    runtime: str | None = None                      # local | docker | agentcore | vertex | foundry (where it runs; see /api/runtimes)
    sandbox: str | None = None                      # "" = the default sandbox | docker-container | docker-microvm
    data: list[str] | None = None                   # data source ids this agent may read; ["*"] = all
    live: dict | None = None                        # Live profile {enabled, engine, mode, voice, language, avatar_name, agent_turns, budget_usd} (see live_profile.py)
    agui: dict | None = None                        # {components, endpoint}: rich components in its answers / the AG-UI stream endpoint (both on by default)


class McpPatch(BaseModel):
    enabled: bool | None = None
    env: dict[str, str] | None = None


class McpAdd(BaseModel):
    id: str
    label: str = ""
    description: str = ""
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = []
    url: str | None = None
    env: dict[str, str] = {}
    enabled: bool = True


class SkillIn(BaseModel):
    name: str
    description: str
    body: str
    mcp: list[str] = []
    overwrite: bool = False
    group: str = "General"


class SkillImport(BaseModel):
    text: str
    name: str | None = None
    overwrite: bool = False


class SkillDup(BaseModel):
    name: str | None = None


class VoicePatch(BaseModel):
    api_key: str | None = None          # write-only; "" clears
    voice_id: str | None = None
    tts_model: str | None = None
    stt_model: str | None = None
    auto_read: bool | None = None
    dictation: bool | None = None
    char_cap: int | None = Field(default=None, ge=0)


class TtsIn(BaseModel):
    text: str
    voice_id: str | None = None


class PickIn(BaseModel):
    initial: str | None = None


class CreditsPatch(BaseModel):
    mode: str | None = None              # auto | manual | off
    balance_usd: float | None = Field(default=None, ge=0)
    strict: bool | None = None


class PullIn(BaseModel):
    model: str = Field(min_length=1, max_length=200)


class OpenApiIn(BaseModel):
    id: str = Field(min_length=2, max_length=31)
    label: str = Field(min_length=1, max_length=80)
    spec: str = Field(min_length=3, max_length=2000)          # an absolute file path, or an https URL
    base_url: str | None = None
    allow_write: bool = False                                  # POST/PUT/PATCH/DELETE operations
    headers: dict[str, str] = {}                               # e.g. {"Authorization": "Bearer …"} - stored locally, never shown


class MemEntityPatch(BaseModel):
    observations: list[str] | None = None
    entityType: str | None = None


class BrowserIn(BaseModel):
    browser: str = "chromium"
    headless: bool = True
    isolated: bool = True
    viewport: str = "1280x720"
    allowed_origins: list[str] = []
    blocked_origins: list[str] = []


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    mode: str = "queue"                          # queue | steer


class ChatRename(BaseModel):
    title: str


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
    month_cost_cap: float | None = Field(default=None, ge=0)        # USD, on the cost the agent reports (0 = none)
    month_turn_cap: int | None = Field(default=None, ge=0)
    month_minutes_cap: float | None = Field(default=None, ge=0)


class AcpAgentCreate(BaseModel):
    id: str
    label: str = ""
    command: str
    args: list[str] = []
    notes: str = ""


class SettingsPatch(BaseModel):
    workspace_dir: str | None = Field(default=None, max_length=500)
    sandbox_network: bool | None = None
    max_llm_calls_per_turn: int | None = Field(default=None, ge=1, le=100000)
    max_turn_minutes: float | None = Field(default=None, ge=0, le=100000)      # 0 = no wall-clock limit
    agents_can_call_external: bool | None = None
    thinking_level: str | None = None              # off | low | medium | high
    auto_debug_mcp: bool | None = None
    model_fallbacks: list[dict] | None = None
    max_external_calls_per_turn: int | None = Field(default=None, ge=1, le=1000)
    external_timeout_min: float | None = Field(default=None, ge=1, le=100000)
    agent_spawning_enabled: bool | None = None
    max_spawns_per_turn: int | None = Field(default=None, ge=1, le=20)
    sidebot_enabled: bool | None = None
    sidebot_page_context: bool | None = None       # may the assistant read what is on the screen (never secrets)?
    sidebot_provider: str | None = Field(default=None, max_length=60)            # "" = the first enabled provider
    sidebot_model: str | None = Field(default=None, max_length=200)               # "" = that provider's default model
    sidebot_guidance: str | None = Field(default=None, max_length=4000)     # "" = the default guidance; added AFTER the fixed safety rules
    sidebot_tools_off: list[str] | None = None     # assistant tools switched off (refused server-side too)


class OtelPatch(BaseModel):
    enabled: bool | None = None
    inject_env: bool | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    receiver_url: str | None = Field(default=None, max_length=300)
    forward_url: str | None = Field(default=None, max_length=300)
    forward_headers: dict[str, str] | None = None


DEBUG_PROMPT = " ".join([
    "An MCP tool server failed to start in Agent Studio.",
    "Server: {label} (id: {source}).",
    "What the harness saw: {detail}",
    "Work out why it fails. Check the command and arguments are valid on this PC (is the executable installed? is the version pinned",
    "and does it exist?), whether required environment variables are missing, and whether the server speaks MCP over stdio.",
    "Do not change files outside the Agent Studio data. Report: (1) the most likely cause in plain words, (2) the evidence,",
    "(3) the smallest fix the user can make in Agent Studio (MCP servers page: command, args, environment, or the Test button), and",
    "(4) what you could not verify.",
])


def start_debug_run(s: "State", issue: dict) -> str | None:
    """Start a background debugging run with the Super Engineer. Returns the job id (or None if nothing is enabled)."""
    if issue.get("debug_job"):
        j = s.jobs.jobs.get(issue["debug_job"])
        if j is not None and j.ended is None:
            return j.id
    provs = [p for p in s.providers.list() if p.enabled and (p.kind != "mock" or __import__("app.providers", fromlist=["x"]).mock_enabled())]
    if not provs:
        return None
    pid = provs[0].id
    prompt = DEBUG_PROMPT.format(label=issue.get("title", "").replace("MCP server '", "").split("' failed")[0], source=issue["source"],
                                 detail=issue.get("detail") or "(no detail)")

    async def work(emit, inbox):
        try:
            res = await s.chat.send(pid, None, prompt, None, agent_id="super_engineer", emit=emit, inbox=inbox)
            emit({"type": "done", **res})
        except Exception as e:  # noqa: BLE001
            emit({"type": "error", "message": f"Debug run failed: {type(e).__name__}: {e}"})

    job = s.jobs.start(work)
    s.issues.set_debug_job(issue["id"], job.id)
    return job.id


def _maybe_auto_debug(s: "State", issue: dict) -> None:
    if issue.get("kind") == "mcp_failed" and s.db.get_setting("auto_debug_mcp", "0") == "1":
        start_debug_run(s, issue)


def create_app(state: State | None = None) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(a: FastAPI):
        a.state.s.wf_schedule.start()        # scheduled workflows (catches up once on a time missed while the app was closed)
        yield
        await a.state.s.wf_schedule.stop()
        a.state.s.ocsf.close()               # send what is still queued
        a.state.s.redteam.close()            # red-team tools cannot outlive the address they attack
        a.state.s.app_runner.stop_all()      # containers of running server apps
        a.state.s.apps_server.stop()         # the apps preview server
        shutdown_ard(a)                      # the optional discovery server (a separate loopback listener)
        await a.state.s.jobs.shutdown()     # running turns are cancelled cleanly: progress saved, reservations settled
        await a.state.s.acp.close_all()     # never leave agent subprocesses behind
        a.state.s.acp.stop()

    app = FastAPI(title="Agent Studio", version="0.1.1", lifespan=lifespan)
    app.state.s = state or State()
    app.add_middleware(A2AMiddleware)    # /a2a/{agent}/...: A2A protocol, behind the API-key check (added first = innermost: the host check below still runs before it)
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
        return [dict(p.public(), catalog=dict(CATALOG[p.kind]), credits=s.credits.public(p)) for p in s.providers.list()]

    @app.post("/api/providers/custom")
    async def create_custom_provider(body: CustomProviderIn, s: State = Depends(S)):
        """Add an OpenAI-compatible endpoint (vLLM, LM Studio, a gateway...). The key and header values are write-only."""
        try:
            p = s.providers.create_custom(body.label, body.api_base, body.default_model, body.auth_mode, body.api_key, body.api_key_env, body.headers,
                                          body.organization, body.cap_usd, body.cap_period, body.max_output_tokens, body.model_prices,
                                          body.manual_models, body.enabled)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if body.api_key or body.headers:
            s.tel.emit("entity", activity_id=3, kind="credential", name=p.id, secret=True)         # which credential, never its value
        return dict(p.public(), catalog=dict(CATALOG[p.kind]), credits=s.credits.public(p))

    @app.post("/api/providers/custom/discover")
    async def discover_custom_models(body: EndpointProbeIn):
        """List the models an address serves, before anything is saved."""
        try:
            probe = transient_endpoint(body.api_base, body.auth_mode, body.api_key, body.api_key_env, body.headers, body.organization)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return await discover_endpoint(probe)

    @app.delete("/api/providers/{pid}")
    def delete_provider(pid: str, s: State = Depends(S)):
        try:
            s.providers.delete_custom(pid)
        except KeyError:
            raise HTTPException(404, "unknown provider")
        except ValueError as e:
            raise HTTPException(422, str(e))
        forget_models(pid)
        return {"ok": True}

    @app.post("/api/providers/{pid}/test")
    async def test_provider(pid: str, body: EndpointTestIn, s: State = Depends(S)):
        """One tiny chat call to a custom endpoint, with the exact reason when it fails."""
        p = s.providers.get(pid)
        if p is None or not p.custom:
            raise HTTPException(404, "not a custom endpoint")
        return await test_endpoint(p, body.model, s.guard)

    @app.get("/api/providers/{pid}/ollama/suggestions")
    def ollama_suggestions():
        return OLLAMA_SUGGESTIONS

    @app.post("/api/providers/{pid}/pull")
    async def ollama_pull(pid: str, body: PullIn, s: State = Depends(S)):
        """Download a model into the local Ollama (SSE progress: status, completed, total; then done | error)."""
        p = s.providers.get(pid)
        if p is None or p.kind != "ollama":
            raise HTTPException(404, "not an Ollama provider")
        name = body.model.strip()
        if not re.match(r"^[A-Za-z0-9][\w.\-/:]*$", name):
            raise HTTPException(422, "invalid model name")
        base = (p.api_base or "http://127.0.0.1:11434").rstrip("/")

        async def gen():
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10)) as c:
                    async with c.stream("POST", f"{base}/api/pull", json={"model": name, "name": name, "stream": True}) as r:
                        if r.status_code >= 400:
                            yield {"type": "error", "message": f"Ollama {r.status_code}: {(await r.aread()).decode()[:200]}"}
                            return
                        async for line in r.aiter_lines():
                            if not line.strip():
                                continue
                            d = json.loads(line)
                            if d.get("error"):
                                yield {"type": "error", "message": d["error"]}
                                return
                            yield {"type": "progress", "status": d.get("status"), "completed": d.get("completed"), "total": d.get("total")}
                forget_models(pid)
                yield {"type": "done", "model": name}
            except httpx.HTTPError as e:
                yield {"type": "error", "message": f"Ollama isn't reachable at {base} ({type(e).__name__}). Start the Ollama app."}
        return _sse(gen())

    @app.get("/api/providers/{pid}/credits")
    def provider_credits(pid: str, s: State = Depends(S)):
        p = s.providers.get(pid)
        if p is None:
            raise HTTPException(404, "unknown provider")
        return s.credits.public(p)

    @app.put("/api/providers/{pid}/credits")
    def provider_credits_update(pid: str, body: CreditsPatch, s: State = Depends(S)):
        p = s.providers.get(pid)
        if p is None:
            raise HTTPException(404, "unknown provider")
        try:
            s.credits.update(p, body.mode, body.balance_usd, body.strict)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return s.credits.public(p)

    @app.post("/api/providers/{pid}/credits/refresh")
    async def provider_credits_refresh(pid: str, s: State = Depends(S)):
        p = s.providers.get(pid)
        if p is None:
            raise HTTPException(404, "unknown provider")
        return await s.credits.refresh(p)

    @app.get("/api/providers/{pid}/models")
    async def provider_models(pid: str, refresh: bool = False, s: State = Depends(S)):
        """Models available to this provider, loaded live from its API (falls back to LiteLLM's catalog)."""
        p = s.providers.get(pid)
        if p is None:
            raise HTTPException(404, "unknown provider")
        return await list_models(p, refresh)

    @app.put("/api/providers/{pid}")
    async def update_provider(pid: str, patch: ProviderPatch, s: State = Depends(S)):
        try:
            p = s.providers.update(pid, patch.model_dump(exclude_unset=True))
        except KeyError:
            raise HTTPException(404, "unknown provider")
        except ValueError as e:
            raise HTTPException(422, str(e))
        sent = patch.model_dump(exclude_unset=True)
        if any(sent.get(k) for k in ("api_key", "aws_access_key_id", "aws_secret_access_key", "aws_session_token", "vertex_credentials_json", "extra_headers")):
            s.tel.emit("entity", activity_id=3, kind="credential", name=pid, secret=True)         # which credential changed, never its value
        if p.enabled and p.auth_mode is not None or patch.api_key:   # connecting: grab the account balance where the API allows
            await s.credits.refresh(p)
        return dict(p.public(), credits=s.credits.public(p))

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
        price = price_of(p, model, lm)
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

    def _sse(gen):
        async def stream():
            async for ev in gen:
                yield (": keepalive" if ev is None else "data: " + json.dumps(ev, default=str)) + chr(10) * 2
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/chat/stream")
    async def chat_stream(body: ChatIn, s: State = Depends(S)):
        """Start a chat turn as a BACKGROUND JOB and stream its events (SSE): job | session | text | tool_call | tool_result |
        model_call | agent_* | external_* | done | refused | error | end. Closing the connection does NOT stop the turn:
        re-attach with GET /api/chat/jobs/{id}/events?after=<seq>; stop it with POST /api/chat/jobs/{id}/cancel."""
        if body.session_id and (busy := s.jobs.active_for(body.session_id)):
            raise HTTPException(409, f"this chat is still running (job {busy.id}); wait for it or stop it first")

        work = make_chat_work(s, body)

        job = s.jobs.start(work, float(s.db.get_setting("max_turn_minutes", "0") or 0))
        return _sse(s.jobs.follow(job))

    @app.get("/api/chat/jobs")
    def chat_jobs(s: State = Depends(S)):
        return [j.public() for j in s.jobs.jobs.values()]

    @app.get("/api/chat/jobs/{jid}/events")
    async def chat_job_events(jid: str, after: int = -1, s: State = Depends(S)):
        job = s.jobs.jobs.get(jid)
        if job is None:
            raise HTTPException(404, "unknown or expired job (finished runs are in the chat history)")
        return _sse(s.jobs.follow(job, after))

    @app.post("/api/chat/jobs/{jid}/notes")
    def chat_job_note(jid: str, body: NoteIn, s: State = Depends(S)):
        """Send a note to a RUNNING chat: queue (read at the next step) or steer (interrupts the step in progress)."""
        job = s.jobs.jobs.get(jid)
        if job is None or job.ended is not None or job.inbox is None:
            raise HTTPException(409, "this run has finished - send your message as a new message")
        try:
            return job.inbox.push(body.text, body.mode)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/chat/jobs/{jid}/notes/{nid}")
    def chat_job_note_retract(jid: str, nid: str, s: State = Depends(S)):
        job = s.jobs.jobs.get(jid)
        if job is None or job.inbox is None or not job.inbox.retract(nid):
            raise HTTPException(404, "that note was already read by the agent (or the run finished)")
        return {"ok": True}

    # ---- memory (what agents remember across chats) ----
    from . import memory_store as MS

    @app.get("/api/memory")
    def memory_summary():
        return MS.summary()

    @app.get("/api/memory/entities")
    def memory_entities(q: str | None = None, type: str | None = None, limit: int = 200):
        return MS.list_entities(q, type, max(1, min(limit, 1000)))

    @app.put("/api/memory/entities/{name}")
    def memory_update(name: str, body: MemEntityPatch):
        try:
            return MS.update_entity(name, body.observations, body.entityType)
        except KeyError:
            raise HTTPException(404, "unknown memory")

    @app.delete("/api/memory/entities/{name}")
    def memory_delete(name: str):
        try:
            return MS.delete_entity(name)
        except KeyError:
            raise HTTPException(404, "unknown memory")

    @app.delete("/api/memory/entities/{name}/observations/{index}")
    def memory_delete_obs(name: str, index: int):
        try:
            return MS.delete_observation(name, index)
        except KeyError:
            raise HTTPException(404, "unknown memory")
        except IndexError:
            raise HTTPException(404, "no such observation")

    @app.post("/api/memory/clear")
    def memory_clear(body: dict):
        if body.get("confirm") != "DELETE ALL MEMORY":
            raise HTTPException(422, 'send {"confirm": "DELETE ALL MEMORY"} to erase everything (a backup is kept)')
        return MS.clear_all()

    @app.get("/api/issues")
    def issues_list(status: str = "open", s: State = Depends(S)):
        return s.issues.list(None if status == "all" else status)

    @app.post("/api/issues/{iid}/debug")
    async def issue_debug(iid: str, s: State = Depends(S)):          # async: the job is scheduled on the running event loop
        issue = s.issues.get(iid)
        if issue is None:
            raise HTTPException(404, "unknown issue")
        job_id = start_debug_run(s, issue)
        if job_id is None:
            raise HTTPException(409, "enable a model provider first - the debugging agent needs one")
        return {"job_id": job_id}

    @app.post("/api/issues/{iid}/resolve")
    def issue_resolve(iid: str, s: State = Depends(S)):
        if not s.issues.resolve(iid):
            raise HTTPException(404, "unknown issue")
        return {"ok": True}

    @app.get("/api/chat/sessions/{sid}/context")
    def chat_context(sid: str, s: State = Depends(S)):
        """Context-window usage for this chat: how full the model's window is, and whether it has been summarised."""
        sess = s.chat.store.get(sid)
        if sess is None:
            raise HTTPException(404, "unknown chat")
        return s.chat.context_info(sid, sess["provider"], sess.get("model"))

    @app.post("/api/chat/sessions/{sid}/summarize")
    async def chat_summarize(sid: str, s: State = Depends(S)):
        """Summarise the conversation so far and resume from the summary (the full history stays visible)."""
        if s.jobs.active_for(sid):
            raise HTTPException(409, "wait for the running turn to finish before summarising")
        try:
            return await s.chat.summarize(sid)
        except KeyError:
            raise HTTPException(404, "unknown chat or provider")
        except ValueError as e:
            raise HTTPException(422, str(e))
        except BudgetExceeded:
            raise
        except Exception as e:  # noqa: BLE001
            from .errors import as_event
            raise HTTPException(502, as_event(e).get("message", str(e)))

    @app.post("/api/chat/jobs/{jid}/cancel")
    def chat_job_cancel(jid: str, s: State = Depends(S)):
        if not s.jobs.cancel(jid):
            raise HTTPException(404, "no running job with that id")
        return {"ok": True}

    # ---- saved chats ----
    @app.get("/api/chat/sessions")
    def chat_sessions(limit: int = 100, s: State = Depends(S)):
        running = s.jobs.running_sessions()
        return [dict(r, running=r["id"] in running) for r in s.chat.store.list(max(1, min(limit, 500)))]

    @app.get("/api/chat/sessions/{sid}")
    def chat_session(sid: str, s: State = Depends(S)):
        sess = s.chat.store.get(sid)
        if sess is None:
            raise HTTPException(404, "unknown chat")
        job = s.jobs.active_for(sid)
        return dict(session=sess, messages=s.chat.store.messages(sid), active_job=job.public() if job else None)

    @app.put("/api/chat/sessions/{sid}")
    def chat_rename(sid: str, body: ChatRename, s: State = Depends(S)):
        if not s.chat.store.rename(sid, body.title):
            raise HTTPException(404, "unknown chat")
        return {"ok": True}

    @app.delete("/api/chat/sessions/{sid}")
    async def chat_delete(sid: str, s: State = Depends(S)):
        if not s.chat.store.delete(sid):
            raise HTTPException(404, "unknown chat")
        with contextlib.suppress(Exception):          # also drop the live in-memory ADK session
            await s.chat.sessions.delete_session(app_name="agent_studio", user_id="local", session_id=sid)
        return {"ok": True}

    # ---- agents ----
    @app.get("/api/agents")
    def list_agents(s: State = Depends(S)):
        return [a.__dict__ for a in s.agents.list()]

    @app.post("/api/agents")
    def create_agent(body: AgentIn, s: State = Depends(S)):
        try:
            d = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
            d.setdefault("agui", s.uikit.config()["new_agents"])           # a new agent starts with the default chosen in Settings
            made = s.agents.create(AgentDef(id=d.pop("id", ""), name=d.pop("name", ""), **d))
            s.tel.emit("entity", activity_id=1, kind="agent", name=made.id)
            return made.__dict__
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.put("/api/agents/{aid}")
    def update_agent(aid: str, body: AgentIn, s: State = Depends(S)):
        try:
            changed = s.agents.update(aid, body.model_dump(exclude_unset=True))
            s.tel.emit("entity", activity_id=3, kind="agent", name=aid, detail={"fields": ", ".join(sorted(body.model_dump(exclude_unset=True)))})
            return changed.__dict__
        except KeyError:
            raise HTTPException(404, "unknown agent")
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/agents/{aid}")
    def delete_agent(aid: str, s: State = Depends(S)):
        try:
            s.agents.delete(aid)
            s.tel.emit("entity", activity_id=4, kind="agent", name=aid)
        except KeyError:
            raise HTTPException(404, "unknown agent")
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"ok": True}

    @app.get("/api/tools")
    def list_tools():
        return [dict(name=n, description=d, read_only=ro) for n, (d, ro) in TOOL_INFO.items()]

    # ---- MCP ----
    MCP_CATEGORY = {"filesystem": "Files & code", "git": "Files & code", "github": "Files & code",
                    "semgrep": "Quality & security", "eslint": "Quality & security", "snyk": "Quality & security",
                    "fetch": "Web & docs", "brave": "Web & docs", "context7": "Web & docs", "playwright": "Web & docs",
                    "memory": "Memory & voice", "elevenlabs": "Memory & voice", "d1": "Local models", "timeseries": "Local models", "trainer": "Local models", "cloudml": "Cloud"}

    @app.get("/api/mcp")
    def list_mcp(s: State = Depends(S)):
        agents = s.agents.list()
        catalog_ids = mcp_catalog.installed(s.mcp)
        out = []
        for m in s.mcp.list():
            d = m.public()
            d["category"] = MCP_CATEGORY.get(m.id, "Agent-proposed" if m.status == "pending_approval" else "Catalog" if m.id in catalog_ids else "Yours")
            d["used_by"] = [dict(id=a.id, name=a.name) for a in agents if m.id in a.mcp]
            d["last_test"] = s.mcp_tests.get(m.id)
            out.append(d)
        return out

    @app.put("/api/mcp/{sid}")
    def update_mcp(sid: str, body: McpPatch, s: State = Depends(S)):
        try:
            return s.mcp.update(sid, body.enabled, body.env).public()
        except KeyError:
            raise HTTPException(404, "unknown MCP server")

    @app.post("/api/mcp")
    def add_mcp(body: McpAdd, s: State = Depends(S)):
        try:
            return s.mcp.add(body.id, body.label, body.description, body.transport, body.command, body.args, body.url, body.env, body.enabled).public()
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/browser/settings")
    def browser_get(s: State = Depends(S)):
        return s.mcp.browser_settings()

    @app.put("/api/browser/settings")
    def browser_put(body: BrowserIn, s: State = Depends(S)):
        try:
            cfg = s.mcp.set_browser_settings(body.model_dump())
        except ValueError as e:
            raise HTTPException(422, str(e))
        return cfg

    @app.post("/api/mcp/openapi")
    def add_openapi_mcp(body: OpenApiIn, s: State = Depends(S)):
        """Load an OpenAPI document as an MCP server: its operations become tools (read-only unless writes are allowed)."""
        from . import mcp_openapi
        spec = body.spec.strip()
        if not spec.startswith(("http://", "https://")) and not Path(spec).expanduser().is_absolute():
            raise HTTPException(422, "use an absolute file path (e.g. C:/specs/api.yaml) or an https URL")
        if spec.startswith("http://") and not spec.startswith("http://127.0.0.1") and not spec.startswith("http://localhost"):
            raise HTTPException(422, "use https for remote specs")
        if not spec.startswith(("http://", "https://")):
            spec = str(Path(spec).expanduser().resolve())
        try:                                                   # check it is a real OpenAPI document before saving it
            mcp_openapi.configure(spec, body.base_url or None)
            n = len(mcp_openapi._ops)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"could not load that OpenAPI document: {e}")
        env = {"PYTHONPATH": "{backend}", "OPENAPI_ALLOW_WRITE": "1" if body.allow_write else "0"}
        for k, v in body.headers.items():
            env["HEADER_" + re.sub(r"[^A-Za-z0-9]+", "_", k).strip("_").upper()] = v
        args = ["-m", "app.mcp_openapi", spec] + (["--base", body.base_url] if body.base_url else [])
        try:
            m = s.mcp.add(body.id, body.label, f"{n} operations from an OpenAPI document ({'read & write' if body.allow_write else 'read-only'}).",
                          "stdio", "{python}", args, None, env, enabled=True)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {**m.public(), "operations": n}

    @app.post("/api/mcp/{sid}/test")
    async def test_mcp(sid: str, s: State = Depends(S)):
        """Start the server, list its tools, stop it. Uses the default workspace."""
        if not s.mcp.get(sid):
            raise HTTPException(404, "unknown MCP server")
        warns: list[str] = []
        try:
            sets = await asyncio.wait_for(build_toolsets(s.mcp, [sid], str(s.chat.workspace_for(None)), warns), 150)
        except asyncio.TimeoutError:
            s.issues.add("mcp_failed", sid, f"MCP server '{s.mcp.get(sid).label}' timed out starting",
                         detail="no answer within 150 seconds", suggestion="The first run may be downloading the server: try Test again. If it keeps timing out, check the command runs in a terminal.",
                         action={"type": "debug_mcp", "mcp": sid})
            return {"ok": False, "error": "timed out starting the server (first run may be downloading it - try again)"}
        if not sets:
            s.mcp_tests[sid] = dict(ok=False, error=("; ".join(warns) or "did not start")[:300], ts=__import__("time").time())
            return {"ok": False, "error": "; ".join(warns) or "did not start"}
        try:
            tools = await sets[0].get_tools()
            res = {"ok": True, "tools": [dict(name=t.name, description=(getattr(t, "description", "") or "")[:300]) for t in tools]}
            s.mcp_tests[sid] = dict(ok=True, tools=len(tools), names=[t["name"] for t in res["tools"]][:40], ts=__import__("time").time())
            return res
        finally:
            with contextlib.suppress(Exception):
                await sets[0].close()

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
    def _skill_view(s: State, k, with_body: bool = False) -> dict:
        d = k.public(with_body=with_body)
        d["used_by"] = [a.id for a in s.agents.list() if covers(a.skills, k)]
        return d

    @app.get("/api/skills")
    def list_skills(s: State = Depends(S)):
        return [_skill_view(s, k) for k in s.skills.list()]

    @app.get("/api/skill-groups")
    def skill_groups(s: State = Depends(S)):
        return s.skills.groups()

    @app.get("/api/skills/{name}")
    def get_skill(name: str, s: State = Depends(S)):
        k = s.skills.get(name)
        if not k:
            raise HTTPException(404, "unknown skill")
        return _skill_view(s, k, with_body=True)

    @app.get("/api/skills/{name}/export")
    def export_skill(name: str, s: State = Depends(S)):
        k = s.skills.get(name)
        if not k:
            raise HTTPException(404, "unknown skill")
        return Response(render_skill(k), media_type="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="{k.name}-SKILL.md"'})

    @app.post("/api/skills")
    def create_skill(body: SkillIn, s: State = Depends(S)):
        try:
            return _skill_view(s, s.skills.create(body.name, body.description, body.body, body.mcp, body.overwrite, body.group), True)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post("/api/skills/import")
    def import_skill(body: SkillImport, s: State = Depends(S)):
        try:
            return _skill_view(s, s.skills.import_text(body.text, body.name, body.overwrite), True)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post("/api/skills/{name}/duplicate")
    def duplicate_skill(name: str, body: SkillDup | None = None, s: State = Depends(S)):
        try:
            return _skill_view(s, s.skills.duplicate(name, body.name if body else None), True)
        except KeyError:
            raise HTTPException(404, "unknown skill")
        except ValueError as e:
            raise HTTPException(422, str(e))

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
                    max_llm_calls_per_turn=int(s.db.get_setting("max_llm_calls_per_turn", "40")),
                    agent_spawning_enabled=s.db.get_setting("agent_spawning_enabled", "1") == "1",
                    max_spawns_per_turn=int(s.db.get_setting("max_spawns_per_turn", "4")),
                    max_turn_minutes=float(s.db.get_setting("max_turn_minutes", "0") or 0),
                    agents_can_call_external=s.db.get_setting("agents_can_call_external", "1") == "1",
                    thinking_level=s.db.get_setting("thinking_level", "medium"),
                    auto_debug_mcp=s.db.get_setting("auto_debug_mcp", "0") == "1",
                    model_fallbacks=json.loads(s.db.get_setting("model_fallbacks", "[]") or "[]"),
                    max_external_calls_per_turn=int(s.db.get_setting("max_external_calls_per_turn", "6")),
                    external_timeout_min=float(s.db.get_setting("external_timeout_min", "120")),
                    **_sidebot_settings(s))

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
        if body.agent_spawning_enabled is not None:
            s.db.set_setting("agent_spawning_enabled", "1" if body.agent_spawning_enabled else "0")
        if body.max_spawns_per_turn is not None:
            s.db.set_setting("max_spawns_per_turn", str(body.max_spawns_per_turn))
        for k in ("max_turn_minutes", "max_external_calls_per_turn", "external_timeout_min"):
            if getattr(body, k) is not None:
                s.db.set_setting(k, str(getattr(body, k)))
        if body.auto_debug_mcp is not None:
            s.db.set_setting("auto_debug_mcp", "1" if body.auto_debug_mcp else "0")
        if body.model_fallbacks is not None:
            clean = [{"provider": str(f.get("provider")), "model": (str(f["model"]) if f.get("model") else None)}
                     for f in body.model_fallbacks if isinstance(f, dict) and f.get("provider")]
            s.db.set_setting("model_fallbacks", json.dumps(clean[:5]))
        if body.thinking_level is not None:
            if body.thinking_level not in ("off", "low", "medium", "high"):
                raise HTTPException(422, "thinking_level must be off, low, medium or high")
            s.db.set_setting("thinking_level", body.thinking_level)
        if body.agents_can_call_external is not None:
            s.db.set_setting("agents_can_call_external", "1" if body.agents_can_call_external else "0")
        if body.sidebot_enabled is not None:
            s.db.set_setting("sidebot_enabled", "1" if body.sidebot_enabled else "0")
        if body.sidebot_page_context is not None:
            s.db.set_setting("sidebot_page_context", "1" if body.sidebot_page_context else "0")
        if body.sidebot_provider is not None:
            if body.sidebot_provider and s.providers.get(body.sidebot_provider) is None:
                raise HTTPException(422, f"unknown provider '{body.sidebot_provider}'")
            s.db.set_setting("sidebot_provider", body.sidebot_provider)
        if body.sidebot_model is not None:
            s.db.set_setting("sidebot_model", body.sidebot_model.strip())
        if body.sidebot_guidance is not None:
            s.db.set_setting("sidebot_guidance", body.sidebot_guidance.strip())
        if body.sidebot_tools_off is not None:
            from .sidebot import SIDEBOT_TOOLS
            bad = [t for t in body.sidebot_tools_off if t not in SIDEBOT_TOOLS]
            if bad:
                raise HTTPException(422, f"unknown assistant tools: {bad}")
            s.db.set_setting("sidebot_tools_off", json.dumps(sorted(set(body.sidebot_tools_off))))
        return _settings(s)

    def _sidebot_off(s: State) -> frozenset[str]:
        try:
            return frozenset(json.loads(s.db.get_setting("sidebot_tools_off", "[]") or "[]"))
        except ValueError:
            return frozenset()

    def _sidebot_settings(s: State) -> dict:
        from . import sidebot as sb
        pid, model = _sidebot_target(s)
        guidance = s.db.get_setting("sidebot_guidance", "") or ""
        off = _sidebot_off(s)
        return dict(sidebot_guidance=guidance, sidebot_tools=[dict(name=n, description=d, enabled=n not in off) for n, d in sb.SIDEBOT_TOOLS.items()],
                    sidebot_prompt=dict(core=sb.CORE_RULES, default_guidance=sb.DEFAULT_GUIDANCE, effective=sb.compose_prompt(guidance), max_guidance=sb.GUIDANCE_MAX),
                    sidebot_enabled=s.db.get_setting("sidebot_enabled", "1") == "1",
                    sidebot_page_context=s.db.get_setting("sidebot_page_context", "1") == "1",
                    sidebot_provider=s.db.get_setting("sidebot_provider", "") or "",
                    sidebot_model=s.db.get_setting("sidebot_model", "") or "",
                    sidebot_resolved={"provider": pid, "model": model})

    def _sidebot_target(s: State) -> tuple[str | None, str | None]:
        """Which provider and model the side agent uses right now (Settings, else the first enabled provider)."""
        pid = s.db.get_setting("sidebot_provider", "") or ""
        p = s.providers.get(pid) if pid else None
        if p is None:
            p = next((x for x in s.providers.list() if x.enabled and x.kind != "mock"), None)
        if p is None:
            return None, None
        return p.id, (s.db.get_setting("sidebot_model", "") or p.default_model or None)

    @app.post("/api/sidebot")
    async def sidebot_run(request: Request, s: State = Depends(S)):
        """AG-UI endpoint for the side agent: posts a RunAgentInput, streams AG-UI events (SSE)."""
        from . import sidebot
        from .providers import litellm_target
        try:
            inp = sidebot.parse_run_input(await request.json())
        except ValueError as e:
            raise HTTPException(422, str(e))
        if s.db.get_setting("sidebot_enabled", "1") != "1":
            raise HTTPException(409, "the side agent is switched off in Settings")
        pid, model = _sidebot_target(s)
        p = s.providers.get(pid) if pid else None
        if p is None:
            raise HTTPException(422, "no provider for the side agent: add one in Settings → Providers, or pick one in Settings → Side agent")
        try:
            target, kw = litellm_target(p, model)
        except ValueError as e:
            raise HTTPException(422, str(e))
        import litellm

        return StreamingResponse(sidebot.encode(sidebot.stream_events(inp, target, kw, litellm.acompletion,
                                                                      sidebot.compose_prompt(s.db.get_setting("sidebot_guidance", "") or ""), _sidebot_off(s))),
                                 media_type="text/event-stream", headers=sidebot.sse_headers())

    # ---- dashboard ----
    @app.get("/api/dashboard")
    def dashboard(days: int = 30, s: State = Depends(S)):
        import calendar
        import time as _t
        days = max(7, min(days, 365))
        now = _t.time()
        g = _t.gmtime()
        month_start = calendar.timegm((g.tm_year, g.tm_mon, 1, 0, 0, 0))
        provs = []
        for p in s.providers.list():
            st = s.guard.status(p)
            if not p.enabled and st["spent_usd"] == 0:
                continue
            provs.append(dict(st, kind=p.kind, auth_ready=p.public()["auth_ready"], credits=s.credits.public(p)))
        rows = s.db.query("SELECT CAST((ts - ?) / 86400 AS INTEGER) d, SUM(cost_usd) c, COUNT(*) n, SUM(input_tokens) i, SUM(output_tokens) o "
                          "FROM ledger WHERE kind='call' AND ts >= ? GROUP BY d", (now - days * 86400, now - days * 86400))
        by = {r["d"]: r for r in rows}
        daily = []
        for d in range(days):
            r = by.get(d)
            daily.append(dict(day=_t.strftime("%Y-%m-%d", _t.localtime(now - (days - d) * 86400 + 1)), cost_usd=round(r["c"], 6) if r else 0.0,
                              calls=r["n"] if r else 0, input_tokens=(r["i"] or 0) if r else 0, output_tokens=(r["o"] or 0) if r else 0))
        m = s.db.one("SELECT COALESCE(SUM(cost_usd),0) c, COUNT(*) n FROM ledger WHERE kind='call' AND ts>=?", (month_start,))
        refused = s.db.one("SELECT COUNT(*) n FROM ledger WHERE kind='refused' AND ts>=?", (month_start,))["n"]
        credits_known = [x["credits"]["remaining_usd"] for x in provs if x["credits"]["remaining_usd"] is not None]
        running = s.jobs.running_sessions()
        recent = [dict(r, running=r["id"] in running) for r in s.chat.store.list(8)]
        week = s.db.one("SELECT COUNT(*) n FROM chat_messages WHERE role='user' AND ts>=?", (now - 7 * 86400,))["n"]
        ext = [dict(id=a["id"], label=a["label"], installed=a["installed"], enabled=a["enabled"], month=a["month"],
                    month_turn_cap=a["month_turn_cap"], month_minutes_cap=a["month_minutes_cap"], month_cost_cap=a["month_cost_cap"])
               for a in s.acp.store.list()]
        mcp = s.mcp.list()
        return dict(
            kpis=dict(month_spent_usd=round(m["c"], 6), month_calls=m["n"], month_refused=refused,
                      credits_left_usd=round(sum(credits_known), 6) if credits_known else None, credits_tracked=len(credits_known),
                      turns_7d=week, running=len(running), external_turns_month=sum(a["month"]["turns"] for a in ext)),
            daily=daily, providers=provs, recent=recent, external=ext,
            health=dict(providers_ready=sum(1 for x in provs if x["enabled"] and (x["auth_ready"] or x["kind"] in ("ollama", "mock"))),
                        providers_enabled=sum(1 for x in provs if x["enabled"]),
                        mcp_ready=sum(1 for x in mcp if x.ready()), mcp_total=len(mcp), agents=len(s.agents.list()),
                        skills=len(s.skills.list()), otel_records=s.tel.summary()["total"],
                        external_installed=sum(1 for a in ext if a["installed"]), external_total=len(ext)))

    # ---- folder picking (workspace) ----
    @app.post("/api/fs/pick-folder")
    async def fs_pick(body: PickIn, s: State = Depends(S)):
        """Open the native folder dialog on this machine's desktop. 501 if there is no GUI (use /api/fs/dirs instead)."""
        try:
            path = await asyncio.to_thread(fsbrowse.pick_folder, body.initial or str(s.chat.workspace_for(None)))
        except RuntimeError as e:
            raise HTTPException(501, str(e))
        return {"path": path, "cancelled": path is None}

    @app.get("/api/fs/dirs")
    def fs_dirs(path: str | None = None):
        try:
            return fsbrowse.list_dirs(path)
        except ValueError as e:
            raise HTTPException(422, str(e))

    # ---- voice (ElevenLabs) ----
    def _voice(fn):
        async def call(*a, **k):
            try:
                return await fn(*a, **k)
            except VoiceError as e:
                raise HTTPException(e.status, str(e))
            except httpx.HTTPError as e:
                raise HTTPException(502, f"could not reach ElevenLabs: {type(e).__name__}")
        return call

    @app.get("/api/voice/settings")
    def voice_settings(s: State = Depends(S)):
        return s.voice.cfg()

    @app.put("/api/voice/settings")
    def voice_update(body: VoicePatch, s: State = Depends(S)):
        try:
            out = s.voice.update(body.model_dump(exclude_unset=True))
        except VoiceError as e:
            raise HTTPException(e.status, str(e))
        m = s.mcp.get("elevenlabs")                     # convenience: the ElevenLabs MCP server can use the same key
        if body.api_key and m is not None and not m.env.get("ELEVENLABS_API_KEY"):
            s.mcp.update("elevenlabs", None, {"ELEVENLABS_API_KEY": body.api_key.strip()})
        return out

    @app.get("/api/voice/voices")
    async def voice_voices(s: State = Depends(S)):
        return await _voice(s.voice.voices)()

    @app.get("/api/voice/models")
    async def voice_models(s: State = Depends(S)):
        return await _voice(s.voice.models)()

    @app.get("/api/voice/subscription")
    async def voice_subscription(s: State = Depends(S)):
        return await _voice(s.voice.subscription)()

    @app.post("/api/voice/tts")
    async def voice_tts(body: TtsIn, s: State = Depends(S)):
        audio, n = await _voice(s.voice.tts)(body.text, body.voice_id)
        return Response(content=audio, media_type="audio/mpeg", headers={"X-Characters": str(n), "Cache-Control": "no-store"})

    @app.post("/api/voice/stt")
    async def voice_stt(file: UploadFile, s: State = Depends(S)):
        data = await file.read()
        return await _voice(s.voice.stt)(data, file.filename or "speech.webm", file.content_type or "audio/webm")

    # ---- OpenTelemetry (Phase 4) ----
    @app.get("/api/otel/settings")
    def otel_settings(s: State = Depends(S)):
        return s.tel.cfg()

    @app.put("/api/otel/settings")
    def otel_update(body: OtelPatch, s: State = Depends(S)):
        try:
            return s.tel.update(body.model_dump(exclude_unset=True))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/telemetry/insights")
    def telemetry_insights(days: int = 7, agent: str = "", model: str = "", status: str = "", tool: str = "", limit: int = 100, s: State = Depends(S)):
        from . import insights
        if status not in ("", "ok", "error"):
            raise HTTPException(422, "status must be ok or error")
        return insights.build(s.db, days, agent, model, status, tool, limit)

    @app.get("/api/telemetry/runs/{trace_id}/explain")
    def telemetry_explain(trace_id: str, s: State = Depends(S)):
        from . import insights
        out = insights.explain(s.db, trace_id)
        if out is None:
            raise HTTPException(404, "no recorded run with that id")
        return out

    @app.get("/api/telemetry/export")
    def telemetry_export(format: str = "csv", days: int = 7, agent: str = "", model: str = "", status: str = "", tool: str = "", s: State = Depends(S)):
        from . import insights
        if format not in ("csv", "json"):
            raise HTTPException(422, "format must be csv or json")
        ctype, body = insights.export(s.db, format, days=days, agent=agent, model=model, status=status, tool=tool)
        return Response(content=body, media_type=ctype, headers={"Content-Disposition": f"attachment; filename=agent-studio-runs.{format}"})

    @app.get("/api/otel/summary")
    def otel_summary(s: State = Depends(S)):
        return s.tel.summary()

    @app.get("/api/otel/traces")
    def otel_traces(source: str | None = None, limit: int = 50, s: State = Depends(S)):
        return s.tel.traces(source, max(1, min(limit, 500)))

    @app.get("/api/otel/traces/{trace_id}")
    def otel_trace(trace_id: str, s: State = Depends(S)):
        spans = s.tel.trace(trace_id)
        if not spans:
            raise HTTPException(404, "unknown trace")
        return spans

    @app.get("/api/otel/records")
    def otel_records(signal: str = "metric", source: str | None = None, limit: int = 100, s: State = Depends(S)):
        if signal not in ("trace", "metric", "log"):
            raise HTTPException(422, "signal must be trace, metric or log")
        return s.tel.records(signal, source, max(1, min(limit, 1000)))

    @app.delete("/api/otel/records")
    def otel_clear(source: str | None = None, s: State = Depends(S)):
        return {"deleted": s.tel.clear(source)}

    @app.post("/v1/{signal}")
    async def otlp_receive(signal: str, request: Request, s: State = Depends(S)):
        """OTLP/HTTP receiver (JSON encoding) for external agents. Loopback only."""
        if signal not in SIGNALS:
            raise HTTPException(404, "unknown OTLP signal")
        if (request.client.host if request.client else "") not in LOOPBACK:
            raise HTTPException(403, "the OTLP receiver only accepts connections from this machine")
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise HTTPException(415, "send OTLP as JSON: set OTEL_EXPORTER_OTLP_PROTOCOL=http/json (protobuf is not enabled)")
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "payload too large")
        try:
            n = s.tel.ingest(signal, json.loads(raw or b"{}"), forward_raw=raw)
        except (ValueError, TypeError, AttributeError) as e:
            raise HTTPException(400, f"not valid OTLP/JSON: {e}")
        return {"partialSuccess": {}, "stored": n}

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

    register_d1(app)        # /api/d1/*: install / status / uninstall of the local decision model
    register_ts(app)        # /api/ts/*: install / status / uninstall of the local time-series forecasting models
    register_rag(app)       # /api/rag/*: knowledge bases, vector stores, documents, search
    register_ard(app)       # /api/aibom/*, /api/discovery/*: AI bill of materials + Agentic Resource Discovery publishing
    register_auto(app)      # /api/auto/*: Auto mode settings, availability and a dry-run of its routing
    register_ocsf(app)      # /api/ocsf/*: the OCSF security export (sinks, preview, test)
    register_guardrails(app)  # /api/guardrails/*: safety models and cloud services that check inputs, outputs and tool calls
    register_evals(app)       # /api/evals/*: test sets, runs, exports (promptfoo, Inspect, DeepEval, Ragas, CSV, JSONL)
    register_notifications(app)   # /api/notifications: the bell (list, read, delete)
    register_security(app)    # /api/security/* (scorecard) and /api/redteam/* (red-team runners)
    register_artifacts(app)   # /api/artifacts/*: files agents produced, storages, search, preview, download
    register_apps(app)        # /api/apps/*: apps agents build with you (files, versions, run, export, git)
    register_science(app)     # /api/science/*: the Science Lab (sources, calculator, investigations, evidence, components)
    register_canvas(app)      # /api/canvas/*: the shared document (versions, proposals, live updates)
    register_uikit(app)       # /api/uikit/*: rich component settings, catalog and validator
    register_agui(app)        # /api/agui: the chat run as an AG-UI event stream (any AG-UI client can render it)
    register_live(app, ALLOWED_ORIGINS)   # /api/live/*: local voice install + settings + self-test; /api/live/ws is the live conversation
    register_identity(app)    # /api/identity/*: agent identities, tokens, delegation (RFC 8693), identity providers
    register_memory(app)    # /api/memories/*: memory kinds, scopes, backends, review queue, notes, export/import
    register_settings(app)  # /api/settings/registry|export|import|reset|history: every setting described, backed up, reset
    trainer_mod.register(app)   # /api/trainer/*: install ML frameworks, train jobs
    register_workflows(app)     # /api/workflows/*: drag-and-drop workflow builder (graphs, runs, wizard, imported APIs)
    register_harness(app)       # /api/harness/*: permission mode, loop guard, hooks, web tools, checkpoints (rewind)
    register_claude_code(app)   # /api/agents/{id}/claude-code/*: deploy an agent to Claude Code; /agent-mcp/{id}: a live agent as an MCP server
    register_access(app)        # /v1/*: OpenAI-compatible API for agents (keys, scopes, rate limits); /api/access/*: its settings
    register_a2a(app)           # /api/access/remotes/*: other A2A agents our agents can call
    register_data(app)          # /api/data/*: data sources (read-only), data products, who can see what, audit
    register_sandboxes(app)     # /api/sandboxes/*: which sandbox commands run in (Docker container / micro VM), limits, status
    register_runtimes(app)      # /api/runtimes/*: where agents run (settings, plan/deploy/shadow/switch/rollback)
    register_frameworks(app)    # /api/frameworks/*: agent frameworks (matrix, install); /internal/fw/*: a framework worker's private gateway
    catalog_api.register(app)   # /api/catalog/*: domain skills, public MCP servers, cloud ML accounts
    register_easy(app)      # /api/easy/*: system check, onboarding, one-click Ollama + coding-agent installs, model library, agent wizard
    mount_web(app, os.environ)  # desktop build only: the static UI at "/" (mounted last, so every API route wins)
    return app

