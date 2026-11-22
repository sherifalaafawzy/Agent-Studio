"""A careful OpenAPI reader for the workflow builder: OpenAPI 3.0/3.1 and Swagger 2.0, JSON or YAML, text in, operations out.

What it does that the small reader in `mcp_openapi` does not: resolves local `$ref`s (cycle-safe, depth-limited), reads parameter types, enums,
defaults and descriptions, flattens a JSON request body into typed fields, reads Swagger 2.0 hosts, lists every server (variables at their defaults),
suggests an auth type from the security schemes, and keeps a spec's operation ids unique. Uploaded specs are **data**: nothing in them is executed,
external (`http://…` or file) `$ref`s are never followed (they are reported as warnings), and size/shape limits stop hostile files early.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

MAX_BYTES, MAX_PATHS, MAX_DEPTH, MAX_OPS = 5_000_000, 5000, 8, 2000
METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
READ_METHODS = ("get", "head", "options")


class SpecError(ValueError):
    """The text is not an OpenAPI/Swagger description we can use; the message says why."""


def safe_name(s: str) -> str:
    n = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]+", "_", str(s).lower())).strip("_")
    return (n or "op")[:64]


def _load_text(content: str) -> Any:
    if not isinstance(content, str) or not content.strip():
        raise SpecError("the description is empty")
    if len(content.encode("utf-8", "ignore")) > MAX_BYTES:
        raise SpecError(f"the description is larger than {MAX_BYTES // 1_000_000} MB")
    try:
        return json.loads(content)
    except ValueError:
        pass
    try:
        import yaml
        return yaml.safe_load(content)
    except Exception as e:  # noqa: BLE001 - yaml raises many error types; the person only needs the reason
        raise SpecError(f"it is neither valid JSON nor valid YAML ({str(e)[:160]})")


class _Refs:
    """Resolves local JSON-pointer references against the document; anything else is skipped with a warning."""

    def __init__(self, doc: dict, warnings: list[str]):
        self.doc, self.warnings = doc, warnings

    def resolve(self, node: Any, depth: int = 0, stack: tuple[str, ...] = ()) -> Any:
        while isinstance(node, dict) and isinstance(node.get("$ref"), str):
            ref = node["$ref"]
            if not ref.startswith("#/"):
                self._warn(f"external reference '{ref[:80]}' was ignored")
                return {}
            if ref in stack or depth > MAX_DEPTH:
                return {}
            cur: Any = self.doc
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                    cur = cur[int(part)]
                else:
                    self._warn(f"reference '{ref[:80]}' points at nothing")
                    return {}
            stack, depth, node = (*stack, ref), depth + 1, cur
        return node

    def _warn(self, msg: str) -> None:
        if msg not in self.warnings and len(self.warnings) < 30:
            self.warnings.append(msg)


def _schema_info(refs: _Refs, schema: Any) -> dict:
    s = refs.resolve(schema)
    if not isinstance(s, dict):
        return {"type": "any"}
    t = s.get("type")
    if isinstance(t, list):                                                  # OpenAPI 3.1: ["string", "null"]
        t = next((x for x in t if x != "null"), "any")
    if t is None:
        t = "object" if "properties" in s else "array" if "items" in s else "any"
    out: dict = {"type": str(t)}
    for k in ("enum", "default", "format", "description"):
        if k in s and isinstance(s[k], (str, int, float, bool, list)):
            out[k] = s[k] if k != "enum" else [x for x in s[k] if isinstance(x, (str, int, float, bool))][:50]
    if out["type"] == "array":
        out["items"] = _schema_info(refs, s.get("items"))["type"]
    return out


def _props(refs: _Refs, schema: Any, depth: int = 0) -> tuple[dict, set]:
    s = refs.resolve(schema)
    if not isinstance(s, dict) or depth > 3:
        return {}, set()
    props: dict = dict(s.get("properties") or {}) if isinstance(s.get("properties"), dict) else {}
    req = set(s.get("required") or []) if isinstance(s.get("required"), list) else set()
    for part in (s.get("allOf") or []) if isinstance(s.get("allOf"), list) else []:
        p2, r2 = _props(refs, part, depth + 1)
        props.update(p2)
        req |= r2
    return props, req


def _body(refs: _Refs, op: dict, swagger2: bool, params: list) -> tuple[list[str] | None, list[dict], str]:
    """(field names for the old consumers, typed fields, content type)."""
    schema, ctype = None, ""
    if swagger2:
        for p in params:
            if p.get("in") == "body":
                schema, ctype = p.get("schema"), "application/json"
    else:
        body = refs.resolve(op.get("requestBody") or {})
        content = body.get("content") if isinstance(body, dict) and isinstance(body.get("content"), dict) else {}
        for ct in ("application/json", *[c for c in content if "json" in c]):
            if ct in content:
                schema, ctype = (content[ct] or {}).get("schema"), ct
                break
        else:
            if content:
                return ["(body)"], [], next(iter(content))
    props, req = _props(refs, schema)
    fields = [dict(name=str(k), required=k in req, **_schema_info(refs, v)) for k, v in list(props.items())[:60]]
    if props:
        return sorted(str(k) for k in props), fields, ctype or "application/json"
    return (["(body)"] if schema else None), [], ctype


def _servers(doc: dict, swagger2: bool) -> list[dict]:
    out: list[dict] = []
    if swagger2:
        host = str(doc.get("host") or "")
        if host:
            for sch in (doc.get("schemes") or ["https"])[:3]:
                out.append(dict(url=f"{sch}://{host}{doc.get('basePath') or ''}".rstrip("/"), description=""))
        elif doc.get("basePath"):
            out.append(dict(url=str(doc["basePath"]), description=""))
        return out
    for s in (doc.get("servers") or [])[:20]:
        if not isinstance(s, dict) or not isinstance(s.get("url"), str):
            continue
        url = s["url"]
        for name, v in (s.get("variables") or {}).items() if isinstance(s.get("variables"), dict) else []:
            url = url.replace("{" + name + "}", str((v or {}).get("default", "")) if isinstance(v, dict) else "")
        out.append(dict(url=url.rstrip("/"), description=str(s.get("description") or "")[:120]))
    return out


def _security(doc: dict, swagger2: bool) -> list[dict]:
    schemes = doc.get("securityDefinitions") if swagger2 else (doc.get("components") or {}).get("securitySchemes")
    out = []
    for name, s in (schemes or {}).items() if isinstance(schemes, dict) else []:
        if not isinstance(s, dict):
            continue
        t, sch = str(s.get("type") or ""), str(s.get("scheme") or "").lower()
        if t == "http" and sch == "bearer" or t == "oauth2":
            kind = "bearer"
        elif t == "http" and sch == "basic" or t == "basic":
            kind = "basic"
        elif t == "apiKey":
            kind = "query" if s.get("in") == "query" else "header"
        else:
            kind = "header"
        out.append(dict(name=str(name)[:40], type=kind, header=str(s.get("name") or "") if t == "apiKey" else "Authorization"))
    return out


def parse_text(content: str, filename: str = "") -> dict:
    """Parse an OpenAPI/Swagger description. Returns {title, version, spec_version, servers, security, ops, warnings}."""
    doc = _load_text(content)
    if not isinstance(doc, dict) or not (doc.get("openapi") or doc.get("swagger")) or not isinstance(doc.get("paths"), dict):
        raise SpecError("this is not an OpenAPI description (it needs 'openapi' or 'swagger', and 'paths')")
    paths = doc["paths"]
    if len(paths) > MAX_PATHS:
        raise SpecError(f"too many paths ({len(paths)}; the limit is {MAX_PATHS})")
    swagger2 = bool(doc.get("swagger"))
    warnings: list[str] = []
    refs = _Refs(doc, warnings)
    info = doc.get("info") if isinstance(doc.get("info"), dict) else {}
    ops: dict[str, dict] = {}
    for path, raw_item in paths.items():
        item = refs.resolve(raw_item)
        if not isinstance(item, dict) or not str(path).startswith("/"):
            continue
        for m in METHODS:
            op = refs.resolve(item.get(m))
            if not isinstance(op, dict):
                continue
            if len(ops) >= MAX_OPS:
                warnings.append(f"only the first {MAX_OPS} operations were read")
                break
            params = [refs.resolve(p) for p in [*(item.get("parameters") or []), *(op.get("parameters") or [])] if isinstance(p, (dict,))]
            params = [p for p in params if isinstance(p, dict) and p.get("name") and p.get("in") in ("path", "query", "header", "cookie", "body", "formData")]
            plist = []
            for p in params:
                if p["in"] in ("body", "header", "cookie"):
                    continue
                info_ = _schema_info(refs, p.get("schema") if "schema" in p else {k: v for k, v in p.items() if k in ("type", "enum", "default", "format", "items")})
                plist.append(dict(name=str(p["name"]), **{"in": "query" if p["in"] == "formData" else p["in"]}, required=bool(p.get("required")) or p["in"] == "path",
                                  type=info_["type"], enum=info_.get("enum"), default=info_.get("default"), description=str(p.get("description") or "")[:200]))
            names, fields, ctype = _body(refs, op, swagger2, params)
            base = safe_name(op.get("operationId") or f"{m}_{path}")
            oid, n = base, 2
            while oid in ops:                                              # the old reader silently overwrote duplicates
                oid, n = f"{base}_{n}", n + 1
            sec = op.get("security")
            ops[oid] = dict(operation_id=oid, method=m, path=str(path), summary=str(op.get("summary") or op.get("description") or "")[:200],
                            description=str(op.get("description") or "")[:600], tags=[str(t)[:40] for t in (op.get("tags") or [])[:5]] if isinstance(op.get("tags"), list) else [],
                            parameters=plist, body=names, body_fields=fields, body_type=ctype, deprecated=bool(op.get("deprecated")),
                            responses=sorted(str(c) for c in (op.get("responses") or {}))[:12] if isinstance(op.get("responses"), dict) else [],
                            secured=bool(sec) if isinstance(sec, list) else bool(doc.get("security")))
    if not ops:
        raise SpecError("that description has no operations")
    servers = _servers(doc, swagger2)
    if not servers:
        warnings.append("the description names no server: enter the API's address when you import it")
    writes = sum(1 for o in ops.values() if o["method"] not in READ_METHODS)
    if writes:
        warnings.append(f"{writes} operation(s) change data; they stay switched off until you allow writes and confirm each use")
    return dict(title=str(info.get("title") or filename or "API")[:80], version=str(info.get("version") or "")[:30], spec_version=str(doc.get("openapi") or doc.get("swagger")),
                servers=servers, security=_security(doc, swagger2), ops=ops, warnings=warnings)


def pick_base(spec: dict, override: str = "", src: str = "") -> str:
    """The address calls go to: the person's override, else the spec's first server (a relative one is resolved against the spec's own URL)."""
    url = (override or "").strip() or (spec["servers"][0]["url"] if spec.get("servers") else "")
    if not url:
        raise SpecError("the description names no server: enter the API's address (https://…)")
    if not url.startswith(("http://", "https://")):
        if src.startswith(("http://", "https://")):
            url = urljoin(src, url)
        else:
            raise SpecError("the server address in the description is relative: enter the full address (https://…)")
    return url.rstrip("/")


def diff_ops(old: dict[str, dict], new: dict[str, dict]) -> dict:
    """What a refresh would change: added, removed and changed operations (method/path/parameters/body)."""
    def sig(o: dict) -> tuple:
        return (o.get("method"), o.get("path"), json.dumps(o.get("parameters"), sort_keys=True, default=str), json.dumps(o.get("body_fields", o.get("body")), sort_keys=True, default=str))
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if sig(old[k]) != sig(new[k]))
    return dict(added=added, removed=removed, changed=changed)
