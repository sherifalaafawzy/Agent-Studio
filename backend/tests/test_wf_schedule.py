"""The Schedule trigger (cron parser, settings, the scheduler's clock) and the notification center with the Notify box."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.notifications import KEEP, NotificationError, NotificationStore
from app.workflows import schedule as SC
from tests.test_workflows import finish, rich, save  # noqa: F401  (rich is a fixture)
from tests.test_wf_engine import D, nd


def ts(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi).timestamp()


def dt(t):
    return datetime.fromtimestamp(t)


# ---------------------------------------------------------------------------------------------------- cron
def test_cron_patterns():
    c = SC.Cron("*/15 9-17 * * mon-fri")
    assert c.minutes == {0, 15, 30, 45} and c.hours == set(range(9, 18)) and c.dows == {1, 2, 3, 4, 5}
    assert dt(c.next_after(ts(2026, 11, 27, 17, 50))) == datetime(2026, 11, 30, 9, 0)            # Friday evening -> Monday 9:00
    assert dt(c.next_after(ts(2026, 11, 30, 9, 0))) == datetime(2026, 11, 30, 9, 15)            # strictly after
    assert SC.Cron("0 0 * * 7").dows == {0} and SC.Cron("5 4 1,15 jan-mar *").months == {1, 2, 3}
    assert SC.Cron("0 12 10/10 * *").days == {10, 20, 30}
    either = SC.Cron("0 8 1 * 1")                                                                  # day 1 OR a Monday (classic cron)
    assert dt(either.next_after(ts(2026, 11, 27))) == datetime(2026, 11, 30, 8, 0) and dt(either.next_after(ts(2026, 11, 30, 9))) == datetime(2026, 12, 1, 8, 0)
    assert dt(SC.Cron("0 0 29 2 *").next_after(ts(2026, 3, 1))) == datetime(2028, 2, 29)
    for bad, msg in [("* * *", "5 parts"), ("*/0 * * * *", "step"), ("x * * * *", "not a number"), ("61 * * * *", "outside"), ("5-1 * * * *", "backwards"), ("* * * * */9", "step")]:
        with pytest.raises(SC.CronError, match=msg):
            SC.Cron(bad)
    with pytest.raises(SC.CronError, match="never matches"):
        SC.Cron("0 0 31 2 *").next_after(ts(2026, 1, 1))


def test_settings_times_and_words():
    ok = SC.clean({"task": "go"}, "b")
    assert ok == dict(mode="interval", every_minutes=60, at="09:00", cron="0 9 * * 1-5", task="go", missed="run_once", max_per_day=48)
    for cfg, msg in [(dict(mode="hourly"), "repeat"), (dict(every_minutes=0), "every 1"), (dict(every_minutes=True), "every 1"), (dict(at="25:00"), "24-hour"), (dict(mode="cron", cron="nope"), "5 parts"),
                     (dict(task=" "), "task"), (dict(missed="later"), "missed"), (dict(max_per_day=0), "a day"), (dict(max_per_day=False), "a day")]:
        with pytest.raises(ValueError, match=msg):
            SC.clean({"task": "go", **cfg}, "b")
    base = ts(2026, 11, 27, 10, 0)
    assert SC.next_time(dict(ok, every_minutes=30), base) == base + 1800
    assert dt(SC.next_time(dict(ok, mode="daily", at="9:30"), base)) == datetime(2026, 11, 28, 9, 30)
    assert dt(SC.next_time(dict(ok, mode="cron", cron="0 12 * * *"), base)) == datetime(2026, 11, 27, 12, 0)
    assert [SC.describe(dict(ok, every_minutes=n)) for n in (1, 5, 60, 120, 90)] == ["every 1 minute", "every 5 minutes", "every 1 hour", "every 2 hours", "every 90 minutes"]
    assert SC.describe(dict(ok, mode="daily")) == "every day at 09:00" and SC.describe(dict(ok, mode="cron")) == "cron '0 9 * * 1-5'"


# ---------------------------------------------------------------------------------------------------- the scheduler
def sched_flow(c, wid="sch", cfg=None, after=None):
    nodes = [nd("s", "schedule", {"task": "tick", "every_minutes": 10, **(cfg or {})}), *(after or [nd("t", "template", dict(text="ran: {{ $input }}")), nd("o", "output")])]
    ids = [n["id"] for n in nodes]
    save(c, D(nodes, list(zip(ids, ids[1:])), wid=wid))


def tick(c, state, at):
    state.wf_schedule.clock = lambda: at
    return c.portal.call(state.wf_schedule.tick)


def test_switch_on_run_and_list(rich, state):
    assert rich.get("/api/workflows/ghost/schedule").status_code == 404 and rich.post("/api/workflows/ghost/schedule", json={"enabled": True}).status_code == 404
    assert rich.post("/api/workflows/ghost/schedule", json={"enabled": False}).status_code == 404
    save(rich, D([nd("s", "input"), nd("o", "output")], [("s", "o")], wid="plain"))
    assert "Schedule box" in rich.post("/api/workflows/plain/schedule", json={"enabled": True}).json()["detail"]
    sched_flow(rich)
    assert rich.post("/api/workflows/sch/schedule", json={}).status_code == 422
    st = rich.get("/api/workflows/sch/schedule").json()
    assert st["has_schedule_box"] is True and st["enabled"] is False and st["describe"] == "every 10 minutes" and st["upcoming"] == []
    now = time.time()
    state.wf_schedule.clock = lambda: now
    st = rich.post("/api/workflows/sch/schedule", json={"enabled": True}).json()
    assert st["enabled"] and st["next_run"] == pytest.approx(now + 600) and len(st["upcoming"]) == 3 and st["upcoming"][2] == pytest.approx(now + 1800)
    listed = {w["id"]: w for w in rich.get("/api/workflows").json()["workflows"]}
    assert listed["sch"]["trigger"] == "schedule" and listed["sch"]["schedule"]["enabled"] and listed["plain"]["schedule"] is None
    assert tick(rich, state, now + 10) == []                                         # not due yet
    started = tick(rich, state, now + 601)
    assert len(started) == 1
    run = finish(rich, started[0])
    assert run["final"] == "ran: tick" and run["origin"] == "schedule"
    st = rich.get("/api/workflows/sch/schedule").json()
    assert st["runs"] == 1 and st["last_status"] == "done" and st["next_run"] == pytest.approx(now + 1201) and st["fails"] == 0
    assert rich.get("/api/workflows/runs", params={"origin": "schedule"}).json()["total"] == 1
    st = rich.post("/api/workflows/sch/schedule", json={"enabled": False}).json()
    assert st["enabled"] is False and st["next_run"] is None and tick(rich, state, now + 99999) == []
    assert rich.get("/api/workflows").json()["workflows"][0]["schedule"]["next_run"] is None


def test_overlap_daily_limit_edits_and_global_pause(rich, state):
    sched_flow(rich, cfg=dict(max_per_day=1))
    now = time.time()
    state.wf_schedule.clock = lambda: now
    rich.post("/api/workflows/sch/schedule", json={"enabled": True})
    state.wf_runner.runs["busy"] = SimpleNamespace(task_ref=SimpleNamespace(done=lambda: False))
    state.db.execute("UPDATE workflow_schedules SET last_run_id='busy'")
    assert tick(rich, state, now + 601) == [] and "still working" in rich.get("/api/workflows/sch/schedule").json()["note"]
    state.wf_runner.runs.pop("busy")
    finish(rich, tick(rich, state, now + 1202)[0])
    assert tick(rich, state, now + 1803) == [] and "daily limit" in rich.get("/api/workflows/sch/schedule").json()["note"]
    rich.put("/api/workflows/sch", json=D([nd("s", "schedule", {"task": "tick", "every_minutes": 5}), nd("o", "output")], [("s", "o")], wid="sch"))
    assert tick(rich, state, now + 2404) == [] and rich.get("/api/workflows/sch/schedule").json()["next_run"] == pytest.approx(now + 2404 + 300)     # edited: counts from now
    assert rich.put("/api/workflows/settings", json=dict(schedules_enabled=False)).json()["settings"]["schedules_enabled"] is False
    assert rich.put("/api/workflows/settings", json=dict(schedules_enabled="no")).status_code == 422
    assert tick(rich, state, now + 99999) == [] and rich.get("/api/workflows/sch/schedule").json()["schedules_enabled"] is False


def test_failures_notify_and_switch_off(rich, state, monkeypatch):
    sched_flow(rich, after=[nd("x", "stop", dict(message="broken"))])
    now = time.time()
    state.wf_schedule.clock = lambda: now
    rich.post("/api/workflows/sch/schedule", json={"enabled": True})
    for i in range(2):
        run = finish(rich, tick(rich, state, now + 601 * (i + 1))[0])
        assert run["status"] == "error"
    assert rich.get("/api/workflows/sch/schedule").json()["fails"] == 2
    notes = rich.get("/api/notifications").json()
    assert notes["unread"] == 2 and notes["items"][0]["source"] == "schedule" and notes["items"][0]["link"] == "/workflows/edit/?id=sch" and notes["items"][0]["level"] == "error"
    monkeypatch.setattr(state.wf_runner, "start", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cap reached")))
    assert tick(rich, state, now + 601 * 3) == []                                     # refused to start: the 3rd failure in a row
    st = rich.get("/api/workflows/sch/schedule").json()
    assert st["enabled"] is False and "3 failed runs" in st["note"] and st["last_status"] == "error"
    assert [n["title"] for n in rich.get("/api/notifications").json()["items"][:2]] == ["Schedule of 'sch' switched off", "Scheduled run of 'sch' failed"]


def test_a_good_run_resets_failures_and_late_finish_after_delete(rich, state):
    sched_flow(rich)
    now = time.time()
    state.wf_schedule.clock = lambda: now
    rich.post("/api/workflows/sch/schedule", json={"enabled": True})
    state.db.execute("UPDATE workflow_schedules SET fails=2")
    finish(rich, tick(rich, state, now + 601)[0])
    assert rich.get("/api/workflows/sch/schedule").json()["fails"] == 0
    state.wf_schedule.delete("sch")
    state.wf_schedule._finished("sch", SimpleNamespace(status="error", id="r", error=None), "sch")         # a run that ends after its schedule was removed: nothing happens
    assert rich.get("/api/notifications").json()["unread"] == 0
    state.wf_schedule._failed("sch", "sch", "x")                                       # no row: just the notice
    assert rich.get("/api/notifications").json()["unread"] == 1


def test_catch_up_and_workflows_that_changed(rich, state):
    sched_flow(rich, "once")
    sched_flow(rich, "skipper", cfg=dict(missed="skip"))
    now = time.time()
    state.wf_schedule.clock = lambda: now
    for w in ("once", "skipper"):
        rich.post(f"/api/workflows/{w}/schedule", json={"enabled": True})
    later = now + 3 * 3600                                                             # the app was closed for 3 hours
    state.wf_schedule.clock = lambda: later
    state.wf_schedule.catch_up()
    assert rich.get("/api/workflows/skipper/schedule").json()["next_run"] == pytest.approx(later + 600) and "skipped" in rich.get("/api/workflows/skipper/schedule").json()["note"]
    started = tick(rich, state, later)
    assert len(started) == 1                                                           # 'once' runs a single time, not 18
    finish(rich, started[0])
    rich.put("/api/workflows/once", json=D([nd("s", "input"), nd("o", "output")], [("s", "o")], wid="once"))
    rich.delete("/api/workflows/skipper")
    state.db.execute("INSERT INTO workflow_schedules(wf_id,enabled,spec,next_run,created) VALUES('gone',1,'',0,0)")
    assert tick(rich, state, later + 99999) == []
    st = rich.get("/api/workflows/once/schedule").json()
    assert st["enabled"] is False and "no longer starts" in st["note"] and st["has_schedule_box"] is False
    assert state.db.one("SELECT 1 FROM workflow_schedules WHERE wf_id IN ('gone','skipper')") is None
    with pytest.raises(KeyError):
        state.wf_schedule.pause("gone")


def test_loop_starts_and_stops(state):
    async def main():
        calls = []
        state.wf_schedule.tick = lambda: calls.append(1) or asyncio.sleep(0)
        state.wf_schedule.start()
        state.wf_schedule.start()                                                      # once only
        await asyncio.sleep(0.05)
        await state.wf_schedule.stop()
        await state.wf_schedule.stop()
        return calls
    assert asyncio.run(main()) == [1]


# ---------------------------------------------------------------------------------------------------- notifications
def test_notification_store(state):
    n = NotificationStore(state.db)
    a = n.add(" Hello ", "body", level="success", source="test", link="/x", desktop=True)
    assert a["title"] == "Hello" and a["desktop"] is True and a["read"] is False and n.unread() == 1
    for kw, msg in [(dict(title=""), "title"), (dict(title="t", level="loud"), "level"), (dict(title="t", link="https://x"), "link")]:
        with pytest.raises(NotificationError, match=msg):
            n.add(**kw)
    b = n.add("second")
    assert [x["id"] for x in n.list()["items"]] == [b["id"], a["id"]] and n.list(after=a["created"] - 1)["unread"] == 2
    n.mark_read(a["id"])
    assert [x["id"] for x in n.list(unread_only=True)["items"]] == [b["id"]]
    with pytest.raises(KeyError):
        n.mark_read("nope")
    n.mark_all_read()
    assert n.unread() == 0
    n.delete(a["id"])
    n.clear()
    assert n.list(limit=0)["items"] == []
    for i in range(KEEP + 3):
        n.add(f"n{i}")
    assert len(n.list(limit=1000)["items"]) == 200 and state.db.one("SELECT COUNT(*) c FROM notifications")["c"] == KEEP


def test_notify_box_and_api(rich, state):
    save(rich, D([nd("s", "input"), nd("n", "notify", dict(title="Done: {{ $workflow.name }}", message="result {{ $input }}", level="success", desktop=True)), nd("o", "output")],
                 [("s", "n"), ("n", "o")], wid="tell"))
    run = finish(rich, rich.post("/api/workflows/tell/run", json={"task": "42"}).json()["id"])
    assert run["final"] == "result 42"
    got = rich.get("/api/notifications").json()
    item = got["items"][0]
    assert got["unread"] == 1 and item["title"] == "Done: tell" and item["level"] == "success" and item["desktop"] is True and item["link"] == f"/workflows/?tab=runs&run={run['id']}"
    save(rich, D([nd("s", "input"), nd("n", "notify", dict(title="{{ $error }}", message="")), nd("o", "output")], [("s", "n"), ("n", "o")], wid="blank"))
    finish(rich, rich.post("/api/workflows/blank/run", json={"task": "x"}).json()["id"])
    assert rich.get("/api/notifications").json()["items"][0]["title"] == "blank"                # an empty title falls back to the workflow's name
    for bad in (dict(title=""), dict(title="t", level="loud")):
        r = rich.post("/api/workflows", json=D([nd("s", "input"), nd("n", "notify", bad), nd("o", "output")], [("s", "n"), ("n", "o")], wid="bad"))
        assert r.status_code == 422
    assert rich.post("/api/notifications/nope/read").status_code == 404
    assert rich.post(f"/api/notifications/{item['id']}/read").json() == {"ok": True, "unread": 1}
    assert len(rich.get("/api/notifications", params={"unread_only": True}).json()["items"]) == 1
    state.notifications.add("x")
    assert rich.post("/api/notifications/read-all").json()["unread"] == 0
    assert rich.delete(f"/api/notifications/{item['id']}").json()["unread"] == 0
    assert rich.delete("/api/notifications").json() == {"ok": True, "unread": 0} and rich.get("/api/notifications").json()["items"] == []
