"""Guardrail service: profiles (named guard setups), which profiles an agent uses, running the checks, and the audit trail.

* Master switch `guardrails_enabled` (off by default). Profiles live in the `guardrail_config` setting; credentials for
  cloud guards in `guardrail_secrets` (write-only: the API says only which are set).
* An agent picks profiles in `AgentDef.guardrails` ([] = the default profiles, ["none"] = no guardrails for this agent).
* Per profile: stages (input / output / tool), mode (off / monitor / block) and what to do when the guard itself cannot
  answer (fail open = carry on and record it, fail closed = treat it as a block).
* Every flag, block and guard failure is written to `guardrail_events` WITHOUT the checked text (only its length), and is
  sent to the security export as a finding.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict

import httpx

from . import cloud, d1gate, modelguard, rules
from .base import MODES, STAGES, Env, Guard, GuardError, Kind, Profile, Verdict

CONFIG_KEY, SECRETS_KEY, ENABLED_KEY = "guardrail_config", "guardrail_secrets", "guardrails_enabled"
KINDS: dict[str, Kind] = {k.id: k for k in (rules.KIND, modelguard.KIND, d1gate.KIND, cloud.BEDROCK, cloud.AZURE, cloud.ARMOR)}
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")
BASIC = Profile(id="basic", name="Basic rules", kind="rules", mode="monitor", default=True)
NONE = "none"


class GuardrailError(ValueError):
    """A request the guardrail settings refuse. The message is plain and safe to show."""


class Decision:
    def __init__(self):
        self.blocked = False
        self.message = ""
        self.events: list[dict] = []                 # one per profile that had something to say


def _default_env() -> Env:
    def aws(region, profile="", ak="", sk="", token=""):
        from ..rag.stores.base import StoreError
        from ..rag.stores.bedrock_kb import aws_clients
        try:
            return aws_clients(region, profile, ak, sk, token)
        except StoreError as e:
            raise GuardError(str(e)) from e

    def google(sa: str):
        from ..rag.stores.base import StoreError
        from ..rag.stores.vertex_rag import google_token_provider
        try:
            return google_token_provider(sa)
        except StoreError as e:
            raise GuardError(str(e)) from e
    return Env(http=httpx.AsyncClient, aws_clients=aws, google_token=google)


class GuardrailService:
    def __init__(self, db, tel=None, env: Env | None = None):
        self.db, self.tel = db, tel
        self.env = env or _default_env()
        if self.env.decide is None:
            self.env.decide = self._d1_decide
        self.d1_ready = lambda model: False                  # State wires these to the Auto router's decision-model worker
        self.d1_worker = lambda model: None
        self._cache: dict[str, tuple[str, Guard]] = {}

    async def _d1_decide(self, model: str, state, questions: dict, timeout: float) -> dict:
        if not self.d1_ready(model):
            raise GuardError("the d1 model is not installed (Settings > Auto > Install)")
        return await asyncio.to_thread(self.d1_worker(model).decide, state, questions, timeout)

    # ------------------------------------------------------------------ settings
    def enabled(self) -> bool:
        return self.db.get_setting(ENABLED_KEY, "0") == "1"

    def set_enabled(self, on: bool) -> None:
        self.db.set_setting(ENABLED_KEY, "1" if on else "0")

    def _raw(self) -> list[dict]:
        try:
            v = json.loads(self.db.get_setting(CONFIG_KEY, "{}") or "{}")
        except json.JSONDecodeError:
            return []
        return [p for p in (v.get("profiles") if isinstance(v, dict) else None) or [] if isinstance(p, dict) and p.get("id")]

    def _secrets(self) -> dict:
        try:
            v = json.loads(self.db.get_setting(SECRETS_KEY, "{}") or "{}")
        except json.JSONDecodeError:
            return {}
        return v if isinstance(v, dict) else {}

    def profiles(self) -> list[Profile]:
        saved = [Profile(**{k: p[k] for k in Profile.__dataclass_fields__ if k in p}) for p in self._raw()]
        return saved or [BASIC]

    def by_id(self, pid: str) -> Profile | None:
        return next((p for p in self.profiles() if p.id == pid), None)

    def public(self) -> dict:
        sec = self._secrets()
        out = []
        for p in self.profiles():
            k = KINDS.get(p.kind)
            out.append({**asdict(p), "secrets_set": sorted(n for n, v in (sec.get(p.id) or {}).items() if v),
                        "leaves_pc": bool(k and k.leaves_pc), "stored": p is not BASIC})
        return {"enabled": self.enabled(), "profiles": out, "kinds": self.kinds()}

    @staticmethod
    def kinds() -> list[dict]:
        return [dict(id=k.id, label=k.label, summary=k.summary, where=k.where, leaves_pc=k.leaves_pc, cost=k.cost,
                     fields=[dict(name=f.name, label=f.label, type=f.type, required=f.required, default=f.default, placeholder=f.placeholder,
                                  choices=list(f.choices), help=f.help) for f in k.fields]) for k in KINDS.values()]

    # ------------------------------------------------------------------ editing
    def _validate(self, body: dict, existing: Profile | None, secrets: dict) -> Profile:
        pid = (body.get("id") or (existing.id if existing else "")).strip()
        if not ID_RE.match(pid):
            raise GuardrailError("the id must be lowercase letters, digits, - or _, starting with a letter (2-31 characters)")
        base = asdict(existing) if existing else asdict(Profile(id=pid, name=pid, kind=body.get("kind") or "rules"))
        d = {**base, **{k: v for k, v in body.items() if k in Profile.__dataclass_fields__ and k != "id"}, "id": pid}
        if d["kind"] not in KINDS:
            raise GuardrailError(f"kind must be one of: {', '.join(KINDS)}")
        if not str(d["name"]).strip() or len(str(d["name"])) > 60:
            raise GuardrailError("give the profile a name (at most 60 characters)")
        if d["mode"] not in MODES:
            raise GuardrailError(f"mode must be one of: {', '.join(MODES)}")
        if d["fail"] not in ("open", "closed"):
            raise GuardrailError("fail must be open or closed")
        st = d["stages"] if isinstance(d["stages"], dict) else {}
        d["stages"] = {s: bool(st.get(s, True)) for s in STAGES}
        if not any(d["stages"].values()):
            raise GuardrailError("pick at least one place to look (input, output or tool calls)")
        if not isinstance(d["params"], dict):
            raise GuardrailError("params must be an object")
        d["default"] = bool(d["default"])
        p = Profile(**d)
        try:
            KINDS[p.kind].make(p.params, secrets, self.env)           # refuses a setup that could never work
        except GuardError as e:
            raise GuardrailError(str(e)) from e
        return p

    def save(self, body: dict) -> dict:
        pid = (body.get("id") or "").strip()
        existing = self.by_id(pid) if pid in {x["id"] for x in self._raw()} else None
        kind = body.get("kind") or (existing.kind if existing else "rules")
        if kind not in KINDS:
            raise GuardrailError(f"kind must be one of: {', '.join(KINDS)}")
        if existing is not None and kind != existing.kind:
            raise GuardrailError("a profile's kind cannot change: create a new profile instead")
        sec_all = self._secrets()
        sec = dict(sec_all.get(pid) or {})
        for name, val in (body.get("secrets") or {}).items():          # None = unchanged, "" = clear
            if val is None:
                continue
            if name not in KINDS[kind].secrets:
                raise GuardrailError(f"'{name}' is not a credential of this kind of guard")
            if val == "":
                sec.pop(name, None)
            else:
                sec[name] = str(val)
        p = self._validate({**body, "kind": kind}, existing, sec)
        raw = [x for x in self._raw() if x["id"] != p.id] + [asdict(p)]
        if p.default:
            raw = [{**x, "default": x["id"] == p.id} for x in raw]
        self.db.set_setting(CONFIG_KEY, json.dumps({"profiles": raw}))
        self.db.set_setting(SECRETS_KEY, json.dumps({**sec_all, p.id: sec}))
        self._cache.pop(p.id, None)
        return self.public()

    def import_profiles(self, value: dict) -> None:
        """Settings import: each profile goes through the normal validation. Credentials are never imported, so a cloud profile
        that needs them is refused with a reason (add it in Security > Guardrails instead)."""
        for p in value.get("profiles") or []:
            if not isinstance(p, dict):
                raise GuardrailError("a guardrail profile must be an object")
            self.save({k: v for k, v in p.items() if k not in ("secrets_set", "leaves_pc", "stored")})

    def delete(self, pid: str) -> dict:
        raw = self._raw()
        if pid not in {x["id"] for x in raw}:
            raise KeyError(pid)
        self.db.set_setting(CONFIG_KEY, json.dumps({"profiles": [x for x in raw if x["id"] != pid]}))
        sec = self._secrets()
        sec.pop(pid, None)
        self.db.set_setting(SECRETS_KEY, json.dumps(sec))
        self._cache.pop(pid, None)
        return self.public()

    # ------------------------------------------------------------------ choosing and running
    def active_profiles(self, agent_rails: list[str] | None, stage: str) -> list[Profile]:
        """The profiles that look at `stage` for an agent that picked `agent_rails` (empty = the default profiles)."""
        if not self.enabled() or agent_rails == [NONE]:
            return []
        every = self.profiles()
        chosen = [p for p in every if p.id in agent_rails] if agent_rails else [p for p in every if p.default]
        return [p for p in chosen if p.mode != "off" and p.stages.get(stage)]

    def _guard(self, p: Profile) -> Guard:
        sec = (self._secrets().get(p.id) or {}) if p is not BASIC else {}
        sig = json.dumps([p.kind, p.params, sec], sort_keys=True, default=str)
        hit = self._cache.get(p.id)
        if hit and hit[0] == sig:
            return hit[1]
        g = KINDS[p.kind].make(p.params, sec, self.env)
        self._cache[p.id] = (sig, g)
        return g

    async def run_one(self, p: Profile, text: str, stage: str) -> Verdict:
        v = await self._guard(p).check(text, stage)
        v.guard = p.id
        return v

    async def evaluate(self, stage: str, text: str, agent: str, agent_rails: list[str] | None) -> Decision:
        d = Decision()
        if not text.strip():
            return d
        for p in self.active_profiles(agent_rails, stage):
            try:
                v = await self.run_one(p, text, stage)
                if not v.flagged:
                    continue
                action = "blocked" if p.mode == "block" else "flagged"
            except GuardError as e:
                v, action = Verdict(True, "guard_unavailable", str(e), guard=p.id), ("blocked" if p.fail == "closed" and p.mode == "block" else "error")
                if p.fail == "closed" and p.mode == "block":
                    v.reason = f"the guard could not answer, and this profile blocks when that happens ({e})"
            ev = dict(stage=stage, agent=agent, profile=p.id, profile_name=p.name, kind=p.kind, mode=p.mode, action=action,
                      category=v.category, reason=v.reason, score=v.score, chars=len(text))
            self._record(ev)
            d.events.append(ev)
            if action == "blocked":
                d.blocked = True
                d.message = f"I stopped here: the safety rule '{p.name}' blocked this {_STAGE_WORDS[stage]} ({v.reason})."
                break
        return d

    def _record(self, ev: dict) -> None:
        self.db.execute("INSERT INTO guardrail_events(ts,stage,agent,profile,kind,mode,action,category,reason,score,chars) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (time.time(), ev["stage"], ev["agent"], ev["profile"], ev["kind"], ev["mode"], ev["action"], ev["category"],
                         ev["reason"][:300], ev["score"], ev["chars"]))
        if self.tel is not None:
            self.tel.emit("finding", kind="guardrail", title=f"A guardrail {ev['action']} a {ev['stage']} ({ev['category'] or 'no category'})",
                          severity_id=3 if ev["action"] == "blocked" else 2, agent=ev["agent"], profile=ev["profile"], stage=ev["stage"])

    async def test(self, body: dict, text: str, stage: str) -> dict:
        """Try a profile (saved, or the unsaved draft in `body`) on some text. Nothing is recorded."""
        if stage not in STAGES:
            raise GuardrailError(f"stage must be one of: {', '.join(STAGES)}")
        if not (text or "").strip():
            raise GuardrailError("type some text to check")
        pid = (body.get("id") or "").strip()
        sec = dict(self._secrets().get(pid) or {})
        sec.update({k: v for k, v in (body.get("secrets") or {}).items() if v})
        if body.get("kind"):                                          # an unsaved draft
            p = self._validate({**body, "stages": {s: True for s in STAGES}}, None, sec)
        else:
            p = self.by_id(pid)
            if p is None:
                raise GuardrailError("choose or define a profile to test")
        t0 = time.time()
        try:
            g = KINDS[p.kind].make(p.params, sec, self.env)
            v = await g.check(text, stage)
        except GuardError as e:
            return {"ok": False, "error": str(e), "ms": round((time.time() - t0) * 1000)}
        return {"ok": True, "flagged": v.flagged, "category": v.category, "reason": v.reason, "score": v.score,
                "would": ("block" if p.mode == "block" else "record") if v.flagged else "allow", "ms": round((time.time() - t0) * 1000)}

    # ------------------------------------------------------------------ audit
    def events(self, limit: int = 100, stage: str | None = None, action: str | None = None) -> list[dict]:
        conds = [(c, v) for c, v in (("stage", stage), ("action", action)) if v]
        where = (" WHERE " + " AND ".join(f"{c}=?" for c, _ in conds)) if conds else ""
        rows = self.db.query(f"SELECT * FROM guardrail_events{where} ORDER BY id DESC LIMIT ?", (*[v for _, v in conds], max(1, min(limit, 500))))
        return [dict(r) for r in rows]

    def stats(self, days: int = 7) -> dict:
        since = time.time() - days * 86400
        rows = self.db.query("SELECT action, COUNT(*) n FROM guardrail_events WHERE ts>=? GROUP BY action", (since,))
        by = {r["action"]: r["n"] for r in rows}
        cats = self.db.query("SELECT category, COUNT(*) n FROM guardrail_events WHERE ts>=? GROUP BY category ORDER BY n DESC LIMIT 5", (since,))
        return {"days": days, "flagged": by.get("flagged", 0), "blocked": by.get("blocked", 0), "errors": by.get("error", 0),
                "top_categories": [{"category": r["category"] or "none", "count": r["n"]} for r in cats]}


_STAGE_WORDS = {"input": "message", "output": "answer", "tool": "tool call"}
