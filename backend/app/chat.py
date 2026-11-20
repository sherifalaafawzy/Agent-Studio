"""Chat round-trip: ADK LlmAgent -> LiteLlm -> provider, with the budget guard
wired in as ADK before/after/on-error model callbacks."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .budget import BudgetExceeded, BudgetGuard, Reservation, estimate_tokens
from .providers import ProviderStore, litellm_target

APP_NAME = "agent_studio"
USER_ID = "local"

SYSTEM_PROMPT = "You are Agent Studio's assistant. Be concise and accurate."


@dataclass
class CallTrace:
    """What the guard saw during one chat turn (surfaced to the API / demo)."""
    refused: BudgetExceeded | None = None
    settled_cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    model_invocations: int = 0  # times the guard let a request through to the provider
    pending: Reservation | None = None


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
        chars += len(str(cfg.tools))  # tool schemas count toward input
    return chars, n


def make_guard_callbacks(guard: BudgetGuard, provider_id: str, model: str, litellm_model: str,
                         max_output_tokens: int, trace: CallTrace):
    """Return (before_model, after_model, on_model_error) ADK callbacks bound to one provider."""

    def before_model(callback_context, llm_request):
        # Make the worst-case bound real: clamp the request's output limit.
        cfg = llm_request.config or types.GenerateContentConfig()
        llm_request.config = cfg
        cap_out = min(cfg.max_output_tokens or max_output_tokens, max_output_tokens)
        cfg.max_output_tokens = cap_out
        chars, n = _request_chars(llm_request)
        try:
            trace.pending = guard.reserve(provider_id, model, estimate_tokens(chars, n), cap_out, litellm_model)
        except BudgetExceeded as e:
            trace.refused = e
            # No `content`: the refusal must not enter the conversation history (the model never sees it,
            # and it would inflate the input of every later turn).
            return LlmResponse(error_code="BUDGET_EXCEEDED", error_message=e.detail["message"], turn_complete=True)
        trace.model_invocations += 1
        return None  # proceed to the provider

    def after_model(callback_context, llm_response):
        r = trace.pending
        if r is None or llm_response.partial or llm_response.error_code == "BUDGET_EXCEEDED":
            return None
        um = llm_response.usage_metadata
        if um is not None and um.prompt_token_count is not None and (um.candidates_token_count is not None or um.total_token_count):
            out = (um.candidates_token_count or 0) + (getattr(um, "thoughts_token_count", None) or 0)
            tin, tout = um.prompt_token_count, out
        else:
            tin = tout = None  # no usage -> charge worst case
        trace.settled_cost_usd += guard.settle(r, tin, tout)
        trace.input_tokens += tin or r.est_input_tokens
        trace.output_tokens += tout or r.max_output_tokens
        trace.pending = None
        return None

    def on_model_error(callback_context, llm_request, error):
        r = trace.pending
        if r is not None:
            guard.release(r, note=f"call_failed: {type(error).__name__}")
            trace.pending = None
        return None  # let the error propagate

    return before_model, after_model, on_model_error


class ChatService:
    def __init__(self, guard: BudgetGuard, providers: ProviderStore):
        self.guard = guard
        self.providers = providers
        self.sessions = InMemorySessionService()

    async def send(self, provider_id: str, model: str | None, message: str, session_id: str | None,
                   max_tokens: int | None = None) -> dict:
        p = self.providers.get(provider_id)
        if p is None:
            raise KeyError(provider_id)
        model = model or p.default_model
        lite_model, kwargs = litellm_target(p, model)  # ValueError if misconfigured
        max_out = min(max_tokens or p.max_output_tokens, p.max_output_tokens)
        if p.kind == "mock":
            kwargs["mock_response"] = f"[mock:{model}] You said: {message[:300]}"
        trace = CallTrace()
        before, after, on_err = make_guard_callbacks(self.guard, p.id, model, lite_model, max_out, trace)
        agent = LlmAgent(
            name="assistant", model=LiteLlm(model=lite_model, **kwargs), instruction=SYSTEM_PROMPT,
            before_model_callback=before, after_model_callback=after, on_model_error_callback=on_err,
        )
        sid = session_id or uuid.uuid4().hex
        runner = Runner(app_name=APP_NAME, agent=agent, session_service=self.sessions, auto_create_session=True)
        reply_parts: list[str] = []
        async for ev in runner.run_async(user_id=USER_ID, session_id=sid,
                                         new_message=types.Content(role="user", parts=[types.Part(text=message)])):
            if ev.content and ev.content.parts and ev.is_final_response():
                reply_parts.extend(pt.text for pt in ev.content.parts if pt.text)
        if trace.refused:
            raise trace.refused
        return dict(session_id=sid, provider=p.id, model=model, reply="".join(reply_parts),
                    usage=dict(input_tokens=trace.input_tokens, output_tokens=trace.output_tokens,
                               cost_usd=trace.settled_cost_usd),
                    billing=self.guard.status(self.providers.get(p.id)))
