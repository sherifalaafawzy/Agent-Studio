"""Workflow settings and the credentials that workflow boxes sign in with.

Settings: whether the Webhook box may be called from outside, how often and how large, how long a caller waits, how many boxes a workflow may have, how many finished runs are
kept, and what the HTTP request box may reach. Credentials are kept apart from the workflows (a workflow file never holds a secret): a box names a credential by id and the engine
adds the header when it calls. The list shows ids and labels only; a secret value is write-only.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any

from . import core as C

CONFIG_KEY, SECRETS_KEY = "workflow_config", "workflow_secrets"
DEFAULTS: dict[str, Any] = dict(webhooks_enabled=False, schedules_enabled=True, hook_rate_per_min=30, hook_max_body_kb=256, hook_wait_max_s=30, max_nodes=60, run_retention=100, http_timeout_s=30, http_max_kb=512, http_allow_hosts=[])
BOUNDS = dict(hook_rate_per_min=(1, 600), hook_max_body_kb=(1, 4096), hook_wait_max_s=(0, 120), max_nodes=(10, 200), run_retention=(5, 1000), http_timeout_s=(1, 120), http_max_kb=(1, 4096))
KINDS = ("bearer", "header", "basic")
CRED_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,40}$")
MAX_CREDENTIALS = 20


class WorkflowSettings:
    def __init__(self, db):
        self.db = db
        self.apply()

    def config(self) -> dict:
        try:
            raw = json.loads(self.db.get_setting(CONFIG_KEY, "{}") or "{}")
        except ValueError:
            raw = {}
        cfg = json.loads(json.dumps(DEFAULTS))
        if isinstance(raw, dict):
            cfg.update({k: v for k, v in raw.items() if k in cfg})
        return cfg

    def apply(self) -> None:
        """Hand the limits to the graph checker and the engine."""
        cfg = self.config()
        C.LIMITS.update(max_nodes=cfg["max_nodes"], run_retention=cfg["run_retention"])

    def set_config(self, patch: dict) -> dict:
        cfg = self.config()
        for k, v in (patch or {}).items():
            if k not in DEFAULTS or v is None:
                continue
            if k in ("webhooks_enabled", "schedules_enabled"):
                if not isinstance(v, bool):
                    raise C.WorkflowError(f"{k} must be true or false")
            elif k == "http_allow_hosts":
                if not isinstance(v, list) or len(v) > 30:
                    raise C.WorkflowError("give at most 30 allowed hosts")
                hosts = [str(h).strip().lower().lstrip(".") for h in v if str(h).strip()]
                if any(not re.match(r"^[a-z0-9.\-]{1,100}$", h) for h in hosts):
                    raise C.WorkflowError("a host is a name like api.internal.example (letters, digits, . and -)")
                v = sorted(set(hosts))
            else:
                lo, hi = BOUNDS[k]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or not lo <= v <= hi:
                    raise C.WorkflowError(f"{k} must be a whole number from {lo} to {hi}")
                v = int(v)
            cfg[k] = v
        self.db.set_setting(CONFIG_KEY, json.dumps(cfg))
        self.apply()
        return cfg

    # ---- credentials
    def _secrets(self) -> dict:
        try:
            s = json.loads(self.db.get_setting(SECRETS_KEY, "{}") or "{}")
        except ValueError:
            return {}
        return s if isinstance(s, dict) else {}

    def credentials(self) -> list[dict]:
        return [dict(id=k, label=v.get("label", k), kind=v.get("kind", "bearer"), header=v.get("header", ""), set=bool(v.get("value"))) for k, v in sorted(self._secrets().items())]

    def ids(self) -> set[str]:
        return set(self._secrets())

    def set_credential(self, body: dict) -> dict:
        cid = str(body.get("id") or "")
        if not CRED_ID.match(cid):
            raise C.WorkflowError("a credential id is lowercase letters, digits and underscores, starting with a letter (2-31 characters)")
        kind = body.get("kind", "bearer")
        if kind not in KINDS:
            raise C.WorkflowError(f"kind must be one of {', '.join(KINDS)}")
        header = str(body.get("header") or "").strip()
        if kind == "header" and not HEADER.match(header):
            raise C.WorkflowError("name the header to send (like X-Api-Key)")
        if kind == "header" and header.lower() in ("host", "content-length", "transfer-encoding"):
            raise C.WorkflowError(f"'{header}' cannot be used")
        secrets = self._secrets()
        old = secrets.get(cid)
        if old is None and len(secrets) >= MAX_CREDENTIALS:
            raise C.WorkflowError(f"at most {MAX_CREDENTIALS} credentials")
        value = str(body.get("value") or "")
        if not value and old is None:
            raise C.WorkflowError("give the secret value (it is write-only: it cannot be read back)")
        if kind == "basic" and value and ":" not in value:
            raise C.WorkflowError("for basic sign-in give user:password")
        if not value:
            value = old["value"]
        elif len(value) > 4000:
            raise C.WorkflowError("that secret is too long")
        secrets[cid] = dict(label=str(body.get("label") or cid)[:60], kind=kind, header=header if kind == "header" else "", value=value)
        self.db.set_setting(SECRETS_KEY, json.dumps(secrets))
        return next(c for c in self.credentials() if c["id"] == cid)

    def delete_credential(self, cid: str, used_by: list[str]) -> None:
        secrets = self._secrets()
        if cid not in secrets:
            raise KeyError(cid)
        if used_by:
            raise C.WorkflowError(f"'{cid}' is used by {', '.join(used_by)}: change those boxes first")
        del secrets[cid]
        self.db.set_setting(SECRETS_KEY, json.dumps(secrets))

    def header_for(self, cid: str) -> tuple[str, str]:
        """(header name, header value) for one credential; raises when it is gone."""
        c = self._secrets().get(cid)
        if c is None:
            raise C.WorkflowError(f"the credential '{cid}' does not exist")
        if c["kind"] == "bearer":
            return "Authorization", f"Bearer {c['value']}"
        if c["kind"] == "basic":
            return "Authorization", "Basic " + base64.b64encode(c["value"].encode()).decode()
        return c["header"], c["value"]
