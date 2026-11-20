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
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  instruction TEXT NOT NULL DEFAULT '',
  provider TEXT,                       -- NULL = inherit the provider chosen in chat
  tier TEXT NOT NULL DEFAULT 'flash',  -- pro | flash | custom
  model TEXT,                          -- used when tier = custom
  tools TEXT NOT NULL DEFAULT '[]',
  mcp TEXT NOT NULL DEFAULT '[]',
  skills TEXT NOT NULL DEFAULT '[]',   -- ["*"] = all
  sub_agents TEXT NOT NULL DEFAULT '[]',
  builtin INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mcp_servers(
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  transport TEXT NOT NULL DEFAULT 'stdio',   -- stdio | http
  command TEXT,
  args TEXT NOT NULL DEFAULT '[]',
  url TEXT,
  env TEXT NOT NULL DEFAULT '{}',            -- values (secrets live here, never returned by the API)
  requires_env TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 0,
  default_on INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',     -- active | pending_approval (agent-proposed)
  builtin INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS acp_agents(
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  command TEXT NOT NULL,
  args TEXT NOT NULL DEFAULT '[]',
  env TEXT NOT NULL DEFAULT '{}',            -- secrets live here, never returned by the API
  enabled INTEGER NOT NULL DEFAULT 1,
  builtin INTEGER NOT NULL DEFAULT 0,
  max_turns INTEGER NOT NULL DEFAULT 30,     -- per live session (billing guard does NOT cover these agents)
  notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS acp_usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  agent TEXT NOT NULL,
  session TEXT NOT NULL,
  stop_reason TEXT,
  input_tokens INTEGER,                      -- NULL = the agent did not report usage
  output_tokens INTEGER,
  total_tokens INTEGER,
  cost REAL,                                 -- as reported by the agent (NULL = not reported)
  cost_currency TEXT,
  duration_s REAL
);
CREATE INDEX IF NOT EXISTS acp_usage_a ON acp_usage(agent, ts);
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
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(providers)")}
            for c in ("pro_model", "flash_model"):  # Phase 2 migration
                if c not in cols:
                    self.conn.execute(f"ALTER TABLE providers ADD COLUMN {c} TEXT")

    def execute(self, sql: str, params: tuple | list = ()):
        with self.lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list = ()):
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return r["value"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
