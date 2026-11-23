"""Fetching the Ollama model a guard needs, so nobody has to run `ollama pull` by hand.

One GuardPuller per app. `ensure()` starts a background download of a model that is missing (at most one per server+model, however
many chats ask at once) and returns its state; the UI polls `states()` for a progress bar. It reuses the Phase 1 machinery
(`easy.ollama_local.pull` streams Ollama's own progress), adds a disk-space check, and turns raw errors into plain sentences.
Only the model a guard profile names is ever pulled.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from urllib.parse import urlparse

from ..easy import ollama_local, syscheck

KEEP_S = 600.0                 # a finished or failed download stays visible this long
RETRY_AFTER_S = 60.0           # after a failed download, a chat does not try again for this long
MIN_FREE_GB = 2.0              # with no size known, refuse below this much free space
HEADROOM = 1.15                # a download needs its size plus 15 %


def norm(name: str) -> str:
    """Ollama's own name for a model: no tag means :latest."""
    n = name.strip()
    return n if ":" in n.rsplit("/", 1)[-1] else n + ":latest"


def is_local(base: str) -> bool:
    return (urlparse(base).hostname or "").lower() in ("127.0.0.1", "localhost", "::1")


def plain_error(message: str, model: str) -> tuple[str, str]:
    """(kind, a sentence a person can act on) for an error from Ollama or from the network."""
    m = message.lower()
    if "isn't reachable" in m or "connection refused" in m or "all connection attempts failed" in m:
        return "ollama_down", "Ollama is not running, so the guard model cannot be downloaded. Start Ollama (Setup page), then try again."
    if "no space left" in m or "disk full" in m:
        return "disk", "The disk is full, so the download stopped. Free some space (the model is several GB), then try again."
    if "file does not exist" in m or "not found" in m or "manifest" in m or "invalid model name" in m or "pull access denied" in m:
        return "unknown_model", f"Ollama does not know a model called '{model}'. Check the name in the profile (for example llama-guard3:8b)."
    if any(w in m for w in ("no such host", "dial tcp", "timeout", "timed out", "tls", "lookup", "connection reset", "eof", "unreachable", "network")):
        return "network", "The download could not reach the internet (ollama.com). Check the connection and try again; what was fetched is kept."
    return "other", f"The download failed: {message[:160].rstrip('. ')}."


class GuardPuller:
    def __init__(self, list_models: Callable[[str, dict | None], list[str]] | None = None,
                 pull: Callable[..., AsyncIterator[dict]] | None = None,
                 free_gb: Callable[[], float | None] | None = None, clock: Callable[[], float] = time.time):
        self._list = list_models or (lambda base, headers: [m["name"] for m in ollama_local.installed(base, headers)])
        self._pull = pull or ollama_local.pull
        self._free = free_gb or (lambda: syscheck.free_gb(Path.home()))
        self._clock = clock
        self._st: dict[str, dict] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------ queries
    @staticmethod
    def key(base: str, model: str) -> str:
        return f"{base.rstrip('/')}|{norm(model)}"

    async def has(self, base: str, model: str, headers: dict | None = None) -> bool:
        names = await asyncio.to_thread(self._list, base, headers)
        return norm(model) in {norm(n) for n in names}

    def state(self, base: str, model: str) -> dict | None:
        st = self._st.get(self.key(base, model))
        return dict(st) if st else None

    def states(self) -> list[dict]:
        self._prune()
        return [dict(s) for s in sorted(self._st.values(), key=lambda s: -s["started"])]

    def _prune(self) -> None:
        now = self._clock()
        for k in [k for k, s in self._st.items() if s["status"] != "downloading" and now - s["finished"] > KEEP_S]:
            del self._st[k]

    # ------------------------------------------------------------------ starting and stopping
    def _new(self, base: str, model: str, licence: str, size_gb: float | None) -> dict:
        st = dict(key=self.key(base, model), model=model, base=base, where="on this PC" if is_local(base) else "on this network", status="downloading",
                  completed=0, total=0, percent=None, speed_bps=None, eta_s=None, size_hint_gb=size_gb, licence=licence, message="", error_kind="",
                  started=self._clock(), finished=0.0)
        self._st[st["key"]] = st
        return st

    def _fail(self, st: dict, kind: str, message: str) -> dict:
        st.update(status="error", error_kind=kind, message=message, finished=self._clock())
        return dict(st)

    def _disk_problem(self, base: str, size_gb: float | None) -> str:
        if not is_local(base):
            return ""                                           # the free space of another computer is not ours to read
        free = self._free()
        if free is None:
            return ""
        need = round(size_gb * HEADROOM, 1) if size_gb else MIN_FREE_GB
        if free < need:
            return f"Not enough free disk space for the guard model: it needs about {need} GB and {free} GB is free. Free some space, then try again."
        return ""

    async def ensure(self, base: str, model: str, *, headers: dict | None = None, licence: str = "", size_gb: float | None = None,
                     check: bool = True, force: bool = False) -> dict:
        """The state of the download for this model: starts one when the model is missing. `check=False` when the caller already saw a 404.
        A model that is already there comes back as {status: "ready"} and nothing is stored.
        `force=True` (a person pressed "try again") skips the pause after a failed download."""
        key = self.key(base, model)
        self._prune()
        cur = self._st.get(key)
        if cur and cur["status"] == "downloading":
            return dict(cur)
        if cur and cur["status"] == "error" and not force and self._clock() - cur["finished"] < RETRY_AFTER_S:
            return dict(cur)
        if cur and cur["status"] == "done" and not check:
            return dict(cur)
        if check:
            try:
                if await self.has(base, model, headers):
                    return {"key": key, "model": model, "base": base, "status": "ready"}
            except ollama_local.LocalError as e:
                return self._fail(self._new(base, model, licence, size_gb), *plain_error(str(e), model))
        st = self._new(base, model, licence, size_gb)
        problem = self._disk_problem(base, size_gb)
        if problem:
            return self._fail(st, "disk", problem)
        self._tasks[key] = asyncio.get_running_loop().create_task(self._run(st, headers))
        return dict(st)

    def cancel(self, base: str, model: str) -> bool:
        task = self._tasks.get(self.key(base, model))
        st = self._st.get(self.key(base, model))
        if task is None or st is None or st["status"] != "downloading":
            return False
        task.cancel()
        return True

    # ------------------------------------------------------------------ the download itself
    async def _run(self, st: dict, headers: dict | None) -> None:
        gen = self._pull(st["model"], st["base"], headers)
        mark_t, mark_c = self._clock(), 0
        try:
            async for ev in gen:
                if ev["type"] == "progress":
                    done, total = int(ev.get("completed") or 0), int(ev.get("total") or 0)
                    now = self._clock()
                    if now - mark_t >= 1.0:
                        st["speed_bps"] = max(0, round((done - mark_c) / (now - mark_t)))
                        mark_t, mark_c = now, done
                        if st["speed_bps"] and total:
                            st["eta_s"] = round((total - done) / st["speed_bps"])
                    st.update(completed=done, total=total, percent=ev.get("percent"))
                    if total and is_local(st["base"]):
                        free = self._free()
                        if free is not None and free * 1e9 < (total - done) * 1.02:
                            self._fail(st, "disk", f"Not enough free disk space to finish the guard model: it needs {round((total - done) / 1e9, 1)} GB more "
                                                   f"and {free} GB is free. Free some space, then try again.")
                            break
                elif ev["type"] == "done":
                    st.update(status="done", percent=100.0, message="", finished=self._clock())
                else:
                    self._fail(st, *plain_error(str(ev.get("message") or "unknown error"), st["model"]))
                    break
            else:
                if st["status"] == "downloading":              # the stream ended without a verdict
                    self._fail(st, "other", "The download ended early. Try again; what was fetched is kept.")
        except asyncio.CancelledError:
            st.update(status="cancelled", message="Download cancelled. What was fetched is kept, so a retry resumes.", finished=self._clock())
        finally:
            await gen.aclose()
            self._tasks.pop(st["key"], None)
