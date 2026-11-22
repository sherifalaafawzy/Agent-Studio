"""Context window: how full a chat is, and summarising it so the conversation can continue.

* window  - the model's context size: LiteLLM's table (e.g. 1M for Claude Opus 5.5), or the Ollama num_ctx you set.
* used    - the real prompt size of the last model call (what the provider counted) plus its reply; estimated from
            the saved text when a provider reported no usage.
* summary - "Summarize context" writes a summary of the conversation so far (one guarded model call). Later turns
            resume from the summary plus the messages written after it; the original messages stay visible in the UI.
"""
from __future__ import annotations

import json
import time
from typing import Any

import litellm

FALLBACK_WINDOW = 128_000
WARN_AT = 0.75          # yellow
CRITICAL_AT = 0.90      # red: the UI suggests summarising


def window_for(lite_model: str, override: int | None = None) -> int:
    if override:
        return int(override)
    try:
        info = litellm.get_model_info(lite_model)
        n = info.get("max_input_tokens") or info.get("max_tokens")
        if n:
            return int(n)
    except Exception:  # noqa: BLE001 - unknown model: use a safe default
        pass
    return FALLBACK_WINDOW


def est_tokens(text: str) -> int:
    return max(1, round(len(text) / 3.6)) if text else 0


def usage_for(messages: list[dict]) -> dict:
    """Current context usage from the saved conversation."""
    last_call = None
    for m in reversed(messages):
        calls = (m.get("meta") or {}).get("calls") or []
        if calls:
            last_call = calls[-1]
            break
    if last_call and last_call.get("usage_reported", True) and last_call.get("input_tokens") is not None:
        used = int(last_call["input_tokens"]) + int(last_call.get("output_tokens") or 0)
        source = "reported by the provider (last call)"
    else:
        used = sum(est_tokens(m.get("text") or "") for m in messages if m.get("role") in ("user", "bot", "summary")) + 1500
        source = "estimated from the chat text"
    return dict(used_tokens=used, source=source)


def summary_point(messages: list[dict]) -> tuple[dict | None, list[dict]]:
    """(latest summary message or None, messages that come after it)."""
    idx = next((i for i in range(len(messages) - 1, -1, -1) if messages[i]["role"] == "summary"), None)
    if idx is None:
        return None, messages
    return messages[idx], messages[idx + 1:]


def level(pct: float) -> str:
    return "critical" if pct >= CRITICAL_AT else "warn" if pct >= WARN_AT else "ok"


SUMMARY_PROMPT = (
    "Summarise the conversation below so the work can continue later without the full transcript. Keep: the user's goals and "
    "constraints, decisions made and why, facts and results found, files and commands involved, what is done, what is still "
    "open, and any open questions. Write it as a briefing in Markdown, under 1,200 words. Do not add anything that was not said."
)


def transcript(messages: list[dict]) -> str:
    out = []
    for m in messages:
        if m["role"] == "summary":
            out.append(f"[Earlier summary]\n{m['text']}")
        elif m["role"] in ("user", "bot") and m.get("text"):
            who = "User" if m["role"] == "user" else "Assistant"
            out.append(f"{who}: {m['text']}")
    return "\n\n".join(out)


def summary_meta(messages: list[dict], provider: str, model: str | None, usage: dict[str, Any] | None) -> dict:
    last = messages[-1] if messages else None
    return dict(summary=True, upto_id=last["id"] if last else None, covered=len(messages), provider=provider, model=model,
                ts=time.time(), usage=usage or {})


def meta_json(d: dict) -> str:
    return json.dumps(d, default=str)
