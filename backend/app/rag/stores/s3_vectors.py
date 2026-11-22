"""Amazon S3 Vectors: vector storage inside S3 itself (a "vector bucket" holding one "index" per knowledge base).

Agent Studio embeds the text (with the embedding model chosen on the knowledge base) and stores the vectors here, so this is a
normal VectorStore, not a managed one. Cosine distance, float32. S3 Vectors reports a distance (0 = identical); the score we
return is 1 - distance so that higher is closer like every other store.

* The vector bucket is created on first use if it does not exist (vector buckets are separate from normal S3 buckets).
* Each knowledge base gets one index named after it. The index dimension is fixed at creation.
* Chunk text and source are stored as NON-filterable metadata (S3 Vectors limits filterable metadata to 2 KB per vector).
* There is no delete-by-filter, so every key is "<doc_id>#<chunk id>": removing a document lists the keys and deletes the matching ones.
* S3 Vectors has no statistics call, so counting pages through the keys.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable

from .base import Hit, Point, StoreError, VectorStore
from .bedrock_kb import DENIED, MISSING, REGION_RE

BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
INDEX_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
PUT_BATCH = 500                    # API limits: 500 vectors per put and per delete
LIST_PAGE = 1000
MAX_TOP_K = 100
NOT_FILTERABLE = ["text", "source"]
GONE = MISSING | {"NotFoundException"}


class S3VectorsStore(VectorStore):
    kind = "s3_vectors"

    def __init__(self, region: str, bucket: str, clients: Callable[[str], object]):
        if not REGION_RE.match(region or ""):
            raise StoreError("the AWS region must look like us-east-1")
        if not BUCKET_RE.match(bucket or ""):
            raise StoreError("a vector bucket name is 3-63 characters: lowercase letters, digits and -")
        self.region, self.bucket, self._clients = region, bucket, clients
        self._bucket_ready = False

    # ------------------------------------------------------------ plumbing
    def _call_sync(self, op: str, ok_missing: bool = False, **kw):
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
        try:
            return getattr(self._clients("s3vectors"), op)(**kw)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            message = str(e.response.get("Error", {}).get("Message", ""))[:300]
            if code in GONE:
                if ok_missing:
                    return None
                raise StoreError(f"not found in S3 Vectors ({code}): {message}") from e
            if code in DENIED:
                raise StoreError(f"AWS rejected the credentials or they lack S3 Vectors permission ({code}): {message}") from e
            raise StoreError(f"S3 Vectors error {code}: {message}") from e
        except NoCredentialsError as e:
            raise StoreError("no AWS credentials found: set keys on the store, a profile, or sign in with the AWS CLI") from e
        except BotoCoreError as e:
            raise StoreError(f"cannot reach S3 Vectors in {self.region}: {type(e).__name__}") from e

    async def _call(self, op: str, **kw):
        return await asyncio.to_thread(lambda: self._call_sync(op, **kw))

    @staticmethod
    def _index(collection: str) -> str:
        name = collection.lower().replace("_", "-")
        if not INDEX_RE.match(name):
            raise StoreError("an index name is 3-63 characters: lowercase letters, digits, - and .")
        return name

    async def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        if await self._call("get_vector_bucket", ok_missing=True, vectorBucketName=self.bucket) is None:
            try:
                await self._call("create_vector_bucket", vectorBucketName=self.bucket)
            except StoreError as e:
                if "ConflictException" not in str(e):         # created by someone else a moment ago is fine
                    raise
        self._bucket_ready = True

    async def _keys(self, index: str, prefix: str = "") -> list[str]:
        keys, token = [], None
        while True:
            got = await self._call("list_vectors", ok_missing=True, vectorBucketName=self.bucket, indexName=index, maxResults=LIST_PAGE,
                                   **({"nextToken": token} if token else {}))
            if got is None:
                return keys
            keys += [v["key"] for v in got.get("vectors") or [] if v["key"].startswith(prefix)]
            token = got.get("nextToken")
            if not token:
                return keys

    # ------------------------------------------------------------ VectorStore
    async def health(self) -> dict:
        try:
            got = await self._call("get_vector_bucket", ok_missing=True, vectorBucketName=self.bucket)
            if got is None:
                return {"ok": True, "detail": f"S3 Vectors in {self.region}: bucket '{self.bucket}' does not exist yet and will be created on first use"}
            idx = await self._call("list_indexes", vectorBucketName=self.bucket, maxResults=100)
        except StoreError as e:
            return {"ok": False, "detail": str(e)}
        n = len(idx.get("indexes") or [])
        return {"ok": True, "detail": f"S3 Vectors bucket '{self.bucket}' in {self.region}: {n} index{'es' if n != 1 else ''}"}

    async def ensure(self, collection: str, dim: int) -> None:
        index = self._index(collection)
        await self._ensure_bucket()
        got = await self._call("get_index", ok_missing=True, vectorBucketName=self.bucket, indexName=index)
        if got is None:
            await self._call("create_index", vectorBucketName=self.bucket, indexName=index, dataType="float32", dimension=dim,
                             distanceMetric="cosine", metadataConfiguration={"nonFilterableMetadataKeys": NOT_FILTERABLE})
            return
        have = got["index"]
        if have.get("dimension") != dim:
            raise StoreError(f"the S3 Vectors index '{index}' has dimension {have.get('dimension')}, but this embedding model produces {dim}")
        if have.get("distanceMetric") != "cosine":
            raise StoreError(f"the S3 Vectors index '{index}' uses {have.get('distanceMetric')} distance; Agent Studio needs cosine")

    async def upsert(self, collection: str, points: list[Point]) -> None:
        index = self._index(collection)
        for i in range(0, len(points), PUT_BATCH):
            await self._call("put_vectors", vectorBucketName=self.bucket, indexName=index, vectors=[
                {"key": f"{p.payload.get('doc_id', '')}#{p.id}", "data": {"float32": [float(x) for x in p.vector]}, "metadata": p.payload}
                for p in points[i:i + PUT_BATCH]])

    async def search(self, collection: str, vector: list[float], k: int) -> list[Hit]:
        got = await self._call("query_vectors", ok_missing=True, vectorBucketName=self.bucket, indexName=self._index(collection),
                               queryVector={"float32": [float(x) for x in vector]}, topK=max(1, min(k, MAX_TOP_K)),
                               returnMetadata=True, returnDistance=True)
        return [Hit(v["key"], 1.0 - float(v["distance"]), v.get("metadata") or {}) for v in (got or {}).get("vectors") or []]

    async def delete_doc(self, collection: str, doc_id: str) -> int:
        index = self._index(collection)
        keys = await self._keys(index, f"{doc_id}#")
        for i in range(0, len(keys), PUT_BATCH):
            await self._call("delete_vectors", vectorBucketName=self.bucket, indexName=index, keys=keys[i:i + PUT_BATCH])
        return len(keys)

    async def drop(self, collection: str) -> None:
        await self._call("delete_index", ok_missing=True, vectorBucketName=self.bucket, indexName=self._index(collection))

    async def count(self, collection: str) -> int:
        return len(await self._keys(self._index(collection)))
