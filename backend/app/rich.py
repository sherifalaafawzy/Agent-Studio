"""Rich blocks in an answer, as structured data.

The chat page draws fenced blocks (```table, ```chart, ```jsonview, ```html-preview, ```audio, ```video, ```csv, ```diff) itself. An AG-UI client has no
such renderer, so the same blocks are also sent as ACTIVITY_SNAPSHOT events (activity type `rich.<kind>`), already parsed and size-limited, right after the
message that contained them. Nothing here is ever executed; HTML is carried as text together with the sandbox rules a client must apply.

Event contents:
  rich.table    {title, columns: [{key, label, type: text|number|date|boolean}], rows: [[cell, ...]], truncated}
  rich.chart    {...the chart JSON, unchanged}
  rich.json     {title, value}             (or {title, text, truncated: true} when the document is too large)
  rich.html     {title, html, sandbox: "", csp}   (render in an iframe with an empty sandbox attribute and that CSP; never in the page itself)
  rich.audio / rich.video   {artifact: <id>, title, captions: <artifact id> | null}   (files are artifacts; fetch /api/artifacts/<id>/content)
  rich.diff     {text}
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

MAX_BLOCKS = 12
MAX_ROWS = 5000
MAX_COLS = 50
MAX_CELL = 2000
MAX_JSON_CHARS = 200_000
MAX_HTML_CHARS = 200_000
HTML_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
ARTIFACT_ID = re.compile(r"^[0-9a-f]{12}$")

_FENCE = re.compile(r"^[ \t]*(```|~~~)[ \t]*([\w+#.-]*)[ \t]*\n(.*?)\n?[ \t]*\1[ \t]*$", re.S | re.M)
_KINDS = {"table": "table", "csv": "table", "tsv": "table", "chart": "chart", "jsonview": "json", "html-preview": "html", "audio": "audio", "video": "video",
          "diff": "diff", "patch": "diff"}
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?")


def _cell(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float)):
        return v
    return str(v)[:MAX_CELL]


def _kind_of(values: list[Any]) -> str:
    """text / number / boolean / date, from the non-empty values of a column."""
    vals = [v for v in values if v not in (None, "")]
    if not vals:
        return "text"
    if all(isinstance(v, bool) or str(v).lower() in ("true", "false") for v in vals):
        return "boolean"
    if all(not isinstance(v, bool) and (isinstance(v, (int, float)) or _NUMBER.match(str(v).strip())) for v in vals):
        return "number"
    if all(isinstance(v, str) and _DATE.match(v.strip()) for v in vals):
        return "date"
    return "text"


def normalize_table(raw: Any, title: str = "") -> dict | None:
    """A table from {columns, rows} (rows as lists or as objects keyed by column), or None when the data is not a table."""
    if not isinstance(raw, dict) or not isinstance(raw.get("rows"), list):
        return None
    rows_in = raw["rows"]
    cols_in = raw.get("columns")
    if not isinstance(cols_in, list) or not cols_in:
        if rows_in and isinstance(rows_in[0], dict):
            cols_in = list(rows_in[0].keys())
        else:
            return None
    cols = []
    for i, c in enumerate(cols_in[:MAX_COLS]):
        if isinstance(c, dict):
            key = str(c.get("key") if c.get("key") is not None else c.get("label") if c.get("label") is not None else i)
            cols.append({"key": key[:60], "label": str(c.get("label") if c.get("label") is not None else key)[:80], "type": c.get("type") if c.get("type") in ("text", "number", "date", "boolean") else ""})
        else:
            cols.append({"key": str(c)[:60], "label": str(c)[:80], "type": ""})
    rows = []
    for r in rows_in[:MAX_ROWS]:
        if isinstance(r, dict):
            rows.append([_cell(r.get(c["key"])) for c in cols])
        elif isinstance(r, list):
            rows.append([_cell(r[i]) if i < len(r) else None for i in range(len(cols))])
        else:
            return None
    for i, c in enumerate(cols):
        c["type"] = c["type"] or _kind_of([r[i] for r in rows])
    return {"title": str(raw.get("title") or title)[:100], "columns": cols, "rows": rows, "truncated": len(rows_in) > MAX_ROWS}


def table_from_delimited(text: str, sep: str) -> dict | None:
    rows = [r for r in csv.reader(io.StringIO(text.replace("\r\n", "\n")), delimiter=sep) if any(c.strip() for c in r)]
    if not rows:
        return None
    return normalize_table({"columns": rows[0], "rows": rows[1:MAX_ROWS + 1]}) | {"truncated": len(rows) - 1 > MAX_ROWS}


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def block_content(lang: str, body: str) -> dict | None:
    """The structured content of one fenced block, or None when it is not valid (the client then shows it as code)."""
    kind = _KINDS[lang]
    if kind == "table":
        if lang in ("csv", "tsv"):
            return table_from_delimited(body, "\t" if lang == "tsv" else ",")
        return normalize_table(_json(body))
    if kind == "chart":
        raw = _json(body)
        return raw if isinstance(raw, dict) else None
    if kind == "json":
        if len(body) > MAX_JSON_CHARS:
            return {"title": "", "text": body[:MAX_JSON_CHARS], "truncated": True}
        raw = _json(body)
        return None if raw is None and body.strip() != "null" else {"title": "", "value": raw}
    if kind == "html":
        return {"title": "", "html": body[:MAX_HTML_CHARS], "sandbox": "", "csp": HTML_CSP}
    if kind in ("audio", "video"):
        raw = _json(body)
        raw = {"artifact": body.strip()} if raw is None and ARTIFACT_ID.match(body.strip()) else raw
        if not isinstance(raw, dict) or not ARTIFACT_ID.match(str(raw.get("artifact") or "")):
            return None
        cap = str(raw.get("captions") or "")
        return {"artifact": raw["artifact"], "title": str(raw.get("title") or "")[:100], "captions": cap if ARTIFACT_ID.match(cap) else None}
    return {"text": body[:100_000]}                                    # diff


def extract_blocks(text: str) -> list[tuple[str, dict]]:
    """(kind, content) for each valid rich block in a message, in order (at most MAX_BLOCKS)."""
    out: list[tuple[str, dict]] = []
    for m in _FENCE.finditer(text or ""):
        lang = m.group(2).lower()
        if lang not in _KINDS:
            continue
        content = block_content(lang, m.group(3))
        if content is not None:
            out.append((_KINDS[lang], content))
        if len(out) >= MAX_BLOCKS:
            break
    return out
