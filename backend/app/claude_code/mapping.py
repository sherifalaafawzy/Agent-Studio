"""What an Agent Studio agent turns into inside Claude Code (the CLI).

Claude Code has its own tool set (Read, Edit, Bash ...), its own skills and its own MCP servers. Agent Studio's tool names are
translated where there is a real equivalent; everything else is listed as "not carried over" so nobody is surprised.
Frontmatter format: https://code.claude.com/docs/en/sub-agents (checked 2026-11-23: only `name` and `description` are required,
`tools` is a comma-separated string or a list, `skills` preloads skills, `mcpServers` scopes MCP servers to the subagent).
"""
from __future__ import annotations

# Agent Studio tool -> Claude Code tool
TOOL_MAP: dict[str, str] = {
    "read_file": "Read", "list_dir": "Glob", "glob_files": "Glob", "search_text": "Grep",
    "write_file": "Write", "edit_file": "Edit", "run_shell": "Bash",
    "web_fetch": "WebFetch", "web_search": "WebSearch", "todo_write": "TodoWrite",
}
# Tools that are handled by another frontmatter field instead of the `tools` list.
VIA_FIELD = {"load_skill": "Skills it uses are copied and preloaded with the `skills` field."}

# Why a tool, or a whole feature, has no counterpart in Claude Code (shown in the preview).
TOOL_WHY: dict[str, str] = {
    "repo_map": "Claude Code has no project map tool; it uses Read, Glob and Grep instead.",
    "memory_notes": "Claude Code keeps project notes in CLAUDE.md, which Agent Studio does not write.",
}
GROUP_WHY: list[tuple[str, str]] = [
    ("rag_", "Knowledge-base search (RAG) only exists inside Agent Studio."),
    ("data_", "Data-source tools only exist inside Agent Studio."),
    ("memory_", "Agent Studio's memory (facts, people, how-tos) is not available in Claude Code."),
    ("canvas_", "The canvas is an Agent Studio screen."),
    ("app_", "The Apps builder is part of Agent Studio."),
    ("science_", "Science Lab tools are part of Agent Studio."),
]
SINGLE_WHY: dict[str, str] = {
    "save_artifact": "Artifacts are an Agent Studio screen; files it writes stay in the project folder instead.",
    "create_skill": "Authoring skills inside Agent Studio is not carried over.",
    "propose_mcp_server": "Agent Studio's approval queue for new MCP servers does not exist in Claude Code.",
    "propose_openapi_mcp": "Agent Studio's approval queue for new MCP servers does not exist in Claude Code.",
    "render_ui": "Rich answer components (AG-UI) are drawn by Agent Studio's chat, not by Claude Code.",
    "ui_catalog": "Rich answer components (AG-UI) are drawn by Agent Studio's chat, not by Claude Code.",
    "run_workflow": "Workflows run inside Agent Studio.",
    "call_remote_agent": "A2A delegation is done by Agent Studio; use the live-agent export to keep it.",
}


def why_not(tool: str) -> str:
    if tool in TOOL_WHY:
        return TOOL_WHY[tool]
    if tool in SINGLE_WHY:
        return SINGLE_WHY[tool]
    for prefix, why in GROUP_WHY:
        if tool.startswith(prefix):
            return why
    return "No Claude Code tool does this."


def translate_tools(tools: list[str]) -> tuple[list[str], list[dict]]:
    """(Claude Code tool names in a stable order, [{item, why}] for the ones that have no equivalent)."""
    out: list[str] = []
    lost: list[dict] = []
    for t in tools:
        if t in TOOL_MAP:
            if TOOL_MAP[t] not in out:
                out.append(TOOL_MAP[t])
        elif t in VIA_FIELD:
            continue
        else:
            lost.append(dict(item=t, why=why_not(t)))
    return out, lost


# Agent Studio features with no counterpart at all (reported once per agent when the agent uses them).
def feature_losses(agent) -> list[dict]:
    lost: list[dict] = []
    if agent.guardrails != ["none"]:
        lost.append(dict(item="guardrails", why="Agent Studio's input and output guardrails do not run in Claude Code (Claude Code has its own permissions and hooks)."))
    if agent.memory:
        lost.append(dict(item="memory", why="Agent memory is stored by Agent Studio and is not carried over."))
    if agent.rag:
        lost.append(dict(item="knowledge bases", why="Knowledge-base search (RAG) only exists inside Agent Studio."))
    if agent.data:
        lost.append(dict(item="data sources", why="Data-source tools only exist inside Agent Studio."))
    if agent.acp_agents:
        lost.append(dict(item="external agents (ACP)", why="Calling other coding agents is done by Agent Studio; Claude Code uses its own subagents."))
    if agent.can_spawn:
        lost.append(dict(item="creating sub-agents at run time", why="Claude Code subagents are files; they cannot be created by the agent while it runs."))
    if agent.identity:
        lost.append(dict(item="agent identity and scopes", why="Agent Studio's agent identity (tokens, scopes, delegation) is not carried over."))
    if (agent.live or {}).get("enabled"):
        lost.append(dict(item="Live profile", why="Voice and avatar live mode is part of Agent Studio."))
    if (agent.agui or {}).get("components", True):
        lost.append(dict(item="AG-UI rich components", why="Charts, tables, maps and cards are drawn by Agent Studio's chat, not by Claude Code; answers are plain text there."))
    if agent.runtime != "local":
        lost.append(dict(item=f"runtime ({agent.runtime})", why="The agent will run in Claude Code, not in that runtime."))
    if agent.framework != "adk":
        lost.append(dict(item=f"framework ({agent.framework})", why="The agent will run on Claude Code's own engine."))
    return lost
