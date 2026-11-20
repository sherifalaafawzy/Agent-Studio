"""The make-or-break tests: the guard blocks spend BEFORE the model is called."""
import threading

import litellm
import pytest

from app.budget import BudgetExceeded, estimate_tokens


def set_cap(client, cap, pid="mock", **kw):
    r = client.put(f"/api/providers/{pid}", json={"cap_usd": cap, **kw})
    assert r.status_code == 200, r.text


def chat(client, msg="hello", **kw):
    return client.post("/api/chat", json={"provider": "mock", "message": msg, **kw})


def test_round_trip_through_adk_and_litellm(client):
    r = chat(client)
    assert r.status_code == 200, r.text
    j = r.json()
    assert "You said: hello" in j["reply"]
    assert j["usage"]["cost_usd"] > 0 and j["usage"]["input_tokens"] > 0
    assert j["billing"]["spent_usd"] == pytest.approx(j["usage"]["cost_usd"])


def test_refuses_over_cap_and_model_never_called(client, monkeypatch):
    calls = []
    orig = litellm.acompletion

    async def spy(*a, **k):
        calls.append(1)
        return await orig(*a, **k)

    monkeypatch.setattr(litellm, "acompletion", spy)
    set_cap(client, 0.0001)                      # tiny cap
    r = chat(client)
    assert r.status_code == 402
    d = r.json()["detail"]
    assert d["error"] == "budget_exceeded" and d["projected_usd"] > d["remaining_usd"]
    assert calls == [], "provider must not be contacted when the guard refuses"
    s = client.get("/api/billing/summary").json()
    mock = next(p for p in s["providers"] if p["provider"] == "mock")
    assert mock["spent_usd"] == 0 and mock["refused"] == 1


def test_spend_accumulates_then_refuses(client):
    set_cap(client, 0.40, max_output_tokens=64)
    ok = 0
    for _ in range(10):
        r = chat(client, "ping")
        if r.status_code == 402:
            break
        assert r.status_code == 200, r.text
        ok += 1
    assert ok >= 1 and r.status_code == 402
    mock = next(p for p in client.get("/api/billing/summary").json()["providers"] if p["provider"] == "mock")
    assert mock["spent_usd"] <= 0.40  # never exceeded the cap


def test_caps_are_per_provider_independent(client, state):
    set_cap(client, 0.0001, "mock")
    state.providers.update("ollama", {"enabled": True})   # free local, cap 0
    assert chat(client).status_code == 402               # mock exhausted
    # a different provider's cap is unaffected: guard allows free ollama pricing at cap 0
    g = state.guard
    res = g.reserve("ollama", "llama3.2", 100, 100, "ollama_chat/llama3.2")
    g.release(res)


def test_disabled_provider_refused(client):
    client.put("/api/providers/mock", json={"enabled": False})
    assert chat(client).status_code == 402


def test_unknown_model_price_is_conservative_not_free(state):
    state.providers.update("openai", {"enabled": True, "cap_usd": 0.001, "api_key": "sk-test"})
    with pytest.raises(BudgetExceeded) as e:
        state.guard.reserve("openai", "totally-unknown-model", 1000, 2000, "openai/totally-unknown-model")
    assert e.value.detail["price_known"] is False


def test_concurrent_reservations_cannot_jointly_overshoot(state):
    state.providers.update("mock", {"cap_usd": 0.50})
    one = None
    ok, refused = [], []

    def go():
        try:
            ok.append(state.guard.reserve("mock", "mock-expensive", 50, 100, "openai/mock-expensive"))
        except BudgetExceeded:
            refused.append(1)

    # each projects 50*1000/1e6 + 100*2000/1e6 = 0.25 -> only 2 of 8 may hold reservations
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(ok) == 2 and len(refused) == 6


def test_missing_usage_charges_worst_case(state):
    r = state.guard.reserve("mock", "mock-expensive", 50, 100, "openai/mock-expensive")
    cost = state.guard.settle(r, None, None)
    assert cost == pytest.approx(r.projected_usd)


def test_failed_call_releases_reservation(state):
    r = state.guard.reserve("mock", "mock-expensive", 50, 100, "openai/mock-expensive")
    assert state.guard.reserved("mock") > 0
    state.guard.release(r)
    assert state.guard.reserved("mock") == 0


def test_api_key_never_returned(client):
    client.put("/api/providers/openai", json={"api_key": "sk-supersecret-1234"})
    body = client.get("/api/providers").text
    assert "supersecret" not in body and "sk-" not in body
    p = next(x for x in client.get("/api/providers").json() if x["id"] == "openai")
    assert p["has_api_key"] and p["api_key_hint"].endswith("1234")


def test_estimate_is_pessimistic():
    assert estimate_tokens(3000, 1) > 1000


def test_refusal_does_not_pollute_session_history(client):
    set_cap(client, 0.30, max_output_tokens=64)
    r1 = chat(client, "one")
    assert r1.status_code == 200
    sid = r1.json()["session_id"]
    d1 = d2 = None
    for i in range(3):
        r = chat(client, "x", session_id=sid)
        if r.status_code == 402:
            d = r.json()["detail"]
            d1, d2 = (d1 or d["projected_usd"]), d["projected_usd"]
    assert d1 is not None
    # projections must not balloon turn over turn because of stored refusal text
    assert d2 < d1 * 1.2
