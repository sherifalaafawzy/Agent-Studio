---
name: incident-postmortem
group: Engineering
mcp:
- kubernetes
description: 'Write a blameless incident postmortem from logs, timeline notes and chat: impact, timeline, causes, what went well/badly and action items. Use after an outage or serious bug.'
---

# Incident postmortem

**When to use:** an incident has been resolved and the team needs a written review.

1. Gather facts: start/detect/mitigate/resolve times, impact (who, how many, how long), alerts, changes shipped around then. Mark each as **observed** (log/graph/ticket) or **reported** (someone said).
2. Build the **timeline** in UTC with sources. Do not smooth over gaps - write "unknown" where it is.
3. Causes: the **trigger**, the **contributing conditions** (missing alert, no rollback, unclear ownership) and the **detection gap**. Avoid naming individuals; describe systems and decisions.
4. What went well, what went badly, where we got lucky.
5. Action items: each with an owner role, priority, due date and a check for "how will we know it is fixed". Prefer few, high-leverage actions over a long list.

**Safety:** read-only. If a cluster is connected, use read-only commands (get/describe/logs); never restart, scale or delete anything as part of writing the review.
