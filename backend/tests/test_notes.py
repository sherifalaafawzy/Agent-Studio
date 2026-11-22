"""Notes sent while an agent runs: queue (read at the next step) and steer (cuts the step in progress, keeps the work)."""
import asyncio

import pytest
from google.adk.events import Event
from google.genai import types

import app.chat as chat_mod
from app.chat import inject_notes, notes_text
from app.jobs import NoteInbox


def test_inbox_queue_retract_and_take():
    events = []
    box = NoteInbox(events.append)
    a = box.push("use pytest", "queue")
    b = box.push("and keep it small", "queue")
    assert box.retract(b["id"]) and not box.retract(b["id"])            # retracted before it was read
    taken = box.take()
    assert [n["text"] for n in taken] == ["use pytest"] and box.items == []
    types_ = [e["type"] for e in events]
    assert types_ == ["note_queued", "note_queued", "note_retracted", "note_delivered"]
    with pytest.raises(ValueError):
        box.push("   ", "queue")
    with pytest.raises(ValueError):
        box.push("x", "shout")


def test_queued_note_joins_the_last_user_turn_without_breaking_tool_pairs():
    from google.adk.models.llm_request import LlmRequest
    req = LlmRequest()
    req.contents = [types.Content(role="user", parts=[types.Part(text="fix the bug")]),
                    types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="read_file", args={}, id="c1"))]),
                    types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(name="read_file", response={"ok": 1}, id="c1"))])]
    inject_notes(req, [{"text": "prefer the stdlib"}])
    assert len(req.contents) == 3                                       # no new turn: the tool result stays the last user part
    parts = req.contents[-1].parts
    assert parts[0].function_response is not None and "prefer the stdlib" in parts[1].text
    assert "Notes from the user" in notes_text([{"text": "x"}])


def test_note_after_a_plain_model_reply_becomes_a_new_user_turn():
    from google.adk.models.llm_request import LlmRequest
    req = LlmRequest()
    req.contents = [types.Content(role="user", parts=[types.Part(text="hi")]), types.Content(role="model", parts=[types.Part(text="hello")])]
    inject_notes(req, [{"text": "be brief"}])
    assert req.contents[-1].role == "user" and "be brief" in req.contents[-1].parts[0].text


class SteerableRunner:
    """First run: streams a partial answer and then hangs (a long model call). Later runs answer normally."""
    calls: list[str] = []
    hang_first = True

    def __init__(self, **kw):
        self.agent = kw["agent"]

    async def run_async(self, *, user_id, session_id, new_message, run_config):
        text = new_message.parts[0].text
        SteerableRunner.calls.append(text)
        if len(SteerableRunner.calls) == 1 and SteerableRunner.hang_first:
            yield Event(invocation_id="i", author=self.agent.name, partial=True, content=types.Content(role="model", parts=[types.Part(text="Half an ans")]))
            await asyncio.sleep(30)                               # cut off by the steer
            return
        yield Event(invocation_id="i", author=self.agent.name, content=types.Content(role="model", parts=[types.Part(text="Used your note.")]))


def test_steer_cuts_the_step_keeps_partial_and_continues_with_the_note(state, monkeypatch, tmp_path):
    state.db.set_setting("workspace_dir", str(tmp_path / "ws"))
    monkeypatch.setattr(chat_mod, "Runner", SteerableRunner)
    SteerableRunner.calls = []
    events = []
    box = NoteInbox(events.append)

    async def go():
        async def steer_later():
            while not box.steer_cancel:                          # wait until the first step is running
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            box.push("use the other approach", "steer")
        t = asyncio.create_task(steer_later())
        res = await state.chat.send("mock", None, "Solve it", None, emit=events.append, inbox=box)
        await t
        return res

    res = asyncio.run(go())
    assert res["reply"] == "Used your note."
    assert "use the other approach" in SteerableRunner.calls[1]          # the next step carries the note
    types_ = [e["type"] for e in events]
    assert "steered" in types_ and types_.index("steered") < types_.index("note_delivered") + 2
    msgs = state.chat.store.messages(res["session_id"])
    assert any(m["role"] == "bot" and m["text"] == "Half an ans" and m["meta"]["status"] == "interrupted" for m in msgs)
    assert any(m["role"] == "user" and m["meta"].get("note") and m["text"] == "use the other approach" for m in msgs)
    assert state.db.query("SELECT * FROM reservations") == []             # the cut-off call was settled, not leaked


def test_steer_with_no_running_step_is_just_queued(state):
    box = NoteInbox(lambda e: None)
    box.push("hello", "steer")                                           # no step in progress: nothing to cut
    assert [n["mode"] for n in box.items] == ["steer"] and not box.steer_requested


def test_notes_api_rules(client, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 64})
    assert client.post("/api/chat/jobs/nope/notes", json={"text": "x", "mode": "queue"}).status_code == 409
    assert client.post("/api/chat/jobs/nope/notes", json={"text": "x", "mode": "shout"}).status_code == 409
