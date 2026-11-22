"""Ollama: only installed models are offered, models can be downloaded from the UI, missing models get a clear error."""
import json

import httpx
import pytest

from app import models as M


def _patch_completion(monkeypatch, fn):
    """Patch LiteLLM's completion AND ADK's lazily-bound copy of it (ADK binds it on first use)."""
    import litellm
    import google.adk.models.lite_llm as adk_lite
    monkeypatch.setattr(litellm, "acompletion", fn)
    monkeypatch.setattr(adk_lite, "acompletion", fn, raising=False)


@pytest.fixture(autouse=True)
def _fresh():
    M._cache.clear()
    yield
    M._cache.clear()


def test_no_installed_models_means_an_empty_list_not_catalog_names(client, monkeypatch):
    async def tags(url, headers=None, params=None):
        return {"models": []}
    monkeypatch.setattr(M, "_get_json", tags)
    r = client.get("/api/providers/ollama/models").json()
    assert r["models"] == [] and "No models are installed" in r["error"]


def test_unreachable_ollama_is_explained(client, monkeypatch):
    async def down(url, headers=None, params=None):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(M, "_get_json", down)
    r = client.get("/api/providers/ollama/models").json()
    assert r["models"] == [] and "isn't reachable" in r["error"]


def test_pull_streams_progress_and_refreshes_the_list(client, monkeypatch):
    import app.main as main_mod
    lines = [{"status": "pulling manifest"}, {"status": "downloading", "completed": 50, "total": 100},
             {"status": "downloading", "completed": 100, "total": 100}, {"status": "success"}]

    def handler(req):
        assert req.url.path == "/api/pull" and json.loads(req.content)["model"] == "qwen2.5-coder:7b"
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
    real = httpx.AsyncClient
    monkeypatch.setattr(main_mod.httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}))
    M._cache["x"] = (1e18, {"provider": "ollama", "models": []})
    with client.stream("POST", "/api/providers/ollama/pull", json={"model": "qwen2.5-coder:7b"}) as r:
        evs = [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]
    assert evs[1]["completed"] == 50 and evs[-1] == {"type": "done", "model": "qwen2.5-coder:7b"} and "x" not in M._cache
    assert client.post("/api/providers/ollama/pull", json={"model": "bad name; rm"}).status_code == 422
    assert client.post("/api/providers/openai/pull", json={"model": "x"}).status_code == 404
    assert client.get("/api/providers/ollama/ollama/suggestions").json()[0]["id"] == "qwen2.5-coder:7b"


def test_missing_model_error_is_actionable(client, monkeypatch):
    import litellm
    client.put("/api/providers/ollama", json={"enabled": True})

    async def nf(*a, **k):
        raise litellm.NotFoundError("Ollama_chatException - {\"error\":\"model 'mistral-7B-Instruct-v0.1' not found\"}", model="x", llm_provider="ollama")
    _patch_completion(monkeypatch, nf)
    with client.stream("POST", "/api/chat/stream", json={"provider": "ollama", "message": "hi", "model": "mistral-7B-Instruct-v0.1"}) as r:
        evs = [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]
    err = next(e for e in evs if e["type"] == "error")["message"]
    assert err.startswith("Ollama model not installed") and "Download it" in err


def test_out_of_memory_error_is_actionable(client, monkeypatch):
    import litellm
    client.put("/api/providers/ollama", json={"enabled": True, "default_model": "qwen3:8b"})

    async def oom(*a, **k):
        assert k.get("num_ctx") == 16384                       # the fixed context window reaches Ollama
        raise litellm.InternalServerError('Ollama_chatException - {"error":"llama-server process has terminated: exit status 1: '
                                          'llama_init_from_model: failed to initialize the context: std::bad_alloc"}', model="x", llm_provider="ollama")
    _patch_completion(monkeypatch, oom)
    with client.stream("POST", "/api/chat/stream", json={"provider": "ollama", "message": "hi"}) as r:
        evs = [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]
    err = next(e for e in evs if e["type"] == "error")["message"]
    assert err.startswith("Ollama ran out of memory") and "Lower the context window" in err
