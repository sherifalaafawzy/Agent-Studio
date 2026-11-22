---
name: code-quality
group: Coding
description: Raise code quality before finishing work - lint, type-check, test, remove duplication and dead code, keep changes small and idiomatic. Use after implementing a change, when asked to clean up / refactor, or when a review flags quality problems.
mcp: [eslint, semgrep]
---

# Code quality pass

Run this on the files you touched (or the scope you were given). Prefer the project's own tools and configs.

1. **Discover the toolchain.** Read package.json scripts, pyproject/setup.cfg, Makefile, CI workflow files. Use
   exactly what the project uses (eslint/prettier/tsc, ruff/black/mypy/pytest, go vet, cargo clippy...).
2. **Static checks** (sandbox `run_shell`, or the MCP servers):
   - JS/TS: ESLint `lint-files` on changed files; `npx tsc --noEmit` if TypeScript.
   - Python: `ruff check` / `python -m pyflakes`, `mypy` if configured.
   - Any language: `semgrep_scan` for bug-prone patterns.
   Fix real problems; don't silence rules without a reason written next to the suppression.
3. **Tests.** Run the relevant tests. New behaviour needs a test; a bug fix needs a test that failed before.
4. **Design smells to fix in your own change:** duplicated logic (reuse the existing helper), functions doing several
   things, unclear names, magic numbers, swallowed exceptions, dead code / unused imports, comments that restate code.
   Don't refactor unrelated code unless asked — mention it instead.
5. **Report**: what you ran (exact commands) and their results, what you fixed, what you left and why.
