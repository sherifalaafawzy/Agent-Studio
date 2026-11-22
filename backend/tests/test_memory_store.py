"""Memory management: view, edit and delete what agents remember (the memory server's file), with backups."""
import json

import pytest

from app import memory_store as MS


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_HOME", str(tmp_path / "home"))
    p = MS.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(x) for x in [
        {"type": "entity", "name": "Alice", "entityType": "person", "observations": ["likes tea", "works on billing"]},
        {"type": "entity", "name": "Billing", "entityType": "project", "observations": ["uses Stripe"]},
        {"type": "relation", "from": "Alice", "to": "Billing", "relationType": "works_on"}]) + "\n", encoding="utf-8")
    return p


def test_list_search_and_relations(mem, client):
    ents = client.get("/api/memory/entities").json()
    assert {e["name"] for e in ents} == {"Alice", "Billing"}
    alice = next(e for e in ents if e["name"] == "Alice")
    assert alice["relations"][0]["relationType"] == "works_on"
    assert [e["name"] for e in client.get("/api/memory/entities", params={"q": "stripe"}).json()] == ["Billing"]
    s = client.get("/api/memory").json()
    assert s["entities"] == 2 and s["relations"] == 1 and s["observations"] == 3


def test_edit_delete_observation_and_entity(mem, client):
    r = client.put("/api/memory/entities/Alice", json={"observations": ["likes coffee now"]})
    assert r.status_code == 200 and client.get("/api/memory/entities", params={"q": "coffee"}).json()[0]["name"] == "Alice"
    assert client.delete("/api/memory/entities/Billing/observations/9").status_code == 404
    assert client.delete("/api/memory/entities/Nobody").status_code == 404
    assert client.delete("/api/memory/entities/Alice").json()["relations_removed"] == 1
    assert [e["name"] for e in MS.list_entities()] == ["Billing"]
    assert any(p.name.startswith("memory.backup-") for p in mem.parent.iterdir())   # every change keeps a backup


def test_clear_needs_confirmation(mem, client):
    assert client.post("/api/memory/clear", json={}).status_code == 422
    assert MS.summary()["entities"] == 2
    assert client.post("/api/memory/clear", json={"confirm": "DELETE ALL MEMORY"}).json()["cleared"] == 2
    assert MS.summary()["entities"] == 0
