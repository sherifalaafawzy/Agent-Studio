---
title: External agents
section: Work
order: 15
summary: Hand a big job to a coding agent you already use, such as Claude Code or Codex, and answer its questions from Agent Studio.
tags: external, acp, claude code, codex, aider, openhands, cursor, antigravity, grok, coding agent
routes: /external/
---
Agent Studio can drive separate coding assistants through the **Agent Client Protocol (ACP)**. It starts the program, streams what it writes and does, and answers what it asks: permissions, questions, plan approvals and file reads or writes.

![External coding agents](shot:external)

## Which agents

Claude Code, OpenAI Codex, Cursor, Antigravity and Grok Build speak ACP. Aider and OpenHands have no usable ACP, so Agent Studio runs their command-line versions in the background behind the same tool. Any other ACP program can be added in Settings, External agents. Claude Code was tested live end to end; the others were set up from their documentation but not run here.

## Install and connect

1. Open the [set-up guide](doc:first-steps) or Settings, External agents.
2. Press the install button for the agent. Installs try pinned npm packages, uv, winget and the official registry first, and the vendor's own script last, which needs a ticked confirmation next to the exact command.
3. Sign in once with the vendor's own login (for example `claude` login), or put its key in that agent's own environment settings.
4. Press **Test connection**. For the command-line agents this only runs `--version`, with no model call.

## Use one

Pick the agent in chat or call it from one of your agents with the `call_external_agent` tool. Choose how permission requests are handled:

- **Ask for everything** (default).
- **Auto-allow reads, ask the rest.**
- **Read-only**, which refuses anything that is not a read.

Network fetches are never auto-allowed.

## Read this first

- **Spend limits do not cover these agents.** They call their own models on their own accounts. Agent Studio records what they report and caps turns per session (30 by default). Set real limits with each vendor.
- Their own shell and tools run **as you**, unsandboxed, as in a terminal. Only file access they delegate to Agent Studio is jailed to the workspace.
- They get a minimal environment with none of Agent Studio's provider keys.
- Modes such as `bypassPermissions` remove the prompts entirely, and the screen warns you when you select one.

See also [Security](doc:security) and [Billing and limits](doc:billing-and-limits).
