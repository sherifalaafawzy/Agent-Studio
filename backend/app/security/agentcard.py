"""Per-agent security scorecards: the same OWASP checks as the overall scorecard, judged on ONE agent's own configuration.

How the two views fit together (this is the definition, and tests hold it to it):

* The OVERALL card (`scorecard.build`) judges the app's settings: guardrails as the default profiles apply them, sandbox and
  limits as set, and "your own agents" in aggregate. It does not average the agents.
* An AGENT card runs the same 26 checks (same ids, titles and OWASP mapping) against what that agent actually uses: its own
  guardrail choice, tools, MCP servers, memory, knowledge bases, sandbox, provider and identity. A check that cannot apply to the
  agent (for example the sandbox network check for an agent that cannot run commands) is marked "not applicable" with the reason and
  is left out of its score. Checks about the whole app (telemetry, security export, AI-BOM, built-in protections) are "shared": they
  read the same for every agent.
* An agent card adds a few checks that only make sense for one agent: permission mode, data sources, API/A2A exposure, where it runs,
  its framework and the remote agents it may call.
* Consistency: an agent that keeps every default (no tools, no memory, default guardrails) gets exactly the overall result on every
  check that looks at the app's settings. Where an agent differs, it is because it chose something different, and the card says so.
* Scores use the same points as the overall card (pass 1, partly 0.5, missing 0) over the checks that apply.

Like the overall card it measures configuration, never whether an attack would succeed."""
from __future__ import annotations

import copy
import json
import re
import time
from functools import cached_property
from typing import Any, Callable

from ..frameworks.catalog import FRAMEWORKS as AGENT_FRAMEWORKS
from . import scorecard as SC
from .scorecard import FRAMEWORKS, POINTS, RANK, Check

NA = "na"
STATUS_WORD = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", NA: "N/A", "none": "NOT COVERED"}
CHANGE_TOOLS = ("run_shell", "write_file", "edit_file")                       # tools that change files or run commands
RISKY_TOOLS = (*CHANGE_TOOLS, "create_skill", "propose_mcp_server", "propose_openapi_mcp", "call_remote_agent")
SHARED = {"aibom", "telemetry", "ocsf", "transparency", "secrets-write-only", "sandbox-only", "spawn-rules", "inter-agent"}   # read the same for every agent that they apply to
SCOPE_NEEDS = {"files:write": ("write_file", "edit_file"), "shell:run": ("run_shell",), "web:fetch": ("web_fetch",), "memory:write": ("memory_save",)}
LOCAL_TARGETS = ("local", "docker")
BANDS = (("good", 80), ("middling", 50), ("low", 0))                           # the same thresholds the score bar uses


def link(a: Any, anchor: str) -> str:
    """Where to fix something for this agent: the agent editor, scrolled to the section."""
    return f"/agents/?agent={a.id}#{anchor}"


def band(percent: int) -> str:
    return next(name for name, low in BANDS if percent >= low)


def na(cid: str, title: str, maps: tuple, reason: str, kind: str = "setting") -> Check:
    return Check(cid, title, maps, NA, reason, kind=kind)


class _Rails:
    """The guardrail service as this agent sees it: the same switch, but its own choice of profiles."""

    def __init__(self, service: Any, rails: list[str]):
        self.service, self.rails = service, rails

    def enabled(self) -> bool:
        return self.service.enabled()

    def active_profiles(self, _ignored: Any, stage: str) -> list:
        return self.service.active_profiles(self.rails, stage)


class _View:
    """What the overall card's guardrail checks read from the app (only `.guardrails`), with this agent's own choice of profiles."""

    def __init__(self, s: Any, a: Any):
        self.guardrails = _Rails(s.guardrails, a.guardrails)


class Ctx:
    """Facts read once per run, so scoring many agents does not read the database again for each one."""

    def __init__(self, s: Any):
        self.s = s

    @cached_property
    def agents(self) -> list:
        return self.s.agents.list()

    @cached_property
    def mcp(self) -> dict:
        return {m.id: m for m in self.s.mcp.list()}

    @cached_property
    def catalog(self) -> dict:
        from .. import mcp_catalog
        return mcp_catalog.installed(self.s.mcp)

    @cached_property
    def providers(self) -> list:
        return self.s.providers.list()

    @cached_property
    def policy(self) -> dict:
        from ..harness.policy import get_policy
        return get_policy(self.s.db)

    @cached_property
    def memory_cfg(self) -> dict:
        return self.s.memory.config()

    @cached_property
    def sandbox_kind(self) -> str:
        return self.s.chat.sandboxes.policy()["kind"]

    @cached_property
    def access(self) -> dict:
        from ..apiaccess.api import access_config
        return access_config(self.s.db)

    @cached_property
    def keys(self) -> list:
        now = time.time()
        return [k for k in self.s.apikeys.list() if not k["revoked"] and not (k["expires"] and k["expires"] < now)]

    @cached_property
    def stores(self) -> dict:
        return {st["id"]: st for st in self.s.rag.list_stores()}

    @cached_property
    def sources(self) -> list:
        return self.s.data.public()

    @cached_property
    def products(self) -> list:
        return self.s.data.products()

    @cached_property
    def shared(self) -> dict:
        s = self.s
        return {"aibom": SC.c_aibom(s), "telemetry": SC.c_telemetry(s), "ocsf": SC.c_ocsf(s), "transparency": SC.c_transparency(s),
                "secrets-write-only": SC.c_secrets(s), "sandbox-only": SC.c_sandbox_design(s), "spawn-rules": SC.c_spawning(s),
                "inter-agent": SC.c_inter_agent(s), "identity": SC.c_identity(s)}

    def deployed(self, a: Any) -> str:
        """The runtime target a live deployment of this agent answers from ('' when it runs on this PC)."""
        rts = getattr(self.s, "runtimes", None)
        return a.runtime if a.runtime != "local" and rts is not None and rts.live(a.id) is not None else ""


Fn = Callable[[Any, Any, Ctx], Check]


# ------------------------------------------------------------------------------------------------------------ guardrails
def _deployed_guard(a: Any, x: Ctx, cid: str, title: str, maps: tuple, status: str, what: str) -> Check | None:
    where = x.deployed(a)
    if not where:
        return None
    return Check(cid, title, maps, status, f"Its answers come from the deployment on {where}, which has no guardrails, so {what}.",
                 "Run this agent on this PC, or put your own safety check in front of the deployed endpoint.", link(a, "ag-runtime"))


def a_guard_input(s: Any, a: Any, x: Ctx) -> Check:
    hit = _deployed_guard(a, x, "guard-input", "Guardrails check what goes into models", ("LLM01", "ASI01"), "fail", "nothing it is sent is checked")
    if hit:
        return hit
    c = SC.c_guard_input(_View(s, a))
    if a.guardrails == ["none"] and s.guardrails.enabled():
        c.detail, c.fix = "Guardrails are switched off for this agent: it runs unchecked.", "Choose the default profiles, or pick profiles, for this agent."
    c.link = link(a, "ag-guardrails")
    return c


def _guard_variant(fn: Callable, cid: str, title: str, maps: tuple, status: str, what: str) -> Fn:
    def check(s: Any, a: Any, x: Ctx) -> Check:
        hit = _deployed_guard(a, x, cid, title, maps, status, what)
        if hit:
            return hit
        c = fn(_View(s, a))
        c.link = link(a, "ag-guardrails")
        return c
    return check


a_guard_model = _guard_variant(SC.c_guard_model, "guard-model", "A safety model (not only word rules) is in use", ("LLM01", "LLM05"), "warn", "no safety model watches it")
a_guard_output = _guard_variant(SC.c_guard_output, "guard-output", "Answers are checked before they are shown", ("LLM02", "LLM05", "LLM07"), "fail", "its answers are not checked")
a_guard_leaks = _guard_variant(SC.c_guard_leaks, "guard-leaks", "Personal data and keys are looked for", ("LLM02", "LLM07"), "warn", "nothing looks for personal data or keys")


def a_guard_tool(s: Any, a: Any, x: Ctx) -> Check:
    if not (a.tools or a.mcp or a.sub_agents or a.acp_agents):
        return na("guard-tool", "Tool calls are checked before they run", ("ASI02", "LLM06"), "This agent has no tools to call.")
    if x.deployed(a):
        return na("guard-tool", "Tool calls are checked before they run", ("ASI02", "LLM06"), "A deployed agent has no tools.")
    c = SC.c_guard_tool(_View(s, a))
    c.link = link(a, "ag-guardrails")
    return c


def a_retrieved(s: Any, a: Any, x: Ctx) -> Check:
    title = "Text from files, the web and documents is checked as data"
    if not (a.tools or a.mcp or a.rag or a.data):
        return na("retrieved-text", title, ("LLM04", "LLM08", "ASI01", "ASI06"), "This agent reads no files, web pages, documents or tool results.")
    if x.deployed(a):
        return na("retrieved-text", title, ("LLM04", "LLM08", "ASI01", "ASI06"), "A deployed agent reads no files or tool results.")
    c = SC.c_retrieval(_View(s, a))
    c.link = link(a, "ag-guardrails")
    return c


# --------------------------------------------------------------------------------------------------- commands and tools
def a_sandbox_net(s: Any, a: Any, x: Ctx) -> Check:
    title, maps = "Commands run without network access", ("LLM06", "ASI05")
    if "run_shell" not in a.tools:
        return na("sandbox-network", title, maps, "This agent cannot run commands.")
    if (a.sandbox or x.sandbox_kind) == "docker-microvm":
        return Check("sandbox-network", title, maps, "warn", "Its commands run in a micro VM whose network rules come from sbx, so Agent Studio cannot check them.",
                     "Check them with `sbx policy ls`, or use the Docker sandbox, whose network is off by default.", link(a, "ag-runtime"))
    c = SC.c_sandbox_net(s)
    c.link = "/settings/#harness"
    return c


def a_sandbox_only(s: Any, a: Any, x: Ctx) -> Check:
    if "run_shell" not in a.tools:
        return na("sandbox-only", "Commands run only inside a Docker sandbox", ("ASI05", "LLM05", "LLM06"), "This agent cannot run commands.", "built-in")
    return x.shared["sandbox-only"]


def a_least_privilege(s: Any, a: Any, x: Ctx) -> Check:
    risky = [t for t in CHANGE_TOOLS if t in a.tools]
    return Check("least-privilege", "Your own agents hold only the tools they need", ("LLM06", "ASI02", "ASI03"), "warn" if risky else "pass",
                 f"It can change files or run commands ({', '.join(risky)})." if risky else "It cannot change files or run commands.",
                 "Untick the tools this agent does not need." if risky else "", link(a, "ag-tools"))


def a_permissions(s: Any, a: Any, x: Ctx) -> Check:
    title, maps = "The permission mode limits what its tools may do", ("LLM06", "ASI02")
    mine = [t for t in RISKY_TOOLS if t in a.tools]
    if not mine:
        return na("permission-mode", title, maps, "This agent has no tool that changes things.")
    from ..harness.policy import NETWORK, SHELL_AND_EXTEND
    mode = x.policy["permission_mode"]
    if mode == "full":
        return Check("permission-mode", title, maps, "warn", f"The permission mode is 'full': nothing stops it using {', '.join(mine)}.",
                     "Use 'accept edits' or 'plan' in Settings > Agents & sandbox.", "/settings/#harness")
    refused = [t for t in mine if mode == "plan" or t in SHELL_AND_EXTEND or t in NETWORK]
    return Check("permission-mode", title, maps, "pass", f"The permission mode is '{mode}'" + (f": it refuses {', '.join(refused)}." if refused else "."), link="/settings/#harness")


def a_spawn(s: Any, a: Any, x: Ctx) -> Check:
    if not a.can_spawn:
        return na("spawn-rules", "Agents that create agents cannot extend themselves", ("ASI10", "ASI08"), "This agent cannot create other agents.", "built-in")
    return x.shared["spawn-rules"]


def a_limits(s: Any, a: Any, x: Ctx) -> Check:
    g = s.db.get_setting
    calls, spawns = int(g("max_llm_calls_per_turn", "40")), int(g("max_spawns_per_turn", "4"))
    pool = [p for p in x.providers if p.id == a.provider] if a.provider else list(x.providers)
    big = [f"{p.label} cap ${p.cap_usd:g}" for p in pool if p.enabled and p.kind not in ("ollama", "mock") and p.cap_usd > 100]
    outside = []
    info = AGENT_FRAMEWORKS.get(a.framework)
    if info is not None and info.caps.get("spend_caps") == "no":
        outside.append(f"{info.label} bills your own account, outside Agent Studio's spend caps")
    if x.deployed(a):
        outside.append(f"the deployment on {a.runtime} is billed in its own account, outside the spend caps")
    bad = calls > 100 or (a.can_spawn and spawns > 8) or big or outside
    detail = f"At most {calls} model calls per turn" + (f" and {spawns} created agents" if a.can_spawn else "") + (f"; high caps: {', '.join(big)}" if big else "")
    detail += (f"; but {'; '.join(outside)}" if outside else "; spend caps apply") + "."
    return Check("limits", "Runaway loops and spending are capped", ("LLM10", "ASI08"), "warn" if bad else "pass", detail,
                 "Lower the per-turn limits or the provider caps, or move the agent onto a framework and runtime the caps cover." if bad else "", "/settings/#harness")


def a_external(s: Any, a: Any, x: Ctx) -> Check:
    title, maps = "External agents ask before they act", ("ASI02", "ASI03", "ASI09")
    if not a.acp_agents:
        return na("external-agents", title, maps, "This agent does not call external agents.")
    if s.db.get_setting("agents_can_call_external", "1") != "1":
        return Check("external-agents", title, maps, "pass", "External agents are switched off in Settings, so it cannot call them.", link="/settings/#harness")
    if a.acp_policy == "reads_auto":
        return Check("external-agents", title, maps, "warn", f"Reads by {', '.join(a.acp_agents[:5])} are approved automatically.", "Set their permissions to 'ask' or 'read-only'.", link(a, "ag-acp"))
    return Check("external-agents", title, maps, "pass", "Every permission request by an external agent " + ("is refused unless it only reads." if a.acp_policy == "readonly" else "waits for you."), link=link(a, "ag-acp"))


def a_remote(s: Any, a: Any, x: Ctx) -> Check:
    title, maps = "Remote agents it can call are known and labelled", ("ASI07", "ASI09", "LLM02")
    if "call_remote_agent" not in a.tools:
        return na("agent-remote", title, maps, "This agent has no tool for calling remote A2A agents.")
    ids = s.a2a_remotes.enabled_ids(a.id)
    if not ids:
        return Check("agent-remote", title, maps, "pass", "It may call remote agents, but none is connected for it.", link=link(a, "ag-tools"))
    rows = [s.a2a_remotes.get(i) for i in ids]
    far = [r["label"] for r in rows if r["network_scope"] == "internet"]
    return Check("agent-remote", title, maps, "warn" if far else "pass",
                 (f"It may send text to agents on the internet ({', '.join(far[:5])}); they run outside Agent Studio's caps and guardrails." if far
                  else f"It may call {len(rows)} remote agent(s), all on this PC or this network."),
                 "Disconnect them, or limit which agents may call them (Agents > the remote agent > who may call it)." if far else "", "/agents/")


# ----------------------------------------------------------------------------------------------------------------- MCP
def a_mcp_pins(s: Any, a: Any, x: Ctx) -> Check:
    title, maps = "MCP servers are pinned to exact versions", ("LLM03", "ASI04")
    if not a.mcp:
        return na("mcp-pinned", title, maps, "This agent uses no MCP servers.")
    loose = [m.id for i in a.mcp if (m := x.mcp.get(i)) and m.enabled and m.transport == "stdio" and (m.command or "") in ("npx", "uvx", "pipx")
             and not any(SC.PINNED.search(arg) for arg in m.args)]
    return Check("mcp-pinned", title, maps, "warn" if loose else "pass", f"Not pinned: {', '.join(loose[:6])}." if loose else "Every enabled MCP server it uses names an exact version.",
                 "Edit the server's arguments to include an exact version (name@1.2.3)." if loose else "", "/mcp/")


def a_mcp_writes(s: Any, a: Any, x: Ctx) -> Check:
    title, maps = "Catalog servers stay read-only", ("LLM06", "ASI02")
    if not a.mcp:
        return na("mcp-writes", title, maps, "This agent uses no MCP servers.")
    writes = [i for i in a.mcp if x.catalog.get(i, {}).get("allow_writes")]
    return Check("mcp-writes", title, maps, "warn" if writes else "pass", f"It uses servers that may change data: {', '.join(writes[:6])}." if writes else "None of its servers may change data.",
                 "Switch their 'allow changes' off unless this agent needs it." if writes else "", "/mcp/")


# --------------------------------------------------------------------------------------------------------- memory, data
def a_memory_pii(s: Any, a: Any, x: Ctx) -> Check:
    title, maps = "Personal data is removed before saving to memory", ("LLM02", "ASI06")
    if not a.memory:
        return na("memory-pii", title, maps, "This agent has no memory.")
    ok = x.memory_cfg["redact_pii"]
    return Check("memory-pii", title, maps, "pass" if ok else "warn", "What it remembers is cleaned of personal data first." if ok else "What it remembers may contain personal data.",
                 "" if ok else "Turn on 'remove personal data' in Memory settings.", "/settings/#memory")


def a_memory_review(s: Any, a: Any, x: Ctx) -> Check:
    title, maps = "New memories can be reviewed before they are trusted", ("LLM04", "ASI06")
    if not a.memory:
        return na("memory-review", title, maps, "This agent has no memory.")
    saving, shared = a.memory.get("auto_save", "review"), "global" in (a.memory.get("write_scopes") or [])
    bad = saving == "auto" and not x.memory_cfg["review"]
    why = "it saves on its own without review" + (" and writes to memory every agent shares" if shared else "")
    return Check("memory-review", title, maps, "warn" if bad else "pass", f"{why[0].upper()}{why[1:]}." if bad else "Its new memories wait for your approval, or it does not save on its own.",
                 "Turn on 'review before remembering', or set this agent to review its saves." if bad else "", link(a, "ag-memory"))


def a_vector_stores(s: Any, a: Any, x: Ctx) -> Check:
    title, maps = "Vector stores are reached over a secure connection", ("LLM08",)
    if not a.rag:
        return na("vector-stores", title, maps, "This agent uses no knowledge bases.")
    wanted = list(x.stores) if "*" in a.rag else [i for i in a.rag if i in x.stores]
    bad = [i for i in wanted if str((x.stores[i].get("config") or {}).get("url", "")).startswith("http://")
           and not re.match(r"http://(localhost|127\.0\.0\.1|\[::1\])", str(x.stores[i]["config"]["url"]))]
    return Check("vector-stores", title, maps, "warn" if bad else "pass", f"Plain http:// to a remote host: {', '.join(bad)}." if bad else "Every store it reads is local or uses https://.",
                 "Use https:// for remote stores." if bad else "", "/knowledge/")


def a_data(s: Any, a: Any, x: Ctx) -> Check:
    title, maps = "Data sources are read-only, narrow and masked", ("LLM02", "LLM06", "ASI03")
    if not a.data:
        return na("agent-data", title, maps, "This agent reads no data sources.")
    ids = s.data.granted_ids(a.data, x.sources)
    unmasked = [src["id"] for src in x.sources if src["id"] in ids and not src["redact"]]
    top = [p["classification"] for p in x.products if p["source"] in ids and p["classification"] in ("confidential", "restricted")]
    notes = []
    if "*" in a.data:
        notes.append("it may read every data source, including ones added later")
    if unmasked:
        notes.append(f"personal data is not masked for {', '.join(unmasked[:5])}")
    if top:
        notes.append(f"it can reach {len(top)} {'/'.join(sorted(set(top)))} data product(s)")
    bad = "*" in a.data or bool(unmasked)
    return Check("agent-data", title, maps, "warn" if bad else "pass",
                 ("It reads " + "; ".join(notes) + ".") if notes else f"It reads {len(ids)} source(s): read-only, row-limited and masked.",
                 "Grant only the sources it needs and keep masking on." if bad else "", link(a, "ag-data"))


# --------------------------------------------------------------------------------------------- identity, access, runtime
def a_identity(s: Any, a: Any, x: Ctx) -> Check:
    c = copy.copy(x.shared["identity"])
    explicit = a.identity.get("scopes")
    extra = [sc for sc, tools in SCOPE_NEEDS.items() if explicit is not None and sc in explicit and not any(t in a.tools for t in tools)]
    if explicit is not None and "mcp:call" in explicit and not a.mcp:
        extra.append("mcp:call")
    if explicit is not None and "agents:delegate" in explicit and not (a.sub_agents or a.can_spawn):
        extra.append("agents:delegate")
    if extra:
        c.status = "warn" if RANK[c.status] > RANK["warn"] else c.status
        c.detail = f"Its pass lists permissions it has no tool for: {', '.join(extra)}. " + c.detail
        c.fix = "Untick them, or let the pass follow its tools. " + c.fix
    c.link = link(a, "ag-identity")
    return c


def a_exposure(s: Any, a: Any, x: Ctx) -> Check:
    title, maps = "Calls from outside are authenticated and narrow", ("ASI03", "ASI07", "LLM06")
    if not (a.api_access or a.a2a):
        return na("agent-exposure", title, maps, "It cannot be called through the API or A2A.")
    if not ((a.api_access and x.access["api_enabled"]) or (a.a2a and x.access["a2a_enabled"])):
        return Check("agent-exposure", title, maps, "pass", "It is switched on for outside calls, but the API / A2A switch is off in Settings, so nobody can reach it yet.", link=link(a, "ag-access"))
    keys = [k for k in x.keys if "*" in k["agents"] or a.id in k["agents"]]
    broad = [k["name"] for k in keys if "*" in k["agents"]]
    change = [t for t in CHANGE_TOOLS if t in a.tools]
    notes = []
    if broad:
        notes.append(f"keys that allow every agent can call it ({', '.join(broad[:4])})")
    if change and keys:
        notes.append(f"a key holder can make it use {', '.join(change)}")
    return Check("agent-exposure", title, maps, "warn" if notes else "pass",
                 ("; ".join(notes)[0].upper() + "; ".join(notes)[1:] + ".") if notes else (f"{len(keys)} key(s) may call it, each limited to chosen agents." if keys else "No key lets anyone call it yet."),
                 "Limit keys to the agents they need, and take change tools away from agents you expose." if notes else "", "/settings/#access")


def a_runtime(s: Any, a: Any, x: Ctx) -> Check:
    title, maps = "It runs where you expect, with its protections", ("LLM02", "LLM10", "ASI04")
    where = x.deployed(a)
    if not where:
        return Check("agent-runtime", title, maps, "pass", "It runs on this PC" + (f" (set to {a.runtime}, but not deployed, so chats still run here)." if a.runtime != "local" else "."), link=link(a, "ag-runtime"))
    if where in LOCAL_TARGETS:
        return Check("agent-runtime", title, maps, "pass", f"It answers from a container on this PC ({where}); no tools, memory or guardrails there.", link=link(a, "ag-runtime"))
    return Check("agent-runtime", title, maps, "warn", f"It answers from {where}: what you send it leaves this PC, and it has no tools, memory, guardrails or spend caps there.",
                 "Keep it on this PC unless it needs to be hosted, and give the cloud key the smallest permissions.", link(a, "ag-runtime"))


def a_framework(s: Any, a: Any, x: Ctx) -> Check:
    title, maps = "Its framework keeps Agent Studio's protections", ("LLM03", "LLM10", "ASI04")
    info = AGENT_FRAMEWORKS.get(a.framework)
    if info is None or info.native:
        return Check("agent-framework", title, maps, "pass", "It runs on the built-in engine: guardrails, spend caps, memory and tool policy all apply.", link=link(a, "ag-access"))
    gaps = [k.replace("_", " ") for k in ("guardrails", "spend_caps") if info.caps.get(k) != "yes"]
    return Check("agent-framework", title, maps, "warn" if gaps else "pass",
                 f"{info.label} runs in its own folder with pinned packages" + (f", but these protections are {'only partly' if info.caps.get('guardrails') == 'partial' else 'not'} applied: {', '.join(gaps)}." if gaps else "; guardrails and spend caps still apply."),
                 f"Use the built-in engine if you need {' and '.join(gaps)}." if gaps else "", link(a, "ag-access"))


# --------------------------------------------------------------------------------------------------- tested, shared
def a_evals(s: Any, a: Any, x: Ctx) -> Check:
    n = s.db.one("SELECT COUNT(*) n FROM eval_runs WHERE agent=? AND started>=? AND status='done'", (a.id, time.time() - 30 * 86400))["n"]
    return Check("evals", "Agents are tested regularly", ("LLM09", "ASI09"), "pass" if n else "warn", f"{n} evaluation run(s) of this agent in the last 30 days." if n else "This agent has not been evaluated in the last 30 days.",
                 "" if n else "Create a test set and run it against this agent.", "/evals/")


def a_redteam(s: Any, a: Any, x: Ctx) -> Check:
    n = s.db.one("SELECT COUNT(*) n FROM redteam_runs WHERE agent=? AND status IN ('done','failed')", (a.id,))["n"]
    return Check("redteam", "Agents have been attacked on purpose", ("LLM01", "ASI01", "ASI10"), "pass" if n else "warn", f"{n} red-team run(s) against this agent finished." if n else "No red-team run against this agent has finished yet.",
                 "" if n else "Install a red-team tool and run it against this agent.", "/security/#redteam")


def a_inter_agent(s: Any, a: Any, x: Ctx) -> Check:
    if not (a.sub_agents or a.can_spawn or a.a2a):
        return na("inter-agent", "Agents talk to each other only inside this app", ("ASI07",), "It does not work with other agents.", "built-in")
    return x.shared["inter-agent"]


def shared(cid: str) -> Fn:
    return lambda s, a, x: x.shared[cid]


# The same 26 checks as the overall card, in the same order, then the checks that only make sense for one agent.
CHECKS: tuple[tuple[str, Fn], ...] = (
    ("guard-input", a_guard_input), ("guard-model", a_guard_model), ("guard-tool", a_guard_tool), ("guard-output", a_guard_output), ("guard-leaks", a_guard_leaks),
    ("sandbox-network", a_sandbox_net), ("sandbox-only", a_sandbox_only), ("least-privilege", a_least_privilege), ("limits", a_limits), ("spawn-rules", a_spawn),
    ("external-agents", a_external), ("mcp-pinned", a_mcp_pins), ("mcp-writes", a_mcp_writes), ("aibom", shared("aibom")), ("telemetry", shared("telemetry")),
    ("ocsf", shared("ocsf")), ("memory-pii", a_memory_pii), ("memory-review", a_memory_review), ("retrieved-text", a_retrieved), ("vector-stores", a_vector_stores),
    ("identity", a_identity), ("inter-agent", a_inter_agent), ("evals", a_evals), ("redteam", a_redteam), ("transparency", shared("transparency")),
    ("secrets-write-only", shared("secrets-write-only")),
    ("permission-mode", a_permissions), ("agent-data", a_data), ("agent-exposure", a_exposure), ("agent-runtime", a_runtime), ("agent-framework", a_framework),
    ("agent-remote", a_remote))


# ----------------------------------------------------------------------------------------------------------------- cards
def evaluate(s: Any, a: Any, x: Ctx) -> list[Check]:
    return [fn(s, a, x) for _, fn in CHECKS]


def public(c: Check) -> dict:
    return {**c.public(), "scope": "shared" if c.id in SHARED else "agent"}


def agent_info(a: Any) -> dict:
    return dict(id=a.id, name=a.name, builtin=a.builtin, framework=a.framework, runtime=a.runtime, tools=len(a.tools), mcp=len(a.mcp),
                memory=bool(a.memory), api_access=a.api_access, a2a=a.a2a)


def card(s: Any, a: Any, x: Ctx | None = None) -> dict:
    """The full scorecard of one agent, in the same shape as the overall card (plus `agent`, `scope` and `na` counts)."""
    x = x or Ctx(s)
    checks = evaluate(s, a, x)
    live = [c for c in checks if c.status != NA]
    frameworks = []
    for fid, name, url, risks in FRAMEWORKS:
        out = []
        for rid, rname in risks:
            mine = [c for c in checks if rid in c.maps]
            used = [c for c in mine if c.status != NA]
            status = min((c.status for c in used), key=lambda v: RANK[v]) if used else (NA if mine else "none")
            out.append(dict(id=rid, name=rname, status=status, checks=[c.id for c in mine]))
        frameworks.append(dict(id=fid, name=name, url=url, risks=out, covered=sum(1 for r in out if r["status"] != "none")))
    counts = {k: sum(1 for c in checks if c.status == k) for k in ("pass", "warn", "fail", NA)}
    percent = round(100 * sum(POINTS[c.status] for c in live) / len(live))
    return dict(generated=time.time(), scope="agent", agent=agent_info(a), percent=percent, band=band(percent), counts=counts,
                checks=[public(c) for c in checks], frameworks=frameworks,
                note="This judges one agent's own settings against the OWASP lists. Checks that cannot apply to it are marked not applicable and left out of its score. "
                     "It cannot tell whether an attack would work; the red-team tools test that.")


def ranking(s: Any, x: Ctx | None = None) -> list[dict]:
    """Every agent with its score and its most serious open issue, worst first."""
    x = x or Ctx(s)
    rows = []
    for a in x.agents:
        c = card(s, a, x)
        open_ = [ch for ch in c["checks"] if ch["status"] in ("fail", "warn")]
        top = min(open_, key=lambda ch: RANK[ch["status"]]) if open_ else None
        rows.append(dict(**agent_info(a), percent=c["percent"], band=c["band"], counts=c["counts"],
                         top_issue={k: top[k] for k in ("id", "title", "status", "detail", "fix", "link")} if top else None))
    rows.sort(key=lambda r: (r["percent"], -r["counts"]["fail"], -r["counts"]["warn"], r["name"].lower()))
    return rows


def summary(s: Any, rows: list[dict] | None = None) -> dict:
    """What the overall card says about the agents: how many, the average, the weakest one and how many sit in each band."""
    rows = rows if rows is not None else ranking(s)
    bands = {name: sum(1 for r in rows if r["band"] == name) for name, _ in BANDS}
    low = rows[0] if rows else None
    return dict(count=len(rows), average=round(sum(r["percent"] for r in rows) / len(rows)) if rows else None, bands=bands,
                lowest={k: low[k] for k in ("id", "name", "percent")} if low else None)


def brief(s: Any, a: Any, x: Ctx | None = None) -> dict:
    """A score and counts only (for the AI-BOM): never the details of what is weak."""
    c = card(s, a, x)
    return dict(percent=c["percent"], band=c["band"], fail=c["counts"]["fail"], warn=c["counts"]["warn"])


# --------------------------------------------------------------------------------------------------------------- exports
def _lines(c: dict) -> list[str]:
    by_id = {ch["id"]: ch for ch in c["checks"]}
    out: list[str] = []
    for fw in c["frameworks"]:
        out += [f"## {fw['name']}", f"Source: {fw['url']}", ""]
        for r in fw["risks"]:
            out.append(f"### {r['id']} {r['name']} - {STATUS_WORD[r['status']]}")
            for cid in r["checks"]:
                ch = by_id[cid]
                out.append(f"- **{STATUS_WORD[ch['status']]}** {ch['title']} ({ch['scope']}): {ch['detail']}" + (f" Fix: {ch['fix']}" if ch["fix"] else ""))
            if not r["checks"]:
                out.append("- No control in Agent Studio is mapped to this risk yet.")
            out.append("")
    return out


def card_markdown(c: dict) -> str:
    n = c["counts"]
    a = c["agent"]
    return "\n".join([f"# Security scorecard: {a['name']} ({a['id']})", "",
                      f"Score: {c['percent']}% ({n['pass']} pass, {n['warn']} warn, {n['fail']} fail, {n['na']} not applicable)", "", c["note"], "",
                      *_lines(c)])


def report_markdown(rows: list[dict], cards: list[dict], overall: dict | None = None) -> str:
    """All agents in one report: a ranking table, then each agent's open issues."""
    lines = ["# Agent Studio security report: all agents", ""]
    if overall:
        lines += [f"Overall (app settings) score: {overall['percent']}%", ""]
    lines += ["| Agent | Score | Fail | Warn | Most serious issue |", "|---|---|---|---|---|"]
    for r in rows:
        top = r["top_issue"]
        lines.append(f"| {r['name']} (`{r['id']}`) | {r['percent']}% | {r['counts']['fail']} | {r['counts']['warn']} | {top['title'] if top else 'none'} |")
    lines.append("")
    for c in cards:
        open_ = sorted((ch for ch in c["checks"] if ch["status"] in ("fail", "warn")), key=lambda ch: RANK[ch["status"]])
        lines += [f"## {c['agent']['name']} - {c['percent']}%", ""]
        lines += [f"- **{STATUS_WORD[ch['status']]}** {ch['title']}: {ch['detail']}" + (f" Fix: {ch['fix']}" if ch["fix"] else "") for ch in open_] or ["- Nothing to fix."]
        lines.append("")
    return "\n".join(lines)


def to_json(obj: dict) -> str:
    return json.dumps(obj, indent=2)
