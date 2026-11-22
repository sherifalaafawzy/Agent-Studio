"""Discovery & AI-BOM settings, stored in the app's settings table. Publishing is OFF until the user turns it on, names a
domain they control, and chooses what to publish. The access token is kept only as a hash; the push API key is write-only."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from urllib.parse import urlsplit

from ..aibom.redact import LEVELS, host_class

KEY = "ard_settings"
TOKEN_KEY = "ard_token_hash"
PUSH_KEY = "ard_push_api_key"
DEFAULT_PORT = 8766
FQDN = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
RESERVED_SUFFIX = (".local", ".localhost", ".internal", ".test", ".example", ".invalid", ".lan", ".home", ".corp", ".intranet", ".arpa")
RESERVED_DOMAINS = ("example.com", "example.org", "example.net")      # reserved for documentation: never a real publisher
TYPES = ("agents", "mcp", "skills", "bom_system", "bom_agents", "bom_mcp", "bom_models")
TYPE_LABELS = {
    "agents": "Agents (name, description, tools, model tier)",
    "mcp": "MCP servers (transport, pinned package, requirements)",
    "skills": "Skills (the SKILL.md files)",
    "bom_system": "AI-BOM: the whole system",
    "bom_agents": "AI-BOM: one per agent",
    "bom_mcp": "AI-BOM: one per MCP server",
    "bom_models": "AI-BOM: one per model",
}
DEFAULTS = {"enabled": False, "publisher": "", "base_url": "", "level": "public", "level_confirmed": False, "sign": False,
            "port": DEFAULT_PORT, "publish": {t: False for t in TYPES},
            "push_url": "", "push_project": "Agent Studio", "push_version": "", "push_level": "public"}


class SettingsError(ValueError):
    """The value is not acceptable; the message is shown to the user."""


def valid_publisher(domain: str) -> str | None:
    """A reason the domain cannot be a publisher, or None when it is acceptable."""
    d = (domain or "").strip().lower().rstrip(".")
    if not d:
        return "set the publisher domain: a domain you control, for example agents.yourcompany.com"
    if not FQDN.match(d):
        return "the publisher must be a fully qualified domain name (letters, digits, dots and hyphens), not an address or a URL"
    if d.endswith(RESERVED_SUFFIX) or d in RESERVED_DOMAINS or any(d.endswith("." + r) for r in RESERVED_DOMAINS):
        return "that domain is reserved for documentation or local use and cannot be a publisher: use a domain you control"
    return None


def valid_push_url(url: str) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return "the push address is not a valid URL"
    if parts.scheme == "https" and host:
        return None
    if parts.scheme == "http" and host_class(host) == "loopback":
        return None
    return "the push address must be https:// (plain http is allowed only for localhost)"


class DiscoverySettings:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------ read
    def get(self) -> dict:
        try:
            raw = json.loads(self.db.get_setting(KEY, "{}") or "{}")
        except ValueError:
            raw = {}
        out = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
        out["publish"] = {t: bool((raw.get("publish") or {}).get(t, False)) for t in TYPES}
        if out["level"] not in LEVELS:
            out["level"] = "public"
        return out

    def base_url(self, s: dict | None = None) -> str:
        s = s or self.get()
        return (s["base_url"] or (f"https://{s['publisher']}" if s["publisher"] else "")).rstrip("/")

    def readiness(self, s: dict | None = None) -> list[str]:
        """Why publishing cannot start (empty = ready). Nothing here ever publishes by itself."""
        s = s or self.get()
        why = []
        if not s["enabled"]:
            why.append("publishing is switched off")
        bad = valid_publisher(s["publisher"])
        if bad:
            why.append(bad)
        if s["base_url"] and not bad:
            host = (urlsplit(s["base_url"]).hostname or "").lower()
            if host != s["publisher"] and not host.endswith("." + s["publisher"]):
                why.append(f"the public address is not on the publisher domain ({s['publisher']}): fix one of them")
        if not any(s["publish"].values()):
            why.append("choose at least one kind of resource to publish")
        if s["level"] != "public":
            if not s["level_confirmed"]:
                why.append(f"the '{s['level']}' redaction level shares more than a public BOM: confirm it in the settings")
            if not self.db.get_setting(TOKEN_KEY):
                why.append(f"the '{s['level']}' level is served only with an access token: generate one")
        return why

    def public(self) -> dict:
        s = self.get()
        return {**s, "types": [{"id": t, "label": TYPE_LABELS[t]} for t in TYPES], "has_token": bool(self.db.get_setting(TOKEN_KEY)),
                "has_push_key": bool(self.db.get_setting(PUSH_KEY)), "effective_base_url": self.base_url(s), "not_ready": self.readiness(s),
                "publisher_problem": valid_publisher(s["publisher"]) if s["publisher"] else None}

    # ------------------------------------------------------------------ write
    def update(self, patch: dict) -> dict:
        s = self.get()
        if patch.get("publisher") is not None:
            pub = str(patch["publisher"]).strip().lower().rstrip(".")
            if pub and (bad := valid_publisher(pub)):
                raise SettingsError(bad)
            s["publisher"] = pub
        if patch.get("base_url") is not None:
            url = str(patch["base_url"]).strip().rstrip("/")
            if url:
                parts = urlsplit(url)
                host = (parts.hostname or "").lower()
                pub = s["publisher"]
                if parts.scheme != "https" or not host or parts.query or parts.fragment:
                    raise SettingsError("the public address must be an https:// URL without a query or fragment")
                if pub and host != pub and not host.endswith("." + pub):
                    raise SettingsError(f"the public address must be on the publisher domain ({pub}) or one of its subdomains")
            s["base_url"] = url
        if patch.get("level") is not None:
            if patch["level"] not in LEVELS:
                raise SettingsError(f"the redaction level must be one of: {', '.join(LEVELS)}")
            if patch["level"] != s["level"]:
                s["level_confirmed"] = False
            s["level"] = patch["level"]
        if patch.get("level_confirmed") is not None:
            s["level_confirmed"] = bool(patch["level_confirmed"]) and s["level"] != "public"
        for k in ("enabled", "sign"):
            if patch.get(k) is not None:
                s[k] = bool(patch[k])
        if patch.get("port") is not None:
            port = int(patch["port"])
            if not 1024 <= port <= 65535:
                raise SettingsError("the port must be between 1024 and 65535")
            s["port"] = port
        if patch.get("publish") is not None:
            for t, v in patch["publish"].items():
                if t not in TYPES:
                    raise SettingsError(f"unknown resource type '{t}'")
                s["publish"][t] = bool(v)
        for k in ("push_url", "push_project", "push_version"):
            if patch.get(k) is not None:
                s[k] = str(patch[k]).strip()
        if patch.get("push_level") is not None:
            if patch["push_level"] not in LEVELS:
                raise SettingsError(f"the push redaction level must be one of: {', '.join(LEVELS)}")
            s["push_level"] = patch["push_level"]
        if bad := valid_push_url(s["push_url"]):
            raise SettingsError(bad)
        if patch.get("push_api_key") is not None:                    # "" clears; the value is never returned
            self.db.set_setting(PUSH_KEY, str(patch["push_api_key"]).strip())
        self.db.set_setting(KEY, json.dumps(s))
        return self.public()

    # ------------------------------------------------------------------ access token
    def new_token(self) -> str:
        """A fresh access token. Shown once; only its hash is stored."""
        token = "asd_" + secrets.token_urlsafe(32)
        self.db.set_setting(TOKEN_KEY, hashlib.sha256(token.encode()).hexdigest())
        return token

    def clear_token(self) -> None:
        self.db.set_setting(TOKEN_KEY, "")

    def check_token(self, presented: str) -> bool:
        stored = self.db.get_setting(TOKEN_KEY)
        return bool(stored) and hmac.compare_digest(stored, hashlib.sha256(presented.encode()).hexdigest())

    def push_key(self) -> str:
        return self.db.get_setting(PUSH_KEY, "") or ""
