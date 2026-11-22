"""Auto mode: the router (app/auto.py) and its REST API (app/auto_api.py).

No LLM is called and no model is downloaded: the decision-model worker is a fake, prices come from the in-app override file,
and providers are the seeded ones (the offline mock provider plus real providers given a fake key).
"""
import asyncio
import json

import pytest

from app import auto as A
from app import d1_spec
from app.d1_client import WorkerTimeout, WorkerUnavailable


def run(c):
    return asyncio.run(c)


class FakeWorker:
    instances: list = []

    def __init__(self, model):
        self.model, self.warmed, self.closed = model, 0, False
        self.reply = {"ok": True, "answers": answers()}
        self.raises = None
        self.calls = []
        FakeWorker.instances.append(self)

    def warm(self):
        self.warmed += 1

    def decide(self, state, questions, timeout):
        self.calls.append((state, questions, timeout))
        if self.raises:
            raise self.raises
        return self.reply

    def close(self):
        self.closed = True

    def public(self):
        return {"model": self.model, "state": "ready", "error": "", "device": "cuda", "load_seconds": 1.0}


def answers(level=1.0, code=0.1, reasoning=0.1, tools=0.1, conf=0.8):
    return {"complexity": {"type": "score", "score": level, "confidence": conf}, "code": {"type": "noul", "noul": code},
            "reasoning": {"type": "noul", "noul": reasoning}, "tools": {"type": "noul", "noul": tools}}


@pytest.fixture
def router(state):
    FakeWorker.instances = []
    r = state.auto
    r.d1_ready = lambda m: True
    r._worker_factory = FakeWorker
    return r


def price_file(tmp_path, prices):
    import os
    from pathlib import Path
    f = Path(os.environ["AGENT_STUDIO_HOME"]) / "pricing_overrides.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({k: {"input_per_m": a, "output_per_m": b} for k, (a, b) in prices.items()}), encoding="utf-8")


def real_providers(state, tmp_path, *ids, mock=False, caps=None):
    """Enable real providers with a fake key and fixed prices (cheap flash, dear pro) so choices are predictable."""
    state.providers.update("mock", {"enabled": mock})
    table = {"openai": ("gpt-5", "gpt-5-mini", (10.0, 40.0), (0.5, 2.0)), "anthropic": ("claude-opus-5-5", "claude-haiku-5-5", (15.0, 75.0), (1.0, 5.0)),
             "groq": ("llama-3.3-70b-versatile", "llama-3.1-8b-instant", (0.6, 0.8), (0.05, 0.08))}
    prices = {}
    for pid in ids:
        pro, flash, p_pro, p_flash = table[pid]
        state.providers.update(pid, {"enabled": True, "api_key": "k-" + pid, "cap_usd": (caps or {}).get(pid, 50.0)})
        prices[f"{pid}/{pro}"], prices[f"{pid}/{flash}"] = p_pro, p_flash
    price_file(tmp_path, prices)


# ---------------------------------------------------------------------------------------------------------- signals
def test_rule_signals_read_length_code_reasoning_and_flags():
    s = A.rule_signals("hi there", [])
    assert (s.complexity, s.code, s.reasoning, s.tools, s.vision) == (0, False, False, False, False) and s.public()["complexity"] == "simple"
    assert A.rule_signals("Fix this:\n```python\nx=1\n```", []).code
    assert A.rule_signals("Why does the sky look blue? Explain step by step", []).reasoning
    s = A.rule_signals("x" * 2000, ["ctx " * 100], agent=True, has_images=True)
    assert s.complexity == 2 and s.tools and s.vision and s.est_tokens > 600
    assert A.rule_signals("a? b? c? d?", []).complexity == 1 and "several questions" in A.rule_signals("a? b? c? d?", []).why
    assert A.rule_signals("line\n" * 12, []).complexity == 1


def test_d1_questions_carry_the_message_and_recent_context():
    state, q = A.d1_questions("y" * 5000, ["first", "second", "third", "fourth", "z" * 900])
    assert len(state["user_request"]) == A.MAX_STATE_CHARS and len(state["earlier_messages"]) == 3
    assert len(state["earlier_messages"][-1]) == A.CONTEXT_CHARS
    assert set(q) == {"complexity", "code", "reasoning", "tools"} and q["complexity"]["type"] == "score" and len(q["complexity"]["criteria"]) == 3
    assert "earlier_messages" not in A.d1_questions("hello", [])[0]


@pytest.mark.parametrize("level,expect", [(0.0, 0), (0.59, 0), (0.6, 1), (1.39, 1), (1.4, 2), (2.0, 2)])
def test_d1_complexity_thresholds(level, expect):
    assert A.d1_signals(answers(level=level), 10, False, False).complexity == expect


def test_d1_signals_flags_confidence_and_bad_shapes():
    s = A.d1_signals(answers(level=1.0, code=0.9, reasoning=0.5, tools=0.2, conf=0.6), 77, False, True)
    assert s.code and s.reasoning and not s.tools and s.vision and s.est_tokens == 77
    assert s.confidence == round((0.6 + 0.8 + 0.0 + 0.6) / 4, 2)
    assert A.d1_signals(answers(), 1, True, False).tools                                   # an agent is selected: tools needed
    for broken in ({}, {"complexity": {"score": "high"}}, {**answers(), "code": {}}, {**answers(), "code": None}):
        with pytest.raises(A.RouterUnavailable, match="unexpected answer"):
            A.d1_signals(broken, 1, False, False)


def test_which_messages_deserve_the_strong_tier():
    S = lambda c=0, code=False, r=False, t=False: A.Signals(c, code, r, t)
    assert not any(A.need_pro(S(), p) for p in A.PREFERENCES)                              # a greeting never needs pro
    assert A.need_pro(S(1), "quality") and not A.need_pro(S(1), "balanced") and not A.need_pro(S(1), "cost")
    assert A.need_pro(S(1, r=True), "balanced") and not A.need_pro(S(1, r=True), "cost")  # reasoning + moderate = 3
    assert A.need_pro(S(1, code=True, t=True), "balanced")                                 # an agentic coding task
    assert A.need_pro(S(2), "balanced") and not A.need_pro(S(2), "cost")
    assert A.need_pro(S(2, code=True), "cost") and A.need_pro(S(2, r=True), "cost")
    assert A.need_pro(S(2, r=True, code=True), "cost")


# ----------------------------------------------------------------------------------------------------------- config
def test_config_defaults_persist_and_survive_damage(router, state):
    cfg = router.config()
    assert cfg == {**A.DEFAULT_CONFIG} and cfg["enabled"] and cfg["engine"] == "rules" and cfg["preference"] == "balanced"
    out = router.set_config({"preference": "cost", "allow": [" openai ", "", "groq/llama"], "deny": ["x"], "timeout_s": 2, "enabled": False,
                             "fallback": 0, "tier_models": {"pro": "openai/gpt-5"}, "engine": None, "bogus": 1})
    assert out["preference"] == "cost" and out["allow"] == ["openai", "groq/llama"] and out["timeout_s"] == 2.0 and out["enabled"] is False
    assert out["fallback"] is False and out["tier_models"] == {"flash": "", "pro": "openai/gpt-5"} and out["engine"] == "rules" and "bogus" not in out
    assert router.config() == out
    router.set_config({"tier_models": {"pro": ""}})                                        # "" clears one override
    assert router.config()["tier_models"] == {"flash": "", "pro": ""}
    state.db.set_setting(A.CONFIG_KEY, "{not json")
    assert router.config() == A.DEFAULT_CONFIG
    state.db.set_setting(A.CONFIG_KEY, json.dumps([1]))
    assert router.config() == A.DEFAULT_CONFIG
    state.db.set_setting(A.CONFIG_KEY, json.dumps({"tier_models": "nope"}))
    assert router.config()["tier_models"] == {"flash": "", "pro": ""}


@pytest.mark.parametrize("patch,msg", [
    ({"engine": "gpt"}, "engine must be one of"), ({"preference": "cheap"}, "preference must be one of"),
    ({"allow": "openai"}, "allow must be a list"), ({"deny": [1]}, "deny must be a list"),
    ({"timeout_s": 0.1}, "between 0.5 and 30"), ({"timeout_s": "5"}, "between 0.5 and 30"), ({"timeout_s": True}, "between 0.5 and 30"),
    ({"tier_models": ["x"]}, "tier_models must look like"), ({"tier_models": {"mid": "a/b"}}, "tier_models must look like"),
    ({"tier_models": {"pro": "ghost/model"}}, "must be 'provider/model'"), ({"tier_models": {"pro": "openai"}}, "must be 'provider/model'"),
])
def test_config_rejects_bad_values(router, patch, msg):
    with pytest.raises(A.AutoError, match=msg):
        router.set_config(patch)
    assert router.config() == A.DEFAULT_CONFIG


def test_choosing_a_d1_engine_loads_it_in_the_background_only_when_it_is_installed(router):
    router.set_config({"engine": "d1-3b"})
    assert FakeWorker.instances[-1].model == "d1-3b" and FakeWorker.instances[-1].warmed == 1
    router.set_config({"engine": "d1-2b"})                                                  # switching closes the other model
    assert FakeWorker.instances[0].closed and FakeWorker.instances[-1].model == "d1-2b"
    router.d1_ready = lambda m: False
    n = len(FakeWorker.instances)
    router.set_config({"engine": "d1-3b"})
    router.set_config({"engine": "rules"})
    router.set_config({"engine": "d1-2b", "enabled": False})
    assert len(FakeWorker.instances) == n                                                   # nothing started: not installed / rules / off
    assert router.d1_states()["active"]["model"] == "d1-2b"
    router.close()
    assert router.d1_states() == {"active": None} and FakeWorker.instances[-1].closed
    router.close()                                                                          # idempotent


# ------------------------------------------------------------------------------------------------------- candidates
def test_candidates_are_the_two_tiers_of_each_usable_provider(router, state, tmp_path):
    real_providers(state, tmp_path, "openai", "groq", mock=True)
    state.providers.update("ollama", {"enabled": True})
    cands, skipped = router.candidates(router.config(), 100)
    got = {(c.provider, c.tier, c.model) for c in cands}
    assert ("openai", "pro", "gpt-5") in got and ("openai", "flash", "gpt-5-mini") in got and ("groq", "flash", "llama-3.1-8b-instant") in got
    assert ("mock", "flash", "mock-cheap") in got and ("mock", "pro", "mock-expensive") in got
    assert {c.tier for c in cands if c.provider == "ollama"} == {"flash"}                   # a local model is never the "strong" tier
    assert next(c for c in cands if c.provider == "ollama").blended == 0.0
    assert any(s["provider"] == "anthropic" and s["why"] == "turned off in Settings" for s in skipped)
    c = next(c for c in cands if c.model == "gpt-5")
    assert c.ref() == {"provider": "openai", "model": "gpt-5", "tier": "pro"} and (c.in_per_m, c.out_per_m, c.known) == (10.0, 40.0, True)


def test_allow_and_deny_lists_match_providers_and_single_models(router, state, tmp_path):
    real_providers(state, tmp_path, "openai", "groq")
    cfg = {**router.config(), "deny": ["openai/gpt-5"], "allow": ["openai", "groq/llama-3.1-8b-instant"]}
    cands, skipped = router.candidates(cfg)
    assert {(c.provider, c.model) for c in cands} == {("openai", "gpt-5-mini"), ("groq", "llama-3.1-8b-instant")}
    whys = {(s.get("model"), s["why"]) for s in skipped if s["provider"] in ("openai", "groq")}
    assert ("gpt-5", "on your block list") in whys and ("llama-3.3-70b-versatile", "not on your allow list") in whys


def test_a_provider_without_credentials_is_explained_once(router, state, tmp_path):
    real_providers(state, tmp_path, "openai")
    state.providers.update("anthropic", {"enabled": True})                                  # enabled, but no key
    cands, skipped = router.candidates(router.config())
    assert all(c.provider != "anthropic" for c in cands)
    assert [s["why"] for s in skipped if s["provider"] == "anthropic"] == ["Anthropic: API key not set in Settings"]


def test_spend_caps_and_credits_keep_a_provider_out(router, state, tmp_path):
    real_providers(state, tmp_path, "openai", "groq", caps={"openai": 0.0001})
    cands, skipped = router.candidates(router.config(), 4000)
    assert all(c.provider == "groq" for c in cands)
    why = next(s["why"] for s in skipped if s["provider"] == "openai" and s.get("model") == "gpt-5-mini")
    assert why.startswith("over budget: this message could cost $") and "is left under its cap or credits" in why
    state.credits.set_manual("groq", 5.0) if hasattr(state.credits, "set_manual") else None
    state.guard.credits.strict = lambda pid: pid == "groq"                                  # strict mode and no known balance
    state.guard.credits.remaining = lambda pid: None
    cands, skipped = router.candidates(router.config())
    assert all(c.provider != "groq" for c in cands)
    assert any(s["provider"] == "groq" and "strict credits mode" in s["why"] for s in skipped)


def test_a_message_too_long_for_a_models_window_skips_it(router, state, tmp_path, monkeypatch):
    real_providers(state, tmp_path, "openai")
    monkeypatch.setitem(A.litellm.model_cost, "openai/gpt-5-mini", {"max_input_tokens": 1000})
    cands, skipped = router.candidates(router.config(), est_tokens=900)
    assert ("openai", "gpt-5-mini") not in {(c.provider, c.model) for c in cands} and ("openai", "gpt-5") in {(c.provider, c.model) for c in cands}
    assert any("too long for its 1,000-token window" in s["why"] for s in skipped)


def test_images_need_a_vision_model(router, state, tmp_path, monkeypatch):
    real_providers(state, tmp_path, "openai")
    monkeypatch.setitem(A.litellm.model_cost, "gpt-5", {"supports_vision": True})
    monkeypatch.setitem(A.litellm.model_cost, "gpt-5-mini", {"supports_vision": False})
    monkeypatch.setitem(A.litellm.model_cost, "openai/gpt-5", {"supports_vision": True})
    monkeypatch.setitem(A.litellm.model_cost, "openai/gpt-5-mini", {"supports_vision": False})
    cands, skipped = router.candidates(router.config(), need_vision=True)
    assert [c.model for c in cands] == ["gpt-5"] and cands[0].vision
    assert any(s.get("model") == "gpt-5-mini" and s["why"] == "cannot read images" for s in skipped)


def test_only_one_provider_when_an_agent_is_pinned_to_it(router, state, tmp_path):
    real_providers(state, tmp_path, "openai", "groq")
    cands, _ = router.candidates(router.config(), only_provider="groq")
    assert {c.provider for c in cands} == {"groq"}


def test_a_per_tier_override_replaces_the_normal_choice_for_that_tier(router, state, tmp_path):
    real_providers(state, tmp_path, "openai", "groq")
    cfg = {**router.config(), "tier_models": {"flash": "groq/llama-3.3-70b-versatile", "pro": ""}}
    cands, skipped = router.candidates(cfg)
    assert [(c.provider, c.model) for c in cands if c.tier == "flash"] == [("groq", "llama-3.3-70b-versatile")]
    assert {c.provider for c in cands if c.tier == "pro"} == {"openai", "groq"}
    bad = {**router.config(), "tier_models": {"pro": "anthropic/claude-opus-5-5", "flash": ""}}       # that provider is off
    cands, skipped = router.candidates(bad)
    assert {c.provider for c in cands if c.tier == "pro"} == {"openai", "groq"}
    assert any("the pro override is not usable right now" in s["why"] for s in skipped)


def test_availability_says_why_auto_cannot_be_offered(router, state, tmp_path):
    assert router.availability() == {"available": True, "reason": "", "models": 2}                  # the offline mock provider
    state.providers.update("mock", {"enabled": False})
    a = router.availability()
    assert a["available"] is False and "No model provider is ready for Auto (" in a["reason"] and "turned off in Settings" in a["reason"]
    router.set_config({"enabled": False})
    assert router.availability() == {"available": False, "reason": "Auto is turned off in Settings > Auto."}
    router.set_config({"enabled": True, "allow": ["nobody"]})
    state.providers.update("mock", {"enabled": True})
    only_mock = [state.providers.get("mock")]
    router.providers.list = lambda: only_mock
    assert "mock/mock-cheap: not on your allow list" in router.availability()["reason"]
    router.providers.list = lambda: []
    assert router.availability()["reason"].endswith("Add and enable one in Settings > Model providers.")


# ---------------------------------------------------------------------------------------------------------- routing
def test_rules_route_a_greeting_to_the_cheapest_fast_model(router, state, tmp_path):
    real_providers(state, tmp_path, "openai", "groq")
    r = run(router.route("hello!"))
    assert (r.provider, r.model, r.tier, r.engine) == ("groq", "llama-3.1-8b-instant", "flash", "rules")
    assert r.requested_engine == "rules" and r.engine_note == "" and r.preference == "balanced"
    assert "built-in rules read this as simple: the fast tier, the cheapest of" in r.reason and "price" not in r.reason.split("(")[0]
    assert r.alternates[0] == {"provider": "openai", "model": "gpt-5-mini", "tier": "flash"} and len(r.alternates) == 3
    d = r.public()
    assert d["price"] == {"input_per_m": 0.05, "output_per_m": 0.08, "known": True} and d["signals"]["complexity"] == "simple" and d["engine"] == "rules"
    assert d["skipped"] and d["confidence"] == 0.6


def test_hard_messages_get_the_strong_tier_and_preferences_change_the_choice(router, state, tmp_path):
    real_providers(state, tmp_path, "openai", "groq", "anthropic")
    msg = "Analyze the trade-offs of these two architectures step by step and prove which scales better."
    r = run(router.route(msg))
    assert (r.provider, r.model, r.tier) == ("groq", "llama-3.3-70b-versatile", "pro")        # the cheapest strong model
    q = run(router.route(msg, override={"preference": "quality"}))
    assert (q.provider, q.model, q.tier) == ("anthropic", "claude-opus-5-5", "pro") and "the priciest known of 3" in q.reason
    c = run(router.route("explain this", override={"preference": "cost"}))
    assert c.tier == "flash"
    assert run(router.route("a? b? c? d?", override={"preference": "quality"})).tier == "pro"   # quality: anything non-trivial gets pro
    assert run(router.route("hello", override={"preference": "quality"})).tier == "flash"


def test_when_the_strong_tier_is_unusable_the_fast_tier_is_used_and_it_says_so(router, state, tmp_path):
    real_providers(state, tmp_path, "openai")
    router.set_config({"deny": ["openai/gpt-5"]})
    r = run(router.route("Analyze this step by step and prove it."))
    assert r.tier == "flash" and "No pro-tier model is usable right now" in r.reason and "the fast tier was used" not in r.reason
    router.set_config({"deny": ["openai/gpt-5-mini"]})
    r = run(router.route("hello"))
    assert r.tier == "pro" and "No flash-tier model is usable right now" in r.reason


def test_unknown_prices_are_flagged_and_never_beat_known_ones(router, state, tmp_path):
    real_providers(state, tmp_path, "openai")
    state.providers.update("groq", {"enabled": True, "api_key": "k", "flash_model": "made-up-model", "pro_model": "made-up-model"})
    r = run(router.route("hello"))
    assert r.provider == "openai" and r.price["known"]                                     # a known price beats an unpriced model
    router.set_config({"allow": ["groq"]})
    r = run(router.route("hello"))
    assert r.provider == "groq" and r.price["known"] is False and "price unknown" in r.reason and "1 usable model)" not in r.reason


def test_nothing_usable_raises_a_message_the_user_can_act_on(router, state, tmp_path):
    state.providers.update("mock", {"enabled": False})
    with pytest.raises(A.AutoError, match=r"Auto could not find a model that can take this message - .*turned off in Settings"):
        run(router.route("hello"))
    router.providers.list = lambda: []
    with pytest.raises(A.AutoError, match="Add and enable a model provider in Settings"):
        run(router.route("hello"))


def test_a_bad_engine_or_preference_override_is_refused(router):
    with pytest.raises(A.AutoError, match="engine must be one of"):
        run(router.route("hello", override={"engine": "gpt"}))
    with pytest.raises(A.AutoError, match="preference one of"):
        run(router.route("hello", override={"preference": "x"}))


def test_the_fixed_prompt_and_the_chat_so_far_count_towards_what_a_call_could_cost(router, state, tmp_path):
    """The guard reserves the worst case of the WHOLE request, so Auto must not pick a model the guard would then refuse."""
    state.providers.update("mock", {"cap_usd": 1.5})
    msg = "Analyze the trade-offs step by step."
    assert run(router.route(msg)).model == "mock-expensive"                                          # a plain chat: small overhead fits
    assert run(router.route(msg, agent=True)).model == "mock-cheap"                                  # an agent's tool prompt does not
    assert run(router.route(msg, history_chars=3000)).model == "mock-cheap"                           # a long chat does not either
    assert run(router.route(msg, ["x" * 4000], history_chars=0)).model == "mock-cheap"                # context alone also counts


def test_the_budget_is_part_of_the_decision(router, state, tmp_path):
    """The mock provider's strong model is priced to trip caps: a huge message must fall back to its cheap model."""
    r = run(router.route("Analyze the trade-offs step by step. " + "word " * 600))
    assert r.model == "mock-cheap" and r.tier == "flash" and "No pro-tier model is usable" in r.reason
    assert any(s.get("model") == "mock-expensive" and s["why"].startswith("over budget") for s in r.skipped)


# ------------------------------------------------------------------------------------------------- the d1 engines
def test_d1_signals_drive_the_choice_and_the_engine_is_reported(router, state, tmp_path):
    real_providers(state, tmp_path, "openai", "groq")
    router.set_config({"engine": "d1-2b", "timeout_s": 3})
    w = FakeWorker.instances[-1]
    w.reply = {"ok": True, "answers": answers(level=1.9, code=0.9, reasoning=0.8, tools=0.1, conf=0.9)}
    r = run(router.route("please refactor this module", ["earlier question"], agent=False))
    assert r.engine == "d1-2b" and r.requested_engine == "d1-2b" and r.engine_note == "" and r.tier == "pro"
    assert r.provider == "groq" and "d1-2B read this as demanding, code, deep reasoning" in r.reason
    state_sent, questions, timeout = w.calls[-1]
    assert state_sent["user_request"] == "please refactor this module" and state_sent["earlier_messages"] == ["earlier question"] and timeout == 3.0
    assert set(questions) == {"complexity", "code", "reasoning", "tools"} and r.confidence == 0.78
    w.reply = {"ok": True, "answers": answers(level=0.1, conf=0.5)}
    assert run(router.route("hi")).tier == "flash"
    router.set_config({"engine": "d1-3b"})
    run(router.route("hi", agent=True))
    assert FakeWorker.instances[-1].model == "d1-3b" and "needs tools" in run(router.route("hi", agent=True)).reason


@pytest.mark.parametrize("setup,expect", [
    (lambda r, w: setattr(r, "d1_ready", lambda m: False), "d1-2B could not be used (it is not installed (Settings > Auto > Install))"),
    (lambda r, w: setattr(w, "raises", WorkerUnavailable("the decision model is still loading")), "(the decision model is still loading)"),
    (lambda r, w: setattr(w, "raises", WorkerTimeout("no answer within 3 s")), "(no answer within 3 s)"),
    (lambda r, w: setattr(w, "reply", {"ok": False, "error": "decision failed: OOM"}), "(decision failed: OOM)"),
    (lambda r, w: setattr(w, "reply", {"ok": False}), "(no answer)"),
    (lambda r, w: setattr(w, "reply", {"ok": True, "answers": {"complexity": {}}}), "(unexpected answer from the decision model (KeyError))"),
    (lambda r, w: setattr(w, "reply", {"ok": True}), "(unexpected answer from the decision model (KeyError))"),
])
def test_when_d1_cannot_answer_the_rules_decide_and_the_user_is_told(router, state, tmp_path, setup, expect):
    real_providers(state, tmp_path, "openai")
    router.set_config({"engine": "d1-2b"})
    setup(router, FakeWorker.instances[-1])
    r = run(router.route("hello"))
    assert r.engine == "rules" and r.requested_engine == "d1-2b" and r.tier == "flash"
    assert r.engine_note.startswith("d1-2B could not be used") and expect in r.engine_note and r.engine_note.endswith("so the built-in rules decided.")


# ------------------------------------------------------------------------------------------------------------ REST
def test_api_get_put_and_validation(client, state, tmp_path):
    g = client.get("/api/auto").json()
    assert g["enabled"] and g["engine"] == "rules" and g["available"] is True and g["engines"] == ["rules", "d1-2b", "d1-3b"]
    assert g["preferences"] == ["cost", "balanced", "quality"] and g["worker"] is None and g["engine_ready"] == {"d1-2b": False, "d1-3b": False}
    p = client.put("/api/auto", json={"preference": "quality", "allow": ["mock"], "timeout_s": 6}).json()
    assert p["preference"] == "quality" and p["allow"] == ["mock"] and p["timeout_s"] == 6.0
    bad = client.put("/api/auto", json={"engine": "gpt"})
    assert bad.status_code == 422 and "engine must be one of" in bad.json()["detail"]
    assert client.put("/api/auto", json={"fallback": False}).json()["fallback"] is False


def test_api_dry_run_routes_without_calling_a_model(client, state):
    r = client.post("/api/auto/route", json={"message": "hello"})
    assert r.status_code == 200 and r.json()["provider"] == "mock" and r.json()["engine"] == "rules" and r.json()["tier"] == "flash"
    t = client.post("/api/auto/route", json={"message": "hello", "agent": True, "preference": "quality"}).json()
    assert t["signals"]["tools"] is True and t["preference"] == "quality"
    assert client.post("/api/auto/route", json={"message": "hi", "engine": "nope"}).status_code == 422
    assert client.post("/api/auto/route", json={"message": ""}).status_code == 422
    state.providers.update("mock", {"enabled": False})
    r = client.post("/api/auto/route", json={"message": "hi"})
    assert r.status_code == 422 and "could not find a model" in r.json()["detail"]
    assert state.guard.ledger() == []                                                          # nothing was reserved or spent


def test_api_warm_needs_an_installed_decision_model(client, state):
    assert client.post("/api/auto/warm").status_code == 422                                    # rules: nothing to load
    client.put("/api/auto", json={"engine": "d1-3b"})
    w = client.post("/api/auto/warm")
    assert w.status_code == 409 and "d1-3B is not installed yet" in w.json()["detail"]
    FakeWorker.instances = []
    state.auto.d1_ready = lambda m: True
    state.auto._worker_factory = FakeWorker
    ok = client.post("/api/auto/warm")
    assert ok.status_code == 200 and ok.json()["model"] == "d1-3b" and FakeWorker.instances[-1].warmed == 1
    assert client.get("/api/auto").json()["worker"]["state"] == "ready"
