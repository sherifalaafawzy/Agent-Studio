"""Agent identity: SPIFFE-style ids, the built-in token issuer, RFC 8693 delegation, external identity providers, audit and API.
Fakes only: no identity provider, no SPIRE agent."""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import time

import httpx
import pytest

from app.agents import AgentDef
from app.identity import providers as P
from app.identity.issuer import LocalIssuer, TokenError
from app.identity.model import clean_identity
from app.identity.service import IdentityError, derive_scopes, fingerprint

ISS = "http://127.0.0.1:8765"


def run(c):
    return asyncio.run(c)


@pytest.fixture
def idn(state):
    return state.identity


def agent(aid="writer", **kw):
    return AgentDef(id=aid, name=aid.title(), tools=kw.pop("tools", ["read_file", "write_file"]), **kw)


# ----------------------------------------------------------------------------------------------------- model
def test_clean_identity():
    assert clean_identity(None) == {} and clean_identity({}) == {}
    assert clean_identity({"scopes": ["files:read", "files:read"]}) == {"scopes": ["files:read"]}          # a missing key stays automatic
    assert clean_identity({"scopes": [], "delegates": ["helper"]}) == {"scopes": [], "delegates": ["helper"]}
    for bad in ("x", {"nope": 1}, {"scopes": "files"}, {"scopes": ["BAD"]}, {"delegates": ["Bad Id"]}, {"scopes": [f"s{i}" for i in range(31)]}):
        with pytest.raises(ValueError, match="identity"):
            clean_identity(bad)


# ---------------------------------------------------------------------------------------------------- issuer
def test_issuer_signs_and_verifies(tmp_path):
    from app.ard.signing import Signer
    iss = LocalIssuer(Signer(tmp_path / "k.pem"))
    now = time.time()
    claims = {"iss": ISS, "sub": "s", "aud": ["a", "b"], "exp": now + 60, "nbf": now - 1}
    tok = iss.sign(claims)
    assert iss.verify(tok, ISS, "a")["sub"] == "s" and iss.verify(tok, ISS)["aud"] == ["a", "b"]
    assert iss.jwks()["keys"][0]["kty"] == "OKP"
    for bad, msg in ((iss.sign({**claims, "iss": "other"}), "someone else"), (iss.sign({**claims, "exp": now - 5}), "expired"),
                     (iss.sign({**claims, "exp": "x"}), "expired"), (iss.sign({**claims, "nbf": now + 999}), "not valid yet")):
        with pytest.raises(TokenError, match=msg):
            iss.verify(bad, ISS)
    with pytest.raises(TokenError, match="different audience"):
        iss.verify(tok, ISS, "c")
    iss.verify(iss.sign({**claims, "aud": "a"}), ISS, "a")
    h, p, s = tok.split(".")
    forged = base64.urlsafe_b64encode(json.dumps({**claims, "sub": "evil"}).encode()).rstrip(b"=").decode()
    with pytest.raises(TokenError, match="signature does not match"):
        iss.verify(f"{h}.{forged}.{s}", ISS)
    with pytest.raises(TokenError, match="signature does not match"):
        iss.verify(f"{h}.{p}.!!!", ISS)
    for junk in ("nope", "a.b.c", ".."):
        with pytest.raises(TokenError, match="not a token this app issued"):
            iss.verify(junk, ISS)
    hdr = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    with pytest.raises(TokenError, match="not a token this app issued"):
        iss.verify(f"{hdr}.{p}.", ISS)
    iss.rotate()
    with pytest.raises(TokenError, match="signature does not match"):
        iss.verify(tok, ISS)


# -------------------------------------------------------------------------------------------------- providers
def oauth(kind, handler, **params):
    base = {"oidc": dict(token_endpoint="https://idp.example.com/token", audience_param="audience"),
            "okta": dict(domain="dev-1.okta.com", auth_server="aus1"), "entra": dict(tenant="contoso"),
            "keycloak": dict(base_url="https://kc.example.com", realm="r")}[kind]
    return P.make_provider(kind, {**base, "client_id": "cid", **params}, {"client_secret": "sec"}, lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def ok(req):
    return httpx.Response(200, json={"access_token": "tok", "expires_in": 60, "scope": "a b"})


def test_token_endpoints_per_provider():
    assert P.token_endpoint("okta", {"domain": "d.okta.com"}) == "https://d.okta.com/oauth2/default/v1/token"
    assert P.token_endpoint("entra", {"tenant": "t"}) == "https://login.microsoftonline.com/t/oauth2/v2.0/token"
    assert P.token_endpoint("keycloak", {"base_url": "https://k.example.com/", "realm": "r"}) == "https://k.example.com/realms/r/protocol/openid-connect/token"
    assert P.token_endpoint("oidc", {"token_endpoint": "http://localhost:9/t"}) == "http://localhost:9/t"
    for kind, params, msg in (("oidc", {"token_endpoint": "http://evil.example.com/t"}, "https://"), ("okta", {"domain": "a/b"}, "Okta domain"),
                              ("entra", {}, "tenant"), ("keycloak", {"base_url": "https://k", "realm": ""}, "realm"), ("nope", {}, "unknown")):
        with pytest.raises(P.ProviderError, match=msg):
            P.token_endpoint(kind, params)


def test_client_credentials_per_provider():
    seen = []

    def handler(req):
        seen.append((str(req.url), dict(x.split("=") for x in req.content.decode().split("&"))))
        return ok(req)
    r = run(oauth("oidc", handler).token("https://api.example.com", ["files:read", "mcp:call"]))
    assert r == {"access_token": "tok", "expires_in": 60, "scope": "a b", "token_type": "Bearer"}
    form = seen[-1][1]
    assert form["grant_type"] == "client_credentials" and form["audience"] == "https%3A%2F%2Fapi.example.com" and form["scope"] == "files%3Aread+mcp%3Acall"
    assert form["client_secret"] == "sec"
    run(oauth("entra", handler).token("api://resource", ["x"]))
    assert seen[-1][1]["scope"] == "api%3A%2F%2Fresource%2F.default"
    run(oauth("okta", handler).token("aud", []))
    assert "scope" not in seen[-1][1] and seen[-1][0].endswith("/oauth2/aus1/v1/token")
    run(oauth("keycloak", handler).token("aud", ["s"]))


def test_exchange_rfc8693_and_entra_on_behalf_of():
    seen = []

    def handler(req):
        seen.append(dict(x.split("=") for x in req.content.decode().split("&")))
        return ok(req)
    run(oauth("keycloak", handler).exchange("subj", "actor", "target", ["files:read"]))
    f = seen[-1]
    assert f["grant_type"].startswith("urn%3Aietf") and f["subject_token"] == "subj" and f["actor_token"] == "actor" and f["audience"] == "target" and f["scope"] == "files%3Aread"
    run(oauth("okta", handler).exchange("subj", None, "target", []))
    assert "actor_token" not in seen[-1] and "scope" not in seen[-1]
    run(oauth("entra", handler).exchange("subj", "actor", "api://r", []))
    assert "jwt-bearer" in seen[-1]["grant_type"] and seen[-1]["requested_token_use"] == "on_behalf_of" and seen[-1]["assertion"] == "subj"


def test_provider_errors_are_explained():
    def status(code, body=None, content=None):
        return lambda r: httpx.Response(code, json=body) if content is None else httpx.Response(code, content=content)
    for handler, msg in ((status(401, {"error": "invalid_client", "error_description": "bad secret"}), "refused \\(401\\): bad secret"),
                         (status(400, {"error": "invalid_grant"}), "invalid_grant"), (status(500, content=b"oops"), "no reason given"),
                         (status(200, {"nope": 1}), "no access token"), (status(200, content=b"oops"), "no access token")):
        with pytest.raises(P.ProviderError, match=msg):
            run(oauth("oidc", handler).token("a", []))

    def boom(req):
        raise httpx.ConnectError("x")
    with pytest.raises(P.ProviderError, match="cannot reach the identity provider: ConnectError"):
        run(oauth("oidc", boom).token("a", []))
    with pytest.raises(P.ProviderError, match="client id and client secret"):
        P.make_provider("oidc", {"token_endpoint": "https://x/t"}, {}, httpx.AsyncClient)
    with pytest.raises(P.ProviderError, match="unknown"):
        P.make_provider("zzz", {}, {}, httpx.AsyncClient)


def test_spire_provider(monkeypatch):
    payload = base64.urlsafe_b64encode(json.dumps({"iat": 100, "exp": 400, "sub": "spiffe://x/y"}).encode()).rstrip(b"=").decode()
    svid = f"h.{payload}.s"
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return json.dumps([{"svids": [{"spiffe_id": "spiffe://x/y", "svid": svid}]}, {"bundles": {}}])
    sp = P.make_provider("spire", {"socket_path": "/s.sock", "spiffe_id": "spiffe://x/y", "binary": "spire-agent"}, {}, None, runner)
    r = run(sp.token("aud1", []))
    assert r["access_token"] == svid and r["expires_in"] == 300 and r["token_type"] == "JWT-SVID"
    assert calls[0][:4] == ["spire-agent", "api", "fetch", "jwt"] and "-spiffeID" in calls[0] and calls[0][calls[0].index("-audience") + 1] == "aud1"
    nojwt = P.make_provider("spire", {"socket_path": "/s"}, {}, None, lambda c: json.dumps({"svids": [{"svid": "opaque"}]}))
    assert run(nojwt.token("a", []))["expires_in"] == 0
    for out in ("not json", "[]", json.dumps([{"svids": [{}]}])):
        with pytest.raises(P.ProviderError, match="did not contain a JWT-SVID"):
            run(P.make_provider("spire", {"socket_path": "/s"}, {}, None, lambda c, o=out: o).token("a", []))
    with pytest.raises(P.ProviderError, match="cannot be exchanged"):
        run(sp.exchange("a", "b", "c", []))
    with pytest.raises(P.ProviderError, match="socket path is required"):
        P.make_provider("spire", {}, {}, None)


def test_spire_runner_subprocess(monkeypatch):
    class R:
        def __init__(self, rc=0, out="o", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert P._run(["spire-agent"]) == "o"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R(1, err="line1\nrefused: no identity"))
    with pytest.raises(P.ProviderError, match="refused: no identity"):
        P._run(["spire-agent"])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R(1, err=""))
    with pytest.raises(P.ProviderError, match="no reason given"):
        P._run(["spire-agent"])

    def missing(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(P.ProviderError, match="was not found"):
        P._run(["spire-agent"])

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("x", 20)
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(P.ProviderError, match="did not answer"):
        P._run(["spire-agent"])


def test_jwt_claims_helper():
    body = base64.urlsafe_b64encode(json.dumps({"a": 1}).encode()).rstrip(b"=").decode()
    assert P.jwt_claims(f"h.{body}.s") == {"a": 1}
    assert P.jwt_claims("opaque") is None and P.jwt_claims("h.!!.s") is None and P.jwt_claims(None) is None
    assert P.jwt_claims("h." + base64.urlsafe_b64encode(b"[1]").decode() + ".s") is None


# --------------------------------------------------------------------------------------------------- service
def test_scopes_derive_from_tools_and_can_be_set(idn):
    a = AgentDef(id="x1", name="X", tools=["read_file", "edit_file", "run_shell", "memory_search", "memory_save"], mcp=["m"], sub_agents=["y1"])
    assert derive_scopes(a) == ["files:read", "files:write", "shell:run", "memory:read", "memory:write", "mcp:call", "agents:delegate"]
    assert derive_scopes(AgentDef(id="x2", name="X")) == []
    a.identity = {"scopes": ["files:read"], "delegates": []}
    assert idn.allowed_scopes(a) == ["files:read"] and idn.delegates_of(a) == []
    d = idn.describe(a)
    assert d["spiffe_id"] == "spiffe://agent-studio.local/agent/x1" and d["scopes_automatic"] is False and d["delegates_automatic"] is False
    assert idn.describe(AgentDef(id="x3", name="X", sub_agents=["q"]))["delegates"] == ["q"]
    assert idn.user_id() == "spiffe://agent-studio.local/user/local"


def test_set_config_validation_and_secrets(idn):
    for patch, msg in (({"trust_domain": "Bad Domain"}, "trust domain"), ({"trust_domain": 5}, "trust domain"), ({"issuer": "ftp://x"}, "issuer"),
                       ({"ttl_s": 5}, "between 30"), ({"ttl_s": True}, "between 30"), ({"enforce": "yes"}, "true or false"), ({"provider": "x"}, "provider must"),
                       ({"provider": {"kind": 5}}, "provider must"), ({"provider": {"kind": "zzz"}}, "provider kind"),
                       ({"provider": {"kind": "local", "secrets": {"client_secret": "x"}}}, "not a credential"),
                       ({"provider": {"kind": "okta", "params": {"domain": "d.okta.com", "client_id": "c"}}}, "client secret are required"),
                       ({"provider": {"kind": "okta", "params": {"domain": "d.okta.com", "client_id": "c"}, "secrets": {"nope": "x"}}}, "not a credential")):
        with pytest.raises(IdentityError, match=msg):
            idn.set_config(patch)
    c = idn.set_config({"trust_domain": "example.org", "issuer": "https://studio.example.org/", "ttl_s": 600, "enforce": True})
    assert c["trust_domain"] == "example.org" and c["issuer"] == "https://studio.example.org" and c["ttl_s"] == 600 and c["enforce"] is True
    c = idn.set_config({"provider": {"kind": "okta", "params": {"domain": "d.okta.com", "client_id": "c"}, "secrets": {"client_secret": "S3CRET"}}})
    assert c["active"] == "okta" and c["provider"]["secrets_set"] == ["client_secret"] and "S3CRET" not in json.dumps(c)
    assert {p["id"] for p in c["providers"]} == {"local", "oidc", "okta", "entra", "keycloak", "spire"}
    idn.set_config({"provider": {"kind": "okta", "params": {"domain": "d.okta.com", "client_id": "c"}, "secrets": {"client_secret": None}}})     # unchanged
    assert idn.public_config()["provider"]["secrets_set"] == ["client_secret"]
    with pytest.raises(IdentityError, match="client id and client secret"):
        idn.set_config({"provider": {"kind": "okta", "params": {"domain": "d.okta.com", "client_id": "c"}, "secrets": {"client_secret": ""}}})
    c = idn.set_config({"provider": {"kind": "local"}})
    assert c["active"] == "local" and c["provider"]["secrets_set"] == []


def test_broken_stored_config_falls_back(idn, state):
    state.db.set_setting("identity_config", "nope")
    assert idn.config()["trust_domain"] == "agent-studio.local"
    state.db.set_setting("identity_config", json.dumps({"provider": "weird", "ttl_s": 99, "junk": 1}))
    assert idn.config()["provider"] == {"kind": "local", "params": {}} and "junk" not in idn.config()
    state.db.set_setting("identity_config", "[1]")
    assert idn.config()["ttl_s"] == 300
    state.db.set_setting("identity_secrets", "nope")
    assert idn._secrets() == {}
    state.db.set_setting("identity_secrets", "[1]")
    assert idn._secrets() == {}


def test_mint_checks_scopes_audience_and_lifetime(idn):
    a = agent(identity={"scopes": ["files:read"]})
    tok, claims = idn.mint_local(a, "https://api.example.com", None, 120)
    assert claims["sub"] == "spiffe://agent-studio.local/agent/writer" and claims["scope"] == "files:read" and claims["exp"] - claims["iat"] == 120
    assert idn.verify_local(tok, "https://api.example.com")["jti"] == claims["jti"]
    with pytest.raises(IdentityError, match="audience"):
        idn.verify_local(tok, "other")
    with pytest.raises(IdentityError, match="not a token"):
        idn.verify_local("zzz")
    with pytest.raises(IdentityError, match="may not hold: files:write"):
        idn.mint_local(a, "https://api.example.com", ["files:read", "files:write"])
    for bad in ("", "has space", None):
        with pytest.raises(IdentityError, match="name the audience"):
            idn.mint_local(a, bad)
    for ttl in (10, 99999, True, "x"):
        with pytest.raises(IdentityError, match="between 30"):
            idn.mint_local(a, "aud", None, ttl)
    ev = idn.events()
    assert [e["event"] for e in ev][:2] == ["refused", "issued"] or "issued" in {e["event"] for e in ev}
    assert tok not in json.dumps(ev)                                                   # tokens are never logged
    assert fingerprint(tok) == fingerprint(tok) and len(fingerprint(tok)) == 12


def test_delegation_narrows_scopes_and_nests_the_chain(idn):
    boss = agent("boss", identity={"scopes": ["files:read", "files:write", "shell:run"]})
    helper = agent("helper", identity={"scopes": ["files:read", "files:write", "web:fetch"]})
    third = agent("third", identity={"scopes": ["files:read"]})
    bt, _ = idn.mint_local(boss, "agent-studio")
    ht, _ = idn.mint_local(helper, "agent-studio")
    dt, d = idn.exchange_local(bt, ht, "agent:helper")
    assert d["sub"] == idn.spiffe_id("boss") and d["act"] == {"sub": idn.spiffe_id("helper")} and sorted(d["scope"].split()) == ["files:read", "files:write"]
    assert d["exp"] <= idn.verify_local(bt)["exp"]
    # a second hop: the delegated token is the subject, the next agent the actor -> the act claim nests
    tt, _ = idn.mint_local(third, "agent-studio")
    _, d2 = idn.exchange_local(dt, tt, "agent:third", ["files:read"])
    assert d2["act"] == {"sub": idn.spiffe_id("third"), "act": {"sub": idn.spiffe_id("helper")}} and d2["scope"] == "files:read"
    with pytest.raises(IdentityError, match="may not receive: files:write"):
        idn.exchange_local(dt, tt, "agent:third", ["files:write"])
    with pytest.raises(IdentityError, match="no permission would be left"):
        idn.exchange_local(bt, idn.mint_local(agent("other", identity={"scopes": ["web:fetch"]}), "agent-studio")[0], "agent:other")
    with pytest.raises(IdentityError, match="cannot exchange: this is not a token"):
        idn.exchange_local("junk", ht, "agent:x")
    # the chain is limited
    t, tok = dt, tt
    with pytest.raises(IdentityError, match="at most 3 agents"):
        for i in range(5):
            t, _ = idn.exchange_local(t, tok, f"agent:n{i}")
    assert {e["event"] for e in idn.events(200)} >= {"exchanged", "refused"}
    assert idn.stats()["exchanged"] >= 2 and idn.stats()["refused"] >= 3
    assert [e["event"] for e in idn.events(5, "refused")] == ["refused"] * len(idn.events(5, "refused"))


def test_runtime_delegation_is_logged_and_only_enforced_when_asked(idn):
    parent = agent("parent", tools=["read_file"], sub_agents=["kid"])
    kid = agent("kid", tools=["read_file"])
    stranger = agent("stranger", tools=["read_file"])
    assert idn.delegate(parent, kid) is None
    last = idn.events(1)[0]
    assert last["event"] == "delegated" and last["detail"] == "parent -> kid" and last["scopes"] == "files:read"
    assert idn.delegate(parent, stranger) is None                                      # recorded as refused, but not enforced yet
    assert idn.events(1)[0]["event"] == "refused"
    idn.delegate(parent, agent("parent__helper_1", tools=["read_file"]), spawned=True)
    assert "created by the agent" in idn.events(1)[0]["detail"]
    idn.set_config({"enforce": True})
    assert "not allowed to hand work to Stranger" in idn.delegate(parent, stranger)
    assert idn.delegate(parent, kid) is None
    nothing = agent("blank", tools=["write_file"])                                     # shares no scope with the parent: nothing to lend, still logged
    parent.sub_agents = ["kid", "blank"]
    assert idn.delegate(parent, nothing) is None
    last = idn.events(1)[0]
    assert last["event"] == "delegated" and "no permissions handed over" in last["detail"] and last["scopes"] == ""


def test_mcp_token_needs_the_mcp_scope(idn):
    a = agent("caller", tools=[], mcp=["remote"])
    tok = idn.mcp_token(a, "https://mcp.example.com/mcp/")
    c = idn.verify_local(tok, "https://mcp.example.com/mcp")
    assert c["scope"] == "mcp:call" and c["exp"] - c["iat"] == 120
    assert idn.mcp_token(agent("nocall", tools=["read_file"]), "https://mcp.example.com") is None


def test_check_delegation_dry_run(idn, state):
    state.agents.create(AgentDef(id="aide1", name="Aide", tools=["read_file"]))
    state.agents.create(AgentDef(id="lead1", name="Lead", tools=["read_file", "write_file"], sub_agents=["aide1"]))
    state.agents.create(AgentDef(id="loner", name="Loner", tools=["web"][:0]))
    assert idn.check_delegation("lead1", "aide1", None) == {"allowed": True, "reason": "", "scopes": ["files:read"], "chain": [idn.spiffe_id("lead1"), idn.spiffe_id("aide1")]}
    assert idn.check_delegation("lead1", "aide1", ["files:read"])["allowed"]
    assert "may not receive: files:write" in idn.check_delegation("lead1", "aide1", ["files:write"])["reason"]
    assert "not allowed to hand work" in idn.check_delegation("lead1", "loner", None)["reason"]
    state.agents.update("lead1", {"sub_agents": ["aide1", "loner"]})
    assert "no permission" in idn.check_delegation("lead1", "loner", None)["reason"]
    with pytest.raises(KeyError):
        idn.check_delegation("lead1", "nobody", None)
    with pytest.raises(KeyError):
        idn.check_delegation("nobody", "lead1", None)
    assert idn.events() == []                                                         # a dry run records nothing


def test_token_for_local_and_provider(idn, state):
    state.agents.create(AgentDef(id="tok1", name="Tok", tools=["read_file"]))
    r = run(idn.token_for("tok1", "https://api.example.com", None, None, "local"))
    assert r["source"] == "local" and r["claims"]["scope"] == "files:read" and len(r["fingerprint"]) == 12 and "token" not in r
    with pytest.raises(KeyError):
        run(idn.token_for("zzz", "aud", None, None, "local"))
    run(idn.token_for("tok1", "aud", None, None, "provider"))                          # provider is "local": the built-in issuer is used
    body = base64.urlsafe_b64encode(json.dumps({"jti": "j1", "sub": "x"}).encode()).rstrip(b"=").decode()
    seen = {}

    def handler(req):
        seen["body"] = req.content.decode()
        return httpx.Response(200, json={"access_token": f"h.{body}.s", "expires_in": 3600})
    idn.http = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    idn.set_config({"provider": {"kind": "keycloak", "params": {"base_url": "https://kc.example.com", "realm": "r", "client_id": "c"}, "secrets": {"client_secret": "s"}}})
    r = run(idn.token_for("tok1", "https://api.example.com", ["files:read"], None, "provider"))
    assert r["source"] == "keycloak" and r["claims"]["jti"] == "j1" and r["expires_in"] == 3600 and "does not separate one agent" in r["note"]
    assert "scope=files%3Aread" in seen["body"]
    assert run(idn.token_for("tok1", "aud", None, None, "local"))["source"] == "local"       # the local issuer is still available
    with pytest.raises(IdentityError, match="may not hold: shell:run"):
        run(idn.token_for("tok1", "aud", ["shell:run"], None, "provider"))
    idn.http = lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "invalid_client"})))
    with pytest.raises(IdentityError, match="refused \\(401\\)"):
        run(idn.token_for("tok1", "aud", None, None, "provider"))
    assert idn.events(1)[0]["event"] == "refused"
    idn.http = lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"access_token": "opaque"})))
    assert run(idn.token_for("tok1", "aud", None, None, "provider"))["claims"] is None


# ---------------------------------------------------------------------------------------------------------- API
def test_api(client, state):
    assert client.get("/api/identity/config").json()["active"] == "local"
    assert client.put("/api/identity/config", json={"ttl_s": 1}).status_code == 422
    assert client.put("/api/identity/config", json={"ttl_s": 120}).json()["ttl_s"] == 120
    agents = client.get("/api/identity/agents").json()["agents"]
    assert any(a["id"] == "engineer" and a["spiffe_id"].endswith("/agent/engineer") and "agents:delegate" in a["scopes"] for a in agents)
    t = client.post("/api/identity/agents/engineer/token", json={"audience": "https://api.example.com"})
    assert t.status_code == 200 and t.json()["claims"]["sub"].endswith("/agent/engineer")
    assert client.post("/api/identity/agents/nobody/token", json={"audience": "x"}).status_code == 404
    assert client.post("/api/identity/agents/engineer/token", json={"audience": ""}).status_code == 422
    idn = state.identity
    eng, expl = state.agents.get("engineer"), state.agents.get("explorer")
    st, _ = idn.mint_local(eng, "agent-studio")
    at, _ = idn.mint_local(expl, "agent-studio")
    g = "urn:ietf:params:oauth:grant-type:token-exchange"
    r = client.post("/api/identity/token", json={"grant_type": g, "subject_token": st, "actor_token": at, "audience": "agent:explorer", "scope": "files:read"})
    assert r.status_code == 200 and r.json()["token_type"] == "Bearer" and r.json()["scope"] == "files:read" and 119 <= r.json()["expires_in"] <= 120   # capped by the subject token, so a clock-second tick between mint and exchange gives 119
    assert client.post("/api/identity/token", json={"grant_type": "password"}).status_code == 400
    assert client.post("/api/identity/token", json={"grant_type": g, "subject_token": st}).status_code == 400
    bad = client.post("/api/identity/token", json={"grant_type": g, "subject_token": st, "actor_token": at, "audience": ""})
    assert bad.status_code == 400 and bad.json()["detail"]["error"] == "invalid_target"
    bad = client.post("/api/identity/token", json={"grant_type": g, "subject_token": st, "actor_token": at, "audience": "agent:x", "scope": "shell:run"})
    assert bad.status_code == 400 and bad.json()["detail"]["error"] == "invalid_request"
    nonstr = client.post("/api/identity/token", json={"grant_type": g, "subject_token": st, "actor_token": at, "audience": "agent:x", "scope": ["a"]})
    assert nonstr.status_code == 200
    v = client.post("/api/identity/verify", json={"token": st, "audience": "agent-studio"}).json()
    assert v["valid"] and v["claims"]["scope"]
    assert client.post("/api/identity/verify", json={"token": "bad"}).json()["valid"] is False
    assert client.post("/api/identity/delegations/check", json={"parent": "engineer", "child": "explorer"}).json()["allowed"] is True
    assert client.post("/api/identity/delegations/check", json={"parent": "engineer", "child": "ghost"}).status_code == 404
    assert client.get("/api/identity/jwks.json").json()["keys"][0]["alg"] == "EdDSA"
    assert client.post("/api/identity/rotate-key").json() == {"ok": True}
    assert client.post("/api/identity/verify", json={"token": st}).json()["valid"] is False
    assert any(e["event"] == "rotated" for e in client.get("/api/identity/events").json()["events"])
    assert client.get("/api/identity/stats?days=0").json()["days"] == 1


def test_agents_endpoints_keep_identity_automatic_by_default(client):
    client.post("/api/agents", json={"id": "plain1", "name": "Plain", "tools": ["read_file"]})
    a = [x for x in client.get("/api/agents").json() if x["id"] == "plain1"][0]
    assert a["identity"] == {} and a["guardrails"] == []
    listed = [x for x in client.get("/api/identity/agents").json()["agents"] if x["id"] == "plain1"][0]
    assert listed["scopes"] == ["files:read"] and listed["scopes_automatic"] is True


def test_settings_import_goes_to_the_owner(client, state):
    state.identity.set_config({"trust_domain": "example.org", "ttl_s": 900})
    exported = client.get("/api/settings/export").json()
    assert exported["settings"]["identity_config"]["trust_domain"] == "example.org" and "identity_secrets" not in exported["settings"]
    state.identity.set_config({"trust_domain": "other.org"})
    r = client.post("/api/settings/import", json={"data": exported, "dry_run": False}).json()
    assert not r["errors"] and state.identity.config()["trust_domain"] == "example.org"


# ------------------------------------------------------------------------------------ hooks in the agent loop
def test_subagent_hand_over_is_logged_and_can_be_refused(client, state):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    msg = '!agent explorer {"request": "say hi"}'
    r = client.post("/api/chat", json={"provider": "mock", "agent": "engineer", "message": msg})
    assert r.status_code == 200
    ev = state.identity.events(10, "delegated")
    assert ev and ev[0]["detail"] == "engineer -> explorer" and ev[0]["actor"].endswith("/agent/explorer")
    client.put("/api/agents/engineer", json={"identity": {"delegates": []}})
    client.put("/api/identity/config", json={"enforce": True})
    r = client.post("/api/chat", json={"provider": "mock", "agent": "engineer", "message": msg})
    j = r.json()
    assert r.status_code == 200 and any(x["event"] == "refused" for x in state.identity.events(10))
    assert any("not allowed to hand work to Explorer" in json.dumps(s) for s in j["steps"])


def test_spawned_agents_are_logged_as_delegations(client, state, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    client.post("/api/agents", json={"id": "boss", "name": "Boss", "can_spawn": True, "tools": ["read_file"]})
    msg = '!tool spawn_agent {"name": "helper", "instruction": "be brief", "task": "say hi", "tools": [], "mcp": [], "tier": "flash"}'
    r = client.post("/api/chat", json={"provider": "mock", "agent": "boss", "message": msg})
    assert r.status_code == 200
    assert any("created by the agent" in e["detail"] for e in state.identity.events(20, "delegated"))


def test_http_mcp_servers_can_opt_in_to_the_agent_identity(state, tmp_path):
    from app import mcp_registry as R
    from google.adk.tools.mcp_tool import mcp_toolset as mod
    from tests.test_cov_mcp_registry import FakeToolset
    FakeToolset.instances, FakeToolset.fail_probe, FakeToolset.close_error = [], None, None
    mod_orig = mod.McpToolset
    mod.McpToolset = FakeToolset
    try:
        state.mcp.add("idrem", "IdRemote", "", "http", None, [], "https://mcp.example.test/mcp", {"AGENT_STUDIO_IDENTITY": "1"}, enabled=True)
        state.mcp.add("plainrem", "Plain", "", "http", None, [], "https://mcp.example.test/other", {}, enabled=True)
        state.mcp.add("tokrem", "Tok", "", "http", None, [], "https://mcp.example.test/t", {"AGENT_STUDIO_IDENTITY": "1", "GITHUB_PERSONAL_ACCESS_TOKEN": "own"}, enabled=True)
        warnings: list = []
        out = asyncio.run(R.build_toolsets(state.mcp, ["idrem", "plainrem", "tokrem"], str(tmp_path), warnings, lambda url: "TOKEN-for-" + url))
        assert len(out) == 3
        hdr = {i.connection_params.url: i.connection_params.headers for i in FakeToolset.instances}
        assert hdr["https://mcp.example.test/mcp"] == {"Authorization": "Bearer TOKEN-for-https://mcp.example.test/mcp"}
        assert hdr["https://mcp.example.test/other"] == {} and hdr["https://mcp.example.test/t"] == {"Authorization": "Bearer own"}      # an explicit credential wins
        FakeToolset.instances = []
        warnings = []
        asyncio.run(R.build_toolsets(state.mcp, ["idrem"], str(tmp_path), warnings, lambda url: None))
        assert FakeToolset.instances[0].connection_params.headers == {} and "no mcp:call scope" in warnings[0]
    finally:
        mod.McpToolset = mod_orig


def test_without_an_identity_service_hand_overs_are_not_checked(state):
    state.chat.identity = None
    assert state.chat._delegation(agent("a1"), agent("b1")) is None
