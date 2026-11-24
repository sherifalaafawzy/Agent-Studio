"""The language-model leg of live mode: a token stream with the lowest overhead each provider allows.

* **Ollama** is called directly (`/api/chat`, streamed) over one kept-alive connection, with `think: false` (a thinking model otherwise
  spends seconds on hidden reasoning before the first spoken word - measured 7 s with no output on qwen3:8b) and `keep_alive` so the
  model stays in memory (a cold load measured 15 s). The context size is the provider's own, so the model is shared with normal chat
  and Ollama does not reload it.
* **Every other provider** goes through LiteLLM's streaming call, under the same spending caps as chat (reserve before, settle after).

Both expose `warm()` and `stream(messages, max_tokens)`, an async iterator of text pieces.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

KEEP_ALIVE = "30m"
DEFAULT_MAX_TOKENS = 300


class LlmError(Exception):
    """The model could not be reached or answered with an error. The message is safe to show."""


class OllamaLlm:
    def __init__(self, base: str, model: str, num_ctx: int, client: httpx.AsyncClient | None = None):
        self.base = (base or "http://127.0.0.1:11434").rstrip("/")
        self.model, self.num_ctx = model, num_ctx
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0))
        self.cost_usd = 0.0
        self.tokens_in = self.tokens_out = 0                      # cumulative, from Ollama's own counts
        self.local = True

    def _body(self, messages: list[dict], max_tokens: int, think: bool | None) -> dict:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "stream": True, "keep_alive": KEEP_ALIVE,
                                "options": {"num_ctx": self.num_ctx, "num_predict": max_tokens, "temperature": 0.7}}
        if think is not None:
            body["think"] = think
        return body

    async def warm(self) -> None:
        """Load the model into memory now (an empty chat request does only that) so the first turn is not a cold start."""
        try:
            r = await self.client.post(f"{self.base}/api/chat", json={"model": self.model, "messages": [], "keep_alive": KEEP_ALIVE,
                                                                       "options": {"num_ctx": self.num_ctx}}, timeout=300)
        except httpx.HTTPError as e:
            raise LlmError(f"Ollama is not reachable at {self.base}: {e}") from e
        if r.status_code >= 400:
            raise LlmError(_ollama_error(r.text, r.status_code, self.model))
        try:                                     # one-token generation: the first real prompt would otherwise pay for the model's first pass (about 2 s measured)
            await self.client.post(f"{self.base}/api/chat", json={**self._body([{"role": "user", "content": "Hi"}], 1, False), "stream": False}, timeout=120)
        except httpx.HTTPError:
            pass                                 # priming is a bonus; the real request will report a real problem

    async def stream(self, messages: list[dict], max_tokens: int = DEFAULT_MAX_TOKENS) -> AsyncIterator[str]:
        think: bool | None = False
        for attempt in (0, 1):
            try:
                async with self.client.stream("POST", f"{self.base}/api/chat", json=self._body(messages, max_tokens, think)) as r:
                    if r.status_code >= 400:
                        text = (await r.aread()).decode("utf-8", "replace")
                        if attempt == 0 and "think" in text.lower():       # a model that does not know the `think` option: ask without it
                            think = None
                            continue
                        raise LlmError(_ollama_error(text, r.status_code, self.model))
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        d = json.loads(line)
                        if d.get("error"):
                            raise LlmError(str(d["error"])[:300])
                        piece = (d.get("message") or {}).get("content") or ""
                        if piece:
                            yield piece
                        if d.get("done"):
                            self.tokens_in += int(d.get("prompt_eval_count") or 0)
                            self.tokens_out += int(d.get("eval_count") or 0)
                            return
                    return
            except httpx.HTTPError as e:
                raise LlmError(f"Ollama is not reachable at {self.base}: {e}") from e


def _ollama_error(text: str, status: int, model: str) -> str:
    try:
        msg = json.loads(text).get("error") or text
    except ValueError:
        msg = text
    msg = str(msg)[:300]
    if status == 404 or "not found" in msg.lower():
        return f"The model '{model}' is not on that Ollama server (pull it first, or choose another model). {msg}"
    return f"Ollama answered {status}: {msg}"


class LiteLlm:
    """Any other provider, through LiteLLM, charged against the provider's cap like a chat message."""

    def __init__(self, provider_id: str, model: str, target: str, kwargs: dict, guard: Any, local: bool = False, completion: Any = None):
        self.provider_id, self.model, self.target, self.kwargs, self.guard, self.local = provider_id, model, target, kwargs, guard, local
        self._completion = completion
        self.cost_usd = 0.0
        self.tokens_in = self.tokens_out = 0                      # cumulative estimates (about 4 characters per token)

    async def warm(self) -> None:
        return None

    async def stream(self, messages: list[dict], max_tokens: int = DEFAULT_MAX_TOKENS) -> AsyncIterator[str]:
        completion = self._completion
        if completion is None:  # pragma: no cover - the real LiteLLM import
            import litellm
            completion = litellm.acompletion
        est_in = sum(len(str(m.get("content", ""))) for m in messages) // 4 + 20
        try:
            r = self.guard.reserve(self.provider_id, self.model, est_in, max_tokens, self.target)
        except Exception as e:  # noqa: BLE001 - BudgetExceeded: the message says which cap
            raise LlmError(str(e)) from e
        out_chars = 0
        try:
            resp = await completion(model=self.target, messages=messages, stream=True, max_tokens=max_tokens, **self.kwargs)
            async for chunk in resp:
                piece = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                if piece:
                    out_chars += len(piece)
                    yield piece
        except LlmError:
            raise
        except GeneratorExit:
            self.cost_usd += self.guard.settle(r, est_in, out_chars // 4 + 1, note="live_voice")
            self.tokens_in, self.tokens_out = self.tokens_in + est_in, self.tokens_out + out_chars // 4 + 1
            raise
        except Exception as e:  # noqa: BLE001
            self.guard.release(r, note="live_failed")
            raise LlmError(f"{type(e).__name__}: {str(e)[:300]}") from e
        else:
            self.cost_usd += self.guard.settle(r, est_in, out_chars // 4 + 1, note="live_voice")
            self.tokens_in, self.tokens_out = self.tokens_in + est_in, self.tokens_out + out_chars // 4 + 1


class AgentLlm:
    """The 'model' of an agent profile with `agent_turns`: every reply is a FULL Agent Studio turn (the agent's tools, knowledge, memory, guardrails), spoken once it is
    complete. Slower than streaming a model, but the agent can do things. `run(text)` is a chat turn returning the usual result dict."""

    def __init__(self, run: Any, local: bool = False, chunk_words: int = 6):
        self.run, self.local, self.chunk_words = run, local, chunk_words
        self.cost_usd = 0.0
        self.tokens_in = self.tokens_out = 0

    async def warm(self) -> None:
        return None

    async def stream(self, messages: list[dict], max_tokens: int = DEFAULT_MAX_TOKENS) -> AsyncIterator[str]:
        question = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        try:
            res = await self.run(question)
        except Exception as e:  # noqa: BLE001
            raise LlmError(f"The agent could not answer: {type(e).__name__}: {str(e)[:300]}") from e
        usage = res.get("usage") or {}
        self.cost_usd += float(usage.get("cost_usd") or 0)
        self.tokens_in += int(usage.get("input_tokens") or 0)
        self.tokens_out += int(usage.get("output_tokens") or 0)
        words = (res.get("reply") or "").split(" ")
        for i in range(0, len(words), self.chunk_words):                      # hand the finished answer over a few words at a time, as a stream would
            yield " ".join(words[i:i + self.chunk_words]) + (" " if i + self.chunk_words < len(words) else "")


def make_llm(providers: Any, guard: Any, provider_id: str, model: str | None, completion: Any = None) -> OllamaLlm | LiteLlm:
    """The right streamer for a provider id + model. Raises LlmError when the provider is unknown or misconfigured."""
    from .providers import litellm_target
    p = providers.get(provider_id)
    if p is None:
        raise LlmError(f"unknown provider '{provider_id}'")
    chosen = model or p.default_model
    try:
        target, kw = litellm_target(p, chosen)
    except ValueError as e:
        raise LlmError(str(e)) from e
    if p.kind == "ollama":
        return OllamaLlm(kw.get("api_base") or "", chosen or "", int(kw.get("num_ctx") or 16384))
    return LiteLlm(p.id, chosen or "", target, kw, guard, local=False, completion=completion)
