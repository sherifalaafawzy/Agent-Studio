---
title: Add an MCP server
section: How-to
order: 75
summary: Add an add-on to give agents a new ability, test it, and attach it to one agent.
tags: how to, mcp, server, add, tools, playwright, credentials, approve
---
**You will need:** to know what the server will be allowed to do. Read its badges first.

## Steps

1. Open **MCP servers** and press **Add server**.
2. Choose the **catalog** (best for a first try), **your own server**, or an **OpenAPI file**.
3. Fill in the settings. If it can change things, you must tick that explicitly. Prefer a read-only setting.
4. Enter credentials in the Settings tab. They are write-only.
5. Switch the server **on**. New servers stay off until you approve them.
6. On **Health and logs** press **Test**. Read the result.
7. Open an agent in the [editor](doc:agent-editor#mcp-servers) and tick the server for that agent only.
8. Ask the agent to use it and watch the run in [Runs](doc:runs).

## Keep it tight

- Give a server only to agents that need it.
- For Playwright keep the allowed and blocked sites short.
- Servers run on your PC as you. Trust the publisher.

See [MCP servers](doc:mcp-servers).
