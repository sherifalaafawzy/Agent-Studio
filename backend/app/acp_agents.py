"""External coding agents over ACP (Agent Client Protocol).

Agent Studio is the ACP *client*: it spawns an agent CLI (Claude Code, Cursor, Antigravity, Grok Build, ...)
as a subprocess, talks JSON-RPC over stdio, streams the agent's output to the UI, and answers the agent's
requests (permissions, questions, file reads/writes) on the user's behalf.

IMPORTANT - what is and is NOT enforced here:
  * The per-provider BudgetGuard does NOT cover these agents: they make their own model calls with their own
    credentials. We record the usage/cost they REPORT (after the fact) and cap turns per session.
  * Permission requests go to the user (policy "ask") unless the run policy auto-handles them.
  * File reads/writes the agent delegates to us (fs/*) are path-jailed to the workspace. The agent's OWN
    shell/tools are not sandboxed by us - they run as your user, as they would in a terminal.
"""
from __future__ import annotations

import asyncio
import calendar
import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import acp
from acp import schema as S

from . import cli_agents
from .cli_agents import CliError
from .db import Database, data_dir
from .harness.files import ToolError, jail, read_text, write_text

CLIENT_VERSION = "0.1.0"
INTERACT_TIMEOUT_S = 600          # a permission/question left unanswered this long is cancelled (= denied)
DEFAULT_TURN_TIMEOUT_S = 900
MAX_LIVE_SESSIONS = 6
IDLE_REAP_S = 1800
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
READ_KINDS = {"read", "search", "think"}   # 'fetch' is network access -> always asked

# Launch commands come from each vendor's own docs (verified 2026-11-21); the user can edit them in Settings.
# kind "acp": spawned and spoken to over the Agent Client Protocol. kind "cli": a headless-CLI adapter (cli_agents.py).
# `install` = the exact instruction shown when a harness is missing; `billing` = whose account/keys it spends;
# `version_cmd` = a free health check (never starts a model call).
CATALOG: list[dict] = [
    dict(id="claude", label="Claude Code", kind="acp", command="npx",
         args=["-y", "@agentclientprotocol/claude-agent-acp@0.101.0"], version_cmd=["claude", "--version"],
         install="Needs Node.js (npx fetches the pinned ACP adapter on first use). Sign in once by running `claude` in a terminal, or store ANTHROPIC_API_KEY below.",
         billing="your Claude subscription or Anthropic API key (not capped by Agent Studio)",
         notes="DEFAULT for large coding tasks. ACP adapter over the Claude Agent SDK. Uses your Claude Code login (run `claude` once) or ANTHROPIC_API_KEY in env.",
         env_hint=["ANTHROPIC_API_KEY"]),
    dict(id="codex", label="OpenAI Codex", kind="acp", command="npx",
         args=["-y", "@agentclientprotocol/codex-acp@2.5.0"], version_cmd=["codex", "--version"],
         install="Needs Node.js (npx fetches the pinned ACP adapter, which bundles the Codex CLI). Sign in once with `npx -y @openai/codex@0.167.0 login` (ChatGPT account), or store OPENAI_API_KEY / CODEX_API_KEY below.",
         billing="your ChatGPT plan or OpenAI API key (not capped by Agent Studio)",
         notes="OpenAI Codex CLI through the ACP adapter (`codex-acp`). Codex applies its own sandbox/approval rules; permission requests it sends are handled by the run policy.",
         env_hint=["OPENAI_API_KEY", "CODEX_API_KEY"]),
    dict(id="aider", label="Aider", kind="cli", command="aider",
         args=["--yes-always", "--no-auto-commits", "--no-pretty", "--no-stream", "--no-check-update", "--analytics-disable",
               "--no-suggest-shell-commands", "--no-show-release-notes", "--no-detect-urls"],
         version_cmd=["aider", "--version"],
         install="Install: `uv tool install --python 3.12 aider-chat==0.86.2` (needs uv; pulls Python 3.12 if missing). Then set the model with `--model <name>` in the arguments below and store that provider's key (e.g. OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY, or any provider Aider supports).",
         billing="YOUR OWN provider key (any provider Aider supports, incl. local models via an OpenAI-compatible base URL); Aider prints per-run cost",
         notes="No ACP: run through its headless CLI (`aider --message-file`, one fresh run per call - put the full context in the task). Edits files, no shell. Not under Agent Studio's caps.",
         env_hint=["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_BASE"]),
    dict(id="openhands", label="OpenHands", kind="cli", command="openhands",
         args=["--headless", "--json", "--override-with-envs", "--exit-without-confirmation"],
         version_cmd=["openhands", "--version"],
         install="Linux/macOS/WSL only. On Windows run inside WSL (Ubuntu): `uv tool install openhands==1.16.0 --python 3.12` (needs uv >= 0.11.6 and Python 3.12 there; set WSL_DISTRO below if it is not your default distro). Provide the model via LLM_MODEL, LLM_API_KEY and optionally LLM_BASE_URL below. The CLI is flagged 'no longer actively maintained' upstream.",
         billing="YOUR OWN provider via LLM_MODEL / LLM_API_KEY / LLM_BASE_URL (any LiteLLM provider, incl. local); cost is parsed if reported, else unknown",
         notes="No stable ACP on Windows: run through its headless CLI (`openhands --headless --json`, in WSL on Windows). It AUTO-APPROVES every action incl. shell for the whole run, so Agent Studio asks you once before each run and refuses the readonly policy.",
         env_hint=["LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL", "WSL_DISTRO"]),
    dict(id="cursor", label="Cursor", kind="acp", command="cursor-agent", args=["acp"],
         notes="Needs the standalone Cursor Agent CLI (`cursor-agent`; Grok also installs a different `agent`, so don't use that name) - the Cursor IDE alone does not include it. Install: `irm 'https://cursor.com/install?win32=true' | iex`, then `agent login` once (or set CURSOR_API_KEY).",
         env_hint=["CURSOR_API_KEY"]),
    dict(id="kiro", label="Kiro", kind="acp", command="kiro-cli", args=["acp"],
         notes="Kiro CLI in ACP mode (`kiro-cli acp`). Sign in once with `kiro-cli login`. Add `--trust-all-tools` to args only if you want it to skip its own approvals.",
         env_hint=[]),
    dict(id="antigravity", label="Antigravity", kind="acp", command="agy", args=["acp"],
         notes="Needs the Antigravity CLI (`agy`) - the Antigravity IDE alone does not put it on PATH. Sign in once with `agy acp --login` (Google account), OR store a Gemini API key below (GEMINI_API_KEY) and Agent Studio uses Antigravity's API-key sign-in.",
         env_hint=["GEMINI_API_KEY"]),
    dict(id="grok", label="Grok Build", kind="acp", command="grok", args=["agent", "stdio"],
         notes="xAI's Grok Build CLI (`grok`). This is NOT the 'Grok Bot' desktop app, which has no ACP interface. Install Grok Build and sign in first.",
         env_hint=["XAI_API_KEY"]),
]
_HINTS = {c["id"]: c["env_hint"] for c in CATALOG}
_META = {c["id"]: c for c in CATALOG}
CLI_KINDS = {c["id"] for c in CATALOG if c["kind"] == "cli"}


class AcpError(Exception):
    """Expected, user-actionable failure."""


def _refresh_path() -> None:
    """Windows: pick up PATH entries added by installers after this server started (no restart needed)."""
    if os.name != "nt":
        return
    try:
        import winreg
        parts: list[str] = []
        for hive, sub in ((winreg.HKEY_CURRENT_USER, "Environment"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            with contextlib.suppress(OSError), winreg.OpenKey(hive, sub) as k:
                parts += os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]).split(";")
        have = {x.lower().rstrip("\\") for x in os.environ.get("PATH", "").split(";")}
        extra = [x for x in parts if x and x.lower().rstrip("\\") not in have]
        if extra:
            os.environ["PATH"] = os.environ.get("PATH", "") + ";" + ";".join(extra)
    except Exception:
        pass


def _clip(v: Any, n: int = 4000) -> Any:
    if v is None or isinstance(v, (int, float, bool)):
        return v
    s = v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"…[{len(s) - n} more chars]"


def _content_text(content: Any) -> str:
    """Best-effort text of an ACP content block / tool-call content list."""
    if content is None:
        return ""
    if isinstance(content, list):
        return "\n".join(t for t in (_content_text(c) for c in content) if t)
    t = getattr(content, "type", None)
    if t == "content":
        return _content_text(content.content)
    if t == "diff":
        return f"edit {getattr(content, 'path', '')}"
    if t == "terminal":
        return f"terminal {getattr(content, 'terminal_id', '')}"
    txt = getattr(content, "text", None)
    return txt if txt is not None else (f"[{t}]" if t else "")


# --------------------------------------------------------------------------- configuration store

class AcpStore:
    def __init__(self, db: Database):
        self.db = db
        cols = {r["name"] for r in db.query("PRAGMA table_info(acp_agents)")}
        for c, ddl in (("month_cost_cap", "REAL NOT NULL DEFAULT 0"), ("month_turn_cap", "INTEGER NOT NULL DEFAULT 0"),
                       ("month_minutes_cap", "REAL NOT NULL DEFAULT 0")):
            if c not in cols:     # per-agent monthly limits Agent Studio enforces itself (0 = no limit)
                db.execute(f"ALTER TABLE acp_agents ADD COLUMN {c} {ddl}")
        for c in CATALOG:
            notes = c["notes"] + (f" SETUP: {c['install']}" if c.get("install") else "")   # Settings shows `notes`: keep the install steps in it
            db.execute("INSERT OR IGNORE INTO acp_agents(id,label,command,args,env,enabled,builtin,notes) VALUES(?,?,?,?,?,1,1,?)",
                       (c["id"], c["label"], c["command"], json.dumps(c["args"]), "{}", notes))
            db.execute("UPDATE acp_agents SET notes=? WHERE id=? AND builtin=1", (notes, c["id"]))   # refresh guidance on upgrade
        # migration: Grok's installer ships its own `agent`, which shadows Cursor's; Cursor also installs `cursor-agent`
        db.execute("UPDATE acp_agents SET command='cursor-agent' WHERE id='cursor' AND builtin=1 AND command='agent'")

    def _view(self, r) -> dict:
        _refresh_path()
        env = json.loads(r["env"] or "{}")
        meta = _META.get(r["id"], {})
        kind = meta.get("kind", "acp") if r["builtin"] else "acp"
        exe = shutil.which(r["command"]) or (r["command"] if Path(r["command"]).is_file() else None)
        if exe is None and kind == "cli" and cli_agents.is_installed(r["command"], env):
            exe = r["command"]                         # present inside WSL (Windows): the launcher resolves it per run
        keys = sorted(set(_HINTS.get(r["id"], [])) | set(env))
        return dict(id=r["id"], label=r["label"], command=r["command"], args=json.loads(r["args"] or "[]"),
                    kind=kind, install=meta.get("install", ""), billing=meta.get("billing", "its own account (not capped by Agent Studio)"),
                    enabled=bool(r["enabled"]), builtin=bool(r["builtin"]), max_turns=r["max_turns"], notes=r["notes"],
                    month_cost_cap=r["month_cost_cap"], month_turn_cap=r["month_turn_cap"], month_minutes_cap=r["month_minutes_cap"],
                    month=self.month_usage(r["id"]),
                    installed=exe is not None, resolved_path=exe, env_keys=keys, env_set={k: bool(env.get(k)) for k in keys})

    def list(self) -> list[dict]:
        return [self._view(r) for r in self.db.query("SELECT * FROM acp_agents ORDER BY builtin DESC, id")]

    def get(self, aid: str) -> dict:
        r = self.db.one("SELECT * FROM acp_agents WHERE id=?", (aid,))
        if not r:
            raise AcpError(f"unknown external agent '{aid}'")
        return self._view(r)

    def env_values(self, aid: str) -> dict[str, str]:
        r = self.db.one("SELECT env FROM acp_agents WHERE id=?", (aid,))
        return {k: v for k, v in json.loads(r["env"] or "{}").items() if v} if r else {}

    def create(self, aid: str, label: str, command: str, args: list[str], notes: str = "") -> dict:
        if not ID_RE.match(aid):
            raise AcpError("id must be 2-32 chars: lowercase letters, digits, - or _, starting with a letter")
        if self.db.one("SELECT 1 FROM acp_agents WHERE id=?", (aid,)):
            raise AcpError(f"'{aid}' already exists")
        if not command.strip():
            raise AcpError("command is required")
        self.db.execute("INSERT INTO acp_agents(id,label,command,args,env,enabled,builtin,notes) VALUES(?,?,?,?,?,1,0,?)",
                        (aid, label or aid, command.strip(), json.dumps(args), "{}", notes))
        return self.get(aid)

    def update(self, aid: str, patch: dict) -> dict:
        self.get(aid)
        sets, vals = [], []
        for k in ("label", "command", "notes"):
            if patch.get(k) is not None:
                if k == "command" and not str(patch[k]).strip():
                    raise AcpError("command cannot be empty")
                sets.append(f"{k}=?"); vals.append(str(patch[k]).strip() if k == "command" else patch[k])
        if patch.get("args") is not None:
            sets.append("args=?"); vals.append(json.dumps([str(a) for a in patch["args"]]))
        if patch.get("enabled") is not None:
            sets.append("enabled=?"); vals.append(1 if patch["enabled"] else 0)
        if patch.get("max_turns") is not None:
            sets.append("max_turns=?"); vals.append(max(1, min(int(patch["max_turns"]), 1000)))
        for k in ("month_cost_cap", "month_turn_cap", "month_minutes_cap"):
            if patch.get(k) is not None:
                if float(patch[k]) < 0:
                    raise AcpError(f"{k} must be >= 0")
                sets.append(f"{k}=?"); vals.append(int(patch[k]) if k == "month_turn_cap" else float(patch[k]))
        if patch.get("env"):
            env = self.env_values(aid)
            for k, v in patch["env"].items():
                if v:
                    env[k] = v
                else:
                    env.pop(k, None)
            sets.append("env=?"); vals.append(json.dumps(env))
        if sets:
            self.db.execute(f"UPDATE acp_agents SET {','.join(sets)} WHERE id=?", (*vals, aid))
        return self.get(aid)

    def delete(self, aid: str) -> None:
        r = self.db.one("SELECT builtin FROM acp_agents WHERE id=?", (aid,))
        if not r:
            raise AcpError(f"unknown external agent '{aid}'")
        if r["builtin"]:
            raise AcpError("built-in agents can be disabled but not deleted")
        self.db.execute("DELETE FROM acp_agents WHERE id=?", (aid,))

    async def version(self, aid: str, workspace: Path) -> str | None:
        """Free health check: run the harness' own `--version` (never a model call). None if it has no such check
        or the underlying CLI is not on this machine (e.g. an ACP adapter that bundles its own copy)."""
        cfg, cmd = self.get(aid), _META.get(aid, {}).get("version_cmd")
        if not cmd or not cfg["builtin"]:
            return None
        env = self.env_values(aid)
        if cfg["kind"] == "cli":                          # the harness itself (possibly via WSL), using the configured command
            return await cli_agents.version_of(cfg["command"], cmd[1:], workspace, {**os.environ, **env})
        if not cli_agents.is_installed(cmd[0], env):      # ACP adapters bundle their CLI: the global one is only informational
            return None
        return await cli_agents.version_of(cmd[0], cmd[1:], workspace, {**os.environ, **env})

    def month_usage(self, aid: str) -> dict:
        """This calendar month (UTC): turns, minutes and the cost the agent REPORTED (None if it never reports cost)."""
        g = time.gmtime()
        start = calendar.timegm((g.tm_year, g.tm_mon, 1, 0, 0, 0))
        r = self.db.one("SELECT COUNT(*) n, SUM(cost) c, SUM(duration_s) d, SUM(CASE WHEN cost IS NOT NULL THEN 1 ELSE 0 END) nc "
                        "FROM acp_usage WHERE agent=? AND ts>=?", (aid, start))
        return dict(turns=r["n"], minutes=round((r["d"] or 0) / 60, 2), reported_cost=r["c"], cost_reported_turns=r["nc"])

    def check_limits(self, aid: str) -> None:
        """Refuse a new turn BEFORE it starts if a monthly limit is used up."""
        a, m = self.get(aid), self.month_usage(aid)
        if a["month_turn_cap"] and m["turns"] >= a["month_turn_cap"]:
            raise AcpError(f"{a['label']}: monthly turn limit reached ({m['turns']}/{a['month_turn_cap']}). Raise it in Settings -> External agents.")
        if a["month_minutes_cap"] and m["minutes"] >= a["month_minutes_cap"]:
            raise AcpError(f"{a['label']}: monthly time limit reached ({m['minutes']:.0f}/{a['month_minutes_cap']:.0f} min). Raise it in Settings -> External agents.")
        if a["month_cost_cap"]:
            if m["turns"] and not m["cost_reported_turns"]:
                raise AcpError(f"{a['label']}: a monthly cost limit is set, but this agent doesn't report cost - Agent Studio can't verify it. "
                               "Use a turn or minute limit for it instead (Settings -> External agents).")
            if (m["reported_cost"] or 0) >= a["month_cost_cap"]:
                raise AcpError(f"{a['label']}: monthly cost limit reached (reported ${m['reported_cost']:.2f} of ${a['month_cost_cap']:.2f}).")

    def usage(self) -> list[dict]:
        rows = self.db.query("""SELECT agent, COUNT(*) turns, SUM(input_tokens) i, SUM(output_tokens) o, SUM(total_tokens) t,
                                SUM(cost) c, MAX(cost_currency) cur, SUM(duration_s) d, MAX(ts) last,
                                SUM(CASE WHEN total_tokens IS NULL AND cost IS NULL THEN 1 ELSE 0 END) unreported
                                FROM acp_usage GROUP BY agent ORDER BY last DESC""")
        return [dict(agent=r["agent"], turns=r["turns"], input_tokens=r["i"], output_tokens=r["o"], total_tokens=r["t"],
                     reported_cost=r["c"], currency=r["cur"], seconds=round(r["d"] or 0, 1), last_ts=r["last"],
                     turns_without_usage=r["unreported"], capped=False) for r in rows]


# --------------------------------------------------------------------------- live sessions

@dataclass
class Pending:
    id: str
    kind: str                     # permission | question | plan
    session_id: str
    fut: asyncio.Future
    options: list[str] = field(default_factory=list)


def _noop(_ev: dict) -> None:
    return None


class AcpSession:
    def __init__(self, sid: str, agent: dict, workspace: Path):
        self.id, self.agent, self.workspace = sid, agent, workspace
        self.stack = AsyncExitStack()
        self.conn: Any = None
        self.proc: Any = None
        self.acp_id: str = ""
        self.turns = 0
        self.lock = asyncio.Lock()
        self.emit: Callable[[dict], None] = _noop
        self.policy = "ask"
        self.stderr: deque[str] = deque(maxlen=40)
        self.stderr_task: asyncio.Task | None = None
        self.info: dict = {}
        self.modes: dict | None = None
        self.last_used = time.time()
        self.last_usage_update: dict | None = None
        self.closed = False
        self.kind: str = agent.get("kind", "acp")      # "cli" sessions have no connection: one process per prompt (cli_agents.py)
        self.cancel_requested = False

    def public(self) -> dict:
        return dict(session_id=self.id, agent=self.agent["id"], workspace=self.workspace.as_posix(), turns=self.turns,
                    busy=self.lock.locked(), idle_s=int(time.time() - self.last_used), modes=self.modes, info=self.info)


class _Client:
    """The ACP Client implementation handed to the SDK (one per session)."""

    def __init__(self, mgr: "AcpManager", sess: AcpSession):
        self.mgr, self.sess = mgr, sess

    # ---- streaming updates
    async def session_update(self, session_id: str, update: Any, **kw: Any) -> None:
        s, t = self.sess, getattr(update, "session_update", None)
        if t == "agent_message_chunk":
            s.emit({"type": "text", "text": _content_text(update.content)})
        elif t == "agent_thought_chunk":
            s.emit({"type": "thought", "text": _content_text(update.content)})
        elif t == "tool_call":
            s.emit({"type": "tool_call", "id": update.tool_call_id, "title": update.title, "kind": update.kind,
                    "status": update.status, "input": _clip(update.raw_input, 1500),
                    "locations": [getattr(x, "path", "") for x in (update.locations or [])][:8]})
        elif t == "tool_call_update":
            out = _content_text(update.content) or _clip(update.raw_output, 1500) or ""
            s.emit({"type": "tool_update", "id": update.tool_call_id, "status": update.status, "title": update.title,
                    "output": _clip(out, 3000)})
        elif t == "plan":
            s.emit({"type": "plan", "entries": [dict(content=e.content, status=e.status, priority=getattr(e, "priority", None))
                                                for e in update.entries]})
        elif t == "usage_update":
            cost = getattr(update, "cost", None)
            s.last_usage_update = dict(used=update.used, size=update.size,
                                       cost=(cost.amount if cost else None), currency=(cost.currency if cost else None))
            s.emit({"type": "usage", **s.last_usage_update})
        elif t == "current_mode_update":
            if s.modes:
                s.modes["current"] = getattr(update, "current_mode_id", None)
            s.emit({"type": "mode", "mode": getattr(update, "current_mode_id", None)})

    # ---- permissions
    async def request_permission(self, session_id: str, tool_call: Any, options: list[Any], **kw: Any) -> S.RequestPermissionResponse:
        s = self.sess
        kind = getattr(tool_call, "kind", None)
        title = getattr(tool_call, "title", None) or "tool call"

        def pick(*kinds: str):
            for k in kinds:
                for o in options:
                    if o.kind == k:
                        return o
            return None

        def allowed(o: Any) -> S.RequestPermissionResponse:
            return S.RequestPermissionResponse(outcome=S.AllowedOutcome(option_id=o.option_id))

        if s.policy in ("reads_auto", "readonly") and kind in READ_KINDS and (o := pick("allow_once")):
            s.emit({"type": "auto", "decision": "allowed", "title": title, "kind": kind, "policy": s.policy})
            return allowed(o)
        if s.policy == "readonly":
            s.emit({"type": "auto", "decision": "rejected", "title": title, "kind": kind, "policy": "readonly"})
            o = pick("reject_once", "reject_always")
            return allowed(o) if o else S.RequestPermissionResponse(outcome=S.DeniedOutcome())

        rid = uuid.uuid4().hex[:12]
        ids = [o.option_id for o in options]
        res = await self.mgr._wait(rid, "permission", s, ids, {
            "type": "permission", "request_id": rid, "title": title, "kind": kind,
            "input": _clip(getattr(tool_call, "raw_input", None), 2000),
            "options": [dict(id=o.option_id, name=o.name, kind=o.kind) for o in options]})
        if res and res.get("option_id") in ids:
            s.emit({"type": "resolved", "request_id": rid, "decision": res["option_id"]})
            return S.RequestPermissionResponse(outcome=S.AllowedOutcome(option_id=res["option_id"]))
        s.emit({"type": "resolved", "request_id": rid, "decision": "cancelled"})
        return S.RequestPermissionResponse(outcome=S.DeniedOutcome())

    # ---- file system delegation (path-jailed to the workspace)
    async def read_text_file(self, session_id: str, path: str, line: int | None = None, limit: int | None = None, **kw: Any):
        try:
            text, _ = read_text(jail(self.sess.workspace, path))
        except ToolError as e:
            raise acp.RequestError(-32602, str(e))
        except FileNotFoundError:
            raise acp.RequestError.resource_not_found(path)
        if line or limit:
            lines = text.split("\n")
            start = max(1, line or 1) - 1
            text = "\n".join(lines[start:start + limit] if limit else lines[start:])
        return S.ReadTextFileResponse(content=text)

    async def write_text_file(self, session_id: str, path: str, content: str, **kw: Any):
        s = self.sess
        if s.policy == "readonly":
            raise acp.RequestError(-32602, "the workspace is read-only for this run")
        try:
            p = jail(s.workspace, path)
            nl = read_text(p)[1] if p.is_file() else "\n"
            write_text(p, content, nl)
        except ToolError as e:
            raise acp.RequestError(-32602, str(e))
        s.emit({"type": "fs_write", "path": p.relative_to(s.workspace.resolve()).as_posix(), "bytes": len(content)})
        return S.WriteTextFileResponse()

    # ---- Cursor extension methods (documented at cursor.com/docs/cli/acp)
    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        m, s = method.lstrip("_"), self.sess
        if m == "cursor/ask_question":
            rid = uuid.uuid4().hex[:12]
            res = await self.mgr._wait(rid, "question", s, [], {
                "type": "question", "request_id": rid, "title": params.get("title"), "questions": params.get("questions", [])})
            s.emit({"type": "resolved", "request_id": rid, "decision": "answered" if res and res.get("answers") else "skipped"})
            if res and res.get("answers"):
                return {"outcome": {"outcome": "answered", "answers": res["answers"]}}
            return {"outcome": {"outcome": "skipped" if res else "cancelled"}}
        if m == "cursor/create_plan":
            rid = uuid.uuid4().hex[:12]
            res = await self.mgr._wait(rid, "plan", s, [], {
                "type": "plan_approval", "request_id": rid, "name": params.get("name"), "overview": params.get("overview"),
                "plan": _clip(params.get("plan"), 8000), "todos": params.get("todos", [])})
            s.emit({"type": "resolved", "request_id": rid, "decision": ("accepted" if res and res.get("accepted") else "rejected") if res else "cancelled"})
            if res is None:
                return {"outcome": {"outcome": "cancelled"}}
            if res.get("accepted"):
                return {"outcome": {"outcome": "accepted"}}
            return {"outcome": {"outcome": "rejected", "reason": res.get("reason") or "rejected by user"}}
        raise acp.RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        self.sess.emit({"type": "ext", "method": method.lstrip("_"), "data": _clip(params, 2000)})


class AcpManager:
    def __init__(self, db: Database, store: AcpStore, workspace_for: Callable[[str | None], Path], tel: Any = None):
        self.db, self.store, self.workspace_for = db, store, workspace_for
        self.tel = tel          # Telemetry | None: OTel env for agents + a span per turn / tool call
        self.sessions: dict[str, AcpSession] = {}
        self.pending: dict[str, Pending] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._tlock = threading.Lock()

    # ------------------------------------------------------------ dedicated event loop
    # Agent subprocesses need an event loop that supports them (Windows: ProactorEventLoop). The host server's loop
    # can be a SelectorEventLoop (uvicorn --reload on Windows), where asyncio subprocesses raise NotImplementedError.
    # So all ACP work runs on its own loop in a daemon thread, and the public methods below bridge to it.
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._tlock:
            if self._loop is None or self._loop.is_closed():
                loop = asyncio.ProactorEventLoop() if os.name == "nt" else asyncio.new_event_loop()
                ready = threading.Event()

                def runner() -> None:
                    asyncio.set_event_loop(loop)
                    loop.call_soon(ready.set)
                    loop.run_forever()

                self._thread = threading.Thread(target=runner, name="acp-loop", daemon=True)
                self._thread.start()
                ready.wait(5)
                self._loop = loop
            return self._loop

    def _run(self, coro: Any):
        fut = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        return asyncio.wrap_future(fut)       # awaitable in the CALLER's loop; cancelling it cancels the ACP-side task

    def stop(self) -> None:
        with self._tlock:
            loop, th = self._loop, self._thread
            self._loop = self._thread = None
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
            if th:
                th.join(5)
            with contextlib.suppress(Exception):
                loop.close()

    async def get_or_open(self, agent_id: str, session_id: str | None, workspace: str | None) -> AcpSession:
        return await self._run(self._get_or_open(agent_id, session_id, workspace))

    async def set_mode(self, sess: AcpSession, mode_id: str) -> None:
        return await self._run(self._set_mode(sess, mode_id))

    async def prompt(self, sess: AcpSession, text: str, emit: Callable[[dict], None], policy: str = "ask",
                     timeout: float = DEFAULT_TURN_TIMEOUT_S) -> dict:
        caller = asyncio.get_running_loop()

        def safe_emit(ev: dict) -> None:          # events are produced on the ACP thread, delivered on the caller's loop, in order
            with contextlib.suppress(RuntimeError):
                caller.call_soon_threadsafe(emit, ev)

        return await self._run(self._prompt(sess, text, safe_emit, policy, timeout))

    async def cancel(self, sess: AcpSession) -> None:
        return await self._run(self._cancel(sess))

    async def close(self, session_id: str) -> bool:
        return await self._run(self._close(session_id))

    async def close_all(self) -> None:
        if self._loop is not None:
            await self._run(self._close_all())

    async def probe(self, agent_id: str) -> dict:
        return await self._run(self._probe(agent_id))

    # ------------------------------------------------------------ interactions (permission / question / plan)
    async def _wait(self, rid: str, kind: str, sess: AcpSession, options: list[str], event: dict) -> dict | None:
        """Register the pending request BEFORE telling the UI about it (the answer can arrive on another thread
        immediately), then wait. None = cancelled / timed out (= denied)."""
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[rid] = Pending(rid, kind, sess.id, fut, options)
        try:
            sess.emit(event)
            return await asyncio.wait_for(fut, INTERACT_TIMEOUT_S)
        except asyncio.TimeoutError:
            return None
        finally:
            self.pending.pop(rid, None)

    def respond(self, rid: str, body: dict) -> dict:
        p = self.pending.get(rid)
        if not p:
            raise AcpError("no such pending request (it was already answered, cancelled or timed out)")
        if body.get("cancel"):
            val: dict | None = None
        elif p.kind == "permission":
            if body.get("option_id") not in p.options:
                raise AcpError(f"option_id must be one of {p.options}")
            val = {"option_id": body["option_id"]}
        elif p.kind == "question":
            val = {"answers": [] if body.get("skip") else (body.get("answers") or [])}
        else:
            val = {"accepted": bool(body.get("accepted")), "reason": body.get("reason")}

        def settle() -> None:
            if not p.fut.done():
                p.fut.set_result(val)

        loop = self._loop
        if loop is None or loop.is_closed():
            raise AcpError("the agent runtime is not running")
        loop.call_soon_threadsafe(settle)
        return {"ok": True}

    def _cancel_pending(self, sid: str) -> None:
        for p in list(self.pending.values()):
            if p.session_id == sid and not p.fut.done():
                p.fut.set_result(None)

    # ------------------------------------------------------------ lifecycle
    @staticmethod
    async def _drain(stream: Any, buf: deque) -> None:
        with contextlib.suppress(Exception):
            while stream is not None:
                line = await stream.readline()
                if not line:
                    return
                buf.append(line.decode("utf-8", "replace").rstrip()[:400])

    async def _get_or_open(self, agent_id: str, session_id: str | None, workspace: str | None) -> AcpSession:
        if session_id:
            s = self.sessions.get(session_id)
            if not s or s.agent["id"] != agent_id:
                raise AcpError("unknown or expired session (start a new one)")
            if s.kind == "acp" and s.proc is not None and s.proc.returncode is not None:
                await self._close(session_id)
                raise AcpError(f"the agent process exited (code {s.proc.returncode}). {self._tail(s)}")
            return s
        return await self._open(agent_id, workspace)

    @staticmethod
    def _tail(s: AcpSession) -> str:
        return ("stderr: " + " | ".join(list(s.stderr)[-6:])) if s.stderr else ""

    async def _open(self, agent_id: str, workspace: str | None = None, ws_path: Path | None = None) -> AcpSession:
        cfg = self.store.get(agent_id)
        if not cfg["enabled"]:
            raise AcpError(f"{cfg['label']} is disabled in Settings")
        if not cfg["installed"]:
            raise AcpError(f"'{cfg['command']}' was not found on PATH. {cfg['notes']}")
        await self._reap_idle()
        if len(self.sessions) >= MAX_LIVE_SESSIONS:
            raise AcpError(f"too many live agent sessions ({MAX_LIVE_SESSIONS}); close one first")
        ws = ws_path or self.workspace_for(workspace)
        ws.mkdir(parents=True, exist_ok=True)
        sess = AcpSession(uuid.uuid4().hex[:12], cfg, ws)
        if sess.kind == "cli":                      # nothing to spawn yet: each prompt runs its own process
            sess.info = dict(name=cfg["label"], title=cfg["label"], version=None, protocol="n/a (headless CLI adapter)", auth_methods=[], capabilities={})
            self.sessions[sess.id] = sess
            return sess
        client = _Client(self, sess)
        try:
            sess.conn, sess.proc = await sess.stack.enter_async_context(acp.spawn_agent_process(
                client, cfg["resolved_path"], *cfg["args"],
                env={**(self.tel.agent_env(agent_id, sess.id) if self.tel else {}), **self.store.env_values(agent_id)}, cwd=str(ws),
                transport_kwargs={"limit": 16 << 20}))
            sess.stderr_task = asyncio.create_task(self._drain(sess.proc.stderr, sess.stderr))
            init = await asyncio.wait_for(sess.conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=S.ClientCapabilities(fs=S.FileSystemCapabilities(read_text_file=True, write_text_file=True), terminal=False),
                client_info=S.Implementation(name="agent-studio", title="Agent Studio", version=CLIENT_VERSION)), 90)
            ai = init.agent_info
            sess.info = dict(name=getattr(ai, "name", None), title=getattr(ai, "title", None), version=getattr(ai, "version", None),
                             protocol=init.protocol_version, auth_methods=[dict(id=m.id, name=m.name) for m in (init.auth_methods or [])],
                             capabilities=_clip(init.agent_capabilities.model_dump(by_alias=True, exclude_none=True) if init.agent_capabilities else {}, 1500))

            async def new() -> Any:
                return await asyncio.wait_for(sess.conn.new_session(cwd=str(ws), mcp_servers=[]), 90)

            try:
                ns = await new()
            except acp.RequestError as e:
                methods = init.auth_methods or []
                if e.code != -32000 or not methods:
                    raise
                ids = [m.id for m in methods]
                keyed = [i for i in ids if re.search(r"api[-_]?key", i, re.I)]
                # a key stored for this agent -> use its API-key method; else Cursor's login; else the agent's first method
                has_key = any(v and "KEY" in k.upper() for k, v in self.store.env_values(agent_id).items())
                mid = keyed[0] if keyed and has_key else (
                    "cursor_login" if "cursor_login" in ids else ids[0])
                try:
                    await asyncio.wait_for(sess.conn.authenticate(method_id=mid), 60)
                    ns = await new()
                except acp.RequestError as e2:
                    raise AcpError(f"{cfg['label']} needs you to sign in first ({e2}). Run its login command in a terminal, then retry. {cfg['notes']}")
            sess.acp_id = ns.session_id
            if ns.modes:
                sess.modes = dict(current=ns.modes.current_mode_id,
                                  available=[dict(id=m.id, name=m.name, description=m.description) for m in ns.modes.available_modes])
        except BaseException as e:
            await self._close_session(sess)
            if isinstance(e, AcpError):
                raise
            if isinstance(e, asyncio.CancelledError):
                raise
            detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            raise AcpError(f"could not start {cfg['label']}: {detail}. {self._tail(sess)}".strip())
        self.sessions[sess.id] = sess
        return sess

    async def _set_mode(self, sess: AcpSession, mode_id: str) -> None:
        if not sess.modes or mode_id not in [m["id"] for m in sess.modes["available"]]:
            raise AcpError(f"mode '{mode_id}' is not offered by this agent")
        if mode_id != sess.modes["current"]:
            await asyncio.wait_for(sess.conn.set_session_mode(session_id=sess.acp_id, mode_id=mode_id), 30)
            sess.modes["current"] = mode_id

    async def _prompt(self, sess: AcpSession, text: str, emit: Callable[[dict], None], policy: str = "ask",
                     timeout: float = DEFAULT_TURN_TIMEOUT_S) -> dict:
        cfg = self.store.get(sess.agent["id"])
        if sess.lock.locked():
            raise AcpError("this session is still busy with another prompt")
        if policy not in ("ask", "reads_auto", "readonly"):
            raise AcpError("policy must be ask | reads_auto | readonly")
        async with sess.lock:
            self.store.check_limits(sess.agent["id"])
            if sess.turns >= cfg["max_turns"]:
                raise AcpError(f"turn limit reached ({cfg['max_turns']}) for this session - start a new session (limit is editable in Settings)")
            if sess.kind == "acp" and sess.proc.returncode is not None:
                raise AcpError(f"the agent process exited (code {sess.proc.returncode}). {self._tail(sess)}")
            tel, trace_id, root_span = self.tel, uuid.uuid4().hex, uuid.uuid4().hex[:16]
            tool_t0: dict[str, tuple[float, str]] = {}
            user_emit = emit

            def emit(ev: dict) -> None:         # noqa: F811 - wraps the caller's sink to record a span per tool call
                if tel and ev.get("type") == "tool_call":
                    tool_t0[ev["id"]] = (time.time(), str(ev.get("title") or ev.get("kind") or "tool"))
                elif tel and ev.get("type") == "tool_update" and ev.get("status") in ("completed", "failed") and ev.get("id") in tool_t0:
                    st, title = tool_t0.pop(ev["id"])
                    tel.span(sess.agent["id"], trace_id, uuid.uuid4().hex[:16], root_span, f"tool.{title}"[:80], st, time.time(),
                             {"tool_call_id": ev["id"], "status": ev["status"]}, "error" if ev["status"] == "failed" else "ok")
                user_emit(ev)

            sess.emit, sess.policy, sess.last_usage_update = emit, policy, None
            t0 = time.time()
            stop = "error"
            try:
                resp = await asyncio.wait_for(self._turn(sess, text, policy), timeout)
                stop = str(resp.stop_reason)
            except asyncio.TimeoutError:
                await self._cancel(sess)
                raise AcpError(f"the turn exceeded {int(timeout)}s and was cancelled")
            except acp.RequestError as e:
                raise AcpError(f"agent error {e.code}: {e}")
            except CliError as e:
                raise AcpError(str(e))
            finally:
                sess.emit = _noop
                self._cancel_pending(sess.id)
                sess.last_used = time.time()
                if tel:
                    tel.span(sess.agent["id"], trace_id, root_span, None, f"acp.turn.{sess.agent['id']}", t0, time.time(),
                             {"agent": sess.agent["id"], "session": sess.id, "stop_reason": stop, "policy": policy, "turn": sess.turns + 1,
                              "prompt_chars": len(text), "billing": "external account - not capped by Agent Studio"},
                             "ok" if stop in ("end_turn", "max_tokens", "max_turn_requests") else "error")
            sess.turns += 1
            u = resp.usage
            lu = sess.last_usage_update or {}
            dur = time.time() - t0
            self.db.execute("INSERT INTO acp_usage(ts,agent,session,stop_reason,input_tokens,output_tokens,total_tokens,cost,cost_currency,duration_s) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (time.time(), sess.agent["id"], sess.id, resp.stop_reason, u.input_tokens if u else None,
                             u.output_tokens if u else None, u.total_tokens if u else None, lu.get("cost"), lu.get("currency"), dur))
            return dict(stop_reason=resp.stop_reason, turns=sess.turns, max_turns=cfg["max_turns"], seconds=round(dur, 2),
                        usage=(dict(input_tokens=u.input_tokens, output_tokens=u.output_tokens, total_tokens=u.total_tokens) if u else None),
                        reported_cost=lu.get("cost"), currency=lu.get("currency"), context=lu or None,
                        billing_note="External agent: not covered by the Agent Studio spend caps; usage shown is what the agent reported.")

    async def _turn(self, sess: AcpSession, text: str, policy: str) -> Any:
        """One model turn: ACP prompt, or (cli harnesses) one headless process. Returns an object with .stop_reason/.usage."""
        if sess.kind == "acp":
            return await sess.conn.prompt(session_id=sess.acp_id, prompt=[S.TextContentBlock(text=text)])
        ok = await self._approve_unattended(sess, policy)
        if not ok:
            sess.emit({"type": "text", "text": "The unattended run was not approved, so nothing was executed."})
            return SimpleNamespace(stop_reason="cancelled", usage=None)
        res = await cli_agents.run_cli(sess.agent["id"], sess.agent, self.store.env_values(sess.agent["id"]), sess.workspace, text, policy,
                                       sess.emit, sess)
        if res.cost is not None:
            sess.last_usage_update = dict(used=(res.usage or {}).get("total_tokens"), size=None, cost=res.cost, currency="USD")
        if res.truncated:
            sess.emit({"type": "note", "text": f"{res.truncated} characters of output were not kept (cap)."})
        u = res.usage
        return SimpleNamespace(stop_reason=res.stop_reason, usage=SimpleNamespace(**u) if u else None)

    async def _approve_unattended(self, sess: AcpSession, policy: str) -> bool:
        """CLI harnesses have no per-action permission protocol. 'readonly' needs the harness' own read-only mode (run_cli
        refuses otherwise); 'ask'/'reads_auto' ask the user ONCE for the whole unattended run (unanswered = denied)."""
        if policy == "readonly":
            return True
        rid = uuid.uuid4().hex[:12]
        what = cli_agents.SPECS[sess.agent["id"]].unattended
        res = await self._wait(rid, "permission", sess, ["allow", "reject"], {
            "type": "permission", "request_id": rid, "title": f"Run {sess.agent['label']} unattended in {sess.workspace.as_posix()}", "kind": "execute",
            "input": what, "options": [dict(id="allow", name="Allow this run", kind="allow_once"), dict(id="reject", name="Reject", kind="reject_once")]})
        decision = res["option_id"] if res else "cancelled"
        sess.emit({"type": "resolved", "request_id": rid, "decision": decision})
        return decision == "allow"

    async def _cancel(self, sess: AcpSession) -> None:
        self._cancel_pending(sess.id)
        if sess.kind == "cli":
            sess.cancel_requested = True
            if sess.proc is not None:
                cli_agents.kill_tree(sess.proc)
            return
        with contextlib.suppress(Exception):
            await asyncio.wait_for(sess.conn.cancel(session_id=sess.acp_id), 10)

    async def _close_session(self, sess: AcpSession) -> None:
        if sess.closed:
            return
        sess.closed = True
        self._cancel_pending(sess.id)
        if sess.kind == "cli":
            cli_agents.kill_tree(sess.proc)
            return
        pid = getattr(sess.proc, "pid", None)
        if os.name == "nt" and pid and sess.proc.returncode is None:   # npx/cmd wrappers leave grandchildren: kill the tree
            with contextlib.suppress(Exception):
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(sess.stack.aclose(), 15)
        if sess.stderr_task:
            sess.stderr_task.cancel()
        # Close the pipe transports explicitly (Windows proactor loop otherwise warns when they are GC'd late).
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(sess.proc.wait(), 5)
            tr = getattr(sess.proc, "_transport", None)
            if tr is not None:
                tr.close()
            await asyncio.sleep(0.05)

    async def _close(self, session_id: str) -> bool:
        s = self.sessions.pop(session_id, None)
        if s:
            await self._close_session(s)
        return s is not None

    async def _reap_idle(self) -> None:
        now = time.time()
        for sid, s in list(self.sessions.items()):
            if not s.lock.locked() and now - s.last_used > IDLE_REAP_S:
                await self._close(sid)

    async def _close_all(self) -> None:
        for sid in list(self.sessions):
            await self._close(sid)

    async def _probe(self, agent_id: str) -> dict:
        """Start the agent, initialize + open a session (verifies install/login), report what it advertises, close."""
        ws = data_dir() / "workspaces" / "acp-probe"
        cfg = self.store.get(agent_id)
        if cfg["kind"] == "cli":                   # no ACP handshake to do: the version check is the whole (free) health check
            if not cfg["installed"]:
                raise AcpError(f"'{cfg['command']}' was not found on PATH. {cfg['notes']}")
            ws.mkdir(parents=True, exist_ok=True)
            try:
                v = await self.store.version(agent_id, ws)
            except CliError as e:
                raise AcpError(f"{cfg['label']} did not pass its version check: {e}")
            return dict(ok=True, name=cfg["label"], title=cfg["label"], version=v, protocol="n/a (headless CLI adapter)", auth_methods=[], modes=None)
        sess = await self._open(agent_id, ws_path=ws)
        try:
            try:
                cli_version = await self.store.version(agent_id, ws)
            except CliError:
                cli_version = None
            return dict(ok=True, **sess.info, modes=sess.modes, cli_version=cli_version)
        finally:
            await self._close_session(sess)
            self.sessions.pop(sess.id, None)
