---
name: code-review
group: Coding
description: Review a code change for correctness, edge cases, security and missing tests, and report findings by severity. Use when asked to review a diff/PR/files or to double-check your own change before reporting done.
---

# Code review

1. Establish scope: which files changed (`git diff --stat` / `git diff` via run_shell, or the files named). Read each fully, not just the hunk.
2. For each change ask: does it do what was intended? What inputs break it (empty, None, huge, unicode, concurrent)?
   Are errors handled and surfaced? Any injection / path traversal / secret exposure? Resource leaks?
3. Check tests: does a test fail without the change and pass with it? Run them (`run_shell`). Missing coverage is a finding.
4. Check consistency with surrounding code (naming, error style, existing helpers that should have been reused).
5. Report, most severe first, each as `path:line - problem - suggested fix`. Separate **must-fix** from **nits**.
   Say explicitly what you did NOT verify. "No issues" is only valid if you actually read and ran things.

Do not edit files while reviewing; propose changes.
