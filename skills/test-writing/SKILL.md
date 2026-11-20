---
name: test-writing
description: Write focused automated tests for new or changed behaviour, matching the repo's existing test style. Use when adding a feature, fixing a bug (regression test first), or when asked to improve coverage.
---

# Writing tests

1. Find the existing test framework and a neighbouring test; copy its layout, fixtures and naming.
2. For a bug: write the failing test FIRST and watch it fail (`run_shell`), then fix, then watch it pass.
3. Cover: the happy path, boundaries (empty/zero/max), error paths, and one realistic integration case. One behaviour per test, descriptive names.
4. Tests must be deterministic: no real network/time/randomness unless controlled; use tmp dirs and fakes.
5. Run the new tests and the whole file. Report the command and the pass/fail counts.

Pitfall: a test that cannot fail is worthless - temporarily break the code (or assert the opposite) to confirm it can.
