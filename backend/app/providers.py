"""Provider catalog + provider config store + LiteLLM kwargs builder."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .db import Database

# kind -> (label, litellm prefix, needs_key, default model, note)
CATALOG: dict[str, dict] = {
    "vertex_ai": dict(label="Google Vertex AI (Gemini)", prefix="vertex_ai/", needs_key=False,
                      default_model="gemini-3.8-flash",
                      note="Uses your GCP project + service-account JSON. Default provider."),
    "openai": dict(label="OpenAI", prefix="openai/", needs_key=True, default_model="gpt-5-mini", note=""),
    "anthropic": dict(label="Anthropic", prefix="anthropic/", needs_key=True, default_model="claude-sonnet-5-5", note=""),
    "xai": dict(label="xAI (Grok)", prefix="xai/", needs_key=True, default_model="grok-4", note=""),
    "groq": dict(label="Groq", prefix="groq/", needs_key=True, default_model="llama-3.3-70b-versatile", note=""),
    "openrouter": dict(label="OpenRouter", prefix="openrouter/", needs_key=True,
                       default_model="anthropic/claude-sonnet-5-5", note=""),
    "ollama": dict(label="Ollama (local)", prefix="ollama_chat/", needs_key=False, default_model="llama3.2",
                   note="Free/local; api_base defaults to http://127.0.0.1:11434"),
    "mock": dict(label="Mock (no network, no spend)", prefix="openai/", needs_key=False, default_model="mock-expensive",
                 note="Offline test provider with artificially high prices - for demoing the cap guard."),
}

# One row per kind to start; ids are the kind (users may add more rows later).
DEFAULT_CAP_USD = {"mock": 0.25, "ollama": 0.0}
SAFE_DEFAULT_CAP_USD = 1.0  # new paid providers start with a small, safe cap


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

    @classmethod
    def from_row(cls, r) -> "Provider":
        d = dict(r)
        d["enabled"] = bool(d["enabled"])
        return cls(**d)

    def public(self) -> dict:
        d = self.__dict__.copy()
        key = d.pop("api_key")
        d["has_api_key"] = bool(key)
        d["api_key_hint"] = ("…" + key[-4:]) if key and len(key) > 8 else ("set" if key else None)
        return d


class ProviderStore:
    def __init__(self, db: Database):
        self.db = db
        self.seed()

    def seed(self) -> None:
        for kind, c in CATALOG.items():
            if self.db.one("SELECT 1 FROM providers WHERE id=?", (kind,)):
                continue
            cap = DEFAULT_CAP_USD.get(kind, SAFE_DEFAULT_CAP_USD)
            self.db.execute(
                "INSERT INTO providers(id,kind,label,enabled,default_model,cap_usd,cap_period,vertex_location,max_output_tokens)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (kind, kind, c["label"], 1 if kind == "mock" else 0, c["default_model"], cap, "total",
                 "global" if kind == "vertex_ai" else None, 64 if kind == "mock" else 2048),
            )

    def list(self) -> list[Provider]:
        return [Provider.from_row(r) for r in self.db.query("SELECT * FROM providers ORDER BY rowid")]

    def get(self, pid: str) -> Provider | None:
        r = self.db.one("SELECT * FROM providers WHERE id=?", (pid,))
        return Provider.from_row(r) if r else None

    EDITABLE = ("enabled", "api_key", "api_base", "vertex_project", "vertex_location",
                "vertex_credentials_path", "default_model", "cap_usd", "cap_period", "max_output_tokens")

    def update(self, pid: str, patch: dict) -> Provider:
        if not self.get(pid):
            raise KeyError(pid)
        sets, vals = [], []
        for k in self.EDITABLE:
            if k not in patch:
                continue
            v = patch[k]
            if k == "api_key" and v is None:   # None = leave unchanged; "" = clear
                continue
            if k == "enabled":
                v = 1 if v else 0
            if k == "cap_usd":
                v = float(v)
                if v < 0:
                    raise ValueError("cap_usd must be >= 0")
            if k == "cap_period" and v not in ("total", "daily", "monthly"):
                raise ValueError("cap_period must be total|daily|monthly")
            if k == "max_output_tokens":
                v = int(v)
                if not 16 <= v <= 1_000_000:
                    raise ValueError("max_output_tokens out of range")
            if k == "api_key" and v == "":
                v = None
            sets.append(f"{k}=?")
            vals.append(v)
        if sets:
            self.db.execute(f"UPDATE providers SET {','.join(sets)} WHERE id=?", (*vals, pid))
        return self.get(pid)  # type: ignore[return-value]


def litellm_target(p: Provider, model: str | None = None) -> tuple[str, dict]:
    """(litellm model string, kwargs) for a provider. Raises ValueError if misconfigured."""
    c = CATALOG[p.kind]
    model = model or p.default_model or c["default_model"]
    kwargs: dict = {}
    if p.kind == "vertex_ai":
        if not p.vertex_project:
            raise ValueError("Vertex AI: set the GCP project in Settings")
        kwargs["vertex_project"] = p.vertex_project
        kwargs["vertex_location"] = p.vertex_location or "global"
        if p.vertex_credentials_path:
            path = Path(p.vertex_credentials_path)
            if not path.is_file():
                raise ValueError(f"Vertex AI: credentials file not found: {path}")
            kwargs["vertex_credentials"] = json.dumps(json.loads(path.read_text("utf-8")))
        # else: falls back to Application Default Credentials
    elif p.kind == "mock":
        pass  # mock_response is injected by the chat layer
    else:
        if c["needs_key"] and not p.api_key:
            raise ValueError(f"{c['label']}: API key not set in Settings")
        if p.api_key:
            kwargs["api_key"] = p.api_key
        if p.api_base:
            kwargs["api_base"] = p.api_base
        elif p.kind == "ollama":
            kwargs["api_base"] = "http://127.0.0.1:11434"
    return f"{c['prefix']}{model}", kwargs
