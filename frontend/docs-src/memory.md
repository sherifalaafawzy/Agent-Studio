---
title: Memory
section: Build
order: 38
summary: Facts agents keep across chats: kinds, scopes, the review queue, backends and how to forget.
tags: memory, remember, forget, facts, mem0, vertex memory, agentcore, scope, review
routes: /memory/
---
**Memory** is what agents keep across chats. You can read, correct or forget any of it.

![Memory: what agents remember](shot:memory)

## Kinds

Facts, past chats, people and things, how-tos, project notes and working memory.

## Scopes

Each memory belongs to one **agent**, a **user**, a **workspace** or is **global**. Narrow scopes keep an agent from learning things it should not know.

## Review queue

New memories can wait for your approval. Each one shows where it came from. You can export and import memory and forget by description ("forget everything about project X").

## Give an agent memory

Tick the kinds of memory in the [agent editor](doc:agent-editor#memory). Agents with memory on get `memory_search`, `memory_save`, `memory_forget` and `memory_notes` tools.

## Backends

- **SQLite on this PC** (default).
- A **knowledge-base vector store** for search by meaning.
- **Vertex AI Memory Bank**, **Bedrock AgentCore Memory**, or your own **Mem0** server.

Cloud backends are opt-in and say that memory text leaves your PC. They were tested against fakes only. Change the backend in Settings, Memory. See [Settings](doc:settings).
