"""The Docker coding sandbox exposed as an MCP server: it starts over stdio, lists its tools, and runs code isolated."""
import asyncio
import pytest

from app.harness import sandbox as sbx
from app import mcp_sandbox as S
from app.mcp_registry import McpStore, build_toolsets

READY = sbx.status()
needs_docker = pytest.mark.skipif(not (READY["docker"] and READY["image"]), reason="docker sandbox image not available")


@needs_docker
def test_server_starts_over_stdio_and_lists_tools(state, tmp_path):
    state.mcp.update("code-sandbox", True)
    warns = []
    sets = asyncio.run(build_toolsets(state.mcp, ["code-sandbox"], str(tmp_path / "ws"), warns))
    assert sets, warns
    names = {t.name for t in asyncio.run(sets[0].get_tools())}
    assert {"run_command", "run_python", "sandbox_status"} <= {n.split("_", 1)[1] if n.startswith("code-sandbox_") else n for n in names}
    asyncio.run(sets[0].close())


@needs_docker
def test_runs_code_inside_the_container_without_network(tmp_path):
    S._sb = sbx.Sandbox(tmp_path / "ws")
    try:
        r = S.run_python("import socket\nprint('hello from sandbox')\ntry:\n    socket.create_connection(('1.1.1.1', 53), 3)\n    print('NET')\nexcept OSError:\n    print('NO NET')\n")
        assert r["ok"] and "hello from sandbox" in r["stdout"] and "NO NET" in r["stdout"]
        r = S.run_command("id -u && ls /workspace | head -3")
        assert r["ok"] and r["stdout"].strip().splitlines()[0] == "1000"               # non-root inside the box
        r = S.run_command("exit 3")
        assert r["exit_code"] == 3 and not r["ok"]
    finally:
        S._sb.stop()
        S._sb = None


def test_unavailable_docker_is_reported_not_raised(tmp_path, monkeypatch):
    S._sb = sbx.Sandbox(tmp_path / "ws")
    monkeypatch.setattr(sbx, "status", lambda: dict(docker=False, image=False, detail="Docker not running"))
    r = S.run_command("echo hi")
    assert r["ok"] is False and "sandbox unavailable" in r["error"]
    S._sb = None
