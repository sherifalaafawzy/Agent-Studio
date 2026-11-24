---
title: Guardrails and identity
section: Observe and protect
order: 52
summary: Profiles that check what goes into a model, what comes out and what tools are asked to do, and agent identity with short-lived tokens.
tags: guardrails, llama guard, shieldgemma, granite guardian, bedrock, azure, model armor, identity, spiffe, tokens, injection, pii
---
Everything here is off or in "record only" until you switch it on. The settings are in Settings, **Security**.

## Guardrails

A **guardrail profile** checks three things: what goes **into** a model (including tool results, the usual road for indirect prompt injection), what comes **out**, and what **tools** are asked to do.

### Kinds of check

- **Built-in rules:** personal data, secrets, injection phrases, and your own words and patterns.
- **An open-source safety model**, such as Llama Guard, ShieldGemma or Granite Guardian (or your own), run through Ollama or any OpenAI-compatible server.
- **The d1 decision model** as a gate.
- **Cloud services:** Amazon Bedrock Guardrails, Azure content safety, Google Model Armor.
- **Science safety:** the screen from the [Science Lab](doc:science-lab), usable on ordinary chats.

### Safety models download themselves

If a profile uses a safety model that your Ollama server does not have, pressing **Try it**, saving the profile or switching guardrails on starts the download. Progress, size, licence and **Cancel** are at the top of the Guardrails card. Live chat never waits for a download.

### Pick a profile for an agent

Set it in the agent editor under [Guardrails and identity](doc:agent-editor#guardrails-and-identity).

## Agent identity

Every agent has a SPIFFE-style ID, `spiffe://<trust domain>/agent/<id>`, and can be issued short-lived scoped tokens. Hand-overs between agents are checked and logged with **RFC 8693 token exchange**: the `act` claim records who acted for whom, scopes only ever shrink, and chains are limited to 3. In **enforce** mode a refused hand-over stops; otherwise it is only logged.

## What is tested

All external services here were tested against fakes. Run a [red-team](doc:red-team) or [evaluation](doc:evaluations) to see how your own setup behaves.
