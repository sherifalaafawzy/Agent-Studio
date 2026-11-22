"""Issues the harness raises by itself (for example an MCP server that failed to start).

Each issue says what happened, why it matters, and what to do next. Issues are deduplicated while open, and can
start a debugging run automatically (Settings -> Agents & sandbox -> "Debug failing tools automatically", off by default).
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Callable

from .db import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues(
  id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,              -- mcp_failed | ...
  source TEXT NOT NULL,            -- the server / agent / provider it is about
  severity TEXT NOT NULL DEFAULT 'warn',
  title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  suggestion TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL DEFAULT '{}',   -- JSON, e.g. {"type": "debug_mcp", "mcp": "semgrep"}
  status TEXT NOT NULL DEFAULT 'open', -- open | resolved
  debug_job TEXT
);
CREATE INDEX IF NOT EXISTS issues_open ON issues(status, kind, source);
"""


class IssueStore:
    def __init__(self, db: Database):
        self.db = db
        self.on_new: Callable[[dict], None] | None = None
        db.conn.executescript(SCHEMA)

    def add(self, kind: str, source: str, title: str, detail: str = "", suggestion: str = "", action: dict | None = None,
            severity: str = "warn") -> dict:
        row = self.db.one("SELECT * FROM issues WHERE status='open' AND kind=? AND source=?", (kind, source))
        if row:                                              # already raised and still open: refresh the detail, don't duplicate
            self.db.execute("UPDATE issues SET detail=?, ts=? WHERE id=?", (detail[:2000], time.time(), row["id"]))
            return self.get(row["id"])
        iid = uuid.uuid4().hex[:12]
        self.db.execute("INSERT INTO issues(id,ts,kind,source,severity,title,detail,suggestion,action) VALUES(?,?,?,?,?,?,?,?,?)",
                        (iid, time.time(), kind, source, severity, title[:200], detail[:2000], suggestion[:1000], json.dumps(action or {})))
        issue = self.get(iid)
        if self.on_new:
            try:
                self.on_new(issue)
            except Exception:  # noqa: BLE001 - raising an issue must never break the thing that raised it
                pass
        return issue

    def get(self, iid: str) -> dict | None:
        r = self.db.one("SELECT * FROM issues WHERE id=?", (iid,))
        return self._row(r) if r else None

    def list(self, status: str | None = "open") -> list[dict]:
        q, a = "SELECT * FROM issues", []
        if status:
            q += " WHERE status=?"
            a.append(status)
        return [self._row(r) for r in self.db.query(q + " ORDER BY ts DESC LIMIT 200", a)]

    def resolve(self, iid: str) -> bool:
        return self.db.execute("UPDATE issues SET status='resolved' WHERE id=?", (iid,)).rowcount > 0

    def set_debug_job(self, iid: str, job_id: str) -> None:
        self.db.execute("UPDATE issues SET debug_job=? WHERE id=?", (job_id, iid))

    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["action"] = json.loads(d.get("action") or "{}")
        return d
