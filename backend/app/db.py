"""SQLite storage (stdlib). One process, one lock: every ledger/reservation
mutation is serialised so concurrent calls can never jointly overshoot a cap."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
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
CREATE TABLE IF NOT EXISTS runtime_releases(           -- one row per deployment of an agent to a runtime target (see runtimes/service.py)
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  target TEXT NOT NULL,
  version INTEGER NOT NULL,
  state TEXT NOT NULL,                  -- deploying | ready | failed | retired | deleted
  live INTEGER NOT NULL DEFAULT 0,      -- the release that serves the agent
  shadow INTEGER NOT NULL DEFAULT 0,    -- deployed next to the live one, receiving no traffic yet
  spec TEXT NOT NULL,                   -- the agent definition that was deployed (never a secret)
  spec_hash TEXT NOT NULL,
  image TEXT NOT NULL DEFAULT '',
  ref TEXT NOT NULL DEFAULT '{}',       -- the platform's own ids (arn, version, container...)
  prev_id TEXT,                         -- the release that was live before this one (rollback goes back to it)
  health TEXT NOT NULL DEFAULT '{}',
  detail TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS runtime_releases_agent ON runtime_releases(agent_id, created);
CREATE TABLE IF NOT EXISTS data_audit(                 -- every access to a data source (statement text with secrets removed; never the rows)
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  source TEXT NOT NULL,
  agent TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,                   -- query | schema | test
  statement TEXT NOT NULL DEFAULT '',
  rows INTEGER NOT NULL DEFAULT 0,
  ms INTEGER NOT NULL DEFAULT 0,
  ok INTEGER NOT NULL DEFAULT 1,
  error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS data_audit_source ON data_audit(source, id);
CREATE TABLE IF NOT EXISTS canvases(                    -- documents a user and an agent edit together (see canvas/service.py)
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  type TEXT NOT NULL,                   -- markdown | code | table | html | text
  language TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1,
  session_id TEXT NOT NULL DEFAULT '',
  agent TEXT NOT NULL DEFAULT '',
  artifact_id TEXT NOT NULL DEFAULT '',
  selection TEXT NOT NULL DEFAULT '{}',
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS canvases_session ON canvases(session_id, updated);
CREATE TABLE IF NOT EXISTS canvas_versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  canvas_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  author TEXT NOT NULL,                 -- user | restore | agent:<name>
  note TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL,
  UNIQUE(canvas_id, version)
);
CREATE TABLE IF NOT EXISTS canvas_proposals(            -- changes an agent suggested, waiting for review
  id TEXT PRIMARY KEY,
  canvas_id TEXT NOT NULL,
  base_version INTEGER NOT NULL,
  edits TEXT NOT NULL,
  author TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,                 -- pending | accepted | rejected | stale
  created REAL NOT NULL,
  resolved REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS canvas_proposals_canvas ON canvas_proposals(canvas_id, status);
CREATE TABLE IF NOT EXISTS science_investigations(     -- Science Lab: one question with its evidence (see science/service.py)
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  domain TEXT NOT NULL,                 -- biology | chemistry | physics | mathematics | cross
  question TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',  -- open | done
  autonomy TEXT NOT NULL DEFAULT 'approve',   -- suggest | approve | autonomous
  session_id TEXT NOT NULL DEFAULT '',
  agent TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS science_inv_updated ON science_investigations(updated);
CREATE TABLE IF NOT EXISTS science_evidence(            -- what a claim rests on: where it came from, when, with what query
  id TEXT PRIMARY KEY,
  inv_id TEXT NOT NULL,
  kind TEXT NOT NULL,                   -- source | computed | inference
  source TEXT NOT NULL DEFAULT '',      -- connector id, or "model" / "sandbox"
  ref TEXT NOT NULL DEFAULT '',         -- doi:.. | pmid:.. | uniprot:.. | url
  title TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  tool TEXT NOT NULL DEFAULT '',
  query TEXT NOT NULL DEFAULT '',
  quote TEXT NOT NULL DEFAULT '',
  value TEXT NOT NULL DEFAULT '',
  run_id TEXT NOT NULL DEFAULT '',
  retrieved_at REAL NOT NULL DEFAULT 0,
  verified INTEGER NOT NULL DEFAULT -1, -- 1 checked and found | 0 checked and NOT found | -1 not checked / could not check
  verified_note TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS science_evidence_inv ON science_evidence(inv_id, created);
CREATE TABLE IF NOT EXISTS science_claims(
  id TEXT PRIMARY KEY,
  inv_id TEXT NOT NULL,
  text TEXT NOT NULL,
  evidence TEXT NOT NULL DEFAULT '[]',
  conflicting INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS science_claims_inv ON science_claims(inv_id, created);
CREATE TABLE IF NOT EXISTS science_hypotheses(
  id TEXT PRIMARY KEY,
  inv_id TEXT NOT NULL,
  text TEXT NOT NULL,
  elo REAL NOT NULL DEFAULT 1200,
  wins INTEGER NOT NULL DEFAULT 0,
  losses INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS science_hyp_inv ON science_hypotheses(inv_id);
CREATE TABLE IF NOT EXISTS science_runs(                -- every computation and every data-source call of an investigation (the reproducibility record)
  id TEXT PRIMARY KEY,
  inv_id TEXT NOT NULL DEFAULT '',
  tool TEXT NOT NULL,
  record TEXT NOT NULL,
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS science_runs_inv ON science_runs(inv_id, created);
CREATE TABLE IF NOT EXISTS apps(                        -- apps that agents build with you (see apps/store.py); the files live in data/apps/<id>/
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  template TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'advanced',  -- simple (the builder's changes apply straight away) | advanced (you review each change)
  session_id TEXT NOT NULL DEFAULT '',    -- the builder chat that last worked on it
  version INTEGER NOT NULL DEFAULT 1,
  git TEXT NOT NULL DEFAULT '{}',         -- {remote, branch}: never a token
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS app_versions(                -- a snapshot (zip of the files) each time a change is accepted, restored or saved on purpose
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  app_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  author TEXT NOT NULL,                   -- user | restore | agent:<name>
  note TEXT NOT NULL DEFAULT '',
  files BLOB NOT NULL,
  created REAL NOT NULL,
  UNIQUE(app_id, version)
);
CREATE TABLE IF NOT EXISTS app_proposals(               -- file changes a builder agent suggested, waiting for review
  id TEXT PRIMARY KEY,
  app_id TEXT NOT NULL,
  author TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  changes TEXT NOT NULL,
  status TEXT NOT NULL,                   -- pending | accepted | rejected | stale
  created REAL NOT NULL,
  resolved REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS app_proposals_app ON app_proposals(app_id, status);
CREATE TABLE IF NOT EXISTS settings_log(               -- what changed, when (secret values are never written here)
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  key TEXT NOT NULL,
  old TEXT,
  new TEXT
);
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
CREATE TABLE IF NOT EXISTS chat_sessions(
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL,
  agent TEXT,                                -- NULL = plain chat
  model TEXT,
  workspace TEXT,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  ts REAL NOT NULL,
  role TEXT NOT NULL,                        -- user | bot | refused
  text TEXT NOT NULL DEFAULT '',
  meta TEXT NOT NULL DEFAULT '{}'            -- JSON: agent, usage, steps, model_calls, warnings, status
);
CREATE INDEX IF NOT EXISTS chat_messages_s ON chat_messages(session_id, id);
CREATE TABLE IF NOT EXISTS otel_records(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  signal TEXT NOT NULL,                      -- trace | metric | log
  source TEXT NOT NULL,                      -- agent id / service.name
  name TEXT NOT NULL DEFAULT '',
  trace_id TEXT NOT NULL DEFAULT '',
  span_id TEXT NOT NULL DEFAULT '',
  parent_id TEXT NOT NULL DEFAULT '',
  start_ns INTEGER NOT NULL DEFAULT 0,
  end_ns INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'ok',
  attrs TEXT NOT NULL DEFAULT '{}',
  resource TEXT NOT NULL DEFAULT '{}',
  body TEXT NOT NULL DEFAULT ''              -- metric value / log body (JSON)
);
CREATE INDEX IF NOT EXISTS otel_trace ON otel_records(trace_id);
CREATE INDEX IF NOT EXISTS otel_sig ON otel_records(signal, source, ts);
CREATE TABLE IF NOT EXISTS memories(                   -- what agents remember (see app/memory)
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,                                   -- episodic | semantic | entity | procedural
  scope TEXT NOT NULL,                                  -- agent | user | workspace | global
  scope_id TEXT NOT NULL,
  text TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',                      -- structured part (entities: name, kind, attrs, history)
  key TEXT NOT NULL DEFAULT '',                         -- same key + scope + type = the same fact (newer replaces older)
  confidence REAL NOT NULL DEFAULT 0.8,
  status TEXT NOT NULL DEFAULT 'active',                -- active | pending (awaiting your review) | superseded | rejected
  source TEXT NOT NULL DEFAULT '{}',                    -- provenance: agent, session, run, origin
  created REAL NOT NULL,
  updated REAL NOT NULL,
  expires REAL,
  supersedes TEXT,
  embedding TEXT,
  emb_model TEXT NOT NULL DEFAULT '',
  remote_id TEXT NOT NULL DEFAULT '',
  redacted TEXT NOT NULL DEFAULT '[]',
  hits INTEGER NOT NULL DEFAULT 0,
  last_used REAL
);
CREATE INDEX IF NOT EXISTS memories_scope ON memories(scope, scope_id, status);
CREATE TABLE IF NOT EXISTS guardrail_events(            -- every flag / block / guard failure (never the checked text, only its length)
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  stage TEXT NOT NULL,                                  -- input | output | tool
  agent TEXT NOT NULL DEFAULT '',
  profile TEXT NOT NULL,
  kind TEXT NOT NULL,
  mode TEXT NOT NULL,
  action TEXT NOT NULL,                                 -- flagged | blocked | error
  category TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  score REAL,
  chars INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS guardrail_events_ts ON guardrail_events(ts);
CREATE TABLE IF NOT EXISTS identity_events(             -- tokens issued, exchanged, delegated, refused (never the tokens)
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  event TEXT NOT NULL,                                  -- issued | exchanged | delegated | refused
  subject TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT '',
  audience TEXT NOT NULL DEFAULT '',
  scopes TEXT NOT NULL DEFAULT '',
  jti TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS identity_events_ts ON identity_events(ts);
CREATE TABLE IF NOT EXISTS api_keys(                      -- keys for the OpenAI-compatible API and A2A: only a hash is stored
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  prefix TEXT NOT NULL,                                 -- the first characters, so a key can be recognised in lists
  hash TEXT NOT NULL,
  agents TEXT NOT NULL DEFAULT '[]',                    -- agent ids this key may call; ["*"] = every agent with API access
  rate_per_min INTEGER NOT NULL DEFAULT 60,
  created REAL NOT NULL,
  last_used REAL,
  expires REAL,
  revoked INTEGER NOT NULL DEFAULT 0,
  uses INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS a2a_remotes(                   -- other agents (anywhere) that our agents may call over A2A
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  url TEXT NOT NULL,                                    -- the remote agent's base URL (its agent card is at /.well-known/agent-card.json)
  token TEXT NOT NULL DEFAULT '',                       -- bearer token for it (write-only)
  enabled INTEGER NOT NULL DEFAULT 1,
  card TEXT NOT NULL DEFAULT '{}',                      -- the agent card as last fetched
  created REAL NOT NULL,
  auth_kind TEXT NOT NULL DEFAULT 'bearer',             -- how `token` is sent: bearer (Authorization header) or header (an API key in a header)
  auth_header TEXT NOT NULL DEFAULT '',                 -- the header's name when auth_kind is 'header'
  delegates TEXT NOT NULL DEFAULT '[]'                  -- agent ids that may call it ([] = any agent that has the call_remote_agent tool)
);
CREATE TABLE IF NOT EXISTS workflows(                     -- drag-and-drop agent workflows (the current version of each)
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  doc TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  wf_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  doc TEXT NOT NULL,
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS workflow_versions_wf ON workflow_versions(wf_id, version);
CREATE TABLE IF NOT EXISTS openapi_sources(               -- APIs imported as workflow components (operations are the building blocks)
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  src TEXT NOT NULL,
  base TEXT NOT NULL,
  ops TEXT NOT NULL,
  token TEXT NOT NULL DEFAULT '',                       -- Authorization header value (write-only)
  allow_write INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_runs(                 -- finished (and running) workflow runs, kept per workflow
  id TEXT PRIMARY KEY,
  wf_id TEXT NOT NULL,
  wf_version INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  autonomy TEXT NOT NULL DEFAULT 'auto',
  task TEXT NOT NULL DEFAULT '',
  started REAL NOT NULL,
  ended REAL,
  cost_usd REAL NOT NULL DEFAULT 0,
  error TEXT,
  final TEXT NOT NULL DEFAULT '',
  nodes TEXT NOT NULL DEFAULT '{}',
  origin TEXT NOT NULL DEFAULT 'manual',                -- manual | webhook | schedule | agent | run_from: what started it
  tokens INTEGER NOT NULL DEFAULT 0,
  from_node TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS workflow_runs_wf ON workflow_runs(wf_id, started);
CREATE INDEX IF NOT EXISTS workflow_runs_started ON workflow_runs(started);
CREATE TABLE IF NOT EXISTS workflow_hooks(                -- the secret address that starts a workflow from outside: only a hash of the secret is kept
  wf_id TEXT PRIMARY KEY,
  secret_hash TEXT NOT NULL,
  created REAL NOT NULL,
  last_used REAL,
  calls INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS workflow_schedules(            -- the on/off switch and bookkeeping of a workflow's Schedule box (never exported)
  wf_id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 0,
  spec TEXT NOT NULL DEFAULT '',
  next_run REAL,
  last_run REAL,
  last_run_id TEXT,
  last_status TEXT,
  runs INTEGER NOT NULL DEFAULT 0,
  fails INTEGER NOT NULL DEFAULT 0,
  day TEXT NOT NULL DEFAULT '',
  day_count INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications(                 -- the in-app notification center (workflows' Notify boxes, the scheduler); nothing is sent anywhere
  id TEXT PRIMARY KEY,
  created REAL NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  level TEXT NOT NULL DEFAULT 'info',
  source TEXT NOT NULL DEFAULT '',
  link TEXT NOT NULL DEFAULT '',
  read INTEGER NOT NULL DEFAULT 0,
  desktop INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS notifications_created ON notifications(created);
CREATE TABLE IF NOT EXISTS workflow_drafts(               -- the autosaved, unpublished edit of a workflow (one per workflow id)
  wf_id TEXT PRIMARY KEY,
  doc TEXT NOT NULL,
  updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_sets(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  cases TEXT NOT NULL DEFAULT '[]',                     -- [{id, prompt, expect:[assertion...]}]
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_runs(
  id TEXT PRIMARY KEY,
  set_id TEXT NOT NULL,
  agent TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  started REAL NOT NULL,
  ended REAL,
  status TEXT NOT NULL DEFAULT 'running',               -- running | done | error | cancelled
  summary TEXT NOT NULL DEFAULT '{}',
  results TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS eval_runs_set ON eval_runs(set_id, started);
CREATE TABLE IF NOT EXISTS redteam_runs(                -- red-team tool runs against one agent (see app/security/redteam.py)
  id TEXT PRIMARY KEY,
  tool TEXT NOT NULL,
  agent TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  started REAL NOT NULL,
  ended REAL,
  status TEXT NOT NULL DEFAULT 'running',               -- running | done | failed | cancelled | interrupted
  max_requests INTEGER NOT NULL DEFAULT 0,
  requests INTEGER NOT NULL DEFAULT 0,
  blocked INTEGER NOT NULL DEFAULT 0,                   -- probes a guardrail stopped
  errors INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  options TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS artifacts(                   -- files and outputs agents produced (see app/artifacts); the bytes live in the storages
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  path TEXT NOT NULL DEFAULT '',                        -- where it was in the workspace ('' = saved from text)
  type TEXT NOT NULL,                                   -- document | code | image | data | model | archive | audio | video | other
  mime TEXT NOT NULL,
  preview TEXT NOT NULL DEFAULT 'none',
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  created REAL NOT NULL,
  agent TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  trace_id TEXT NOT NULL DEFAULT '',                    -- the run (Runs page) that made it
  workspace TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'auto',                  -- auto (found in the workspace) | tool (the agent saved it) | chat (saved from an answer) | upload
  tags TEXT NOT NULL DEFAULT '[]',
  summary TEXT NOT NULL DEFAULT '',
  search_text TEXT NOT NULL DEFAULT '',                 -- the start of a text file, secrets removed, for content search
  secrets INTEGER NOT NULL DEFAULT 0                    -- 1 = a secret-looking value was found in it
);
CREATE INDEX IF NOT EXISTS artifacts_created ON artifacts(created);
CREATE INDEX IF NOT EXISTS artifacts_path ON artifacts(workspace, path);
CREATE TABLE IF NOT EXISTS artifact_copies(             -- one row per storage that holds (or should hold) the artifact
  artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  storage_id TEXT NOT NULL,
  key TEXT NOT NULL,
  status TEXT NOT NULL,                                 -- pending | synced | failed | blocked
  detail TEXT NOT NULL DEFAULT '',
  size INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT NOT NULL DEFAULT '',
  updated REAL NOT NULL,
  PRIMARY KEY(artifact_id, storage_id)
);
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
        self.change_hook = None                 # called with (key, old, new) after a setting changes (the OCSF export listens)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(SCHEMA)
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(providers)")}
            for c in ("pro_model", "flash_model"):  # Phase 2 migration
                if c not in cols:
                    self.conn.execute(f"ALTER TABLE providers ADD COLUMN {c} TEXT")
            acols = {r["name"] for r in self.conn.execute("PRAGMA table_info(agents)")}
            if "can_spawn" not in acols:  # Phase 4: agents may create sub-agents (opt-in per agent; the seeded engineer starts enabled)
                self.conn.execute("ALTER TABLE agents ADD COLUMN can_spawn INTEGER NOT NULL DEFAULT 0")
                self.conn.execute("UPDATE agents SET can_spawn=1 WHERE id='engineer' AND builtin=1")
            if "acp_agents" not in acols:  # agents may call external ACP agents (opt-in, empty by default)
                self.conn.execute("ALTER TABLE agents ADD COLUMN acp_agents TEXT NOT NULL DEFAULT '[]'")
                self.conn.execute("ALTER TABLE agents ADD COLUMN acp_policy TEXT NOT NULL DEFAULT 'ask'")
            if "rag" not in acols:  # knowledge bases (RAG) an agent may search; empty by default
                self.conn.execute("ALTER TABLE agents ADD COLUMN rag TEXT NOT NULL DEFAULT '[]'")
            if "memory" not in acols:  # memory settings (kinds, scopes, recall, saving); {} = memory off
                self.conn.execute("ALTER TABLE agents ADD COLUMN memory TEXT NOT NULL DEFAULT '{}'")
            if "guardrails" not in acols:  # guardrail profile ids this agent uses; [] = the default profiles, ["none"] = no guardrails
                self.conn.execute("ALTER TABLE agents ADD COLUMN guardrails TEXT NOT NULL DEFAULT '[]'")
            if "identity" not in acols:  # {scopes, delegates}: what this agent's identity may be used for; {} = the defaults
                self.conn.execute("ALTER TABLE agents ADD COLUMN identity TEXT NOT NULL DEFAULT '{}'")
            if "framework" not in acols:  # which agent framework runs it: adk (built in), langgraph, strands, ...
                self.conn.execute("ALTER TABLE agents ADD COLUMN framework TEXT NOT NULL DEFAULT 'adk'")
            if "api_access" not in acols:  # callable through the authenticated OpenAI-compatible API (/v1)
                self.conn.execute("ALTER TABLE agents ADD COLUMN api_access INTEGER NOT NULL DEFAULT 0")
            if "a2a" not in acols:  # reachable by other agents over A2A (agent card + JSON-RPC)
                self.conn.execute("ALTER TABLE agents ADD COLUMN a2a INTEGER NOT NULL DEFAULT 0")
            if "runtime" not in acols:  # where it runs: local (default), docker, agentcore, vertex, foundry (see runtimes/)
                self.conn.execute("ALTER TABLE agents ADD COLUMN runtime TEXT NOT NULL DEFAULT 'local'")
            if "sandbox" not in acols:  # which sandbox its commands run in: '' = the default one (see sandboxes/)
                self.conn.execute("ALTER TABLE agents ADD COLUMN sandbox TEXT NOT NULL DEFAULT ''")
            if "data" not in acols:  # data sources it may read (see datasources/); [] = none, ["*"] = all
                self.conn.execute("ALTER TABLE agents ADD COLUMN data TEXT NOT NULL DEFAULT '[]'")
            if "agui" not in acols:  # AG-UI switches: {"components": bool, "endpoint": bool}; '{}' = both on (see agui_flags.py)
                self.conn.execute("ALTER TABLE agents ADD COLUMN agui TEXT NOT NULL DEFAULT '{}'")
            if "live" not in acols:  # Live profile: how the agent behaves in live mode (engine, voice or avatar, voice, budget); '{}' = none (see live_profile.py)
                self.conn.execute("ALTER TABLE agents ADD COLUMN live TEXT NOT NULL DEFAULT '{}'")
            ocols = {r["name"] for r in self.conn.execute("PRAGMA table_info(openapi_sources)")}
            for col, ddl in (("spec", "TEXT NOT NULL DEFAULT ''"), ("origin", "TEXT NOT NULL DEFAULT 'url'"), ("auth", "TEXT NOT NULL DEFAULT '{}'"),
                             ("servers", "TEXT NOT NULL DEFAULT '[]'"), ("refreshed", "REAL"), ("updated", "REAL"), ("overrides", "TEXT NOT NULL DEFAULT '{}'"),
                             ("custom", "TEXT NOT NULL DEFAULT '{}'"), ("meta", "TEXT NOT NULL DEFAULT '{}'")):
                if col not in ocols:  # imported APIs: the stored spec text, where it came from, how it authenticates, per-operation edits and custom endpoints
                    self.conn.execute(f"ALTER TABLE openapi_sources ADD COLUMN {col} {ddl}")
            vcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(workflow_versions)")}
            for col in ("label", "note"):
                if col not in vcols:  # a name and a note for a saved workflow version
                    self.conn.execute(f"ALTER TABLE workflow_versions ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            wcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(workflow_runs)")}
            for col, ddl in (("origin", "TEXT NOT NULL DEFAULT 'manual'"), ("tokens", "INTEGER NOT NULL DEFAULT 0"), ("from_node", "TEXT NOT NULL DEFAULT ''")):
                if col not in wcols:  # runs of every workflow in one list: what started each run (origin), how many tokens it used, which box it began from
                    self.conn.execute(f"ALTER TABLE workflow_runs ADD COLUMN {col} {ddl}")
            rcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(a2a_remotes)")}
            for col, ddl in (("auth_kind", "TEXT NOT NULL DEFAULT 'bearer'"), ("auth_header", "TEXT NOT NULL DEFAULT ''"), ("delegates", "TEXT NOT NULL DEFAULT '[]'")):
                if col not in rcols:  # remote A2A agents: how the token is sent, and which agents may call it
                    self.conn.execute(f"ALTER TABLE a2a_remotes ADD COLUMN {col} {ddl}")

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
        from .settings_registry import INTERNAL_KEYS, SECRET_KEYS
        old = self.get_setting(key)
        self.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        if old != value and key not in INTERNAL_KEYS:           # history for the Settings page; secrets show only "changed"
            hide = key in SECRET_KEYS
            self.execute("INSERT INTO settings_log(ts,key,old,new) VALUES(?,?,?,?)",
                         (time.time(), key, None if hide else (old[:300] if old is not None else None), "(changed)" if hide else value[:300]))
            self.execute("DELETE FROM settings_log WHERE id <= (SELECT MAX(id) FROM settings_log) - 500")
            if self.change_hook is not None:
                try:
                    self.change_hook(key, old, value)
                except Exception:  # noqa: BLE001 - a listener must never break saving a setting
                    pass
