"""REST API for artifacts: /api/artifacts/*. Pydantic-free on purpose (main.py uses postponed annotations)."""
from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import Response

from .service import ArtifactError
from .types import INLINE_IMAGES, INLINE_MEDIA

SANDBOX_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"


def content_headers(a: dict, download: bool) -> tuple[str, dict]:
    """(media type, headers) that make serving an agent-made file safe: nothing a browser could run is sent as runnable, and every response is sandboxed."""
    mime = a["mime"]
    h = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": SANDBOX_CSP, "Cache-Control": "private, max-age=0"}
    if mime in INLINE_IMAGES or mime in INLINE_MEDIA or mime in ("application/pdf", "image/svg+xml"):
        pass                                                         # shown or played by the browser inside the sandbox rules above
    elif mime == "text/vtt":
        mime = "text/vtt; charset=utf-8"                             # captions for a video: plain text a <track> can read
    elif a["preview"] in ("markdown", "text", "csv", "json"):
        mime = "text/plain; charset=utf-8"                           # html, xml, js... are shown as text, never run
    else:
        mime, download = "application/octet-stream", True
    if mime == "application/pdf":
        h["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; object-src 'self'"
    h["Content-Disposition"] = f"{'attachment' if download else 'inline'}; filename*=UTF-8''{quote(a['name'])}"
    return mime, h


def byte_range(header: str, size: int) -> tuple[int, int] | None:
    """The (first, last) byte of a single `Range: bytes=a-b` request, or None when it is absent, malformed, a multi-range or unsatisfiable (the whole file is sent)."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m or not (m[1] or m[2]) or size <= 0:
        return None
    if not m[1]:                                                     # the last N bytes
        n = int(m[2])
        return (max(0, size - n), size - 1) if n else None
    first = int(m[1])
    last = min(int(m[2]), size - 1) if m[2] else size - 1
    return (first, last) if first <= last and first < size else None


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

    def svc(request: Request):
        return request.app.state.s.artifacts

    def guard(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except ArtifactError as e:
            raise HTTPException(404 if "no such" in str(e) else 422, str(e))

    @r.get("/config")
    def get_config(request: Request):
        return svc(request).public_config()

    @r.put("/config")
    def put_config(body: dict, request: Request):
        return guard(svc(request).set_config, body)

    @r.post("/storages/{sid}/test")
    def test_storage(sid: str, request: Request):
        s = svc(request)
        if not any(x["id"] == sid for x in s.config()["storages"]):
            raise HTTPException(404, f"no storage called '{sid}'")
        return s.test_storage(sid)

    @r.get("/facets")
    def facets(request: Request):
        return svc(request).facets()

    @r.post("/purge")
    def purge(request: Request, days: int | None = None):
        return svc(request).purge(days)

    @r.get("")
    def list_artifacts(request: Request, q: str = "", agent: str = "", session_id: str = "", trace_id: str = "", type: str = "", storage: str = "", source: str = "",
                       since: float | None = None, until: float | None = None, min_size: int | None = None, max_size: int | None = None,
                       sort: str = "created", order: str = "desc", limit: int = 60, offset: int = 0):
        return svc(request).list(q=q[:200], agent=agent, session_id=session_id, trace_id=trace_id, type=type, storage=storage, source=source, since=since, until=until,
                                 min_size=min_size, max_size=max_size, sort=sort, order=order, limit=limit, offset=offset)

    @r.post("")
    def save(body: dict, request: Request):
        """Save text (for example an answer from the chat) as an artifact."""
        tags = body.get("tags") if isinstance(body.get("tags"), list) else []
        return guard(svc(request).save_text, str(body.get("name") or "").strip() or "note.md", str(body.get("content") or ""), agent=str(body.get("agent") or ""),
                     session_id=str(body.get("session_id") or ""), trace_id=str(body.get("trace_id") or ""), source="chat", tags=tags)

    @r.get("/{aid}")
    def get_one(aid: str, request: Request):
        return guard(svc(request).get, aid)

    @r.get("/{aid}/preview")
    def preview(aid: str, request: Request):
        return guard(svc(request).preview, aid)

    @r.get("/{aid}/content")
    def content(aid: str, request: Request, download: bool = False):
        s = svc(request)
        a = guard(s.get, aid)
        data, _ = guard(s.read_bytes, aid)
        mime, headers = content_headers(a, download)
        if a["mime"] in INLINE_MEDIA and not download:
            headers["Accept-Ranges"] = "bytes"
            rng = byte_range(request.headers.get("range", ""), len(data))
            if rng:
                headers["Content-Range"] = f"bytes {rng[0]}-{rng[1]}/{len(data)}"
                return Response(content=data[rng[0]:rng[1] + 1], status_code=206, media_type=mime, headers=headers)
        return Response(content=data, media_type=mime, headers=headers)

    @r.post("/{aid}/resync")
    def resync(aid: str, request: Request):
        return guard(svc(request).resync, aid)

    @r.delete("/{aid}")
    def delete(aid: str, request: Request):
        return guard(svc(request).delete, aid)

    app.include_router(r)
