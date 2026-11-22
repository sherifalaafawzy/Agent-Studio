---
name: plan-and-delegate
group: General
description: Break a large task into steps and delegate them to sub-agents (explorer/coder/reviewer) with isolated context, then integrate and verify. Use for multi-file or multi-step work that would bloat your own context.
---

# Plan and delegate

1. Write a short plan: goal, steps, how each step is verified. Keep it under ~10 lines.
2. **Explore** via `explorer` for anything needing broad reading - ask a specific question, get a short answer with `path:line`.
3. **Implement** via `coder` in small, independent chunks. Sub-agents see NOTHING of your conversation, so each request must contain: goal,
   exact files/paths, constraints, acceptance check, and "report files changed + test result".
4. **Review** via `reviewer` on the finished change; address must-fix items.
5. **Verify yourself** with `run_shell` (tests/lint). Never relay a sub-agent's "it works" as proof.
6. Report: what changed, evidence (commands + results), anything left undone.

Cost note: sub-agents default to the cheaper Flash tier; keep the Pro-tier orchestrator for planning and judgement, not bulk reading.
