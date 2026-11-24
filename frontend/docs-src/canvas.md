---
title: Canvas
section: Work
order: 11
summary: A document, code file, table or web page you and an agent edit side by side next to the chat.
tags: canvas, edit, document, code, proposals, versions, collaborate
---
The **canvas** is a workspace that opens beside the chat. You type anywhere in it; the agent suggests changes that show highlighted, and you accept or reject each one.

## Open one

- Press the **Canvas** button above the message box.
- Press **Edit in canvas** under any answer, or **Open in canvas** on a code block.
- Ask the assistant: "open a canvas for my meeting notes". See [The assistant and these docs](doc:assistant-and-docs).
- Go to `/?canvas=latest` for your newest one, or `/?canvas=<id>` for a particular one.

## What it can hold

Markdown documents, code (Python, JavaScript, TypeScript, JSON, HTML, CSS, SQL, YAML, shell), tables (CSV) and web pages. A web page is only ever shown in a locked preview that cannot reach the rest of Agent Studio.

## Working with the agent

1. Select some text and ask a question about it, or just ask for a change.
2. The agent proposes edits. Each shows highlighted with **accept**, **reject** and **accept all**. Each proposal is checked again at the moment you accept, so a stale change cannot overwrite newer text.
3. In plan mode the agent can only propose, never change.

## Saving and versions

- The canvas **autosaves**. If someone changed it meanwhile you are asked to keep yours, take theirs or compare.
- Every change is a **version**. Open the version list to compare two versions or restore an earlier one.
- Each canvas is also kept as an [artifact](doc:artifacts).

## Layout

Drag the divider to resize it (arrow keys work too). On a phone, **Chat** and **Canvas** are two tabs and only one shows at a time.

## Settings

Settings, Agents and sandbox, Canvas lets you set limits and defaults. See [Settings](doc:settings).
