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
import shutil
import sys
from dataclasses import dataclass

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
    dict(id="github", label="GitHub", default_on=0, transport="http",
         description="GitHub repos/issues/PRs via GitHub's official remote MCP server. One-click: paste a fine-grained token.",
         url="https://api.githubcopilot.com/mcp/", requires_env=["GITHUB_PERSONAL_ACCESS_TOKEN"]),
    dict(id="playwright", label="Playwright", default_on=0, transport="stdio",
         description="Browser automation (downloads a browser on first use).",
         command="npx", args=["-y", "@playwright/mcp@0.0.86", "--headless"]),
    dict(id="brave", label="Brave Search", default_on=0, transport="stdio",
         description="Web search (needs a Brave Search API key).",
         command="npx", args=["-y", "@brave/brave-search-mcp-server@2.2.0"], requires_env=["BRAVE_API_KEY"]),
    dict(id="context7", label="Context7", default_on=0, transport="stdio",
         description="Up-to-date library documentation lookup.",
         command="npx", args=["-y", "@upstash/context7-mcp@4.7.3"]),
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

    def ready(self) -> bool:
        return self.enabled and self.status == "active" and not self.missing_env()


class McpStore:
    def __init__(self, db: Database):
        self.db = db
        for c in CATALOG:
            if self.db.one("SELECT 1 FROM mcp_servers WHERE id=?", (c["id"],)):
                continue
            self.db.execute(
                "INSERT INTO mcp_servers(id,label,description,transport,command,args,url,env,requires_env,enabled,default_on,builtin)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,1)",
                (c["id"], c["label"], c["description"], c["transport"], c.get("command"), json.dumps(c.get("args", [])),
                 c.get("url"), json.dumps(c.get("env", {})), json.dumps(c.get("requires_env", [])),
                 c["default_on"], c["default_on"]))

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


def _subst(v: str, workspace: str) -> str:
    return v.replace("{workspace}", workspace).replace("{data}", str(data_dir()))


async def build_toolsets(store: McpStore, ids: list[str], workspace: str, warnings: list[str]) -> list:
    """Create ADK McpToolsets for READY servers; probe each (connect + list tools) and skip failures with a warning."""
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
            why = "pending approval" if s.status == "pending_approval" else ("disabled" if not s.enabled else f"missing {s.missing_env()}")
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
                params = StreamableHTTPConnectionParams(url=s.url, headers=headers, timeout=30)
            else:
                env = {k: _subst(str(v), workspace) for k, v in {**{"PATH": __import__("os").environ.get("PATH", "")}, **s.env}.items()}
                sp = StdioServerParameters(command=_cmd(s.command or ""), args=[_subst(a, workspace) for a in s.args], env=env)
                params = StdioConnectionParams(server_params=sp, timeout=120)
            ts = McpToolset(connection_params=params, tool_name_prefix=f"{s.id}_")
            await ts.get_tools()  # probe: fails fast if the server cannot start
            out.append(ts)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"MCP '{sid}' failed to start: {type(e).__name__}: {str(e)[:200]}")
            try:
                await ts.close()  # type: ignore[name-defined]
            except Exception:
                pass
    return out
