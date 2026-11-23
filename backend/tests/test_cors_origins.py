"""The local API must not trust the "null" origin (file:// pages and every sandboxed iframe on any website send it)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import main


def test_null_origin_is_not_allowed_by_default():
    origins = main.allowed_origins({})
    assert "null" not in origins and "http://127.0.0.1:3100" in origins and "app://agent-studio" in origins
    assert "null" not in main.ALLOWED_ORIGINS


def test_null_origin_is_opt_in_and_extra_origins_are_added():
    assert "null" in main.allowed_origins({"AGENT_STUDIO_ALLOW_NULL_ORIGIN": "1"})
    assert "null" in main.allowed_origins({"AGENT_STUDIO_ALLOW_NULL_ORIGIN": " TRUE "})
    assert "null" not in main.allowed_origins({"AGENT_STUDIO_ALLOW_NULL_ORIGIN": "0"})
    assert "http://127.0.0.1:3200" in main.allowed_origins({"AGENT_STUDIO_EXTRA_ORIGINS": " http://127.0.0.1:3200 , "})


def test_a_sandboxed_frame_cannot_read_the_api(client: TestClient):
    r = client.get("/api/health", headers={"Origin": "null"})
    assert "access-control-allow-origin" not in r.headers
    ok = client.get("/api/health", headers={"Origin": "http://127.0.0.1:3100"})
    assert ok.headers.get("access-control-allow-origin") == "http://127.0.0.1:3100"
