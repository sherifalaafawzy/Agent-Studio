"""Provider catalog + provider config store + LiteLLM kwargs builder.

Every connector accepts every way its service can authenticate (`auth_mode`), plus an optional custom endpoint
(`api_base`) and extra request headers. Secrets (API keys, pasted service-account JSON, header values) are stored
locally and never returned by the API.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .db import Database
from .pricing import Price, price_for

# Vertex AI "express mode": Google Cloud API keys (they look like `AQ.…`) call the publisher-model endpoint directly.
VERTEX_EXPRESS_BASE = "https://aiplatform.googleapis.com/v1/publishers/google"

AUTH_LABELS = {
    "adc": "Application Default Credentials (gcloud auth application-default login / GOOGLE_APPLICATION_CREDENTIALS)",
    "service_account_file": "Service-account JSON file (path)",
    "service_account_json": "Service-account JSON (pasted, stored locally)",
    "api_key": "API key (stored locally)",
    "env": "API key from an environment variable",
    "none": "No authentication (local server / proxy that adds auth)",
    "aws_default": "AWS default credentials (env vars, ~/.aws, SSO, instance/role)",
    "aws_keys": "AWS access key + secret (stored locally)",
    "aws_profile": "AWS named profile (~/.aws/config)",
}

# kind -> label, litellm prefix, default model, auth modes (first = default), note
CATALOG: dict[str, dict] = {
    "vertex_ai": dict(label="Google Vertex AI (Gemini)", prefix="vertex_ai/", default_model="gemini-3.8-flash",
                      auth_modes=["adc", "service_account_file", "service_account_json", "api_key", "env"],
                      key_env="GOOGLE_API_KEY",
                      note="Project + ADC / service account, or a Vertex API key (express mode, keys starting AQ.)."),
    "gemini": dict(label="Google Gemini API (AI Studio)", prefix="gemini/", default_model="gemini-3.8-flash",
                   auth_modes=["api_key", "env"], key_env="GEMINI_API_KEY", note="API key from aistudio.google.com."),
    "openai": dict(label="OpenAI", prefix="openai/", default_model="gpt-5-mini", auth_modes=["api_key", "env", "none"],
                   key_env="OPENAI_API_KEY", note="Optional organization; API base for Azure-style gateways / OpenAI-compatible servers."),
    "anthropic": dict(label="Anthropic", prefix="anthropic/", default_model="claude-sonnet-5-5", auth_modes=["api_key", "env", "none"],
                      key_env="ANTHROPIC_API_KEY", note=""),
    "xai": dict(label="xAI (Grok)", prefix="xai/", default_model="grok-4", auth_modes=["api_key", "env", "none"], key_env="XAI_API_KEY", note=""),
    "groq": dict(label="Groq", prefix="groq/", default_model="llama-3.3-70b-versatile", auth_modes=["api_key", "env", "none"],
                 key_env="GROQ_API_KEY", note=""),
    "openrouter": dict(label="OpenRouter", prefix="openrouter/", default_model="anthropic/claude-sonnet-5-5",
                       auth_modes=["api_key", "env", "none"], key_env="OPENROUTER_API_KEY", note=""),
    "bedrock": dict(label="Amazon Bedrock", prefix="bedrock/", default_model="us.anthropic.claude-sonnet-5-5",
                    auth_modes=["aws_default", "aws_keys", "aws_profile", "api_key", "env"], key_env="AWS_BEARER_TOKEN_BEDROCK",
                    note="Region + AWS credentials (default chain, keys, profile) or a Bedrock API key. Newer models need a cross-region "
                         "inference profile id (us./eu./apac./global.) - the model list shows them."),
    "ollama": dict(label="Ollama (local)", prefix="ollama_chat/", default_model="llama3.2", auth_modes=["none", "api_key", "env"],
                   key_env="OLLAMA_API_KEY", note="Free/local; api_base defaults to http://127.0.0.1:11434. Key only for a remote/proxied Ollama."),
    "mock": dict(label="Mock (no network, no spend)", prefix="openai/", default_model="mock-expensive", auth_modes=["none"],
                 key_env="", note="Offline test provider with artificially high prices - for demoing the cap guard."),
    # user-defined endpoints: any number of rows share this kind (see ProviderStore.create_custom)
    "openai_compatible": dict(label="OpenAI-compatible endpoint", prefix="openai/", default_model="", auth_modes=["api_key", "env", "none"],
                              key_env="", custom=True, note="Any server that speaks the OpenAI chat API (vLLM, LM Studio, a LiteLLM proxy, OpenRouter, Together, "
                              "Fireworks, a company gateway...)."),
}
CUSTOM_KIND = "openai_compatible"
for _c in CATALOG.values():   # back-compat field used by the UI / older code
    _c["needs_key"] = "none" not in _c["auth_modes"] and _c["auth_modes"][0] in ("api_key", "env")

# Pro/Flash tiering defaults per kind (names come from LiteLLM's cost map on 2026-11-20 - verify in Settings).
TIERS = {
    "vertex_ai": ("gemini-3.1-pro-preview", "gemini-3.8-flash"),
    "gemini": ("gemini-3.1-pro-preview", "gemini-3.8-flash"),
    "openai": ("gpt-5", "gpt-5-mini"),
    "anthropic": ("claude-opus-5-5", "claude-haiku-5-5"),
    "xai": ("grok-4", "grok-4"),
    "groq": ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
    "openrouter": ("anthropic/claude-sonnet-5-5", "google/gemini-3.8-flash"),
    "bedrock": ("us.anthropic.claude-sonnet-5-5", "us.anthropic.claude-haiku-5-5"),
    "ollama": ("llama3.2", "llama3.2"),
    "mock": ("mock-expensive", "mock-cheap"),
}

DEFAULT_CAP_USD = {"mock": 0.25, "ollama": 0.0}
OLLAMA_DEFAULT_CTX = 16384
SAFE_DEFAULT_CAP_USD = 1.0  # new paid providers start with a small, safe cap
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def mock_enabled() -> bool:
    """The offline mock provider exists only for the automated tests / demos (AGENT_STUDIO_ENABLE_MOCK=1)."""
    return os.environ.get("AGENT_STUDIO_ENABLE_MOCK") == "1"


def network_scope(url: str | None) -> str:
    """Where an endpoint is: 'this_pc' (loopback), 'network' (a private/LAN address or an internal-looking name) or 'internet'.
    No DNS lookup (a label must not depend on the network): a dotted public-looking name is assumed to be on the internet."""
    host = (urlsplit(url or "").hostname or "").lower()
    if not host:
        return "internet"
    if host == "localhost" or host.endswith(".localhost"):
        return "this_pc"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback:
            return "this_pc"
        return "network" if (ip.is_private or ip.is_link_local or ip.is_unspecified) else "internet"
    except ValueError:
        pass
    if "." not in host or host.endswith((".local", ".lan", ".internal", ".home", ".corp", ".intranet", ".localdomain", ".docker", ".test")):
        return "network"
    return "internet"


SCOPE_LABEL = {"this_pc": "on this PC", "network": "on this network", "internet": "data leaves this PC"}


def normalize_openai_base(url: str) -> str:
    """The address LiteLLM and the model list use: http(s) only, no trailing slash, and `/v1` added when only a host was given."""
    u = urlsplit((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("the endpoint address must start with http:// or https:// and include a host")
    if u.username or u.password:
        raise ValueError("do not put a password in the address; use the API key field")
    path = u.path.rstrip("/")
    return urlunsplit((u.scheme, u.netloc, path or "/v1", "", "")).rstrip("/")


def slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:30] or "endpoint"


def price_of(p: "Provider", model: str, litellm_model: str) -> Price:
    """The price of one model on one provider: a custom endpoint's own prices first, then the usual lookup.
    An endpoint can carry a default price ("*", e.g. 0 for your own vLLM); with none, an unknown model keeps the conservative fallback."""
    if p.custom:
        e = p.prices().get(model) or p.prices().get("*")
        if isinstance(e, dict) and "input_per_m" in e:
            return Price(float(e["input_per_m"]), float(e["output_per_m"]), True, "endpoint")
        return price_for(p.id, model, litellm_model)
    return price_for(p.kind, model, litellm_model)


def context_of(p: "Provider", model: str | None) -> int | None:
    """The context size set for a model on a custom endpoint (None = unknown, so LiteLLM's table or the default is used)."""
    if not p.custom:
        return None
    e = p.prices().get(model or "") or p.prices().get("*")
    return int(e["context"]) if isinstance(e, dict) and "context" in e else None


@dataclass
class Provider:
    id: str
    kind: str
    label: str
    enabled: bool
    api_key: str | None
    api_base: str | None
    vertex_project: str | None
    vertex_location: str | None
    vertex_credentials_path: str | None
    default_model: str | None
    cap_usd: float
    cap_period: str
    max_output_tokens: int
    pro_model: str | None = None
    flash_model: str | None = None
    auth_mode: str | None = None
    api_key_env: str | None = None
    vertex_credentials_json: str | None = None
    extra_headers: str | None = None        # JSON object
    organization: str | None = None
    aws_region: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    aws_profile: str | None = None
    context_window: int | None = None       # Ollama num_ctx (None = OLLAMA_DEFAULT_CTX)
    model_prices: str | None = None         # custom endpoints: JSON {"*" or model id: {input_per_m, output_per_m, context}}
    manual_models: str | None = None        # custom endpoints: JSON list of model ids typed by hand

    @classmethod
    def from_row(cls, r) -> "Provider":
        d = dict(r)
        d["enabled"] = bool(d["enabled"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def mode(self) -> str:
        modes = CATALOG[self.kind]["auth_modes"]
        return self.auth_mode if self.auth_mode in modes else modes[0]

    @property
    def custom(self) -> bool:
        return self.kind == CUSTOM_KIND

    def prices(self) -> dict:
        try:
            d = json.loads(self.model_prices or "{}")
            return d if isinstance(d, dict) else {}
        except json.JSONDecodeError:
            return {}

    def manual(self) -> list[str]:
        try:
            d = json.loads(self.manual_models or "[]")
            return [str(x) for x in d] if isinstance(d, list) else []
        except json.JSONDecodeError:
            return []

    def headers(self) -> dict:
        try:
            h = json.loads(self.extra_headers or "{}")
            return {str(k): str(v) for k, v in h.items()} if isinstance(h, dict) else {}
        except json.JSONDecodeError:
            return {}

    def public(self) -> dict:
        d = self.__dict__.copy()
        key = d.pop("api_key")
        sa = d.pop("vertex_credentials_json")
        d.pop("extra_headers")
        d["has_aws_secret"] = bool(d.pop("aws_secret_access_key"))
        d["has_aws_session_token"] = bool(d.pop("aws_session_token"))
        akid = d.pop("aws_access_key_id")
        d["aws_access_key_hint"] = (akid[:4] + "…" + akid[-4:]) if akid and len(akid) > 8 else None
        d["auth_mode"] = self.mode
        d["auth_modes"] = [dict(id=m, label=AUTH_LABELS[m]) for m in CATALOG[self.kind]["auth_modes"]]
        d["has_api_key"] = bool(key)
        d["api_key_hint"] = ("…" + key[-4:]) if key and len(key) > 8 else ("set" if key else None)
        d["has_service_account_json"] = bool(sa)
        d["service_account_email"] = _sa_email(sa)
        d["extra_header_names"] = sorted(self.headers())
        d["key_env_default"] = CATALOG[self.kind]["key_env"]
        d["custom"] = self.custom
        d["model_prices"] = self.prices()
        d["manual_models"] = self.manual()
        d["network_scope"] = network_scope(self.api_base) if self.custom else None
        try:
            _, kw, how = _resolve(self, None)
            d["auth_ready"], d["auth_detail"] = True, how
        except ValueError as e:
            d["auth_ready"], d["auth_detail"] = False, str(e)
        return d


def _sa_email(sa: str | None) -> str | None:
    try:
        return json.loads(sa).get("client_email") if sa else None
    except (json.JSONDecodeError, AttributeError):
        return None


class ProviderStore:
    def __init__(self, db: Database):
        self.db = db
        self._migrate()
        self.seed()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.db.query("PRAGMA table_info(providers)")}
        for c in ("auth_mode", "api_key_env", "vertex_credentials_json", "extra_headers", "organization",
                  "aws_region", "aws_access_key_id", "aws_secret_access_key", "aws_session_token", "aws_profile", "context_window",
                  "model_prices", "manual_models"):
            if c not in cols:
                self.db.execute(f"ALTER TABLE providers ADD COLUMN {c} TEXT")
        # existing Vertex rows: a configured credentials path means service-account-file auth, otherwise ADC
        self.db.execute("UPDATE providers SET auth_mode=CASE WHEN COALESCE(vertex_credentials_path,'')<>'' THEN 'service_account_file' ELSE 'adc' END "
                        "WHERE kind='vertex_ai' AND auth_mode IS NULL")

    def seed(self) -> None:
        if not mock_enabled():
            self.db.execute("DELETE FROM providers WHERE kind='mock'")
        # agent turns need room for reasoning plus a written answer; 2048 starved Claude's answers
        self.db.execute("UPDATE providers SET max_output_tokens=8192 WHERE id IN ('anthropic','bedrock') AND max_output_tokens=2048")
        for kind, c in CATALOG.items():
            if kind == CUSTOM_KIND or (kind == "mock" and not mock_enabled()):
                continue
            if self.db.one("SELECT 1 FROM providers WHERE id=?", (kind,)):
                self.db.execute("UPDATE providers SET pro_model=COALESCE(pro_model,?), flash_model=COALESCE(flash_model,?) WHERE id=?",
                                (*TIERS[kind], kind))
                continue
            cap = DEFAULT_CAP_USD.get(kind, SAFE_DEFAULT_CAP_USD)
            self.db.execute(
                "INSERT INTO providers(id,kind,label,enabled,default_model,cap_usd,cap_period,vertex_location,max_output_tokens,"
                "pro_model,flash_model,auth_mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (kind, kind, c["label"], 1 if kind == "mock" else 0, c["default_model"], cap, "total",
                 "global" if kind == "vertex_ai" else None, 64 if kind == "mock" else (8192 if kind in ("anthropic", "bedrock") else 2048), *TIERS[kind], c["auth_modes"][0]),
            )
            if kind == "bedrock":
                self.db.execute("UPDATE providers SET aws_region='us-east-1' WHERE id='bedrock'")

    def list(self) -> list[Provider]:
        return [Provider.from_row(r) for r in self.db.query("SELECT * FROM providers ORDER BY rowid")]

    def get(self, pid: str) -> Provider | None:
        r = self.db.one("SELECT * FROM providers WHERE id=?", (pid,))
        return Provider.from_row(r) if r else None

    EDITABLE = ("enabled", "api_key", "api_base", "vertex_project", "vertex_location", "vertex_credentials_path", "default_model",
                "pro_model", "flash_model", "cap_usd", "cap_period", "max_output_tokens", "auth_mode", "api_key_env",
                "vertex_credentials_json", "extra_headers", "organization", "aws_region", "aws_access_key_id",
                "aws_secret_access_key", "aws_session_token", "aws_profile", "context_window", "label", "model_prices", "manual_models")
    SECRETS = ("api_key", "vertex_credentials_json", "aws_secret_access_key", "aws_session_token", "aws_access_key_id")   # None = unchanged; "" = clear

    def update(self, pid: str, patch: dict) -> Provider:
        cur = self.get(pid)
        if not cur:
            raise KeyError(pid)
        sets, vals = [], []
        for k in self.EDITABLE:
            if k not in patch:
                continue
            v = patch[k]
            if k in self.SECRETS and v is None:
                continue
            if k == "enabled":
                v = 1 if v else 0
            elif k == "cap_usd":
                v = float(v)
                if v < 0:
                    raise ValueError("cap_usd must be >= 0")
            elif k == "cap_period" and v not in ("total", "daily", "monthly"):
                raise ValueError("cap_period must be total|daily|monthly")
            elif k == "max_output_tokens":
                v = int(v)
                if not 16 <= v <= 1_000_000:
                    raise ValueError("max_output_tokens out of range")
            elif k == "auth_mode":
                if v not in CATALOG[cur.kind]["auth_modes"]:
                    raise ValueError(f"auth_mode for {cur.kind} must be one of {CATALOG[cur.kind]['auth_modes']}")
            elif k == "api_key_env" and v:
                v = v.strip()
                if not ENV_NAME_RE.match(v):
                    raise ValueError("api_key_env must be an environment variable name")
            elif k == "vertex_credentials_json" and v:
                try:
                    j = json.loads(v)
                except json.JSONDecodeError:
                    raise ValueError("service-account JSON is not valid JSON")
                if not isinstance(j, dict) or "type" not in j:
                    raise ValueError("service-account JSON must be a Google credentials object (it has a \"type\" field)")
                v = json.dumps(j)
            elif k == "extra_headers":
                if v is None:
                    continue
                if not isinstance(v, dict):
                    raise ValueError("extra_headers must be an object of header name -> value")
                v = json.dumps({str(a).strip(): str(b) for a, b in v.items() if str(a).strip()})
            elif k == "context_window" and v not in (None, ""):
                v = int(v)
                if not 512 <= v <= 1_048_576:
                    raise ValueError("context_window must be between 512 and 1048576 tokens")
            elif k == "aws_region" and v:
                v = v.strip()
                if not re.match(r"^[a-z]{2}(-gov)?-[a-z]+-\d$", v):
                    raise ValueError("aws_region must look like us-east-1")
            elif k in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token", "aws_profile") and v:
                v = v.strip()
            elif k == "label":
                if not cur.custom:
                    raise ValueError("only custom endpoints can be renamed")
                v = _label(v)
            elif k == "model_prices":
                if not cur.custom:
                    raise ValueError("model prices are set on custom endpoints only (the built-in providers use LiteLLM's price table)")
                v = json.dumps(_clean_prices(v))
            elif k == "manual_models":
                if not cur.custom:
                    raise ValueError("typed-in models are for custom endpoints only")
                v = json.dumps(_clean_models(v))
            elif k == "api_base" and cur.custom:
                v = normalize_openai_base(v or "")
            elif k == "api_base" and v:
                v = v.strip().rstrip("/")
                if not v.startswith(("http://", "https://")):
                    raise ValueError("api_base must start with http:// or https://")
            if v == "" and k not in ("enabled",):
                v = None
            sets.append(f"{k}=?")
            vals.append(v)
        if sets:
            self.db.execute(f"UPDATE providers SET {','.join(sets)} WHERE id=?", (*vals, pid))
        return self.get(pid)  # type: ignore[return-value]


    # ---- custom OpenAI-compatible endpoints
    def create_custom(self, label: str, api_base: str, default_model: str, auth_mode: str = "api_key", api_key: str | None = None,
                      api_key_env: str | None = None, headers: dict | None = None, organization: str | None = None,
                      cap_usd: float = SAFE_DEFAULT_CAP_USD, cap_period: str = "total", max_output_tokens: int = 4096,
                      model_prices: dict | None = None, manual_models: list | None = None, enabled: bool = True) -> Provider:
        label = _label(label)
        base = normalize_openai_base(api_base)
        model = (default_model or "").strip()
        if not model:
            raise ValueError("choose a default model (use 'Find models' to list what the endpoint serves, or type its name)")
        if auth_mode not in CATALOG[CUSTOM_KIND]["auth_modes"]:
            raise ValueError(f"authentication must be one of {CATALOG[CUSTOM_KIND]['auth_modes']}")
        if auth_mode == "env" and not ENV_NAME_RE.match((api_key_env or "").strip()):
            raise ValueError("give the name of the environment variable that holds the key")
        if cap_usd < 0 or cap_period not in ("total", "daily", "monthly") or not 16 <= int(max_output_tokens) <= 1_000_000:
            raise ValueError("check the spend cap, its window and the max output tokens")
        pid = "custom_" + slugify(label)
        taken = {r["id"] for r in self.db.query("SELECT id FROM providers")}
        n, uniq = 2, pid
        while uniq in taken:
            uniq, n = f"{pid}_{n}", n + 1
        hdrs = json.dumps({str(a).strip(): str(b) for a, b in (headers or {}).items() if str(a).strip()})
        self.db.execute(
            "INSERT INTO providers(id,kind,label,enabled,default_model,cap_usd,cap_period,max_output_tokens,auth_mode,api_key,api_key_env,api_base,"
            "organization,extra_headers,model_prices,manual_models) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uniq, CUSTOM_KIND, label, 1 if enabled else 0, model, float(cap_usd), cap_period, int(max_output_tokens), auth_mode, (api_key or "").strip() or None,
             (api_key_env or "").strip() or None, base, (organization or "").strip() or None, hdrs,
             json.dumps(_clean_prices(model_prices or {})), json.dumps(_clean_models(manual_models or []))))
        return self.get(uniq)  # type: ignore[return-value]

    def used_by(self, pid: str) -> list[str]:
        """What still points at this provider, in plain words (so deleting it does not silently break them)."""
        out = [f"agent '{r['name']}'" for r in self.db.query("SELECT name FROM agents WHERE provider=?", (pid,))]
        if self.db.get_setting("sidebot_provider", "") == pid:
            out.append("the assistant")
        return out

    def delete_custom(self, pid: str) -> None:
        cur = self.get(pid)
        if cur is None:
            raise KeyError(pid)
        if not cur.custom:
            raise ValueError("built-in providers cannot be deleted (turn them off instead)")
        use = self.used_by(pid)
        if use:
            raise ValueError(f"'{cur.label}' is still used by {', '.join(use[:5])}{' and more' if len(use) > 5 else ''}. Point them at another provider first.")
        self.db.execute("DELETE FROM providers WHERE id=?", (pid,))

    def export_custom(self) -> list[dict]:
        """Custom endpoints without any secret (no key, no header values): what a settings file may carry."""
        return [dict(label=p.label, api_base=p.api_base, default_model=p.default_model, auth_mode=p.mode, api_key_env=p.api_key_env,
                     header_names=sorted(p.headers()), organization=p.organization, cap_usd=p.cap_usd, cap_period=p.cap_period,
                     max_output_tokens=p.max_output_tokens, model_prices=p.prices(), manual_models=p.manual())
                for p in self.list() if p.custom]


def _label(v: object) -> str:
    t = str(v or "").strip()
    if not 1 <= len(t) <= 60:
        raise ValueError("the name must be 1 to 60 characters")
    return t


def _clean_models(v: object) -> list[str]:
    if not isinstance(v, list):
        raise ValueError("models must be a list of names")
    out: list[str] = []
    for x in v:
        t = str(x).strip()
        if t and t not in out:
            if len(t) > 200:
                raise ValueError("a model name is too long (200 characters at most)")
            out.append(t)
    if len(out) > 300:
        raise ValueError("at most 300 typed-in models")
    return out


def _clean_prices(v: object) -> dict:
    """{"*" or model id: {input_per_m, output_per_m, context?}} with the numbers checked; empty entries are dropped."""
    if not isinstance(v, dict):
        raise ValueError("prices must be an object of model -> prices")
    out: dict = {}
    for k, e in v.items():
        if not isinstance(e, dict):
            raise ValueError(f"the price of '{k}' must be an object")
        clean: dict = {}
        for f, what in (("input_per_m", "input"), ("output_per_m", "output")):
            if e.get(f) not in (None, ""):
                try:
                    n = float(e[f])
                except (TypeError, ValueError):
                    raise ValueError(f"the {what} price of '{k}' must be a number (USD per million tokens)")
                if not 0 <= n <= 100_000:
                    raise ValueError(f"the {what} price of '{k}' must be between 0 and 100000")
                clean[f] = n
        if ("input_per_m" in clean) != ("output_per_m" in clean):
            raise ValueError(f"give both the input and the output price of '{k}', or neither")
        if e.get("context") not in (None, ""):
            try:
                c = int(e["context"])
            except (TypeError, ValueError):
                raise ValueError(f"the context size of '{k}' must be a whole number of tokens")
            if not 512 <= c <= 10_000_000:
                raise ValueError(f"the context size of '{k}' must be between 512 and 10000000 tokens")
            clean["context"] = c
        if clean:
            out[str(k).strip()[:200]] = clean
    if len(out) > 300:
        raise ValueError("at most 300 priced models")
    return out


def tier_model(p: Provider, tier: str, custom: str | None = None) -> str:
    if tier == "custom" and custom:
        return custom
    m = p.pro_model if tier == "pro" else p.flash_model
    return m or p.default_model or CATALOG[p.kind]["default_model"]


def _key(p: Provider, label: str) -> tuple[str, str]:
    """(key, human description) for api_key / env modes."""
    if p.mode == "env":
        name = p.api_key_env or CATALOG[p.kind]["key_env"]
        if not name:
            raise ValueError(f"{label}: set the name of the environment variable that holds the key")
        val = os.environ.get(name)
        if not val:
            raise ValueError(f"{label}: environment variable {name} is not set (for the Agent Studio server process)")
        return val, f"API key from ${name}"
    if not p.api_key:
        raise ValueError(f"{label}: API key not set in Settings")
    return p.api_key, "stored API key"


def _resolve(p: Provider, model: str | None) -> tuple[str, dict, str]:
    c = CATALOG[p.kind]
    model = model or p.default_model or c["default_model"]
    label, mode, kw = (p.label if p.custom else c["label"]), p.mode, {}
    how = AUTH_LABELS[mode]
    if p.kind == "vertex_ai":
        if mode in ("api_key", "env"):          # express mode: API key, no project needed
            key, how = _key(p, label)
            kw.update(api_key=key, api_base=p.api_base or VERTEX_EXPRESS_BASE)
            target = f"gemini/{model}"           # Gemini-format request + x-goog-api-key header against the Vertex endpoint
            how = f"Vertex express mode, {how}"
        else:
            sa = None
            if mode == "service_account_file":
                if not p.vertex_credentials_path:
                    raise ValueError(f"{label}: set the service-account JSON path")
                path = Path(p.vertex_credentials_path)
                if not path.is_file():
                    raise ValueError(f"{label}: credentials file not found: {path}")
                try:
                    sa = json.loads(path.read_text("utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    raise ValueError(f"{label}: cannot read credentials file: {e}")
            elif mode == "service_account_json":
                if not p.vertex_credentials_json:
                    raise ValueError(f"{label}: paste the service-account JSON")
                sa = json.loads(p.vertex_credentials_json)
            project = p.vertex_project or (sa or {}).get("project_id") or (sa or {}).get("quota_project_id")
            if not project:
                raise ValueError(f"{label}: set the GCP project ID")
            kw.update(vertex_project=project, vertex_location=p.vertex_location or "global")
            if sa is not None:
                kw["vertex_credentials"] = json.dumps(sa)
            if p.api_base:
                kw["api_base"] = p.api_base
            target = f"{c['prefix']}{model}"
            how = f"{how} · project {project}"
    elif p.kind == "bedrock":
        region = p.aws_region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        if not region:
            raise ValueError(f"{label}: set the AWS region")
        kw["aws_region_name"] = region
        if mode == "aws_keys":
            if not (p.aws_access_key_id and p.aws_secret_access_key):
                raise ValueError(f"{label}: set the AWS access key id and secret")
            kw.update(aws_access_key_id=p.aws_access_key_id, aws_secret_access_key=p.aws_secret_access_key)
            if p.aws_session_token:
                kw["aws_session_token"] = p.aws_session_token
            how = f"AWS keys {p.aws_access_key_id[:4]}…"
        elif mode == "aws_profile":
            if not p.aws_profile:
                raise ValueError(f"{label}: set the AWS profile name")
            kw["aws_profile_name"] = p.aws_profile
            how = f"AWS profile '{p.aws_profile}'"
        elif mode in ("api_key", "env"):
            key, how = _key(p, label)
            kw["api_key"] = key                   # Bedrock API key -> sent as a bearer token
            how = f"Bedrock API key ({how})"
        else:
            how = "AWS default credential chain"
        if p.api_base:
            kw["api_base"] = p.api_base           # e.g. a VPC endpoint
        target = f"{c['prefix']}{model}"
        how += f" · {region}"
    elif p.kind == "mock":
        target = f"{c['prefix']}{model}"     # mock_response is injected by the chat layer
    else:
        if mode in ("api_key", "env"):
            key, how = _key(p, label)
            kw["api_key"] = key
        elif p.kind not in ("ollama",):
            kw["api_key"] = "no-key"             # the SDKs insist on a value; the proxy/gateway does the auth
        if p.custom and not p.api_base:
            raise ValueError(f"{label}: set the endpoint address")
        if p.api_base:
            kw["api_base"] = p.api_base
        elif p.kind == "ollama":
            kw["api_base"] = "http://127.0.0.1:11434"
        if p.kind == "ollama":
            # Fix the context window: Ollama otherwise picks the model's maximum (e.g. 40k for qwen3), whose KV cache can
            # exhaust GPU/RAM ("std::bad_alloc") and forces reloads whenever it changes.
            kw["num_ctx"] = int(p.context_window or OLLAMA_DEFAULT_CTX)
            how += f" · {kw['num_ctx']:,}-token context"
        if p.organization and p.kind in ("openai", CUSTOM_KIND):
            kw["organization"] = p.organization
        target = f"{c['prefix']}{model}"
    h = p.headers()
    if h:
        kw["extra_headers"] = h
        how += f" · +{len(h)} header(s)"
    return target, kw, how


def litellm_target(p: Provider, model: str | None = None) -> tuple[str, dict]:
    """(litellm model string, kwargs) for a provider. Raises ValueError if misconfigured."""
    target, kw, _ = _resolve(p, model)
    return target, kw
