"""Apps: projects that agents build with you. This is the store: the files, the versions, the changes waiting for review and the per-app key.

How it works
* An app is a folder under data/apps/<id>/ (see templates.py for the layout) plus a row in the database. Nothing an agent writes ever runs on this
  PC outside a sandbox: files are only written here, as text, inside the app folder (no `..`, no links, no `.git`).
* Every accepted change, restore or save-on-purpose makes a version (a zip of the files), so any earlier state can be brought back.
* A builder agent edits through proposals: file writes and exact-text patches that appear as a diff and wait for you to accept or reject them.
  In "simple" mode (a brand new app made from a prompt) they apply straight away; every change is still a version you can undo. An agent in plan
  mode can only ever propose. A proposal is checked again when you accept it. Text that looks like a secret is refused.
* An app that calls your agents gets its OWN API key, limited to the agents listed in its manifest (and never "all agents"). Only the key's hash is
  kept by the API; the key itself is kept here so Agent Studio can hand it to the app when it runs. It is never written into a file.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import stat
import time
import uuid
import zipfile
from pathlib import Path

from ..artifacts.service import scan_text
from ..canvas.service import CanvasError, apply_edits, clean_edits
from ..db import data_dir
from .errors import AppError
from .manifest import ManifestError, clean_manifest, slugify
from .studio import StudioMixin
from .templates import TEMPLATES, render

KEY = "apps_config"
SECRETS_KEY = "apps_secrets"
DEFAULTS: dict = dict(enabled=True, auto_apply=False, allow_server_apps=False, preview_port=8767, max_apps=50, max_files=300, max_file_kb=512, max_total_mb=20, keep_versions=50,
                      key_rate_per_min=60, git_name="", git_email="")
LIMITS = {"preview_port": (1024, 65535), "max_apps": (1, 500), "max_files": (10, 2000), "max_file_kb": (16, 4096), "max_total_mb": (1, 200), "keep_versions": (5, 500), "key_rate_per_min": (1, 10000)}
BOOLS = ("enabled", "auto_apply", "allow_server_apps")
BLOCKED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".next"}
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
ASSET_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".csv", ".json", ".txt", ".md", ".pdf", ".yaml", ".yml"}
MAX_ASSET = 2 * 1024 * 1024
MAX_READ = 2 * 1024 * 1024
MAX_CHANGES = 40
MAX_PENDING = 20
AUTHOR_RE = re.compile(r"^(user|restore|agent:[\w .-]{1,60})$")


def safe_rel(rel: object) -> str:
    """A path inside an app, normalised (forward slashes). Raises AppError for anything that could leave the folder or touch what we protect."""
    if not isinstance(rel, str) or not rel.strip():
        raise AppError("a file path is needed")
    p = rel.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    if len(p) > 200 or "\x00" in p or any(ord(c) < 32 for c in p):
        raise AppError("that file path is not allowed")
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        raise AppError("paths must be inside the app (no leading / or drive letter)")
    parts = p.split("/")
    if len(parts) > 8 or any(x in ("", ".", "..") or x.endswith((" ", ".")) for x in parts):
        raise AppError("that file path is not allowed (no '..', empty parts or trailing dots)")
    if any(x in BLOCKED_DIRS or x.lower() in BLOCKED_DIRS for x in parts):
        raise AppError("that folder is managed by Agent Studio and cannot be edited here")
    if any(x.split(".")[0].lower() in RESERVED for x in parts):
        raise AppError("that file name is reserved by Windows")
    return "/".join(parts)


def _unlock_and_retry(func, path, _exc) -> None:
    """rmtree helper: git makes its object files read-only, which stops a delete on Windows."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, onexc=_unlock_and_retry)


def _is_text(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


class AppStore(StudioMixin):
    def __init__(self, db, home: Path | None = None, keys=None, agents=None):
        self.db = db
        self.base = (Path(home) if home else data_dir()) / "apps"
        self.keys, self.agents = keys, agents                        # ApiKeys and the agent registry, set by State
        self.data = None                                             # the DataService (set by State): the data sources an app may be connected to
        self._init_studio()
        self._subs: dict[str, set] = {}

    # ---- settings
    def config(self) -> dict:
        try:
            stored = json.loads(self.db.get_setting(KEY, "{}") or "{}")
        except ValueError:
            stored = {}
        return {**DEFAULTS, **({k: v for k, v in stored.items() if k in DEFAULTS} if isinstance(stored, dict) else {})}

    def set_config(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise AppError("send an object")
        cur = self.config()
        for k, v in patch.items():
            if k not in DEFAULTS:
                raise AppError(f"unknown apps setting '{k}'")
            if k in BOOLS:
                if not isinstance(v, bool):
                    raise AppError(f"{k} must be true or false")
            elif k in ("git_name", "git_email"):
                if not isinstance(v, str) or len(v) > 120 or "\n" in v:
                    raise AppError(f"{k} must be a short piece of text")
            else:
                lo, hi = LIMITS[k]
                if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                    raise AppError(f"{k} must be a whole number from {lo} to {hi}")
            cur[k] = v
        self.db.set_setting(KEY, json.dumps(cur))
        return cur

    def _on(self) -> dict:
        cfg = self.config()
        if not cfg["enabled"]:
            raise AppError("apps are switched off in Settings")
        return cfg

    # ---- live updates (server-sent events)
    def subscribe(self, app_id: str):
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(app_id, set()).add((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, app_id: str, q) -> None:
        self._subs[app_id] = {s for s in self._subs.get(app_id, set()) if s[1] is not q}

    def _emit(self, app_id: str, event: dict) -> None:
        for loop, q in list(self._subs.get(app_id, ())):
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:                                      # that client's loop has closed
                self._subs[app_id].discard((loop, q))

    # ---- reading
    def _row(self, app_id: str):
        r = self.db.one("SELECT * FROM apps WHERE id=?", (app_id,)) if isinstance(app_id, str) and re.match(r"^[0-9a-f]{12}$", app_id) else None
        if r is None:
            raise AppError(f"no such app '{app_id}'")
        return r

    def root(self, app_id: str) -> Path:
        self._row(app_id)
        return self.base / app_id

    def _pending_count(self, app_id: str) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM app_proposals WHERE app_id=? AND status='pending'", (app_id,))["n"]

    def manifest(self, app_id: str) -> dict:
        f = self.root(app_id) / "app.json"
        try:
            return clean_manifest(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            raise AppError(f"app.json is missing or not valid: {e}")

    def _public(self, r) -> dict:
        try:
            m = self.manifest(r["id"])
            kind, agents = m["type"], [a["id"] for a in m["agents"]]
        except AppError:
            kind, agents = TEMPLATES.get(r["template"], {}).get("type", "static"), []
        files = self._walk(self.base / r["id"])
        return dict(id=r["id"], name=r["name"], slug=r["slug"], description=r["description"], template=r["template"], type=kind, mode=r["mode"], session_id=r["session_id"],
                    version=r["version"], git=json.loads(r["git"] or "{}"), agents=agents, pending=self._pending_count(r["id"]), files=len(files),
                    size=sum(f["size"] for f in files), created=r["created"], updated=r["updated"])

    def get(self, app_id: str) -> dict:
        return self._public(self._row(app_id))

    def list(self) -> list[dict]:
        return [self._public(r) for r in self.db.query("SELECT * FROM apps ORDER BY updated DESC")]

    def latest_for_session(self, session_id: str) -> str:
        r = self.db.one("SELECT id FROM apps WHERE session_id=? ORDER BY updated DESC LIMIT 1", (session_id,)) if session_id else None
        return r["id"] if r else ""

    def templates(self) -> list[dict]:
        return [dict(id=k, **v) for k, v in TEMPLATES.items()]

    # ---- files
    def _walk(self, root: Path) -> list[dict]:
        out: list[dict] = []
        if not root.is_dir():
            return out
        for p in sorted(root.rglob("*")):
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if any(x in BLOCKED_DIRS for x in rel.split("/")):
                continue
            out.append(dict(path=rel, size=p.stat().st_size))
        return out

    def tree(self, app_id: str) -> list[dict]:
        root = self.root(app_id)
        out = []
        for f in self._walk(root):
            try:
                binary = not _is_text((root / f["path"]).read_bytes()[:8192])
            except OSError:
                binary = True
            out.append({**f, "binary": binary})
        return out

    def _resolve(self, app_id: str, rel: str) -> tuple[Path, str]:
        rel = safe_rel(rel)
        root = self.root(app_id).resolve()
        cur = root
        for part in rel.split("/"):
            cur = cur / part
            if cur.is_symlink():
                raise AppError("that path goes through a link, which is not allowed")
        p = (root / rel).resolve()
        if root not in p.parents:
            raise AppError("that path is outside the app")
        return p, rel

    def read(self, app_id: str, rel: str) -> dict:
        p, rel = self._resolve(app_id, rel)
        if not p.is_file():
            raise AppError(f"no such file '{rel}'")
        data = p.read_bytes()
        if len(data) > MAX_READ:
            raise AppError(f"'{rel}' is larger than {MAX_READ // 1024 // 1024} MB: it is not shown here")
        if not _is_text(data):
            return dict(path=rel, size=len(data), binary=True, content=None)
        return dict(path=rel, size=len(data), binary=False, content=data.decode("utf-8"))

    def _quota(self, app_id: str, overlay: dict[str, bytes | None]) -> None:
        cfg = self.config()
        root = self.root(app_id)
        sizes = {f["path"]: f["size"] for f in self._walk(root)}
        for rel, data in overlay.items():
            if data is None:
                sizes.pop(rel, None)
            else:
                if len(data) > cfg["max_file_kb"] * 1024 and rel.split("/")[0] != "assets":
                    raise AppError(f"'{rel}' is larger than {cfg['max_file_kb']} KB")
                sizes[rel] = len(data)
        if len(sizes) > cfg["max_files"]:
            raise AppError(f"an app can hold at most {cfg['max_files']} files")
        if sum(sizes.values()) > cfg["max_total_mb"] * 1024 * 1024:
            raise AppError(f"an app can hold at most {cfg['max_total_mb']} MB")

    def _commit(self, app_id: str, overlay: dict[str, bytes | None]) -> None:
        root = self.root(app_id)
        for rel, data in overlay.items():
            p, _ = self._resolve(app_id, rel)
            if data is None:
                if p.is_file():
                    p.unlink()
                    d = p.parent
                    while d != root.resolve() and d.is_dir() and not any(d.iterdir()):
                        d.rmdir()
                        d = d.parent
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
        self.db.execute("UPDATE apps SET updated=? WHERE id=?", (time.time(), app_id))

    def write(self, app_id: str, rel: str, content: str, author: str = "user") -> dict:
        """Save a file you edited (your own typing is saved as is). `app.json` must stay a valid manifest."""
        self._on()
        rel = safe_rel(rel)
        if not isinstance(content, str):
            raise AppError("the content must be text")
        data = content.encode("utf-8")
        if rel == "app.json":
            self._check_manifest_text(content)
        self._quota(app_id, {rel: data})
        self._commit(app_id, {rel: data})
        self._emit(app_id, {"type": "files", "author": author})
        return self.read(app_id, rel)

    def delete_file(self, app_id: str, rel: str) -> dict:
        self._on()
        p, rel = self._resolve(app_id, rel)
        if rel == "app.json":
            raise AppError("app.json is the app's manifest: it cannot be deleted")
        if not p.is_file():
            raise AppError(f"no such file '{rel}'")
        self._commit(app_id, {rel: None})
        self._emit(app_id, {"type": "files", "author": "user"})
        return {"deleted": rel}

    def add_asset(self, app_id: str, name: str, data: bytes) -> dict:
        """Keep a file the user attached (a spec, sample data, an image) under assets/ so the builder and the app can use it."""
        self._on()
        base = re.sub(r"[^\w.-]+", "_", str(name or "").replace("\\", "/").split("/")[-1])[:80].strip("._") or "file"
        ext = Path(base).suffix.lower()
        if ext not in ASSET_EXT:
            raise AppError(f"attachments can be: {', '.join(sorted(ASSET_EXT))}")
        if len(data) > MAX_ASSET:
            raise AppError(f"an attachment can be at most {MAX_ASSET // 1024 // 1024} MB")
        rel = safe_rel(f"assets/{base}")
        self._quota(app_id, {rel: data})
        self._commit(app_id, {rel: data})
        self._emit(app_id, {"type": "files", "author": "user"})
        return dict(path=rel, size=len(data))

    def files_dict(self, app_id: str) -> dict[str, bytes]:
        root = self.root(app_id)
        return {f["path"]: (root / f["path"]).read_bytes() for f in self._walk(root)}

    # ---- manifest
    @staticmethod
    def _check_manifest_text(text: str) -> dict:
        try:
            return clean_manifest(json.loads(text))
        except (ValueError, ManifestError) as e:
            raise AppError(f"app.json is not a valid manifest: {e}")

    def set_manifest(self, app_id: str, patch: dict, author: str = "user") -> dict:
        self._on()
        if not isinstance(patch, dict):
            raise AppError("send an object")
        cur = self.manifest(app_id)
        merged = {**cur, **{k: v for k, v in patch.items() if k in cur}}
        if isinstance(patch.get("permissions"), dict):
            merged["permissions"] = {**cur["permissions"], **patch["permissions"]}
        try:
            new = clean_manifest(merged)
        except ManifestError as e:
            raise AppError(str(e))
        self._commit(app_id, {"app.json": (json.dumps(new, indent=2) + "\n").encode()})
        if new["name"] != cur["name"]:
            self.db.execute("UPDATE apps SET name=? WHERE id=?", (new["name"], app_id))
        self.db.execute("UPDATE apps SET description=? WHERE id=?", (new["description"], app_id))
        self._sync_key(app_id)
        self._emit(app_id, {"type": "files", "author": author})
        return new

    # ---- creating, renaming, deleting
    def create(self, name: str, template: str = "static-chat", description: str = "", agent_id: str = "", mode: str = "advanced", session_id: str = "") -> dict:
        cfg = self._on()
        if template not in TEMPLATES:
            raise AppError(f"template must be one of: {', '.join(TEMPLATES)}")
        if mode not in ("simple", "advanced"):
            raise AppError("mode must be simple or advanced")
        name = (name or "").strip()[:60] or "Untitled app"
        if self.db.one("SELECT COUNT(*) AS n FROM apps")["n"] >= cfg["max_apps"]:
            raise AppError(f"at most {cfg['max_apps']} apps: delete one you no longer use")
        aid, now = uuid.uuid4().hex[:12], time.time()
        slug, n = slugify(name), 2
        while self.db.one("SELECT id FROM apps WHERE slug=?", (slug,)):
            slug, n = f"{slugify(name)[:36]}-{n}", n + 1
        files = render(template, name, description, agent_id)
        self.db.execute("INSERT INTO apps(id,name,slug,description,template,mode,session_id,version,git,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (aid, name, slug, (description or "").strip()[:300], template, mode, session_id, 0, "{}", now, now))
        root = self.base / aid
        root.mkdir(parents=True, exist_ok=True)
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(text.encode("utf-8"))                # bytes: no newline translation on Windows
        self.snapshot(aid, "created from the template", "user")
        self._sync_key(aid)
        return self.get(aid)

    def update(self, app_id: str, name: str | None = None, description: str | None = None, mode: str | None = None, session_id: str | None = None) -> dict:
        self._on()
        r = self._row(app_id)
        if mode is not None and mode not in ("simple", "advanced"):
            raise AppError("mode must be simple or advanced")
        if name is not None or description is not None:
            patch = {}
            if name is not None:
                patch["name"] = name
            if description is not None:
                patch["description"] = description
            self.set_manifest(app_id, patch)
        self.db.execute("UPDATE apps SET mode=?, session_id=?, updated=? WHERE id=?",
                        (mode if mode is not None else r["mode"], session_id if session_id is not None else r["session_id"], time.time(), app_id))
        return self.get(app_id)

    def delete(self, app_id: str) -> dict:
        self._row(app_id)
        s = self._secrets()
        sec = s.pop(app_id, None)
        self.db.set_setting(SECRETS_KEY, json.dumps(s))
        if sec and sec.get("key_id") and self.keys is not None:
            try:
                self.keys.delete(sec["key_id"])
            except KeyError:
                pass
        self.forget(app_id)
        for t in ("app_proposals", "app_versions"):
            self.db.execute(f"DELETE FROM {t} WHERE app_id=?", (app_id,))
        self.db.execute("DELETE FROM apps WHERE id=?", (app_id,))
        remove_tree(self.base / app_id)
        self._emit(app_id, {"type": "deleted"})
        return {"deleted": True}

    def duplicate(self, app_id: str) -> dict:
        cfg = self._on()
        src = self._row(app_id)
        if self.db.one("SELECT COUNT(*) AS n FROM apps")["n"] >= cfg["max_apps"]:
            raise AppError(f"at most {cfg['max_apps']} apps: delete one you no longer use")
        aid, now = uuid.uuid4().hex[:12], time.time()
        name = f"{src['name']} copy"[:60]
        slug, n = slugify(name), 2
        while self.db.one("SELECT id FROM apps WHERE slug=?", (slug,)):
            slug, n = f"{slugify(name)[:36]}-{n}", n + 1
        self.db.execute("INSERT INTO apps(id,name,slug,description,template,mode,session_id,version,git,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (aid, name, slug, src["description"], src["template"], src["mode"], "", 0, "{}", now, now))
        files = self.files_dict(app_id)
        root = self.base / aid
        for rel, data in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        self.set_manifest(aid, {"name": name})
        self.snapshot(aid, f"copied from {src['name']}", "user")
        return self.get(aid)

    # ---- versions
    def snapshot(self, app_id: str, note: str = "", author: str = "user") -> int:
        self._row(app_id)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for rel, data in self.files_dict(app_id).items():
                z.writestr(rel, data)
        n = (self.db.one("SELECT MAX(version) AS v FROM app_versions WHERE app_id=?", (app_id,))["v"] or 0) + 1
        if not AUTHOR_RE.match(author):
            author = "user"
        self.db.execute("INSERT INTO app_versions(app_id,version,author,note,files,created) VALUES(?,?,?,?,?,?)", (app_id, n, author, (note or "")[:200], buf.getvalue(), time.time()))
        self.db.execute("DELETE FROM app_versions WHERE app_id=? AND version <= ?", (app_id, n - self.config()["keep_versions"]))
        self.db.execute("UPDATE apps SET version=?, updated=? WHERE id=?", (n, time.time(), app_id))
        self.new_files(app_id, author)
        self._emit(app_id, {"type": "version", "version": n})
        return n

    def versions(self, app_id: str) -> list[dict]:
        self._row(app_id)
        return [dict(version=r["version"], author=r["author"], note=r["note"], created=r["created"], size=r["size"])
                for r in self.db.query("SELECT version,author,note,created,LENGTH(files) AS size FROM app_versions WHERE app_id=? ORDER BY version DESC", (app_id,))]

    def version_files(self, app_id: str, n: int) -> dict[str, bytes]:
        self._row(app_id)
        r = self.db.one("SELECT files FROM app_versions WHERE app_id=? AND version=?", (app_id, n)) if isinstance(n, int) and not isinstance(n, bool) else None
        if r is None:
            raise AppError(f"version {n} is not kept (only the newest {self.config()['keep_versions']} are)")
        with zipfile.ZipFile(io.BytesIO(r["files"])) as z:
            return {i.filename: z.read(i) for i in z.infolist()}

    def diff(self, app_id: str, n: int) -> list[dict]:
        """What differs between version n and the files now: added / removed / changed, with the text of both sides for text files."""
        old, cur = self.version_files(app_id, n), self.files_dict(app_id)
        out = []
        for rel in sorted(set(old) | set(cur)):
            a, b = old.get(rel), cur.get(rel)
            if a == b:
                continue
            status = "added" if a is None else "removed" if b is None else "changed"
            ent = dict(path=rel, status=status)
            for k, v in (("old", a), ("new", b)):
                ent[k] = v.decode("utf-8") if v is not None and _is_text(v) and len(v) <= 200_000 else None
            out.append(ent)
        return out

    def restore(self, app_id: str, n: int) -> dict:
        self._on()
        old = self.version_files(app_id, n)
        cur = self.files_dict(app_id)
        overlay: dict[str, bytes | None] = {rel: None for rel in cur if rel not in old}
        overlay.update(old)
        self._quota(app_id, overlay)
        self._commit(app_id, overlay)
        v = self.snapshot(app_id, f"restored version {n} (was v{self._row(app_id)['version']})", "restore")
        self._sync_key(app_id)
        return dict(version=v, app=self.get(app_id))

    # ---- agent changes (proposals)
    def _clean_changes(self, raw: object) -> list[dict]:
        cfg = self.config()
        if not isinstance(raw, list) or not raw or len(raw) > MAX_CHANGES:
            raise AppError(f"send 1 to {MAX_CHANGES} changes")
        out = []
        for i, c in enumerate(raw, 1):
            if not isinstance(c, dict) or c.get("op") not in ("write", "patch", "delete"):
                raise AppError(f"change {i}: op must be write, patch or delete")
            ent = {"op": c["op"], "path": safe_rel(c.get("path"))}
            if c["op"] == "write":
                if not isinstance(c.get("content"), str) or len(c["content"].encode("utf-8")) > cfg["max_file_kb"] * 1024:
                    raise AppError(f"change {i}: 'content' must be text of at most {cfg['max_file_kb']} KB")
                ent["content"] = c["content"]
            elif c["op"] == "patch":
                try:
                    ent["edits"] = [e for e in clean_edits(c.get("edits"), cfg["max_file_kb"] * 1024)]
                except CanvasError as e:
                    raise AppError(f"change {i}: {e}")
            out.append(ent)
        return out

    def _plan(self, app_id: str, changes: list[dict], skip: bool = False) -> tuple[dict[str, bytes | None], list[int]]:
        """What the files would become: {path: new bytes or None for deleted}, and the numbers of changes that no longer fit (raises unless `skip`)."""
        root = self.root(app_id)
        overlay: dict[str, bytes | None] = {}
        bad: list[int] = []
        for i, c in enumerate(changes, 1):
            rel = c["path"]
            cur = overlay[rel] if rel in overlay else ((root / rel).read_bytes() if (root / rel).is_file() else None)
            try:
                if c["op"] == "write":
                    new = c["content"].encode("utf-8")
                elif c["op"] == "delete":
                    if cur is None:
                        raise AppError(f"change {i}: '{rel}' does not exist")
                    if rel == "app.json":
                        raise AppError("app.json is the app's manifest: it cannot be deleted")
                    new = None
                else:
                    if cur is None or not _is_text(cur):
                        raise AppError(f"change {i}: '{rel}' is not an existing text file")
                    try:
                        text, _ = apply_edits(cur.decode("utf-8"), c["edits"])
                    except CanvasError as e:
                        raise AppError(f"change {i} ({rel}): {e}")
                    new = text.encode("utf-8")
                if new is not None:
                    text_new = new.decode("utf-8", "replace")
                    if scan_text(text_new)[1]:
                        raise AppError(f"change {i} ({rel}): the text looks like it contains a secret (a key or password). Keep secrets out of files: list the name in the manifest's env instead")
                    if rel == "app.json":
                        self._check_manifest_text(text_new)
                overlay[rel] = new
            except AppError:
                if not skip:
                    raise
                bad.append(i)
        self._quota(app_id, overlay)
        return overlay, bad

    def _public_proposal(self, r) -> dict:
        root = self.base / r["app_id"]
        out = []
        for c in json.loads(r["changes"]):
            p = root / c["path"]
            old = p.read_text(encoding="utf-8", errors="replace") if p.is_file() and r["status"] == "pending" else None
            new, fits = c.get("content"), True
            if c["op"] == "patch":
                try:
                    new, _ = apply_edits(old or "", c["edits"]) if old is not None else (None, [])
                    fits = old is not None
                except CanvasError:
                    new, fits = None, False
            out.append({**{k: v for k, v in c.items() if k != "content"}, "old": old, "new": new, "fits": fits and (c["op"] != "delete" or old is not None)})
        return dict(id=r["id"], app_id=r["app_id"], author=r["author"], note=r["note"], status=r["status"], created=r["created"], resolved=r["resolved"], changes=out)

    def propose(self, app_id: str, changes: list, author: str = "user", note: str = "", apply: bool = False, session_id: str = "") -> dict:
        """Record changes an agent wants to make. With apply=True (simple mode, or the setting) they are applied now and kept as a version."""
        self._on()
        r = self._row(app_id)
        clean = self._clean_changes(changes)
        if not AUTHOR_RE.match(author):
            author = "user"
        if session_id and r["session_id"] != session_id:
            self.db.execute("UPDATE apps SET session_id=? WHERE id=?", (session_id, app_id))
        self._plan(app_id, clean)                                     # refuse changes that cannot apply at all, before anything is recorded
        pid = uuid.uuid4().hex[:12]
        if apply:
            overlay, _ = self._plan(app_id, clean)
            self._commit(app_id, overlay)
            v = self.snapshot(app_id, note or "agent changes", author)
            self._sync_key(app_id)
            self.db.execute("INSERT INTO app_proposals(id,app_id,author,note,changes,status,created,resolved) VALUES(?,?,?,?,?,?,?,?)",
                            (pid, app_id, author, note[:200], json.dumps(clean), "accepted", time.time(), time.time()))
            self._emit(app_id, {"type": "applied", "proposal": pid, "version": v, "author": author})
            return dict(proposal_id=pid, status="applied", version=v)
        if self._pending_count(app_id) >= MAX_PENDING:
            raise AppError(f"{MAX_PENDING} changes are already waiting for review: accept or reject some first")
        self.db.execute("INSERT INTO app_proposals(id,app_id,author,note,changes,status,created,resolved) VALUES(?,?,?,?,?,?,?,?)",
                        (pid, app_id, author, note[:200], json.dumps(clean), "pending", time.time(), 0))
        self._emit(app_id, {"type": "proposed", "proposal": pid, "pending": self._pending_count(app_id), "author": author})
        return dict(proposal_id=pid, status="pending", version=r["version"])

    def proposals(self, app_id: str, status: str = "pending") -> list[dict]:
        self._row(app_id)
        rows = self.db.query("SELECT * FROM app_proposals WHERE app_id=?" + ("" if status == "all" else " AND status=?") + " ORDER BY created", [app_id] + ([] if status == "all" else [status]))
        return [self._public_proposal(r) for r in rows]

    def _proposal(self, app_id: str, pid: str):
        r = self.db.one("SELECT * FROM app_proposals WHERE id=? AND app_id=?", (pid, app_id))
        if r is None:
            raise AppError(f"no such change '{pid}'")
        if r["status"] != "pending":
            raise AppError("that change was already handled")
        return r

    def accept(self, app_id: str, pid: str) -> dict:
        self._on()
        r = self._proposal(app_id, pid)
        try:
            overlay, _ = self._plan(app_id, json.loads(r["changes"]))
        except AppError as e:
            self.db.execute("UPDATE app_proposals SET status='stale', resolved=? WHERE id=?", (time.time(), pid))
            raise AppError(f"this change no longer fits the files: {e}")
        self._commit(app_id, overlay)
        v = self.snapshot(app_id, r["note"] or "accepted agent changes", r["author"])
        self._sync_key(app_id)
        self.db.execute("UPDATE app_proposals SET status='accepted', resolved=? WHERE id=?", (time.time(), pid))
        self._emit(app_id, {"type": "applied", "proposal": pid, "version": v, "author": r["author"]})
        return dict(version=v, app=self.get(app_id))

    def reject(self, app_id: str, pid: str) -> dict:
        self._proposal(app_id, pid)
        self.db.execute("UPDATE app_proposals SET status='rejected', resolved=? WHERE id=?", (time.time(), pid))
        self._emit(app_id, {"type": "rejected", "proposal": pid, "pending": self._pending_count(app_id)})
        return dict(rejected=True)

    def accept_all(self, app_id: str) -> dict:
        done, stale = 0, []
        for r in self.db.query("SELECT id FROM app_proposals WHERE app_id=? AND status='pending' ORDER BY created", (app_id,)):
            try:
                self.accept(app_id, r["id"])
                done += 1
            except AppError as e:
                stale.append({"id": r["id"], "reason": str(e)})
        return dict(accepted=done, stale=stale)

    def reject_all(self, app_id: str) -> dict:
        n = self._pending_count(app_id)
        self.db.execute("UPDATE app_proposals SET status='rejected', resolved=? WHERE app_id=? AND status='pending'", (time.time(), app_id))
        self._emit(app_id, {"type": "rejected", "pending": 0})
        return dict(rejected=n)

    # ---- the app's own API key and its environment values (kept in the settings store like other write-only secrets)
    def _secrets(self) -> dict:
        try:
            s = json.loads(self.db.get_setting(SECRETS_KEY, "{}") or "{}")
        except ValueError:
            return {}
        return s if isinstance(s, dict) else {}

    def _save_secret(self, app_id: str, patch: dict) -> None:
        s = self._secrets()
        s[app_id] = {**s.get(app_id, {}), **patch}
        self.db.set_setting(SECRETS_KEY, json.dumps(s))

    def _agent_ids(self, app_id: str) -> list[str]:
        try:
            return [a["id"] for a in self.manifest(app_id)["agents"]]
        except AppError:
            return []

    def _sync_key(self, app_id: str) -> None:
        """Keep the key's agent list equal to the manifest's (never 'all agents': with no agents declared the key is not used)."""
        sec = self._secrets().get(app_id, {})
        kid, ids = sec.get("key_id"), self._agent_ids(app_id)
        if self.keys is None or not kid or not ids:
            return
        row = self.db.one("SELECT agents FROM api_keys WHERE id=?", (kid,))
        if row is not None and set(json.loads(row["agents"])) != set(ids):
            self.keys.update(kid, {"agents": ids})

    def key_info(self, app_id: str) -> dict:
        self._row(app_id)
        sec = self._secrets().get(app_id, {})
        row = self.db.one("SELECT * FROM api_keys WHERE id=?", (sec.get("key_id", ""),)) if sec.get("key_id") else None
        ids = self._agent_ids(app_id)
        if row is None:
            return dict(exists=False, agents=ids, note="The key is created the first time the app runs (it needs at least one agent in the manifest)." if ids else "Add an agent to the manifest first: an app with no agents gets no key.")
        pub = self.keys._public(row)
        return dict(exists=True, id=pub["id"], prefix=pub["prefix"], agents=pub["agents"], revoked=pub["revoked"], uses=pub["uses"], last_used=pub["last_used"], rate_per_min=pub["rate_per_min"])

    def ensure_key(self, app_id: str) -> str | None:
        """The app's API key (created on first use), or None when the manifest lists no agents. A revoked key stays revoked until you rotate it."""
        ids = self._agent_ids(app_id)
        if self.keys is None or not ids:
            return None
        sec = self._secrets().get(app_id, {})
        kid = sec.get("key_id")
        row = self.db.one("SELECT revoked FROM api_keys WHERE id=?", (kid,)) if kid else None
        if row is not None and sec.get("key"):
            if row["revoked"]:
                raise AppError("this app's key is revoked: rotate it to make a new one")
            self._sync_key(app_id)
            return sec["key"]
        rec = self.keys.create(f"app:{self._row(app_id)['slug']}"[:60], ids, rate_per_min=self.config()["key_rate_per_min"])
        self._save_secret(app_id, {"key_id": rec["id"], "key": rec["key"]})
        return rec["key"]

    def rotate_key(self, app_id: str) -> dict:
        """A new secret for the app's key, shown once (for using the app outside Agent Studio). The old secret stops working."""
        self._on()
        if not self._agent_ids(app_id):
            raise AppError("add an agent to the manifest first: an app with no agents gets no key")
        kid = self._secrets().get(app_id, {}).get("key_id")
        if not (kid and self.db.one("SELECT id FROM api_keys WHERE id=?", (kid,))):
            self.ensure_key(app_id)
            kid = self._secrets()[app_id]["key_id"]
        rec = self.keys.rotate(kid)
        self._save_secret(app_id, {"key": rec["key"]})
        return dict(id=rec["id"], prefix=rec["prefix"], key=rec["key"], agents=rec["agents"])

    def revoke_key(self, app_id: str) -> dict:
        self._row(app_id)
        kid = self._secrets().get(app_id, {}).get("key_id")
        if not kid:
            raise AppError("this app has no key yet")
        self.keys.update(kid, {"revoked": True})
        return self.key_info(app_id)

    def env_status(self, app_id: str) -> list[dict]:
        vals = self._secrets().get(app_id, {}).get("env", {})
        return [dict(name=e["name"], description=e["description"], required=e["required"], set=bool(vals.get(e["name"]))) for e in self.manifest(app_id)["env"]]

    def set_env(self, app_id: str, values: dict) -> list[dict]:
        """Save environment values (write-only). Only names listed in the manifest are accepted; an empty value clears one."""
        self._on()
        if not isinstance(values, dict):
            raise AppError("send {NAME: value}")
        names = {e["name"] for e in self.manifest(app_id)["env"]}
        cur = dict(self._secrets().get(app_id, {}).get("env", {}))
        for k, v in values.items():
            if k not in names:
                raise AppError(f"'{k}' is not listed in the manifest's env")
            if not isinstance(v, str) or len(v) > 4000:
                raise AppError(f"the value of {k} must be text of at most 4000 characters")
            if v:
                cur[k] = v
            else:
                cur.pop(k, None)
        self._save_secret(app_id, {"env": cur})
        return self.env_status(app_id)

    def env_values(self, app_id: str) -> dict[str, str]:
        """For the runner only: the values to put in the app's environment."""
        names = {e["name"] for e in self.manifest(app_id)["env"]}
        return {k: v for k, v in self._secrets().get(app_id, {}).get("env", {}).items() if k in names}

    # ---- checks
    def check(self, app_id: str) -> dict:
        """Look the app over: the manifest, the files it needs, anything that loads code from another website or looks like a secret, and whether the agents it calls can be called."""
        problems: list[dict] = []

        def add(level: str, text: str) -> None:
            problems.append(dict(level=level, text=text))
        try:
            m = self.manifest(app_id)
        except AppError as e:
            return dict(ok=False, problems=[dict(level="error", text=str(e))], agents=[])
        files = self.files_dict(app_id)
        if m["entry"] not in files:
            add("error", f"the entry file '{m['entry']}' does not exist")
        if "public/index.html" not in files:
            add("warn", "there is no public/index.html, so the preview has nothing to show")
        remote = re.compile(r"""(<script[^>]+src=["']|<link[^>]+href=["']|@import\s+url\(["']?|from\s+["'])https?://""", re.I)
        for rel, data in files.items():
            if not _is_text(data):
                continue
            text = data.decode("utf-8")
            if scan_text(text)[1]:
                add("error", f"{rel}: looks like it contains a secret. Remove it and list the name in the manifest's env")
            if rel.startswith("public/") and rel.endswith((".html", ".js", ".css")) and remote.search(text):
                add("warn", f"{rel}: loads code or styles from another website. The preview blocks that: bundle it into the app instead")
        agents = []
        for a in m["agents"]:
            ag = self.agents.get(a["id"]) if self.agents is not None else None
            agents.append(dict(id=a["id"], exists=ag is not None, api_access=bool(ag and ag.api_access)))
            if ag is None:
                add("error", f"the agent '{a['id']}' does not exist")
            elif not ag.api_access:
                add("warn", f"the agent '{a['id']}' has API access switched off, so the app cannot call it: turn it on for that agent")
        if m["type"] != "static" and not self.config()["allow_server_apps"]:
            add("info", "server apps run in a container on an open network: allow that in Settings > Apps to run this one")
        return dict(ok=not any(p["level"] == "error" for p in problems), problems=problems, agents=agents)

    # ---- counts (for the AI-BOM: never names or content)
    def counts(self) -> dict:
        by_type: dict[str, int] = {}
        agents: set[str] = set()
        for a in self.list():
            by_type[a["type"]] = by_type.get(a["type"], 0) + 1
            agents.update(a["agents"])
        return dict(apps=len(self.list()), by_type=by_type, agents_called=len(agents), pending=self.db.one("SELECT COUNT(*) AS n FROM app_proposals WHERE status='pending'")["n"])
