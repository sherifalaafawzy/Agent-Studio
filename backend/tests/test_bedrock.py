"""Amazon Bedrock connector: every AWS auth method, region, secrets never returned, live model list, prices, routing."""
import asyncio
import json

import litellm
import pytest

from app import models as M
from app.providers import litellm_target


def prov(client):
    return next(p for p in client.get("/api/providers").json() if p["id"] == "bedrock")


def put(client, **kw):
    return client.put("/api/providers/bedrock", json=kw)


def tgt(state, model="us.anthropic.claude-sonnet-5-5"):
    return litellm_target(state.providers.get("bedrock"), model)


def test_listed_with_all_auth_modes_and_defaults(client):
    p = prov(client)
    assert p["label"] == "Amazon Bedrock" and not p["enabled"] and p["aws_region"] == "us-east-1"
    assert [m["id"] for m in p["auth_modes"]] == ["aws_default", "aws_keys", "aws_profile", "api_key", "env"]
    assert p["default_model"] == "us.anthropic.claude-sonnet-5-5" and p["flash_model"] == "us.anthropic.claude-haiku-5-5"


def test_each_auth_mode_builds_the_right_litellm_call(client, state, monkeypatch):
    t, kw = tgt(state)                                                    # default chain
    assert t == "bedrock/us.anthropic.claude-sonnet-5-5" and kw == {"aws_region_name": "us-east-1"}
    assert put(client, auth_mode="aws_keys").status_code == 200
    assert "access key id and secret" in prov(client)["auth_detail"]
    put(client, aws_access_key_id="AKIAEXAMPLE12345678", aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYSECRET", aws_session_token="tok-123",
        aws_region="eu-central-1")
    _, kw = tgt(state)
    assert kw == {"aws_region_name": "eu-central-1", "aws_access_key_id": "AKIAEXAMPLE12345678",
                  "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYSECRET", "aws_session_token": "tok-123"}
    body = json.dumps(prov(client))
    assert "SECRET" not in body and "tok-123" not in body and "AKIAEXAMPLE12345678" not in body
    p = prov(client)
    assert p["has_aws_secret"] and p["has_aws_session_token"] and p["aws_access_key_hint"] == "AKIA…5678" and p["auth_ready"]
    put(client, auth_mode="aws_profile", aws_profile="work")
    assert tgt(state)[1] == {"aws_region_name": "eu-central-1", "aws_profile_name": "work"}
    put(client, auth_mode="api_key", api_key="ABSKbedrock-api-key-123")
    assert tgt(state)[1] == {"aws_region_name": "eu-central-1", "api_key": "ABSKbedrock-api-key-123"}
    put(client, auth_mode="env")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "from-env")
    assert tgt(state)[1]["api_key"] == "from-env"
    put(client, auth_mode="aws_default", api_base="https://vpce-123.bedrock-runtime.eu-central-1.vpce.amazonaws.com")
    assert tgt(state)[1]["api_base"].startswith("https://vpce-123")
    assert put(client, aws_region="Frankfurt").status_code == 422
    put(client, aws_region="")
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    assert "region" in prov(client)["auth_detail"]


def test_litellm_accepts_the_kwargs(state, client):
    put(client, auth_mode="aws_keys", aws_access_key_id="AKIAEXAMPLE12345678", aws_secret_access_key="x" * 40)
    t, kw = tgt(state)
    r = asyncio.run(litellm.acompletion(model=t, messages=[{"role": "user", "content": "hi"}], mock_response="pong", **kw))
    assert r.choices[0].message.content == "pong"


def test_price_known_for_cross_region_profiles():
    from app.pricing import price_for
    p = price_for("bedrock", "us.anthropic.claude-sonnet-5-5", "bedrock/us.anthropic.claude-sonnet-5-5")
    assert p.known and p.input_per_m > 0


def test_models_listed_live_with_inference_profiles(client, monkeypatch):
    M._cache.clear()

    class FakeBedrock:
        def list_foundation_models(self, **kw):
            assert kw == {"byOutputModality": "TEXT"}
            return {"modelSummaries": [
                {"modelId": "amazon.nova-pro-v1:0", "providerName": "Amazon", "modelName": "Nova Pro", "inputModalities": ["TEXT", "IMAGE"],
                 "inferenceTypesSupported": ["ON_DEMAND"], "modelLifecycle": {"status": "ACTIVE"}},
                {"modelId": "anthropic.claude-sonnet-5-5", "providerName": "Anthropic", "modelName": "Claude", "inputModalities": ["TEXT"],
                 "inferenceTypesSupported": ["INFERENCE_PROFILE"]},                         # profile-only -> listed via profiles
                {"modelId": "old.model-v1", "providerName": "X", "modelName": "Old", "inputModalities": ["TEXT"],
                 "inferenceTypesSupported": ["ON_DEMAND"], "modelLifecycle": {"status": "LEGACY"}}]}

        def list_inference_profiles(self, **kw):
            if kw.get("nextToken"):
                return {"inferenceProfileSummaries": [{"inferenceProfileId": "eu.anthropic.claude-haiku-5-5", "inferenceProfileName": "EU Haiku"}]}
            return {"inferenceProfileSummaries": [{"inferenceProfileId": "us.anthropic.claude-sonnet-5-5", "inferenceProfileName": "US Sonnet", "status": "ACTIVE"}],
                    "nextToken": "p2"}

    seen = {}

    class FakeSession:
        def __init__(self, **kw):
            seen.update(kw)

        def client(self, name):
            assert name == "bedrock"
            return FakeBedrock()

    import boto3
    monkeypatch.setattr(boto3, "Session", FakeSession)
    put(client, auth_mode="aws_keys", aws_access_key_id="AKIAEXAMPLE12345678", aws_secret_access_key="s" * 40, aws_region="us-west-2")
    r = client.get("/api/providers/bedrock/models").json()
    assert r["source"] == "live"
    assert [m["id"] for m in r["models"]] == ["amazon.nova-pro-v1:0", "eu.anthropic.claude-haiku-5-5", "us.anthropic.claude-sonnet-5-5"]
    assert seen["region_name"] == "us-west-2" and seen["aws_access_key_id"] == "AKIAEXAMPLE12345678"
    assert next(m for m in r["models"] if m["id"] == "us.anthropic.claude-sonnet-5-5")["input_per_m"] > 0
    M._cache.clear()
    put(client, auth_mode="api_key", api_key="ABSK-key-123456789")
    r = client.get("/api/providers/bedrock/models").json()                      # API keys can't list -> catalog, with the reason
    assert r["source"] == "catalog" and "needs AWS credentials" in r["error"] and any(m["id"].startswith("us.anthropic.") for m in r["models"])
    assert not any(m["id"].startswith("bedrock/") for m in r["models"])
