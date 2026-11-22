"""JSON-schema descriptions of Agent Studio's tool functions, for frameworks that call them through the tool bridge.

The harness tools are plain async functions with a Google-style docstring (`Args:` lines); this reads the signature and the
docstring and produces the OpenAI function-calling shape every framework understands.
"""
from __future__ import annotations

import inspect
import re
import types
import typing
from typing import Any, Callable

_SIMPLE = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object", list: "array"}
SKIP_PARAMS = {"tool_context", "self"}


def _type(ann: Any) -> dict:
    if ann is inspect.Parameter.empty or ann is Any:
        return {"type": "string"}
    origin = typing.get_origin(ann)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        return _type(args[0]) if args else {"type": "string"}
    if origin in (list, typing.List):  # noqa: UP006
        inner = typing.get_args(ann)
        return {"type": "array", "items": _type(inner[0]) if inner else {"type": "string"}}
    if origin in (dict, typing.Dict):  # noqa: UP006
        return {"type": "object"}
    return {"type": _SIMPLE.get(ann, "string")}


def _doc(fn: Callable) -> tuple[str, dict[str, str]]:
    doc = inspect.getdoc(fn) or ""
    head, tail = (re.split(r"(?m)^Args:\s*$", doc, maxsplit=1) + [""])[:2]
    params: dict[str, str] = {}
    cur = None
    for line in tail.splitlines():
        m = re.match(r"^\s{1,8}(\w+)\s*(?:\([^)]*\))?:\s*(.*)$", line)
        if m:
            cur = m.group(1)
            params[cur] = m.group(2).strip()
        elif cur and line.strip():
            params[cur] += " " + line.strip()
    return " ".join(head.split())[:600], params


def tool_spec(fn: Callable) -> dict:
    """{name, description, parameters} for one tool function."""
    desc, pdocs = _doc(fn)
    props, required = {}, []
    try:
        hints = typing.get_type_hints(fn)                    # annotations are strings under `from __future__ import annotations`
    except Exception:  # noqa: BLE001 - an unresolvable annotation falls back to a string argument
        hints = {}
    for name, p in inspect.signature(fn).parameters.items():
        if name in SKIP_PARAMS or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        prop = _type(hints.get(name, p.annotation))
        if pdocs.get(name):
            prop["description"] = pdocs[name][:300]
        props[name] = prop
        if p.default is inspect.Parameter.empty:
            required.append(name)
    return dict(name=fn.__name__, description=desc or fn.__name__, parameters=dict(type="object", properties=props, required=required))


def openai_tools(specs: list[dict]) -> list[dict]:
    """The `tools` array of a chat-completions request."""
    return [{"type": "function", "function": s} for s in specs]
