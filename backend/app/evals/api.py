"""REST API for evaluations: /api/evals/*. Pydantic-free on purpose."""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .service import EXPORTS, EvalError
from .starters import starters


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/evals", tags=["evals"])

    def svc(request: Request):
        return request.app.state.s.evals

    def wrap(fn, *a):
        try:
            return fn(*a)
        except EvalError as e:
            raise HTTPException(422, str(e))
        except KeyError:
            raise HTTPException(404, "not found")

    @r.get("/sets")
    def sets(request: Request):
        return {"sets": svc(request).sets(), "exports": {k: v[0] for k, v in EXPORTS.items()}}

    @r.get("/starters")
    def starters_list(request: Request):
        """Ready-made test sets (Science Lab citation validity and refusal behaviour) and whether each is already added."""
        have = {x["id"] for x in svc(request).sets()}
        return {"starters": [dict(id=s["id"], name=s["name"], description=s["description"], cases=len(s["cases"]), added=s["id"] in have) for s in starters()]}

    @r.post("/starters/{sid}")
    def starters_add(sid: str, request: Request):
        """Add (or reset to the original) one ready-made test set. It is an ordinary set afterwards: edit it as you like."""
        found = next((s for s in starters() if s["id"] == sid), None)
        if found is None:
            raise HTTPException(404, "no such ready-made set")
        return wrap(svc(request).save_set, found)

    @r.get("/sets/{sid}")
    def get_set(sid: str, request: Request):
        return wrap(svc(request).get_set, sid)

    @r.put("/sets")
    def put_set(body: dict, request: Request):
        return wrap(svc(request).save_set, body)

    @r.delete("/sets/{sid}")
    def delete_set(sid: str, request: Request):
        wrap(svc(request).delete_set, sid)
        return {"ok": True}

    @r.get("/runs")
    def runs(request: Request, set_id: str | None = None):
        return {"runs": svc(request).runs(set_id)}

    @r.post("/runs")
    async def start(body: dict, request: Request):
        s = request.app.state.s
        ev = svc(request)
        rid = wrap(ev.begin, body.get("set_id") or "", body.get("agent") or "", body.get("provider") or "", body.get("model"), body.get("judge"))

        async def work(emit, inbox):
            await ev.execute(rid, body.get("model"), body.get("judge"))
        s.jobs.start(work)
        return ev.run(rid)

    @r.get("/runs/{rid}")
    def run(rid: str, request: Request):
        return wrap(svc(request).run, rid)

    @r.post("/runs/{rid}/cancel")
    def cancel(rid: str, request: Request):
        return wrap(svc(request).cancel, rid)

    @r.get("/export")
    def export(request: Request, kind: str, id: str, format: str):
        name, media, text = wrap(svc(request).export, kind, id, format)
        return PlainTextResponse(text, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    app.include_router(r)
