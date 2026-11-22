"""Canvas: one document (text, code, a table or a small web page) that you and an agent edit together next to the chat.

How it works
* The canvas lives in the database with a version number. Every change (yours, an accepted agent change, a restore) makes a new version, so
  nothing is ever lost and any earlier version can be brought back.
* Your own typing saves with the version you were looking at. If someone else changed it meanwhile the save is refused (a conflict) and
  you choose what to do: your text is never overwritten without you knowing.
* An agent edits through small text replacements ("this exact text becomes that"). By default they are PROPOSALS: they appear highlighted
  in the canvas and you accept or reject each one. An agent in plan mode can only propose, whatever the setting. Switch on "apply agent
  changes straight away" to skip the review (every change is still a version you can undo).
* A proposal is checked again when you accept it: if the text it replaces has changed or become ambiguous, you are told which part
  conflicts and can skip it.
* Each canvas is also kept as an artifact (a new version of the same file), so it shows on the Artifacts page and goes to your storages.
  Nothing a canvas holds is ever run: HTML is shown only in the sandboxed preview.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid

from ..artifacts.types import safe_name

KEY = "canvas_config"
DEFAULTS: dict = dict(enabled=True, auto_apply=False, max_chars=200_000, keep_versions=100)
LIMITS = {"max_chars": (1000, 2_000_000), "keep_versions": (5, 1000)}
TYPES = {"markdown": "md", "code": "txt", "table": "csv", "html": "html", "text": "txt"}
LANGUAGES = {"python": "py", "javascript": "js", "typescript": "ts", "json": "json", "html": "html", "css": "css", "sql": "sql", "yaml": "yaml", "bash": "sh", "markdown": "md", "text": "txt"}
MAX_EDITS = 30
MAX_PENDING = 20
MAX_PER_SESSION = 50
AUTHORS_RE = re.compile(r"^(user|restore|agent:[\w .-]{1,60})$")


class CanvasError(ValueError):
    """Something the canvas cannot accept or find. Safe to show."""


class CanvasConflict(CanvasError):
    """The canvas changed since the version the caller was looking at (or an edit no longer fits). `current` is the version now; `conflicts` lists edit numbers."""

    def __init__(self, message: str, current: int | None = None, conflicts: list[int] | None = None):
        super().__init__(message)
        self.current, self.conflicts = current, conflicts or []


def apply_edits(content: str, edits: list[dict], skip: bool = False) -> tuple[str, list[int]]:
    """Apply replacement edits to `content` in order. An edit is {old, new} (old must appear exactly once) or {at: "start" | "end", new}.
    Returns the new text and the numbers (from 1) of the edits that could not be applied; without `skip` the first of those raises CanvasConflict."""
    out, bad = content, []
    for i, e in enumerate(edits, 1):
        if "at" in e:
            out = (e["new"] + out) if e["at"] == "start" else (out + e["new"])
            continue
        n = out.count(e["old"])
        if n != 1:
            if not skip:
                raise CanvasConflict(f"edit {i}: " + ("the text it replaces is no longer there" if n == 0 else f"the text it replaces appears {n} times, so it is ambiguous"), conflicts=[i])
            bad.append(i)
            continue
        out = out.replace(e["old"], e["new"], 1)
    return out, bad


def clean_edits(raw, max_chars: int) -> list[dict]:
    """Validate the edits an agent (or a caller) sent; raises CanvasError with a plain reason."""
    if not isinstance(raw, list) or not raw or len(raw) > MAX_EDITS:
        raise CanvasError(f"send 1 to {MAX_EDITS} edits")
    out = []
    for i, e in enumerate(raw, 1):
        if not isinstance(e, dict) or not isinstance(e.get("new"), str):
            raise CanvasError(f"edit {i}: needs 'new' text")
        if len(e["new"]) > max_chars:
            raise CanvasError(f"edit {i}: the new text is longer than {max_chars} characters")
        if e.get("at") in ("start", "end"):
            out.append({"at": e["at"], "new": e["new"]})
        elif isinstance(e.get("old"), str) and e["old"]:
            out.append({"old": e["old"], "new": e["new"]})
        else:
            raise CanvasError(f"edit {i}: give the exact 'old' text to replace, or at: 'start' / 'end'")
    return out


class CanvasService:
    def __init__(self, db, artifacts=None):
        self.db, self.artifacts = db, artifacts
        self._subs: dict[str, set] = {}

    # ---- settings
    def config(self) -> dict:
        try:
            stored = json.loads(self.db.get_setting(KEY, "{}") or "{}")
        except ValueError:
            stored = {}
        return {**DEFAULTS, **(stored if isinstance(stored, dict) else {})}

    def set_config(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise CanvasError("send an object")
        cur = self.config()
        for k, v in patch.items():
            if k not in DEFAULTS:
                raise CanvasError(f"unknown canvas setting '{k}'")
            if k in ("enabled", "auto_apply"):
                if not isinstance(v, bool):
                    raise CanvasError(f"{k} must be true or false")
            else:
                lo, hi = LIMITS[k]
                if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                    raise CanvasError(f"{k} must be a whole number from {lo} to {hi}")
            cur[k] = v
        self.db.set_setting(KEY, json.dumps(cur))
        return cur

    def _on(self) -> dict:
        cfg = self.config()
        if not cfg["enabled"]:
            raise CanvasError("the canvas is switched off in Settings")
        return cfg

    # ---- live updates (server-sent events)
    def subscribe(self, cid: str):
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(cid, set()).add((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, cid: str, q) -> None:
        self._subs[cid] = {s for s in self._subs.get(cid, set()) if s[1] is not q}

    def _emit(self, cid: str, event: dict) -> None:
        for loop, q in list(self._subs.get(cid, ())):
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:                          # that client's loop has closed
                self._subs[cid].discard((loop, q))

    # ---- reading
    def _row(self, cid: str):
        r = self.db.one("SELECT * FROM canvases WHERE id=?", (cid,))
        if r is None:
            raise CanvasError(f"no such canvas '{cid}'")
        return r

    def _pending_count(self, cid: str) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM canvas_proposals WHERE canvas_id=? AND status='pending'", (cid,))["n"]

    def _public(self, r, content: bool = True) -> dict:
        d = dict(id=r["id"], title=r["title"], type=r["type"], language=r["language"], version=r["version"], session_id=r["session_id"], agent=r["agent"],
                 artifact_id=r["artifact_id"], size=len(r["content"]), created=r["created"], updated=r["updated"], pending=self._pending_count(r["id"]))
        if content:
            d["content"] = r["content"]
        return d

    def get(self, cid: str) -> dict:
        return self._public(self._row(cid))

    def list(self, session_id: str = "", limit: int = 50) -> list[dict]:
        rows = self.db.query("SELECT * FROM canvases" + (" WHERE session_id=?" if session_id else "") + " ORDER BY updated DESC LIMIT ?",
                             ([session_id] if session_id else []) + [max(1, min(limit, 200))])
        return [self._public(r, content=False) for r in rows]

    def latest_for_session(self, session_id: str) -> str:
        r = self.db.one("SELECT id FROM canvases WHERE session_id=? ORDER BY updated DESC LIMIT 1", (session_id,)) if session_id else None
        return r["id"] if r else ""

    # ---- writing
    def _version(self, cid: str, version: int, content: str, author: str, note: str = "") -> None:
        self.db.execute("INSERT INTO canvas_versions(canvas_id,version,content,author,note,created) VALUES(?,?,?,?,?,?)", (cid, version, content, author, note[:200], time.time()))
        keep = self.config()["keep_versions"]
        self.db.execute("DELETE FROM canvas_versions WHERE canvas_id=? AND version <= ?", (cid, version - keep))

    def create(self, title: str, type: str = "markdown", content: str = "", language: str = "", session_id: str = "", agent: str = "", author: str = "user") -> dict:
        cfg = self._on()
        if type not in TYPES:
            raise CanvasError(f"type must be one of {', '.join(TYPES)}")
        if language and language not in LANGUAGES:
            raise CanvasError(f"language must be one of {', '.join(LANGUAGES)}")
        if len(content) > cfg["max_chars"]:
            raise CanvasError(f"the content is longer than the {cfg['max_chars']} character limit")
        if session_id and self.db.one("SELECT COUNT(*) AS n FROM canvases WHERE session_id=?", (session_id,))["n"] >= MAX_PER_SESSION:
            raise CanvasError(f"a chat can hold at most {MAX_PER_SESSION} canvases: delete one first")
        if not AUTHORS_RE.match(author):
            author = "user"
        cid, now = uuid.uuid4().hex[:12], time.time()
        self.db.execute("INSERT INTO canvases(id,title,type,language,content,version,session_id,agent,artifact_id,selection,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, (title or "").strip()[:100] or "Untitled", type, language, content, 1, session_id, agent, "", "{}", now, now))
        self._version(cid, 1, content, author, "created")
        out = self.snapshot(cid) if content else self.get(cid)
        self._emit(cid, {"type": "created", "version": 1})
        return out

    def update(self, cid: str, *, content: str | None = None, title: str | None = None, language: str | None = None, base_version: int | None = None, author: str = "user") -> dict:
        cfg = self._on()
        r = self._row(cid)
        if content is not None:
            if base_version is not None and base_version != r["version"]:
                raise CanvasConflict("this canvas changed since you opened it", current=r["version"])
            if len(content) > cfg["max_chars"]:
                raise CanvasError(f"the content is longer than the {cfg['max_chars']} character limit")
        if language is not None and language and language not in LANGUAGES:
            raise CanvasError(f"language must be one of {', '.join(LANGUAGES)}")
        new_title = (title.strip()[:100] or r["title"]) if title is not None else r["title"]
        new_lang = r["language"] if language is None else language
        changed = content is not None and content != r["content"]
        version = r["version"] + 1 if changed else r["version"]
        self.db.execute("UPDATE canvases SET title=?, language=?, content=?, version=?, updated=? WHERE id=?",
                        (new_title, new_lang, content if changed else r["content"], version, time.time(), cid))
        if changed:
            self._version(cid, version, content, author)
        self._emit(cid, {"type": "version", "version": version, "author": author})
        return self.get(cid)

    def delete(self, cid: str) -> dict:
        self._row(cid)
        for t in ("canvas_proposals", "canvas_versions"):
            self.db.execute(f"DELETE FROM {t} WHERE canvas_id=?", (cid,))
        self.db.execute("DELETE FROM canvases WHERE id=?", (cid,))
        self._emit(cid, {"type": "deleted"})
        return {"deleted": True}

    def versions(self, cid: str) -> list[dict]:
        self._row(cid)
        rows = self.db.query("SELECT version,author,note,created,LENGTH(content) AS size FROM canvas_versions WHERE canvas_id=? ORDER BY version DESC", (cid,))
        return [dict(version=x["version"], author=x["author"], note=x["note"], created=x["created"], size=x["size"]) for x in rows]

    def version_content(self, cid: str, n: int) -> dict:
        self._row(cid)
        r = self.db.one("SELECT version,content,author,created FROM canvas_versions WHERE canvas_id=? AND version=?", (cid, n))
        if r is None:
            raise CanvasError(f"version {n} is not kept (only the newest {self.config()['keep_versions']} are)")
        return dict(version=r["version"], content=r["content"], author=r["author"], created=r["created"])

    def restore(self, cid: str, n: int) -> dict:
        old, before = self.version_content(cid, n), self._row(cid)["version"]
        out = self.update(cid, content=old["content"], author="restore")
        if out["version"] != before:
            self.db.execute("UPDATE canvas_versions SET note=? WHERE canvas_id=? AND version=?", (f"restored version {n}", cid, out["version"]))
        return self.snapshot(cid)

    # ---- the user's selection (so "make this shorter" can target it)
    def set_selection(self, cid: str, start: int, end: int) -> dict:
        r = self._row(cid)
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)) or not 0 <= start <= end <= len(r["content"]):
            raise CanvasError("the selection is outside the document")
        sel = dict(start=start, end=end, text=r["content"][start:end][:5000], version=r["version"])
        self.db.execute("UPDATE canvases SET selection=? WHERE id=?", (json.dumps(sel), cid))
        return sel

    def selection(self, cid: str) -> dict | None:
        """The text the user has selected, if it is still there (found at the saved place, or exactly once elsewhere)."""
        r = self._row(cid)
        try:
            sel = json.loads(r["selection"] or "{}")
        except ValueError:
            sel = {}
        text = sel.get("text") or ""
        if not text:
            return None
        content = r["content"]
        if content[sel["start"]:sel["end"]] == text:
            return {"text": text, "start": sel["start"], "end": sel["end"]}
        if content.count(text) == 1:
            i = content.index(text)
            return {"text": text, "start": i, "end": i + len(text)}
        return None

    # ---- proposals
    def _proposal_public(self, p, content: str) -> dict:
        edits = []
        for i, e in enumerate(json.loads(p["edits"]), 1):
            ok, start, end = True, None, None
            if "at" in e:
                start = end = 0 if e["at"] == "start" else len(content)
            elif content.count(e["old"]) == 1:
                start = content.index(e["old"])
                end = start + len(e["old"])
            else:
                ok = False
            edits.append({**e, "n": i, "fits": ok, "start": start, "end": end})
        return dict(id=p["id"], canvas_id=p["canvas_id"], base_version=p["base_version"], author=p["author"], note=p["note"], status=p["status"], created=p["created"],
                    resolved=p["resolved"], edits=edits)

    def proposals(self, cid: str, status: str = "pending") -> list[dict]:
        r = self._row(cid)
        rows = self.db.query("SELECT * FROM canvas_proposals WHERE canvas_id=?" + (" AND status=?" if status else "") + " ORDER BY created", ([cid, status] if status else [cid]))
        return [self._proposal_public(p, r["content"]) for p in rows]

    def propose(self, cid: str, edits, *, author: str, note: str = "", base_version: int = 0, apply: bool = False) -> dict:
        """An agent's change. It is checked now (so the agent hears at once if it does not fit), then either applied (when allowed) or kept for you to review."""
        cfg = self._on()
        r = self._row(cid)
        if base_version and base_version != r["version"]:
            raise CanvasConflict(f"the canvas is now at version {r['version']}, not {base_version}: read it again before editing", current=r["version"])
        edits = clean_edits(edits, cfg["max_chars"])
        new, _ = apply_edits(r["content"], edits)
        if len(new) > cfg["max_chars"]:
            raise CanvasError(f"the result would be longer than the {cfg['max_chars']} character limit")
        if not AUTHORS_RE.match(author):
            author = "agent:agent"
        if apply and cfg["auto_apply"]:
            out = self.update(cid, content=new, author=author)
            self.db.execute("UPDATE canvas_versions SET note=? WHERE canvas_id=? AND version=?", (note[:200] or "agent change", cid, out["version"]))
            self.snapshot(cid)
            return dict(status="applied", version=out["version"], proposal_id="")
        if self._pending_count(cid) >= MAX_PENDING:
            raise CanvasError(f"there are already {MAX_PENDING} changes waiting for your review: ask the user to accept or reject some first")
        pid = uuid.uuid4().hex[:12]
        self.db.execute("INSERT INTO canvas_proposals(id,canvas_id,base_version,edits,author,note,status,created,resolved) VALUES(?,?,?,?,?,?,?,?,?)",
                        (pid, cid, r["version"], json.dumps(edits), author, note[:300], "pending", time.time(), 0))
        self._emit(cid, {"type": "proposal", "id": pid})
        return dict(status="proposed", version=r["version"], proposal_id=pid)

    def _resolve(self, pid: str, status: str) -> None:
        self.db.execute("UPDATE canvas_proposals SET status=?, resolved=? WHERE id=?", (status, time.time(), pid))

    def _proposal(self, cid: str, pid: str):
        p = self.db.one("SELECT * FROM canvas_proposals WHERE id=? AND canvas_id=?", (pid, cid))
        if p is None:
            raise CanvasError(f"no such proposal '{pid}'")
        if p["status"] != "pending":
            raise CanvasError(f"this change was already {p['status']}")
        return p

    def accept(self, cid: str, pid: str, skip_conflicts: bool = False) -> dict:
        self._on()
        p, r = self._proposal(cid, pid), self._row(cid)
        edits = json.loads(p["edits"])
        new, bad = apply_edits(r["content"], edits, skip=skip_conflicts)
        if bad and len(bad) == len(edits):
            self._resolve(pid, "stale")
            raise CanvasConflict("none of this change fits any more, so it was dropped", conflicts=bad)
        out = self.update(cid, content=new, author=p["author"])
        self.db.execute("UPDATE canvas_versions SET note=? WHERE canvas_id=? AND version=?", ((p["note"] or "agent change")[:200], cid, out["version"]))
        self._resolve(pid, "accepted")
        self.snapshot(cid)
        return {**self.get(cid), "skipped": bad}

    def reject(self, cid: str, pid: str) -> dict:
        self._proposal(cid, pid)
        self._resolve(pid, "rejected")
        self._emit(cid, {"type": "proposal", "id": pid})
        return {"rejected": True}

    def accept_all(self, cid: str, skip_conflicts: bool = False) -> dict:
        done, skipped, dropped = 0, 0, 0
        for p in self.proposals(cid):
            try:
                out = self.accept(cid, p["id"], skip_conflicts=skip_conflicts)
                done += 1
                skipped += len(out["skipped"])
            except CanvasConflict as e:
                if not skip_conflicts:
                    raise CanvasConflict(f"change '{p['note'] or p['id']}' does not fit any more: {e}", conflicts=e.conflicts) from e
                dropped += 1
        return {**self.get(cid), "accepted": done, "skipped": skipped, "dropped": dropped}

    def reject_all(self, cid: str) -> dict:
        n = 0
        for p in self.proposals(cid):
            self._resolve(p["id"], "rejected")
            n += 1
        self._emit(cid, {"type": "proposal", "id": ""})
        return {"rejected": n}

    # ---- keeping a copy as an artifact
    def snapshot(self, cid: str) -> dict:
        """Keep the canvas as an artifact (a new version of the same file when the text changed). Nothing happens when artifacts are off."""
        r = self._row(cid)
        if self.artifacts is not None and r["content"]:
            ext = LANGUAGES.get(r["language"], "txt") if r["type"] == "code" else TYPES[r["type"]]
            name = safe_name(f"{r['title']}.{ext}")
            try:
                art = self.artifacts.save_text(name, r["content"], agent=r["agent"], session_id=r["session_id"], workspace="canvas", source="canvas", tags=["canvas"], path=f"canvas/{cid}/{name}")
                self.db.execute("UPDATE canvases SET artifact_id=? WHERE id=?", (art["id"], cid))
            except ValueError:                                   # artifacts switched off, or the file is over the size limit: the canvas itself is safe in the database
                pass
        return self.get(cid)

    def counts(self) -> dict:
        """For the AI-BOM: how many canvases and pending changes exist (never their content)."""
        return dict(canvases=self.db.one("SELECT COUNT(*) AS n FROM canvases")["n"], pending=self.db.one("SELECT COUNT(*) AS n FROM canvas_proposals WHERE status='pending'")["n"])
