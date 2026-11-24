---
title: API and A2A
section: Reference
order: 93
summary: Let other programs use your agents through an OpenAI-compatible API, A2A, or AG-UI, with scoped keys.
tags: api, openai compatible, a2a, ag-ui, keys, access, agent card, scoped, rate limit
---
Other programs can use your agents. All of it is **off until you switch it on**, needs a **key**, and reaches only agents that you opted in.

## OpenAI-compatible API

Each agent looks like a model to any OpenAI client: `/v1/models` and `/v1/chat/completions`, with streaming. Turn it on in Settings, **API and A2A**, create a key and tick the agents it may use.

## A2A 1.0

Each opted-in agent has an agent card at `/a2a/<agent>/.well-known/agent-card.json`, using the official `a2a-sdk`. Other agents can call yours with a key. You can also connect to remote A2A agents. See [Agents](doc:agents#connect-an-a2a-agent).

## AG-UI stream

`POST /api/agui` runs a chat turn and streams it as AG-UI events, including text, reasoning, tool calls and rich components. An agent's AG-UI stream switch controls this per agent. See [Rich answers](doc:rich-answers).

## Keys

- Shown **once**, stored only as a hash.
- **Scoped** to named agents, **rate-limited**, **revocable** and **rotatable**.
- Each [app](doc:apps) gets its own key limited to the agents in its manifest.

## Per agent

The agent editor's **Access** section chooses whether that agent is reachable this way. See [Agent editor](doc:agent-editor#access).
