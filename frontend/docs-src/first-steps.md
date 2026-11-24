---
title: Your first ten minutes
section: Start here
order: 2
summary: The set-up guide, step by step, from a fresh install to a first working agent.
tags: setup, install, ollama, first run, onboarding, getting started
routes: /setup/
---
The **set-up guide** is a short checklist that takes you from a fresh install to your first working agent. You are sent to it once on your first visit. You can skip any step, and you can come back to it any time from the sidebar footer (the tools icon) or with **Ctrl+K**, then "Set-up guide".

![The set-up guide with its checklist](shot:setup-guide)

## The steps

1. **Welcome and PC check.** Agent Studio looks at your memory, graphics card, Docker, Ollama and any coding agents already installed, and tells you plainly what it found.
2. **How agents are powered.** Choose local models (free, private, need a decent PC), online models (an account and a key, they cost money) or both.
3. **Ollama.** If you chose local models, install Ollama with one click (Windows). The installer is checked against its published SHA-256 before it runs.
4. **A model.** Pick a model from the library. Each size says whether it should run well on your PC. Downloads show one overall percentage and can be stopped and resumed.
5. **An online provider key** (optional). The guide sends you to Settings to paste it there. **Never paste a key into a chat.**
6. **A work folder.** The one folder your agents may read and change.
7. **Coding agents** (optional). One-click installs for Claude Code, Codex and others. See [External agents](doc:external-agents).
8. **Your first agent.** The wizard from [Agents](doc:agents) opens with a starter profile.

> **Tip:** if you skipped something, a small "Continue set-up" reminder appears until you finish or dismiss it.

## When a step fails

Every failure says in plain words what went wrong and what to do. Common ones:

- *Ollama did not start.* Open Settings, Local models, and press the start button, or start Ollama from the Windows Start menu.
- *A download stopped.* Press retry. The download resumes where it stopped.
- *No model answers.* Check that a provider is switched on in [Settings, Model providers](doc:providers).

More help: [Troubleshooting](doc:troubleshooting).
