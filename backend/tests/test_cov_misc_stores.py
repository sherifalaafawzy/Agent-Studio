"""Coverage for edge branches in app/memory_store.py, app/budget.py, app/issues.py, app/chat_store.py,
app/context.py, app/voice.py and app/credits.py. HTTP is mocked with httpx.MockTransport; nothing leaves the machine."""
import asyncio
import datetime as dt
import json

import httpx
import pytest

from app import budget as B
from app import context as CX
from app import credits as C
from app import memory_store as MEM
from app import voice as V
from app.budget import BudgetExceeded


# ---------- memory_store ----------

@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(MEM, "path", lambda: tmp_path / "memory.json")
    return tmp_path / "memory.json"


def test_memory_missing_or_blank_file_reads_empty(mem):
    assert MEM.summary() == dict(file=str(mem), entities=0, relations=0, observations=0, types={}, exists=False)
    mem.write_text("   \n", encoding="utf-8")
    assert MEM.list_entities() == []


def test_memory_reads_json_array_and_skips_junk(mem):
    mem.write_text(json.dumps([
        {"type": "entity", "name": "Ada", "entityType": "person", "observations": ["likes maths"]},
        {"name": "Grace", "observations": ["compilers"]},          # no type key, but has observations -> entity
        {"type": "relation", "from": "Ada", "to": "Grace", "relationType": "knows"},
        5,                                                        # not an object -> ignored
    ]), encoding="utf-8")
    s = MEM.summary()
    assert s["entities"] == 2 and s["relations"] == 1 and s["observations"] == 2 and s["exists"] is True
    assert s["types"] == {"person": 1, "unknown": 1}


def test_memory_line_format_skips_bad_lines(mem):
    mem.write_text('{"type": "entity", "name": "Ada", "observations": []}\nnot json at all\n\n'
                   '{"type": "relation", "from": "Ada", "to": "X"}\n', encoding="utf-8")
    assert [e["name"] for e in MEM.list_entities()] == ["Ada"]
    assert MEM.summary()["relations"] == 1


def test_memory_list_filters_by_type_and_text(mem):
    mem.write_text("\n".join(json.dumps(x) for x in [
        {"type": "entity", "name": "Ada", "entityType": "person", "observations": ["fast"]},
        {"type": "entity", "name": "Acme", "entityType": "company", "observations": ["fast shipping"]},
    ]), encoding="utf-8")
    assert [e["name"] for e in MEM.list_entities(entity_type="company")] == ["Acme"]
    assert [e["name"] for e in MEM.list_entities(q="SHIPPING")] == ["Acme"]
    assert MEM.list_entities(q="nothing-matches") == []


def test_memory_update_entity_observations_type_and_errors(mem):
    mem.write_text(json.dumps({"type": "entity", "name": "Ada", "entityType": "person", "observations": ["a"]}) + "\n", encoding="utf-8")
    with pytest.raises(KeyError):
        MEM.update_entity("Nobody", observations=["x"])
    e = MEM.update_entity("Ada", observations=["  kept  ", "", "  "], entity_type=" engineer ")
    assert e["observations"] == ["kept"] and e["entityType"] == "engineer"
    again = MEM.list_entities()[0]
    assert again["observations"] == ["kept"] and again["entityType"] == "engineer"
    assert any(p.name.startswith("memory.backup-") for p in mem.parent.iterdir())


def test_memory_delete_observation_success_and_errors(mem):
    mem.write_text("\n".join(json.dumps(x) for x in [
        {"type": "entity", "name": "Ada", "observations": ["one", "two"]},
        {"type": "relation", "from": "Ada", "to": "Bob"},
    ]), encoding="utf-8")
    with pytest.raises(KeyError):
        MEM.delete_observation("Nobody", 0)
    with pytest.raises(IndexError):
        MEM.delete_observation("Ada", 5)
    e = MEM.delete_observation("Ada", 0)
    assert e["observations"] == ["two"]
    assert MEM.list_entities()[0]["observations"] == ["two"]
    assert MEM.summary()["relations"] == 1                       # relations survive an observation delete


# ---------- budget ----------

def test_window_start_for_daily_and_monthly_windows():
    now = 1_700_000_000.0                                        # 2023-11-14 22:13:20 UTC
    day = dt.datetime(2023, 11, 14, tzinfo=dt.timezone.utc).timestamp()
    month = dt.datetime(2023, 11, 1, tzinfo=dt.timezone.utc).timestamp()
    assert B.window_start("daily", now) == day
    assert B.window_start("monthly", now) == month
    assert B.window_start("total", now) == 0.0


def test_reserve_refuses_unknown_provider_and_logs_nothing(state):
    with pytest.raises(BudgetExceeded) as e:
        state.guard.reserve("no-such-provider", "m", 10, 10, "m")
    assert e.value.detail["reason"] == "unknown_provider" and "Unknown provider" in e.value.detail["message"]
    assert state.guard.ledger() == []


def test_ledger_and_model_breakdown_filter_by_provider(state):
    r1 = state.guard.reserve("mock", "mock-cheap", 100, 50, "mock/mock-cheap")
    state.guard.settle(r1, 100, 50)
    assert [row["provider"] for row in state.guard.ledger(provider="mock")] == ["mock"]
    assert state.guard.ledger(provider="openai") == []
    rows = state.guard.by_model(provider="mock")
    assert rows == [dict(provider="mock", model="mock-cheap", calls=1, input_tokens=100, output_tokens=50,
                         cost_usd=pytest.approx(state.guard.by_model()[0]["cost_usd"]))]
    assert state.guard.by_model(provider="openai") == []


# ---------- issues ----------

def test_issue_hook_failure_never_breaks_the_raiser(state):
    def boom(issue):
        raise RuntimeError("hook exploded")

    state.issues.on_new = boom
    issue = state.issues.add("test_kind", "src-a", "Something broke", detail="d")
    assert issue["title"] == "Something broke" and issue["status"] == "open"
    again = state.issues.add("test_kind", "src-a", "Something broke", detail="newer detail")
    assert again["id"] == issue["id"] and again["detail"] == "newer detail"       # refreshed, not duplicated


# ---------- chat_store ----------

def test_chat_store_exists_is_false_for_unknown_session(state):
    assert state.chat.store.exists("no-such-session") is False


# ---------- context ----------

def test_transcript_includes_earlier_summary_and_skips_empty_turns():
    msgs = [{"role": "summary", "text": "S1"}, {"role": "user", "text": "hi"},
            {"role": "bot", "text": "hello"}, {"role": "bot", "text": ""}]
    assert CX.transcript(msgs) == "[Earlier summary]\nS1\n\nUser: hi\n\nAssistant: hello"


def test_meta_json_serialises_non_standard_values():
    assert json.loads(CX.meta_json({"when": dt.date(2024, 1, 2), "n": 1})) == {"when": "2024-01-02", "n": 1}


# ---------- voice ----------

@pytest.fixture
def eleven_http(monkeypatch):
    replies = {"fn": None}

    def handler(req):
        return replies["fn"](req)
    real = httpx.AsyncClient

    def client(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)
    monkeypatch.setattr(V.httpx, "AsyncClient", client)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return replies


def test_tts_without_a_key_says_so(state, eleven_http):
    state.voice.update({"voice_id": "v1"})                     # a voice is chosen, so the key is what's missing
    with pytest.raises(V.VoiceError) as e:
        asyncio.run(state.voice.tts("hello there"))
    assert e.value.status == 412 and "not set up" in str(e.value)


def test_tts_refuses_empty_text_after_markup_is_stripped(state):
    state.voice.update({"api_key": "el-key-123456789", "voice_id": "v1"})
    with pytest.raises(V.VoiceError, match="nothing to read aloud"):
        asyncio.run(state.voice.tts("| | |"))              # table separators only: nothing speakable remains


def test_voice_char_cap_must_not_be_negative(state):
    with pytest.raises(V.VoiceError, match="char_cap must be"):
        state.voice.update({"char_cap": -1})


def test_error_body_that_is_not_json_is_passed_on_as_text(state, eleven_http):
    state.voice.update({"api_key": "el-key-123456789"})
    eleven_http["fn"] = lambda req: httpx.Response(500, text="upstream exploded")
    with pytest.raises(V.VoiceError) as e:
        asyncio.run(state.voice.voices())
    assert e.value.status == 502 and str(e.value) == "ElevenLabs 500: upstream exploded"


def test_stt_refuses_oversize_recording(state):
    state.voice.update({"api_key": "el-key-123456789"})
    with pytest.raises(V.VoiceError, match="25 MB"):
        asyncio.run(state.voice.stt(b"x" * (25 * 1024 * 1024 + 1), "a.webm", "audio/webm"))


# ---------- credits ----------

def test_credit_mode_and_balance_validation(state):
    p = state.providers.get("openai")
    with pytest.raises(ValueError, match="mode must be"):
        state.credits.update(p, mode="sometimes")
    with pytest.raises(ValueError, match="balance must be"):
        state.credits.update(p, balance_usd=-5)
    state.credits.update(p, mode="manual", balance_usd=12.5)
    assert state.credits.remaining("openai") == pytest.approx(12.5)


def test_stale_openrouter_balance_is_refreshed_on_demand(state, client, monkeypatch):
    seen = []

    def handler(req):
        seen.append(req.url.path)
        return httpx.Response(200, json={"data": {"total_credits": 10.0, "total_usage": 4.0}})

    real = httpx.AsyncClient
    monkeypatch.setattr(C.httpx, "AsyncClient", lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    client.put("/api/providers/openrouter", json={"enabled": True, "api_key": "sk-or-v1-test-123456"})
    seen.clear()
    p = state.providers.get("openrouter")
    r = state.credits._row(p.id)
    r["checked_at"] = None                                       # never checked -> stale
    state.credits._save(r)
    asyncio.run(state.credits.refresh_if_stale(p))
    assert seen and seen[0].endswith("/credits")
    assert state.credits.public(p)["balance_usd"] == pytest.approx(6.0)
