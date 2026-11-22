"""Talking to the LOCAL Ollama server (its documented API): installed models, details, loaded models, delete, pull with progress.

Never touches ollama.com/api (robots.txt disallows it) - that is only for the website pages in ollama_library.py.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

import httpx

from .syscheck import OLLAMA_URL

TRANSPORT: httpx.BaseTransport | None = None
ATRANSPORT: httpx.AsyncBaseTransport | None = None
NAME_RE = re.compile(r"^[A-Za-z0-9][\w.\-/:]*$")


class LocalError(Exception):
    """Ollama is unreachable or refused. The message is safe to show."""


def _base(base: str | None) -> str:
    return (base or OLLAMA_URL).rstrip("/")


def _req(method: str, base: str | None, path: str, body: dict | None = None, timeout: float = 15) -> dict:
    try:
        with httpx.Client(transport=TRANSPORT, timeout=timeout) as c:
            r = c.request(method, _base(base) + path, json=body)
    except httpx.HTTPError as e:
        raise LocalError(f"Ollama isn't reachable ({type(e).__name__}). Start it first.") from e
    if r.status_code >= 400:
        try:
            msg = r.json().get("error") or r.text
        except ValueError:
            msg = r.text
        raise LocalError(f"Ollama said: {str(msg)[:200]}")
    try:
        return r.json() if r.content else {}
    except ValueError:
        return {}


def installed(base: str | None = None) -> list[dict]:
    """Installed models with size, family, parameter size and quantisation (from /api/tags)."""
    out = []
    for m in _req("GET", base, "/api/tags").get("models", []):
        d = m.get("details") or {}
        out.append(dict(name=m["name"], size=int(m.get("size") or 0), modified=m.get("modified_at"), family=d.get("family"),
                        parameters=d.get("parameter_size"), quantization=d.get("quantization_level"), capabilities=m.get("capabilities") or []))
    return out


def show(name: str, base: str | None = None) -> dict:
    if not NAME_RE.match(name):
        raise LocalError("invalid model name")
    d = _req("POST", base, "/api/show", {"model": name})
    info = d.get("model_info") or {}
    ctx = next((v for k, v in info.items() if k.endswith(".context_length")), None)
    return dict(name=name, capabilities=d.get("capabilities") or [], context_length=ctx, details=d.get("details") or {},
                parameter_count=info.get("general.parameter_count"))


def loaded(base: str | None = None) -> list[dict]:
    """Models currently in memory (from /api/ps), with how much sits on the GPU."""
    return [dict(name=m["name"], size=int(m.get("size") or 0), vram=int(m.get("size_vram") or 0), expires=m.get("expires_at"))
            for m in _req("GET", base, "/api/ps").get("models", [])]


def delete(name: str, base: str | None = None) -> None:
    if not NAME_RE.match(name):
        raise LocalError("invalid model name")
    _req("DELETE", base, "/api/delete", {"model": name})


class Progress:
    """Folds Ollama's per-layer pull lines into one overall percentage."""

    def __init__(self) -> None:
        self.layers: dict[str, list[int]] = {}
        self.status = "starting"

    def add(self, d: dict) -> dict:
        self.status = d.get("status") or self.status
        digest = d.get("digest")
        if digest and d.get("total"):
            slot = self.layers.setdefault(digest, [0, 0])
            slot[0], slot[1] = int(d.get("completed") or 0), int(d["total"])
        total = sum(t for _, t in self.layers.values())
        done = sum(min(c, t) for c, t in self.layers.values())
        return dict(type="progress", status=self.status, completed=done, total=total, percent=round(done * 100 / total, 1) if total else None)


async def pull(name: str, base: str | None = None) -> AsyncIterator[dict]:
    """Stream {type: progress|done|error}. Closing the generator stops the download; Ollama keeps what it fetched, so a retry resumes."""
    if not NAME_RE.match(name):
        yield dict(type="error", message="That is not a valid model name.")
        return
    prog = Progress()
    try:
        async with httpx.AsyncClient(transport=ATRANSPORT, timeout=httpx.Timeout(None, connect=10)) as c:
            async with c.stream("POST", _base(base) + "/api/pull", json={"model": name, "stream": True}) as r:
                if r.status_code >= 400:
                    yield dict(type="error", message=f"Ollama {r.status_code}: {(await r.aread()).decode(errors='replace')[:200]}")
                    return
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if d.get("error"):
                        yield dict(type="error", message=str(d["error"]))
                        return
                    yield prog.add(d)
        yield dict(type="done", model=name)
    except httpx.HTTPError as e:
        yield dict(type="error", message=f"Ollama isn't reachable ({type(e).__name__}). Start it first.")
