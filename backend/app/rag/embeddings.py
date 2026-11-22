"""Embeddings through your own providers (LiteLLM), under the same hard spend caps as chat."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import litellm

from ..budget import BudgetGuard
from ..providers import ProviderStore, litellm_target

BATCH = 64
CHARS_PER_TOKEN_EST = 3          # deliberately pessimistic, so the reservation covers the real cost

# provider kind -> the `litellm_provider` names LiteLLM uses for its embedding models
_LITELLM_PROVIDERS: dict[str, set[str]] = {
    "vertex_ai": {"vertex_ai-embedding-models", "vertex_ai"},
    "gemini": {"gemini"},
    "openai": {"openai", "text-completion-openai"},
    "bedrock": {"bedrock"},
    "openrouter": {"openrouter"},
    "xai": {"xai"},
}
_PREFIXES = {"vertex_ai", "gemini", "bedrock", "openrouter", "xai", "openai"}
# Local models worth suggesting for Ollama (LiteLLM's cost map has no entries for them).
OLLAMA_SUGGESTED = ["nomic-embed-text", "mxbai-embed-large", "bge-m3", "all-minilm"]


class EmbeddingError(Exception):
    """Embedding could not run: unknown or disabled provider, bad model, inconsistent vectors."""


def embedding_models(kind: str) -> list[dict]:
    """Embedding models a provider kind offers: [{model, dim, input_per_m}] (dim and price may be None)."""
    if kind == "ollama":
        return [{"model": m, "dim": None, "input_per_m": 0.0} for m in OLLAMA_SUGGESTED]
    names = _LITELLM_PROVIDERS.get(kind)
    if not names:
        return []
    out: dict[str, dict] = {}
    for key, v in litellm.model_cost.items():
        if v.get("mode") != "embedding" or v.get("litellm_provider") not in names:
            continue
        head, _, rest = key.partition("/")
        bare = rest if rest and head in _PREFIXES else key
        if "/" in bare:                                  # region- or deployment-specific entries
            continue
        cost = v.get("input_cost_per_token")
        out.setdefault(bare, {"model": bare, "dim": v.get("output_vector_size"),
                              "input_per_m": None if cost is None else cost * 1e6})
    return sorted(out.values(), key=lambda m: m["model"])


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


class Embedder:
    def __init__(self, providers: ProviderStore, guard: BudgetGuard):
        self.providers, self.guard = providers, guard

    async def embed(self, provider_id: str, model: str, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in order. BudgetExceeded propagates (nothing is sent when the cap would be crossed)."""
        if not texts:
            return []
        p = self.providers.get(provider_id)
        if p is None:
            raise EmbeddingError(f"unknown provider '{provider_id}'")
        if not p.enabled:
            raise EmbeddingError(f"provider '{provider_id}' is switched off - enable it in Settings -> Model providers")
        try:
            target, kw = litellm_target(p, model)
        except ValueError as e:
            raise EmbeddingError(str(e)) from e
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            batch = list(texts[i:i + BATCH])
            est = sum(len(t) for t in batch) // CHARS_PER_TOKEN_EST + len(batch)
            res = self.guard.reserve(provider_id, model, est, 0, target)
            try:
                resp = await litellm.aembedding(model=target, input=batch, **kw)
            except Exception:
                self.guard.release(res, "embedding_failed")
                raise
            rows = sorted(_get(resp, "data") or [], key=lambda d: _get(d, "index", 0))
            vecs = [[float(x) for x in _get(r, "embedding")] for r in rows]
            if len(vecs) != len(batch) or len({len(v) for v in vecs}) != 1 or not vecs[0]:
                self.guard.release(res, "embedding_bad_response")
                raise EmbeddingError(f"{provider_id}/{model} returned {len(vecs)} vectors for {len(batch)} texts - is it an embedding model?")
            used = _get(_get(resp, "usage"), "prompt_tokens")
            self.guard.settle(res, used, 0 if used is not None else None, note="embedding")
            out.extend(vecs)
        return out
