"""Custom OpenAI-compatible endpoints: probing a not-yet-saved address and the Test button.

Both talk to the endpoint with plain HTTP (not LiteLLM) so the failure can be named exactly: wrong key, unreachable, certificate
problem, model not found, wrong address, rate limit. The Test call is one tiny chat request that goes through the spend guard like
any other model call, so it is capped and shows up in the ledger.
"""
from __future__ import annotations

import ssl
import time

import httpx

from .budget import BudgetExceeded
from .models import _live
from .providers import CUSTOM_KIND, SCOPE_LABEL, Provider, _resolve, network_scope, normalize_openai_base

TEST_PROMPT = "Reply with the single word: ok"
TEST_MAX_TOKENS = 8
TIMEOUT_S = 30


def transient(api_base: str, auth_mode: str = "none", api_key: str | None = None, api_key_env: str | None = None,
              headers: dict | None = None, organization: str | None = None) -> Provider:
    """A Provider that is never stored: lets the address and key be tried before anything is saved."""
    import json
    return Provider(id="custom_probe", kind=CUSTOM_KIND, label="this endpoint", enabled=True, api_key=(api_key or "").strip() or None,
                    api_base=normalize_openai_base(api_base), vertex_project=None, vertex_location=None, vertex_credentials_path=None,
                    default_model=None, cap_usd=0.0, cap_period="total", max_output_tokens=16, auth_mode=auth_mode,
                    api_key_env=(api_key_env or "").strip() or None, extra_headers=json.dumps(headers or {}), organization=organization)


async def discover(p: Provider) -> dict:
    """The models an endpoint says it serves (GET {base}/models), or why it could not be asked."""
    if p.mode == "api_key" and not p.api_key:
        return {"ok": False, "kind": "config", "title": "No key yet", "detail": "Type the API key above, or choose 'No key' if the endpoint needs none."}
    try:
        models = await _live(p)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, **explain_exception(e, p)}
    return {"ok": True, "models": sorted({m["id"] for m in models}), "count": len(models),
            "detail": f"{len(models)} model(s) found" if models else "The endpoint answered but lists no models; type the model name by hand."}


def explain_status(status: int, body: str, p: Provider, model: str) -> dict:
    """A plain-language reason for an HTTP error from the endpoint."""
    low = (body or "").lower()
    if status in (401, 403):
        return dict(kind="auth", title="The key was refused",
                    detail=f"The endpoint rejected the credentials (HTTP {status}). Check the API key, and any extra headers it needs.")
    if status == 404 and ("model" in low and ("not found" in low or "does not exist" in low or "not_found" in low or "unknown" in low)):
        return dict(kind="model", title="Model not found", detail=f"The endpoint does not know a model called '{model}'. Use 'Find models' to see what it serves.")
    if status == 404:
        return dict(kind="address", title="Wrong address",
                    detail="The endpoint answered 404 for the chat route. The address usually ends in /v1 (for example http://host:8000/v1).")
    if status == 429:
        return dict(kind="rate", title="Rate limited or out of credit", detail="The endpoint is limiting you (HTTP 429): too many requests, or no credit left on that account.")
    if status == 400:
        return dict(kind="request", title="The endpoint refused the request", detail=f"HTTP 400: {body[:200] or 'no details'}")
    if status >= 500:
        return dict(kind="server", title="The endpoint had an error", detail=f"HTTP {status}: the server failed ({body[:160] or 'no details'}). Try again, or check its logs.")
    return dict(kind="other", title="Unexpected answer", detail=f"HTTP {status}: {body[:200] or 'no details'}")


def explain_exception(e: BaseException, p: Provider) -> dict:
    host = (p.api_base or "").split("//")[-1].split("/")[0]
    text = f"{type(e).__name__}: {e}"
    cause = e.__cause__ or e.__context__
    if isinstance(e, ssl.SSLError) or isinstance(cause, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in text or "[SSL" in text:
        return dict(kind="tls", title="Certificate problem",
                    detail=f"The secure connection to {host} failed its certificate check. A self-signed or company certificate must be trusted by this PC, or use http:// for a server on your own network.")
    if isinstance(e, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout, TimeoutError)):
        return dict(kind="timeout", title="Timed out", detail=f"{host} did not answer in time. It may be busy, loading a model, or blocked by a firewall.")
    if isinstance(e, httpx.ConnectError):
        return dict(kind="unreachable", title="Cannot reach it", detail=f"Nothing answered at {host}. Check the address and port, that the server is running, and that this PC can reach it.")
    if isinstance(e, ValueError):
        return dict(kind="config", title="Not set up yet", detail=str(e))
    msg = str(e)
    if msg.startswith("HTTP "):                                   # raised by models._get_json
        code = int(msg[5:8]) if msg[5:8].isdigit() else 0
        return explain_status(code, msg[msg.find(":") + 1:].strip(), p, p.default_model or "")
    return dict(kind="other", title="The call failed", detail=text[:300])


async def test_endpoint(p: Provider, model: str | None = None, guard=None, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """One tiny chat call. Returns {ok, kind, title, detail, latency_ms, model, reply, scope}. Never raises."""
    model = (model or p.default_model or "").strip()
    scope = network_scope(p.api_base)
    base = dict(model=model, scope=scope, scope_label=SCOPE_LABEL[scope])
    if not model:
        return dict(ok=False, kind="config", title="No model chosen", detail="Choose a model first (use 'Find models').", **base)
    try:
        lite, kw, _ = _resolve(p, model)
    except ValueError as e:
        return dict(ok=False, kind="config", title="Not set up yet", detail=str(e), **base)
    reservation = None
    if guard is not None:
        try:
            reservation = guard.reserve(p.id, model, 30, TEST_MAX_TOKENS, lite)
        except BudgetExceeded as e:
            return dict(ok=False, kind="budget", title="Blocked by the spend cap", detail=e.detail["message"], **base)
    headers = {"Content-Type": "application/json", **(kw.get("extra_headers") or {})}
    if kw.get("api_key") and kw["api_key"] != "no-key":
        headers["Authorization"] = f"Bearer {kw['api_key']}"
    if kw.get("organization"):
        headers["OpenAI-Organization"] = kw["organization"]
    body = {"model": model, "messages": [{"role": "user", "content": TEST_PROMPT}], "max_tokens": TEST_MAX_TOKENS}
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport, follow_redirects=False) as c:
            r = await c.post(f"{p.api_base}/chat/completions", headers=headers, json=body)
    except Exception as e:  # noqa: BLE001
        if reservation is not None:
            guard.release(reservation, "test_failed")
        return dict(ok=False, **explain_exception(e, p), **base)
    ms = int((time.monotonic() - t0) * 1000)
    if r.status_code >= 400:
        if reservation is not None:
            guard.release(reservation, "test_failed")
        return dict(ok=False, latency_ms=ms, **explain_status(r.status_code, r.text, p, model), **base)
    try:
        j = r.json()
        reply = str(((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        usage = j.get("usage") or {}
    except (ValueError, AttributeError, IndexError, TypeError):
        if reservation is not None:
            guard.release(reservation, "test_failed")
        return dict(ok=False, kind="other", title="Not an OpenAI-style answer", latency_ms=ms,
                    detail="The endpoint answered, but not with an OpenAI chat response. Check that the address is the OpenAI-compatible one (usually ending in /v1).", **base)
    if reservation is not None:
        guard.settle(reservation, usage.get("prompt_tokens"), usage.get("completion_tokens"), "endpoint_test")
    return dict(ok=True, kind="ok", title="Works", detail=f"{model} answered in {ms} ms.", latency_ms=ms, reply=reply[:80], **base)
