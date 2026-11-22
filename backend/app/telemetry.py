"""OpenTelemetry connector (Phase 4).

Stores OTel data from every agent in SQLite, in one table (`otel_records`, signal = trace | metric | log):
  * Agent Studio's own agents (ADK teams, spawned sub-agents, model calls, tool calls) are recorded as spans directly.
  * External ACP agents are launched with OTEL_* env vars pointing at this server's OTLP/HTTP-JSON receiver
    (POST /v1/traces | /v1/metrics | /v1/logs), so whatever they export lands here too. Agents that do not export
    OTel (most don't) still get an Agent Studio span per turn and per tool call.
  * Optionally everything is forwarded to your own collector (Settings -> Telemetry).

Only OTLP/HTTP with JSON encoding is accepted (no protobuf dependency): set OTEL_EXPORTER_OTLP_PROTOCOL=http/json.
Prompts / tool arguments are NOT captured for external agents (we never enable their content-logging switches);
Agent Studio's own spans carry sizes, names and costs, plus a short clipped task / result for sub-agent runs.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.request
import uuid
from typing import Any

from .db import Database

MAX_BODY = 4 * 1024 * 1024
SIGNALS = ("traces", "metrics", "logs")
_SIGNAL_NAME = {"traces": "trace", "metrics": "metric", "logs": "log"}
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def _attrs(lst: Any) -> dict:
    """OTLP/JSON attribute list -> plain dict."""
    out: dict = {}
    for a in lst or []:
        v = a.get("value", {}) or {}
        for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if k in v:
                out[a.get("key", "")] = int(v[k]) if k == "intValue" and str(v[k]).lstrip("-").isdigit() else v[k]
                break
        else:
            out[a.get("key", "")] = json.dumps(v, default=str)[:500]
    return out


def _otlp_attrs(d: dict) -> list[dict]:
    out = []
    for k, v in d.items():
        if isinstance(v, bool):
            out.append({"key": k, "value": {"boolValue": v}})
        elif isinstance(v, int):
            out.append({"key": k, "value": {"intValue": str(v)}})
        elif isinstance(v, float):
            out.append({"key": k, "value": {"doubleValue": v}})
        else:
            out.append({"key": k, "value": {"stringValue": str(v)[:2000]}})
    return out


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_span_id() -> str:
    return uuid.uuid4().hex[:16]


class Telemetry:
    def __init__(self, db: Database):
        self.db = db
        self._q: queue.Queue = queue.Queue(maxsize=2000)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_purge = 0.0
        self.forwarded = 0
        self.span_listener = None          # called with every one of Agent Studio's own spans (the OCSF export)
        self.event_listener = None         # called with (name, kwargs) for security events and configuration changes
        self.forward_errors = 0
        self.last_forward_error: str | None = None

    # ------------------------------------------------------------------ settings
    def cfg(self) -> dict:
        g = self.db.get_setting
        hdr = json.loads(g("otel_forward_headers", "{}") or "{}")
        return dict(
            enabled=g("otel_enabled", "1") == "1",
            inject_env=g("otel_inject_env", "1") == "1",
            retention_days=int(g("otel_retention_days", "30")),
            receiver_url=g("otel_receiver_url") or os.environ.get("AGENT_STUDIO_API_URL", "http://127.0.0.1:8765"),
            forward_url=g("otel_forward_url", "") or "",
            forward_header_names=sorted(hdr),                       # values are never returned
            forwarded=self.forwarded, forward_errors=self.forward_errors, last_forward_error=self.last_forward_error)

    def update(self, patch: dict) -> dict:
        s = self.db.set_setting
        if patch.get("enabled") is not None:
            s("otel_enabled", "1" if patch["enabled"] else "0")
        if patch.get("inject_env") is not None:
            s("otel_inject_env", "1" if patch["inject_env"] else "0")
        if patch.get("retention_days") is not None:
            s("otel_retention_days", str(patch["retention_days"]))
        if patch.get("receiver_url") is not None:
            url = patch["receiver_url"].strip().rstrip("/")
            if url and not url.startswith(("http://", "https://")):
                raise ValueError("receiver_url must start with http:// or https://")
            s("otel_receiver_url", url)
        if patch.get("forward_url") is not None:
            url = patch["forward_url"].strip().rstrip("/")
            if url and not url.startswith(("http://", "https://")):
                raise ValueError("forward_url must start with http:// or https://")
            s("otel_forward_url", url)
        if patch.get("forward_headers") is not None:                # {} clears; values are write-only
            s("otel_forward_headers", json.dumps({str(k): str(v) for k, v in patch["forward_headers"].items()}))
        return self.cfg()

    @property
    def enabled(self) -> bool:
        return self.db.get_setting("otel_enabled", "1") == "1"

    # ------------------------------------------------------------------ writing
    def _insert(self, rows: list[tuple]) -> None:
        if not rows:
            return
        with self.db.lock:
            self.db.conn.executemany(
                "INSERT INTO otel_records(ts,signal,source,name,trace_id,span_id,parent_id,start_ns,end_ns,status,attrs,resource,body) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self._maybe_purge()

    def _maybe_purge(self) -> None:
        now = time.time()
        if now - self._last_purge < 60:
            return
        self._last_purge = now
        days = int(self.db.get_setting("otel_retention_days", "30"))
        self.db.execute("DELETE FROM otel_records WHERE ts < ?", (now - days * 86400,))

    def span(self, source: str, trace_id: str, span_id: str, parent_id: str | None, name: str, start_s: float, end_s: float,
             attrs: dict | None = None, status: str = "ok", resource: dict | None = None) -> None:
        """Record one of Agent Studio's own spans (and forward it as OTLP/JSON if a collector is configured)."""
        if self.span_listener is not None:
            try:
                self.span_listener(dict(source=source, trace_id=trace_id, span_id=span_id, parent_id=parent_id, name=name, start_s=start_s, end_s=end_s, attrs=attrs or {}, status=status))
            except Exception:  # noqa: BLE001 - an export problem must never reach an agent run
                pass
        if not self.enabled:
            return
        a = dict(attrs or {})
        res = {"service.name": "agent-studio", "agent_studio.agent": source, **(resource or {})}
        self._insert([(end_s, "trace", source, name, trace_id, span_id, parent_id or "", int(start_s * 1e9), int(end_s * 1e9), status,
                       json.dumps(a, default=str), json.dumps(res), "")])
        if self.db.get_setting("otel_forward_url"):
            self._enqueue("traces", {"resourceSpans": [{"resource": {"attributes": _otlp_attrs(res)}, "scopeSpans": [{
                "scope": {"name": "agent-studio"}, "spans": [{
                    "traceId": trace_id, "spanId": span_id, **({"parentSpanId": parent_id} if parent_id else {}), "name": name,
                    "kind": 1, "startTimeUnixNano": str(int(start_s * 1e9)), "endTimeUnixNano": str(int(end_s * 1e9)),
                    "attributes": _otlp_attrs(a), "status": {"code": 2 if status == "error" else 1}}]}]}]})

    def emit(self, event: str, **kw: Any) -> None:
        """A security finding or configuration change, for listeners such as the OCSF export. Never raises."""
        if self.event_listener is not None:
            try:
                self.event_listener(event, kw)
            except Exception:  # noqa: BLE001
                pass

    def ingest(self, signal: str, payload: dict, forward_raw: bytes | None = None) -> int:
        """Store an OTLP/HTTP-JSON export request from an external agent. Returns the number of records stored."""
        if signal not in SIGNALS:
            raise ValueError("unknown signal")
        now, rows = time.time(), []
        if signal == "traces":
            for rs in payload.get("resourceSpans", []):
                res = _attrs(rs.get("resource", {}).get("attributes"))
                src = str(res.get("agent_studio.agent") or res.get("service.name") or "unknown")
                for ss in rs.get("scopeSpans", []):
                    for sp in ss.get("spans", []):
                        st = sp.get("status", {}) or {}
                        rows.append((now, "trace", src, sp.get("name", ""), sp.get("traceId", ""), sp.get("spanId", ""),
                                     sp.get("parentSpanId", ""), int(sp.get("startTimeUnixNano", 0) or 0), int(sp.get("endTimeUnixNano", 0) or 0),
                                     "error" if st.get("code") in (2, "STATUS_CODE_ERROR") else "ok",
                                     json.dumps(_attrs(sp.get("attributes")), default=str), json.dumps(res), ""))
        elif signal == "metrics":
            for rm in payload.get("resourceMetrics", []):
                res = _attrs(rm.get("resource", {}).get("attributes"))
                src = str(res.get("agent_studio.agent") or res.get("service.name") or "unknown")
                for sm in rm.get("scopeMetrics", []):
                    for m in sm.get("metrics", []):
                        kind = next((k for k in ("sum", "gauge", "histogram", "exponentialHistogram", "summary") if k in m), None)
                        for dp in (m.get(kind, {}) or {}).get("dataPoints", []) if kind else []:
                            t = int(dp.get("timeUnixNano", 0) or 0)
                            val = dp.get("asDouble", dp.get("asInt", dp.get("sum")))
                            body = dict(type=kind, unit=m.get("unit", ""), value=val, **({"count": dp.get("count")} if "count" in dp else {}))
                            rows.append((now, "metric", src, m.get("name", ""), "", "", "", t, t, "ok",
                                         json.dumps(_attrs(dp.get("attributes")), default=str), json.dumps(res), json.dumps(body, default=str)))
        else:
            for rl in payload.get("resourceLogs", []):
                res = _attrs(rl.get("resource", {}).get("attributes"))
                src = str(res.get("agent_studio.agent") or res.get("service.name") or "unknown")
                for sl in rl.get("scopeLogs", []):
                    for lr in sl.get("logRecords", []):
                        a = _attrs(lr.get("attributes"))
                        t = int(lr.get("timeUnixNano", 0) or 0)
                        body = _attrs([{"key": "b", "value": lr.get("body", {})}]).get("b", "")
                        rows.append((now, "log", src, str(a.get("event.name") or lr.get("severityText") or "log"), lr.get("traceId", ""),
                                     lr.get("spanId", ""), "", t, t, "error" if str(lr.get("severityText", "")).upper() in ("ERROR", "FATAL") else "ok",
                                     json.dumps(a, default=str), json.dumps(res), json.dumps(body, default=str)))
        if self.enabled:
            self._insert(rows)
            if forward_raw is not None and self.db.get_setting("otel_forward_url"):
                self._enqueue(signal, forward_raw)
        return len(rows)

    # ------------------------------------------------------------------ forwarding (best effort, never blocks agents)
    def _enqueue(self, signal: str, body: Any) -> None:
        data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._pump, name="otel-forward", daemon=True)
                self._thread.start()
        try:
            self._q.put_nowait((signal, bytes(data)))
        except queue.Full:
            self.forward_errors += 1
            self.last_forward_error = "forward queue full - dropped"

    def _pump(self) -> None:
        while True:
            signal, data = self._q.get()
            url = (self.db.get_setting("otel_forward_url") or "").rstrip("/")
            if not url:
                continue
            try:
                hdr = json.loads(self.db.get_setting("otel_forward_headers", "{}") or "{}")
                req = urllib.request.Request(f"{url}/v1/{signal}", data=data, method="POST",
                                             headers={"Content-Type": "application/json", **hdr})
                with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - user-configured collector
                    r.read()
                self.forwarded += 1
            except Exception as e:  # noqa: BLE001
                self.forward_errors += 1
                self.last_forward_error = f"{type(e).__name__}: {e}"[:300]

    # ------------------------------------------------------------------ env for external agents
    def agent_env(self, agent_id: str, session_id: str | None = None) -> dict[str, str]:
        c = self.cfg()
        if not (c["enabled"] and c["inject_env"]):
            return {}
        res = f"agent_studio.agent={agent_id}" + (f",agent_studio.session={session_id}" if session_id else "")
        env = {
            "OTEL_EXPORTER_OTLP_ENDPOINT": c["receiver_url"], "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
            "OTEL_SERVICE_NAME": agent_id, "OTEL_RESOURCE_ATTRIBUTES": res,
        }
        if agent_id == "claude":      # Claude Code's documented switches: metrics + events (no prompt / tool-content logging)
            env.update(CLAUDE_CODE_ENABLE_TELEMETRY="1", OTEL_METRICS_EXPORTER="otlp", OTEL_LOGS_EXPORTER="otlp")
        return env

    # ------------------------------------------------------------------ reading
    def summary(self) -> dict:
        rows = self.db.query("SELECT signal, source, COUNT(*) n, MAX(ts) last FROM otel_records GROUP BY signal, source ORDER BY n DESC")
        total = self.db.one("SELECT COUNT(*) n FROM otel_records")["n"]
        return dict(total=total, by_source=[dict(r) for r in rows])

    def traces(self, source: str | None = None, limit: int = 50) -> list[dict]:
        where, p = ("WHERE signal='trace'" + (" AND source=?" if source else "")), ([source] if source else [])
        rows = self.db.query(
            f"SELECT trace_id, MIN(start_ns) s, MAX(end_ns) e, COUNT(*) spans, SUM(status='error') errors, MAX(ts) last, "
            f"GROUP_CONCAT(DISTINCT source) sources FROM otel_records {where} GROUP BY trace_id ORDER BY last DESC LIMIT ?", (*p, limit))
        out = []
        for r in rows:
            root = self.db.one("SELECT name FROM otel_records WHERE trace_id=? AND signal='trace' AND (parent_id='' OR parent_id IS NULL) "
                               "ORDER BY start_ns LIMIT 1", (r["trace_id"],))
            out.append(dict(trace_id=r["trace_id"], root=root["name"] if root else None, spans=r["spans"], errors=r["errors"] or 0,
                            duration_ms=round((r["e"] - r["s"]) / 1e6, 1) if r["e"] and r["s"] else None, sources=(r["sources"] or "").split(","), last=r["last"]))
        return out

    def trace(self, trace_id: str) -> list[dict]:
        return [self._row(r) for r in self.db.query("SELECT * FROM otel_records WHERE trace_id=? AND signal='trace' ORDER BY start_ns", (trace_id,))]

    def records(self, signal: str, source: str | None = None, limit: int = 100) -> list[dict]:
        q, p = "SELECT * FROM otel_records WHERE signal=?", [signal]
        if source:
            q += " AND source=?"
            p.append(source)
        return [self._row(r) for r in self.db.query(q + " ORDER BY id DESC LIMIT ?", (*p, limit))]

    def clear(self, source: str | None = None) -> int:
        cur = self.db.execute("DELETE FROM otel_records" + (" WHERE source=?" if source else ""), (source,) if source else ())
        return cur.rowcount

    @staticmethod
    def _row(r) -> dict:
        return dict(id=r["id"], ts=r["ts"], signal=r["signal"], source=r["source"], name=r["name"], trace_id=r["trace_id"], span_id=r["span_id"],
                    parent_id=r["parent_id"], start_ns=r["start_ns"], end_ns=r["end_ns"],
                    duration_ms=round((r["end_ns"] - r["start_ns"]) / 1e6, 2) if r["end_ns"] and r["start_ns"] else None, status=r["status"],
                    attrs=json.loads(r["attrs"] or "{}"), resource=json.loads(r["resource"] or "{}"), body=json.loads(r["body"]) if r["body"] else None)
