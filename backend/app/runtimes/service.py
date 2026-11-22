"""Deploying agents to a runtime and moving between runtimes safely.

A *release* is one deployment of an agent's definition to a target. The flow the UI walks the user through:
  plan      -> what would be lost on that target (compatibility report), what changed since the live release, the bundle and the
               exact build/push commands, the cost and side effects
  deploy    -> (confirm) a new release. With a live release already there it is a *shadow* (a candidate that gets no traffic);
               the first release goes live by itself once the platform says it is ready
  health    -> the platform's own status, plus an optional test message (that one costs model tokens)
  promote   -> (confirm) switch traffic to the candidate; the previous live release is kept for rollback
  rollback  -> (confirm) switch traffic back to the release that was live before
  delete    -> (confirm) remove a release from the platform; the live one is protected
Editing an agent that is deployed never changes the deployment by itself: `migration()` shows what changed and what the target
cannot do, and the user decides.  Nothing here touches a cloud unless the user confirms; credentials are write-only.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ..agents import AgentDef
from .bundle import agent_spec, commands, default_image, image_ok, spec_diff, spec_hash, write_bundle
from .targets import TARGETS, TargetError

CONFIG_KEY, SECRETS_KEY = "runtime_config", "runtime_secrets"
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,60}$")
SMOKE = "Reply with the single word: ok"


class RuntimeFault(Exception):
    """A refusal or failure with a plain explanation."""


def compat(a: AgentDef, target: str) -> list[dict]:
    """What the agent would lose (or must give up) on a target. level: block (cannot deploy) | warn | info."""
    if target == "local":
        return []
    out: list[dict] = []

    def add(level: str, feature: str, text: str) -> None:
        out.append(dict(level=level, feature=feature, text=text))
    if a.acp_agents:
        add("block", "external agents", "Coding agents such as Claude Code run on this PC and cannot be called from a cloud runtime. Remove them from this agent or keep it local.")
    if a.tools:
        add("warn", "tools", "The built-in tools (files, shell, search...) do not exist in a deployed runtime: it can only answer with the model. They keep working when it runs on this PC.")
    if a.mcp:
        add("warn", "tools", "MCP servers run on this PC, so a deployed agent cannot use them.")
    if a.sub_agents or a.can_spawn:
        add("warn", "sub-agents", "Sub-agents and agent-created helpers are not part of a deployed runtime.")
    if a.rag:
        add("warn", "knowledge", "Knowledge bases live on this PC: the deployed agent cannot search them.")
    if a.data:
        add("warn", "data", "Data sources are reached from this PC: the deployed agent cannot query them.")
    if a.memory:
        add("warn", "memory", "Memory stays on this PC: the deployed agent will not remember anything between chats.")
    if a.skills:
        add("info", "skills", "Skills are not included in the deployment.")
    if a.guardrails != ["none"]:
        add("warn", "guardrails", "Guardrails run in Agent Studio: calls made straight to the deployed agent are not checked.")
    if a.framework != "adk":
        add("info", "framework", f"The deployed agent is a plain model loop, not {a.framework}.")
    add("warn", "spend", "Model calls happen in the runtime with the key you give it, not under Agent Studio's spend caps. Watch that account's usage.")
    return out


class RuntimeService:
    def __init__(self, db: Any, agents: Any, model_for: Callable[[AgentDef], str], home: Path, factory: Callable[..., Any] | None = None):
        self.db, self.agents, self.model_for, self.home = db, agents, model_for, Path(home)
        self.factory = factory or (lambda tid, cfg, sec: TARGETS[tid](cfg, sec))

    # ---------------------------------------------------------------------------------------------------------------- configuration
    def config(self) -> dict:
        try:
            c = json.loads(self.db.get_setting(CONFIG_KEY) or "{}")
        except ValueError:
            c = {}
        return {"targets": c.get("targets") if isinstance(c.get("targets"), dict) else {}, "image_template": str(c.get("image_template") or "")}

    def _secrets(self) -> dict:
        try:
            s = json.loads(self.db.get_setting(SECRETS_KEY) or "{}")
        except ValueError:
            s = {}
        return s if isinstance(s, dict) else {}

    def set_config(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise RuntimeFault("the settings must be an object")
        cfg, secrets = self.config(), self._secrets()
        if "image_template" in patch:
            t = patch["image_template"]
            if not isinstance(t, str) or len(t) > 300 or (t and not image_ok(t.replace("{agent}", "a").replace("{tag}", "t"))):
                raise RuntimeFault("the image template must look like registry.example.com/agents:{agent}-{tag}")
            cfg["image_template"] = t
        for tid, vals in (patch.get("targets") or {}).items():
            if tid not in TARGETS or tid == "local":
                raise RuntimeFault(f"unknown runtime target '{tid}'")
            if not isinstance(vals, dict):
                raise RuntimeFault(f"settings for {tid} must be an object")
            names = {f["name"] for f in TARGETS[tid].fields if not f["secret"]} | {"enabled"}
            bad = sorted(set(vals) - names)
            if bad:
                raise RuntimeFault(f"{tid} has no setting {', '.join(bad)}")
            if any(k != "enabled" and not isinstance(v, str) or len(str(v)) > 300 for k, v in vals.items()):
                raise RuntimeFault("every runtime setting is text of at most 300 characters")
            if "enabled" in vals and not isinstance(vals["enabled"], bool):
                raise RuntimeFault("'enabled' must be true or false")
            cfg["targets"][tid] = {**cfg["targets"].get(tid, {}), **vals}
        for tid, vals in (patch.get("secrets") or {}).items():
            if tid == "model_env":
                if not isinstance(vals, dict) or any(not ENV_NAME.match(str(k)) or not isinstance(v, str) or len(v) > 4000 for k, v in vals.items()):
                    raise RuntimeFault("model keys must look like {OPENAI_API_KEY: 'sk-...'} (upper-case names)")
            elif tid not in TARGETS or not isinstance(vals, dict) or any(k not in {f["name"] for f in TARGETS[tid].fields if f["secret"]} or not isinstance(v, str) for k, v in vals.items()):
                raise RuntimeFault(f"those are not secret settings of {tid}")
            merged = {**secrets.get(tid, {}), **vals}
            secrets[tid] = {k: v for k, v in merged.items() if v}               # an empty value clears it
        for k in patch.get("clear_model_env") or []:
            secrets.get("model_env", {}).pop(k, None)
        self.db.set_setting(CONFIG_KEY, json.dumps(cfg))
        self.db.set_setting(SECRETS_KEY, json.dumps(secrets))
        return self.public()

    def public(self) -> dict:
        cfg, sec = self.config(), self._secrets()
        out = []
        for tid, cls in TARGETS.items():
            vals = cfg["targets"].get(tid, {})
            problem = ""
            if tid != "local":
                try:
                    cls({k: v for k, v in vals.items() if k != "enabled"}, sec.get(tid, {}))
                except TargetError as e:
                    problem = str(e)
            out.append({**_info(cls), "enabled": tid == "local" or bool(vals.get("enabled")),
                        "values": {k: v for k, v in vals.items() if k != "enabled"}, "secrets_set": sorted(sec.get(tid, {})), "configured": not problem, "problem": problem})
        return {"targets": out, "image_template": cfg["image_template"], "model_env_set": sorted(sec.get("model_env", {}))}

    def _target(self, tid: str) -> Any:
        if tid not in TARGETS or tid == "local":
            raise RuntimeFault("choose a runtime other than 'local' to deploy to")
        vals = self.config()["targets"].get(tid, {})
        if not vals.get("enabled"):
            raise RuntimeFault(f"{TARGETS[tid].label} is switched off (Settings > Runtimes)")
        try:
            return self.factory(tid, {k: v for k, v in vals.items() if k != "enabled"}, self._secrets().get(tid, {}))
        except TargetError as e:
            raise RuntimeFault(str(e))

    def test(self, tid: str) -> dict:
        try:
            return self._target(tid).check()
        except TargetError as e:
            return dict(ok=False, detail=str(e))
        except RuntimeFault as e:
            return dict(ok=False, detail=str(e))

    # ------------------------------------------------------------------------------------------------------------------- releases
    def _row(self, rid: str):
        r = self.db.one("SELECT * FROM runtime_releases WHERE id=?", (rid,))
        if not r:
            raise RuntimeFault(f"no such deployment '{rid}'")
        return r

    @staticmethod
    def public_release(r: Any) -> dict:
        ref = json.loads(r["ref"])
        ref.pop("token", None)
        return dict(id=r["id"], agent_id=r["agent_id"], target=r["target"], version=r["version"], state=r["state"], live=bool(r["live"]), shadow=bool(r["shadow"]),
                    spec_hash=r["spec_hash"], image=r["image"], ref=ref, prev_id=r["prev_id"], health=json.loads(r["health"]), detail=r["detail"],
                    created=r["created"], updated=r["updated"])

    def releases(self, agent_id: str) -> list[dict]:
        return [self.public_release(r) for r in self.db.query("SELECT * FROM runtime_releases WHERE agent_id=? AND state!='deleted' ORDER BY created DESC", (agent_id,))]

    def live(self, agent_id: str) -> Any:
        return self.db.one("SELECT * FROM runtime_releases WHERE agent_id=? AND live=1 AND state!='deleted'", (agent_id,))

    def _agent(self, agent_id: str) -> AgentDef:
        a = self.agents.get(agent_id)
        if a is None:
            raise RuntimeFault(f"no such agent '{agent_id}'")
        return a

    def _save(self, rid: str, **cols: Any) -> None:
        cols["updated"] = time.time()
        self.db.execute(f"UPDATE runtime_releases SET {','.join(k + '=?' for k in cols)} WHERE id=?", (*cols.values(), rid))

    def _model(self, a: AgentDef) -> str:
        try:
            return self.model_for(a)
        except Exception as e:  # noqa: BLE001 - no provider / no key
            raise RuntimeFault(f"choose a model provider with a key for this agent first ({str(e)[:120]})")

    def _names(self, tid: str, a: AgentDef, version: int) -> str:
        base = a.id
        return {"agentcore": f"as_{base}"[:48], "foundry": f"as-{base.replace('_', '-')}"[:63], "vertex": f"as-{base.replace('_', '-')}-v{version}"[:63],
                "docker": f"agent-studio-rt-{base}-v{version}"}[tid]

    def plan(self, agent_id: str, tid: str, image: str | None = None) -> dict:
        a = self._agent(agent_id)
        if tid not in TARGETS or tid == "local":
            raise RuntimeFault("choose a runtime other than 'local' to deploy to")
        spec = agent_spec(a, self._model(a))
        h = spec_hash(spec)
        cfg = self.config()
        live = self.db.one("SELECT * FROM runtime_releases WHERE agent_id=? AND target=? AND live=1 AND state!='deleted'", (agent_id, tid))
        img = image or (f"agent-studio-rt-{agent_id}:{h[:12]}" if tid == "docker" else default_image(cfg["image_template"], agent_id, h) if cfg["image_template"] else "")
        folder = self.home / "runtime-bundles" / agent_id / h[:12]
        files = write_bundle(folder, spec)
        report = compat(a, tid)
        problem = ""
        try:
            self._target(tid)
        except RuntimeFault as e:
            problem = str(e)
        cls = TARGETS[tid]
        return dict(agent=agent_id, target=tid, info=_info(cls), compat=report, blocked=any(c["level"] == "block" for c in report), spec_hash=h,
                    diff=spec_diff(json.loads(live["spec"]) if live else None, spec), live=self.public_release(live) if live else None, shadow=bool(live),
                    image=img, image_ok=image_ok(img), bundle=dict(folder=str(folder), files=files), commands=commands(tid, img, str(folder), cfg["targets"].get(tid, {})) if img else [],
                    cost_note=cls.cost_note, side_effects=list(cls.side_effects), ready=not problem, problem=problem)

    def deploy(self, agent_id: str, tid: str, image: str | None = None, confirm: bool = False) -> dict:
        p = self.plan(agent_id, tid, image)
        if not confirm:
            raise RuntimeFault(f"this will create resources on {p['info']['label']} and may cost money ({p['cost_note']}). Confirm to continue.")
        if p["blocked"]:
            raise RuntimeFault("this agent cannot be deployed there: " + " ".join(c["text"] for c in p["compat"] if c["level"] == "block"))
        if not p["ready"]:
            raise RuntimeFault(p["problem"])
        if not p["image_ok"]:
            raise RuntimeFault("give the image to deploy (the registry address you pushed it to), or set an image template in Settings > Runtimes")
        target = self._target(tid)
        a = self._agent(agent_id)
        spec = agent_spec(a, self._model(a))
        n = (self.db.one("SELECT COALESCE(MAX(version),0) AS n FROM runtime_releases WHERE agent_id=? AND target=?", (agent_id, tid))["n"]) + 1
        live = self.db.one("SELECT * FROM runtime_releases WHERE agent_id=? AND target=? AND live=1 AND state!='deleted'", (agent_id, tid))
        env = dict(self._secrets().get("model_env", {}))
        try:
            ref = target.deploy(spec, self._names(tid, a, n), p["image"], env, json.loads(live["ref"]) if live else None, p["bundle"]["folder"])
        except TargetError as e:
            raise RuntimeFault(str(e))
        rid, now = uuid.uuid4().hex[:12], time.time()
        self.db.execute("INSERT INTO runtime_releases(id,agent_id,target,version,state,live,shadow,spec,spec_hash,image,ref,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, agent_id, tid, n, "deploying", 0, int(bool(live)), json.dumps(spec), p["spec_hash"], p["image"], json.dumps(ref), now, now))
        return self.public_release(self._row(rid))

    def refresh(self, rid: str) -> dict:
        """Ask the platform how the release is doing; a first release goes live by itself once it is ready."""
        r = self._row(rid)
        if r["state"] in ("deleted", "retired"):
            return self.public_release(r)
        try:
            st = self._target(r["target"]).status(json.loads(r["ref"]))
        except (TargetError, RuntimeFault) as e:
            raise RuntimeFault(str(e))
        state = {"ready": "ready", "failed": "failed", "creating": "deploying", "gone": "deleted"}[st["state"]]
        self._save(rid, state=state, detail=st.get("detail", ""), **({"ref": json.dumps(st["ref"])} if st.get("ref") else {}))
        r = self._row(rid)
        if state == "ready" and not r["shadow"] and not r["live"]:
            return self.promote(rid, confirm=True)
        return self.public_release(r)

    def health(self, rid: str, smoke: bool = False) -> dict:
        r = self.refresh(rid)
        out: dict = dict(ok=r["state"] == "ready", state=r["state"], detail=r["detail"], at=time.time())
        if smoke and out["ok"]:
            t0 = time.time()
            try:
                row = self._row(rid)
                tgt = self._target(row["target"])
                ref = json.loads(row["ref"])
                if not ref.get("slot"):
                    ref = tgt.route(ref, "shadow")              # make it reachable without sending it traffic
                    self._save(rid, ref=json.dumps(ref))
                answer = tgt.invoke(ref, SMOKE)
                out.update(ok=bool(answer.strip()), detail=("answered: " + answer.strip()[:120]) if answer.strip() else "it answered with nothing", ms=int((time.time() - t0) * 1000), smoke=True)
            except (TargetError, RuntimeFault) as e:
                out.update(ok=False, detail=str(e), smoke=True)
        self._save(rid, health=json.dumps(out))
        return out

    def _switch(self, new: Any, old: Any | None) -> None:
        self.db.execute("UPDATE runtime_releases SET live=0, state='retired', updated=? WHERE agent_id=? AND target=? AND live=1", (time.time(), new["agent_id"], new["target"]))
        self._save(new["id"], live=1, shadow=0, state="ready", prev_id=old["id"] if old else None)
        self.agents.update(new["agent_id"], {"runtime": new["target"]})

    def promote(self, rid: str, confirm: bool = False) -> dict:
        if not confirm:
            raise RuntimeFault("switching traffic changes which version answers. Confirm to continue.")
        r = self._row(rid)
        if r["state"] != "ready":
            raise RuntimeFault(f"this version is '{r['state']}', not ready: check its health first")
        old = self.db.one("SELECT * FROM runtime_releases WHERE agent_id=? AND live=1 AND state!='deleted'", (r["agent_id"],))
        if old and old["id"] == rid:
            raise RuntimeFault("this version is already live")
        try:
            ref = self._target(r["target"]).route(json.loads(r["ref"]), "live")
        except TargetError as e:
            raise RuntimeFault(str(e))
        self._save(rid, ref=json.dumps(ref))
        self._switch(self._row(rid), old)
        return self.public_release(self._row(rid))

    def rollback(self, agent_id: str, confirm: bool = False) -> dict:
        if not confirm:
            raise RuntimeFault("rolling back switches traffic to the previous version. Confirm to continue.")
        cur = self.live(agent_id)
        if cur is None or not cur["prev_id"]:
            raise RuntimeFault("there is no earlier version to roll back to")
        prev = self._row(cur["prev_id"])
        if prev["state"] in ("deleted", "failed"):
            raise RuntimeFault("the earlier version no longer exists")
        try:
            tgt = self._target(prev["target"])
            ref = tgt.route(json.loads(prev["ref"]), "live")
        except TargetError as e:
            raise RuntimeFault(str(e))
        self._save(prev["id"], ref=json.dumps(ref))
        self._switch(self._row(prev["id"]), cur)
        return self.public_release(self._row(prev["id"]))

    def delete_release(self, rid: str, confirm: bool = False) -> dict:
        if not confirm:
            raise RuntimeFault("this removes the deployment from the platform and cannot be undone. Confirm to continue.")
        r = self._row(rid)
        others = self.db.one("SELECT COUNT(*) AS n FROM runtime_releases WHERE agent_id=? AND target=? AND state!='deleted' AND id!=?", (r["agent_id"], r["target"], rid))["n"]
        if r["live"] and others:
            raise RuntimeFault("this version is live: switch traffic to another version (or roll back) before deleting it")
        try:
            self._target(r["target"]).delete(json.loads(r["ref"]), whole=not others)
        except TargetError as e:
            if e.code != "notfound":
                raise RuntimeFault(str(e))
        self._save(rid, state="deleted", live=0, shadow=0)
        if r["live"]:
            self.agents.update(r["agent_id"], {"runtime": "local"})
        return self.public_release(self._row(rid))

    # ---------------------------------------------------------------------------------------------------------- editing a deployed agent
    def migration(self, agent_id: str) -> dict:
        """After an edit: is the deployed agent behind the saved one, and what would the target lose?"""
        a = self._agent(agent_id)
        base = dict(agent=agent_id, runtime=a.runtime, live=None, stale=False, changes=[], compat=compat(a, a.runtime), candidate=None)
        if a.runtime == "local":
            return {**base, "message": "This agent runs on this PC. Pick a runtime in its editor to deploy it."}
        live = self.live(agent_id)
        if live is None:
            return {**base, "message": f"This agent is set to run on {a.runtime} but has no live deployment: it runs on this PC until one is deployed."}
        changes = spec_diff(json.loads(live["spec"]), agent_spec(a, self._model(a)))
        cand = self.db.one("SELECT * FROM runtime_releases WHERE agent_id=? AND shadow=1 AND live=0 AND state IN ('deploying','ready') ORDER BY created DESC", (agent_id,))
        msg = ("You changed this agent after it was deployed: the live version does not have the change yet. Deploy it as a shadow version, test it, then switch."
               if changes else "The live deployment matches this agent.")
        return {**base, "live": self.public_release(live), "stale": bool(changes), "changes": changes, "candidate": self.public_release(cand) if cand else None, "message": msg}

    def invoke(self, agent_id: str, text: str) -> str:
        live = self.live(agent_id)
        if live is None:
            raise RuntimeFault("this agent has no live deployment")
        try:
            return self._target(live["target"]).invoke(json.loads(live["ref"]), text)
        except TargetError as e:
            raise RuntimeFault(str(e))

    def summary(self) -> dict:
        """For the AI-BOM and the discovery catalog: which agent runs where. No credentials, no addresses."""
        out: dict = {}
        for a in self.agents.list():
            if a.runtime != "local":
                live = self.live(a.id)
                out[a.id] = dict(target=a.runtime, deployed=bool(live), version=live["version"] if live else None)
        return out


def _info(cls: type) -> dict:
    return dict(id=cls.id, label=cls.label, docs=cls.docs, remote=cls.remote, cost_note=cls.cost_note, platform=cls.platform, side_effects=list(cls.side_effects), fields=cls.fields)


def model_resolver(providers: Any) -> Callable[[AgentDef], str]:
    """agent -> the LiteLLM model string its provider/tier/model choose (no key is read into the result)."""
    from ..providers import litellm_target, tier_model

    def resolve(a: AgentDef) -> str:
        prov = providers.get(a.provider) if a.provider else next((x for x in providers.list() if x.enabled), None)
        if prov is None:
            raise ValueError("no model provider is enabled")
        return litellm_target(prov, tier_model(prov, a.tier, a.model))[0]
    return resolve
