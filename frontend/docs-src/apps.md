---
title: Apps
section: Build
order: 32
summary: Small web apps that an agent builds with you, in Simple or Advanced mode, with a live preview, versions, themes and one-click connections to your agents.
tags: apps, builder, studio, preview, simple, advanced, templates, export, git, lovable, app builder
routes: /apps/
---
An **app** is a small project (a page, or a page plus a tiny Node or Python server) that calls *your* agents. Agent Studio builds it with you.

![Apps: your apps and templates](shot:apps-list)

## Two ways to make one

- **Simple:** describe the app, attach files (a spec, sample data, a picture), pick the agent it should use, and the built-in **App Builder** agent writes it while you watch.
- **Advanced:** start from a standard template, edit the files in the editor, and review each change the builder suggests as a diff to accept or reject.

Press **New app** and choose.

## The studio

Every app opens in a studio. The **Build** tab has the builder chat beside a live preview. On a narrow screen they become tabs.

- **Plan first.** The builder writes a plan (pages, features, steps) before it touches a file. You see it and watch the steps get ticked off. You can dismiss it but not edit it by hand.
- **Small edits.** It starts from a template (chat front-end, form, dashboard, data table, Node or Python server) and then changes files with small exact-text patches, not whole rewrites.
- **It checks its own work.** The preview reports script errors, rejected promises, console errors, failed files and blocked code. After each change the studio reads those reports plus a read-only scan of the files. If something is broken it sends the builder exactly what is wrong. It tries at most three times in a row, then stops and tells you. You can switch this off, and **Stop** works.
- **Click and change.** Press **Pick an element**, click anything in the preview, and say what to change. The builder is told the element's selector, HTML and look.
- **Versions.** Every change is a version: undo (and undo the undo), compare any two, restore.
- **Design tab:** eight colour themes in light and dark, four fonts, three corner styles, and ready-made accessible parts (navbar, hero, stats, table with CSV, chart, tabs, modal, toast, form, empty state), all plain files in your app.
- **Connect tab:** one click lists one of your agents (local or cloud) or data sources in the app's manifest. A page reads a data source only through `DataClient.query` (SELECT only, row-limited, audited, only the sources its manifest lists). Turning on an agent's API access is its own explicit click that the builder never does. Data hookup works for static pages.
- **Files tab:** the files beside the preview.
- **Model per app:** "Best available" (the first ready provider in a fixed ranking) or a provider and model you pick.

> **The preview and your browser.** The preview is a sandboxed frame on a separate local port. Some embedded browsers block it and show a blank page. Open Agent Studio in a normal browser such as Chrome if the preview is blank.

## Safe by construction

- The builder writes only inside the app's folder, as text, and cannot run anything.
- A page runs in an iframe with `sandbox="allow-scripts"` served from a separate loopback port under a strict Content-Security-Policy, so it cannot reach the rest of Agent Studio.
- It calls your agents through a proxy with a short-lived signed token. Only agents listed in its manifest are allowed, and the app's own key (limited to those agents, never "all") stays on the server.
- Text that looks like a secret is refused in files, exports and pushes.
- **Server apps** run in a hardened Docker container. A published port needs an open outbound network, so they are **off until you allow them** in Settings, Apps.
- A table's **Download CSV** is blocked inside the sandboxed preview; **Copy CSV** and opening in a new tab work.

## Export and git

- Download a **zip** (without `.env` files or anything `.gitignore` excludes).
- Start a **git repository** in the app folder, commit, and push or pull to a remote you set. Nothing is pushed or pulled without a confirmation, and the remote's token is write-only.
- **Deploy plan** shows the exact `docker` commands. Agent Studio does not deploy apps to a cloud runtime itself.

## Use an app in the system

An app's own key can call the agents in its manifest. **Rotate** the key to get a new one to use outside Agent Studio (shown once), or **revoke** it to stop the app calling agents.

See [Build an app for your agent](doc:howto-app-for-agent).
