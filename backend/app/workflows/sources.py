"""APIs imported as workflow building blocks: upload / URL / paste an OpenAPI description, then create, read, update and delete the API and its endpoints.

Rules that never change: credentials are write-only (never returned, masked in previews); only the API's own server is called (the path is appended to the
base address, never replaces it); redirects are not followed; GET/HEAD/OPTIONS only unless the API allows writes AND the node/test confirms the call.
Spec-derived endpoints can be renamed, switched off, given default arguments or hidden; endpoints the person adds by hand are `custom` and fully editable.
Deleting an API or an endpoint that workflows use is refused (409) unless forced, and says which workflows use it.
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from ..db import Database
from . import openapi_spec as OS
from .core import MAX_TEXT, SRC_ID, WorkflowError

AUTH_TYPES = ("none", "bearer", "header", "query", "basic")
ORIGINS = ("url", "file", "upload", "paste")
READ = OS.READ_METHODS
PARAM_IN = ("path", "query")
PARAM_TYPES = ("string", "integer", "number", "boolean", "array", "object", "any")


class InUse(WorkflowError):
    """Refused because workflows still use it; `used_by` lists them."""

    def __init__(self, what: str, used_by: list[str]):
        super().__init__(f"{what} is used by {', '.join(used_by[:6])}{' …' if len(used_by) > 6 else ''}: remove it there first, or delete anyway")
        self.used_by = used_by


def _risk(method: str) -> str:
    return "read" if method in READ else "delete" if method == "delete" else "write"


class OpenApiSources:
    def __init__(self, db: Database, transport: httpx.AsyncBaseTransport | None = None, loader=None, clock=time.time,
                 usage: Callable[[str, str | None], list[str]] | None = None):
        self.db, self.transport, self.clock, self.loader = db, transport, clock, loader
        self.usage = usage                                           # (source id, operation id | None) -> ids of workflows that use it

    # -- reading a description
    def _read(self, src: str, base: str) -> tuple[dict, str, str]:
        """(spec, base URL, raw text) for an address (https://…) or a workspace file."""
        if self.loader:                                              # tests / custom loaders return the legacy (doc, base, ops) shape
            _doc, base_url, ops = self.loader(src, base or None)
            return dict(title="", version="", spec_version="", servers=[dict(url=base_url, description="")], security=[], ops=ops, warnings=[]), base_url, ""
        if src.startswith(("http://", "https://")):
            with httpx.Client(timeout=30, follow_redirects=True) as c:
                resp = c.get(src)
            resp.raise_for_status()
            text = resp.text
        else:
            text = Path(src).read_text(encoding="utf-8")
        spec = OS.parse_text(text, src.rsplit("/", 1)[-1])
        return spec, OS.pick_base(spec, base, src), text

    def _spec(self, src: str, content: str, filename: str, base: str) -> tuple[dict, str, str]:
        try:
            if content:
                spec = OS.parse_text(content, filename)
                return spec, OS.pick_base(spec, base), content
            return self._read(src, base)
        except Exception as e:  # noqa: BLE001 - a plain reason for the person
            raise WorkflowError(f"could not read that OpenAPI description: {str(e)[:300]}")

    # -- the operations of a source
    @staticmethod
    def _effective(r) -> dict[str, dict]:
        spec_ops, custom = json.loads(r["ops"]), json.loads(r["custom"] or "{}")
        ov = json.loads(r["overrides"] or "{}")
        out: dict[str, dict] = {}
        for oid, o in {**spec_ops, **custom}.items():
            o2 = ov.get(oid, {})
            x = {"summary": "", "parameters": [], "body": None, "tags": [], "description": "", "body_fields": [], **o}
            x.update(id=oid, operation_id=oid, origin="custom" if oid in custom else "spec", enabled=bool(o2.get("enabled", True)), hidden=bool(o2.get("hidden")),
                     confirm_required=bool(o2.get("confirm_required")), defaults=o2.get("defaults") or {}, risk=_risk(o["method"]))
            if o2.get("label"):
                x["summary"] = o2["label"]
            if o2.get("description") is not None:
                x["description"] = o2["description"]
            out[oid] = x
        return out

    @staticmethod
    def _op_public(o: dict) -> dict:
        keys = ("id", "method", "path", "summary", "description", "parameters", "body", "body_fields", "tags", "origin", "enabled", "hidden", "risk", "confirm_required", "defaults", "deprecated")
        return {k: o.get(k) for k in keys}

    def _used(self, sid: str, op: str | None = None) -> list[str]:
        return self.usage(sid, op) if self.usage else []

    def _public(self, r, detail: bool = True) -> dict:
        ops = self._effective(r)
        vis = [o for o in ops.values() if not o["hidden"]]
        auth = json.loads(r["auth"] or "{}")
        meta = json.loads(r["meta"] or "{}")
        out = dict(id=r["id"], name=r["name"], src=r["src"], base=r["base"], allow_write=bool(r["allow_write"]), has_token=bool(r["token"]), created=r["created"],
                   origin=r["origin"] or "url", auth=dict(type=auth.get("type") or ("header" if r["token"] else "none"), name=auth.get("name") or ""),
                   servers=json.loads(r["servers"] or "[]"), refreshed=r["refreshed"], updated=r["updated"] or r["created"], title=meta.get("title", ""), version=meta.get("version", ""),
                   spec_version=meta.get("spec_version", ""), warnings=meta.get("warnings", []), security=meta.get("security", []),
                   counts=dict(total=len(vis), enabled=sum(1 for o in vis if o["enabled"]), write=sum(1 for o in vis if o["risk"] != "read"), hidden=len(ops) - len(vis)),
                   used_by=self._used(r["id"]))
        out["operations"] = [self._op_public(o) for o in vis] if detail else []
        return out

    def _row(self, sid: str):
        r = self.db.one("SELECT * FROM openapi_sources WHERE id=?", (sid,))
        if r is None:
            raise KeyError(sid)
        return r

    def list(self) -> list[dict]:
        return [self._public(r) for r in self.db.query("SELECT * FROM openapi_sources ORDER BY created")]

    def get(self, sid: str) -> dict:
        return self._public(self._row(sid))

    def for_validation(self) -> dict[str, dict]:
        return {r["id"]: dict(ops={k: v for k, v in self._effective(r).items() if not v["hidden"]}, allow_write=bool(r["allow_write"])) for r in self.db.query("SELECT * FROM openapi_sources")}

    # -- import
    def preview(self, src: str = "", content: str = "", filename: str = "", base: str = "") -> dict:
        if not src and not content:
            raise WorkflowError("upload a file, give an address or paste the description")
        spec, base_url, _text = self._spec(src, content, filename, base)
        ops = spec["ops"]
        tags: dict[str, int] = {}
        for o in ops.values():
            for t in (o.get("tags") or ["(untagged)"])[:1]:
                tags[t] = tags.get(t, 0) + 1
        return dict(title=spec["title"], version=spec["version"], spec_version=spec["spec_version"], servers=spec["servers"], base=base_url, security=spec["security"],
                    warnings=spec["warnings"], suggested_id=OS.safe_name(spec["title"] or "api")[:30].lstrip("_0123456789") or "api",
                    suggested_auth=(spec["security"][0] if spec["security"] else dict(type="none", name="", header="")),
                    counts=dict(total=len(ops), read=sum(1 for o in ops.values() if o["method"] in READ), write=sum(1 for o in ops.values() if o["method"] not in READ), tags=tags),
                    operations=[dict(id=k, method=o["method"], path=o["path"], summary=o.get("summary", ""), tags=o.get("tags", [])) for k, o in list(ops.items())[:300]])

    def add(self, sid: str, name: str, src: str = "", base: str = "", token: str = "", allow_write: bool = False, content: str = "", filename: str = "",
            auth: dict | None = None, origin: str = "") -> dict:
        if not SRC_ID.match(sid or ""):
            raise WorkflowError("the API id must be lowercase letters, digits and underscores, starting with a letter")
        if self.db.one("SELECT id FROM openapi_sources WHERE id=?", (sid,)):
            raise WorkflowError(f"an API '{sid}' is already imported")
        if not content and (not src or len(src) > 500):
            raise WorkflowError("give the address (https://...) or the workspace file of the OpenAPI description")
        spec, base_url, text = self._spec(src, content, filename, base)
        if not spec["ops"]:
            raise WorkflowError("that description has no operations")
        a = self._auth(auth, token)
        kind = origin if origin in ORIGINS else ("upload" if content and filename else "paste" if content else "url" if src.startswith(("http://", "https://")) else "file")
        now = self.clock()
        meta = dict(title=spec["title"], version=spec["version"], spec_version=spec["spec_version"], warnings=spec["warnings"], security=spec["security"])
        self.db.execute("INSERT INTO openapi_sources(id,name,src,base,ops,token,allow_write,created,spec,origin,auth,servers,refreshed,updated,overrides,custom,meta) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sid, (name or sid)[:60], src if not content else (filename or "pasted")[:200], base_url, json.dumps(spec["ops"]), token or "", int(bool(allow_write)), now, text, kind,
                         json.dumps(a), json.dumps(spec["servers"]), now, now, "{}", "{}", json.dumps(meta)))
        return self.get(sid)

    @staticmethod
    def _auth(auth: dict | None, token: str) -> dict:
        auth = auth or {}
        t = auth.get("type") or ("header" if token else "none")
        if t not in AUTH_TYPES:
            raise WorkflowError(f"the sign-in type must be one of {', '.join(AUTH_TYPES)}")
        name = str(auth.get("name") or ("Authorization" if t in ("header", "bearer", "basic") else ""))[:60]
        if t in ("header", "query") and not re.match(r"^[A-Za-z0-9_.\-]{1,60}$", name):
            raise WorkflowError("give the header or query-parameter name the key goes in (letters, digits, - _ .)")
        return dict(type=t, name=name)

    def update(self, sid: str, patch: dict) -> dict:
        r = self._row(sid)
        token = r["token"] if patch.get("token") is None else patch["token"]                       # None = unchanged, "" = clear
        a = json.loads(r["auth"] or "{}")
        if "auth" in patch:
            a = self._auth(patch["auth"], token)
        base = r["base"]
        if patch.get("base"):
            base = str(patch["base"]).strip().rstrip("/")
            if not base.startswith(("http://", "https://")):
                raise WorkflowError("the API address must start with http:// or https://")
        self.db.execute("UPDATE openapi_sources SET name=?, token=?, allow_write=?, auth=?, base=?, updated=? WHERE id=?",
                        ((patch.get("name") or r["name"])[:60], token, int(patch.get("allow_write", bool(r["allow_write"]))), json.dumps(a), base, self.clock(), sid))
        return self.get(sid)

    def duplicate(self, sid: str, new_id: str, name: str = "") -> dict:
        r = self._row(sid)
        if not SRC_ID.match(new_id or ""):
            raise WorkflowError("the API id must be lowercase letters, digits and underscores, starting with a letter")
        if self.db.one("SELECT id FROM openapi_sources WHERE id=?", (new_id,)):
            raise WorkflowError(f"an API '{new_id}' is already imported")
        now = self.clock()                                                                              # copies the spec and the edits, never the credentials
        self.db.execute("INSERT INTO openapi_sources(id,name,src,base,ops,token,allow_write,created,spec,origin,auth,servers,refreshed,updated,overrides,custom,meta) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (new_id, (name or f"{r['name']} copy")[:60], r["src"], r["base"], r["ops"], "", int(bool(r["allow_write"])), now, r["spec"], r["origin"], r["auth"], r["servers"],
                         r["refreshed"], now, r["overrides"], r["custom"], r["meta"]))
        return self.get(new_id)

    def delete(self, sid: str, force: bool = False) -> None:
        self._row(sid)
        used = self._used(sid)
        if used and not force:
            raise InUse(f"API '{sid}'", used)
        self.db.execute("DELETE FROM openapi_sources WHERE id=?", (sid,))

    def export_spec(self, sid: str) -> str:
        r = self._row(sid)
        if r["spec"]:
            return r["spec"]
        return json.dumps(dict(openapi="3.0.0", info=dict(title=r["name"], version="0"), servers=[dict(url=r["base"])], paths={}), indent=2)

    # -- refresh
    def refresh(self, sid: str, src: str = "", content: str = "", filename: str = "", dry_run: bool = True) -> dict:
        r = self._row(sid)
        if not content and not src and r["origin"] in ("upload", "paste"):
            raise WorkflowError("this API came from a file you provided: upload or paste the new version")
        spec, _base, text = self._spec(src or ("" if content else r["src"]), content, filename, "")
        old = json.loads(r["ops"])
        d = OS.diff_ops(old, spec["ops"])
        affected = sorted({w for k in (*d["removed"], *d["changed"]) for w in self._used(sid, k)})
        res = dict(diff=d, affected=affected, applied=False, warnings=spec["warnings"], title=spec["title"], version=spec["version"])
        if not dry_run:
            meta = dict(title=spec["title"], version=spec["version"], spec_version=spec["spec_version"], warnings=spec["warnings"], security=spec["security"])
            now = self.clock()
            self.db.execute("UPDATE openapi_sources SET ops=?, spec=?, servers=?, meta=?, refreshed=?, updated=?, src=? WHERE id=?",
                            (json.dumps(spec["ops"]), text or r["spec"], json.dumps(spec["servers"]), json.dumps(meta), now, now, src or r["src"], sid))
            res["applied"] = True
        return res

    # -- endpoints (operations)
    def operations(self, sid: str, q: str = "", tag: str = "", method: str = "", enabled: bool | None = None, hidden: bool = False) -> list[dict]:
        out = []
        for o in self._effective(self._row(sid)).values():
            if o["hidden"] and not hidden:
                continue
            if q and q.lower() not in f"{o['id']} {o['path']} {o['summary']} {' '.join(o['tags'])}".lower():
                continue
            if tag and tag not in o["tags"] or method and o["method"] != method.lower() or enabled is not None and o["enabled"] != enabled:
                continue
            out.append({**self._op_public(o), "used_by": self._used(sid, o["id"])})
        return out

    def operation(self, sid: str, op: str) -> dict:
        o = self._effective(self._row(sid)).get(op)
        if o is None:
            raise KeyError(op)
        return {**self._op_public(o), "used_by": self._used(sid, op)}

    @staticmethod
    def _clean_params(params: Any, path: str) -> list[dict]:
        if not isinstance(params, list) or len(params) > 40:
            raise WorkflowError("parameters are a list of at most 40")
        out: list[dict] = []
        for p in params:
            if not isinstance(p, dict) or not re.match(r"^[A-Za-z0-9_.\-]{1,60}$", str(p.get("name") or "")) or p.get("in", "query") not in PARAM_IN:
                raise WorkflowError("each parameter needs a name and to be a path or query parameter")
            t = p.get("type", "string")
            out.append(dict(name=p["name"], **{"in": p.get("in", "query")}, required=bool(p.get("required")) or p.get("in") == "path", type=t if t in PARAM_TYPES else "string",
                            enum=None, default=p.get("default"), description=str(p.get("description") or "")[:200]))
        for name in re.findall(r"\{([^}]+)\}", path):                                               # every {placeholder} in the path is a required path parameter
            if not any(p["name"] == name and p["in"] == "path" for p in out):
                out.append(dict(name=name, **{"in": "path"}, required=True, type="string", enum=None, default=None, description=""))
        return out

    def _clean_custom(self, body: dict, oid: str) -> dict:
        method, path = str(body.get("method") or "get").lower(), str(body.get("path") or "")
        if method not in OS.METHODS:
            raise WorkflowError(f"the method must be one of {', '.join(OS.METHODS)}")
        if not path.startswith("/") or len(path) > 300 or "//" in path or ".." in path or "?" in path or "#" in path:
            raise WorkflowError("the path must start with / and be a plain path (no ?, # or ..)")
        fields = body.get("body_fields") or []
        if not isinstance(fields, list) or len(fields) > 60 or any(not isinstance(f, dict) or not re.match(r"^[A-Za-z0-9_.\-]{1,60}$", str(f.get("name") or "")) for f in fields):
            raise WorkflowError("body fields are a list of at most 60 named fields")
        bf = [dict(name=f["name"], required=bool(f.get("required")), type=f.get("type") if f.get("type") in PARAM_TYPES else "string", description=str(f.get("description") or "")[:200]) for f in fields]
        return dict(operation_id=oid, method=method, path=path, summary=str(body.get("label") or body.get("summary") or oid)[:200], description=str(body.get("description") or "")[:600],
                    tags=["custom"], parameters=self._clean_params(body.get("parameters") or [], path), body=sorted(f["name"] for f in bf) if bf else None, body_fields=bf,
                    body_type="application/json" if bf else "", deprecated=False)

    def _save_json(self, sid: str, col: str, value: dict) -> None:
        self.db.execute(f"UPDATE openapi_sources SET {col}=?, updated=? WHERE id=?", (json.dumps(value), self.clock(), sid))

    def create_operation(self, sid: str, body: dict) -> dict:
        r = self._row(sid)
        oid = OS.safe_name(body.get("id") or body.get("label") or f"{body.get('method', 'get')}_{body.get('path', '')}")
        if oid in self._effective(r):
            raise WorkflowError(f"the API already has an endpoint '{oid}': choose another name")
        custom = json.loads(r["custom"] or "{}")
        custom[oid] = self._clean_custom(body, oid)
        self._save_json(sid, "custom", custom)
        return self.operation(sid, oid)

    def update_operation(self, sid: str, op: str, patch: dict) -> dict:
        r = self._row(sid)
        o = self._effective(r).get(op)
        if o is None:
            raise KeyError(op)
        custom, ov = json.loads(r["custom"] or "{}"), json.loads(r["overrides"] or "{}")
        if op in custom and any(k in patch for k in ("method", "path", "parameters", "body_fields")):
            custom[op] = self._clean_custom({**custom[op], "label": custom[op]["summary"], **patch}, op)
            self._save_json(sid, "custom", custom)
        mine = dict(ov.get(op, {}))
        if "label" in patch:
            mine["label"] = str(patch["label"] or "")[:200]
        if "description" in patch:
            mine["description"] = str(patch["description"] or "")[:600]
        for k in ("enabled", "hidden", "confirm_required"):
            if k in patch:
                mine[k] = bool(patch[k])
        if "defaults" in patch:
            if not isinstance(patch["defaults"], dict) or len(patch["defaults"]) > 30:
                raise WorkflowError("default arguments are an object with at most 30 entries")
            mine["defaults"] = {str(k)[:60]: v if isinstance(v, (int, float, bool)) else str(v)[:2000] for k, v in patch["defaults"].items()}
        ov[op] = mine
        self._save_json(sid, "overrides", ov)
        return self.operation(sid, op)

    def delete_operation(self, sid: str, op: str, force: bool = False) -> dict:
        r = self._row(sid)
        o = self._effective(r).get(op)
        if o is None:
            raise KeyError(op)
        used = self._used(sid, op)
        if used and not force:
            raise InUse(f"endpoint '{op}'", used)
        if o["origin"] == "custom":
            custom = json.loads(r["custom"])
            custom.pop(op, None)
            self._save_json(sid, "custom", custom)
            ov = json.loads(r["overrides"] or "{}")
            ov.pop(op, None)
            self._save_json(sid, "overrides", ov)
            return dict(ok=True, removed=True)
        ov = json.loads(r["overrides"] or "{}")
        ov[op] = {**ov.get(op, {}), "hidden": True}                                              # a spec endpoint is hidden, not lost: a refresh or "show hidden" brings it back
        self._save_json(sid, "overrides", ov)
        return dict(ok=True, removed=False, hidden=True)

    # -- calling
    @staticmethod
    def _auth_parts(r) -> tuple[dict, dict]:
        tok, a = r["token"], json.loads(r["auth"] or "{}")
        t = a.get("type") or ("header" if tok else "none")
        name = a.get("name") or "Authorization"
        if not tok or t == "none":
            return {}, {}
        if t == "bearer":
            return {"Authorization": tok if tok.lower().startswith("bearer ") else f"Bearer {tok}"}, {}
        if t == "basic":
            return {"Authorization": tok if tok.lower().startswith("basic ") else "Basic " + base64.b64encode(tok.encode()).decode()}, {}
        if t == "query":
            return {}, {name: tok}
        return {name: tok}, {}

    def _build(self, r, op: dict, args: dict, confirmed: bool) -> dict:
        if op["method"] not in READ and not (r["allow_write"] and confirmed):
            return {"ok": False, "error": f"{op['method'].upper()} changes data: it needs 'allow writes' on the API and 'I confirm' on the node"}
        if op.get("confirm_required") and not confirmed:
            return {"ok": False, "error": "this endpoint needs 'I confirm' on the node"}
        if op.get("hidden") or not op.get("enabled", True):
            return {"ok": False, "error": f"the endpoint '{op.get('id', '')}' is switched off"}
        args = {**(op.get("defaults") or {}), **args}
        path = op["path"]
        for p in re.findall(r"\{([^}]+)\}", path):
            if p not in args:
                return {"ok": False, "error": f"missing path parameter '{p}'"}
            path = path.replace("{" + p + "}", str(args.pop(p)))
        qnames = {p["name"] for p in op["parameters"] if p["in"] == "query"}
        query = {k: v for k, v in args.items() if k in qnames}
        body = {k: v for k, v in args.items() if k not in qnames}
        ah, aq = self._auth_parts(r)
        return dict(ok=True, method=op["method"].upper(), url=r["base"].rstrip("/") + path,      # always the API's own server: the path is appended to the base, never replaces it
                    query={**query, **aq}, body=body, headers={"Accept": "application/json", **ah}, secret_query=list(aq), secret_headers=list(ah))

    async def _send(self, req: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=self.transport) as c:
                resp = await c.request(req["method"], req["url"], params=req["query"] or None, json=req["body"] or None, headers=req["headers"])
        except httpx.HTTPError as e:
            return {"ok": False, "error": f"request failed: {type(e).__name__}"}
        try:
            payload: Any = resp.json()
        except ValueError:
            payload = resp.text[:MAX_TEXT]
        return {"ok": resp.status_code < 400, "status": resp.status_code, "body": payload}

    async def call(self, sid: str, operation: str, args: dict, confirmed: bool) -> dict:
        r = self.db.one("SELECT * FROM openapi_sources WHERE id=?", (sid,))
        if r is None:
            return {"ok": False, "error": f"API '{sid}' is not imported"}
        op = self._effective(r).get(operation)
        if op is None:
            return {"ok": False, "error": f"the API has no operation '{operation}'"}
        req = self._build(r, op, dict(args), confirmed)
        return await self._send(req) if req["ok"] else req

    async def test(self, sid: str, operation: str, args: dict, confirmed: bool) -> dict:
        """One real call for the person to see: what was sent (credentials masked) and what came back (cut at 20 000 characters)."""
        r = self._row(sid)
        op = self._effective(r).get(operation)
        if op is None:
            raise KeyError(operation)
        req = self._build(r, {**op, "hidden": False, "enabled": True}, dict(args), confirmed)
        if not req["ok"]:
            return req
        shown = dict(method=req["method"], url=req["url"], query={k: ("•••" if k in req["secret_query"] else v) for k, v in req["query"].items()},
                     body=req["body"], headers={k: ("•••" if k in req["secret_headers"] else v) for k, v in req["headers"].items()})
        res = await self._send(req)
        if isinstance(res.get("body"), (dict, list)):
            text = json.dumps(res["body"], default=str)
            if len(text) > 20_000:
                res["body"] = text[:20_000]
                res["truncated"] = True
        elif isinstance(res.get("body"), str):
            res["body"] = res["body"][:20_000]
        return {**res, "request": shown}
