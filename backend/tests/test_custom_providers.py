"""Custom OpenAI-compatible endpoints: storage and validation, prices, model discovery, the Test button, the API, settings export/import."""
from __future__ import annotations

import asyncio
import json
import ssl

import httpx
import pytest

from app import custom_providers as CP
from app import models as M
from app.budget import BudgetExceeded
from app.db import Database
from app.providers import (CUSTOM_KIND, ProviderStore, _clean_models, _clean_prices, context_of, litellm_target, network_scope, normalize_openai_base,
                           price_of, slugify)


def run(c):
    return asyncio.run(c)


def store(tmp_path):
    return ProviderStore(Database(tmp_path / "p.db"))


def make(st, label="My vLLM", base="http://127.0.0.1:8000", model="qwen", **kw):
    return st.create_custom(label, base, model, **{"auth_mode": "none", **kw})


# ------------------------------------------------------------------------------------------------ helpers
def test_network_scope_says_where_an_endpoint_is():
    assert network_scope("http://localhost:8000/v1") == "this_pc"
    assert network_scope("http://api.localhost/v1") == "this_pc"
    assert network_scope("http://127.0.0.1:8000") == "this_pc"
    assert network_scope("http://[::1]:8000") == "this_pc"
    assert network_scope("http://192.168.1.20:8000/v1") == "network"
    assert network_scope("http://10.0.0.5/v1") == "network"
    assert network_scope("http://169.254.1.1/v1") == "network"
    assert network_scope("http://8.8.8.8/v1") == "internet"
    assert network_scope("http://gpu-box:8000/v1") == "network"             # a single-label name is on your network
    assert network_scope("http://vllm.company.internal/v1") == "network"
    assert network_scope("https://api.together.xyz/v1") == "internet"
    assert network_scope("") == "internet" and network_scope(None) == "internet"


def test_normalize_openai_base_adds_v1_only_for_a_bare_host():
    assert normalize_openai_base("http://localhost:8000") == "http://localhost:8000/v1"
    assert normalize_openai_base(" https://openrouter.ai/api/v1/ ") == "https://openrouter.ai/api/v1"
    assert normalize_openai_base("http://gw.local/openai?x=1#f") == "http://gw.local/openai"
    for bad in ("", "ftp://x/v1", "localhost:8000", "http://"):
        with pytest.raises(ValueError, match="http:// or https://"):
            normalize_openai_base(bad)
    with pytest.raises(ValueError, match="password"):
        normalize_openai_base("http://user:pw@host/v1")


def test_slugify():
    assert slugify("My vLLM (GPU #1)") == "my_vllm_gpu_1" and slugify("***") == "endpoint"


def test_clean_models_and_prices_explain_what_is_wrong():
    assert _clean_models([" a ", "b", "a", ""]) == ["a", "b"]
    with pytest.raises(ValueError, match="list"):
        _clean_models("a")
    with pytest.raises(ValueError, match="too long"):
        _clean_models(["x" * 201])
    with pytest.raises(ValueError, match="at most 300"):
        _clean_models([f"m{i}" for i in range(301)])
    assert _clean_prices({"m": {"input_per_m": "1", "output_per_m": 2, "context": "8192"}, "empty": {}, "*": {"input_per_m": 0, "output_per_m": 0}}) == {
        "m": {"input_per_m": 1.0, "output_per_m": 2.0, "context": 8192}, "*": {"input_per_m": 0.0, "output_per_m": 0.0}}
    with pytest.raises(ValueError, match="object of model"):
        _clean_prices([])
    with pytest.raises(ValueError, match="must be an object"):
        _clean_prices({"m": 5})
    with pytest.raises(ValueError, match="input price of 'm' must be a number"):
        _clean_prices({"m": {"input_per_m": "abc", "output_per_m": 1}})
    with pytest.raises(ValueError, match="output price of 'm' must be between"):
        _clean_prices({"m": {"input_per_m": 1, "output_per_m": -1}})
    with pytest.raises(ValueError, match="both the input and the output"):
        _clean_prices({"m": {"input_per_m": 1}})
    with pytest.raises(ValueError, match="whole number"):
        _clean_prices({"m": {"context": "big"}})
    with pytest.raises(ValueError, match="between 512"):
        _clean_prices({"m": {"context": 10}})
    with pytest.raises(ValueError, match="at most 300"):
        _clean_prices({f"m{i}": {"context": 1024} for i in range(301)})


# ------------------------------------------------------------------------------------------------ the store
def test_create_custom_stores_a_named_endpoint_and_numbers_clashing_ids(tmp_path):
    st = store(tmp_path)
    a = make(st, headers={"X-Team": "ml"}, organization="org1", model_prices={"qwen": {"input_per_m": 0, "output_per_m": 0, "context": 32768}}, manual_models=["qwen", "llama"])
    b = make(st)
    assert a.id == "custom_my_vllm" and b.id == "custom_my_vllm_2" and a.kind == CUSTOM_KIND and a.custom
    assert a.api_base == "http://127.0.0.1:8000/v1" and a.enabled and a.cap_usd == 1.0 and a.headers() == {"X-Team": "ml"}
    assert a.manual() == ["qwen", "llama"] and a.prices()["qwen"]["context"] == 32768 and a.mode == "none"
    pub = a.public()
    assert pub["custom"] and pub["network_scope"] == "this_pc" and pub["extra_header_names"] == ["X-Team"] and "X-Team: ml" not in json.dumps(pub)
    assert [p.id for p in st.list()].count("custom_my_vllm") == 1
    assert not st.get("openai").public()["custom"] and st.get("openai").public()["network_scope"] is None
    st.seed()                                                                   # re-seeding never touches or duplicates custom rows
    assert st.get("custom_my_vllm") is not None and "openai_compatible" not in [p.id for p in st.list()]


def test_create_custom_validates_everything(tmp_path):
    st = store(tmp_path)
    with pytest.raises(ValueError, match="1 to 60"):
        make(st, label=" ")
    with pytest.raises(ValueError, match="http://"):
        make(st, base="nope")
    with pytest.raises(ValueError, match="default model"):
        make(st, model=" ")
    with pytest.raises(ValueError, match="authentication must be"):
        make(st, auth_mode="aws_keys")
    with pytest.raises(ValueError, match="environment variable"):
        make(st, auth_mode="env", api_key_env="1bad")
    with pytest.raises(ValueError, match="spend cap"):
        make(st, cap_usd=-1)
    with pytest.raises(ValueError, match="spend cap"):
        make(st, cap_period="weekly")
    with pytest.raises(ValueError, match="spend cap"):
        make(st, max_output_tokens=1)
    assert make(st, auth_mode="env", api_key_env="MY_KEY").api_key_env == "MY_KEY"
    assert make(st, label="Keyed", auth_mode="api_key", api_key=" sk-123 ").api_key == "sk-123"


def test_update_edits_a_custom_endpoint_and_refuses_what_does_not_apply(tmp_path):
    st = store(tmp_path)
    p = make(st)
    q = st.update(p.id, {"label": "Renamed", "api_base": "http://gpu-box:9000", "model_prices": {"*": {"input_per_m": 0.5, "output_per_m": 1.5}},
                         "manual_models": ["a", "b"], "organization": "o"})
    assert q.label == "Renamed" and q.api_base == "http://gpu-box:9000/v1" and q.prices() == {"*": {"input_per_m": 0.5, "output_per_m": 1.5}} and q.manual() == ["a", "b"]
    with pytest.raises(ValueError, match="1 to 60"):
        st.update(p.id, {"label": ""})
    with pytest.raises(ValueError, match="http://"):
        st.update(p.id, {"api_base": ""})
    with pytest.raises(ValueError, match="only custom endpoints can be renamed"):
        st.update("openai", {"label": "x"})
    with pytest.raises(ValueError, match="custom endpoints only"):
        st.update("openai", {"model_prices": {}})
    with pytest.raises(ValueError, match="custom endpoints only"):
        st.update("openai", {"manual_models": []})


def test_prices_and_context_come_from_the_endpoint_first(tmp_path):
    st = store(tmp_path)
    p = make(st, model_prices={"qwen": {"input_per_m": 1, "output_per_m": 2, "context": 4096}, "*": {"input_per_m": 0, "output_per_m": 0, "context": 16000}})
    pr = price_of(p, "qwen", "openai/qwen")
    assert (pr.input_per_m, pr.output_per_m, pr.known, pr.source) == (1.0, 2.0, True, "endpoint")
    assert price_of(p, "other", "openai/other").input_per_m == 0.0                      # the "*" default
    assert context_of(p, "qwen") == 4096 and context_of(p, "other") == 16000 and context_of(p, None) == 16000
    bare = make(st, label="Bare")
    unknown = price_of(bare, "x", "openai/x")
    assert not unknown.known and unknown.source == "fallback"                           # never treated as free unless you say so
    assert context_of(bare, "x") is None and context_of(st.get("openai"), "gpt-5") is None
    assert price_of(st.get("openai"), "gpt-5-mini", "openai/gpt-5-mini").known


def test_stored_json_that_is_damaged_reads_as_empty(tmp_path):
    st = store(tmp_path)
    p = make(st)
    st.db.execute("UPDATE providers SET model_prices='{oops', manual_models='[oops' WHERE id=?", (p.id,))
    p = st.get(p.id)
    assert p.prices() == {} and p.manual() == []
    st.db.execute("UPDATE providers SET model_prices='[1]', manual_models='{\"a\":1}' WHERE id=?", (p.id,))
    p = st.get(p.id)
    assert p.prices() == {} and p.manual() == []


def test_litellm_target_for_a_custom_endpoint(tmp_path, monkeypatch):
    st = store(tmp_path)
    p = make(st, auth_mode="api_key", api_key="sk-live-1", headers={"X-A": "1"}, organization="org9")
    target, kw = litellm_target(p, "qwen")
    assert target == "openai/qwen" and kw["api_base"] == "http://127.0.0.1:8000/v1" and kw["api_key"] == "sk-live-1" and kw["organization"] == "org9"
    assert kw["extra_headers"] == {"X-A": "1"}
    nokey = make(st, label="Open")
    assert litellm_target(nokey, "m")[1]["api_key"] == "no-key"
    keyless = make(st, label="Keyless", auth_mode="api_key")
    with pytest.raises(ValueError, match="Keyless: API key not set"):
        litellm_target(keyless, "m")
    envp = make(st, label="Envy", auth_mode="env", api_key_env="CUSTOM_EP_KEY")
    with pytest.raises(ValueError, match="Envy: environment variable CUSTOM_EP_KEY is not set"):
        litellm_target(envp, "m")
    monkeypatch.setenv("CUSTOM_EP_KEY", "from-env")
    assert litellm_target(envp, "m")[1]["api_key"] == "from-env"
    st.db.execute("UPDATE providers SET api_key_env=NULL WHERE id=?", (envp.id,))
    with pytest.raises(ValueError, match="name of the environment variable"):
        litellm_target(st.get(envp.id), "m")
    st.db.execute("UPDATE providers SET api_base=NULL WHERE id=?", (nokey.id,))
    with pytest.raises(ValueError, match="Open: set the endpoint address"):
        litellm_target(st.get(nokey.id), "m")
    ok = st.get(p.id).public()
    assert ok["auth_ready"] and "sk-live-1" not in json.dumps(ok)


def test_delete_custom_refuses_builtins_and_endpoints_in_use(tmp_path):
    st = store(tmp_path)
    p = make(st)
    with pytest.raises(KeyError):
        st.delete_custom("nope")
    with pytest.raises(ValueError, match="cannot be deleted"):
        st.delete_custom("openai")
    st.db.execute("INSERT INTO agents(id,name,description,instruction,provider,tier,tools,mcp,skills,sub_agents,builtin) VALUES('a1','Alpha','','', ?,'flash','[]','[]','[]','[]',0)", (p.id,))
    st.db.set_setting("sidebot_provider", p.id)
    with pytest.raises(ValueError, match="agent 'Alpha', the assistant"):
        st.delete_custom(p.id)
    for i in range(6):
        st.db.execute("INSERT INTO agents(id,name,description,instruction,provider,tier,tools,mcp,skills,sub_agents,builtin) VALUES(?,?,'','', ?,'flash','[]','[]','[]','[]',0)", (f"x{i}", f"X{i}", p.id))
    with pytest.raises(ValueError, match="and more"):
        st.delete_custom(p.id)
    st.db.execute("DELETE FROM agents WHERE provider=?", (p.id,))
    st.db.set_setting("sidebot_provider", "")
    st.delete_custom(p.id)
    assert st.get(p.id) is None


def test_export_custom_carries_no_secret(tmp_path):
    st = store(tmp_path)
    make(st, auth_mode="api_key", api_key="sk-secret-zzz", headers={"Authorization": "Bearer hidden"}, manual_models=["m1"])
    out = st.export_custom()
    assert len(out) == 1 and out[0]["header_names"] == ["Authorization"] and out[0]["manual_models"] == ["m1"]
    assert "sk-secret-zzz" not in json.dumps(out) and "hidden" not in json.dumps(out)


# ------------------------------------------------------------------------------------------------ model discovery
def fake_get(payload=None, exc=None, calls=None):
    async def get(url, headers=None, params=None):
        if calls is not None:
            calls.append((url, headers))
        if exc:
            raise exc
        return payload
    return get


def test_model_list_merges_live_typed_in_and_published_prices(tmp_path, monkeypatch):
    st = store(tmp_path)
    p = make(st, auth_mode="api_key", api_key="sk-1", organization="org1", manual_models=["hand-made", "qwen"],
             model_prices={"qwen": {"input_per_m": 3, "output_per_m": 4}})
    calls: list = []
    payload = {"data": [{"id": "qwen"}, {"id": "gw/model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
                        {"id": "bad-price", "pricing": {"prompt": "x"}}, {"id": "no-price", "pricing": {"completion": "1"}}, {"name": "no id"}, "junk"]}
    monkeypatch.setattr(M, "_get_json", fake_get(payload, calls=calls))
    res = run(M.list_models(p))
    by = {m["id"]: m for m in res["models"]}
    assert res["source"] == "live" and res["error"] is None and set(by) == {"qwen", "gw/model", "bad-price", "no-price", "hand-made"}
    assert by["qwen"]["input_per_m"] == 3.0                                          # the endpoint's own price wins
    assert by["gw/model"]["input_per_m"] == pytest.approx(1.0) and by["gw/model"]["output_per_m"] == pytest.approx(2.0)
    assert by["bad-price"]["input_per_m"] is None and by["hand-made"]["input_per_m"] is None
    assert calls[0][0] == "http://127.0.0.1:8000/v1/models" and calls[0][1]["Authorization"] == "Bearer sk-1" and calls[0][1]["OpenAI-Organization"] == "org1"
    assert run(M.list_models(p))["cached"] is True
    p2 = st.update(p.id, {"manual_models": ["another"]})                             # editing the typed-in list busts the cache
    assert run(M.list_models(p2))["cached"] is False


def test_model_list_falls_back_to_the_typed_in_names_and_says_why(tmp_path, monkeypatch):
    st = store(tmp_path)
    p = make(st, manual_models=["only-me"])
    monkeypatch.setattr(M, "_get_json", fake_get(exc=httpx.ConnectError("refused")))
    res = run(M.list_models(p))
    assert res["source"] == "manual" and [m["id"] for m in res["models"]] == ["only-me"] and "ConnectError" in res["error"]
    monkeypatch.setattr(M, "_get_json", fake_get({"data": []}))
    res = run(M.list_models(p, refresh=True))
    assert "returned no models" in res["error"] and res["source"] == "manual"
    q = make(st, label="Other")
    st.db.execute("UPDATE providers SET api_base=NULL WHERE id=?", (q.id,))
    res = run(M.list_models(st.get(q.id)))
    assert res["models"] == [] and "set the endpoint address" in res["error"]


# ------------------------------------------------------------------------------------------------ probing and testing
def mock_transport(handler):
    return httpx.MockTransport(handler)


def test_discover_an_unsaved_address(monkeypatch):
    probe = CP.transient("http://127.0.0.1:8000", "api_key", "k", None, {"X": "1"}, "o")
    assert probe.api_base == "http://127.0.0.1:8000/v1" and probe.custom and probe.headers() == {"X": "1"}
    monkeypatch.setattr(M, "_get_json", fake_get({"data": [{"id": "b"}, {"id": "a"}]}))
    assert run(CP.discover(probe)) == {"ok": True, "models": ["a", "b"], "count": 2, "detail": "2 model(s) found"}
    monkeypatch.setattr(M, "_get_json", fake_get({"data": []}))
    assert "type the model name" in run(CP.discover(probe))["detail"]
    monkeypatch.setattr(M, "_get_json", fake_get(exc=httpx.ConnectError("no")))
    r = run(CP.discover(probe))
    assert not r["ok"] and r["kind"] == "unreachable" and "127.0.0.1:8000" in r["detail"]
    monkeypatch.setattr(M, "_get_json", fake_get(exc=RuntimeError("HTTP 401: bad key")))
    assert run(CP.discover(probe))["kind"] == "auth"
    keyless = CP.transient("http://127.0.0.1:8000", "api_key")
    r = run(CP.discover(keyless))                                                    # no key typed: says so, does not call out
    assert r["kind"] == "config" and r["title"] == "No key yet" and "No key" in r["detail"]
    envless = CP.transient("http://127.0.0.1:8000", "env", None, "ENDPOINT_KEY_NOT_SET_ANYWHERE")
    assert run(CP.discover(envless))["kind"] == "config"
    with pytest.raises(ValueError):
        CP.transient("nope")


def test_every_failure_has_a_plain_reason():
    p = CP.transient("https://api.example.com/v1")
    assert CP.explain_status(401, "", p, "m")["kind"] == "auth" and CP.explain_status(403, "", p, "m")["kind"] == "auth"
    assert CP.explain_status(404, '{"error":{"message":"The model `m` does not exist"}}', p, "m")["kind"] == "model"
    assert CP.explain_status(404, "model not_found", p, "m")["kind"] == "model"
    assert CP.explain_status(404, "Not Found", p, "m")["kind"] == "address" and "/v1" in CP.explain_status(404, "", p, "m")["detail"]
    assert CP.explain_status(429, "", p, "m")["kind"] == "rate"
    assert CP.explain_status(400, "bad json", p, "m")["detail"].endswith("bad json") and "no details" in CP.explain_status(400, "", p, "m")["detail"]
    assert CP.explain_status(503, "overloaded", p, "m")["kind"] == "server" and "no details" in CP.explain_status(500, "", p, "m")["detail"]
    assert CP.explain_status(418, "teapot", p, "m")["kind"] == "other" and "no details" in CP.explain_status(418, "", p, "m")["detail"]
    ssl_err = ssl.SSLCertVerificationError("certificate verify failed")
    assert CP.explain_exception(ssl_err, p)["kind"] == "tls"
    wrapped = httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version")
    assert CP.explain_exception(wrapped, p)["kind"] == "tls"
    chained = httpx.ConnectError("tls")
    chained.__cause__ = ssl.SSLError("x")
    assert CP.explain_exception(chained, p)["kind"] == "tls" and "api.example.com" in CP.explain_exception(chained, p)["detail"]
    assert CP.explain_exception(httpx.ConnectTimeout("t"), p)["kind"] == "timeout" and CP.explain_exception(TimeoutError(), p)["kind"] == "timeout"
    assert CP.explain_exception(httpx.ConnectError("refused"), p)["kind"] == "unreachable"
    assert CP.explain_exception(ValueError("API key not set"), p) == {"kind": "config", "title": "Not set up yet", "detail": "API key not set"}
    assert CP.explain_exception(RuntimeError("HTTP 429: slow down"), p)["kind"] == "rate"
    assert CP.explain_exception(RuntimeError("HTTP ???: odd"), p)["kind"] == "other"
    assert CP.explain_exception(KeyError("x"), p)["kind"] == "other"
    q = CP.transient("https://api.example.com/v1")
    assert CP.explain_exception(RuntimeError("HTTP 404: model not found"), q)["kind"] in ("model", "address")


def ok_answer(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(200, json={"choices": [{"message": {"content": " ok "}}], "usage": {"prompt_tokens": 12, "completion_tokens": 1}, "echo": body["model"]})


def test_test_call_succeeds_and_is_charged_to_the_ledger(tmp_path):
    from app.budget import BudgetGuard
    st = store(tmp_path)
    p = make(st, auth_mode="api_key", api_key="sk-1", organization="o1", headers={"X-A": "1"}, model_prices={"*": {"input_per_m": 1000, "output_per_m": 2000}})
    guard = BudgetGuard(st.db, st)
    seen: dict = {}

    def handler(request):
        seen.update(request.headers)
        seen["url"] = str(request.url)
        return ok_answer(request)
    res = run(CP.test_endpoint(p, None, guard, mock_transport(handler)))
    assert res["ok"] and res["reply"] == "ok" and res["model"] == "qwen" and res["scope"] == "this_pc" and res["scope_label"] == "on this PC"
    assert seen["url"] == "http://127.0.0.1:8000/v1/chat/completions" and seen["authorization"] == "Bearer sk-1" and seen["openai-organization"] == "o1" and seen["x-a"] == "1"
    row = st.db.one("SELECT * FROM ledger WHERE provider=? AND kind='call'", (p.id,))
    assert row["input_tokens"] == 12 and row["output_tokens"] == 1 and row["note"] == "endpoint_test"
    open_ = make(st, label="NoGuard")
    r2 = run(CP.test_endpoint(open_, "m2", None, mock_transport(ok_answer)))
    assert r2["ok"] and r2["model"] == "m2"


def test_test_call_reports_each_kind_of_failure_and_releases_the_reservation(tmp_path):
    from app.budget import BudgetGuard
    st = store(tmp_path)
    p = make(st, label="Gateway", base="https://api.example.com/v1")
    guard = BudgetGuard(st.db, st)

    def call(handler, **kw):
        return run(CP.test_endpoint(p, kw.get("model"), guard, mock_transport(handler)))
    r = call(lambda req: httpx.Response(401, text="no"))
    assert not r["ok"] and r["kind"] == "auth" and r["scope"] == "internet" and r["scope_label"] == "data leaves this PC" and r["latency_ms"] >= 0
    assert call(lambda req: httpx.Response(404, text="model 'qwen' not found"))["kind"] == "model"
    assert call(lambda req: httpx.Response(200, text="<html>not json</html>"))["kind"] == "other"
    assert call(lambda req: httpx.Response(200, json={"choices": []}))["ok"]                          # an empty reply still proves the call works

    def boom(req):
        raise httpx.ConnectError("refused")
    assert call(boom)["kind"] == "unreachable"
    assert guard.reserved(p.id) == 0                                                                   # every failed call let go of its reservation
    assert run(CP.test_endpoint(p, " ", guard))["kind"] == "config"
    st.db.execute("UPDATE providers SET api_base=NULL WHERE id=?", (p.id,))
    assert run(CP.test_endpoint(st.get(p.id), "m", guard))["kind"] == "config"


def test_test_call_respects_the_spend_cap(tmp_path):
    from app.budget import BudgetGuard
    st = store(tmp_path)
    p = make(st, cap_usd=0.0)
    res = run(CP.test_endpoint(p, "m", BudgetGuard(st.db, st), mock_transport(ok_answer)))
    assert not res["ok"] and res["kind"] == "budget" and "REFUSED" in res["detail"]

    class Boom:
        def reserve(self, *a):
            raise BudgetExceeded({"message": "nope"})
    assert run(CP.test_endpoint(p, "m", Boom(), mock_transport(ok_answer)))["detail"] == "nope"


# ------------------------------------------------------------------------------------------------ the API
BODY = {"label": "Office GPU", "api_base": "http://192.168.1.5:8000", "default_model": "qwen3", "auth_mode": "api_key", "api_key": "sk-office-key-123",
        "headers": {"X-Team": "ml"}, "model_prices": {"*": {"input_per_m": 0, "output_per_m": 0}}, "manual_models": ["qwen3"]}


REAL_CLIENT = httpx.AsyncClient


def routed(monkeypatch, handler):
    def fake(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return REAL_CLIENT(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", fake)


def test_api_creates_lists_tests_edits_and_deletes_an_endpoint(client, monkeypatch):
    r = client.post("/api/providers/custom", json=BODY)
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == "custom_office_gpu" and d["custom"] and d["network_scope"] == "network" and d["has_api_key"] and d["catalog"]["custom"]
    assert "sk-office-key-123" not in json.dumps(d) and d["extra_header_names"] == ["X-Team"]
    assert any(p["id"] == d["id"] for p in client.get("/api/providers").json())
    assert client.post("/api/providers/custom", json={**BODY, "api_base": "nope"}).status_code == 422
    assert client.post("/api/providers/custom", json={**BODY, "label": "Keyless", "api_key": None, "headers": None}).status_code == 200

    routed(monkeypatch, lambda req: httpx.Response(200, json={"data": [{"id": "qwen3"}]}) if req.url.path.endswith("/models") else ok_answer(req))
    t = client.post(f"/api/providers/{d['id']}/test", json={}).json()
    assert t["ok"] and t["model"] == "qwen3"
    assert client.post("/api/providers/openai/test", json={}).status_code == 404 and client.post("/api/providers/zzz/test", json={}).status_code == 404
    models = client.get(f"/api/providers/{d['id']}/models").json()
    assert models["source"] == "live" and [m["id"] for m in models["models"]] == ["qwen3"]

    u = client.put(f"/api/providers/{d['id']}", json={"label": "Office GPU 2", "model_prices": {"qwen3": {"input_per_m": 1, "output_per_m": 2, "context": 8192}}, "manual_models": ["a"]})
    assert u.status_code == 200 and u.json()["label"] == "Office GPU 2" and u.json()["model_prices"]["qwen3"]["context"] == 8192 and u.json()["manual_models"] == ["a"]
    assert client.put(f"/api/providers/{d['id']}", json={"model_prices": {"m": {"input_per_m": 1}}}).status_code == 422
    assert client.delete("/api/providers/openai").status_code == 422
    assert client.delete("/api/providers/zzz").status_code == 404
    assert client.delete(f"/api/providers/{d['id']}").json() == {"ok": True}
    assert not any(p["id"] == d["id"] for p in client.get("/api/providers").json())


def test_api_discover_before_saving(client, monkeypatch):
    routed(monkeypatch, lambda req: httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]}))
    r = client.post("/api/providers/custom/discover", json={"api_base": "http://10.0.0.2:8000", "auth_mode": "api_key", "api_key": "k"})
    assert r.json()["ok"] and r.json()["models"] == ["m1", "m2"]
    assert client.post("/api/providers/custom/discover", json={"api_base": "ftp://x"}).status_code == 422
    routed(monkeypatch, lambda req: httpx.Response(401, text="denied"))
    bad = client.post("/api/providers/custom/discover", json={"api_base": "http://10.0.0.2:8000", "auth_mode": "api_key", "api_key": "k"}).json()
    assert not bad["ok"] and bad["kind"] == "auth"


def test_a_custom_endpoint_is_used_for_chat_pricing_and_the_ai_bom(client, state, monkeypatch):
    d = client.post("/api/providers/custom", json={**BODY, "api_key": None, "auth_mode": "none", "headers": None,
                                                    "model_prices": {"qwen3": {"input_per_m": 0.2, "output_per_m": 0.4, "context": 4096}}}).json()
    pre = client.post("/api/billing/preflight", json={"provider": d["id"], "model": "qwen3", "message": "hello"}).json()
    assert pre["price_known"] and pre["price_source"] == "endpoint" and pre["would_allow"]
    ctx = state.chat.context_info("s1", d["id"], "qwen3")
    assert ctx["window_tokens"] == 4096
    cands, _ = state.auto.candidates(state.auto.config(), 20)
    mine = [c for c in cands if c.provider == d["id"]]
    assert mine and mine[0].in_per_m == 0.2 and mine[0].kind == "openai_compatible"
    bom = client.get("/api/aibom?level=internal").json()
    svc = next(s for s in bom["services"] if s["bom-ref"] == f"provider:{d['id']}")
    props = {p["name"].split(":")[-1]: p["value"] for p in svc["properties"]}
    assert props["custom-endpoint"] == "true" and props["network-scope"] == "network" and props["data-leaves-machine"] == "true"
    models = [c for c in bom["components"] if c.get("group") == d["id"]]
    assert models and models[0]["supplier"]["name"] == "Office GPU"
    assert "sk-office" not in json.dumps(bom)


def test_a_local_endpoint_is_marked_as_staying_on_this_pc_in_the_ai_bom(client):
    d = client.post("/api/providers/custom", json={"label": "Local", "api_base": "http://localhost:1234", "default_model": "m", "auth_mode": "none"}).json()
    bom = client.get("/api/aibom?level=internal").json()
    svc = next(s for s in bom["services"] if s["bom-ref"] == f"provider:{d['id']}")
    assert any(p["name"].endswith("data-leaves-machine") and p["value"] == "false" for p in svc["properties"])


def test_settings_file_carries_endpoints_without_keys_and_imports_them_switched_off(client):
    client.post("/api/providers/custom", json=BODY)
    exp = client.get("/api/settings/export").json()
    assert exp["custom_providers"][0]["label"] == "Office GPU" and "sk-office-key-123" not in json.dumps(exp)
    # a second machine: same file, endpoint absent there
    client.delete("/api/providers/custom_office_gpu")
    plan = client.post("/api/settings/import", json={"data": exp, "dry_run": True}).json()
    assert any(c["key"] == "custom_providers" and "added switched off" in c["note"] for c in plan["changes"])
    done = client.post("/api/settings/import", json={"data": exp, "dry_run": False}).json()
    assert done["applied"] and not done["errors"]
    p = next(x for x in client.get("/api/providers").json() if x["id"] == "custom_office_gpu")
    assert p["enabled"] is False and p["has_api_key"] is False and p["manual_models"] == ["qwen3"]
    again = client.post("/api/settings/import", json={"data": exp, "dry_run": True}).json()
    assert any("already exists" in e["error"] for e in again["errors"])
    odd = {"format": "agent-studio-settings", "version": 1, "settings": {}, "custom_providers": [{"label": ""}, {"label": "Bad", "api_base": "nope", "default_model": "m"}, "junk"]}
    plan = client.post("/api/settings/import", json={"data": odd, "dry_run": False}).json()
    msgs = " | ".join(e["error"] for e in plan["errors"])
    assert "no name" in msgs and "Bad" in msgs
    assert client.post("/api/settings/import", json={"data": {**odd, "custom_providers": "x"}, "dry_run": True}).json()["errors"][0]["error"] == "custom endpoints must be a list"
