"""Models are loaded from each provider's API (no typing names); the mock provider only exists for tests."""
import pytest

from app import models as M
from app.db import Database
from app.providers import ProviderStore


@pytest.fixture(autouse=True)
def _fresh_cache():
    M._cache.clear()
    yield
    M._cache.clear()


@pytest.fixture
def fake_http(monkeypatch):
    calls = []
    replies = {}

    async def get_json(url, headers=None, params=None):
        calls.append((url, headers or {}, params or {}))
        for k, v in replies.items():
            if k in url:
                if isinstance(v, Exception):
                    raise v
                return v
        raise RuntimeError("HTTP 404: no fake for " + url)

    monkeypatch.setattr(M, "_get_json", get_json)
    return calls, replies


def ids(client, pid, **q):
    r = client.get(f"/api/providers/{pid}/models", params=q).json()
    return r, [m["id"] for m in r["models"]]


def test_openai_live_list_filters_non_chat_models(client, fake_http):
    calls, replies = fake_http
    replies["api.openai.com/v1/models"] = {"data": [{"id": i} for i in ("gpt-5", "gpt-5-mini", "o4-mini", "text-embedding-3-large", "whisper-1", "dall-e-3", "gpt-4o-realtime-preview")]}
    client.put("/api/providers/openai", json={"api_key": "sk-test-123456789", "organization": "org-9"})
    r, got = ids(client, "openai")
    assert r["source"] == "live" and got == ["gpt-5", "gpt-5-mini", "o4-mini"]
    assert calls[0][1]["Authorization"] == "Bearer sk-test-123456789" and calls[0][1]["OpenAI-Organization"] == "org-9"
    assert next(m for m in r["models"] if m["id"] == "gpt-5")["input_per_m"] is not None       # prices attached when known
    ids(client, "openai")
    assert len(calls) == 1                                                                         # cached
    ids(client, "openai", refresh="true")
    assert len(calls) == 2


def test_anthropic_gemini_vertex_express_ollama_openrouter(client, fake_http):
    calls, replies = fake_http
    replies["api.anthropic.com/v1/models"] = {"data": [{"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5"}]}
    replies["generativelanguage.googleapis.com"] = {"models": [
        {"name": "models/gemini-3.8-flash", "displayName": "Gemini 3.8 Flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]}]}
    replies["aiplatform.googleapis.com/v1beta1/publishers/google/models"] = {"publisherModels": [
        {"name": "publishers/google/models/gemini-3.1-pro-preview"}, {"name": "publishers/google/models/imagen-4"}]}
    replies["127.0.0.1:11434/api/tags"] = {"models": [{"name": "llama3.2:latest"}]}
    replies["openrouter.ai/api/v1/models"] = {"data": [{"id": "x/y", "name": "X Y", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]}
    client.put("/api/providers/anthropic", json={"api_key": "sk-ant-123456789"})
    r, got = ids(client, "anthropic")
    assert got == ["claude-opus-5-5"] and r["models"][0]["label"] == "Claude Opus 5.5"
    client.put("/api/providers/gemini", json={"api_key": "AIza-test-123456"})
    assert ids(client, "gemini")[1] == ["gemini-3.8-flash"]
    client.put("/api/providers/vertex_ai", json={"auth_mode": "api_key", "api_key": "AQ.test-123456789"})
    assert ids(client, "vertex_ai")[1] == ["gemini-3.1-pro-preview"]
    assert any(h.get("x-goog-api-key") == "AQ.test-123456789" for _, h, _ in calls)
    assert ids(client, "ollama")[1] == ["llama3.2:latest"]
    r, got = ids(client, "openrouter")
    assert got == ["x/y"] and r["models"][0]["input_per_m"] == pytest.approx(1.0)


def test_falls_back_to_catalog_and_says_why(client, fake_http):
    r, got = ids(client, "openai")                                                  # no key configured
    assert r["source"] == "catalog" and "API key not set" in r["error"] and len(got) > 5 and "gpt-5" in got
    assert not any("embedding" in g for g in got)
    _, replies = fake_http
    replies["api.x.ai"] = RuntimeError("HTTP 401: bad key")
    client.put("/api/providers/xai", json={"api_key": "xai-wrong-123456"})
    r, _ = ids(client, "xai")
    assert r["source"] == "catalog" and "401" in r["error"]
    assert client.get("/api/providers/nope/models").status_code == 404


def test_mock_provider_is_hidden_outside_tests(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    ProviderStore(db)
    assert db.one("SELECT 1 FROM providers WHERE id='mock'") is not None          # tests enable it
    monkeypatch.delenv("AGENT_STUDIO_ENABLE_MOCK")
    st = ProviderStore(db)                                                          # a normal start removes it
    assert db.one("SELECT 1 FROM providers WHERE id='mock'") is None and "mock" not in [p.id for p in st.list()]
