"""REST API for the Catalog page: domain skills, public MCP servers, and the cloud-ML account settings.

  GET  /api/catalog/domains                 the eight domains
  GET  /api/catalog/skills                  skills in a domain group, with which agents use them
  GET  /api/catalog/mcp                     vetted public MCP servers (+ installed / ready)
  POST /api/catalog/mcp/{id}/install        add one (credentials are write-only; writes need a confirm)
  PUT  /api/catalog/mcp/{id}/mode           switch an installed server between read-only and writes allowed
  GET  /api/catalog/cloud                   cloud ML providers: which credentials are set (never their values)
  PUT  /api/catalog/cloud                   set / clear credentials and the 'allow create/delete' switch
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import cloudml, mcp_catalog as MC

GROUP_DOMAIN = {"Accounting": "accounting", "Sales": "sales", "Marketing": "marketing", "Manufacturing": "manufacturing", "Engineering": "engineering",
                "Data": "data", "Machine Learning": "machine-learning", "Deep Learning": "deep-learning"}
CLOUD_MCP = "cloudml"
ALLOW_KEY = "CLOUDML_ALLOW_WRITE"


class InstallIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    allow_writes: bool = False


class ModeIn(BaseModel):
    allow_writes: bool


class CloudIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)      # "" clears a value
    allow_writes: bool | None = None


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/catalog", tags=["catalog"])

    def S(request: Request):
        return request.app.state.s

    @r.get("/domains")
    def domains():
        return MC.DOMAINS

    @r.get("/skills")
    def skills(s=Depends(S)):
        agents = s.agents.list()
        out = []
        for k in s.skills.list():
            dom = GROUP_DOMAIN.get(k.group)
            if dom is None:
                continue
            d = k.public()
            d.update(domain=dom, used_by=[dict(id=a.id, name=a.name) for a in agents if k.name in a.skills or f"group:{k.group}".lower() in [x.lower() for x in a.skills] or "*" in a.skills])
            out.append(d)
        return out

    @r.get("/mcp")
    def mcp_list(s=Depends(S)):
        inst = MC.installed(s.mcp)
        out = []
        for e in MC.CATALOG:
            srv = s.mcp.get(e["id"]) if e["id"] in inst else None
            out.append(MC.public(e, inst.get(e["id"]), bool(srv and srv.ready())))
        return out

    @r.post("/mcp/{cid}/install")
    def mcp_install(cid: str, body: InstallIn, s=Depends(S)):
        try:
            return MC.install(s.mcp, cid, body.values, body.allow_writes)
        except KeyError:
            raise HTTPException(404, "not in the catalog")
        except ValueError as e:
            raise HTTPException(422, str(e))

    @r.put("/mcp/{cid}/mode")
    def mcp_mode(cid: str, body: ModeIn, s=Depends(S)):
        try:
            return MC.set_mode(s.mcp, cid, body.allow_writes)
        except KeyError:
            raise HTTPException(404, "that server was not added from the catalog")
        except ValueError as e:
            raise HTTPException(422, str(e))

    def _cloud_env(s) -> dict:
        srv = s.mcp.get(CLOUD_MCP)
        return dict(srv.env) if srv else {}

    @r.get("/cloud")
    def cloud_get(s=Depends(S)):
        env = _cloud_env(s)
        return dict(providers=cloudml.provider_info(env), allow_writes=env.get(ALLOW_KEY) == "1", enabled=bool(s.mcp.get(CLOUD_MCP) and s.mcp.get(CLOUD_MCP).enabled))

    @r.put("/cloud")
    def cloud_put(body: CloudIn, s=Depends(S)):
        known = {f["name"] for p in cloudml.PROVIDERS.values() for f in p.fields}
        bad = sorted(set(body.values) - known)
        if bad:
            raise HTTPException(422, f"unknown setting(s): {', '.join(bad)}")
        if any("\n" in v or "\r" in v for v in body.values.values()):
            raise HTTPException(422, "values must be a single line")
        env = {k: v.strip() for k, v in body.values.items()}
        if body.allow_writes is not None:
            env[ALLOW_KEY] = "1" if body.allow_writes else ""
        s.mcp.update(CLOUD_MCP, env=env)
        return cloud_get(s)

    app.include_router(r)
