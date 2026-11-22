"""Embeddings: provider checks, batching, and spend-guard accounting."""
import asyncio

import litellm
import pytest

from app.budget import BudgetExceeded
from app.rag import embeddings as E
from tests.rag_fakes import embedding_response


@pytest.fixture
def emb(state):
    state.providers.update("mock", {"enabled": True, "cap_usd": 1000})
    return E.Embedder(state.providers, state.guard)


def _patch(monkeypatch, handler):
    calls = []

    async def fake(model, input, **kw):
        calls.append((model, list(input), kw))
        return handler(list(input))
    monkeypatch.setattr(litellm, "aembedding", fake)
    return calls


def ledger(state):
    return [dict(r) for r in state.db.query("SELECT * FROM ledger ORDER BY id")]


def test_vectors_come_back_in_order_and_usage_is_charged(emb, state, monkeypatch):
    calls = _patch(monkeypatch, lambda texts: embedding_response(texts, shuffle=True))
    vecs = asyncio.run(emb.embed("mock", "mock-cheap", ["alpha one", "beta two", "gamma"]))
    assert len(vecs) == 3 and vecs[0] != vecs[1]
    assert all(isinstance(x, float) for x in vecs[0])                           # plain floats, ready for any store
    assert len(calls) == 1 and calls[0][1] == ["alpha one", "beta two", "gamma"]
    row = ledger(state)[-1]
    assert row["kind"] == "call" and row["note"] == "embedding" and row["input_tokens"] == 5 and row["output_tokens"] == 0
    assert row["settled_from"] == "usage"


def test_nothing_to_embed_costs_nothing(emb, state, monkeypatch):
    calls = _patch(monkeypatch, lambda texts: embedding_response(texts))
    assert asyncio.run(emb.embed("mock", "mock-cheap", [])) == []
    assert calls == [] and ledger(state) == []


def test_big_inputs_are_sent_in_batches(emb, state, monkeypatch):
    calls = _patch(monkeypatch, lambda texts: embedding_response(texts))
    texts = [f"text {i}" for i in range(E.BATCH * 2 + 2)]
    vecs = asyncio.run(emb.embed("mock", "mock-cheap", texts))
    assert len(vecs) == len(texts) and [len(c[1]) for c in calls] == [E.BATCH, E.BATCH, 2]
    assert len([r for r in ledger(state) if r["kind"] == "call"]) == 3


def test_missing_usage_is_charged_at_the_reservation(emb, state, monkeypatch):
    _patch(monkeypatch, lambda texts: embedding_response(texts, usage=False))
    asyncio.run(emb.embed("mock", "mock-cheap", ["one two three"]))
    assert ledger(state)[-1]["settled_from"] == "reservation"


def test_unknown_and_disabled_providers_are_refused(emb, state):
    with pytest.raises(E.EmbeddingError, match="unknown provider 'nope'"):
        asyncio.run(emb.embed("nope", "m", ["x"]))
    state.providers.update("mock", {"enabled": False})
    with pytest.raises(E.EmbeddingError, match="switched off"):
        asyncio.run(emb.embed("mock", "mock-cheap", ["x"]))


def test_a_provider_without_credentials_is_refused(state):
    state.providers.update("openai", {"enabled": True})                  # no API key stored
    with pytest.raises(E.EmbeddingError, match="API key"):
        asyncio.run(E.Embedder(state.providers, state.guard).embed("openai", "text-embedding-3-small", ["x"]))


def test_a_failed_call_releases_the_reservation_and_reraises(emb, state, monkeypatch):
    async def boom(model, input, **kw):
        raise RuntimeError("provider down")
    monkeypatch.setattr(litellm, "aembedding", boom)
    with pytest.raises(RuntimeError, match="provider down"):
        asyncio.run(emb.embed("mock", "mock-cheap", ["x"]))
    assert state.db.query("SELECT * FROM reservations") == []
    assert ledger(state)[-1]["note"] == "embedding_failed"


@pytest.mark.parametrize("response", [
    lambda t: {"data": [], "usage": {"prompt_tokens": 1}},                                                 # wrong count
    lambda t: {"data": [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1.0, 2.0]}][:len(t)]},   # mixed sizes
    lambda t: {"data": [{"index": i, "embedding": []} for i in range(len(t))]},                              # empty vectors
])
def test_a_response_that_is_not_embeddings_is_rejected(emb, state, monkeypatch, response):
    _patch(monkeypatch, response)
    with pytest.raises(E.EmbeddingError, match="is it an embedding model"):
        asyncio.run(emb.embed("mock", "mock-cheap", ["a", "b"]))
    assert state.db.query("SELECT * FROM reservations") == []
    assert ledger(state)[-1]["note"] == "embedding_bad_response"


def test_the_spend_cap_stops_embedding_before_anything_is_sent(emb, state, monkeypatch):
    calls = _patch(monkeypatch, lambda texts: embedding_response(texts))
    state.providers.update("mock", {"cap_usd": 0.0000001})
    with pytest.raises(BudgetExceeded):
        asyncio.run(emb.embed("mock", "mock-cheap", ["a long text " * 200]))
    assert calls == []


def test_object_shaped_responses_work_too(emb, monkeypatch):
    from types import SimpleNamespace as NS
    resp = NS(data=[NS(index=0, embedding=[0.5, 0.5])], usage=NS(prompt_tokens=2))
    _patch(monkeypatch, lambda texts: resp)
    assert asyncio.run(emb.embed("mock", "mock-cheap", ["x"])) == [[0.5, 0.5]]


# ----------------------------------------------------------- model catalogue
def test_catalogue_lists_embedding_models_per_provider():
    openai = {m["model"]: m for m in E.embedding_models("openai")}
    assert openai["text-embedding-3-small"]["dim"] == 1536 and openai["text-embedding-3-small"]["input_per_m"] > 0
    assert all("/" not in name for kind in ("openai", "gemini", "vertex_ai", "bedrock") for name in
               [m["model"] for m in E.embedding_models(kind)])
    assert [m["model"] for m in E.embedding_models("gemini")] == sorted(m["model"] for m in E.embedding_models("gemini"))
    assert any(m["model"].startswith("gemini-embedding") for m in E.embedding_models("gemini"))
    assert any(m["model"].startswith("gemini-embedding") for m in E.embedding_models("vertex_ai"))


def test_ollama_suggestions_are_free_and_unknown_kinds_are_empty():
    names = [m["model"] for m in E.embedding_models("ollama")]
    assert "nomic-embed-text" in names and all(m["input_per_m"] == 0.0 for m in E.embedding_models("ollama"))
    assert E.embedding_models("anthropic") == []


def test_embedding_models_are_priced_even_without_an_output_price(monkeypatch):
    """LiteLLM omits the output price for some embedding models; the guard must not treat them as unknown and expensive."""
    from app.pricing import price_for
    monkeypatch.setitem(litellm.model_cost, "unit/embed-x", {"mode": "embedding", "input_cost_per_token": 2e-7})
    p = price_for("openai", "unit/embed-x", "unit/embed-x")
    assert p.known and p.input_per_m == pytest.approx(0.2) and p.output_per_m == 0
    monkeypatch.setitem(litellm.model_cost, "unit/chat-x", {"mode": "chat", "input_cost_per_token": 2e-7})
    assert not price_for("openai", "unit/chat-x", "unit/chat-x").known                     # chat models still need both prices
