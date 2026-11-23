"""Builds everything that would be published: the ARD manifest, the AI-BOMs it points to, the skill files, the signing key.

One `Site` object feeds the local preview, the discovery server and the zip export, so the digest in a trust manifest always
matches the bytes served at that URL. Entries follow ARD v0.91 (https://github.com/ards-project/ard-spec):

* identifier `urn:air:<publisher>:agent-studio:<kind>:<id>`, publisher = the configured domain (publishing is refused without one)
* `type` is a media type. Skills use `application/agent-skills+md` and BOMs use `application/vnd.cyclonedx+json`. Agents and MCP
  servers use `application/vnd.agent-studio.<kind>+json` with the description inline: ARD's standard card types
  (`a2a-agent-card`, `mcp-server-card`) describe a server that speaks that protocol at a URL, which Agent Studio's local agents
  and stdio MCP servers do not, so claiming them would be wrong.
* bills of materials are NOT an ARD field. They are separate CycloneDX documents, referenced from `trustManifest.attestations`
  (type `SBOM`, with the sha256 of the document) and also published as entries of their own.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from ..aibom import builder
from ..aibom.redact import sha256_hex
from ..aibom.validate import validate_bom, validate_manifest
from ..skills import render_skill
from . import signing
from .settings import DiscoverySettings, SettingsError, valid_publisher

DATA_PRODUCT_TYPE = "application/vnd.agent-studio.data-product+json"
CYCLONEDX_TYPE = "application/vnd.cyclonedx+json"
AGENT_TYPE = "application/vnd.agent-studio.agent+json"
MCP_TYPE = "application/vnd.agent-studio.mcp-server+json"
SKILL_TYPE = "application/agent-skills+md"
WELL_KNOWN = "/.well-known/ard.json"
LEGACY = "/.well-known/ai-catalog.json"        # the name ARD's predecessor used; consumers MAY still look there
JWKS = "/.well-known/jwks.json"
TTL_S = 30


@dataclass
class Artifact:
    path: str                                  # URL path without a leading slash, e.g. ard/bom/system.json
    content_type: str
    data: bytes
    sha256: str
    kind: str                                  # manifest | bom | skill | jwks | page
    serial: str = ""

    @property
    def digest(self) -> str:
        return f"sha256:{self.sha256}"


@dataclass
class Site:
    level: str
    publisher: str
    base_url: str
    manifest: dict
    files: dict[str, Artifact]
    entries: list[dict]                        # [{identifier, type, displayName, url, kind, path, digest}]
    built_at: float = 0.0
    signed: bool = False
    problems: list[str] = field(default_factory=list)


def _artifact(path: str, content_type: str, data: bytes, kind: str, serial: str = "") -> Artifact:
    return Artifact(path, content_type, data, sha256_hex(data), kind, serial)


def pmap(item: dict) -> dict:
    """A service/component's agentstudio: properties as a dict (JSON values decoded)."""
    out = {}
    for p in item.get("properties", []):
        if not p["name"].startswith(builder.P):
            continue
        v = p["value"]
        if v[:1] in "[{":
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[p["name"][len(builder.P):]] = v
    return out


def first_sentence(text: str, limit: int = 140) -> str:
    s = re.split(r"(?<=[.!?])\s", (text or "").strip(), 1)[0].rstrip(".")
    return (s[: limit - 1] + "…") if len(s) > limit else s


class Publisher:
    """Turns settings + the live system into a Site."""

    def __init__(self, state: Any, settings: DiscoverySettings, signer: signing.Signer | None = None):
        self.s, self.settings, self.signer = state, settings, signer or signing.Signer()

    # ------------------------------------------------------------------ pieces
    def _trust(self, cfg: dict, atts: list[Artifact]) -> dict:
        pub = cfg["publisher"]
        signed = cfg["sign"] and self.signer.exists()
        trust: dict = {"identity": f"https://{pub}", "identityType": "https",
                       "trustSchema": {"identifier": signing.SCHEME_ID if signed else "agent-studio:self-declared",
                                       "version": signing.SCHEME_VERSION,
                                       "verificationMethods": ["jws-detached-eddsa"] if signed else ["none"]}}
        if signed:
            trust["trustSchema"]["jwksUri"] = f"{self.settings.base_url(cfg)}{JWKS}"
        if atts:
            trust["attestations"] = [{"type": "SBOM", "uri": f"{self.settings.base_url(cfg)}/{a.path}", "mediaType": CYCLONEDX_TYPE, "digest": a.digest}
                                     for a in atts]
        if signed:
            trust["signature"] = self.signer.sign(trust)
        return trust

    def build(self, previous: "Site | None" = None) -> Site:
        cfg = self.settings.get()
        bad = valid_publisher(cfg["publisher"])
        if bad:
            raise SettingsError(bad)
        pub, level, base = cfg["publisher"], cfg["level"], self.settings.base_url(cfg)
        prev_files = previous.files if previous and previous.level == level and previous.publisher == pub else {}
        full = builder.build_full(self.s, level, True, publisher=pub)
        files: dict[str, Artifact] = {}

        def bom_file(path: str, doc: dict) -> Artifact:
            old = prev_files.get(path)
            if old is not None and old.serial == doc["serialNumber"]:
                files[path] = old                      # unchanged content: keep the same bytes (and timestamp) so digests stay put
            else:
                files[path] = _artifact(path, CYCLONEDX_TYPE, builder.serialize(doc), "bom", doc["serialNumber"])
            return files[path]

        updated = full["metadata"]["timestamp"]
        sw = cfg["publish"]
        entries: list[dict] = []

        def entry(kind: str, ident: str, name: str, mtype: str, *, url: str | None = None, data: dict | None = None, description: str | None = None,
                  queries: list[str], tags: list[str], atts: list[Artifact] | None = None, capabilities: list[str] | None = None) -> None:
            e: dict = {"identifier": f"urn:air:{pub}:agent-studio:{kind}:{ident}", "displayName": name, "type": mtype}
            if url:
                e["url"] = url
            else:
                e["data"] = data or {}
            e["representativeQueries"] = queries[:5]
            e["tags"] = sorted(set(tags))
            if capabilities:
                e["capabilities"] = capabilities
            if description:
                e["description"] = description
            e["updatedAt"] = updated
            e["metadata"] = {"redactionLevel": level, "generator": builder.GENERATOR}
            e["trustManifest"] = self._trust(cfg, atts or [])
            entries.append(e)

        def slug(text: str) -> str:
            return builder.clean_id(text.replace(":", "-"))

        roots = builder.scope_roots(full)
        by_ref = {x["bom-ref"]: x for x in [*full.get("services", []), *full["components"]]}
        sysbom = bom_file("ard/bom/system.json", full) if sw["bom_system"] else None
        per: dict[str, Artifact] = {}

        def scoped(group: str, flag: str, prefix: str) -> None:
            if not sw[flag]:
                return
            for ref in roots[group]:
                per[ref] = bom_file(f"ard/bom/{prefix}/{slug(ref.split(':', 1)[-1] if ref.startswith(('agent:', 'mcp:')) else ref)}.json",
                                    builder.cut(full, ref))

        scoped("agents", "bom_agents", "agent")
        scoped("mcp", "bom_mcp", "mcp")
        scoped("models", "bom_models", "model")

        if sw["agents"]:
            for ref in roots["agents"]:
                sv = by_ref[ref]
                p = pmap(sv)
                aid = ref.split(":", 1)[1]
                desc = sv.get("description")
                data = {"id": aid, "name": sv["name"], "description": desc, "tier": p.get("tier"), "provider": p.get("provider"), "model": p.get("model"),
                        "tools": p.get("tools", []), "mcp": p.get("mcp", []), "skills": p.get("resolved-skills", []), "sub_agents": p.get("sub-agents", []),
                        "knowledge_bases": p.get("knowledge-bases", []), "can_spawn": p.get("can-spawn") == "true", "builtin": p.get("builtin") == "true",
                        "framework": p.get("framework"), "api_access": p.get("api-access") == "true", "a2a": p.get("a2a-enabled-for-agent") == "true",
                        "agui": {"components": p.get("agui-components-off") != "true", "endpoint": p.get("agui-endpoint-off") != "true"},
                        "runtime": p.get("runtime"), "runtime_deployed": (p.get("runtime-deployed") == "true") if p.get("runtime") else None, "sandbox": p.get("sandbox"),
                        "data_source_kinds": p.get("data-source-kinds"), "data_sources": p.get("data-sources"), "data_products": p.get("data-products"),
                        "data_classification": p.get("data-classification"),
                        "security_score": int(p["security-score"]) if p.get("security-score") is not None else None,
                        "identity": {k: v for k, v in (("spiffe_id", p.get("identity-spiffe-id")), ("scopes", p.get("identity-scopes")),
                                                       ("delegates", p.get("identity-delegates"))) if v} or None}          # who the agent is - never a token or key
                entry("agent", slug(aid), sv["name"], AGENT_TYPE, data={k: v for k, v in data.items() if v not in (None, "", [])},
                      description=desc, capabilities=[t for t in p.get("tools", [])][:20],
                      queries=([f"Find an agent for: {first_sentence(desc)}"] if desc else [f"Use the {sv['name']} agent"]) + [f"Delegate a task to the {sv['name']} agent"],
                      tags=["agent", f"tier:{p.get('tier', 'flash')}"], atts=[per[ref]] if ref in per else [])
            used_by: dict[str, list[str]] = {}
            for dep in full["dependencies"]:
                if dep["ref"].startswith("agent:"):
                    for t in dep.get("dependsOn", []):
                        if t.startswith("acp:"):
                            used_by.setdefault(t, []).append(dep["ref"].split(":", 1)[1])
            for c in full["components"]:                           # coding agents that run outside Agent Studio (Claude Code, Cursor, ...): who may call them, what they are
                if c["bom-ref"].startswith("acp:"):
                    p = pmap(c)
                    eid = c["bom-ref"].split(":", 1)[1]
                    d = {"id": eid, "name": c["name"], "description": c.get("description"), "external": True, "launcher": p.get("kind"), "enabled": p.get("enabled") == "true",
                         "installed": p.get("installed") == "true", "billing": p.get("billing"), "used_by": sorted(used_by.get(c["bom-ref"], [])),
                         "governed_data_access": False}          # it runs outside Agent Studio: what it can read is up to its own account
                    entry("agent", slug("external-" + eid), c["name"], AGENT_TYPE, data={k: v for k, v in d.items() if v not in (None, "", [])}, description=c.get("description"),
                          queries=[f"Which external coding agent can I use: {c['name']}?", f"Which agents may call {c['name']}?"], tags=["agent", "external"])
            for ref in [x["bom-ref"] for x in full.get("services", []) if x["bom-ref"].startswith("dataproduct:")]:
                sv = by_ref[ref]
                p = pmap(sv)
                pid = ref.split(":", 1)[1]
                readers = sorted(dep["ref"].split(":", 1)[1] for dep in full["dependencies"] if dep["ref"].startswith("agent:") and ref in dep.get("dependsOn", []))
                d = {"id": pid, "name": sv["name"], "description": sv.get("description"), "classification": p.get("classification"), "contains_pii": p.get("contains-pii") == "true",
                     "source_kind": p.get("source-kind"), "objects": p.get("objects"), "objects_count": p.get("objects-count"), "owner": p.get("owner"), "tags": p.get("tags"),
                     "readable_by": readers}
                entry("data-product", slug(pid), sv["name"], DATA_PRODUCT_TYPE, data={k: v for k, v in d.items() if v not in (None, "", [])}, description=sv.get("description"),
                      queries=[f"Which data product holds {sv['name']}?", f"Which agents can read {sv['name']}?"], tags=["data-product", f"classification:{p.get('classification', 'internal')}"])
        if sw["mcp"]:
            for ref in roots["mcp"]:
                sv = by_ref[ref]
                p = pmap(sv)
                mid = ref.split(":", 1)[1]
                pkg = next((d for d in full["dependencies"] if d["ref"] == ref), {}).get("dependsOn", [])
                purls = [x for x in pkg if x.startswith("pkg:npm") or x.startswith("pkg:pypi")]
                data = {"id": mid, "name": sv["name"], "description": sv.get("description"), "transport": p.get("transport"), "command": p.get("command"),
                        "args": p.get("args"), "requires_env": p.get("requires_env"), "endpoints": sv.get("endpoints"), "packages": purls,
                        "enabled": p.get("enabled") == "true"}
                entry("mcp", slug(mid), sv["name"], MCP_TYPE, data={k: v for k, v in data.items() if v not in (None, "", [])}, description=sv.get("description"),
                      queries=[f"Which MCP server provides {sv['name']} tools?", f"Connect an agent to {sv['name']}"],
                      tags=["mcp", f"transport:{p.get('transport', 'stdio')}"], atts=[per[ref]] if ref in per else [])
        if sw["skills"]:
            for sk in self.s.skills.list():
                if sk.source != "builtin" and level == "public":
                    continue                           # a user's own skills are shared from the internal level up
                path = f"ard/skills/{sk.name}/SKILL.md"
                files[path] = _artifact(path, "text/markdown; charset=utf-8", render_skill(sk).encode("utf-8"), "skill")
                entry("skill", slug(sk.name), sk.name, SKILL_TYPE, url=f"{base}/{path}", description=sk.description,
                      queries=[f"How do I {first_sentence(sk.description).lower()}", f"Load the {sk.name} skill"], tags=["skill", f"group:{sk.group}"])
        bom_q = ["Show the AI bill of materials of this system", "Which models, dependencies and licences does this agent system use?"]
        if sysbom:
            entry("bom", "system", "Agent Studio: AI bill of materials", CYCLONEDX_TYPE, url=f"{base}/{sysbom.path}",
                  description="CycloneDX 1.7 AI-BOM of the whole system: dependencies, models, agents, MCP servers, skills, knowledge bases and data flows.",
                  queries=bom_q, tags=["bom", "cyclonedx", "ai-bom"], atts=[sysbom])
        for ref, art in per.items():
            kind = "agent" if ref.startswith("agent:") else "mcp" if ref.startswith("mcp:") else "model"
            nm = by_ref[ref]["name"]
            entry("bom", f"{kind}:{slug(ref.split(':', 1)[-1] if kind != 'model' else ref)}", f"AI-BOM: {nm}", CYCLONEDX_TYPE, url=f"{base}/{art.path}",
                  description=f"CycloneDX 1.7 AI-BOM of the {kind} '{nm}' and everything it depends on.",
                  queries=[f"What does {nm} depend on?", f"AI bill of materials for {nm}"], tags=["bom", "cyclonedx", kind], atts=[art])

        manifest: dict = {"entries": entries}
        files[WELL_KNOWN.lstrip("/")] = _artifact(WELL_KNOWN.lstrip("/"), "application/json", (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode(), "manifest")
        files[LEGACY.lstrip("/")] = _artifact(LEGACY.lstrip("/"), "application/json", files[WELL_KNOWN.lstrip("/")].data, "manifest")
        signed = bool(cfg["sign"] and self.signer.exists())
        if signed:
            jwk = self.signer.public_jwk()
            files[JWKS.lstrip("/")] = _artifact(JWKS.lstrip("/"), "application/jwk-set+json", (json.dumps({"keys": [jwk]}, indent=2) + "\n").encode(), "jwks")
        problems = []
        if cfg["sign"] and not signed:
            problems.append("signing is on but no key exists: generate one, or entries are published unsigned")
        listing = [{"identifier": e["identifier"], "type": e["type"], "displayName": e["displayName"],
                    "url": e.get("url") or f"{base}{WELL_KNOWN}", "inline": "data" in e,
                    "digests": [a["digest"] for a in e["trustManifest"].get("attestations", [])]} for e in entries]
        site = Site(level, pub, base, manifest, files, listing, datetime.now(timezone.utc).timestamp(), signed, problems)
        files["index.html"] = _artifact("index.html", "text/html; charset=utf-8", self.index_html(site).encode("utf-8"), "page")
        return site

    @staticmethod
    def index_html(site: Site) -> str:
        rows = "\n".join(f'<li><code>{html.escape(e["identifier"])}</code> &mdash; {html.escape(e["displayName"])} '
                         f'<small>({html.escape(e["type"])})</small></li>' for e in site.entries)
        return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
                f"<title>{html.escape(site.publisher)} - agentic resources</title>\n"
                f'<link rel="ard" href="{WELL_KNOWN}">\n'
                "</head><body style=\"font-family:system-ui;max-width:48rem;margin:2rem auto;padding:0 1rem\">\n"
                f"<h1>{html.escape(site.publisher)}</h1>\n<p>Agentic resources published with Agent Studio. "
                f'Machine-readable catalog: <a href="{WELL_KNOWN}">{WELL_KNOWN}</a> (Agentic Resource Discovery v0.91).</p>\n<ul>\n{rows}\n</ul>\n</body></html>\n')

    # ------------------------------------------------------------------ checks
    def validate(self, site: Site) -> dict:
        """Validate the manifest and every BOM in the site against the official schemas."""
        man = validate_manifest(site.manifest, site.publisher)
        boms = {}
        for path, art in site.files.items():
            if art.kind == "bom":
                boms[path] = validate_bom(json.loads(art.data))
        ok = man["ok"] and all(v["ok"] for v in boms.values())
        return {"ok": ok, "manifest": man, "boms": {k: {kk: vv for kk, vv in v.items() if kk != "errors"} | {"errors": v["errors"][:10]} for k, v in boms.items()}}


class SiteCache:
    """Rebuilds the site at most every TTL_S seconds; unchanged BOMs keep their exact bytes."""

    def __init__(self, publisher: Publisher, ttl: float = TTL_S):
        self.p, self.ttl = publisher, ttl
        self.site: Site | None = None
        self.key = ""
        self.lock = Lock()

    def get(self, force: bool = False) -> Site:
        import time
        with self.lock:
            cfg = self.p.settings.get()
            key = json.dumps(cfg, sort_keys=True) + str(self.p.signer.exists())
            if force or self.site is None or key != self.key or time.time() - self.site.built_at >= self.ttl:
                self.site = self.p.build(self.site)
                self.key = key
            return self.site
