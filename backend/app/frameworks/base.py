"""Agent frameworks behind one interface (the abstract-factory seam).

An *agent framework* is the library that actually runs an agent's loop: Google ADK (built in), or LangGraph, Strands Agents,
Pydantic AI, the OpenAI Agents SDK, Microsoft Agent Framework or the Claude Agent SDK (each installed on demand into its own
isolated environment). Agent Studio keeps everything that matters to *you* the same whichever one runs:

* the model is reached through Agent Studio's own guarded gateway (spend caps, budgets, telemetry),
* tools are Agent Studio's tools, executed in Agent Studio's sandbox through a tool bridge,
* memory, knowledge bases, guardrails, artifacts and the run record are Agent Studio's.

A framework contributes the loop (planning, tool-calling strategy, graph/state handling) and its own strengths. What each one
can and cannot do here is the capability matrix (`CAPS`), shown in the UI and used for compatibility warnings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# the capability columns, in the order the UI shows them: id -> (label, what it means)
CAPS: dict[str, tuple[str, str]] = {
    "tools": ("Tools", "Agent Studio's file, search, shell and knowledge tools (run in the sandbox)"),
    "mcp": ("MCP servers", "tools from the MCP servers attached to the agent"),
    "sub_agents": ("Sub-agents", "delegating to other agents, and agents creating agents"),
    "memory": ("Memory", "recall before a turn and saving after it (the memory tools included)"),
    "knowledge": ("Knowledge bases", "rag_search over the agent's knowledge bases"),
    "guardrails": ("Guardrails", "input, answer and tool-call checks"),
    "spend_caps": ("Spend caps", "every model call goes through the budget guard"),
    "streaming": ("Live streaming", "the answer appears word by word"),
    "artifacts": ("Artifacts", "files the agent writes are kept on the Artifacts page"),
}
LEVELS = ("yes", "partial", "no")


class FrameworkError(Exception):
    """A framework cannot be used right now; the message says what to do."""


@dataclass(frozen=True)
class FrameworkInfo:
    id: str
    label: str
    blurb: str                       # one plain sentence on what it is good at
    strengths: tuple[str, ...]       # when to pick it
    licence: str
    homepage: str
    caps: dict[str, str]             # CAPS id -> yes | partial | no
    native: bool = False             # True = runs inside Agent Studio's own process (ADK); no install
    packages: tuple[str, ...] = ()   # pip requirements (exact pins; installed with --uploaded-prior-to = today - 14 days)
    dist: str = ""                   # distribution name used to read the installed version
    module: str = ""                 # module imported to check the install
    size_mb: int = 0
    notes: str = ""
    billing: str = "Agent Studio's spend caps apply (every model call goes through its guard)."
    models: str = "any provider you configured"      # which models it can use here

    def public(self) -> dict:
        return dict(id=self.id, label=self.label, blurb=self.blurb, strengths=list(self.strengths), licence=self.licence,
                    homepage=self.homepage, caps=dict(self.caps), native=self.native, packages=list(self.packages), size_mb=self.size_mb,
                    notes=self.notes, billing=self.billing, models=self.models)


@dataclass
class RunJob:
    """Everything a framework needs to run one agent for one message (plain data: it is sent to the worker as JSON)."""
    agent_id: str
    instructions: str
    message: str
    history: list[dict] = field(default_factory=list)        # [{role: user|assistant, text}] before this message
    tools: list[dict] = field(default_factory=list)          # [{name, description, parameters (JSON schema)}] reachable through the tool bridge
    model: str = ""                                          # what the worker asks the gateway for
    max_steps: int = 25                                      # tool-call rounds the worker may take
    timeout_s: float = 600.0
    workspace: str = ""

    def public(self) -> dict:
        return dict(agent=self.agent_id, instructions_chars=len(self.instructions), history=len(self.history),
                    tools=[t["name"] for t in self.tools], model=self.model, max_steps=self.max_steps)


@dataclass
class RunOutcome:
    text: str = ""
    status: str = "ok"                # ok | error | cancelled
    error: str | None = None
    exit_code: int | None = None


EventSink = Callable[[dict], None]


class AgentFramework:
    """What every framework adapter provides. Subclasses set `info` and implement `run`."""

    info: FrameworkInfo

    @property
    def id(self) -> str:
        return self.info.id

    def ready(self) -> bool:
        raise NotImplementedError

    def compat_warnings(self, agent: Any) -> list[str]:
        """Plain-language things this agent has that this framework will not use (never raises)."""
        c, out = self.info.caps, []
        if getattr(agent, "mcp", None) and c.get("mcp") == "no":
            out.append(f"{self.info.label} does not use the MCP servers attached to this agent ({', '.join(agent.mcp)}). They are ignored.")
        if (getattr(agent, "sub_agents", None) or getattr(agent, "can_spawn", False)) and c.get("sub_agents") == "no":
            out.append(f"{self.info.label} cannot hand work to sub-agents here. The sub-agents and agent-creation setting are ignored.")
        if getattr(agent, "acp_agents", None) and c.get("sub_agents") == "no":
            out.append("External coding agents (Claude Code, Codex...) can only be called from agents that run on Google ADK.")
        if c.get("spend_caps") != "yes":
            out.append(f"{self.info.label} bills on its own account: Agent Studio's spend caps do not limit it.")
        if getattr(agent, "tools", None) and c.get("tools") == "no":
            out.append(f"{self.info.label} cannot use Agent Studio's tools.")
        if getattr(agent, "skills", None) and c.get("tools") == "no":
            out.append("Skills need tools to load, so they are ignored.")
        return out

    async def run(self, job: RunJob, session: Any, sink: EventSink, extra_env: dict | None = None) -> RunOutcome:
        raise NotImplementedError
