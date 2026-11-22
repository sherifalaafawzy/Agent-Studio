"""Agent identity service.

* Every agent has a SPIFFE-style ID: `spiffe://<trust domain>/agent/<agent id>` (the user is `.../user/local`). It always exists;
  nothing has to be switched on to have one.
* The local issuer mints short-lived signed tokens for an agent ("workload tokens": sub = the agent) with the scopes the agent may
  hold: its own list, or - when it has none - scopes derived from the tools it has.
* Delegation is RFC 8693 token exchange: the delegate's authority is the delegator's token narrowed to scopes both hold; the new
  token has sub = delegator and `act` = the delegate (nested for chains, at most MAX_CHAIN deep). Scopes only ever shrink.
* When an agent calls a sub-agent, that hand-over is checked and logged. `enforce` (off by default) makes a refused hand-over stop
  the call; off, it is only recorded as "refused".
* An external identity provider (Okta, Entra, Keycloak, OIDC, SPIRE) can issue the token instead - see providers.py for what that
  covers. Tokens are never returned by the API or written to the audit log; only claims and a fingerprint are.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

import httpx

from ..agents import AgentDef
from . import providers as P
from .issuer import LocalIssuer, TokenError
from .model import KNOWN_SCOPES

CONFIG_KEY, SECRETS_KEY = "identity_config", "identity_secrets"
DEFAULTS = {"trust_domain": "agent-studio.local", "issuer": "http://127.0.0.1:8765", "ttl_s": 300, "enforce": False, "provider": {"kind": "local", "params": {}}}
MAX_TTL, MAX_CHAIN = 3600, 3
TD_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,60}[a-z0-9])?$")
AUD_RE = re.compile(r"^[A-Za-z0-9:/._?&=%#@+~-]{1,300}$")
FILE_READ, FILE_WRITE = {"read_file", "list_dir", "glob_files", "search_text"}, {"write_file", "edit_file"}


class IdentityError(ValueError):
    """A request the identity settings refuse. The message is plain and safe to show."""


def derive_scopes(a: AgentDef) -> list[str]:
    """The scopes an agent holds when it has no list of its own: what its tools and links need."""
    t = set(a.tools)
    out = []
    for scope, hit in (("files:read", t & FILE_READ), ("files:write", t & FILE_WRITE), ("shell:run", "run_shell" in t),
                       ("memory:read", t & {"memory_search", "memory_notes"}), ("memory:write", t & {"memory_save", "memory_forget"}), ("artifacts:write", "save_artifact" in t),
                       ("mcp:call", a.mcp), ("agents:delegate", a.sub_agents or a.can_spawn or a.acp_agents)):
        if hit:
            out.append(scope)
    return out


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:12]


class IdentityService:
    def __init__(self, db, agents, tel=None, issuer: LocalIssuer | None = None, http=None, spire_runner=None):
        self.db, self.agents, self.tel = db, agents, tel
        self.issuer = issuer or LocalIssuer()
        self.http = http or httpx.AsyncClient
        self.spire_runner = spire_runner

    # ------------------------------------------------------------------ config
    def config(self) -> dict:
        try:
            raw = json.loads(self.db.get_setting(CONFIG_KEY, "{}") or "{}")
        except json.JSONDecodeError:
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        cfg = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
        prov = cfg["provider"] if isinstance(cfg["provider"], dict) else {}
        cfg["provider"] = {"kind": prov.get("kind", "local"), "params": prov.get("params") if isinstance(prov.get("params"), dict) else {}}
        return cfg

    def _secrets(self) -> dict:
        try:
            v = json.loads(self.db.get_setting(SECRETS_KEY, "{}") or "{}")
        except json.JSONDecodeError:
            return {}
        return v if isinstance(v, dict) else {}

    def set_config(self, patch: dict) -> dict:
        cfg = self.config()
        if "trust_domain" in patch:
            if not isinstance(patch["trust_domain"], str) or not TD_RE.match(patch["trust_domain"]):
                raise IdentityError("the trust domain must look like example.org (lowercase letters, digits, dots and dashes)")
            cfg["trust_domain"] = patch["trust_domain"]
        if "issuer" in patch:
            if not isinstance(patch["issuer"], str) or not patch["issuer"].startswith(("http://", "https://")) or len(patch["issuer"]) > 200:
                raise IdentityError("the issuer must be a web address starting with http:// or https://")
            cfg["issuer"] = patch["issuer"].rstrip("/")
        if "ttl_s" in patch:
            if isinstance(patch["ttl_s"], bool) or not isinstance(patch["ttl_s"], int) or not 30 <= patch["ttl_s"] <= MAX_TTL:
                raise IdentityError(f"token lifetime must be between 30 and {MAX_TTL} seconds")
            cfg["ttl_s"] = patch["ttl_s"]
        if "enforce" in patch:
            if not isinstance(patch["enforce"], bool):
                raise IdentityError("enforce must be true or false")
            cfg["enforce"] = patch["enforce"]
        sec = self._secrets()
        if "provider" in patch:
            pv = patch["provider"]
            if not isinstance(pv, dict) or not isinstance(pv.get("kind", "local"), str):
                raise IdentityError("provider must be an object with a kind")
            kind, params = pv.get("kind", "local"), pv.get("params") or {}
            if kind != "local" and kind not in P.KINDS:
                raise IdentityError(f"provider kind must be local or one of: {', '.join(P.KINDS)}")
            for name, val in (pv.get("secrets") or {}).items():
                if val is None:
                    continue
                if kind == "local" or name not in P.KINDS[kind].secrets:
                    raise IdentityError(f"'{name}' is not a credential of this provider")
                sec = {**sec, name: str(val)} if val != "" else {k: v for k, v in sec.items() if k != name}
            if kind == "local":
                params, sec = {}, {}
            else:
                self._provider(kind, params, sec)                          # refuses a setup that could never work
            cfg["provider"] = {"kind": kind, "params": params}
        self.db.set_setting(CONFIG_KEY, json.dumps(cfg))
        self.db.set_setting(SECRETS_KEY, json.dumps(sec))
        return self.public_config()

    def _provider(self, kind: str, params: dict, secrets: dict):
        try:
            return P.make_provider(kind, params, secrets, self.http, self.spire_runner)
        except P.ProviderError as e:
            raise IdentityError(str(e)) from e

    def public_config(self) -> dict:
        cfg = self.config()
        kind = cfg["provider"]["kind"]
        sec = self._secrets()
        return {**cfg, "provider": {**cfg["provider"], "secrets_set": sorted(k for k, v in sec.items() if v)},
                "providers": [dict(id="local", label="Built-in issuer", summary="Agent Studio signs short-lived tokens itself (Ed25519). Nothing leaves this PC.", fields=[], exchange="rfc8693")] +
                             [dict(id=k.id, label=k.label, summary=k.summary, exchange=k.exchange, fields=[f.__dict__ for f in k.fields]) for k in P.KINDS.values()],
                "scopes": KNOWN_SCOPES, "active": kind}

    # ------------------------------------------------------------------ identities
    def spiffe_id(self, agent_id: str) -> str:
        return f"spiffe://{self.config()['trust_domain']}/agent/{agent_id}"

    def user_id(self) -> str:
        return f"spiffe://{self.config()['trust_domain']}/user/local"

    def allowed_scopes(self, a: AgentDef) -> list[str]:
        return list(a.identity["scopes"]) if "scopes" in a.identity else derive_scopes(a)

    def delegates_of(self, a: AgentDef) -> list[str]:
        return list(a.identity["delegates"]) if "delegates" in a.identity else list(a.sub_agents)

    def describe(self, a: AgentDef) -> dict:
        """What the UI, the AI-BOM and the discovery catalog show: nothing secret."""
        cfg = self.config()
        return {"spiffe_id": self.spiffe_id(a.id), "issuer": cfg["provider"]["kind"], "trust_domain": cfg["trust_domain"],
                "scopes": self.allowed_scopes(a), "scopes_automatic": "scopes" not in a.identity, "delegates": self.delegates_of(a),
                "delegates_automatic": "delegates" not in a.identity, "ttl_s": cfg["ttl_s"]}

    # ------------------------------------------------------------------ audit
    def record(self, event: str, subject: str, actor: str = "", audience: str = "", scopes=(), jti: str = "", detail: str = "") -> None:
        self.db.execute("INSERT INTO identity_events(ts,event,subject,actor,audience,scopes,jti,detail) VALUES(?,?,?,?,?,?,?,?)",
                        (time.time(), event, subject, actor, audience[:300], " ".join(scopes), jti, detail[:300]))
        if event == "refused" and self.tel is not None:
            self.tel.emit("finding", kind="identity", title=f"An identity request was refused ({detail[:80]})", severity_id=3, agent=subject, profile="identity", stage="delegation")

    def events(self, limit: int = 100, event: str | None = None) -> list[dict]:
        where, args = (" WHERE event=?", (event,)) if event else ("", ())
        return [dict(r) for r in self.db.query(f"SELECT * FROM identity_events{where} ORDER BY id DESC LIMIT ?", (*args, max(1, min(limit, 500))))]

    def stats(self, days: int = 7) -> dict:
        rows = self.db.query("SELECT event, COUNT(*) n FROM identity_events WHERE ts>=? GROUP BY event", (time.time() - days * 86400,))
        by = {r["event"]: r["n"] for r in rows}
        return {"days": days, **{k: by.get(k, 0) for k in ("issued", "exchanged", "delegated", "refused")}}

    # ------------------------------------------------------------------ tokens (local issuer)
    def _claims(self, sub: str, aud: str, scopes: list[str], ttl: int, extra: dict | None = None, exp_cap: float | None = None) -> dict:
        now = time.time()
        exp = min(now + ttl, exp_cap) if exp_cap else now + ttl
        return {"iss": self.config()["issuer"], "sub": sub, "aud": aud, "iat": int(now), "nbf": int(now), "exp": int(exp), "jti": uuid.uuid4().hex,
                "scope": " ".join(scopes), **(extra or {})}

    def _ttl(self, ttl: int | None) -> int:
        t = ttl if ttl is not None else self.config()["ttl_s"]
        if isinstance(t, bool) or not isinstance(t, int) or not 30 <= t <= MAX_TTL:
            raise IdentityError(f"token lifetime must be between 30 and {MAX_TTL} seconds")
        return t

    def _aud(self, audience: str) -> str:
        if not isinstance(audience, str) or not AUD_RE.match(audience):
            raise IdentityError("name the audience (the service the token is for), for example https://api.example.com")
        return audience

    def mint_local(self, a: AgentDef, audience: str, scopes: list[str] | None = None, ttl: int | None = None, audit: bool = True) -> tuple[str, dict]:
        """A workload token for an agent: sub = its SPIFFE id. Asking for scopes the agent may not hold is refused."""
        held = self.allowed_scopes(a)
        want = list(dict.fromkeys(scopes)) if scopes is not None else held
        over = [s for s in want if s not in held]
        aud = self._aud(audience)
        if over:
            self.record("refused", self.spiffe_id(a.id), audience=aud, scopes=over, detail=f"asked for scopes the agent does not hold: {', '.join(over)}")
            raise IdentityError(f"{a.name} may not hold: {', '.join(over)}")
        claims = self._claims(self.spiffe_id(a.id), aud, want, self._ttl(ttl))
        token = self.issuer.sign(claims)
        if audit:
            self.record("issued", claims["sub"], audience=aud, scopes=want, jti=claims["jti"])
        return token, claims

    def exchange_local(self, subject_token: str, actor_token: str, audience: str, scopes: list[str] | None = None, audit: bool = True) -> tuple[str, dict]:
        """RFC 8693 delegation. The subject token says whose authority is lent; the actor token says who borrows it (impersonation,
        a token without an actor, is not offered). The result can only hold scopes that both tokens hold."""
        cfg = self.config()
        aud = self._aud(audience)
        try:
            subj = self.issuer.verify(subject_token, cfg["issuer"])
            act = self.issuer.verify(actor_token, cfg["issuer"])
        except TokenError as e:
            self.record("refused", "unknown", audience=aud, detail=f"bad token: {e}")
            raise IdentityError(f"cannot exchange: {e}") from e
        have = set(subj.get("scope", "").split()) & set(act.get("scope", "").split())          # the delegate cannot receive more than it may hold itself
        extra = sorted(set(scopes or []) - have)
        if extra:
            self.record("refused", subj["sub"], actor=act["sub"], audience=aud, scopes=extra, detail="asked for more than the delegator and delegate both hold")
            raise IdentityError("the delegate may not receive: " + ", ".join(extra))
        want = list(dict.fromkeys(scopes)) if scopes is not None else sorted(have)
        if not want:
            self.record("refused", subj["sub"], actor=act["sub"], audience=aud, detail="no permission would be left to hand over")
            raise IdentityError("no permission would be left to hand over")
        chain: dict = {"sub": act["sub"], **({"act": subj["act"]} if subj.get("act") else {})}
        depth, node = 0, chain
        while node:
            depth, node = depth + 1, node.get("act")
        if depth > MAX_CHAIN:
            self.record("refused", subj["sub"], actor=act["sub"], audience=aud, detail=f"delegation chain longer than {MAX_CHAIN}")
            raise IdentityError(f"delegation chains may be at most {MAX_CHAIN} agents long")
        claims = self._claims(subj["sub"], aud, want, cfg["ttl_s"], {"act": chain}, exp_cap=subj["exp"])
        token = self.issuer.sign(claims)
        if audit:
            self.record("exchanged", subj["sub"], actor=act["sub"], audience=aud, scopes=want, jti=claims["jti"])
        return token, claims

    def verify_local(self, token: str, audience: str | None = None) -> dict:
        try:
            return self.issuer.verify(token, self.config()["issuer"], audience)
        except TokenError as e:
            raise IdentityError(str(e)) from e

    # ------------------------------------------------------------------ delegation at run time
    def delegate(self, parent: AgentDef, child: AgentDef, spawned: bool = False) -> str | None:
        """An agent hands work to another. Checks the hand-over, narrows the scopes, logs it. Returns a message when it must be
        refused (only if `enforce` is on); otherwise None."""
        cfg = self.config()
        ok_target = spawned or child.id in self.delegates_of(parent)
        if not ok_target:
            self.record("refused", self.spiffe_id(parent.id), actor=self.spiffe_id(child.id), detail=f"{parent.name} may not hand work to {child.name}")
            return f"{parent.name} is not allowed to hand work to {child.name} (Security > Identity)" if cfg["enforce"] else None
        note = " (created by the agent)" if spawned else ""
        if not set(self.allowed_scopes(parent)) & set(self.allowed_scopes(child)):         # e.g. a helper with no tools: nothing to lend
            self.record("delegated", self.spiffe_id(parent.id), actor=self.spiffe_id(child.id), audience=f"agent:{child.id}",
                        detail=f"{parent.id} -> {child.id}{note} (no permissions handed over)")
            return None
        pt, _ = self.mint_local(parent, "agent-studio", audit=False)
        ct, _ = self.mint_local(child, "agent-studio", audit=False)
        _, claims = self.exchange_local(pt, ct, f"agent:{child.id}", audit=False)
        self.record("delegated", claims["sub"], actor=self.spiffe_id(child.id), audience=claims["aud"], scopes=claims["scope"].split(), jti=claims["jti"],
                    detail=f"{parent.id} -> {child.id}{note}")
        return None

    def mcp_token(self, agent: AgentDef, server_url: str) -> str | None:
        """The bearer token an agent presents to an HTTP MCP server it was linked to: audience = that server (RFC 8707 resource
        indicator, as the MCP authorization spec asks), scope mcp:call. None if the agent may not call MCP servers."""
        if "mcp:call" not in self.allowed_scopes(agent):
            return None
        return self.mint_local(agent, server_url.rstrip("/"), ["mcp:call"], 120)[0]

    # ------------------------------------------------------------------ what the API offers
    async def token_for(self, agent_id: str, audience: str, scopes: list[str] | None, ttl: int | None, source: str) -> dict:
        a = self.agents.get(agent_id)
        if a is None:
            raise KeyError(agent_id)
        cfg = self.config()
        if source == "provider" and cfg["provider"]["kind"] != "local":
            held = self.allowed_scopes(a)
            want = held if scopes is None else scopes
            if [s for s in want if s not in held]:
                raise IdentityError(f"{a.name} may not hold: {', '.join(s for s in want if s not in held)}")
            prov = self._provider(cfg["provider"]["kind"], cfg["provider"]["params"], self._secrets())
            try:
                res = await prov.token(self._aud(audience), want)
            except P.ProviderError as e:
                self.record("refused", self.spiffe_id(a.id), audience=audience, detail=f"provider: {e}")
                raise IdentityError(str(e)) from e
            claims = P.jwt_claims(res["access_token"])
            self.record("issued", self.spiffe_id(a.id), audience=audience, scopes=want, jti=(claims or {}).get("jti", ""), detail=f"from {cfg['provider']['kind']}")
            return {"source": cfg["provider"]["kind"], "claims": claims, "fingerprint": fingerprint(res["access_token"]), "expires_in": res["expires_in"],
                    "note": "The identity provider authenticates Agent Studio as a workload; it does not separate one agent from another."}
        token, claims = self.mint_local(a, audience, scopes, ttl)
        return {"source": "local", "claims": claims, "fingerprint": fingerprint(token), "expires_in": claims["exp"] - claims["iat"]}

    def check_delegation(self, parent_id: str, child_id: str, scopes: list[str] | None) -> dict:
        """A dry run of a hand-over (the UI's 'try it'): what the delegate would receive. Nothing is issued or logged."""
        p, c = self.agents.get(parent_id), self.agents.get(child_id)
        if p is None or c is None:
            raise KeyError(parent_id if p is None else child_id)
        if child_id not in self.delegates_of(p):
            return {"allowed": False, "reason": f"{p.name} is not allowed to hand work to {c.name}", "scopes": []}
        have = [s for s in self.allowed_scopes(p) if s in set(self.allowed_scopes(c))]
        want = have if scopes is None else [s for s in scopes if s in have]
        if scopes is not None and set(scopes) - set(have):
            return {"allowed": False, "reason": "the delegate may not receive: " + ", ".join(sorted(set(scopes) - set(have))), "scopes": have}
        if not want:
            return {"allowed": False, "reason": "no permission would be left to hand over", "scopes": []}
        return {"allowed": True, "reason": "", "scopes": want, "chain": [self.spiffe_id(p.id), self.spiffe_id(c.id)]}
