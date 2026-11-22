"""Pinecone over REST (remote only). Agent Studio never creates or deletes indexes: you bring an existing serverless
index with the right dimension and the cosine metric, and every knowledge base gets its own NAMESPACE inside it.

Pinecone serverless cannot delete by metadata filter, so each vector id is "<doc_id>#<chunk id>": removing a document
lists the ids with that prefix and deletes them. New vectors can take a few seconds to become searchable.
"""
from __future__ import annotations

import re

import httpx

from .base import Hit, Point, StoreError, VectorStore

API_VERSION = "2025-10"
CONTROL = "https://api.pinecone.io"
UPSERT_BATCH = 100
DELETE_BATCH = 1000
_NAME = re.compile(r"^[a-z0-9-]{1,45}$")
_NS = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class PineconeStore(VectorStore):
    kind = "pinecone"

    def __init__(self, api_key: str, index_host: str = "", index_name: str = "",
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30):
        if not api_key:
            raise StoreError("a Pinecone API key is required")
        if not index_host and not index_name:
            raise StoreError("give the index host (from the Pinecone console) or the index name")
        if index_name and not _NAME.match(index_name):
            raise StoreError("a Pinecone index name uses lowercase letters, digits and - only")
        host = index_host.strip().rstrip("/")
        self._host = host if host.startswith(("http://", "https://")) or not host else f"https://{host}"
        self._name = index_name
        self._meta: dict = {}
        self._headers = {"Api-Key": api_key, "X-Pinecone-Api-Version": API_VERSION}
        self._transport, self._timeout = transport, timeout

    async def _req(self, method: str, base: str, path: str, json=None, params=None, ok_404: bool = False):
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout, headers=self._headers) as c:
                r = await c.request(method, base + path, json=json, params=params)
        except httpx.HTTPError as e:
            raise StoreError(f"cannot reach Pinecone at {base}: {type(e).__name__}") from e
        if r.status_code == 404 and ok_404:
            return None
        if r.status_code in (401, 403):
            raise StoreError("Pinecone rejected the API key")
        if r.status_code >= 400:
            try:
                body = r.json()
                err = body.get("message") or (body.get("error") or {}).get("message") if isinstance(body, dict) else None
            except ValueError:
                err = None
            raise StoreError(f"Pinecone error {r.status_code}: {str(err or r.text)[:300]}")
        return r.json() if r.content else {}

    async def _base(self) -> str:
        if not self._host:
            info = await self._req("GET", CONTROL, f"/indexes/{self._name}", ok_404=True)
            if info is None:
                raise StoreError(f"Pinecone index '{self._name}' does not exist: create it in the Pinecone console "
                                 "(serverless, cosine metric) with this embedding model's dimension")
            self._meta = info
            self._host = "https://" + info["host"]
        return self._host

    async def _stats(self) -> dict:
        """Index statistics, after checking the index is usable (cosine metric, when Pinecone told us the metric)."""
        stats = await self._req("POST", await self._base(), "/describe_index_stats", {})
        metric = self._meta.get("metric")
        if metric not in (None, "cosine"):
            raise StoreError(f"the Pinecone index uses the '{metric}' metric; Agent Studio needs a cosine index")
        return stats

    @staticmethod
    def _ns(collection: str) -> str:
        if not _NS.match(collection):
            raise StoreError("the namespace may only contain letters, digits, - and _ (1-64 characters)")
        return collection

    async def health(self) -> dict:
        try:
            s = await self._stats()
        except StoreError as e:
            return {"ok": False, "detail": str(e)}
        n = len(s.get("namespaces") or {})
        return {"ok": True, "detail": f"Pinecone index: {s.get('dimension', '?')} dimensions, {s.get('totalVectorCount', 0)} vectors, "
                                      f"{n} namespace{'s' if n != 1 else ''}"}

    async def ensure(self, collection: str, dim: int) -> None:
        self._ns(collection)
        have = (await self._stats()).get("dimension")
        if have != dim:
            raise StoreError(f"the Pinecone index has dimension {have}, but this embedding model produces {dim}. "
                             "Indexes cannot change size: use an index created with the right dimension")

    async def upsert(self, collection: str, points: list[Point]) -> None:
        ns, base = self._ns(collection), await self._base()
        for i in range(0, len(points), UPSERT_BATCH):
            await self._req("POST", base, "/vectors/upsert", {"namespace": ns, "vectors": [
                {"id": f"{p.payload.get('doc_id', '')}#{p.id}", "values": p.vector, "metadata": p.payload}
                for p in points[i:i + UPSERT_BATCH]]})

    async def search(self, collection: str, vector: list[float], k: int) -> list[Hit]:
        got = await self._req("POST", await self._base(), "/query", {
            "namespace": self._ns(collection), "vector": vector, "topK": k, "includeMetadata": True})
        return [Hit(str(m["id"]), float(m["score"]), m.get("metadata") or {}) for m in got.get("matches") or []]

    async def _ids_of(self, ns: str, doc_id: str) -> list[str]:
        base, ids, token = await self._base(), [], None
        while True:
            params = {"namespace": ns, "prefix": f"{doc_id}#", **({"paginationToken": token} if token else {})}
            got = await self._req("GET", base, "/vectors/list", params=params, ok_404=True)
            if got is None:
                return ids
            ids += [v["id"] for v in got.get("vectors") or []]
            token = (got.get("pagination") or {}).get("next")
            if not token:
                return ids

    async def delete_doc(self, collection: str, doc_id: str) -> int:
        ns = self._ns(collection)
        ids = await self._ids_of(ns, doc_id)
        for i in range(0, len(ids), DELETE_BATCH):
            await self._req("POST", await self._base(), "/vectors/delete", {"namespace": ns, "ids": ids[i:i + DELETE_BATCH]})
        return len(ids)

    async def drop(self, collection: str) -> None:
        await self._req("POST", await self._base(), "/vectors/delete", {"namespace": self._ns(collection), "deleteAll": True}, ok_404=True)

    async def count(self, collection: str) -> int:
        ns = self._ns(collection)
        return int(((await self._stats()).get("namespaces") or {}).get(ns, {}).get("vectorCount", 0))
