"""AG-UI (https://docs.ag-ui.com): the same chat run, as a stream any AG-UI client can render.

`POST /api/agui` takes a RunAgentInput and streams AG-UI events. The run is the normal chat job (it keeps going if the client
disconnects). Our own events are translated one by one:

  text / thinking            -> TEXT_MESSAGE_* / REASONING_*
  tool_call / tool_result    -> TOOL_CALL_START, ARGS, END / TOOL_CALL_RESULT
  sub-agent runs             -> SUBAGENT_STARTED / FINISHED / ERROR, and their events carry subagent_run_id
  cost, steps, artifacts     -> STATE_SNAPSHOT once, then STATE_DELTA (JSON Patch)
  memory, guardrails, files  -> ACTIVITY_SNAPSHOT (activity types: memory, guardrail, artifact, canvas; canvases are also in the state under /canvases)
  rich blocks in an answer   -> ACTIVITY_SNAPSHOT after the message (activity types rich.table, rich.chart, rich.json, rich.html, rich.audio, rich.video,
                                rich.diff; shapes in app/rich.py)
  routing, fallback, notes   -> CUSTOM ("agent_studio.<name>")
  an external agent asking   -> RUN_FINISHED with an interrupt outcome; the run itself keeps waiting. Post again with `resume`
                                (interrupt id + payload) to answer it and carry on with the same run.
  done / error / refused     -> RUN_FINISHED / RUN_ERROR

Events are built with the official ag_ui Python SDK, so every one is validated against the protocol models.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from ag_ui.core import (
    ActivitySnapshotEvent, BaseEvent, CustomEvent, Interrupt, ReasoningEndEvent, ReasoningMessageContentEvent, ReasoningMessageEndEvent,
    ReasoningMessageStartEvent, ReasoningStartEvent, RunErrorEvent, RunFinishedEvent, RunFinishedInterruptOutcome, RunFinishedSuccessOutcome, RunStartedEvent,
    StateDeltaEvent, StateSnapshotEvent, StepFinishedEvent, StepStartedEvent, SubagentErrorEvent, SubagentFinishedEvent, SubagentStartedEvent, TextMessageContentEvent,
    TextMessageEndEvent, TextMessageStartEvent, ToolCallArgsEvent, ToolCallEndEvent, ToolCallResultEvent, ToolCallStartEvent,
)

from .rich import extract_blocks

ROOT = "root"
CUSTOM_EVENTS = ("route", "fallback", "steered", "note_queued", "note_delivered", "note_retracted", "external_resolved", "external_note", "model_call")


def _s(v: Any, n: int = 4000) -> str:
    text = v if isinstance(v, str) else json.dumps(v, default=str)
    return text if len(text) <= n else text[:n] + "…"


def interrupt_schema(ev: dict) -> dict:
    """What an answer to this question must look like (the payload of the resume entry)."""
    ask = ev.get("ask")
    if ask == "permission":
        return {"type": "object", "properties": {"option_id": {"type": "string", "enum": [str(o.get("id")) for o in ev.get("options") or []]}}, "required": ["option_id"]}
    if ask == "question":
        return {"type": "object", "properties": {"answers": {"type": "array", "items": {"type": "string"}}, "skip": {"type": "boolean"}}}
    return {"type": "object", "properties": {"accepted": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["accepted"]}


class AguiTranslator:
    """Turns the events of one chat job into AG-UI events. Feed it our events in order; it returns the AG-UI events to send."""

    def __init__(self, thread_id: str, run_id: str, parent_run_id: str | None = None):
        self.thread_id, self.run_id, self.parent_run_id = thread_id, run_id, parent_run_id
        self.text_id: dict[str, str | None] = {}            # run key -> the open text message
        self.text_buf: dict[str, str] = {}                  # run key -> the text of that message so far (for rich blocks)
        self.reasoning_id: str | None = None
        self.pending_calls: dict[tuple[str, str], list[str]] = {}
        self.finished = False                               # RUN_FINISHED / RUN_ERROR was sent: stop
        self.paused = False                                 # an interrupt was sent: the run is waiting for the user's answer
        self.state: dict = {"session_id": None, "status": "running", "agents": {}, "totals": {"cost_usd": 0.0, "model_calls": 0, "tool_calls": 0}, "artifacts": [], "route": None}

    # ---- lifecycle
    def start(self) -> list[BaseEvent]:
        return [RunStartedEvent(thread_id=self.thread_id, run_id=self.run_id, parent_run_id=self.parent_run_id), StateSnapshotEvent(snapshot=self.state)]

    def _delta(self, *ops: tuple[str, str, Any]) -> StateDeltaEvent:
        return StateDeltaEvent(delta=[{"op": op, "path": path, "value": value} for op, path, value in ops])

    @staticmethod
    def _sub(run_id: str | None) -> str | None:
        return None if not run_id or run_id == ROOT else run_id

    def _close_text(self, key: str) -> list[BaseEvent]:
        mid = self.text_id.pop(key)                         # callers only close messages that are open
        text = self.text_buf.pop(key, "")
        return [TextMessageEndEvent(message_id=mid, subagent_run_id=self._sub(key)),
                *[ActivitySnapshotEvent(message_id=uuid.uuid4().hex, activity_type=f"rich.{kind}", content=content) for kind, content in extract_blocks(text)]]

    def _close_reasoning(self) -> list[BaseEvent]:
        if not self.reasoning_id:
            return []
        mid, self.reasoning_id = self.reasoning_id, None
        return [ReasoningMessageEndEvent(message_id=mid), ReasoningEndEvent(message_id=mid)]

    def close(self) -> list[BaseEvent]:
        """The stream ended without a final event: close whatever is open so the client's view is consistent."""
        out = self._close_reasoning()
        for key in list(self.text_id):
            out += self._close_text(key)
        return out

    # ---- translation
    def feed(self, ev: dict) -> list[BaseEvent]:
        if self.finished or self.paused:
            return []
        t = ev.get("type")
        run = ev.get("run_id") or ROOT
        out: list[BaseEvent] = []
        if t not in ("thinking",):
            out += self._close_reasoning()
        if t == "session":
            self.state["session_id"] = ev.get("session_id")
            return out + [self._delta(("replace", "/session_id", ev.get("session_id"))), CustomEvent(name="agent_studio.session", value={k: ev.get(k) for k in ("session_id", "agent", "provider", "trace_id")})]
        if t in ("text", "external_text"):
            key = run if t == "external_text" else ROOT
            delta = ev.get("delta") or ""
            if not delta:
                return out
            if key not in self.text_id:
                self.text_id[key] = uuid.uuid4().hex
                out.append(TextMessageStartEvent(message_id=self.text_id[key], role="assistant", subagent_run_id=self._sub(key)))
            self.text_buf[key] = self.text_buf.get(key, "") + delta
            return out + [TextMessageContentEvent(message_id=self.text_id[key], delta=delta, subagent_run_id=self._sub(key))]
        if t == "thinking":
            delta = ev.get("delta") or ""
            if not delta:
                return out
            if not self.reasoning_id:
                self.reasoning_id = uuid.uuid4().hex
                out += [ReasoningStartEvent(message_id=self.reasoning_id), ReasoningMessageStartEvent(message_id=self.reasoning_id, role="reasoning")]
            return out + [ReasoningMessageContentEvent(message_id=self.reasoning_id, delta=delta)]
        for key in list(self.text_id):                       # any other event ends the answer text that was streaming
            out += self._close_text(key)
        if t == "tool_call":
            call_id = uuid.uuid4().hex
            self.pending_calls.setdefault((run, ev.get("tool", "")), []).append(call_id)
            sub = self._sub(run)
            return out + [ToolCallStartEvent(tool_call_id=call_id, tool_call_name=str(ev.get("tool") or "tool"), subagent_run_id=sub),
                          ToolCallArgsEvent(tool_call_id=call_id, delta=_s(ev.get("args") if ev.get("args") is not None else {}), subagent_run_id=sub),
                          ToolCallEndEvent(tool_call_id=call_id, subagent_run_id=sub),
                          self._count("tool_calls")]
        if t == "tool_result":
            queue = self.pending_calls.get((run, ev.get("tool", "")))
            call_id = queue.pop(0) if queue else uuid.uuid4().hex
            return out + [ToolCallResultEvent(message_id=uuid.uuid4().hex, tool_call_id=call_id, content=_s(ev.get("result", "")), role="tool", subagent_run_id=self._sub(run))]
        if t == "agent_start":
            self.state["agents"][run] = {"agent": ev.get("agent"), "status": "running", "parent": ev.get("parent"), "external": bool(ev.get("external"))}
            delta = self._delta(("add", f"/agents/{run}", self.state["agents"][run]))
            if run == ROOT:
                return out + [StepStartedEvent(step_name=str(ev.get("agent") or "agent")), delta]
            return out + [SubagentStartedEvent(subagent_run_id=run, name=str(ev.get("agent") or "agent"), description=_s(ev.get("task") or "", 300) or None,
                                               parent_subagent_run_id=self._sub(ev.get("parent"))), delta]
        if t == "agent_end":
            status = ev.get("status") or "done"
            self.state["agents"].setdefault(run, {})["status"] = status
            delta = self._delta(("replace", f"/agents/{run}/status", status))
            if run == ROOT:
                return out + [StepFinishedEvent(step_name=str(ev.get("agent") or "agent")), delta]
            if status in ("error", "cancelled"):
                return out + [SubagentErrorEvent(subagent_run_id=run, message=_s(ev.get("result") or f"the sub-agent {status}", 500)), delta]
            return out + [SubagentFinishedEvent(subagent_run_id=run, result=ev.get("result")), delta]
        if t == "model_call":
            self.state["totals"]["model_calls"] += 1
            self.state["totals"]["cost_usd"] = round(float(ev.get("turn_cost_usd") or 0), 6)
            return out + [self._delta(("replace", "/totals/model_calls", self.state["totals"]["model_calls"]), ("replace", "/totals/cost_usd", self.state["totals"]["cost_usd"]))]
        if t == "route":
            self.state["route"] = {k: ev.get(k) for k in ("provider", "model", "tier", "reason")}
            return out + [self._delta(("replace", "/route", self.state["route"])), CustomEvent(name="agent_studio.route", value={k: v for k, v in ev.items() if k not in ("type", "seq")})]
        if t == "artifact":
            a = ev.get("artifact") or {}
            brief = {k: a.get(k) for k in ("id", "name", "type", "size", "mime", "preview")}
            self.state["artifacts"].append(brief)
            return out + [ActivitySnapshotEvent(message_id=uuid.uuid4().hex, activity_type="artifact", content={**brief, "sha256": a.get("sha256"), "path": a.get("path"), "version": a.get("version")}),
                          self._delta(("add", "/artifacts/-", brief))]
        if t == "canvas":
            c = {k: v for k, v in ev.items() if k not in ("type", "seq")}
            self.state.setdefault("canvases", {})[str(c.get("canvas_id"))] = {k: c.get(k) for k in ("title", "version", "pending") if c.get(k) is not None}
            return out + [ActivitySnapshotEvent(message_id=uuid.uuid4().hex, activity_type="canvas", content=c), self._delta(("add", "/canvases", self.state["canvases"]))]
        if t in ("memory", "guardrail"):
            return out + [ActivitySnapshotEvent(message_id=uuid.uuid4().hex, activity_type=t, content={k: v for k, v in ev.items() if k not in ("type", "seq")})]
        if t == "external_ask":
            self.paused = True
            self.state["status"] = "waiting"
            return out + [RunFinishedEvent(thread_id=self.thread_id, run_id=self.run_id, outcome=RunFinishedInterruptOutcome(interrupts=[Interrupt(
                id=str(ev.get("request_id")), reason=str(ev.get("ask") or "input_required"), message=str(ev.get("title") or ev.get("label") or "The agent is asking you something"),
                subagent_run_id=self._sub(run), response_schema=interrupt_schema(ev), metadata={"agent": ev.get("agent"), "label": ev.get("label"), "options": ev.get("options"), "input": ev.get("input")})]))]
        if t in CUSTOM_EVENTS:
            return out + [CustomEvent(name=f"agent_studio.{t}", value={k: v for k, v in ev.items() if k not in ("type", "seq")})]
        if t == "done":
            self.finished = True
            result = {k: ev[k] for k in ("session_id", "reply", "halted", "trace_id", "usage", "no_answer") if ev.get(k) is not None}
            return out + [RunFinishedEvent(thread_id=self.thread_id, run_id=self.run_id, outcome=RunFinishedSuccessOutcome(), result=result)]
        if t == "refused":
            self.finished = True
            return out + [RunErrorEvent(message=str((ev.get("detail") or {}).get("message") or "A spend cap stopped this run"), code="budget_refused")]
        if t == "error":
            self.finished = True
            return out + [RunErrorEvent(message=_s(f"{ev.get('title') or 'Error'}. {ev.get('hint') or ev.get('message') or ''}".strip(), 800), code=ev.get("code") or "error")]
        return out                                           # job / end / unknown: nothing to say

    def _count(self, name: str) -> StateDeltaEvent:
        self.state["totals"][name] += 1
        return self._delta(("replace", f"/totals/{name}", self.state["totals"][name]))


def last_user_text(messages: list) -> str:
    """The newest user message of a RunAgentInput as text (text parts of a multi-part message are joined)."""
    for m in reversed(messages):
        if getattr(m, "role", None) != "user":
            continue
        c = getattr(m, "content", "")
        if isinstance(c, str):
            return c.strip()
        return "\n".join(getattr(p, "text", "") for p in c if getattr(p, "type", "") == "text").strip()
    return ""
