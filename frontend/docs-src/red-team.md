---
title: Red team
section: Observe and protect
order: 53
summary: Attack your own agents on purpose with garak, PyRIT or promptfoo, and read what worked, what failed and why.
tags: red team, garak, pyrit, promptfoo, attack, probes, jailbreak, injection, local only, email
---
The **Red team** area on the Security page points an attack tool at one of your agents and counts what gets through.

## Pick a tool

**garak**, **PyRIT** and **promptfoo**. Each installs on its own, after you accept its size and licence. Some of their dependencies have no ready-made wheel, so a short vetted list is built from source; the install says in plain words if something fails and why.

## How a run works

1. Choose a tool and one of your agents.
2. Agent Studio makes a **temporary, token-protected, OpenAI-style address** on this PC for that agent. The agent keeps its guardrails and spend limits. Probes are not kept in your chat history, and a hard probe limit applies.
3. The tool sends probes. The run page shows probes sent, how many succeeded, and the log.

## promptfoo and the e-mail question

promptfoo's own hosted attack writer asks for a **verified work e-mail** and sends your agent's purpose text to its service. Agent Studio never types, stores or submits an e-mail for you. By default promptfoo runs in **Local only** mode: your own model writes and grades the attacks through a private address on this server, and nothing is sent to promptfoo. The hosted mode needs you to verify yourself once, and the page shows the exact command.

## What counts for the scorecard

Only a run that finished with at least one probe sent. A failed, zero-probe or cancelled run is shown as "tried but did not work" with the reason. See [Security](doc:security#what-counts-as-evidence).

## If it fails

- A run that stops without a result may have been interrupted. If the backend restarts while a scan runs (for example when developing), the scan stops. Start it again.
- Read the log on the run page for the tool's own message.

More: [Troubleshooting](doc:troubleshooting).
