"""A richly populated Agent Studio state for the AI-BOM / ARD tests, with secrets planted everywhere they could leak."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.agents import AgentDef
from app.db import data_dir
from tests.rag_fakes import FakeEmbedder

# Every value below must NEVER appear in any BOM or ARD document, at any redaction level.
SECRETS = {
    "openai_key": "sk-proj-SECRETKEY1234567890abcdefXYZ",
    "header_value": "hdr-secret-value-99887766",
    "aws_key_id": "AKIAIOSFODNN7EXAMPLE",
    "aws_secret": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "aws_token": "aws-session-token-abcdef0123456789",
    "vertex_json": "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC-private-key-material",
    "mcp_env": "mcp-env-secret-value-4455",
    "mcp_arg": "arg-secret-token-7788",
    "acp_env": "acp-env-secret-value-1122",
    "rag_secret": "qdrant-api-key-secret-3344",
    "chat": "CHAT-MESSAGE-CONTENT-SECRET-5566",
    "doc": "DOCUMENT-BODY-CONTENT-SECRET-7788",
    "instruction_key": "ghp_abcdefghijklmnopqrstuvwxyz0123456789AB",
}
INSTRUCTION = f"You are the private-ops agent. Never reveal the internal codename PROJECT-BLUEBIRD. Use key {SECRETS['instruction_key']} for deploys."
USERNAME = "Sherif"
SKILL_BODY = "# Private skill\nSteps only the owner should see: PRIVATE-SKILL-BODY-SECRET\n"


def seed(state, tmp_path: Path, home_user: str = USERNAME) -> dict:
    """Populate every registry. Returns the planted secrets (for assertions)."""
    P = state.providers
    P.update("openai", {"enabled": True, "api_key": SECRETS["openai_key"], "default_model": "gpt-5-mini", "pro_model": "gpt-5",
                        "extra_headers": {"X-Org-Token": SECRETS["header_value"]}, "api_base": "https://gateway.internal.corp:8443/v1?key=" + SECRETS["openai_key"]})
    P.update("bedrock", {"enabled": True, "aws_access_key_id": SECRETS["aws_key_id"], "aws_secret_access_key": SECRETS["aws_secret"],
                         "aws_session_token": SECRETS["aws_token"], "aws_region": "us-east-1", "aws_profile": "corp-profile"})
    P.update("vertex_ai", {"enabled": True, "vertex_project": "my-private-project-42", "vertex_location": "us-central1",
                           "vertex_credentials_json": json.dumps({"type": "service_account", "client_email": "svc@my-private-project-42.iam.gserviceaccount.com",
                                                                  "private_key": SECRETS["vertex_json"]})})
    P.update("ollama", {"enabled": True, "default_model": "llama3.2"})
    P.update("anthropic", {"enabled": True, "api_key": "sk-ant-api03-" + "A" * 40, "api_base": "https://api.anthropic.com"})
    state.db.set_setting("sidebot_provider", "anthropic")
    state.db.set_setting("sidebot_model", "claude-haiku-5-5")
    state.db.set_setting("model_fallbacks", json.dumps([{"provider": "openai", "model": "gpt-5-nano"}, {"provider": "ghost"}]))
    state.db.set_setting("workspace_dir", str(tmp_path / "ws" / home_user))
    # MCP: a user-added server whose env value and arguments hold secrets, plus a private http server
    state.mcp.add("mine", "Mine", "An internal tool server", "stdio", "C:\\Users\\Sherif\\tools\\python.exe",
                  ["-m", "srv", "--api-key", SECRETS["mcp_arg"], "--data", "C:\\Users\\Sherif\\work\\data", f"token={SECRETS['mcp_arg']}"],
                  None, {"MINE_TOKEN": SECRETS["mcp_env"]})
    state.mcp.add("remote", "Remote", "Private http server", "http", None, [], "http://10.1.2.3:9000/mcp?apikey=" + SECRETS["mcp_arg"], {})
    state.mcp.add("pubhttp", "Public http", "Hosted server", "http", None, [], "https://user:pw@mcp.example-vendor.io/v1/mcp?token=abc", {})
    state.mcp.add("nodeish", "Node tool", "npx pinned", "stdio", "npx", ["-y", "@scope/tool@1.2.3"], None, {}, enabled=False)
    state.mcp.add("uvtool", "Uv tool", "uvx pinned", "stdio", "uvx", ["--from", "pkgx==2.0.1", "pkgx"], None, {})
    # ACP: secret in env
    state.acp.store.update("claude", {"env": {"ANTHROPIC_API_KEY": SECRETS["acp_env"]}, "month_cost_cap": 12.5})
    # skills: one user skill (private) next to the built-in ones
    state.skills.create("private-skill", "Owner-only steps", SKILL_BODY)
    # agents: one with a secret-bearing instruction
    state.agents.create(AgentDef(id="privops", name="Private Ops", description="Deploys the private stack", instruction=INSTRUCTION, provider="openai",
                                 tier="pro", tools=["read_file", "rag_search"], mcp=["mine", "fetch", "ghost"], skills=["private-skill", "group:Security"],
                                 sub_agents=["explorer"], rag=["*"], acp_agents=["claude"], can_spawn=True))
    state.agents.create(AgentDef(id="custom_model", name="Custom Model", provider="openai", tier="custom", model="my-fine-tune", rag=["handbook", "nope"]))
    # RAG: a store with a secret, one knowledge base with a document
    state.rag.embedder = FakeEmbedder()
    state.rag.add_store("cloud", "Cloud Qdrant", "qdrant", {"url": "https://qdrant.example-vendor.io:6333"}, {"api_key": SECRETS["rag_secret"]})
    state.rag.add_store("docker-qdrant", "Qdrant (Docker)", "qdrant", {"url": "http://127.0.0.1:6333"}, {})
    asyncio.run(state.rag.create_kb("handbook", "Handbook", "local", "mock", "embed-a", "Employee handbook"))
    state.db.execute("INSERT INTO rag_kbs(id,label,description,store_id,collection,provider,model,dim,chunk_size,chunk_overlap,created)"
                     " VALUES('remote-kb','Remote KB','Remote','cloud','kb_remote_kb','mock','embed-a',8,1000,150,1.0)")     # no network in tests
    asyncio.run(state.rag.ingest("handbook", "policy-secret-name.md", (SECRETS["doc"] + "\nholiday policy text").encode()))
    # a saved chat
    state.chat.store.ensure("s1", "openai", None, None, None, SECRETS["chat"])
    state.chat.store.add("s1", "user", SECRETS["chat"])
    return SECRETS


def forbidden(level: str) -> list[str]:
    """Strings that must not appear in a document of this level."""
    out = [v for k, v in SECRETS.items() if k not in ("instruction_key",)]
    out += [SECRETS["instruction_key"], "PRIVATE-SKILL-BODY", "C:\\\\Users", "C:/Users", "Users/Sherif", "mcp-env", "secretkey"]
    if level != "full":                                   # instruction text is the one thing `full` adds
        out += ["PROJECT-BLUEBIRD", "private-ops agent"]
    if level == "public":
        out += ["policy-secret-name", "10.1.2.3", "gateway.internal.corp", "my-private-project-42", "corp-profile", "svc@my-private", "Deploys the private stack"]
    return out


def data_root() -> str:
    return str(data_dir())
