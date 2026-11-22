"""Memory backends: a vector database, Vertex AI Memory Bank, Amazon Bedrock AgentCore Memory and Mem0 - all against fakes.

Requests are checked against the real API descriptions: AgentCore calls are validated with botocore's own service model (the same check boto3
runs before sending), and the Vertex request bodies are limited to fields in Google's published discovery document.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime

import httpx
import pytest

from app.memory import model as M
from app.memory.remote import COLLECTION, FACTORY, AgentCoreMemory, Mem0, VectorMirror, VertexMemoryBank
from app.memory.service import MemoryError_, MemoryService
from app.memory.store import Kind, MemoryFactory, RemoteIndex
from app.rag.stores.base import StoreError
from app.rag.stores.qdrant import QdrantStore
from tests.rag_fakes import FakeEmbedder, FakeQdrant, vec_for
from tests.rag_fakes_stores import aws_error

run = asyncio.run
REC = dict(id="m_abc", scope="user", scope_id="local-user", type="semantic", text="The billing system is Xero", updated=1_700_000_000.0, remote_id="")


# ------------------------------------------------------------------------------------------------ a vector database
def mirror():
    q = FakeQdrant()
    return VectorMirror(QdrantStore("http://qdrant.test", None, transport=q.transport), "qdrant"), q


def test_vector_mirror_stores_searches_and_deletes_by_scope():
    m, q = mirror()
    assert run(m.health())["ok"] and m.kind == "vector" and m.cloud is False
    rid = run(m.put(REC, vec_for(REC["text"])))
    run(m.put({**REC, "id": "m_other", "scope_id": "someone-else", "text": "The billing team"}, vec_for("The billing team")))
    assert rid and COLLECTION in q.cols
    hits = run(m.search("billing system", vec_for("billing system"), "user", "local-user", 5))
    assert [h[0] for h in hits] == ["m_abc"] and hits[0][1] > 0.5                       # the other scope's memory is filtered out
    assert run(m.search("billing", None, "user", "local-user", 5)) == []
    run(m.delete(REC))
    assert run(m.search("billing system", vec_for("billing system"), "user", "local-user", 5)) == []
    with pytest.raises(MemoryError_, match="needs an embedding model"):
        run(m.put(REC, None))


def test_vector_mirror_turns_store_failures_into_memory_errors():
    class Down:
        async def health(self): return {"ok": False, "detail": "down"}
        async def ensure(self, *a): raise StoreError("no store")
        async def upsert(self, *a): raise StoreError("no store")
        async def search(self, *a): raise StoreError("search broke")
        async def delete_doc(self, *a): raise StoreError("delete broke")
    m = VectorMirror(Down())
    for call in (lambda: m.put(REC, [1.0]), lambda: m.search("q", [1.0], "user", "x", 3), lambda: m.delete(REC)):
        with pytest.raises(MemoryError_):
            run(call())


# ------------------------------------------------------------------------------------------------ Vertex AI Memory Bank
BANK = "projects/demo/locations/us-central1/reasoningEngines/123"
ALLOWED_MEMORY = {"fact", "displayName", "scope", "ttl", "expireTime", "metadata", "description", "topics", "context"}              # from the discovery document
ALLOWED_RETRIEVE = {"scope", "similaritySearchParams", "simpleRetrievalParams", "filter", "memoryTypes", "filterGroups"}
ALLOWED_SIMILARITY = {"searchQuery", "topK"}


class FakeVertex:
    def __init__(self, slow: int = 0, fail_op: bool = False):
        self.memories, self.ops, self.slow, self.fail_op, self.calls = {}, {}, slow, fail_op, []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        path, body = req.url.path, (json.loads(req.content) if req.content else None)
        self.calls.append((req.method, path))
        assert req.headers["authorization"] == "Bearer tok" and req.url.host == "us-central1-aiplatform.googleapis.com"
        if req.method == "POST" and path.endswith("/memories"):
            assert set(body) <= ALLOWED_MEMORY and body["scope"] == {"agent_studio_scope": "user", "agent_studio_id": "local-user"}
            name = f"{BANK}/memories/{len(self.memories) + 1}"
            self.memories[name] = {"name": name, **body}
            return self.op(name)
        if req.method == "POST" and path.endswith("/memories:retrieve"):
            assert set(body) <= ALLOWED_RETRIEVE and set(body["similaritySearchParams"]) <= ALLOWED_SIMILARITY
            found = [{"memory": m, "distance": 0.2} for m in self.memories.values() if m["scope"] == body["scope"] and m["fact"]]
            return httpx.Response(200, json={"retrievedMemories": found + [{"memory": {"name": "x/y"}, "distance": 0.5}]})
        if req.method == "DELETE":
            self.memories.pop(path.lstrip("/").removeprefix("v1beta1/"), None)
            return self.op("deleted")
        if req.method == "GET" and "/operations/" in path:
            self.ops[path] = self.ops.get(path, 0) + 1
            return httpx.Response(200, json=self.opjson(path.removeprefix("/v1beta1/"), self.ops[path] > self.slow))
        if req.method == "GET" and path.endswith("/memories"):
            return httpx.Response(200, json={"memories": []})
        return httpx.Response(500, text="unhandled " + path)

    def opjson(self, name, done):
        if self.fail_op:
            return {"name": name, "done": True, "error": {"message": "quota exceeded"}}
        return {"name": name, "done": done, **({"response": {"name": self.last}} if done else {})}

    def op(self, name):
        self.last = name
        opname = f"{BANK}/operations/{len(self.ops) + 1}"
        if self.slow:
            return httpx.Response(200, json={"name": opname, "done": False})
        return httpx.Response(200, json=self.opjson(opname, True))


def vertex(fake=None, token=lambda: "tok"):
    fake = fake or FakeVertex()
    return VertexMemoryBank(BANK, token, fake.transport, poll=0), fake


def test_vertex_memory_bank_roundtrip():
    v, f = vertex()
    assert v.kind == "vertex" and v.cloud is True and run(v.health())["ok"]
    name = run(v.put(REC, None))
    assert name.startswith(BANK + "/memories/")
    got = run(v.search("billing", None, "user", "local-user", 3))
    assert got[0][0] == "m_abc" and got[0][1] == pytest.approx(0.8) and got[1][0] == "x/y"           # display name = our id; else the remote name; closer = higher
    assert run(v.search("billing", None, "user", "another", 3)) == [("x/y", 0.5)]
    run(v.delete({**REC, "remote_id": name}))
    assert name not in f.memories
    run(v.delete(REC))                                                                              # nothing remote to remove


def test_vertex_waits_for_long_running_operations():
    v, f = vertex(FakeVertex(slow=2))
    name = run(v.put(REC, None))
    assert name and sum(f.ops.values()) == 3
    v2, _ = vertex(FakeVertex(slow=50))
    with pytest.raises(MemoryError_, match="still processing"):
        run(v2.put(REC, None))
    v3, _ = vertex(FakeVertex(fail_op=True))
    with pytest.raises(MemoryError_, match="quota exceeded"):
        run(v3.put(REC, None))


def test_vertex_errors_are_explained():
    for status, text in ((401, "no"), (403, "no")):
        v = VertexMemoryBank(BANK, lambda: "tok", httpx.MockTransport(lambda r, s=status: httpx.Response(s, text="no")), poll=0)
        with pytest.raises(MemoryError_, match="rejected the credentials"):
            run(v.put(REC, None))
    v = VertexMemoryBank(BANK, lambda: "tok", httpx.MockTransport(lambda r: httpx.Response(400, text="bad scope")), poll=0)
    with pytest.raises(MemoryError_, match="Vertex AI error 400: bad scope"):
        run(v.search("q", None, "user", "x", 1))
    assert run(v.health())["ok"] is False

    def boom(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(MemoryError_, match="cannot reach Vertex AI: ConnectError"):
        run(VertexMemoryBank(BANK, lambda: "tok", httpx.MockTransport(boom), poll=0).put(REC, None))

    def no_creds():
        raise StoreError("the service account JSON is not valid")

    def other():
        raise RuntimeError("DefaultCredentialsError")
    with pytest.raises(MemoryError_, match="service account JSON"):
        run(VertexMemoryBank(BANK, no_creds, httpx.MockTransport(boom), poll=0).put(REC, None))
    with pytest.raises(MemoryError_, match="Google credentials are not usable: RuntimeError"):
        run(VertexMemoryBank(BANK, other, httpx.MockTransport(boom), poll=0).put(REC, None))
    for bad in ("", "projects/p/locations/l/other/1", "nonsense"):
        with pytest.raises(MemoryError_, match="memory bank must look like"):
            VertexMemoryBank(bad, lambda: "t")
    assert VertexMemoryBank("projects/p/locations/europe-west4/memoryBanks/9", lambda: "t")._base.startswith("https://europe-west4-aiplatform")


def test_vertex_empty_responses():
    v = VertexMemoryBank(BANK, lambda: "tok", httpx.MockTransport(lambda r: httpx.Response(200, content=b"")), poll=0)
    with pytest.raises(MemoryError_):                          # an empty body is not a finished operation
        run(v.put(REC, None))


# ------------------------------------------------------------------------------------------------ Amazon Bedrock AgentCore Memory
MEMORY_ID = "agent_studio-AbC123XyZ9"


class FakeAgentCore:
    """Both AgentCore clients. Every call's parameters are validated against the real botocore service model first."""

    def __init__(self):
        import boto3
        self.models = {s: boto3.client(s, region_name="us-east-1", aws_access_key_id="a", aws_secret_access_key="b").meta.service_model
                       for s in ("bedrock-agentcore", "bedrock-agentcore-control")}
        self.records: dict[str, dict] = {}
        self.fail: dict[str, Exception] = {}
        self.fail_records = False
        self.status = "ACTIVE"

    def clients(self, service: str):
        return _Client(self, service)


class _Client:
    def __init__(self, fake, service):
        self.fake, self.service = fake, service

    def __getattr__(self, op):
        from botocore.validate import validate_parameters

        def call(**kw):
            camel = "".join(p.title() for p in op.split("_"))
            validate_parameters(kw, self.fake.models[self.service].operation_model(camel).input_shape)         # raises ParamValidationError if it is not a valid request
            if op in self.fake.fail:
                raise self.fake.fail[op]
            return getattr(self, "_" + op)(**kw)
        return call

    def _get_memory(self, memoryId):
        return {"memory": {"id": memoryId, "status": self.fake.status}}

    def _batch_create_memory_records(self, memoryId, records):
        if self.fake.fail_records:
            return {"successfulRecords": [], "failedRecords": [{"requestIdentifier": records[0]["requestIdentifier"], "status": "FAILED", "errorCode": "400", "errorMessage": "too big"}]}
        out = []
        for r in records:
            assert isinstance(r["timestamp"], datetime) and r["namespaces"][0].startswith("/agent-studio/")
            rid = f"mr-{len(self.fake.records) + 1}-" + "x" * 40
            self.fake.records[rid] = r
            out.append({"memoryRecordId": rid, "status": "SUCCEEDED", "requestIdentifier": r["requestIdentifier"]})
        return {"successfulRecords": out, "failedRecords": []}

    def _retrieve_memory_records(self, memoryId, searchCriteria, namespace=None, maxResults=None):
        sums = [{"memoryRecordId": rid, "content": r["content"], "memoryStrategyId": "s", "namespaces": r["namespaces"], "createdAt": datetime.now(), "score": 0.9,
                 "metadata": r.get("metadata", {})} for rid, r in self.fake.records.items() if namespace in r["namespaces"]]
        sums.append({"memoryRecordId": "mr-native" + "x" * 40, "content": {"text": "extracted"}, "memoryStrategyId": "s", "namespaces": [namespace], "createdAt": datetime.now()})
        return {"memoryRecordSummaries": sums}

    def _delete_memory_record(self, memoryId, memoryRecordId):
        self.fake.records.pop(memoryRecordId, None)
        return {"memoryRecordId": memoryRecordId}


def agentcore(fake=None):
    fake = fake or FakeAgentCore()
    return AgentCoreMemory("us-east-1", MEMORY_ID, fake.clients), fake


def test_agentcore_roundtrip_with_requests_checked_against_the_real_service_model():
    a, f = agentcore()
    assert a.kind == "agentcore" and a.cloud is True and a.namespace("user", "local-user") == "/agent-studio/user/local-user"
    assert run(a.health()) == {"ok": True, "detail": f"AgentCore memory {MEMORY_ID} is ACTIVE"}
    rid = run(a.put(REC, None))
    assert rid == "mr-1-" + "x" * 40
    got = run(a.search("billing", None, "user", "local-user", 3))
    assert got[0] == ("m_abc", 0.9) and got[1] == ("mr-native" + "x" * 40, 0.0)                       # our id comes back from the metadata we stored
    run(a.delete({**REC, "remote_id": rid}))
    assert f.records == {}
    run(a.delete(REC))                                                                        # nothing remote to remove
    f.status = "CREATING"
    assert run(a.health())["ok"] is False


def test_agentcore_failures_are_explained():
    from botocore.exceptions import EndpointConnectionError, NoCredentialsError
    a, f = agentcore()
    f.fail_records = True
    with pytest.raises(MemoryError_, match="refused the memory: too big"):
        run(a.put(REC, None))
    f.fail_records = False
    for op, exc, want in (("batch_create_memory_records", aws_error("AccessDeniedException", "no"), "lack permission"),
                          ("retrieve_memory_records", aws_error("ResourceNotFoundException", "gone"), "not found in AWS"),
                          ("delete_memory_record", aws_error("ThrottlingException", "slow"), "AWS error ThrottlingException"),
                          ("get_memory", NoCredentialsError(), "no AWS credentials"),
                          ("retrieve_memory_records", EndpointConnectionError(endpoint_url="https://x"), "cannot reach AgentCore in us-east-1")):
        f.fail = {op: exc}
        call = {"batch_create_memory_records": lambda: a.put(REC, None), "retrieve_memory_records": lambda: a.search("q", None, "user", "x", 1),
                "delete_memory_record": lambda: a.delete({**REC, "remote_id": "r" * 40}), "get_memory": lambda: a._call("bedrock-agentcore-control", "get_memory", memoryId=MEMORY_ID)}[op]
        with pytest.raises(MemoryError_, match=want):
            run(call())
    f.fail = {}
    with pytest.raises(MemoryError_, match="AgentCore rejected the request"):
        run(a.delete({**REC, "remote_id": "too-short"}))                              # a request botocore itself would refuse is explained, not shown as "cannot reach"
    f.fail = {"get_memory": aws_error("AccessDeniedException", "no")}
    assert run(a.health())["ok"] is False
    for region, mid, msg in (("nowhere", MEMORY_ID, "region"), ("us-east-1", "bad id", "memory id looks wrong"), ("us-east-1", "", "memory id looks wrong")):
        with pytest.raises(MemoryError_, match=msg):
            AgentCoreMemory(region, mid, f.clients)


# ------------------------------------------------------------------------------------------------ Mem0
class FakeMem0:
    def __init__(self, key="k"):
        self.items, self.key, self.transport = {}, key, httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        if req.headers.get("x-api-key") != self.key:
            return httpx.Response(401, json={"detail": "Authentication required"})
        body = json.loads(req.content) if req.content else None
        if req.method == "POST" and req.url.path == "/memories":
            assert body["infer"] is False and body["messages"][0]["role"] == "user" and body["metadata"]["memory_id"]
            if body["messages"][0]["content"] == "EMPTY":
                return httpx.Response(200, json={"results": []})
            mid = f"mem-{len(self.items) + 1}"
            self.items[mid] = {"id": mid, "memory": body["messages"][0]["content"], "user_id": body["user_id"], "metadata": body["metadata"]}
            return httpx.Response(200, json={"results": [{"id": mid, "memory": body["messages"][0]["content"], "event": "ADD"}]})
        if req.method == "POST" and req.url.path == "/search":
            uid = body["filters"]["user_id"]
            return httpx.Response(200, json={"results": [{**m, "score": 0.77} for m in self.items.values() if m["user_id"] == uid] + [{"id": "plain", "memory": "x", "score": None}]})
        if req.method == "DELETE":
            self.items.pop(req.url.path.rsplit("/", 1)[1], None)
            return httpx.Response(200, json={"message": "Memory deleted successfully"})
        if req.method == "GET" and req.url.path == "/memories":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(500, text="unhandled")


def test_mem0_roundtrip_and_errors():
    f = FakeMem0()
    m = Mem0("http://127.0.0.1:8888/", "k", f.transport)
    assert m.kind == "mem0" and m.cloud is False and run(m.health())["ok"]
    rid = run(m.put(REC, None))
    assert rid == "mem-1" and f.items[rid]["user_id"] == "agent-studio-user-local-user"
    assert run(m.search("billing", None, "user", "local-user", 3)) == [("m_abc", 0.77), ("plain", 0.0)]
    run(m.delete({**REC, "remote_id": rid}))
    assert f.items == {}
    run(m.delete(REC))
    with pytest.raises(MemoryError_, match="did not store"):
        run(m.put({**REC, "text": "EMPTY"}, None))
    bad = Mem0("http://127.0.0.1:8888", "wrong", f.transport)
    with pytest.raises(MemoryError_, match="rejected the API key"):
        run(bad.put(REC, None))
    assert run(bad.health())["ok"] is False

    def server_error(request):
        return httpx.Response(500, text="boom")

    def down(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(MemoryError_, match="Mem0 error 500"):
        run(Mem0("http://x", "", httpx.MockTransport(server_error)).search("q", None, "user", "x", 1))
    with pytest.raises(MemoryError_, match="cannot reach the Mem0 server"):
        run(Mem0("http://x", "", httpx.MockTransport(down)).put(REC, None))
    assert run(Mem0("http://x", "", httpx.MockTransport(lambda r: httpx.Response(200, content=b""))).search("q", None, "user", "x", 1)) == []
    for url in ("", "ftp://x", "localhost:8888"):
        with pytest.raises(MemoryError_, match="must start with http"):
            Mem0(url)


# ------------------------------------------------------------------------------------------------ the factory builds each backend
def test_factory_builds_every_kind(state, monkeypatch, tmp_path):
    assert set(FACTORY.kinds) == {"local", "vector", "vertex", "agentcore", "mem0"}
    assert FACTORY.create("local", {}, {}, {}) is None
    m0 = FACTORY.create("mem0", {"url": "http://127.0.0.1:8888"}, {"api_key": "k"}, {"transport": FakeMem0().transport})
    assert isinstance(m0, Mem0) and m0._headers == {"X-API-Key": "k"} and isinstance(FACTORY.create("mem0", {"url": "http://x"}, {}, {}), Mem0)
    import boto3

    class Sess:
        def __init__(self, **kw): self.kw = kw
        def client(self, name): return name
    monkeypatch.setattr(boto3, "Session", Sess)
    ac = FACTORY.create("agentcore", {"region": "us-east-1", "memory_id": MEMORY_ID}, {"access_key_id": "AKIA1", "secret_access_key": "s"}, {})
    assert isinstance(ac, AgentCoreMemory) and ac._clients("bedrock-agentcore") == "bedrock-agentcore"
    with pytest.raises(MemoryError_, match="Region is required|AWS region is required"):
        FACTORY.create("agentcore", {"memory_id": MEMORY_ID}, {}, {})
    import app.memory.remote as R
    monkeypatch.setattr(R, "google_token_provider", lambda sa: (lambda: "tok"))
    vx = FACTORY.create("vertex", {"bank": BANK}, {"service_account_json": "{}"}, {"transport": FakeVertex().transport})
    assert isinstance(vx, VertexMemoryBank) and vx._token() == "tok"
    q = FakeQdrant()

    class Rag:
        def _store(self, sid):
            if sid != "q":
                raise RuntimeError("unknown store")
            return QdrantStore("http://q", None, transport=q.transport)
    assert isinstance(FACTORY.create("vector", {"store_id": "q"}, {}, {"rag": Rag()}), VectorMirror)
    with pytest.raises(MemoryError_, match="vector store 'nope': unknown store"):
        FACTORY.create("vector", {"store_id": "nope"}, {}, {"rag": Rag()})
    with pytest.raises(MemoryError_, match="not available"):
        FACTORY.create("vector", {"store_id": "q"}, {}, {})
    assert all(k.verified or k.id in ("local", "vector") for k in FACTORY.kinds.values())          # every external API says where it was checked


# ------------------------------------------------------------------------------------------------ the service with a second home
class FakeRemote(RemoteIndex):
    kind, cloud = "fake", True

    def __init__(self):
        self.store, self.fail_put, self.fail_search, self.fail_delete, self.puts = {}, False, False, False, 0

    async def health(self): return {"ok": True, "detail": "fake"}

    async def put(self, rec, vector):
        self.puts += 1
        if self.fail_put:
            raise MemoryError_("service unavailable")
        self.store[rec["id"]] = rec["text"]
        return "r-" + rec["id"]

    async def search(self, query, vector, scope, scope_id, k):
        if self.fail_search:
            raise MemoryError_("search unavailable")
        words = set(re.findall(r"[a-z]+", query.lower()))
        return [(("r-" + i) if i.startswith("m_") else i, 0.5) for i, t in self.store.items() if words & set(re.findall(r"[a-z]+", t.lower()))] + [("ghost", 0.4)]

    async def delete(self, rec):
        if self.fail_delete:
            raise MemoryError_("cannot delete")
        self.store.pop(rec["id"], None)


@pytest.fixture
def rsvc(state):
    remote = FakeRemote()
    f = MemoryFactory()
    f.register(Kind("local", "Local", "", False))
    f.register(Kind("fake", "Fake cloud", "", True, (), lambda c, s, x: remote))
    s = MemoryService(state.db, state.providers, state.guard, factory=f)
    s.embedder = FakeEmbedder()
    s.set_config({"backend": "fake"})
    s.remote_obj = remote
    return s


def add(svc, text, scope="user", sid=M.USER_ID, **kw):
    return run(svc.add(type="semantic", text=text, scope=scope, scope_id=sid, **kw))


def test_an_approved_memory_is_copied_to_the_backend_and_removed_with_it(rsvc):
    r = rsvc.remote_obj
    a = add(rsvc, "The billing system is Xero")
    assert rsvc.get(a["id"])["remote_id"] == "r-" + a["id"] and r.store[a["id"]] == "The billing system is Xero"
    p = add(rsvc, "Pending billing fact", status="pending")
    assert p["id"] not in r.store                                              # nothing waits in the cloud before it is approved
    run(rsvc.approve(p["id"]))
    assert p["id"] in r.store
    run(rsvc.update(p["id"], text="Edited billing fact"))
    assert r.store[p["id"]] == "Edited billing fact"
    old = add(rsvc, "The user's timezone is Paris", key="tz")
    new = add(rsvc, "The user's timezone is Tokyo", key="tz")
    assert new["superseded"] == [old["id"]] and old["id"] not in r.store
    assert run(rsvc.forget(memory_id=a["id"]))["forgotten"] and a["id"] not in r.store
    run(rsvc.wipe())
    assert r.store == {}


def test_remote_failures_never_lose_the_memory_and_are_reported(rsvc):
    r = rsvc.remote_obj
    r.fail_put = True
    a = add(rsvc, "A fact worth keeping")
    assert rsvc.get(a["id"])["remote_id"] == "" and "saved on this PC; the copy in fake failed: service unavailable" in a["warnings"][0]
    r.fail_put = False
    b = add(rsvc, "Another fact")
    r.fail_delete = True
    w: list[str] = []
    assert run(rsvc.delete(b["id"], w)) and "could not be removed: cannot delete" in w[0]
    assert run(rsvc.delete(add(rsvc, "third fact")["id"])) is True                   # no warnings list: still removed here
    r.fail_search = True
    warns: list[str] = []
    assert run(rsvc.search("keeping", scopes=[("user", M.USER_ID)], warnings=warns))[0]["id"] == a["id"] and "only this PC was searched" in warns[0]
    assert run(rsvc.search("keeping", scopes=[("user", M.USER_ID)]))[0]["id"] == a["id"]


def test_search_merges_the_backend_results_and_ignores_what_does_not_belong(rsvc):
    r = rsvc.remote_obj
    a = add(rsvc, "The invoice archive lives in Dropbox")
    other = add(rsvc, "The invoice archive of another scope", sid="other-user")
    hidden = add(rsvc, "Invoice draft", status="pending")
    expired = add(rsvc, "Old invoice rule", ttl_days=1)
    rsvc.local.update(expired["id"], expires=time.time() - 1)
    r.store[hidden["id"]] = "Invoice draft"                                           # present in the cloud but not approved here
    r.store[expired["id"]] = "Old invoice rule"
    found = run(rsvc.search("zzz yyy invoice", scopes=[("user", M.USER_ID)], min_score=0))
    assert [x["id"] for x in found] == [a["id"]] and other["id"] not in [x["id"] for x in found]
    found = run(rsvc.search("dropbox", scopes=[("user", M.USER_ID)]))
    assert found[0]["id"] == a["id"] and found[0]["score"] > 0
    only_remote = add(rsvc, "Completely different wording")
    rsvc.local.db.execute("UPDATE memories SET text='zzz' WHERE id=?", (only_remote["id"],))
    r.store[only_remote["id"]] = "wording"
    assert only_remote["id"] in [x["id"] for x in run(rsvc.search("wording", scopes=[("user", M.USER_ID)]))]          # found only by the backend


def test_a_backend_that_cannot_be_built_is_reported_not_crashed(rsvc):
    rsvc.db.set_setting("memory_config", json.dumps({"backend": "mem0", "backend_config": {}}))
    rsvc.factory.register(Kind("mem0", "Mem0", "", False, (("url", "Server address", False, True, ""),), lambda c, s, x: None))
    rsvc._remote_sig = None
    w: list[str] = []
    assert run(rsvc.search("anything", scopes=[("user", M.USER_ID)], warnings=w)) == [] and "Server address is required" in w[0]
    assert run(rsvc.search("anything", scopes=[("user", M.USER_ID)])) == []
    saved = add(rsvc, "can we still save?")                                           # saving still works: it stays on this PC, with a warning
    assert saved["status"] == "active" and "Server address is required" in saved["warnings"][0]
