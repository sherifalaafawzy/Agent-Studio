"""Zero-setup store: vectors in one SQLite file. Exact (brute-force) cosine search.

Good up to a few tens of thousands of chunks, needs no Docker. Bigger knowledge bases belong in Qdrant or pgvector.
"""
from __future__ import annotations

import asyncio
import heapq
import json
import math
import sqlite3
import threading
from array import array
from pathlib import Path

from .base import Hit, Point, StoreError, VectorStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS collections(name TEXT PRIMARY KEY, dim INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS points(
  collection TEXT NOT NULL, id TEXT NOT NULL, doc_id TEXT NOT NULL, vec BLOB NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY(collection, id));
CREATE INDEX IF NOT EXISTS points_doc ON points(collection, doc_id);
"""


def _unit(vec: list[float]) -> array:
    norm = math.sqrt(math.sumprod(vec, vec)) or 1.0
    return array("f", (x / norm for x in vec))


class LocalStore(VectorStore):
    kind = "local"

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.executescript(SCHEMA)

    def _run(self, sql: str, args: tuple = (), many: bool = False):
        with self._lock:
            cur = self._db.executemany(sql, args) if many else self._db.execute(sql, args)
            rows = cur.fetchall()
            self._db.commit()
            return rows if not many else cur.rowcount

    async def health(self) -> dict:
        n = (await asyncio.to_thread(self._run, "SELECT COUNT(*) FROM collections"))[0][0]
        return {"ok": True, "detail": f"local file ({n} collection{'s' if n != 1 else ''})"}

    async def ensure(self, collection: str, dim: int) -> None:
        def go() -> None:
            row = self._run("SELECT dim FROM collections WHERE name=?", (collection,))
            if row and row[0][0] != dim:
                raise StoreError(f"collection '{collection}' already exists with dimension {row[0][0]}, not {dim}")
            if not row:
                self._run("INSERT INTO collections(name,dim) VALUES(?,?)", (collection, dim))
        await asyncio.to_thread(go)

    async def upsert(self, collection: str, points: list[Point]) -> None:
        def go() -> None:
            row = self._run("SELECT dim FROM collections WHERE name=?", (collection,))
            if not row:
                raise StoreError(f"collection '{collection}' does not exist")
            bad = next((p for p in points if len(p.vector) != row[0][0]), None)
            if bad is not None:
                raise StoreError(f"a vector has {len(bad.vector)} dimensions; the collection expects {row[0][0]}")
            self._run("INSERT OR REPLACE INTO points(collection,id,doc_id,vec,payload) VALUES(?,?,?,?,?)",
                      tuple((collection, p.id, str(p.payload.get("doc_id", "")), _unit(p.vector).tobytes(), json.dumps(p.payload))
                            for p in points), many=True)
        await asyncio.to_thread(go)

    async def search(self, collection: str, vector: list[float], k: int) -> list[Hit]:
        def go() -> list[Hit]:
            row = self._run("SELECT dim FROM collections WHERE name=?", (collection,))
            if not row:
                return []
            if len(vector) != row[0][0]:
                raise StoreError(f"the query has {len(vector)} dimensions; the collection expects {row[0][0]}")
            q = _unit(vector)
            scored = []
            for pid, blob, payload in self._run("SELECT id, vec, payload FROM points WHERE collection=?", (collection,)):
                v = array("f")
                v.frombytes(blob)
                scored.append((math.sumprod(q, v), pid, payload))
            return [Hit(pid, float(s), json.loads(payload)) for s, pid, payload in heapq.nlargest(k, scored, key=lambda t: t[0])]
        return await asyncio.to_thread(go)

    async def delete_doc(self, collection: str, doc_id: str) -> int:
        def go() -> int:
            n = self._run("SELECT COUNT(*) FROM points WHERE collection=? AND doc_id=?", (collection, doc_id))[0][0]
            self._run("DELETE FROM points WHERE collection=? AND doc_id=?", (collection, doc_id))
            return n
        return await asyncio.to_thread(go)

    async def drop(self, collection: str) -> None:
        def go() -> None:
            self._run("DELETE FROM points WHERE collection=?", (collection,))
            self._run("DELETE FROM collections WHERE name=?", (collection,))
        await asyncio.to_thread(go)

    async def count(self, collection: str) -> int:
        return (await asyncio.to_thread(self._run, "SELECT COUNT(*) FROM points WHERE collection=?", (collection,)))[0][0]
