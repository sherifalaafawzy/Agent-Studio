"""Live mode's model leg: direct Ollama streaming and the LiteLLM path under the spending caps."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app import live_llm as LL


def ollama(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LL.OllamaLlm("http://ollama.test/", "qwen3:8b", 16384, client=client)


def ndjson(*objs):
    return "\n".join(json.dumps(o) for o in objs) + "\n"


async def collect(llm, messages=None, **kw):
    return [p async for p in llm.stream(messages or [{"role": "user", "content": "hi"}], **kw)]


# ---------------------------------------------------------------- Ollama
@pytest.mark.asyncio
async def test_warm_loads_the_model_then_primes_it_with_one_token():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={})
    await ollama(handler).warm()
    load, prime = seen
    assert load["messages"] == [] and load["keep_alive"] == "30m" and load["options"] == {"num_ctx": 16384}
    assert prime["options"]["num_predict"] == 1 and prime["think"] is False and prime["stream"] is False and prime["messages"][0]["content"] == "Hi"


@pytest.mark.asyncio
async def test_warm_reports_a_missing_model_or_server_and_ignores_a_failed_priming():
    with pytest.raises(LL.LlmError, match="not on that Ollama server"):
        await ollama(lambda r: httpx.Response(404, json={"error": "model 'qwen3:8b' not found"})).warm()
    with pytest.raises(LL.LlmError, match="not reachable at http://ollama.test"):
        def refuse(req):
            raise httpx.ConnectError("refused", request=req)
        await ollama(refuse).warm()
    n = []

    def second_fails(req):
        n.append(1)
        if len(n) == 2:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, json={})
    await ollama(second_fails).warm()                                      # the priming request failing is not an error
    assert len(n) == 2


@pytest.mark.asyncio
async def test_stream_yields_text_pieces_with_thinking_off():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, text=ndjson({"message": {"content": "Hel"}}, {"message": {"content": ""}}, {"message": {"content": "lo"}}, {"done": True}))
    assert await collect(ollama(handler), max_tokens=80) == ["Hel", "lo"]
    b = seen[0]
    assert b["think"] is False and b["stream"] is True and b["keep_alive"] == "30m" and b["options"]["num_predict"] == 80 and b["options"]["num_ctx"] == 16384


@pytest.mark.asyncio
async def test_stream_tolerates_blank_lines_and_a_stream_with_no_done_line():
    body = "\n" + ndjson({"message": {"content": "a"}}) + "\n"
    assert await collect(ollama(lambda r: httpx.Response(200, text=body))) == ["a"]


@pytest.mark.asyncio
async def test_a_model_that_does_not_know_the_think_option_is_asked_again_without_it():
    seen = []

    def handler(req):
        b = json.loads(req.content)
        seen.append(b.get("think", "absent"))
        if "think" in b:
            return httpx.Response(400, json={"error": "registry.ollama.ai/library/llama3 does not support think"})
        return httpx.Response(200, text=ndjson({"message": {"content": "ok"}}, {"done": True}))
    assert await collect(ollama(handler)) == ["ok"]
    assert seen == [False, "absent"]
    n = []

    def always_think(req):
        n.append(1)
        return httpx.Response(400, json={"error": "think is wrong"})
    with pytest.raises(LL.LlmError, match="answered 400"):
        await collect(ollama(always_think))
    assert len(n) == 2                                                     # retried once, then reported


@pytest.mark.asyncio
async def test_stream_errors_become_plain_messages():
    with pytest.raises(LL.LlmError, match="not on that Ollama server"):
        await collect(ollama(lambda r: httpx.Response(404, json={"error": "model not found"})))
    with pytest.raises(LL.LlmError, match="answered 500: boom"):
        await collect(ollama(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(LL.LlmError, match="out of memory"):
        await collect(ollama(lambda r: httpx.Response(200, text=ndjson({"error": "out of memory"}))))

    def refuse(req):
        raise httpx.ConnectError("refused", request=req)
    with pytest.raises(LL.LlmError, match="not reachable"):
        await collect(ollama(refuse))


def test_default_address_and_error_texts():
    assert LL.OllamaLlm("", "m", 4096).base == "http://127.0.0.1:11434" and LL.OllamaLlm("http://h:1/", "m", 1).base == "http://h:1"
    assert "answered 503: plain" in LL._ollama_error("plain", 503, "m")
    assert "answered 400: bad" in LL._ollama_error(json.dumps({"error": "bad"}), 400, "m")


# ---------------------------------------------------------------- LiteLLM
class Guard:
    def __init__(self, refuse=False):
        self.refuse, self.log = refuse, []

    def reserve(self, provider, model, est_in, max_out, target):
        if self.refuse:
            raise RuntimeError("daily cap reached")
        self.log.append(("reserve", provider, model, max_out, target))
        return "r1"

    def settle(self, r, tin, tout, note=None):
        self.log.append(("settle", tin, tout, note))
        return 0.002

    def release(self, r, note=None):
        self.log.append(("release", note))


def chunk(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


def fake_completion(pieces, fail_after=None):
    async def completion(**kw):
        completion.kw = kw

        async def gen():
            for i, p in enumerate(pieces):
                if fail_after is not None and i == fail_after:
                    raise ValueError("stream broke")
                yield chunk(p)
            yield SimpleNamespace(choices=[])                              # a usage-only chunk has no choices
        return gen()
    return completion


@pytest.mark.asyncio
async def test_litellm_streams_and_charges_the_provider_like_a_chat_message():
    g = Guard()
    comp = fake_completion(["Hi", None, " there"])
    llm = LL.LiteLlm("openai", "gpt-5-mini", "openai/gpt-5-mini", {"api_key": "k"}, g, completion=comp)
    assert await llm.warm() is None
    assert await collect(llm, max_tokens=50) == ["Hi", " there"]
    assert comp.kw["stream"] is True and comp.kw["max_tokens"] == 50 and comp.kw["api_key"] == "k" and comp.kw["model"] == "openai/gpt-5-mini"
    assert g.log[0] == ("reserve", "openai", "gpt-5-mini", 50, "openai/gpt-5-mini") and g.log[1][0] == "settle" and g.log[1][3] == "live_voice"
    assert llm.cost_usd == 0.002 and llm.local is False


@pytest.mark.asyncio
async def test_litellm_refusals_failures_and_early_stops():
    with pytest.raises(LL.LlmError, match="daily cap reached"):
        await collect(LL.LiteLlm("p", "m", "t", {}, Guard(refuse=True), completion=fake_completion(["x"])))
    g = Guard()
    with pytest.raises(LL.LlmError, match="ValueError: stream broke"):
        await collect(LL.LiteLlm("p", "m", "t", {}, g, completion=fake_completion(["a", "b"], fail_after=1)))
    assert g.log[-1] == ("release", "live_failed")
    g = Guard()
    llm = LL.LiteLlm("p", "m", "t", {}, g, completion=fake_completion(["a", "b", "c"]))
    agen = llm.stream([{"role": "user", "content": "x"}], 10)
    assert await agen.__anext__() == "a"
    await agen.aclose()                                                    # the person interrupted: what was produced is still charged
    assert g.log[-1][0] == "settle"

    async def boom(**kw):
        raise LL.LlmError("inner")
    with pytest.raises(LL.LlmError, match="inner"):
        await collect(LL.LiteLlm("p", "m", "t", {}, Guard(), completion=boom))


# ---------------------------------------------------------------- choosing the right streamer
def test_make_llm_picks_ollama_directly_and_everything_else_through_litellm(state):
    o = LL.make_llm(state.providers, state.guard, "ollama", "qwen3:8b")
    assert isinstance(o, LL.OllamaLlm) and o.model == "qwen3:8b" and o.num_ctx == 16384 and o.base == "http://127.0.0.1:11434"
    o2 = LL.make_llm(state.providers, state.guard, "ollama", None)
    assert o2.model == "llama3.2"                                          # the provider's default
    m = LL.make_llm(state.providers, state.guard, "mock", "mock-expensive")
    assert isinstance(m, LL.LiteLlm) and m.provider_id == "mock"
    with pytest.raises(LL.LlmError, match="unknown provider 'nope'"):
        LL.make_llm(state.providers, state.guard, "nope", "x")


def test_make_llm_reports_a_misconfigured_provider(state, monkeypatch):
    import app.providers as P

    def bad(p, model=None):
        raise ValueError("set the endpoint address")
    monkeypatch.setattr(P, "litellm_target", bad)
    with pytest.raises(LL.LlmError, match="set the endpoint address"):
        LL.make_llm(state.providers, state.guard, "ollama", "x")


# ---------------------------------------------------------------- an agent's full turn as the model
@pytest.mark.asyncio
async def test_an_agent_turn_is_handed_over_a_few_words_at_a_time_and_its_usage_is_counted():
    asked = []

    async def run(text):
        asked.append(text)
        return {"reply": "one two three four five six seven eight nine", "usage": {"cost_usd": 0.004, "input_tokens": 70, "output_tokens": 9}}
    llm = LL.AgentLlm(run, chunk_words=4)
    assert await llm.warm() is None
    pieces = await collect(llm, [{"role": "system", "content": "s"}, {"role": "user", "content": "old"}, {"role": "assistant", "content": "a"}, {"role": "user", "content": "the question"}])
    assert asked == ["the question"] and pieces == ["one two three four ", "five six seven eight ", "nine"] and "".join(pieces) == "one two three four five six seven eight nine"
    assert (llm.cost_usd, llm.tokens_in, llm.tokens_out) == (0.004, 70, 9)
    again = await collect(LL.AgentLlm(lambda t: _noreply(), chunk_words=3), [{"role": "user", "content": "x"}])
    assert again == [""]


async def _noreply():
    return {}


@pytest.mark.asyncio
async def test_an_agent_that_fails_is_reported_in_words():
    async def run(text):
        raise RuntimeError("the agent's tool broke")
    with pytest.raises(LL.LlmError, match="The agent could not answer: RuntimeError: the agent's tool broke"):
        await collect(LL.AgentLlm(run), [{"role": "user", "content": "x"}])


@pytest.mark.asyncio
async def test_ollama_and_litellm_keep_cumulative_token_counts_for_the_meter():
    body = ndjson({"message": {"content": "a"}}, {"done": True, "prompt_eval_count": 33, "eval_count": 7})
    o = ollama(lambda r: httpx.Response(200, text=body))
    await collect(o)
    await collect(o)
    assert (o.tokens_in, o.tokens_out) == (66, 14)
    g = Guard()
    llm = LL.LiteLlm("p", "m", "t", {}, g, completion=fake_completion(["abcd", "efgh"]))
    await collect(llm, [{"role": "user", "content": "x" * 40}])
    assert llm.tokens_in > 0 and llm.tokens_out == 3
