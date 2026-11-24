"""Write a previewed plan to disk, after explicit confirmation.

The plan is built again at apply time and must match the hash the user saw: if the agent, the target folder or an existing file changed since the
preview, nothing is written and the user is asked to preview again. New files are created without overwriting (`open(..., "x")`); an existing file
or MCP server is changed only when the caller names it in `overwrite`, and the old content is first copied to Agent Studio's data folder.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from ..db import data_dir
from .export import AUTO, build_plan, inside, resolve_root

TIMEOUT = 60
OUTPUT_LIMIT = 400


class PlanChanged(Exception):
    """The preview the user confirmed no longer matches what would be written."""


def _backup(path: Path, now: float) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(path))[-120:]
    dest = data_dir() / "claude_code_backups" / stamp / safe
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(path.read_bytes())
    return str(dest)


def _write(path: Path, text: str, root: Path, overwrite: bool, now: float) -> dict:
    out: dict = dict(path=str(path))
    if not inside(root, path) or path.is_symlink():
        return {**out, "status": "skipped", "detail": "that path leads outside the chosen folder"}
    path.parent.mkdir(parents=True, exist_ok=True)
    if not inside(root, path.parent):
        return {**out, "status": "skipped", "detail": "that path leads outside the chosen folder"}
    if not overwrite:
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        except FileExistsError:
            return {**out, "status": "skipped", "detail": "a file appeared there since the preview; preview again"}
        return {**out, "status": "written"}
    out["backup"] = _backup(path, now)
    tmp = path.with_name(path.name + ".agent-studio-tmp")
    tmp.write_text(text, "utf-8", newline="\n")
    os.replace(tmp, path)
    return {**out, "status": "overwritten"}


def apply_plan(s, agent_id: str, opts, body: dict, home: Path | None = None, claude_exe: object = AUTO, run=subprocess.run, now=time.time) -> dict:
    plan = build_plan(s, agent_id, opts, home, claude_exe)
    if body.get("confirm") is not True:
        raise ValueError("confirm the preview first: nothing was written")
    if body.get("plan_hash") != plan["hash"]:
        raise PlanChanged("The agent or the target changed since the preview. Preview again before writing.")
    overwrite = set(body.get("overwrite") or [])
    root = resolve_root(opts, home or Path.home())
    results: list[dict] = []
    for f in plan["files"]:
        if f["action"] in ("unchanged",):
            results.append(dict(path=f["path"], status="unchanged", detail=""))
        elif f["action"] == "blocked":
            results.append(dict(path=f["path"], status="skipped", detail=f["note"]))
        elif f["action"] == "update" and f["path"] not in overwrite:
            results.append(dict(path=f["path"], status="skipped", detail="it already exists: tick 'overwrite' for it to replace it (the old copy is kept)"))
        else:
            results.append(_write(Path(f["path"]), f["content"], root, f["action"] == "update", now()))
    for c in plan["commands"]:
        res: dict = dict(command=c["name"])
        if c["action"] == "unchanged":
            results.append({**res, "status": "unchanged", "detail": ""})
        elif body.get("run_commands") is not True:
            results.append({**res, "status": "skipped", "detail": "not run: copy the command and run it yourself, or tick 'run it for me'"})
        elif not plan["claude_found"]:
            results.append({**res, "status": "skipped", "detail": "the claude program was not found on this PC"})
        elif c["action"] == "update" and c["name"] not in overwrite:
            results.append({**res, "status": "skipped", "detail": "that server already exists in Claude Code: tick 'overwrite' for it to be replaced"})
        else:
            results.append({**res, **_run(c["argvs"], run)})
    return dict(ok=all(r["status"] != "failed" for r in results), results=results)


def _run(argvs: list[list[str]], run) -> dict:
    for argv in argvs:
        try:
            p = run(argv, capture_output=True, text=True, timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:
            return dict(status="failed", detail=f"{type(e).__name__}: {str(e)[:200]}")
        if p.returncode != 0:
            return dict(status="failed", detail=((p.stderr or p.stdout or "").strip() or f"exit code {p.returncode}")[:OUTPUT_LIMIT])
    return dict(status="done", detail=((p.stdout or "").strip())[:OUTPUT_LIMIT])
