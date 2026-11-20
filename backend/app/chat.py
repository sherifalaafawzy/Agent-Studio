"""Chat engine: builds an ADK agent tree (sub-agents via AgentTool, isolated context) for a turn, with the
budget guard attached to EVERY agent's model calls as ADK before/after/on-error callbacks."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from .agents import HARNESS_RULES, AgentDef, AgentStore
from .budget import BudgetExceeded, BudgetGuard, Reservation, estimate_tokens
from .db import Database, data_dir
from .harness.sandbox import Sandbox
from .harness.tools import ToolContext, build_tools
from .mcp_registry import McpStore, build_toolsets
from .mock_llm import MockLlm
from .pricing import price_for
from .providers import ProviderStore, litellm_target, tier_model
from .skills import SkillRegistry

APP_NAME = "agent_studio"
USER_ID = "local"
SYSTEM_PROMPT = "You are Agent Studio's assistant. Be concise and accurate."
MAX_DEPTH = 3
DEFAULT_MAX_LLM_CALLS = 40


@dataclass
class TurnTrace:
    """Everything the guard and tools saw during one chat turn (shared by all agents in the tree)."""
    max_llm_calls: int = DEFAULT_MAX_LLM_CALLS
    refused: BudgetExceeded | None = None
    halted: bool = False
    model_invocations: int = 0
    calls: list[dict] = field(default_factory=list)   # settled model calls
    steps: list[dict] = field(default_factory=list)   # tool calls/results, any agent depth
    warnings: list[str] = field(default_factory=list)

    @property
    def cost(self) -> float:
        return sum(c["cost_usd"] for c in self.calls)


def _clip(v, n=600):
    s = v if isinstance(v, str) else repr(v)
    return s if len(s) <= n else s[:n] + "…"


def _request_chars(llm_request) -> tuple[int, int]:
    chars, n = 0, 0
    cfg = llm_request.config
    si = getattr(cfg, "system_instruction", None) if cfg else None
    if si:
        chars += len(str(si))
    for c in llm_request.contents or []:
        n += 1
        for part in c.parts or []:
            chars += len(part.text or "")
            if part.function_call:
                chars += len(str(part.function_call.args or {})) + 40
            if part.function_response:
                chars += len(str(part.function_response.response or {})) + 40
    if cfg and getattr(cfg, "tools", None):
        chars += len(str(cfg.tools))
    return chars, n


def make_callbacks(guard: BudgetGuard, turn: TurnTrace, agent_id: str, provider_id: str, model: str,
                   litellm_model: str, max_output_tokens: int):
    """(before_model, after_model, on_model_error, before_tool, after_tool) bound to one agent."""
    state: dict = {"pending": None}

    def _stop(e: BudgetExceeded):
        turn.refused = turn.refused or e
        turn.halted = True
        return LlmResponse(error_code="BUDGET_EXCEEDED", error_message=e.detail["message"], turn_complete=True)

    def before_model(callback_context, llm_request):
        if turn.halted:  # a guard refusal anywhere stops the whole turn: no further spend
            return LlmResponse(error_code="BUDGET_EXCEEDED", error_message="turn halted by budget guard", turn_complete=True)
        if turn.model_invocations >= turn.max_llm_calls:
            msg = f"Turn limit: {turn.max_llm_calls} model calls reached (loop protection). Raise 'max model calls per turn' in Settings."
            return _stop(BudgetExceeded(dict(error="turn_limit", provider=provider_id, model=model, projected_usd=0.0,
                                             remaining_usd=0.0, cap_usd=0.0, spent_usd=0.0, reserved_usd=0.0, price_known=True, message=msg)))
        cfg = llm_request.config or types.GenerateContentConfig()
        llm_request.config = cfg
        cap_out = min(cfg.max_output_tokens or max_output_tokens, max_output_tokens)
        cfg.max_output_tokens = cap_out  # makes the worst-case bound real
        chars, n = _request_chars(llm_request)
        try:
            state["pending"] = guard.reserve(provider_id, model, estimate_tokens(chars, n), cap_out, litellm_model)
        except BudgetExceeded as e:
            return _stop(e)
        turn.model_invocations += 1
        return None

    def after_model(callback_context, llm_response):
        r: Reservation | None = state["pending"]
        if r is None or llm_response.partial or llm_response.error_code == "BUDGET_EXCEEDED":
            return None
        um = llm_response.usage_metadata
        if um is not None and um.prompt_token_count is not None and (um.candidates_token_count is not None or um.total_token_count):
            tin = um.prompt_token_count
            tout = (um.candidates_token_count or 0) + (getattr(um, "thoughts_token_count", None) or 0)
        else:
            tin = tout = None  # no usage -> charge worst case
        cost = guard.settle(r, tin, tout)
        turn.calls.append(dict(agent=agent_id, provider=provider_id, model=model, cost_usd=cost,
                               input_tokens=tin or r.est_input_tokens, output_tokens=tout or r.max_output_tokens))
        state["pending"] = None
        return None

    def on_model_error(callback_context, llm_request, error):
        r = state["pending"]
        if r is not None:
            guard.release(r, note=f"call_failed: {type(error).__name__}")
            state["pending"] = None
        return None

    def before_tool(tool, args, tool_context):
        turn.steps.append(dict(agent=agent_id, type="call", tool=tool.name, args=_clip(args, 400)))
        return None

    def after_tool(tool, args, tool_context, tool_response):
        turn.steps.append(dict(agent=agent_id, type="result", tool=tool.name, result=_clip(tool_response)))
        return None

    return before_model, after_model, on_model_error, before_tool, after_tool


class ChatService:
    def __init__(self, guard: BudgetGuard, providers: ProviderStore, agents: AgentStore, mcp: McpStore,
                 skills: SkillRegistry, db: Database):
        self.guard, self.providers, self.agents, self.mcp, self.skills, self.db = guard, providers, agents, mcp, skills, db
        self.sessions = InMemorySessionService()

    # ---- settings helpers ----
    def workspace_for(self, requested: str | None) -> Path:
        raw = requested or self.db.get_setting("workspace_dir") or str(data_dir() / "workspaces" / "default")
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = data_dir() / "workspaces" / p
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()

    def sandbox_for(self, ws: Path) -> Sandbox:
        return Sandbox(ws, network=self.db.get_setting("sandbox_network", "0") == "1")

    # ---- agent tree ----
    async def _build(self, d: AgentDef, *, chat_provider: str, root_model: str | None, max_tokens: int | None, ctx: ToolContext,
                     turn: TurnTrace, toolsets: list, depth: int = 0, trail: tuple = ()) -> LlmAgent:
        if depth > MAX_DEPTH or d.id in trail:
            raise ValueError(f"agent nesting too deep or cyclic at '{d.id}'")
        p = self.providers.get(d.provider or chat_provider)
        if p is None:
            raise KeyError(d.provider or chat_provider)
        model = (root_model if depth == 0 and root_model else None) or tier_model(p, d.tier, d.model)
        lite_model, kwargs = litellm_target(p, model)  # ValueError if misconfigured
        llm = MockLlm(model=lite_model, **kwargs) if p.kind == "mock" else LiteLlm(model=lite_model, **kwargs)
        max_out = min(max_tokens or p.max_output_tokens, p.max_output_tokens)
        b, a, e, bt, at = make_callbacks(self.guard, turn, d.id, p.id, model, lite_model, max_out)

        tools: list = build_tools(ctx, d.tools)
        # MCP: explicit list + MCPs linked by explicitly-named skills (a "*" skills list does not auto-link)
        mcp_ids = list(d.mcp)
        for sn in d.skills:
            sk = self.skills.get(sn) if sn != "*" else None
            if sk:
                mcp_ids += sk.mcp
        if mcp_ids:
            toolsets.extend(ts := await build_toolsets(self.mcp, mcp_ids, str(ctx.workspace), turn.warnings))
            tools.extend(ts)

        instruction = d.instruction or SYSTEM_PROMPT
        if d.tools:
            instruction += HARNESS_RULES
        subs: list[AgentDef] = [s for s in (self.agents.get(i) for i in d.sub_agents) if s]
        if subs:
            instruction += "\n\n## Sub-agents (call them like tools; each has isolated context)\n" + "\n".join(
                f"- {s.id} [{s.tier}]: {s.description}" for s in subs)
            for s in subs:
                child = await self._build(s, chat_provider=chat_provider, root_model=None, max_tokens=max_tokens, ctx=ToolContext(
                    ctx.workspace, ctx.sandbox, ctx.skills, ctx.mcp), turn=turn, toolsets=toolsets, depth=depth + 1, trail=trail + (d.id,))
                tools.append(AgentTool(agent=child))
        if d.skills:
            instruction += self.skills.index_text(d.skills)
        return LlmAgent(name=d.id, model=llm, description=d.description or d.name, instruction=instruction, tools=tools,
                        before_model_callback=b, after_model_callback=a, on_model_error_callback=e,
                        before_tool_callback=bt, after_tool_callback=at)

    async def send(self, provider_id: str, model: str | None, message: str, session_id: str | None,
                   max_tokens: int | None = None, agent_id: str | None = None, workspace: str | None = None) -> dict:
        p = self.providers.get(provider_id)
        if p is None:
            raise KeyError(provider_id)
        if agent_id:
            root = self.agents.get(agent_id)
            if root is None:
                raise KeyError(agent_id)
        else:  # Phase-1 plain chat: no tools, provider default model
            root = AgentDef(id="plain", name="Plain chat", instruction=SYSTEM_PROMPT, tier="custom", model=model or p.default_model)
        turn = TurnTrace(max_llm_calls=int(self.db.get_setting("max_llm_calls_per_turn", str(DEFAULT_MAX_LLM_CALLS))))
        ws = self.workspace_for(workspace)
        ctx = ToolContext(ws, self.sandbox_for(ws), self.skills, self.mcp)
        toolsets: list = []
        sid = session_id or uuid.uuid4().hex
        reply_parts: list[str] = []
        try:
            agent = await self._build(root, chat_provider=provider_id, root_model=model if agent_id else None,
                                      max_tokens=max_tokens, ctx=ctx, turn=turn, toolsets=toolsets)
            runner = Runner(app_name=APP_NAME, agent=agent, session_service=self.sessions, auto_create_session=True)
            async for ev in runner.run_async(user_id=USER_ID, session_id=sid,
                                             new_message=types.Content(role="user", parts=[types.Part(text=message)]),
                                             run_config=RunConfig(max_llm_calls=turn.max_llm_calls * 2 + 10)):
                if ev.content and ev.content.parts and ev.is_final_response() and ev.author == root.id:
                    reply_parts.extend(pt.text for pt in ev.content.parts if pt.text)
        finally:
            for ts in toolsets:
                try:
                    await ts.close()
                except Exception:  # noqa: BLE001
                    pass
        halted = None
        if turn.refused:
            if turn.model_invocations == 0:
                raise turn.refused  # nothing happened: plain 402
            halted = turn.refused.detail  # partial progress: return what happened + why it stopped
        return dict(
            session_id=sid, provider=p.id, agent=root.id, model=turn.calls[0]["model"] if turn.calls else None,
            reply="".join(reply_parts), halted=halted, warnings=turn.warnings, steps=turn.steps, model_calls=turn.calls,
            usage=dict(input_tokens=sum(c["input_tokens"] for c in turn.calls), output_tokens=sum(c["output_tokens"] for c in turn.calls),
                       cost_usd=turn.cost),
            billing=self.guard.status(self.providers.get(p.id)))
