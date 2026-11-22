"""SKILL.md-style skills. A skill is a folder with SKILL.md: YAML frontmatter (name, description, optional
`mcp: [server ids]`) + a markdown body. Progressive disclosure: agents see only name+description in their
prompt and pull the body with load_skill(). Built-ins live in <repo>/skills, agent/user-made ones in <data>/skills."""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .db import REPO_ROOT, data_dir

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
GROUP_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 &/+-]{0,23}$")
GROUP_PREFIX = "group:"          # an agent's `skills` entry "group:Security" attaches every skill in that group
DEFAULT_GROUP = "General"
SUGGESTED_GROUPS = ["Coding", "Security", "Research", "Writing", "Data", "Ops", "General"]
MAX_BODY = 30_000


def clean_group(value: object) -> str:
    """Normalise a group label (blank -> General). Raises ValueError when it is not a short plain label."""
    g = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    if not g:
        return DEFAULT_GROUP
    if not GROUP_RE.match(g):
        raise ValueError("group must be a short label (letters, digits, spaces, & / + -; up to 24 chars, starting with a letter)")
    return g


def covers(allowed: list[str], skill: "Skill") -> bool:
    """Does an agent's `skills` list include this skill? ('*', its name, or 'group:<its group>', group case-insensitive)"""
    gref = GROUP_PREFIX + skill.group.lower()
    return any(a == "*" or a == skill.name or a.lower() == gref for a in allowed)


@dataclass
class Skill:
    name: str
    description: str
    body: str
    mcp: list[str] = field(default_factory=list)
    source: str = "builtin"  # builtin | user
    path: str = ""
    group: str = DEFAULT_GROUP

    def public(self, with_body: bool = False) -> dict:
        d = dict(name=self.name, description=self.description, mcp=self.mcp, source=self.source, group=self.group)
        if with_body:
            d["body"] = self.body
        return d


def parse_skill(text: str, fallback_name: str) -> Skill:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        raise ValueError("SKILL.md must start with YAML frontmatter (--- name/description ---)")
    meta = yaml.safe_load(m.group(1)) or {}
    name = str(meta.get("name") or fallback_name)
    mcp = meta.get("mcp") or []
    if isinstance(mcp, str):
        mcp = [x.strip() for x in mcp.split(",") if x.strip()]
    try:
        group = clean_group(meta.get("group"))
    except ValueError:
        group = DEFAULT_GROUP  # a hand-edited bad label must not hide the skill
    return Skill(name=name, description=str(meta.get("description", "")).strip(), body=m.group(2).strip(), mcp=list(mcp), group=group)


def render_skill(s: Skill) -> str:
    """The SKILL.md text for a skill (what is written to disk and what export returns)."""
    meta: dict = {"name": s.name, "description": s.description.strip(), "group": s.group}
    if s.mcp:
        meta["mcp"] = list(s.mcp)
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n\n" + s.body.strip() + "\n"


class SkillRegistry:
    def __init__(self, builtin_dir: Path | None = None, user_dir: Path | None = None):
        self.builtin_dir = builtin_dir or (REPO_ROOT / "skills")
        self.user_dir = user_dir or (data_dir() / "skills")
        self.user_dir.mkdir(parents=True, exist_ok=True)

    def _scan(self, d: Path, source: str) -> list[Skill]:
        out = []
        if not d.is_dir():
            return out
        for sub in sorted(d.iterdir()):
            f = sub / "SKILL.md"
            if f.is_file():
                try:
                    s = parse_skill(f.read_text("utf-8"), sub.name)
                    s.source, s.path = source, str(f)
                    out.append(s)
                except Exception:
                    continue  # malformed skill must not break the registry
        return out

    def list(self) -> list[Skill]:
        by = {s.name: s for s in self._scan(self.builtin_dir, "builtin")}
        for s in self._scan(self.user_dir, "user"):
            by.setdefault(s.name, s)  # built-ins cannot be shadowed
        return sorted(by.values(), key=lambda s: s.name)

    def get(self, name: str) -> Skill | None:
        return next((s for s in self.list() if s.name == name), None)

    def create(self, name: str, description: str, body: str, mcp: list[str] | None = None, overwrite: bool = False,
               group: str | None = None) -> Skill:
        if not NAME_RE.match(name):
            raise ValueError("skill name must be lowercase letters/digits/hyphens, 2-41 chars")
        if not description.strip():
            raise ValueError("description is required (it is how agents decide to load the skill)")
        if len(body) > MAX_BODY:
            raise ValueError("skill body too large")
        group = clean_group(group)
        ex = self.get(name)
        if ex and ex.source == "builtin":
            raise ValueError(f"'{name}' is a built-in skill and cannot be replaced")
        if ex and not overwrite:
            raise ValueError(f"skill '{name}' already exists (pass overwrite=true to replace)")
        d = self.user_dir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(render_skill(Skill(name, description, body, list(mcp or []), group=group)), "utf-8")
        return self.get(name)  # type: ignore[return-value]

    def duplicate(self, name: str, new_name: str | None = None) -> Skill:
        """Copy any skill (typically a read-only built-in) into an editable user skill."""
        src = self.get(name)
        if not src:
            raise KeyError(name)
        new = (new_name or f"{name}-custom")[:41].rstrip("-")
        return self.create(new, src.description, src.body, src.mcp, group=src.group)

    def import_text(self, text: str, name: str | None = None, overwrite: bool = False) -> Skill:
        """Create a user skill from SKILL.md text (frontmatter + body). `name` overrides the frontmatter name."""
        if len(text) > MAX_BODY + 4_000:
            raise ValueError("SKILL.md too large")
        s = parse_skill(text, name or "")
        return self.create(name or s.name, s.description, s.body, s.mcp, overwrite, s.group)

    def groups(self) -> list[dict]:
        """Groups in use (plus the suggested ones, so a picker always has choices) with per-source counts."""
        out: dict[str, dict] = {g: dict(name=g, count=0, builtin=0, user=0) for g in SUGGESTED_GROUPS}
        for s in self.list():
            key = next((k for k in out if k.lower() == s.group.lower()), s.group)
            row = out.setdefault(key, dict(name=key, count=0, builtin=0, user=0))
            row["count"] += 1
            row[s.source] += 1
        return sorted(out.values(), key=lambda r: (r["name"] == DEFAULT_GROUP, r["name"].lower()))

    def resolve(self, allowed: list[str]) -> list[Skill]:
        """The skills an agent's `skills` list stands for: '*', explicit names and 'group:<label>' (evaluated now,
        so a skill added to the group later is picked up automatically)."""
        return [s for s in self.list() if covers(allowed, s)]

    def linked_mcp(self, allowed: list[str]) -> list[str]:
        """MCP servers brought by explicitly-named or group-attached skills ('*' alone does not auto-link)."""
        explicit = [a for a in allowed if a != "*"]
        return [m for s in self.list() if covers(explicit, s) for m in s.mcp]

    def delete(self, name: str) -> None:
        s = self.get(name)
        if not s:
            raise KeyError(name)
        if s.source == "builtin":
            raise ValueError("built-in skills cannot be deleted")
        shutil.rmtree(Path(s.path).parent)

    def index_text(self, allowed: list[str]) -> str:
        sk = self.resolve(allowed)
        if not sk:
            return ""
        lines = "\n".join(f"- {s.name}: {s.description}" for s in sk)
        return ("\n\n## Skills\nReusable playbooks. When a task matches one, call load_skill(name) BEFORE starting and follow it.\n" + lines)
