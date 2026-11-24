---
title: Agents
section: Build
order: 20
summary: Create agents with the wizard or the advanced editor, connect remote A2A agents, and understand sub-agents.
tags: agents, wizard, create, editor, a2a, remote, sub-agents, delegation, tier, new agent
routes: /agents/
---
**Agent Studio** (the page in the sidebar) is where you design agents. An agent has a model tier, instructions, tools, MCP servers, skills, knowledge, memory and sub-agents.

![Agent Studio: the list of agents and the delegation tree](shot:agents-list)

## A seeded team

A fresh install comes with a ready-made team. **Engineer** and **Super Engineer** are strong agents that delegate to fast helpers (explorer, coder, reviewer), and there is a general assistant plus built-in App Builder, Science Lab and Security Auditor agents. Open one to see how it is set up, then copy the idea.

## Create an agent

Press **+ New**. You are asked what you want:

![The + New choice: a guided wizard or connect an A2A agent](shot:agents-new-choice)

### New agent (wizard)

The wizard takes you through these steps. You can leave and come back; it saves the same definition the advanced editor does.

1. **Purpose.** What the agent is for. Eight starter profiles are offered.
2. **Name and model.** A short name and which model tier it uses.
3. **Abilities** in plain words with risk labels (reads only, changes files, runs commands).
4. **Permissions.** What it may touch, and whether rich AG-UI answers are on. See [Rich answers](doc:rich-answers).
5. **Instructions.** Write them yourself, or press **Draft it for me**, which makes one spend-guarded model call.
6. **Review.**
7. **Try it.** Send a short message. This is a real request, so it counts toward your spend limit if you use an online model.

![The agent wizard, first step](shot:agents-wizard)

There is an **Advanced editor** button at any point. See [Agent editor](doc:agent-editor).

### Connect an A2A agent

For an agent that lives somewhere else and speaks **A2A**. Give the address of its agent card (or a link) and a bearer token or API-key header if it needs one. You see a preview of the card, can send a test message, refresh, edit or remove it, and choose which of your agents may call it. Remote agents are listed with a **Remote A2A** badge and a label that says where they run.

> **Remember:** a remote agent runs on its own servers with its own models and billing, so your spend limits do not cover it, and what you send it leaves this PC.

## Sub-agents

An agent can hand work to **sub-agents**. Each runs with its own session, so the main conversation stays tidy. The **delegation tree** on this page shows who can call whom, and with which models. An agent can also be allowed to **create its own short-lived helpers**: a helper can only be given tools its creator already has, helpers cannot create helpers, and every model call still counts toward your limits.

## The list

Each row can show badges: the model tier, a security score (see [Security](doc:security)), whether AG-UI is off, a Live profile, and a Remote A2A marker. Press a row to edit it.

## Phone

The list stacks above the editor on a narrow screen.

![The agent list on a phone](shot:agents-mobile)

More: [Build your first agent](doc:howto-first-agent), [Agent editor](doc:agent-editor).
