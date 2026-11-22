"""What a memory is: its kinds, scopes, statuses, the per-agent memory settings, and small text helpers.

Kinds of memory (each agent switches on the ones it needs):
  working     the current chat window: the agent summarises it automatically when it fills up (existing context handling)
  episodic    what happened in past chats: short summaries and key events, searchable
  semantic    facts about the user and their projects, with how sure we are and where they came from
  entity      people, projects, places and things, as structured records that are updated in place
  procedural  how-tos the agent learned ("to deploy X, run Y"), which can be promoted to a Skill
  notes       a notes file (AGENTS.md) in the workspace folder that you can edit by hand

Scopes decide who can read or write a memory:
  agent      only the agent that made it       user       every agent, for this user
  workspace  agents working in one folder      global     everyone / every agent (shared team memory)
"""
from __future__ import annotations

import re
import time
import uuid

STORED_TYPES = ("episodic", "semantic", "entity", "procedural")            # kept as records
AGENT_TYPES = ("working", *STORED_TYPES, "notes")                            # what an agent can switch on
SCOPES = ("agent", "user", "workspace", "global")
STATUSES = ("active", "pending", "superseded", "rejected")
AUTO_SAVE = ("off", "review", "auto")
MAX_TEXT = 2000
USER_ID = "local-user"

TYPE_INFO = {
    "working": ("Working memory", "The current chat. When it fills up the agent summarises it and carries on, so long chats keep working."),
    "episodic": ("Past chats", "Short summaries and key events from earlier chats, so the agent can say 'last week we decided...'."),
    "semantic": ("Facts", "Facts about you and your projects ('prefers TypeScript', 'the invoice system is Xero') with where each came from."),
    "entity": ("People & things", "Structured records for people, projects and places that are updated as you learn more."),
    "procedural": ("How-tos", "Steps the agent has learned for doing something. A good one can be turned into a Skill."),
    "notes": ("Project notes", "A notes file (AGENTS.md) in the workspace folder. You can edit it yourself; agents read it and add to it."),
}

# What switching memory on gives an agent unless it says otherwise.
AGENT_DEFAULT = dict(types=[], read_scopes=["agent", "user", "workspace", "global"], write_scopes=["agent", "user", "workspace"],
                     auto_recall=True, auto_save="review")


def new_id() -> str:
    return "m_" + uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()


_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an the and or of to in on for with is are was were be been it this that i you we they my your our at by as from".split())


def tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def norm(text: str) -> str:
    """The comparison form of a memory: lower case, single spaces, no punctuation."""
    return " ".join(_WORD.findall(text.lower()))


def text_score(query: str, text: str) -> float:
    """0..1: how much of the query's meaningful words the text contains (a plain fallback when there are no embeddings)."""
    q = set(tokens(query))
    if not q:
        return 0.0
    return len(q & set(tokens(text))) / len(q)


def cosine(a: list[float], b: list[float]) -> float:
    import math
    na, nb = math.sqrt(math.sumprod(a, a)), math.sqrt(math.sumprod(b, b))
    return math.sumprod(a, b) / (na * nb) if na and nb and len(a) == len(b) else 0.0


def slug(value: str, limit: int = 40) -> str:
    """A scope id that is safe in every backend (letters, digits, - and _). Long or odd values become a short hash."""
    import hashlib
    v = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return v if v and v == value and len(v) <= limit else (v[:20] + "-" if v else "") + hashlib.sha1(value.encode()).hexdigest()[:12]


def scope_id(scope: str, agent_id: str, workspace: str) -> str:
    return {"agent": agent_id, "user": USER_ID, "workspace": slug(workspace.replace("\\", "/").lower()), "global": "*"}[scope]


def clean_agent_memory(raw: dict | None) -> dict:
    """Validate an agent's memory settings. {} means memory is off. Raises ValueError with a plain explanation."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("memory must be an object")
    out = {**AGENT_DEFAULT, **{k: v for k, v in raw.items() if k in AGENT_DEFAULT}}
    for key, allowed in (("types", AGENT_TYPES), ("read_scopes", SCOPES), ("write_scopes", SCOPES)):
        v = out[key]
        if not isinstance(v, list) or [x for x in v if x not in allowed]:
            raise ValueError(f"memory.{key} must be a list of: {', '.join(allowed)}")
        out[key] = [x for x in allowed if x in v]                       # canonical order, no duplicates
    if not out["types"]:
        return {}
    if not isinstance(out["auto_recall"], bool):
        raise ValueError("memory.auto_recall must be true or false")
    if out["auto_save"] not in AUTO_SAVE:
        raise ValueError(f"memory.auto_save must be one of: {', '.join(AUTO_SAVE)}")
    return out


def uses_records(mem: dict) -> bool:
    """True when the agent uses any kind of memory that is stored as records (so it gets the search/save/forget tools)."""
    return bool(mem) and any(t in STORED_TYPES for t in mem.get("types", []))
