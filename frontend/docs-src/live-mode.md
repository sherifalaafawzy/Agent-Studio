---
title: Live mode
section: Work
order: 12
summary: Talk to a model out loud, voice only or with an avatar, on this PC or through Google Gemini Live, with latency and cost readouts.
tags: live, voice, speech, avatar, gemini, whisper, kokoro, microphone, latency, cost, barge-in
routes: /live/
---
**Live** lets you talk to a model by voice. Choose **Voice only** or **With avatar**, and pick one of two engines.

![Live mode: voice only or with an avatar, with the latency and cost readouts](shot:live-page)

## The two engines

| | On this PC | Google Gemini Live |
|---|---|---|
| Listens with | Whisper | Gemini Live (model `gemini-3.8-live`) |
| Thinks with | Any model you have, for example an Ollama model | Gemini Live |
| Speaks with | Kokoro | Gemini Live voices |
| Avatar | A face drawn in your browser, lip-synced to the voice, works offline | Google's Live Avatar video |
| Leaves this PC | No | Yes: audio and answers go to Google Cloud |
| Cost | Free | Google bills your project; Agent Studio ends the session at your limit |

## Start a session

1. Open **Live** in the sidebar.
2. Pick the engine, then **Voice only** or **With avatar**.
3. The first time on this PC, press the one-click install. It sets up the speech tools in their own folder, with pinned versions and checked files.
4. Press **Start talking** and speak. Press **Interrupt**, or just talk over it, to cut the answer off. You can also type and press **Send**.

> **Tip:** the browser asks for the microphone once. If you refused, allow it in the address bar and reload.

## Barge-in

Talking over the assistant stops the model and the voice, throws away audio already queued, and keeps in the history only what was actually spoken. The next question works like the first.

## Latency

Each answer shows every stage and the **first sound after you stop**, measured in the browser. On the local engine the biggest piece you control is the short wait that makes sure you have finished speaking (a setting, 350 ms by default). The **speed test** speaks a sentence, plays it back as the microphone and times three answers, so you can test without talking. Local engines were measured at roughly 0.7 to 0.9 seconds from the end of your speech to the first sound on a machine with a modern GPU. Gemini voice was roughly 1.4 seconds in the browser, and the avatar's first video chunk 2 to 3 seconds. Your numbers will differ.

## Live cost

A running meter shows the cost so far, cost per minute, elapsed time, tokens, audio seconds, video seconds and a breakdown by stage. Each number says whether it is **reported** (Gemini's own usage figures), **estimated** (marked with ≈), **mixed** or **local** (usage shown, no cloud cost). The prices live in an editable table labelled "prices as of <date>, verify", with a button that re-reads Google's pricing page.

> **Important:** the Gemini avatar streams video the whole time a session is open, even while nobody speaks, at roughly 15 cents a minute at list prices. Close the session when you are done.

### Budget limits

Set a cost limit per session or per agent. At 80 percent you get a warning. At 100 percent the session stops with a clear message.

## Give an agent a voice

Any agent can have a **Live profile**: engine, voice or avatar mode, voice, language, avatar name and a budget per session. With a profile, Live runs that agent with its own instructions, tools and knowledge. On Gemini the agent's tools are reached through one function that runs a normal agent turn. On the local engine you can switch on "use the agent's tools", which speaks each answer after it is complete. See [Agent editor](doc:agent-editor#live-profile) and [Build a voice agent with an avatar](doc:howto-voice-avatar-agent).

## History

The Live page keeps a list of past sessions with their cost. Gemini sessions also add rows to the spend ledger, so [Billing](doc:billing-and-limits) shows them.

## Settings

Settings, Voice holds the defaults: engine, avatar, the end-of-speech wait, the longest session (10 minutes by default) and the cost limit ($1 by default).

## Set up Gemini

Gemini Live uses the Google Vertex AI provider you already set in Settings, Model providers (its project, region and sign-in). An API key alone is not accepted. See [Model providers](doc:providers).

## If something goes wrong

- *No sound at all.* Check the browser's sound output and that the page may use the microphone. Press **Start talking** again to start a fresh session.
- *"Gemini ended that turn without replying."* This happens occasionally on Google's side. Say it again.
- *The speech tools will not install.* The error says why. Retry after checking your internet and disk space.
