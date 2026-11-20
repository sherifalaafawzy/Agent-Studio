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

    async def create_skill(name: str, description: str, body: str, mcp_servers: list[str] | None = None, overwrite: bool = False) -> dict:
        """Author a new reusable skill (saved as SKILL.md). Use the skill-creator skill for guidance first.

        Args:
            name: lowercase-hyphenated skill name.
            description: One or two sentences: what it does and WHEN to use it (this is what agents see in their index).
            body: Markdown instructions.
            mcp_servers: Optional MCP server ids this skill needs (agents using the skill get those servers if enabled).
            overwrite: Replace an existing user skill of the same name.
        """
        try:
            known = {s.id for s in ctx.mcp.list()}
            bad = [m for m in (mcp_servers or []) if m not in known]
            if bad:
                raise ValueError(f"unknown MCP servers {bad}; known: {sorted(known)}. Use propose_mcp_server for a new one.")
            s = ctx.skills.create(name, description, body, mcp_servers, overwrite)
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

    registry = {f.__name__: f for f in (read_file, list_dir, glob_files, search_text, write_file, edit_file, run_shell,
                                         load_skill, create_skill, propose_mcp_server)}
    return [registry[n] for n in allowed if n in registry]
