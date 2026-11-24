"""Agent definitions (stored in SQLite) + the seeded team."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import agui_flags, live_profile
from .db import Database
from .harness.tools import ALL_TOOLS, READ_ONLY_TOOLS
from .identity.model import clean_identity
from .memory.model import clean_agent_memory, uses_records
from .skills import GROUP_PREFIX, GROUP_RE

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")  # also a valid Python identifier (ADK agent names)
GUARD_RE = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")  # a guardrail profile id (see guardrails/service.py)
KB_REF = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")  # a knowledge base id (see rag/service.py)

ARTIFACT_RULES = """

## Results and rich answers
- Keep deliverables the user will want later (a report, a chart image, a data file, a model) with `save_artifact`; files you create in the workspace are also collected automatically.
- Besides Markdown, tables and code, your answer can show: a ```chart block (JSON: {"type": "bar" or "line", "title": "...", "data": [{"label": "...", "value": 1}]} or {"x": [...], "series": [{"name": "...", "values": [...]}]}),
  a ```diff block, a ```csv block, and a ```form block (JSON: {"title": "...", "fields": [{"name": "...", "label": "...", "type": "text|select|checkbox|number", "options": [...]}]}) when you need the user to choose or confirm something.
- Also available: a ```table block for data the user may sort, filter and export (JSON: {"title": "...", "columns": ["name", "total"] or [{"key": "name", "label": "Name", "type": "text|number|date|boolean"}], "rows": [["a", 1], ...]}),
  a ```jsonview block (any JSON, shown as a collapsible tree), a ```html-preview block (a small self-contained HTML page, shown in a locked frame: no scripts, no network, inline CSS only),
  and ```audio / ```video blocks that play a file you saved: put the artifact id (12 characters, from `save_artifact`) as the body, or JSON {"artifact": "<id>", "title": "...", "captions": "<id of a .vtt file>"}.
- Never use remote images, remote audio or video, or HTML outside an html-preview block: they are not shown.
"""

# Saving results is one thing, showing rich blocks in the answer another: an agent with AG-UI components switched off is only told the first.
ARTIFACT_KEEP = ARTIFACT_RULES.split("- Besides Markdown")[0].replace("## Results and rich answers", "## Results")


def artifact_rules(components: bool = True) -> str:
    return ARTIFACT_RULES if components else ARTIFACT_KEEP


HARNESS_RULES = """

## Working rules (tool harness)
- Explore before acting: list_dir / search_text / read_file. Never guess file contents.
- Edit with edit_file (small exact replacements). Re-read after a failed edit; never retry blindly.
- Every write/edit is auto-verified (syntax/lint). If it reports a failure the change was rolled back: fix it.
  A 'skipped' check means the file was NOT verified - run its tests or build with run_shell.
- run_shell runs only in an isolated Docker sandbox (no network by default). Use it to run tests.
- Paths are relative to the workspace root. Finish by stating what changed and how you verified it.
"""


@dataclass
class AgentDef:
    id: str
    name: str
    description: str = ""
    instruction: str = ""
    provider: str | None = None
    tier: str = "flash"
    model: str | None = None
    tools: list[str] = field(default_factory=list)
    mcp: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    sub_agents: list[str] = field(default_factory=list)
    builtin: bool = False
    can_spawn: bool = False   # may create + run its own sub-agents (spawn_agent); also needs the global switch
    acp_agents: list[str] = field(default_factory=list)   # external ACP agents it may call (call_external_agent)
    acp_policy: str = "ask"   # permissions for those agents: ask (you approve in chat) | reads_auto | readonly
    rag: list[str] = field(default_factory=list)          # knowledge bases it may search (rag_search); ["*"] = all of them
    memory: dict = field(default_factory=dict)            # what it remembers: {types, read_scopes, write_scopes, auto_recall, auto_save}; {} = off
    guardrails: list[str] = field(default_factory=list)   # guardrail profile ids; [] = the default profiles, ["none"] = no guardrails for this agent
    identity: dict = field(default_factory=dict)          # {scopes: [...], delegates: [...]}: what its identity may be used for; {} = the defaults
    framework: str = "adk"                                # which agent framework runs it (see frameworks/catalog.py); adk = built in
    api_access: bool = False                              # callable through the authenticated OpenAI-compatible API (/v1)
    a2a: bool = False                                     # reachable by other agents over A2A
    runtime: str = "local"                                # where it runs: local, docker, agentcore, vertex or foundry (see runtimes/)
    sandbox: str = ""                                     # where its commands run: "" = the default sandbox, docker-container, docker-microvm (see sandboxes/)
    data: list[str] = field(default_factory=list)         # data sources it may read (see datasources/); ["*"] = all of them
    live: dict = field(default_factory=dict)              # Live profile: engine, voice-or-avatar mode, voice, language, budget ... ({} = none; see live_profile.py)
    agui: dict = field(default_factory=dict)              # AG-UI switches {components, endpoint}: rich components in its answers, and the AG-UI stream endpoint (see agui_flags.py)

    @classmethod
    def from_row(cls, r) -> "AgentDef":
        return cls(id=r["id"], name=r["name"], description=r["description"], instruction=r["instruction"], provider=r["provider"],
                   tier=r["tier"], model=r["model"], tools=json.loads(r["tools"]), mcp=json.loads(r["mcp"]),
                   skills=json.loads(r["skills"]), sub_agents=json.loads(r["sub_agents"]), builtin=bool(r["builtin"]),
                   can_spawn=bool(r["can_spawn"]), acp_agents=json.loads(r["acp_agents"] or "[]"), acp_policy=r["acp_policy"] or "ask",
                   rag=json.loads(r["rag"] or "[]"), memory=json.loads(r["memory"] or "{}"),
                   guardrails=json.loads(r["guardrails"] or "[]"), identity=json.loads(r["identity"] or "{}"),
                   framework=r["framework"] or "adk", api_access=bool(r["api_access"]), a2a=bool(r["a2a"]),
                   runtime=r["runtime"] or "local", sandbox=r["sandbox"] or "", data=json.loads(r["data"] or "[]"),
                   agui=agui_flags.flags(json.loads(r["agui"] or "{}")), live=live_profile.profile(json.loads(r["live"] or "{}")))


# Wording of the Super Engineer's "Build" step before the coding harnesses were added: existing databases are upgraded
# only if their instruction still contains it verbatim (user edits are never overwritten).
OLD_APP_BUILDER = ("You build small, clear web apps for the user. Work only through the app_* tools. First understand what the app should do and which of the user's agents it should call "
                   "(app_agents), start from the closest template (app_create) or open the user's app (app_list, app_tree, app_read_file), then make focused changes with app_write_file / "
                   "app_patch_file and declare the agents and permissions with app_set_manifest. Keep the page accessible, readable on a phone and free of external scripts, styles and fonts; "
                   "keep secrets out of files; use no third-party packages. When you finish run app_check, fix what it reports, and tell the user what you built, which agents it calls and what it may reach.")
APP_BUILDER_TOOLS = ["app_agents", "app_list", "app_create", "app_tree", "app_read_file", "app_write_file", "app_patch_file", "app_delete_file", "app_set_manifest", "app_check",
                     "app_plan", "app_plan_done", "app_verify", "app_components", "app_theme", "app_connect_agent", "app_data_sources", "app_connect_data", "app_undo", "load_skill"]
NEW_APP_BUILDER = ("You are App Builder, a careful senior front-end developer who builds small web apps with the user. Work only through the app_* tools and follow the Apps rules you are given: "
                   "plan first (app_plan), scaffold from a template, add ready-made components, then change files in small patches; run app_verify after each round and fix exactly what it reports, "
                   "stopping honestly when it says give_up. Keep the result accessible, readable on a phone, without external scripts, styles or fonts and without secrets. "
                   "Finish by saying, briefly, what you built, which agents and data it uses and what it may reach.")
OLD_SE_STEP = (
    "or create a specialist with spawn_agent when the work needs a distinct focus. For large self-contained work, or a "
    "second opinion from another model, use call_external_agent (see the delegate-to-external-agents skill) — and verify "
    "its work yourself.\n")
NEW_SE_STEP = (
    "or create a specialist with spawn_agent when the work needs a distinct focus. For large self-contained coding work "
    "default to call_external_agent('claude', ...) (Claude Code); use 'codex' (OpenAI Codex) for a second opinion, 'aider' for "
    "small targeted multi-file edits and 'openhands' for autonomous runs on the user's own model provider (see the "
    "delegate-to-external-agents skill) — and ALWAYS verify its work yourself (read the diff, run the tests).\n")

SEEDS = [
    AgentDef(
        id="engineer", name="Engineer", tier="pro", builtin=True,
        description="Lead engineer: plans, delegates exploration/implementation/review to sub-agents, verifies with tests.",
        instruction=("You are the lead engineer. For non-trivial work: (1) delegate repository exploration to `explorer`, "
                     "(2) make a short plan, (3) delegate focused implementation to `coder`, (4) ask `reviewer` to check the result, "
                     "(5) run the tests yourself with run_shell. Sub-agents have isolated context: give each a complete, self-contained "
                     "request (paths, goal, constraints) and ask for a concise report. Do small tasks directly. Never claim success without verification."),
        tools=list(ALL_TOOLS), mcp=["memory", "fetch"], skills=["*"], sub_agents=["explorer", "coder", "reviewer"], can_spawn=True),
    AgentDef(
        id="explorer", name="Explorer", tier="flash", builtin=True,
        description="Read-only code explorer. Give it a question about the repository; it returns a concise, file-referenced answer.",
        instruction="You explore a codebase read-only and answer precisely with file:line references. Be concise; do not dump whole files.",
        tools=list(READ_ONLY_TOOLS), skills=["repo-orientation"]),
    AgentDef(
        id="coder", name="Coder", tier="flash", builtin=True,
        description="Implements a well-specified change (edits files, runs tests) and reports a diff summary + verification result.",
        instruction="You implement exactly the change requested, minimal and idiomatic. Read first, edit with edit_file, then run the relevant tests. Report: files changed, test output summary.",
        tools=["read_file", "list_dir", "glob_files", "search_text", "write_file", "edit_file", "run_shell", "load_skill"], skills=["debugging", "test-writing"]),
    AgentDef(
        id="reviewer", name="Reviewer", tier="flash", builtin=True,
        description="Reviews a change for correctness, edge cases and missing tests; can run tests. Does not edit files.",
        instruction="You review code changes critically: correctness, edge cases, security, missing tests. You cannot edit. Report concrete issues with file:line, ordered by severity; say 'no issues' only if you checked.",
        tools=list(READ_ONLY_TOOLS) + ["run_shell", "load_skill"], skills=["code-review"]),
    AgentDef(
        id="assistant", name="Assistant", tier="flash", builtin=True,
        description="Plain conversational assistant (no tools).",
        instruction="You are Agent Studio's assistant. Be concise and accurate."),
    AgentDef(
        id="app_builder", name="App Builder", tier="pro", builtin=True,
        description="Builds small web apps with you from standard templates: static pages that call your agents, or a tiny Node / Python server. Writes only inside the app's folder; you review its changes.",
        instruction=NEW_APP_BUILDER,
        tools=list(APP_BUILDER_TOOLS)),
    AgentDef(
        id="science_lab", name="Science Lab", tier="pro", builtin=True,
        description="A careful research scientist for biology, chemistry, physics and maths: reads open databases, computes locally, keeps the evidence for every claim and writes a labelled report.",
        instruction=("You are a research scientist working in the Science Lab. Start an investigation (science_start), find what is known with science_search (science_sources lists the sources), "
                     "compute with science_calc or science_run_python, keep competing hypotheses and judge them against the evidence (science_add_hypothesis, science_compare), attach evidence "
                     "(science_add_evidence) and record claims (science_add_claim), check citations (science_check_citations), then answer from science_report. Never state what the evidence does not say."),
        tools=["science_sources", "science_start", "science_search", "science_calc", "science_run_python", "science_add_evidence", "science_add_claim", "science_add_hypothesis", "science_compare",
               "science_check_citations", "science_report", "render_ui", "ui_catalog", "load_skill"], skills=["science-literature-review", "science-hypothesis-ranking"]),
    AgentDef(
        id="security_auditor", name="Security Auditor", tier="pro", builtin=True,
        description="Read-only security specialist: scans code and dependencies (Semgrep, Snyk), verifies each finding by reading the code, and reports exploitable issues with fixes.",
        instruction=("You are an application-security engineer. Follow the security-audit skill. Scan with the Semgrep / Snyk tools you have, "
                     "then verify every finding by tracing the data flow in the code — report only what you can justify, each with severity, "
                     "path:line, attacker impact, evidence and the minimal fix. List what you did not check. You never edit files."),
        tools=list(READ_ONLY_TOOLS) + ["run_shell", "load_skill"], mcp=["semgrep", "snyk"],
        skills=["security-audit", "code-review"]),
    AgentDef(
        id="super_engineer", name="Super Engineer", tier="pro", builtin=True,
        description=("Senior engineer that plans, codes, tests, and hardens: delegates to its team (explorer, coder, reviewer, security "
                     "auditor), builds its own specialists, and can hand large tasks to external coding harnesses (Claude Code by default, Codex, Aider, OpenHands, Cursor, Kiro...)."),
        instruction=(
            "You are a principal software engineer responsible for shipping correct, secure, maintainable code.\n\n"
            "Workflow for anything non-trivial:\n"
            "1. Understand: explore the repository first (delegate broad exploration to `explorer`); restate the goal and the "
            "definition of done.\n"
            "2. Plan: a short numbered plan; identify risks (security, data loss, breaking APIs) and how you'll verify each step.\n"
            "3. Build: implement in small verified steps — do focused edits yourself, delegate well-specified chunks to `coder`, "
            "or create a specialist with spawn_agent when the work needs a distinct focus. For large self-contained coding work "
            "default to call_external_agent('claude', ...) (Claude Code); use 'codex' (OpenAI Codex) for a second opinion, 'aider' for "
            "small targeted multi-file edits and 'openhands' for autonomous runs on the user's own model provider (see the "
            "delegate-to-external-agents skill) — and ALWAYS verify its work yourself (read the diff, run the tests).\n"
            "4. Verify: run the tests in the sandbox; run the code-quality skill on what changed (linters, type checks); for "
            "anything touching input, auth, files, network or dependencies ask `security_auditor` for a review and fix what it confirms.\n"
            "5. Review: have `reviewer` check the final diff.\n"
            "6. Report: what changed (files), how it was verified (exact commands + results), risks and what was NOT verified.\n\n"
            "Rules: never claim success without evidence; prefer the project's existing patterns and tools; keep changes minimal; "
            "never print or commit secrets; ask before destructive operations."),
        tools=list(ALL_TOOLS), mcp=["git", "memory", "fetch", "context7", "semgrep", "eslint", "snyk", "code-sandbox"], skills=["*"],
        sub_agents=["explorer", "coder", "reviewer", "security_auditor"], can_spawn=True,
        acp_agents=["claude", "codex", "aider", "openhands", "cursor", "kiro", "antigravity", "grok"], acp_policy="ask"),
]


class AgentStore:
    def __init__(self, db: Database):
        self.db = db
        for a in SEEDS:
            if not self.get(a.id):
                self._insert(a)
        self._upgrade_super_engineer()
        self._upgrade_app_builder()

    def _upgrade_app_builder(self) -> None:
        """Existing databases keep the seed they were created with: give the built-in App Builder the smarter builder's tools (only ever added, never removed) and its new
        instruction (only where it still has the old seed wording, so a text you edited is left alone)."""
        row = self.db.one("SELECT instruction, tools FROM agents WHERE id='app_builder' AND builtin=1")
        if not row:
            return
        tools = json.loads(row["tools"] or "[]")
        add = [t for t in APP_BUILDER_TOOLS if t not in tools] if "app_plan" not in tools else []        # one-time: a tool you later remove stays removed
        if add:
            self.db.execute("UPDATE agents SET tools=? WHERE id='app_builder'", (json.dumps(tools + add),))
        if row["instruction"] == OLD_APP_BUILDER:
            self.db.execute("UPDATE agents SET instruction=? WHERE id='app_builder'", (NEW_APP_BUILDER,))

    def _upgrade_super_engineer(self) -> None:
        """Existing databases keep the seed they were created with: bring the built-in Super Engineer up to date with the
        new coding harnesses. Harness ids are only ever appended (an empty list = the user opted out: left alone) and the
        instruction text is replaced only where it still has the old seed wording."""
        se = next(a for a in SEEDS if a.id == "super_engineer")
        row = self.db.one("SELECT instruction, acp_agents FROM agents WHERE id='super_engineer' AND builtin=1")
        if not row:
            return
        ids = json.loads(row["acp_agents"] or "[]")
        add = [i for i in se.acp_agents if i in ("codex", "aider", "openhands") and i not in ids]
        if ids and add:
            self.db.execute("UPDATE agents SET acp_agents=? WHERE id='super_engineer'", (json.dumps(ids + add),))
        if OLD_SE_STEP in row["instruction"]:
            self.db.execute("UPDATE agents SET instruction=? WHERE id='super_engineer'", (row["instruction"].replace(OLD_SE_STEP, NEW_SE_STEP),))

    def _insert(self, a: AgentDef) -> None:
        self.db.execute(
            "INSERT INTO agents(id,name,description,instruction,provider,tier,model,tools,mcp,skills,sub_agents,builtin,can_spawn,acp_agents,acp_policy,rag,memory,guardrails,identity,framework,api_access,a2a,runtime,sandbox,data,agui,live) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (a.id, a.name, a.description, a.instruction, a.provider, a.tier, a.model, json.dumps(a.tools), json.dumps(a.mcp),
             json.dumps(a.skills), json.dumps(a.sub_agents), int(a.builtin), int(a.can_spawn), json.dumps(a.acp_agents), a.acp_policy,
             json.dumps(a.rag), json.dumps(a.memory), json.dumps(a.guardrails), json.dumps(a.identity), a.framework, int(a.api_access), int(a.a2a), a.runtime, a.sandbox, json.dumps(a.data), json.dumps(agui_flags.flags(a.agui)), json.dumps(live_profile.profile(a.live))))

    def list(self) -> list[AgentDef]:
        return [AgentDef.from_row(r) for r in self.db.query("SELECT * FROM agents ORDER BY builtin DESC, rowid")]

    def get(self, aid: str) -> AgentDef | None:
        r = self.db.one("SELECT * FROM agents WHERE id=?", (aid,))
        return AgentDef.from_row(r) if r else None

    def validate(self, a: AgentDef) -> None:
        if not ID_RE.match(a.id):
            raise ValueError("id must be lowercase letters/digits/underscore, start with a letter (2-31 chars)")
        if not a.name.strip():
            raise ValueError("name is required")
        if a.tier not in ("pro", "flash", "custom"):
            raise ValueError("tier must be pro, flash or custom")
        if a.acp_policy not in ("ask", "reads_auto", "readonly"):
            raise ValueError("acp_policy must be ask, reads_auto or readonly")
        if a.tier == "custom" and not a.model:
            raise ValueError("tier 'custom' needs a model")
        for sk in a.skills:
            if sk.startswith(GROUP_PREFIX) and not GROUP_RE.match(sk[len(GROUP_PREFIX):]):
                raise ValueError(f"bad skill group reference '{sk}'")
        bad = [t for t in a.tools if t not in ALL_TOOLS]
        if bad:
            raise ValueError(f"unknown tools: {bad}")
        for kb in a.rag:
            if kb != "*" and not KB_REF.match(kb):
                raise ValueError(f"bad knowledge base reference '{kb}'")
        if a.rag and "rag_search" not in a.tools:            # attaching a knowledge base switches the search tool on
            a.tools = [*a.tools, "rag_search", "rag_list"]
        if not isinstance(a.guardrails, list) or any(not isinstance(g, str) or not (g == "none" or GUARD_RE.match(g)) for g in a.guardrails) or ("none" in a.guardrails and len(a.guardrails) > 1):
            raise ValueError("guardrails must be a list of profile ids, or just ['none'] to switch them off for this agent")
        a.identity = clean_identity(a.identity)
        from .frameworks.catalog import FRAMEWORKS
        if a.framework not in FRAMEWORKS:
            raise ValueError(f"framework must be one of {', '.join(FRAMEWORKS)}")
        from .runtimes.targets import TARGET_IDS
        if a.runtime not in TARGET_IDS:
            raise ValueError(f"runtime must be one of {', '.join(TARGET_IDS)}")
        from .sandboxes.base import SANDBOX_KINDS
        if a.sandbox and a.sandbox not in SANDBOX_KINDS:
            raise ValueError(f"sandbox must be empty (the default) or one of {', '.join(SANDBOX_KINDS)}")
        a.agui = agui_flags.normalise(a.agui)
        a.live = live_profile.normalise(a.live)
        from .datasources.base import DS_REF
        if not isinstance(a.data, list) or any(not isinstance(d, str) or not (d == "*" or DS_REF.match(d)) for d in a.data):
            raise ValueError("data must be a list of data source ids (or ['*'] for all of them)")
        if a.data and "data_query" not in a.tools:            # attaching a data source switches the data tools on (like knowledge bases)
            a.tools = [*a.tools, "data_sources", "data_schema", "data_query"]
        a.memory = clean_agent_memory(a.memory)               # raises ValueError with a plain explanation; {} = memory off
        wanted = (["memory_search", "memory_save", "memory_forget"] if uses_records(a.memory) else []) + (["memory_notes"] if "notes" in a.memory.get("types", []) else [])
        a.tools = [*a.tools, *[t for t in wanted if t not in a.tools]]     # switching memory on gives the agent its memory tools
        for s in a.sub_agents:
            if s == a.id:
                raise ValueError("an agent cannot be its own sub-agent")
            if not self.get(s):
                raise ValueError(f"unknown sub-agent '{s}'")
        # cycle check: can any sub-agent reach `a`?
        seen, stack = set(), list(a.sub_agents)
        while stack:
            cur = stack.pop()
            if cur == a.id:
                raise ValueError("sub-agent cycle detected")
            if cur in seen:
                continue
            seen.add(cur)
            c = self.get(cur)
            if c:
                stack.extend(c.sub_agents)

    def create(self, a: AgentDef) -> AgentDef:
        if self.get(a.id):
            raise ValueError(f"agent '{a.id}' already exists")
        self.validate(a)
        self._insert(a)
        return self.get(a.id)  # type: ignore[return-value]

    def update(self, aid: str, patch: dict) -> AgentDef:
        cur = self.get(aid)
        if not cur:
            raise KeyError(aid)
        d = {**cur.__dict__, **{k: v for k, v in patch.items() if k in cur.__dict__ and k not in ("id", "builtin")}}
        a = AgentDef(**d)
        self.validate(a)
        self.db.execute(
            "UPDATE agents SET name=?,description=?,instruction=?,provider=?,tier=?,model=?,tools=?,mcp=?,skills=?,sub_agents=?,can_spawn=?,acp_agents=?,acp_policy=?,rag=?,memory=?,guardrails=?,identity=?,framework=?,api_access=?,a2a=?,runtime=?,sandbox=?,data=?,agui=?,live=? WHERE id=?",
            (a.name, a.description, a.instruction, a.provider, a.tier, a.model, json.dumps(a.tools), json.dumps(a.mcp),
             json.dumps(a.skills), json.dumps(a.sub_agents), int(a.can_spawn), json.dumps(a.acp_agents), a.acp_policy,
             json.dumps(a.rag), json.dumps(a.memory), json.dumps(a.guardrails), json.dumps(a.identity), a.framework, int(a.api_access), int(a.a2a), a.runtime, a.sandbox, json.dumps(a.data), json.dumps(a.agui), json.dumps(a.live), aid))
        return self.get(aid)  # type: ignore[return-value]

    def delete(self, aid: str) -> None:
        a = self.get(aid)
        if not a:
            raise KeyError(aid)
        if a.builtin:
            raise ValueError("built-in agents can be edited but not deleted")
        users = [x.id for x in self.list() if aid in x.sub_agents]
        if users:
            raise ValueError(f"still used as a sub-agent by {users}")
        self.db.execute("DELETE FROM agents WHERE id=?", (aid,))
