"""API keys for the OpenAI-compatible API (/v1) and A2A.

A key looks like `asx_<id>_<secret>`; only its SHA-256 is stored (the secret is 192 random bits, so a plain hash is enough),
and the full key is shown exactly once when it is created or rotated. Each key may be limited to some agents, has a requests-
per-minute limit, can expire, and can be revoked or rotated (rotating keeps the id, scope and limit and kills the old secret).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque

from ..db import Database

PREFIX = "asx"
ID_RE = re.compile(r"^[a-z0-9]{8}$")
MAX_KEYS = 50


class KeyError_(ValueError):
    """Raised with a plain explanation when a key operation is refused."""


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ApiKeys:
    def __init__(self, db: Database, clock=time.time):
        self.db, self.clock = db, clock
        self.hits: dict[str, deque] = defaultdict(deque)

    @staticmethod
    def _public(r) -> dict:
        import json
        return dict(id=r["id"], name=r["name"], prefix=r["prefix"], agents=json.loads(r["agents"]), rate_per_min=r["rate_per_min"], created=r["created"],
                    last_used=r["last_used"], expires=r["expires"], revoked=bool(r["revoked"]), uses=r["uses"])

    def list(self) -> list[dict]:
        return [self._public(r) for r in self.db.query("SELECT * FROM api_keys ORDER BY created DESC")]

    def _new_secret(self, kid: str) -> str:
        return f"{PREFIX}_{kid}_{secrets.token_urlsafe(24)}"

    def create(self, name: str, agents: list[str] | None = None, rate_per_min: int = 60, expires_days: float | None = None) -> dict:
        import json
        name = (name or "").strip()
        if not name or len(name) > 60:
            raise KeyError_("give the key a name (up to 60 characters)")
        if not 1 <= rate_per_min <= 100000:
            raise KeyError_("requests per minute must be between 1 and 100000")
        if expires_days is not None and not 0 < expires_days <= 3650:
            raise KeyError_("expiry must be between 1 day and 10 years")
        agents = list(agents or ["*"])
        if not agents or any(not isinstance(a, str) or not a for a in agents):
            raise KeyError_("choose which agents the key may call (or all of them)")
        if len(self.list()) >= MAX_KEYS:
            raise KeyError_(f"at most {MAX_KEYS} keys: delete one you no longer use")
        kid = secrets.token_hex(4)
        key = self._new_secret(kid)
        now = self.clock()
        self.db.execute("INSERT INTO api_keys(id,name,prefix,hash,agents,rate_per_min,created,expires) VALUES(?,?,?,?,?,?,?,?)",
                        (kid, name, key[:len(PREFIX) + 1 + 8 + 5], _hash(key), json.dumps(agents), rate_per_min, now, now + expires_days * 86400 if expires_days else None))
        return dict(self._public(self.db.one("SELECT * FROM api_keys WHERE id=?", (kid,))), key=key)

    def update(self, kid: str, patch: dict) -> dict:
        import json
        r = self.db.one("SELECT * FROM api_keys WHERE id=?", (kid,))
        if r is None:
            raise KeyError(kid)
        name = (patch.get("name", r["name"]) or "").strip()
        rate = patch.get("rate_per_min", r["rate_per_min"])
        agents = patch.get("agents", json.loads(r["agents"]))
        if not name or len(name) > 60 or not isinstance(rate, int) or not 1 <= rate <= 100000 or not agents or not all(isinstance(a, str) and a for a in agents):
            raise KeyError_("name, rate limit or agent list is not valid")
        self.db.execute("UPDATE api_keys SET name=?, rate_per_min=?, agents=?, revoked=? WHERE id=?",
                        (name, rate, json.dumps(agents), int(bool(patch.get("revoked", r["revoked"]))), kid))
        return self._public(self.db.one("SELECT * FROM api_keys WHERE id=?", (kid,)))

    def rotate(self, kid: str) -> dict:
        r = self.db.one("SELECT * FROM api_keys WHERE id=?", (kid,))
        if r is None:
            raise KeyError(kid)
        key = self._new_secret(kid)
        self.db.execute("UPDATE api_keys SET hash=?, prefix=?, revoked=0 WHERE id=?", (_hash(key), key[:len(PREFIX) + 1 + 8 + 5], kid))
        return dict(self._public(self.db.one("SELECT * FROM api_keys WHERE id=?", (kid,))), key=key)

    def delete(self, kid: str) -> None:
        if self.db.one("SELECT id FROM api_keys WHERE id=?", (kid,)) is None:
            raise KeyError(kid)
        self.db.execute("DELETE FROM api_keys WHERE id=?", (kid,))
        self.hits.pop(kid, None)

    def verify(self, bearer: str | None) -> dict | None:
        """The key's public record when `bearer` is a live key, else None (constant-time compare; revoked and expired keys fail)."""
        m = re.match(rf"^{PREFIX}_([0-9a-f]{{8}})_[\w-]{{20,64}}$", bearer or "")
        if m is None:
            return None
        r = self.db.one("SELECT * FROM api_keys WHERE id=?", (m.group(1),))
        if r is None or not hmac.compare_digest(r["hash"], _hash(bearer or "")):
            return None
        if r["revoked"] or (r["expires"] is not None and r["expires"] < self.clock()):
            return None
        return self._public(r)

    def allows(self, key: dict, agent_id: str) -> bool:
        return "*" in key["agents"] or agent_id in key["agents"]

    def hit(self, key: dict) -> float | None:
        """Count one request. Returns None when allowed, or the seconds to wait when the key is over its per-minute limit."""
        now = self.clock()
        q = self.hits[key["id"]]
        while q and q[0] <= now - 60:
            q.popleft()
        if len(q) >= key["rate_per_min"]:
            return max(1.0, round(q[0] + 60 - now, 1))
        q.append(now)
        self.db.execute("UPDATE api_keys SET uses=uses+1, last_used=? WHERE id=?", (now, key["id"]))
        return None
