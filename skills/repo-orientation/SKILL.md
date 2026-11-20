---
name: repo-orientation
description: Quickly map an unfamiliar repository (stack, entry points, tests, conventions) before changing anything. Use at the start of any task in a codebase you have not seen this session.
---

# Repo orientation (aim: < 10 tool calls)

1. `list_dir .` then read the top-level README / package manifest (`package.json`, `pyproject.toml`, `requirements.txt`, `go.mod`).
2. Identify: language/framework, how to run, **how to run tests**, lint config.
3. `glob_files` for entry points (`main.*`, `app.*`, `index.*`, `manage.py`, `cmd/`).
4. `search_text` for the symbols/strings in the task to find the code that matters. Read only those files (use line ranges).
5. Look at one existing test next to similar code to learn the testing style.
6. Report in <= 15 lines: stack, run/test commands, relevant files with `path:line`, conventions to follow, risks.

Do not read whole large files; page with start_line/end_line. Never guess - say what you could not determine.
