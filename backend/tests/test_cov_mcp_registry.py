"""Coverage for app/mcp_registry.py: store validation branches and build_toolsets header/warning/cleanup paths.
McpToolset is replaced by a recording fake, so no MCP server is started and no network is used."""
import asyncio

import pytest

from app import mcp_registry as R


class FakeToolset:
    instances = []
    fail_probe = None
    close_error = None

    def __init__(self, connection_params, tool_name_prefix):
        self.connection_params = connection_params
        self.tool_name_prefix = tool_name_prefix
        self.closed = False
        FakeToolset.instances.append(self)

    async def get_tools(self):
        if FakeToolset.fail_probe is not None:
            raise FakeToolset.fail_probe
        return []

    async def close(self):
        self.closed = True
        if FakeToolset.close_error is not None:
            raise FakeToolset.close_error


@pytest.fixture
def toolset(monkeypatch):
    from google.adk.tools.mcp_tool import mcp_toolset as mod
    FakeToolset.instances = []
    FakeToolset.fail_probe = None
    FakeToolset.close_error = None
    monkeypatch.setattr(mod, "McpToolset", FakeToolset)
    return FakeToolset


# ---------- McpStore validation and update rules ----------

def test_update_unknown_server_is_a_key_error(state):
    with pytest.raises(KeyError):
        state.mcp.update("no-such-server", enabled=True)


def test_update_env_blank_value_removes_that_variable(state):
    state.mcp.add("envtest", "Env test", "", "http", None, [], "https://mcp.example.test", {"A": "1", "B": "2"}, enabled=False)
    s = state.mcp.update("envtest", env={"A": ""})
    assert s.env == {"B": "2"}


def test_propose_refuses_an_id_that_exists(state):
    with pytest.raises(ValueError, match="already exists"):
        state.mcp.propose("github", "Dup", "x", "npx", [])


def test_add_refuses_unknown_transport(state):
    with pytest.raises(ValueError, match="transport must be stdio or http"):
        state.mcp.add("wstest", "WS", "", "ws", None, [], None, {})


def test_openapi_proposal_needs_a_valid_id(state):
    with pytest.raises(ValueError, match="id must be lowercase"):
        state.mcp.propose_openapi("BAD ID", "x", "x", [], [])


def test_delete_unknown_and_builtin_servers_are_refused(state):
    with pytest.raises(KeyError):
        state.mcp.delete("no-such-server")
    with pytest.raises(ValueError, match="cannot be deleted|not deleted"):
        state.mcp.delete("github")


# ---------- build_toolsets ----------

def test_unknown_server_is_reported_not_raised(state, toolset, tmp_path):
    warnings = []
    out = asyncio.run(R.build_toolsets(state.mcp, ["ghost-server"], str(tmp_path), warnings))
    assert out == [] and warnings == ["MCP 'ghost-server': unknown server"]


def test_http_server_gets_bearer_and_mapped_header_values(state, toolset, tmp_path):
    state.mcp.add("remote", "Remote", "", "http", None, [], "https://mcp.example.test/sse",
                  {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_tok", "HEADER_X_API_KEY": "k-123"}, enabled=True)
    warnings = []
    out = asyncio.run(R.build_toolsets(state.mcp, ["remote"], str(tmp_path), warnings))
    assert len(out) == 1 and warnings == []
    params = toolset.instances[0].connection_params
    assert params.url == "https://mcp.example.test/sse"
    assert params.headers == {"Authorization": "Bearer ghp_tok", "X-Api-Key": "k-123"}
    assert toolset.instances[0].tool_name_prefix == "remote_"


def test_failing_probe_warns_raises_an_issue_and_closes_the_toolset(state, toolset, tmp_path):
    state.mcp.add("flaky", "Flaky", "", "http", None, [], "https://mcp.example.test/x", {}, enabled=True)
    toolset.fail_probe = ConnectionError("refused")
    toolset.close_error = RuntimeError("close also failed")          # cleanup errors are swallowed
    warnings = []
    out = asyncio.run(R.build_toolsets(state.mcp, ["flaky"], str(tmp_path), warnings))
    assert out == []
    assert warnings == ["MCP 'flaky' failed to start: ConnectionError: refused"]
    assert toolset.instances[0].closed is True
    issue = next(i for i in state.issues.list() if i["source"] == "flaky")
    assert issue["kind"] == "mcp_failed" and issue["action"] == {"type": "debug_mcp", "mcp": "flaky"}
