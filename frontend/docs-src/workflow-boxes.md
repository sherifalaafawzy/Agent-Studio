---
title: Workflow boxes
section: Build
order: 31
summary: Every kind of box in the workflow editor, grouped by what it does.
tags: boxes, nodes, workflow, trigger, agent, if, switch, loop, http, mcp, webhook, schedule, cron, notify, notification, data, filter, merge
routes: /workflows/edit/
---
The editor has **44 kinds of box** that the engine really runs, plus a sticky note. Press `/` in the editor to add one.

## Triggers

- **Task input:** the normal start. The text or details you give when you run it.
- **Webhook:** starts when something calls its secret address. Off until switched on in Workflows, Settings.
- **Schedule:** starts on its own: every N minutes, every day at a time, or on a cron pattern such as `0 9 * * 1-5` (9:00 on weekdays), in the computer's local time. Save the workflow, then press **Switch on** in the box's panel. See [Schedules](#schedules).
- **On error:** starts when another box fails and nothing caught it.

## AI

- **Agent:** asks one of your agents to do something.
- **AI prompt:** one model call with your wording.
- **Classifier:** sorts input into the categories you name.
- **Guardrail check:** applies a guardrail profile to some text. Fails if guardrails are off.

## Flow

- **If**, **Compare** and **Switch** choose a path.
- **Merge** brings paths back together.
- **Wait** pauses.
- **Ask a person** stops and asks. It can ask for details (text, number, a choice, yes or no) that continue as JSON.
- **Stop** ends the run, optionally with an error.
- **Run a workflow** calls another workflow.
- **For each**, **Sequence**, **Parallel** and **Loop** repeat or order steps.

## Data

Set fields, Text template, Parse JSON, Filter, Sort, Limit, Remove duplicates, Summarise, Split into a list, Join a list, Merge by key, Date and time, Text pattern. They reshape data without calling any outside service.

## Integrations

- **Tool:** one of an agent's tools.
- **API call:** an endpoint from an imported OpenAPI file.
- **HTTP request:** a web address you give. Public addresses only, GET unless you confirm another method, credentials kept apart.
- **MCP tool:** a tool of one of your [MCP servers](doc:mcp-servers), read-only unless the server marks the tool read-only or you confirm.
- **Memory:** search or save a [memory](doc:memory).
- **Knowledge search:** search a [knowledge base](doc:knowledge).
- **Data query:** a read-only query on one of your data sources.

## Output

- **Result:** the final answer of the run.
- **Respond to webhook:** the reply a webhook caller receives (the caller can be answered immediately, when done, or from this box).
- **Save as artifact:** keep something as an [artifact](doc:artifacts).
- **Notify me:** puts a message under the bell in the sidebar (and, if you tick it and allowed pop-ups, a desktop pop-up). Nothing is sent outside this computer: no e-mail, chat or push service.

## Notes

A **sticky note** is not run. It is only for you.

## Schedules

- A schedule runs only while Agent Studio is running. A time missed while it was closed runs **once** when it starts again, or is skipped if the box says so; never once per missed time.
- Every model call in a scheduled run uses your [spend caps](doc:billing-and-limits) like any other run. On top of that a schedule starts at most **N runs a day** (the box's setting), never starts a run while its previous one is still working, and **switches itself off after 3 failed runs in a row**. Each failure lands under the bell.
- **Workflows, Settings, "Let scheduled workflows run"** pauses every schedule at once.
- Schedules are never exported or imported: an imported workflow never starts running on its own.
- Scheduled runs show "Schedule" as what started them on the [Runs tab](doc:runs).

## Not built

A code box, on purpose: running code written into a workflow would bypass the sandbox and the read-only guards every other box goes through. Use an agent with the sandbox, or a Tool box.
