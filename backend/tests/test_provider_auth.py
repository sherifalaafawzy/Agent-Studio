"""Every connector accepts every auth method its service supports; secrets never come back out of the API."""
import json

import pytest

from app.db import Database
from app.providers import VERTEX_EXPRESS_BASE, ProviderStore, litellm_target

SA = {"type": "service_account", "project_id": "proj-from-json", "client_email": "bot@proj.iam.gserviceaccount.com", "private_key": "-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----\n"}


def prov(client, pid):
    return next(p for p in client.get("/api/providers").json() if p["id"] == pid)


def put(client, pid, **kw):
    return client.put(f"/api/providers/{pid}", json=kw)


def target(state, pid, model="m1"):
    return litellm_target(state.providers.get(pid), model)


def test_every_connector_lists_its_auth_modes(client):
    modes = {p["id"]: [m["id"] for m in p["auth_modes"]] for p in client.get("/api/providers").json()}
    assert modes["vertex_ai"] == ["adc", "service_account_file", "service_account_json", "api_key", "env"]
    assert modes["gemini"] == ["api_key", "env"]
    for k in ("openai", "anthropic", "xai", "groq", "openrouter"):
        assert modes[k] == ["api_key", "env", "none"]
    assert modes["ollama"] == ["none", "api_key", "env"] and modes["mock"] == ["none"]


def test_vertex_api_key_express_mode(client, state):
    assert put(client, "vertex_ai", auth_mode="api_key", api_key="AQ.test-key-0000000000").status_code == 200
    t, kw = target(state, "vertex_ai", "gemini-x")
    assert t == "gemini/gemini-x" and kw["api_key"] == "AQ.test-key-0000000000" and kw["api_base"] == VERTEX_EXPRESS_BASE
    assert "vertex_project" not in kw                                     # no project needed for express mode
    p = prov(client, "vertex_ai")
    assert p["auth_ready"] and "express" in p["auth_detail"] and "AQ.test-key" not in json.dumps(p)


def test_vertex_adc_needs_a_project(client, state):
    put(client, "vertex_ai", auth_mode="adc")
    assert not prov(client, "vertex_ai")["auth_ready"]
    put(client, "vertex_ai", vertex_project="p1", vertex_location="europe-west4")
    t, kw = target(state, "vertex_ai", "gemini-x")
    assert t == "vertex_ai/gemini-x" and kw == {"vertex_project": "p1", "vertex_location": "europe-west4"}


def test_vertex_service_account_pasted_and_file(client, state, tmp_path):
    r = put(client, "vertex_ai", auth_mode="service_account_json", vertex_credentials_json=json.dumps(SA))
    assert r.status_code == 200
    _, kw = target(state, "vertex_ai")
    assert kw["vertex_project"] == "proj-from-json" and json.loads(kw["vertex_credentials"])["client_email"] == SA["client_email"]
    p = prov(client, "vertex_ai")
    assert p["has_service_account_json"] and p["service_account_email"] == SA["client_email"] and "PRIVATE KEY" not in json.dumps(p)
    assert put(client, "vertex_ai", vertex_credentials_json="not json").status_code == 422
    assert put(client, "vertex_ai", vertex_credentials_json='{"no": "type"}').status_code == 422
    f = tmp_path / "sa.json"
    put(client, "vertex_ai", auth_mode="service_account_file", vertex_credentials_path=str(tmp_path / "missing.json"))
    assert "not found" in prov(client, "vertex_ai")["auth_detail"]
    f.write_text(json.dumps(SA))
    put(client, "vertex_ai", vertex_credentials_path=str(f), vertex_project="explicit")
    assert target(state, "vertex_ai")[1]["vertex_project"] == "explicit"


def test_env_var_mode(client, state, monkeypatch):
    put(client, "openai", auth_mode="env", api_key_env="MY_OPENAI_KEY")
    assert "MY_OPENAI_KEY is not set" in prov(client, "openai")["auth_detail"]
    monkeypatch.setenv("MY_OPENAI_KEY", "sk-from-env")
    assert target(state, "openai")[1]["api_key"] == "sk-from-env"
    put(client, "gemini", auth_mode="env")                                  # default variable name per connector
    monkeypatch.setenv("GEMINI_API_KEY", "g-env")
    assert target(state, "gemini") == ("gemini/m1", {"api_key": "g-env"})
    put(client, "vertex_ai", auth_mode="env", api_key_env="VX_KEY")
    monkeypatch.setenv("VX_KEY", "AQ.env")
    assert target(state, "vertex_ai")[1]["api_key"] == "AQ.env"
    assert put(client, "openai", api_key_env="bad name!").status_code == 422


def test_no_auth_proxy_mode_headers_and_org(client, state):
    r = put(client, "openai", auth_mode="none", api_base="https://gateway.local/v1/", organization="org-1",
            extra_headers={"X-Proxy-Token": "secret-hdr", "X-Team": "a"})
    assert r.status_code == 200
    t, kw = target(state, "openai")
    assert kw == {"api_key": "no-key", "api_base": "https://gateway.local/v1", "organization": "org-1",
                  "extra_headers": {"X-Proxy-Token": "secret-hdr", "X-Team": "a"}}
    p = prov(client, "openai")
    assert p["extra_header_names"] == ["X-Proxy-Token", "X-Team"] and "secret-hdr" not in json.dumps(p)
    put(client, "openai", extra_headers={})
    assert "extra_headers" not in target(state, "openai")[1]
    assert put(client, "openai", auth_mode="adc").status_code == 422        # not a mode OpenAI supports
    assert put(client, "openai", api_base="ftp://x").status_code == 422


def test_ollama_optional_key(client, state):
    assert target(state, "ollama")[1] == {"api_base": "http://127.0.0.1:11434", "num_ctx": 16384}      # fixed context (no 40k default)
    put(client, "ollama", auth_mode="api_key", api_key="ol-remote-key-123", api_base="https://ollama.example", context_window=8192)
    assert target(state, "ollama")[1] == {"api_key": "ol-remote-key-123", "api_base": "https://ollama.example", "num_ctx": 8192}
    assert put(client, "ollama", context_window=100).status_code == 422


def test_existing_vertex_rows_are_migrated(tmp_path):
    db = Database(tmp_path / "t.db")
    ProviderStore(db)
    db.execute("UPDATE providers SET auth_mode=NULL, vertex_credentials_path='C:/k/sa.json' WHERE id='vertex_ai'")
    ProviderStore(db)
    assert db.one("SELECT auth_mode FROM providers WHERE id='vertex_ai'")["auth_mode"] == "service_account_file"
