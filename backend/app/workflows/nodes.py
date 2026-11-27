"""The workflow node catalog: one registry that the editor's palette, the settings panel, the validator and the engine all read.

Every node here is something the engine really executes (or, for `note`, a purely visual sticky note). Nodes have named output ports so a branch can be
followed or pruned (If: true/false, Switch: one per case, Classifier: one per label, Approval: approved/rejected). Every node also has the common
settings: what to do when it fails, retries, a time limit, "switched off" (passes its input through) and a pinned output for free test runs.
"""
from __future__ import annotations

import re
from typing import Any

from . import expr as X

LEGACY = ("input", "agent", "tool", "openapi", "sequential", "parallel", "loop", "output")
STEP_KINDS = ("agent", "tool", "openapi")
CONTAINERS = ("sequential", "parallel", "loop")
MAX_CASES, MAX_FIELDS, MAX_WAIT = 12, 30, 300
ENTRY = ("input", "webhook", "schedule")           # a workflow starts at exactly one of these
ROOTS = ENTRY + ("error_handler",)                 # boxes that nothing leads into
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")
READ_METHODS = ("GET", "HEAD")
MAX_RULES, MAX_FORM = 12, 10
RULE_OPS = ("equals", "not_equals", "contains", "not_contains", "starts_with", "ends_with", "gt", "gte", "lt", "lte", "is_empty", "not_empty", "regex")
AGG_OPS = ("count", "sum", "avg", "min", "max", "first", "last", "join", "collect")
MEMORY_KINDS = ("semantic", "episodic", "procedural", "entity")
FIELD_TYPES = ("text", "number", "select", "bool")
PATH = re.compile(r"^[\w.\-]{0,80}$")
IDENT = re.compile(r"^[A-Za-z_][\w]{0,30}$")
ON_ERROR = ("stop", "continue", "branch")
GROUPS = [("trigger", "Triggers"), ("ai", "AI"), ("flow", "Flow"), ("data", "Data"), ("integration", "Integrations"), ("output", "Output"), ("canvas", "Canvas")]


def F(name: str, label: str, kind: str = "text", **kw) -> dict:
    return dict(name=name, label=label, kind=kind, **kw)


def N(type_: str, group: str, label: str, desc: str, fields: list[dict], outputs: list[str] | str = ("out",), **kw) -> dict:
    return dict(type=type_, group=group, label=label, desc=desc, fields=fields, outputs=outputs, inputs=kw.pop("inputs", 1), **kw)


PROMPT_HELP = "Use {{ $input }} for the task, {{ $prev }} for what came just before, {{ $node[\"Name\"].text }} for any earlier box."
SPECS: list[dict] = [
    N("input", "trigger", "Task input", "Where the task text enters the workflow.", [], inputs=0),
    N("agent", "ai", "Agent", "Ask one of your agents (it uses its own tools, memory and limits).", [F("agent", "Agent", "agent", required=True), F("prompt", "What to ask it", "textarea", default="{{ $prev }}", help=PROMPT_HELP)]),
    N("llm", "ai", "AI prompt", "One model call with no tools: summarise, rewrite, translate, answer.", [F("prompt", "Prompt", "textarea", default="{{ $prev }}", required=True, help=PROMPT_HELP),
      F("system", "Instructions (optional)", "textarea"), F("json", "Answer as JSON", "bool", default=False)]),
    N("classifier", "ai", "Classifier", "A model picks one label for the text; the flow continues down that label's branch.", [F("text", "Text to classify", "textarea", default="{{ $prev }}"),
      F("labels", "Labels (one per line)", "lines", required=True, default=["yes", "no"]), F("hint", "How to decide (optional)", "textarea")], outputs="labels"),
    N("tool", "integration", "Tool", "One of Agent Studio's tools (read-only ones unless you confirm).", [F("tool", "Tool", "tool", required=True), F("args", "Arguments", "json", default={}), F("confirmed", "I confirm it may change things", "bool")]),
    N("openapi", "integration", "API call", "One endpoint of an API you imported (GET unless writes are allowed and you confirm).", [F("source", "API", "source", required=True), F("operation", "Endpoint", "operation", required=True),
      F("args", "Arguments", "json", default={}), F("confirmed", "I confirm it may change data", "bool")]),
    N("sequential", "flow", "Sequence", "Steps run one after another; each sees the one before.", [F("steps", "Steps", "steps")]),
    N("parallel", "flow", "Parallel", "Steps run at the same time; their results are joined.", [F("steps", "Steps", "steps")]),
    N("loop", "flow", "Loop", "Steps repeat until the result contains a phrase, or a maximum is reached.", [F("steps", "Steps", "steps"), F("max_iterations", "Repeat at most", "number", default=3, min=1, max=10),
      F("until", "Stop when the result contains", "text")]),
    N("if", "flow", "If", "Go down 'true' or 'false' depending on a condition.", [F("condition", "Condition", "expr", required=True, default="{{ len($prev) > 0 }}", help="True when the result is yes/true/1 or not empty.")], outputs=["true", "false"]),
    N("switch", "flow", "Switch", "Go down the branch whose value matches; 'other' when none does.", [F("value", "Value to compare", "expr", default="{{ $prev }}"), F("cases", "Cases (one per line)", "lines", required=True)], outputs="cases"),
    N("merge", "flow", "Merge", "Wait for the branches that ran and combine what they produced.", [F("mode", "Combine as", "select", options=["text", "json_array"], default="text"), F("separator", "Separator", "text", default="\n\n")]),
    N("wait", "flow", "Wait", "Pause for a number of seconds (up to 300).", [F("seconds", "Seconds", "number", default=5, min=0, max=MAX_WAIT)]),
    N("approval", "flow", "Ask a person", "Stop and wait for a yes or no from you before continuing.", [F("message", "What to ask", "textarea", default="Continue with: {{ $prev }}?"),
      F("form", "Ask for details too (optional)", "formfields", default=[], help="The answers go on as JSON.")], outputs=["approved", "rejected"]),
    N("stop", "flow", "Stop with error", "End the run on purpose with a message.", [F("message", "Message", "textarea", default="Stopped: {{ $prev }}")], outputs=[]),
    N("sub_workflow", "flow", "Run a workflow", "Run another saved workflow with a task and use its result.", [F("workflow", "Workflow", "workflow", required=True), F("task", "Task", "textarea", default="{{ $prev }}")]),
    N("for_each", "flow", "For each item", "Run another workflow once per item of a list and collect the results.", [F("items", "List", "expr", default="{{ $json }}", help="A JSON list, or text with one item per line."),
      F("workflow", "Workflow to run per item", "workflow", required=True), F("task", "Task per item", "textarea", default="{{ $item }}"), F("concurrency", "At the same time", "number", default=1, min=1, max=5)]),
    N("set", "data", "Set fields", "Build a JSON object from values and expressions.", [F("fields", "Fields", "pairs", required=True, help="Each value may use {{ … }}.")]),
    N("template", "data", "Text template", "Build text from {{ … }} values.", [F("text", "Text", "textarea", required=True, default="{{ $prev }}")]),
    N("parse_json", "data", "Parse JSON", "Read JSON text so later boxes can use its fields ($node[\"Name\"].json.field).", [F("from", "Text", "expr", default="{{ $prev }}")]),
    N("output", "output", "Result", "The final result (what the workflow returns).", [F("value", "Result (optional)", "textarea", default="", help="Leave empty to return what came in.")], outputs=[]),
    N("compare", "flow", "Compare", "Check one or more conditions and go down 'true' or 'false' (a friendlier If).", [F("conditions", "Conditions", "conditions", required=True), F("combine", "All or any must hold", "select", options=["and", "or"], default="and")], outputs=["true", "false"]),
    N("error_handler", "trigger", "On error", "Starts when any box fails and nothing else catches it: use it to tidy up or tell someone. The run still ends as failed.", [], inputs=0),
    N("webhook", "trigger", "Webhook", "Starts the workflow when something calls its secret address (set it up from the Flows table). What it sends becomes the task.", [F("payload", "What becomes the task", "select", options=["body", "json", "all"], default="body", help="body = the text sent; json = the JSON sent, tidied; all = body, query and headers together."),
      F("respond", "Answer the caller", "select", options=["immediately", "when_done", "node"], default="immediately", help="immediately = 'accepted' at once; when_done = the final result; node = what a Respond to webhook box says.")], inputs=0),
    N("schedule", "trigger", "Schedule", "Starts the workflow on its own: every few minutes, every day at a time, or on a cron pattern (the computer's local time). Switch it on from this box's panel once the workflow is saved.",
      [F("mode", "Repeat", "select", options=["interval", "daily", "cron"], default="interval", help="interval = every N minutes; daily = every day at a time; cron = a 5-part pattern."),
       F("every_minutes", "Every (minutes)", "number", default=60, min=1, max=10080), F("at", "At (daily, 24-hour)", "text", default="09:00"),
       F("cron", "Cron pattern", "text", default="0 9 * * 1-5", help="minute hour day month weekday, for example '0 9 * * 1-5' = 9:00 on weekdays, '*/15 * * * *' = every 15 minutes."),
       F("task", "The task each run gets", "textarea", default="Scheduled run", required=True), F("missed", "A time missed while the app was closed", "select", options=["run_once", "skip"], default="run_once"),
       F("max_per_day", "At most this many runs a day", "number", default=48, min=1, max=1440)], inputs=0),
    N("notify", "output", "Notify me", "Show a notification in the app's notification center (the bell), and optionally a desktop pop-up. Nothing is sent outside this computer.",
      [F("title", "Title", "text", default="{{ $workflow.name }}", required=True), F("message", "Message", "textarea", default="{{ $prev }}"),
       F("level", "Kind", "select", options=["info", "success", "warning", "error"], default="info"), F("desktop", "Also show a desktop pop-up (if you allowed them)", "bool", default=False)]),
    N("respond_webhook", "output", "Respond to webhook", "What to send back to the caller of the Webhook (when the Webhook is set to 'node').", [F("status", "Status code", "number", default=200, min=100, max=599), F("content_type", "Content type", "select", options=["text/plain", "application/json"], default="text/plain"),
      F("body", "Body", "textarea", default="{{ $prev }}")]),
    N("http_request", "integration", "HTTP request", "Call any web address (GET unless you pick another method and confirm). Public addresses only, unless you allow a host in the workflow settings.", [F("method", "Method", "select", options=list(METHODS), default="GET"),
      F("url", "Address", "text", required=True, help="Starts with https:// (or http://). May use {{ … }}."), F("query", "Query parameters", "json", default={}), F("headers", "Headers (no secrets here)", "json", default={}), F("body", "Body", "textarea"),
      F("credential", "Sign in with", "ref", ref="credential", help="Credentials are kept apart from the workflow, in the workflow settings."), F("confirmed", "I confirm it may change data", "bool"), F("fail_on_error", "Treat 4xx/5xx as a failure", "bool", default=True)]),
    N("mcp_tool", "integration", "MCP tool", "Call one tool of a connected MCP server. A tool that is not marked read-only needs your confirmation.", [F("server", "MCP server", "ref", ref="mcp", required=True), F("tool", "Tool name", "text", required=True), F("args", "Arguments", "json", default={}), F("confirmed", "I confirm it may change things", "bool")]),
    N("memory", "integration", "Memory", "Search what your agents remember, or save something for later.", [F("operation", "What to do", "select", options=["search", "save"], default="search"), F("text", "Query or what to save", "textarea", default="{{ $prev }}", required=True),
      F("kind", "Kind of memory", "select", options=list(MEMORY_KINDS), default="semantic"), F("scope", "Where", "select", options=["user", "workspace", "global"], default="user"), F("key", "Key (save only; the same key replaces the old value)", "text"), F("k", "How many results", "number", default=5, min=1, max=10)]),
    N("knowledge", "integration", "Knowledge search", "Search one of your knowledge bases and get the best passages with their sources.", [F("kb", "Knowledge base", "ref", ref="knowledge", required=True), F("query", "Question", "expr", default="{{ $prev }}"), F("k", "How many passages", "number", default=5, min=1, max=10)]),
    N("data_query", "integration", "Data query", "Run ONE read-only query on a data source (SQL, or SOQL for Salesforce). Writes are refused. Do not paste untrusted text into the query.", [F("source", "Data source", "ref", ref="data_source", required=True), F("sql", "Query", "textarea", required=True), F("max_rows", "At most this many rows", "number", default=50, min=1, max=1000)]),
    N("artifact", "output", "Save as artifact", "Keep text as a file on the Artifacts page so you can find, preview and download it later.", [F("name", "File name", "text", default="result.md", required=True), F("content", "Content", "textarea", default="{{ $prev }}"), F("tags", "Tags (comma separated)", "text")]),
    N("guardrail", "ai", "Guardrail check", "Run your guardrails on some text. Goes down 'passed' or 'blocked'.", [F("stage", "Check as", "select", options=["input", "output", "tool"], default="input"), F("text", "Text to check", "expr", default="{{ $prev }}"), F("profile", "Only this profile (optional)", "ref", ref="guardrail")], outputs=["passed", "blocked"]),
    N("filter", "data", "Filter", "Keep only the items of a list that pass the rules.", [F("items", "List", "expr", default="{{ $json }}", help="A JSON list, or text with one item per line."), F("rules", "Rules", "rules", required=True), F("combine", "All or any must hold", "select", options=["and", "or"], default="and")]),
    N("sort", "data", "Sort", "Put a list in order.", [F("items", "List", "expr", default="{{ $json }}"), F("field", "Sort by (name inside each item; empty = the item)", "text"), F("order", "Order", "select", options=["asc", "desc"], default="asc"), F("numeric", "Sort as numbers", "bool")]),
    N("limit", "data", "Limit", "Keep only the first (or last) few items.", [F("items", "List", "expr", default="{{ $json }}"), F("count", "How many", "number", default=10, min=1, max=1000), F("from", "From the", "select", options=["start", "end"], default="start")]),
    N("dedupe", "data", "Remove duplicates", "Drop items that repeat (by a field, or whole).", [F("items", "List", "expr", default="{{ $json }}"), F("field", "Compare by (empty = the whole item)", "text")]),
    N("aggregate", "data", "Summarise", "Count, add up, average or collect the items of a list, optionally per group.", [F("items", "List", "expr", default="{{ $json }}"), F("op", "Do what", "select", options=list(AGG_OPS), default="count"),
      F("field", "Field", "text"), F("group_by", "Per group of (optional)", "text"), F("separator", "Separator (join)", "text", default=", ")]),
    N("split_out", "data", "Split into a list", "Turn text into a list: one item per line, per separator, or taken from a field.", [F("from", "Text or data", "expr", default="{{ $prev }}"), F("mode", "Split by", "select", options=["lines", "separator", "field"], default="lines"), F("separator", "Separator", "text", default=","), F("field", "Field holding the list", "text")]),
    N("join", "data", "Join a list", "Turn a list into one text.", [F("items", "List", "expr", default="{{ $json }}"), F("field", "Field (empty = the item)", "text"), F("separator", "Separator", "text", default=", ")]),
    N("merge_by_key", "data", "Merge by key", "Combine two lists of objects that share a key (like a spreadsheet lookup).", [F("left", "First list", "expr", required=True, default="{{ $json }}"), F("right", "Second list", "expr", required=True, default="{{ $json }}"), F("key", "Key in the first list", "text", required=True),
      F("right_key", "Key in the second (if different)", "text"), F("mode", "Keep", "select", options=["inner", "left"], default="inner", help="inner = only matches; left = everything from the first list.")]),
    N("date_time", "data", "Date and time", "Get the time, format a date, add or subtract time, or count the days between two dates.", [F("operation", "Do what", "select", options=["now", "format", "add", "subtract", "diff_days", "parse"], default="now"), F("value", "Date", "expr", default="{{ now() }}"),
      F("amount", "Amount", "number", default=1, min=0, max=100000), F("unit", "Unit", "select", options=["minutes", "hours", "days", "weeks"], default="days"), F("format", "Format", "text", default="%Y-%m-%d"), F("other", "Other date (diff_days)", "expr", default="{{ now() }}")]),
    N("regex", "data", "Text pattern", "Find or replace text with a regular expression.", [F("text", "Text", "expr", default="{{ $prev }}"), F("pattern", "Pattern", "text", required=True), F("mode", "Do what", "select", options=["test", "first", "all", "groups", "replace"], default="first"),
      F("replacement", "Replace with", "text"), F("ignore_case", "Ignore upper/lower case", "bool")]),
    N("note", "canvas", "Sticky note", "A note on the canvas. It never runs.", [F("text", "Note", "textarea")], outputs=[], inputs=0, visual=True),
]
BY_TYPE = {s["type"]: s for s in SPECS}
NODE_TYPES = tuple(s["type"] for s in SPECS)
NEW_TYPES = tuple(t for t in NODE_TYPES if t not in LEGACY)
ASKS = ("agent", "llm", "classifier", "tool", "openapi", "sequential", "parallel", "loop", "sub_workflow", "for_each", "http_request", "mcp_tool", "memory", "knowledge", "data_query", "artifact")     # boxes that wait for a yes in "approve" autonomy
LINES = re.compile(r"\r?\n")


def catalog() -> dict:
    return dict(groups=[dict(id=g, label=l) for g, l in GROUPS], nodes=SPECS)


def _lines(v: Any, limit: int, where: str, what: str) -> list[str]:
    items = v if isinstance(v, list) else LINES.split(str(v or ""))
    items = [str(x).strip()[:80] for x in items if str(x).strip()]
    if len(items) > limit or len(set(items)) != len(items):
        raise ValueError(f"{where}: {what} (at most {limit}, no repeats)")
    return items


def ports(n: dict) -> list[str]:
    """The names of a node's output ports (an extra `error` port when 'on error' is set to branch)."""
    spec = BY_TYPE.get(n["type"], {})
    out = spec.get("outputs", ["out"])
    cfg = n.get("config") or {}
    if out == "labels":
        out = [*(cfg.get("labels") or []), "other"]
    elif out == "cases":
        out = [*(cfg.get("cases") or []), "other"]
    out = list(out)
    if (n.get("settings") or {}).get("on_error") == "branch" and out:
        out.append("error")
    return out


def clean_settings(s: Any, where: str) -> dict:
    s = s if isinstance(s, dict) else {}
    on_error = s.get("on_error", "stop")
    retries, wait, timeout = s.get("retries", 0), s.get("retry_wait_s", 1), s.get("timeout_s", 0)
    if on_error not in ON_ERROR:
        raise ValueError(f"{where}: 'on error' must be one of {', '.join(ON_ERROR)}")
    for v, lo, hi, name in ((retries, 0, 3, "retries"), (wait, 0, 30, "wait between retries"), (timeout, 0, 600, "time limit")):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValueError(f"{where}: {name} must be {lo} to {hi}")
    pin = s.get("pin")
    return dict(on_error=on_error, retries=int(retries), retry_wait_s=float(wait), timeout_s=float(timeout), disabled=bool(s.get("disabled")), notes=str(s.get("notes") or "")[:1000],
                color=str(s.get("color") or "")[:12], pin=str(pin)[:20_000] if isinstance(pin, str) else None)


def _expr_ok(v: Any, where: str, what: str) -> str:
    s = str(v or "")[:4000]
    errs = X.template_errors(s)
    if errs:
        raise ValueError(f"{where}: {what}: {errs[0]}")
    return s


def clean_config(t: str, cfg: dict, where: str) -> dict:
    """Validate and normalise the config of a node of one of the NEW types (the original types are cleaned in core.clean)."""
    g = lambda k, d="": cfg.get(k, d)                                           # noqa: E731
    if t == "llm":
        return dict(prompt=_expr_ok(g("prompt", "{{ $prev }}"), where, "prompt"), system=_expr_ok(g("system"), where, "instructions"), json=bool(g("json", False)))
    if t == "classifier":
        labels = _lines(g("labels", []), MAX_CASES, where, "give 2 or more labels")
        if len(labels) < 2 or "other" in labels or "error" in labels:
            raise ValueError(f"{where}: give at least 2 labels (not 'other' or 'error')")
        return dict(text=_expr_ok(g("text", "{{ $prev }}"), where, "text"), labels=labels, hint=str(g("hint"))[:500])
    if t == "if":
        return dict(condition=_expr_ok(g("condition", "{{ len($prev) > 0 }}"), where, "condition"))
    if t == "switch":
        cases = _lines(g("cases", []), MAX_CASES, where, "give 1 or more cases")
        if not cases or "other" in cases or "error" in cases:
            raise ValueError(f"{where}: give at least 1 case (not 'other' or 'error')")
        return dict(value=_expr_ok(g("value", "{{ $prev }}"), where, "value"), cases=cases)
    if t == "merge":
        mode = g("mode", "text")
        if mode not in ("text", "json_array"):
            raise ValueError(f"{where}: merge as text or json_array")
        return dict(mode=mode, separator=str(g("separator", "\n\n"))[:40])
    if t == "wait":
        sec = g("seconds", 5)
        if isinstance(sec, bool) or not isinstance(sec, (int, float)) or not 0 <= sec <= MAX_WAIT:
            raise ValueError(f"{where}: wait 0 to {MAX_WAIT} seconds")
        return dict(seconds=sec)
    if t == "approval":
        return dict(message=_expr_ok(g("message"), where, "message"), form=_form(g("form", []), where))
    if t == "stop":
        return dict(message=_expr_ok(g("message"), where, "message"))
    if t in EXTRA:
        return EXTRA[t](cfg, where)
    if t == "sub_workflow":
        return dict(workflow=str(g("workflow"))[:41], task=_expr_ok(g("task", "{{ $prev }}"), where, "task"))
    if t == "for_each":
        c = g("concurrency", 1)
        if isinstance(c, bool) or not isinstance(c, int) or not 1 <= c <= 5:
            raise ValueError(f"{where}: run 1 to 5 items at the same time")
        return dict(items=_expr_ok(g("items", "{{ $json }}"), where, "list"), workflow=str(g("workflow"))[:41], task=_expr_ok(g("task", "{{ $item }}"), where, "task"), concurrency=c)
    if t == "set":
        pairs = g("fields", [])
        if not isinstance(pairs, list) or not pairs or len(pairs) > MAX_FIELDS:
            raise ValueError(f"{where}: set 1 to {MAX_FIELDS} fields")
        out = []
        for p in pairs:
            if not isinstance(p, dict) or not re.match(r"^[A-Za-z_][\w.-]{0,40}$", str(p.get("name") or "")):
                raise ValueError(f"{where}: each field needs a name (letters, digits, _ . -)")
            out.append(dict(name=p["name"], value=_expr_ok(p.get("value", ""), where, f"field {p['name']}")))
        return dict(fields=out)
    if t == "template":
        return dict(text=_expr_ok(g("text", "{{ $prev }}"), where, "text"))
    if t == "parse_json":
        return dict(**{"from": _expr_ok(g("from", "{{ $prev }}"), where, "text")})
    if t == "note":
        return dict(text=str(g("text"))[:2000])
    raise ValueError(f"{where}: unknown node type {t}")           # pragma: no cover - callers pass only NEW_TYPES


def clean_output(cfg: dict, where: str) -> dict:
    return dict(value=_expr_ok(cfg.get("value", ""), where, "result"))


# ---------------------------------------------------------------------------------------------------- the newer boxes: validation
def _int(v: Any, lo: int, hi: int, where: str, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or not lo <= v <= hi:
        raise ValueError(f"{where}: {what} must be a whole number from {lo} to {hi}")
    return int(v)


def _choice(v: Any, options: tuple | list, where: str, what: str) -> str:
    if v not in options:
        raise ValueError(f"{where}: {what} must be one of {', '.join(options)}")
    return v


def _path(v: Any, where: str, what: str) -> str:
    s = str(v or "").strip()
    if not PATH.match(s):
        raise ValueError(f"{where}: {what} is a name like price or customer.email (letters, digits, . _ -)")
    return s


def _rules(v: Any, where: str, left: str, right: str, left_is_path: bool) -> list[dict]:
    if not isinstance(v, list) or not 1 <= len(v) <= MAX_RULES:
        raise ValueError(f"{where}: give 1 to {MAX_RULES} rules")
    out = []
    for r in v:
        if not isinstance(r, dict):
            raise ValueError(f"{where}: each rule needs a field, a comparison and a value")
        op = _choice(r.get("op", "equals"), RULE_OPS, where, "a comparison")
        a = _path(r.get(left), where, "the field") if left_is_path else _expr_ok(str(r.get(left) or "")[:500], where, "the left side")
        out.append({left: a, "op": op, right: _expr_ok(str(r.get(right) if r.get(right) is not None else "")[:500], where, "the value")})
    return out


def _form(v: Any, where: str) -> list[dict]:
    if not isinstance(v, list) or len(v) > MAX_FORM:
        raise ValueError(f"{where}: ask for at most {MAX_FORM} details")
    out, seen = [], set()
    for f in v:
        if not isinstance(f, dict) or not IDENT.match(str(f.get("name") or "")) or f["name"] in seen:
            raise ValueError(f"{where}: each detail needs its own short name (letters, digits, _)")
        seen.add(f["name"])
        typ = _choice(f.get("type", "text"), FIELD_TYPES, where, "a detail's type")
        opts = [str(o).strip()[:60] for o in (f.get("options") or []) if str(o).strip()][:20]
        if typ == "select" and len(opts) < 2:
            raise ValueError(f"{where}: '{f['name']}' needs at least two options")
        out.append(dict(name=f["name"], label=str(f.get("label") or f["name"])[:60], type=typ, options=opts if typ == "select" else [], required=bool(f.get("required"))))
    return out


def _json_obj(v: Any, where: str, what: str, limit: int = 20) -> dict:
    if not isinstance(v, dict) or len(v) > limit:
        raise ValueError(f"{where}: {what} must be an object with at most {limit} entries")
    return {str(k)[:60]: (x if isinstance(x, (int, float, bool)) else _expr_ok(str(x)[:2000], where, what)) for k, x in v.items()}


def _items_expr(cfg: dict, where: str, key: str = "items") -> str:
    return _expr_ok(cfg.get(key, "{{ $json }}"), where, "the list")


def _c_compare(c: dict, w: str) -> dict:
    return dict(conditions=_rules(c.get("conditions"), w, "left", "right", False), combine=_choice(c.get("combine", "and"), ("and", "or"), w, "combine"))


def _c_filter(c: dict, w: str) -> dict:
    return dict(items=_items_expr(c, w), rules=_rules(c.get("rules"), w, "field", "value", True), combine=_choice(c.get("combine", "and"), ("and", "or"), w, "combine"))


def _c_webhook(c: dict, w: str) -> dict:
    return dict(payload=_choice(c.get("payload", "body"), ("body", "json", "all"), w, "what becomes the task"), respond=_choice(c.get("respond", "immediately"), ("immediately", "when_done", "node"), w, "how to answer"))


def _c_respond(c: dict, w: str) -> dict:
    return dict(status=_int(c.get("status", 200), 100, 599, w, "the status code"), content_type=_choice(c.get("content_type", "text/plain"), ("text/plain", "application/json"), w, "the content type"),
                body=_expr_ok(c.get("body", "{{ $prev }}"), w, "the body"))


def _c_http(c: dict, w: str) -> dict:
    url = _expr_ok(str(c.get("url") or "").strip(), w, "the address")
    if not url.startswith(("http://", "https://", "{{")):
        raise ValueError(f"{w}: the address must start with https:// (or http://)")
    return dict(method=_choice(str(c.get("method", "GET")).upper(), METHODS, w, "the method"), url=url, query=_json_obj(c.get("query", {}), w, "the query"), headers=_json_obj(c.get("headers", {}), w, "the headers"),
                body=_expr_ok(str(c.get("body") or "")[:20_000], w, "the body"), credential=str(c.get("credential") or "")[:40], confirmed=bool(c.get("confirmed")), fail_on_error=bool(c.get("fail_on_error", True)))


def _c_mcp(c: dict, w: str) -> dict:
    tool = str(c.get("tool") or "").strip()
    if not re.match(r"^[\w.\-]{1,80}$", tool):
        raise ValueError(f"{w}: give the tool's name (letters, digits, . _ -)")
    return dict(server=str(c.get("server") or "")[:40], tool=tool, args=_json_obj(c.get("args", {}), w, "the arguments", 30), confirmed=bool(c.get("confirmed")))


def _c_memory(c: dict, w: str) -> dict:
    op = _choice(c.get("operation", "search"), ("search", "save"), w, "what to do")
    kind = _choice(c.get("kind", "semantic"), MEMORY_KINDS, w, "the kind of memory")
    if op == "save" and kind == "entity":
        raise ValueError(f"{w}: saving people and things needs a name and details: use an agent for that, or save a fact instead")
    return dict(operation=op, text=_expr_ok(c.get("text", "{{ $prev }}"), w, "the text"), kind=kind, scope=_choice(c.get("scope", "user"), ("user", "workspace", "global"), w, "where"),
                key=str(c.get("key") or "")[:60], k=_int(c.get("k", 5), 1, 10, w, "how many results"))


def _c_knowledge(c: dict, w: str) -> dict:
    return dict(kb=str(c.get("kb") or "")[:40], query=_expr_ok(c.get("query", "{{ $prev }}"), w, "the question"), k=_int(c.get("k", 5), 1, 10, w, "how many passages"))


def _c_data(c: dict, w: str) -> dict:
    sql = str(c.get("sql") or "").strip()
    if not sql:
        raise ValueError(f"{w}: write the query")
    return dict(source=str(c.get("source") or "")[:40], sql=_expr_ok(sql[:4000], w, "the query"), max_rows=_int(c.get("max_rows", 50), 1, 1000, w, "the row limit"))


def _c_artifact(c: dict, w: str) -> dict:
    name = _expr_ok(str(c.get("name") or "").strip()[:120], w, "the file name")
    if not name:
        raise ValueError(f"{w}: give the file a name")
    return dict(name=name, content=_expr_ok(c.get("content", "{{ $prev }}"), w, "the content"), tags=str(c.get("tags") or "")[:200])


def _c_guardrail(c: dict, w: str) -> dict:
    return dict(stage=_choice(c.get("stage", "input"), ("input", "output", "tool"), w, "the stage"), text=_expr_ok(c.get("text", "{{ $prev }}"), w, "the text"), profile=str(c.get("profile") or "")[:40])


def _c_sort(c: dict, w: str) -> dict:
    return dict(items=_items_expr(c, w), field=_path(c.get("field"), w, "the field"), order=_choice(c.get("order", "asc"), ("asc", "desc"), w, "the order"), numeric=bool(c.get("numeric")))


def _c_limit(c: dict, w: str) -> dict:
    return dict(items=_items_expr(c, w), count=_int(c.get("count", 10), 1, 1000, w, "how many"), **{"from": _choice(c.get("from", "start"), ("start", "end"), w, "from where")})


def _c_dedupe(c: dict, w: str) -> dict:
    return dict(items=_items_expr(c, w), field=_path(c.get("field"), w, "the field"))


def _c_aggregate(c: dict, w: str) -> dict:
    return dict(items=_items_expr(c, w), op=_choice(c.get("op", "count"), AGG_OPS, w, "what to do"), field=_path(c.get("field"), w, "the field"), group_by=_path(c.get("group_by"), w, "the group field"),
                separator=str(c.get("separator", ", "))[:40])


def _c_split(c: dict, w: str) -> dict:
    mode = _choice(c.get("mode", "lines"), ("lines", "separator", "field"), w, "how to split")
    if mode == "field" and not str(c.get("field") or "").strip():
        raise ValueError(f"{w}: name the field that holds the list")
    return dict(**{"from": _expr_ok(c.get("from", "{{ $prev }}"), w, "the text")}, mode=mode, separator=str(c.get("separator", ","))[:20], field=_path(c.get("field"), w, "the field"))


def _c_join(c: dict, w: str) -> dict:
    return dict(items=_items_expr(c, w), field=_path(c.get("field"), w, "the field"), separator=str(c.get("separator", ", "))[:40])


def _c_merge_key(c: dict, w: str) -> dict:
    key = _path(c.get("key"), w, "the key")
    if not key:
        raise ValueError(f"{w}: name the key both lists share")
    return dict(left=_items_expr(c, w, "left"), right=_items_expr(c, w, "right"), key=key, right_key=_path(c.get("right_key"), w, "the second key"), mode=_choice(c.get("mode", "inner"), ("inner", "left"), w, "what to keep"))


def _c_date(c: dict, w: str) -> dict:
    amount = c.get("amount", 1)
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 0 <= amount <= 100000:
        raise ValueError(f"{w}: the amount must be a number from 0 to 100000")
    return dict(operation=_choice(c.get("operation", "now"), ("now", "format", "add", "subtract", "diff_days", "parse"), w, "what to do"), value=_expr_ok(c.get("value", "{{ now() }}"), w, "the date"), amount=amount,
                unit=_choice(c.get("unit", "days"), ("minutes", "hours", "days", "weeks"), w, "the unit"), format=str(c.get("format", "%Y-%m-%d"))[:40], other=_expr_ok(c.get("other", "{{ now() }}"), w, "the other date"))


def _c_schedule(c: dict, w: str) -> dict:
    from .schedule import clean
    return clean(c, w)


def _c_notify(c: dict, w: str) -> dict:
    title = str(c.get("title") or "").strip()
    if not title or len(title) > 200:
        raise ValueError(f"{w}: give the notification a title (up to 200 characters)")
    return dict(title=_expr_ok(title, w, "the title"), message=_expr_ok(str(c.get("message", "{{ $prev }}"))[:4000], w, "the message"),
                level=_choice(c.get("level", "info"), ("info", "success", "warning", "error"), w, "the kind"), desktop=bool(c.get("desktop")))


def _c_regex(c: dict, w: str) -> dict:
    pattern = str(c.get("pattern") or "")
    if not pattern.strip() or len(pattern) > 200:
        raise ValueError(f"{w}: write a pattern (up to 200 characters)")
    return dict(text=_expr_ok(c.get("text", "{{ $prev }}"), w, "the text"), pattern=pattern, mode=_choice(c.get("mode", "first"), ("test", "first", "all", "groups", "replace"), w, "what to do"),
                replacement=str(c.get("replacement") or "")[:200], ignore_case=bool(c.get("ignore_case")))


EXTRA = {"compare": _c_compare, "filter": _c_filter, "webhook": _c_webhook, "respond_webhook": _c_respond, "http_request": _c_http, "mcp_tool": _c_mcp, "memory": _c_memory, "knowledge": _c_knowledge,
         "data_query": _c_data, "artifact": _c_artifact, "guardrail": _c_guardrail, "sort": _c_sort, "limit": _c_limit, "dedupe": _c_dedupe, "aggregate": _c_aggregate, "split_out": _c_split, "join": _c_join,
         "merge_by_key": _c_merge_key, "date_time": _c_date, "regex": _c_regex, "error_handler": lambda c, w: {}, "schedule": _c_schedule, "notify": _c_notify}
