"""Where memories live.

* LocalMemoryStore - the source of truth, always: a table in Agent Studio's own SQLite file (works offline; the review queue,
  provenance, export, expiry and every kind of memory behave the same whatever else is connected).
* RemoteIndex - an optional second place that makes memories searchable somewhere else or keeps a durable copy there:
  a vector database you already connected, or a cloud memory service. Only approved (active) memories are sent.
* MemoryFactory - builds the backend the user chose (abstract factory), so another service is one registered class.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from ..db import Database
from .model import cosine, now, text_score

COLUMNS = ("id", "type", "scope", "scope_id", "text", "data", "key", "confidence", "status", "source", "created", "updated", "expires",
           "supersedes", "embedding", "emb_model", "remote_id", "redacted", "hits", "last_used")


class MemoryError_(Exception):
    """A memory operation that cannot be done. The message is safe to show."""


class MemoryNotFound(MemoryError_):
    """No memory has that id."""


def public(row, with_vector: bool = False) -> dict:
    """A stored row as the API shows it (the vector itself is never sent to the UI)."""
    d = {k: row[k] for k in COLUMNS}
    d["data"] = json.loads(d["data"] or "{}")
    d["source"] = json.loads(d["source"] or "{}")
    d["redacted"] = json.loads(d["redacted"] or "[]")
    d["embedded"] = bool(d["embedding"])
    vec = d.pop("embedding")
    if with_vector:
        d["embedding"] = json.loads(vec) if vec else None
    return d


class LocalMemoryStore:
    def __init__(self, db: Database):
        self.db = db

    # ---- records
    def insert(self, rec: dict) -> dict:
        row = {c: rec.get(c) for c in COLUMNS}
        row["data"], row["source"], row["redacted"] = (json.dumps(rec.get("data") or {}), json.dumps(rec.get("source") or {}), json.dumps(rec.get("redacted") or []))
        row["embedding"] = json.dumps(rec["embedding"]) if rec.get("embedding") else None
        for k, default in (("key", ""), ("confidence", 0.8), ("status", "active"), ("emb_model", ""), ("remote_id", ""), ("hits", 0)):
            row[k] = default if row[k] is None else row[k]
        self.db.execute(f"INSERT INTO memories({','.join(COLUMNS)}) VALUES({','.join('?' * len(COLUMNS))})", [row[c] for c in COLUMNS])
        return self.get(rec["id"])  # type: ignore[return-value]

    def get(self, mid: str, with_vector: bool = False) -> dict | None:
        r = self.db.one("SELECT * FROM memories WHERE id=?", (mid,))
        return public(r, with_vector) if r else None

    def update(self, mid: str, **fields) -> dict | None:
        enc = {"data": json.dumps, "source": json.dumps, "redacted": json.dumps, "embedding": lambda v: json.dumps(v) if v else None}
        sets, params = [], []
        for k, v in fields.items():
            assert k in COLUMNS and k != "id", k
            sets.append(f"{k}=?")
            params.append(enc[k](v) if k in enc else v)
        sets.append("updated=?")
        params += [fields.get("updated", now()), mid]
        self.db.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id=?", params)
        return self.get(mid)

    def delete(self, mid: str) -> bool:
        return self.db.execute("DELETE FROM memories WHERE id=?", (mid,)).rowcount > 0

    def query(self, *, type: str | None = None, scope: str | None = None, scope_id: str | None = None, status: str | list[str] | None = None,
              q: str = "", limit: int = 200, offset: int = 0, expired: bool = False, key: str | None = None, ids: list[str] | None = None) -> list[dict]:
        where, p = [], []
        for col, val in (("type", type), ("scope", scope), ("scope_id", scope_id), ("key", key)):
            if val is not None:
                where.append(f"{col}=?")
                p.append(val)
        if status is not None:
            sts = [status] if isinstance(status, str) else status
            where.append(f"status IN ({','.join('?' * len(sts))})")
            p += sts
        if ids is not None:
            where.append(f"id IN ({','.join('?' * len(ids))})")
            p += ids
        if not expired:
            where.append("(expires IS NULL OR expires > ?)")
            p.append(now())
        if q.strip():
            where.append("LOWER(text) LIKE ?")
            p.append(f"%{q.strip().lower()}%")
        sql = "SELECT * FROM memories" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated DESC LIMIT ? OFFSET ?"
        return [public(r) for r in self.db.query(sql, (*p, limit, offset))]

    def search(self, query: str, scopes: list[tuple[str, str]], types: list[str], k: int, *, vector: list[float] | None = None, model: str = "",
               min_score: float = 0.0) -> list[dict]:
        """Best active memories in the given (scope, scope_id) pairs. Cosine similarity when the memory has a vector from the same
        model as the query; otherwise the share of the query's words it contains."""
        if not scopes or not types:
            return []
        clause = " OR ".join("(scope=? AND scope_id=?)" for _ in scopes)
        params = [x for pair in scopes for x in pair]
        rows = self.db.query(f"SELECT * FROM memories WHERE status='active' AND type IN ({','.join('?' * len(types))}) AND ({clause}) "
                             "AND (expires IS NULL OR expires > ?)", (*types, *params, now()))
        scored = []
        for r in rows:
            if vector is not None and r["embedding"] and r["emb_model"] == model:
                score = cosine(vector, json.loads(r["embedding"]))
            else:
                score = text_score(query, r["text"])
            if score >= min_score and score > 0:
                scored.append((score + 0.02 * min(r["confidence"], 1.0), public(r), score))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [{**rec, "score": round(raw, 4)} for _, rec, raw in scored[:k]]

    def touch(self, ids: list[str]) -> None:
        if ids:
            self.db.execute(f"UPDATE memories SET hits=hits+1, last_used=? WHERE id IN ({','.join('?' * len(ids))})", (now(), *ids))

    def purge_expired(self) -> list[dict]:
        rows = [public(r) for r in self.db.query("SELECT * FROM memories WHERE expires IS NOT NULL AND expires <= ?", (now(),))]
        self.db.execute("DELETE FROM memories WHERE expires IS NOT NULL AND expires <= ?", (now(),))
        return rows

    def stats(self) -> dict:
        rows = self.db.query("SELECT type, status, COUNT(*) n FROM memories GROUP BY type, status")
        by_type: dict[str, int] = {}
        pending = total = 0
        for r in rows:
            if r["status"] == "active":
                by_type[r["type"]] = r["n"]
                total += r["n"]
            elif r["status"] == "pending":
                pending += r["n"]
        return dict(total=total, pending=pending, by_type=by_type)

    def wipe(self, scope: str | None = None, scope_id: str | None = None) -> list[dict]:
        rows = self.query(scope=scope, scope_id=scope_id, limit=10**9, expired=True)
        if scope is None:
            self.db.execute("DELETE FROM memories")
        else:
            self.db.execute("DELETE FROM memories WHERE scope=?" + (" AND scope_id=?" if scope_id else ""), (scope, scope_id) if scope_id else (scope,))
        return rows


class RemoteIndex(ABC):
    """A second place for approved memories. Failures are reported to the caller, never swallowed here."""
    kind: str = ""
    cloud: bool = False                      # True when memory text leaves this PC

    @abstractmethod
    async def health(self) -> dict:
        """{ok: bool, detail: str}. Never raises for an unreachable service; reports it."""

    @abstractmethod
    async def put(self, rec: dict, vector: list[float] | None) -> str:
        """Store/replace one memory. Returns the remote id ('' when the service has none)."""

    @abstractmethod
    async def search(self, query: str, vector: list[float] | None, scope: str, scope_id: str, k: int) -> list[tuple[str, float]]:
        """(Agent Studio memory id, score) pairs, best first, for one scope."""

    @abstractmethod
    async def delete(self, rec: dict) -> None: ...


@dataclass(frozen=True)
class Kind:
    id: str
    label: str
    description: str
    cloud: bool                                        # memory text leaves this PC
    fields: tuple = ()                                 # ((name, label, secret, required, help), ...)
    factory: Callable | None = None                    # (config, secrets, ctx) -> RemoteIndex ; None for the local backend
    notes: str = ""
    verified: str = ""                                 # where the API was checked


@dataclass
class MemoryFactory:
    kinds: dict[str, Kind] = field(default_factory=dict)

    def register(self, kind: Kind) -> None:
        self.kinds[kind.id] = kind

    def create(self, kind_id: str, config: dict, secrets: dict, ctx: dict) -> RemoteIndex | None:
        if kind_id not in self.kinds:
            raise MemoryError_(f"unknown memory backend '{kind_id}' (use one of: {', '.join(self.kinds)})")
        k = self.kinds[kind_id]
        for name, label, secret, required, _help in k.fields:
            if required and not (secrets.get(name) if secret else config.get(name)):
                raise MemoryError_(f"{label} is required for {k.label}")
        return k.factory(config, secrets, ctx) if k.factory else None
