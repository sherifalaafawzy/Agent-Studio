"""ChromaStore and PineconeStore against in-memory servers: behaviour, auth, dimension checks, 404s, error mapping."""
import asyncio

import httpx
import pytest

from app.rag.stores import registry as R
from app.rag.stores.base import Point, StoreError
from app.rag.stores.chroma import UPSERT_BATCH as CHROMA_BATCH, ChromaStore
from app.rag.stores.pinecone import API_VERSION, UPSERT_BATCH as PC_BATCH, PineconeStore
from tests.rag_fakes import DIM, vec_for
from tests.rag_fakes_stores import FakeChroma, FakePinecone

run = asyncio.run


def pt(pid, text, doc="d1"):
    return Point(pid, vec_for(text), {"doc_id": doc, "source": "src", "chunk": 0, "text": text})


DOCS = [pt("a", "red apple pie recipe"), pt("b", "blue ocean waves"), pt("c", "green apple orchard", doc="d2"), pt("d", "quantum physics", doc="d2")]


def refuse(req):
    raise httpx.ConnectError("refused")


# ------------------------------------------------------------------------------------------------------------- Chroma
def chroma(fake=None, **kw):
    fake = fake or FakeChroma()
    return ChromaStore("http://chroma.test:8000/", transport=fake.transport, **kw), fake


def test_chroma_url_tenant_and_database_are_validated():
    with pytest.raises(StoreError, match="http://"):
        ChromaStore("localhost:8000")
    for bad in ("a/b", "x y", "", "../z"):
        with pytest.raises(StoreError, match="tenant and database"):
            ChromaStore("http://x", tenant=bad)
        with pytest.raises(StoreError, match="tenant and database"):
            ChromaStore("http://x", database=bad)
    assert ChromaStore("http://x:1/").url == "http://x:1"


def test_chroma_collection_names_cannot_inject_path_segments():
    s, fake = chroma()
    for name in ("../x", "a/b", "", "a b"):
        for call in (s.ensure(name, DIM), s.drop(name), s.search(name, vec_for("x"), 1)):
            with pytest.raises(StoreError, match="collection name may only"):
                run(call)
    assert fake.requests == []


def test_chroma_health_reports_version_and_collections_or_the_problem():
    s, _ = chroma()
    assert run(s.health()) == {"ok": True, "detail": "Chroma 1.5.9 (0 collections)"}
    run(s.ensure("kb_x", DIM))
    assert run(s.health())["detail"] == "Chroma 1.5.9 (1 collection)"
    down = ChromaStore("http://down.test", transport=httpx.MockTransport(refuse))
    h = run(down.health())
    assert h["ok"] is False and "cannot reach Chroma at http://down.test" in h["detail"]
    wrong_db, _ = chroma(database="other")
    assert "Chroma error 404" in run(wrong_db.health())["detail"]


def test_chroma_token_is_sent_and_checked():
    fake = FakeChroma(token="tok")
    assert run(ChromaStore("http://c.test", "tok", transport=fake.transport).health())["ok"] is True
    assert run(ChromaStore("http://c.test", "bad", transport=fake.transport).health()) == {"ok": False, "detail": "Chroma rejected the token"}


def test_chroma_ensure_creates_with_cosine_and_checks_space_and_dimension():
    s, fake = chroma()
    run(s.ensure("kb_x", DIM))
    run(s.ensure("kb_x", DIM))
    assert fake.cols["kb_x"]["space"] == "cosine"
    run(s.upsert("kb_x", DOCS[:1]))                                           # the first insert fixes the dimension
    with pytest.raises(StoreError, match=f"dimension {DIM}, not 8"):
        run(s.ensure("kb_x", 8))
    fake.cols["kb_x"]["space"] = "l2"
    with pytest.raises(StoreError, match="distance 'l2', not cosine"):
        run(s.ensure("kb_x", DIM))


def test_chroma_upsert_search_count_delete_drop():
    s, fake = chroma()
    run(s.ensure("kb_x", DIM))
    run(s.upsert("kb_x", DOCS))
    assert run(s.count("kb_x")) == 4
    hits = run(s.search("kb_x", vec_for("apple recipe"), 2))
    assert hits[0].id == "a" and len(hits) == 2 and hits[0].score > hits[1].score and hits[0].score <= 1.0001
    assert hits[0].payload == {"doc_id": "d1", "source": "src", "chunk": 0, "text": "red apple pie recipe"}
    assert run(s.delete_doc("kb_x", "d2")) == 2 and run(s.count("kb_x")) == 2 and run(s.delete_doc("kb_x", "d2")) == 0
    run(s.drop("kb_x"))
    assert "kb_x" not in fake.cols


def test_chroma_resolves_the_collection_id_by_name_after_reconnecting():
    s, fake = chroma()
    run(s.ensure("kb_x", DIM))
    run(s.upsert("kb_x", DOCS))
    other, _ = chroma(fake)                                                   # a fresh adapter has no cached id
    assert run(other.count("kb_x")) == 4


def test_chroma_upsert_is_sent_in_batches():
    s, fake = chroma()
    run(s.ensure("kb_x", DIM))
    run(s.upsert("kb_x", [pt(f"p{i}", f"t{i}") for i in range(CHROMA_BATCH + 1)]))
    assert len([r for r in fake.requests if r[1].endswith("/upsert")]) == 2


def test_chroma_missing_collections_are_empty_and_writes_say_so():
    s, _ = chroma()
    assert run(s.search("kb_x", vec_for("x"), 3)) == [] and run(s.count("kb_x")) == 0 and run(s.delete_doc("kb_x", "d")) == 0
    run(s.drop("kb_x"))
    with pytest.raises(StoreError, match="does not exist"):
        run(s.upsert("kb_x", DOCS))


def test_chroma_errors_are_readable():
    s, _ = chroma()
    run(s.ensure("kb_x", DIM))
    run(s.upsert("kb_x", DOCS[:1]))
    with pytest.raises(StoreError, match="Chroma error 400: Collection expecting embedding with dimension of 32, got 2"):
        run(s.upsert("kb_x", [Point("z", [1.0, 2.0], {"doc_id": "d"})]))
    broken = ChromaStore("http://c.test", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="kaboom")))
    with pytest.raises(StoreError, match="Chroma error 500: kaboom"):
        run(broken.drop("kb_x"))
    odd = ChromaStore("http://c.test", transport=httpx.MockTransport(lambda r: httpx.Response(502, json=["bad", "gateway"])))
    with pytest.raises(StoreError, match="Chroma error 502"):
        run(odd.drop("kb_x"))


def test_registry_builds_chroma(tmp_path):
    c = R.make_store("chroma", {"url": "http://c:8000"}, {"token": "t"}, tmp_path)
    assert isinstance(c, ChromaStore) and c._headers == {"Authorization": "Bearer t"} and "default_tenant/databases/default_database" in c._base
    d = R.make_store("chroma", {"url": "http://c", "tenant": "acme", "database": "prod"}, {}, tmp_path)
    assert d._headers == {} and "acme/databases/prod" in d._base
    with pytest.raises(StoreError, match="URL is required"):
        R.check_fields("chroma", {}, {})


# ---------------------------------------------------------------------------------------------------------- Pinecone
def pinecone(fake=None, host=FakePinecone.HOST, name="", **kw):
    fake = fake or FakePinecone()
    return PineconeStore("pc-key", host, name, transport=fake.transport, **kw), fake


def test_pinecone_needs_a_key_and_an_index_and_a_sane_name():
    with pytest.raises(StoreError, match="API key is required"):
        PineconeStore("", "h")
    with pytest.raises(StoreError, match="index host.*or the index name"):
        PineconeStore("k")
    with pytest.raises(StoreError, match="lowercase letters"):
        PineconeStore("k", index_name="Bad Name")
    assert PineconeStore("k", "http://localhost:5080")._host == "http://localhost:5080"
    assert PineconeStore("k", "h.example.com/")._host == "https://h.example.com"


def test_pinecone_namespaces_are_validated():
    s, fake = pinecone()
    for call in (s.ensure("a b", DIM), s.search("../x", vec_for("x"), 1), s.drop(""), s.count("a/b"), s.delete_doc("x y", "d"), s.upsert("a b", DOCS)):
        with pytest.raises(StoreError, match="namespace may only contain"):
            run(call)
    assert fake.requests == []


def test_pinecone_health_ensure_and_dimension_check():
    s, fake = pinecone()
    assert run(s.health()) == {"ok": True, "detail": f"Pinecone index: {DIM} dimensions, 0 vectors, 0 namespaces"}
    run(s.ensure("kb_x", DIM))
    run(s.upsert("kb_x", DOCS))
    assert run(s.health())["detail"].endswith("4 vectors, 1 namespace")
    with pytest.raises(StoreError, match=f"index has dimension {DIM}, but this embedding model produces 8"):
        run(s.ensure("kb_x", 8))
    assert ("POST", FakePinecone.HOST, "/describe_index_stats") in fake.requests


def test_pinecone_sends_the_key_and_api_version_and_maps_auth_errors():
    s, fake = pinecone()
    seen = {}

    def spy(req):
        seen.update(req.headers)
        return fake.handle(req)
    spied = PineconeStore("pc-key", FakePinecone.HOST, transport=httpx.MockTransport(spy))
    run(spied.health())
    assert seen["api-key"] == "pc-key" and seen["x-pinecone-api-version"] == API_VERSION
    bad = PineconeStore("wrong", FakePinecone.HOST, transport=fake.transport)
    assert run(bad.health()) == {"ok": False, "detail": "Pinecone rejected the API key"}


def test_pinecone_unreachable_is_reported():
    down = PineconeStore("k", "h.example.com", transport=httpx.MockTransport(refuse))
    h = run(down.health())
    assert h["ok"] is False and "cannot reach Pinecone at https://h.example.com" in h["detail"]


def test_pinecone_upsert_search_count_delete_and_drop_use_one_namespace_per_kb():
    s, fake = pinecone()
    run(s.ensure("kb_x", DIM))
    run(s.upsert("kb_x", DOCS))
    run(s.upsert("kb_y", [pt("z", "other knowledge base")]))
    assert run(s.count("kb_x")) == 4 and run(s.count("kb_y")) == 1 and run(s.count("kb_none")) == 0
    assert sorted(fake.ns["kb_x"])[0] == "d1#a"                                   # id = doc#chunk, so a document can be listed by prefix
    hits = run(s.search("kb_x", vec_for("apple recipe"), 2))
    assert hits[0].id == "d1#a" and len(hits) == 2 and hits[0].payload["text"] == "red apple pie recipe"
    assert run(s.delete_doc("kb_x", "d2")) == 2 and run(s.count("kb_x")) == 2           # two pages of the prefix listing
    assert run(s.delete_doc("kb_x", "d2")) == 0 and run(s.delete_doc("kb_none", "d")) == 0
    run(s.drop("kb_x"))
    run(s.drop("kb_x"))                                                           # a missing namespace is fine
    assert "kb_x" not in fake.ns and "kb_y" in fake.ns


def test_pinecone_delete_pages_through_a_long_listing():
    s, fake = pinecone()
    run(s.upsert("kb_x", [pt(f"p{i}", f"t{i}", doc="big") for i in range(5)]))
    assert run(s.delete_doc("kb_x", "big")) == 5 and fake.ns["kb_x"] == {}


def test_pinecone_upsert_is_sent_in_batches_and_a_wrong_size_is_an_error():
    s, fake = pinecone()
    run(s.upsert("kb_x", [pt(f"p{i}", f"t{i}") for i in range(PC_BATCH + 1)]))
    assert len([r for r in fake.requests if r[2] == "/vectors/upsert"]) == 2
    with pytest.raises(StoreError, match="Pinecone error 400: Vector dimension 2 does not match"):
        run(s.upsert("kb_x", [Point("z", [1.0, 2.0], {"doc_id": "d"})]))


def test_pinecone_resolves_the_host_from_the_index_name_and_checks_the_metric():
    s, fake = pinecone(host="", name="my-index")
    run(s.ensure("kb_x", DIM))
    run(s.upsert("kb_x", DOCS[:1]))
    assert [r for r in fake.requests if r[1] == "api.pinecone.io"] == [("GET", "api.pinecone.io", "/indexes/my-index")]   # looked up once
    dot = FakePinecone(metric="dotproduct")
    s2, _ = pinecone(dot, host="", name="my-index")
    with pytest.raises(StoreError, match="'dotproduct' metric.*cosine"):
        run(s2.ensure("kb_x", DIM))
    assert "cosine" in run(s2.health())["detail"]


def test_pinecone_unknown_index_name_says_how_to_fix_it():
    s, _ = pinecone(host="", name="ghost-index")
    with pytest.raises(StoreError, match="'ghost-index' does not exist: create it in the Pinecone console"):
        run(s.ensure("kb_x", DIM))


def test_pinecone_plain_text_errors_are_shown():
    broken = PineconeStore("k", "h.example.com", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="kaboom")))
    with pytest.raises(StoreError, match="Pinecone error 500: kaboom"):
        run(broken.upsert("kb_x", DOCS))
    odd = PineconeStore("k", "h.example.com", transport=httpx.MockTransport(lambda r: httpx.Response(503, json=["x"])))
    with pytest.raises(StoreError, match="Pinecone error 503"):
        run(odd.upsert("kb_x", DOCS))
    empty = PineconeStore("k", "h.example.com", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    run(empty.upsert("kb_x", DOCS))                                           # an empty success body is fine


def test_registry_builds_and_validates_pinecone(tmp_path):
    p = R.make_store("pinecone", {"index_host": "h.example.com"}, {"api_key": "k"}, tmp_path)
    assert isinstance(p, PineconeStore) and p._headers["Api-Key"] == "k"
    assert R.make_store("pinecone", {"index_name": "my-index"}, {"api_key": "k"}, tmp_path)._name == "my-index"
    with pytest.raises(StoreError, match="API key is required"):
        R.check_fields("pinecone", {"index_host": "h"}, {})
    with pytest.raises(StoreError, match="index host or the index name"):
        R.check_fields("pinecone", {}, {"api_key": "k"})
