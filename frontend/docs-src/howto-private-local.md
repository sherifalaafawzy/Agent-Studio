---
title: Run everything locally and privately
section: How-to
order: 76
summary: Use only models and tools on this PC so nothing you type leaves it.
tags: how to, local, private, offline, ollama, privacy, no cloud, data leaves
---
**You will need:** Ollama with a model, and the speech tools if you want voice.

## Steps

1. Finish the [set-up guide](doc:first-steps) with **local models** only. Install Ollama and pick a model that your PC can run (each size says how it should run).
2. In Settings, Model providers, switch **off** online providers you do not use.
3. In Settings, Auto, make sure Auto uses only the local provider, or pin the model in chat.
4. For voice, use the **On this PC** engine in [Live](doc:live-mode), not Gemini.
5. For guardrails use built-in rules or a safety model on your own Ollama. See [Guardrails](doc:guardrails).
6. Leave maps on the offline outline and do not add a tile address. See [Rich answers](doc:rich-answers).
7. Leave artifact storage on **a folder on this PC**.
8. Leave publishing and OCSF sinks off.

## Check

Look for "data leaves this PC" badges. Each MCP server, provider, storage and engine says whether data leaves. The AI-BOM also shows what is on. See [AI-BOM](doc:ai-bom).
