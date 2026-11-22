"""Assertions a test case can make about an agent's reply. Each returns (ok, plain explanation). `llm_judge` is the only one that
costs money (it asks a model); the others are exact and free."""
from __future__ import annotations

import json
import re

TYPES = {
    "contains": "the reply contains the text (ignoring case)",
    "not_contains": "the reply does not contain the text (ignoring case)",
    "equals": "the reply is exactly the text (ignoring spaces at the ends)",
    "regex": "the reply matches the regular expression",
    "not_regex": "the reply does not match the regular expression",
    "json_valid": "the reply is valid JSON",
    "max_chars": "the reply is at most this many characters",
    "min_chars": "the reply is at least this many characters",
    "llm_judge": "a judge model agrees the reply meets this criterion (costs a model call)",
}
NEEDS_VALUE = set(TYPES) - {"json_valid"}
NUMERIC = {"max_chars", "min_chars"}


class AssertionError_(ValueError):
    """An assertion that is not well formed. Safe to show."""


def clean(a) -> dict:
    if not isinstance(a, dict) or a.get("type") not in TYPES:
        raise AssertionError_(f"each assertion needs a type from: {', '.join(TYPES)}")
    t, v = a["type"], a.get("value")
    if t in NUMERIC:
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 1_000_000:
            raise AssertionError_(f"{t} needs a whole number between 1 and 1000000")
    elif t in NEEDS_VALUE:
        if not isinstance(v, str) or not v.strip() or len(v) > 2000:
            raise AssertionError_(f"{t} needs some text (at most 2000 characters)")
    if t in ("regex", "not_regex"):
        try:
            re.compile(v)
        except re.error as e:
            raise AssertionError_(f"the pattern is not valid ({e.msg})") from e
    return {"type": t, **({"value": v} if t in NEEDS_VALUE else {})}


def check_exact(a: dict, reply: str) -> tuple[bool, str]:
    t, v = a["type"], a.get("value")
    if t == "contains":
        return v.lower() in reply.lower(), f"contains '{v[:40]}'"
    if t == "not_contains":
        return v.lower() not in reply.lower(), f"does not contain '{v[:40]}'"
    if t == "equals":
        return reply.strip() == v.strip(), "equals the expected text"
    if t == "regex":
        return bool(re.search(v, reply)), f"matches /{v[:40]}/"
    if t == "not_regex":
        return not re.search(v, reply), f"does not match /{v[:40]}/"
    if t == "max_chars":
        return len(reply) <= v, f"is at most {v} characters ({len(reply)})"
    if t == "min_chars":
        return len(reply) >= v, f"is at least {v} characters ({len(reply)})"
    try:
        json.loads(reply)
        return True, "is valid JSON"
    except ValueError:
        return False, "is valid JSON"


JUDGE_PROMPT = ("You are grading an AI assistant's reply.\nCriterion: {criterion}\n\nUser request:\n{prompt}\n\nAssistant reply:\n{reply}\n\n"
                "Does the reply meet the criterion? Answer with the single word PASS or FAIL, then one short sentence why.")


def parse_judge(text: str) -> tuple[bool, str]:
    m = re.match(r"\s*(PASS|FAIL)\b[\s:.,-]*(.*)", text or "", re.I | re.S)
    if not m:
        raise AssertionError_("the judge model did not answer PASS or FAIL")
    return m.group(1).upper() == "PASS", (m.group(2).strip().splitlines() or [""])[0][:200]
