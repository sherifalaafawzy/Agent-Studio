---
title: AI-BOM and discovery
section: Observe and protect
order: 55
summary: A bill of materials of your whole AI system in a standard format, and an optional discovery catalog you can publish.
tags: ai-bom, cyclonedx, ard, discovery, redaction, signing, dependency-track, bill of materials
routes: /discovery/
---
Agent Studio can describe itself in standard formats. **Everything is read-only until you turn publishing on.**

![Discovery and AI-BOM](shot:discovery)

## The AI-BOM

**Discovery and AI-BOM** previews, validates (against the official CycloneDX 1.7 schema) and downloads a bill of materials of the whole system or of one agent, MCP server, model or skill. It lists Python and npm dependencies (versions, package URLs, licences, npm integrity hashes), hosted and local models (pinned Hugging Face revision, licence, SHA-256 of weight files), agents and their graph, MCP servers, skills, knowledge bases (hashes only), vector stores, Docker images and data flows. It is discovered from the live system, so new things appear on their own.

### Redaction

Choose **public** (the default), **internal** or **full** (adds agent instruction text). At every level: no keys, tokens, passwords, environment values, chats, document contents, local paths or user names.

### What is not in it

Hashes of Python wheels, Docker image digests, training data and licences of hosted models, dependencies of MCP packages fetched at run time, installed versions of outside harnesses, and vulnerability data. The document says so inside.

## Discovery publishing (ARD)

In Settings, Discovery and AI-BOM: name a domain you control, pick what to publish and a level. A separate read-only server (127.0.0.1 port 8766) serves `/.well-known/ard.json`, the BOMs and skill files, or you can export everything as a zip and host it yourself. Agent Studio never exposes anything itself; put your own domain, tunnel or web host in front. Levels other than public need an access token.

## Signing and push

- **Signing** (optional): a detached JWS signature, with the key kept in the data folder. It shows who signed; nothing is called "verified".
- **Push** (optional) to Dependency-Track, only on request, after you review the exact payload.

From a terminal: `cd backend && .venv\Scripts\python -m app.aibom --level public --out bom.json --validate`.
