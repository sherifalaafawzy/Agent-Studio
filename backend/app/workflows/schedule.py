"""The Schedule trigger: start a workflow every N minutes, every day at a time, or on a cron pattern.

How it works
* The workflow's first box is a Schedule box; its settings say WHEN (interval / daily / cron) and WHAT the task is. Switching the schedule on and off is
  separate (from the box's panel): the switch and the bookkeeping live in the `workflow_schedules` table, so they survive a restart and are never
  exported or imported with the workflow (an imported workflow never starts running on its own).
* A small loop inside the server (`Scheduler.loop`) wakes every few seconds and starts what is due. Times are the computer's local time.
* After a restart, a time that was missed while the app was closed runs ONCE (or is skipped, if the box says so); never once per missed time.
* Money: every model call in a scheduled run goes through the same spend caps as any other run. On top of that a schedule starts at most
  `max_per_day` runs a day, never starts a run while its previous one is still working, and switches itself off after 3 failed runs in a row
  (a run that hits a spend cap fails). Each failure, and the switch-off, also lands in the notification center.
* Everything can be paused at once with the workflow setting `schedules_enabled`.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta

MODES = ("interval", "daily", "cron")
MISSED = ("run_once", "skip")
MAX_FAILS = 3
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DOW_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
MON_NAMES = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


class CronError(ValueError):
    """A cron pattern that cannot be read. Safe to show."""


def _field(text: str, lo: int, hi: int, names: dict[str, int], what: str) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip().lower()
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or not 1 <= int(s) <= hi:
                raise CronError(f"the step in the {what} field must be a whole number from 1 to {hi}")
            step = int(s)

        def num(x: str) -> int:
            v = names.get(x, None)
            if v is None:
                if not x.isdigit():
                    raise CronError(f"'{x}' is not a number in the {what} field")
                v = int(x)
            if not lo <= v <= hi:
                raise CronError(f"{v} is outside {lo}-{hi} in the {what} field")
            return v
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a, b = num(x), num(y)
            if a > b:
                raise CronError(f"the range {a}-{b} in the {what} field goes backwards")
        else:
            a = num(part)
            b = hi if step > 1 else a
        out.update(range(a, b + 1, step))
    return out


class Cron:
    """A standard 5-field cron pattern: minute hour day-of-month month day-of-week (0 or 7 = Sunday; names like mon, jan work too)."""

    def __init__(self, pattern: str):
        parts = (pattern or "").split()
        if len(parts) != 5:
            raise CronError("a cron pattern has 5 parts: minute hour day month weekday (for example '0 9 * * 1-5')")
        self.pattern = " ".join(parts)
        self.minutes = _field(parts[0], 0, 59, {}, "minute")
        self.hours = _field(parts[1], 0, 23, {}, "hour")
        self.days = _field(parts[2], 1, 31, {}, "day")
        self.months = _field(parts[3], 1, 12, MON_NAMES, "month")
        dows = _field(parts[4], 0, 7, DOW_NAMES, "weekday")
        self.dows = {d % 7 for d in dows}
        self.day_any, self.dow_any = parts[2] == "*", parts[4] == "*"

    def _day_ok(self, d: datetime) -> bool:
        if d.month not in self.months:
            return False
        dom, dow = d.day in self.days, (d.weekday() + 1) % 7 in self.dows
        if self.day_any or self.dow_any:                      # classic cron: when both are restricted, either may match
            return dom and dow
        return dom or dow

    def next_after(self, ts: float) -> float:
        t = datetime.fromtimestamp(ts).replace(second=0, microsecond=0) + timedelta(minutes=1)
        day = t.replace(hour=0, minute=0)
        for _ in range(366 * 5):                              # Feb 29 patterns can wait years
            if self._day_ok(day):
                for h in sorted(self.hours):
                    for m in sorted(self.minutes):
                        cand = day.replace(hour=h, minute=m)
                        if cand >= t:
                            return cand.timestamp()
            day += timedelta(days=1)
        raise CronError("this pattern never matches a real date")


def clean(c: dict, where: str) -> dict:
    """The Schedule box's settings, checked (the node catalog calls this)."""
    mode = c.get("mode", "interval")
    if mode not in MODES:
        raise ValueError(f"{where}: repeat must be one of {', '.join(MODES)}")
    every = c.get("every_minutes", 60)
    if isinstance(every, bool) or not isinstance(every, int) or not 1 <= every <= 10080:
        raise ValueError(f"{where}: every 1 to 10080 minutes (one week)")
    at = str(c.get("at") or "09:00").strip()
    if not TIME_RE.match(at):
        raise ValueError(f"{where}: the time looks like 09:00 (24-hour)")
    cron = str(c.get("cron") or "0 9 * * 1-5").strip()
    if mode == "cron":
        try:
            Cron(cron)
        except CronError as e:
            raise ValueError(f"{where}: {e}") from e
    task = str(c.get("task") or "").strip()
    if not task or len(task) > 2000:
        raise ValueError(f"{where}: write the task each run gets (up to 2000 characters)")
    missed = c.get("missed", "run_once")
    if missed not in MISSED:
        raise ValueError(f"{where}: missed runs: run_once or skip")
    per_day = c.get("max_per_day", 48)
    if isinstance(per_day, bool) or not isinstance(per_day, int) or not 1 <= per_day <= 1440:
        raise ValueError(f"{where}: at most 1 to 1440 runs a day")
    return dict(mode=mode, every_minutes=every, at=at, cron=cron, task=task, missed=missed, max_per_day=per_day)


def next_time(cfg: dict, after: float) -> float:
    """When a schedule with these settings runs next, strictly after `after`."""
    if cfg["mode"] == "interval":
        return after + cfg["every_minutes"] * 60
    if cfg["mode"] == "daily":
        h, m = (int(x) for x in cfg["at"].split(":"))
        return Cron(f"{m} {h} * * *").next_after(after)
    return Cron(cfg["cron"]).next_after(after)


def describe(cfg: dict) -> str:
    if cfg["mode"] == "interval":
        n = cfg["every_minutes"]
        return f"every {n} minute{'s' if n != 1 else ''}" if n < 60 or n % 60 else f"every {n // 60} hour{'s' if n != 60 else ''}"
    if cfg["mode"] == "daily":
        return f"every day at {cfg['at']}"
    return f"cron '{cfg['cron']}'"


class Scheduler:
    def __init__(self, s, clock=time.time):
        self.s, self.db, self.clock = s, s.db, clock
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ helpers
    def _entry(self, wid: str) -> tuple[dict | None, dict | None]:
        doc = self.s.workflows.get(wid)
        if doc is None:
            return None, None
        from .nodes import ENTRY
        entry = next((n for n in doc["nodes"] if n["type"] in ENTRY), None)
        return doc, entry if entry is not None and entry["type"] == "schedule" else None

    def _row(self, wid: str):
        return self.db.one("SELECT * FROM workflow_schedules WHERE wf_id=?", (wid,))

    def _notify(self, title: str, body: str, level: str, wid: str) -> None:
        notes = getattr(self.s, "notifications", None)
        if notes is not None:
            notes.add(title, body, level=level, source="schedule", link=f"/workflows/edit/?id={wid}")

    def global_on(self) -> bool:
        return bool(self.s.wf_config.config().get("schedules_enabled", True))

    # ------------------------------------------------------------------ what the UI calls
    def status(self, wid: str) -> dict:
        doc, entry = self._entry(wid)
        r = self._row(wid)
        upcoming: list[float] = []
        cfg = entry["config"] if entry else None
        if cfg and r and r["enabled"]:
            t = r["next_run"]
            upcoming = [t]
            for _ in range(2):
                t = next_time(cfg, t)
                upcoming.append(t)
        return dict(has_schedule_box=entry is not None, enabled=bool(r and r["enabled"]), next_run=r["next_run"] if r and r["enabled"] else None, upcoming=upcoming,
                    last_run=r["last_run"] if r else None, last_run_id=r["last_run_id"] if r else None, last_status=r["last_status"] if r else None, runs=r["runs"] if r else 0,
                    fails=r["fails"] if r else 0, note=r["note"] if r else "", describe=describe(cfg) if cfg else "", schedules_enabled=self.global_on())

    def enable(self, wid: str) -> dict:
        doc, entry = self._entry(wid)
        if doc is None:
            raise KeyError(wid)
        if entry is None:
            raise ValueError("this workflow does not start with a Schedule box: add one and save first")
        now = self.clock()
        nxt = next_time(entry["config"], now)
        self.db.execute("INSERT INTO workflow_schedules(wf_id,enabled,spec,next_run,created,fails,note) VALUES(?,1,?,?,?,0,'') ON CONFLICT(wf_id) DO UPDATE SET enabled=1,"
                        "spec=excluded.spec,next_run=excluded.next_run,fails=0,note=''", (wid, json.dumps(entry["config"], sort_keys=True), nxt, now))
        return self.status(wid)

    def pause(self, wid: str, note: str = "") -> dict:
        if self.s.workflows.get(wid) is None:
            raise KeyError(wid)
        self.db.execute("UPDATE workflow_schedules SET enabled=0, note=? WHERE wf_id=?", (note, wid))
        return self.status(wid)

    def delete(self, wid: str) -> None:
        self.db.execute("DELETE FROM workflow_schedules WHERE wf_id=?", (wid,))

    # ------------------------------------------------------------------ the clock
    def catch_up(self) -> None:
        """At start-up: a time missed while the app was closed runs once (it is already due) or, for 'skip', moves to the next time."""
        now = self.clock()
        for r in self.db.query("SELECT wf_id,next_run FROM workflow_schedules WHERE enabled=1 AND next_run < ?", (now,)):
            _, entry = self._entry(r["wf_id"])
            if entry is not None and entry["config"]["missed"] == "skip":
                self.db.execute("UPDATE workflow_schedules SET next_run=?, note=? WHERE wf_id=?", (next_time(entry["config"], now), "a missed time was skipped", r["wf_id"]))

    async def tick(self) -> list[str]:
        """Start whatever is due. Returns the ids of the runs it started."""
        if not self.global_on():
            return []
        now, started = self.clock(), []
        for r in self.db.query("SELECT * FROM workflow_schedules WHERE enabled=1 AND next_run <= ?", (now,)):
            wid = r["wf_id"]
            doc, entry = self._entry(wid)
            if entry is None:
                self.pause(wid, "switched off: the workflow no longer starts with a Schedule box") if doc is not None else self.delete(wid)
                continue
            cfg = entry["config"]
            spec = json.dumps(cfg, sort_keys=True)
            if spec != r["spec"]:                                  # the box was edited: start counting from now with the new settings
                self.db.execute("UPDATE workflow_schedules SET spec=?, next_run=? WHERE wf_id=?", (spec, next_time(cfg, now), wid))
                continue
            nxt = next_time(cfg, now)
            prev = self.s.wf_runner.runs.get(r["last_run_id"] or "")
            if prev is not None and prev.task_ref is not None and not prev.task_ref.done():
                self.db.execute("UPDATE workflow_schedules SET next_run=?, note=? WHERE wf_id=?", (nxt, "skipped a time: the previous run was still working", wid))
                continue
            day = time.strftime("%Y-%m-%d", time.localtime(now))
            count = r["day_count"] if r["day"] == day else 0
            if count >= cfg["max_per_day"]:
                self.db.execute("UPDATE workflow_schedules SET next_run=?, note=? WHERE wf_id=?", (nxt, f"daily limit of {cfg['max_per_day']} runs reached", wid))
                continue
            try:
                run = self.s.wf_runner.start(wid, cfg["task"], None, origin="schedule")
            except Exception as e:                                # noqa: BLE001 - any refusal counts as a failed run
                self.db.execute("UPDATE workflow_schedules SET next_run=?, last_run=?, last_status='error', day=?, day_count=? WHERE wf_id=?", (nxt, now, day, count + 1, wid))
                self._failed(wid, doc["name"], str(e)[:300])
                continue
            self.db.execute("UPDATE workflow_schedules SET next_run=?, last_run=?, last_run_id=?, last_status='running', runs=runs+1, day=?, day_count=?, note='' WHERE wf_id=?",
                            (nxt, now, run.id, day, count + 1, wid))
            run.task_ref.add_done_callback(lambda _f, wid=wid, run=run, name=doc["name"]: self._finished(wid, run, name))
            started.append(run.id)
        return started

    def _finished(self, wid: str, run, name: str) -> None:
        if self._row(wid) is None:
            return
        self.db.execute("UPDATE workflow_schedules SET last_status=? WHERE wf_id=? AND last_run_id=?", (run.status, wid, run.id))
        if run.status == "error":
            self._failed(wid, name, run.error or "the run failed")
        else:
            self.db.execute("UPDATE workflow_schedules SET fails=0 WHERE wf_id=?", (wid,))

    def _failed(self, wid: str, name: str, why: str) -> None:
        self.db.execute("UPDATE workflow_schedules SET fails=fails+1 WHERE wf_id=?", (wid,))
        r = self._row(wid)
        self._notify(f"Scheduled run of '{name}' failed", why, "error", wid)
        if r is not None and r["fails"] >= MAX_FAILS and r["enabled"]:
            self.pause(wid, f"switched off after {MAX_FAILS} failed runs in a row")
            self._notify(f"Schedule of '{name}' switched off", f"{MAX_FAILS} runs in a row failed. Fix the workflow, then switch the schedule on again.", "warning", wid)

    async def loop(self, every: float = 15.0) -> None:
        self.catch_up()
        while True:
            try:
                await self.tick()
            except Exception:                                     # noqa: BLE001 - the clock must keep going  # pragma: no cover - defensive
                pass
            await asyncio.sleep(every)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self.loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
