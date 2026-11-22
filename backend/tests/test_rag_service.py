"""RagService: stores, knowledge bases, documents and search."""
import asyncio

import httpx
import pytest

from app.rag import service as SV
from app.rag.stores.base import StoreError
from app.rag.stores.qdrant import QdrantStore
from app.rag.stores.registry import make_store
from tests.rag_fakes import FakeEmbedder, FakeQdrant, vec_for

run = asyncio.run


def factory_with(fake):
    def f(kind, config, secrets, home):
        if kind == "qdrant":
            return QdrantStore(config["url"], secrets.get("api_key"), transport=fake.transport)
        return make_store(kind, config, secrets, home)
    return f


@pytest.fixture
def emb():
    return FakeEmbedder()


@pytest.fixture
def svc(state, tmp_path, emb):
    return SV.RagService(state.db, state.providers, state.guard, home=tmp_path / "home", embedder=emb)


def kb(svc, kb_id="docs", store="local", **kw):
    return run(svc.create_kb(kb_id, kw.pop("label", "Docs"), store, kw.pop("provider", "mock"), kw.pop("model", "embed-a"), **kw))


# -------------------------------------------------------------------- stores
def test_the_local_store_always_exists_and_setup_is_repeatable(state, svc, tmp_path):
    assert [s["id"] for s in svc.list_stores()] == ["local"] and svc.get_store("local")["builtin"]
    SV.RagService(state.db, state.providers, state.guard, home=tmp_path / "home", embedder=FakeEmbedder())
    assert len(svc.list_stores()) == 1
    assert SV.RagService(state.db, state.providers, state.guard).home                # default home comes from the data dir


def test_kinds_describe_what_each_store_needs(svc):
    kinds = {k["id"]: k for k in svc.kinds()}
    assert set(kinds) >= {"local", "qdrant"} and kinds["qdrant"]["docker_stack"] == "qdrant"
    assert {f["name"] for f in kinds["qdrant"]["fields"]} == {"url", "api_key"}


def test_adding_a_store_hides_its_secrets(svc):
    s = svc.add_store("cloud-q", "  Qdrant Cloud ", "qdrant", {"url": "https://q.example.com"}, {"api_key": "topsecret"})
    assert s["label"] == "Qdrant Cloud" and s["secrets_set"] == {"api_key": True} and s["config"] == {"url": "https://q.example.com"}
    assert "topsecret" not in str(svc.list_stores()) and s["kbs"] == 0 and not s["builtin"]
    assert svc.add_store("plain", "", "local", {"file": "x.sqlite"})["label"] == "plain"


@pytest.mark.parametrize("args,msg", [
    (("Bad Id", "x", "local"), "store id must be"),
    (("local", "x", "local"), "already exists"),
    (("nn", "x", "pinecone9"), "unknown store kind"),
    (("nn", "x", "qdrant", {}), "URL is required"),
    (("nn", "x", "local", {"file": "../x.sqlite"}), "file name"),
])
def test_bad_stores_are_refused_with_a_reason(svc, args, msg):
    with pytest.raises(SV.RagError, match=msg):
        svc.add_store(*args)


def test_updating_a_store_keeps_secrets_unless_told_otherwise(svc):
    svc.add_store("qd", "Q", "qdrant", {"url": "http://a.test"}, {"api_key": "k1"})
    assert svc.update_store("qd", label="Renamed")["secrets_set"] == {"api_key": True}
    assert svc.update_store("qd", config={"url": "http://b.test"})["config"]["url"] == "http://b.test"
    assert svc.update_store("qd", secrets={"api_key": ""})["secrets_set"] == {"api_key": False}
    assert svc.update_store("qd", secrets={"api_key": "k2"})["secrets_set"] == {"api_key": True}
    with pytest.raises(SV.RagError, match="URL is required"):
        svc.update_store("qd", config={"url": ""})
    with pytest.raises(SV.NotFound):
        svc.update_store("nope", label="x")


def test_updating_a_store_rebuilds_its_connection(svc):
    svc.add_store("qd", "Q", "qdrant", {"url": "http://a.test"})
    assert svc._store("qd").url == "http://a.test"
    svc.update_store("qd", config={"url": "http://b.test"})
    assert svc._store("qd").url == "http://b.test"


def test_stores_in_use_and_the_builtin_cannot_be_deleted(svc):
    with pytest.raises(SV.RagError, match="built-in"):
        svc.delete_store("local")
    svc.add_store("qd", "Q", "qdrant", {"url": "http://a.test"})
    with pytest.raises(SV.NotFound):
        svc.delete_store("nope")
    svc.db.execute("INSERT INTO rag_kbs(id,label,description,store_id,collection,provider,model,dim,chunk_size,chunk_overlap,created) VALUES('k','K','','qd','kb_k','mock','m',4,1000,150,0)")
    with pytest.raises(SV.RagError, match="1 knowledge base still use"):
        svc.delete_store("qd")
    svc.db.execute("INSERT INTO rag_kbs(id,label,description,store_id,collection,provider,model,dim,chunk_size,chunk_overlap,created) VALUES('k2','K','','qd','kb_k2','mock','m',4,1000,150,0)")
    with pytest.raises(SV.RagError, match="2 knowledge bases still use"):
        svc.delete_store("qd")
    svc.db.execute("DELETE FROM rag_kbs")
    svc.delete_store("qd")
    assert [s["id"] for s in svc.list_stores()] == ["local"]


def test_store_health_is_reported_never_raised(svc):
    assert run(svc.test_store("local"))["ok"] is True
    svc.add_store("qd", "Q", "qdrant", {"url": "http://a.test"})
    svc._factory = lambda *a: (_ for _ in ()).throw(StoreError("settings are broken"))
    svc._stores.clear()
    assert run(svc.test_store("qd")) == {"ok": False, "detail": "settings are broken"}
    with pytest.raises(SV.NotFound):
        run(svc.test_store("nope"))


# ---------------------------------------------------------- knowledge bases
def test_creating_a_knowledge_base_probes_the_embedding_size(svc, emb):
    k = kb(svc, "my-docs", label=" My docs ", description="notes")
    assert k["dim"] == 32 and k["id"] == "my-docs" and k["label"] == "My docs"
    assert svc._kb_row("my-docs")["collection"] == "kb_my_docs" and k["docs"] == 0 and k["store_kind"] == "local"
    assert emb.calls[0] == ("mock", "embed-a", ["dimension probe"])
    assert svc.get_kb("my-docs") == k and svc.list_kbs() == [k]


@pytest.mark.parametrize("kw,exc,msg", [
    (dict(kb_id="Bad Id"), SV.RagError, "knowledge base id must be"),
    (dict(model="  "), SV.RagError, "choose an embedding model"),
    (dict(chunk_size=50), SV.RagError, "chunk size"),
    (dict(store="nope"), SV.NotFound, "unknown store"),
])
def test_bad_knowledge_bases_are_refused(svc, kw, exc, msg):
    with pytest.raises(exc, match=msg):
        kb(svc, **kw)


def test_duplicate_ids_and_a_clashing_collection_are_refused(state, tmp_path):
    fake = FakeQdrant()
    svc = SV.RagService(state.db, state.providers, state.guard, home=tmp_path, embedder=FakeEmbedder(), factory=factory_with(fake))
    kb(svc, "one")
    with pytest.raises(SV.RagError, match="already exists"):
        kb(svc, "one")
    svc.add_store("qd", "Q", "qdrant", {"url": "http://q.test"})
    run(svc._store("qd").ensure("kb_clash", 8))                       # same collection name, different vector size
    with pytest.raises(SV.RagError, match="dimension 8, not 32"):
        kb(svc, "clash", store="qd")


def test_updating_a_knowledge_base(svc):
    kb(svc)
    k = svc.update_kb("docs", label="Renamed", description="new text")
    assert (k["label"], k["description"]) == ("Renamed", "new text")
    k = svc.update_kb("docs", label="   ")
    assert k["label"] == "Renamed" and k["description"] == "new text"
    with pytest.raises(SV.NotFound):
        svc.update_kb("nope", label="x")


def test_deleting_a_knowledge_base_removes_vectors_and_records(svc):
    kb(svc)
    run(svc.ingest_text("docs", "Note", "Some text to remember."))
    store = svc._store("local")
    assert run(store.count("kb_docs")) == 1
    run(svc.delete_kb("docs"))
    assert run(store.count("kb_docs")) == 0 and svc.list_kbs() == [] and svc.db.query("SELECT * FROM rag_docs") == []
    with pytest.raises(SV.NotFound):
        run(svc.delete_kb("docs"))


def test_deleting_when_the_store_is_down_needs_force(state, tmp_path):
    fake = FakeQdrant()
    svc = SV.RagService(state.db, state.providers, state.guard, home=tmp_path, embedder=FakeEmbedder(), factory=factory_with(fake))
    svc.add_store("qd", "Q", "qdrant", {"url": "http://q.test"})
    kb(svc, "remote", store="qd")

    def down(req):
        raise httpx.ConnectError("refused")
    svc._stores["qd"]._transport = httpx.MockTransport(down)
    with pytest.raises(SV.RagError, match="could not remove its vectors .*cannot reach Qdrant.*force"):
        run(svc.delete_kb("remote"))
    assert len(svc.list_kbs()) == 1
    run(svc.delete_kb("remote", force=True))
    assert svc.list_kbs() == []


def test_a_broken_store_configuration_is_reported_on_use(svc):
    svc.add_store("qd", "Q", "qdrant", {"url": "http://a.test"})
    svc._factory = lambda *a: (_ for _ in ()).throw(StoreError("settings are broken"))
    svc._stores.clear()
    with pytest.raises(SV.RagError, match="settings are broken"):
        svc._store("qd")


# ----------------------------------------------------------------- documents
def test_ingesting_adds_replaces_and_skips_unchanged_documents(svc, emb):
    kb(svc)
    first = run(svc.ingest("docs", "guide.md", b"# Guide\n\nHow to reset your password.", source="guide.md"))
    assert first["status"] == "added" and first["chunks"] == 1 and first["source"] == "guide.md"
    calls = len(emb.calls)
    assert run(svc.ingest("docs", "guide.md", b"# Guide\n\nHow to reset your password.", source="guide.md"))["status"] == "unchanged"
    assert len(emb.calls) == calls                                            # nothing was embedded again
    long_text = ("A paragraph about invoices and billing. " * 40 + "\n\n") * 6
    second = run(svc.ingest("docs", "guide.md", long_text.encode(), source="guide.md"))
    assert second["status"] == "updated" and second["chunks"] > 1 and second["doc_id"] == first["doc_id"]
    assert run(svc._store("local").count("kb_docs")) == second["chunks"]       # the old chunks are gone
    docs = svc.list_docs("docs")
    assert len(docs) == 1 and docs[0]["chunks"] == second["chunks"] and svc.get_kb("docs")["chunks"] == second["chunks"]


def test_the_source_defaults_to_the_file_name(svc):
    kb(svc)
    assert run(svc.ingest("docs", "notes.txt", b"hello there"))["source"] == "notes.txt"


def test_unreadable_files_and_unknown_knowledge_bases_are_refused(svc):
    kb(svc)
    with pytest.raises(SV.RagError, match="unsupported file type"):
        run(svc.ingest("docs", "a.exe", b"MZ"))
    with pytest.raises(SV.NotFound):
        run(svc.ingest("nope", "a.txt", b"x"))
    with pytest.raises(SV.NotFound):
        svc.list_docs("nope")


def test_a_changed_embedding_size_is_caught(svc):
    kb(svc)
    svc.embedder = FakeEmbedder(dim=8)
    with pytest.raises(SV.RagError, match="8-dimension vectors.*built with 32"):
        run(svc.ingest("docs", "a.txt", b"hello world"))
    assert svc.list_docs("docs") == []


def test_a_failed_embedding_keeps_the_previous_version(svc):
    kb(svc)
    run(svc.ingest("docs", "a.txt", b"original text about cats"))

    class Boom:
        async def embed(self, *a):
            raise RuntimeError("provider down")
    svc.embedder = Boom()
    with pytest.raises(RuntimeError):
        run(svc.ingest("docs", "a.txt", b"new text about dogs"))
    assert run(svc._store("local").count("kb_docs")) == 1
    assert run(svc._store("local").search("kb_docs", vec_for("cats"), 1))[0].payload["text"] == "original text about cats"


def test_a_store_failure_during_ingest_is_reported(svc):
    kb(svc)
    run(svc._store("local").drop("kb_docs"))
    with pytest.raises(SV.RagError, match="does not exist"):
        run(svc.ingest("docs", "a.txt", b"hello"))
    assert svc.list_docs("docs") == []


def test_ingesting_text_needs_a_title(svc):
    kb(svc)
    with pytest.raises(SV.RagError, match="give the text a title"):
        run(svc.ingest_text("docs", "  ", "body"))
    r = run(svc.ingest_text("docs", " Meeting notes ", "We agreed to ship on Friday."))
    assert r["source"] == "Meeting notes" and r["status"] == "added"


def test_deleting_a_document(svc):
    kb(svc)
    r = run(svc.ingest("docs", "a.txt", b"first doc"))
    run(svc.ingest("docs", "b.txt", b"second doc"))
    assert run(svc.delete_doc("docs", r["doc_id"])) == 1 and [d["source"] for d in svc.list_docs("docs")] == ["b.txt"]
    with pytest.raises(SV.NotFound, match="unknown document"):
        run(svc.delete_doc("docs", r["doc_id"]))
    with pytest.raises(SV.NotFound):
        run(svc.delete_doc("nope", "x"))


def test_a_store_failure_while_deleting_a_document_is_reported(svc):
    kb(svc)
    r = run(svc.ingest("docs", "a.txt", b"first doc"))

    class Down:
        async def delete_doc(self, *a):
            raise StoreError("store is down")
    svc._stores["local"] = Down()
    with pytest.raises(SV.RagError, match="store is down"):
        run(svc.delete_doc("docs", r["doc_id"]))
    assert len(svc.list_docs("docs")) == 1


# -------------------------------------------------------------------- search
def seed(svc):
    kb(svc)
    for name, text in [("billing.txt", "Invoices and billing: how refunds and charges work."),
                       ("shipping.txt", "Shipping and delivery: tracking parcels and delays."),
                       ("login.txt", "Login problems: reset your password and sign in.")]:
        run(svc.ingest("docs", name, text.encode(), source=name))


def test_search_returns_the_best_chunks_first_with_their_source(svc):
    seed(svc)
    hits = run(svc.search("refunds and charges", ["docs"], k=2))
    assert [h["source"] for h in hits][0] == "billing.txt" and len(hits) == 2
    assert hits[0]["score"] >= hits[1]["score"] and hits[0]["kb"] == "docs" and hits[0]["kb_label"] == "Docs"
    assert hits[0]["chunk"] == 0 and "refunds" in hits[0]["text"] and isinstance(hits[0]["doc_id"], str)


def test_search_text_is_capped(svc):
    kb(svc, chunk_size=4000, chunk_overlap=0)
    run(svc.ingest_text("docs", "Big", "word " * 700))
    assert len(run(svc.search("word", ["docs"]))[0]["text"]) == SV.MAX_HIT_CHARS


def test_search_validates_its_input(svc):
    seed(svc)
    with pytest.raises(SV.RagError, match="empty"):
        run(svc.search("  ", ["docs"]))
    with pytest.raises(SV.RagError, match="between 1 and 20"):
        run(svc.search("x", ["docs"], k=0))
    with pytest.raises(SV.RagError, match="between 1 and 20"):
        run(svc.search("x", ["docs"], k=21))
    with pytest.raises(SV.RagError, match="at least one"):
        run(svc.search("x", []))
    with pytest.raises(SV.NotFound):
        run(svc.search("x", ["nope"]))


def test_search_spans_knowledge_bases_and_embeds_the_query_once_per_model(svc, emb):
    seed(svc)
    kb(svc, "other", label="Other", model="embed-b")
    run(svc.ingest_text("other", "Refund policy", "Refunds are issued within five days of the request."))
    emb.calls.clear()
    hits = run(svc.search("refunds", ["docs", "other", "docs"], k=3))
    assert {h["kb"] for h in hits} == {"docs", "other"} and [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
    assert sorted(c[1] for c in emb.calls) == ["embed-a", "embed-b"]           # one query embedding per distinct model


def test_a_store_failure_during_search_names_the_knowledge_base(svc):
    seed(svc)

    class Down:
        async def search(self, *a):
            raise StoreError("store is down")
    svc._stores["local"] = Down()
    with pytest.raises(SV.RagError, match="Docs: store is down"):
        run(svc.search("refunds", ["docs"]))
