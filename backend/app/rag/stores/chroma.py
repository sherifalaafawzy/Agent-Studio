"""Chroma over its REST API v2: the Docker container we manage, or a remote server (URL, optional bearer token).

Collections are created with cosine distance. Chroma only learns a collection's dimension from its first insert, so a
dimension mismatch on an EMPTY existing collection is reported by Chroma itself when the first chunks are added.
"""
from __future__ import annotations

import re

import httpx

from .base import Hit, Point, StoreError, VectorStore

UPSERT_BATCH = 128
_SCOPE = re.compile(r"^[A-Za-z0-9_-]{1,63}$")


def _safe(collection: str) -> str:
    """Collection names go into URL paths: only letters, digits, - and _ may pass."""
    if not _SCOPE.match(collection):
        raise StoreError("the collection name may only contain letters, digits, - and _ (1-63 characters)")
    return collection


class ChromaStore(VectorStore):
    kind = "chroma"

    def __init__(self, url: str, token: str | None = None, tenant: str = "default_tenant", database: str = "default_database",
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30):
        if not url.startswith(("http://", "https://")):
            raise StoreError("the Chroma URL must start with http:// or https://")
        if not _SCOPE.match(tenant) or not _SCOPE.match(database):
            raise StoreError("the Chroma tenant and database names may only contain letters, digits, - and _")
        self.url = url.rstrip("/")
        self._base = f"/api/v2/tenants/{tenant}/databases/{database}/collections"
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._transport, self._timeout = transport, timeout
        self._ids: dict[str, str] = {}

    async def _req(self, method: str, path: str, json=None, ok_404: bool = False):
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout, headers=self._headers) as c:
                r = await c.request(method, self.url + path, json=json)
        except httpx.HTTPError as e:
            raise StoreError(f"cannot reach Chroma at {self.url}: {type(e).__name__}") from e
        if r.status_code == 404 and ok_404:
            return None
        if r.status_code in (401, 403):
            raise StoreError("Chroma rejected the token")
        if r.status_code >= 400:
            try:
                body = r.json()
                detail = body.get("message") or body.get("error") if isinstance(body, dict) else body
            except ValueError:
                detail = r.text
            raise StoreError(f"Chroma error {r.status_code}: {str(detail or r.text)[:300]}")
        return r.json() if r.content else None

    async def _id(self, collection: str) -> str | None:
        if collection not in self._ids:
            got = await self._req("GET", f"{self._base}/{_safe(collection)}", ok_404=True)
            if got is None:
                return None
            self._ids[collection] = got["id"]
        return self._ids[collection]

    async def _need(self, collection: str) -> str:
        cid = await self._id(collection)
        if cid is None:
            raise StoreError(f"collection '{collection}' does not exist")
        return cid

    async def health(self) -> dict:
        try:
            await self._req("GET", "/api/v2/heartbeat")
            version = await self._req("GET", "/api/v2/version")
            cols = await self._req("GET", self._base)
        except StoreError as e:
            return {"ok": False, "detail": str(e)}
        n = len(cols or [])
        return {"ok": True, "detail": f"Chroma {version} ({n} collection{'s' if n != 1 else ''})"}

    async def ensure(self, collection: str, dim: int) -> None:
        got = await self._req("POST", self._base, {"name": _safe(collection), "get_or_create": True,
                                                   "configuration": {"hnsw": {"space": "cosine"}}})
        space = ((got.get("configuration_json") or {}).get("hnsw") or {}).get("space")
        if space not in (None, "cosine"):
            raise StoreError(f"collection '{collection}' already exists with distance '{space}', not cosine")
        have = got.get("dimension")
        if have is not None and have != dim:
            raise StoreError(f"collection '{collection}' already exists with dimension {have}, not {dim}")
        self._ids[collection] = got["id"]

    async def upsert(self, collection: str, points: list[Point]) -> None:
        cid = await self._need(collection)
        for i in range(0, len(points), UPSERT_BATCH):
            batch = points[i:i + UPSERT_BATCH]
            await self._req("POST", f"{self._base}/{cid}/upsert", {
                "ids": [p.id for p in batch], "embeddings": [p.vector for p in batch],
                "documents": [str(p.payload.get("text", "")) for p in batch],
                "metadatas": [{k: v for k, v in p.payload.items() if k != "text"} for p in batch]})

    async def search(self, collection: str, vector: list[float], k: int) -> list[Hit]:
        cid = await self._id(collection)
        if cid is None:
            return []
        got = await self._req("POST", f"{self._base}/{cid}/query", {
            "query_embeddings": [vector], "n_results": k, "include": ["documents", "metadatas", "distances"]})
        ids, docs, metas, dists = ((got.get(key) or [[]])[0] for key in ("ids", "documents", "metadatas", "distances"))
        return [Hit(str(i), 1.0 - float(d), {**(m or {}), "text": doc or ""}) for i, doc, m, d in zip(ids, docs, metas, dists)]

    async def delete_doc(self, collection: str, doc_id: str) -> int:
        cid = await self._id(collection)
        if cid is None:
            return 0
        got = await self._req("POST", f"{self._base}/{cid}/get", {"where": {"doc_id": doc_id}, "include": []})
        ids = got.get("ids") or []
        if ids:
            await self._req("POST", f"{self._base}/{cid}/delete", {"ids": ids})
        return len(ids)

    async def drop(self, collection: str) -> None:
        self._ids.pop(collection, None)
        await self._req("DELETE", f"{self._base}/{_safe(collection)}", ok_404=True)

    async def count(self, collection: str) -> int:
        cid = await self._id(collection)
        if cid is None:
            return 0
        return int(await self._req("GET", f"{self._base}/{cid}/count"))
