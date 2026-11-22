---
name: debugging
group: Coding
description: Systematically find and fix the root cause of a bug or failing test (reproduce, isolate, fix, verify). Use whenever something fails and the cause is not already obvious.
---

# Debugging loop

1. **Reproduce** with the smallest command (`run_shell`, e.g. `pytest -x tests/test_x.py::test_y`). No reproduction -> no fix; say so.
2. **Read the actual error**: the last traceback frame in *our* code is usually the place to look. Quote it.
3. **Form one hypothesis** and test it cheaply (print/assert, read the code path, `search_text` callers) before editing.
4. **Fix the cause, not the symptom.** Smallest `edit_file` change. Do not weaken tests or add broad try/except to silence errors.
5. **Verify**: rerun the failing test, then the surrounding test file/suite for regressions.
6. If 3 hypotheses fail, stop and report what you ruled out instead of thrashing.

Report: root cause (one sentence), the fix (file:line), verification output.
