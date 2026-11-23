"""One small, realistic example for every component type.

They are what `ui_catalog` shows a model, what the /components gallery draws, and what the tests render. The browser reads a generated copy
(frontend/lib/uikit/samples.generated.ts, made by scripts/gen_uikit_samples.py); a test fails when the copy is out of date.
"""
from __future__ import annotations

import math

_M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]


def _days(n: int, start: str = "2025-03-01") -> list[str]:
    import datetime
    d0 = datetime.date.fromisoformat(start)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


def _wave(n: int, base: float, amp: float, phase: float = 0.0) -> list[float]:
    return [round(base + amp * math.sin(i / 5 + phase) + i * 0.15, 2) for i in range(n)]


_TREE = {"name": "Spend", "children": [
    {"name": "Compute", "children": [{"name": "GPU", "value": 40}, {"name": "CPU", "value": 25}]},
    {"name": "Storage", "children": [{"name": "Hot", "value": 18}, {"name": "Cold", "value": 8}]},
    {"name": "Network", "value": 9}]}

_DAYS = _days(40)
_TS = _wave(40, 100, 12)

SAMPLES: dict[str, dict] = {
    "table": {"type": "table", "title": "Q3 sales by region", "columns": [
        {"key": "region", "label": "Region", "type": "text"}, {"key": "revenue", "label": "Revenue", "type": "number", "format": "currency", "currency": "USD", "color": "bar"},
        {"key": "growth", "label": "Growth", "type": "number", "format": "percent", "color": "scale"}, {"key": "status", "label": "Status", "format": "badge"}],
        "rows": [["EMEA", 1200000, 0.12, "on track"], ["Americas", 2100000, 0.08, "on track"], ["APAC", 860000, -0.03, "at risk"], ["LATAM", 430000, 0.21, "on track"]]},
    "chart.bar": {"type": "chart.bar", "title": "Revenue by month", "unit": "k USD", "x": _M, "series": [{"name": "2024", "values": [12, 15, 14, 18, 20, 22]}, {"name": "2025", "values": [14, 17, 19, 21, 26, 30]}]},
    "chart.line": {"type": "chart.line", "title": "Weekly active users", "x": _M, "series": [{"name": "Web", "values": [120, 135, 150, 148, 170, 190]}, {"name": "Mobile", "values": [80, 95, 120, 140, 160, 185]}]},
    "chart.area": {"type": "chart.area", "title": "Traffic by source", "stack": "stacked", "x": _M, "series": [{"name": "Search", "values": [30, 34, 40, 42, 48, 55]}, {"name": "Direct", "values": [20, 22, 21, 25, 27, 30]}]},
    "chart.lollipop": {"type": "chart.lollipop", "title": "Top products", "data": [{"label": "Alpha", "value": 90}, {"label": "Beta", "value": 72}, {"label": "Gamma", "value": 55}, {"label": "Delta", "value": 31}]},
    "chart.dumbbell": {"type": "chart.dumbbell", "title": "Score before and after", "x": ["Team A", "Team B", "Team C"], "series": [{"name": "Before", "values": [52, 61, 47]}, {"name": "After", "values": [70, 66, 72]}]},
    "chart.slope": {"type": "chart.slope", "title": "Rank change", "x": ["2024", "2025"], "series": [{"name": "Alpha", "values": [3, 1]}, {"name": "Beta", "values": [1, 2]}, {"name": "Gamma", "values": [2, 3]}]},
    "chart.sparkline": {"type": "chart.sparkline", "title": "Latency", "values": [12, 14, 13, 18, 16, 21, 19, 24]},
    "chart.waterfall": {"type": "chart.waterfall", "title": "Profit bridge", "unit": "k USD", "data": [{"label": "Revenue", "value": 500}, {"label": "Costs", "value": -320}, {"label": "Tax", "value": -40}, {"label": "Other", "value": 25}, {"label": "Profit", "total": True}]},
    "chart.funnel": {"type": "chart.funnel", "title": "Sales funnel", "data": [{"label": "Visits", "value": 12000}, {"label": "Signups", "value": 2400}, {"label": "Trials", "value": 900}, {"label": "Paid", "value": 310}]},
    "chart.bullet": {"type": "chart.bullet", "title": "Targets", "data": [{"label": "Revenue", "value": 270, "target": 300, "ranges": [150, 225, 300]}, {"label": "Profit", "value": 22, "target": 27, "ranges": [15, 22, 30]}]},
    "chart.scatter": {"type": "chart.scatter", "title": "Height vs weight", "x_label": "Height (cm)", "y_label": "Weight (kg)", "trend": True,
                      "points": [{"x": 150 + i * 2, "y": round(48 + i * 1.4 + (i % 3) * 2.5, 1), "group": "A" if i % 2 else "B"} for i in range(24)]},
    "chart.bubble": {"type": "chart.bubble", "title": "Markets", "x_label": "Growth %", "y_label": "Margin %", "points": [
        {"x": 4, "y": 12, "size": 40, "label": "EMEA", "group": "A"}, {"x": 9, "y": 8, "size": 70, "label": "APAC", "group": "B"}, {"x": 6, "y": 15, "size": 25, "label": "LATAM", "group": "A"}]},
    "chart.histogram": {"type": "chart.histogram", "title": "Response time", "unit": "ms", "bins": 8, "values": [round(80 + 40 * math.sin(i) + i * 3, 1) for i in range(60)]},
    "chart.pie": {"type": "chart.pie", "title": "Spend by type", "donut": True, "data": [{"label": "Compute", "value": 65}, {"label": "Storage", "value": 26}, {"label": "Network", "value": 9}]},
    "chart.heatmap": {"type": "chart.heatmap", "title": "Support tickets by hour", "x": ["Mon", "Tue", "Wed", "Thu", "Fri"], "y": ["Morning", "Noon", "Evening"],
                      "values": [[12, 18, 15, 20, 9], [22, 25, 30, 28, 18], [8, 10, 12, 9, 5]]},
    "chart.calendar": {"type": "chart.calendar", "title": "Commits per day", "days": [{"date": d, "value": (i * 7) % 11} for i, d in enumerate(_DAYS)]},
    "chart.box": {"type": "chart.box", "title": "Delivery time by carrier", "unit": "days", "groups": [
        {"name": "Fast", "values": [1, 2, 2, 3, 3, 4, 9]}, {"name": "Standard", "stats": {"min": 2, "q1": 3, "median": 5, "q3": 7, "max": 12, "outliers": [16]}}]},
    "chart.candlestick": {"type": "chart.candlestick", "title": "ACME daily", "data": [
        {"t": d, "open": round(p, 2), "high": round(p + 1.4, 2), "low": round(p - 1.1, 2), "close": round(p + (0.6 if i % 3 else -0.5), 2), "volume": 1000 + i * 37} for i, (d, p) in enumerate(zip(_DAYS[:20], _wave(20, 50, 3)))]},
    "chart.sankey": {"type": "chart.sankey", "title": "Where the budget goes", "nodes": [{"name": "Budget"}, {"name": "Team A"}, {"name": "Team B"}, {"name": "Salaries"}, {"name": "Tools"}],
                     "links": [{"source": "Budget", "target": "Team A", "value": 60}, {"source": "Budget", "target": "Team B", "value": 40}, {"source": "Team A", "target": "Salaries", "value": 45},
                               {"source": "Team A", "target": "Tools", "value": 15}, {"source": "Team B", "target": "Salaries", "value": 30}, {"source": "Team B", "target": "Tools", "value": 10}]},
    "chart.treemap": {"type": "chart.treemap", "title": "Cloud spend", "root": _TREE},
    "chart.sunburst": {"type": "chart.sunburst", "title": "Cloud spend", "root": _TREE},
    "chart.tree": {"type": "chart.tree", "title": "Org chart", "root": {"name": "CEO", "children": [{"name": "CTO", "children": [{"name": "Platform"}, {"name": "Data"}]}, {"name": "CFO", "children": [{"name": "Finance"}]}]}},
    "chart.radar": {"type": "chart.radar", "title": "Model comparison", "axes": [{"name": "Speed", "max": 10}, {"name": "Quality", "max": 10}, {"name": "Cost", "max": 10}, {"name": "Context", "max": 10}],
                    "series": [{"name": "Model A", "values": [8, 6, 9, 5]}, {"name": "Model B", "values": [5, 9, 4, 9]}]},
    "chart.gauge": {"type": "chart.gauge", "title": "Disk used", "value": 72, "min": 0, "max": 100, "unit": "%", "bands": [{"to": 60, "label": "OK"}, {"to": 85, "label": "Watch"}, {"to": 100, "label": "Full"}]},
    "chart.network": {"type": "chart.network", "title": "Service calls", "nodes": [{"id": "web", "label": "Web", "group": "edge"}, {"id": "api", "label": "API", "group": "core"}, {"id": "db", "label": "DB", "group": "data"}, {"id": "q", "label": "Queue", "group": "core"}],
                      "links": [{"source": "web", "target": "api", "value": 5}, {"source": "api", "target": "db", "value": 3}, {"source": "api", "target": "q", "value": 2}]},
    "chart.gantt": {"type": "chart.gantt", "title": "Launch plan", "tasks": [
        {"name": "Design", "start": "2025-03-01", "end": "2025-03-12", "group": "Product", "progress": 1}, {"name": "Build", "start": "2025-03-10", "end": "2025-04-05", "group": "Eng", "progress": 0.55},
        {"name": "Launch", "start": "2025-04-06", "end": "2025-04-10", "group": "Marketing", "progress": 0}]},
    "timeseries": {"type": "timeseries", "title": "Daily sales", "series": [{"name": "Sales", "unit": "USD", "kind": "line", "points": [[d, v] for d, v in zip(_DAYS, _TS)]}],
                   "annotations": [{"t": _DAYS[12], "label": "Promotion"}, {"t": _DAYS[25], "end": _DAYS[29], "label": "Outage"}], "anomalies": [{"series": "Sales", "t": _DAYS[18], "label": "spike"}],
                   "thresholds": [{"value": 110, "label": "Target"}],
                   "forecast": {"series": "Sales", "timestamps": _days(7, "2025-04-10"), "median": [112, 113, 115, 114, 116, 118, 119], "quantiles": {"0.1": [104, 104, 105, 104, 105, 106, 106], "0.9": [120, 122, 125, 125, 128, 131, 133]}}},
    "map": {"type": "map", "title": "Offices and routes", "center": [20, 30], "zoom": 1.6, "layers": [
        {"kind": "choropleth", "name": "Users by country", "legend": "Users (k)", "values": {"USA": 120, "BRA": 60, "IND": 90, "DEU": 40, "EGY": 25, "AUS": 18}},
        {"kind": "points", "name": "Offices", "points": [{"lat": 30.04, "lon": 31.24, "label": "Cairo", "detail": "Regional HQ"}, {"lat": 51.5, "lon": -0.12, "label": "London"}, {"lat": 40.71, "lon": -74.0, "label": "New York"}]},
        {"kind": "arc", "name": "Traffic", "arcs": [{"from": [31.24, 30.04], "to": [-0.12, 51.5], "value": 5}, {"from": [31.24, 30.04], "to": [-74.0, 40.71], "value": 3}]}]},
    # ---- cards
    "card.weather": {"type": "card.weather", "place": "Cairo", "units": "C", "current": {"temp": 24, "condition": "Sunny", "feels_like": 25, "humidity": 38, "wind": 14, "uv": 7, "aqi": 62, "sunrise": "05:42", "sunset": "18:21"},
                     "hourly": [{"t": f"{h}:00", "temp": 24 + (h % 5), "condition": "Sunny"} for h in range(12, 20)],
                     "daily": [{"day": "Mon", "high": 27, "low": 18, "condition": "Sunny", "precip": 0}, {"day": "Tue", "high": 26, "low": 17, "condition": "Cloudy", "precip": 10}, {"day": "Wed", "high": 24, "low": 16, "condition": "Rain", "precip": 70}],
                     "alerts": ["Heat advisory until 18:00"]},
    "card.forecast": {"type": "card.forecast", "place": "London", "units": "C", "days": [{"day": "Mon", "high": 14, "low": 7, "precip": 20}, {"day": "Tue", "high": 12, "low": 6, "precip": 60}, {"day": "Wed", "high": 15, "low": 8, "precip": 5}]},
    "card.quote": {"type": "card.quote", "symbol": "ACME", "name": "Acme Corp", "price": 123.45, "currency": "USD", "change": 2.1, "change_pct": 1.73, "spark": [118, 119, 121, 120, 123, 124], "as_of": "2025-03-04 16:00 UTC", "delayed": True},
    "card.kpi": {"type": "card.kpi", "label": "Monthly revenue", "value": 128400, "unit": "USD", "delta": 5.2, "delta_label": "vs last month", "target": 150000, "spark": [100, 108, 113, 120, 125, 128], "status": "good"},
    "card.kpis": {"type": "card.kpis", "items": [{"label": "Users", "value": 12840, "delta": 3.1}, {"label": "Churn", "value": 2.4, "unit": "%", "delta": -0.3}, {"label": "NPS", "value": 41, "delta": 2}, {"label": "Uptime", "value": 99.97, "unit": "%"}]},
    "card.compare": {"type": "card.compare", "items": ["Plan A", "Plan B"], "rows": [{"label": "Price", "values": ["$10", "$18"], "best": 0}, {"label": "Storage", "values": ["50 GB", "200 GB"], "best": 1}, {"label": "Support", "values": ["Email", "24/7"], "best": 1}]},
    "card.person": {"type": "card.person", "name": "Dana Whitfield", "role": "Head of Data", "org": "Northwind", "email": "dana@example.com", "phone": "+1 555 0100", "links": [{"label": "Profile", "url": "https://example.com/dana"}]},
    "card.company": {"type": "card.company", "name": "Northwind Traders", "about": "Wholesale food distribution.", "facts": [{"label": "Founded", "value": "1998"}, {"label": "Employees", "value": "1,200"}, {"label": "HQ", "value": "Seattle"}], "links": [{"label": "Website", "url": "https://example.com"}]},
    "card.product": {"type": "card.product", "name": "Trail Pro 2 backpack", "price": "$149", "specs": [{"label": "Volume", "value": "32 L"}, {"label": "Weight", "value": "1.1 kg"}], "pros": ["Light", "Waterproof"], "cons": ["Pricey"], "url": "https://example.com/p"},
    "card.source": {"type": "card.source", "title": "Annual climate report", "publisher": "Example Institute", "date": "2025-01-12", "snippet": "Average temperatures rose 0.2 degrees over the decade.", "url": "https://example.com/report", "note": "Primary source"},
    "card.results": {"type": "card.results", "query": "best static site generators", "items": [{"title": "Eleventy", "url": "https://example.com/11ty", "snippet": "A simple static site generator.", "source": "example.com"}, {"title": "Astro", "url": "https://example.com/astro", "snippet": "Content-focused web framework.", "source": "example.com"}]},
    "card.news": {"type": "card.news", "headline": "City approves new transit line", "bullets": ["Construction starts in 2026", "Budget of 2.1 billion", "Opens in 2030"], "source": "Example News", "time": "2 hours ago", "url": "https://example.com/news"},
    "card.file": {"type": "card.file", "name": "q3-report.pdf", "kind": "pdf", "size": 482113, "note": "Draft for review"},
    "card.code": {"type": "card.code", "language": "python", "filename": "hello.py", "highlight": [2], "code": "def hello(name):\n    return f\"Hello, {name}\"\n\nprint(hello(\"world\"))"},
    "card.timeline": {"type": "card.timeline", "events": [{"t": "2025-01-05", "title": "Kickoff", "detail": "Scope agreed", "status": "done"}, {"t": "2025-02-10", "title": "Beta", "status": "active"}, {"t": "2025-03-30", "title": "Launch", "status": "todo"}]},
    "card.event": {"type": "card.event", "date": "Tue 4 Mar", "items": [{"title": "Standup", "start": "09:30", "end": "09:45"}, {"title": "Design review", "start": "10:00", "end": "11:00", "conflict": True}, {"title": "1:1", "start": "10:30", "end": "11:00", "conflict": True}]},
    "card.tasks": {"type": "card.tasks", "title": "Launch checklist", "items": [{"text": "Write release notes", "done": True}, {"text": "Update docs", "done": False, "due": "Fri"}, {"text": "Announce", "done": False}]},
    "card.kanban": {"type": "card.kanban", "columns": [{"name": "To do", "cards": [{"title": "Fix login bug", "tag": "bug"}]}, {"name": "Doing", "cards": [{"title": "New dashboard", "tag": "feature"}]}, {"name": "Done", "cards": [{"title": "Docs"}]}]},
    "card.steps": {"type": "card.steps", "steps": [{"name": "Plan", "status": "done"}, {"name": "Build", "status": "active", "detail": "2 of 5 modules"}, {"name": "Test", "status": "todo"}, {"name": "Ship", "status": "todo"}]},
    "card.alert": {"type": "card.alert", "level": "warning", "title": "Spend cap close", "text": "You have used 92% of this month's cap."},
    "card.itinerary": {"type": "card.itinerary", "title": "Cairo to London", "legs": [{"kind": "flight", "from": "CAI", "to": "LHR", "start": "2025-05-01 08:10", "end": "2025-05-01 12:30", "ref": "MS777"}, {"kind": "hotel", "from": "Central London hotel", "to": "", "start": "2025-05-01", "end": "2025-05-04", "note": "Breakfast included"}]},
    "card.place": {"type": "card.place", "name": "Cafe Lumiere", "address": "12 Rue Cler, Paris", "hours": "Mon-Sat 8:00-18:00", "rating": 4.6, "lat": 48.8566, "lon": 2.3522},
    "card.recipe": {"type": "card.recipe", "title": "Simple tomato pasta", "servings": 2, "time": "20 min", "ingredients": [{"item": "Pasta", "qty": 200, "unit": "g"}, {"item": "Tomatoes", "qty": 4, "unit": ""}, {"item": "Olive oil", "qty": 2, "unit": "tbsp"}], "steps": ["Boil the pasta.", "Cook the tomatoes in oil.", "Combine and serve."], "nutrition": {"kcal": 520, "protein_g": 17}},
    "card.receipt": {"type": "card.receipt", "title": "Invoice 1042", "currency": "USD", "items": [{"name": "Design work", "qty": 10, "price": 80}, {"name": "Hosting", "qty": 1, "price": 25}], "tax": 82.5, "total": 907.5, "meta": [{"label": "Due", "value": "2025-04-01"}]},
    "card.poll": {"type": "card.poll", "question": "Which option do you prefer?", "options": ["Option A", "Option B", "Option C"]},
    "card.definition": {"type": "card.definition", "term": "Latency", "meaning": "The time between a request and the first part of the answer.", "source": "Glossary"},
    "card.faq": {"type": "card.faq", "items": [{"q": "Is my data private?", "a": "It stays on this PC unless you connect a cloud service."}, {"q": "Can I undo changes?", "a": "Yes, versions are kept."}]},
    "card.toc": {"type": "card.toc", "title": "Report outline", "items": [{"title": "Summary", "summary": "Key findings in one page."}, {"title": "Method", "summary": "How the data was collected."}, {"title": "Results"}]},
    "card.scorecard": {"type": "card.scorecard", "title": "Security score", "score": 82, "parts": [{"label": "Guardrails", "score": 90}, {"label": "Identity", "score": 75}, {"label": "Data", "score": 80}], "note": "2 checks need attention."},
    "card.countdown": {"type": "card.countdown", "label": "Timer", "seconds": 300},
    "card.equation": {"type": "card.equation", "latex": "E = mc^2", "caption": "Mass-energy equivalence"},
    "card.molecule": {"type": "card.molecule", "name": "Caffeine", "formula": "C8H10N4O2", "smiles": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C", "properties": [{"label": "Molar mass", "value": "194.19 g/mol"}, {"label": "Melting point", "value": "235 C"}]},
    "card.request": {"type": "card.request", "method": "POST", "url": "https://api.example.com/v1/orders", "status": 201, "ms": 184, "headers": {"accept": "application/json", "authorization": "Bearer abc"}, "request": {"sku": "A1", "qty": 2}, "response": {"id": "o_1", "ok": True}},
    "card.log": {"type": "card.log", "lines": ["2025-03-04 10:00:01 INFO  starting", "2025-03-04 10:00:02 WARN  slow query 840ms", "2025-03-04 10:00:03 ERROR timeout calling billing", "2025-03-04 10:00:04 INFO  retry ok"]},
    "card.finding": {"type": "card.finding", "title": "Prompt injection through web content", "severity": "high", "evidence": "The agent follows instructions found in fetched pages.", "fix": "Turn on the injection guardrail for tool results.", "refs": ["LLM01", "ASI01"]},
    "card.usage": {"type": "card.usage", "spend": 3.42, "cap": 10, "tokens": 482000, "by_model": [{"model": "model-a", "spend": 2.1}, {"model": "model-b", "spend": 1.32}]},
    "card.run": {"type": "card.run", "name": "Research agent", "status": "ok", "steps": 7, "duration_s": 42, "cost_usd": 0.031, "url": "/runs/"},
    "card.approval": {"type": "card.approval", "action": "Delete 3 files from the workspace", "risk": "medium", "details": [{"label": "Files", "value": "tmp/a.txt, tmp/b.txt, tmp/c.txt"}], "approve": "Approve", "reject": "Reject"},
    "card.email": {"type": "card.email", "to": ["sam@example.com"], "cc": [], "subject": "Meeting notes", "body": "Hi Sam,\n\nHere are the notes from today.\n\nBest,\nAlex"},
    "card.palette": {"type": "card.palette", "colors": [{"name": "Blue", "hex": "#2a78d6"}, {"name": "Orange", "hex": "#eb6834"}, {"name": "Green", "hex": "#1baf7a"}, {"name": "Ink", "hex": "#1f1e1d"}]},
    "card.qr": {"type": "card.qr", "text": "https://example.com/join", "caption": "Scan to join"},
    "card.sports": {"type": "card.sports", "home": {"name": "Reds", "score": 2}, "away": {"name": "Blues", "score": 1}, "status": "Full time", "when": "Sat 15:00"},
    # ---- layouts, media, data
    "layout.stack": {"type": "layout.stack", "children": [{"type": "card.alert", "level": "info", "text": "Two parts follow."}, {"type": "card.kpi", "label": "Users", "value": 1200}]},
    "layout.grid": {"type": "layout.grid", "columns": 2, "children": [{"type": "card.kpi", "label": "Users", "value": 1200}, {"type": "card.kpi", "label": "Orders", "value": 310}, {"type": "chart.sparkline", "values": [1, 3, 2, 5]}, {"type": "card.alert", "level": "success", "text": "All systems normal."}]},
    "layout.tabs": {"type": "layout.tabs", "tabs": [{"label": "Chart", "child": {"type": "chart.bar", "data": [{"label": "A", "value": 3}, {"label": "B", "value": 5}]}}, {"label": "Table", "child": {"type": "table", "columns": ["Name", "Value"], "rows": [["A", 3], ["B", 5]]}}]},
    "layout.carousel": {"type": "layout.carousel", "children": [{"type": "card.definition", "term": "One", "meaning": "First."}, {"type": "card.definition", "term": "Two", "meaning": "Second."}]},
    "layout.split": {"type": "layout.split", "left": {"type": "chart.pie", "data": [{"label": "A", "value": 60}, {"label": "B", "value": 40}]}, "right": {"type": "card.kpi", "label": "Total", "value": 100}},
    "media.audio": {"type": "media.audio", "artifact": "0123456789ab", "title": "Meeting recording"},
    "media.video": {"type": "media.video", "artifact": "ba9876543210", "title": "Demo"},
    "json": {"type": "json", "title": "Config", "value": {"name": "demo", "retries": 3, "tags": ["a", "b"], "limits": {"rows": 5000}}},
    "html": {"type": "html", "title": "Preview", "html": "<h1 style=\"font-family:sans-serif\">Hello</h1><p>A locked preview: no scripts, no network.</p>"},
}
