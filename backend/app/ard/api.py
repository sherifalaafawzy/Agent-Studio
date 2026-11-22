"""REST API for AI-BOM export and Agentic Resource Discovery publishing: /api/aibom/* and /api/discovery/*.

These routes live on the local API (127.0.0.1:8765) and are for the Agent Studio UI. What the world can read is served by the
separate discovery server (server.py), and only when the user has switched publishing on.
Pydantic models are module-level (main.py uses postponed annotations).
"""
from __future__ import annotations

import contextlib
import io
import zipfile
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel

from ..aibom import builder
from ..aibom.redact import LEVEL_INFO, LEVELS
from ..aibom.validate import validate_bom
from . import push as push_mod
from . import signing
from .server import DiscoveryServer
from .settings import DiscoverySettings, SettingsError
from .site import Publisher, SiteCache

README = """Agent Studio - Agentic Resource Discovery export (redaction level: {level})

Upload these files to the web root of https://{publisher}/ keeping the folder layout.
  .well-known/ard.json       the catalog consumers fetch (serve as Content-Type: application/json)
  .well-known/ai-catalog.json the same catalog under the name ARD's predecessor used (optional)
  .well-known/jwks.json      the public signing key (only when signing is on)
  ard/bom/...                CycloneDX 1.7 AI bills of materials
  ard/skills/...             skill files
  index.html                 a page with <link rel="ard"> so crawlers find the catalog
Serve the public level with the header  Access-Control-Allow-Origin: *  and HTTPS only.
Files of the internal and full levels contain more than a public BOM: do not host them where anyone can read them.
Digests in the catalog are sha256 of the files exactly as exported: do not reformat them.
"""


class SettingsPatch(BaseModel):
    enabled: bool | None = None
    publisher: str | None = None
    base_url: str | None = None
    level: str | None = None
    level_confirmed: bool | None = None
    sign: bool | None = None
    port: int | None = None
    publish: dict[str, bool] | None = None
    push_url: str | None = None
    push_project: str | None = None
    push_version: str | None = None
    push_level: str | None = None
    push_api_key: str | None = None            # write-only; "" clears


class PortIn(BaseModel):
    port: int | None = None


class ConfirmIn(BaseModel):
    confirm: str


def _level(level: str) -> str:
    if level not in LEVELS:
        raise HTTPException(422, f"unknown redaction level '{level}' (use one of: {', '.join(LEVELS)})")
    return level


def register(app: FastAPI) -> None:
    s = app.state.s
    settings = DiscoverySettings(s.db)
    publisher = Publisher(s, settings)
    cache = SiteCache(publisher)
    server = DiscoveryServer(cache, settings)
    app.state.discovery = SimpleNamespace(settings=settings, publisher=publisher, cache=cache, server=server)
    r = APIRouter(prefix="/api", tags=["discovery"])

    def bad(e: Exception, code: int = 422):
        raise HTTPException(code, str(e))

    # ---- AI-BOM ----
    @r.get("/aibom/levels")
    def levels():
        return [{"id": k, "description": LEVEL_INFO[k]} for k in LEVELS]

    @r.get("/aibom")
    def bom(level: str = "public", scope: str = "system", stable: bool = True):
        try:
            doc = builder.build_bom(s, _level(level), scope, stable, publisher=settings.get()["publisher"])
        except ValueError as e:
            bad(e)
        raw = builder.serialize(doc)
        return Response(raw, media_type="application/vnd.cyclonedx+json", headers={"X-AIBOM-SHA256": builder.sha256_hex(raw), "Cache-Control": "no-store",
                                                                                   "Access-Control-Expose-Headers": "X-AIBOM-SHA256, Content-Disposition"})   # the UI (another origin) reads the digest

    @r.get("/aibom/download")
    def bom_download(level: str = "public", scope: str = "system", stable: bool = True):
        resp = bom(level, scope, stable)
        name = "agent-studio-aibom-" + (scope if scope == "system" else builder.clean_id(scope.replace(":", "-"))) + f"-{level}.cdx.json"
        resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
        return resp

    @r.get("/aibom/scopes")
    def scopes(level: str = "public"):
        full = builder.build_full(s, _level(level), True)
        names = {x["bom-ref"]: x["name"] for x in [*full.get("services", []), *full["components"]]}
        return {k: [{"ref": ref, "name": names[ref]} for ref in refs] for k, refs in builder.scope_roots(full).items()}

    @r.get("/aibom/validate")
    def bom_validate(level: str = "public", scope: str = "system"):
        try:
            return validate_bom(builder.build_bom(s, _level(level), scope, True))
        except ValueError as e:
            bad(e)

    @r.post("/aibom/push/preview")
    def push_preview():
        try:
            return push_mod.plan(s, settings)[0]
        except SettingsError as e:
            bad(e)

    @r.post("/aibom/push")
    def push(body: ConfirmIn):
        try:
            return push_mod.send(s, settings, body.confirm)
        except SettingsError as e:
            bad(e, 409)
        except push_mod.PushError as e:
            bad(e, 502)

    # ---- settings ----
    @r.get("/discovery/settings")
    def get_settings():
        return settings.public()

    @r.put("/discovery/settings")
    def put_settings(body: SettingsPatch):
        try:
            out = settings.update(body.model_dump(exclude_unset=True))
        except SettingsError as e:
            bad(e)
        if server.running() and out["not_ready"]:
            server.stop()                                 # settings no longer allow publishing: stop serving at once
        return out

    @r.post("/discovery/token")
    def new_token():
        return {"token": settings.new_token(), "note": "Shown once. Consumers send it as 'Authorization: Bearer <token>'. Only its hash is kept."}

    @r.delete("/discovery/token")
    def clear_token():
        settings.clear_token()
        if server.running() and settings.readiness():
            server.stop()
        return {"ok": True}

    # ---- manifest preview / export ----
    @r.get("/discovery/manifest")
    def manifest():
        cfg = settings.get()
        try:
            site = cache.get(force=True)
        except SettingsError as e:
            return {"manifest": None, "entries": [], "validation": None, "reasons": [str(e)], "publishable": False}
        why = settings.readiness(cfg)
        return {"manifest": site.manifest, "entries": site.entries, "validation": publisher.validate(site), "reasons": why, "publishable": not why,
                "level": site.level, "publisher": site.publisher, "base_url": site.base_url, "signed": site.signed, "problems": site.problems,
                "files": [{"path": a.path, "kind": a.kind, "bytes": len(a.data), "sha256": a.sha256, "content_type": a.content_type} for a in site.files.values()]}

    @r.get("/discovery/manifest/download")
    def manifest_download():
        try:
            site = cache.get(force=True)
        except SettingsError as e:
            bad(e)
        a = site.files[".well-known/ard.json"]
        return Response(a.data, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="ard.json"'})

    @r.get("/discovery/export.zip")
    def export_zip():
        try:
            site = cache.get(force=True)
        except SettingsError as e:
            bad(e)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for a in site.files.values():
                z.writestr(a.path, a.data)
            z.writestr("README.txt", README.format(level=site.level, publisher=site.publisher))
        return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="agent-studio-ard-site.zip"'})

    # ---- signing key ----
    @r.get("/discovery/keys")
    def keys():
        return {"exists": cache.p.signer.exists(), "jwk": cache.p.signer.public_jwk(), "scheme": signing.SCHEME_ID,
                "note": "ARD defines no signing procedure of its own. A signature here proves only that the holder of this key signed the "
                        "entry's trust manifest; consumers must agree to this scheme and obtain the public key from a source they trust."}

    @r.post("/discovery/keys")
    def new_key():
        jwk = cache.p.signer.generate()
        with contextlib.suppress(SettingsError):        # the new key signs from the next build on
            cache.get(force=True)
        return {"exists": True, "jwk": jwk}

    @r.delete("/discovery/keys")
    def delete_key():
        cache.p.signer.delete()
        return {"exists": False}

    # ---- discovery server ----
    @r.get("/discovery/server")
    def server_status():
        return {**server.status(), "not_ready": settings.readiness()}

    @r.post("/discovery/server/start")
    def server_start(body: PortIn | None = None):
        try:
            return server.start(body.port if body else None)
        except SettingsError as e:
            bad(e, 409)

    @r.post("/discovery/server/stop")
    def server_stop():
        return server.stop()

    app.include_router(r)


def shutdown(app: FastAPI) -> None:
    d = getattr(app.state, "discovery", None)
    if d is not None:
        d.server.stop()

