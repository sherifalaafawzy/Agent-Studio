"""The agent frameworks Agent Studio can run, with exact pins and what each does here.

Every package and version was checked against PyPI on 2026-11-22: the pin is the newest release at least 14 days old (the
installer also passes `--uploaded-prior-to` so transitive packages follow the same rule). Names, licences and the quick-start
APIs the workers use were read from each project's PyPI page. Nothing is installed until the user clicks Install.
"""
from __future__ import annotations

from .base import FrameworkInfo

CHECKED = "2026-11-22"

_ALL_BRIDGED = dict(tools="yes", memory="yes", knowledge="yes", guardrails="yes", spend_caps="yes", streaming="yes", artifacts="yes",
                    mcp="no", sub_agents="no")

FRAMEWORKS: dict[str, FrameworkInfo] = {f.id: f for f in (
    FrameworkInfo(
        id="adk", label="Google ADK", native=True, licence="Apache-2.0", homepage="https://google.github.io/adk-docs/",
        blurb="The built-in engine: every Agent Studio feature works, including sub-agents, MCP servers and agents creating agents.",
        strengths=("Sub-agent teams with isolated context", "Any MCP server", "Agents that create agents", "External coding agents (Claude Code, Codex...)"),
        caps=dict(tools="yes", mcp="yes", sub_agents="yes", memory="yes", knowledge="yes", guardrails="yes", spend_caps="yes", streaming="yes", artifacts="yes"),
        notes="Runs inside Agent Studio. Nothing to install.",
    ),
    FrameworkInfo(
        id="langgraph", label="LangGraph", licence="MIT", homepage="https://www.langchain.com/langgraph",
        blurb="Stateful graphs: good when the agent's steps form a workflow with branches and loops.",
        strengths=("Explicit state and branching", "Long, resumable workflows", "Large ecosystem of LangChain integrations"),
        packages=("langgraph==1.2.20", "langchain-openai==1.7.4"), dist="langgraph", module="langgraph", size_mb=250,
        caps=dict(_ALL_BRIDGED, artifacts="partial"),
        notes="Used as a tool-calling graph (model node + tool node) over Agent Studio's gateway. Needs Python 3.11 or newer.",
    ),
    FrameworkInfo(
        id="strands", label="Strands Agents", licence="Apache-2.0", homepage="https://strandsagents.com/",
        blurb="AWS's model-driven agent loop: small, quick to read, strong on AWS and Bedrock deployments.",
        strengths=("Minimal model-driven loop", "Good fit for AWS (Bedrock, AgentCore)", "Native MCP client"),
        packages=("strands-agents[openai]==1.64.0",), dist="strands-agents", module="strands", size_mb=150,
        caps=dict(_ALL_BRIDGED),
        notes="Reaches the model through Agent Studio's OpenAI-compatible gateway, so it works with every provider.",
    ),
    FrameworkInfo(
        id="pydantic_ai", label="Pydantic AI", licence="MIT", homepage="https://ai.pydantic.dev/",
        blurb="Type-safe agents: tool arguments and results are validated, which catches malformed model output early.",
        strengths=("Validated tool arguments and outputs", "Clear errors", "Small and fast"),
        packages=("pydantic-ai-slim[openai]==1.107.10",), dist="pydantic-ai-slim", module="pydantic_ai", size_mb=120,
        caps=dict(_ALL_BRIDGED),
        notes="Pinned to 1.107.10, the newest release that is at least 14 days old (the 2.x line is newer).",
    ),
    FrameworkInfo(
        id="openai_agents", label="OpenAI Agents SDK", licence="MIT", homepage="https://openai.github.io/openai-agents-python/",
        blurb="OpenAI's agent loop with handoffs and tracing; works with any OpenAI-compatible model through the gateway.",
        strengths=("Simple agent + tools loop", "Handoffs between agents", "Built-in tracing"),
        packages=("openai-agents==0.24.0",), dist="openai-agents", module="agents", size_mb=120,
        caps=dict(_ALL_BRIDGED),
        notes="Its own tracing to OpenAI is switched off; Agent Studio's telemetry records the run.",
    ),
    FrameworkInfo(
        id="ms_agent", label="Microsoft Agent Framework", licence="MIT", homepage="https://learn.microsoft.com/agent-framework/",
        blurb="Microsoft's agent framework (successor to Semantic Kernel and AutoGen): strong for Azure and enterprise workflows.",
        strengths=("Azure and Microsoft 365 ecosystem", "Workflows and orchestration patterns", ".NET and Python parity"),
        packages=("agent-framework-core==1.24.0",), dist="agent-framework-core", module="agent_framework", size_mb=200,
        caps=dict(_ALL_BRIDGED),
        notes="Uses its OpenAI chat client against Agent Studio's gateway. Needs Python 3.11 or newer.",
    ),
    FrameworkInfo(
        id="claude_sdk", label="Claude Agent SDK", licence="MIT (the Claude Code it drives is Anthropic's)", homepage="https://docs.claude.com/en/api/agent-sdk/overview",
        blurb="Anthropic's agent harness (the engine inside Claude Code): excellent at long, careful, multi-step work, on Claude models only.",
        strengths=("Claude Code's agent loop", "Strong long-horizon reasoning", "Automatic context management"),
        packages=("claude-agent-sdk==0.2.179",), dist="claude-agent-sdk", module="claude_agent_sdk", size_mb=250,
        caps=dict(tools="yes", memory="yes", knowledge="yes", guardrails="partial", spend_caps="no", streaming="yes", artifacts="partial", mcp="no", sub_agents="no"),
        billing="Runs on your Anthropic account through the Claude Code engine: Agent Studio's spend caps do NOT limit it.",
        models="Claude models only (needs an Anthropic provider with an API key)",
        notes="Its built-in file and shell tools are switched off, because they would run on this PC outside the sandbox. It uses Agent Studio's tools instead.",
    ),
)}

ORDER = list(FRAMEWORKS)
DEFAULT = "adk"


def get(fid: str) -> FrameworkInfo | None:
    return FRAMEWORKS.get(fid)
