"""Qdrant over its REST API: the Docker container we manage, or any remote server / Qdrant Cloud (URL + API key)."""
from __future__ import annotations

import httpx

from .base import Hit, Point, StoreError, VectorStore

UPSERT_BATCH = 128


class QdrantStore(VectorStore):
    kind = "qdrant"

    def __init__(self, url: str, api_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30):
        if not url.startswith(("http://", "https://")):
            raise StoreError("the Qdrant URL must start with http:// or https://")
        self.url = url.rstrip("/")
        self._headers = {"api-key": api_key} if api_key else {}
        self._transport, self._timeout = transport, timeout

    async def _req(self, method: str, path: str, json: dict | None = None, ok_404: bool = False) -> dict | None:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout, headers=self._headers) as c:
                r = await c.request(method, self.url + path, json=json)
        except httpx.HTTPError as e:
            raise StoreError(f"cannot reach Qdrant at {self.url}: {type(e).__name__}") from e
        if r.status_code == 404 and ok_404:
            return None
        if r.status_code in (401, 403):
            raise StoreError("Qdrant rejected the API key")
        if r.status_code >= 400:
            try:
                detail = (r.json().get("status") or {}).get("error") or r.text
            except ValueError:
                detail = r.text
            raise StoreError(f"Qdrant error {r.status_code}: {str(detail)[:300]}")
        return r.json()

    async def health(self) -> dict:
        try:
            info = await self._req("GET", "/")
            cols = await self._req("GET", "/collections")
        except StoreError as e:
            return {"ok": False, "detail": str(e)}
        n = len(((cols or {}).get("result") or {}).get("collections") or [])
        return {"ok": True, "detail": f"Qdrant {(info or {}).get('version', '?')} ({n} collection{'s' if n != 1 else ''})"}

    async def ensure(self, collection: str, dim: int) -> None:
        got = await self._req("GET", f"/collections/{collection}", ok_404=True)
        if got is not None:
            vectors = (((got.get("result") or {}).get("config") or {}).get("params") or {}).get("vectors") or {}
            have = vectors.get("size")
            if have != dim:
                raise StoreError(f"collection '{collection}' already exists with dimension {have}, not {dim}")
            return
        await self._req("PUT", f"/collections/{collection}", {"vectors": {"size": dim, "distance": "Cosine"}})
        await self._req("PUT", f"/collections/{collection}/index?wait=true", {"field_name": "doc_id", "field_schema": "keyword"})

    async def upsert(self, collection: str, points: list[Point]) -> None:
        for i in range(0, len(points), UPSERT_BATCH):
            batch = points[i:i + UPSERT_BATCH]
            await self._req("PUT", f"/collections/{collection}/points?wait=true",
                            {"points": [{"id": p.id, "vector": p.vector, "payload": p.payload} for p in batch]})

    async def search(self, collection: str, vector: list[float], k: int) -> list[Hit]:
        got = await self._req("POST", f"/collections/{collection}/points/search",
                              {"vector": vector, "limit": k, "with_payload": True}, ok_404=True)
        return [Hit(str(h["id"]), float(h["score"]), h.get("payload") or {}) for h in ((got or {}).get("result") or [])]

    async def delete_doc(self, collection: str, doc_id: str) -> int:
        flt = {"filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]}}
        got = await self._req("POST", f"/collections/{collection}/points/count", {**flt, "exact": True}, ok_404=True)
        if got is None:
            return 0
        await self._req("POST", f"/collections/{collection}/points/delete?wait=true", flt)
        return int((got.get("result") or {}).get("count") or 0)

    async def drop(self, collection: str) -> None:
        await self._req("DELETE", f"/collections/{collection}", ok_404=True)

    async def count(self, collection: str) -> int:
        got = await self._req("POST", f"/collections/{collection}/points/count", {"exact": True}, ok_404=True)
        return int(((got or {}).get("result") or {}).get("count") or 0)
