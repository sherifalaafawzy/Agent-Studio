---
title: Runs
section: Work
order: 13
summary: A timeline of what agents did in each chat: which agents worked, which tools they used, and what each step cost.
tags: runs, timeline, replay, history, trace, debug
routes: /runs/
---
**Runs** replays each chat as a timeline. Use it when you want to know why an answer came out the way it did.

![Runs: a timeline of what agents did](shot:runs)

## What you see

- Which agents worked: the main agent, built-in helpers and helpers an agent created itself.
- The task each was given, its tool calls and results, its output, and its model calls.
- What each step cost, estimated from the provider's published prices and the tokens used.

## How to use it

1. Open **Runs** in the sidebar.
2. Pick a run. The most recent are on top.
3. Open a step to see its input and output.

> **Tip:** costs for local models and external agents may show nothing, or only what the agent reported. See [Billing and limits](doc:billing-and-limits).

## Related

- Workflow runs have their own table. See [Workflows](doc:workflows#runs-tab).
- For charts, anomalies and traces across everything, see [Telemetry](doc:telemetry).
