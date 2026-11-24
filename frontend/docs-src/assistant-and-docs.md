---
title: The assistant and these docs
section: Reference
order: 92
summary: How the side assistant uses what is on your screen, what it can do for you, how its edits are approved, and the documentation shortcuts.
tags: assistant, side agent, docs, documentation, search, shortcut, ctrl+/, page context, approval, privacy, highlight, guide, canvas
routes: /docs/
---
The **assistant** is the helper in the corner of the screen. It explains screens, takes you places, points at things, opens this documentation beside you and, only if you approve, fills in forms.

![The assistant panel open beside the screen](shot:assistant-panel)

## Open it

Press the **Assistant** button in the corner. Its **Docs** tab shows this documentation without leaving your page.

### Keyboard

| Keys | What it does |
|---|---|
| **Ctrl+/** (or Cmd+/) | Open or close the documentation panel |
| **?** (when no text box is selected) | Open the documentation panel |
| **Ctrl+K** | Jump to any page or chat |

## The documentation panel

![The documentation open in the side panel, on the MCP servers screen](shot:docs-panel)

- **Search** across every page, offline. It forgives plurals and small typos.
- **Help for this screen** opens the page that explains where you are.
- **Open full page** takes the same page to the full Documentation screen, with a page tree and an "on this page" list.
- Links inside the docs open inside the panel; links to screens of the app take you there.

## What the assistant can see

On each question it reads the screen you are on: the route, the page title and headings, the form fields (label, type and current value), buttons, tabs, selected items and error messages. Pages can also describe themselves, for example which agent is open.

**It never sees secrets.** Password fields and any field whose name or label looks like a key, token, password, credential or similar are listed as "kept private" with no value. Text it reads on a page is treated as information, never as instructions.

What you have typed into ordinary fields is part of that description, so it goes to the assistant's model. If that is an online model, it leaves this PC; if it is a local Ollama model it does not. You can switch screen reading off in Settings, Assistant. Then it only knows which page you are on.

## What it can do

| Tool | Does | Needs approval |
|---|---|---|
| Navigate | Open a page of the app, optionally at a tab or section | No |
| Start a tour | Run one of the guided tours | No |
| Explain | Highlight one thing and explain it | No |
| Guide me | Walk you through up to eight things, one card after another | No |
| Read the screen | Re-read what is on the screen | No |
| Open docs / Search docs | Show a documentation page, or search | No |
| Set a field / Fill a form | Put values into form fields | **Yes, every time** |
| Open canvas | Create a new canvas and open it | No |

Each of these can be switched off in Settings, Assistant, and a switched-off action is refused on the server too.

## How an edit is approved

1. The assistant proposes a value for one or more fields.
2. A card in the assistant panel shows **each field before and after**.
3. **Apply** puts the values in, the way you would type them. **Not now** does nothing.
4. The screen's own **Save** is still yours to press.

Fields that hold secrets, and read-only fields, cannot be changed. If a field disappeared, the proposal is dropped with a reason.

## Make the assistant better

Add your own guidance in Settings, Assistant. It is added **after** locked safety rules that cannot be removed.

See [Let the assistant help you set something up](doc:howto-assistant-setup).
