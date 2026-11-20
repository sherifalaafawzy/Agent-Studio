"""Agent definitions (stored in SQLite) + the seeded team."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .db import Database
from .harness.tools import ALL_TOOLS, READ_ONLY_TOOLS

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")  # also a valid Python identifier (ADK agent names)

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

    @classmethod
    def from_row(cls, r) -> "AgentDef":
        return cls(id=r["id"], name=r["name"], description=r["description"], instruction=r["instruction"], provider=r["provider"],
                   tier=r["tier"], model=r["model"], tools=json.loads(r["tools"]), mcp=json.loads(r["mcp"]),
                   skills=json.loads(r["skills"]), sub_agents=json.loads(r["sub_agents"]), builtin=bool(r["builtin"]))


SEEDS = [
    AgentDef(
        id="engineer", name="Engineer", tier="pro", builtin=True,
        description="Lead engineer: plans, delegates exploration/implementation/review to sub-agents, verifies with tests.",
        instruction=("You are the lead engineer. For non-trivial work: (1) delegate repository exploration to `explorer`, "
                     "(2) make a short plan, (3) delegate focused implementation to `coder`, (4) ask `reviewer` to check the result, "
                     "(5) run the tests yourself with run_shell. Sub-agents have isolated context: give each a complete, self-contained "
                     "request (paths, goal, constraints) and ask for a concise report. Do small tasks directly. Never claim success without verification."),
        tools=list(ALL_TOOLS), mcp=["memory", "fetch"], skills=["*"], sub_agents=["explorer", "coder", "reviewer"]),
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
]


class AgentStore:
    def __init__(self, db: Database):
        self.db = db
        for a in SEEDS:
            if not self.get(a.id):
                self._insert(a)

    def _insert(self, a: AgentDef) -> None:
        self.db.execute(
            "INSERT INTO agents(id,name,description,instruction,provider,tier,model,tools,mcp,skills,sub_agents,builtin) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (a.id, a.name, a.description, a.instruction, a.provider, a.tier, a.model, json.dumps(a.tools), json.dumps(a.mcp),
             json.dumps(a.skills), json.dumps(a.sub_agents), int(a.builtin)))

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
        if a.tier == "custom" and not a.model:
            raise ValueError("tier 'custom' needs a model")
        bad = [t for t in a.tools if t not in ALL_TOOLS]
        if bad:
            raise ValueError(f"unknown tools: {bad}")
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
            "UPDATE agents SET name=?,description=?,instruction=?,provider=?,tier=?,model=?,tools=?,mcp=?,skills=?,sub_agents=? WHERE id=?",
            (a.name, a.description, a.instruction, a.provider, a.tier, a.model, json.dumps(a.tools), json.dumps(a.mcp),
             json.dumps(a.skills), json.dumps(a.sub_agents), aid))
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
