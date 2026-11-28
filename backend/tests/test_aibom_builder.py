"""The AI-BOM generator against a fully populated state: structure, validation, redaction, scoping, determinism."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app import d1_spec, ts_spec
from app.aibom import builder as B
from app.aibom.redact import LEVELS
from app.aibom.validate import validate_bom
from tests.aibom_helpers import SECRETS, forbidden, seed


@pytest.fixture
def rich(state, tmp_path):
    seed(state, tmp_path)
    return state


def by_ref(bom: dict) -> dict:
    return {x["bom-ref"]: x for x in [*bom.get("services", []), *bom["components"]]}


def p(item: dict, name: str):
    return next((x["value"] for x in item.get("properties", []) if x["name"] == f"agentstudio:{name}"), None)


# ------------------------------------------------------------------------------------------------ validity
@pytest.mark.parametrize("level", LEVELS)
def test_every_level_validates_against_the_official_cyclonedx_schema(rich, level):
    bom = B.build_full(rich, level)
    v = validate_bom(bom)
    assert v["ok"], v["errors"][:5]
    assert bom["bomFormat"] == "CycloneDX" and bom["specVersion"] == "1.7" and bom["serialNumber"].startswith("urn:uuid:")
    assert bom["metadata"]["lifecycles"] == [{"phase": "operations"}] and bom["metadata"]["component"]["name"] == "Agent Studio"
    assert any("limitations" in x["name"] for x in bom["metadata"]["properties"])


@pytest.mark.parametrize("level", LEVELS)
def test_no_secret_user_name_or_absolute_path_in_any_level(rich, level):
    text = json.dumps(B.build_full(rich, level), ensure_ascii=False) + B.serialize(B.build_full(rich, level)).decode()
    for needle in forbidden(level):
        assert needle not in text, f"{needle!r} leaked at level {level}"
    # no scoped document leaks either
    full = B.build_full(rich, level)
    for ref in B.scope_roots(full)["agents"] + B.scope_roots(full)["mcp"]:
        assert not any(n in json.dumps(B.cut(full, ref)) for n in forbidden(level))


def test_instruction_text_only_at_full_and_still_masked(rich):
    pub, internal, full = (by_ref(B.build_full(rich, lvl))["agent:privops"] for lvl in LEVELS)
    for a in (pub, internal):
        assert p(a, "instruction-text") is None and p(a, "instruction-length") == str(len(__import__("tests.aibom_helpers", fromlist=["x"]).INSTRUCTION))
        assert len(p(a, "instruction-sha256")) == 64
    text = p(full, "instruction-text")
    assert "PROJECT-BLUEBIRD" in text and SECRETS["instruction_key"] not in text and "<redacted>" in text


def test_public_hides_private_endpoints_accounts_and_user_text_internal_shows_hosts_not_secrets(rich):
    pub, internal = B.build_full(rich, "public"), B.build_full(rich, "internal")
    rp, ri = by_ref(pub), by_ref(internal)
    assert "endpoints" not in rp["mcp:remote"] and ri["mcp:remote"]["endpoints"] == ["http://10.1.2.3:9000/mcp"]       # query (the key) dropped
    assert rp["mcp:pubhttp"]["endpoints"] == ["https://mcp.example-vendor.io/v1/mcp"]                                   # public host kept, userinfo/query dropped
    assert "endpoints" not in rp["provider:openai"] and ri["provider:openai"]["endpoints"] == ["https://gateway.internal.corp:8443/v1"]
    assert p(rp["provider:vertex_ai"], "vertex-project") is None and p(ri["provider:vertex_ai"], "vertex-project") == "my-private-project-42"
    assert p(rp["provider:openai"], "endpoint-class") == "private"
    assert "description" not in rp["agent:privops"] and ri["agent:privops"]["description"] == "Deploys the private stack"
    assert "description" in rp["agent:explorer"]                                                                         # built-in text always shown
    assert "skill:private-skill" not in rp and "skill:private-skill" in ri
    assert p(rp["acp:claude"], "monthly-cost-cap-usd") is None and p(ri["acp:claude"], "monthly-cost-cap-usd") == "12.5"
    # secrets are named but never valued
    assert json.loads(p(ri["mcp:mine"], "env-names")) == ["MINE_TOKEN"] and SECRETS["mcp_env"] not in json.dumps(internal)
    assert json.loads(p(ri["mcp:mine"], "args")) == ["-m", "srv", "--api-key", "<redacted>", "--data", "<redacted>".replace("<redacted>", "<path>/data"), "token=<redacted>"] \
        or "<redacted>" in p(ri["mcp:mine"], "args")
    assert p(rp["mcp:mine"], "args") is None                                                                            # a user's own server: no arguments at public


# ------------------------------------------------------------------------------------------------ contents
def test_components_services_and_graph(rich):
    bom = B.build_full(rich, "internal")
    refs = by_ref(bom)
    # libraries from both ecosystems, with the data that is really known
    eco = {p(c, "ecosystem") for c in bom["components"] if c["type"] == "library"}
    assert eco == {"pypi", "npm"}
    npm = next(c for c in bom["components"] if c["bom-ref"].startswith("pkg:npm/next@"))
    assert npm["hashes"][0]["alg"] == "SHA-512" and len(npm["hashes"][0]["content"]) == 128 and npm["licenses"]
    assert all("hashes" not in c for c in bom["components"] if p(c, "ecosystem") == "pypi")           # none invented
    assert any(c["name"] == "fastapi" and c["purl"].startswith("pkg:pypi/fastapi@") for c in bom["components"])
    # models: hosted ones with prices, the embedding model, a custom fine-tune
    gpt = refs["model:openai:gpt-5-mini"]
    assert gpt["type"] == "machine-learning-model" and p(gpt, "hosted") == "true" and float(p(gpt, "price-input-usd-per-million-tokens")) > 0
    assert set(json.loads(p(gpt, "roles"))) >= {"default"}
    assert "embedding" in json.loads(p(refs["model:mock:embed-a"], "roles")) and refs["model:mock:embed-a"]["modelCard"]["modelParameters"]["task"] == "feature-extraction"
    assert "model:openai:my-fine-tune" in refs and p(refs["model:ollama:llama3.2"], "hosted") == "false"
    assert "agent" in json.loads(p(refs["model:openai:gpt-5"], "roles"))
    assert "assistant" in json.loads(p(refs["model:anthropic:claude-haiku-5-5"], "roles")) and "fallback" in json.loads(p(refs["model:openai:gpt-5-nano"], "roles"))
    # agents: graph edges to provider, model, MCP, skills, sub-agent, external agent, knowledge bases
    dep = {d["ref"]: d["dependsOn"] for d in bom["dependencies"]}
    privops = dep["agent:privops"]
    for x in ("provider:openai", "model:openai:gpt-5", "mcp:mine", "mcp:fetch", "skill:private-skill", "agent:explorer", "acp:claude", "kb:handbook", "kb:remote-kb"):
        assert x in privops, x
    assert "mcp:ghost" not in privops and "kb:nope" not in dep["agent:custom_model"] and "kb:handbook" in dep["agent:custom_model"]
    assert any(x.startswith("skill:") and x != "skill:private-skill" for x in privops)                  # group:Security resolved
    assert p(refs["agent:privops"], "provider") == "openai" and p(refs["agent:explorer"], "provider") == "chat-selected"
    # MCP packages are pinned components; local modules depend on the app
    assert "pkg:npm/%40scope/tool@1.2.3" in dep["mcp:nodeish"] and refs["pkg:npm/%40scope/tool@1.2.3"]["scope"] == "optional"
    assert "pkg:pypi/pkgx@2.0.1" in dep["mcp:uvtool"] and "agent-studio" in dep["mcp:code-sandbox"]
    # knowledge bases: hashes only
    kb = refs["kb:handbook"]
    assert kb["type"] == "data" and kb["hashes"][0]["alg"] == "SHA-256" and kb["components"][0]["hashes"][0]["alg"] == "SHA-256"
    assert kb["components"][0]["name"].endswith("policy-secret-name.md") and p(kb, "documents") == "1"
    assert "components" not in by_ref(B.build_full(rich, "public"))["kb:handbook"]                          # document names only from internal
    assert dep["kb:handbook"] == ["model:mock:embed-a", "store:local"] and "store:cloud" in dep["kb:remote-kb"]
    assert p(refs["store:cloud"], "location") == "public" and p(refs["store:local"], "location") == "local file"
    # docker: a registered docker store pulls in its pinned image; the sandbox image has its base image
    assert "pkg:docker/qdrant/qdrant@v1.20.0" in dep["store:docker-qdrant"] and "pkg:docker/agent-studio-sandbox@1" in dep["agent-studio"]
    assert "pkg:docker/python@3.12-slim" in dep["pkg:docker/agent-studio-sandbox@1"]
    # coding harnesses: not under our caps, version not queried, pinned adapters
    claude = refs["acp:claude"]
    assert p(claude, "external-to-spend-caps") == "true" and p(claude, "installed-version") == "not queried"
    assert any(x.startswith("pkg:npm/%40agentclientprotocol/claude-agent-acp@") for x in dep["acp:claude"])
    assert any(x.startswith("pkg:pypi/aider-chat@") for x in dep["acp:aider"]) and any(x.startswith("pkg:pypi/openhands@") for x in dep["acp:openhands"])
    assert any(x.startswith("pkg:npm/%40openai/codex@") or x.startswith("pkg:npm/%40agentclientprotocol/codex-acp@") for x in dep["acp:codex"])
    # skills: content hash
    sk = refs["skill:private-skill"]
    assert sk["type"] == "file" and sk["hashes"][0]["content"] == B.sha256_hex(SKILL_BODY_TEXT := rich.skills.get("private-skill").body)
    del SKILL_BODY_TEXT


def test_dependency_graph_has_no_dangling_references_and_is_sorted(rich):
    bom = B.build_full(rich, "internal")
    known = set(by_ref(bom)) | {bom["metadata"]["component"]["bom-ref"]}
    refs = [d["ref"] for d in bom["dependencies"]]
    assert len(refs) == len(set(refs)) and set(refs) <= known
    for d in bom["dependencies"]:
        assert set(d["dependsOn"]) <= known and d["dependsOn"] == sorted(d["dependsOn"])
    assert [c["bom-ref"] for c in bom["components"]] == sorted(c["bom-ref"] for c in bom["components"])


# ------------------------------------------------------------------------------------------------ local models
def test_installed_local_models_carry_pinned_revision_and_weight_hashes(rich, monkeypatch):
    monkeypatch.setattr(d1_spec, "ready_models", lambda h=None: ["d1-2b"])
    monkeypatch.setattr(ts_spec, "installed_ids", lambda h=None: ["chronos-2", "ghost-model"])
    bom = B.build_full(rich, "public")
    refs = by_ref(bom)
    sp = d1_spec.MODELS["d1-2b"]
    d1 = refs[f"pkg:huggingface/LiquidAI/d1-2B@{sp.revision}"]
    assert d1["version"] == sp.revision and d1["licenses"][0]["license"]["name"] == "LFM Open License v1.0" and p(d1, "runs-locally") == "true"
    assert d1["components"] and all(len(f["hashes"][0]["content"]) == 64 for f in d1["components"])
    assert any(f["name"].endswith("model.safetensors") for f in d1["components"])
    ts = next(c for c in bom["components"] if c["name"] == "chronos-2")
    assert ts["licenses"][0]["license"]["name"] == "Apache-2.0" and p(ts, "commercial") == "yes"
    dep = {d["ref"]: d["dependsOn"] for d in bom["dependencies"]}
    assert d1["bom-ref"] in dep["mcp:d1"] and ts["bom-ref"] in dep["mcp:timeseries"]
    assert any(x.startswith("pkg:pypi/transformers@") for x in dep[d1["bom-ref"]]) and any(x.startswith("pkg:pypi/chronos-forecasting@") for x in dep[ts["bom-ref"]])
    assert validate_bom(bom)["ok"]
    assert not any(c["type"] == "machine-learning-model" and "huggingface" in c["bom-ref"] for c in B.build_full(rich, "public")["components"]) is False


def test_uninstalled_local_models_are_not_listed(rich):
    assert not any(c["bom-ref"].startswith("pkg:huggingface/") for c in B.build_full(rich, "public")["components"])


def test_auto_routing_settings_and_engine_model(rich, monkeypatch):
    monkeypatch.setattr(d1_spec, "ready_models", lambda h=None: ["d1-2b"])
    rich.auto.set_config({"engine": "d1-2b", "preference": "cost", "allow": ["openai"], "tier_models": {"flash": "openai/gpt-5-nano", "pro": "anthropic/claude-opus-5-5"}})
    bom = B.build_full(rich, "internal")
    app = bom["metadata"]["component"]
    assert p(app, "auto-routing-engine") == "d1-2b" and p(app, "auto-routing-preference") == "cost" and json.loads(p(app, "auto-routing-allow")) == ["openai"]
    dep = {d["ref"]: d["dependsOn"] for d in bom["dependencies"]}
    assert any("d1-2B" in x for x in dep["agent-studio"]) and "model:openai:gpt-5-nano" in dep["provider:openai"]
    assert p(B.build_full(rich, "public")["metadata"]["component"], "auto-routing-allow") is None
    del rich.auto
    assert "auto-routing-engine" not in json.dumps(B.build_full(rich, "public")["metadata"]["component"])


def test_auto_config_errors_never_stop_the_bom(rich):
    class Broken:
        def config(self):
            raise RuntimeError("boom")
    rich.auto = Broken()
    assert validate_bom(B.build_full(rich, "public"))["ok"]


# ------------------------------------------------------------------------------------------------ scoping and determinism
def test_scoped_boms_contain_only_what_the_resource_depends_on(rich):
    full = B.build_full(rich, "internal")
    roots = B.scope_roots(full)
    assert "agent:privops" in roots["agents"] and "mcp:mine" in roots["mcp"] and "skill:private-skill" in roots["skills"]
    assert any(m.startswith("model:") for m in roots["models"])
    one = B.cut(full, "agent:privops")
    assert validate_bom(one)["ok"]
    refs = by_ref(one)
    assert "agent:privops" in refs and "mcp:mine" in refs and "kb:handbook" in refs and "agent:custom_model" not in refs
    assert len(one["components"]) < len(full["components"]) // 2
    assert one["dependencies"][0] == {"ref": "agent-studio", "dependsOn": ["agent:privops"]}
    assert p(one["metadata"], "scope") == "agent:privops" and p(one["metadata"], "redaction-level") == "internal"
    model_only = B.cut(full, "model:openai:gpt-5")
    assert "services" not in model_only and validate_bom(model_only)["ok"]
    skill_only = B.cut(full, "skill:private-skill")
    assert [c["bom-ref"] for c in skill_only["components"]] == ["skill:private-skill"]
    assert B.build_bom(rich, "internal", "mcp:mine")["metadata"]["properties"][1]["value"] == "mcp:mine"
    with pytest.raises(ValueError, match="unknown scope"):
        B.cut(full, "agent:nobody")
    with pytest.raises(ValueError, match="unknown scope"):
        B.build_bom(rich, "public", "agent-studio")


def test_prune_when_the_root_depends_on_the_application(rich):
    full = B.build_full(rich, "public")
    one = B.cut(full, "mcp:code-sandbox")                       # a module of Agent Studio itself: its dependency is the application
    assert validate_bom(one)["ok"] and one["dependencies"][0]["dependsOn"] == ["mcp:code-sandbox"]
    assert sum(1 for d in one["dependencies"] if d["ref"] == "agent-studio") == 1


def test_stable_serial_and_content_hash(rich):
    t1, t2 = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc)
    a, b = B.build_full(rich, "public", True, t1), B.build_full(rich, "public", True, t2)
    assert a["serialNumber"] == b["serialNumber"] and a["metadata"]["timestamp"] != b["metadata"]["timestamp"] and B.content_hash(a) == B.content_hash(b)
    assert B.build_full(rich, "public", False)["serialNumber"] != a["serialNumber"]
    rich.mcp.add("another", "Another", "x", "stdio", "npx", ["-y", "zz@1.0.0"], None, {})
    assert B.build_full(rich, "public", True, t1)["serialNumber"] != a["serialNumber"]
    assert B.build_full(rich, "internal")["serialNumber"] != a["serialNumber"]
    assert B.serialize(a) == B.serialize(json.loads(B.serialize(a)))


def test_publisher_becomes_the_supplier_and_levels_are_checked(rich):
    assert B.build_full(rich, "public", publisher="agents.acme-corp.dev")["metadata"]["supplier"]["name"] == "agents.acme-corp.dev"
    assert "supplier" not in B.build_full(rich, "public")["metadata"]
    with pytest.raises(ValueError, match="unknown redaction level"):
        B.build_full(rich, "secret")


def test_empty_services_and_a_broken_workspace_setting_are_survivable(state, monkeypatch):
    def boom(_):
        raise OSError("no workspace")
    monkeypatch.setattr(state.chat, "workspace_for", boom)
    assert validate_bom(B.build_full(state, "public"))["ok"]


def test_helpers(monkeypatch, tmp_path):
    assert B.prop("x", None) is None and B.prop("x", []) is None and B.prop("x", False)["value"] == "false" and B.prop("x", {"b": 1, "a": 2})["value"] == '{"a": 2, "b": 1}'
    assert B.props(some_name=3) == [{"name": "agentstudio:some-name", "value": "3"}]
    assert B.clean_id("a/b c") .startswith("a-b-c-") and B.clean_id("ok.id-1") == "ok.id-1" and B.clean_id("///") .startswith("x-")
    assert B.purl_docker("qdrant/qdrant:v1.20.0") == ("qdrant/qdrant", "v1.20.0", "pkg:docker/qdrant/qdrant@v1.20.0") and B.purl_docker("redis")[2] == "pkg:docker/redis@latest"
    assert B.app_version() == "0.1.1"
    monkeypatch.setattr(B.Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert B.app_version() == "0.0.0"


def test_missing_dockerfile_and_npx_without_a_pin(rich, monkeypatch):
    monkeypatch.setattr(B, "REPO_ROOT", tmp_path_missing := __import__("pathlib").Path("Z:/nowhere"))
    bom = B.build_full(rich, "public")
    refs = by_ref(bom)
    assert "pkg:docker/agent-studio-sandbox@1" in refs and "pkg:docker/python@3.12-slim" not in refs
    rich.mcp.add("unpinned", "Unpinned", "x", "stdio", "npx", ["-y", "left-pad"], None, {})
    assert not any(d["ref"] == "mcp:unpinned" and d["dependsOn"] for d in B.build_full(rich, "public")["dependencies"])
    del tmp_path_missing


def test_acp_listing_failure_is_survivable(rich, monkeypatch):
    monkeypatch.setattr(rich.acp.store, "list", lambda: (_ for _ in ()).throw(RuntimeError("probe failed")))
    bom = B.build_full(rich, "public")
    assert not any(x["bom-ref"].startswith("acp:") for x in bom["components"]) and validate_bom(bom)["ok"]


def test_store_config_is_masked_by_name_and_shown_only_internally(rich):
    rich.rag.add_store("pg", "Postgres", "pgvector", {"host": "db.corp.example-vendor.io", "port": "5432", "database": "kb", "user": "agent"}, {"password": "pg-pass-secret-1"})
    rich.rag.add_store("pine", "Pine", "pinecone", {"index_host": "idx-abc.svc.pinecone.io"}, {"api_key": "pc-key-secret-2"})
    pub, internal = B.build_full(rich, "public"), B.build_full(rich, "internal")
    cfg = json.loads(p(by_ref(internal)["store:pg"], "config"))
    assert cfg["host"] == "db.corp.example-vendor.io" and "pg-pass-secret-1" not in json.dumps(internal) and "pc-key-secret-2" not in json.dumps(internal)
    assert p(by_ref(pub)["store:pg"], "config") is None and by_ref(internal)["store:pine"]["endpoints"] == ["https://idx-abc.svc.pinecone.io"]
    assert p(by_ref(pub)["store:pg"], "location") == "public"


def test_odd_settings_and_stores_never_break_the_bom(rich):
    rich.db.set_setting("model_fallbacks", "{not json")
    rich.rag.add_store("docker-ghost", "Ghost stack", "qdrant", {"url": "http://127.0.0.1:6399"}, {})              # a docker- store with no known stack
    bom = B.build_full(rich, "public")
    assert validate_bom(bom)["ok"] and "store:docker-ghost" in by_ref(bom)
    assert not any(c.get("purl", "").startswith("pkg:docker/ghost") for c in bom["components"])


def test_pins_ignore_what_is_not_an_exact_pin(rich):
    b = B.Build(rich, "public")
    assert b.pins(["torch>=2", "numpy", "", "ok==1.0"], "test") == ["pkg:pypi/ok@1.0"]


def test_a_system_with_nothing_registered_still_gives_a_valid_bom(state, monkeypatch):
    monkeypatch.setattr(B.Build, "run", lambda self: None)
    bom = B.build_full(state, "public")
    assert "services" not in bom and validate_bom(bom)["ok"] and bom["dependencies"] == [{"ref": "agent-studio", "dependsOn": []}]
