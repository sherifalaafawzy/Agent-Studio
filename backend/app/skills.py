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
MAX_BODY = 30_000


@dataclass
class Skill:
    name: str
    description: str
    body: str
    mcp: list[str] = field(default_factory=list)
    source: str = "builtin"  # builtin | user
    path: str = ""

    def public(self, with_body: bool = False) -> dict:
        d = dict(name=self.name, description=self.description, mcp=self.mcp, source=self.source)
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
    return Skill(name=name, description=str(meta.get("description", "")).strip(), body=m.group(2).strip(), mcp=list(mcp))


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

    def create(self, name: str, description: str, body: str, mcp: list[str] | None = None, overwrite: bool = False) -> Skill:
        if not NAME_RE.match(name):
            raise ValueError("skill name must be lowercase letters/digits/hyphens, 2-41 chars")
        if not description.strip():
            raise ValueError("description is required (it is how agents decide to load the skill)")
        if len(body) > MAX_BODY:
            raise ValueError("skill body too large")
        ex = self.get(name)
        if ex and ex.source == "builtin":
            raise ValueError(f"'{name}' is a built-in skill and cannot be replaced")
        if ex and not overwrite:
            raise ValueError(f"skill '{name}' already exists (pass overwrite=true to replace)")
        d = self.user_dir / name
        d.mkdir(parents=True, exist_ok=True)
        meta = {"name": name, "description": description.strip()}
        if mcp:
            meta["mcp"] = list(mcp)
        (d / "SKILL.md").write_text("---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n\n" + body.strip() + "\n", "utf-8")
        return self.get(name)  # type: ignore[return-value]

    def delete(self, name: str) -> None:
        s = self.get(name)
        if not s:
            raise KeyError(name)
        if s.source == "builtin":
            raise ValueError("built-in skills cannot be deleted")
        shutil.rmtree(Path(s.path).parent)

    def index_text(self, allowed: list[str]) -> str:
        sk = [s for s in self.list() if "*" in allowed or s.name in allowed]
        if not sk:
            return ""
        lines = "\n".join(f"- {s.name}: {s.description}" for s in sk)
        return ("\n\n## Skills\nReusable playbooks. When a task matches one, call load_skill(name) BEFORE starting and follow it.\n" + lines)
