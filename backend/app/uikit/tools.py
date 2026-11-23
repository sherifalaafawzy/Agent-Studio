"""The two tools an agent uses to show rich components: `render_ui` (draw one) and `ui_catalog` (what exists and how to ask for it).

A spec that cannot be drawn is refused with a reason the model can act on. When the data can still be shown as a table, the user sees that table instead,
so a mistake in a chart never leaves them with nothing.
"""
from __future__ import annotations

from typing import Any

from .kinds import GROUPS, KINDS
from .samples import SAMPLES
from .validate import DEFAULT_LIMITS, validate

MAX_PER_TURN = 30

UI_RULES = """

## Rich components
Show data the way a person reads it best with `render_ui` (a typed JSON spec). Prefer a table when you are unsure; use a chart for trends and comparisons, a time series
for data over time, a map for places, and a card for a single thing (weather, a quote, a person, a finding). Call `ui_catalog` with no argument to list the types
(""" + ", ".join(GROUPS) + """), and with a type (for example "chart.line") to get its exact shape and an example. Give real data from your tools; never invent numbers.
Components never load remote images, scripts or tiles. Do not repeat a component's numbers in your text: add one useful sentence instead.
"""


def fallback_table(raw: Any) -> dict | None:
    """A plain table from whatever tabular data a failed spec carried, or None."""
    if not isinstance(raw, dict):
        return None
    rows = raw.get("rows")
    if isinstance(rows, list) and rows:
        table: dict = {"type": "table", "title": str(raw.get("title") or "")[:100], "rows": rows}
        if isinstance(raw.get("columns"), list) and raw["columns"]:
            table["columns"] = raw["columns"]
        elif isinstance(rows[0], list):
            table["columns"] = [f"Column {i + 1}" for i in range(len(rows[0]))]
        return table
    data = raw.get("data")
    if isinstance(data, list) and data and all(isinstance(d, dict) for d in data):
        return {"type": "table", "title": str(raw.get("title") or "")[:100], "rows": data}
    x, series = raw.get("x"), raw.get("series")
    if isinstance(x, list) and isinstance(series, list) and series and all(isinstance(s, dict) and isinstance(s.get("values"), list) for s in series):
        names = [str(s.get("name") or f"Series {i + 1}") for i, s in enumerate(series)]
        return {"type": "table", "title": str(raw.get("title") or "")[:100], "columns": ["", *names], "rows": [[x[i], *[s["values"][i] if i < len(s["values"]) else None for s in series]] for i in range(len(x))]}
    return None


def uikit_tools(ctx) -> dict:
    svc = ctx.uikit
    off = {"ok": False, "errors": ["AG-UI components are switched off for this agent: answer in plain Markdown (tables as Markdown tables)."]}

    def limits() -> dict:
        return {**DEFAULT_LIMITS, **svc.limits()}

    async def render_ui(spec: dict) -> dict:
        """Show a chart, table, time series, map, card or layout to the user. Call ui_catalog first if you are not sure of the shape.

        Args:
            spec: The component as JSON, for example {"type": "chart.bar", "title": "Sales", "x": ["Jan", "Feb"], "series": [{"name": "2025", "values": [3, 5]}]}.
        """
        if not getattr(ctx, "agui_components", True):
            return dict(off)
        clean, errors, warnings = validate(spec, limits())
        shown_as = None
        if clean is None:
            alt = fallback_table(spec)
            alt_clean = validate(alt, limits())[0] if alt else None
            if alt_clean is None:
                return {"ok": False, "errors": errors, "hint": "Fix the spec and call render_ui again, or write a Markdown table instead. ui_catalog shows the exact shape."}
            clean, shown_as = alt_clean, "table"
        if getattr(ctx, "on_ui", None) is None or ctx.on_ui(clean) is False:
            return {"ok": False, "errors": [f"at most {MAX_PER_TURN} components can be shown in one answer"]}
        out: dict = {"ok": True, "type": clean["type"], "note": "It is shown to the user. Do not repeat its numbers in your text; add one useful sentence instead."}
        if shown_as:
            out.update(ok=False, shown_as="a plain table", errors=errors, note="The chart could not be drawn, so a plain table of the same data was shown. Tell the user only if it matters; call render_ui again with a corrected spec if you want the chart.")
        if warnings:
            out["warnings"] = warnings
        return out

    async def ui_catalog(type: str = "") -> dict:
        """List the components you can show, or describe one.

        Args:
            type: Leave empty to list every type. Give a type such as "chart.line" or "card.weather" (or a group such as "chart" or "card") for its shape and an example.
        """
        if not getattr(ctx, "agui_components", True):
            return dict(off)
        t = (type or "").strip().lower()
        if not t:
            return {"ok": True, "groups": {g: [{"type": k.type, "about": k.about} for k in KINDS.values() if k.group == g] for g in GROUPS}, "limits": limits(),
                    "note": "Call ui_catalog with a type to get its shape and an example."}
        if t in KINDS:
            k = KINDS[t]
            return {"ok": True, "type": k.type, "about": k.about, "shape": k.shape, "required": [list(g) for g in k.need], "example": SAMPLES.get(k.type), "limits": limits()}
        group = [k for k in KINDS.values() if k.group == t or k.type.startswith(t + ".")]
        if group:
            return {"ok": True, "group": t, "types": [{"type": k.type, "about": k.about} for k in group]}
        from .kinds import suggestions
        return {"ok": False, "error": f"unknown type '{t[:40]}'", "did_you_mean": suggestions(t)}

    return {"render_ui": render_ui, "ui_catalog": ui_catalog}
