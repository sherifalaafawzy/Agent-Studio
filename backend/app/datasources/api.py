"""REST API for data sources and data products: /api/data/*. Pydantic-free on purpose (main.py uses postponed annotations)."""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request

from .base import DataError


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/data", tags=["data"])

    def svc(request: Request):
        return request.app.state.s.data

    def guard(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except DataError as e:
            raise HTTPException(404 if str(e).startswith("no ") else 422, str(e))

    @r.get("/sources")
    def sources(request: Request):
        return {"sources": svc(request).public(), "kinds": svc(request).kinds()}

    @r.put("/sources")
    def put_source(body: dict, request: Request):
        return guard(svc(request).set_source, body)

    @r.delete("/sources/{sid}")
    def delete_source(sid: str, request: Request):
        guard(svc(request).delete_source, sid)
        return {"ok": True}

    @r.post("/sources/{sid}/test")
    def test(sid: str, request: Request):
        return guard(svc(request).test, sid)

    @r.get("/sources/{sid}/schema")
    def schema(sid: str, request: Request, table: str = ""):
        return guard(svc(request).schema, sid, table, "you")

    @r.post("/sources/{sid}/query")
    def query(sid: str, body: dict, request: Request):
        return guard(svc(request).query, sid, str(body.get("sql") or ""), body.get("max_rows"), "you")

    @r.get("/audit")
    def audit(request: Request, source: str = "", limit: int = 100):
        return {"audit": svc(request).audit(source, limit)}

    @r.get("/products")
    def products(request: Request):
        return {"products": svc(request).products()}

    @r.put("/products")
    def put_product(body: dict, request: Request):
        return guard(svc(request).set_product, body)

    @r.delete("/products/{pid}")
    def delete_product(pid: str, request: Request):
        guard(svc(request).delete_product, pid)
        return {"ok": True}

    @r.get("/descriptors")
    def descriptors(request: Request):
        s = request.app.state.s
        try:
            externals = s.acp.store.list()
        except Exception:  # noqa: BLE001 - probing for installed CLIs must never break the page
            externals = []
        return svc(request).descriptors(s.agents.list(), externals)

    app.include_router(r)
