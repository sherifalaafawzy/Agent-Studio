---
name: ai-bom-and-discovery
group: Security
description: Explain, interpret and help use Agent Studio's AI bill of materials (CycloneDX 1.7) and its Agentic Resource Discovery (ARD) publishing. Use when asked what models, dependencies, licences, agents, MCP servers or data flows the system contains, for an audit, a supply-chain or compliance question, how to export or validate the BOM, or how other tools can discover the agents.
---

# AI-BOM and Agentic Resource Discovery

Agent Studio can describe itself in two standard ways. Neither is something you run or change: point the user to the
**Discovery & AI-BOM** page (sidebar > AI-BOM) and to **Settings > Discovery & AI-BOM**.

## The AI bill of materials
- Format: **CycloneDX 1.7** JSON. Preview, validate and download it on the Discovery page, or write it from a terminal:
  `python -m app.aibom --level public --out agent-studio.cdx.json --validate` (run from the `backend` folder; exit status 1
  means it failed validation).
- It lists: Python and npm libraries (versions, purls, licences, npm integrity hashes), models (hosted models with their
  configured price; local models with the pinned Hugging Face revision, licence and SHA-256 of every weight file), agents
  as services with their tools, skills, MCP servers, sub-agents, knowledge bases and external agents, MCP servers with
  their pinned packages, skills (content hash), knowledge bases and vector stores (hashes and counts, never content),
  Docker images, and the dependency graph between them. Properties named `agentstudio:*` carry the Agent Studio facts.
- **Scope**: the whole system, or one agent, MCP server, model or skill with everything it depends on.
- **Redaction levels**: `public` (safe for anyone: no instruction text, no private addresses, no cloud account ids, no
  document names), `internal` (adds those, for your own organisation), `full` (adds agent instruction text). At every level:
  never API keys, tokens, passwords, environment values, chats, document contents, local paths or user names.

## Answering questions from it
- "Which models do we use / what do they cost?" -> `machine-learning-model` components; price properties are USD per million tokens.
- "Does data leave the machine?" -> provider services carry `agentstudio:data-leaves-machine`; local Ollama and local d1 / time-series models do not.
- "What does agent X depend on?" -> cut the BOM to scope `agent:<id>` and follow `dependencies`.
- "Which licences?" -> `licenses` on each component. Hosted models have none recorded: say so, do not guess.
- External coding agents (Claude Code, Codex, Aider, OpenHands) are **not under Agent Studio's spend caps**.

## What it cannot tell you (say so plainly)
No hashes for Python wheels (installed metadata has none), no Docker image digests (pinned tags only), no weights or
training data for hosted models, no dependencies of MCP packages fetched at run time, no installed version of external
harnesses, and **no vulnerability data**. Do not invent any of these; suggest `pip-audit` / `npm audit` or the Snyk and
Semgrep servers for vulnerabilities.

## Publishing (Agentic Resource Discovery, spec v0.91, a proposal)
- Off by default. The user must name a domain they control, choose what to publish and a redaction level. Then a small
  read-only server (127.0.0.1 only) serves `/.well-known/ard.json`, the BOMs and skill files. Making it reachable needs the
  user's own domain, tunnel or web host: never try to set that up or expose anything yourself.
- The catalog points to BOMs through each entry's `trustManifest.attestations`, with the file's sha256.
- Signing is optional (detached JWS, EdDSA). ARD defines no verification procedure, so a signature only shows that the key
  holder signed that entry. Never describe entries as "verified" or "trusted".
- Never publish, push the BOM to Dependency-Track, or change these settings on the user's behalf: describe the steps and let them.
