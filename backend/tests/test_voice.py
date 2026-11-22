"""ElevenLabs voice: settings (key write-only), voices/models, TTS with a local character cap, STT, MCP server entry."""
import json

import httpx
import pytest

from app import voice as V


@pytest.fixture
def eleven(monkeypatch):
    """Fake ElevenLabs API (no network, no credits)."""
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.headers.get("xi-api-key") != "el-key-123456789":
            return httpx.Response(401, json={"detail": {"message": "invalid api key"}})
        p = req.url.path
        if p == "/v1/voices":
            return httpx.Response(200, json={"voices": [{"voice_id": "v1", "name": "Rachel", "category": "premade", "preview_url": "https://x/p.mp3"}]})
        if p == "/v1/models":
            return httpx.Response(200, json=[{"model_id": "eleven_flash_v2_5", "name": "Flash", "can_do_text_to_speech": True},
                                             {"model_id": "scribe_v1", "name": "Scribe", "can_do_text_to_speech": False}])
        if p == "/v1/user/subscription":
            return httpx.Response(200, json={"tier": "creator", "character_count": 1200, "character_limit": 100000})
        if p.startswith("/v1/text-to-speech/"):
            return httpx.Response(200, content=b"ID3fake-mp3", headers={"content-type": "audio/mpeg"})
        if p == "/v1/speech-to-text":
            assert b"scribe_v1" in req.content
            return httpx.Response(200, json={"text": " hello world ", "language_code": "en"})
        return httpx.Response(404, json={"detail": "nope"})

    real = httpx.AsyncClient

    def client(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)
    monkeypatch.setattr(V.httpx, "AsyncClient", client)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return seen


def test_not_configured_is_a_clear_error(client, eleven):
    assert client.get("/api/voice/settings").json()["configured"] is False
    r = client.post("/api/voice/tts", json={"text": "hi"})
    assert r.status_code == 412 and "Settings" in r.json()["detail"]


def test_settings_voices_models_subscription(client, eleven):
    r = client.put("/api/voice/settings", json={"api_key": "el-key-123456789", "voice_id": "v1", "auto_read": True, "char_cap": 50}).json()
    assert r["configured"] and r["key_hint"] == "…6789" and r["voice_id"] == "v1" and r["auto_read"] is True and r["char_cap"] == 50
    assert "el-key-123456789" not in json.dumps(client.get("/api/voice/settings").json())
    assert client.get("/api/voice/voices").json()[0]["name"] == "Rachel"
    assert [m["model_id"] for m in client.get("/api/voice/models").json()] == ["eleven_flash_v2_5"]
    assert client.get("/api/voice/subscription").json()["character_limit"] == 100000
    client.put("/api/voice/settings", json={"api_key": "wrong-key-0000000"})
    assert client.get("/api/voice/voices").status_code == 401


def test_tts_reads_clean_text_and_enforces_the_cap_before_sending(client, eleven):
    client.put("/api/voice/settings", json={"api_key": "el-key-123456789", "voice_id": "v1", "char_cap": 60})
    r = client.post("/api/voice/tts", json={"text": "## Result\nUse **this**:\n```py\nx = 1\n```\nDone."})
    assert r.status_code == 200 and r.content == b"ID3fake-mp3" and r.headers["content-type"] == "audio/mpeg"
    sent = json.loads(eleven[-1].content)
    assert "```" not in sent["text"] and "**" not in sent["text"] and "code block omitted" in sent["text"] and sent["model_id"] == "eleven_flash_v2_5"
    used = client.get("/api/voice/settings").json()["chars_used_month"]
    assert used == int(r.headers["X-Characters"]) > 0
    n_before = len(eleven)
    r = client.post("/api/voice/tts", json={"text": "x" * 200})
    assert r.status_code == 402 and "Monthly voice cap" in r.json()["detail"] and len(eleven) == n_before     # nothing sent


def test_stt(client, eleven):
    client.put("/api/voice/settings", json={"api_key": "el-key-123456789"})
    r = client.post("/api/voice/stt", files={"file": ("a.webm", b"RIFFfake", "audio/webm")})
    assert r.status_code == 200 and r.json() == {"text": "hello world", "language": "en"}
    assert client.post("/api/voice/stt", files={"file": ("a.webm", b"", "audio/webm")}).status_code == 400


def test_elevenlabs_mcp_server_listed_and_key_shared(client, eleven):
    m = next(x for x in client.get("/api/mcp").json() if x["id"] == "elevenlabs")
    assert m["args"] == ["elevenlabs-mcp==0.12.2"] and m["requires_env"] == ["ELEVENLABS_API_KEY"] and not m["enabled"]
    client.put("/api/voice/settings", json={"api_key": "el-key-123456789"})
    m = next(x for x in client.get("/api/mcp").json() if x["id"] == "elevenlabs")
    assert m["env_set"] == {"ELEVENLABS_API_KEY": True} and not m["enabled"]      # key shared, not switched on


def test_speakable():
    assert V.speakable("- **a** [link](http://x) `code`\n# T") == "a link code\nT"
