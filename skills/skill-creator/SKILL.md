---
name: skill-creator
group: General
description: Create a new reusable skill (SKILL.md) and optionally link MCP servers it needs. Use when the user asks you to "make a skill", or when you notice a repeatable procedure worth saving for next time.
---

# Creating a skill

A skill is a short playbook other agents load on demand. Quality bar: an agent that has never seen this task should
succeed by following it.

## Steps
1. **Check it is worth it.** Is the procedure repeatable and non-obvious? One-off facts belong in memory, not a skill.
   Call `load_skill` on existing skills first - extend rather than duplicate.
2. **Name it.** lowercase-hyphenated, verb/noun, e.g. `release-checklist`, `sql-migration-review`.
3. **Write the description** (most important field): one or two sentences saying *what it does* and *when to use it*.
   Agents only see this line in their index and decide from it whether to load the skill.
4. **Write the body** in markdown:
   - Goal and when NOT to use it
   - Numbered steps, each concrete and checkable (commands, file paths, expected output)
   - Pitfalls / verification at the end ("how do I know it worked?")
   - Keep it under ~150 lines; link to details rather than inlining them.
5. **Link MCP servers** the skill needs via `mcp_servers` (ids such as `git`, `fetch`, `github`, `playwright`, `context7`).
   Linked servers are attached to agents that name the skill explicitly, *if enabled in Settings*.
   Need a server that is not in the list? Call `propose_mcp_server` (pin exact versions). It stays disabled until a human
   approves it - tell the user to enable it in Settings > MCP.
6. **Save** with `create_skill(name, description, body, mcp_servers)`. Then re-read it with `load_skill` and sanity-check
   that every step is unambiguous.

## Pitfalls
- Vague descriptions ("helps with code") never get loaded. Be specific about the trigger.
- Do not embed secrets or API keys in a skill.
- Built-in skills cannot be overwritten; pick a new name.
