"""Phase 7 settings: they are in the registry, export/import hands them to their owners, and the agent editor fields validate."""
from __future__ import annotations


def imp(client, settings):
    return client.post("/api/settings/import", json={"data": {"format": "agent-studio-settings", "settings": settings}, "dry_run": False}).json()


def test_runtime_settings_import_without_credentials_and_with_validation(client, state):
    r = imp(client, {"runtime_config": {"targets": {"agentcore": {"enabled": True, "region": "us-east-1", "role_arn": "arn:x"}}, "image_template": "reg.io/a:{agent}-{tag}", "secrets": {"model_env": {"K_KEY": "x"}}}})
    assert not r["errors"]
    pub = state.runtimes.public()
    assert pub["image_template"] == "reg.io/a:{agent}-{tag}" and pub["model_env_set"] == []                     # a secret in a file is ignored: credentials never travel
    bad = imp(client, {"runtime_config": {"targets": {"nowhere": {}}}})
    assert bad["errors"][0]["key"] == "runtime_config" and "unknown runtime target" in bad["errors"][0]["error"]


def test_sandbox_settings_import_but_never_switch_the_micro_vm_on(client, state):
    r = imp(client, {"sandbox_config": {"memory": "2g", "pids": 100, "microvm_enabled": True}})
    assert not r["errors"] and state.chat.sandboxes.policy()["memory"] == "2g" and state.chat.sandboxes.policy()["pids"] == 100 and state.chat.sandboxes.policy()["microvm_enabled"] is False
    assert "memory must look like" in imp(client, {"sandbox_config": {"memory": "lots"}})["errors"][0]["error"]


def test_data_products_import_but_data_source_credentials_do_not(client, state, tmp_path):
    state.data.set_source({"id": "shop", "kind": "sqlite", "config": {"path": str(tmp_path / "x.db")}})
    ok = imp(client, {"data_products": {"products": [{"id": "orders", "name": "Orders", "source": "shop", "classification": "internal"}]}, "datasource_config": {}})
    assert not ok["errors"] and [p["id"] for p in state.data.products()] == ["orders"]
    bad = imp(client, {"data_products": {"products": [{"id": "x1", "name": "n", "source": "ghost"}]}})
    assert "no data source" in bad["errors"][0]["error"]
    src = imp(client, {"datasource_config": {"sources": [{"id": "other"}]}})
    assert "credentials cannot travel" in src["errors"][0]["error"]


def test_the_registry_describes_the_new_settings(client):
    reg = {s["key"]: s for s in client.get("/api/settings/registry").json()["settings"]}
    for key, tab in (("runtime_config", "runtimes"), ("runtime_secrets", "runtimes"), ("sandbox_config", "harness"), ("datasource_config", "data"), ("datasource_secrets", "data"), ("data_products", "data")):
        assert reg[key]["tab"] == tab and reg[key]["effect"]
    assert reg["runtime_secrets"]["secret"] and reg["datasource_secrets"]["secret"] and "value" not in reg["runtime_secrets"] or reg["runtime_secrets"]["value"] is None


def test_framework_instructions_mention_data_sources(state):
    from app.agents import AgentDef
    from app.frameworks import turn as T
    state.chat.data = state.data
    text = T.instructions_for(state.chat, AgentDef(id="x", name="x", data=["db"]), type("T", (), {"recall_text": ""})(), [])
    assert "## Data sources" in text and "data_query" in text
