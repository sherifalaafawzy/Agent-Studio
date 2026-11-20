"""Path-jailed workspace file operations + the diff-based edit engine."""
from __future__ import annotations

import difflib
import re
from pathlib import Path

MAX_READ_BYTES = 400_000
MAX_WRITE_BYTES = 1_000_000


class ToolError(Exception):
    """Expected, model-actionable failure (returned to the agent, not raised to the user)."""


def jail(ws: Path, rel: str) -> Path:
    """Resolve rel inside ws; reject escapes (.., absolute paths, symlinks pointing out)."""
    if not rel or "\x00" in rel:
        raise ToolError("invalid path")
    ws = ws.resolve()
    p = Path(rel)
    cand = (p if p.is_absolute() else ws / p).resolve()
    if cand != ws and not cand.is_relative_to(ws):
        raise ToolError(f"path escapes the workspace: {rel}")
    parts = cand.relative_to(ws).parts
    if parts and parts[0] == ".git" and len(parts) > 1:
        raise ToolError("refusing to touch .git internals (use git commands)")
    return cand


def read_text(path: Path) -> tuple[str, str]:
    """(text with \\n newlines, original newline style)."""
    raw = path.read_bytes()
    if len(raw) > MAX_READ_BYTES * 4:
        raise ToolError("file too large")
    if b"\x00" in raw[:8192]:
        raise ToolError("binary file")
    txt = raw.decode("utf-8", errors="replace")
    nl = "\r\n" if "\r\n" in txt else "\n"
    return txt.replace("\r\n", "\n"), nl


def write_text(path: Path, text: str, nl: str) -> None:
    if len(text) > MAX_WRITE_BYTES:
        raise ToolError("content too large")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", nl).encode("utf-8"))


def numbered(text: str, start: int = 1, end: int = 0) -> str:
    lines = text.split("\n")
    end = end or len(lines)
    start = max(1, start)
    out, size = [], 0
    for i in range(start, min(end, len(lines)) + 1):
        ln = f"{i:>5}\t{lines[i - 1]}"
        size += len(ln)
        if size > MAX_READ_BYTES // 4:
            out.append(f"…[truncated at line {i}; use start_line/end_line to page]")
            break
        out.append(ln)
    return "\n".join(out)


def unified(old: str, new: str, name: str) -> str:
    d = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{name}", f"b/{name}", n=2))
    return d if len(d) < 8000 else d[:8000] + "\n…[diff truncated]"


def apply_edit(text: str, old: str, new: str, replace_all: bool = False) -> tuple[str, str]:
    """Replace `old` with `new` in text. Exact match must be unique (unless replace_all).
    Falls back to a whitespace-tolerant whole-line match. Returns (new_text, how)."""
    if old == "":
        raise ToolError("old_text must not be empty (use write_file to create files)")
    if old == new:
        raise ToolError("old_text and new_text are identical - nothing to change")
    n = text.count(old)
    if n == 1 or (n > 1 and replace_all):
        return text.replace(old, new), "exact"
    if n > 1:
        lines = [text.count("\n", 0, m.start()) + 1 for m in re.finditer(re.escape(old), text)]
        raise ToolError(f"old_text matches {n} places (lines {lines[:8]}). Include more surrounding context to make it unique, or pass replace_all=true.")
    # whitespace-tolerant fallback on whole lines
    tl, ol = text.split("\n"), old.strip("\n").split("\n")
    norm = lambda s: re.sub(r"\s+", " ", s.strip())  # noqa: E731
    on = [norm(x) for x in ol]
    hits = [i for i in range(len(tl) - len(ol) + 1) if [norm(x) for x in tl[i:i + len(ol)]] == on]
    if len(hits) == 1:
        i = hits[0]
        return "\n".join(tl[:i] + new.strip("\n").split("\n") + tl[i + len(ol):]), "whitespace-tolerant"
    if len(hits) > 1:
        raise ToolError(f"old_text (ignoring whitespace) matches {len(hits)} places; add more context.")
    first = ol[0].strip()
    close = difflib.get_close_matches(first, [x.strip() for x in tl], n=3, cutoff=0.6)
    hint = f" Closest existing lines: {close}" if close else ""
    raise ToolError("old_text not found in file. Re-read the file and copy the text exactly." + hint)
