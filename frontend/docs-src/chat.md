---
title: Chat
section: Work
order: 10
summary: Talking to a model or an agent: picking who answers, steering a running turn, the context meter and saved chats.
tags: chat, conversation, composer, auto, model, steer, queue, context, sessions, stop
routes: /
---
Chat is the home screen. Type what you want done and press **Enter** (**Shift+Enter** starts a new line).

![The chat screen: sidebar on the left, the message box at the bottom](shot:chat-home)

## Pick who answers

Next to the message box you choose **plain chat** (one model answers directly) or **one of your agents** (it can plan, use tools and hand work to sub-agents). The model is **Auto** by default: Agent Studio reads each message and picks the cheapest model that can handle it, within your spend limits. Under each answer it shows what it chose and why. You can also pin a provider and model yourself. See [Settings, Auto](doc:settings#auto).

## While an agent works

You see the answer stream in, plus live cards for each agent, tool call and model call. While it works you can:

- **Queue** a note, which the agent reads at its next step.
- **Steer**, which interrupts the current step now.
- **Stop**, which cancels the turn. A model call that may already have run is charged at its worst case, so no spend is hidden.

> **Tip:** the **Agents** panel in the chat lists every run of the turn (the main agent, built-in helpers and helpers an agent created itself) with its task, tool calls, output and cost. It is saved with the chat so you can reopen old runs.

## The context meter

The ring shows how full the model's context window is, that is, how much text it can keep in mind at once. When it fills up, **Summarise** shrinks the conversation so far without losing the thread.

## Answers you can keep

- **Keep** saves an answer as an [artifact](doc:artifacts).
- **Edit in canvas** opens an answer in the [canvas](doc:canvas) to edit it with the agent.
- Answers can contain tables, charts, maps and cards. See [Rich answers](doc:rich-answers).

## Saved chats

Every chat is stored and listed under **Recents** in the sidebar. A running chat shows a small pulsing dot, and it keeps running if you move to another page. **Ctrl+K** jumps to any chat. After a restart the conversation is rebuilt from the stored messages. Tool calls and results are not replayed into the model's memory, only the text of what was said.

## When an outside agent asks you something

If you chose an [external coding agent](doc:external-agents) and it needs permission, a plan approved, or an answer, the question appears as a card in the chat (for example **Approve plan**, **Reject** or **Skip**). The run waits for you.

## If something goes wrong

Errors appear as a red card that says what happened and what to try, with a **Try again** button. Typical causes: no provider switched on, a spend limit reached, or Ollama not running. See [Troubleshooting](doc:troubleshooting).
