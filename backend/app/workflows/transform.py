"""The data boxes of a workflow, as plain functions: filter, sort, limit, remove duplicates, aggregate, split, join, date and time, regular expressions,
conditions and merge-by-key. Nothing here touches the network, the disk or a model, so each one can be tested on its own.

Items are JSON lists (or text with one item per line). A `path` is a dotted name into an item (`customer.email`, `lines.0.sku`); an empty path means the item itself.
Every function either returns the result or raises `WorkflowError` with a sentence that says what to change.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from .core import WorkflowError

MAX_ITEMS, MAX_RE_PATTERN, MAX_RE_TEXT = 1000, 200, 20_000
OPS = ("equals", "not_equals", "contains", "not_contains", "starts_with", "ends_with", "gt", "gte", "lt", "lte", "is_empty", "not_empty", "regex")
AGG_OPS = ("count", "sum", "avg", "min", "max", "first", "last", "join", "collect")
UNITS = {"minutes": 60, "hours": 3600, "days": 86400, "weeks": 604800}
NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]")          # (a+)+ and friends: the classic way to make a regular expression run forever


def text(v: Any) -> str:
    return v if isinstance(v, str) else "" if v is None else json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else str(v)


def to_json(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def items_of(v: Any) -> list:
    """A list from a list, from JSON text holding a list, or from text with one item per line."""
    if isinstance(v, list):
        return v[:MAX_ITEMS]
    if isinstance(v, str):
        t = v.strip()
        if t[:1] == "[":
            try:
                j = json.loads(t)
            except ValueError:
                j = None
            if isinstance(j, list):
                return j[:MAX_ITEMS]
        return [x for x in v.splitlines() if x.strip()][:MAX_ITEMS]
    return [] if v is None else [v]


def path_get(obj: Any, path: str) -> Any:
    """Read `a.b.0.c` from nested dicts and lists; a missing step gives None."""
    cur = obj
    for part in [p for p in (path or "").split(".") if p]:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            i = int(part)
            cur = cur[i] if -len(cur) <= i < len(cur) else None
        else:
            return None
    return cur


def number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def safe_regex(pattern: str, ignore_case: bool = False) -> re.Pattern:
    if not pattern or len(pattern) > MAX_RE_PATTERN:
        raise WorkflowError(f"write a pattern (up to {MAX_RE_PATTERN} characters)")
    if NESTED_QUANTIFIER.search(pattern):
        raise WorkflowError("that pattern repeats a repeat (like (a+)+), which can freeze a run: simplify it")
    try:
        return re.compile(pattern, re.I if ignore_case else 0)
    except re.error as e:
        raise WorkflowError(f"that pattern is not valid: {e}")


# -------------------------------------------------------------------------------------------------- rules and conditions
def rule_match(a: Any, op: str, b: Any) -> bool:
    """One comparison: `a` (the thing being checked) against `b` (what to compare with)."""
    if op not in OPS:
        raise WorkflowError(f"unknown comparison '{op}'")
    if op == "is_empty":
        return a in (None, "", [], {})
    if op == "not_empty":
        return a not in (None, "", [], {})
    if op in ("equals", "not_equals"):
        na, nb = number(a), number(b)
        eq = (na == nb) if na is not None and nb is not None else text(a) == text(b)
        return eq if op == "equals" else not eq
    if op in ("contains", "not_contains"):
        has = (any(text(x) == text(b) for x in a) if isinstance(a, list) else text(b) in text(a))
        return has if op == "contains" else not has
    if op == "starts_with":
        return text(a).startswith(text(b))
    if op == "ends_with":
        return text(a).endswith(text(b))
    if op == "regex":
        return safe_regex(text(b)).search(text(a)[:MAX_RE_TEXT]) is not None
    na, nb = number(a), number(b)
    x, y = (na, nb) if na is not None and nb is not None else (text(a), text(b))
    return {"gt": x > y, "gte": x >= y, "lt": x < y, "lte": x <= y}[op]       # type: ignore[operator]


def combine(results: list[bool], how: str) -> bool:
    return (any(results) if how == "or" else all(results)) if results else True


def filter_items(items: list, rules: list[dict], how: str) -> list:
    """Keep the items that pass the rules ({field, op, value}); `how` is 'and' or 'or'."""
    return [it for it in items if combine([rule_match(path_get(it, r["field"]), r["op"], r["value"]) for r in rules], how)]


def conditions_hold(rules: list[dict], how: str) -> bool:
    """Rules of the Compare box: {left, op, right}, both sides already filled in."""
    return combine([rule_match(r["left"], r["op"], r["right"]) for r in rules], how)


# -------------------------------------------------------------------------------------------------- lists
def sort_items(items: list, path: str, descending: bool, numeric: bool) -> list:
    def key(it: Any):
        v = path_get(it, path) if path else it
        if numeric:
            n = number(v)
            return (n is None, n if n is not None else 0.0)
        return (v is None, text(v).lower())
    return sorted(items, key=key, reverse=descending)


def limit_items(items: list, count: int, from_end: bool) -> list:
    return items[-count:] if from_end else items[:count]


def dedupe_items(items: list, path: str) -> list:
    seen: set[str] = set()
    out = []
    for it in items:
        k = text(path_get(it, path) if path else it)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def _agg(items: list, op: str, path: str, sep: str) -> Any:
    vals = [path_get(it, path) if path else it for it in items]
    if op == "count":
        return len(items)
    if op == "first":
        return items[0] if items else None
    if op == "last":
        return items[-1] if items else None
    if op == "collect":
        return vals
    if op == "join":
        return sep.join(text(v) for v in vals)
    nums = [n for n in (number(v) for v in vals) if n is not None]
    if not nums:
        raise WorkflowError(f"there are no numbers to {op}: check the field name")
    out = {"sum": sum(nums), "avg": sum(nums) / len(nums), "min": min(nums), "max": max(nums)}[op]
    return int(out) if float(out).is_integer() else round(out, 6)


def aggregate(items: list, op: str, path: str, group_by: str, sep: str) -> Any:
    if op not in AGG_OPS:
        raise WorkflowError(f"summarise with one of: {', '.join(AGG_OPS)}")
    if not group_by:
        return _agg(items, op, path, sep)
    groups: dict[str, list] = {}
    for it in items:
        groups.setdefault(text(path_get(it, group_by)), []).append(it)
    return {k: _agg(v, op, path, sep) for k, v in groups.items()}


def split_out(value: Any, mode: str, sep: str, path: str) -> list:
    if mode == "lines":
        return [x for x in text(value).splitlines() if x.strip()][:MAX_ITEMS]
    if mode == "separator":
        return [x.strip() for x in text(value).split(sep or ",") if x.strip()][:MAX_ITEMS]
    inner = path_get(value if not isinstance(value, str) else _loads(value), path)
    if not isinstance(inner, list):
        raise WorkflowError(f"'{path}' is not a list in what came in")
    return inner[:MAX_ITEMS]


def _loads(s: str) -> Any:
    try:
        return json.loads(s)
    except ValueError:
        raise WorkflowError("what came in is not valid JSON")


def join_items(items: list, path: str, sep: str) -> str:
    return sep.join(text(path_get(it, path) if path else it) for it in items)


def merge_by_key(left: list, right: list, key: str, right_key: str, mode: str) -> list:
    """Join two lists of objects on a key (SQL-style). 'inner' keeps matches only; 'left' keeps every left item."""
    rk = right_key or key
    index: dict[str, list] = {}
    for r in right:
        index.setdefault(text(path_get(r, rk)), []).append(r)
    out = []
    for l in left:
        matches = index.get(text(path_get(l, key)), [])
        for m in matches:
            out.append({**(l if isinstance(l, dict) else {"left": l}), **(m if isinstance(m, dict) else {"right": m})})
        if not matches and mode == "left":
            out.append(l)
    return out


# -------------------------------------------------------------------------------------------------- dates and regular expressions
def parse_date(v: Any) -> dt.datetime:
    s = text(v).strip()
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise WorkflowError(f"'{s[:40]}' is not a date (use 2026-03-31 or 2026-03-31T09:30:00Z)")
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def date_time(op: str, value: Any, amount: float, unit: str, fmt: str, other: Any, now: dt.datetime | None = None) -> str:
    if op == "now":
        return (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
    d = parse_date(value)
    if op == "parse":
        return d.isoformat(timespec="seconds")
    if op == "format":
        return d.strftime(fmt or "%Y-%m-%d")
    if op in ("add", "subtract"):
        if unit not in UNITS:
            raise WorkflowError(f"unit must be one of: {', '.join(UNITS)}")
        delta = dt.timedelta(seconds=UNITS[unit] * amount)
        return (d + delta if op == "add" else d - delta).isoformat(timespec="seconds")
    if op == "diff_days":
        return str(round((d - parse_date(other)).total_seconds() / 86400, 4))
    raise WorkflowError("choose now, format, add, subtract, diff_days or parse")


def regex_op(mode: str, value: Any, pattern: str, replacement: str, ignore_case: bool) -> Any:
    rx = safe_regex(pattern, ignore_case)
    t = text(value)[:MAX_RE_TEXT]
    if mode == "test":
        return rx.search(t) is not None
    if mode == "first":
        m = rx.search(t)
        return m.group(0) if m else ""
    if mode == "all":
        return [m.group(0) for m in rx.finditer(t)][:MAX_ITEMS]
    if mode == "groups":
        m = rx.search(t)
        if not m:
            return {}
        return {**{str(i): g for i, g in enumerate(m.groups(), 1)}, **m.groupdict()}
    if mode == "replace":
        try:
            return rx.sub(replacement, t)
        except (re.error, IndexError) as e:
            raise WorkflowError(f"the replacement is not valid: {e}")
    raise WorkflowError("choose test, first, all, groups or replace")
