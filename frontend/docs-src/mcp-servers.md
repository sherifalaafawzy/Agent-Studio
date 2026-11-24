---
title: MCP servers
section: Build
order: 36
summary: Add-ons that give agents new abilities; how to find, add, test and limit them.
tags: mcp, servers, tools, playwright, filesystem, git, catalog, add server, health, credentials
routes: /mcp/
---
**MCP servers** are add-ons that give agents new abilities such as browsing the web, using Git, reading files or your calendar. Each is a small program agents can call. New ones stay **off** until you approve them.

![MCP servers: the list on the left, the selected server on the right](shot:mcp-list)

## The list

Servers are grouped as **Needs setup**, **Installed**, **Built-in tools** and **Available from the catalog**. Each row has a status dot and badges: *read-only* or *can write*, and *data leaves PC*. Search, filter by state (All, Ready, Off, Needs approval) and, under **More filters**, by access and by area. Arrow keys, Home, End and Enter work in the list.

## The details

Pick a server and its details open on the right (on a phone, two screens with a Back button). Tabs:

- **Overview.**
- **Settings:** credentials, catalog settings and the panel for that server. For Playwright this holds the browser rules (keep allowed and blocked sites tight). Other servers have panels for the decision model, forecasting models, the trainer and cloud accounts.
- **Tools:** what the server offers.
- **Health and logs:** a **Test** button and the last result.

Rarely used options and **Delete** are under **Advanced**.

## Add a server

Press **Add server**. Three steps:

![Add server: catalog, your own server or an OpenAPI file](shot:mcp-add)

1. Choose the source: the **catalog**, **your own server** (a local command or a remote URL) or an **OpenAPI file**.
2. Fill in its settings. A server that can change things needs an explicit tick.
3. **Test** it.

## Credentials

Credentials are **write-only**: you can enter them but never read them back. The AI-BOM lists catalog servers (publisher, read-only kind, whether writes are allowed) and never credentials.

## Links

`/mcp/?server=<id>` opens a given server. The Catalog and the issue bar use it.

## Default servers

Filesystem, fetch, git and memory are on by default. GitHub (remote), Playwright, Brave and Context7 are one click in Settings. Servers run on this PC, as you, so approve only ones you trust.

On a phone:

![MCP servers on a phone](shot:mcp-mobile)

See [Add an MCP server](doc:howto-add-mcp) and [Catalog](doc:catalog).
