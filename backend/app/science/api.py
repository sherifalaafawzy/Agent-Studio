"""REST API for the Science Lab: /api/science/*. Bodies are plain dicts on purpose (main.py uses postponed annotations)."""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request

from . import compute
from .service import ScienceError


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/science", tags=["science"])

    def svc(request: Request):
        return request.app.state.s.science

    def ins(request: Request):
        return request.app.state.s.science_components

    def guard(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except ScienceError as e:
            raise HTTPException(404 if str(e).startswith("no such") else 422, str(e))

    # ---- settings, keys, sources, safety
    @r.get("/config")
    def get_config(request: Request):
        return svc(request).public_config()

    @r.put("/config")
    def put_config(body: dict, request: Request):
        guard(svc(request).set_config, body)
        return svc(request).public_config()

    @r.put("/keys/{cid}")
    def put_key(cid: str, body: dict, request: Request):
        guard(svc(request).set_key, cid, body.get("key", ""))
        return {"ok": True, "connectors": svc(request).connectors()}

    @r.get("/connectors")
    def connectors(request: Request):
        return svc(request).connectors()

    @r.post("/screen")
    def screen(body: dict, request: Request):
        text = body.get("text", "")
        if not isinstance(text, str):
            raise HTTPException(422, "text must be a string")
        return svc(request).screen(text, "input").public()

    @r.post("/search")
    def search(body: dict, request: Request):
        s = svc(request)
        out = guard(s.source, str(body.get("source", "")), str(body.get("operation", "search")), str(body.get("query", "")), int(body.get("limit", 5) or 5), str(body.get("investigation_id", "")), "user")
        return out

    @r.post("/calc")
    def calc(body: dict, request: Request):
        try:
            v = body.get("variables") or {}
            if not isinstance(v, dict):
                raise ValueError("variables must be an object")
            return compute.dispatch(str(body.get("kind", "")), str(body.get("expression", "")), v, float(body.get("a", 0) or 0), float(body.get("b", 1) or 1),
                                    str(body.get("variable", "x") or "x"), str(body.get("unit_from", "")), str(body.get("unit_to", "")))
        except (ValueError, TypeError) as e:
            raise HTTPException(422, str(e))

    @r.get("/constants")
    def constants():
        return {"source": compute.CONSTANTS_SOURCE, "constants": [compute.constant(k) for k in compute.CONSTANTS]}

    # ---- investigations
    @r.get("/investigations")
    def list_inv(request: Request, domain: str = "", limit: int = 100):
        return svc(request).list(domain, limit)

    @r.post("/investigations")
    def create(body: dict, request: Request):
        return guard(svc(request).create, body.get("title", ""), body.get("domain", "cross"), body.get("question", ""), body.get("autonomy", "approve"), body.get("session_id", ""), "user")

    @r.get("/investigations/{iid}")
    def get_inv(iid: str, request: Request):
        s = svc(request)
        head = guard(s.get, iid)
        return {**head, "claims_list": s.claims(iid), "evidence_list": s.evidence(iid), "hypotheses_list": s.hypotheses(iid), "runs_list": s.runs(iid)}

    @r.put("/investigations/{iid}")
    def put_inv(iid: str, body: dict, request: Request):
        return guard(svc(request).update, iid, body)

    @r.delete("/investigations/{iid}")
    def del_inv(iid: str, request: Request):
        guard(svc(request).delete, iid)
        return {"ok": True}

    @r.post("/investigations/{iid}/evidence")
    def add_evidence(iid: str, body: dict, request: Request):
        return guard(svc(request).add_evidence, iid, body)

    @r.post("/investigations/{iid}/claims")
    def add_claim(iid: str, body: dict, request: Request):
        return guard(svc(request).add_claim, iid, body.get("text", ""), body.get("evidence_ids") or [], bool(body.get("conflicting", False)))

    @r.delete("/investigations/{iid}/claims/{cid}")
    def del_claim(iid: str, cid: str, request: Request):
        guard(svc(request).delete_claim, iid, cid)
        return {"ok": True}

    @r.post("/investigations/{iid}/hypotheses")
    def add_hyp(iid: str, body: dict, request: Request):
        return guard(svc(request).add_hypothesis, iid, body.get("text", ""))

    @r.post("/investigations/{iid}/compare")
    def compare(iid: str, body: dict, request: Request):
        return guard(svc(request).compare, iid, body.get("winner", ""), body.get("loser", ""))

    @r.post("/investigations/{iid}/verify")
    def verify(iid: str, request: Request):
        s = svc(request)
        guard(s.get, iid)
        return s.verify_citations(iid)

    @r.get("/investigations/{iid}/report")
    def report(iid: str, request: Request):
        return guard(svc(request).report, iid)

    @r.post("/investigations/{iid}/notebook")
    def notebook(iid: str, request: Request):
        return guard(svc(request).save_notebook, iid, "user")

    # ---- optional components (isolated installs)
    @r.get("/components")
    def components(request: Request):
        return ins(request).status()

    @r.post("/components/{cid}/install")
    def install(cid: str, body: dict, request: Request):
        try:
            return ins(request).install(cid, body.get("licence_ack") is True)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @r.delete("/components/{cid}")
    def uninstall(cid: str, request: Request):
        try:
            return ins(request).uninstall(cid)
        except ValueError as e:
            raise HTTPException(422, str(e))

    app.include_router(r)
