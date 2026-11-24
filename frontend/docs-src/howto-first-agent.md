---
title: Build your first agent
section: How-to
order: 70
summary: From nothing to a working agent with the wizard, in about five minutes.
tags: how to, first agent, wizard, tutorial, create, build an agent, getting started
---
**You will need:** at least one model working. Either Ollama with a model, or an online provider with a key. See [Your first ten minutes](doc:first-steps).

## Steps

1. Open **Agent Studio** in the sidebar and press **+ New**.
2. Choose **New agent (wizard)**.
3. **Purpose:** pick the starter profile that is closest to what you want, or describe it in your own words.
4. **Name and model:** a short name. Choose **fast** unless the work is hard planning.
5. **Abilities:** tick only what the job needs. Read the risk labels. A reading-only agent is the safest place to start.
6. **Permissions:** keep the defaults unless you know you need more.
7. **Instructions:** press **Draft it for me** and read what it wrote. This is one model call. Change anything you disagree with. Good instructions say what the agent is for, how it should work and what it must never do.
8. **Review**, then **Try it**. Send a short message and judge the answer.

> **Tip:** an online model costs money for the "Try it" message. A local model is free.

## Then

- Improve the instructions until the answers are right. Open the agent in the [advanced editor](doc:agent-editor).
- Give it knowledge: [Build an agent that knows your documents](doc:howto-knowledge-agent).
- Check its score on the [Security](doc:security#per-agent) page.
- Talk to it: [Build a voice agent with an avatar](doc:howto-voice-avatar-agent).

## If the answer is poor

Try a stronger tier, tighten the instructions, give it the right knowledge base, or look at the run in [Runs](doc:runs) to see what it actually did.
