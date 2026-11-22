"""Managed RAG adapters (Vertex AI RAG Engine, Amazon Bedrock Knowledge Bases) against in-memory fakes."""
import asyncio
import json
import types

import httpx
import pytest

from app.rag.stores import bedrock_kb as BK
from app.rag.stores import registry as R
from app.rag.stores import vertex_rag as VX
from app.rag.stores.base import ManagedRef, ManagedStore, StoreError
from app.rag.stores.bedrock_kb import BedrockKbStore
from app.rag.stores.vertex_rag import VertexRagStore
from tests.rag_fakes_stores import FakeAws, FakeVertexRag, aws_error

run = asyncio.run
PARENT = FakeVertexRag.PARENT


async def no_sleep(_):
    return None


def vertex(fake=None, token=lambda: "tok", **kw):
    fake = fake or FakeVertexRag()
    return VertexRagStore("demo-proj", "us-central1", token, transport=fake.transport, sleep=no_sleep, **kw), fake


# ------------------------------------------------------------------------------------------------------------- Vertex
def test_vertex_validates_project_and_location():
    for project, location in (("", "us-central1"), ("a b", "us-central1"), ("my-proj", ""), ("my-proj", "US"), ("my-proj", "us-central1/../x")):
        with pytest.raises(StoreError):
            VertexRagStore(project, location, lambda: "t")


def test_vertex_is_a_managed_store_that_can_ingest():
    s, _ = vertex()
    assert isinstance(s, ManagedStore) and s.managed and s.can_ingest


def test_vertex_health_counts_corpora_across_pages():
    s, fake = vertex()
    assert run(s.health()) == {"ok": True, "detail": f"Vertex AI RAG Engine ({PARENT}): 0 corpuses"}
    fake.add_corpus("1", "a")
    assert run(s.health())["detail"].endswith(": 1 corpus")
    fake.add_corpus("2", "b")
    assert run(s.health())["detail"].endswith(": 2 corpuses")


def test_vertex_creating_a_corpus_waits_for_the_long_running_operation():
    s, fake = vertex(FakeVertexRag(pending_polls=3))
    ref = run(s.ensure("kb_docs", ""))
    assert ref == ManagedRef(f"{PARENT}/ragCorpora/1001", True) and fake.corpora[ref.collection]["displayName"] == "kb_docs"
    assert len([r for r in fake.requests if "/operations/" in r[1]]) == 3


def test_vertex_refuses_to_adopt_a_corpus_that_already_has_the_name_and_reports_failed_creation():
    s, fake = vertex()
    fake.add_corpus("7", "kb_docs")
    with pytest.raises(StoreError, match="already exists in this project: attach it with its id"):
        run(s.ensure("kb_docs", ""))
    broken, _ = vertex(FakeVertexRag(create_error="quota exceeded"))
    with pytest.raises(StoreError, match="Vertex AI error: quota exceeded"):
        run(broken.ensure("kb_x", ""))
    nameless = FakeVertexRag()
    nameless.nameless = True
    with pytest.raises(StoreError, match="did not return its name"):
        run(vertex(nameless)[0].ensure("kb_x", ""))


def test_vertex_gives_up_waiting_eventually(monkeypatch):
    monkeypatch.setattr(VX, "POLL_TRIES", 2)
    s, _ = vertex(FakeVertexRag(pending_polls=99))
    with pytest.raises(StoreError, match="still working on it"):
        run(s.ensure("kb_x", ""))


def test_vertex_attaches_an_existing_corpus_by_id_or_full_name_and_never_owns_it():
    s, fake = vertex()
    name = fake.add_corpus("42", "mine")
    assert run(s.ensure("kb_x", "42")) == ManagedRef(name, False)
    assert run(s.ensure("kb_x", name)) == ManagedRef(name, False)
    with pytest.raises(StoreError, match="was not found"):
        run(s.ensure("kb_x", "999"))
    with pytest.raises(StoreError, match="corpus must be a corpus id"):
        run(s.ensure("kb_x", "../../other"))


def test_vertex_uploads_searches_replaces_and_deletes_documents():
    s, fake = vertex()
    corpus = run(s.ensure("kb_x", "")).collection
    run(s.ingest(corpus, "doc1", "Refund policy.md", "Refunds are issued within five days."))
    run(s.ingest(corpus, "doc2", "Shipping.md", "Parcels are tracked until delivery."))
    hits = run(s.search(corpus, "refunds issued", 1))
    assert len(hits) == 1 and hits[0].payload == {"doc_id": "doc1", "source": "doc1", "text": "Refunds are issued within five days."}
    assert hits[0].score > 0 and hits[0].id == "gs://demo/doc1.txt"
    assert run(s.delete_doc(corpus, "doc1")) == 1 and run(s.delete_doc(corpus, "doc1")) == 0
    assert [f["displayName"] for f in fake.corpora[corpus]["files"].values()] == ["doc2"]


def test_vertex_hits_without_a_score_use_the_distance_and_without_a_name_use_the_uri():
    def handler(req):
        return httpx.Response(200, json={"contexts": {"contexts": [{"sourceUri": "gs://b/x.pdf", "text": "t", "distance": 0.25}, {"text": "n"}]}})
    s = VertexRagStore("demo-proj", "us-central1", lambda: "tok", transport=httpx.MockTransport(handler))
    a, b = run(s.search(f"{PARENT}/ragCorpora/1", "q", 5))
    assert a.score == 0.75 and a.payload["source"] == "gs://b/x.pdf" and a.id == "gs://b/x.pdf"
    assert b.score == 0.0 and b.payload["source"] == "" and b.id == ":1"


def test_vertex_missing_corpus_is_an_empty_search_and_a_failed_write():
    s, _ = vertex()
    ghost = f"{PARENT}/ragCorpora/404"
    assert run(s.search(ghost, "q", 3)) == [] and run(s.delete_doc(ghost, "d")) == 0
    run(s.drop(ghost))
    with pytest.raises(StoreError, match="corpus not found"):
        run(s.ingest(ghost, "d", "d", "text"))


def test_vertex_drop_deletes_the_corpus_with_its_files():
    s, fake = vertex()
    name = fake.add_corpus("5", "kb_x", {"f": {"displayName": "d", "text": "t"}})
    run(s.drop(name))
    assert name not in fake.corpora


def test_vertex_errors_are_mapped():
    s, fake = vertex()
    wrong = VertexRagStore("demo-proj", "us-central1", lambda: "stale", transport=fake.transport)
    assert run(wrong.health()) == {"ok": False, "detail": "Google rejected the credentials (expired or wrong account)"}

    def denied(req):
        return httpx.Response(403, json={"error": {"message": "Permission 'aiplatform.ragCorpora.list' denied"}})
    h = run(VertexRagStore("demo-proj", "us-central1", lambda: "t", transport=httpx.MockTransport(denied)).health())
    assert "Vertex AI User role" in h["detail"] and "ragCorpora.list" in h["detail"]

    def boom(req):
        return httpx.Response(429, text="slow down")
    with pytest.raises(StoreError, match="Vertex AI error 429: slow down"):
        run(VertexRagStore("demo-proj", "us-central1", lambda: "t", transport=httpx.MockTransport(boom)).drop("projects/p/locations/l/ragCorpora/1"))

    def refuse(req):
        raise httpx.ConnectError("refused")
    h = run(VertexRagStore("demo-proj", "us-central1", lambda: "t", transport=httpx.MockTransport(refuse)).health())
    assert "cannot reach Vertex AI at https://us-central1-aiplatform.googleapis.com" in h["detail"]


def test_vertex_a_200_with_an_error_body_and_an_unparseable_body_are_errors_or_empty():
    s, fake = vertex()
    corpus = run(s.ensure("kb_x", "")).collection
    with pytest.raises(StoreError, match="unsupported file"):
        run(s.ingest(corpus, "d", "x", "REJECT me"))
    garbled = VertexRagStore("demo-proj", "us-central1", lambda: "t", transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html>")))
    assert run(garbled.search("projects/p/locations/l/ragCorpora/1", "q", 1)) == []
    server = VertexRagStore("demo-proj", "us-central1", lambda: "t", transport=httpx.MockTransport(lambda r: httpx.Response(500, content=b"<html>oops")))
    with pytest.raises(StoreError, match="Vertex AI error 500: <html>oops"):
        run(server.drop("projects/p/locations/l/ragCorpora/1"))


def test_vertex_sign_in_failures_are_one_clear_message():
    def no_creds():
        raise RuntimeError("secret path C:/keys/sa.json")
    s, _ = vertex(token=no_creds)
    h = run(s.health())
    assert h["ok"] is False and "could not sign in to Google Cloud (RuntimeError)" in h["detail"] and "C:/keys" not in h["detail"]


def test_vertex_sync_has_nothing_to_do():
    s, _ = vertex()
    assert "nothing to sync" in run(s.sync("x"))


# ------------------------------------------------------------------------------------- google-auth token provider
class FakeCreds:
    def __init__(self):
        self.valid, self.token, self.refreshed = False, "ya29.fake", 0

    def refresh(self, request):
        self.valid = True
        self.refreshed += 1


def test_token_provider_uses_a_service_account_json_and_refreshes_once(monkeypatch):
    from google.oauth2 import service_account
    seen = {}
    creds = FakeCreds()

    def from_info(info, scopes):
        seen["info"], seen["scopes"] = info, scopes
        return creds
    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", staticmethod(from_info))
    token = VX.google_token_provider(json.dumps({"client_email": "sa@x"}))
    assert token() == "ya29.fake" and token() == "ya29.fake" and creds.refreshed == 1
    assert seen["info"] == {"client_email": "sa@x"} and seen["scopes"] == ["https://www.googleapis.com/auth/cloud-platform"]


def test_token_provider_rejects_a_broken_service_account_json():
    with pytest.raises(StoreError, match="not valid"):
        VX.google_token_provider("{not json")
    with pytest.raises(StoreError, match="not valid"):
        VX.google_token_provider(json.dumps({"type": "service_account"}))                     # missing keys


def test_token_provider_falls_back_to_application_default_credentials(monkeypatch):
    import google.auth
    creds = FakeCreds()
    monkeypatch.setattr(google.auth, "default", lambda scopes: (creds, "proj"))
    token = VX.google_token_provider("")
    assert token() == "ya29.fake" and token() == "ya29.fake" and creds.refreshed == 1


# ------------------------------------------------------------------------------------------------------------ Bedrock
def bedrock(aws=None, **kw):
    aws = aws or FakeAws()
    args = {"bucket": aws.bucket, "data_source_id": aws.ds_id, **kw}
    return BedrockKbStore("us-east-1", aws.clients, **args), aws


def search_only(aws=None):
    aws = aws or FakeAws()
    return BedrockKbStore("us-east-1", aws.clients), aws


def test_bedrock_validates_its_settings():
    clients = FakeAws().clients
    for kw, msg in (({"region": "nowhere"}, "region"), ({"bucket": "A_B"}, "bucket name"), ({"bucket": "my-bucket"}, "data source"),
                    ({"prefix": "bad prefix"}, "prefix")):
        args = {"region": "us-east-1", **kw}
        with pytest.raises(StoreError, match=msg):
            BedrockKbStore(args.pop("region"), clients, **args)
    assert BedrockKbStore("eu-west-1", clients, "b-1", "docs", "ABCDEFGHIJ").prefix == "docs/"
    assert BedrockKbStore("eu-west-1", clients, "b-1", "", "ABCDEFGHIJ").prefix == ""


def test_bedrock_ingest_capability_depends_on_the_bucket():
    s, _ = bedrock()
    assert isinstance(s, ManagedStore) and s.can_ingest
    s2, _ = search_only()
    assert not s2.can_ingest and "search-only" in s2.ingest_note


def test_bedrock_health_lists_visible_knowledge_bases():
    s, aws = bedrock()
    assert run(s.health()) == {"ok": True, "detail": "Amazon Bedrock Knowledge Bases in us-east-1: 1 knowledge base visible"}
    assert run(search_only(aws)[0].health())["detail"].endswith("(search-only)")
    aws.fail["list_knowledge_bases"] = aws_error("AccessDeniedException", "not authorized")
    h = run(s.health())
    assert h["ok"] is False and "lack permission (AccessDeniedException): not authorized" in h["detail"]


def test_bedrock_attaches_an_existing_knowledge_base_and_never_owns_it():
    s, aws = bedrock()
    assert run(s.ensure("kb_x", aws.kb_id)) == ManagedRef(aws.kb_id, False)
    for bad in ("", "lowercase1", "TOOSHORT", "../../x"):
        with pytest.raises(StoreError, match="cannot create Bedrock knowledge bases"):
            run(s.ensure("kb_x", bad))
    with pytest.raises(StoreError, match="was not found in us-east-1"):
        run(s.ensure("kb_x", "ZZZZZZZZZZ"))
    aws.status = "CREATING"
    with pytest.raises(StoreError, match="CREATING, not ACTIVE"):
        run(s.ensure("kb_x", aws.kb_id))


def test_bedrock_checks_the_data_source_when_ingestion_is_configured():
    s, aws = bedrock(data_source_id="NOPENOPE01")
    with pytest.raises(StoreError, match="data source 'NOPENOPE01' was not found"):
        run(s.ensure("kb_x", aws.kb_id))
    other, aws = bedrock(FakeAws(bucket="someone-else"), bucket="my-bucket")
    with pytest.raises(StoreError, match="does not read from the bucket 'my-bucket'"):
        run(other.ensure("kb_x", aws.kb_id))


def test_bedrock_search_works_without_ingestion_and_returns_provider_scores():
    s, aws = search_only()
    hits = run(s.search(aws.kb_id, "parental leave holidays", 2))
    assert hits[0].payload["source"] == "s3://other-bucket/handbook.pdf" and hits[0].payload["doc_id"] is None
    assert 0 < hits[0].score <= 1 and "parental leave" in hits[0].payload["text"]
    assert hits[-1].id == "https://x.test" and hits[-1].score == 0.0 and hits[-1].payload["text"] == ""     # web source without content
    assert run(s.search("ZZZZZZZZZZ", "x", 3)) == []


def test_bedrock_ingest_writes_to_s3_starts_a_job_and_makes_the_document_searchable():
    s, aws = bedrock()
    note = run(s.ingest(aws.kb_id, "abcd1234abcd1234", "Refunds.md", "Refunds are issued within five days."))
    assert "background" in note and aws.jobs == 1
    key = ("my-bucket", f"agent-studio/{aws.kb_id}/abcd1234abcd1234.txt")
    assert aws.s3[key] == b"Refunds are issued within five days."
    hits = run(s.search(aws.kb_id, "refunds issued", 1))
    assert hits[0].payload["doc_id"] == "abcd1234abcd1234" and hits[0].id == f"s3://my-bucket/{key[1]}"
    assert run(s.delete_doc(aws.kb_id, "abcd1234abcd1234")) == 1 and aws.jobs == 2 and key not in aws.s3 and key not in aws.indexed


def test_bedrock_tolerates_a_job_already_running_and_says_to_sync_again():
    s, aws = bedrock()
    aws.fail["start_ingestion_job"] = aws_error("ConflictException", "a job is running")
    note = run(s.ingest(aws.kb_id, "abcd1234abcd1234", "x.md", "text"))
    assert "run Sync again" in note and ("my-bucket", f"agent-studio/{aws.kb_id}/abcd1234abcd1234.txt") in aws.s3
    aws.fail["start_ingestion_job"] = aws_error("ThrottlingException", "slow down")
    with pytest.raises(StoreError, match="AWS error ThrottlingException: slow down"):
        run(s.sync(aws.kb_id))


def test_bedrock_sync_starts_a_job():
    s, aws = bedrock()
    assert "indexing" in run(s.sync(aws.kb_id)) and aws.jobs == 1


def test_bedrock_without_a_bucket_is_search_only_and_says_so():
    s, aws = search_only()
    for call in (s.ingest(aws.kb_id, "d", "d", "t"), s.delete_doc(aws.kb_id, "d"), s.sync(aws.kb_id)):
        with pytest.raises(StoreError, match="search-only"):
            run(call)
    run(s.drop(aws.kb_id))                                                      # nothing to delete: the knowledge base is not ours


def test_managed_defaults_refuse_ingestion():
    class Minimal(ManagedStore):
        async def health(self):
            return {"ok": True, "detail": ""}

        async def ensure(self, name, remote):
            return ManagedRef(name, False)

        async def search(self, collection, query, k):
            return []

        async def drop(self, collection):
            return None
    m = Minimal()
    assert m.can_ingest is False
    for call in (m.ingest("c", "d", "s", "t"), m.delete_doc("c", "d")):
        with pytest.raises(StoreError, match="search-only"):
            run(call)
    assert "nothing to sync" in run(m.sync("c"))


@pytest.mark.parametrize("code,expect", [
    ("UnrecognizedClientException", "rejected the credentials or they lack permission"),
    ("ExpiredTokenException", "rejected the credentials"),
    ("ValidationException", "AWS error ValidationException"),
])
def test_bedrock_error_codes_are_mapped(code, expect):
    s, aws = bedrock()
    aws.fail["retrieve"] = aws_error(code, "details here")
    with pytest.raises(StoreError, match=expect):
        run(s.search(aws.kb_id, "q", 1))


def test_bedrock_missing_s3_resources_and_connection_problems():
    from botocore.exceptions import EndpointConnectionError, NoCredentialsError
    s, aws = bedrock()
    aws.fail["put_object"] = aws_error("NoSuchBucket", "gone")
    with pytest.raises(StoreError, match="not found in AWS \\(NoSuchBucket\\)"):
        run(s.ingest(aws.kb_id, "d", "d", "t"))
    aws.fail["retrieve"] = EndpointConnectionError(endpoint_url="https://bedrock.example")
    with pytest.raises(StoreError, match="cannot reach Amazon Bedrock in us-east-1: EndpointConnectionError"):
        run(s.search(aws.kb_id, "q", 1))
    aws.fail["retrieve"] = NoCredentialsError()
    with pytest.raises(StoreError, match="no AWS credentials found"):
        run(s.search(aws.kb_id, "q", 1))


# ------------------------------------------------------------------------------------------- boto3 client factory
class FakeSession:
    made = []

    def __init__(self, **kw):
        FakeSession.made.append(kw)
        if kw.get("profile_name") == "ghost":
            raise RuntimeError("The config profile (ghost) could not be found")

    def client(self, service):
        return types.SimpleNamespace(service=service)


def test_aws_clients_use_keys_then_a_profile_then_the_default_chain(monkeypatch):
    import boto3
    monkeypatch.setattr(boto3, "Session", FakeSession)
    FakeSession.made.clear()
    c = BK.aws_clients("eu-west-1", "prof", "AKIA1", "sec", "tok")
    assert c("s3") is c("s3") and c("s3").service == "s3" and c("bedrock-agent").service == "bedrock-agent"
    BK.aws_clients("eu-west-1", "prof", "AKIA1", "sec")
    BK.aws_clients("eu-west-1", "prof")
    BK.aws_clients("eu-west-1")
    assert FakeSession.made == [
        {"region_name": "eu-west-1", "aws_access_key_id": "AKIA1", "aws_secret_access_key": "sec", "aws_session_token": "tok"},
        {"region_name": "eu-west-1", "aws_access_key_id": "AKIA1", "aws_secret_access_key": "sec"},
        {"region_name": "eu-west-1", "profile_name": "prof"},
        {"region_name": "eu-west-1"}]
    with pytest.raises(StoreError, match="AWS settings are not usable: RuntimeError"):
        BK.aws_clients("eu-west-1", "ghost")


# --------------------------------------------------------------------------------------------------------- registry
def test_registry_builds_the_managed_kinds(tmp_path, monkeypatch):
    import boto3
    monkeypatch.setattr(boto3, "Session", FakeSession)
    v = R.make_store("vertex_rag", {"project": "demo-proj"}, {}, tmp_path)
    assert isinstance(v, VertexRagStore) and v._parent == PARENT and callable(v._token)
    assert R.make_store("vertex_rag", {"project": "demo-proj", "location": "europe-west4"}, {}, tmp_path)._api.startswith("https://europe-west4-")
    b = R.make_store("bedrock_kb", {"region": "us-east-1", "s3_bucket": "my-bucket", "data_source_id": "DSDSDSDS01", "s3_prefix": "x/"},
                     {"access_key_id": "AKIA1", "secret_access_key": "sec"}, tmp_path)
    assert isinstance(b, BedrockKbStore) and b.can_ingest and b.prefix == "x/"
    assert not R.make_store("bedrock_kb", {"region": "us-east-1"}, {}, tmp_path).can_ingest


def test_registry_validates_the_managed_kinds():
    with pytest.raises(StoreError, match="Google Cloud project id is required"):
        R.check_fields("vertex_rag", {}, {})
    with pytest.raises(StoreError, match="AWS region is required"):
        R.check_fields("bedrock_kb", {}, {})
    with pytest.raises(StoreError, match="both the access key id and the secret"):
        R.check_fields("bedrock_kb", {"region": "us-east-1"}, {"access_key_id": "AKIA1"})
    with pytest.raises(StoreError, match="data source id together with the bucket"):
        R.check_fields("bedrock_kb", {"region": "us-east-1", "s3_bucket": "b-1"}, {})
    R.check_fields("bedrock_kb", {"region": "us-east-1"}, {})


def test_kinds_describe_managed_capabilities():
    assert R.KINDS["vertex_rag"].managed and R.KINDS["vertex_rag"].ingest == "yes" and R.KINDS["vertex_rag"].remote.name == "corpus"
    assert R.KINDS["bedrock_kb"].ingest == "conditional" and R.KINDS["bedrock_kb"].remote.required is True
    assert not R.KINDS["pgvector"].managed and R.KINDS["pgvector"].docker_stack == "pgvector" and R.KINDS["chroma"].docker_stack == "chroma"
    assert R.KINDS["pinecone"].docker_stack is None
    secrets = {k: {f.name for f in v.fields if f.secret} for k, v in R.KINDS.items()}
    assert secrets["pgvector"] == {"password", "dsn"} and secrets["chroma"] == {"token"} and secrets["pinecone"] == {"api_key"}
    assert secrets["vertex_rag"] == {"service_account_json"} and secrets["bedrock_kb"] == {"access_key_id", "secret_access_key", "session_token"}
    assert all(f.help for v in R.KINDS.values() for f in v.fields if f.secret)
