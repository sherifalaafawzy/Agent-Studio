"""Coverage for app/jobs.py: failure, wall-clock limit, cancel, shutdown, event-buffer trimming and cleanup."""
import asyncio
import time

from app import jobs as J


def test_failing_turn_is_marked_error_and_reported():
    async def main():
        jm = J.JobManager()

        async def work(emit, inbox):
            emit({"type": "text", "text": "partial answer"})
            raise ValueError("model exploded")

        job = jm.start(work)
        await job.task
        return job

    job = asyncio.run(main())
    assert job.status == "error" and job.ended is not None
    assert job.events[-2] == {"type": "error", "message": "ValueError: model exploded", "seq": job.events[-2]["seq"]}
    assert job.events[-1]["type"] == "end" and job.events[-1]["status"] == "error"


def test_wall_clock_limit_stops_the_turn_with_a_clear_message():
    async def main():
        jm = J.JobManager()

        async def slow(emit, inbox):
            await asyncio.sleep(5)

        job = jm.start(slow, max_minutes=0.0005)                 # ~30 ms
        await job.task
        return job

    job = asyncio.run(main())
    assert job.status == "timeout"
    msg = next(e["message"] for e in job.events if e["type"] == "error")
    assert msg.startswith("Stopped: the turn reached the 0.0005-minute limit") and "Progress was saved." in msg


def test_cancel_stops_a_running_turn_and_reports_the_reason():
    async def main():
        jm = J.JobManager()

        async def forever(emit, inbox):
            await asyncio.sleep(60)

        job = jm.start(forever)
        await asyncio.sleep(0)
        assert jm.cancel("no-such-job") is False
        assert jm.cancel(job.id, reason="stopped by you") is True
        await job.task
        assert jm.cancel(job.id) is False                        # already finished
        return job

    job = asyncio.run(main())
    assert job.status == "cancelled"
    assert any(e.get("message") == "Stopped (stopped by you). Progress so far was saved." for e in job.events)


def test_server_shutdown_cancels_every_live_turn_with_that_reason():
    async def main():
        jm = J.JobManager()

        async def forever(emit, inbox):
            await asyncio.sleep(60)

        a, b = jm.start(forever), jm.start(forever)
        await asyncio.sleep(0)
        await jm.shutdown()
        return a, b

    a, b = asyncio.run(main())
    for job in (a, b):
        assert job.status == "cancelled" and job.stop_reason == "server shutting down"
        assert any(e.get("message") == "Stopped (server shutting down). Progress so far was saved." for e in job.events)


def test_event_buffer_drops_oldest_text_first_when_full(monkeypatch):
    monkeypatch.setattr(J, "MAX_EVENTS", 3)
    jm = J.JobManager()
    job = J.Job("buf")
    jm.emit(job, {"type": "text", "text": "a"})
    jm.emit(job, {"type": "tool", "name": "read_file"})
    jm.emit(job, {"type": "text", "text": "b"})
    jm.emit(job, {"type": "text", "text": "c"})                  # 4th event: oldest text ("a") is dropped
    assert job.dropped == 1
    assert [e.get("text") for e in job.events if e["type"] == "text"] == ["b", "c"]
    assert job.public()["events"] == len(job.events) + job.dropped == 4


def test_finished_jobs_are_forgotten_after_an_hour():
    jm = J.JobManager()
    old = J.Job("old-job")
    old.ended = time.time() - 7200
    fresh = J.Job("fresh-job")
    fresh.ended = time.time() - 60
    jm.jobs = {"old-job": old, "fresh-job": fresh}
    jm._gc()
    assert list(jm.jobs) == ["fresh-job"]
