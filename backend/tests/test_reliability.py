"""Failures are kept with the chat; turns cut off by a restart are shown as interrupted."""
import asyncio
import json

import pytest
from google.adk.events import Event
from google.genai import types

import app.chat as chat_mod
from app.main import State
from app.db import Database


class Boom:
    def __init__(self, **kw):
        self.agent = kw["agent"]

    async def run_async(self, *, user_id, session_id, new_message, run_config):
        raise RuntimeError("Error code: 503 - overloaded_error")
        yield  # pragma: no cover


def test_a_failed_turn_keeps_its_error_with_the_chat(state, monkeypatch, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    monkeypatch.setattr(chat_mod, "Runner", Boom)
    with pytest.raises(RuntimeError):
        asyncio.run(state.chat.send("mock", None, "hello", None, emit=lambda e: None))
    sid = state.chat.store.list(1)[0]["id"]
    bot = state.chat.store.messages(sid)[-1]
    assert bot["role"] == "bot" and bot["meta"]["status"] == "error"
    assert bot["meta"]["error"]["title"] == "The provider is temporarily unavailable" and bot["meta"]["error"]["retryable"] is True


def test_turns_cut_off_by_a_restart_are_marked(tmp_path):
    db = Database(tmp_path / "t.db")
    st = State(db)
    st.chat.store.ensure("cut", "mock", None, None, None, "half way")
    st.chat.store.add("cut", "user", "half way")
    st.chat.store.ensure("done", "mock", None, None, None, "finished")
    st.chat.store.add("done", "user", "finished")
    st.chat.store.add("done", "bot", "yes", {"status": "ok"})
    assert st.chat.store.mark_interrupted() == 1
    last = st.chat.store.messages("cut")[-1]
    assert last["meta"]["status"] == "interrupted" and last["meta"]["error"]["code"] == "interrupted"
    assert st.chat.store.messages("done")[-1]["text"] == "yes"                    # finished chats are untouched
    st2 = State(db)                                                             # a second start does not duplicate it
    assert st2.chat.store.mark_interrupted() == 0
    assert len(st2.chat.store.messages("cut")) == 2
