---
title: Workflows
section: Build
order: 30
summary: Boxes and arrows that run steps in order, with branches, approvals, webhooks, an OpenAPI importer and a table of every run.
tags: workflows, flows, editor, openapi, webhook, runs, approval, canvas, boxes, automation, n8n
routes: /workflows/
---
A **workflow** is a set of boxes joined by arrows. Each box does one thing (ask an agent, call an API, decide, wait for a person) and passes its result on. The Workflows page has three tabs: **Flows**, **OpenAPI steps** and **Runs**.

![Workflows: the Flows tab](shot:workflows-flows)

## Flows tab

A table of your workflows with search, last run, and actions: duplicate, rename, export, import (with clash handling), and delete (with a warning when another workflow uses it). Tick several and press **Export selected** for one file. **Import** accepts one workflow or a file of several.

## Start a new workflow

Press **New workflow**, give it a name, and choose how to start:

![New workflow: build it yourself, talk to an agent, or start from a template](shot:workflows-new)


- **Build it myself** in the editor, starting from a blank canvas.
- **Talk to an agent** that proposes the workflow in your own words. It shows exactly what would change and nothing happens until you press **Apply**. It can never tick "I confirm", create keys or run anything.
- **Start from a template.**

## The editor

The editor is full screen.

![The workflow editor: palette on the left, canvas in the middle, settings on the right](shot:workflows-editor)

- **Palette:** press `/` to add a box. See [Workflow boxes](doc:workflow-boxes) for all of them.
- **Canvas:** drag boxes, draw arrows from the dots on the right of one box to the left of another. Hold **Shift** to select several; copy and paste them together with their connections. **Tidy up** arranges left to right. The minimap and zoom controls follow your light or dark theme.
- **Right panel:** the selected box's **Settings**, **Input**, **Output** and **Notes**, with Text, JSON and Table views and an option to **pin** the output so test runs reuse it.
- **Undo and redo**, autosaved drafts, saved **versions** with labels.
- **Run drawer:** run it, see each box light up, answer approvals.

### Expressions

Anywhere a box takes text you can use `{{ }}` expressions to read earlier results, in the style of n8n. They are evaluated by a safe built-in evaluator, never by running code. There is no code box.

### When something goes wrong

Each box can **stop the run**, **carry on with an empty result**, or **follow its error output**. You can also set retries and a time limit, switch a box off, or pin its output. An **On error** box starts when something fails and nothing else caught it; the run still ends as failed.

### Try one box

**Test this box** runs one box with text you type (a box that could change something only runs with **Allow side effects** ticked). **Run from here** starts at a box and reuses the last run for everything before it.

## OpenAPI steps tab

Import an API so every endpoint becomes a box.

![Workflows: the OpenAPI steps tab](shot:workflows-openapi)

1. Choose **Upload**, **URL** or **Paste** and give the OpenAPI description.
2. Review the endpoints. Switch off ones you do not want, rename them, give them default values, or add your own by hand. Endpoints from the description can be hidden but not deleted (a refresh brings them back); ones you add can be deleted.
3. Add a key if the API needs one. It is stored but never shown again.
4. Use the endpoint as a box. Only that API's own server is ever called. Anything that changes data needs **allow writes** on the API and **I confirm** on the box.

See [Build a workflow from an API](doc:howto-workflow-from-api).

## Runs tab

Every run of every workflow in one table: result, what started it (you, a webhook, an agent, or "from a box"), when, how long, how many boxes ran, failed or were skipped, cost and tokens, and the workflow version.

![Workflows: the Runs tab](shot:workflows-runs)

- **Search** by task, error or workflow. **Filter** by workflow, result, trigger, time, and **Needs you** (a run waiting for an approval). Sort by when, time taken, cost or result.
- **Open a run** to see what it was given, the workflow as it was drawn when it ran with each box's status, every box's input, output, error and time, the log and the result. The address `/workflows/?tab=runs&run=<id>` opens one directly.
- **Actions:** Run again, Run again from the failed box, Run again from this box, Stop, answer a waiting approval right there, Export JSON, Open in editor, Delete (one or many).
- **Secrets never show:** anything that looks like a key or password is replaced on the server before it is shown.
- Only the latest runs of each workflow are kept (a setting).

## Webhooks

Switch the start of a workflow to **Webhook**, save a version, then press **Make the address**. The address is shown **once**; only a hash is kept. Webhooks are **off** until you switch them on in Workflows, Settings (an import never switches them on). Calls are rate-limited and size-limited.

## Safety defaults

- The **HTTP request** box calls public addresses only; a private host must be allowed in Workflows, Settings. It uses GET unless you pick another method and tick **I confirm**. Credentials are kept apart from workflows and never read back.
- An **MCP tool** box that its server does not mark read-only needs **I confirm**.
- **Data query** uses the same read-only guard as agents.
- A **Guardrail check** fails instead of passing when guardrails are off.
