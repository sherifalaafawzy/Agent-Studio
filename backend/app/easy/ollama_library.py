"""The Ollama model library: a curated starter list + 'find new models' from ollama.com's public pages.

ollama.com has NO official search API (and robots.txt disallows /api/), so discovery reads two public HTML pages that robots.txt
allows: /search?q=<text> and /library/<model>/tags. That markup can change, so every parser fails SOFT: if a page does not parse we
keep serving the curated list (and the last good cache) and say why. Network reads are cached and rate-limited.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

import httpx

from ..db import data_dir

BASE = "https://ollama.com"
TRANSPORT: httpx.BaseTransport | None = None
HEADERS = {"User-Agent": "AgentStudio-setup/1.0 (local app; reads public pages)"}
CACHE_TTL_S = 6 * 3600                 # automatic reads at most every 6 hours
MANUAL_MIN_S = 600                     # the button never hits the website more than once per 10 minutes
SLUG_RE = re.compile(r"^[A-Za-z0-9][\w.\-]*(/[A-Za-z0-9][\w.\-]*)?$")
CAPS = {"vision", "tools", "thinking", "embedding", "cloud"}
CURATED_FILE = Path(__file__).with_name("ollama_curated.json")


class ParseError(Exception):
    """A page loaded but did not look like we expected."""


def load_curated() -> dict:
    return json.loads(CURATED_FILE.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ fit estimate
def fit(size_gb: float | None, ram_gb: float | None, vram_gb: float | None) -> dict:
    """Will a model of this download size run well here? A heuristic: weights + ~20% + 1 GB for the working memory."""
    if not size_gb:
        return dict(level="unknown", text="Open its sizes to see how much room it needs.")
    need = round(size_gb * 1.2 + 1.0, 1)
    if vram_gb and vram_gb >= need:
        return dict(level="great", text=f"Fits in your graphics card memory ({vram_gb} GB): should be fast. Needs about {need} GB.")
    if ram_gb is None and not vram_gb:
        return dict(level="unknown", text=f"Needs about {need} GB of memory. Your memory size could not be read.")
    ram = ram_gb or 0
    if ram >= need + 6:
        return dict(level="ok", text=f"Runs on your processor ({ram} GB memory), slower than on a graphics card. Needs about {need} GB.")
    if ram >= need:
        return dict(level="tight", text=f"Will run but leaves little free memory ({ram} GB total, needs about {need} GB). Close other apps first.")
    return dict(level="too_big", text=f"Probably too big for this PC: needs about {need} GB, you have {ram} GB.")


# ------------------------------------------------------------------ parsers
class _SearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self.cur: dict | None = None
        self.kind: str | None = None
        self.in_p = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = {k: v or "" for k, v in attrs}
        href = a.get("href", "")
        if tag == "a" and href.startswith("/library/") and SLUG_RE.match(href[len("/library/"):]) and self.cur is None:
            self.cur = dict(name=href[len("/library/"):], caps=[], sizes=[], desc="", pulls=None, updated="", tags_count=None)
        elif self.cur is None:
            return
        elif tag == "span":
            title, cls = a.get("title", ""), a.get("class", "")
            if title.startswith("Updated "):
                self.cur["updated"] = title[len("Updated "):]
            m = re.match(r"^([\d,]+) downloads$", title)
            if m:
                self.cur["pulls"] = int(m.group(1).replace(",", ""))
            self.kind = "cap" if "bg-indigo" in cls else "size" if "ddf4ff" in cls else None
        elif tag == "p":
            self.in_p = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "span":
            self.kind = None
        elif tag == "p":
            self.in_p = False
        elif tag == "li" and self.cur is not None:
            self.items.append(self.cur)
            self.cur = None

    def handle_data(self, data: str) -> None:
        if self.cur is None or not data.strip():
            return
        t = data.strip()
        if self.kind == "cap" and t in CAPS:
            self.cur["caps"].append(t)
        elif self.kind == "size":
            self.cur["sizes"].append(t)
        elif self.in_p and not self.cur["desc"]:
            self.cur["desc"] = t
        else:
            m = re.match(r"^(\d+)\s+tags?$", t)
            if m:
                self.cur["tags_count"] = int(m.group(1))


def parse_search(html: str) -> list[dict]:
    """Models from a /search page. Raises ParseError if a non-trivial page yields nothing (the markup probably changed)."""
    p = _SearchParser()
    p.feed(html)
    if not p.items and "/library/" in html:
        raise ParseError("ollama.com's search page no longer looks the way Agent Studio expects")
    return p.items


def parse_tags(html: str, model: str) -> list[dict]:
    """Per-tag download sizes from /library/<model>/tags."""
    rows, seen = [], set()
    key = f'href="/library/{model}:'
    parts = html.split(key)[1:]
    for chunk in parts:
        tag = chunk.split('"', 1)[0]
        if not tag or tag in seen:
            continue
        seen.add(tag)
        window = chunk[:900]
        size = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB|TB)", window)
        ctx = re.search(r"(\d+K)\s+context", window)
        gb = None
        if size:
            n = float(size.group(1))
            gb = round(n * {"GB": 1, "MB": 1 / 1000, "TB": 1000}[size.group(2)], 2)
        rows.append(dict(tag=tag, size_gb=gb, context=ctx.group(1) if ctx else None))
    if not rows:
        raise ParseError(f"the tag list for {model} did not look the way Agent Studio expects")
    return rows


# ------------------------------------------------------------------ the library
def _fetch(url: str) -> str:
    with httpx.Client(transport=TRANSPORT, timeout=15, headers=HEADERS, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text


class Library:
    def __init__(self, fetch: Callable[[str], str] | None = None, home: Path | None = None, clock: Callable[[], float] = time.time):
        self._fetch, self._home, self._now = fetch or _fetch, home, clock

    @property
    def _file(self) -> Path:
        d = (self._home or data_dir()) / "ollama"
        d.mkdir(parents=True, exist_ok=True)
        return d / "library.json"

    def _load(self) -> dict:
        try:
            d = json.loads(self._file.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, d: dict) -> None:
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        tmp.replace(self._file)

    # ---- network
    def refresh(self, query: str = "", force: bool = False) -> dict:
        """Read ollama.com's search page (newest first when no query). Returns {ok, message, throttled, count}."""
        c = self._load()
        last = float(c.get("fetched_at") or 0) if not query else float((c.get("queries") or {}).get(query.lower(), 0))
        ttl = MANUAL_MIN_S if force else CACHE_TTL_S
        if self._now() - last < ttl:
            return dict(ok=True, throttled=True, message="Using the saved list (it was refreshed recently).", count=len(c.get("found") or []))
        url = f"{BASE}/search?q={quote(query)}" if query else f"{BASE}/search?o=newest"
        try:
            items = parse_search(self._fetch(url))
        except (httpx.HTTPError, ParseError) as e:
            return dict(ok=False, throttled=False, count=len(c.get("found") or []),
                        message=f"Could not read ollama.com right now ({type(e).__name__}: {e}). Showing the built-in list and the last saved results.")
        known = {m["name"]: m for m in c.get("found", [])}
        seen = set(c.get("seen", []))
        baseline = not seen
        for it in items:
            if it["name"] not in known:
                it["first_seen"] = None if baseline else self._now()
            else:
                it["first_seen"] = known[it["name"]].get("first_seen")
            known[it["name"]] = it
            seen.add(it["name"])
        c.update(found=list(known.values()), seen=sorted(seen))
        if query:
            c.setdefault("queries", {})[query.lower()] = self._now()
        else:
            c["fetched_at"] = self._now()
        self._save(c)
        return dict(ok=True, throttled=False, count=len(items), message=f"Found {len(items)} models on ollama.com.")

    def tags(self, model: str) -> dict:
        if not SLUG_RE.match(model):
            return dict(ok=False, tags=[], message="invalid model name")
        c = self._load()
        cached = (c.get("tags") or {}).get(model)
        if cached and self._now() - cached["at"] < CACHE_TTL_S:
            return dict(ok=True, tags=cached["rows"], message="")
        try:
            rows = parse_tags(self._fetch(f"{BASE}/library/{model}/tags"), model)
        except (httpx.HTTPError, ParseError) as e:
            return dict(ok=False, tags=cached["rows"] if cached else [], message=f"Could not read the size list ({type(e).__name__}: {e}).")
        c.setdefault("tags", {})[model] = dict(at=self._now(), rows=rows)
        self._save(c)
        return dict(ok=True, tags=rows, message="")

    # ---- what the UI shows
    def catalog(self, installed: list[str], ram_gb: float | None, vram_gb: float | None, query: str = "") -> list[dict]:
        have = set(installed)
        have_base = {n.split(":")[0] for n in installed}
        c, cur = self._load(), load_curated()
        out, names = [], set()
        for m in cur["models"]:
            names.add(m["name"])
            out.append(dict(name=m["name"], label=m["label"], best_for=m["best_for"], caps=m["caps"], source="curated", new=False, downloads=None,
                            updated=None, installed=m["name"] in have_base or any(n in have for n in (f"{m['name']}:{t['tag']}" for t in m["tags"])),
                            tags=[dict(tag=t["tag"], size_gb=t["size_gb"], fit=fit(t["size_gb"], ram_gb, vram_gb), installed=f"{m['name']}:{t['tag']}" in have)
                                  for t in m["tags"]]))
        for f in c.get("found", []):
            if f["name"] in names:
                continue
            sizes = f.get("sizes") or []
            fresh = f.get("first_seen") and self._now() - f["first_seen"] < 14 * 86400
            cached = (c.get("tags") or {}).get(f["name"], {}).get("rows")
            tags = ([dict(tag=r["tag"], size_gb=r["size_gb"], fit=fit(r["size_gb"], ram_gb, vram_gb), installed=f"{f['name']}:{r['tag']}" in have) for r in cached]
                    if cached else [dict(tag=s, size_gb=None, fit=fit(None, ram_gb, vram_gb), installed=f"{f['name']}:{s}" in have) for s in sizes])
            out.append(dict(name=f["name"], label=f["name"], best_for=f.get("desc") or "", caps=f.get("caps") or [], source="ollama.com", new=bool(fresh),
                            downloads=f.get("pulls"), updated=f.get("updated") or None, installed=f["name"] in have_base, tags=tags))
        q = query.strip().lower()
        if q:
            out = [m for m in out if q in m["name"].lower() or q in m["label"].lower() or q in m["best_for"].lower() or q in " ".join(m["caps"])]
        return out
