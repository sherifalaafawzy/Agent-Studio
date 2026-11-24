"""Build the deploy plan for one agent: every file and command that WOULD be written, with diffs. Nothing here writes anything.

Two modes:
* export  - the agent becomes a Claude Code subagent file (`.claude/agents/<name>.md`), its skills are copied to `.claude/skills/`, and its MCP
            servers go to `.mcp.json` (project scope) or `claude mcp add-json ... --scope user` commands (user scope). A copy: it runs on Claude Code.
* live    - Claude Code calls the RUNNING Agent Studio agent through its MCP endpoint (`/agent-mcp/<agent>`, see mcp_endpoint.py). Agent Studio keeps
            running the agent, so guardrails, memory, spend caps and the run record all apply.

Secrets never travel: an MCP server's stored credentials become `${VAR}` placeholders the user sets in their own environment; text that looks like a
credential is masked; the live endpoint's API key is created in the app, shown once there, and read from the AGENT_STUDIO_KEY environment variable.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from ..aibom.redact import MASK, SECRET_NAME, SECRET_VALUE
from ..apiaccess.api import access_config
from ..skills import NAME_RE as SKILL_NAME_RE
from .mapping import feature_losses, translate_tools

SERVER_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_SUB_AGENTS = 12
PRELOAD_LIMIT = 8                      # more skills than this are copied but not preloaded (each preloaded skill is injected whole)
KEY_ENV = "AGENT_STUDIO_KEY"
MODES = ("export", "live")
SCOPES = ("project", "user")
PLACEMENTS = ("config", "agent")
DIFF_LIMIT = 20_000
LIVE_PATH = "agent-mcp"
FALLBACK_TOOLS = ["Read", "Glob", "Grep"]
AUTO = object()                        # "look for the claude program on PATH"


@dataclass
class Opts:
    mode: str = "export"
    scope: str = "project"
    project_path: str = ""
    include_sub_agents: bool = True
    include_skills: bool = True
    mcp_placement: str = "config"
    base_url: str = "http://127.0.0.1:8765"


def parse_opts(body: dict) -> Opts:
    """Check the options of a plan/apply request. Raises ValueError with a plain reason."""
    body = body or {}
    o = Opts()
    o.mode = body.get("mode", o.mode)
    o.scope = body.get("scope", o.scope)
    o.mcp_placement = body.get("mcp_placement", o.mcp_placement)
    if o.mode not in MODES:
        raise ValueError("mode must be 'export' (copy the agent into Claude Code) or 'live' (Claude Code calls the running agent)")
    if o.scope not in SCOPES:
        raise ValueError("scope must be 'project' (one folder) or 'user' (all your projects)")
    if o.mcp_placement not in PLACEMENTS:
        raise ValueError("mcp_placement must be 'config' or 'agent'")
    for k in ("include_sub_agents", "include_skills"):
        v = body.get(k, getattr(o, k))
        if not isinstance(v, bool):
            raise ValueError(f"{k} must be true or false")
        setattr(o, k, v)
    pp = body.get("project_path", "")
    if not isinstance(pp, str) or len(pp) > 1000 or "\x00" in pp:
        raise ValueError("project_path must be a folder path")
    o.project_path = pp.strip()
    base = body.get("base_url") or o.base_url
    sp = urlsplit(base) if isinstance(base, str) else None
    if sp is None or sp.scheme not in ("http", "https") or not sp.hostname or sp.username or sp.password or sp.query or sp.fragment or sp.path not in ("", "/"):
        raise ValueError("base_url must look like http://127.0.0.1:8765 (no path, login or query)")
    o.base_url = base.rstrip("/")
    return o


def claude_name(agent_id: str) -> str:
    return agent_id.replace("_", "-")


def resolve_root(opts: Opts, home: Path) -> Path:
    """The folder the `.claude` directory lives in: the chosen project, or the user's home (its `.claude` is the user scope)."""
    if opts.scope == "user":
        return home
    if not opts.project_path:
        raise ValueError("choose the project folder Claude Code should use")
    p = Path(opts.project_path)
    if not p.is_absolute():
        raise ValueError("the project folder must be a full path, such as C:\\work\\my-repo")
    if not p.is_dir():
        raise ValueError("that project folder does not exist")
    p = p.resolve()
    if p.parent == p:
        raise ValueError("choose a project folder, not the root of a drive")
    return p


def inside(root: Path, target: Path) -> bool:
    try:
        return target.resolve().is_relative_to(root.resolve())
    except OSError:      # pragma: no cover - unreadable path
        return False


def clean_text(text: str, where: str, warnings: list[str]) -> str:
    """Mask anything that looks like a credential before it is written to a file."""
    if SECRET_VALUE.search(text):
        warnings.append(f"{where} contained text that looks like a credential. It was replaced with {MASK} in the exported file; check the original.")
        return SECRET_VALUE.sub(MASK, text)
    return text


def one_line(text: str, limit: int = 600) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _diff(old: str, new: str, path: str) -> str:
    d = "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile=f"{path} (now)", tofile=f"{path} (after)", lineterm="", n=2))
    return d if len(d) <= DIFF_LIMIT else d[:DIFF_LIMIT] + "\n... (diff shortened)"


def front_matter(meta: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=10_000, default_flow_style=False) + "---\n\n" + body.strip() + "\n"


def marker(agent_id: str) -> str:
    return f"<!-- Exported from Agent Studio (agent: {agent_id}). Edit the agent in Agent Studio and deploy again to update this file. -->"


# ---------------------------------------------------------------------------------------------------- MCP servers
def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def server_entry(srv, scope: str) -> tuple[dict | None, list[dict], str]:
    """(Claude Code server entry or None, [{name, used_by, help}] environment variables to set, why it was left out).
    Stored credentials are never copied: they become ${VAR} references."""
    env_vars: list[dict] = []
    if not SERVER_OK.match(srv.id):
        return None, [], f"its name '{srv.id[:40]}' is not a name Claude Code accepts (letters, digits, - and _)"
    if srv.transport == "http":
        url = srv.url or ""
        sp = urlsplit(url)
        if sp.scheme not in ("http", "https") or not sp.hostname or sp.username or sp.password or SECRET_VALUE.search(url) or any(SECRET_NAME.search(k) for k in re.findall(r"[?&]([^=&]+)=", url)):
            return None, [], "its address is missing or carries a credential, which is never copied"
        headers: dict[str, str] = {}
        for k, v in srv.env.items():
            var = f"AGENT_STUDIO_{k.upper()}"
            if k == "GITHUB_PERSONAL_ACCESS_TOKEN":
                headers["Authorization"] = f"Bearer ${{{var}}}"
            elif k.upper().startswith("HEADER_") and v:
                headers["-".join(w.capitalize() for w in k[7:].split("_"))] = f"${{{var}}}"
            else:
                continue
            env_vars.append(dict(name=var, used_by=srv.id, help=f"The value Agent Studio stores for {k} (set it in your own environment; it was not copied)."))
        entry: dict = {"type": "http", "url": url}
        if headers:
            entry["headers"] = headers
        return entry, env_vars, ""
    cmd = srv.command or ""
    if not cmd or "{" in cmd:
        return None, [], "it runs from Agent Studio's own installation, which Claude Code does not have"
    ws = "${CLAUDE_PROJECT_DIR:-.}" if scope == "project" else "."
    args = [str(a).replace("{workspace}", ws) for a in srv.args]
    if any("{" in a.replace("${", "") for a in args):
        return None, [], "it needs a path inside Agent Studio's own data folder"
    env: dict[str, str] = {}
    for k, v in srv.env.items():
        v = str(v)
        if "{data}" in v:
            continue                          # a path in Agent Studio's data folder: the server's own default location is used instead
        if k in srv.requires_env or SECRET_NAME.search(k) or SECRET_VALUE.search(v):
            env[k] = f"${{{k}}}"
            env_vars.append(dict(name=k, used_by=srv.id, help=f"{k} for {srv.label} (set it in your own environment; Agent Studio's stored value was not copied)."))
        else:
            env[k] = v.replace("{workspace}", ws)
    if "{" in "".join(env.values()).replace("${", ""):
        return None, [], "it needs a path inside Agent Studio's own data folder"
    for k in srv.requires_env:
        if k not in env:
            env[k] = f"${{{k}}}"
            env_vars.append(dict(name=k, used_by=srv.id, help=f"{k} for {srv.label} (set it in your own environment)."))
    entry = {"type": "stdio", "command": cmd, "args": args}
    if env:
        entry["env"] = env
    return entry, env_vars, ""


def user_servers(home: Path) -> tuple[dict, str]:
    """(servers already in Claude Code's user scope, a warning if the file could not be read). Read only."""
    f = home / ".claude.json"
    if not f.is_file():
        return {}, ""
    try:
        data = json.loads(f.read_text("utf-8"))
        srv = data.get("mcpServers", {}) if isinstance(data, dict) else {}
        return (srv if isinstance(srv, dict) else {}), ""
    except (OSError, ValueError):
        return {}, "Could not read ~/.claude.json, so existing user-scope servers are not compared."


# ---------------------------------------------------------------------------------------------------- the planner
class Planner:
    def __init__(self, s, agent, opts: Opts, home: Path, claude_exe: str | None):
        self.s, self.agent, self.o, self.home = s, agent, opts, home
        self.root = resolve_root(opts, home)
        self.claude_dir = home / ".claude" if opts.scope == "user" else self.root / ".claude"
        self.claude_exe = claude_exe
        self.files: list[dict] = []
        self.commands: list[dict] = []
        self.not_carried: list[dict] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.prereq: list[dict] = []
        self.env_vars: list[dict] = []
        self.entries: dict[str, dict] = {}             # MCP server name -> entry that goes to the config (placement "config")
        self.seen_paths: set[str] = set()
        self.inherit: list[str] = []                   # agents whose Agent Studio model has no Claude Code counterpart

    # -- files
    def add_file(self, path: Path, text: str, kind: str, note: str = "") -> None:
        key = str(path)
        if key in self.seen_paths:
            return
        self.seen_paths.add(key)
        action, old, why = "create", "", note
        if not inside(self.root if self.o.scope == "project" else self.home, path):
            action, why = "blocked", "that path leads outside the chosen folder"
        elif path.is_symlink() or path.is_dir():
            action, why = "blocked", "something other than a plain file is already there"
        elif path.exists():
            try:
                old = path.read_text("utf-8")
            except (OSError, UnicodeDecodeError):
                action, why = "blocked", "the existing file cannot be read as text"
            else:
                action = "unchanged" if old == text else "update"
        self.files.append(dict(path=key, kind=kind, action=action, note=why, content=text, diff=_diff(old, text, key) if action in ("create", "update") else ""))

    def add_json_config(self, path: Path, servers: dict[str, dict]) -> None:
        """Merge server entries into a `.mcp.json`: other servers and keys stay exactly as they are."""
        old_text, data = "", {}
        if path.is_file() and not path.is_symlink():
            try:
                old_text = path.read_text("utf-8")
                data = json.loads(old_text) if old_text.strip() else {}
                if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
                    raise ValueError
            except (OSError, ValueError):
                self.seen_paths.add(str(path))
                self.files.append(dict(path=str(path), kind="mcp-config", action="blocked", note="it is not a valid MCP configuration file; fix or remove it first", content="", diff=""))
                return
        merged = dict(data)
        merged["mcpServers"] = {**data.get("mcpServers", {}), **servers}
        self.add_file(path, json.dumps(merged, indent=2, ensure_ascii=False) + "\n", "mcp-config", "")
        for f in self.files:
            if f["path"] == str(path) and f["action"] == "update":
                changed = [n for n in servers if n in data.get("mcpServers", {}) and data["mcpServers"][n] != servers[n]]
                f["note"] = ("changes the existing server entry " + ", ".join(changed)) if changed else "adds server entries; the others stay as they are"

    # -- MCP
    def place_servers(self) -> None:
        if not self.entries or self.o.mcp_placement == "agent":
            return
        if self.o.scope == "project":
            self.add_json_config(self.root / ".mcp.json", self.entries)
            return
        existing, warn = user_servers(self.home)
        if warn:
            self.warnings.append(warn)
        exe = self.claude_exe or "claude"
        for name, entry in self.entries.items():
            blob = json.dumps(entry, separators=(",", ":"))
            add = [exe, "mcp", "add-json", name, blob, "--scope", "user"]
            if name not in existing:
                action, argvs = "create", [add]
            elif existing[name] == entry:
                action, argvs = "unchanged", []
            else:
                action, argvs = "update", [[exe, "mcp", "remove", name, "--scope", "user"], add]
            shown = f"claude mcp add-json {name} {_shell_quote(blob)} --scope user"
            self.commands.append(dict(name=name, action=action, argvs=argvs, display=shown if action != "update" else f"claude mcp remove {name} --scope user\n{shown}",
                                      diff=_diff(json.dumps(existing.get(name), indent=2) if name in existing else "", json.dumps(entry, indent=2), name) if action != "unchanged" else ""))

    def servers_for(self, ids: list[str]) -> tuple[list[str], dict[str, dict]]:
        """(names of servers this agent can use in Claude Code, inline definitions of them)."""
        names, inline = [], {}
        for sid in dict.fromkeys(ids):
            srv = self.s.mcp.get(sid)
            if srv is None:
                self.not_carried.append(dict(item=f"MCP server {sid}", why="Agent Studio no longer has this server."))
                continue
            entry, env, why = server_entry(srv, self.o.scope)
            if entry is None:
                self.not_carried.append(dict(item=f"MCP server {srv.id}", why=f"Not copied: {why}."))
                continue
            names.append(srv.id)
            inline[srv.id] = entry
            self.entries.setdefault(srv.id, entry)
            for v in env:
                if v["name"] not in [e["name"] for e in self.env_vars]:
                    self.env_vars.append(v)
        return names, inline

    # -- model
    def model_for(self, a) -> str:
        p = self.s.providers.get(a.provider) if a.provider else None
        if p is not None and p.kind == "anthropic":
            if a.tier == "custom" and a.model and a.model.startswith("claude-"):
                return a.model
            return "opus" if a.tier == "pro" else "sonnet"
        who = f"its provider ({p.label})" if p is not None else "the provider chosen in the chat"
        self.inherit.append(f"{a.name} ({who})")
        return "inherit"

    # -- one subagent file
    def subagent(self, a, name: str, subs: list[tuple[str, object]]) -> None:
        w = self.warnings
        tools, lost = translate_tools(a.tools)
        lost += feature_losses(a)
        wants = [x for x in a.skills if x != "*"]
        if "*" in a.skills and self.o.include_skills:
            w.append(f"{a.name}: it may use every Agent Studio skill. Skills are only copied when the agent names them (or a whole group), so none were copied for 'all'.")
        skills = self.s.skills.resolve(wants) if self.o.include_skills else []
        good = [k for k in skills if SKILL_NAME_RE.match(k.name)]
        if len(good) != len(skills):
            w.append(f"{a.name}: a skill with an unusable name was left out.")
        ids = list(a.mcp) + self.s.skills.linked_mcp(wants)
        names, inline = self.servers_for(ids)
        for k in good:
            self.copy_skill(k)
        preload = [k.name for k in good][:] if len(good) <= PRELOAD_LIMIT else []
        if len(good) > PRELOAD_LIMIT:
            w.append(f"{a.name}: {len(good)} skills were copied but not preloaded (preloading injects each one whole). Claude Code can still use them through its Skill tool.")
        allow = list(tools)
        if good and not preload:
            allow.append("Skill")
        if subs:
            allow.append("Agent")
        allow += [f"mcp__{n}__*" for n in names]
        if not allow:
            allow = list(FALLBACK_TOOLS)
            w.append(f"{a.name}: it has no tool that Claude Code can run, so it gets the read-only file tools (Read, Glob, Grep) to be able to start.")
        meta: dict = {"name": name, "description": one_line(a.description) or f"{a.name} (exported from Agent Studio)", "tools": ", ".join(allow), "model": self.model_for(a)}
        if preload:
            meta["skills"] = preload
        if self.o.mcp_placement == "agent" and inline:
            meta["mcpServers"] = [{n: e} for n, e in inline.items()]
        body = clean_text(a.instruction.strip() or f"You are {a.name}.", f"The instruction of {a.name}", w)
        if subs:
            body += "\n\n## Sub-agents\nDelegate focused work to these subagents (they were exported together with this agent):\n" + "\n".join(
                f"- `{n}`: {one_line(getattr(x, 'description', '') or x.name, 200)}" for n, x in subs)
        self.add_file(self.claude_dir / "agents" / f"{name}.md", front_matter(meta, body + "\n\n" + marker(a.id)), "agent")
        for item in lost:
            if item not in self.not_carried:
                self.not_carried.append(dict(item=f"{a.name}: {item['item']}", why=item["why"]))

    def copy_skill(self, k) -> None:
        text = clean_text(k.body, f"The skill {k.name}", self.warnings)
        self.add_file(self.claude_dir / "skills" / k.name / "SKILL.md", front_matter({"name": k.name, "description": one_line(k.description, 1000) or k.name}, text), "skill")

    # -- the two modes
    def export(self) -> None:
        a = self.agent
        subs: list[tuple[str, object]] = []
        if self.o.include_sub_agents:
            seen, queue = {a.id}, list(a.sub_agents)
            while queue and len(subs) < MAX_SUB_AGENTS:
                sid = queue.pop(0)
                if sid in seen:
                    continue
                seen.add(sid)
                sub = self.s.agents.get(sid)
                if sub is None:
                    continue
                subs.append((claude_name(sid), sub))
                queue += sub.sub_agents
            if queue and len(subs) >= MAX_SUB_AGENTS:
                self.warnings.append(f"Only the first {MAX_SUB_AGENTS} sub-agents were included.")
        elif a.sub_agents:
            self.not_carried.append(dict(item=f"sub-agents ({', '.join(a.sub_agents)})", why="Left out because 'also export its sub-agents' is off."))
        by_id = {a.id: (claude_name(a.id), a), **{x.id: (n, x) for n, x in subs}}

        def kids(x) -> list[tuple[str, object]]:
            return [by_id[k] for k in x.sub_agents if k in by_id and k not in (x.id, a.id)]
        self.subagent(a, claude_name(a.id), kids(a))
        for n, x in subs:
            self.subagent(x, n, kids(x))
        self.place_servers()

    def live(self) -> None:
        a = self.agent
        cfg = access_config(self.s.db)
        self.prereq = [
            dict(id="api", ok=bool(cfg["api_enabled"]), text="The API is switched on in Settings > API & A2A."),
            dict(id="agent", ok=bool(a.api_access), text=f"API access is switched on for the agent '{a.name}' (agent editor > Framework and access)."),
            dict(id="key", ok=True, text=f"Create a key for this agent below, then set it as {KEY_ENV} in the environment Claude Code starts from."),
        ]
        server = f"agent-studio-{claude_name(a.id)}"
        entry = {"type": "http", "url": f"{self.o.base_url}/{LIVE_PATH}/{a.id}", "headers": {"Authorization": f"Bearer ${{{KEY_ENV}}}"}}
        self.entries[server] = entry
        self.env_vars.append(dict(name=KEY_ENV, used_by=server, help="An Agent Studio API key allowed to call this agent. It is shown once when you create it; it is never written to any file."))
        meta: dict = {"name": f"{claude_name(a.id)}-live", "description": one_line(f"Relay to the live Agent Studio agent \"{a.name}\". {a.description}", 600), "tools": f"mcp__{server}__*", "model": "inherit"}
        if self.o.mcp_placement == "agent":
            meta["mcpServers"] = [{server: entry}]
        body = (f"You are a thin relay to the Agent Studio agent \"{a.name}\", which runs in Agent Studio with its own instructions, tools, knowledge and guardrails.\n\n"
                f"For every task, call the `ask` tool of the `{server}` MCP server with the user's request (add the context the agent needs, since it cannot see this conversation or these files) "
                "and return its answer. Do not try to do the work yourself, and do not change the answer except to quote it.\n\n" + marker(a.id))
        self.add_file(self.claude_dir / "agents" / f"{claude_name(a.id)}-live.md", front_matter(meta, body), "agent")
        self.place_servers()
        self.notes += ["Agent Studio keeps running the agent, so its guardrails, memory, spend caps and run record all apply.",
                       "The agent cannot see your files in Claude Code; it only gets the text of each request."]

    def plan(self) -> dict:
        (self.export if self.o.mode == "export" else self.live)()
        if self.inherit:
            self.warnings.append("These agents run on another provider in Agent Studio; in Claude Code they use the model of your Claude Code session ('inherit'): " + ", ".join(self.inherit) + ".")
        if self.o.scope == "user" and self.o.mode == "export" and any(k in ("filesystem", "git") for k in self.entries):
            self.notes.append("Filesystem and git servers are limited to the folder Claude Code starts in.")
        blob = json.dumps([self.o.mode, self.o.scope, [(f["path"], f["action"], f["content"]) for f in self.files], [c["argvs"] for c in self.commands]], sort_keys=True)
        return dict(mode=self.o.mode, scope=self.o.scope, agent=dict(id=self.agent.id, name=self.agent.name), root=str(self.claude_dir), files=self.files, commands=self.commands,
                    not_carried=self.not_carried, warnings=self.warnings, notes=self.notes, prerequisites=self.prereq, env_vars=self.env_vars,
                    claude_found=bool(self.claude_exe), hash=hashlib.sha256(blob.encode("utf-8")).hexdigest())


def build_plan(s, agent_id: str, opts: Opts, home: Path | None = None, claude_exe: object = AUTO) -> dict:
    agent = s.agents.get(agent_id)
    if agent is None:
        raise KeyError(agent_id)
    exe = shutil.which("claude") if claude_exe is AUTO else claude_exe
    return Planner(s, agent, opts, home or Path.home(), exe if isinstance(exe, str) else None).plan()
