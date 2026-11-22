"""Persistent chat history (Phase 4). The UI shows exactly what is stored here; the ADK session (in memory) is
rebuilt from it on resume, so a conversation survives a server restart."""
from __future__ import annotations

import json
import time

from .db import Database

TITLE_MAX = 60


class ChatStore:
    def __init__(self, db: Database):
        self.db = db

    def ensure(self, sid: str, provider: str, agent: str | None, model: str | None, workspace: str | None, first_text: str) -> None:
        now = time.time()
        title = " ".join(first_text.split())[:TITLE_MAX] or "New chat"
        self.db.execute(
            "INSERT INTO chat_sessions(id,title,provider,agent,model,workspace,created,updated) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET provider=excluded.provider, agent=excluded.agent, model=excluded.model, "
            "workspace=excluded.workspace, updated=excluded.updated",
            (sid, title, provider, agent, model, workspace, now, now))

    def add(self, sid: str, role: str, text: str, meta: dict | None = None) -> int:
        cur = self.db.execute("INSERT INTO chat_messages(session_id,ts,role,text,meta) VALUES(?,?,?,?,?)",
                              (sid, time.time(), role, text, json.dumps(meta or {}, default=str)))
        self.db.execute("UPDATE chat_sessions SET updated=? WHERE id=?", (time.time(), sid))
        return cur.lastrowid

    def mark_interrupted(self) -> int:
        """At startup nothing is running any more. A chat whose last message is the user's has no answer: a server
        restart (or a crash) cut that turn off. Record it, so the chat says so and the user can continue."""
        n = 0
        for s in self.db.query("SELECT id FROM chat_sessions"):
            last = self.db.one("SELECT role FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 1", (s["id"],))
            if last and last["role"] == "user":
                self.add(s["id"], "bot", "", {"status": "interrupted", "error": {
                    "title": "The run was interrupted", "code": "interrupted", "retryable": True, "raw": "the server restarted or stopped while this turn was running",
                    "hint": "Progress up to that point was kept. Send “continue” to pick it up."}})
                n += 1
        return n

    def messages(self, sid: str) -> list[dict]:
        return [dict(id=r["id"], ts=r["ts"], role=r["role"], text=r["text"], meta=json.loads(r["meta"] or "{}"))
                for r in self.db.query("SELECT * FROM chat_messages WHERE session_id=? ORDER BY id", (sid,))]

    def exists(self, sid: str) -> bool:
        return self.db.one("SELECT 1 FROM chat_sessions WHERE id=?", (sid,)) is not None

    def get(self, sid: str) -> dict | None:
        r = self.db.one("SELECT * FROM chat_sessions WHERE id=?", (sid,))
        return None if r is None else dict(r)

    def list(self, limit: int = 100) -> list[dict]:
        rows = self.db.query(
            "SELECT s.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.id AND m.role!='refused') AS messages "
            "FROM chat_sessions s ORDER BY s.updated DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def rename(self, sid: str, title: str) -> bool:
        cur = self.db.execute("UPDATE chat_sessions SET title=? WHERE id=?", (title.strip()[:120] or "Untitled", sid))
        return cur.rowcount > 0

    def delete(self, sid: str) -> bool:
        self.db.execute("DELETE FROM chat_messages WHERE session_id=?", (sid,))
        return self.db.execute("DELETE FROM chat_sessions WHERE id=?", (sid,)).rowcount > 0
