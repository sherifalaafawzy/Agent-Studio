"""REST API for agent identity: /api/identity/*. Pydantic-free on purpose (main.py uses postponed annotations)."""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request

from .service import IdentityError


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/identity", tags=["identity"])

    def svc(request: Request):
        return request.app.state.s.identity

    @r.get("/config")
    def get_config(request: Request):
        return svc(request).public_config()

    @r.put("/config")
    def put_config(body: dict, request: Request):
        try:
            return svc(request).set_config(body)
        except IdentityError as e:
            raise HTTPException(422, str(e))

    @r.get("/agents")
    def agents(request: Request):
        s = request.app.state.s
        return {"agents": [{"id": a.id, "name": a.name, **s.identity.describe(a)} for a in s.agents.list()]}

    @r.post("/agents/{aid}/token")
    async def token(aid: str, body: dict, request: Request):
        try:
            return await svc(request).token_for(aid, body.get("audience") or "", body.get("scopes"), body.get("ttl_s"), body.get("source") or "local")
        except KeyError:
            raise HTTPException(404, "unknown agent")
        except IdentityError as e:
            raise HTTPException(422, str(e))

    @r.post("/delegations/check")
    def check(body: dict, request: Request):
        try:
            return svc(request).check_delegation(body.get("parent") or "", body.get("child") or "", body.get("scopes"))
        except KeyError as e:
            raise HTTPException(404, f"unknown agent {e}")

    @r.post("/token")
    def exchange(body: dict, request: Request):
        """RFC 8693 token exchange against the built-in issuer (form fields as JSON)."""
        if body.get("grant_type") != "urn:ietf:params:oauth:grant-type:token-exchange":
            raise HTTPException(400, {"error": "unsupported_grant_type"})
        if not body.get("subject_token") or not body.get("actor_token"):
            raise HTTPException(400, {"error": "invalid_request", "error_description": "subject_token and actor_token are required"})
        scope = body.get("scope")
        try:
            token, claims = svc(request).exchange_local(body["subject_token"], body["actor_token"], body.get("audience") or "",
                                                        scope.split() if isinstance(scope, str) else None)
        except IdentityError as e:
            raise HTTPException(400, {"error": "invalid_target" if "audience" in str(e) else "invalid_request", "error_description": str(e)})
        return {"access_token": token, "issued_token_type": "urn:ietf:params:oauth:token-type:access_token", "token_type": "Bearer",
                "expires_in": claims["exp"] - claims["iat"], "scope": claims["scope"]}

    @r.post("/verify")
    def verify(body: dict, request: Request):
        try:
            return {"valid": True, "claims": svc(request).verify_local(body.get("token") or "", body.get("audience"))}
        except IdentityError as e:
            return {"valid": False, "error": str(e)}

    @r.get("/jwks.json")
    def jwks(request: Request):
        return svc(request).issuer.jwks()

    @r.post("/rotate-key")
    def rotate(request: Request):
        svc(request).issuer.rotate()
        svc(request).record("rotated", "issuer", detail="signing key rotated: tokens issued before this no longer verify")
        return {"ok": True}

    @r.get("/events")
    def events(request: Request, limit: int = 100, event: str | None = None):
        return {"events": svc(request).events(limit, event)}

    @r.get("/stats")
    def stats(request: Request, days: int = 7):
        return svc(request).stats(max(1, min(days, 365)))

    app.include_router(r)
