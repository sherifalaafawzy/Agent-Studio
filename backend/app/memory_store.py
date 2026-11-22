"""Manage the Memory MCP server's knowledge graph (what agents remember across chats).

The memory server keeps its data in one file (JSON lines: entities and relations). This module reads and edits that same
file, so the user can see, correct and delete what agents have stored. Writes are atomic (temp file + rename), and the
memory server is told nothing: it reads the file again on its next call.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .db import data_dir


def path() -> Path:
    return data_dir() / "memory.json"


def _read() -> tuple[list[dict], list[dict]]:
    p = path()
    entities: list[dict] = []
    relations: list[dict] = []
    if not p.exists():
        return entities, relations
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return entities, relations
    items: list[dict] = []
    if text.startswith("["):
        items = json.loads(text)
    else:
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("type") == "relation":
            relations.append(it)
        elif it.get("type") == "entity" or "observations" in it:
            entities.append(it)
    return entities, relations


def _write(entities: list[dict], relations: list[dict]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "entity", **{k: v for k, v in e.items() if k != "type"}}) for e in entities]
    lines += [json.dumps({"type": "relation", **{k: v for k, v in r.items() if k != "type"}}) for r in relations]
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(tmp, p)


def _backup() -> None:
    p = path()
    if p.exists():
        p.with_name(f"memory.backup-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")


def summary() -> dict:
    ents, rels = _read()
    types: dict[str, int] = {}
    for e in ents:
        types[e.get("entityType") or "unknown"] = types.get(e.get("entityType") or "unknown", 0) + 1
    return dict(file=str(path()), entities=len(ents), relations=len(rels), observations=sum(len(e.get("observations") or []) for e in ents),
                types=types, exists=path().exists())


def list_entities(q: str | None = None, entity_type: str | None = None, limit: int = 200) -> list[dict]:
    ents, rels = _read()
    qq = (q or "").strip().lower()
    out = []
    for e in ents:
        if entity_type and e.get("entityType") != entity_type:
            continue
        hay = " ".join([e.get("name", ""), e.get("entityType", ""), *(e.get("observations") or [])]).lower()
        if qq and qq not in hay:
            continue
        name = e.get("name")
        out.append(dict(name=name, entityType=e.get("entityType"), observations=e.get("observations") or [],
                        relations=[r for r in rels if r.get("from") == name or r.get("to") == name]))
    return out[:limit]


def update_entity(name: str, observations: list[str] | None = None, entity_type: str | None = None) -> dict:
    ents, rels = _read()
    e = next((x for x in ents if x.get("name") == name), None)
    if e is None:
        raise KeyError(name)
    _backup()
    if observations is not None:
        e["observations"] = [o.strip() for o in observations if o and o.strip()]
    if entity_type:
        e["entityType"] = entity_type.strip()
    _write(ents, rels)
    return e


def delete_entity(name: str) -> dict:
    ents, rels = _read()
    if not any(x.get("name") == name for x in ents):
        raise KeyError(name)
    _backup()
    ents = [x for x in ents if x.get("name") != name]
    gone = [r for r in rels if r.get("from") == name or r.get("to") == name]
    rels = [r for r in rels if r.get("from") != name and r.get("to") != name]
    _write(ents, rels)
    return dict(deleted=name, relations_removed=len(gone))


def delete_observation(name: str, index: int) -> dict:
    ents, rels = _read()
    e = next((x for x in ents if x.get("name") == name), None)
    if e is None:
        raise KeyError(name)
    obs = e.get("observations") or []
    if not 0 <= index < len(obs):
        raise IndexError(index)
    _backup()
    obs.pop(index)
    e["observations"] = obs
    _write(ents, rels)
    return e


def clear_all() -> dict:
    ents, rels = _read()
    _backup()
    _write([], [])
    return dict(cleared=len(ents), relations=len(rels))
