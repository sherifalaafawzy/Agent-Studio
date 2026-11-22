"""Background chat turns (long runs).

A turn runs as a server-side job, NOT tied to the HTTP request: closing the browser or losing the connection does not
stop it. Every event is buffered (with a sequence number) so a client can re-attach later and replay from where it
left off. Jobs stop only when they finish, when the user presses Stop, when the optional wall-clock limit is hit, or
when the server shuts down (progress is saved in all cases).
"""
from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

MAX_EVENTS = 200_000          # per job; older text deltas are dropped first if a run is truly enormous


class NoteInbox:
    """Messages the user sends WHILE a job runs. Two modes:
       queue - read by the agent before its next model call (the run is not interrupted);
       steer - cancels the model call / step in progress, keeps all work done so far, and sends the note immediately.
    The agent loop drains the inbox; the job emits note_queued / note_delivered / steered events for the UI."""

    def __init__(self, emit: Callable[[dict], None]):
        self.emit = emit
        self.items: list[dict] = []          # not yet delivered, oldest first
        self.steer_cancel: Callable[[], None] | None = None   # set by the chat engine while a model step runs
        self.steer_requested = False
        self.on_deliver: Callable[[list[dict]], None] | None = None   # chat engine persists delivered notes

    def push(self, text: str, mode: str = "queue") -> dict:
        if mode not in ("queue", "steer"):
            raise ValueError("mode must be queue or steer")
        text = (text or "").strip()
        if not text:
            raise ValueError("the note is empty")
        note = {"id": uuid.uuid4().hex[:10], "text": text[:8000], "mode": mode, "ts": time.time()}
        self.items.append(note)
        self.emit({"type": "note_queued", "note_id": note["id"], "text": note["text"], "mode": mode})
        if mode == "steer" and self.steer_cancel is not None:
            self.steer_requested = True
            self.steer_cancel()                # interrupts the model call / step in progress; the engine resumes
        return note

    def retract(self, nid: str) -> bool:
        n = next((x for x in self.items if x["id"] == nid), None)
        if not n:
            return False
        self.items.remove(n)
        self.emit({"type": "note_retracted", "note_id": nid})
        return True

    def take(self, mode: str | None = None) -> list[dict]:
        """Remove and return the pending notes (optionally only one mode). Delivered notes are announced."""
        taken = [n for n in self.items if mode is None or n["mode"] == mode]
        self.items = [n for n in self.items if n not in taken]
        for n in taken:
            self.emit({"type": "note_delivered", "note_id": n["id"], "mode": n["mode"], "text": n["text"]})
        if taken and self.on_deliver:
            self.on_deliver(taken)
        return taken


@dataclass
class Job:
    id: str
    session_id: str | None = None
    started: float = field(default_factory=time.time)
    ended: float | None = None
    status: str = "running"           # running | done | error | cancelled | timeout
    events: list[dict] = field(default_factory=list)
    dropped: int = 0
    task: asyncio.Task | None = None
    inbox: "NoteInbox | None" = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    stop_reason: str | None = None

    def public(self) -> dict:
        return dict(job_id=self.id, session_id=self.session_id, status=self.status, started=self.started, ended=self.ended,
                    events=len(self.events) + self.dropped, elapsed_s=round((self.ended or time.time()) - self.started, 1))


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    def emit(self, job: Job, ev: dict) -> None:
        ev = {**ev, "seq": len(job.events) + job.dropped}
        if ev.get("type") == "session":
            job.session_id = ev.get("session_id")
        job.events.append(ev)
        if len(job.events) > MAX_EVENTS:            # keep memory bounded: drop the oldest text deltas
            for i, e in enumerate(job.events):
                if e.get("type") == "text":
                    job.events.pop(i)
                    job.dropped += 1
                    break
        job.changed.set()

    def start(self, work: Callable[..., Awaitable[Any]], max_minutes: float = 0) -> Job:
        job = Job(uuid.uuid4().hex[:16])
        job.inbox = NoteInbox(lambda ev: self.emit(job, ev))
        self.jobs[job.id] = job
        self.emit(job, {"type": "job", "job_id": job.id})

        async def run() -> None:
            try:
                coro = work(lambda ev: self.emit(job, ev), job.inbox)
                if max_minutes and max_minutes > 0:
                    try:
                        await asyncio.wait_for(coro, max_minutes * 60)
                    except asyncio.TimeoutError:
                        job.status = "timeout"
                        self.emit(job, {"type": "error", "message": f"Stopped: the turn reached the {max_minutes:g}-minute limit (Settings → Agents & sandbox). Progress was saved."})
                        return
                else:
                    await coro
                job.status = "done" if job.status == "running" else job.status
            except asyncio.CancelledError:
                job.status = "cancelled" if job.status == "running" else job.status
                self.emit(job, {"type": "error", "message": f"Stopped ({job.stop_reason or 'cancelled'}). Progress so far was saved."})
            except Exception as e:  # noqa: BLE001
                job.status = "error"
                self.emit(job, {"type": "error", "message": f"{type(e).__name__}: {e}"})
            finally:
                job.ended = time.time()
                self.emit(job, {"type": "end", "status": job.status})
                self._gc()

        job.task = asyncio.create_task(run())
        return job

    async def follow(self, job: Job, after: int = -1) -> AsyncIterator[dict | None]:
        """Yield buffered events with seq > after, then live ones until the job ends. Yields None as a keepalive tick."""
        while True:
            job.changed.clear()
            for ev in [e for e in job.events if e["seq"] > after]:
                after = ev["seq"]
                yield ev
            if job.ended is not None and (not job.events or job.events[-1]["seq"] <= after):
                return
            try:
                await asyncio.wait_for(job.changed.wait(), 15)
            except asyncio.TimeoutError:
                yield None

    def active_for(self, session_id: str) -> Job | None:
        return next((j for j in self.jobs.values() if j.session_id == session_id and j.ended is None), None)

    def running_sessions(self) -> set[str]:
        return {j.session_id for j in self.jobs.values() if j.ended is None and j.session_id}

    def cancel(self, job_id: str, reason: str = "stopped by you") -> bool:
        j = self.jobs.get(job_id)
        if not j or j.ended is not None or not j.task:
            return False
        j.stop_reason = reason
        j.task.cancel()
        return True

    async def shutdown(self) -> None:
        live = [j for j in self.jobs.values() if j.ended is None and j.task]
        for j in live:
            j.stop_reason = "server shutting down"
            j.task.cancel()
        for j in live:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(asyncio.shield(j.task), 20)

    def _gc(self) -> None:
        """Forget finished jobs after an hour (their results are in the chat history)."""
        cutoff = time.time() - 3600
        for jid in [k for k, j in self.jobs.items() if j.ended and j.ended < cutoff]:
            self.jobs.pop(jid, None)
