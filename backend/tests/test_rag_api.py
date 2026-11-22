"""/api/rag/*: stores, Docker stacks, knowledge bases, documents and search over HTTP."""
import pytest

from app.budget import BudgetExceeded
from app.rag import api as RA
from app.rag import stack as stack_mod
from tests.rag_fakes import FakeEmbedder
from tests.test_rag_stack import FakeDocker


@pytest.fixture
def api(client, state):
    state.rag.embedder = FakeEmbedder()
    return client


def make_kb(api, kb_id="docs", **kw):
    body = {"id": kb_id, "label": "Docs", "store_id": "local", "provider": "mock", "model": "embed-a", **kw}
    r = api.post("/api/rag/kbs", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def upload(api, kb_id, *files):
    return api.post(f"/api/rag/kbs/{kb_id}/docs", files=[("files", f) for f in files])


# -------------------------------------------------------------------- stores
def test_kinds_and_the_builtin_store(api):
    assert {k["id"] for k in api.get("/api/rag/kinds").json()} >= {"local", "qdrant"}
    stores = api.get("/api/rag/stores").json()
    assert [s["id"] for s in stores] == ["local"] and stores[0]["builtin"]


def test_store_lifecycle_over_http(api):
    r = api.post("/api/rag/stores", json={"id": "cloud", "label": "Cloud", "kind": "qdrant",
                                         "config": {"url": "http://127.0.0.1:1"}, "secrets": {"api_key": "s3cret"}})
    assert r.status_code == 200 and r.json()["secrets_set"] == {"api_key": True} and "s3cret" not in r.text
    assert api.put("/api/rag/stores/cloud", json={"label": "Renamed", "secrets": {"api_key": ""}}).json()["secrets_set"] == {"api_key": False}
    health = api.post("/api/rag/stores/cloud/test").json()
    assert health["ok"] is False and "cannot reach Qdrant" in health["detail"]
    assert api.post("/api/rag/stores/local/test").json()["ok"] is True
    assert api.delete("/api/rag/stores/cloud").json() == {"ok": True}
    assert [s["id"] for s in api.get("/api/rag/stores").json()] == ["local"]


def test_store_errors_map_to_404_and_422(api):
    assert api.post("/api/rag/stores", json={"id": "x1", "kind": "nope"}).status_code == 422
    assert api.post("/api/rag/stores", json={"id": "local", "kind": "local"}).status_code == 422
    assert api.put("/api/rag/stores/nope", json={"label": "x"}).status_code == 404
    assert api.delete("/api/rag/stores/nope").status_code == 404
    assert api.delete("/api/rag/stores/local").status_code == 422
    assert api.post("/api/rag/stores/nope/test").status_code == 404


# ------------------------------------------------------------------- stacks
@pytest.fixture
def docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(stack_mod, "_docker", fake)
    return fake


def test_docker_stack_lifecycle_over_http(api, docker):
    assert api.get("/api/rag/stacks").json()[0]["state"] == "missing"
    assert api.post("/api/rag/stacks/qdrant/start").json()["state"] == "running"
    assert api.post("/api/rag/stacks/qdrant/stop").json()["state"] == "stopped"
    assert api.delete("/api/rag/stacks/qdrant?delete_data=true").json()["state"] == "missing"
    assert ["volume", "rm", "-f", "agent-studio-qdrant"] in docker.calls


def test_docker_failures_are_409(api, docker):
    docker.fail = {"run"}
    r = api.post("/api/rag/stacks/qdrant/start")
    assert r.status_code == 409 and "port is already allocated" in r.json()["detail"]


def test_registering_a_stack_adds_one_store_pointing_at_it(api, docker):
    first = api.post("/api/rag/stacks/qdrant/register")
    assert first.status_code == 200 and first.json()["id"] == "docker-qdrant" and first.json()["config"] == {"url": "http://127.0.0.1:6333"}
    assert api.post("/api/rag/stacks/qdrant/register").json()["id"] == "docker-qdrant"
    assert len(api.get("/api/rag/stores").json()) == 2                       # not added twice
    assert api.post("/api/rag/stacks/nope/register").status_code == 404


# --------------------------------------------------------- embedding models
def test_embedding_models_for_a_provider(api):
    r = api.get("/api/rag/embedding-models/openai").json()
    assert r["kind"] == "openai" and any(m["model"] == "text-embedding-3-small" for m in r["models"])
    assert api.get("/api/rag/embedding-models/nope").status_code == 404


# ----------------------------------------------------------- knowledge bases
def test_knowledge_base_lifecycle(api):
    kb = make_kb(api, description="notes")
    assert kb["dim"] == 32 and api.get("/api/rag/kbs").json() == [kb] and api.get("/api/rag/kbs/docs").json() == kb
    assert api.put("/api/rag/kbs/docs", json={"label": "Renamed"}).json()["label"] == "Renamed"
    assert api.delete("/api/rag/kbs/docs").json() == {"ok": True}
    assert api.get("/api/rag/kbs").json() == []


def test_knowledge_base_errors(api):
    assert api.post("/api/rag/kbs", json={"id": "Bad Id", "provider": "mock", "model": "m"}).status_code == 422
    assert api.get("/api/rag/kbs/nope").status_code == 404
    assert api.put("/api/rag/kbs/nope", json={}).status_code == 404
    assert api.delete("/api/rag/kbs/nope").status_code == 404
    assert api.get("/api/rag/kbs/nope/docs").status_code == 404
    assert api.post("/api/rag/search", json={"query": "x", "kbs": ["nope"]}).status_code == 404


def test_a_spend_cap_is_a_402_and_provider_trouble_is_a_502(api, state):
    class Capped:
        async def embed(self, *a):
            raise BudgetExceeded({"message": "cap reached", "reason": "cap_exceeded"})

    class Down:
        async def embed(self, *a):
            raise RuntimeError("connection reset")
    state.rag.embedder = Capped()
    r = api.post("/api/rag/kbs", json={"id": "docs", "provider": "mock", "model": "m"})
    assert r.status_code == 402 and r.json()["detail"]["reason"] == "cap_exceeded"
    state.rag.embedder = Down()
    r = api.post("/api/rag/kbs", json={"id": "docs", "provider": "mock", "model": "m"})
    assert r.status_code == 502 and r.json()["detail"]


# ----------------------------------------------------------------- documents
def test_uploading_files_reports_each_outcome(api):
    make_kb(api)
    r = upload(api, "docs", ("guide.md", b"# Guide\n\nReset your password here.", "text/markdown"),
               ("tool.exe", b"MZ", "application/octet-stream"), ("again.txt", b"hello", "text/plain"))
    out = r.json()
    assert [o["status"] for o in out] == ["added", "skipped", "added"]
    assert "unsupported file type" in out[1]["error"] and out[0]["name"] == "guide.md"
    assert upload(api, "docs", ("guide.md", b"# Guide\n\nReset your password here.", "text/markdown")).json()[0]["status"] == "unchanged"
    assert {d["source"] for d in api.get("/api/rag/kbs/docs/docs").json()} == {"guide.md", "again.txt"}


def test_a_nameless_upload_is_rejected_and_unknown_kbs_stop_the_upload(api):
    make_kb(api)
    assert upload(api, "docs", ("", b"hello there", "text/plain")).status_code == 422          # FastAPI refuses it first
    assert upload(api, "nope", ("a.txt", b"x", "text/plain")).status_code == 404


def test_adding_text_and_removing_a_document(api):
    make_kb(api)
    r = api.post("/api/rag/kbs/docs/text", json={"title": "Standup", "text": "We ship on Friday."})
    assert r.status_code == 200 and r.json()["status"] == "added"
    assert api.post("/api/rag/kbs/docs/text", json={"title": " ", "text": "x"}).status_code == 422
    doc_id = r.json()["doc_id"]
    assert api.delete(f"/api/rag/kbs/docs/docs/{doc_id}").json() == {"ok": True, "removed": 1}
    assert api.delete(f"/api/rag/kbs/docs/docs/{doc_id}").status_code == 404


@pytest.fixture
def workspace(api, tmp_path):
    ws = tmp_path / "ws"
    (ws / "notes").mkdir(parents=True)
    (ws / "node_modules" / "pkg").mkdir(parents=True)
    (ws / "notes" / "a.md").write_text("Alpha notes about billing.", encoding="utf-8")
    (ws / "notes" / "b.txt").write_text("Bravo notes about shipping.", encoding="utf-8")
    (ws / "notes" / "image.bin").write_bytes(b"\x00\x01")
    (ws / "node_modules" / "pkg" / "x.md").write_text("junk", encoding="utf-8")
    (ws / "top.txt").write_text("Top level file.", encoding="utf-8")
    api.put("/api/settings", json={"workspace_dir": str(ws)})
    return ws


def test_ingesting_a_workspace_folder_skips_junk_and_unsupported_files(api, workspace):
    make_kb(api)
    out = api.post("/api/rag/kbs/docs/path", json={"path": "."}).json()
    assert sorted(o["name"] for o in out) == ["notes/a.md", "notes/b.txt", "top.txt"] and {o["status"] for o in out} == {"added"}
    assert api.post("/api/rag/kbs/docs/path", json={"path": "notes"}).json()[0]["status"] == "unchanged"


def test_ingesting_a_single_workspace_file_and_its_failures(api, workspace):
    make_kb(api)
    assert api.post("/api/rag/kbs/docs/path", json={"path": "top.txt"}).json()[0]["name"] == "top.txt"
    bad = api.post("/api/rag/kbs/docs/path", json={"path": "notes/image.bin"}).json()
    assert bad[0]["status"] == "skipped" and "unsupported" in bad[0]["error"]
    assert api.post("/api/rag/kbs/docs/path", json={"path": "../outside"}).status_code == 422
    assert api.post("/api/rag/kbs/docs/path", json={"path": "missing"}).status_code == 422
    assert api.post("/api/rag/kbs/nope/path", json={"path": "top.txt"}).status_code == 404


def test_folder_ingestion_is_capped(api, workspace, monkeypatch):
    make_kb(api)
    monkeypatch.setattr(RA, "MAX_FOLDER_FILES", 2)
    assert len(api.post("/api/rag/kbs/docs/path", json={"path": "notes"}).json()) == 2


# -------------------------------------------------------------------- search
def test_search_over_http(api):
    make_kb(api)
    api.post("/api/rag/kbs/docs/text", json={"title": "Billing", "text": "Refunds and invoices are handled by billing."})
    api.post("/api/rag/kbs/docs/text", json={"title": "Shipping", "text": "Parcels are tracked until delivery."})
    hits = api.post("/api/rag/search", json={"query": "refunds invoices", "kbs": ["docs"], "k": 1}).json()
    assert len(hits) == 1 and hits[0]["source"] == "Billing" and hits[0]["kb"] == "docs"
    assert api.post("/api/rag/search", json={"query": "  ", "kbs": ["docs"]}).status_code == 422
