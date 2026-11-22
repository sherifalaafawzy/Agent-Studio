"""Load each provider's available models straight from its API (so nobody has to type model names).

Uses the provider's configured credentials (key / env var / service account / ADC). If the live call fails (no key yet,
offline, endpoint not supported) it falls back to LiteLLM's built-in catalog for that provider and says so.
Results are cached for 10 minutes per provider + credential.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any

import httpx
import litellm

from .providers import CATALOG, VERTEX_EXPRESS_BASE, Provider, _key, price_of

CACHE_TTL_S = 600
_cache: dict[str, tuple[float, dict]] = {}

# LiteLLM cost-map provider names, for the offline fallback
_LITELLM_PROVIDER = {"openai": "openai", "anthropic": "anthropic", "vertex_ai": "vertex_ai-language-models", "gemini": "gemini",
                     "xai": "xai", "groq": "groq", "openrouter": "openrouter", "ollama": "ollama", "bedrock": ("bedrock", "bedrock_converse")}
_NOT_CHAT = re.compile(r"(embed|tts|whisper|dall-e|davinci|babbage|moderation|transcri|audio|realtime|image|search-preview|computer-use|veo|imagen|lyria|aqa|robotics|live)", re.I)


async def _get_json(url: str, headers: dict | None = None, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        r = await c.get(url, headers=headers or {}, params=params)
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {})
                msg = msg.get("message") if isinstance(msg, dict) else msg
            except Exception:  # noqa: BLE001
                msg = r.text[:200]
            raise RuntimeError(f"HTTP {r.status_code}: {str(msg)[:240]}")
        return r.json()


def _google_token(p: Provider) -> tuple[str, str | None]:
    """OAuth token for Vertex (ADC or a service account). Runs the google-auth refresh (blocking -> call in a thread)."""
    import google.auth
    import google.auth.transport.requests
    from google.oauth2 import service_account
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    if p.mode == "service_account_file":
        creds = service_account.Credentials.from_service_account_file(p.vertex_credentials_path, scopes=scopes)
        project = p.vertex_project or creds.project_id
    elif p.mode == "service_account_json":
        info = json.loads(p.vertex_credentials_json or "{}")
        creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        project = p.vertex_project or info.get("project_id")
    else:
        creds, project = google.auth.default(scopes=scopes)
        project = p.vertex_project or project
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token, project


def _bedrock_models(p: Provider) -> list[dict]:
    """List Bedrock text models + system inference profiles with boto3 (blocking -> run in a thread)."""
    import boto3
    region = p.aws_region or None
    if p.mode in ("api_key", "env"):
        raise RuntimeError("model listing needs AWS credentials; a Bedrock API key only works for inference")
    if p.mode == "aws_keys":
        sess = boto3.Session(aws_access_key_id=p.aws_access_key_id, aws_secret_access_key=p.aws_secret_access_key,
                             aws_session_token=p.aws_session_token or None, region_name=region)
    elif p.mode == "aws_profile":
        sess = boto3.Session(profile_name=p.aws_profile, region_name=region)
    else:
        sess = boto3.Session(region_name=region)
    bd = sess.client("bedrock")
    out = []
    for m in bd.list_foundation_models(byOutputModality="TEXT").get("modelSummaries", []):
        if "TEXT" in (m.get("inputModalities") or []) and "ON_DEMAND" in (m.get("inferenceTypesSupported") or []) \
                and (m.get("modelLifecycle") or {}).get("status", "ACTIVE") == "ACTIVE":
            out.append({"id": m["modelId"], "label": f"{m.get('providerName', '')} {m.get('modelName', '')}".strip()})
    token = None
    for _ in range(20):                                     # cross-region inference profiles (required by most new models)
        r = bd.list_inference_profiles(typeEquals="SYSTEM_DEFINED", **({"nextToken": token} if token else {}))
        for ip in r.get("inferenceProfileSummaries", []):
            if ip.get("status", "ACTIVE") == "ACTIVE":
                out.append({"id": ip["inferenceProfileId"], "label": ip.get("inferenceProfileName") or ip["inferenceProfileId"]})
        token = r.get("nextToken")
        if not token:
            break
    return out


async def _live(p: Provider) -> list[dict]:
    k, base = p.kind, (p.api_base or "").rstrip("/")
    if k == "bedrock":
        return await asyncio.to_thread(_bedrock_models, p)
    if p.custom:
        if not base:
            raise RuntimeError("set the endpoint address first")
        hdr = {"Authorization": f"Bearer {_key(p, p.label)[0]}"} if p.mode != "none" else {}
        if p.organization:
            hdr["OpenAI-Organization"] = p.organization
        data = (await _get_json(f"{base}/models", {**hdr, **p.headers()})).get("data", [])
        out = []
        for m in data:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            row = {"id": m["id"]}
            pr = m.get("pricing")                          # OpenRouter-style gateways publish USD per token
            if isinstance(pr, dict):
                try:
                    row.update(input_per_m=float(pr["prompt"]) * 1e6, output_per_m=float(pr["completion"]) * 1e6)
                except (KeyError, TypeError, ValueError):
                    pass
            out.append(row)
        return out
    if k in ("openai", "xai", "groq"):
        default = {"openai": "https://api.openai.com/v1", "xai": "https://api.x.ai/v1", "groq": "https://api.groq.com/openai/v1"}[k]
        hdr = {}
        if p.mode != "none":
            hdr["Authorization"] = f"Bearer {_key(p, CATALOG[k]['label'])[0]}"
        if p.organization and k == "openai":
            hdr["OpenAI-Organization"] = p.organization
        data = (await _get_json(f"{base or default}/models", {**hdr, **p.headers()})).get("data", [])
        ids = [m["id"] for m in data if m.get("id")]
        if k == "openai" and not base:   # the real OpenAI list mixes in embeddings / audio / image models
            ids = [i for i in ids if re.match(r"^(gpt|o\d|chatgpt)", i) and not _NOT_CHAT.search(i)]
        return [{"id": i} for i in ids]
    if k == "anthropic":
        hdr = {"anthropic-version": "2023-06-01"}
        if p.mode != "none":
            hdr["x-api-key"] = _key(p, "Anthropic")[0]
        data = (await _get_json(f"{base or 'https://api.anthropic.com'}/v1/models", {**hdr, **p.headers()}, {"limit": 1000})).get("data", [])
        return [{"id": m["id"], "label": m.get("display_name")} for m in data]
    if k == "gemini":
        key = _key(p, "Gemini API")[0]
        data = (await _get_json(f"{base or 'https://generativelanguage.googleapis.com/v1beta'}/models", {"x-goog-api-key": key}, {"pageSize": 1000})).get("models", [])
        return [{"id": m["name"].split("/", 1)[-1], "label": m.get("displayName")} for m in data
                if "generateContent" in (m.get("supportedGenerationMethods") or []) and not _NOT_CHAT.search(m["name"])]
    if k == "vertex_ai":
        if p.mode in ("api_key", "env"):
            hdr = {"x-goog-api-key": _key(p, "Vertex AI")[0]}
            url = "https://aiplatform.googleapis.com/v1beta1/publishers/google/models"
        else:
            token, project = await asyncio.to_thread(_google_token, p)
            hdr = {"Authorization": f"Bearer {token}", **({"x-goog-user-project": project} if project else {})}
            loc = p.vertex_location or "global"
            host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
            url = f"https://{host}/v1beta1/publishers/google/models"
        out, token_ = [], None
        for _ in range(10):                              # paginate
            j = await _get_json(url, {**hdr, **p.headers()}, {"pageSize": 200, "listAllVersions": "false", **({"pageToken": token_} if token_ else {})})
            for m in j.get("publisherModels", []):
                mid = m.get("name", "").rsplit("/", 1)[-1]
                if mid.startswith("gemini") and not _NOT_CHAT.search(mid):
                    out.append({"id": mid})
            token_ = j.get("nextPageToken")
            if not token_:
                break
        return out
    if k == "openrouter":
        hdr = {"Authorization": f"Bearer {_key(p, 'OpenRouter')[0]}"} if p.mode != "none" and (p.api_key or p.mode == "env") else {}
        data = (await _get_json(f"{base or 'https://openrouter.ai/api/v1'}/models", {**hdr, **p.headers()})).get("data", [])
        out = []
        for m in data:
            pr = m.get("pricing") or {}
            try:
                pi, po = float(pr.get("prompt")) * 1e6, float(pr.get("completion")) * 1e6
            except (TypeError, ValueError):
                pi = po = None
            out.append({"id": m["id"], "label": m.get("name"), "input_per_m": pi, "output_per_m": po})
        return out
    if k == "ollama":
        hdr = {"Authorization": f"Bearer {_key(p, 'Ollama')[0]}"} if p.mode in ("api_key", "env") else {}
        data = (await _get_json(f"{base or 'http://127.0.0.1:11434'}/api/tags", {**hdr, **p.headers()})).get("models", [])
        return [{"id": m["name"], "input_per_m": 0.0, "output_per_m": 0.0} for m in data]
    return []


def forget(pid: str) -> None:
    """Drop cached model lists for a provider (e.g. after pulling an Ollama model)."""
    for k in [k for k, (_, r) in _cache.items() if r.get("provider") == pid]:
        _cache.pop(k, None)


OLLAMA_SUGGESTIONS = [   # good, tool-capable picks for agents (any name from ollama.com/library works)
    {"id": "qwen2.5-coder:7b", "note": "coding, tool use · ~4.7 GB"},
    {"id": "qwen3:8b", "note": "general + tools · ~5.2 GB"},
    {"id": "llama3.1:8b", "note": "general + tools · ~4.9 GB"},
    {"id": "llama3.2:3b", "note": "small & fast · ~2 GB"},
    {"id": "mistral:7b", "note": "general · ~4.1 GB"},
    {"id": "gemma3:4b", "note": "small, no tool calls · ~3.3 GB"},
    {"id": "deepseek-r1:8b", "note": "reasoning, no tool calls · ~5.2 GB"},
    {"id": "gpt-oss:20b", "note": "strong, needs ~16 GB RAM · ~14 GB"},
]


def catalog(kind: str) -> list[dict]:
    """Offline fallback: chat models LiteLLM knows for this provider (with prices)."""
    prov = _LITELLM_PROVIDER.get(kind)
    provs = prov if isinstance(prov, tuple) else (prov,)
    out = []
    for name, v in litellm.model_cost.items():
        if v.get("litellm_provider") not in provs or v.get("mode") not in ("chat", None) or _NOT_CHAT.search(name):
            continue
        mid = name.split("/", 1)[1] if name.startswith(f"{kind}/") or name.startswith("gemini/") or name.startswith("vertex_ai/") else name
        if kind == "openai" and not re.match(r"^(gpt|o\d|chatgpt)", mid):
            continue
        if kind == "bedrock" and mid.startswith("bedrock/"):
            mid = mid[len("bedrock/"):]
        if mid.startswith("ft:") or "/" in mid and kind not in ("openrouter",):
            continue
        out.append({"id": mid})
    return out


def _fingerprint(p: Provider) -> str:
    raw = json.dumps([p.id, p.mode, p.api_base, p.api_key, p.api_key_env, p.vertex_project, p.vertex_location,
                      p.vertex_credentials_path, bool(p.vertex_credentials_json), p.organization, p.aws_region,
                      p.aws_access_key_id, p.aws_secret_access_key, p.aws_profile, p.extra_headers, p.model_prices, p.manual_models])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _annotate(p: Provider, models: list[dict]) -> list[dict]:
    prefix = "gemini/" if p.kind == "vertex_ai" and p.mode in ("api_key", "env") else CATALOG[p.kind]["prefix"]
    seen, out = set(), []
    for m in models:
        mid = m["id"]
        if mid in seen:
            continue
        seen.add(mid)
        if m.get("input_per_m") is None:
            pr = price_of(p, mid, prefix + mid)
            m = {**m, "input_per_m": pr.input_per_m if pr.known else None, "output_per_m": pr.output_per_m if pr.known else None}
        out.append({"id": mid, "label": m.get("label") or mid, "input_per_m": m.get("input_per_m"), "output_per_m": m.get("output_per_m")})
    out.sort(key=lambda r: r["id"])
    return out


async def list_models(p: Provider, refresh: bool = False) -> dict:
    fp = _fingerprint(p)
    hit = _cache.get(fp)
    if hit and not refresh and time.time() - hit[0] < CACHE_TTL_S:
        return {**hit[1], "cached": True}
    if p.kind == "ollama":        # only models actually installed locally - catalog names would just fail with "not found"
        base = (p.api_base or "http://127.0.0.1:11434").rstrip("/")
        try:
            models = _annotate(p, await _live(p))
            err = None if models else ("No models are installed in Ollama yet. Download one in Settings -> Ollama (Download a model), "
                                       "or run `ollama pull <model>` in a terminal.")
        except Exception as e:  # noqa: BLE001
            models, err = [], f"Ollama isn't reachable at {base} ({type(e).__name__}). Start the Ollama app, then reload the list."
        res = dict(provider=p.id, source="live", error=err, models=models)
        _cache[fp] = (time.time(), res)
        return {**res, "cached": False}
    if p.custom:                  # the endpoint's own list plus the names typed in by hand; a failed lookup keeps the typed-in ones usable
        try:
            live, err = await _live(p), None
        except Exception as e:  # noqa: BLE001
            live, err = [], f"{type(e).__name__}: {e}"[:300]
        typed = [{"id": m} for m in p.manual()]
        models = _annotate(p, live + typed)
        if not err and not live:
            err = "the endpoint returned no models; type the model names by hand"
        res = dict(provider=p.id, source="live" if live else "manual", error=err, models=models)
        _cache[fp] = (time.time(), res)
        return {**res, "cached": False}
    try:
        models = await _live(p)
        res = dict(provider=p.id, source="live", error=None, models=_annotate(p, models))
        if not res["models"]:
            raise RuntimeError("the provider returned no chat models")
    except Exception as e:  # noqa: BLE001 - fall back to the catalog, but say why
        res = dict(provider=p.id, source="catalog", error=f"{type(e).__name__}: {e}"[:300], models=_annotate(p, catalog(p.kind)))
    _cache[fp] = (time.time(), res)
    return {**res, "cached": False}
