---
title: Telemetry
section: Observe and protect
order: 50
summary: A record of what your agents did, with costs, anomalies and traces, and how to export it.
tags: telemetry, traces, opentelemetry, otlp, anomalies, cost, errors, spans, export, ocsf
routes: /telemetry/
---
**Telemetry** records what your agents did: each step, tool and model call, how long it took and what it cost. It stays on this PC unless you choose to forward it.

![Telemetry overview](shot:telemetry)

## The overview

- **KPIs** and **cost per day.**
- **Anomalies:** cost spikes, loops, slow tools and repeated failures.
- **Top** agents, models and tools.
- **Plain-language errors**, not stack traces.
- **Run comparison**, and **Explain this run** (by rules, with no model call).
- **Saved views** and **CSV or JSON export.**

## Traces

Agent Studio stores spans for every Agent Studio agent (turn, then agent, then each model and tool call, with sub-agents kept as a tree) and one span per turn and tool call of each external agent. External agents are started with the OpenTelemetry settings pointing at a built-in receiver (`POST /v1/traces`, `/v1/metrics`, `/v1/logs`, on this PC only, OTLP over HTTP with JSON).

## Security export (OCSF)

Settings, Telemetry can send Agent Studio's events as **OCSF 1.10.0** to a rotating file, an HTTPS webhook or Splunk HEC, an S3 bucket or syslog. **Prompts and answers are never sent.** Cloud sinks were tested against fakes only.

## Related

[Runs](doc:runs) for a per-chat timeline, [Security](doc:security) for the scorecard, [Billing and limits](doc:billing-and-limits) for spend.
