---
title: Security
section: Observe and protect
order: 51
summary: The OWASP scorecard for your whole system and for each agent, with fixes that open the right setting.
tags: security, scorecard, owasp, llm top 10, agentic, asi, score, fix, harden, per agent
routes: /security/
---
The **Security** page measures how well your **configuration** protects you against the OWASP Top 10 for LLM Applications (2025) and for Agentic Applications (2026). It measures configuration, not whether an attack works.

![Security: the scorecard](shot:security)

## Overall

Each risk is mapped to controls with a live status: pass, partly or missing, computed from your real settings (26 live checks). Each one says what to do about it. You can download a Markdown report.

## Per agent

Switch to **Per agent** (or open `/security/?agent=<id>`) for a ranking of your agents, weakest first, with the top issue for each, search, sort and filters. Each agent's card re-judges the same checks on **its own settings**: its guardrail choice, tools, permission mode, MCP servers, memory, knowledge bases, data sources, sandbox, provider and framework, who can call it over the API or A2A, where it runs, its identity and the remote agents it may call. A check that cannot apply to an agent is explained and left out of its score. Six extra checks are agent-only: permission mode, data sources, API and A2A exposure, runtime, framework and remote agents.

- A score badge shows in the agent list and a bar in the editor.
- Every fix link opens the editor at the right section (`/agents/?agent=<id>#ag-...`).
- Export Markdown or JSON for one agent or for all.

## What counts as evidence

The checks "agents have been attacked on purpose" and "agents are tested regularly" count **only runs that worked**. A [red-team](doc:red-team) run must be finished with at least one probe sent, and an [evaluation](doc:evaluations) run must be finished with at least one case answered. Failed, cancelled, interrupted and zero-probe runs show as "tried but did not work" with the reason, and a red-team run older than 90 days no longer counts.

## Activity

The page also lists recent security events: blocks, flags and refused hand-overs.

## Guardrails, identity, red team

- [Guardrails and identity](doc:guardrails)
- [Red team](doc:red-team)
- [Evaluations](doc:evaluations)

See [Run a security check](doc:howto-security-check).
