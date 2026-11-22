"""Turn an OpenAPI spec (file or URL) into an MCP server.

Three tools, so any API works without generating a tool per operation:
  list_operations()                      - every operation: id, method, path, summary
  describe_operation(operation_id)       - its parameters, body fields and whether it is allowed
  call_operation(operation_id, arguments) - call it (arguments: a JSON object of path/query/body fields)

Safety:
  * requests only go to the spec's server (no other hosts), redirects are not followed;
  * GET/HEAD/OPTIONS only, unless the server was added with writes allowed (OPENAPI_ALLOW_WRITE=1);
  * credentials come from HEADER_* environment variables (e.g. HEADER_AUTHORIZATION=Bearer …), stored locally.

Run:  python -m app.mcp_openapi <spec path or https URL> [--base https://api.example.com] [--sandbox-json]
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from mcp.server.mcpserver import MCPServer

READ_METHODS = {"get", "head", "options"}
METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
TRANSPORT = None                       # tests can inject an httpx transport
MAX_OUT = 20_000

server = MCPServer("openapi", instructions=(
    "Tools for an API described by an OpenAPI document. Start with list_operations, then describe_operation for the one you "
    "need, then call_operation. Read operations are allowed; changes need the server to be set up to allow them."))

_spec: dict = {}
_base: str = ""
_ops: dict[str, dict] = {}
_allow_write = os.environ.get("OPENAPI_ALLOW_WRITE") == "1"


def _safe_name(s: str) -> str:
    n = re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")
    return (n or "op")[:64]


def _load(src: str) -> dict:
    if src.startswith(("http://", "https://")):
        with httpx.Client(timeout=30, transport=TRANSPORT) as c:     # httpx.get() has no transport argument
            r = c.get(src)
        r.raise_for_status()
        text = r.text
    else:
        text = Path(src).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import yaml                    # OpenAPI is often written in YAML
        return yaml.safe_load(text)


def configure(src: str, base: str | None = None) -> None:
    """Load a spec into this process (the MCP server does this on start)."""
    global _spec, _base, _ops
    _spec, _base, _ops = parse(src, base)


def validate(src: str, base: str | None = None) -> int:
    """Check a spec without changing this process's state. Returns the number of operations."""
    return len(parse(src, base)[2])


def parse(src: str, base: str | None = None) -> tuple[dict, str, dict]:
    """(document, base URL, operations). Raises ValueError if the document is not an OpenAPI description."""
    doc = _load(src)
    if not isinstance(doc, dict) or not (doc.get("openapi") or doc.get("swagger")) or not isinstance(doc.get("paths"), dict):
        raise ValueError("this is not an OpenAPI document (it needs 'openapi' or 'swagger' and 'paths')")
    servers = doc.get("servers") or []
    url = base or (servers[0].get("url") if servers and isinstance(servers[0], dict) else None)
    if not url:
        raise ValueError("the spec has no server URL - pass --base https://…")
    if not url.startswith(("http://", "https://")):
        if src.startswith(("http://", "https://")):
            url = urljoin(src, url)
        else:
            raise ValueError("a relative server URL needs --base https://…")
    base_url = url.rstrip("/")
    ops: dict[str, dict] = {}
    for path, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for m in METHODS:
            op = item.get(m)
            if not isinstance(op, dict):
                continue
            oid = _safe_name(op.get("operationId") or f"{m}_{path}")
            params = [*item.get("parameters", []), *op.get("parameters", [])]
            ops[oid] = dict(operation_id=oid, method=m, path=path, summary=(op.get("summary") or op.get("description") or "")[:200],
                             parameters=[{"name": p.get("name"), "in": p.get("in"), "required": bool(p.get("required"))}
                                         for p in params if isinstance(p, dict)],
                             body=_body_fields(op))
    return doc, base_url, ops


def _body_fields(op: dict) -> list[str] | None:
    body = op.get("requestBody") or {}
    content = (body.get("content") or {}) if isinstance(body, dict) else {}
    schema = ((content.get("application/json") or {}).get("schema")) or {}
    props = schema.get("properties") if isinstance(schema, dict) else None
    return sorted(props.keys()) if isinstance(props, dict) else (["(body)"] if content else None)


def _allowed(op: dict) -> bool:
    return op["method"] in READ_METHODS or _allow_write


@server.tool(description="List the API's operations: operation_id, method, path and summary.")
def list_operations() -> dict:
    return {"api": _spec.get("info", {}).get("title"), "base_url": _base,
            "operations": [{"operation_id": o["operation_id"], "method": o["method"].upper(), "path": o["path"], "summary": o["summary"],
                            "allowed": _allowed(o)} for o in _ops.values()]}


@server.tool(description="Describe one operation: its parameters, body fields, and whether it is allowed to run here.")
def describe_operation(operation_id: str) -> dict:
    op = _ops.get(operation_id)
    if op is None:
        return {"ok": False, "error": f"no operation '{operation_id}' - call list_operations"}
    return {"ok": True, **op, "allowed": _allowed(op),
            "note": None if _allowed(op) else "write operations are disabled on this server (set OPENAPI_ALLOW_WRITE=1 to allow them)"}


def _headers() -> dict:
    out = {"Accept": "application/json"}
    for k, v in os.environ.items():
        if k.upper().startswith("HEADER_") and v:
            out["-".join(w.capitalize() for w in k[7:].split("_"))] = v
    return out


@server.tool(description="Call one operation. arguments is a JSON object with its path, query and body fields.")
def call_operation(operation_id: str, arguments: str = "{}") -> dict:
    op = _ops.get(operation_id)
    if op is None:
        return {"ok": False, "error": f"no operation '{operation_id}' - call list_operations"}
    if not _allowed(op):
        return {"ok": False, "error": f"{op['method'].upper()} is a write operation and is disabled on this server"}
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        return {"ok": False, "error": f"arguments: {e}"}
    path = op["path"]
    for p in re.findall(r"\{([^}]+)\}", path):
        if p not in args:
            return {"ok": False, "error": f"missing path parameter '{p}'"}
        path = path.replace("{" + p + "}", str(args.pop(p)))
    url = _base + path
    if urlparse(url).netloc != urlparse(_base).netloc:
        return {"ok": False, "error": "refused: the request would leave the API's server"}
    query = {k: v for k, v in args.items() if k in {p["name"] for p in op["parameters"] if p["in"] == "query"}}
    body = {k: v for k, v in args.items() if k not in query}
    try:
        with httpx.Client(timeout=30, follow_redirects=False, transport=TRANSPORT) as c:
            r = c.request(op["method"].upper(), url, params=query or None, json=body or None, headers=_headers())
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"request failed: {type(e).__name__}: {e}"[:400]}
    text = r.text
    truncated = len(text) > MAX_OUT
    try:
        payload = r.json()
    except ValueError:
        payload = text[:MAX_OUT]
    return {"ok": r.status_code < 400, "status": r.status_code, "body": payload, "truncated": truncated}


def main(argv: list[str]) -> None:
    if not argv:
        print("usage: python -m app.mcp_openapi <spec path or URL> [--base URL]", file=sys.stderr)
        raise SystemExit(2)
    base = argv[argv.index("--base") + 1] if "--base" in argv else None
    configure(argv[0], base)
    server.run("stdio")


if __name__ == "__main__":
    main(sys.argv[1:])
