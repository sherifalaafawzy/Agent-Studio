"""External identity providers. The local issuer is the default; these let an agent obtain tokens from your own identity system.

What each really does (so the UI does not over-promise):
* oidc / okta / keycloak - OAuth 2.0 client-credentials grant (RFC 6749 4.4) for the token, and RFC 8693 token exchange for
  delegation (Okta needs the Token Exchange grant enabled on its custom authorization server; Keycloak 26.2+ standard token exchange).
* entra - client credentials with scope `<audience>/.default`; for delegation Entra does NOT use RFC 8693: it uses the JWT-bearer
  "on-behalf-of" grant, which only works for tokens of signed-in users, not for application-only tokens.
* spire - asks the local SPIRE agent for a JWT-SVID (`spire-agent api fetch jwt`); SPIFFE JWT-SVIDs cannot be exchanged.

All of them authenticate Agent Studio (the workload) to the identity system. Telling agents apart inside Agent Studio stays with
the local audit log and the local delegation tokens, because one OAuth client cannot mint a separate subject per agent."""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

import httpx

TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"


class ProviderError(Exception):
    """The identity provider could not help. The message is safe to show."""


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    type: str = "text"                # text | secret
    required: bool = False
    placeholder: str = ""
    help: str = ""


@dataclass(frozen=True)
class ProviderKind:
    id: str
    label: str
    summary: str
    fields: tuple[Field, ...]
    exchange: str                     # what delegation does here: "rfc8693" | "obo" | "none"
    secrets: tuple[str, ...] = ()


def _https(u: str, what: str) -> str:
    u = (u or "").strip().rstrip("/")
    if not u.startswith("https://") and not u.startswith("http://localhost") and not u.startswith("http://127.0.0.1"):
        raise ProviderError(f"{what} must start with https:// (http:// is allowed only for localhost)")
    return u


def token_endpoint(kind: str, p: dict) -> str:
    if kind == "oidc":
        return _https(p.get("token_endpoint", ""), "the token endpoint")
    if kind == "okta":
        domain, server = (p.get("domain") or "").strip(), (p.get("auth_server") or "default").strip()
        if not domain or "/" in domain:
            raise ProviderError("the Okta domain looks wrong (for example dev-123456.okta.com)")
        return f"https://{domain}/oauth2/{server}/v1/token"
    if kind == "entra":
        tenant = (p.get("tenant") or "").strip()
        if not tenant or "/" in tenant:
            raise ProviderError("the Entra tenant id (or domain) is required")
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    if kind == "keycloak":
        base, realm = (p.get("base_url") or "").strip().rstrip("/"), (p.get("realm") or "").strip()
        if not realm or "/" in realm:
            raise ProviderError("the Keycloak realm is required")
        return f"{_https(base, 'the Keycloak address')}/realms/{realm}/protocol/openid-connect/token"
    raise ProviderError("unknown identity provider")


KINDS: dict[str, ProviderKind] = {k.id: k for k in (
    ProviderKind("oidc", "Any OpenID Connect / OAuth server", "Generic OAuth 2.0 server with a token endpoint.", (
        Field("token_endpoint", "Token endpoint", required=True, placeholder="https://idp.example.com/oauth2/token"),
        Field("client_id", "Client id", required=True), Field("client_secret", "Client secret", "secret", required=True),
        Field("audience_param", "Send audience as", placeholder="audience", help="Some servers (Auth0) read the audience from this form field. Empty = not sent."),
    ), "rfc8693", ("client_secret",)),
    ProviderKind("okta", "Okta", "Okta custom authorization server (service app).", (
        Field("domain", "Okta domain", required=True, placeholder="dev-123456.okta.com"), Field("auth_server", "Authorization server id", placeholder="default"),
        Field("client_id", "Client id", required=True), Field("client_secret", "Client secret", "secret", required=True),
    ), "rfc8693", ("client_secret",)),
    ProviderKind("entra", "Microsoft Entra ID", "Entra app registration (client credentials).", (
        Field("tenant", "Tenant id", required=True), Field("client_id", "Application (client) id", required=True),
        Field("client_secret", "Client secret", "secret", required=True),
    ), "obo", ("client_secret",)),
    ProviderKind("keycloak", "Keycloak", "Keycloak realm (confidential client).", (
        Field("base_url", "Server address", required=True, placeholder="https://keycloak.example.com"), Field("realm", "Realm", required=True),
        Field("client_id", "Client id", required=True), Field("client_secret", "Client secret", "secret", required=True),
    ), "rfc8693", ("client_secret",)),
    ProviderKind("spire", "SPIFFE / SPIRE", "Ask the local SPIRE agent for a JWT-SVID.", (
        Field("socket_path", "Agent socket", required=True, placeholder="/tmp/spire-agent/public/api.sock", help="Path of the SPIRE agent's Workload API socket."),
        Field("spiffe_id", "SPIFFE ID to request (optional)", placeholder="spiffe://example.org/agent-studio"),
        Field("binary", "spire-agent program", placeholder="spire-agent"),
    ), "none"),
)}


def jwt_claims(token: str) -> dict | None:
    """The payload of a JWT, WITHOUT verifying it (for showing what was issued). None if it is not a JWT."""
    try:
        body = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data if isinstance(data, dict) else None
    except (IndexError, ValueError, AttributeError):
        return None


class OAuthProvider:
    def __init__(self, kind: str, params: dict, secrets: dict, http: Callable[[], Any]):
        self.kind, self.p, self.secrets, self.http = kind, params, secrets, http
        self.endpoint = token_endpoint(kind, params)
        if not params.get("client_id") or not secrets.get("client_secret"):
            raise ProviderError("the client id and client secret are required")

    async def _post(self, form: dict) -> dict:
        data = {**form, "client_id": self.p["client_id"], "client_secret": self.secrets["client_secret"]}
        try:
            async with self.http() as c:
                r = await c.post(self.endpoint, data=data, headers={"Accept": "application/json"}, timeout=20)
        except httpx.HTTPError as e:
            raise ProviderError(f"cannot reach the identity provider: {type(e).__name__}") from e
        if r.status_code >= 400:
            try:
                err = r.json()
                why = err.get("error_description") or err.get("error") or ""
            except ValueError:
                why = ""
            raise ProviderError(f"the identity provider refused ({r.status_code}): {str(why)[:200] or 'no reason given'}")
        try:
            d = r.json()
            return {"access_token": d["access_token"], "expires_in": int(d.get("expires_in") or 0), "scope": d.get("scope", ""),
                    "token_type": d.get("token_type", "Bearer")}
        except (ValueError, KeyError, TypeError) as e:
            raise ProviderError("the identity provider's reply had no access token") from e

    async def token(self, audience: str, scopes: list[str]) -> dict:
        form: dict = {"grant_type": "client_credentials"}
        if self.kind == "entra":
            form["scope"] = f"{audience}/.default"
        elif scopes:
            form["scope"] = " ".join(scopes)
        if self.kind == "oidc" and self.p.get("audience_param"):
            form[self.p["audience_param"]] = audience
        return await self._post(form)

    async def exchange(self, subject_token: str, actor_token: str | None, audience: str, scopes: list[str]) -> dict:
        if self.kind == "entra":
            return await self._post({"grant_type": JWT_BEARER, "assertion": subject_token, "requested_token_use": "on_behalf_of",
                                     "scope": f"{audience}/.default"})
        form = {"grant_type": TOKEN_EXCHANGE, "subject_token": subject_token, "subject_token_type": ACCESS_TOKEN,
                "requested_token_type": ACCESS_TOKEN, "audience": audience}
        if actor_token:
            form.update(actor_token=actor_token, actor_token_type=ACCESS_TOKEN)
        if scopes:
            form["scope"] = " ".join(scopes)
        return await self._post(form)


class SpireProvider:
    kind = "spire"

    def __init__(self, params: dict, runner: Callable[[list[str]], str] | None = None):
        self.p = params
        if not (params.get("socket_path") or "").strip():
            raise ProviderError("the SPIRE agent socket path is required")
        self.runner = runner or _run

    async def token(self, audience: str, scopes: list[str]) -> dict:
        cmd = [self.p.get("binary") or "spire-agent", "api", "fetch", "jwt", "-audience", audience, "-socketPath", self.p["socket_path"], "-output", "json"]
        if self.p.get("spiffe_id"):
            cmd += ["-spiffeID", self.p["spiffe_id"]]
        out = await asyncio.to_thread(self.runner, cmd)
        try:
            doc = json.loads(out)
            svids = next(x["svids"] for x in (doc if isinstance(doc, list) else [doc]) if isinstance(x, dict) and x.get("svids"))
            tok = svids[0]["svid"]
        except (ValueError, StopIteration, KeyError, IndexError, TypeError) as e:
            raise ProviderError("SPIRE's answer did not contain a JWT-SVID") from e
        c = jwt_claims(tok) or {}
        return {"access_token": tok, "expires_in": max(0, int(c.get("exp", 0) - c.get("iat", 0))) if c.get("exp") else 0, "scope": "", "token_type": "JWT-SVID"}

    async def exchange(self, *a, **k) -> dict:
        raise ProviderError("a SPIFFE JWT-SVID cannot be exchanged: ask SPIRE for one for the audience you need")


def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except FileNotFoundError as e:
        raise ProviderError(f"'{cmd[0]}' was not found on this PC (install SPIRE or give the full path)") from e
    except subprocess.TimeoutExpired as e:
        raise ProviderError("the SPIRE agent did not answer within 20 seconds") from e
    if r.returncode != 0:
        raise ProviderError("the SPIRE agent refused: " + (r.stderr.strip().splitlines() or ["no reason given"])[-1][:200])
    return r.stdout


def make_provider(kind: str, params: dict, secrets: dict, http: Callable[[], Any], runner=None):
    if kind == "spire":
        return SpireProvider(params, runner)
    if kind not in KINDS:
        raise ProviderError("unknown identity provider")
    return OAuthProvider(kind, params, secrets, http)
