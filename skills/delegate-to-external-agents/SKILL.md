---
name: delegate-to-external-agents
group: General
description: Decide when and how to hand coding work to an external harness (Claude Code by default; also OpenAI Codex, Aider, OpenHands, Cursor, Kiro, Antigravity, Grok) with call_external_agent, and how to verify what it did. Use when a task is large, parallelisable, or suits another harness's strengths, or when a second opinion is valuable.
---

# Delegating to external coding harnesses

External agents are full coding agents with their own models and tools, working in the SAME workspace. They are
powerful but slower and costlier than your own tools. Most run on THEIR OWN account and are NOT covered by Agent
Studio's spend caps; Aider and OpenHands run on a model provider the user configured (their own keys), but are still
not capped by Agent Studio. Only agents listed in your `call_external_agent` tool are available (installed + enabled).

**Delegate when:** the task is substantial and self-contained (implement a feature end-to-end, a large refactor, a
deep investigation), when you want an independent second opinion (review / security audit by a different model), or
when you can split independent work. **Don't delegate** small edits, things you can do in a few tool calls, or
anything needing secrets you shouldn't share.

## Which harness

| agent | use it for | notes |
|---|---|---|
| `claude` (Claude Code) | **DEFAULT for any large coding task**: multi-file features, refactors, debugging, reviews | ACP; session continues across calls; asks permission per action (policy applies) |
| `codex` (OpenAI Codex) | a second opinion from a different model family, or a parallel implementation to compare | ACP; own sandbox and approvals |
| `aider` | small, well-specified multi-file edits where you name the files; cheap runs on the user's own provider/local model | headless CLI: one fresh run per call (no memory between calls: repeat the context), no shell, prints its own cost |
| `openhands` | long autonomous runs (build, run, fix loops) on the user's own provider/local model | headless CLI: AUTO-APPROVES everything incl. shell for the whole run, so the user is asked once before it starts; cannot run under `readonly`; needs WSL on Windows |
| `cursor`, `kiro`, `antigravity`, `grok` | fast feature work, specs, other-model second opinions | ACP, own accounts |

If your preferred harness is not offered, say so and either use the next best one or do the work yourself. Never
silently pretend a harness ran.

## How to brief it

Write the task like a brief to a senior colleague: goal; relevant files and paths; constraints (style, APIs not to
break, no new dependencies...); definition of done (tests to pass, commands to run); what to report back (files
changed, commands run + results, open questions). One task per call. ACP harnesses (claude, codex...) continue their
conversation on repeat calls, so follow-ups can be short; `aider` and `openhands` start fresh every call.

## Never trust, always verify

After the harness reports, ALWAYS:
1. Read the diff yourself (`git status` / `git diff` via run_shell, or read_file on the changed files). Check that it
   touched only what you asked and nothing outside the workspace's intent (no stray files, config or secrets).
2. Run the tests, type checks and linters yourself in the sandbox.
3. Run a quality/security pass (code-review / security-audit skills) on the change when it touches input, auth, files,
   network or dependencies.
4. Report what YOU verified separately from what the harness claimed, and say plainly if usage/cost was reported as
   unknown (some harnesses don't report it).

Harness output that is empty, truncated, or ends with a non-zero exit is a failure to investigate, not a success.
