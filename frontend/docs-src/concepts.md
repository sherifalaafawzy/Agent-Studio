---
title: The building blocks
section: Start here
order: 3
summary: Agents, tools, MCP servers, skills, knowledge, memory and spend limits in plain words.
tags: concepts, glossary, vocabulary, agent, tool, mcp, skill, knowledge, memory, rag, provider
---
Once you know these ideas, every screen makes sense.

## Provider and model

A **provider** is the company (Anthropic, OpenAI, Google) or the program (Ollama on this PC) whose AI **model** answers. You can also connect any server that speaks the OpenAI language. See [Model providers](doc:providers). Each online provider has a **spend limit**: Agent Studio works out the worst case before each request and refuses it if it could go over. See [Billing and limits](doc:billing-and-limits).

**Auto mode** picks a provider and model for each message, cheap and fast for easy questions, stronger for hard ones, within your limits. It tells you what it chose and why.

## Agent

An agent is a model plus a **job description** (instructions), a **tier** (fast or strong), and the abilities it may use. Agents can hand work to **sub-agents**, each with its own fresh memory. See [Agents](doc:agents).

## Tools

A **tool** is one thing an agent may do: read a file, change a file, run a command, search the web. Fewer tools means fewer surprises, so give an agent only what its job needs. Tools are labelled by risk: some only read, some change files (and undo the change if it breaks), some run commands but only inside the sandbox.

## Sandbox and workspace

The **workspace** is the one folder an agent may see. The **sandbox** is a locked Docker box where commands run: no internet by default, only your workspace visible.

## MCP servers

**MCP servers** are add-ons that give agents new abilities such as browsing, Git or your calendar. New ones stay off until you approve them. See [MCP servers](doc:mcp-servers).

## Skills

A **skill** is a saved how-to guide an agent reads when it needs it, like "how to review code". See [Skills](doc:skills).

## Knowledge bases

Your documents, made searchable, so an agent looks things up and shows where each fact came from. This is often called RAG. See [Knowledge](doc:knowledge).

## Memory

Facts agents keep across chats. You can read, correct or forget any of them. See [Memory](doc:memory).

## Workflows, apps, canvas

- A **workflow** is boxes and arrows that run steps in order, with branches and approvals. See [Workflows](doc:workflows).
- An **app** is a small web page an agent builds with you that uses your agents. See [Apps](doc:apps).
- The **canvas** is a document, code file, table or web page you and an agent edit side by side. See [Canvas](doc:canvas).

## Artifacts

Files and results agents produce. Agent Studio keeps a copy. See [Artifacts](doc:artifacts).

## Guardrails and identity

**Guardrails** check what goes into a model, what comes out and what tools are asked to do. **Agent identity** gives each agent an ID and short-lived tokens so hand-overs between agents can be checked. See [Security](doc:security).

## Protocols you will see mentioned

- **AG-UI**: how an agent shows rich answers (charts, tables, maps, cards) and how other apps can follow its work live. See [Rich answers](doc:rich-answers).
- **A2A**: the open "agent to agent" language, for agents that live somewhere else. See [API and A2A](doc:api-and-a2a).
- **ACP**: how Agent Studio drives outside coding agents. See [External agents](doc:external-agents).
- **OpenTelemetry (OTLP)**: the standard trace format behind [Telemetry](doc:telemetry).

A longer list of terms is in the [Glossary](doc:glossary).
