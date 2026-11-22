"""Managed RAG through RagService and /api/rag: schema migration, KBs without embedding models, ingestion, mixed search."""
import asyncio

import pytest

from app.db import Database
from app.rag import api as RA
from app.rag import service as SV
from app.rag import stack as stack_mod
from app.rag.stores.base import StoreError
from app.rag.stores.bedrock_kb import BedrockKbStore
from app.rag.stores.registry import make_store
from app.rag.stores.vertex_rag import VertexRagStore
from tests.rag_fakes import FakeEmbedder
from tests.rag_fakes_stores import FakeAws, FakeVertexRag
from tests.test_rag_stack import FakeDocker

run = asyncio.run


async def no_sleep(_):
    return None


def make_factory(vx, aws):
    def f(kind, config, secrets, home):
        if kind == "vertex_rag":
            return VertexRagStore(config["project"], config.get("location") or "us-central1", lambda: "tok", transport=vx.transport, sleep=no_sleep)
        if kind == "bedrock_kb":
            return BedrockKbStore(config["region"], aws.clients, config.get("s3_bucket", ""), config.get("s3_prefix") or "agent-studio/",
                                  config.get("data_source_id", ""))
        return make_store(kind, config, secrets, home)
    return f


@pytest.fixture
def vx():
    return FakeVertexRag()


@pytest.fixture
def aws():
    return FakeAws()


@pytest.fixture
def emb():
    return FakeEmbedder()


@pytest.fixture
def svc(state, tmp_path, emb, vx, aws):
    s = SV.RagService(state.db, state.providers, state.guard, home=tmp_path / "home", embedder=emb, factory=make_factory(vx, aws))
    s.add_store("gcp", "Vertex", "vertex_rag", {"project": "demo-proj"})
    s.add_store("aws", "Bedrock (upload)", "bedrock_kb", {"region": "us-east-1", "s3_bucket": "my-bucket", "data_source_id": "DSDSDSDS01"})
    s.add_store("aws-ro", "Bedrock (search only)", "bedrock_kb", {"region": "us-east-1"})
    return s


def vkb(svc, kb_id="gdocs", **kw):
    return run(svc.create_kb(kb_id, kw.pop("label", "Google docs"), kw.pop("store", "gcp"), **kw))


def bkb(svc, aws, kb_id="adocs", store="aws", **kw):
    return run(svc.create_kb(kb_id, "AWS docs", store, remote=aws.kb_id, **kw))


# -------------------------------------------------------------------------------------------------------- migration
def test_an_old_database_is_upgraded_in_place_and_its_knowledge_bases_stay_vector_kbs(tmp_path, state):
    db = Database(tmp_path / "old.db")
    db.conn.executescript("""
        CREATE TABLE rag_kbs(id TEXT PRIMARY KEY, label TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', store_id TEXT NOT NULL,
          collection TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL,
          chunk_size INTEGER NOT NULL, chunk_overlap INTEGER NOT NULL, created REAL NOT NULL);
        INSERT INTO rag_kbs VALUES('legacy','Legacy','','local','kb_legacy','mock','embed-a',32,1000,150,1.0);""")
    svc = SV.RagService(db, state.providers, state.guard, home=tmp_path / "h", embedder=FakeEmbedder())
    kb = svc.get_kb("legacy")
    assert kb["managed"] is False and kb["owned"] is False and kb["remote"] == "" and kb["dim"] == 32 and kb["can_ingest"] is True
    SV.RagService(db, state.providers, state.guard, home=tmp_path / "h", embedder=FakeEmbedder())            # running it again is harmless
    assert {r["name"] for r in db.query("PRAGMA table_info(rag_kbs)")} >= {"managed", "owned"}


# ------------------------------------------------------------------------------------------------- kinds and stores
def test_kinds_carry_help_text_managed_flags_and_ingest_capability(svc):
    kinds = {k["id"]: k for k in svc.kinds()}
    assert {"local", "qdrant", "pgvector", "chroma", "pinecone", "vertex_rag", "bedrock_kb"} <= set(kinds)
    assert kinds["local"]["managed"] is False and kinds["local"]["remote"] is None and kinds["local"]["ingest"] == "yes"
    assert kinds["bedrock_kb"]["managed"] and kinds["bedrock_kb"]["ingest"] == "conditional" and kinds["bedrock_kb"]["remote"]["required"] is True
    assert kinds["vertex_rag"]["remote"]["required"] is False and "never deleted" in kinds["vertex_rag"]["remote"]["help"]
    pw = next(f for f in kinds["pgvector"]["fields"] if f["name"] == "password")
    assert pw["secret"] and pw["help"] and set(pw) == {"name", "label", "secret", "required", "placeholder", "default", "help"}
    assert "search-only" in kinds["bedrock_kb"]["description"] and kinds["pinecone"]["docker_stack"] is None


def test_stores_report_whether_documents_can_be_added(svc, state):
    stores = {s["id"]: s for s in svc.list_stores()}
    assert stores["local"]["can_ingest"] is True and stores["local"]["managed"] is False
    assert stores["gcp"]["managed"] and stores["gcp"]["can_ingest"] and "uploaded" in stores["gcp"]["ingest_note"]
    assert stores["aws"]["can_ingest"] is True
    assert stores["aws-ro"]["can_ingest"] is False and "search-only" in stores["aws-ro"]["ingest_note"]
    svc._factory = lambda *a: (_ for _ in ()).throw(StoreError("settings are broken"))
    svc._stores.clear()
    broken = svc.get_store("gcp")
    assert broken["can_ingest"] is False and broken["ingest_note"] == "settings are broken"


def test_secrets_of_managed_stores_are_write_only(svc):
    s = svc.add_store("aws2", "B", "bedrock_kb", {"region": "us-east-1"}, {"access_key_id": "AKIAEXAMPLE", "secret_access_key": "topsecret"})
    assert s["secrets_set"] == {"access_key_id": True, "secret_access_key": True, "session_token": False}
    assert "topsecret" not in str(svc.list_stores()) and "AKIAEXAMPLE" not in str(svc.list_stores())


# ------------------------------------------------------------------------------------------------- creating managed KBs
def test_a_vertex_knowledge_base_has_no_embedding_model_and_owns_its_corpus(svc, emb, vx):
    kb = vkb(svc, provider="mock", model="ignored")
    assert (kb["provider"], kb["model"], kb["dim"]) == ("", "", 0) and kb["managed"] and kb["owned"] and kb["can_ingest"]
    assert kb["remote"].endswith("/ragCorpora/1001") and kb["store_kind"] == "vertex_rag" and kb["chunks"] == 0
    assert emb.calls == [] and vx.corpora[kb["remote"]]["displayName"] == "kb_gdocs"


def test_attaching_existing_resources_is_never_owned(svc, aws, vx):
    name = vx.add_corpus("42", "theirs")
    kb = vkb(svc, "attached", remote="42")
    assert kb["remote"] == name and kb["owned"] is False and kb["managed"]
    b = bkb(svc, aws)
    assert b["remote"] == aws.kb_id and b["owned"] is False and b["can_ingest"] is True
    assert bkb(svc, aws, "ro", store="aws-ro")["can_ingest"] is False


def test_managed_creation_errors_are_readable(svc, aws):
    with pytest.raises(SV.RagError, match="cannot create Bedrock knowledge bases"):
        run(svc.create_kb("nope", "x", "aws"))
    with pytest.raises(SV.RagError, match="was not found"):
        vkb(svc, "ghost", remote="999")
    with pytest.raises(SV.RagError, match="only applies to managed stores"):
        run(svc.create_kb("vv", "V", "local", "mock", "embed-a", remote="X"))
    with pytest.raises(SV.RagError, match="chunk size"):
        vkb(svc, "tiny", chunk_size=10)
    assert svc.list_kbs() == []


# ----------------------------------------------------------------------------------------------------- ingestion
def test_vertex_ingestion_adds_replaces_skips_unchanged_and_deletes(svc, vx):
    kb = vkb(svc)
    first = run(svc.ingest_text("gdocs", "Refund policy", "Refunds are issued within five days."))
    assert first["status"] == "added" and first["chunks"] == 0 and "note" not in first
    assert run(svc.ingest_text("gdocs", "Refund policy", "Refunds are issued within five days."))["status"] == "unchanged"
    second = run(svc.ingest_text("gdocs", "Refund policy", "Refunds now take ten days."))
    assert second["status"] == "updated" and len(vx.corpora[kb["remote"]]["files"]) == 1
    docs = svc.list_docs("gdocs")
    assert len(docs) == 1 and docs[0]["chunks"] == 0 and svc.get_kb("gdocs")["docs"] == 1
    assert run(svc.delete_doc("gdocs", first["doc_id"])) == 1 and vx.corpora[kb["remote"]]["files"] == {}


def test_a_store_error_while_ingesting_into_a_managed_kb_is_reported(svc, vx):
    vkb(svc)
    with pytest.raises(SV.RagError, match="unsupported file"):
        run(svc.ingest_text("gdocs", "Bad", "REJECT this"))
    assert svc.list_docs("gdocs") == []


def test_bedrock_ingestion_returns_the_indexing_note(svc, aws):
    bkb(svc, aws)
    r = run(svc.ingest_text("adocs", "Parental leave", "Parental leave is sixteen weeks."))
    assert r["status"] == "added" and "background" in r["note"] and aws.jobs == 1


def test_a_search_only_knowledge_base_refuses_documents_and_says_why(svc, aws):
    bkb(svc, aws, "ro", store="aws-ro")
    with pytest.raises(SV.RagError, match="'AWS docs' is search-only: .*Add them in AWS"):
        run(svc.ingest_text("ro", "Title", "body"))
    assert svc.list_docs("ro") == []


def test_unreadable_files_are_refused_before_anything_is_uploaded(svc, vx):
    vkb(svc)
    with pytest.raises(SV.RagError, match="unsupported file type"):
        run(svc.ingest("gdocs", "a.exe", b"MZ"))
    assert all(not c["files"] for c in vx.corpora.values())


# -------------------------------------------------------------------------------------------------------- sync
def test_sync_asks_a_managed_store_to_reindex(svc, aws):
    bkb(svc, aws)
    assert run(svc.sync_kb("adocs"))["ok"] is True and aws.jobs == 1
    vkb(svc)
    assert "nothing to sync" in run(svc.sync_kb("gdocs"))["detail"]
    bkb(svc, aws, "ro", store="aws-ro")
    with pytest.raises(SV.RagError, match="search-only"):
        run(svc.sync_kb("ro"))
    run(svc.create_kb("plain", "P", "local", "mock", "embed-a"))
    with pytest.raises(SV.RagError, match="not managed"):
        run(svc.sync_kb("plain"))
    with pytest.raises(SV.NotFound):
        run(svc.sync_kb("nope"))


# ------------------------------------------------------------------------------------------------------- search
def test_search_by_text_needs_no_embedding_and_names_documents_by_their_source(svc, emb):
    vkb(svc)
    run(svc.ingest_text("gdocs", "Refund policy", "Refunds are issued within five days of the request."))
    run(svc.ingest_text("gdocs", "Shipping", "Parcels are tracked until delivery."))
    emb.calls.clear()
    hits = run(svc.search("refunds issued", ["gdocs"], k=1))
    assert emb.calls == [] and len(hits) == 1
    assert hits[0]["source"] == "Refund policy" and hits[0]["kb"] == "gdocs" and hits[0]["score_kind"] == "provider" and hits[0]["chunk"] is None


def test_unknown_documents_in_a_managed_kb_keep_the_providers_own_source(svc, aws):
    bkb(svc, aws)
    hits = run(svc.search("parental leave holidays", ["adocs"], k=1))
    assert hits[0]["source"] == "s3://other-bucket/handbook.pdf" and hits[0]["doc_id"] is None


def test_mixed_search_interleaves_groups_instead_of_comparing_scores(svc, aws):
    run(svc.create_kb("vec", "Vectors", "local", "mock", "embed-a"))
    for name, text in (("a.txt", "refunds and invoices billing"), ("b.txt", "refunds charges policy"), ("c.txt", "refunds money back")):
        run(svc.ingest("vec", name, text.encode(), source=name))
    vkb(svc)
    run(svc.ingest_text("gdocs", "Google refunds", "refunds are issued by Google"))
    bkb(svc, aws)
    run(svc.ingest_text("adocs", "AWS refunds", "refunds are issued by AWS"))
    hits = run(svc.search("refunds", ["vec", "gdocs", "adocs"], k=6))
    assert [h["kb"] for h in hits[:3]] == ["vec", "gdocs", "adocs"]              # best of each group first
    assert [h["kb"] for h in hits] == ["vec", "gdocs", "adocs", "vec", "adocs", "vec"]
    vec_scores = [h["score"] for h in hits if h["kb"] == "vec"]
    assert vec_scores == sorted(vec_scores, reverse=True) and {h["score_kind"] for h in hits} == {"cosine", "provider"}
    only_vec = run(svc.search("refunds", ["vec"], k=2))
    assert [h["score_kind"] for h in only_vec] == ["cosine", "cosine"] and only_vec[0]["score"] >= only_vec[1]["score"]


def test_a_failing_managed_search_names_the_knowledge_base(svc, aws):
    bkb(svc, aws)
    from tests.rag_fakes_stores import aws_error
    aws.fail["retrieve"] = aws_error("AccessDeniedException", "no")
    with pytest.raises(SV.RagError, match="AWS docs: AWS rejected the credentials"):
        run(svc.search("x", ["adocs"]))


# ------------------------------------------------------------------------------------------------------- deleting
def test_deleting_an_owned_corpus_removes_it(svc, vx):
    kb = vkb(svc)
    run(svc.ingest_text("gdocs", "Doc", "some text"))
    run(svc.delete_kb("gdocs"))
    assert kb["remote"] not in vx.corpora and svc.list_kbs() == [] and svc.db.query("SELECT * FROM rag_docs") == []


def test_deleting_an_attached_resource_removes_only_what_we_uploaded(svc, vx, aws):
    name = vx.add_corpus("42", "theirs", {"theirs/ragFiles/1": {"displayName": "keep-me", "text": "their own document"}})
    vkb(svc, "attached", remote="42")
    run(svc.ingest_text("attached", "Mine", "my uploaded document"))
    assert len(vx.corpora[name]["files"]) == 2
    run(svc.delete_kb("attached"))
    assert [f["displayName"] for f in vx.corpora[name]["files"].values()] == ["keep-me"]
    bkb(svc, aws)
    run(svc.ingest_text("adocs", "Mine", "uploaded"))
    run(svc.delete_kb("adocs"))
    assert aws.s3 == {} and aws.kb_id                                              # our object is gone; the Bedrock KB is untouched
    bkb(svc, aws, "ro", store="aws-ro")
    run(svc.delete_kb("ro"))                                                       # search-only: nothing of ours to remove
    assert svc.list_kbs() == []


def test_deleting_when_the_provider_is_down_needs_force(svc, vx):
    import httpx
    vkb(svc)

    def down(req):
        raise httpx.ConnectError("refused")
    svc._stores["gcp"]._transport = httpx.MockTransport(down)
    with pytest.raises(SV.RagError, match="could not remove its vectors .*cannot reach Vertex AI.*force"):
        run(svc.delete_kb("gdocs"))
    run(svc.delete_kb("gdocs", force=True))
    assert svc.list_kbs() == []


# ---------------------------------------------------------------------------------------------------------- HTTP
@pytest.fixture
def api(client, state, vx, aws):
    state.rag.embedder = FakeEmbedder()
    state.rag._factory = make_factory(vx, aws)
    return client


def test_managed_knowledge_bases_over_http(api, aws):
    assert api.post("/api/rag/stores", json={"id": "gcp", "kind": "vertex_rag", "config": {"project": "demo-proj"}}).status_code == 200
    assert api.post("/api/rag/stores", json={"id": "aws", "kind": "bedrock_kb", "config": {"region": "us-east-1"}}).status_code == 200
    r = api.post("/api/rag/kbs", json={"id": "gdocs", "store_id": "gcp"})                       # no provider, no model
    assert r.status_code == 200 and r.json()["managed"] is True and r.json()["model"] == "" and r.json()["can_ingest"] is True
    assert api.post("/api/rag/kbs", json={"id": "adocs", "store_id": "aws"}).status_code == 422       # needs the Bedrock KB id
    b = api.post("/api/rag/kbs", json={"id": "adocs", "store_id": "aws", "remote": aws.kb_id}).json()
    assert b["remote"] == aws.kb_id and b["can_ingest"] is False and "search-only" in b["ingest_note"]
    up = api.post("/api/rag/kbs/adocs/docs", files=[("files", ("a.txt", b"hello there", "text/plain"))]).json()
    assert up[0]["status"] == "skipped" and "search-only" in up[0]["error"]
    assert api.post("/api/rag/kbs/gdocs/text", json={"title": "Refunds", "text": "Refunds are issued within five days."}).json()["status"] == "added"
    hits = api.post("/api/rag/search", json={"query": "refunds issued", "kbs": ["gdocs", "adocs"], "k": 3}).json()
    assert hits[0]["source"] == "Refunds" and {h["kb"] for h in hits} == {"gdocs", "adocs"}
    assert api.post("/api/rag/kbs/adocs/sync").status_code == 422 and api.post("/api/rag/kbs/nope/sync").status_code == 404
    assert api.post("/api/rag/kbs/gdocs/sync").json()["ok"] is True
    kinds = {k["id"]: k for k in api.get("/api/rag/kinds").json()}
    assert kinds["bedrock_kb"]["remote"]["name"] == "knowledge_base_id" and kinds["bedrock_kb"]["managed"]


def test_vector_kbs_still_work_exactly_as_before_over_http(api):
    r = api.post("/api/rag/kbs", json={"id": "docs", "provider": "mock", "model": "embed-a"})
    assert r.status_code == 200 and r.json()["dim"] == 32 and r.json()["managed"] is False and r.json()["remote"] == ""


# ------------------------------------------------------------------------------------------------- Docker stacks
@pytest.fixture
def docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(stack_mod, "_docker", fake)
    return fake


def test_stack_catalog_pins_images_and_binds_to_localhost_with_named_volumes():
    pg, ch = stack_mod.STACKS["pgvector"], stack_mod.STACKS["chroma"]
    assert pg.image == "pgvector/pgvector:0.8.6-pg17" and ch.image == "chromadb/chroma:1.5.9"
    assert (pg.volume, ch.volume) == ("agent-studio-pgvector", "agent-studio-chroma") and "/" not in pg.volume + ch.volume
    assert (pg.store_kind, ch.store_kind) == ("pgvector", "chroma") and pg.port != 5432
    assert "POSTGRES_PASSWORD" in pg.gen_env and "POSTGRES_PASSWORD" not in pg.env and not ch.gen_env


def test_pgvector_stack_generates_a_password_once_and_uses_it_for_the_container_and_the_store(api, docker, tmp_path):
    started = api.post("/api/rag/stacks/pgvector/start").json()
    assert started["state"] == "running" and started["image"] == "pgvector/pgvector:0.8.6-pg17"
    run_args = next(c for c in docker.calls if c[0] == "run")
    env = [a for a in run_args if a.startswith("POSTGRES_PASSWORD=")]
    assert len(env) == 1 and len(env[0].split("=", 1)[1]) == 48 and "POSTGRES_USER=agentstudio" in run_args
    assert ["-p", "127.0.0.1:5433:5432"] == run_args[run_args.index("-p"):run_args.index("-p") + 2]
    reg = api.post("/api/rag/stacks/pgvector/register")
    assert reg.status_code == 200 and reg.json()["id"] == "docker-pgvector" and reg.json()["secrets_set"] == {"password": True, "dsn": False}
    assert reg.json()["config"]["host"] == "127.0.0.1" and reg.json()["config"]["port"] == "5433"
    password = env[0].split("=", 1)[1]
    assert password not in reg.text and password not in api.get("/api/rag/stores").text and password not in api.get("/api/rag/stacks").text
    stored = api.app.state.s.db.one("SELECT secrets FROM rag_stores WHERE id='docker-pgvector'")["secrets"]
    assert password in stored                                                      # the store holds the same secret the container got
    assert stack_mod.stack_secret("pgvector", "password") == password               # stable across calls and restarts


def test_chroma_stack_registers_without_any_secret(api, docker):
    api.post("/api/rag/stacks/chroma/start")
    run_args = next(c for c in docker.calls if c[0] == "run")
    assert "IS_PERSISTENT=TRUE" in run_args and "ANONYMIZED_TELEMETRY=FALSE" in run_args and not [a for a in run_args if "PASSWORD" in a]
    reg = api.post("/api/rag/stacks/chroma/register").json()
    assert reg["config"] == {"url": "http://127.0.0.1:8008"} and reg["secrets_set"] == {"token": False}


def test_deleting_a_stacks_data_forgets_its_generated_secret(docker):
    first = stack_mod.stack_secret("pgvector", "password")
    other = stack_mod.stack_secret("chroma", "token")
    stack_mod.start("pgvector")
    stack_mod.remove("pgvector")                                                   # data kept: so is the password that unlocks it
    assert stack_mod.stack_secret("pgvector", "password") == first
    stack_mod.remove("pgvector", delete_data=True)
    assert stack_mod.stack_secret("pgvector", "password") != first and stack_mod.stack_secret("chroma", "token") == other


def test_secret_files_that_are_missing_or_corrupt_are_handled(tmp_path, monkeypatch):
    monkeypatch.setattr(stack_mod, "_secrets_file", lambda: tmp_path / "x" / "s.json")
    stack_mod.forget_secrets("pgvector")                                           # nothing there yet: fine
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "s.json").write_text("{not json", encoding="utf-8")
    s = stack_mod.stack_secret("pgvector", "password")                             # corrupt file: start over
    assert len(s) == 48 and stack_mod.stack_secret("pgvector", "password") == s
    assert stack_mod.store_secrets(stack_mod.STACKS["pgvector"]) == {"password": s}
    assert stack_mod.store_secrets(stack_mod.STACKS["qdrant"]) == {}


def test_registering_still_works_for_qdrant(api, docker):
    assert api.post("/api/rag/stacks/qdrant/register").json()["config"] == {"url": "http://127.0.0.1:6333"}


def test_module_constants_are_exposed_for_the_ui():
    assert RA.INGESTABLE and SV.MIGRATIONS
