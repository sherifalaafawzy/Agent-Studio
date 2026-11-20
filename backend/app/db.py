"""SQLite storage (stdlib). One process, one lock: every ledger/reservation
mutation is serialised so concurrent calls can never jointly overshoot a cap."""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    d = Path(os.environ.get("AGENT_STUDIO_HOME") or (REPO_ROOT / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


SCHEMA = """
CREATE TABLE IF NOT EXISTS providers(
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  api_key TEXT,
  api_base TEXT,
  vertex_project TEXT,
  vertex_location TEXT,
  vertex_credentials_path TEXT,
  default_model TEXT,
  cap_usd REAL NOT NULL DEFAULT 1.0,
  cap_period TEXT NOT NULL DEFAULT 'total',
  max_output_tokens INTEGER NOT NULL DEFAULT 2048
);
CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  kind TEXT NOT NULL,            -- 'call' (spent) | 'refused' (blocked pre-flight)
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,       -- actual/estimated spend (0 for refused)
  projected_usd REAL NOT NULL DEFAULT 0,  -- worst-case projection at pre-flight
  settled_from TEXT NOT NULL DEFAULT 'usage', -- 'usage' | 'reservation' (no usage reported -> charged worst case)
  price_known INTEGER NOT NULL DEFAULT 1,
  note TEXT
);
CREATE INDEX IF NOT EXISTS ledger_pt ON ledger(provider, ts);
CREATE TABLE IF NOT EXISTS reservations(
  id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  amount_usd REAL NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | str | None = None):
        self.path = str(path or (data_dir() / "agent_studio.db"))
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple | list = ()):
        with self.lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list = ()):
        with self.lock:
            return self.conn.execute(sql, params).fetchone()
