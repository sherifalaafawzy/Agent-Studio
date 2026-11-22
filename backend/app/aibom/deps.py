"""Software dependencies: the Python environment the backend runs in (resolved from requirements.txt through the installed
package metadata) and the npm lockfiles. Only facts that are really recorded are emitted: versions, licences, the registry
integrity hash of npm packages. Python wheels carry no package-level hash in their installed metadata, so none is invented.
"""
from __future__ import annotations

import base64
import json
import re
import sys
from collections.abc import Callable
from importlib import metadata as md
from pathlib import Path
from urllib.parse import quote

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from .redact import sha256_hex  # noqa: F401  (re-exported for callers)

SCHEMA_DIR = Path(__file__).parent / "schemas"


def _spdx_ids() -> set[str]:
    return set(json.loads((SCHEMA_DIR / "spdx.schema.json").read_text("utf-8"))["enum"])


_SPDX: set[str] | None = None
_EXPR_TOKEN = re.compile(r"[()]|\bAND\b|\bOR\b|\bWITH\b|[A-Za-z0-9.+-]+")


def licenses(raw: str | None) -> list[dict]:
    """CycloneDX `licenses` for a licence string: an SPDX id, an SPDX expression, or just a name. [] when unknown."""
    global _SPDX
    text = (raw or "").strip()
    if not text or "\n" in text or len(text) > 120:
        return []                                         # a pasted licence text is not an identifier
    _SPDX = _SPDX or _spdx_ids()
    if text in _SPDX:
        return [{"license": {"id": text}}]
    low = {i.lower(): i for i in _SPDX}
    if text.lower() in low:
        return [{"license": {"id": low[text.lower()]}}]
    tokens = _EXPR_TOKEN.findall(text)
    if len(tokens) > 1 and "".join(tokens) == re.sub(r"\s+", "", text) and \
            all(t in ("(", ")", "AND", "OR", "WITH") or t.rstrip("+") in _SPDX for t in tokens):
        return [{"expression": text}]
    return [{"license": {"name": text}}]


def purl_pypi(name: str, version: str) -> str:
    return f"pkg:pypi/{canonicalize_name(name)}@{quote(version, safe='')}"


def purl_npm(name: str, version: str) -> str:
    if name.startswith("@"):
        scope, rest = name[1:].split("/", 1)
        return f"pkg:npm/%40{quote(scope, safe='')}/{quote(rest, safe='')}@{quote(version, safe='')}"
    return f"pkg:npm/{quote(name, safe='')}@{quote(version, safe='')}"


class Graph:
    """Components keyed by bom-ref, edges bom-ref -> bom-refs."""

    def __init__(self) -> None:
        self.components: dict[str, dict] = {}
        self.edges: dict[str, set[str]] = {}
        self.direct: set[str] = set()

    def add(self, comp: dict) -> str:
        self.components.setdefault(comp["bom-ref"], comp)
        self.edges.setdefault(comp["bom-ref"], set())
        return comp["bom-ref"]


# --------------------------------------------------------------------------------------------------- Python
def _py_license(dist: md.Distribution) -> list[dict]:
    meta = dist.metadata
    expr = meta.get("License-Expression")
    if expr:
        return licenses(expr)
    raw = meta.get("License")
    got = licenses(raw)
    if got and "name" not in got[0].get("license", {}):
        return got
    for c in meta.get_all("Classifier") or []:
        if c.startswith("License :: OSI Approved :: "):
            name = c.rsplit(" :: ", 1)[-1]
            return licenses({"MIT License": "MIT", "BSD License": "BSD-3-Clause", "Apache Software License": "Apache-2.0",
                             "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0", "ISC License (ISCL)": "ISC",
                             "Python Software Foundation License": "PSF-2.0"}.get(name, name))
    return got


def _py_component(dist: md.Distribution) -> dict:
    name = dist.metadata["Name"]
    version = dist.version
    purl = purl_pypi(name, version)
    comp: dict = {"type": "library", "bom-ref": purl, "name": canonicalize_name(name), "version": version, "purl": purl,
                  "externalReferences": [{"type": "distribution", "url": f"https://pypi.org/project/{canonicalize_name(name)}/{version}/"}],
                  "properties": [{"name": "agentstudio:ecosystem", "value": "pypi"}]}
    lic = _py_license(dist)
    if lic:
        comp["licenses"] = lic
    return comp


def parse_requirements(path: Path) -> list[Requirement]:
    out: list[Requirement] = []
    for line in path.read_text("utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        try:
            out.append(Requirement(line))
        except InvalidRequirement:
            continue
    return out


def python_graph(requirements: Path, lookup: Callable[[str], md.Distribution | None] | None = None) -> Graph:
    """Every installed distribution reachable from requirements.txt, with its `Requires-Dist` edges."""
    def default_lookup(name: str):
        try:
            return md.distribution(name)
        except md.PackageNotFoundError:
            return None
    look = lookup or default_lookup
    g = Graph()
    env = {**default_environment(), "extra": ""}
    seen: dict[str, str] = {}
    stack: list[tuple[str, tuple[str, ...], str | None]] = []
    for r in parse_requirements(requirements):
        stack.append((r.name, tuple(sorted(r.extras)), None))
    while stack:
        name, extras, parent = stack.pop()
        dist = look(name)
        if dist is None:
            continue                                       # optional or platform-specific and not installed here
        ref = purl_pypi(dist.metadata["Name"], dist.version)
        if ref not in g.components:
            g.add(_py_component(dist))
        if parent is None:
            g.direct.add(ref)
        else:
            g.edges[parent].add(ref)
        key = f"{ref}|{','.join(extras)}"
        if key in seen:
            continue
        seen[key] = ref
        for raw in dist.requires or []:
            try:
                req = Requirement(raw)
            except InvalidRequirement:
                continue
            if req.marker is not None and not any(req.marker.evaluate({**env, "extra": e}) for e in (("",) + extras)):
                continue
            stack.append((req.name, tuple(sorted(req.extras)), ref))
    return g


# --------------------------------------------------------------------------------------------------- npm
def _sri_to_hash(integrity: str | None) -> list[dict]:
    """`sha512-<base64>` (npm's registry integrity) -> a CycloneDX hash. Only the first (strongest listed) algorithm."""
    if not integrity:
        return []
    algo, _, b64 = integrity.split(" ")[0].partition("-")
    alg = {"sha512": "SHA-512", "sha384": "SHA-384", "sha256": "SHA-256", "sha1": "SHA-1"}.get(algo)
    if not alg or not b64:
        return []
    try:
        return [{"alg": alg, "content": base64.b64decode(b64, validate=True).hex()}]
    except ValueError:
        return []


def _npm_name(key: str, entry: dict) -> str:
    return entry.get("name") or key.rsplit("node_modules/", 1)[-1]


def _resolve(packages: dict, from_key: str, dep: str) -> str | None:
    """Node's lookup: node_modules next to the package, then each parent folder's."""
    base = from_key
    while True:
        cand = f"{base}/node_modules/{dep}" if base else f"node_modules/{dep}"
        if cand in packages:
            return cand
        if not base:
            return None
        idx = base.rfind("/node_modules/")
        base = base[:idx] if idx >= 0 else ""


def npm_graph(lock: Path, label: str, g: Graph | None = None) -> Graph:
    """Components and edges from a package-lock.json (lockfileVersion 2/3). `label` names the project in properties."""
    g = g or Graph()
    data = json.loads(lock.read_text("utf-8"))
    packages: dict = data.get("packages") or {}
    ref_of: dict[str, str] = {}
    for key, e in packages.items():
        if key == "" or e.get("link") or "version" not in e:
            continue
        name = _npm_name(key, e)
        purl = purl_npm(name, e["version"])
        ref_of[key] = purl
        if purl not in g.components:
            comp: dict = {"type": "library", "bom-ref": purl, "name": name, "version": e["version"], "purl": purl,
                          "properties": [{"name": "agentstudio:ecosystem", "value": "npm"}]}
            if hashes := _sri_to_hash(e.get("integrity")):
                comp["hashes"] = hashes
            lic = licenses(e["license"]) if isinstance(e.get("license"), str) else []
            if lic:
                comp["licenses"] = lic
            if e.get("resolved", "").startswith("https://registry.npmjs.org/"):
                comp["externalReferences"] = [{"type": "distribution", "url": e["resolved"]}]
            if e.get("dev"):
                comp["scope"] = "optional"
                comp["properties"].append({"name": "agentstudio:dev-dependency", "value": "true"})
            elif e.get("optional"):
                comp["scope"] = "optional"
            g.add(comp)
        elif not e.get("dev") and not e.get("optional"):
            g.components[purl].pop("scope", None)           # also needed at runtime through another path
            g.components[purl]["properties"] = [p for p in g.components[purl]["properties"] if p["name"] != "agentstudio:dev-dependency"]
    for key, e in packages.items():
        if key not in ref_of:
            continue
        for field in ("dependencies", "optionalDependencies"):
            for dep in (e.get(field) or {}):
                target = _resolve(packages, key, dep)
                if target in ref_of:
                    g.edges[ref_of[key]].add(ref_of[target])
    root = packages.get("") or {}
    for field in ("dependencies", "devDependencies", "optionalDependencies"):
        for dep in (root.get(field) or {}):
            target = _resolve(packages, "", dep)
            if target in ref_of:
                g.direct.add(ref_of[target])
    return g


_CACHE: dict[tuple, Graph] = {}


def cached(key: tuple, build: Callable[[], Graph]) -> Graph:
    """Scanning ~300 installed distributions and a lockfile takes about a second; reuse the result until the inputs change.
    The graph is read-only for callers."""
    if key not in _CACHE:
        while len(_CACHE) >= 8:                          # a handful of input sets; the oldest is dropped, so it never grows
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = build()
    return _CACHE[key]


def clear_cache() -> None:
    _CACHE.clear()


def merge(*graphs: Graph) -> Graph:
    out = Graph()
    for g in graphs:
        for ref, comp in g.components.items():
            out.components.setdefault(ref, comp)
            out.edges.setdefault(ref, set()).update(g.edges.get(ref, ()))
        out.direct |= g.direct
    return out


def python_version() -> str:
    return ".".join(str(x) for x in sys.version_info[:3])
