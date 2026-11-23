"""The manifest (`app.json`) every app carries: what it is, which of your agents it calls, what it may reach and how it starts.

It is the one standard description, so an app built by any agent looks and runs the same way. Everything in it is checked here; a value that does
not fit is refused with a plain reason. It never holds a secret: environment variables are listed by NAME only (the values are write-only).
"""
from __future__ import annotations

import re

TYPES = ("static", "node", "python")
NETWORKS = ("none", "allowlist", "open")
RUNTIMES = ("local", "docker")
ENTRY = {"static": "public/index.html", "node": "server/index.js", "python": "server/main.py"}
START = {"static": "", "node": "node server/index.js", "python": "python server/main.py"}
MAX_AGENTS, MAX_ENV, MAX_ALLOW, MAX_DATA = 10, 20, 10, 10
DATA_RE = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
ORIGIN_RE = re.compile(r"^https://[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?(:\d{2,5})?$")
START_RE = re.compile(r"^[A-Za-z0-9_./-]+( [A-Za-z0-9_./=:-]+){0,5}$")
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
RESERVED_ENV = {"AGENT_STUDIO_API_KEY", "AGENT_STUDIO_BASE_URL", "PORT", "PATH", "HOME"}      # set by Agent Studio itself; an app cannot redefine them


class ManifestError(ValueError):
    """A manifest value that does not fit. Safe to show."""


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40].strip("-")
    return s or "app"


def default_manifest(name: str, app_type: str = "static", description: str = "") -> dict:
    return {
        "schema": 1, "name": (name or "").strip()[:60] or "Untitled app", "description": (description or "").strip()[:300], "version": "0.1.0", "type": app_type,
        "entry": ENTRY[app_type], "start": START[app_type], "port": 8080, "agents": [], "data": [], "permissions": {"network": "none", "allow": []},
        "env": [], "runtime": "local",
    }


def clean_manifest(raw: object) -> dict:
    """The manifest with every value checked (ManifestError says what is wrong). Unknown keys are dropped."""
    if not isinstance(raw, dict):
        raise ManifestError("the manifest must be an object")
    t = raw.get("type", "static")
    if t not in TYPES:
        raise ManifestError(f"type must be one of: {', '.join(TYPES)}")
    name = raw.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise ManifestError("give the app a name (1 to 60 characters)")
    desc = raw.get("description", "")
    if not isinstance(desc, str) or len(desc) > 300:
        raise ManifestError("the description must be text of at most 300 characters")
    version = raw.get("version", "0.1.0")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise ManifestError("version must look like 1.2.3")
    entry = raw.get("entry", ENTRY[t])
    if not isinstance(entry, str) or not re.match(r"^[A-Za-z0-9_./-]{1,120}$", entry) or ".." in entry or entry.startswith("/"):
        raise ManifestError("entry must be a file path inside the app")
    start = raw.get("start", START[t])
    if t == "static":
        start = ""
    elif not isinstance(start, str) or not START_RE.match(start):
        raise ManifestError("start must be a simple command such as: node server/index.js (no pipes, quotes or shell characters)")
    port = raw.get("port", 8080)
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ManifestError("port must be a whole number from 1024 to 65535")
    agents = raw.get("agents", [])
    if not isinstance(agents, list) or len(agents) > MAX_AGENTS:
        raise ManifestError(f"list at most {MAX_AGENTS} agents")
    seen, ag = set(), []
    for a in agents:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not re.match(r"^[\w.-]{1,60}$", a["id"]):
            raise ManifestError("each agent needs an id")
        if a["id"] in seen:
            raise ManifestError(f"agent '{a['id']}' is listed twice")
        purpose = a.get("purpose", "")
        if not isinstance(purpose, str) or len(purpose) > 200:
            raise ManifestError("an agent's purpose must be text of at most 200 characters")
        seen.add(a["id"])
        ag.append({"id": a["id"], "purpose": purpose.strip()})
    data = raw.get("data", [])
    if not isinstance(data, list) or len(data) > MAX_DATA:
        raise ManifestError(f"list at most {MAX_DATA} data sources")
    seen_d, dt = set(), []
    for d in data:
        if not isinstance(d, dict) or not isinstance(d.get("id"), str) or not DATA_RE.match(d["id"]):
            raise ManifestError("each data source needs an id (lowercase letters, digits, - or _)")
        if d["id"] in seen_d:
            raise ManifestError(f"data source '{d['id']}' is listed twice")
        purpose = d.get("purpose", "")
        if not isinstance(purpose, str) or len(purpose) > 200:
            raise ManifestError("a data source's purpose must be text of at most 200 characters")
        seen_d.add(d["id"])
        dt.append({"id": d["id"], "purpose": purpose.strip()})
    perms = raw.get("permissions", {})
    if not isinstance(perms, dict):
        raise ManifestError("permissions must be an object")
    net = perms.get("network", "none")
    if net not in NETWORKS:
        raise ManifestError(f"network must be one of: {', '.join(NETWORKS)}")
    allow = perms.get("allow", [])
    if not isinstance(allow, list) or len(allow) > MAX_ALLOW or any(not isinstance(o, str) or not ORIGIN_RE.match(o) for o in allow):
        raise ManifestError(f"allow lists at most {MAX_ALLOW} addresses like https://api.example.com (https only)")
    env = raw.get("env", [])
    if not isinstance(env, list) or len(env) > MAX_ENV:
        raise ManifestError(f"list at most {MAX_ENV} environment variables")
    names, ev = set(), []
    for e in env:
        if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not ENV_RE.match(e["name"]):
            raise ManifestError("each environment variable needs a NAME in capitals, digits and underscores")
        if e["name"] in RESERVED_ENV or e["name"] in names:
            raise ManifestError(f"{e['name']} is reserved or listed twice")
        d = e.get("description", "")
        if not isinstance(d, str) or len(d) > 200:
            raise ManifestError("an environment variable's description must be text of at most 200 characters")
        names.add(e["name"])
        ev.append({"name": e["name"], "description": d.strip(), "required": bool(e.get("required", False))})
    runtime = raw.get("runtime", "local")
    if runtime not in RUNTIMES:
        raise ManifestError(f"runtime must be one of: {', '.join(RUNTIMES)}")
    return {"schema": 1, "name": name.strip(), "description": desc.strip(), "version": version, "type": t, "entry": entry, "start": start, "port": port, "agents": ag, "data": dt,
            "permissions": {"network": net, "allow": list(allow)}, "env": ev, "runtime": runtime}
