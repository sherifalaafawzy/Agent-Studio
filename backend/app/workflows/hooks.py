"""The secret address that starts a workflow from outside (the Webhook box).

The address is /api/hooks/<workflow>/<secret>. The secret is shown once, when it is made; only a hash of it is kept, so a copy of the database cannot start anything.
Comparing is done in constant time. A rate limiter keeps one caller from starting runs faster than the setting allows.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import deque


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class HookStore:
    def __init__(self, db, clock=time.time):
        self.db, self.clock = db, clock

    def status(self, wid: str) -> dict:
        r = self.db.one("SELECT created,last_used,calls FROM workflow_hooks WHERE wf_id=?", (wid,))
        return dict(exists=r is not None, created=r["created"] if r else None, last_used=r["last_used"] if r else None, calls=r["calls"] if r else 0)

    def create(self, wid: str) -> str:
        """Make (or replace) the secret. Returns it; it cannot be read again."""
        token = secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO workflow_hooks(wf_id,secret_hash,created,last_used,calls) VALUES(?,?,?,NULL,0) ON CONFLICT(wf_id) DO UPDATE SET secret_hash=excluded.secret_hash, created=excluded.created, last_used=NULL, calls=0",
                        (wid, _hash(token), self.clock()))
        return token

    def delete(self, wid: str) -> None:
        self.db.execute("DELETE FROM workflow_hooks WHERE wf_id=?", (wid,))

    def verify(self, wid: str, token: str) -> bool:
        r = self.db.one("SELECT secret_hash FROM workflow_hooks WHERE wf_id=?", (wid,))
        return bool(r) and hmac.compare_digest(r["secret_hash"], _hash(token or ""))

    def touch(self, wid: str) -> None:
        self.db.execute("UPDATE workflow_hooks SET last_used=?, calls=calls+1 WHERE wf_id=?", (self.clock(), wid))


class RateLimiter:
    """At most `per_min` calls in any 60 seconds, per key."""

    def __init__(self, clock=time.monotonic):
        self.clock, self.seen = clock, {}

    def allow(self, key: str, per_min: int) -> bool:
        now = self.clock()
        q = self.seen.setdefault(key, deque())
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= per_min:
            return False
        q.append(now)
        return True
