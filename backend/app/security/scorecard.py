"""The security scorecard: OWASP Top 10 for LLM Applications (2025) and OWASP Top 10 for Agentic Applications (2026), each risk
mapped to controls this app really has, with a live status computed from the user's ACTUAL settings.

Honesty rules: a check is green only when the thing it names is on right now; a risk with no check says "not covered"; checks about
how the app is built (not a setting) are marked `built-in` and are always green because they cannot be switched off. The score is
a to-do list, not a certificate: it measures configuration, never whether an attack would succeed (the red-team tools do that).

This is the OVERALL card: it judges the app's settings (guardrails as the default profiles apply them, sandbox, limits, aggregates over
your own agents) and does not average the agents. `agentcard.py` runs the same checks against one agent's own configuration; see its
docstring for how the two fit together. `build` also carries a short summary of the per-agent scores under `agents`."""
from __future__ import annotations

import json
import re
import shutil
import time
from typing import Any, Callable

LLM_2025 = (("LLM01", "Prompt Injection"), ("LLM02", "Sensitive Information Disclosure"), ("LLM03", "Supply Chain"),
            ("LLM04", "Data and Model Poisoning"), ("LLM05", "Improper Output Handling"), ("LLM06", "Excessive Agency"),
            ("LLM07", "System Prompt Leakage"), ("LLM08", "Vector and Embedding Weaknesses"), ("LLM09", "Misinformation"),
            ("LLM10", "Unbounded Consumption"))
ASI_2026 = (("ASI01", "Agent Goal Hijack"), ("ASI02", "Tool Misuse and Exploitation"), ("ASI03", "Identity and Privilege Abuse"),
            ("ASI04", "Agentic Supply Chain Vulnerabilities"), ("ASI05", "Unexpected Code Execution"), ("ASI06", "Memory and Context Poisoning"),
            ("ASI07", "Insecure Inter-Agent Communication"), ("ASI08", "Cascading Failures"), ("ASI09", "Human-Agent Trust Exploitation"),
            ("ASI10", "Rogue Agents"))
FRAMEWORKS = (("llm2025", "OWASP Top 10 for LLM Applications 2025", "https://genai.owasp.org/llm-top-10/", LLM_2025),
              ("asi2026", "OWASP Top 10 for Agentic Applications 2026", "https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/", ASI_2026))
RANK = {"fail": 0, "warn": 1, "pass": 2}
POINTS = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
SECURITY = "/settings/#security"
PINNED = re.compile(r"(@\d+\.\d+\.\d+|==\d|@sha256:|:[0-9]+\.[0-9]+)")


class Check:
    def __init__(self, id: str, title: str, maps: tuple, status: str, detail: str, fix: str = "", link: str = SECURITY, kind: str = "setting"):
        self.id, self.title, self.maps, self.status, self.detail, self.fix, self.link, self.kind = id, title, maps, status, detail, fix, link, kind

    def public(self) -> dict:
        return dict(id=self.id, title=self.title, maps=list(self.maps), status=self.status, detail=self.detail, fix=self.fix, link=self.link, kind=self.kind)


def _stages(s, stage: str) -> list:
    return s.guardrails.active_profiles([], stage) if s.guardrails.enabled() else []


def _blocking(profiles: list) -> list:
    return [p for p in profiles if p.mode == "block"]


# ---------------------------------------------------------------------------------------------------------- checks
def c_guard_input(s) -> Check:
    on = _stages(s, "input")
    if not s.guardrails.enabled():
        return Check("guard-input", "Guardrails check what goes into models", ("LLM01", "ASI01"), "fail", "Guardrails are switched off.",
                     "Turn guardrails on and add a profile (Security > Guardrails).")
    if _blocking(on):
        return Check("guard-input", "Guardrails check what goes into models", ("LLM01", "ASI01"), "pass", f"{len(on)} profile(s) check inputs and tool results; {len(_blocking(on))} can block.")
    if on:
        return Check("guard-input", "Guardrails check what goes into models", ("LLM01", "ASI01"), "warn", "Inputs are checked but only recorded, nothing is blocked.",
                     "Switch a profile from 'record only' to 'block' once you trust its results.")
    return Check("guard-input", "Guardrails check what goes into models", ("LLM01", "ASI01"), "fail", "Guardrails are on, but no profile looks at inputs.", "Tick 'input' on a profile.")


def c_guard_model(s) -> Check:
    smart = [p for p in _stages(s, "input") + _stages(s, "output") if p.kind != "rules"]
    if smart:
        return Check("guard-model", "A safety model (not only word rules) is in use", ("LLM01", "LLM05"), "pass", "Using: " + ", ".join(sorted({p.kind for p in smart})) + ".")
    return Check("guard-model", "A safety model (not only word rules) is in use", ("LLM01", "LLM05"), "warn",
                 "Only pattern rules (or nothing) are active. Rules miss rephrased attacks.",
                 "Add an open-source safety model, the d1 gate or a cloud guardrail.")


def c_guard_tool(s) -> Check:
    on = _stages(s, "tool")
    return Check("guard-tool", "Tool calls are checked before they run", ("ASI02", "LLM06"), "pass" if on else "warn",
                 f"{len(on)} profile(s) check tool-call arguments." if on else "No guardrail looks at tool-call arguments.", "" if on else "Tick 'tool calls' on a profile.")


def c_guard_output(s) -> Check:
    on = _stages(s, "output")
    return Check("guard-output", "Answers are checked before they are shown", ("LLM02", "LLM05", "LLM07"), "pass" if on else "warn",
                 f"{len(on)} profile(s) check answers." if on else "No guardrail looks at what models answer.", "" if on else "Tick 'output' on a profile.")


def c_guard_leaks(s) -> Check:
    profiles = [p for p in _stages(s, "input") + _stages(s, "output") + _stages(s, "tool") if p.kind in ("rules", "bedrock", "model_armor")]
    rules = [p for p in profiles if p.kind != "rules" or {"pii", "secrets"} & set(p.params.get("detect", ["pii", "secrets", "injection"]))]
    return Check("guard-leaks", "Personal data and keys are looked for", ("LLM02", "LLM07"), "pass" if rules else "warn",
                 "At least one active profile looks for personal data or secrets." if rules else "No active profile looks for personal data or secrets.",
                 "" if rules else "Use the built-in rules profile with 'personal data' and 'secrets' ticked.")


def c_sandbox_net(s) -> Check:
    on = s.db.get_setting("sandbox_network", "0") == "1"
    return Check("sandbox-network", "Commands run without network access", ("LLM06", "ASI05"), "warn" if on else "pass",
                 "The sandbox can reach the internet." if on else "The sandbox has no network.", "Switch 'allow network inside the sandbox' off." if on else "", "/settings/#harness")


def c_sandbox_design(s) -> Check:
    docker = bool(shutil.which("docker"))
    return Check("sandbox-only", "Commands run only inside a Docker sandbox", ("ASI05", "LLM05", "LLM06"), "pass",
                 "run_shell has no fallback to your own PC" + ("" if docker else " (Docker is not installed here, so commands cannot run at all)") + ". Every file write is verified and rolled back if it breaks.",
                 kind="built-in")


def c_least_privilege(s) -> Check:
    risky = [a.id for a in s.agents.list() if not a.builtin and {"run_shell", "write_file", "edit_file"} & set(a.tools)]
    return Check("least-privilege", "Your own agents hold only the tools they need", ("LLM06", "ASI02", "ASI03"), "warn" if risky else "pass",
                 f"{len(risky)} of your agents can change files or run commands: {', '.join(risky[:5])}." if risky else "None of your own agents can change files or run commands.",
                 "Open each agent and untick tools it does not need." if risky else "", "/agents/")


def c_limits(s) -> Check:
    g = s.db.get_setting
    calls, spawns = int(g("max_llm_calls_per_turn", "40")), int(g("max_spawns_per_turn", "4"))
    big = [f"{p.label} cap ${p.cap_usd:g}" for p in s.providers.list() if p.enabled and p.kind not in ("ollama", "mock") and p.cap_usd > 100]
    bad = calls > 100 or spawns > 8 or big
    detail = f"At most {calls} model calls and {spawns} created agents per turn" + (f"; high caps: {', '.join(big)}" if big else "; spend caps are always enforced") + "."
    return Check("limits", "Runaway loops and spending are capped", ("LLM10", "ASI08"), "warn" if bad else "pass", detail,
                 "Lower the per-turn limits or the provider spend caps." if bad else "", "/settings/#harness")


def c_spawning(s) -> Check:
    return Check("spawn-rules", "Agents that create agents cannot extend themselves", ("ASI10", "ASI08"), "pass",
                 "A created agent can never create skills, MCP servers or more agents, and gets only tools its creator already has.", kind="built-in")


def c_external(s) -> Check:
    auto = [a.id for a in s.agents.list() if a.acp_agents and a.acp_policy == "reads_auto"]
    on = s.db.get_setting("agents_can_call_external", "1") == "1" and any(a.acp_agents for a in s.agents.list())
    if auto:
        return Check("external-agents", "External agents ask before they act", ("ASI02", "ASI03", "ASI09"), "warn", f"Agents with automatic read approval: {', '.join(auto[:5])}.",
                     "Set their permissions to 'ask' or 'read-only'.", "/agents/")
    return Check("external-agents", "External agents ask before they act", ("ASI02", "ASI03", "ASI09"), "pass",
                 "External agents may be called, and every permission request waits for you." if on else "No agent calls external agents.", "", "/agents/")


def c_mcp_pins(s) -> Check:
    loose = []
    for m in s.mcp.list():
        if m.enabled and m.transport == "stdio" and (m.command or "") in ("npx", "uvx", "pipx") and not any(PINNED.search(a) for a in m.args):
            loose.append(m.id)
    return Check("mcp-pinned", "MCP servers are pinned to exact versions", ("LLM03", "ASI04"), "warn" if loose else "pass",
                 f"Not pinned: {', '.join(loose[:6])}." if loose else "Every enabled MCP server that downloads a package names an exact version.",
                 "Edit the server's arguments to include an exact version (name@1.2.3)." if loose else "", "/mcp/")


def c_mcp_writes(s) -> Check:
    from .. import mcp_catalog
    writes = [k for k, v in mcp_catalog.installed(s.mcp).items() if v.get("allow_writes")]
    return Check("mcp-writes", "Catalog servers stay read-only", ("LLM06", "ASI02"), "warn" if writes else "pass",
                 f"Writes are allowed for: {', '.join(writes[:6])}." if writes else "No catalog server may change data.",
                 "Switch their 'allow changes' off unless you need it." if writes else "", "/catalog/")


def c_aibom(s) -> Check:
    from ..ard.settings import DiscoverySettings
    d = DiscoverySettings(s.db)
    cfg = d.get()
    exposed = cfg["enabled"] and cfg["level"] != "public" and not s.db.get_setting("ard_token_hash")
    if exposed:
        return Check("aibom", "AI bill of materials is available and shared safely", ("LLM03", "ASI04"), "fail", "Discovery is on at a detailed level without an access token.",
                     "Set an access token or lower the level.", "/settings/#discovery")
    return Check("aibom", "AI bill of materials is available and shared safely", ("LLM03", "ASI04"), "pass",
                 "A CycloneDX AI-BOM can be exported at any time" + ("; discovery publishing is on." if cfg["enabled"] else "; discovery publishing is off."), kind="built-in", link="/discovery/")


def c_telemetry(s) -> Check:
    on = s.db.get_setting("otel_enabled", "1") == "1"
    return Check("telemetry", "Every run is recorded", ("ASI10", "ASI09"), "pass" if on else "fail", "Runs, model calls and tool calls are stored." if on else "Telemetry is off, so nothing is recorded.",
                 "" if on else "Turn on 'Store OpenTelemetry data'.", "/settings/#telemetry")


def c_ocsf(s) -> Check:
    cfg = s.ocsf.config()
    sinks = [k for k in cfg.get("sinks", []) if k.get("enabled")]
    broken = [k for k in sinks if (lambda st: st.get("failed", 0) > 0 and not st.get("sent", 0))(s.ocsf.sink_stats.get(k.get("id"), {}))]
    if cfg.get("enabled") and sinks and len(broken) == len(sinks):
        err = (s.ocsf.sink_stats.get(broken[0].get("id"), {}).get("last_error") or "see the destination's test result")[:120]
        return Check("ocsf", "Security events go to your security tools", ("ASI10", "LLM10"), "warn",
                     f"OCSF export is on, but no destination has received an event: {err}.", "Open Settings > Telemetry > Security export and use 'Test' on the destination.", "/settings/#telemetry")
    if cfg.get("enabled") and sinks:
        return Check("ocsf", "Security events go to your security tools", ("ASI10", "LLM10"), "pass", f"OCSF export is on with {len(sinks)} destination(s).", link="/settings/#telemetry")
    return Check("ocsf", "Security events go to your security tools", ("ASI10", "LLM10"), "warn", "No OCSF destination is receiving events.",
                 "Add a destination under Telemetry > Security export.", "/settings/#telemetry")


def c_memory_pii(s) -> Check:
    cfg = s.memory.config()
    return Check("memory-pii", "Personal data is removed before saving to memory", ("LLM02", "ASI06"), "pass" if cfg["redact_pii"] else "warn",
                 "Memories are cleaned of personal data first." if cfg["redact_pii"] else "Memories may contain personal data.",
                 "" if cfg["redact_pii"] else "Turn on 'remove personal data' in Memory settings.", "/memory/")


def c_memory_review(s) -> Check:
    cfg = s.memory.config()
    auto = [a.id for a in s.agents.list() if a.memory.get("auto_save") == "auto"]
    bad = auto and not cfg["review"]
    return Check("memory-review", "New memories can be reviewed before they are trusted", ("LLM04", "ASI06"), "warn" if bad else "pass",
                 f"Agents that save on their own without review: {', '.join(auto[:5])}." if bad else "New memories wait for your approval, or no agent saves on its own.",
                 "Turn on 'review before remembering'." if bad else "", "/memory/")


def c_retrieval(s) -> Check:
    on = _stages(s, "input")
    return Check("retrieved-text", "Text from files, the web and documents is checked as data", ("LLM04", "LLM08", "ASI01", "ASI06"), "pass" if on else "warn",
                 "Tool results and retrieved passages are scanned by the input guardrail before the model reads them." if on else "Retrieved text and tool results reach the model unchecked.",
                 "" if on else "Turn on an input guardrail.")


def c_vector_stores(s) -> Check:
    bad = []
    for st in s.rag.list_stores():
        url = str((st.get("config") or {}).get("url", ""))
        if url.startswith("http://") and not re.match(r"http://(localhost|127\.0\.0\.1|\[::1\])", url):
            bad.append(st["id"])
    return Check("vector-stores", "Vector stores are reached over a secure connection", ("LLM08",), "warn" if bad else "pass",
                 f"Plain http:// to a remote host: {', '.join(bad)}." if bad else "Every store is local or uses https://.", "Use https:// for remote stores." if bad else "", "/knowledge/")


def c_identity(s) -> Check:
    cfg = s.identity.config()
    st = s.identity.stats()
    if cfg["enforce"]:
        return Check("identity", "Agents have identities and hand-overs are enforced", ("ASI03", "ASI07"), "pass",
                     f"Hand-overs between agents are checked and refused when not allowed ({cfg['provider']['kind']} issuer; {st['refused']} refused this week).", link=SECURITY)
    return Check("identity", "Agents have identities and hand-overs are enforced", ("ASI03", "ASI07"), "warn",
                 f"Every agent has an identity and hand-overs are logged ({st['delegated']} this week), but a refused hand-over is not stopped.",
                 "Turn on 'enforce hand-overs' once the log looks right.")


def c_inter_agent(s) -> Check:
    return Check("inter-agent", "Agents talk to each other only inside this app", ("ASI07",), "pass",
                 "Agents call each other in-process with separate context; no network address for agent-to-agent traffic is open.", kind="built-in")


def c_evals(s) -> Check:
    from . import evidence
    st, detail, fix = evidence.evals(s)
    return Check("evals", "Agents are tested regularly", ("LLM09", "ASI09"), st, detail, fix, "/evals/")


def c_redteam(s) -> Check:
    from . import evidence
    st, detail, fix = evidence.redteam(s)
    return Check("redteam", "Agents have been attacked on purpose", ("LLM01", "ASI01", "ASI10"), st, detail, fix, "/security/")


def c_transparency(s) -> Check:
    return Check("transparency", "Answers show how they were made", ("ASI09", "LLM09"), "pass",
                 "Every answer lists the tools used, the steps, the cost and what memory was used, so a person can check the work.", kind="built-in")


def c_secrets(s) -> Check:
    return Check("secrets-write-only", "Keys are write-only and never exported", ("LLM02", "ASI03"), "pass",
                 "API keys, tokens and credentials are never shown again, never put in exports or the AI-BOM, and prompts are never sent to security tools.", kind="built-in")


CHECKS: tuple[Callable[[Any], Check], ...] = (
    c_guard_input, c_guard_model, c_guard_tool, c_guard_output, c_guard_leaks, c_sandbox_net, c_sandbox_design, c_least_privilege, c_limits, c_spawning,
    c_external, c_mcp_pins, c_mcp_writes, c_aibom, c_telemetry, c_ocsf, c_memory_pii, c_memory_review, c_retrieval, c_vector_stores, c_identity,
    c_inter_agent, c_evals, c_redteam, c_transparency, c_secrets)


def build(s) -> dict:
    checks = [c(s) for c in CHECKS]
    frameworks = []
    for fid, name, url, risks in FRAMEWORKS:
        out = []
        for rid, rname in risks:
            mine = [c for c in checks if rid in c.maps]
            status = min((c.status for c in mine), key=lambda x: RANK[x]) if mine else "none"
            out.append(dict(id=rid, name=rname, status=status, checks=[c.id for c in mine]))
        frameworks.append(dict(id=fid, name=name, url=url, risks=out, covered=sum(1 for r in out if r["status"] != "none")))
    counts = {k: sum(1 for c in checks if c.status == k) for k in ("pass", "warn", "fail")}
    percent = round(100 * sum(POINTS[c.status] for c in checks) / len(checks))
    from . import agentcard
    return dict(generated=time.time(), percent=percent, counts=counts, checks=[c.public() for c in checks], frameworks=frameworks, agents=agentcard.summary(s),
                note="This measures how your settings line up with the OWASP lists. It cannot tell whether an attack would work; the red-team tools test that.")


def to_markdown(card: dict) -> str:
    icon = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "none": "NOT COVERED"}
    by_id = {c["id"]: c for c in card["checks"]}
    lines = ["# Agent Studio security scorecard", "", f"Score: {card['percent']}% ({card['counts']['pass']} pass, {card['counts']['warn']} warn, {card['counts']['fail']} fail)", "", card["note"], ""]
    for fw in card["frameworks"]:
        lines += [f"## {fw['name']}", f"Source: {fw['url']}", ""]
        for r in fw["risks"]:
            lines.append(f"### {r['id']} {r['name']} - {icon[r['status']]}")
            for cid in r["checks"]:
                c = by_id[cid]
                lines.append(f"- **{icon[c['status']]}** {c['title']}: {c['detail']}" + (f" Fix: {c['fix']}" if c["fix"] else ""))
            if not r["checks"]:
                lines.append("- No control in Agent Studio is mapped to this risk yet.")
            lines.append("")
    return "\n".join(lines)


def to_json(card: dict) -> str:
    return json.dumps(card, indent=2)
