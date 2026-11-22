import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import State, create_app


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test gets its own data dir (user skills, default workspace) - never the real ./data."""
    monkeypatch.setenv("AGENT_STUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_STUDIO_ENABLE_MOCK", "1")       # the offline mock provider exists only for tests


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
