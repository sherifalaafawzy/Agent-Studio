"""Shared pieces of the data source connectors: the error, the id pattern, the result limits and how a result is shaped for an agent."""
from __future__ import annotations

import re
from typing import Any

DS_REF = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")
SENSITIVE_COLUMN = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|ssn|social[_-]?security|card[_-]?(no|num)|cvv|iban|account[_-]?number|private[_-]?key)", re.I)
LIMITS = {"max_rows": (100, 1, 5000), "max_bytes": (200_000, 1_000, 5_000_000), "timeout_s": (30, 1, 120)}      # name: (default, min, max)


class DataError(Exception):
    """A refusal or failure with a plain explanation (safe to show to the user and to hand to the model)."""


def F(name: str, label: str, secret: bool = False, required: bool = True, help: str = "", default: str = "") -> dict:
    return dict(name=name, label=label, secret=secret, required=required, help=help, default=default)


def clean_limits(raw: Any) -> dict:
    out = {}
    raw = raw if isinstance(raw, dict) else {}
    for k, (dflt, lo, hi) in LIMITS.items():
        v = raw.get(k, dflt)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise DataError(f"{k.replace('_', ' ')} must be a whole number between {lo} and {hi}")
        out[k] = v
    return out


def cell(v: Any) -> Any:
    """A value as JSON-friendly data (bytes and odd types become text)."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    return str(v)


def shape(columns: list[str], rows: list[list], max_rows: int, max_bytes: int, redact: bool) -> dict:
    """The result an agent receives: bounded in rows and bytes, with sensitive columns and personal data masked when `redact` is on."""
    from ..memory.pii import scrub
    cols = [str(c) for c in columns]
    masked = [bool(redact and SENSITIVE_COLUMN.search(c)) for c in cols]
    out: list[list] = []
    size, truncated, found = 0, False, False
    for r in rows:
        if len(out) >= max_rows:
            truncated = True
            break
        line = []
        for v, m in zip(r, masked):
            v = cell(v)
            if m and v not in (None, ""):
                v, found = "[hidden]", True
            elif redact and isinstance(v, str):
                clean, kinds = scrub(v)
                found = found or bool(kinds)
                v = clean
            line.append(v)
        size += sum(len(str(v)) + 2 for v in line)
        if size > max_bytes:
            truncated = True
            break
        out.append(line)
    return dict(columns=cols, rows=out, row_count=len(out), truncated=truncated, redacted=found)
