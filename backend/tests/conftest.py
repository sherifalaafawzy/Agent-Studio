import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import State, create_app


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test gets its own data dir (user skills, default workspace) - never the real ./data."""
    monkeypatch.setenv("AGENT_STUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_STUDIO_ENABLE_MOCK", "1")       # the offline mock provider exists only for tests


@pytest.fixture(autouse=True)
def _no_real_ollama(monkeypatch):
    """A test never talks to a real Ollama on this PC (and so can never start a multi-GB pull): tests that need one install their own transport."""
    import httpx
    from app.easy import ollama_local

    def refuse(request):
        raise httpx.ConnectError("no real Ollama in tests", request=request)
    monkeypatch.setattr(ollama_local, "TRANSPORT", httpx.MockTransport(refuse))
    monkeypatch.setattr(ollama_local, "ATRANSPORT", httpx.MockTransport(refuse))


@pytest.fixture
def state(tmp_path):
    return State(Database(tmp_path / "t.db"))


@pytest.fixture
def client(state):
    return TestClient(create_app(state))


@pytest.fixture(autouse=True, scope="session")
def _cleanup_sandbox_containers():
    yield
    import subprocess, shutil
    d = shutil.which("docker")
    if d:
        ids = subprocess.run([d, "ps", "-aq", "--filter", "label=agent-studio=1"], capture_output=True, text=True).stdout.split()
        if ids:
            subprocess.run([d, "rm", "-f", *ids], capture_output=True)


_EXTERNAL_LAUNCHERS = {"npx", "uvx", "uv", "node", "npm", "docker", "bunx", "pipx"}
BLOCKED_EXTERNAL_MCP: list[str] = []


@pytest.fixture(autouse=True)
def _no_real_external_mcp_servers(monkeypatch):
    """A test never starts a real npx/uvx/node MCP server. Starting one (the built-in 'memory' and 'filesystem' servers, or any
    server a test adds with command "npx") downloads packages, leaves node children behind after the test, and made the full
    coverage run stall for minutes inside test_cov_main. Tests that need a stdio server start one of our own with sys.executable."""
    import os
    import mcp.client.stdio as stdio

    real = stdio._create_platform_compatible_process

    async def guarded(command, args, *rest, **kw):
        name = os.path.splitext(os.path.basename(str(command)))[0].lower()
        if name in _EXTERNAL_LAUNCHERS:
            BLOCKED_EXTERNAL_MCP.append(f"{name} {' '.join(map(str, args))[:60]}")
            raise OSError(f"tests do not start external MCP servers ({name})")
        return await real(command, args, *rest, **kw)
    monkeypatch.setattr(stdio, "_create_platform_compatible_process", guarded)


def pytest_terminal_summary(terminalreporter):
    if BLOCKED_EXTERNAL_MCP:
        terminalreporter.write_line(f"blocked {len(BLOCKED_EXTERNAL_MCP)} attempts to start a real external MCP server (npx/uvx/node), e.g. {BLOCKED_EXTERNAL_MCP[0]}")
