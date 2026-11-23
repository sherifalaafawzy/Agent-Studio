"""Check and clean a component spec before it is shown.

A spec is data, never code. Everything an agent sends is cleaned here and cleaned again by the browser before drawing: unknown keys that could carry
behaviour are dropped, strings and lists are limited, links may only use https, mailto, tel or an Agent Studio artifact, secret-looking values are removed,
and an oversized or malformed spec is refused with a reason the model can act on. A component that cannot be drawn is shown as a table or JSON instead.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from .kinds import KINDS, canonical, suggestions
from ..artifacts.service import scan_text

DEFAULT_LIMITS = {"max_kb": 300, "max_rows": 5000, "max_items": 20000}
MAX_DEPTH = 10
MAX_STR = 20_000
MAX_KEY = 80
MAX_HTML = 200_000
URL_KEYS = {"url", "href", "link", "src", "image", "avatar", "icon", "thumbnail", "poster"}
BAD_KEYS = {"__proto__", "constructor", "prototype", "dangerouslysetinnerhtml", "style", "class", "classname", "srcdoc"}
ARTIFACT_ID = re.compile(r"^[0-9a-f]{12}$")
DATA_IMAGE = re.compile(r"^data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]{1,400000}$")
SAFE_URL = re.compile(r"^(https://|mailto:|tel:|artifact:|/(?!/)|#)", re.I)       # a path starting with one slash is a page of this app
HEADER_SECRET = re.compile(r"authori[sz]ation|cookie|token|secret|api[-_]?key|password|passwd|x-auth", re.I)


class _Scrub:
    def __init__(self, limits: dict, kind: str):
        self.lim, self.kind = {**DEFAULT_LIMITS, **(limits or {})}, kind
        self.warnings: list[str] = []

    def warn(self, msg: str) -> None:
        if msg not in self.warnings and len(self.warnings) < 8:
            self.warnings.append(msg)

    def string(self, s: str, key: str) -> str:
        cap = MAX_HTML if (self.kind == "html" and key == "html") else MAX_STR
        s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
        if len(s) > cap:
            s = s[:cap]
            self.warn(f"'{key}' was cut to {cap} characters")
        s, found = scan_text(s)
        if found:
            self.warn("a value that looked like a secret was removed")
        return s

    def url(self, s: str, key: str) -> str | None:
        s = s.strip()
        if key in ("image", "avatar", "thumbnail", "poster", "src", "icon") and (DATA_IMAGE.match(s) or ARTIFACT_ID.match(s)):
            return s
        if SAFE_URL.match(s):
            return s
        self.warn(f"a link in '{key}' was removed: only https, mailto, tel and artifact links are allowed")
        return None

    def walk(self, v: Any, key: str = "", depth: int = 0) -> Any:
        if depth > MAX_DEPTH:
            self.warn(f"nesting deeper than {MAX_DEPTH} levels was cut")
            return None
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, (int, float)):
            return None if isinstance(v, float) and not math.isfinite(v) else v
        if isinstance(v, str):
            return self.url(v, key) if key in URL_KEYS else self.string(v, key)
        if isinstance(v, list):
            cap = self.lim["max_rows"] if key == "rows" else self.lim["max_items"]
            if len(v) > cap:
                self.warn(f"'{key or 'a list'}' was cut to {cap} items")
            return [self.walk(x, key, depth + 1) for x in v[:cap]]
        if isinstance(v, dict):
            out: dict = {}
            for k, x in v.items():
                ks = str(k)[:MAX_KEY]
                low = ks.lower()
                if low in BAD_KEYS or re.match(r"^on[a-z]+$", low) or (low == "html" and self.kind != "html"):
                    self.warn(f"'{ks}' is not allowed and was removed")
                    continue
                if self.kind == "card.request" and key in ("headers", "request_headers", "response_headers") and HEADER_SECRET.search(ks):
                    out[ks] = "••••"
                    continue
                out[ks] = self.walk(x, ks, depth + 1)
            return out
        return str(v)[:200]


def _missing(kind_key: str, spec: dict) -> str | None:
    need = KINDS[kind_key].need
    if any(all(spec.get(f) not in (None, "", [], {}) for f in group) for group in need):
        return None
    options = " or ".join("+".join(f"'{f}'" for f in g) for g in need)
    return f"{kind_key} needs {options}. Call ui_catalog with type '{kind_key}' to see the shape."


def validate(raw: Any, limits: dict | None = None) -> tuple[dict | None, list[str], list[str]]:
    """(clean spec, errors, warnings). The clean spec is None when there are errors."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None, ["the spec is not valid JSON"], []
    if not isinstance(raw, dict):
        return None, ["a component spec is a JSON object with a 'type'"], []
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    try:
        size = len(json.dumps(raw, default=str))
    except (TypeError, ValueError):
        return None, ["the spec cannot be turned into JSON"], []
    if size > lim["max_kb"] * 1024:
        return None, [f"the spec is {size // 1024} KB; the limit is {lim['max_kb']} KB. Save the data as an artifact and send a summary, or reduce the rows"], []
    key = canonical(raw.get("type"), raw)
    if key is None:
        hint = suggestions(raw.get("type"))
        return None, [f"unknown component type '{str(raw.get('type'))[:40]}'" + (f"; did you mean {', '.join(hint)}?" if hint else "") + ". Call ui_catalog to list the types."], []
    sc = _Scrub(lim, key)
    clean = sc.walk({k: v for k, v in raw.items() if k not in ("type", "version")})
    clean = {"type": key, "version": 1, **clean}
    miss = _missing(key, clean)
    if miss:
        return None, [miss], sc.warnings
    if key.startswith("layout."):
        kids = clean.get("children") or [t.get("child") for t in clean.get("tabs") or [] if isinstance(t, dict)] or [clean.get("left"), clean.get("right")]
        for i, c in enumerate(kids):
            if not isinstance(c, dict):
                return None, [f"{key}: item {i + 1} must be a component spec"], sc.warnings
            sub, errs, warns = validate(c, limits)
            if sub is None:
                return None, [f"{key}: item {i + 1}: {errs[0]}"], sc.warnings
            sc.warnings.extend(w for w in warns if w not in sc.warnings)
        # nested specs were validated for the model's benefit; the clean copy below is rebuilt so every child is canonical too
        clean = _rebuild_layout(clean, limits)
    return clean, [], sc.warnings


def _rebuild_layout(spec: dict, limits: dict | None) -> dict:
    out = dict(spec)
    for name in ("children",):
        if isinstance(out.get(name), list):
            out[name] = [validate(c, limits)[0] for c in out[name]][:24]
    if isinstance(out.get("tabs"), list):
        out["tabs"] = [{"label": str(t.get("label") or "")[:60], "child": validate(t.get("child"), limits)[0]} for t in out["tabs"][:12]]
    for name in ("left", "right"):
        if isinstance(out.get(name), dict):
            out[name] = validate(out[name], limits)[0]
    return out


def clean_limits(raw: dict | None) -> dict:
    return {k: int(v) for k, v in (raw or {}).items() if k in DEFAULT_LIMITS and isinstance(v, int) and not isinstance(v, bool) and v > 0}
