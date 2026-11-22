"""A turn that ends without visible text is retried once for the answer; if still empty, it is reported, not hidden."""
import asyncio

from google.adk.events import Event
from google.genai import types

import app.chat as chat_mod
from app.db import Database
from app.main import State


class ScriptedRunner:
    """Stands in for ADK's Runner: each run_async call yields the next scripted list of texts."""
    script: list[list[str]] = []
    prompts: list[str] = []

    def __init__(self, **kw):
        self.agent = kw["agent"]

    async def run_async(self, *, user_id, session_id, new_message, run_config):
        ScriptedRunner.prompts.append(new_message.parts[0].text)
        texts = ScriptedRunner.script.pop(0) if ScriptedRunner.script else []
        for t in texts:
            yield Event(invocation_id="i", author=self.agent.name, content=types.Content(role="model", parts=[types.Part(text=t)]))


def run_turn(state, monkeypatch, script):
    monkeypatch.setattr(chat_mod, "Runner", ScriptedRunner)
    ScriptedRunner.script, ScriptedRunner.prompts = [list(x) for x in script], []
    events = []
    res = asyncio.run(state.chat.send("mock", None, "What is 2+2?", None, emit=events.append))
    return res, events


def test_empty_answer_is_retried_once_and_the_answer_is_used(state, monkeypatch, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    res, events = run_turn(state, monkeypatch, [[], ["Four."]])
    assert res["reply"] == "Four." and res["no_answer"] is False and res["retried"] is True
    assert len(ScriptedRunner.prompts) == 2 and "Do not call any tools" in ScriptedRunner.prompts[1]
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "Four."
    bot = state.chat.store.messages(res["session_id"])[-1]
    assert bot["role"] == "bot" and bot["text"] == "Four."


def test_still_empty_is_reported_and_saved_as_no_answer(state, monkeypatch, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    res, _ = run_turn(state, monkeypatch, [[], []])
    assert res["reply"] == "" and res["no_answer"] is True and res["retried"] is True
    bot = state.chat.store.messages(res["session_id"])[-1]
    assert bot["text"] == "" and bot["meta"]["no_answer"] is True            # not hidden behind "(no final reply)"


def test_normal_answers_are_not_retried(state, monkeypatch, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    res, _ = run_turn(state, monkeypatch, [["Fine."]])
    assert res["reply"] == "Fine." and res["retried"] is False and len(ScriptedRunner.prompts) == 1


def test_anthropic_output_cap_is_raised_once(tmp_path):    # new installs and existing rows at the old default
    from app.providers import ProviderStore
    db = Database(tmp_path / "t.db")
    db.execute("UPDATE providers SET max_output_tokens=2048 WHERE id='anthropic'")
    ProviderStore(db)
    assert db.one("SELECT max_output_tokens m FROM providers WHERE id='anthropic'")["m"] == 8192
    db.execute("UPDATE providers SET max_output_tokens=4096 WHERE id='anthropic'")
    ProviderStore(db)
    assert db.one("SELECT max_output_tokens m FROM providers WHERE id='anthropic'")["m"] == 4096   # a deliberate choice is kept


class FlakyReasoningRunner:
    """First attempt is rejected like Anthropic's 'invalid signature in thinking block'; the retry works."""
    attempts: list[str] = []

    def __init__(self, **kw):
        self.agent = kw["agent"]

    async def run_async(self, *, user_id, session_id, new_message, run_config):
        FlakyReasoningRunner.attempts.append(new_message.parts[0].text)
        if len(FlakyReasoningRunner.attempts) == 1:
            import litellm
            raise litellm.BadRequestError("AnthropicException - Invalid `signature` in `thinking` block", model="x", llm_provider="anthropic")
        yield Event(invocation_id="i", author=self.agent.name, content=types.Content(role="model", parts=[types.Part(text="Recovered.")]))


def test_reasoning_history_rejection_rebuilds_history_and_retries_once(state, monkeypatch, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    monkeypatch.setattr(chat_mod, "Runner", FlakyReasoningRunner)
    FlakyReasoningRunner.attempts = []
    res = asyncio.run(state.chat.send("mock", None, "Continue please", None, emit=lambda e: None))
    assert res["reply"] == "Recovered." and len(FlakyReasoningRunner.attempts) == 2
    assert FlakyReasoningRunner.attempts == ["Continue please", "Continue please"]


def test_inject_notes_does_not_change_the_history_object(tmp_path):
    from google.adk.models.llm_request import LlmRequest
    from app.chat import inject_notes
    original = types.Content(role="user", parts=[types.Part(text="fix it")])
    req = LlmRequest()
    req.contents = [original]
    inject_notes(req, [{"text": "one more thing"}])
    assert len(original.parts) == 1                                  # the session's object is untouched
    assert "one more thing" in req.contents[-1].parts[-1].text
