---
title: Build a voice agent with an avatar
section: How-to
order: 72
summary: Give an agent a Live profile and talk to it, on this PC or through Google Gemini Live, while watching the cost.
tags: how to, live, voice, avatar, gemini, ollama, agent, profile, budget, cost
---
**You will need:** a working agent (see [Build your first agent](doc:howto-first-agent)), a microphone, and either the local speech tools or the Google Vertex AI provider set up.

## Steps

1. Open the agent in the [advanced editor](doc:agent-editor) and go to the **Live profile** section.
2. Turn the profile on.
3. Choose the **engine**.
   - **On this PC:** free and private, a little slower to start the first time.
   - **Google Gemini Live:** needs the Vertex provider in Settings, Model providers. Audio and answers go to Google.
4. Choose **voice only** or **avatar**. For Gemini, type the avatar name (Google's own example is *Ben*).
5. Pick the voice and language.
6. Set a **budget per session**, for example $0.50.
7. Save.
8. Open **Live** in the sidebar and choose this agent. The first time on this PC, press the one-click install for the speech tools.
9. Press **Start talking** and speak. Interrupt by talking over it.

## Watch the cost

The meter shows cost so far and per minute, labelled reported, estimated or local. At 80 percent of your budget you get a warning, and at 100 percent the session stops.

> **The Gemini avatar streams video all the time the session is open**, even if nobody is speaking, at roughly 15 cents a minute at list prices. Press stop when you are finished.

## Tools

On Gemini, the agent's tools are reached through one function that runs a normal agent turn. On the local engine tick "use the agent's tools" to let it use them; it then speaks each answer once it is complete.

See [Live mode](doc:live-mode).
