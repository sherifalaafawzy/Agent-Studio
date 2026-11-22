"""Coverage for app/providers.py validation and credential-resolution branches (no network, no real keys)."""
import json

import pytest

from app import providers as P
from app.providers import CATALOG, Provider, _resolve, _sa_email


def mkp(kind, **kw):
    base = dict(id=kind, kind=kind, label=CATALOG[kind]["label"], enabled=True, api_key=None, api_base=None,
                vertex_project=None, vertex_location=None, vertex_credentials_path=None, default_model=None,
                cap_usd=0.0, cap_period="monthly", max_output_tokens=64)
    base.update(kw)
    return Provider(**base)


# ---------- small helpers ----------

def test_extra_headers_are_parsed_defensively():
    assert mkp("openai", extra_headers='{"X-Team": "ops", "X-N": 3}').headers() == {"X-Team": "ops", "X-N": "3"}
    assert mkp("openai", extra_headers="{broken").headers() == {}
    assert mkp("openai", extra_headers='["not", "an", "object"]').headers() == {}
    assert mkp("openai", extra_headers=None).headers() == {}


def test_service_account_email_is_read_only_when_well_formed():
    assert _sa_email('{"client_email": "svc@proj.iam.gserviceaccount.com"}') == "svc@proj.iam.gserviceaccount.com"
    assert _sa_email("not json") is None
    assert _sa_email('["list instead of object"]') is None      # .get on a list -> AttributeError -> None
    assert _sa_email(None) is None


# ---------- ProviderStore.update validation ----------

@pytest.fixture
def store(state):
    return state.providers


def test_update_unknown_provider_is_a_key_error(store):
    with pytest.raises(KeyError):
        store.update("no-such-provider", {"cap_usd": 1})


def test_secret_fields_left_as_none_are_unchanged(store):
    store.update("openai", {"api_key": "sk-first-123456789"})
    store.update("openai", {"api_key": None, "cap_usd": 5})      # None on a secret = leave it alone
    p = store.get("openai")
    assert p.api_key == "sk-first-123456789" and p.cap_usd == 5.0


@pytest.mark.parametrize("patch, message", [
    ({"cap_usd": -1}, "cap_usd must be >= 0"),
    ({"cap_period": "hourly"}, "cap_period must be"),
    ({"max_output_tokens": 1}, "max_output_tokens out of range"),
    ({"extra_headers": "not-a-dict"}, "extra_headers must be an object"),
    ({"vertex_credentials_json": "{not json"}, "not valid JSON"),
    ({"vertex_credentials_json": '{"project_id": "x"}'}, 'must be a Google credentials object'),
])
def test_invalid_provider_settings_are_refused(store, patch, message):
    with pytest.raises(ValueError, match=message):
        store.update("vertex_ai" if "vertex_credentials_json" in patch else "openai", patch)


def test_extra_headers_none_leaves_headers_unchanged_and_object_is_stored(store):
    store.update("openai", {"extra_headers": {" X-Team ": "ops", "  ": "dropped"}})
    assert store.get("openai").headers() == {"X-Team": "ops"}
    store.update("openai", {"extra_headers": None})              # None is skipped, not cleared
    assert store.get("openai").headers() == {"X-Team": "ops"}


# ---------- _resolve: credential paths per provider ----------

def test_vertex_service_account_file_needs_a_path(tmp_path):
    with pytest.raises(ValueError, match="set the service-account JSON path"):
        _resolve(mkp("vertex_ai", auth_mode="service_account_file"), None)


def test_vertex_service_account_file_must_exist(tmp_path):
    with pytest.raises(ValueError, match="credentials file not found"):
        _resolve(mkp("vertex_ai", auth_mode="service_account_file", vertex_credentials_path=str(tmp_path / "gone.json")), None)


def test_vertex_service_account_file_must_be_readable_json(tmp_path):
    bad = tmp_path / "sa.json"
    bad.write_text("this is not json", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read credentials file"):
        _resolve(mkp("vertex_ai", auth_mode="service_account_file", vertex_credentials_path=str(bad)), None)


def test_vertex_service_account_file_resolves_project_and_custom_endpoint(tmp_path):
    f = tmp_path / "sa.json"
    f.write_text(json.dumps({"project_id": "proj-sa", "client_email": "x@y.iam"}), encoding="utf-8")
    target, kw, how = _resolve(mkp("vertex_ai", auth_mode="service_account_file", vertex_credentials_path=str(f),
                                   api_base="https://vpc.example.test"), "gemini-2.5-pro")
    assert kw["vertex_project"] == "proj-sa" and kw["vertex_location"] == "global"
    assert json.loads(kw["vertex_credentials"])["client_email"] == "x@y.iam"
    assert kw["api_base"] == "https://vpc.example.test"
    assert "proj-sa" in how and target.endswith("gemini-2.5-pro")


def test_vertex_inline_json_needs_content_and_a_project():
    with pytest.raises(ValueError, match="paste the service-account JSON"):
        _resolve(mkp("vertex_ai", auth_mode="service_account_json"), None)
    with pytest.raises(ValueError, match="set the GCP project ID"):
        _resolve(mkp("vertex_ai", auth_mode="service_account_json", vertex_credentials_json='{"type": "service_account"}'), None)
    _, kw, _ = _resolve(mkp("vertex_ai", auth_mode="service_account_json", vertex_location="europe-west1",
                            vertex_credentials_json='{"type": "service_account", "quota_project_id": "quota-proj"}'), None)
    assert kw["vertex_project"] == "quota-proj" and kw["vertex_location"] == "europe-west1"


def test_bedrock_keys_and_profile_need_their_fields(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with pytest.raises(ValueError, match="set the AWS region"):
        _resolve(mkp("bedrock", auth_mode="aws_default"), None)
    with pytest.raises(ValueError, match="access key id and secret"):
        _resolve(mkp("bedrock", auth_mode="aws_keys", aws_region="us-east-1", aws_access_key_id="AKIAXYZ"), None)
    with pytest.raises(ValueError, match="profile name"):
        _resolve(mkp("bedrock", auth_mode="aws_profile", aws_region="us-east-1"), None)
    _, kw, how = _resolve(mkp("bedrock", auth_mode="aws_keys", aws_region="us-east-1", aws_access_key_id="AKIAXYZ",
                              aws_secret_access_key="s3cret", aws_session_token="tok"), None)
    assert kw["aws_session_token"] == "tok" and kw["aws_access_key_id"] == "AKIAXYZ" and how.startswith("AWS keys AKIA")
    _, kw, how = _resolve(mkp("bedrock", auth_mode="aws_profile", aws_region="eu-west-1", aws_profile="work"), None)
    assert kw["aws_profile_name"] == "work" and "work" in how


def test_bedrock_api_key_mode_becomes_bearer_token(monkeypatch):
    _, kw, how = _resolve(mkp("bedrock", auth_mode="api_key", aws_region="us-west-2", api_key="bedrock-bearer-123"), None)
    assert kw["api_key"] == "bedrock-bearer-123" and "Bedrock API key" in how


def test_generic_api_key_provider_uses_the_key(monkeypatch):
    target, kw, how = _resolve(mkp("openai", auth_mode="api_key", api_key="sk-abc-123456789"), "gpt-5-mini")
    assert kw == {"api_key": "sk-abc-123456789"} and target == "openai/gpt-5-mini" and how
