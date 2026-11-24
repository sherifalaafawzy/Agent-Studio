---
title: Agent editor
section: Build
order: 21
summary: Every section of the advanced agent editor: tools, MCP, skills, knowledge, memory, access, AG-UI, Live profile, runtime and security.
tags: agent editor, advanced, tools, instructions, tier, memory, runtime, guardrails, identity, access, live profile, agui, data
routes: /agents/
---
Open an agent from the list to edit it. Sections have addresses, so a link such as `/agents/?agent=<id>#ag-live` opens the editor at that section. Changes are kept until you press save, and you are warned if you leave with unsaved changes.

![The agent editor: model tier, instructions, tools and more](shot:agents-editor)

## Basics

- **Id** (letters, digits, underscore; fixed after creation) and **Name**.
- **Model tier:** fast (quick, cheap, good for most work), strong (slower, plans better) or custom (name a specific model).
- **Provider:** blank means "inherit from the chat".
- **Description:** one line saying what the agent is good at. When a parent agent decides who to hand work to, this is how it chooses.
- **Instruction:** the standing orders. Plain sentences work best: what it is for, how it should work, what it must never do.

## Tools

Tick what the agent may do. Hover for the description. Risk labels: green only reads; "changes files" edits your workspace and undoes the edit if verification fails; "runs commands" executes inside the sandbox.

## MCP servers

Tick the [MCP servers](doc:mcp-servers) this agent may use. Servers that are off or need setup are marked.

## Skills

Tick individual [skills](doc:skills) or a whole **group**, which also includes skills you add to it later.

## Knowledge bases

Choose the [knowledge bases](doc:knowledge) to search before answering.

## Memory

Choose which kinds of [memory](doc:memory) this agent may read and write.

## Sub-agents

Tick the agents this one may hand work to. See [Agents](doc:agents#sub-agents).

## Create its own sub-agents

Lets the agent make short-lived helpers while it works. Limit per turn (4 by default). Helpers cannot make helpers and cannot get tools their creator lacks.

## Call external agents

Gives the agent a `call_external_agent` tool for the coding agents you installed, with your choice of how permission requests are handled. See [External agents](doc:external-agents).

## Access

Lets other programs use this agent through the OpenAI-compatible API or A2A. Both are off until you switch them on, need a key, and work only for agents you tick. See [API and A2A](doc:api-and-a2a).

## AG-UI

Two switches, both on by default. **Rich components** off means plain Markdown answers and shorter instructions, because the agent is no longer taught to draw charts and cards. **AG-UI stream** off stops other apps running this agent through `/api/agui` (they get a clear 403). Chat inside Agent Studio is not affected. A helper agent inherits its parent's setting. See [Rich answers](doc:rich-answers).

## Live profile

How this agent behaves when you talk to it. Choose the engine (on this PC, or Google Gemini Live), voice only or avatar, a voice, language, an avatar name and a **budget per session**. The agent keeps its own instructions, tools and knowledge. A disabled profile makes [Live](doc:live-mode) refuse the agent; an enabled one overrides the choices on the Live page.

## Data and runtime

- **Data sources:** read-only connections an agent may query. See [Settings, Data sources](doc:settings#data-sources).
- **Runtime:** where the agent runs (this PC, Docker, Vertex, Azure Foundry or AWS AgentCore). Changing it follows a plan with a shadow deploy, a health check, a traffic switch and rollback. Nothing remote is changed without your confirmation.
- **Framework:** which library runs the agent's thinking loop. Google ADK is built in; others install with one click. Models, tools, memory and limits stay Agent Studio's. See [Settings, Agents and sandbox](doc:settings#agents-and-sandbox).

## Guardrails and identity

Pick the guardrail profile that checks this agent's inputs, outputs and tool calls, and see its **agent identity** and issued tokens. See [Security](doc:security) and [Guardrails and identity](doc:guardrails).

## Security score

A bar in the editor shows this agent's score; every fix link opens the right section. See [Security](doc:security#per-agent).
