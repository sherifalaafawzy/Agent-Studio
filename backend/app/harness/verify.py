"""Mandatory post-edit verification. Every write/edit goes through verify(); a failing check
causes the caller to ROLL BACK the change and return the lint output to the agent.

Result states per check: pass | fail | skipped (with reason). 'skipped' is never silent: it is reported
to the agent so it knows the file was NOT fully verified.
"""
from __future__ import annotations

import ast
import json
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .sandbox import Sandbox, SandboxUnavailable


@dataclass
class Verify:
    ok: bool = True
    checks: list[dict] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(dict(check=name, status=status, detail=detail[:2000]))
        if status == "fail":
            self.ok = False

    def summary(self) -> str:
        return "; ".join(f"{c['check']}:{c['status']}" for c in self.checks)


def _sandbox_check(v: Verify, sb: Sandbox | None, name: str, rel: str, cmd: str) -> None:
    if sb is None:
        v.add(name, "skipped", "no sandbox configured")
        return
    try:
        r = sb.exec(cmd, timeout_s=60)
    except SandboxUnavailable as e:
        v.add(name, "skipped", f"sandbox unavailable: {e}")
        return
    out = (r["stdout"] + r["stderr"]).strip()
    if r["exit_code"] == 0:
        v.add(name, "pass", out[:300])
    elif r["exit_code"] == 127:
        v.add(name, "skipped", "tool not installed in sandbox image")
    else:
        v.add(name, "fail", out or f"exit {r['exit_code']}")


def verify(path: Path, rel: str, text: str, sb: Sandbox | None) -> Verify:
    """Verify `text` that has ALREADY been written to `path` (rel is the workspace-relative posix path)."""
    v = Verify()
    ext = path.suffix.lower()
    q = shlex.quote(rel)
    if ext == ".py":
        try:
            ast.parse(text, filename=rel)
            v.add("python-syntax", "pass")
        except SyntaxError as e:
            v.add("python-syntax", "fail", f"{rel}:{e.lineno}:{e.offset}: {e.msg}")
            return v  # no point linting broken syntax
        _sandbox_check(v, sb, "ruff", rel, f"ruff check --select E9,F63,F7,F82 --output-format concise --no-cache {q}")
    elif ext == ".json":
        try:
            json.loads(text)
            v.add("json-syntax", "pass")
        except json.JSONDecodeError as e:
            v.add("json-syntax", "fail", f"{rel}:{e.lineno}:{e.colno}: {e.msg}")
    elif ext == ".toml":
        try:
            tomllib.loads(text)
            v.add("toml-syntax", "pass")
        except tomllib.TOMLDecodeError as e:
            v.add("toml-syntax", "fail", str(e))
    elif ext in (".yaml", ".yml"):
        try:
            list(yaml.safe_load_all(text))
            v.add("yaml-syntax", "pass")
        except yaml.YAMLError as e:
            v.add("yaml-syntax", "fail", str(e))
    elif ext in (".js", ".mjs", ".cjs"):
        _sandbox_check(v, sb, "node-check", rel, f"node --check {q}")
    elif ext in (".sh", ".bash"):
        _sandbox_check(v, sb, "bash-n", rel, f"bash -n {q}")
    else:
        v.add("syntax", "skipped", f"no verifier for '{ext or 'this file type'}' - file NOT checked; run its tests/build via run_shell")
    return v
