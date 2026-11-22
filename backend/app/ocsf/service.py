"""The OCSF export pipeline: settings, a bounded queue, a background worker, retries, counters, preview and connection tests.

Rules it keeps:
* emitting NEVER blocks or fails an agent run: events go into a bounded queue; when it is full the event is dropped and counted;
* mapping, redaction and sending all happen on the worker thread, once per sink at that sink's redaction level;
* a sink that fails is retried with backoff, then the batch is dropped and counted: one broken sink never stops the others;
* credentials live in a separate write-only setting and never appear in an error, a counter or a preview.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
from pathlib import Path

from ..aibom.redact import LEVEL_INFO, LEVELS
from ..db import data_dir
from . import schema
from .mapper import Mapper
from .sinks import KINDS, Sink, SinkError, build_sink
from .validate import validate

CONFIG_KEY = "ocsf_config"
SECRETS_KEY = "ocsf_secrets"
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")
CLASSES = {"agent_runs": "An agent finished a run", "model_calls": "A model was called", "tool_calls": "A tool was used (and memory saved, recalled or forgotten)",
           "security": "A spend cap or guardrail blocked something", "config_changes": "A setting, agent or credential was changed"}
DEFAULT_CONFIG: dict = {"enabled": False, "level": "public", "classes": {k: True for k in CLASSES}, "sinks": [], "batch_size": 100, "flush_seconds": 5}
SECRET_KEYS_OF_SETTINGS = ("key", "token", "secret", "password")


class OcsfError(ValueError):
    """A setting the export cannot accept. Safe to show."""


def _num(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi or int(v) != v:
        raise OcsfError(f"{what} must be a whole number between {lo} and {hi}")
    return int(v)


class OcsfService:
    def __init__(self, db, home: Path | None = None, start_worker: bool = True, retry_delays: tuple[float, ...] = (0.5, 1.0, 2.0), sink_factory=build_sink, queue_size: int = 5000):
        self.db, self.home, self.sink_factory, self.retry_delays = db, Path(home) if home else data_dir(), sink_factory, retry_delays
        self._q: queue.Queue = queue.Queue(maxsize=queue_size)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._start_worker = start_worker
        self._sinks: dict[str, tuple[str, Sink]] = {}
        self.counters = dict(queued=0, dropped_queue_full=0, mapped=0, invalid=0)
        self.sink_stats: dict[str, dict] = {}
        self.sleep = time.sleep

    # ------------------------------------------------------------------ settings
    def config(self) -> dict:
        try:
            raw = json.loads(self.db.get_setting(CONFIG_KEY, "{}") or "{}")
        except ValueError:
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        for k, v in raw.items():
            if k == "classes" and isinstance(v, dict):
                cfg["classes"].update({a: bool(b) for a, b in v.items() if a in CLASSES})
            elif k in cfg:
                cfg[k] = v
        return cfg

    def _secrets(self) -> dict:
        try:
            s = json.loads(self.db.get_setting(SECRETS_KEY, "{}") or "{}")
        except ValueError:
            return {}
        return s if isinstance(s, dict) else {}

    def set_config(self, patch: dict) -> dict:
        cfg = self.config()
        patch = dict(patch or {})
        secrets = patch.pop("secrets", None)
        for k, v in patch.items():
            if v is None or k not in DEFAULT_CONFIG:
                continue
            if k == "enabled":
                if not isinstance(v, bool):
                    raise OcsfError("enabled must be true or false")
            elif k == "level":
                if v not in LEVELS:
                    raise OcsfError(f"level must be one of: {', '.join(LEVELS)}")
            elif k == "classes":
                if not isinstance(v, dict) or not set(v) <= set(CLASSES) or any(not isinstance(b, bool) for b in v.values()):
                    raise OcsfError(f"classes must switch these on or off: {', '.join(CLASSES)}")
                v = {**cfg["classes"], **v}
            elif k == "batch_size":
                v = _num(v, 1, 1000, "batch size")
            elif k == "flush_seconds":
                v = _num(v, 1, 3600, "flush interval (seconds)")
            elif k == "sinks":
                v = self._clean_sinks(v, secrets)
            cfg[k] = v
        if secrets is not None:
            if not isinstance(secrets, dict) or any(not isinstance(a, dict) or any(not isinstance(x, str) for x in a.values()) for a in secrets.values()):
                raise OcsfError("secrets must look like {sink id: {name: value}}")
            merged = {sid: {**self._secrets().get(sid, {}), **vals} for sid, vals in secrets.items()}
            keep = {sid: {a: b for a, b in {**self._secrets().get(sid, {}), **merged.get(sid, {})}.items() if b} for sid in {s["id"] for s in cfg["sinks"]}}
            self.db.set_setting(SECRETS_KEY, json.dumps({k: v for k, v in keep.items() if v}))
        elif "sinks" in patch:                                              # a removed sink takes its credentials with it
            self.db.set_setting(SECRETS_KEY, json.dumps({k: v for k, v in self._secrets().items() if k in {s["id"] for s in cfg["sinks"]}}))
        self.db.set_setting(CONFIG_KEY, json.dumps(cfg))
        self._sinks = {}
        if cfg["enabled"] and cfg["sinks"] and self._start_worker:
            self._ensure_worker()
        return self.public_config()

    def _clean_sinks(self, sinks, secrets) -> list[dict]:
        if not isinstance(sinks, list) or len(sinks) > 8:
            raise OcsfError("sinks must be a list of at most 8 destinations")
        out, seen = [], set()
        stored = self._secrets()
        for s in sinks:
            if not isinstance(s, dict) or not ID_RE.match(str(s.get("id", ""))):
                raise OcsfError("each sink needs an id: lowercase letters, digits, - and _ (up to 31 characters, starting with a letter)")
            if s["id"] in seen:
                raise OcsfError(f"two sinks are called '{s['id']}'")
            seen.add(s["id"])
            kind, level = s.get("kind"), s.get("level") or ""
            if kind not in KINDS:
                raise OcsfError(f"sink '{s['id']}': kind must be one of: {', '.join(KINDS)}")
            if level and level not in LEVELS:
                raise OcsfError(f"sink '{s['id']}': level must be one of: {', '.join(LEVELS)}")
            conf = s.get("config") if isinstance(s.get("config"), dict) else {}
            clean = {k: v for k, v in conf.items() if k in {f[0] for f in KINDS[kind]["fields"] if not f[2]}}
            try:
                self.sink_factory(kind, clean, {**stored.get(s["id"], {}), **((secrets or {}).get(s["id"]) or {})}, self.home)
            except SinkError as e:
                raise OcsfError(f"sink '{s['id']}': {e}") from e
            out.append({"id": s["id"], "kind": kind, "enabled": bool(s.get("enabled", True)), "level": level, "config": clean})
        return out

    def public_config(self) -> dict:
        cfg, secrets = self.config(), self._secrets()
        sinks = [{**s, "secrets_set": sorted(secrets.get(s["id"], {})), "stats": self.sink_stats.get(s["id"], {}), "where": self._describe(s)} for s in cfg["sinks"]]
        return {**cfg, "sinks": sinks, "levels": LEVEL_INFO, "class_info": CLASSES, "schema_version": schema.VERSION,
                "kinds": [{"id": k, "label": v["label"], "note": v["note"], "fields": [{"name": f[0], "label": f[1], "secret": f[2], "required": f[3], "placeholder": f[4]} for f in v["fields"]]}
                          for k, v in KINDS.items()]}

    def _describe(self, s: dict) -> str:
        try:
            return self.sink_factory(s["kind"], s["config"], self._secrets().get(s["id"], {}), self.home).where()
        except SinkError as e:
            return f"not usable: {e}"

    # ------------------------------------------------------------------ emitting (called from agent threads: must be cheap and never raise)
    def _put(self, item: tuple) -> None:
        cfg = self.config()
        if not (cfg["enabled"] and any(s["enabled"] for s in cfg["sinks"])):
            return
        try:
            self._q.put_nowait(item)
            self.counters["queued"] += 1
        except queue.Full:
            self.counters["dropped_queue_full"] += 1
        if self._start_worker:
            self._ensure_worker()

    def on_span(self, span: dict) -> None:
        self._put(("span", span))

    def finding(self, kind: str, title: str, **kw) -> None:
        if self.config()["classes"]["security"]:
            self._put(("finding", dict(kind=kind, title=title, t=time.time(), **kw)))

    def entity(self, activity_id: int, kind: str, name: str, **kw) -> None:
        if self.config()["classes"]["config_changes"]:
            self._put(("entity", dict(activity_id=activity_id, kind=kind, name=name, t=time.time(), **kw)))

    def on_setting(self, key: str, old, new) -> None:
        """Database hook: a setting changed. Secret settings report only that they changed."""
        from ..settings_registry import SECRET_KEYS
        secret = key in SECRET_KEYS or any(w in key for w in SECRET_KEYS_OF_SETTINGS)
        self.entity(3, "setting", key, detail={"new": new, **({"old": old} if old is not None else {})}, secret=secret)

    # ------------------------------------------------------------------ mapping
    def _map(self, item: tuple, mapper: Mapper, classes: dict) -> dict | None:
        kind, p = item
        if kind == "span":
            return mapper.from_span(p, classes)
        if kind == "finding":
            return mapper.finding(**p)
        return mapper.entity_change(**p)

    def _sinks_now(self, cfg: dict) -> list[tuple[dict, Sink]]:
        out = []
        for s in cfg["sinks"]:
            if not s["enabled"]:
                continue
            if s["id"] not in self._sinks:
                try:
                    self._sinks[s["id"]] = (s["kind"], self.sink_factory(s["kind"], s["config"], self._secrets().get(s["id"], {}), self.home))
                except SinkError as e:
                    self._stat(s["id"], failed=1, last_error=str(e), dropped=0)
                    continue
            out.append((s, self._sinks[s["id"]][1]))
        return out

    def _stat(self, sid: str, **kw) -> None:
        st = self.sink_stats.setdefault(sid, dict(sent=0, failed=0, dropped=0, last_error="", last_ok=None))
        for k, v in kw.items():
            st[k] = (st[k] + v) if isinstance(v, int) and k in ("sent", "failed", "dropped") else v

    def process(self, items: list[tuple]) -> None:
        """Map, validate and send a batch. Called by the worker (and directly in tests)."""
        cfg = self.config()
        sinks = self._sinks_now(cfg)
        mappers: dict[str, Mapper] = {}
        per: dict[str, list[dict]] = {s["id"]: [] for s, _ in sinks}
        for item in items:
            for s, _ in sinks:
                lvl = s["level"] or cfg["level"]
                ev = self._map(item, mappers.setdefault(lvl, Mapper(lvl)), cfg["classes"])
                if ev is None:
                    continue
                self.counters["mapped"] += 1
                if validate(ev):                                       # a mapping bug must never put an invalid event on the wire
                    self.counters["invalid"] += 1
                    continue
                per[s["id"]].append(ev)
        for s, sink in sinks:
            evs = per[s["id"]]
            for i in range(0, len(evs), cfg["batch_size"]):
                self._deliver(s["id"], sink, evs[i:i + cfg["batch_size"]])

    def _deliver(self, sid: str, sink: Sink, batch: list[dict]) -> None:
        err = ""
        for attempt in range(len(self.retry_delays) + 1):
            try:
                sink.send(batch)
                self._stat(sid, sent=len(batch), last_ok=time.time(), last_error="")
                return
            except SinkError as e:
                err = str(e)
                self._stat(sid, failed=1, last_error=err)
                if attempt < len(self.retry_delays):
                    self.sleep(self.retry_delays[attempt])
        self._stat(sid, dropped=len(batch))

    # ------------------------------------------------------------------ worker
    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, name="ocsf-export", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            cfg = self.config()
            batch: list[tuple] = []
            deadline = time.time() + cfg["flush_seconds"]
            while len(batch) < cfg["batch_size"] and time.time() < deadline and not self._stop.is_set():
                try:
                    batch.append(self._q.get(timeout=0.25))
                except queue.Empty:
                    continue
            if batch:
                try:
                    self.process(batch)
                except Exception as e:  # noqa: BLE001 - the worker must survive anything
                    self._stat("_worker", last_error=f"{type(e).__name__}: {e}"[:200])

    def drain(self) -> int:
        """Send everything queued right now (used by tests and on shutdown). Returns how many events were taken from the queue."""
        items = []
        while True:
            try:
                items.append(self._q.get_nowait())
            except queue.Empty:
                break
        if items:
            self.process(items)
        return len(items)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self.drain()

    # ------------------------------------------------------------------ preview, test, stats
    def recent_spans(self, limit: int) -> list[dict]:
        rows = self.db.query("SELECT * FROM otel_records WHERE signal='trace' AND (name='llm.call' OR name LIKE 'tool.%' OR name LIKE 'agent.%' OR name LIKE 'memory.%' OR name LIKE 'acp.turn.%') "
                             "ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(source=r["source"], trace_id=r["trace_id"], span_id=r["span_id"], parent_id=r["parent_id"], name=r["name"], start_s=r["start_ns"] / 1e9, end_s=r["end_ns"] / 1e9,
                     attrs=json.loads(r["attrs"] or "{}"), status=r["status"]) for r in reversed(rows)]

    def preview(self, level: str | None = None, limit: int = 10) -> dict:
        cfg = self.config()
        lvl = level or cfg["level"]
        if lvl not in LEVELS:
            raise OcsfError(f"level must be one of: {', '.join(LEVELS)}")
        m = Mapper(lvl)
        events = [e for e in (m.from_span(s, {}) for s in self.recent_spans(max(1, min(limit, 50)))) if e]
        sample = not events
        if sample:                                                         # nothing has happened yet: show what each kind of event looks like
            t = time.time()
            events = [m.from_span(dict(source="engineer", trace_id="0" * 32, span_id="1" * 16, parent_id=None, name="tool.read_file", start_s=t - 0.2, end_s=t, attrs={"tool": "read_file", "args": "src/app.py"}, status="ok"), {}),
                      m.finding("spend_cap", "A spend cap blocked a model call", severity_id=3, agent="engineer", provider="openai"),
                      m.entity_change(3, "setting", "thinking_level", detail={"new": "high"})]
        return {"level": lvl, "sample": sample, "schema_version": schema.VERSION, "events": [{"event": e, "problems": validate(e)} for e in events]}

    def test_sink(self, sid: str) -> dict:
        s = next((x for x in self.config()["sinks"] if x["id"] == sid), None)
        if s is None:
            raise OcsfError(f"no sink called '{sid}'")
        try:
            sink = self.sink_factory(s["kind"], s["config"], self._secrets().get(sid, {}), self.home)
            ev = Mapper(s["level"] or self.config()["level"]).finding("connection_test", "Agent Studio OCSF connection test", severity_id=1, blocked=False)
            sink.send([ev])
        except SinkError as e:
            return {"ok": False, "detail": str(e)}
        return {"ok": True, "detail": f"A test event was delivered to {sink.where()}"}

    def stats(self) -> dict:
        return {**self.counters, "queue": self._q.qsize(), "sinks": self.sink_stats}
