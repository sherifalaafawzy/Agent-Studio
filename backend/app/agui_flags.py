"""Per-agent AG-UI switches: the one place that decides what an agent may do with AG-UI.

`components`: the agent can show rich components (the `render_ui` and `ui_catalog` tools, the instruction text that teaches them, and the
component/rich blocks of its answers over AG-UI). When off, its answers are plain Markdown.
`endpoint`: the agent can be run through the AG-UI event stream (POST /api/agui) by an external AG-UI client.

Both default to on, so agents that existed before the switch behave as they always did.
"""
from __future__ import annotations

from typing import Any

UI_TOOLS = ("render_ui", "ui_catalog")
DEFAULT: dict[str, bool] = {"components": True, "endpoint": True}


def normalise(raw: Any) -> dict[str, bool]:
    """The full, checked setting for an API request. `None` or `{}` mean the defaults."""
    if not raw:
        return dict(DEFAULT)
    if not isinstance(raw, dict):
        raise ValueError("agui must be an object such as {\"components\": true, \"endpoint\": true}")
    for k, v in raw.items():
        if k not in DEFAULT:
            raise ValueError(f"unknown AG-UI setting '{str(k)[:40]}': use components and endpoint")
        if not isinstance(v, bool):
            raise ValueError(f"agui.{k} must be true or false")
    return {**DEFAULT, **raw}


def flags(raw: Any) -> dict[str, bool]:
    """The full setting from stored data: tolerant (a bad or missing value falls back to on)."""
    out = dict(DEFAULT)
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k in DEFAULT and isinstance(v, bool)})
    return out


def components_on(agent: Any) -> bool:
    return flags(getattr(agent, "agui", None))["components"]


def endpoint_on(agent: Any) -> bool:
    return flags(getattr(agent, "agui", None))["endpoint"]


def tools_for(agent: Any) -> list[str]:
    """The tool names an agent really gets: its own list, minus the component tools when components are off."""
    names = list(agent.tools)
    return names if components_on(agent) else [t for t in names if t not in UI_TOOLS]
