import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import State, create_app


@pytest.fixture
def state(tmp_path):
    return State(Database(tmp_path / "t.db"))


@pytest.fixture
def client(state):
    return TestClient(create_app(state))
