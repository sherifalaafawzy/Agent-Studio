"""Test doubles for the RAG tests: a deterministic embedder, a LiteLLM-shaped response, and an in-memory Qdrant."""
from __future__ import annotations

import math
import re
import zlib

import httpx

DIM = 32


def vec_for(text: str, dim: int = DIM) -> list[float]:
    """Hashed bag of words: texts that share words are close (cosine), unrelated texts are not."""
    v = [0.0] * dim
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        v[zlib.crc32(w.encode()) % dim] += 1.0
    return v


class FakeEmbedder:
    """Stands in for rag.embeddings.Embedder."""

    def __init__(self, dim: int = DIM):
        self.dim, self.calls = dim, []

    async def embed(self, provider_id, model, texts):
        self.calls.append((provider_id, model, list(texts)))
        return [vec_for(t, self.dim) for t in texts]


def embedding_response(texts, usage=True, shuffle=False, dim: int = DIM):
    """What litellm.aembedding returns (dict form)."""
    rows = [{"object": "embedding", "index": i, "embedding": vec_for(t, dim)} for i, t in enumerate(texts)]
    if shuffle:
        rows = rows[::-1]
    out = {"data": rows}
    if usage:
        out["usage"] = {"prompt_tokens": sum(len(t.split()) for t in texts) or 1, "total_tokens": 1}
    return out


class FakeQdrant:
    """An in-memory Qdrant speaking just the REST routes the adapter uses. Use .transport with QdrantStore."""

    def __init__(self, version: str = "1.20.0", api_key: str | None = None):
        self.cols: dict[str, dict] = {}
        self.version, self.api_key, self.requests = version, api_key, []
        self.transport = httpx.MockTransport(self.handle)

    @staticmethod
    def _cos(a, b):
        na, nb = math.sqrt(sum(x * x for x in a)) or 1, math.sqrt(sum(x * x for x in b)) or 1
        return sum(x * y for x, y in zip(a, b)) / (na * nb)

    @staticmethod
    def _match(point, flt):
        for cond in (flt or {}).get("must", []):
            if point["payload"].get(cond["key"]) != cond["match"]["value"]:
                return False
        return True

    def handle(self, req: httpx.Request) -> httpx.Response:
        import json
        self.requests.append((req.method, req.url.path))
        if self.api_key and req.headers.get("api-key") != self.api_key:
            return httpx.Response(401, json={"status": {"error": "Must provide an API key"}})
        body = json.loads(req.content) if req.content else {}
        p = req.url.path
        if p == "/":
            return httpx.Response(200, json={"title": "qdrant", "version": self.version})
        if p == "/collections":
            return httpx.Response(200, json={"result": {"collections": [{"name": n} for n in self.cols]}})
        parts = p.strip("/").split("/")                       # collections/{name}[/points[/op]] | collections/{name}/index
        name = parts[1]
        tail = "/".join(parts[2:])
        col = self.cols.get(name)
        if req.method == "PUT" and tail == "":
            self.cols[name] = {"dim": body["vectors"]["size"], "points": {}}
            return httpx.Response(200, json={"result": True})
        if col is None:
            return httpx.Response(404, json={"status": {"error": f"Collection `{name}` doesn't exist!"}})
        if req.method == "GET" and tail == "":
            return httpx.Response(200, json={"result": {"config": {"params": {"vectors": {"size": col["dim"], "distance": "Cosine"}}}}})
        if req.method == "DELETE" and tail == "":
            del self.cols[name]
            return httpx.Response(200, json={"result": True})
        if tail == "index":
            return httpx.Response(200, json={"result": {"status": "acknowledged"}})
        if tail == "points" and req.method == "PUT":
            for pt in body["points"]:
                if len(pt["vector"]) != col["dim"]:
                    return httpx.Response(400, json={"status": {"error": "Wrong input: Vector dimension error"}})
                col["points"][pt["id"]] = pt
            return httpx.Response(200, json={"result": {"status": "completed"}})
        if tail == "points/search":
            scored = sorted(((self._cos(body["vector"], q["vector"]), q) for q in col["points"].values()), key=lambda t: -t[0])
            return httpx.Response(200, json={"result": [{"id": q["id"], "score": s, "payload": q["payload"]} for s, q in scored[:body["limit"]]]})
        if tail == "points/count":
            n = sum(1 for q in col["points"].values() if self._match(q, body.get("filter")))
            return httpx.Response(200, json={"result": {"count": n}})
        if tail == "points/delete":
            for pid in [i for i, q in col["points"].items() if self._match(q, body.get("filter"))]:
                del col["points"][pid]
            return httpx.Response(200, json={"result": {"status": "completed"}})
        return httpx.Response(500, text="unhandled route in FakeQdrant")
