"""Turn raw provider / library exceptions into a short, actionable message for the UI.

Every error keeps its original text under `raw` (the UI shows it behind a "Details" toggle), so nothing is hidden.
"""
from __future__ import annotations

import re

RULES: list[tuple[re.Pattern, dict]] = [
    (re.compile(r"thinking|signature", re.I),
     dict(title="The provider rejected the conversation's reasoning history", hint="Agent Studio rebuilds the history from the saved text and retries once. If the same error comes back, start a new chat.", retryable=True, code="reasoning_history")),
    (re.compile(r"BadRequest|invalid_request|400", re.I),
     dict(title="The provider rejected the request", hint="The request was not accepted (often a conversation or tool-history problem). Try again; if it persists, start a new chat or shorten the conversation.", retryable=True, code="bad_request")),
    (re.compile(r"insufficient[_ ]quota|credit balance|billing|payment required|402", re.I),
     dict(title="The provider account is out of credit", hint="Top up the provider account, or update its credit balance in Settings so Agent Studio blocks calls before they are sent.", retryable=False, code="credits")),
    (re.compile(r"429|resource[_ ]exhausted|rate.?limit|too many requests|quota", re.I),
     dict(title="Rate limit or quota reached", hint="The provider is refusing requests right now. Wait a minute and retry. If it keeps happening, check your plan's quota or billing in the provider's console.", retryable=True, code="rate_limit")),
    (re.compile(r"401|unauthori[sz]ed|invalid api key|api key not valid|incorrect api key|authentication|invalid_api_key|api_key.*invalid", re.I),
     dict(title="The API key was rejected", hint="Check the key in Settings → Model providers. Make sure it is for the right account and has not been revoked.", retryable=False, code="auth")),
    (re.compile(r"403|permission[_ ]denied|forbidden|not authorized to|access denied", re.I),
     dict(title="Access denied by the provider", hint="This key or account is not allowed to use this model or feature. Enable the API / model in the provider's console, or pick another model.", retryable=False, code="permission")),
    (re.compile(r"not[_ ]found|does not exist|no such model|is not a valid model|404", re.I),
     dict(title="Model not found", hint="The model name doesn't exist for this provider or isn't available to your account. Pick one from the model list (Settings → Model providers, or the chat's model menu).", retryable=False, code="model_not_found")),
    (re.compile(r"context.?length|context window|maximum context|too many tokens|prompt is too long|input is too long", re.I),
     dict(title="The conversation is too long for this model", hint="Start a new chat, shorten the message, or pick a model with a larger context window.", retryable=False, code="context_length")),
    (re.compile(r"overloaded|503|service unavailable|unavailable|internal server error|500|bad gateway|502", re.I),
     dict(title="The provider is temporarily unavailable", hint="Its servers are busy or having problems. Wait a moment and retry, or switch to another provider.", retryable=True, code="unavailable")),
    (re.compile(r"timed? ?out|timeout|deadline exceeded", re.I),
     dict(title="The request timed out", hint="The model took too long to answer. Retry, lower the max output tokens, or use a faster model.", retryable=True, code="timeout")),
    (re.compile(r"connect|connection refused|name resolution|network|unreachable", re.I),
     dict(title="Can't reach the provider", hint="Check your internet connection (or the local server, for Ollama) and retry.", retryable=True, code="network")),
    (re.compile(r"guard|budget|cap", re.I),
     dict(title="Blocked by a spend limit", hint="A cap was reached before the call was sent. Raise the cap in Settings if you want to continue.", retryable=False, code="cap")),
]

OLLAMA_RULES: list[tuple[re.Pattern, dict]] = [
    (re.compile(r"model '([^']+)' not found|model \"([^\"]+)\" not found", re.I),
     dict(title="Ollama model not installed", hint="Download it: Settings → Model providers → Ollama → Download a model (or run `ollama pull <name>` in a terminal).", retryable=False, code="model_not_found")),
    (re.compile(r"bad_alloc|out of memory|failed to initialize the context|cudamalloc|llama-server process has terminated", re.I),
     dict(title="Ollama ran out of memory", hint="Lower the context window (Settings → Ollama, e.g. 8192), close other GPU-heavy apps, or use a smaller model.", retryable=True, code="oom")),
    (re.compile(r"connect|refused|unreachable", re.I),
     dict(title="Ollama isn't running", hint="Start the Ollama app and try again.", retryable=True, code="network")),
]


def classify(exc: BaseException, provider_kind: str | None = None) -> dict:
    raw = f"{type(exc).__name__}: {exc}"
    text = f"{type(exc).__name__} {exc}"
    if provider_kind == "ollama":
        for rx, info in OLLAMA_RULES:
            if rx.search(text):
                return {**info, "raw": raw[:1500]}
    if type(exc).__name__ == "RateLimitError":
        return {**next(info for _, info in RULES if info["code"] == "rate_limit"), "raw": raw[:1500]}
    for rx, info in RULES:
        if rx.search(text):
            return {**info, "raw": raw[:1500]}
    return dict(title="Something went wrong", hint="Retry the message. If it keeps failing, open Details and check the provider's status page.",
                retryable=True, code="unknown", raw=raw[:1500])


def as_event(exc: BaseException, provider_kind: str | None = None) -> dict:
    info = classify(exc, provider_kind)
    suggestions = list(getattr(exc, "suggestions", None) or [])
    hint = info["hint"] + (" Or switch to another model below." if suggestions and not info["retryable"] else "")
    return {"type": "error", "title": info["title"], "message": f"{info['title']}. {hint}", "hint": hint,
            "code": info["code"], "retryable": info["retryable"], "raw": info["raw"], "suggestions": suggestions}
