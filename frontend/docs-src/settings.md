---
title: Settings
section: Reference
order: 90
summary: A map of every Settings tab and what to find there, with search, reset, export and import.
tags: settings, configuration, tabs, providers, local models, auto, harness, sandbox, voice, assistant, memory, security, artifacts, runtimes, data sources, backup
routes: /settings/
---
**Settings** is where everything is switched on and tuned. Every setting is described once in the app's settings registry, and a full generated list is in `docs/SETTINGS.md` in the repository.

![Settings: model providers](shot:settings-providers)

## Using the page

- The **search box** finds a setting by name or by what it does.
- Each setting has a **reset** button.
- If you leave with unsaved changes you are warned.
- **Backup and history** exports and imports settings (with a preview), resets everything, and shows what changed. **Secrets are never exported or imported.** An import can never switch on webhooks, publishing or similar and never lowers a safety level.

Tabs have addresses, such as `/settings/#providers`.

## Model providers

Switch providers on, add keys, set limits and models. See [Model providers](doc:providers) and [Billing and limits](doc:billing-and-limits).

## Local models

Install Ollama in one click, browse the model library (with a note on whether each size should run on your PC), pull, stop and retry downloads, and set the decision model.

## Auto

What reads your messages in Auto mode (built-in rules or a local d1 decision model), cost versus quality, which providers it may use, and a box to **try a message** and see the routing without spending anything.

## Agents and sandbox

- **Workspace folder** and **sandbox** (a hardened Docker container, or the experimental micro VM).
- **Agent frameworks:** install and pick the library that runs an agent's loop.
- **Harness rules:** permission modes (full, accept edits, plan), a loop guard, your own block-or-remind hooks, checkpoints with rewind, `AGENTS.md` loading, web tools (off by default).
- **Canvas**, **Apps** (including whether server apps may run), **Rich components** (maps basemap and more).

## External agents

Install, test and configure coding agents. See [External agents](doc:external-agents).

## Voice

Live mode defaults: engine, avatar, end-of-speech wait, longest session, cost limit, and the price table. See [Live mode](doc:live-mode).

## Telemetry

The OpenTelemetry connector and the OCSF security export. See [Telemetry](doc:telemetry).

## Assistant

Switch the assistant on, choose its provider and model, see its exact prompt, add your own guidance after the locked safety rules, switch individual actions off, and choose whether it may see what is on your screen. See [The assistant and these docs](doc:assistant-and-docs).

## Discovery and AI-BOM

Domain, what to publish, signing and push. See [AI-BOM](doc:ai-bom).

## Memory

The memory backend and its rules. See [Memory](doc:memory).

## Security

Guardrail profiles, agent identity and the science safety level. See [Guardrails and identity](doc:guardrails).

## Artifacts

Storage backends, rules, secret scan and retention. See [Artifacts](doc:artifacts).

## API and A2A

Turn on the OpenAI-compatible API and A2A, and manage keys. See [API and A2A](doc:api-and-a2a).

## Runtimes

Where an agent can run (Docker, Vertex, Azure Foundry, AWS AgentCore) and their cloud credentials, which are write-only.

## Data sources

Read-only connections: SQLite, PostgreSQL, MySQL or MariaDB, Databricks, Snowflake, BigQuery and Salesforce. A SQL guard, read-only sessions, row, size and time limits, optional masking of personal data and an audit log. Tick the sources each agent may read in its editor.

## Backup and history

Export, import, reset and a list of what changed.
