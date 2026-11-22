"""Builds the AI bill of materials (CycloneDX 1.7) from the live registries.

Nothing is hard-coded: providers, models, agents, MCP servers, skills, external agents, knowledge bases, vector stores and
local models are read from the same stores the app runs on, so anything added later (a new MCP server, a new local model)
shows up on its own. Every value passes through a `Redactor` (see redact.py) before it is written.

Mapping:  agents, MCP servers, providers and vector stores -> `services`;  models -> `machine-learning-model` components;
skills -> `file` components (content hash);  knowledge bases -> `data` components (hashes only, never content);
libraries -> `library`;  coding harnesses -> `application`;  Docker images -> `container`.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import d1_spec, ts_spec
from ..db import REPO_ROOT, data_dir
from ..harness import sandbox
from ..harness.tools import ALL_TOOLS
from ..pricing import price_for
from ..providers import CATALOG, network_scope, price_of, tier_model
from ..skills import GROUP_PREFIX, covers
from . import deps
from .redact import LEVELS, Redactor, default_roots, sha256_hex

SPEC_VERSION = "1.7"
SERIAL_NS = uuid.UUID("6b1d8a52-3c0e-5f6a-9d44-0a7e1f5c2b90")      # fixed namespace for stable serial numbers
GENERATOR = "agent-studio-aibom"
GENERATOR_VERSION = "1.0.0"
P = "agentstudio:"
HASHES = json.loads((Path(__file__).parent / "model_hashes.json").read_text("utf-8"))

LIMITATIONS = [
    "Python packages carry no package-level hash: installed wheels do not record one, so none is invented.",
    "Docker image digests are not recorded: images are listed by their pinned tag.",
    "Hosted models (OpenAI, Anthropic, Gemini, ...) are listed by id with their configured price; their weights, training data "
    "and licences are not known to Agent Studio.",
    "Dependencies of MCP server packages fetched with npx/uvx at run time are not resolved; only the pinned package is listed.",
    "The installed version of external coding harnesses (Claude Code, Codex, Aider, OpenHands) is not queried; adapter packages "
    "are listed at the version Agent Studio pins.",
    "No vulnerability data is included: run pip-audit / npm audit and merge its output if you need it.",
]


def app_version() -> str:
    try:
        m = re.search(r'FastAPI\([^)]*version="([^"]+)"', (Path(__file__).resolve().parents[1] / "main.py").read_text("utf-8"))
        return m.group(1) if m else "0.0.0"
    except OSError:
        return "0.0.0"


def prop(name: str, value: Any) -> dict | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, bool):
        value = "true" if value else "false"
    elif isinstance(value, (list, dict, tuple)):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return {"name": P + name, "value": str(value)}


def props(**kw: Any) -> list[dict]:
    return [p for k, v in kw.items() if (p := prop(k.replace("_", "-"), v))]


def clean_id(text: str, keep: str = "A-Za-z0-9._-") -> str:
    """A string usable inside a URN segment: other characters become '-', with a short hash when anything changed."""
    out = re.sub(f"[^{keep}]+", "-", text).strip("-") or "x"
    return out if out == text else f"{out}-{sha256_hex(text)[:6]}"


def purl_docker(image: str) -> tuple[str, str, str]:
    """('name', 'version', purl) for 'qdrant/qdrant:v1.20.0' or 'python:3.12-slim'."""
    name, _, tag = image.partition(":")
    return name, tag or "latest", f"pkg:docker/{name}@{tag or 'latest'}"


class Build:
    """Accumulates components, services and edges, then renders the BOM."""

    def __init__(self, state: Any, level: str, now: datetime | None = None):
        self.s = state
        self.level = level
        self.now = now or datetime.now(timezone.utc)
        ws = None
        try:
            ws = state.chat.workspace_for(None)
        except Exception:  # noqa: BLE001 - a broken workspace setting must not stop the BOM
            pass
        self.r = Redactor(level, default_roots(ws, data_dir(), REPO_ROOT))
        self.comps: dict[str, dict] = {}
        self.services: dict[str, dict] = {}
        self.edges: dict[str, set[str]] = {}
        self.app_ref = "agent-studio"
        self.models_by_kind: dict[tuple[str, str], str] = {}
        self.app_props: list[dict] = []                    # extra facts about the application itself (set by the sections)

    # ------------------------------------------------------------------ registry helpers
    def comp(self, c: dict) -> str:
        c = {k: v for k, v in c.items() if v not in (None, "", [])}
        self.comps.setdefault(c["bom-ref"], c)
        self.edges.setdefault(c["bom-ref"], set())
        return c["bom-ref"]

    def service(self, sv: dict) -> str:
        sv = {k: v for k, v in sv.items() if v not in (None, "", [])}
        self.services.setdefault(sv["bom-ref"], sv)
        self.edges.setdefault(sv["bom-ref"], set())
        return sv["bom-ref"]

    def edge(self, a: str, *bs: str) -> None:
        self.edges.setdefault(a, set()).update(b for b in bs if b)

    def user_text(self, text: str, builtin: bool) -> str | None:
        """Free text written by the user is shared only from `internal` up; text that ships with the app is always shared."""
        return self.r.text(text) if text and (builtin or self.r.internal) else None

    # ------------------------------------------------------------------ models
    def model(self, kind: str, model_id: str, role: str, task: str = "text-generation", p=None) -> str:
        """A model component. `p` (the provider) matters for custom endpoints: two of them can serve the same model name."""
        group = p.id if p is not None and p.custom else kind
        ref = self.models_by_kind.get((group, model_id))
        if ref is None:
            ref = f"model:{group}:{model_id}"
            self.models_by_kind[(group, model_id)] = ref
            cat = CATALOG.get(kind, {})
            pr = price_of(p, model_id, f"{cat.get('prefix', '')}{model_id}") if p is not None else price_for(kind, model_id, f"{cat.get('prefix', '')}{model_id}")
            hosted = network_scope(p.api_base) == "internet" if p is not None and p.custom else kind != "ollama"
            self.comp({"type": "machine-learning-model", "bom-ref": ref, "name": model_id, "group": group,
                       "supplier": {"name": p.label if p is not None and p.custom else cat.get("label", kind)},
                       "modelCard": {"modelParameters": {"task": task, "inputs": [{"format": "text"}], "outputs": [{"format": "text"}]}},
                       "properties": props(provider_kind=kind, hosted=hosted, roles=[role], price_input_usd_per_million_tokens=round(pr.input_per_m, 6),
                                           price_output_usd_per_million_tokens=round(pr.output_per_m, 6), price_known=pr.known, price_source=pr.source)})
        else:
            c = self.comps[ref]
            roles = set(json.loads(next(p["value"] for p in c["properties"] if p["name"] == P + "roles")))
            roles.add(role)
            for p in c["properties"]:
                if p["name"] == P + "roles":
                    p["value"] = json.dumps(sorted(roles))
        return ref

    # ------------------------------------------------------------------ sections
    def application(self) -> dict:
        s = self.s
        sets = {k: s.db.get_setting(k) for k in ("sandbox_network", "agent_spawning_enabled", "agents_can_call_external", "thinking_level")}
        return {"type": "application", "bom-ref": self.app_ref, "name": "Agent Studio", "version": app_version(),
                "description": "Model-agnostic agent workbench: a FastAPI + Google ADK backend and a Next.js frontend.",
                "properties": props(python=deps.python_version(), builtin_tools=ALL_TOOLS, sandbox_network=sets["sandbox_network"] == "1",
                                    agent_spawning_enabled=sets["agent_spawning_enabled"] != "0",
                                    agents_can_call_external=sets["agents_can_call_external"] != "0", thinking_level=sets["thinking_level"] or "medium",
                                    redaction_level=self.level) + self.app_props}

    def libraries(self) -> None:
        backend = Path(__file__).resolve().parents[2]
        graphs = []
        req = backend / "requirements.txt"
        if req.is_file():
            graphs.append(deps.cached(("py", str(req), req.stat().st_mtime_ns, sys.prefix), lambda: deps.python_graph(req)))
        for lock, label in ((REPO_ROOT / "frontend" / "package-lock.json", "frontend"), (REPO_ROOT / "package-lock.json", "tooling")):
            if lock.is_file():
                graphs.append(deps.cached(("npm", str(lock), lock.stat().st_mtime_ns), lambda lock=lock, label=label: deps.npm_graph(lock, label)))
        g = deps.merge(*graphs)
        for ref, c in g.components.items():
            self.comp(c)
            self.edge(ref, *g.edges.get(ref, ()))
        self.edge(self.app_ref, *sorted(g.direct))

    def providers(self) -> None:
        s, r = self.s, self.r
        agents = s.agents.list()
        for p in s.providers.list():
            kind = p.kind
            local_only = (kind == "ollama" and r.endpoint_class(p.api_base) in ("none", "loopback")) or (p.custom and network_scope(p.api_base) == "this_pc")
            ref = f"provider:{p.id}"
            ep = r.url(p.api_base)
            sv = {"bom-ref": ref, "name": p.label, "provider": {"name": CATALOG[kind]["label"]},
                  "endpoints": [ep] if ep else None,
                  "authenticated": p.mode not in ("none",),
                  "data": [{"flow": "outbound", "classification": "prompts, tool results and retrieved passages",
                            "description": "Sent to the model provider on every turn" if not local_only else "Stays on this machine (local Ollama)"}],
                  "properties": props(kind=kind, enabled=p.enabled, auth_mode=p.mode, data_leaves_machine=not local_only,
                                      endpoint_class=r.endpoint_class(p.api_base) if p.api_base else None,
                                      custom_endpoint=True if p.custom else None, network_scope=network_scope(p.api_base) if p.custom else None,
                                      spend_cap_usd=p.cap_usd if r.internal else None, spend_cap_period=p.cap_period if r.internal else None,
                                      vertex_project=p.vertex_project if r.internal else None, vertex_location=p.vertex_location,
                                      aws_region=p.aws_region, aws_profile=p.aws_profile if r.internal else None,
                                      extra_header_names=sorted(p.headers()) if r.internal else None)}
            self.service(sv)
            self.edge(self.app_ref, ref)
            refs = set()
            for role, m in (("default", p.default_model), ("pro", p.pro_model), ("flash", p.flash_model)):
                if m and p.enabled:
                    refs.add(self.model(kind, m, role, p=p))
            self.edge(ref, *refs)
        # models chosen by agents, the side assistant and the fallback list
        by_id = {p.id: p for p in s.providers.list()}
        for a in agents:
            p = by_id.get(a.provider or "")
            if p is not None:
                m = tier_model(p, a.tier, a.model)
                self.edge(f"provider:{p.id}", self.model(p.kind, m, "agent", p=p))
        sb_pid, sb_model = s.db.get_setting("sidebot_provider", ""), s.db.get_setting("sidebot_model", "")
        p = by_id.get(sb_pid or "")
        if p is not None:
            self.edge(f"provider:{p.id}", self.model(p.kind, sb_model or p.flash_model or p.default_model or CATALOG[p.kind]["default_model"], "assistant", p=p))
        try:
            fallbacks = json.loads(s.db.get_setting("model_fallbacks", "[]") or "[]")
        except ValueError:
            fallbacks = []
        for f in fallbacks:
            p = by_id.get(str(f.get("provider")))
            if p is not None:
                self.edge(f"provider:{p.id}", self.model(p.kind, f.get("model") or p.default_model or CATALOG[p.kind]["default_model"], "fallback", p=p))

    def local_models(self) -> None:
        """Installed decision / time-series models, with the SHA-256 of every weight file at the pinned revision."""
        d1_ready = d1_spec.ready_models()
        d1_pins = [*d1_spec.LIB_PINS, *d1_spec.TORCH_CPU["packages"]]
        for mid in d1_ready:
            sp = d1_spec.MODELS[mid]
            ref = self.hf_model(sp.repo, sp.revision, sp.label, "decision (calibrated classification and scoring, no text generation)",
                                {"n": d1_spec.LICENSE_NAME, "u": sp.license_url}, {sp.repo: sp.revision})
            self.edge("mcp:" + d1_spec.MCP_ID, ref)
            self.edge(ref, *self.pins(d1_pins, "pinned in the d1 install plan (the CPU/CUDA torch build is chosen at install time)"))
        for tid in ts_spec.installed_ids():
            m = ts_spec.MODELS.get(tid)
            if m is None:
                continue
            ref = self.hf_model(m.repo, m.revision, m.label, "time-series forecasting", {"n": m.license_name, "u": m.license_url},
                                {repo: rev for repo, rev, _ in m.repos}, params=m.params, commercial=m.commercial)
            self.edge("mcp:" + ts_spec.MCP_ID, ref)
            self.edge(ref, *self.pins([*ts_spec.BASE_PINS, *m.pip], "pinned in the time-series install plan"))

    def hf_model(self, repo: str, rev: str, label: str, task: str, lic: dict, repos: dict[str, str], **extra: Any) -> str:
        owner, _, name = repo.partition("/")
        ref = f"pkg:huggingface/{owner}/{name}@{rev}"
        files = []
        for rp, rv in repos.items():
            for fname, h in sorted(HASHES.get(f"{rp}@{rv}", {}).items()):
                files.append({"type": "file", "bom-ref": f"{ref}#{rp}/{fname}", "name": f"{rp}/{fname}",
                              "hashes": [{"alg": "SHA-256", "content": h["sha256"]}], "properties": props(size_bytes=h.get("size"))})
        self.comp({"type": "machine-learning-model", "bom-ref": ref, "name": name, "group": owner, "version": rev, "purl": ref,
                   "supplier": {"name": owner}, "licenses": [{"license": {"name": lic["n"], "url": lic["u"]}}],
                   "externalReferences": [{"type": "distribution", "url": f"https://huggingface.co/{repo}/tree/{rev}"}],
                   "modelCard": {"modelParameters": {"task": task, "inputs": [{"format": "text"}], "outputs": [{"format": "json"}]}},
                   "components": files, "properties": props(runs_locally=True, display_name=label, pinned_revision=rev, **extra)})
        return ref

    def pins(self, pins: list[str], why: str) -> list[str]:
        out = []
        for spec in pins:
            m = re.match(r"^([A-Za-z0-9_.-]+)==([\w.+!-]+)$", spec)
            if not m:
                continue
            purl = deps.purl_pypi(m.group(1), m.group(2))
            out.append(self.comp({"type": "library", "bom-ref": purl, "name": deps.canonicalize_name(m.group(1)), "version": m.group(2), "purl": purl,
                                  "properties": props(ecosystem="pypi", source=why)}))
        return out

    def package_of(self, command: str | None, args: list[str], why: str, scope: str | None) -> str | None:
        """The npm/PyPI package a `npx` / `uvx` command pins, as a component (or None for local scripts)."""
        cmd = (command or "").replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe").removesuffix(".cmd")
        spec = None
        if cmd == "npx":
            spec = next((a for a in args if not a.startswith("-")), None)
            m = re.match(r"^(@?[^@\s]+)@(\d[^\s@]*)$", spec or "")
            if m:
                purl = deps.purl_npm(m.group(1), m.group(2))
                return self.comp({"type": "library", "bom-ref": purl, "name": m.group(1), "version": m.group(2), "purl": purl, "scope": scope,
                                  "properties": props(ecosystem="npm", source=why)})
        elif cmd == "uvx":
            frm = next((args[i + 1] for i, a in enumerate(args[:-1]) if a == "--from"), None)
            spec = frm or next((a for a in args if not a.startswith("-")), None)
            m = re.match(r"^([A-Za-z0-9_.-]+)==([\w.+!-]+)$", spec or "")
            if m:
                purl = deps.purl_pypi(m.group(1), m.group(2))
                return self.comp({"type": "library", "bom-ref": purl, "name": deps.canonicalize_name(m.group(1)), "version": m.group(2), "purl": purl,
                                  "scope": scope, "properties": props(ecosystem="pypi", source=why)})
        return None

    def mcp(self) -> None:
        r = self.r
        from .. import mcp_catalog
        from_catalog = mcp_catalog.installed(self.s.mcp)
        for m in self.s.mcp.list():
            ref = f"mcp:{m.id}"
            ce = mcp_catalog.get(m.id) if m.id in from_catalog else None
            args = r.args(list(m.args), trusted=m.builtin)
            url = r.url(m.url)
            scope = None if m.enabled else "optional"
            sv = {"bom-ref": ref, "name": m.label, "description": self.user_text(m.description, m.builtin), "endpoints": [url] if url else None,
                  "authenticated": bool(m.requires_env),
                  "data": [{"flow": "bi-directional", "classification": "tool arguments and results",
                            "description": "Exchanged with the server whenever an agent that attaches it calls one of its tools"}],
                  "properties": props(transport=m.transport, command=r.command(m.command), args=args, requires_env=list(m.requires_env), enabled=m.enabled,
                                      builtin=m.builtin, default_on=m.default_on,
                                      from_catalog=True if ce else None, publisher=ce["publisher"] if ce else None,
                                      writes_allowed=from_catalog[m.id]["allow_writes"] if ce else None,
                                      read_only_kind=ce["read_only"] if ce else None,
                                      env_set=({k: bool(m.env.get(k)) for k in m.requires_env} if r.internal else None),
                                      endpoint_class=r.endpoint_class(m.url) if m.url else None,
                                      env_names=sorted(m.env) if (r.internal and not m.builtin) else None)}
            self.service(sv)
            self.edge(self.app_ref, ref)
            pkg = self.package_of(m.command, [str(a) for a in m.args], "pinned in the MCP server definition", scope)
            if pkg:
                self.edge(ref, pkg)
            elif any("app.mcp_" in str(a) for a in m.args):
                self.edge(ref, self.app_ref)                    # a module of Agent Studio itself

    def skills(self) -> None:
        r = self.r
        for sk in self.s.skills.list():
            builtin = sk.source == "builtin"
            if not builtin and not r.internal:
                continue
            ref = f"skill:{sk.name}"
            self.comp({"type": "file", "bom-ref": ref, "name": sk.name, "mime-type": "text/markdown", "description": r.text(sk.description),
                       "hashes": [{"alg": "SHA-256", "content": sha256_hex(sk.body)}],
                       "properties": props(group=sk.group, source=sk.source, body_length=len(sk.body), mcp=list(sk.mcp))})
            self.edge(self.app_ref, ref)
            self.edge(ref, *[f"mcp:{x}" for x in sk.mcp])

    def external_agents(self) -> None:
        r = self.r
        try:
            rows = self.s.acp.store.list()
        except Exception:  # noqa: BLE001 - probing for installed CLIs must never break the BOM
            rows = []
        for a in rows:
            ref = f"acp:{a['id']}"
            self.comp({"type": "application", "bom-ref": ref, "name": a["label"], "description": self.user_text(a.get("notes", ""), a["builtin"]),
                       "properties": props(kind=a["kind"], command=r.command(a["command"]), args=r.args(a["args"], a["builtin"]), enabled=a["enabled"],
                                           installed=a["installed"], billing=a["billing"], external_to_spend_caps=True,
                                           env_names=a["env_keys"] if r.internal else None,
                                           monthly_cost_cap_usd=a["month_cost_cap"] if r.internal else None,
                                           installed_version="not queried")})
            self.edge(self.app_ref, ref)
            pkg = self.package_of(a["command"], [str(x) for x in a["args"]], "adapter package pinned by Agent Studio", None)
            if pkg:
                self.edge(ref, pkg)
            for name, ver in re.findall(r"\b([A-Za-z][\w.-]*)==(\d[\w.]*)", a.get("install", "")):
                self.edge(ref, *self.pins([f"{name}=={ver}"], "pinned in the install instructions"))
            for pkg_name, ver in re.findall(r"(@?[a-z][\w./-]*)@(\d[\w.]*)", a.get("install", "")):
                if "/" in pkg_name or pkg_name.startswith("@"):
                    purl = deps.purl_npm(pkg_name, ver)
                    self.edge(ref, self.comp({"type": "library", "bom-ref": purl, "name": pkg_name, "version": ver, "purl": purl,
                                              "properties": props(ecosystem="npm", source="pinned in the install instructions")}))

    def knowledge(self) -> None:
        r, s = self.r, self.s
        stores = s.rag.list_stores()
        images: set[str] = set()
        for st in stores:
            ref = f"store:{st['id']}"
            cfg = st["config"]
            host_url = cfg.get("url") or (f"https://{cfg['index_host']}" if cfg.get("index_host") else None)
            if cfg.get("host"):
                host_url = f"postgresql://{cfg['host']}:{cfg.get('port') or ''}"
            klass = ("local file" if st["kind"] == "local" else r.endpoint_class(host_url) if host_url else "managed cloud service")
            shown = {k: (r.text(str(v)) if not re.search(r"(key|token|secret|password|dsn)", k, re.I) else "<redacted>") for k, v in cfg.items()}
            ep = r.url(host_url) if st["kind"] != "local" else None
            self.service({"bom-ref": ref, "name": st["label"], "description": f"Vector store ({st['kind_label']})", "endpoints": [ep] if ep else None,
                          "data": [{"flow": "bi-directional", "classification": "document chunks, embeddings and search queries",
                                    "description": "Chunks and their embeddings are written on ingest; queries and passages flow on search"}],
                          "properties": props(kind=st["kind"], builtin=st["builtin"], managed=st["managed"], location=klass,
                                              can_ingest=st["can_ingest"], knowledge_bases=st["kbs"], config=shown if r.internal else None,
                                              secrets_set=st["secrets_set"] if r.internal else None)})
            self.edge(self.app_ref, ref)
            if st["id"].startswith("docker-"):
                images.add(st["id"][len("docker-"):])
        from ..rag import stack as stacks                       # local import: the module reads the data dir lazily
        for sid in sorted(images):
            spec = stacks.STACKS.get(sid)
            if spec is None:
                continue
            name, tag, purl = purl_docker(spec.image)
            self.comp({"type": "container", "bom-ref": purl, "name": name, "version": tag, "purl": purl,
                       "properties": props(published_on="127.0.0.1:%d" % spec.port, data_volume=spec.volume, source="pinned in rag/stack.py")})
            self.edge(f"store:docker-{sid}", purl)
        for kb in s.rag.list_kbs():
            ref = f"kb:{kb['id']}"
            docs = [dict(d) for d in s.db.query("SELECT doc_id, source, sha256, chunks, bytes FROM rag_docs WHERE kb_id=? ORDER BY doc_id", (kb["id"],))]
            agg = sha256_hex("\n".join(sorted(d["sha256"] for d in docs))) if docs else None
            nested = []
            if r.internal:
                for d in docs:
                    nested.append({"type": "data", "bom-ref": f"{ref}#{d['doc_id']}", "name": r.text(d["source"]),
                                   "hashes": [{"alg": "SHA-256", "content": d["sha256"]}], "properties": props(size_bytes=d["bytes"], chunks=d["chunks"])})
            self.comp({"type": "data", "bom-ref": ref, "name": kb["label"],
                       "description": self.user_text(kb["description"], False),
                       "hashes": [{"alg": "SHA-256", "content": agg}] if agg else None,
                       "data": [{"type": "dataset", "name": kb["label"], "classification": "knowledge base (contents are never included)"}],
                       "components": nested,
                       "properties": props(store=kb["store_id"], store_kind=kb["store_kind"], managed=kb["managed"], embedding_provider=kb["provider"] or None,
                                           embedding_model=kb["model"] or None, dimensions=kb["dim"] or None, chunk_size=kb["chunk_size"],
                                           chunk_overlap=kb["chunk_overlap"], documents=kb["docs"], chunks=kb["chunks"],
                                           content_hash_note="sha256 over the sorted document hashes")})
            self.edge(ref, f"store:{kb['store_id']}")
            self.edge(self.app_ref, ref)
            if kb["model"]:
                p = next((x for x in s.providers.list() if x.id == kb["provider"]), None)
                kind = p.kind if p else kb["provider"]
                self.edge(ref, self.model(kind, kb["model"], "embedding", "feature-extraction"))
                if p:
                    self.edge(f"provider:{p.id}", self.models_by_kind[(kind, kb["model"])])

    def auto_routing(self) -> None:
        """Auto mode (picks provider and model per message): its settings, the local decision model it may use, its tier models."""
        auto = getattr(self.s, "auto", None)
        if auto is None:
            return
        try:
            cfg = auto.config()
        except Exception:  # noqa: BLE001 - never let a settings problem stop the BOM
            return
        self.app_props += props(auto_routing_enabled=cfg.get("enabled"), auto_routing_engine=cfg.get("engine"), auto_routing_preference=cfg.get("preference"),
                                auto_routing_fallback=cfg.get("fallback"), auto_routing_allow=cfg.get("allow") if self.r.internal else None,
                                auto_routing_deny=cfg.get("deny") if self.r.internal else None)
        engine = str(cfg.get("engine", ""))
        sp = d1_spec.MODELS.get(engine)
        if sp is not None:
            owner, _, name = sp.repo.partition("/")
            ref = f"pkg:huggingface/{owner}/{name}@{sp.revision}"
            if ref in self.comps:                              # installed: the router can use it
                self.edge(self.app_ref, ref)
        by_id = {p.id: p for p in self.s.providers.list()}
        for tier, target in (cfg.get("tier_models") or {}).items():
            pid, _, model = str(target).partition("/")
            p = by_id.get(pid)
            if p is not None and model:
                self.edge(f"provider:{p.id}", self.model(p.kind, model, f"auto-{tier}", p=p))

    def sandbox_image(self) -> None:
        name, tag, purl = purl_docker(sandbox.IMAGE)
        df = REPO_ROOT / "docker" / "sandbox" / "Dockerfile"
        base = None
        try:
            m = re.search(r"^FROM\s+(\S+)", df.read_text("utf-8"), re.M)
            base = m.group(1) if m else None
        except OSError:
            pass
        self.comp({"type": "container", "bom-ref": purl, "name": name, "version": tag, "purl": purl,
                   "description": "Isolated container where agent shell commands and linters run (no network by default)",
                   "properties": props(built_from="docker/sandbox/Dockerfile", unpinned_installs="ruff, pytest (pip); git, ripgrep, nodejs (apt)")})
        self.edge(self.app_ref, purl)
        if base:
            bname, btag, bpurl = purl_docker(base)
            self.edge(purl, self.comp({"type": "container", "bom-ref": bpurl, "name": bname, "version": btag, "purl": bpurl,
                                       "properties": props(role="base image")}))

    def memory(self) -> None:
        """What agents remember: which kinds are on, where memories are kept, and how many there are. Never the memories themselves."""
        r, s = self.r, self.s
        svc = getattr(s, "memory", None)
        if svc is None:
            return
        cfg, stats = svc.config(), svc.stats()
        kind = svc.factory.kinds.get(cfg["backend"])
        shown = {k: ("<redacted>" if re.search(r"(key|token|secret|password|dsn|credential)", k, re.I) else r.text(str(v))) for k, v in cfg["backend_config"].items()}
        url = cfg["backend_config"].get("url") or cfg["backend_config"].get("bank")
        self.service({"bom-ref": "memory", "name": "Agent memory", "description": f"What agents remember between chats ({kind.label if kind else cfg['backend']})",
                      "endpoints": [r.url(url)] if url and url.startswith("http") and r.internal else None,
                      "data": [{"flow": "bi-directional", "classification": "memories: facts, summaries, entities and how-tos (contents are never included)",
                                "description": "Memory text is stored here and, when a cloud backend is chosen, also sent to that service"}],
                      "properties": props(backend=cfg["backend"], cloud=bool(kind and kind.cloud), enabled=cfg["enabled"], pii_removed_before_saving=cfg["redact_pii"],
                                          kinds_on=[t for t, v in cfg["types"].items() if v["enabled"]], auto_extract=cfg["auto_extract"], review_required=cfg["review"],
                                          memories_by_kind=stats["by_type"], memories_total=stats["total"], waiting_for_review=stats["pending"],
                                          embedding_provider=cfg["embedding"]["provider"] or None, embedding_model=cfg["embedding"]["model"] or None,
                                          retention_days={t: v["ttl_days"] for t, v in cfg["types"].items()}, backend_config=shown if r.internal else None,
                                          secrets_set=sorted(svc.public_config()["secrets_set"]) if r.internal else None)})
        self.edge(self.app_ref, "memory")
        emb = cfg["embedding"]
        if emb["model"]:
            p = next((x for x in s.providers.list() if x.id == emb["provider"]), None)
            self.edge("memory", self.model(p.kind if p else emb["provider"], emb["model"], "embedding", "feature-extraction"))

    def security(self) -> None:
        """Which safety checks and identity provider are in use. Counts and kinds only: never credentials, rules, prompts or tokens."""
        r, s = self.r, self.s
        gr, idn = getattr(s, "guardrails", None), getattr(s, "identity", None)
        if gr is None or idn is None:
            return
        profiles = gr.profiles() if gr.enabled() else []
        icfg = idn.config()
        self.service({"bom-ref": "security-controls", "name": "Security controls", "description": "Guardrails on inputs, outputs and tool calls, and the identity of agents",
                      "data": [{"flow": "outbound", "classification": "text being checked (only for guardrails marked as leaving this PC)",
                                "description": "Cloud guardrail services receive the text they check; local ones do not"}] if any(p.kind in ("bedrock", "azure_safety", "model_armor") for p in profiles) else None,
                      "properties": props(guardrails_enabled=gr.enabled(), guardrail_profiles=[{"id": p.id, "kind": p.kind, "mode": p.mode, "fail": p.fail,
                                                                                                "stages": [k for k, v in p.stages.items() if v]} for p in profiles] if r.internal else len(profiles),
                                          guardrail_kinds=sorted({p.kind for p in profiles}), identity_provider=icfg["provider"]["kind"],
                                          identity_trust_domain=icfg["trust_domain"] if r.internal else None, identity_token_ttl_seconds=icfg["ttl_s"],
                                          identity_handovers_enforced=icfg["enforce"])})
        self.edge(self.app_ref, "security-controls")

    def artifacts(self) -> None:
        """Files agents produce: whether they are kept, where copies go, and how many there are. Never names, paths or content."""
        svc = getattr(self.s, "artifacts", None)
        if svc is None:
            return
        st = svc.stats()
        cloud = sorted({x["kind"] for x in st["storages"] if x["enabled"] and x["cloud"]})
        self.service({"bom-ref": "artifacts", "name": "Agent artifacts", "description": "Files and results agents produce, and where copies of them are kept (names, paths and contents are never included)",
                      "data": [{"flow": "outbound", "classification": "files agents produced (only the cloud storages you switched on)",
                                "description": "Copies go to: " + ", ".join(cloud) + ". A secret scan runs first"}] if cloud else None,
                      "properties": props(enabled=st["enabled"], capture=st["capture"], secret_scan=st["secret_scan"], retention_days=st["retention_days"] or None,
                                          artifacts_total=st["total"], artifacts_bytes=st["bytes"], artifacts_by_type=st["by_type"] or None,
                                          storage_kinds=sorted({x["kind"] for x in st["storages"] if x["enabled"]}), cloud_storage=cloud or None)})
        self.edge(self.app_ref, "artifacts")

    def canvas(self) -> None:
        """The shared canvas: whether it is on and how many exist. Never titles or content."""
        svc = getattr(self.s, "canvas", None)
        if svc is None:
            return
        cfg, n = svc.config(), svc.counts()
        self.service({"bom-ref": "canvas", "name": "Canvas", "description": "A document you and an agent edit together (titles and content are never included)",
                      "properties": props(enabled=cfg["enabled"], agent_changes_applied_straight_away=cfg["auto_apply"], canvases=n["canvases"], changes_waiting_for_review=n["pending"])})
        self.edge(self.app_ref, "canvas")

    def data_platform(self) -> None:
        """Data sources, data products, runtimes and the sandbox. Kinds, counts and classifications; hosts, tables and credentials are never included."""
        s, r = self.s, self.r
        dsv, rts = s.data, getattr(s, "runtimes", None)
        sm = dsv.summary()
        pol = s.chat.sandboxes.policy() if getattr(s, "chat", None) is not None else {}
        deployed = rts.summary() if rts is not None else {}
        self.service({"bom-ref": "data-platform", "name": "Data sources, runtimes and sandbox",
                      "description": "Which data sources agents may read (read-only), the data products that describe them, where agents run and how commands are isolated",
                      "data": [{"flow": "inbound", "classification": "data read by agents (read-only queries)", "description": "Only from the sources ticked for each agent; row, size and time limits apply; every access is logged"}] if sm["sources"] else None,
                      "properties": props(data_sources=sm["sources"] or None, data_source_kinds=sm["kinds"] or None, data_products=sm["products"] or None,
                                          deployed_agents=len(deployed) or None, runtime_targets=sorted({v["target"] for v in deployed.values()}) or None,
                                          sandbox_kind=pol.get("kind"), sandbox_memory=pol.get("memory"), sandbox_cpus=pol.get("cpus"), sandbox_pids=pol.get("pids"),
                                          sandbox_read_only_root=pol.get("read_only_root"), sandbox_microvm_enabled=pol.get("microvm_enabled"))})
        self.edge(self.app_ref, "data-platform")
        srcs = {x["id"]: x for x in dsv.public()}
        for p in dsv.products():
            sv = {"bom-ref": f"dataproduct:{p['id']}", "name": p["name"] if r.internal else p["id"], "description": self.user_text(p["description"], False),
                  "properties": props(classification=p["classification"], contains_pii=p["contains_pii"], source_kind=(srcs.get(p["source"]) or {}).get("kind"),
                                      objects_count=len(p["objects"]) or None, objects=p["objects"] if r.internal and p["objects"] else None,
                                      owner=p["owner"] if r.internal and p["owner"] else None, source=p["source"] if r.internal else None, tags=p["tags"] or None)}
            self.service(sv)
            self.edge("data-platform", sv["bom-ref"])

    def access(self) -> None:
        """Agent frameworks in use, API / A2A access, workflows and harness rules. Counts and kinds only: never keys, addresses, tokens or hooks."""
        s = self.s
        from ..apiaccess.api import access_config
        from ..frameworks.catalog import FRAMEWORKS
        from ..frameworks.envs import is_ready
        from ..harness.policy import get_policy
        cfg, pol = access_config(s.db), get_policy(s.db)
        wcfg, wfs = s.wf_config.config(), s.workflows.list()
        used = sorted({a.framework for a in s.agents.list()})
        self.service({"bom-ref": "agent-access", "name": "Agent frameworks, API access and workflows",
                      "description": "Which agent frameworks run agents, who may call them (OpenAI-compatible API, A2A) and the harness rules in force",
                      "data": [{"flow": "inbound", "classification": "requests from API clients and other agents", "description": "Only with a key, only for agents that opted in"}]
                      if cfg["api_enabled"] or cfg["a2a_enabled"] else None,
                      "properties": props(workflow_webhooks_enabled=wcfg["webhooks_enabled"], workflow_webhook_addresses=sum(1 for w in wfs if w["has_hook"]) or None,
                                          workflow_http_credentials=len(s.wf_config.credentials()) or None, workflow_triggers=sorted({w["trigger"] for w in wfs}) or None,
                                          frameworks_in_use=used, frameworks_installed=sorted(f for f in FRAMEWORKS if not FRAMEWORKS[f].native and is_ready(f)) or None,
                                          api_enabled=cfg["api_enabled"], a2a_enabled=cfg["a2a_enabled"], api_keys=len(s.apikeys.list()), remote_a2a_agents=len(s.a2a_remotes.list()) or None,
                                          api_agents=sorted(a.id for a in s.agents.list() if a.api_access) or None, a2a_agents=sorted(a.id for a in s.agents.list() if a.a2a) or None,
                                          workflows=len(wfs), imported_apis=len(s.wf_sources.list()) or None, permission_mode=pol["permission_mode"],
                                          loop_guard_repeats=pol["loop_guard_repeats"], web_tools=pol["web_enabled"], hooks=len(pol["hooks"]) or None, checkpoints=pol["checkpoints_enabled"])})
        self.edge(self.app_ref, "agent-access")

    def agents(self) -> None:
        r, s = self.r, self.s
        by_prov = {p.id: p for p in s.providers.list()}
        kb_ids = [k["id"] for k in s.rag.list_kbs()]
        all_skills = [sk for sk in s.skills.list() if sk.source == "builtin" or r.internal]       # a user's own skills are shared from `internal` up
        visible = {sk.name for sk in all_skills}
        idn = getattr(s, "identity", None)
        dsv = getattr(s, "data", None)
        every_src = dsv.public() if dsv is not None else []
        prods = dsv.products() if dsv is not None else []
        rts = getattr(s, "runtimes", None)
        from ..security import agentcard
        ctx = agentcard.Ctx(s) if r.internal and getattr(s, "guardrails", None) is not None and getattr(s, "identity", None) is not None else None     # the per-agent security score is shared from `internal` up (a number only), and only when the services it reads exist
        for a in s.agents.list():
            ref = f"agent:{a.id}"
            score = agentcard.brief(s, a, ctx) if ctx is not None else {}
            src_ids = dsv.granted_ids(a.data, every_src) if dsv is not None else []
            my_prods = [p for p in prods if p["source"] in src_ids]
            live = rts.live(a.id) if rts is not None and a.runtime != "local" else None
            p = by_prov.get(a.provider or "")
            model_id = tier_model(p, a.tier, a.model) if p else (a.model or None)
            skills = [sk.name for sk in all_skills if covers(a.skills, sk)]
            kbs = kb_ids if "*" in a.rag else [k for k in a.rag if k in kb_ids]
            ip = r.prompt(a.instruction)
            sv = {"bom-ref": ref, "name": a.name, "description": self.user_text(a.description, a.builtin),
                  "data": [{"flow": "outbound", "classification": "prompts, tool calls and tool results",
                            "description": "Sent to the agent's model provider and to the tools and servers it may use"}],
                  "properties": props(tier=a.tier, provider=a.provider or "chat-selected", model=model_id or a.model, builtin=a.builtin, tools=a.tools,
                                      mcp=a.mcp, skills=[x for x in a.skills if x == "*" or x.lower().startswith(GROUP_PREFIX) or x in visible], resolved_skills=skills, sub_agents=a.sub_agents, can_spawn=a.can_spawn,
                                      external_agents=a.acp_agents, external_agent_policy=a.acp_policy if a.acp_agents else None, knowledge_bases=a.rag,
                                      memory_kinds=a.memory.get("types") or None, memory_read_scopes=a.memory.get("read_scopes") or None,
                                      memory_write_scopes=a.memory.get("write_scopes") or None, memory_saving=a.memory.get("auto_save") if a.memory else None,
                                      framework=a.framework, api_access=True if a.api_access else None, a2a_enabled_for_agent=True if a.a2a else None,
                                      runtime=a.runtime if a.runtime != "local" else None, runtime_deployed=(True if live else False) if a.runtime != "local" else None,
                                      sandbox=a.sandbox or None,
                                      data_source_kinds=sorted({x["kind"] for x in every_src if x["id"] in src_ids}) or None,
                                      data_sources=src_ids if r.internal and src_ids else None,
                                      data_products=[p["id"] for p in my_prods] if r.internal and my_prods else None,
                                      data_classification=max((p["classification"] for p in my_prods), key=lambda c: ("public", "internal", "confidential", "restricted").index(c)) if my_prods else None,
                                      guardrails=(a.guardrails or None) if r.internal else None,
                                      security_score=score.get("percent"), security_checks_failing=score.get("fail"), security_checks_partly=score.get("warn"),
                                      guardrails_off=True if a.guardrails == ["none"] else None,
                                      identity_spiffe_id=idn.spiffe_id(a.id) if idn is not None and r.internal else None,
                                      identity_scopes=idn.allowed_scopes(a) if idn is not None and r.internal else None,
                                      identity_delegates=idn.delegates_of(a) if idn is not None and r.internal else None,
                                      instruction_sha256=ip["sha256"], instruction_length=ip["length"], instruction_text=ip.get("text"))}
            self.service(sv)
            self.edge(self.app_ref, ref)
            if p is not None:
                self.edge(ref, f"provider:{p.id}", self.model(p.kind, model_id, "agent", p=p))
            self.edge(ref, *[f"mcp:{m}" for m in a.mcp], *[f"skill:{x}" for x in skills], *[f"agent:{x}" for x in a.sub_agents],
                      *[f"acp:{x}" for x in a.acp_agents], *[f"kb:{k}" for k in kbs], *(["memory"] if a.memory and getattr(s, "memory", None) is not None else []),
                      *[f"dataproduct:{p['id']}" for p in my_prods])

    # ------------------------------------------------------------------ assembly
    def run(self) -> None:
        self.libraries()
        self.providers()
        self.mcp()                                  # before local models: those link to the MCP services
        self.local_models()
        self.auto_routing()
        self.skills()
        self.external_agents()
        self.knowledge()
        self.memory()
        self.security()
        self.artifacts()
        self.canvas()
        self.access()
        self.data_platform()
        self.sandbox_image()
        self.agents()


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def content_hash(bom: dict) -> str:
    """Hash of everything except the volatile fields, so two builds of an unchanged system compare equal."""
    d = json.loads(json.dumps(bom))
    d.pop("serialNumber", None)
    d.get("metadata", {}).pop("timestamp", None)
    return hashlib.sha256(_canonical(d)).hexdigest()


def prune(bom: dict, root_ref: str) -> dict:
    """Keep only what `root_ref` (and the application) can reach through the dependency graph."""
    graph = {d["ref"]: d.get("dependsOn", []) for d in bom["dependencies"]}
    keep: set[str] = set()
    stack = [root_ref]
    while stack:
        cur = stack.pop()
        if cur in keep:
            continue
        keep.add(cur)
        stack.extend(graph.get(cur, []))
    app = bom["metadata"]["component"]["bom-ref"]
    out = dict(bom)
    out["components"] = [c for c in bom.get("components", []) if c["bom-ref"] in keep]
    out["services"] = [sv for sv in bom.get("services", []) if sv["bom-ref"] in keep]
    dm = {d["ref"]: [x for x in d.get("dependsOn", []) if x in keep] for d in bom["dependencies"] if d["ref"] in keep and d["ref"] != app}
    out["dependencies"] = [{"ref": app, "dependsOn": [root_ref]}] + [{"ref": k, "dependsOn": v} for k, v in sorted(dm.items())]
    return out


def scope_roots(bom: dict) -> dict[str, list[str]]:
    """Which per-resource BOMs can be cut from this one: agents, MCP servers, models, skills."""
    refs = {"agents": [], "mcp": [], "models": [], "skills": []}
    for sv in bom.get("services", []):
        if sv["bom-ref"].startswith("agent:"):
            refs["agents"].append(sv["bom-ref"])
        elif sv["bom-ref"].startswith("mcp:"):
            refs["mcp"].append(sv["bom-ref"])
    for c in bom.get("components", []):
        if c["type"] == "machine-learning-model":
            refs["models"].append(c["bom-ref"])
        elif c["bom-ref"].startswith("skill:"):
            refs["skills"].append(c["bom-ref"])
    return {k: sorted(v) for k, v in refs.items()}


def _serial(bom: dict, stable: bool) -> str:
    return f"urn:uuid:{uuid.uuid5(SERIAL_NS, content_hash(bom)) if stable else uuid.uuid4()}"


def build_full(state: Any, level: str = "public", stable: bool = True, now: datetime | None = None, publisher: str = "") -> dict:
    """The whole-system AI-BOM as a CycloneDX 1.7 document (a dict). `stable`: the serial number is derived from the content,
    so an unchanged system gives the same serial number."""
    if level not in LEVELS:
        raise ValueError(f"unknown redaction level '{level}' (use one of: {', '.join(LEVELS)})")
    b = Build(state, level, now)
    b.run()
    app = b.application()
    refs = set(b.comps) | set(b.services) | {b.app_ref}
    deps_list = [{"ref": ref, "dependsOn": sorted(x for x in b.edges.get(ref, ()) if x in refs and x != ref)} for ref in sorted(refs - {b.app_ref})]
    deps_list.insert(0, {"ref": b.app_ref, "dependsOn": sorted(x for x in b.edges.get(b.app_ref, ()) if x in refs and x != b.app_ref)})
    meta: dict = {"timestamp": b.now.strftime("%Y-%m-%dT%H:%M:%SZ"), "lifecycles": [{"phase": "operations"}],
                  "tools": {"components": [{"type": "application", "name": GENERATOR, "version": GENERATOR_VERSION, "group": "agent-studio"}]},
                  "component": app, "properties": props(redaction_level=level, scope="system", limitations=LIMITATIONS)}
    if publisher:
        meta["supplier"] = {"name": publisher, "url": [f"https://{publisher}"]}
    bom = {"bomFormat": "CycloneDX", "specVersion": SPEC_VERSION, "serialNumber": "", "version": 1, "metadata": meta,
           "components": [b.comps[k] for k in sorted(b.comps)], "services": [b.services[k] for k in sorted(b.services)], "dependencies": deps_list}
    if not bom["services"]:
        del bom["services"]
    bom["serialNumber"] = _serial(bom, stable)
    return bom


def cut(bom: dict, scope: str, stable: bool = True) -> dict:
    """The part of a whole-system BOM that one agent, MCP server, model or skill (`scope` = its bom-ref) depends on."""
    app = bom["metadata"]["component"]["bom-ref"]
    known = {c["bom-ref"] for c in bom.get("components", [])} | {sv["bom-ref"] for sv in bom.get("services", [])}
    if scope not in known or scope == app:
        raise ValueError(f"unknown scope '{scope}': use 'system' or the bom-ref of an agent, MCP server, model or skill")
    out = prune(bom, scope)
    out["metadata"] = {**bom["metadata"], "properties": props(redaction_level=bom["metadata"]["properties"][0]["value"], scope=scope, limitations=LIMITATIONS)}
    if not out["services"]:
        del out["services"]
    out["serialNumber"] = _serial(out, stable)
    return out


def build_bom(state: Any, level: str = "public", scope: str = "system", stable: bool = True, now: datetime | None = None,
              publisher: str = "") -> dict:
    """`scope`: 'system' or the bom-ref of an agent, MCP server, model or skill."""
    full = build_full(state, level, stable, now, publisher)
    return full if scope == "system" else cut(full, scope, stable)


def serialize(bom: dict) -> bytes:
    return (json.dumps(bom, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
