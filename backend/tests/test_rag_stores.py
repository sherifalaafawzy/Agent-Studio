"""Vector store adapters (local file, Qdrant) and the registry that builds them."""
import asyncio

import httpx
import pytest

from app.rag.stores.base import Point, StoreError
from app.rag.stores.local import LocalStore
from app.rag.stores.qdrant import UPSERT_BATCH, QdrantStore
from app.rag.stores import registry as R
from tests.rag_fakes import DIM, FakeQdrant, vec_for

run = asyncio.run


def pt(pid, text, doc="d1", **extra):
    return Point(pid, vec_for(text), {"doc_id": doc, "text": text, **extra})


DOCS = [pt("a", "red apple pie recipe"), pt("b", "blue ocean waves"), pt("c", "green apple orchard", doc="d2"), pt("d", "quantum physics lecture", doc="d2")]


# -------------------------------------------------------------------- local
@pytest.fixture
def local(tmp_path):
    return LocalStore(tmp_path / "rag" / "t.sqlite")


def test_local_health_counts_collections(local):
    assert run(local.health()) == {"ok": True, "detail": "local file (0 collections)"}
    run(local.ensure("c", DIM))
    assert run(local.health())["detail"] == "local file (1 collection)"


def test_local_ensure_is_idempotent_but_rejects_a_new_dimension(local):
    run(local.ensure("c", DIM))
    run(local.ensure("c", DIM))
    with pytest.raises(StoreError, match=f"dimension {DIM}, not 8"):
        run(local.ensure("c", 8))


def test_local_search_ranks_by_similarity(local):
    run(local.ensure("c", DIM))
    run(local.upsert("c", DOCS))
    hits = run(local.search("c", vec_for("apple recipe"), 3))
    assert [h.id for h in hits][0] == "a" and len(hits) == 3
    assert hits[0].score > hits[1].score >= hits[2].score and hits[0].score <= 1.0001
    assert hits[0].payload["text"] == "red apple pie recipe"
    assert len(run(local.search("c", vec_for("apple"), 1))) == 1


def test_local_upsert_replaces_by_id(local):
    run(local.ensure("c", DIM))
    run(local.upsert("c", [pt("a", "first version")]))
    run(local.upsert("c", [pt("a", "second version")]))
    assert run(local.count("c")) == 1
    assert run(local.search("c", vec_for("second"), 1))[0].payload["text"] == "second version"


def test_local_refuses_missing_collections_and_wrong_dimensions(local):
    with pytest.raises(StoreError, match="does not exist"):
        run(local.upsert("nope", DOCS))
    run(local.ensure("c", DIM))
    with pytest.raises(StoreError, match="expects 32"):
        run(local.upsert("c", [Point("x", [1.0, 2.0], {})]))
    with pytest.raises(StoreError, match="expects 32"):
        run(local.search("c", [1.0, 2.0], 3))
    assert run(local.search("nope", vec_for("x"), 3)) == []


def test_local_delete_doc_removes_only_that_document(local):
    run(local.ensure("c", DIM))
    run(local.upsert("c", DOCS))
    assert run(local.delete_doc("c", "d2")) == 2 and run(local.count("c")) == 2
    assert run(local.delete_doc("c", "d2")) == 0


def test_local_drop_and_recreate_with_a_new_dimension(local):
    run(local.ensure("c", DIM))
    run(local.upsert("c", DOCS))
    run(local.drop("c"))
    run(local.drop("c"))                                              # dropping twice is fine
    assert run(local.count("c")) == 0
    run(local.ensure("c", 8))


def test_local_data_survives_reopening_the_file(tmp_path):
    path = tmp_path / "keep.sqlite"
    first = LocalStore(path)
    run(first.ensure("c", DIM))
    run(first.upsert("c", DOCS))
    second = LocalStore(path)
    assert run(second.count("c")) == 4 and run(second.search("c", vec_for("ocean"), 1))[0].id == "b"


def test_zero_vectors_do_not_crash(local):
    run(local.ensure("c", 4))
    run(local.upsert("c", [Point("z", [0.0] * 4, {"doc_id": "z"})]))
    assert run(local.search("c", [0.0] * 4, 1))[0].score == 0


# ------------------------------------------------------------------- qdrant
def qstore(fake=None, **kw):
    fake = fake or FakeQdrant()
    return QdrantStore("http://qdrant.test:6333/", transport=fake.transport, **kw), fake


def test_qdrant_rejects_a_url_without_a_scheme():
    with pytest.raises(StoreError, match="http://"):
        QdrantStore("localhost:6333")
    assert QdrantStore("http://x:1/").url == "http://x:1"


def test_qdrant_health_reports_version_and_collections():
    store, _ = qstore()
    assert run(store.health()) == {"ok": True, "detail": "Qdrant 1.20.0 (0 collections)"}
    run(store.ensure("c", DIM))
    assert run(store.health())["detail"] == "Qdrant 1.20.0 (1 collection)"


def test_qdrant_health_reports_instead_of_raising_when_unreachable():
    def refuse(req):
        raise httpx.ConnectError("refused")
    store = QdrantStore("http://down.test", transport=httpx.MockTransport(refuse))
    h = run(store.health())
    assert h["ok"] is False and "cannot reach Qdrant at http://down.test" in h["detail"]


def test_qdrant_api_key_is_sent_and_checked():
    fake = FakeQdrant(api_key="secret")
    assert run(QdrantStore("http://q.test", "secret", transport=fake.transport).health())["ok"] is True
    bad = run(QdrantStore("http://q.test", "wrong", transport=fake.transport).health())
    assert bad == {"ok": False, "detail": "Qdrant rejected the API key"}


def test_qdrant_ensure_creates_once_and_checks_the_dimension():
    store, fake = qstore()
    run(store.ensure("c", DIM))
    assert fake.cols["c"]["dim"] == DIM
    assert ("PUT", "/collections/c/index") in fake.requests                     # doc_id filter is indexed
    fake.requests.clear()
    run(store.ensure("c", DIM))
    assert ("PUT", "/collections/c") not in fake.requests
    with pytest.raises(StoreError, match=f"dimension {DIM}, not 8"):
        run(store.ensure("c", 8))


def test_qdrant_upsert_search_count_and_delete():
    store, fake = qstore()
    run(store.ensure("c", DIM))
    run(store.upsert("c", DOCS))
    assert run(store.count("c")) == 4
    hits = run(store.search("c", vec_for("apple recipe"), 2))
    assert hits[0].id == "a" and len(hits) == 2 and hits[0].payload["doc_id"] == "d1"
    assert run(store.delete_doc("c", "d2")) == 2 and run(store.count("c")) == 2


def test_qdrant_upsert_is_sent_in_batches():
    store, fake = qstore()
    run(store.ensure("c", DIM))
    run(store.upsert("c", [pt(f"p{i}", f"text {i}") for i in range(UPSERT_BATCH + 2)]))
    assert fake.requests.count(("PUT", "/collections/c/points")) == 2
    assert run(store.count("c")) == UPSERT_BATCH + 2


def test_qdrant_missing_collections_are_empty_not_errors():
    store, _ = qstore()
    assert run(store.search("nope", vec_for("x"), 3)) == []
    assert run(store.count("nope")) == 0
    assert run(store.delete_doc("nope", "d")) == 0
    run(store.drop("nope"))


def test_qdrant_drop_removes_the_collection():
    store, fake = qstore()
    run(store.ensure("c", DIM))
    run(store.drop("c"))
    assert "c" not in fake.cols


def test_qdrant_server_errors_become_readable_store_errors():
    store, _ = qstore()
    run(store.ensure("c", DIM))
    with pytest.raises(StoreError, match="Qdrant error 400: Wrong input: Vector dimension error"):
        run(store.upsert("c", [Point("x", [1.0, 2.0], {})]))

    def plain(req):
        return httpx.Response(500, text="kaboom")
    broken = QdrantStore("http://q.test", transport=httpx.MockTransport(plain))
    with pytest.raises(StoreError, match="Qdrant error 500: kaboom"):
        run(broken.drop("c"))


# ----------------------------------------------------------------- registry
def test_unknown_kinds_and_missing_fields_are_explained():
    with pytest.raises(StoreError, match="unknown store kind 'chroma9'"):
        R.kind_info("chroma9")
    with pytest.raises(StoreError, match="URL is required for a Qdrant store"):
        R.check_fields("qdrant", {}, {})
    R.check_fields("qdrant", {"url": "http://x"}, {})                         # the API key is optional
    R.check_fields("local", {}, {})


def test_make_store_builds_each_kind(tmp_path):
    default = R.make_store("local", {}, {}, tmp_path)
    assert isinstance(default, LocalStore) and default.path == tmp_path / "rag" / "local.sqlite"
    assert R.make_store("local", {"file": "notes.sqlite"}, {}, tmp_path).path == tmp_path / "rag" / "notes.sqlite"
    q = R.make_store("qdrant", {"url": "https://q.example.com"}, {"api_key": "k"}, tmp_path)
    assert isinstance(q, QdrantStore) and q._headers == {"api-key": "k"}
    assert R.make_store("qdrant", {"url": "http://x"}, {}, tmp_path)._headers == {}


@pytest.mark.parametrize("name", ["../evil.sqlite", "a/b.sqlite", "notes.db", ".sqlite", "x" * 60 + ".sqlite"])
def test_local_file_names_cannot_escape_the_data_folder(tmp_path, name):
    with pytest.raises(StoreError, match="file name"):
        R.make_store("local", {"file": name}, {}, tmp_path)


def test_kinds_describe_their_settings_for_the_ui():
    assert R.KINDS["qdrant"].docker_stack == "qdrant" and R.KINDS["local"].docker_stack is None
    assert [f.name for f in R.KINDS["qdrant"].fields] == ["url", "api_key"]
    assert next(f for f in R.KINDS["qdrant"].fields if f.name == "api_key").secret
