"""Data sources agents may READ (never write), the data products that describe what is in them, and the audit trail of every access.

A *source* is one connection (a database, warehouse or SaaS org). An agent can only use the sources ticked in its editor
(`agent.data`); every query passes the read-only guard (sqlguard.py), is bounded in rows, bytes and time, can have personal data
masked, and is written to the audit table (statement text with secrets removed - never the rows). Credentials are write-only.
A *data product* is a named, owned, classified description of some objects in a source ("Customer orders - confidential, PII, owner
Sales Ops"). Products are metadata: they travel in the AI-BOM and discovery catalog so "which agent can see what" is answerable.
Grants are per SOURCE, not per table (a SQL guard cannot reliably tell which tables a query touches) - give an agent a source whose
account can only see what the agent may see.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from ..memory.pii import scrub
from .base import DS_REF, DataError, clean_limits, shape
from .connectors import CONNECTORS
from .sqlguard import check_soql, check_sql

CONFIG_KEY, SECRETS_KEY, PRODUCTS_KEY = "datasource_config", "datasource_secrets", "data_products"
CLASSES = ("public", "internal", "confidential", "restricted")
MAX_SOURCES, MAX_PRODUCTS = 30, 100


class DataService:
    def __init__(self, db: Any, factory: Callable[..., Any] | None = None):
        self.db = db
        self.factory = factory or (lambda kind, cfg, sec, limits: CONNECTORS[kind](cfg, sec, limits))

    # ------------------------------------------------------------------------------------------------------------------ sources
    def _sources(self) -> list[dict]:
        try:
            v = json.loads(self.db.get_setting(CONFIG_KEY) or "{}").get("sources", [])
        except (ValueError, AttributeError):
            v = []
        return v if isinstance(v, list) else []

    def _secrets(self) -> dict:
        try:
            v = json.loads(self.db.get_setting(SECRETS_KEY) or "{}")
        except ValueError:
            v = {}
        return v if isinstance(v, dict) else {}

    def kinds(self) -> list[dict]:
        return [c.info() for c in CONNECTORS.values()]

    def get(self, sid: str) -> dict:
        for s in self._sources():
            if s["id"] == sid:
                return s
        raise DataError(f"no data source called '{sid}'")

    def public(self) -> list[dict]:
        sec = self._secrets()
        return [{**s, "secrets_set": sorted(sec.get(s["id"], {}))} for s in self._sources()]

    def set_source(self, body: dict) -> dict:
        """Create or update a source. `secrets` are merged (an empty value clears one) and never returned."""
        if not isinstance(body, dict):
            raise DataError("the data source must be an object")
        sid, kind = str(body.get("id") or ""), str(body.get("kind") or "")
        if not DS_REF.match(sid):
            raise DataError("the id must be lowercase letters, digits, - or _ (2-41 characters, starting with a letter)")
        if kind not in CONNECTORS:
            raise DataError(f"kind must be one of: {', '.join(CONNECTORS)}")
        cls = CONNECTORS[kind]
        sources = self._sources()
        old = next((s for s in sources if s["id"] == sid), None)
        if old is None and len(sources) >= MAX_SOURCES:
            raise DataError(f"at most {MAX_SOURCES} data sources")
        if old and old["kind"] != kind:
            raise DataError("the kind of an existing source cannot change: delete it and add a new one")
        names = {f["name"] for f in cls.fields if not f["secret"]}
        cfg = {**(old["config"] if old else {}), **(body.get("config") or {})}
        if set(cfg) - names or any(not isinstance(v, str) or len(v) > 400 for v in cfg.values()):
            raise DataError(f"unknown or invalid settings: {', '.join(sorted(set(cfg) - names)) or 'values must be text of at most 400 characters'}")
        secret_names = {f["name"] for f in cls.fields if f["secret"]}
        given = body.get("secrets") or {}
        if not isinstance(given, dict) or set(given) - secret_names or any(not isinstance(v, str) for v in given.values()):
            raise DataError("those are not secret settings of this kind")
        allsec = self._secrets()
        merged = {k: v for k, v in {**allsec.get(sid, {}), **given}.items() if v}
        label = str(body.get("label") or (old or {}).get("label") or sid).strip()[:80]
        src = dict(id=sid, kind=kind, label=label or sid, description=str(body.get("description", (old or {}).get("description", "")))[:300], config=cfg,
                   limits=clean_limits({**((old or {}).get("limits") or {}), **(body.get("limits") or {})}),
                   redact=bool(body["redact"]) if "redact" in body else (old["redact"] if old else True),
                   enabled=bool(body["enabled"]) if "enabled" in body else (old["enabled"] if old else True))
        self.factory(kind, cfg, merged, src["limits"])                    # complete? (raises DataError naming what is missing)
        sources = [s for s in sources if s["id"] != sid] + [src]
        allsec[sid] = merged
        self.db.set_setting(CONFIG_KEY, json.dumps({"sources": sources}))
        self.db.set_setting(SECRETS_KEY, json.dumps(allsec))
        return {**src, "secrets_set": sorted(merged)}

    def delete_source(self, sid: str) -> None:
        self.get(sid)
        sec = self._secrets()
        sec.pop(sid, None)
        self.db.set_setting(CONFIG_KEY, json.dumps({"sources": [s for s in self._sources() if s["id"] != sid]}))
        self.db.set_setting(SECRETS_KEY, json.dumps(sec))
        self._save_products([p for p in self.products() if p["source"] != sid])

    def _connector(self, sid: str, need_enabled: bool = True) -> tuple[dict, Any]:
        s = self.get(sid)
        if need_enabled and not s["enabled"]:
            raise DataError(f"the data source '{sid}' is switched off")
        return s, self.factory(s["kind"], s["config"], self._secrets().get(sid, {}), s["limits"])

    # ------------------------------------------------------------------------------------------------------------------- audit
    def _audit(self, source: str, agent: str, kind: str, statement: str, rows: int, started: float, error: str = "") -> None:
        clean, _ = scrub(statement)
        self.db.execute("INSERT INTO data_audit(ts,source,agent,kind,statement,rows,ms,ok,error) VALUES(?,?,?,?,?,?,?,?,?)",
                        (time.time(), source, agent, kind, clean[:400], rows, int((time.time() - started) * 1000), int(not error), error[:300]))
        self.db.execute("DELETE FROM data_audit WHERE id <= (SELECT MAX(id) FROM data_audit) - 2000")

    def audit(self, source: str = "", limit: int = 100) -> list[dict]:
        rows = self.db.query("SELECT * FROM data_audit WHERE (?='' OR source=?) ORDER BY id DESC LIMIT ?", (source, source, max(1, min(int(limit), 500))))
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------------------------------------------------------- use
    def test(self, sid: str) -> dict:
        t0 = time.time()
        try:
            _, c = self._connector(sid, need_enabled=False)
            out = c.test()
        except DataError as e:
            self._audit(sid, "", "test", "", 0, t0, str(e))
            return dict(ok=False, detail=str(e))
        self._audit(sid, "", "test", "", 0, t0)
        return out

    def schema(self, sid: str, table: str = "", agent: str = "") -> dict:
        t0 = time.time()
        try:
            _, c = self._connector(sid)
            out = {"columns": c.columns(table)} if table else {"tables": c.tables()}
        except DataError as e:
            self._audit(sid, agent, "schema", table, 0, t0, str(e))
            raise
        self._audit(sid, agent, "schema", table, len(out.get("columns") or out.get("tables") or []), t0)
        return out

    def query(self, sid: str, sql: str, max_rows: int | None = None, agent: str = "") -> dict:
        t0 = time.time()
        try:
            s, c = self._connector(sid)
            lim = s["limits"]
            n = max(1, min(int(max_rows or lim["max_rows"]), lim["max_rows"]))
            body = check_soql(sql) if s["kind"] == "salesforce" else check_sql(sql)
            cols, rows = c.query(body, n)
            out = shape(cols, rows, n, lim["max_bytes"], s["redact"])
        except DataError as e:
            self._audit(sid, agent, "query", sql, 0, t0, str(e))
            raise
        self._audit(sid, agent, "query", sql, out["row_count"], t0)
        return out

    # ------------------------------------------------------------------------------------------------------------------ products
    def products(self) -> list[dict]:
        try:
            v = json.loads(self.db.get_setting(PRODUCTS_KEY) or "{}").get("products", [])
        except (ValueError, AttributeError):
            v = []
        return v if isinstance(v, list) else []

    def _save_products(self, items: list[dict]) -> None:
        self.db.set_setting(PRODUCTS_KEY, json.dumps({"products": items}))

    def set_product(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise DataError("the data product must be an object")
        pid = str(body.get("id") or "")
        if not DS_REF.match(pid):
            raise DataError("the id must be lowercase letters, digits, - or _ (2-41 characters, starting with a letter)")
        name = str(body.get("name") or "").strip()
        if not name:
            raise DataError("give the data product a name")
        source = str(body.get("source") or "")
        self.get(source)
        objects = body.get("objects") or []
        if not isinstance(objects, list) or len(objects) > 50 or any(not isinstance(o, str) or not re.fullmatch(r"[\w.$ -]{1,200}", o) for o in objects):
            raise DataError("objects must be a list of at most 50 table or object names")
        if body.get("classification", "internal") not in CLASSES:
            raise DataError(f"classification must be one of: {', '.join(CLASSES)}")
        tags = body.get("tags") or []
        if not isinstance(tags, list) or len(tags) > 10 or any(not isinstance(t, str) or len(t) > 40 for t in tags):
            raise DataError("tags must be a list of at most 10 short words")
        prod = dict(id=pid, name=name[:100], description=str(body.get("description") or "")[:500], source=source, objects=objects,
                    classification=body.get("classification", "internal"), owner=str(body.get("owner") or "")[:120], contains_pii=bool(body.get("contains_pii")), tags=tags)
        others = [p for p in self.products() if p["id"] != pid]
        if len(others) >= MAX_PRODUCTS:
            raise DataError(f"at most {MAX_PRODUCTS} data products")
        self._save_products([*others, prod])
        return prod

    def delete_product(self, pid: str) -> None:
        if not any(p["id"] == pid for p in self.products()):
            raise DataError(f"no data product called '{pid}'")
        self._save_products([p for p in self.products() if p["id"] != pid])

    # --------------------------------------------------------------------------------------------------------------- who sees what
    @staticmethod
    def granted_ids(grants: tuple | list, every: list[dict]) -> list[str]:
        return [s["id"] for s in every if s["enabled"] and ("*" in grants or s["id"] in grants)]

    def descriptors(self, agents: list[Any], externals: list[dict]) -> dict:
        """Which agent does what and which data it can read: Agent Studio's own agents and the external (CLI / ACP) ones."""
        sources, products = self._sources(), self.products()
        by_src: dict[str, list[dict]] = {}
        for p in products:
            by_src.setdefault(p["source"], []).append(p)
        rows = []
        for a in agents:
            ids = self.granted_ids(a.data, sources)
            rows.append(dict(id=a.id, name=a.name, kind="agent", purpose=a.description, framework=a.framework, runtime=a.runtime, sandbox=a.sandbox or "default",
                             skills=a.skills, tools=a.tools, mcp=a.mcp, sub_agents=a.sub_agents, external_agents=a.acp_agents, api_access=a.api_access, a2a=a.a2a,
                             data_sources=ids, data_products=[p["id"] for i in ids for p in by_src.get(i, [])]))
        for e in externals:
            rows.append(dict(id=e["id"], name=e["label"], kind="external", purpose=e.get("notes", ""), launcher=e["kind"], enabled=e["enabled"], installed=e["installed"],
                             used_by=sorted(a.id for a in agents if e["id"] in a.acp_agents), data_sources=[], data_products=[],
                             note="Runs outside Agent Studio: it can reach whatever its own account can, and its data access is not governed here."))
        return dict(agents=rows, products=products, sources=[dict(id=s["id"], label=s["label"], kind=s["kind"], enabled=s["enabled"]) for s in sources])

    def summary(self) -> dict:
        """For the AI-BOM: counts and kinds, never hosts, names or credentials."""
        src = self._sources()
        return dict(sources=len(src), kinds=sorted({s["kind"] for s in src}), products=len(self.products()))
