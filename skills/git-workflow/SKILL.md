---
name: git-workflow
description: Make clean, reviewable git commits and branches (status, focused staging, good messages). Use when asked to commit, branch, or summarise changes. Links the git MCP server.
mcp:
  - git
---

# Git workflow

1. `git status` and `git diff` first - know exactly what will be committed. Never `git add -A` blindly; stage intended files.
2. Work on a branch (`feature/<short-name>`) unless told otherwise; never force-push or rewrite shared history.
3. One logical change per commit. Message: imperative subject <= 72 chars, blank line, body explaining *why*.
4. Run tests before committing. If they fail, do not commit - report.
5. Do not commit secrets, `.env`, build output, or large binaries; check `git diff --cached` for them.
6. Pushing/opening PRs is outward-facing: only do it when the user asked.

The `git` MCP server (if enabled) offers structured status/diff/log/commit tools; otherwise use `run_shell` (network off by default, so push needs the user).
