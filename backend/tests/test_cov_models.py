"""Coverage for app/models.py paths that the main model-list tests replace with a fake: the real HTTP helper,
Google OAuth token selection, Bedrock listing (boto3 faked), Vertex regional hosts, OpenRouter pricing
edge cases, the Bedrock catalog prefix rules and the 'no chat models' fallback. No network, no credentials."""
import asyncio
import json

import google.auth
import httpx
import pytest
from google.oauth2 import service_account

from app import models as M
from app.providers import CATALOG, Provider


@pytest.fixture(autouse=True)
def _fresh_cache():
    M._cache.clear()
    yield
    M._cache.clear()


def mkp(kind, **kw):
    base = dict(id=kind, kind=kind, label=CATALOG[kind]["label"], enabled=True, api_key=None, api_base=None,
                vertex_project=None, vertex_location=None, vertex_credentials_path=None, default_model=None,
                cap_usd=0.0, cap_period="monthly", max_output_tokens=64)
    base.update(kw)
    return Provider(**base)


@pytest.fixture
def http(monkeypatch):
    """Route the real httpx.AsyncClient used by _get_json to a handler."""
    state = {"fn": None, "seen": []}

    def handler(req):
        state["seen"].append(req)
        return state["fn"](req)

    real = httpx.AsyncClient
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    return state


# ---------- _get_json ----------

def test_get_json_returns_parsed_body_and_sends_headers(http):
    http["fn"] = lambda req: httpx.Response(200, json={"ok": True})
    out = asyncio.run(M._get_json("https://api.example.test/models", {"X-Test": "1"}, {"limit": 5}))
    assert out == {"ok": True}
    req = http["seen"][0]
    assert req.headers["X-Test"] == "1" and req.url.params["limit"] == "5"


@pytest.mark.parametrize("resp, expected", [
    (httpx.Response(401, json={"error": {"message": "bad key"}}), "HTTP 401: bad key"),
    (httpx.Response(500, json={"error": "plain string error"}), "HTTP 500: plain string error"),
    (httpx.Response(503, text="service down"), "HTTP 503: service down"),
])
def test_get_json_turns_error_responses_into_readable_errors(http, resp, expected):
    http["fn"] = lambda req: resp
    with pytest.raises(RuntimeError) as e:
        asyncio.run(M._get_json("https://api.example.test/models"))
    assert str(e.value) == expected


# ---------- Google OAuth token selection ----------

class FakeCreds:
    def __init__(self, token, project_id=None):
        self.token = token
        self.project_id = project_id
        self.refreshed = False

    def refresh(self, request):
        self.refreshed = True


def test_google_token_from_service_account_file_uses_creds_project(monkeypatch):
    made = []

    def from_file(path, scopes):
        made.append((path, scopes))
        return FakeCreds("tok-file", project_id="proj-from-file")

    monkeypatch.setattr(service_account.Credentials, "from_service_account_file", staticmethod(from_file))
    p = mkp("vertex_ai", auth_mode="service_account_file", vertex_credentials_path="/keys/sa.json")
    token, project = M._google_token(p)
    assert (token, project) == ("tok-file", "proj-from-file")
    assert made[0][0] == "/keys/sa.json" and made[0][1] == ["https://www.googleapis.com/auth/cloud-platform"]


def test_google_token_from_inline_json_prefers_configured_project(monkeypatch):
    info_seen = []

    def from_info(info, scopes):
        info_seen.append(info)
        return FakeCreds("tok-json", project_id=None)

    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", staticmethod(from_info))
    creds_json = json.dumps({"project_id": "json-proj"})
    token, project = M._google_token(mkp("vertex_ai", auth_mode="service_account_json", vertex_credentials_json=creds_json))
    assert token == "tok-json" and project == "json-proj" and info_seen == [{"project_id": "json-proj"}]
    token, project = M._google_token(mkp("vertex_ai", auth_mode="service_account_json", vertex_credentials_json=creds_json,
                                         vertex_project="explicit-proj"))
    assert project == "explicit-proj"


def test_google_token_default_credentials(monkeypatch):
    fake = FakeCreds("tok-adc")
    monkeypatch.setattr(google.auth, "default", lambda scopes: (fake, "adc-proj"))
    token, project = M._google_token(mkp("vertex_ai", auth_mode="adc"))
    assert token == "tok-adc" and project == "adc-proj" and fake.refreshed is True
    _, project = M._google_token(mkp("vertex_ai", auth_mode="adc", vertex_project="set-proj"))
    assert project == "set-proj"


# ---------- Vertex live listing (OAuth path + regional host) ----------

def test_vertex_oauth_listing_uses_regional_host_and_project_header(monkeypatch, http):
    monkeypatch.setattr(M, "_google_token", lambda p: ("tok-v", "proj-v"))
    http["fn"] = lambda req: httpx.Response(200, json={"publisherModels": [
        {"name": "publishers/google/models/gemini-2.5-pro"}, {"name": "publishers/google/models/imagen-4"}]})
    out = asyncio.run(M._live(mkp("vertex_ai", auth_mode="adc", vertex_location="europe-west4")))
    assert out == [{"id": "gemini-2.5-pro"}]
    req = http["seen"][0]
    assert req.url.host == "europe-west4-aiplatform.googleapis.com"
    assert req.headers["Authorization"] == "Bearer tok-v" and req.headers["x-goog-user-project"] == "proj-v"


def test_vertex_oauth_listing_global_host_without_project(monkeypatch, http):
    monkeypatch.setattr(M, "_google_token", lambda p: ("tok-g", None))
    http["fn"] = lambda req: httpx.Response(200, json={"publisherModels": [], "nextPageToken": None})
    assert asyncio.run(M._live(mkp("vertex_ai", auth_mode="adc"))) == []
    req = http["seen"][0]
    assert req.url.host == "aiplatform.googleapis.com" and "x-goog-user-project" not in req.headers


# ---------- Bedrock listing (boto3 faked) ----------

class FakeBedrock:
    def __init__(self):
        self.profile_pages = []

    def list_foundation_models(self, byOutputModality):
        assert byOutputModality == "TEXT"
        return {"modelSummaries": [
            {"modelId": "anthropic.claude-x", "providerName": "Anthropic", "modelName": "Claude X", "inputModalities": ["TEXT"],
             "inferenceTypesSupported": ["ON_DEMAND"], "modelLifecycle": {"status": "ACTIVE"}},
            {"modelId": "old.model", "inputModalities": ["TEXT"], "inferenceTypesSupported": ["ON_DEMAND"],
             "modelLifecycle": {"status": "LEGACY"}},
            {"modelId": "image.only", "inputModalities": ["IMAGE"], "inferenceTypesSupported": ["ON_DEMAND"]},
            {"modelId": "provisioned.only", "inputModalities": ["TEXT"], "inferenceTypesSupported": ["PROVISIONED"]},
        ]}

    def list_inference_profiles(self, typeEquals, **kw):
        assert typeEquals == "SYSTEM_DEFINED"
        self.profile_pages.append(kw.get("nextToken"))
        if not kw.get("nextToken"):
            return {"inferenceProfileSummaries": [
                {"inferenceProfileId": "us.anthropic.claude-x", "inferenceProfileName": "US Claude X", "status": "ACTIVE"},
                {"inferenceProfileId": "eu.retired", "status": "INACTIVE"}], "nextToken": "page2"}
        return {"inferenceProfileSummaries": [{"inferenceProfileId": "apac.nameless", "status": "ACTIVE"}]}


def _fake_boto(monkeypatch, bedrock):
    sessions = []

    class FakeSession:
        def __init__(self, **kw):
            sessions.append(kw)

        def client(self, name):
            assert name == "bedrock"
            return bedrock

    import boto3
    monkeypatch.setattr(boto3, "Session", FakeSession)
    return sessions


def test_bedrock_listing_filters_text_on_demand_active_models(monkeypatch):
    bd = FakeBedrock()
    sessions = _fake_boto(monkeypatch, bd)
    out = M._bedrock_models(mkp("bedrock", auth_mode="aws_default", aws_region="us-east-1"))
    assert out == [
        {"id": "anthropic.claude-x", "label": "Anthropic Claude X"},
        {"id": "us.anthropic.claude-x", "label": "US Claude X"},
        {"id": "apac.nameless", "label": "apac.nameless"},
    ]
    assert bd.profile_pages == [None, "page2"]                  # followed the pagination token
    assert sessions == [{"region_name": "us-east-1"}]


def test_bedrock_session_uses_keys_or_profile_when_configured(monkeypatch):
    sessions = _fake_boto(monkeypatch, FakeBedrock())
    M._bedrock_models(mkp("bedrock", auth_mode="aws_keys", aws_region="eu-west-1", aws_access_key_id="AKIA", aws_secret_access_key="sec"))
    M._bedrock_models(mkp("bedrock", auth_mode="aws_profile", aws_profile="work"))
    assert sessions[0] == {"aws_access_key_id": "AKIA", "aws_secret_access_key": "sec", "aws_session_token": None, "region_name": "eu-west-1"}
    assert sessions[1] == {"profile_name": "work", "region_name": None}


def test_bedrock_refuses_api_key_mode_for_listing(monkeypatch):
    _fake_boto(monkeypatch, FakeBedrock())
    with pytest.raises(RuntimeError, match="needs AWS credentials"):
        M._bedrock_models(mkp("bedrock", auth_mode="api_key", api_key="bedrock-key"))


def test_live_dispatches_bedrock_to_thread_and_unknown_kind_is_empty(monkeypatch):
    monkeypatch.setattr(M, "_bedrock_models", lambda p: [{"id": "from-thread"}])
    assert asyncio.run(M._live(mkp("bedrock"))) == [{"id": "from-thread"}]
    assert asyncio.run(M._live(mkp("mock"))) == []              # no live listing for this kind


# ---------- OpenRouter pricing edge cases ----------

def test_openrouter_bad_or_missing_prices_become_unknown(http):
    http["fn"] = lambda req: httpx.Response(200, json={"data": [
        {"id": "a/free-ish", "name": "A", "pricing": {"prompt": "not-a-number", "completion": "0.000001"}},
        {"id": "b/no-pricing", "name": "B"},
        {"id": "c/ok", "name": "C", "pricing": {"prompt": "0.000002", "completion": "0.000003"}},
    ]})
    out = asyncio.run(M._live(mkp("openrouter")))
    by = {r["id"]: r for r in out}
    assert by["a/free-ish"]["input_per_m"] is None and by["a/free-ish"]["output_per_m"] is None
    assert by["b/no-pricing"]["input_per_m"] is None
    assert by["c/ok"]["input_per_m"] == pytest.approx(2.0) and by["c/ok"]["output_per_m"] == pytest.approx(3.0)


# ---------- offline catalog prefix rules ----------

def test_catalog_strips_bedrock_prefixes_and_skips_fine_tunes(monkeypatch):
    monkeypatch.setattr(M.litellm, "model_cost", {
        "bedrock/anthropic.claude-x": {"litellm_provider": "bedrock", "mode": "chat"},
        "bedrock/bedrock/claude-z": {"litellm_provider": "bedrock_converse", "mode": "chat"},
        "bedrock/ft:claude-y": {"litellm_provider": "bedrock", "mode": "chat"},
        "bedrock/titan-embed-text": {"litellm_provider": "bedrock", "mode": "embedding"},
    })
    ids = sorted(r["id"] for r in M.catalog("bedrock"))
    assert ids == ["anthropic.claude-x", "claude-z"]


# ---------- live list that comes back empty ----------

def test_empty_live_list_falls_back_to_catalog_with_reason(monkeypatch):
    async def empty(p):
        return []

    monkeypatch.setattr(M, "_live", empty)
    res = asyncio.run(M.list_models(mkp("openai", api_key="sk-test-123456789")))
    assert res["source"] == "catalog" and res["error"] == "RuntimeError: the provider returned no chat models"
    assert res["cached"] is False and len(res["models"]) > 0


def test_annotate_drops_duplicate_ids_and_sorts():
    p = mkp("openai")
    out = M._annotate(p, [{"id": "gpt-b", "input_per_m": 1.0, "output_per_m": 2.0}, {"id": "gpt-a", "input_per_m": 3.0, "output_per_m": 4.0},
                          {"id": "gpt-b", "input_per_m": 99.0, "output_per_m": 99.0}])
    assert [r["id"] for r in out] == ["gpt-a", "gpt-b"]
    assert next(r for r in out if r["id"] == "gpt-b")["input_per_m"] == 1.0   # first occurrence wins


def test_forget_drops_only_that_providers_cached_lists():
    M._cache["k1"] = (0.0, {"provider": "ollama"})
    M._cache["k2"] = (0.0, {"provider": "openai"})
    M.forget("ollama")
    assert "k1" not in M._cache and "k2" in M._cache
