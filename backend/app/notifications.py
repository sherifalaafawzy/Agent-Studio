"""The in-app notification center: messages from workflows ("Notify me" boxes) and from the scheduler, shown under the bell in the sidebar.

Nothing here leaves the computer: there is no e-mail, chat or push service behind it. A notification may ask for a desktop pop-up as well (`desktop`);
the page shows one through the browser's (or the desktop app's) own notification API, and only after you allowed it there.
The newest KEEP notifications are kept; older ones are removed.
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

LEVELS = ("info", "success", "warning", "error")
KEEP = 500


class NotificationError(ValueError):
    """A notification that cannot be made. Safe to show."""


class NotificationStore:
    def __init__(self, db, clock=time.time):
        self.db, self.clock = db, clock

    @staticmethod
    def _pub(r) -> dict:
        return dict(id=r["id"], created=r["created"], title=r["title"], body=r["body"], level=r["level"], source=r["source"], link=r["link"], read=bool(r["read"]), desktop=bool(r["desktop"]))

    def add(self, title: str, body: str = "", *, level: str = "info", source: str = "", link: str = "", desktop: bool = False) -> dict:
        title, body = str(title or "").strip()[:200], str(body or "")[:4000]
        if not title:
            raise NotificationError("a notification needs a title")
        if level not in LEVELS:
            raise NotificationError(f"level must be one of {', '.join(LEVELS)}")
        link = str(link or "")[:300]
        if link and not link.startswith("/"):
            raise NotificationError("a link must be a page of this app (starting with /)")
        nid = uuid.uuid4().hex[:12]
        self.db.execute("INSERT INTO notifications(id,created,title,body,level,source,link,read,desktop) VALUES(?,?,?,?,?,?,?,0,?)",
                        (nid, self.clock(), title, body, level, str(source or "")[:40], link, 1 if desktop else 0))
        self.db.execute(f"DELETE FROM notifications WHERE id NOT IN (SELECT id FROM notifications ORDER BY created DESC, rowid DESC LIMIT {KEEP})")
        return self._pub(self.db.one("SELECT * FROM notifications WHERE id=?", (nid,)))

    def list(self, limit: int = 50, unread_only: bool = False, after: float | None = None) -> dict:
        limit = max(1, min(int(limit), 200))
        where, args = [], []
        if unread_only:
            where.append("read=0")
        if after is not None:
            where.append("created > ?")
            args.append(after)
        sql = "SELECT * FROM notifications" + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY created DESC, rowid DESC LIMIT {limit}"
        return dict(items=[self._pub(r) for r in self.db.query(sql, tuple(args))], unread=self.unread())

    def unread(self) -> int:
        return self.db.one("SELECT COUNT(*) n FROM notifications WHERE read=0")["n"]

    def mark_read(self, nid: str) -> None:
        if self.db.one("SELECT 1 FROM notifications WHERE id=?", (nid,)) is None:
            raise KeyError(nid)
        self.db.execute("UPDATE notifications SET read=1 WHERE id=?", (nid,))

    def mark_all_read(self) -> None:
        self.db.execute("UPDATE notifications SET read=1 WHERE read=0")

    def delete(self, nid: str) -> None:
        self.db.execute("DELETE FROM notifications WHERE id=?", (nid,))

    def clear(self) -> None:
        self.db.execute("DELETE FROM notifications")


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/notifications", tags=["notifications"])

    def store(request: Request) -> NotificationStore:
        return request.app.state.s.notifications

    @r.get("")
    def list_(request: Request, limit: int = 50, unread_only: bool = False, after: float | None = None):
        return store(request).list(limit, unread_only, after)

    @r.post("/{nid}/read")
    def read(nid: str, request: Request):
        try:
            store(request).mark_read(nid)
        except KeyError:
            return JSONResponse(status_code=404, content={"detail": "no such notification"})
        return {"ok": True, "unread": store(request).unread()}

    @r.post("/read-all")
    def read_all(request: Request):
        store(request).mark_all_read()
        return {"ok": True, "unread": 0}

    @r.delete("/{nid}")
    def delete(nid: str, request: Request):
        store(request).delete(nid)
        return {"ok": True, "unread": store(request).unread()}

    @r.delete("")
    def clear(request: Request):
        store(request).clear()
        return {"ok": True, "unread": 0}

    app.include_router(r)
