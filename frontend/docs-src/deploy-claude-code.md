---
title: Deploy an agent to Claude Code
section: Build
order: 22
summary: Copy an agent into Claude Code as a subagent, or let Claude Code call the live agent. You see every file first and nothing is written until you confirm.
tags: claude code, cli, deploy, export, subagent, mcp, skills, terminal, live agent, .claude
---
Claude Code is Anthropic's coding assistant for the terminal. You can take an Agent Studio agent there in two ways.

![The Deploy to Claude Code dialog: what should happen, and where](shot:agents-deploy-claude)

## Two ways to deploy

- **Copy the agent into Claude Code.** Agent Studio writes a Claude Code *subagent* file (`.claude/agents/<name>.md`) with the agent's name, description, instructions, the tools Claude Code can run and the model. The skills the agent names are copied to `.claude/skills/`, and its MCP servers are added to Claude Code's MCP settings. After that the agent runs on Claude Code's own engine, with no link back to Agent Studio.
- **Let Claude Code call the live agent.** The agent keeps running in Agent Studio. Claude Code gets a small relay subagent and an MCP server (`/agent-mcp/<agent>`) with one tool, `ask`. Because Agent Studio runs the agent, its guardrails, memory, spend caps and run record all still apply.

## Steps

1. Open **Agents**, select the agent and press **Deploy to Claude Code…** (in the editor, or under the agent in the list).
2. Choose **what** should happen and **where**: one project folder (its `.claude/` folder, good to share with a team) or **All my projects** (your user folder, `~/.claude/`).
3. Press **Preview**. Read every file, open **Show content** or **Show changes**, and read **Not carried over**.
4. Tick **Replace it** only for files you really want replaced. A replaced file is first copied to Agent Studio's data folder.
5. Press **Confirm**. For all-projects MCP servers you can copy the `claude mcp add-json` commands, or tick **Run these commands for me**.
6. Start Claude Code in the project. Subagent files are picked up within a few seconds.

## What is copied, and what is not

| In Agent Studio | In Claude Code |
| :- | :- |
| Name, description, instructions | The subagent's name, description and prompt |
| Tools such as `read_file`, `edit_file`, `run_shell` | `Read`, `Edit`, `Bash` and so on |
| Skills the agent names | Skills in `.claude/skills/`, preloaded when there are 8 or fewer |
| MCP servers | `.mcp.json`, or `claude mcp add-json ... --scope user`, or inside the agent file |
| Sub-agents | Their own subagent files, listed in the parent's prompt |
| Provider on Anthropic (pro / flash) | `opus` / `sonnet`; any other provider uses your Claude Code model |

These have no counterpart and are listed in the preview as **Not carried over**: guardrails, memory, knowledge bases (RAG), data sources, workflows, the canvas, Apps and Science tools, AG-UI rich components, the Live profile, the agent's identity and external (ACP) agents. An agent that uses them is better deployed as a **live agent**.

## Safety

- **Preview first, then confirm.** The preview is built again when you confirm. If the agent or a file changed in between, nothing is written and you are asked to preview again.
- **Nothing is overwritten silently.** New files are created; existing ones change only when you tick them. Other servers in your `.mcp.json` stay exactly as they are.
- **No secrets are written.** Credentials stored for an MCP server become `${NAME}` references you set in your own environment. Text in instructions or skills that looks like a key is masked and reported.
- **Only inside the folder you chose.** Paths that lead elsewhere (for example through a link) are blocked.
- **Servers that only exist inside Agent Studio** (the sandbox, the trainer and similar) are left out, with the reason.

## Letting Claude Code call a live agent

1. In the agent editor tick **API access (OpenAI-compatible)**, and switch the API on in **Settings > API & A2A**.
2. In the Deploy dialog choose **Let Claude Code call the live agent** and preview.
3. Press **Create a key for this agent**. The key is shown once, here. Copy it and set it as `AGENT_STUDIO_KEY` in the environment you start Claude Code from. It is never written to a file.
4. Confirm. Ask Claude Code to *use the `<name>-live` subagent*.

The endpoint accepts only the keys of the API, refuses requests from web pages, and answers one request at a time through the normal chat engine. It does not see your files in Claude Code: the relay passes only the text of each request.

## Good to know

- Claude Code asks for your approval the first time it uses a project's `.mcp.json` servers, and the first time a subagent uses a tool.
- Claude Code starts MCP servers in the background, so a one-shot `claude -p` run may begin before they are ready.
- An agent with no tool Claude Code can run gets the read-only file tools (`Read`, `Glob`, `Grep`) so that it can start.
- See [API and A2A](doc:api-and-a2a) for keys, and [the agent editor](doc:agent-editor) for the agent's settings.
