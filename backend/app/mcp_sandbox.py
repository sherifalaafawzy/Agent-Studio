"""Coding sandbox as an MCP server (stdio).

Gives any MCP-capable agent a Docker-isolated place to run code and tests:
  * no network unless the server was started with --network (off by default),
  * no Linux capabilities, non-root, memory / CPU / process limits,
  * only the workspace folder is mounted (read-write) at /workspace.
Launch:  python -m app.mcp_sandbox <workspace-dir> [--network]
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from .harness.sandbox import Sandbox, SandboxUnavailable, status

server = MCPServer("agent-studio-sandbox", instructions=(
    "Run code, tests and commands in an isolated Docker sandbox. The workspace is mounted at /workspace; files you write "
    "there are the project's files. There is no network unless the user enabled it. Each call is a fresh process; state "
    "does not persist between calls except on disk in /workspace."))

_sb: Sandbox | None = None


def _box() -> Sandbox:
    global _sb
    if _sb is None:
        raise SandboxUnavailable("sandbox not configured")
    return _sb


def _result(r: dict) -> dict:
    return {"ok": r.get("exit_code") == 0 and not r.get("timed_out"), **r}


@server.tool(description="Run a shell command in the sandbox (bash, working directory /workspace). Returns exit code, stdout, stderr.")
def run_command(command: str, timeout_s: int = 120) -> dict:
    try:
        return _result(_box().exec(command, timeout_s))
    except SandboxUnavailable as e:
        return {"ok": False, "error": f"sandbox unavailable: {e}"}


@server.tool(description="Run a Python snippet in the sandbox. It is saved as a file under /workspace/.sandbox-runs and executed with python3.")
def run_python(code: str, timeout_s: int = 120) -> dict:
    name = f".sandbox-runs/run-{uuid.uuid4().hex[:8]}.py"
    try:
        box = _box()
        path = box.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        return _result(box.exec(f"python3 {name}", timeout_s))
    except SandboxUnavailable as e:
        return {"ok": False, "error": f"sandbox unavailable: {e}"}


@server.tool(description="Check whether the sandbox is available (Docker running, image present) and what it allows.")
def sandbox_status() -> dict:
    st = status()
    return {**st, "workspace": str(_box().workspace) if _sb else None, "network": bool(_sb and _sb.network)}


def main(argv: list[str]) -> None:
    global _sb
    if not argv:
        print("usage: python -m app.mcp_sandbox <workspace-dir> [--network]", file=sys.stderr)
        raise SystemExit(2)
    _sb = Sandbox(Path(argv[0]), network="--network" in argv[1:])
    server.run("stdio")


if __name__ == "__main__":
    main(sys.argv[1:])
