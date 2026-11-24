---
title: Workflow boxes
section: Build
order: 31
summary: Every kind of box in the workflow editor, grouped by what it does.
tags: boxes, nodes, workflow, trigger, agent, if, switch, loop, http, mcp, webhook, data, filter, merge
routes: /workflows/edit/
---
The editor has **42 kinds of box** that the engine really runs, plus a sticky note. Press `/` in the editor to add one.

## Triggers

- **Task input:** the normal start. The text or details you give when you run it.
- **Webhook:** starts when something calls its secret address. Off until switched on in Workflows, Settings.
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

## Notes

A **sticky note** is not run. It is only for you.

## Not built

A schedule trigger (there is no scheduler yet), a code box, and a separate "notify me" box (use Save as artifact).
