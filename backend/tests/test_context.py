"""Context window: real limits, real usage, summarise-and-resume."""
from app import context as CX


def test_window_sizes_come_from_the_model_catalog():
    assert CX.window_for("anthropic/claude-opus-5-5") == 1_000_000
    assert CX.window_for("ollama_chat/qwen3:8b") == 40960
    assert CX.window_for("ollama_chat/qwen3:8b", override=16384) == 16384       # the context you set for Ollama wins
    assert CX.window_for("made-up/model-xyz") == CX.FALLBACK_WINDOW


def test_usage_uses_the_provider_reported_size_of_the_last_call():
    msgs = [{"role": "user", "text": "hi", "meta": {}},
            {"role": "bot", "text": "hello", "meta": {"calls": [{"input_tokens": 1200, "output_tokens": 300, "usage_reported": True}]}}]
    assert CX.usage_for(msgs)["used_tokens"] == 1500
    est = CX.usage_for([{"role": "user", "text": "x" * 3600, "meta": {}}])
    assert est["used_tokens"] > 1000 and "estimated" in est["source"]


def test_summary_point_splits_history(client):
    msgs = [{"id": 1, "role": "user", "text": "a"}, {"id": 2, "role": "summary", "text": "S"}, {"id": 3, "role": "bot", "text": "b"}]
    summ, after = CX.summary_point(msgs)
    assert summ["id"] == 2 and [m["id"] for m in after] == [3]


def test_summarise_then_resume_from_the_summary(client, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 64})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    sid = client.post("/api/chat", json={"provider": "mock", "message": "Plan a migration to Postgres"}).json()["session_id"]
    info = client.get(f"/api/chat/sessions/{sid}/context").json()
    assert info["window_tokens"] > 0 and 0 <= info["percent"] <= 100 and info["level"] == "ok" and not info["summarized"]   # a short chat is fine
    r = client.post(f"/api/chat/sessions/{sid}/summarize")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"].startswith("[mock] Summary") and body["covered_messages"] == 2 and body["session_resumed"] is True
    after = client.get(f"/api/chat/sessions/{sid}/context").json()
    assert after["summarized"] is True and after["messages_after_summary"] == 0
    msgs = client.get(f"/api/chat/sessions/{sid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "bot", "summary"]                 # the originals stay visible
    # the model now resumes from the summary, not the full transcript
    import app.chat as chat_mod
    replay = client.app.state.s.chat._replay_rows(sid)
    assert len(replay) == 1 and replay[0]["text"].startswith("[Summary of the conversation so far]")
    client.post("/api/chat", json={"provider": "mock", "message": "and then?", "session_id": sid})
    assert client.get(f"/api/chat/sessions/{sid}/context").json()["messages_after_summary"] == 2


def test_nothing_to_summarise_is_explained(client, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 64})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    sid = client.post("/api/chat", json={"provider": "mock", "message": "hi"}).json()["session_id"]
    client.post(f"/api/chat/sessions/{sid}/summarize")
    r = client.post(f"/api/chat/sessions/{sid}/summarize")
    assert r.status_code == 422 and "nothing new" in r.json()["detail"]
    assert client.post("/api/chat/sessions/nope/summarize").status_code == 404
