"""Curated MCP servers + store + ADK toolset builder.

Spec target: MCP 2026-07-28 (stateless; Streamable HTTP, not HTTP+SSE). Versions are PINNED
(checked on npm/PyPI 2026-11-20). NOTE: the old npm @modelcontextprotocol/server-github and
server-brave-search are archived/deprecated, so GitHub uses GitHub's own remote server and Brave
uses Brave's official package.

Default-on servers (filesystem, fetch, git, memory) start automatically when an agent that attaches
them runs. They execute on the HOST (not in the Docker sandbox) - filesystem/git are scoped to the
workspace directory; third-party code is fetched via npx/uvx at the pinned versions below.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import shutil
import sys
from dataclasses import dataclass

from . import d1_spec, trainer as trainer_mod, ts_spec
from .db import Database, data_dir

CATALOG: list[dict] = [
    dict(id="filesystem", label="Filesystem", default_on=1, transport="stdio",
         description="Read/write files in the workspace (scoped to the workspace directory).",
         command="npx", args=["-y", "@modelcontextprotocol/server-filesystem@2.4.0", "{workspace}"]),
    dict(id="fetch", label="Fetch", default_on=1, transport="stdio",
         description="Fetch a URL and convert it to markdown for reading.",
         command="uvx", args=["mcp-server-fetch==2026.11.18"]),
    dict(id="git", label="Git", default_on=1, transport="stdio",
         description="Git status/diff/log/commit on the workspace repository.",
         command="uvx", args=["mcp-server-git==2026.11.18", "--repository", "{workspace}"]),
    dict(id="memory", label="Memory", default_on=1, transport="stdio",
         description="Persistent knowledge-graph memory shared across chats.",
         command="npx", args=["-y", "@modelcontextprotocol/server-memory@2.2.1"], env={"MEMORY_FILE_PATH": "{data}/memory.json"}),
    dict(id="elevenlabs", label="ElevenLabs", default_on=0, transport="stdio",
         description="ElevenLabs' official MCP server: text-to-speech, voice search/design, sound effects, transcription. "
                     "Spends your ElevenLabs credits (not covered by Agent Studio caps); audio files are written to the workspace.",
         command="uvx", args=["elevenlabs-mcp==0.12.2"], env={"ELEVENLABS_MCP_BASE_PATH": "{workspace}"}, requires_env=["ELEVENLABS_API_KEY"]),
    dict(id="github", label="GitHub", default_on=0, transport="http",
         description="GitHub repos/issues/PRs via GitHub's official remote MCP server. One-click: paste a fine-grained token.",
         url="https://api.githubcopilot.com/mcp/", requires_env=["GITHUB_PERSONAL_ACCESS_TOKEN"]),
    dict(id="playwright", label="Playwright", default_on=0, transport="stdio",
         description="Browser automation (downloads a browser on first use).",
         command="npx", args=["-y", "@playwright/mcp@0.0.84", "--headless"]),
    dict(id="brave", label="Brave Search", default_on=0, transport="stdio",
         description="Web search (needs a Brave Search API key).",
         command="npx", args=["-y", "@brave/brave-search-mcp-server@2.2.0"], requires_env=["BRAVE_API_KEY"]),
    dict(id="code-sandbox", label="Code sandbox (Docker)", default_on=0, transport="stdio",
         description="Run code, tests and shell commands in an isolated Docker container on the workspace: no network by default, "
                     "no privileges, memory/CPU/process limits. Tools: run_command, run_python, sandbox_status. Needs Docker running.",
         command="{python}", args=["-m", "app.mcp_sandbox", "{workspace}"], env={"PYTHONPATH": "{backend}"}),
    dict(id="d1", label="Decision model (d1-2B, local)", default_on=0, transport="stdio", enabled=0,
         description="Liquid AI's d1-2B decision model running LOCALLY (not a chat model): answers routing, triage, moderation, "
                     "classification, extraction and judging questions with calibrated probabilities in one forward pass. Tools: decide, "
                     "classify, yes_no, extract, score, status. Needs a one-time Install (about 6 GB, isolated environment); "
                     "licence: LFM Open License v1.0 (free commercial use only below US$10M annual revenue).",
         command="{d1_python}", args=["-m", "app.mcp_d1"], env={"PYTHONPATH": "{backend}", "D1_HOME": "{d1_home}", "D1_MODEL": "{d1_model}"}),
    dict(id="timeseries", label="Time-series forecasting (local models)", default_on=0, transport="stdio", enabled=0,
         description="Zero-shot forecasting with open foundation models running LOCALLY (Chronos-2, TimesFM 3, Toto 2, Timestral, ci-proto; "
                     "Kronos for candlesticks). Pick one model, several or all and compare their forecasts and backtest scores. Tools: "
                     "list_models, forecast, forecast_file, compare, forecast_ohlcv, status. Each model is installed separately "
                     "(20 MB to 1.3 GB, isolated environment); licences differ - TimesFM is non-commercial, Timestral is limited above "
                     "US$20M monthly revenue.",
         command="{ts_python}", args=["-m", "app.mcp_ts"], env={"PYTHONPATH": "{backend}", "TS_HOME": "{ts_home}", "TS_WORKSPACE": "{workspace}"}),
    dict(id="trainer", label="Model trainer (Ludwig / PyTorch / Keras)", default_on=0, transport="stdio", enabled=0,
         description="Train and evaluate machine-learning models on a CSV in the workspace with Ludwig, PyTorch or Keras (TensorFlow). Runs "
                     "LOCALLY in isolated environments you install one by one (Catalog > ML tools: sizes and licences are shown first). "
                     "Training runs as a background job: tools status, templates, train, job, jobs, cancel, evaluate.",
         command="{python}", args=["-m", "app.mcp_trainer"], env={"PYTHONPATH": "{backend}", "TRAINER_WORKSPACE": "{workspace}", "TRAINER_HOME": "{trainer_home}", "TRAINER_JOBS": "{trainer_jobs}"}),
    dict(id="cloudml", label="Cloud ML (SageMaker, Vertex, Azure ML, DigitalOcean, RunPod, OCI)", default_on=0, transport="stdio", enabled=0,
         description="Look at models, endpoints, training jobs and compute in your cloud accounts: AWS SageMaker, Google Vertex AI, Azure ML, "
                     "DigitalOcean, RunPod and Oracle OCI. READ-ONLY by default; creating or deleting resources costs money, is switched off "
                     "until you allow it (Catalog > ML tools > Cloud accounts) and always needs a confirm. Tools: providers, list_resources, "
                     "describe, deploy, delete.",
         command="{python}", args=["-m", "app.mcp_cloudml"], env={"PYTHONPATH": "{backend}"}),
    dict(id="semgrep", label="Semgrep (security scan)", default_on=0, transport="stdio",
         description="Static analysis for security bugs and code-quality issues (OWASP-style rules, 30+ languages). "
                     "Scans code or files in the workspace; no account needed for the open rules.",
         command="uvx", args=["--from", "semgrep==1.186.0", "semgrep", "mcp", "-t", "stdio"], enabled=1),   # built-in MCP mode (semgrep-mcp pkg is broken)
    dict(id="eslint", label="ESLint (JS/TS quality)", default_on=0, transport="stdio",
         description="Lint JavaScript/TypeScript files with the project's own ESLint config (needs eslint in the project).",
         command="npx", args=["-y", "@eslint/mcp@0.3.17"], enabled=1),
    dict(id="snyk", label="Snyk (dependency & code security)", default_on=0, transport="stdio",
         description="Snyk's MCP server: open-source dependency vulnerabilities (SCA), code scanning (SAST), IaC and container checks. "
                     "Needs a Snyk API token (free tier available).",
         command="npx", args=["-y", "snyk@1.1309.0", "mcp", "-t", "stdio"], requires_env=["SNYK_TOKEN"]),
    dict(id="context7", label="Context7", default_on=0, transport="stdio",
         description="Up-to-date library documentation lookup.",
         command="npx", args=["-y", "@upstash/context7-mcp@4.5.1"]),
]


@dataclass
class McpServer:
    id: str
    label: str
    description: str
    transport: str
    command: str | None
    args: list[str]
    url: str | None
    env: dict
    requires_env: list[str]
    enabled: bool
    default_on: bool
    status: str
    builtin: bool

    @classmethod
    def from_row(cls, r) -> "McpServer":
        return cls(id=r["id"], label=r["label"], description=r["description"], transport=r["transport"], command=r["command"],
                   args=json.loads(r["args"]), url=r["url"], env=json.loads(r["env"]), requires_env=json.loads(r["requires_env"]),
                   enabled=bool(r["enabled"]), default_on=bool(r["default_on"]), status=r["status"], builtin=bool(r["builtin"]))

    def missing_env(self) -> list[str]:
        return [k for k in self.requires_env if not self.env.get(k)]

    def public(self) -> dict:
        return dict(id=self.id, label=self.label, description=self.description, transport=self.transport,
                    command=self.command, args=self.args, url=self.url, requires_env=self.requires_env,
                    env_set={k: bool(self.env.get(k)) for k in self.requires_env}, enabled=self.enabled,
                    default_on=self.default_on, status=self.status, builtin=self.builtin, ready=self.ready())

    def setup_missing(self) -> bool:
        """A server that needs a one-time user-triggered install (the local decision model) which has not completed."""
        if self.id == ts_spec.MCP_ID:
            return not ts_spec.any_ready()
        if self.id == "trainer":
            return not trainer_mod.ready_frameworks()
        return self.id == d1_spec.MCP_ID and not d1_spec.ready_models()

    def ready(self) -> bool:
        return self.enabled and self.status == "active" and not self.missing_env() and not self.setup_missing()


BROWSERS = ("chromium", "chrome", "msedge", "firefox", "webkit")
PLAYWRIGHT_PKG = "@playwright/mcp@0.0.84"
DEFAULT_BROWSER = {"browser": "chromium", "headless": True, "isolated": True, "allowed_origins": [], "blocked_origins": [],
                   "viewport": "1280x720"}


def playwright_args(cfg: dict) -> list[str]:
    """Launch arguments for the Playwright browser MCP from the user's browser settings."""
    c = {**DEFAULT_BROWSER, **(cfg or {})}
    args = ["-y", PLAYWRIGHT_PKG, "--browser", c["browser"] if c["browser"] in BROWSERS else "chromium"]
    if c["headless"]:
        args.append("--headless")
    if c["isolated"]:
        args.append("--isolated")
    if c.get("viewport") and "x" in str(c["viewport"]):
        args += ["--viewport-size", str(c["viewport"])]
    if c.get("allowed_origins"):
        args += ["--allowed-origins", ";".join(c["allowed_origins"])]
    if c.get("blocked_origins"):
        args += ["--blocked-origins", ";".join(c["blocked_origins"])]
    return args


class McpStore:
    def __init__(self, db: Database):
        self.db = db
        for c in CATALOG:
            row = self.db.one("SELECT env, builtin FROM mcp_servers WHERE id=?", (c["id"],))
            if row:
                if row["builtin"]:   # keep built-ins current (pinned versions, fixes) - but never touch stored secrets or on/off
                    env = {**c.get("env", {}), **json.loads(row["env"] or "{}")}
                    args = c.get("args", [])
                    if c["id"] == "playwright":     # the browser settings the user saved are kept
                        saved = self.db.one("SELECT value FROM settings WHERE key='browser_settings'")
                        args = playwright_args(json.loads(saved["value"]) if saved else {})
                    self.db.execute("UPDATE mcp_servers SET label=?, description=?, transport=?, command=?, args=?, url=?, requires_env=?, env=? WHERE id=?",
                                    (c["label"], c["description"], c["transport"], c.get("command"), json.dumps(args), c.get("url"),
                                     json.dumps(c.get("requires_env", [])), json.dumps(env), c["id"]))
                continue
            self.db.execute(
                "INSERT INTO mcp_servers(id,label,description,transport,command,args,url,env,requires_env,enabled,default_on,builtin)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,1)",
                (c["id"], c["label"], c["description"], c["transport"], c.get("command"), json.dumps(c.get("args", [])),
                 c.get("url"), json.dumps(c.get("env", {})), json.dumps(c.get("requires_env", [])),
                 c.get("enabled", c["default_on"]), c["default_on"]))

    def list(self) -> list[McpServer]:
        return [McpServer.from_row(r) for r in self.db.query("SELECT * FROM mcp_servers ORDER BY builtin DESC, rowid")]

    def get(self, sid: str) -> McpServer | None:
        r = self.db.one("SELECT * FROM mcp_servers WHERE id=?", (sid,))
        return McpServer.from_row(r) if r else None

    def update(self, sid: str, enabled: bool | None = None, env: dict | None = None) -> McpServer:
        s = self.get(sid)
        if not s:
            raise KeyError(sid)
        if env is not None:
            merged = dict(s.env)
            for k, v in env.items():
                if v == "":
                    merged.pop(k, None)
                else:
                    merged[k] = v
            self.db.execute("UPDATE mcp_servers SET env=? WHERE id=?", (json.dumps(merged), sid))
        if enabled is not None:
            if enabled and s.status == "pending_approval":  # explicit human approval of an agent-proposed server
                self.db.execute("UPDATE mcp_servers SET status='active' WHERE id=?", (sid,))
            self.db.execute("UPDATE mcp_servers SET enabled=? WHERE id=?", (1 if enabled else 0, sid))
        return self.get(sid)  # type: ignore[return-value]

    def propose(self, sid: str, label: str, description: str, command: str, args: list[str]) -> McpServer:
        """Agent-proposed server: stored DISABLED + pending_approval. A human must enable it in Settings."""
        import re
        if not re.match(r"^[a-z0-9][a-z0-9_-]{1,30}$", sid):
            raise ValueError("id must be lowercase letters/digits/-/_")
        if self.get(sid):
            raise ValueError(f"MCP server '{sid}' already exists")
        self.db.execute(
            "INSERT INTO mcp_servers(id,label,description,transport,command,args,env,requires_env,enabled,default_on,status,builtin)"
            " VALUES(?,?,?,?,?,?,?,?,0,0,'pending_approval',0)",
            (sid, label, description, "stdio", command, json.dumps(args), "{}", "[]"))
        return self.get(sid)  # type: ignore[return-value]

    def add(self, sid: str, label: str, description: str, transport: str, command: str | None, args: list[str],
            url: str | None, env: dict[str, str], enabled: bool = True) -> McpServer:
        """A server the USER adds in the UI (active immediately; agent-proposed ones go through propose())."""
        import re
        if not re.match(r"^[a-z0-9][a-z0-9_-]{1,30}$", sid):
            raise ValueError("id must be 2-31 chars: lowercase letters, digits, - or _")
        if self.get(sid):
            raise ValueError(f"MCP server '{sid}' already exists")
        if transport not in ("stdio", "http"):
            raise ValueError("transport must be stdio or http")
        if transport == "stdio" and not (command or "").strip():
            raise ValueError("a stdio server needs a command (e.g. npx, uvx, node, python)")
        if transport == "http" and not (url or "").startswith(("http://", "https://")):
            raise ValueError("an http server needs an http(s) URL")
        env = {str(k).strip(): str(v) for k, v in (env or {}).items() if str(k).strip()}
        self.db.execute(
            "INSERT INTO mcp_servers(id,label,description,transport,command,args,url,env,requires_env,enabled,default_on,status,builtin)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,0,'active',0)",
            (sid, label.strip() or sid, description.strip(), transport, (command or "").strip() or None, json.dumps(args or []),
             (url or "").strip() or None, json.dumps(env), json.dumps(sorted(env)), 1 if enabled else 0))
        return self.get(sid)  # type: ignore[return-value]

    def propose_openapi(self, sid: str, label: str, description: str, args: list[str], requires_env: list[str]) -> McpServer:
        """Agent-proposed OpenAPI server: stored DISABLED + pending_approval; a human reviews it before it runs."""
        import re
        if not re.match(r"^[a-z0-9][a-z0-9_-]{1,30}$", sid):
            raise ValueError("id must be lowercase letters/digits/-/_")
        if self.get(sid):
            raise ValueError(f"MCP server '{sid}' already exists")
        env = {"PYTHONPATH": "{backend}", "OPENAPI_ALLOW_WRITE": "0"}
        self.db.execute(
            "INSERT INTO mcp_servers(id,label,description,transport,command,args,env,requires_env,enabled,default_on,status,builtin)"
            " VALUES(?,?,?,?,?,?,?,?,0,0,'pending_approval',0)",
            (sid, label, description, "stdio", "{python}", json.dumps(["-m", "app.mcp_openapi", *args]), json.dumps(env), json.dumps(requires_env)))
        return self.get(sid)  # type: ignore[return-value]

    def set_browser_settings(self, cfg: dict) -> dict:
        """Save the browser settings and rebuild the Playwright server's launch arguments from them."""
        c = {**DEFAULT_BROWSER, **(cfg or {})}
        if c["browser"] not in BROWSERS:
            raise ValueError(f"browser must be one of {', '.join(BROWSERS)}")
        if c.get("viewport") and not re.match(r"^\d{3,4}x\d{3,4}$", str(c["viewport"])):
            raise ValueError("viewport must look like 1280x720")
        for k in ("allowed_origins", "blocked_origins"):
            c[k] = [o.strip() for o in (c.get(k) or []) if str(o).strip()]
            if any(" " in o or ";" in o for o in c[k]):
                raise ValueError("origins cannot contain spaces or ';'")
        self.db.execute("INSERT INTO settings(key,value) VALUES('browser_settings',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(c),))
        self.db.execute("UPDATE mcp_servers SET args=? WHERE id='playwright'", (json.dumps(playwright_args(c)),))
        return c

    def browser_settings(self) -> dict:
        r = self.db.one("SELECT value FROM settings WHERE key='browser_settings'")
        return {**DEFAULT_BROWSER, **(json.loads(r["value"]) if r else {})}

    def delete(self, sid: str) -> None:
        s = self.get(sid)
        if not s:
            raise KeyError(sid)
        if s.builtin:
            raise ValueError("built-in servers can be disabled but not deleted")
        self.db.execute("DELETE FROM mcp_servers WHERE id=?", (sid,))


def _cmd(command: str) -> str:
    """Resolve npx/uvx to a real executable (npx is npx.cmd on Windows)."""
    return shutil.which(command) or shutil.which(command + ".cmd") or command


LAUNCHERS = {"npx": "Node.js", "node": "Node.js", "npm": "Node.js", "uvx": "uv", "uv": "uv", "docker": "Docker"}
LAUNCHER_HELP = {"Node.js": "install Node.js 22 or newer from https://nodejs.org, then restart Agent Studio",
                 "uv": "install uv from https://docs.astral.sh/uv/, then restart Agent Studio",
                 "Docker": "install and start Docker Desktop"}


def missing_launcher(command: str) -> str | None:
    """A clear reason when a server's launcher (npx, uvx, docker) is not on this PC, instead of a FileNotFoundError."""
    name = Path(command).name.lower().removesuffix(".cmd").removesuffix(".exe")
    tool = LAUNCHERS.get(name)
    if tool is None or shutil.which(command) or shutil.which(command + ".cmd"):
        return None
    return f"needs {tool}, which is not installed on this PC - {LAUNCHER_HELP[tool]}"


def _subst(v: str, workspace: str) -> str:
    import sys as _sys
    backend = str(Path(__file__).resolve().parents[1])
    return (v.replace("{workspace}", workspace).replace("{data}", str(data_dir()))
             .replace("{python}", _sys.executable).replace("{ts_python}", str(ts_spec.env_python())).replace("{ts_home}", str(ts_spec.home())).replace("{d1_python}", str(d1_spec.env_python())).replace("{d1_home}", str(d1_spec.home())).replace("{d1_model}", d1_spec.preferred_model()).replace("{trainer_home}", str(trainer_mod.home()))
             .replace("{trainer_jobs}", str(trainer_mod.jobs_dir())).replace("{backend}", backend))


async def build_toolsets(store: McpStore, ids: list[str], workspace: str, warnings: list[str], identity_token=None) -> list:
    """Create ADK McpToolsets for READY servers; probe each (connect + list tools) and skip failures with a warning.
    `identity_token(url)` gives the calling agent's identity token for an HTTP server that opted in with AGENT_STUDIO_IDENTITY=1."""
    from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams, StreamableHTTPConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
    from mcp import StdioServerParameters

    out = []
    for sid in dict.fromkeys(ids):
        s = store.get(sid)
        if s is None:
            warnings.append(f"MCP '{sid}': unknown server")
            continue
        if not s.ready():
            why = ("pending approval" if s.status == "pending_approval" else "disabled" if not s.enabled
                   else ("not installed - open Catalog > ML tools and install a framework" if s.id == "trainer"
                         else "not installed - open MCP servers > Time-series forecasting and install a model" if s.id == ts_spec.MCP_ID
                         else "not installed - open MCP servers > Decision model and click Install") if s.setup_missing()
                   else f"missing {s.missing_env()}")
            warnings.append(f"MCP '{sid}' skipped: {why}")
            continue
        if s.id == "git" and not (__import__("pathlib").Path(workspace) / ".git").exists():
            warnings.append("MCP 'git' skipped: the workspace is not a git repository (run `git init` there)")
            continue
        try:
            if s.transport == "http":
                headers = {}
                tok = s.env.get("GITHUB_PERSONAL_ACCESS_TOKEN")
                if tok:
                    headers["Authorization"] = f"Bearer {tok}"
                for k, v in s.env.items():          # user-added http servers: HEADER_X_API_KEY=... -> "X-Api-Key: ..."
                    if k.upper().startswith("HEADER_") and v:
                        headers["-".join(w.capitalize() for w in k[7:].split("_"))] = v
                if identity_token is not None and s.env.get("AGENT_STUDIO_IDENTITY") == "1" and "Authorization" not in headers:
                    tok = identity_token(s.url)
                    if tok:
                        headers["Authorization"] = f"Bearer {tok}"
                    else:
                        warnings.append(f"MCP '{sid}': this agent's identity may not call MCP servers (no mcp:call scope), so no identity token was sent")
                params = StreamableHTTPConnectionParams(url=s.url, headers=headers, timeout=30)
            else:
                missing = missing_launcher(_subst(s.command or "", workspace))
                if missing:
                    warnings.append(f"MCP '{sid}' skipped: {missing}")
                    continue
                env = {k: _subst(str(v), workspace) for k, v in {**{"PATH": __import__("os").environ.get("PATH", "")}, **s.env}.items()}
                sp = StdioServerParameters(command=_cmd(_subst(s.command or "", workspace)), args=[_subst(a, workspace) for a in s.args], env=env)
                params = StdioConnectionParams(server_params=sp, timeout=120)
            ts = McpToolset(connection_params=params, tool_name_prefix=f"{s.id}_")
            await ts.get_tools()  # probe: fails fast if the server cannot start
            out.append(ts)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"MCP '{sid}' failed to start: {type(e).__name__}: {str(e)[:200]}")
            if getattr(store, "issues", None) is not None:          # the harness raises it; it is not left to be noticed
                store.issues.add("mcp_failed", sid, f"MCP server '{s.label}' failed to start",
                                 detail=f"{type(e).__name__}: {str(e)[:800]}\ncommand: {s.command} {' '.join(s.args or [])}",
                                 suggestion=f"Open MCP servers → '{s.label}' → Test to see the error. Check the command runs on this PC, "
                                            "that its version is pinned, and that required environment variables are set. Or ask an agent to debug it.",
                                 action={"type": "debug_mcp", "mcp": sid})
            try:
                await ts.close()  # type: ignore[name-defined]
            except Exception:
                pass
    return out
