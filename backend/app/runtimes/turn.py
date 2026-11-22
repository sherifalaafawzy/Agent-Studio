"""One chat turn for an agent whose live deployment runs on a remote runtime (Docker, AgentCore, Vertex, Foundry).

The deployed agent is a plain model loop in its own container, so this turn is small: keep the chat, send the message (with the
earlier conversation as context) to the live release, show the answer. Model calls happen in the runtime with the key it was given -
they are NOT under Agent Studio's spend caps; the answer says so.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..agents import AgentDef


def history_text(rows: list[dict], message: str) -> str:
    """The runtime keeps no chat, so the earlier turns travel with the message (the last 12 turns at most)."""
    past = [f"{'User' if r['role'] == 'user' else 'Assistant'}: {r['text']}" for r in rows[-12:]]
    return ("Conversation so far:\n" + "\n".join(past) + "\n\nUser: " + message) if past else message


async def send_remote(chat: Any, root: AgentDef, p: Any, model: str | None, message: str, sid: str, emit: Any, is_first_attempt: bool, final: bool,
                      route: dict | None) -> dict:
    from ..chat import TurnTrace, _clip, DEFAULT_MAX_LLM_CALLS
    turn = TurnTrace(max_llm_calls=int(chat.db.get_setting("max_llm_calls_per_turn", str(DEFAULT_MAX_LLM_CALLS))), emit=emit, tel=chat.tel)
    turn.start_run("root", root.id, None, task=_clip(message, 4000))
    turn.runs_by_id["root"]["definition"] = dict(runtime=root.runtime)
    turn.session_id = sid
    chat.store.ensure(sid, p.id, root.id, model, None, message)
    prior = [dict(role="user" if m["role"] == "user" else "assistant", text=m["text"]) for m in chat._replay_rows(sid)]
    if is_first_attempt:
        chat.store.add(sid, "user", message)
    if emit:
        emit({"type": "session", "session_id": sid, "agent": root.id, "provider": p.id, "trace_id": turn.trace_id})
    turn.warnings.append(f"Answered by the deployed agent on {root.runtime}: its model calls are billed there, not under Agent Studio's spend caps, and it has no tools, memory or guardrails.")
    reply, status, err_info = "", "ok", None
    try:
        reply = await asyncio.to_thread(chat.runtimes.invoke, root.id, history_text(prior, message))
        if emit and reply:
            emit({"type": "text", "delta": reply})
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    except BaseException as e:
        status = "error"
        from ..errors import as_event
        err_info = as_event(e, p.kind)
        raise
    finally:
        no_answer = status == "ok" and not reply.strip()
        turn.end_run(turn.runs_by_id["root"], "done" if status == "ok" else status, reply or None)
        usage = dict(input_tokens=0, output_tokens=0, cost_usd=0.0)
        if final or status != "error":
            chat._persist(sid, root, p.id, turn, reply, status, usage, "", no_answer, err_info, route)
    return dict(session_id=sid, provider=p.id, agent=root.id, model=f"runtime:{root.runtime}", reply=reply, halted=None, warnings=turn.warnings, steps=[], model_calls=[],
                usage=usage, runs=turn.runs, trace_id=turn.trace_id, thinking=None, no_answer=no_answer, retried=False, route=route, memory=[], guardrails=[], artifacts=[],
                billing=chat.guard.status(p))
