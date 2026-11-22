"""Artifacts: the files and outputs agents produce, kept in one or more storages and searchable on the Artifacts page.

How it works
* While an agent runs, nothing is slowed down. When the turn ends the workspace is compared with how it looked before: new files
  (or, if you choose, changed ones) become artifacts. An agent can also save one on purpose with the `save_artifact` tool.
* The file is hashed, classified and searched-indexed (the start of a text file, with secret-looking values removed).
* A rule (policy) names the storages that get a copy, by artifact type. The folder on this PC is always available. Copies to the
  local folder are made at once; copies to cloud storages go through a background worker with retries, staged on disk meanwhile.
* Before a copy leaves this PC a secret scan runs: secrets are removed from text (mode "redact"), or the copy is blocked ("block").
  The copy on this PC is never altered. Credentials live in a separate write-only setting and never appear in an error.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
import threading
import time
import uuid
from pathlib import Path

from ..aibom.redact import SECRET_VALUE
from ..db import data_dir
from ..harness.tools import SKIP_DIRS
from .storage import KINDS, ArtifactStorage, StorageError, build_storage
from .types import TEXT_PREVIEWS, TYPES, classify, looks_binary, safe_name

CONFIG_KEY, SECRETS_KEY = "artifact_config", "artifact_secrets"
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")
CAPTURE_MODES = {"off": "Do not collect files automatically", "new": "New files only (recommended)", "new_and_changed": "New and changed files"}
SCAN_MODES = {"redact": "Remove secrets from text before it leaves this PC", "block": "Do not send files that contain a secret", "off": "No secret scan"}
DEFAULT_IGNORE = [".git/**", "node_modules/**", ".venv/**", "venv/**", "__pycache__/**", ".next/**", "dist/**", "build/**", ".pytest_cache/**", ".ruff_cache/**", ".idea/**",
                  ".vscode/**", "*.pyc", "*.lock", "package-lock.json", ".DS_Store", "*.tmp", "*.swp", "AGENTS.md"]
LOCAL = {"id": "local", "kind": "local", "label": "This PC", "enabled": True, "config": {}}
DEFAULT_CONFIG: dict = {"enabled": True, "capture": "new", "max_file_mb": 25, "max_per_turn": 50, "secret_scan": "redact", "retention_days": 0,
                        "ignore": DEFAULT_IGNORE, "storages": [LOCAL], "policy": [], "default_storages": ["local"]}
MAX_SCAN_FILES = 20_000
SEARCH_CHARS = 200_000
SCAN_BYTES = 2_000_000                                      # how much of a file is scanned to flag and index it (the copy that leaves this PC is scanned whole)
BINARY_TYPES = ("image", "audio", "video", "archive", "model")
SUMMARY_CHARS = 300
PREVIEW_BYTES = 200_000
SORTS = {"created": "created", "name": "name COLLATE NOCASE", "size": "size", "type": "type"}


class ArtifactError(ValueError):
    """Something the artifact store cannot accept or find. Safe to show."""


def _int(v, lo: int, hi: int, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or not lo <= v <= hi:
        raise ArtifactError(f"{what} must be a whole number between {lo} and {hi}")
    return int(v)


def ignored(rel: str, patterns: list[str]) -> bool:
    """Does a workspace-relative path match one of the ignore patterns? 'dir/**' ignores a folder anywhere; other patterns match the name or the path."""
    parts = rel.split("/")
    for pat in patterns:
        if pat.endswith("/**"):
            if pat[:-3] in parts[:-1]:
                return True
        elif fnmatch.fnmatch(parts[-1], pat) or fnmatch.fnmatch(rel, pat):
            return True
    return False


def _is_text(data: bytes, kind: str) -> bool:
    return kind not in BINARY_TYPES and not looks_binary(data[:8192])


def scan_text(text: str) -> tuple[str, bool]:
    """(the text with secret-looking values replaced, whether any were found)."""
    clean, n = SECRET_VALUE.subn("[secret removed]", text)
    return clean, n > 0


class ArtifactService:
    def __init__(self, db, home: Path | None = None, start_worker: bool = True, storage_factory=build_storage):
        self.db = db
        self.home = Path(home) if home else data_dir()
        self.stage = self.home / "artifacts-staging"
        self.storage_factory = storage_factory
        self._start_worker = start_worker
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_purge = 0.0

    # ------------------------------------------------------------------ settings
    def config(self) -> dict:
        try:
            raw = json.loads(self.db.get_setting(CONFIG_KEY, "{}") or "{}")
        except ValueError:
            raw = {}
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        if isinstance(raw, dict):
            cfg.update({k: v for k, v in raw.items() if k in cfg})
        if not any(s.get("id") == "local" for s in cfg["storages"]):
            cfg["storages"].insert(0, dict(LOCAL))
        return cfg

    def _secrets(self) -> dict:
        try:
            s = json.loads(self.db.get_setting(SECRETS_KEY, "{}") or "{}")
        except ValueError:
            return {}
        return s if isinstance(s, dict) else {}

    def set_config(self, patch: dict) -> dict:
        cfg = self.config()
        patch = dict(patch or {})
        secrets = patch.pop("secrets", None)
        for k, v in patch.items():
            if v is None or k not in DEFAULT_CONFIG:
                continue
            if k == "enabled":
                if not isinstance(v, bool):
                    raise ArtifactError("enabled must be true or false")
            elif k == "capture":
                if v not in CAPTURE_MODES:
                    raise ArtifactError(f"capture must be one of: {', '.join(CAPTURE_MODES)}")
            elif k == "secret_scan":
                if v not in SCAN_MODES:
                    raise ArtifactError(f"secret_scan must be one of: {', '.join(SCAN_MODES)}")
            elif k == "max_file_mb":
                v = _int(v, 1, 500, "the largest file size (MB)")
            elif k == "max_per_turn":
                v = _int(v, 1, 500, "files collected per turn")
            elif k == "retention_days":
                v = _int(v, 0, 36500, "retention (days)")
            elif k == "ignore":
                if not isinstance(v, list) or len(v) > 100 or any(not isinstance(x, str) or not 0 < len(x) <= 120 for x in v):
                    raise ArtifactError("ignore must be a list of up to 100 patterns, each up to 120 characters")
            elif k == "storages":
                v = self._clean_storages(v, secrets)
            cfg[k] = v
        ids = {s["id"] for s in cfg["storages"]}
        if "policy" in patch:
            cfg["policy"] = self._clean_policy(cfg["policy"], ids)
        elif "storages" in patch:                                                    # a storage was removed: the rules that named it stop naming it
            cfg["policy"] = [{**r, "storages": [x for x in r["storages"] if x in ids]} for r in cfg["policy"]]
            cfg["policy"] = [r for r in cfg["policy"] if r["storages"]]
        if "default_storages" in patch:
            cfg["default_storages"] = self._clean_targets(cfg["default_storages"], ids, "the default storages")
        elif "storages" in patch:
            cfg["default_storages"] = [x for x in cfg["default_storages"] if x in ids] or ["local"]
        if secrets is not None:
            if not isinstance(secrets, dict) or any(not isinstance(a, dict) or any(not isinstance(x, str) for x in a.values()) for a in secrets.values()):
                raise ArtifactError("secrets must look like {storage id: {name: value}}")
            stored = self._secrets()
            merged = {sid: {a: b for a, b in {**stored.get(sid, {}), **vals}.items() if b} for sid, vals in {**{k: {} for k in stored}, **secrets}.items() if sid in ids}
            self.db.set_setting(SECRETS_KEY, json.dumps({k: v for k, v in merged.items() if v}))
        elif "storages" in patch:                                               # a removed storage takes its credentials with it
            self.db.set_setting(SECRETS_KEY, json.dumps({k: v for k, v in self._secrets().items() if k in ids}))
        self.db.set_setting(CONFIG_KEY, json.dumps(cfg))
        return self.public_config()

    def _clean_storages(self, storages, secrets) -> list[dict]:
        if not isinstance(storages, list) or len(storages) > 10:
            raise ArtifactError("storages must be a list of at most 10 places")
        out, seen, stored = [], set(), self._secrets()
        for s in storages:
            if not isinstance(s, dict) or not ID_RE.match(str(s.get("id", ""))):
                raise ArtifactError("each storage needs an id: lowercase letters, digits, - and _ (up to 31 characters, starting with a letter)")
            if s["id"] in seen:
                raise ArtifactError(f"two storages are called '{s['id']}'")
            seen.add(s["id"])
            kind = s.get("kind")
            if kind not in KINDS:
                raise ArtifactError(f"storage '{s['id']}': kind must be one of: {', '.join(KINDS)}")
            if s["id"] == "local" and kind != "local":
                raise ArtifactError("the storage called 'local' is the folder on this PC")
            conf = s.get("config") if isinstance(s.get("config"), dict) else {}
            clean = {k: v for k, v in conf.items() if k in {f[0] for f in KINDS[kind]["fields"] if not f[2]} and isinstance(v, (str, int, bool))}
            try:
                self.storage_factory(kind, clean, {**stored.get(s["id"], {}), **((secrets or {}).get(s["id"]) or {})}, self.home)
            except StorageError as e:
                raise ArtifactError(f"storage '{s['id']}': {e}") from e
            out.append({"id": s["id"], "kind": kind, "label": str(s.get("label") or KINDS[kind]["label"])[:60], "enabled": True if s["id"] == "local" else bool(s.get("enabled", True)), "config": clean})
        if "local" not in seen:
            out.insert(0, dict(LOCAL))
        return out

    @staticmethod
    def _clean_targets(v, ids: set[str], what: str) -> list[str]:
        if not isinstance(v, list) or not v or any(x not in ids for x in v):
            raise ArtifactError(f"{what} must name one or more storages that exist ({', '.join(sorted(ids))})")
        return list(dict.fromkeys(v))

    def _clean_policy(self, rules, ids: set[str]) -> list[dict]:
        if not isinstance(rules, list) or len(rules) > 20:
            raise ArtifactError("rules must be a list of at most 20")
        out = []
        for r in rules:
            if not isinstance(r, dict) or not isinstance(r.get("types"), list) or not r["types"] or any(t not in TYPES for t in r["types"]):
                raise ArtifactError(f"each rule needs types, chosen from: {', '.join(TYPES)}")
            mx = r.get("max_mb")
            if mx not in (None, 0):
                _int(mx, 1, 500, "a rule's largest file size (MB)")
            out.append({"types": list(dict.fromkeys(r["types"])), "max_mb": mx or 0, "storages": self._clean_targets(r.get("storages"), ids, "a rule's storages")})
        return out

    def public_config(self) -> dict:
        cfg, secrets = self.config(), self._secrets()
        storages = [{**s, "secrets_set": sorted(secrets.get(s["id"], {})), "where": self._describe(s), "cloud": KINDS[s["kind"]]["cloud"], "label_default": KINDS[s["kind"]]["label"]} for s in cfg["storages"]]
        return {**cfg, "storages": storages, "capture_modes": CAPTURE_MODES, "scan_modes": SCAN_MODES, "types": list(TYPES), "default_ignore": DEFAULT_IGNORE,
                "kinds": [{"id": k, "label": v["label"], "note": v["note"], "cloud": v["cloud"],
                           "fields": [{"name": f[0], "label": f[1], "secret": f[2], "required": f[3], "placeholder": f[4]} for f in v["fields"]]} for k, v in KINDS.items()]}

    def _describe(self, s: dict) -> str:
        try:
            return self._storage(s["id"], cfg_storage=s).where()
        except StorageError as e:
            return f"not usable: {e}"

    def _storage(self, sid: str, cfg_storage: dict | None = None) -> ArtifactStorage:
        s = cfg_storage or next((x for x in self.config()["storages"] if x["id"] == sid), None)
        if s is None:
            raise StorageError(f"the storage '{sid}' was removed")
        return self.storage_factory(s["kind"], s["config"], self._secrets().get(sid, {}), self.home)

    def targets_for(self, kind: str, size: int, cfg: dict | None = None) -> list[str]:
        """The storages that should hold an artifact of this type and size (enabled ones only; at least the folder on this PC)."""
        cfg = cfg or self.config()
        on = {s["id"] for s in cfg["storages"] if s["enabled"]}
        for rule in cfg["policy"]:
            if kind in rule["types"] and (not rule["max_mb"] or size <= rule["max_mb"] * 1024 * 1024):
                chosen = [x for x in rule["storages"] if x in on]
                if chosen:
                    return chosen
        return [x for x in cfg["default_storages"] if x in on] or ["local"]

    # ------------------------------------------------------------------ finding what a turn produced
    def snapshot(self, ws: Path, cfg: dict | None = None) -> dict[str, tuple[int, int]] | None:
        """{relative path: (mtime_ns, size)} for the workspace; None when collecting is off."""
        cfg = cfg or self.config()
        if not cfg["enabled"] or cfg["capture"] == "off":
            return None
        out: dict[str, tuple[int, int]] = {}
        root = str(ws)
        for base, dirs, files in os.walk(root):
            rel_base = os.path.relpath(base, root).replace("\\", "/")
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not ignored(f"{'' if rel_base == '.' else rel_base + '/'}{d}/x", cfg["ignore"])]
            for f in files:
                rel = f if rel_base == "." else f"{rel_base}/{f}"
                if ignored(rel, cfg["ignore"]):
                    continue
                try:
                    st = os.lstat(os.path.join(base, f))
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):               # symlinks and special files are not collected
                    continue
                out[rel] = (st.st_mtime_ns, st.st_size)
                if len(out) >= MAX_SCAN_FILES:
                    return out
        return out

    def capture_changes(self, before: dict | None, ws: Path, *, agent: str, session_id: str, trace_id: str) -> list[dict]:
        """Turn the files that appeared (or changed) during a turn into artifacts. Never raises: a problem here must not break a chat."""
        if before is None:
            return []
        try:
            cfg = self.config()
            after = self.snapshot(ws, cfg) or {}
            changed = [p for p, sig in after.items() if p not in before or (cfg["capture"] == "new_and_changed" and before[p] != sig)]
            changed.sort(key=lambda p: after[p][0])
            out = []
            for rel in changed:
                if len(out) >= cfg["max_per_turn"]:
                    break
                try:
                    art = self.capture_file(ws / rel, rel, agent=agent, session_id=session_id, trace_id=trace_id, workspace=str(ws), source="auto", cfg=cfg)
                except ArtifactError:
                    continue                                                  # too large, unreadable: skipped quietly
                if art and not art.get("existing"):
                    out.append(art)
            self.maybe_purge(cfg)
            return out
        except Exception:  # noqa: BLE001 - collecting artifacts is a bonus; the chat result must survive anything
            return []

    # ------------------------------------------------------------------ making an artifact
    def capture_file(self, path: Path, rel: str, *, agent: str = "", session_id: str = "", trace_id: str = "", workspace: str = "", source: str = "tool",
                     tags: list[str] | None = None, name: str | None = None, cfg: dict | None = None) -> dict | None:
        cfg = cfg or self.config()
        if not cfg["enabled"]:
            raise ArtifactError("artifacts are switched off in Settings")
        try:
            size = path.stat().st_size
        except OSError as e:
            raise ArtifactError(f"cannot read {rel}: {type(e).__name__}") from e
        if size > cfg["max_file_mb"] * 1024 * 1024:
            raise ArtifactError(f"{rel} is {size // (1024 * 1024)} MB, more than the {cfg['max_file_mb']} MB limit")
        try:
            data = path.read_bytes()
        except OSError as e:
            raise ArtifactError(f"cannot read {rel}: {type(e).__name__}") from e
        return self._store(data, name or safe_name(rel), rel, agent=agent, session_id=session_id, trace_id=trace_id, workspace=workspace, source=source, tags=tags or [], cfg=cfg)

    def save_text(self, name: str, content: str, *, agent: str = "", session_id: str = "", trace_id: str = "", workspace: str = "", source: str = "tool", tags: list[str] | None = None,
                  path: str = "") -> dict:
        cfg = self.config()
        if not cfg["enabled"]:
            raise ArtifactError("artifacts are switched off in Settings")
        data = (content or "").encode("utf-8")
        if not data:
            raise ArtifactError("there is nothing to save: the content is empty")
        if len(data) > cfg["max_file_mb"] * 1024 * 1024:
            raise ArtifactError(f"the content is larger than the {cfg['max_file_mb']} MB limit")
        return self._store(data, safe_name(name), path, agent=agent, session_id=session_id, trace_id=trace_id, workspace=workspace, source=source, tags=tags or [], cfg=cfg)   # `path` makes repeated saves versions of one file

    def _store(self, data: bytes, name: str, rel: str, *, agent: str, session_id: str, trace_id: str, workspace: str, source: str, tags: list[str], cfg: dict) -> dict:
        sha = hashlib.sha256(data).hexdigest()
        if rel:
            same = self.db.one("SELECT id FROM artifacts WHERE workspace=? AND path=? AND sha256=?", (workspace, rel, sha))
            if same:
                return {**self.get(same["id"]), "existing": True}
        kind, mime, preview = classify(name)
        is_text = _is_text(data, kind)
        text = data[:SCAN_BYTES].decode("utf-8", errors="replace") if is_text else ""
        clean, has_secret = scan_text(text) if text else ("", False)
        if is_text and preview == "none":
            preview = "text"
        version = 1 + (self.db.one("SELECT MAX(version) AS v FROM artifacts WHERE workspace=? AND path=?", (workspace, rel))["v"] or 0 if rel else 0)
        aid = uuid.uuid4().hex[:12]
        tags = [str(t).strip()[:40] for t in tags if str(t).strip()][:12]
        self.db.execute("INSERT INTO artifacts(id,name,path,type,mime,preview,size,sha256,version,created,agent,session_id,trace_id,workspace,source,tags,summary,search_text,secrets) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (aid, name, rel, kind, mime, preview, len(data), sha, version, time.time(), agent, session_id, trace_id, workspace, source, json.dumps(tags),
                         re.sub(r"\s+", " ", clean).strip()[:SUMMARY_CHARS], clean[:SEARCH_CHARS], int(has_secret)))
        self.stage.mkdir(parents=True, exist_ok=True)
        (self.stage / aid).write_bytes(data)
        for sid in self.targets_for(kind, len(data), cfg):
            self.db.execute("INSERT INTO artifact_copies(artifact_id,storage_id,key,status,detail,updated) VALUES(?,?,?,?,?,?)", (aid, sid, f"{aid}/{name}", "pending", "", time.time()))
        self.sync(aid, only_local=True)
        if self._pending_remote(aid) and self._start_worker:
            self.kick()
        return self.get(aid)

    # ------------------------------------------------------------------ copying to the storages
    def _pending_remote(self, aid: str | None = None) -> bool:
        sql = "SELECT 1 FROM artifact_copies WHERE status='pending'" + (" AND artifact_id=?" if aid else "") + " LIMIT 1"
        return self.db.one(sql, (aid,) if aid else ()) is not None

    def sync(self, aid: str, only_local: bool = False, retry_failed: bool = False) -> None:
        """Copy an artifact to every storage that still needs it. Failures are recorded on the copy, never raised."""
        row = self.db.one("SELECT * FROM artifacts WHERE id=?", (aid,))
        if row is None:
            return
        cfg = self.config()
        data = self._bytes_for_sync(aid)
        for c in self.db.query("SELECT * FROM artifact_copies WHERE artifact_id=?", (aid,)):
            if c["status"] == "synced" or (c["status"] == "failed" and not retry_failed) or c["status"] == "blocked" and not retry_failed:
                continue
            st = next((s for s in cfg["storages"] if s["id"] == c["storage_id"]), None)
            if st is None or not st["enabled"]:
                self._copy(aid, c["storage_id"], "failed", "this storage is switched off or was removed")
                continue
            if only_local and KINDS[st["kind"]]["cloud"]:
                continue
            if data is None:
                self._copy(aid, c["storage_id"], "failed", "the file is no longer available to copy")
                continue
            self._send_copy(row, c, st, data, cfg)
        self._unstage(aid)

    def _send_copy(self, row, c, st: dict, data: bytes, cfg: dict) -> None:
        payload, detail = data, ""
        if KINDS[st["kind"]]["cloud"] and cfg["secret_scan"] != "off" and _is_text(data, row["type"]):
            text, found = scan_text(data.decode("utf-8", errors="replace"))
            if found and cfg["secret_scan"] == "block":
                self._copy(row["id"], c["storage_id"], "blocked", "it contains a secret-looking value, so it was not sent (Settings > Artifacts > Secret scan)")
                return
            if found:
                payload, detail = text.encode("utf-8"), "secret-looking values were removed from this copy"
        try:
            self._storage(c["storage_id"], cfg_storage=st).put(c["key"], payload, row["mime"])
        except StorageError as e:
            self._copy(row["id"], c["storage_id"], "failed", str(e))
            return
        self._copy(row["id"], c["storage_id"], "synced", detail, len(payload), hashlib.sha256(payload).hexdigest())

    def _copy(self, aid: str, sid: str, status: str, detail: str = "", size: int = 0, sha: str = "") -> None:
        self.db.execute("UPDATE artifact_copies SET status=?, detail=?, size=?, sha256=?, updated=? WHERE artifact_id=? AND storage_id=?", (status, detail[:300], size, sha, time.time(), aid, sid))

    def _bytes_for_sync(self, aid: str) -> bytes | None:
        f = self.stage / aid
        if f.is_file():
            return f.read_bytes()
        try:
            return self.read_bytes(aid)[0]
        except ArtifactError:
            return None

    def _unstage(self, aid: str) -> None:
        """The staged bytes are only needed until every copy has landed (or until a failed one is retried)."""
        left = self.db.one("SELECT 1 FROM artifact_copies WHERE artifact_id=? AND status IN ('pending','failed') LIMIT 1", (aid,))
        has = self.db.one("SELECT 1 FROM artifact_copies WHERE artifact_id=? AND status='synced' LIMIT 1", (aid,))
        if has and not left:
            (self.stage / aid).unlink(missing_ok=True)

    def sync_pending(self) -> int:
        """Send everything waiting (the worker's job; tests and shutdown call it directly). Returns how many artifacts were handled."""
        ids = [r["artifact_id"] for r in self.db.query("SELECT DISTINCT artifact_id FROM artifact_copies WHERE status='pending'")]
        for aid in ids:
            self.sync(aid)
        return len(ids)

    def kick(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="artifact-sync", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        try:
            self.sync_pending()
        except Exception:  # noqa: BLE001 - the worker must never die loudly; failed copies are recorded per storage
            pass

    def resync(self, aid: str) -> dict:
        if self.db.one("SELECT 1 FROM artifacts WHERE id=?", (aid,)) is None:
            raise ArtifactError("no such artifact")
        cfg = self.config()
        on = {s["id"] for s in cfg["storages"] if s["enabled"]}
        row = self.db.one("SELECT type, size FROM artifacts WHERE id=?", (aid,))
        have = {c["storage_id"] for c in self.db.query("SELECT storage_id FROM artifact_copies WHERE artifact_id=?", (aid,))}
        name = self.db.one("SELECT name FROM artifacts WHERE id=?", (aid,))["name"]
        for sid in self.targets_for(row["type"], row["size"], cfg):                 # a rule changed since: add the copies it now asks for
            if sid in on and sid not in have:
                self.db.execute("INSERT INTO artifact_copies(artifact_id,storage_id,key,status,detail,updated) VALUES(?,?,?,?,?,?)", (aid, sid, f"{aid}/{name}", "pending", "", time.time()))
        self.sync(aid, retry_failed=True)
        return self.get(aid)

    # ------------------------------------------------------------------ reading
    def _public(self, r, copies: list[dict]) -> dict:
        return {"id": r["id"], "name": r["name"], "path": r["path"], "type": r["type"], "mime": r["mime"], "preview": r["preview"], "size": r["size"], "sha256": r["sha256"],
                "version": r["version"], "created": r["created"], "agent": r["agent"], "session_id": r["session_id"], "trace_id": r["trace_id"], "workspace": r["workspace"],
                "source": r["source"], "tags": json.loads(r["tags"] or "[]"), "summary": r["summary"], "secrets": bool(r["secrets"]), "copies": copies}

    def _copies(self, ids: list[str]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {i: [] for i in ids}
        if not ids:
            return out
        for c in self.db.query(f"SELECT * FROM artifact_copies WHERE artifact_id IN ({','.join('?' * len(ids))}) ORDER BY storage_id='local' DESC, storage_id", ids):
            out[c["artifact_id"]].append({"storage": c["storage_id"], "status": c["status"], "detail": c["detail"], "size": c["size"], "updated": c["updated"]})
        return out

    def get(self, aid: str) -> dict:
        r = self.db.one("SELECT * FROM artifacts WHERE id=?", (aid,))
        if r is None:
            raise ArtifactError("no such artifact")
        return self._public(r, self._copies([aid])[aid])

    def list(self, *, q: str = "", agent: str = "", session_id: str = "", trace_id: str = "", type: str = "", storage: str = "", source: str = "",
             since: float | None = None, until: float | None = None, min_size: int | None = None, max_size: int | None = None,
             sort: str = "created", order: str = "desc", limit: int = 60, offset: int = 0) -> dict:
        where, args = [], []
        for term in re.findall(r"[^\s]+", q.lower())[:8]:
            where.append("(LOWER(name) LIKE ? ESCAPE '\\' OR LOWER(tags) LIKE ? ESCAPE '\\' OR LOWER(summary) LIKE ? ESCAPE '\\' OR LOWER(search_text) LIKE ? ESCAPE '\\' OR LOWER(path) LIKE ? ESCAPE '\\')")
            like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            args += [like] * 5
        for col, val in (("agent", agent), ("session_id", session_id), ("trace_id", trace_id), ("type", type), ("source", source)):
            if val:
                where.append(f"{col}=?")
                args.append(val)
        if storage:
            where.append("id IN (SELECT artifact_id FROM artifact_copies WHERE storage_id=? AND status='synced')")
            args.append(storage)
        for col, op, val in (("created", ">=", since), ("created", "<=", until), ("size", ">=", min_size), ("size", "<=", max_size)):
            if val is not None:
                where.append(f"{col}{op}?")
                args.append(val)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        order_by = f"{SORTS.get(sort, 'created')} {'ASC' if order == 'asc' else 'DESC'}, created DESC"
        total = self.db.one(f"SELECT COUNT(*) AS n FROM artifacts{clause}", args)["n"]
        rows = self.db.query(f"SELECT * FROM artifacts{clause} ORDER BY {order_by} LIMIT ? OFFSET ?", [*args, max(1, min(limit, 500)), max(0, offset)])
        copies = self._copies([r["id"] for r in rows])
        return {"total": total, "items": [self._public(r, copies[r["id"]]) for r in rows]}

    def facets(self) -> dict:
        def counts(col: str) -> list[dict]:
            return [{"value": r["v"], "count": r["n"]} for r in self.db.query(f"SELECT {col} AS v, COUNT(*) AS n FROM artifacts WHERE {col}!='' GROUP BY {col} ORDER BY n DESC")]
        store = [{"value": r["v"], "count": r["n"]} for r in self.db.query("SELECT storage_id AS v, COUNT(*) AS n FROM artifact_copies WHERE status='synced' GROUP BY storage_id ORDER BY n DESC")]
        t = self.db.one("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS b FROM artifacts")
        return {"agents": counts("agent"), "types": counts("type"), "sources": counts("source"), "storages": store, "total": t["n"], "bytes": t["b"]}

    def read_bytes(self, aid: str) -> tuple[bytes, str]:
        """(the file's bytes, the storage they came from). Tries the folder on this PC first, then the others, then the staging area."""
        if self.db.one("SELECT 1 FROM artifacts WHERE id=?", (aid,)) is None:
            raise ArtifactError("no such artifact")
        problems = []
        for c in self.db.query("SELECT storage_id, key FROM artifact_copies WHERE artifact_id=? AND status='synced' ORDER BY storage_id='local' DESC", (aid,)):
            try:
                return self._storage(c["storage_id"]).get(c["key"]), c["storage_id"]
            except StorageError as e:
                problems.append(f"{c['storage_id']}: {e}")
        f = self.stage / aid
        if f.is_file():
            return f.read_bytes(), "staging"
        raise ArtifactError("the file could not be read from any storage" + (f" ({'; '.join(problems)})" if problems else ""))

    def preview(self, aid: str) -> dict:
        a = self.get(aid)
        if a["preview"] not in TEXT_PREVIEWS and a["preview"] != "text":
            return {"kind": a["preview"], "text": "", "truncated": False}
        data, _ = self.read_bytes(aid)
        return {"kind": a["preview"], "text": data[:PREVIEW_BYTES].decode("utf-8", errors="replace"), "truncated": len(data) > PREVIEW_BYTES}

    # ------------------------------------------------------------------ deleting and retention
    def delete(self, aid: str) -> dict:
        if self.db.one("SELECT 1 FROM artifacts WHERE id=?", (aid,)) is None:
            raise ArtifactError("no such artifact")
        problems = []
        for c in self.db.query("SELECT storage_id, key, status FROM artifact_copies WHERE artifact_id=?", (aid,)):
            if c["status"] != "synced":
                continue
            try:
                self._storage(c["storage_id"]).delete(c["key"])
            except StorageError as e:
                problems.append({"storage": c["storage_id"], "error": str(e)})
        (self.stage / aid).unlink(missing_ok=True)
        self.db.execute("DELETE FROM artifacts WHERE id=?", (aid,))
        return {"deleted": True, "storage_errors": problems}

    def purge(self, days: int | None = None) -> dict:
        days = self.config()["retention_days"] if days is None else days
        if not days:
            return {"deleted": 0}
        n = 0
        for r in self.db.query("SELECT id FROM artifacts WHERE created < ?", (time.time() - days * 86400,)):
            self.delete(r["id"])
            n += 1
        return {"deleted": n}

    def maybe_purge(self, cfg: dict) -> None:
        if cfg["retention_days"] and time.time() - self._last_purge > 3600:
            self._last_purge = time.time()
            self.purge(cfg["retention_days"])

    # ------------------------------------------------------------------ tests, stats
    def test_storage(self, sid: str) -> dict:
        try:
            return self._storage(sid).health()
        except StorageError as e:
            return {"ok": False, "detail": str(e)}

    def stats(self) -> dict:
        """Counts only (for the AI-BOM): never names, paths or content."""
        f = self.facets()
        cfg = self.config()
        return {"total": f["total"], "bytes": f["bytes"], "by_type": {x["value"]: x["count"] for x in f["types"]}, "by_source": {x["value"]: x["count"] for x in f["sources"]},
                "storages": [{"kind": s["kind"], "enabled": s["enabled"], "cloud": KINDS[s["kind"]]["cloud"]} for s in cfg["storages"]],
                "capture": cfg["capture"], "enabled": cfg["enabled"], "secret_scan": cfg["secret_scan"], "retention_days": cfg["retention_days"]}

    def resume(self) -> None:
        """At start-up: copies that were waiting when the app last stopped are sent now."""
        if self._pending_remote() and self._start_worker:
            self.kick()
