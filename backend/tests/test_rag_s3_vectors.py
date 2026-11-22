"""Amazon S3 Vectors store: adapter behaviour against an in-memory fake of the s3vectors API, plus registry/service wiring."""
from __future__ import annotations

import asyncio
import math

import pytest

from app.rag.stores import registry as R
from app.rag.stores.base import Point, StoreError
from app.rag.stores.s3_vectors import S3VectorsStore
from tests.rag_fakes_stores import aws_error


def run(c):
    return asyncio.run(c)


class FakeS3V:
    """The s3vectors client calls S3VectorsStore makes. Stores vectors in memory; distance = 1 - cosine."""

    def __init__(self, bucket_exists: bool = True):
        self.buckets = {"vec-bucket"} if bucket_exists else set()
        self.indexes: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, Exception] = {}
        self.page = 1000

    def clients(self, service: str):
        assert service == "s3vectors"
        return self

    def __getattr__(self, op):
        def call(**kw):
            self.calls.append((op, kw))
            if op in self.fail:
                raise self.fail[op]
            return getattr(self, "_" + op)(**kw)
        return call

    def _need_bucket(self, kw):
        if kw["vectorBucketName"] not in self.buckets:
            raise aws_error("NotFoundException", "no bucket")

    def _need_index(self, kw):
        self._need_bucket(kw)
        if kw["indexName"] not in self.indexes:
            raise aws_error("NotFoundException", "no index")
        return self.indexes[kw["indexName"]]

    def _get_vector_bucket(self, **kw):
        self._need_bucket(kw)
        return {"vectorBucket": {"vectorBucketName": kw["vectorBucketName"]}}

    def _create_vector_bucket(self, **kw):
        self.buckets.add(kw["vectorBucketName"])
        return {}

    def _list_indexes(self, **kw):
        self._need_bucket(kw)
        return {"indexes": [{"indexName": n} for n in self.indexes]}

    def _get_index(self, **kw):
        ix = self._need_index(kw)
        return {"index": {"indexName": kw["indexName"], "dimension": ix["dimension"], "distanceMetric": ix["distanceMetric"]}}

    def _create_index(self, **kw):
        self._need_bucket(kw)
        self.indexes[kw["indexName"]] = {"dimension": kw["dimension"], "distanceMetric": kw["distanceMetric"],
                                         "meta": kw["metadataConfiguration"], "vectors": {}}
        return {}

    def _delete_index(self, **kw):
        self._need_index(kw)
        del self.indexes[kw["indexName"]]
        return {}

    def _put_vectors(self, **kw):
        ix = self._need_index(kw)
        assert 1 <= len(kw["vectors"]) <= 500
        for v in kw["vectors"]:
            ix["vectors"][v["key"]] = (v["data"]["float32"], v["metadata"])
        return {}

    def _query_vectors(self, **kw):
        ix = self._need_index(kw)
        q = kw["queryVector"]["float32"]
        assert kw["returnMetadata"] and kw["returnDistance"] and 1 <= kw["topK"] <= 100
        scored = []
        for key, (vec, meta) in ix["vectors"].items():
            cos = sum(a * b for a, b in zip(q, vec)) / ((math.hypot(*q) * math.hypot(*vec)) or 1)
            scored.append({"key": key, "distance": 1 - cos, "metadata": meta})
        return {"vectors": sorted(scored, key=lambda v: v["distance"])[:kw["topK"]], "distanceMetric": "cosine"}

    def _list_vectors(self, **kw):
        ix = self._need_index(kw)
        keys = sorted(ix["vectors"])
        start = int(kw.get("nextToken") or 0)
        chunk = keys[start:start + self.page]
        out = {"vectors": [{"key": k} for k in chunk]}
        if start + self.page < len(keys):
            out["nextToken"] = str(start + self.page)
        return out

    def _delete_vectors(self, **kw):
        ix = self._need_index(kw)
        assert 1 <= len(kw["keys"]) <= 500
        for k in kw["keys"]:
            ix["vectors"].pop(k, None)
        return {}


def store(fake=None, bucket="vec-bucket"):
    fake = fake or FakeS3V()
    return S3VectorsStore("us-east-1", bucket, fake.clients), fake


def pts(n, doc="d1", dim=3):
    return [Point(f"p{i}", [1.0 if j == i % dim else 0.0 for j in range(dim)], {"doc_id": doc, "source": "s.md", "chunk": i, "text": f"t{i}"})
            for i in range(n)]


def test_rejects_bad_region_and_bucket():
    f = FakeS3V().clients
    with pytest.raises(StoreError, match="region"):
        S3VectorsStore("nowhere", "vec-bucket", f)
    with pytest.raises(StoreError, match="vector bucket name"):
        S3VectorsStore("us-east-1", "Bad_Bucket", f)
    with pytest.raises(StoreError, match="index name"):
        run(store()[0].ensure("x", 3))               # too short for an index name


def test_ensure_creates_bucket_and_index_once_and_checks_the_shape():
    s, f = store(FakeS3V(bucket_exists=False))
    run(s.ensure("kb_my_notes", 3))
    assert "vec-bucket" in f.buckets and list(f.indexes) == ["kb-my-notes"]
    ix = f.indexes["kb-my-notes"]
    assert ix["dimension"] == 3 and ix["distanceMetric"] == "cosine" and ix["meta"] == {"nonFilterableMetadataKeys": ["text", "source"]}
    run(s.ensure("kb_my_notes", 3))                  # same shape: no-op, and the bucket is not checked again
    assert sum(1 for op, _ in f.calls if op == "get_vector_bucket") == 1
    with pytest.raises(StoreError, match="has dimension 3, but this embedding model produces 8"):
        run(s.ensure("kb_my_notes", 8))
    ix["distanceMetric"] = "euclidean"
    with pytest.raises(StoreError, match="needs cosine"):
        run(s.ensure("kb_my_notes", 3))


def test_bucket_created_by_someone_else_in_the_meantime_is_fine_but_other_errors_are_not():
    f = FakeS3V(bucket_exists=False)
    def race(**kw):
        f.buckets.add(kw["vectorBucketName"])        # somebody else created it between our check and our create
        raise aws_error("ConflictException", "exists")
    f._create_vector_bucket = race
    s, _ = store(f)
    run(s.ensure("kb_a_b", 3))
    assert "kb-a-b" in f.indexes
    f2 = FakeS3V(bucket_exists=False)
    f2.fail["create_vector_bucket"] = aws_error("ServiceQuotaExceededException", "too many")
    with pytest.raises(StoreError, match="S3 Vectors error ServiceQuotaExceededException"):
        run(store(f2)[0].ensure("kb_a_b", 3))


def test_upsert_search_count_and_delete_doc():
    s, f = store()
    run(s.ensure("kb_x", 3))
    run(s.upsert("kb_x", pts(3, "d1") + pts(2, "d2")))
    assert run(s.count("kb_x")) == 5
    keys = sorted(f.indexes["kb-x"]["vectors"])
    assert keys[0] == "d1#p0" and all(k.startswith(("d1#", "d2#")) for k in keys)
    hits = run(s.search("kb_x", [1.0, 0.0, 0.0], 2))
    assert [h.payload["chunk"] for h in hits][0] == 0 and hits[0].score == pytest.approx(1.0) and hits[0].score >= hits[1].score
    assert hits[0].payload["text"] == "t0" and hits[0].id.endswith("#p0")
    assert run(s.search("kb_x", [1.0, 0.0, 0.0], 1000)) and f.calls[-1][1]["topK"] == 100      # clamped to the API maximum
    assert run(s.delete_doc("kb_x", "d1")) == 3 and run(s.count("kb_x")) == 2
    assert run(s.delete_doc("kb_x", "nope")) == 0


def test_large_batches_and_paging():
    s, f = store()
    f.page = 2
    run(s.ensure("kb_big", 3))
    run(s.upsert("kb_big", pts(1203, "big")))
    assert [len(kw["vectors"]) for op, kw in f.calls if op == "put_vectors"] == [500, 500, 203]
    f.page = 1000
    assert run(s.count("kb_big")) == 1203
    assert run(s.delete_doc("kb_big", "big")) == 1203
    assert [len(kw["keys"]) for op, kw in f.calls if op == "delete_vectors"] == [500, 500, 203]
    f.page = 2
    run(s.upsert("kb_big", pts(5, "p")))
    assert run(s.count("kb_big")) == 5                                  # follows nextToken across pages


def test_missing_index_is_empty_and_drop_is_idempotent():
    s, f = store()
    assert run(s.search("kb_gone", [1.0, 0.0, 0.0], 3)) == []
    assert run(s.count("kb_gone")) == 0
    assert run(s.delete_doc("kb_gone", "d")) == 0
    run(s.drop("kb_gone"))
    run(s.ensure("kb_here", 3))
    run(s.drop("kb_here"))
    assert f.indexes == {}


def test_health_reports_instead_of_raising():
    s, f = store()
    assert run(s.health()) == {"ok": True, "detail": "S3 Vectors bucket 'vec-bucket' in us-east-1: 0 indexes"}
    run(s.ensure("kb_one", 3))
    assert "1 index" in run(s.health())["detail"] and "indexes" not in run(s.health())["detail"]
    s2, _ = store(FakeS3V(bucket_exists=False))
    h = run(s2.health())
    assert h["ok"] and "will be created on first use" in h["detail"]
    f.fail["get_vector_bucket"] = aws_error("AccessDeniedException", "nope")
    h = run(s.health())
    assert not h["ok"] and "lack S3 Vectors permission" in h["detail"]


def test_aws_errors_are_explained():
    from botocore.exceptions import EndpointConnectionError, NoCredentialsError
    s, f = store()
    run(s.ensure("kb_e", 3))
    f.fail["query_vectors"] = aws_error("ExpiredTokenException", "expired")
    with pytest.raises(StoreError, match="rejected the credentials"):
        run(s.search("kb_e", [1.0, 0.0, 0.0], 1))
    f.fail["query_vectors"] = EndpointConnectionError(endpoint_url="https://s3vectors.example")
    with pytest.raises(StoreError, match="cannot reach S3 Vectors in us-east-1: EndpointConnectionError"):
        run(s.search("kb_e", [1.0, 0.0, 0.0], 1))
    f.fail["query_vectors"] = NoCredentialsError()
    with pytest.raises(StoreError, match="no AWS credentials found"):
        run(s.search("kb_e", [1.0, 0.0, 0.0], 1))
    f.fail["put_vectors"] = aws_error("NotFoundException", "index vanished")
    with pytest.raises(StoreError, match="not found in S3 Vectors"):
        run(s.upsert("kb_e", pts(1)))


def test_registry_builds_and_validates_it(tmp_path, monkeypatch):
    import boto3
    seen = {}

    class Sess:
        def __init__(self, **kw):
            seen.update(kw)

        def client(self, name):
            seen["client"] = name
            return object()
    monkeypatch.setattr(boto3, "Session", Sess)
    st = R.make_store("s3_vectors", {"region": "eu-west-1", "bucket": "my-vecs"}, {"access_key_id": "AKIA1", "secret_access_key": "sec"}, tmp_path)
    assert isinstance(st, S3VectorsStore) and st.bucket == "my-vecs" and seen["region_name"] == "eu-west-1" and seen["aws_access_key_id"] == "AKIA1"
    st._clients("s3vectors")
    assert seen["client"] == "s3vectors"
    k = R.KINDS["s3_vectors"]
    assert not k.managed and k.docker_stack is None and k.ingest == "yes"
    assert {f.name for f in k.fields if f.secret} == {"access_key_id", "secret_access_key", "session_token"}
    with pytest.raises(StoreError, match="AWS region is required"):
        R.check_fields("s3_vectors", {"bucket": "b-1"}, {})
    with pytest.raises(StoreError, match="Vector bucket name is required"):
        R.check_fields("s3_vectors", {"region": "us-east-1"}, {})
    with pytest.raises(StoreError, match="both the access key id and the secret"):
        R.check_fields("s3_vectors", {"region": "us-east-1", "bucket": "b-1"}, {"secret_access_key": "x"})
    R.check_fields("s3_vectors", {"region": "us-east-1", "bucket": "b-1"}, {})
