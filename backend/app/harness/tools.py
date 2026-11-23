"""The agent tool set ("super harness"). Design notes - what was taken from where:

* edit_file = Aider/Claude-Code style exact search-and-replace (unique match required, whitespace-tolerant fallback,
  closest-match hints on failure) -> small diffs instead of whole-file rewrites.
* MANDATORY verify step after every write/edit (SWE-agent linter-guarded edits): a failing check ROLLS BACK the
  file and returns the lint output; 'skipped' checks are reported, never hidden.
* read-before-edit is enforced (Cline/Claude-Code): edit_file refuses files the agent has not read this turn.
* run_shell executes ONLY inside the Docker sandbox (OpenHands-style runtime isolation); fail-closed.
* Tools return {ok: false, error: ...} instead of raising, so the model can self-correct.
"""
from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..mcp_registry import McpStore
from ..skills import SkillRegistry
from . import files as F
from .sandbox import Sandbox, SandboxUnavailable
from .verify import verify

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist", "build", ".pytest_cache", ".ruff_cache"}

# name -> (description for the UI, read_only)
TOOL_INFO: dict[str, tuple[str, bool]] = {
    "read_file": ("Read a file with line numbers", True),
    "list_dir": ("List a directory", True),
    "glob_files": ("Find files by glob pattern", True),
    "search_text": ("Regex search across files", True),
    "write_file": ("Create a file (verified)", False),
    "edit_file": ("Diff-based edit with mandatory lint/verify + rollback", False),
    "run_shell": ("Run a shell command in the Docker sandbox", False),
    "load_skill": ("Load a skill's full instructions", True),
    "create_skill": ("Author a new skill (SKILL.md) and optionally link MCP servers", False),
    "propose_mcp_server": ("Propose an MCP server (disabled until a human approves it)", False),
    "propose_openapi_mcp": ("Propose an API from an OpenAPI file as MCP tools (read-only; disabled until a human approves it)", False),
    "rag_search": ("Search the knowledge bases attached to the agent (RAG) and cite the sources", True),
    "rag_list": ("List the knowledge bases attached to the agent", True),
    "data_sources": ("List the data sources the agent may read (databases, warehouses, Salesforce)", True),
    "data_schema": ("Browse a data source: its tables and columns", True),
    "data_query": ("Run one read-only query on a data source (writes are refused)", True),
    "memory_search": ("Search what the agent remembers (facts, past chats, people and things, how-tos)", True),
    "memory_save": ("Remember a fact, a person or thing, a how-to or an event (may wait for your review)", False),
    "memory_forget": ("Forget a memory by id or description", False),
    "memory_notes": ("Read or add to the project notes file (AGENTS.md) in the workspace", False),
    "save_artifact": ("Keep a file or some text as an artifact you can find later on the Artifacts page", False),
    "todo_write": ("Keep a to-do list for the task that you can watch update live", False),
    "repo_map": ("A compact map of the project: files and their main classes and functions", True),
    "web_fetch": ("Read a public web page (only when web tools are switched on in Settings)", True),
    "web_search": ("Search the web (needs a search service in Settings)", True),
    "canvas_create": ("Open a canvas beside the chat to write a document, code, a table or a web page together", False),
    "canvas_read": ("Read the canvas (and the text you selected)", True),
    "canvas_replace_selection": ("Propose a replacement for the text you selected in the canvas", False),
    "canvas_insert": ("Propose text to add to the canvas", False),
    "canvas_patch": ("Propose exact-text edits to the canvas", False),
    "app_agents": ("List your agents, to choose which ones an app should call", True),
    "app_list": ("List your apps", True),
    "app_create": ("Start a new app from a standard template", False),
    "app_tree": ("List the files of an app", True),
    "app_read_file": ("Read a file of an app", True),
    "app_write_file": ("Propose a new or replaced file in an app", False),
    "app_patch_file": ("Propose exact-text edits to a file of an app", False),
    "app_delete_file": ("Propose deleting a file of an app", False),
    "app_set_manifest": ("Propose a change to an app's manifest (its agents, permissions, variables)", False),
    "app_check": ("Check an app: manifest, files, secrets, remote code, callable agents", True),
    "app_plan": ("Write down the plan for an app (steps, pages, features) before building it", False),
    "app_plan_done": ("Tick steps off the plan of an app", False),
    "app_verify": ("Check an app's files and the errors its running preview reported, so problems can be fixed", True),
    "app_components": ("List the ready-made parts (table, chart, tabs...) or propose adding some to an app", False),
    "app_theme": ("List the colour themes or propose a theme, font and corner style for an app", False),
    "app_connect_agent": ("Propose letting an app call one of your agents (never switches API access on)", False),
    "app_data_sources": ("List your data sources an app can read, and which ones it uses", True),
    "app_connect_data": ("Propose letting an app read one of your data sources (read-only)", False),
    "app_undo": ("Go back one version of an app (only while changes apply at once)", False),
    "science_sources": ("List the open science data sources (biology, chemistry, physics, maths, literature) and their limits", True),
    "science_start": ("Open a Science Lab investigation: a question with its evidence and report", False),
    "science_search": ("Search or look up an open science data source (PubMed, PubChem, UniProt, arXiv, OEIS...), read-only", True),
    "science_calc": ("Exact local calculations: expressions, units, constants, molar mass, sequence stats, numeric calculus", True),
    "science_run_python": ("Run Python in the sandbox with a reproducibility record", False),
    "science_add_evidence": ("Attach evidence (from a recorded source call or run) to an investigation", False),
    "science_add_claim": ("Record a claim; its label (established, single source, inference...) is worked out from the evidence", False),
    "science_add_hypothesis": ("Add a candidate hypothesis to rank", False),
    "science_compare": ("Record which of two hypotheses the evidence supports better (Elo ranking)", False),
    "science_check_citations": ("Check the DOIs and PMIDs in the evidence against Crossref and PubMed", True),
    "science_report": ("Build the investigation report (claims with labels, evidence, computations, limits), optionally keep it as artifacts", False),
    "render_ui": ("Show a chart, table, time series, map or card in the answer", True),
    "ui_catalog": ("List the chart, map and card types the agent can show", True),
    "run_workflow": ("Run one of your saved workflows (a drawn graph of agents, tools and API calls)", False),
    "call_remote_agent": ("Ask another agent over the A2A protocol (agents registered in Settings > API & A2A)", False),
}
READ_ONLY_TOOLS = [n for n, (_, ro) in TOOL_INFO.items() if ro and n not in ("load_skill",)]
ALL_TOOLS = list(TOOL_INFO)


@dataclass
class ToolContext:
    workspace: Path
    sandbox: Sandbox
    skills: SkillRegistry
    mcp: McpStore
    read_files: set[str] = field(default_factory=set)  # read-before-edit tracking (per chat turn)
    rag: object | None = None                          # the RagService, when knowledge bases are available
    rag_kbs: tuple[str, ...] = ()                      # knowledge bases THIS agent may search ("*" = all)
    data: object | None = None                         # the DataService, when data sources exist
    data_grants: tuple[str, ...] = ()                  # data sources THIS agent may read ("*" = all of them)
    memory: object | None = None                       # the MemoryService, when memory is available
    mem: dict = field(default_factory=dict)            # THIS agent's memory settings ({} = none)
    agent_id: str = ""
    session_id: str = ""
    on_memory: object | None = None                    # callback(event dict): what the agent remembered, shown in the chat
    artifacts: object | None = None                    # the ArtifactService, when artifacts are available
    on_artifact: object | None = None                  # callback(artifact dict): an artifact was saved, shown in the chat
    trace_id: str = ""                                 # the run this turn belongs to (links an artifact back to it)
    web: object | None = None                          # the WebClient, when web tools are available (they also need to be switched on)
    remotes: object | None = None                      # the RemoteAgents registry (A2A), when any enabled remote agent exists
    workflows: object | None = None                    # the workflow Runner, when any workflow is saved
    todos: list = field(default_factory=list)          # the agent's current to-do list
    on_todos: object | None = None                     # callback(list): the list changed, shown live in the chat
    canvas: object | None = None                       # the CanvasService, when the canvas is on
    on_canvas: object | None = None                    # callback(event dict): the canvas changed or opened, shown in the chat
    can_apply: bool = True                             # False in plan mode: the agent may only propose canvas changes
    apps: object | None = None                         # the AppStore, when apps are on
    on_app: object | None = None                       # callback(event dict): an app was created or changed, shown in the chat
    agui_components: bool = True                       # False: this agent's AG-UI components are switched off (see agui_flags.py)
    uikit: object | None = None                        # the UiKitService, when rich components are available (render_ui, ui_catalog)
    on_ui: object | None = None                        # callback(spec dict) -> False when full: a component was shown in the chat
    science: object | None = None                      # the ScienceService, when the Science Lab is on
    on_science: object | None = None                   # callback(event dict): an investigation changed, shown in the chat


def build_tools(ctx: ToolContext, allowed: list[str]) -> list:
    ws = ctx.workspace
    ws.mkdir(parents=True, exist_ok=True)

    def rel(p: Path) -> str:
        return p.relative_to(ws.resolve()).as_posix()

    async def read_file(path: str, start_line: int = 1, end_line: int = 0) -> dict:
        """Read a text file from the workspace with line numbers.

        Args:
            path: File path relative to the workspace root.
            start_line: First line to return (1-based).
            end_line: Last line to return (0 = to the end; large files are paged).
        """
        try:
            p = F.jail(ws, path)
            if not p.is_file():
                raise F.ToolError(f"not a file: {path}")
            text, _ = F.read_text(p)
            ctx.read_files.add(rel(p))
            return {"ok": True, "path": rel(p), "total_lines": text.count("\n") + 1, "content": F.numbered(text, start_line, end_line)}
        except F.ToolError as e:
            return {"ok": False, "error": str(e)}

    async def list_dir(path: str = ".") -> dict:
        """List files and folders in a workspace directory (non-recursive).

        Args:
            path: Directory relative to the workspace root.
        """
        try:
            p = F.jail(ws, path)
            if not p.is_dir():
                raise F.ToolError(f"not a directory: {path}")
            items = [{"name": c.name + ("/" if c.is_dir() else ""), "size": c.stat().st_size if c.is_file() else None}
                     for c in sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower())) if c.name not in (".git",)]
            return {"ok": True, "path": rel(p) if p != ws.resolve() else ".", "entries": items[:300], "truncated": len(items) > 300}
        except F.ToolError as e:
            return {"ok": False, "error": str(e)}

    async def glob_files(pattern: str) -> dict:
        """Find files matching a glob pattern (e.g. '**/*.py'), skipping vendor/build directories.

        Args:
            pattern: Glob relative to the workspace root.
        """
        try:
            if pattern.startswith(("/", "\\")) or ".." in Path(pattern).parts:
                raise F.ToolError("pattern must be relative and inside the workspace")
            out = []
            for p in ws.glob(pattern):
                r = p.relative_to(ws)
                if any(x in SKIP_DIRS for x in r.parts) or not p.is_file():
                    continue
                out.append(r.as_posix())
                if len(out) >= 300:
                    break
            return {"ok": True, "matches": sorted(out)}
        except (F.ToolError, ValueError, NotImplementedError) as e:
            return {"ok": False, "error": str(e)}

    async def search_text(pattern: str, path: str = ".", file_glob: str = "") -> dict:
        """Regex search file contents. Returns file:line:text matches (max 100).

        Args:
            pattern: Python regular expression.
            path: Directory or file to search, relative to the workspace root.
            file_glob: Optional filename filter such as '*.py'.
        """
        try:
            rx = re.compile(pattern)
            root = F.jail(ws, path)
            files = [root] if root.is_file() else (p for p in root.rglob("*") if p.is_file())
            hits = []
            for p in files:
                r = p.relative_to(ws.resolve())
                if any(x in SKIP_DIRS for x in r.parts) or (file_glob and not fnmatch.fnmatch(p.name, file_glob)):
                    continue
                try:
                    if p.stat().st_size > 1_000_000:
                        continue
                    t = p.read_text("utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for i, line in enumerate(t.split("\n"), 1):
                    if rx.search(line):
                        hits.append(f"{r.as_posix()}:{i}:{line.strip()[:200]}")
                        if len(hits) >= 100:
                            return {"ok": True, "matches": hits, "truncated": True}
            return {"ok": True, "matches": hits, "truncated": False}
        except re.error as e:
            return {"ok": False, "error": f"bad regex: {e}"}
        except F.ToolError as e:
            return {"ok": False, "error": str(e)}

    def _write_verified(p: Path, new_text: str, nl: str, old_text: str | None) -> dict:
        """Write, VERIFY, roll back on failure. Returns the tool result."""
        F.write_text(p, new_text, nl)
        v = verify(p, rel(p), new_text, ctx.sandbox)
        if not v.ok:
            if old_text is None:
                p.unlink(missing_ok=True)
            else:
                F.write_text(p, old_text, nl)
            return {"ok": False, "rolled_back": True, "error": "verification FAILED - the change was rolled back. Fix the problem and retry.",
                    "verify": v.checks}
        ctx.read_files.add(rel(p))
        return {"ok": True, "verify": v.checks, "verified": all(c["status"] != "skipped" for c in v.checks)}

    async def write_file(path: str, content: str, overwrite: bool = False) -> dict:
        """Create a new file (verified after writing; rolled back if verification fails). To change an existing file use edit_file.

        Args:
            path: File path relative to the workspace root.
            content: Full file content.
            overwrite: Must be true to replace an existing file.
        """
        try:
            p = F.jail(ws, path)
            old = nl = None
            if p.exists():
                if not overwrite:
                    raise F.ToolError("file exists - use edit_file for changes (or overwrite=true to replace the whole file)")
                old, nl = F.read_text(p)
            r = await asyncio.to_thread(_write_verified, p, content, nl or "\n", old)
            return {"path": rel(p), **r}
        except F.ToolError as e:
            return {"ok": False, "error": str(e)}

    async def edit_file(path: str, old_text: str, new_text: str, replace_all: bool = False) -> dict:
        """Edit an existing file by replacing exact text. Preferred over rewriting files. The file must have been read first.
        After applying, the file is automatically verified (syntax/lint); on failure the edit is rolled back and the errors returned.

        Args:
            path: File path relative to the workspace root.
            old_text: Exact existing text to replace (include enough surrounding lines to be unique).
            new_text: Replacement text.
            replace_all: Replace every occurrence instead of requiring a unique match.
        """
        try:
            p = F.jail(ws, path)
            if not p.is_file():
                raise F.ToolError(f"file not found: {path} (use write_file to create it)")
            if rel(p) not in ctx.read_files:
                raise F.ToolError("read the file with read_file before editing it")
            old, nl = F.read_text(p)
            new, how = F.apply_edit(old, old_text, new_text, replace_all)
            r = await asyncio.to_thread(_write_verified, p, new, nl, old)
            out = {"path": rel(p), "match": how, **r}
            if r["ok"]:
                out["diff"] = F.unified(old, new, rel(p))
            return out
        except F.ToolError as e:
            return {"ok": False, "error": str(e)}

    async def run_shell(command: str, timeout_s: int = 60) -> dict:
        """Run a bash command inside the isolated Docker sandbox (cwd = workspace; no network unless enabled in Settings).
        Use it to run tests, linters, builds, git. It never runs on the host.

        Args:
            command: Bash command line.
            timeout_s: Kill after this many seconds (max 600).
        """
        try:
            r = await asyncio.to_thread(ctx.sandbox.exec, command, timeout_s)
            return {"ok": r["exit_code"] == 0, **r}
        except SandboxUnavailable as e:
            return {"ok": False, "error": f"SANDBOX UNAVAILABLE (refusing to run on host): {e}"}

    async def load_skill(name: str) -> dict:
        """Load the full instructions of a skill listed in your Skills index.

        Args:
            name: Skill name.
        """
        s = ctx.skills.get(name)
        if not s:
            return {"ok": False, "error": f"unknown skill '{name}'", "available": [x.name for x in ctx.skills.list()]}
        return {"ok": True, "name": s.name, "mcp_servers_linked": s.mcp, "instructions": s.body}

    async def create_skill(name: str, description: str, body: str, mcp_servers: list[str] | None = None, overwrite: bool = False,
                           group: str = "General") -> dict:
        """Author a new reusable skill (saved as SKILL.md). Use the skill-creator skill for guidance first.

        Args:
            name: lowercase-hyphenated skill name.
            description: One or two sentences: what it does and WHEN to use it (this is what agents see in their index).
            body: Markdown instructions.
            mcp_servers: Optional MCP server ids this skill needs (agents using the skill get those servers if enabled).
            overwrite: Replace an existing user skill of the same name.
            group: Short label to file it under (Coding, Security, Research, Writing, Data, Ops, General or your own).
        """
        try:
            known = {s.id for s in ctx.mcp.list()}
            bad = [m for m in (mcp_servers or []) if m not in known]
            if bad:
                raise ValueError(f"unknown MCP servers {bad}; known: {sorted(known)}. Use propose_mcp_server for a new one.")
            s = ctx.skills.create(name, description, body, mcp_servers, overwrite, group)
            return {"ok": True, "skill": s.public(), "note": "Saved. Agents pick it up via their Skills index."}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    async def propose_mcp_server(id: str, label: str, description: str, command: str, args: list[str]) -> dict:
        """Propose a new MCP server. It is stored DISABLED; a human must review and enable it in Settings (it would run code on the host).

        Args:
            id: short lowercase id.
            label: Display name.
            description: What it provides and why it is needed.
            command: Executable (e.g. npx or uvx).
            args: Arguments (pin exact package versions).
        """
        try:
            s = ctx.mcp.propose(id, label, description, command, args)
            return {"ok": True, "status": s.status, "note": "Pending human approval in Settings > MCP. It will not run until enabled."}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    async def propose_openapi_mcp(id: str, label: str, description: str, spec: str, base_url: str = "",
                                  auth_header_env: str = "") -> dict:
        """Propose exposing an API described by an OpenAPI file as tools. Read-only; a human must approve it first.

        Args:
            id: short lowercase id, e.g. 'billing-api'.
            label: Display name.
            description: What the API is for and why agents need it.
            spec: ABSOLUTE path to the OpenAPI file on this PC, or an https URL.
            base_url: Server URL to call (optional; defaults to the document's own).
            auth_header_env: Leave empty. If the API needs a token, the human adds it after approval (never put secrets here).
        """
        from ..mcp_openapi import validate
        if not spec.startswith("https://") and not (spec.startswith("/") or (len(spec) > 2 and spec[1] == ":")):
            return {"ok": False, "error": "spec must be an absolute file path or an https URL"}
        try:
            n = validate(spec, base_url or None)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"not a usable OpenAPI document: {e}"}
        args = [spec] + (["--base", base_url] if base_url else [])
        requires = ["HEADER_AUTHORIZATION"] if auth_header_env or n else []
        try:
            s = ctx.mcp.propose_openapi(id, label, f"{description} ({n} operations, read-only)", args, requires)
            return {"ok": True, "status": s.status, "operations": n,
                    "note": "Pending human approval on the MCP servers page (review the file path and the operations). It will not run until approved."}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def attached_kbs() -> list[dict]:
        """The knowledge bases this agent may search, as the service describes them."""
        every = ctx.rag.list_kbs()                                 # type: ignore[union-attr]
        return every if "*" in ctx.rag_kbs else [k for k in every if k["id"] in ctx.rag_kbs]

    async def rag_list() -> dict:
        """List the knowledge bases you can search with rag_search (id, what it holds, how big it is)."""
        return {"ok": True, "knowledge_bases": [
            {"id": k["id"], "label": k["label"], "description": k["description"], "documents": k["docs"], "passages": k["chunks"]}
            for k in attached_kbs()]}

    async def rag_search(query: str, knowledge_base: str = "", k: int = 5) -> dict:
        """Search your knowledge bases for passages relevant to a question. Use it before answering from memory when the
        question may be covered by the user's own documents. Cite the `source` of every passage you rely on.

        Args:
            query: What to look for, in natural language (a full question works better than keywords).
            knowledge_base: Id of one knowledge base to search. Empty searches all of yours (see rag_list).
            k: How many passages to return (1-10).
        """
        try:
            ids = [kb["id"] for kb in attached_kbs()]
            if knowledge_base:
                if knowledge_base not in ids:
                    return {"ok": False, "error": f"'{knowledge_base}' is not one of your knowledge bases ({', '.join(ids) or 'none'})"}
                ids = [knowledge_base]
            results = await ctx.rag.search(query, ids, max(1, min(int(k), 10)))     # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001 - every failure (bad query, store down, spend cap) goes back to the model as text
            return {"ok": False, "error": str(e)[:400]}
        return {"ok": True, "results": results,
                "note": ("Each result has a score_kind. 'cosine' scores run to 1 (identical) and low ones mean weak matches; 'provider' scores come "
                         "from a managed service on its own scale and only compare within one knowledge base. "
                         "Say so if nothing relevant was found.")}

    async def save_artifact(name: str, path: str = "", content: str = "", tags: str = "") -> dict:
        """Keep a deliverable (a report, a chart, a data file, a model, a script) as an artifact so the user can find, preview and download it later
        on the Artifacts page. Use it for results the user will want to keep, not for scratch files. Give either `path` (a file in the workspace)
        or `content` (text to keep).

        Args:
            name: A short, descriptive file name, for example 'q3-sales-report.md' (the extension decides how it is shown).
            path: A file inside the workspace to keep, relative to the workspace root. Leave empty when you pass content.
            content: The text to keep when there is no file.
            tags: A few comma-separated words that help find it later (for example 'sales, q3').
        """
        from ..artifacts.service import ArtifactError
        if ctx.artifacts is None:
            return {"ok": False, "error": "artifacts are not available"}
        words = [t.strip() for t in (tags or "").split(",") if t.strip()]
        try:
            if path:
                p = F.jail(ws, path)
                if not p.is_file():
                    raise F.ToolError(f"not a file: {path}")
                art = ctx.artifacts.capture_file(p, rel(p), agent=ctx.agent_id, session_id=ctx.session_id, trace_id=ctx.trace_id, workspace=str(ws), source="tool",
                                                 tags=words, name=name or None)
            else:
                art = ctx.artifacts.save_text(name, content, agent=ctx.agent_id, session_id=ctx.session_id, trace_id=ctx.trace_id, workspace=str(ws), tags=words)
        except (F.ToolError, ArtifactError) as e:
            return {"ok": False, "error": str(e)}
        if not art.get("existing") and ctx.on_artifact:
            ctx.on_artifact(art)
        return {"ok": True, "id": art["id"], "name": art["name"], "type": art["type"], "size": art["size"], "kept_in": [c["storage"] for c in art["copies"]],
                **({"note": "this exact file was already saved"} if art.get("existing") else {})}

    registry = {f.__name__: f for f in (read_file, list_dir, glob_files, search_text, write_file, edit_file, run_shell,
                                         load_skill, create_skill, propose_mcp_server, propose_openapi_mcp, rag_search, rag_list, save_artifact)}
    if ctx.rag is None or not ctx.rag_kbs:                        # no knowledge bases attached: the tools do not exist for this agent
        registry.pop("rag_search")
        registry.pop("rag_list")
    if ctx.data is not None and ctx.data_grants:                  # data tools exist only for agents with a data source ticked
        from ..datasources.tools import data_tools
        registry.update(data_tools(ctx))
    if ctx.memory is not None and ctx.mem:                        # memory tools exist only for agents that have memory switched on
        from ..memory.tools import memory_tools
        registry.update(memory_tools(ctx))
    from .extras import extra_tools
    registry.update(extra_tools(ctx))
    if ctx.canvas is not None:                                    # canvas tools exist only while the canvas is on
        from ..canvas.tools import canvas_tools
        registry.update(canvas_tools(ctx))
    if ctx.apps is not None and ctx.apps.config()["enabled"]:            # app tools exist only while apps are on
        from ..apps.tools import app_tools
        registry.update(app_tools(ctx))
    if ctx.science is not None and ctx.science.config()["enabled"]:      # science tools exist only while the Science Lab is on
        from ..science.tools import science_tools
        registry.update(science_tools(ctx))
    if ctx.uikit is not None and ctx.uikit.config()["enabled"]:    # rich components exist only while the kit is on
        from ..uikit.tools import uikit_tools
        registry.update(uikit_tools(ctx))
    ids = ctx.remotes.enabled_ids(ctx.agent_id or "") if ctx.remotes is not None else []
    wf_ids = ctx.workflows.store.ids() if ctx.workflows is not None else []
    if wf_ids:                                                    # run_workflow exists only once a workflow is saved
        from ..workflows.core import workflow_tool
        registry["run_workflow"] = workflow_tool(ctx.workflows, wf_ids)
    if ids:                                                       # remote A2A agents exist only once one is registered and enabled
        from ..apiaccess.a2a import remote_tool
        registry["call_remote_agent"] = remote_tool(ctx.remotes, ids)
    return [registry[n] for n in allowed if n in registry]
