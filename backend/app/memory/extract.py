"""Finding things worth remembering in a conversation.

Three sources, from safest to most capable:
  explicit   "remember that ..." - the user asked, so it is saved without waiting for review
  rules      a few plain patterns ("my timezone is...", "I prefer...") - free, offline, easy to explain
  model      one extra model call (under the spend guard) that proposes memories as JSON; always parsed strictly
"""
from __future__ import annotations

import json
import re

from .model import MAX_TEXT, STORED_TYPES, norm

MAX_CANDIDATES = 8

_EXPLICIT = re.compile(r"(?:^|[.!?]\s+|\n)\s*(?:please\s+)?remember(?:\s+that)?\s*[:,]?\s+(?P<v>(?:[^\n.!?]|[.!?](?=\S))+)", re.I)
_MY_IS = re.compile(r"\bmy (?P<k>name|email address|role|job title|job|company|team|timezone|time zone|location|city|country|"
                    r"(?:favorite|favourite|preferred) [a-z ]{2,30}?) is (?P<v>[^.\n!?]{1,120})", re.I)
_PREFER = re.compile(r"\bI (?P<verb>prefer|like|love|always use|usually use) (?P<v>[^.\n!?]{2,100})", re.I)
_VERBS = {"prefer": "prefers", "like": "likes", "love": "loves", "always use": "always uses", "usually use": "usually uses"}
_WORK = re.compile(r"\bI (?:work|am working) (?P<prep>at|for|on) (?P<v>[^.\n!?]{2,100})", re.I)
_TEAM = re.compile(r"\bwe (?:use|chose|decided to use) (?P<v>[^.\n!?]{2,100})", re.I)


def _cand(text: str, *, type: str = "semantic", key: str = "", confidence: float = 0.6, explicit: bool = False) -> dict:
    return dict(type=type, text=text[:MAX_TEXT].strip(), key=key, confidence=confidence, explicit=explicit)


def explicit_candidates(user_text: str) -> list[dict]:
    m = _EXPLICIT.search(user_text or "")
    v = m.group("v").strip().rstrip(".!") if m else ""
    return [_cand(v, confidence=0.95, explicit=True)] if v and v.lower() != "that" else []


def rule_candidates(user_text: str) -> list[dict]:
    text = user_text or ""
    out: list[dict] = []
    for m in _MY_IS.finditer(text):
        k = " ".join(m.group("k").lower().replace("favourite", "favorite").split())
        out.append(_cand(f"The user's {k} is {m.group('v').strip()}", key="user." + k.replace(" ", "_"), confidence=0.7))
    for m in _PREFER.finditer(text):
        out.append(_cand(f"The user {_VERBS[m.group('verb').lower()]} {m.group('v').strip()}", confidence=0.55))
    for m in _WORK.finditer(text):
        out.append(_cand(f"The user works {m.group('prep')} {m.group('v').strip()}", key="user.work", confidence=0.6))
    for m in _TEAM.finditer(text):
        out.append(_cand(f"The team uses {m.group('v').strip()}", confidence=0.55))
    seen, uniq = set(), []
    for c in out:
        n = norm(c["text"])
        if n not in seen:
            seen.add(n)
            uniq.append(c)
    return uniq[:MAX_CANDIDATES]


MODEL_PROMPT = """You decide what is worth remembering from one conversation turn, for an assistant that helps one person over many chats.
Return ONLY a JSON list (no prose). Each item: {"type": "semantic" | "entity" | "procedural", "text": "one short sentence", "key": "optional.stable.key",
"name": "entity name (entity only)", "kind": "person|project|place|thing (entity only)", "attributes": {"field": "value"} (entity only)}.
Rules: only durable facts about the user, their projects or how to do something; never secrets, passwords, keys, card numbers or one-off details;
nothing you are not sure about. Return [] when nothing is worth remembering. At most 5 items.

User said:
{user}

Assistant answered:
{reply}
"""


def model_prompt(user_text: str, reply: str) -> str:
    return MODEL_PROMPT.replace("{user}", (user_text or "")[:3000]).replace("{reply}", (reply or "")[:3000])


def parse_model_output(text: str) -> list[dict]:
    """Strict parsing of the model's answer: anything that is not a well-formed item is dropped, never repaired."""
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except ValueError:
        return []
    out: list[dict] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or it.get("type") not in STORED_TYPES or it.get("type") == "episodic":
            continue
        text_ = str(it.get("text") or "").strip()
        if not text_:
            continue
        c = _cand(text_, type=it["type"], key=str(it.get("key") or "")[:80], confidence=0.6)
        if it["type"] == "entity":
            attrs = it.get("attributes")
            if not it.get("name") or not isinstance(attrs, dict):
                continue
            c.update(name=str(it["name"])[:80], kind=str(it.get("kind") or "thing")[:30], attributes={str(k)[:40]: str(v)[:200] for k, v in list(attrs.items())[:12]})
        out.append(c)
    return out[:MAX_CANDIDATES]


def episode_text(first_user: str, reply: str) -> str:
    one = " ".join((first_user or "").split())[:160]
    two = " ".join((reply or "").split())[:240]
    return f"Chat about: {one}" + (f" — {two}" if two else "")
