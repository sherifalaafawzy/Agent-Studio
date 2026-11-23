"""Every component an agent can show, in one table.

A component is a typed JSON spec: {"type": "chart.line", "title": "...", ...}. This table is the single source of truth for which types exist, what an agent
must give for each one (`need`: at least one group of top-level fields must all be present), a one-line description and a compact description of the shape
(`shape`) that `ui_catalog` returns. The browser draws each type with a fixed, safe component (frontend/components/uikit); nothing here is ever executed.

Types: table, chart.<kind>, timeseries, map, card.<kind>, layout.<kind>, media.audio, media.video, json, html.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Kind:
    type: str
    group: str                      # table | chart | timeseries | map | card | layout | media | data
    about: str
    need: tuple[tuple[str, ...], ...]
    shape: str


def _k(type: str, group: str, about: str, need: tuple[tuple[str, ...], ...], shape: str) -> tuple[str, Kind]:
    return type, Kind(type, group, about, need, shape)


_CAT = '{"x": ["Jan","Feb"], "series": [{"name": "Sales", "values": [3, 5]}]} or {"data": [{"label": "A", "value": 3}]}; options: unit, stack ("stacked"|"percent"), orientation ("horizontal")'
_TREE = '{"root": {"name": "All", "value": 10, "children": [{"name": "A", "value": 6}]}}'

KINDS: dict[str, Kind] = dict([
    _k("table", "table", "A table you can sort, filter, page and export (CSV, JSON, Excel, Markdown).", (("rows",),),
       '{"columns": [{"key": "name", "label": "Name", "type": "text|number|date|boolean", "format": "currency|percent|bytes|duration|badge|link|progress", "color": "scale|bar"}], "rows": [["a", 1]] or [{"name": "a"}], "group_by": "column key", "pin_first": true}'),
    # ---- charts
    _k("chart.bar", "chart", "Compare values across categories (grouped, stacked, 100%, horizontal).", (("x", "series"), ("data",)), _CAT),
    _k("chart.line", "chart", "A trend over an ordered axis; several series allowed.", (("x", "series"), ("data",)), _CAT + "; step: true for a step line"),
    _k("chart.area", "chart", "A trend with the area filled; stacked or 100% for parts of a whole over time.", (("x", "series"), ("data",)), _CAT),
    _k("chart.lollipop", "chart", "Like a bar chart with thin stems; good for ranked values.", (("x", "series"), ("data",)), _CAT),
    _k("chart.dumbbell", "chart", "Two values per category (before and after) joined by a line.", (("x", "series"),), '{"x": ["A","B"], "series": [{"name": "2023", "values": [1,2]}, {"name": "2024", "values": [2,4]}]}'),
    _k("chart.slope", "chart", "How categories changed between two moments.", (("x", "series"),), '{"x": ["Before","After"], "series": [{"name": "A", "values": [3,5]}]}'),
    _k("chart.sparkline", "chart", "A tiny inline trend with no axes.", (("values",), ("series",)), '{"values": [1,3,2,5]}'),
    _k("chart.waterfall", "chart", "How a start value becomes an end value through increases and decreases.", (("data",),), '{"data": [{"label": "Start", "value": 100}, {"label": "Costs", "value": -30}, {"label": "Total", "total": true}]}'),
    _k("chart.funnel", "chart", "Stages that narrow, such as a sales pipeline.", (("data",),), '{"data": [{"label": "Visits", "value": 1000}, {"label": "Signups", "value": 120}]}'),
    _k("chart.bullet", "chart", "A value against a target and ranges.", (("data",),), '{"data": [{"label": "Revenue", "value": 270, "target": 300, "ranges": [150, 225, 300]}]}'),
    _k("chart.scatter", "chart", "Two measures per item, optionally grouped, with an optional trend line.", (("points",),), '{"points": [{"x": 1, "y": 2, "group": "A", "label": "item"}], "x_label": "", "y_label": "", "trend": true}'),
    _k("chart.bubble", "chart", "A scatter chart where size is a third measure.", (("points",),), '{"points": [{"x": 1, "y": 2, "size": 10, "group": "A"}]}'),
    _k("chart.histogram", "chart", "How many values fall in each range.", (("values",),), '{"values": [1,2,2,3,5,8], "bins": 6}'),
    _k("chart.pie", "chart", "Parts of a whole (use up to 6 slices); donut: true for a ring.", (("data",),), '{"data": [{"label": "A", "value": 60}], "donut": true}'),
    _k("chart.heatmap", "chart", "Values in a grid of two categories.", (("x", "y", "values"), ("cells",)), '{"x": ["Mon","Tue"], "y": ["AM","PM"], "values": [[1,2],[3,4]]} or {"cells": [{"x": "Mon", "y": "AM", "value": 1}]}'),
    _k("chart.calendar", "chart", "One value per day shown on a calendar grid.", (("days",),), '{"days": [{"date": "2025-01-03", "value": 4}]}'),
    _k("chart.box", "chart", "The spread of values per group (median, quartiles, range).", (("groups",),), '{"groups": [{"name": "A", "values": [1,2,3]} or {"name": "A", "stats": {"min":1,"q1":2,"median":3,"q3":4,"max":5}}]}'),
    _k("chart.candlestick", "chart", "Open, high, low and close prices per period, with optional volume.", (("data",),), '{"data": [{"t": "2025-01-02", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}]}'),
    _k("chart.sankey", "chart", "Flows between stages (value moves from one node to another).", (("links",),), '{"nodes": [{"name": "A"}], "links": [{"source": "A", "target": "B", "value": 5}]}'),
    _k("chart.treemap", "chart", "A hierarchy as nested rectangles sized by value.", (("root",),), _TREE),
    _k("chart.sunburst", "chart", "A hierarchy as nested rings.", (("root",),), _TREE),
    _k("chart.tree", "chart", "A hierarchy as a node-link tree (org chart, file tree).", (("root",),), _TREE),
    _k("chart.radar", "chart", "Several measures per item on spokes (use sparingly).", (("axes", "series"),), '{"axes": [{"name": "Speed", "max": 10}], "series": [{"name": "A", "values": [7]}]}'),
    _k("chart.gauge", "chart", "One value on a dial with coloured bands.", (("value",),), '{"value": 72, "min": 0, "max": 100, "unit": "%", "bands": [{"to": 50, "label": "low"}, {"to": 100, "label": "high"}]}'),
    _k("chart.network", "chart", "Nodes and the links between them.", (("links",),), '{"nodes": [{"id": "a", "label": "A", "group": "x"}], "links": [{"source": "a", "target": "b", "value": 1}]}'),
    _k("chart.gantt", "chart", "Tasks as bars on a time axis.", (("tasks",),), '{"tasks": [{"name": "Design", "start": "2025-01-01", "end": "2025-01-10", "group": "Team A", "progress": 0.5}]}'),
    # ---- time series, map
    _k("timeseries", "timeseries", "One or more time series with zoom, range buttons, annotations, anomalies and forecast bands.", (("series",),),
       '{"series": [{"name": "Sales", "unit": "USD", "kind": "line|area|step|bar", "points": [["2025-01-01", 10], ...]}], "annotations": [{"t": "2025-02-01", "label": "Launch", "end": "optional, makes a band"}], '
       '"anomalies": [{"series": "Sales", "t": "...", "label": ""}], "thresholds": [{"value": 100, "label": "Target"}], "forecast": {"series": "Sales", "timestamps": [...], "median": [...], "quantiles": {"0.1": [...], "0.9": [...]}}}'),
    _k("map", "map", "A map with points, shapes, heatmaps, routes, arcs and coloured regions; works offline with a built-in world outline.", (("layers",),),
       '{"center": [lon, lat], "zoom": 3, "layers": [{"kind": "points|geojson|choropleth|heat|cluster|route|bubble|arc|hexbin", "name": "", "data": ...}], "time": {"field": "t"}}; see the example for each layer kind'),
    # ---- cards
    _k("card.weather", "card", "Current weather, an hourly strip and a daily forecast.", (("current",),), '{"place": "Cairo", "units": "C", "current": {"temp": 24, "condition": "Sunny", "feels_like": 25, "humidity": 40, "wind": 12}, "hourly": [{"t": "14:00", "temp": 25}], "daily": [{"day": "Mon", "high": 27, "low": 18, "condition": "Sunny", "precip": 0}], "alerts": [""]}'),
    _k("card.forecast", "card", "Several days of forecast as a compact chart.", (("days",),), '{"place": "", "units": "C", "days": [{"day": "Mon", "high": 27, "low": 18, "precip": 10}]}'),
    _k("card.quote", "card", "A price quote for a stock, fund, coin or currency with change and a sparkline (data only, never advice).", (("symbol", "price"),), '{"symbol": "ACME", "name": "", "price": 12.3, "currency": "USD", "change": 0.4, "change_pct": 3.4, "spark": [1,2,3], "as_of": "2025-01-02 16:00 UTC", "delayed": true}'),
    _k("card.kpi", "card", "One headline number with change, target and trend.", (("label", "value"),), '{"label": "Revenue", "value": 1200, "unit": "USD", "delta": 5.2, "delta_label": "vs last month", "target": 1500, "spark": [1,2,3], "status": "good|warn|bad"}'),
    _k("card.kpis", "card", "A grid of 2 to 12 headline numbers.", (("items",),), '{"items": [{"label": "", "value": 1, "unit": "", "delta": 0}]}'),
    _k("card.compare", "card", "Two to four options side by side.", (("items", "rows"),), '{"items": ["A", "B"], "rows": [{"label": "Price", "values": ["$1", "$2"], "best": 0}]}'),
    _k("card.person", "card", "A person: name, role, contact and links.", (("name",),), '{"name": "", "role": "", "org": "", "email": "", "phone": "", "links": [{"label": "", "url": "https://..."}], "image": "artifact id or data image"}'),
    _k("card.company", "card", "An organisation with key facts.", (("name",),), '{"name": "", "about": "", "facts": [{"label": "Founded", "value": "2010"}], "links": []}'),
    _k("card.product", "card", "A product with price, specs and pros and cons.", (("name",),), '{"name": "", "price": "", "image": "", "specs": [{"label": "", "value": ""}], "pros": [""], "cons": [""], "url": ""}'),
    _k("card.source", "card", "A cited source: title, publisher, date, quote and link.", (("title",),), '{"title": "", "publisher": "", "date": "", "snippet": "", "url": "https://...", "note": ""}'),
    _k("card.results", "card", "A ranked list of search results with sources.", (("items",),), '{"query": "", "items": [{"title": "", "url": "", "snippet": "", "source": ""}]}'),
    _k("card.news", "card", "A headline with bullet summary and source.", (("headline",),), '{"headline": "", "bullets": [""], "source": "", "time": "", "url": ""}'),
    _k("card.file", "card", "A file: name, type, size and an optional artifact link.", (("name",),), '{"name": "", "kind": "pdf", "size": 1234, "artifact": "12-char id", "note": ""}'),
    _k("card.code", "card", "A code snippet with language, copy button and highlighted lines.", (("code",),), '{"language": "python", "code": "", "highlight": [1, 3], "filename": ""}'),
    _k("card.timeline", "card", "Events in order of time.", (("events",),), '{"events": [{"t": "2025-01-01", "title": "", "detail": "", "status": "done|active|todo"}]}'),
    _k("card.event", "card", "A calendar event or a day agenda.", (("items",), ("title", "start")), '{"title": "", "start": "2025-01-01 10:00", "end": "", "where": ""} or {"date": "", "items": [{"title": "", "start": "", "end": "", "conflict": false}]}'),
    _k("card.tasks", "card", "A checklist with progress; ticking an item sends a message back.", (("items",),), '{"title": "", "items": [{"text": "", "done": false, "due": ""}]}'),
    _k("card.kanban", "card", "A board of columns and cards.", (("columns",),), '{"columns": [{"name": "To do", "cards": [{"title": "", "tag": ""}]}]}'),
    _k("card.steps", "card", "A process with a status for each step.", (("steps",),), '{"steps": [{"name": "", "status": "done|active|todo|failed", "detail": ""}]}'),
    _k("card.alert", "card", "A notice: info, success, warning or error (always icon plus text).", (("text",),), '{"level": "info|success|warning|error", "title": "", "text": ""}'),
    _k("card.itinerary", "card", "A trip: flights, hotels and times.", (("legs",),), '{"title": "", "legs": [{"kind": "flight|hotel|train|car", "from": "", "to": "", "start": "", "end": "", "ref": "", "note": ""}]}'),
    _k("card.place", "card", "A place with address, hours, rating and a small map.", (("name",),), '{"name": "", "address": "", "hours": "", "rating": 4.5, "lat": 0, "lon": 0, "url": ""}'),
    _k("card.recipe", "card", "A recipe with scalable ingredients, steps and nutrition.", (("ingredients", "steps"),), '{"title": "", "servings": 4, "time": "30 min", "ingredients": [{"item": "", "qty": 1, "unit": ""}], "steps": [""], "nutrition": {"kcal": 0}}'),
    _k("card.receipt", "card", "An invoice or receipt with line items and totals.", (("items",),), '{"title": "", "currency": "USD", "items": [{"name": "", "qty": 1, "price": 1}], "tax": 0, "total": 0, "meta": [{"label": "", "value": ""}]}'),
    _k("card.poll", "card", "A question with choices; choosing sends the answer as your next message.", (("question", "options"),), '{"question": "", "options": ["A", "B"]}'),
    _k("card.definition", "card", "A term and its meaning.", (("term", "meaning"),), '{"term": "", "meaning": "", "source": ""}'),
    _k("card.faq", "card", "Questions and answers that expand.", (("items",),), '{"items": [{"q": "", "a": ""}]}'),
    _k("card.toc", "card", "A summary with jump-style sections.", (("items",),), '{"title": "", "items": [{"title": "", "summary": ""}]}'),
    _k("card.scorecard", "card", "A score out of 100 with its parts (health, SLO, security score).", (("score",),), '{"title": "", "score": 82, "parts": [{"label": "", "score": 90}], "note": ""}'),
    _k("card.countdown", "card", "A countdown to a moment, or a timer.", (("target",), ("seconds",)), '{"label": "", "target": "2026-01-01T00:00:00Z"} or {"label": "", "seconds": 300}'),
    _k("card.equation", "card", "A mathematical formula written in LaTeX.", (("latex",),), '{"latex": "E = mc^2", "caption": ""}'),
    _k("card.molecule", "card", "A molecule or structure: name, formula, SMILES and properties.", (("name",),), '{"name": "", "formula": "", "smiles": "", "properties": [{"label": "", "value": ""}]}'),
    _k("card.request", "card", "An API call and its response (secrets are removed).", (("method", "url"),), '{"method": "GET", "url": "https://...", "status": 200, "ms": 120, "headers": {"accept": "..."}, "request": {}, "response": {}}'),
    _k("card.log", "card", "Log or terminal output with search.", (("lines",), ("text",)), '{"lines": [""]} or {"text": ""}'),
    _k("card.finding", "card", "A security or quality finding with severity and a fix.", (("title", "severity"),), '{"title": "", "severity": "critical|high|medium|low|info", "evidence": "", "fix": "", "refs": ["LLM01"]}'),
    _k("card.usage", "card", "Tokens and spend, with the cap.", (("spend",), ("tokens",)), '{"spend": 1.2, "cap": 5, "tokens": 12000, "by_model": [{"model": "", "spend": 0.4}]}'),
    _k("card.run", "card", "A run of an agent or workflow: status, steps and time.", (("name", "status"),), '{"name": "", "status": "ok|running|failed|waiting", "steps": 5, "duration_s": 12, "cost_usd": 0.01, "url": "/runs/"}'),
    _k("card.approval", "card", "Asks the user to approve or reject an action; the answer is sent as your next message.", (("action",),), '{"action": "", "risk": "low|medium|high", "details": [{"label": "", "value": ""}], "approve": "Approve", "reject": "Reject"}'),
    _k("card.email", "card", "An email draft preview (it never sends anything).", (("subject", "body"),), '{"to": [""], "cc": [], "subject": "", "body": ""}'),
    _k("card.palette", "card", "Colour swatches with hex codes and contrast.", (("colors",),), '{"colors": [{"name": "", "hex": "#2a78d6"}]}'),
    _k("card.qr", "card", "A QR code for some text or a link.", (("text",),), '{"text": "https://...", "caption": ""}'),
    _k("card.sports", "card", "A match score or a league table.", (("home", "away"), ("table",)), '{"home": {"name": "", "score": 1}, "away": {"name": "", "score": 0}, "status": "FT", "when": ""} or {"table": [{"team": "", "p": 1, "w": 1, "d": 0, "l": 0, "pts": 3}]}'),
    # ---- layouts
    _k("layout.stack", "layout", "Show several components one under the other.", (("children",),), '{"children": [{component}, ...]}'),
    _k("layout.grid", "layout", "Show several components in a grid (small multiples, dashboards).", (("children",),), '{"columns": 2, "children": [{component}, ...]}'),
    _k("layout.tabs", "layout", "Show several components as tabs.", (("tabs",),), '{"tabs": [{"label": "", "child": {component}}]}'),
    _k("layout.carousel", "layout", "Show several components one at a time with arrows.", (("children",),), '{"children": [{component}, ...]}'),
    _k("layout.split", "layout", "Two components side by side.", (("left", "right"),), '{"left": {component}, "right": {component}}'),
    # ---- media and data views
    _k("media.audio", "media", "Play an audio file you saved as an artifact.", (("artifact",),), '{"artifact": "12-char artifact id", "title": "", "captions": "optional artifact id of a .vtt"}'),
    _k("media.video", "media", "Play a video file you saved as an artifact.", (("artifact",),), '{"artifact": "12-char artifact id", "title": "", "captions": ""}'),
    _k("json", "data", "Any JSON as a collapsible tree.", (("value",),), '{"value": {...any JSON...}}'),
    _k("html", "data", "A small self-contained HTML page in a locked frame (no scripts, no network).", (("html",),), '{"html": "<h1>...</h1>"}'),
])

GROUPS = ["table", "chart", "timeseries", "map", "card", "layout", "media", "data"]


def canonical(raw_type: object, raw: dict | None = None) -> str | None:
    """`chart.line`, `chart` + kind `line` and `Chart.Line` all become `chart.line`; None if it names no known component."""
    t = str(raw_type or "").strip().lower().replace("/", ".").replace(" ", "")
    if t in KINDS:
        return t
    kind = str((raw or {}).get("kind") or "").strip().lower()
    if kind and f"{t}.{kind}" in KINDS:
        return f"{t}.{kind}"
    return None


def suggestions(raw_type: object) -> list[str]:
    import difflib
    t = str(raw_type or "").strip().lower()
    return difflib.get_close_matches(t, list(KINDS), n=4, cutoff=0.5) or [k for k in KINDS if k.startswith(t.split(".")[0])][:4]
