"""The gateway and tool bridge a framework worker talks to.

A worker (LangGraph, Strands, ...) runs in its own environment and knows only two addresses, both private to ONE run and
protected by a random token that stops working when the run ends:

* `POST .../v1/chat/completions` (OpenAI-compatible): the worker's model. The model, provider, token limits and credentials are
  fixed by Agent Studio for this run - whatever `model` the worker asks for is ignored - and every call goes through the budget
  guard, the guardrails, the turn's model-call limit and the run record. A framework therefore cannot overspend or use a model
  the agent was not given.
* `POST .../tool`: Agent Studio's own tools (files, search, shell in the sandbox, knowledge bases, memory...). Only the tools the
  agent was given exist; each call is guardrail-checked, limited, recorded and shown in the chat like an ADK tool call.

Everything here is injectable (the completion function, the clock) so the whole thing is tested with scripted fakes.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import litellm

from ..budget import BudgetExceeded, estimate_tokens
from .toolschema import tool_spec

MAX_TOOL_RESULT = 60_000          # characters of a tool result sent back to the model


class GatewayError(Exception):
    def __init__(self, status: int, message: str, kind: str = "invalid_request_error", detail: dict | None = None):
        super().__init__(message)
        self.status, self.message, self.kind, self.detail = status, message, kind, detail

    def body(self) -> dict:
        return {"error": {"message": self.message, "type": self.kind, **({"detail": self.detail} if self.detail else {})}}


def _clip(v: Any, n: int = 600) -> str:
    s = v if isinstance(v, str) else repr(v)
    return s if len(s) <= n else s[:n] + "…"


@dataclass
class RunSession:
    """One framework run: who may call what, and where the run record is kept."""
    token: str
    agent_id: str
    turn: Any                                   # chat.TurnTrace
    guard: Any
    provider_id: str
    model: str
    lite_model: str
    kwargs: dict
    max_out: int
    mock: bool = False
    tools: dict[str, Callable] = field(default_factory=dict)
    specs: list[dict] = field(default_factory=list)
    rails: Any = None                           # chat.Rails or None
    max_tool_calls: int = 50
    before_tool: Callable[[str, dict], Awaitable[str | None]] | None = None   # returns a refusal message (permission modes, loop guard, hooks)
    after_tool: Callable[[str, dict, Any], str | None] | None = None      # returns a note to add to the result (your hooks), or None
    tool_calls: int = 0
    model_calls: int = 0
    closed: bool = False
    run_id: str = "root"
    started: float = field(default_factory=time.time)

    def public(self) -> dict:
        return dict(agent=self.agent_id, model_calls=self.model_calls, tool_calls=self.tool_calls)


def _chars(messages: list, tools: list | None) -> tuple[int, int]:
    n = 0
    for m in messages:
        c = m.get("content")
        n += len(c) if isinstance(c, str) else len(json.dumps(c, default=str)) if c else 0
        if m.get("tool_calls"):
            n += len(json.dumps(m["tool_calls"], default=str))
    return n + (len(json.dumps(tools, default=str)) if tools else 0), len(messages)


def _input_text(messages: list) -> str:
    """What came from outside the model in the newest turn: the user's message or the results of tools it called."""
    out = []
    for m in reversed(messages):
        if m.get("role") in ("user", "tool"):
            c = m.get("content")
            out.append(c if isinstance(c, str) else json.dumps(c, default=str))
        else:
            break
    return "\n".join(reversed(out))


def _final_text(resp: dict) -> str:
    ch = (resp.get("choices") or [{}])[0].get("message") or {}
    return "" if ch.get("tool_calls") else (ch.get("content") or "")


class FrameworkRuntime:
    def __init__(self, completion: Callable | None = None):
        self.completion = completion or litellm.acompletion
        self.sessions: dict[str, RunSession] = {}

    # ------------------------------------------------------------------------------------------------ sessions
    def open(self, **kw) -> RunSession:
        tools: dict[str, Callable] = kw.pop("tool_fns", {})
        s = RunSession(token=secrets.token_urlsafe(24), tools=tools, specs=[tool_spec(f) for f in tools.values()], **kw)
        self.sessions[s.token] = s
        return s

    def close(self, token: str) -> None:
        s = self.sessions.pop(token, None)
        if s is not None:
            s.closed = True

    def get(self, token: str) -> RunSession:
        s = self.sessions.get(token)
        if s is None or s.closed:
            raise GatewayError(401, "this run has ended or the token is wrong", "invalid_token")
        return s

    # ------------------------------------------------------------------------------------------------ model
    def _prepare(self, s: RunSession, body: dict) -> tuple[list, list | None, dict, Any]:
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise GatewayError(400, "messages is required")
        tools = body.get("tools") or None
        if tools:
            bad = [t["function"]["name"] for t in tools if (t.get("function") or {}).get("name") not in s.tools]
            if bad:                                                  # a worker may only offer the model the tools this agent has
                raise GatewayError(400, f"unknown tools: {bad}")
        if s.turn.halted:
            raise GatewayError(402, "turn halted by budget guard", "budget_exceeded")
        if s.turn.model_invocations >= s.turn.max_llm_calls:
            msg = f"Turn limit: {s.turn.max_llm_calls} model calls reached (loop protection). Raise 'max model calls per turn' in Settings."
            e = BudgetExceeded(dict(error="turn_limit", provider=s.provider_id, model=s.model, projected_usd=0.0, remaining_usd=0.0, cap_usd=0.0,
                                    spent_usd=0.0, reserved_usd=0.0, price_known=True, message=msg))
            s.turn.refused = s.turn.refused or e                      # shown as a stopped turn, like the ADK path
            s.turn.halted = True
            raise GatewayError(429, msg, "turn_limit")
        extra = {k: body[k] for k in ("temperature", "top_p", "tool_choice", "response_format", "stop", "seed") if body.get(k) is not None}
        if tools:
            extra["tools"] = tools
        chars, n = _chars(messages, tools)
        try:
            r = s.guard.reserve(s.provider_id, s.model, estimate_tokens(chars, n), s.max_out, s.lite_model)
        except BudgetExceeded as e:
            s.turn.refused = s.turn.refused or e
            s.turn.halted = True
            raise GatewayError(402, e.detail["message"], "budget_exceeded", e.detail)
        s.turn.model_invocations += 1
        s.turn.open_reservations[r.id] = r
        return messages, tools, extra, r

    def _args(self, s: RunSession, messages: list, extra: dict, stream: bool) -> dict:
        a: dict = dict(model=s.lite_model, messages=messages, max_tokens=s.max_out, **s.kwargs, **extra)
        if s.mock:
            a["mock_response"] = "[mock] " + (_input_text(messages) or "ok")[:200]
        if stream:
            a.update(stream=True, stream_options={"include_usage": True})
        return a

    def _settle(self, s: RunSession, r, usage: dict | None, t0: float) -> None:
        tin = (usage or {}).get("prompt_tokens")
        tout = (usage or {}).get("completion_tokens")
        if tin is None or tout is None:
            tin = tout = None                                       # no usage: charged at the worst case
        cost = s.guard.settle(r, tin, tout)
        s.turn.open_reservations.pop(r.id, None)
        s.model_calls += 1
        call = dict(agent=s.agent_id, run_id=s.run_id, provider=s.provider_id, model=s.model, cost_usd=cost,
                    input_tokens=tin or r.est_input_tokens, output_tokens=tout or r.max_output_tokens,
                    t0=t0, t1=time.time(), usage_reported=tin is not None)
        s.turn.calls.append(call)
        s.turn.run(s.run_id)["calls"].append(call)
        s.turn.note({"type": "model_call", **call, "turn_cost_usd": s.turn.cost})
        if s.turn.tel:
            from ..telemetry import new_span_id
            s.turn.tel.span(s.agent_id, s.turn.trace_id, new_span_id(), s.turn.run(s.run_id)["span_id"], "llm.call", t0, time.time(),
                            {"gen_ai.request.model": s.model, "gen_ai.provider": s.provider_id, "gen_ai.usage.input_tokens": call["input_tokens"],
                             "gen_ai.usage.output_tokens": call["output_tokens"], "cost_usd": cost, "usage_reported": tin is not None})

    def _release(self, s: RunSession, r, note: str) -> None:
        s.guard.release(r, note=note)
        s.turn.open_reservations.pop(r.id, None)

    def _blocked(self, message: str, model: str) -> dict:
        return {"id": "blocked", "object": "chat.completion", "model": model, "created": int(time.time()),
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": message}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

    async def complete(self, token: str, body: dict) -> dict:
        """One non-streaming model call for the run."""
        s = self.get(token)
        messages, tools, extra, r = self._prepare(s, body)
        shown = body.get("model") or s.model
        text = _input_text(messages)
        if s.rails is not None and text and s.rails.wants("input"):
            d = await s.rails.check("input", text)
            if d.blocked:
                self._release(s, r, "guardrail_blocked")
                return self._blocked(d.message, shown)
        t0 = time.time()
        try:
            resp = await self.completion(**self._args(s, messages, extra, False))
        except asyncio.CancelledError:
            raise                                                    # the call may have run: the turn settles the reservation at its worst case
        except BaseException as e:
            self._release(s, r, f"call_failed: {type(e).__name__}")
            raise
        out = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
        self._settle(s, r, out.get("usage"), t0)
        out["model"] = shown
        final = _final_text(out)
        if s.rails is not None and final and s.rails.wants("output"):
            d = await s.rails.check("output", final)
            if d.blocked:
                return self._blocked(d.message, shown)
        return out

    async def stream(self, token: str, body: dict) -> AsyncIterator[dict]:
        """A streaming model call: yields chat.completion.chunk dicts (the last one carries usage)."""
        s = self.get(token)
        messages, tools, extra, r = self._prepare(s, body)
        shown = body.get("model") or s.model
        text = _input_text(messages)
        if s.rails is not None and text and s.rails.wants("input"):
            d = await s.rails.check("input", text)
            if d.blocked:
                self._release(s, r, "guardrail_blocked")
                yield {"id": "blocked", "object": "chat.completion.chunk", "model": shown, "created": int(time.time()),
                       "choices": [{"index": 0, "delta": {"role": "assistant", "content": d.message}, "finish_reason": "stop"}]}
                return
        t0, usage, parts = time.time(), None, []
        try:
            resp = await self.completion(**self._args(s, messages, extra, True))
            async for chunk in resp:
                c = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)
                c["model"] = shown
                if c.get("usage"):
                    usage = c["usage"]
                for ch in c.get("choices") or []:
                    if (ch.get("delta") or {}).get("content"):
                        parts.append(ch["delta"]["content"])
                yield c
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except BaseException as e:
            self._release(s, r, f"call_failed: {type(e).__name__}")
            raise
        self._settle(s, r, usage, t0)
        if s.rails is not None and parts and s.rails.wants("output"):
            await s.rails.check("output", "".join(parts))            # the words are already out: flagged, never recalled

    # ------------------------------------------------------------------------------------------------ tools
    async def call_tool(self, token: str, name: str, args: Any) -> dict:
        s = self.get(token)
        fn = s.tools.get(name)
        if fn is None:
            raise GatewayError(404, f"no tool named '{name}' for this agent", "unknown_tool")
        if not isinstance(args, dict):
            raise GatewayError(400, "args must be an object")
        limit = s.max_tool_calls
        if s.tool_calls >= limit:
            return {"ok": False, "error": f"tool-call limit reached ({limit} per turn)"}
        s.tool_calls += 1
        rid = s.run_id
        step = dict(agent=s.agent_id, run_id=rid, type="call", tool=name, args=_clip(args, 400), ts=time.time())
        s.turn.steps.append(step)
        s.turn.run(rid)["steps"].append(step)
        s.turn.note({**step, "type": "tool_call"})
        t0 = time.time()
        refusal = None
        if s.rails is not None and s.rails.wants("tool"):
            d = await s.rails.check("tool", f"{name} {json.dumps(args, default=str)[:20000]}")
            refusal = d.message if d.blocked else None
        if refusal is None and s.before_tool is not None:
            refusal = await s.before_tool(name, args)
        if refusal is not None:
            result: Any = {"ok": False, "error": refusal, "refused": True}
        else:
            try:
                sig = inspect.signature(fn)
                bound = {k: v for k, v in args.items() if k in sig.parameters}
                res = fn(**bound)
                result = await res if inspect.isawaitable(res) else res
            except TypeError as e:
                result = {"ok": False, "error": f"bad arguments for {name}: {e}"}
            except Exception as e:  # noqa: BLE001 - the model gets the error and can adapt
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        now = time.time()
        ok = result.get("ok") is not False
        done = dict(agent=s.agent_id, run_id=rid, type="result", tool=name, result=_clip(result), ts=now, ms=round((now - t0) * 1000, 1), ok=ok)
        s.turn.steps.append(done)
        s.turn.run(rid)["steps"].append(done)
        s.turn.note({**done, "type": "tool_result"})
        if s.turn.tel:
            from ..telemetry import new_span_id
            s.turn.tel.span(s.agent_id, s.turn.trace_id, new_span_id(), s.turn.run(rid)["span_id"], f"tool.{name}", t0, now,
                            {"tool": name, "args": _clip(args, 300)}, "ok" if ok else "error")
        if s.after_tool is not None:
            note = s.after_tool(name, args, result)
            if note:
                result = {**result, "note": note}
        text = json.dumps(result, default=str)
        if len(text) > MAX_TOOL_RESULT:
            result = {"ok": result.get("ok", True), "truncated": True, "result": text[:MAX_TOOL_RESULT]}
        return result
