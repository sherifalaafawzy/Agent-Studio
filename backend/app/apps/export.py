"""Getting an app out of Agent Studio: a zip, a git repository, and a plan (the exact commands) for hosting it elsewhere.

Everything here refuses to send out a file that looks like it holds a secret, never writes a token into a file or a URL, and never pushes without an
explicit confirm. Git runs through the `git` program on this PC; the credentials of a remote are write-only and are passed for one command only.
"""
from __future__ import annotations

import base64
import fnmatch
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from ..artifacts.service import scan_text
from .store import AppError, AppStore, _is_text

ALWAYS_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".next"}


def _patterns(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def ignored(rel: str, patterns: list[str]) -> bool:
    """Does a path match the .gitignore-style patterns (name, folder/, *.ext, !negation; the last match wins)?"""
    parts, hit = rel.split("/"), False
    for pat in patterns:
        neg = pat.startswith("!")
        p = pat[1:] if neg else pat
        if p.endswith("/"):
            m = p[:-1] in parts[:-1]
        else:
            m = fnmatch.fnmatch(parts[-1], p) or fnmatch.fnmatch(rel, p)
        if m:
            hit = not neg
    return hit


def secret_findings(store: AppStore, app_id: str) -> list[str]:
    """The files that look like they contain a secret (checked on everything that would leave this PC)."""
    out = []
    for rel, data in store.files_dict(app_id).items():
        if _is_text(data) and scan_text(data.decode("utf-8"))[1]:
            out.append(rel)
    return out


def exported_files(store: AppStore, app_id: str) -> tuple[dict[str, bytes], list[str]]:
    files = store.files_dict(app_id)
    pats = _patterns(files.get(".gitignore", b"").decode("utf-8", "replace"))
    keep, skipped = {}, []
    for rel, data in files.items():
        if ignored(rel, pats) or any(x in ALWAYS_SKIP for x in rel.split("/")):
            skipped.append(rel)
        else:
            keep[rel] = data
    return keep, sorted(skipped)


def export_zip(store: AppStore, app_id: str) -> tuple[bytes, dict]:
    """A zip of the app (a folder named after it), without what .gitignore excludes. Refused while any file looks like it holds a secret."""
    app = store.get(app_id)
    bad = secret_findings(store, app_id)
    if bad:
        raise AppError("not exported: these files look like they contain a secret: " + ", ".join(bad[:8]) + ". Remove it (list the name in the manifest's env instead) and try again.")
    keep, skipped = exported_files(store, app_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in keep.items():
            z.writestr(f"{app['slug']}/{rel}", data)
    return buf.getvalue(), dict(slug=app["slug"], files=len(keep), skipped=skipped, size=len(buf.getvalue()))


# ---------------------------------------------------------------------------------------------------- git
REMOTE_URL = re.compile(r"^https://[A-Za-z0-9.-]+(:\d{2,5})?/[\w./~-]+$")


def _run(args: list[str], cwd: str, env: dict, timeout: float):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


class Git:
    def __init__(self, store: AppStore, run=None, which=shutil.which):
        self.store, self.run, self.which = store, run or _run, which

    def _require_identity(self) -> dict:
        cfg = self.store.config()
        if not cfg["git_name"] or not cfg["git_email"]:
            raise AppError("Set your name and email for commits in Settings > Apps first (git needs them).")
        return cfg

    def _git(self, app_id: str, args: list[str], token: str = "", timeout: float = 60, identity: bool = False):
        exe = self.which("git")
        if not exe:
            raise AppError("Git is not installed on this PC (install it from git-scm.com), so the app cannot be connected to a repository.")
        root = self.store.root(app_id)
        cmd = [exe]
        if identity:
            cfg = self._require_identity()
            cmd += ["-c", f"user.name={cfg['git_name']}", "-c", f"user.email={cfg['git_email']}"]
        if token:
            cmd += ["-c", "http.extraheader=Authorization: Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()]
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo", "GCM_INTERACTIVE": "never"}
        try:
            r = self.run(cmd + args, str(root), env, timeout)
        except subprocess.TimeoutExpired:
            raise AppError("git did not finish in time")
        return r

    def _ok(self, r, what: str) -> str:
        if r.returncode != 0:
            msg = (r.stderr or r.stdout or "").strip().splitlines()
            raise AppError(f"git {what} failed: {msg[-1][:300] if msg else 'no message'}")
        return r.stdout

    def _info(self, app_id: str) -> dict:
        return json.loads(self.store._row(app_id)["git"] or "{}")

    def status(self, app_id: str) -> dict:
        info = self._info(app_id)
        if not (self.store.root(app_id) / ".git").is_dir():
            return dict(initialised=False, remote=info.get("remote", ""), branch="", changes=[], has_token=False)
        r = self._git(app_id, ["status", "--porcelain=v1", "-b"])
        lines = self._ok(r, "status").splitlines()
        head = lines[0][3:] if lines else ""
        for pre in ("No commits yet on ", "Initial commit on "):             # what git prints before the first commit
            if head.startswith(pre):
                head = head[len(pre):]
        changes = [dict(status=ln[:2].strip() or "?", path=ln[3:]) for ln in lines[1:]]
        last = self._git(app_id, ["log", "-1", "--format=%h %s"])
        return dict(initialised=True, remote=info.get("remote", ""), branch=head.split("...")[0], tracking=head, changes=changes, last_commit=last.stdout.strip() if last.returncode == 0 else "",
                    has_token=bool(self.store._secrets().get(app_id, {}).get("git_token")))

    def init(self, app_id: str, branch: str = "main") -> dict:
        self.store._on()
        if not re.match(r"^[\w.-]{1,40}$", branch or ""):
            raise AppError("the branch name is not valid")
        root = self.store.root(app_id)
        if (root / ".git").is_dir():
            raise AppError("this app is already a git repository")
        self._ok(self._git(app_id, ["init", "-b", branch]), "init")
        return self.status(app_id)

    def commit(self, app_id: str, message: str) -> dict:
        self.store._on()
        message = (message or "").strip()
        if not message or len(message) > 200:
            raise AppError("write a commit message (up to 200 characters)")
        if not (self.store.root(app_id) / ".git").is_dir():
            raise AppError("this app is not a git repository yet: start one first")
        bad = secret_findings(self.store, app_id)
        if bad:
            raise AppError("not committed: these files look like they contain a secret: " + ", ".join(bad[:8]))
        self._require_identity()
        self._ok(self._git(app_id, ["add", "-A"]), "add")
        r = self._git(app_id, ["commit", "-m", message], identity=True)
        if r.returncode != 0 and "nothing to commit" in (r.stdout + r.stderr):
            raise AppError("there is nothing new to commit")
        self._ok(r, "commit")
        h = self._git(app_id, ["rev-parse", "--short", "HEAD"])
        return dict(commit=h.stdout.strip(), status=self.status(app_id))

    def diff(self, app_id: str) -> dict:
        if not (self.store.root(app_id) / ".git").is_dir():
            raise AppError("this app is not a git repository yet")
        d = self._ok(self._git(app_id, ["diff", "HEAD"]) if self._has_commit(app_id) else self._git(app_id, ["diff"]), "diff")
        return dict(diff=d[:60000], truncated=len(d) > 60000)

    def _has_commit(self, app_id: str) -> bool:
        return self._git(app_id, ["rev-parse", "--verify", "HEAD"]).returncode == 0

    def log(self, app_id: str, n: int = 20) -> list[dict]:
        if not (self.store.root(app_id) / ".git").is_dir() or not self._has_commit(app_id):
            return []
        out = self._ok(self._git(app_id, ["log", f"-{max(1, min(int(n), 100))}", "--format=%h%x09%an%x09%ad%x09%s", "--date=short"]), "log")
        return [dict(zip(("hash", "author", "date", "message"), ln.split("\t", 3))) for ln in out.splitlines() if ln.count("\t") >= 3]

    def set_remote(self, app_id: str, url: str, token: str | None = None, branch: str = "main") -> dict:
        """Point the app at a remote: an https address or a folder on this PC (a repository you made with `git init --bare`). The token is write-only."""
        self.store._on()
        url = (url or "").strip()
        if not re.match(r"^[\w.-]{1,40}$", branch or ""):
            raise AppError("the branch name is not valid")
        if not (self.store.root(app_id) / ".git").is_dir():
            raise AppError("start a git repository first")
        if url.startswith("https://"):
            if "@" in url.split("/")[2] or not REMOTE_URL.match(url):
                raise AppError("use a plain https address such as https://github.com/you/app.git (no user name or password in it)")
        elif not (os.path.isabs(url) and Path(url).is_dir()):
            raise AppError("the remote must be an https address, or the full path of a folder on this PC")
        cur = self._git(app_id, ["remote", "get-url", "origin"])
        self._ok(self._git(app_id, ["remote", "set-url" if cur.returncode == 0 else "add", "origin", url]), "remote")
        self.store.db.execute("UPDATE apps SET git=? WHERE id=?", (json.dumps(dict(remote=url, branch=branch)), app_id))
        if token is not None:
            if not isinstance(token, str) or len(token) > 400:
                raise AppError("the token must be text of at most 400 characters")
            self.store._save_secret(app_id, {"git_token": token})
        return self.status(app_id)

    def _remote(self, app_id: str) -> tuple[dict, str]:
        info = self._info(app_id)
        if not info.get("remote"):
            raise AppError("connect a remote first")
        return info, self.store._secrets().get(app_id, {}).get("git_token", "")

    def push(self, app_id: str, confirm: bool = False) -> dict:
        info, token = self._remote(app_id)
        if confirm is not True:
            raise AppError(f"pushing sends this app's files to {info['remote']}: confirm to go ahead")
        bad = secret_findings(self.store, app_id)
        if bad:
            raise AppError("not pushed: these files look like they contain a secret: " + ", ".join(bad[:8]))
        self._ok(self._git(app_id, ["push", "-u", "origin", info.get("branch") or "main"], token, 120), "push")
        return dict(pushed=True, remote=info["remote"], status=self.status(app_id))

    def pull(self, app_id: str, confirm: bool = False) -> dict:
        info, token = self._remote(app_id)
        if confirm is not True:
            raise AppError(f"pulling changes from {info['remote']} replaces files here: confirm to go ahead (the app is saved as a version first)")
        self.store.snapshot(app_id, "before pulling from the remote", "user")
        self._ok(self._git(app_id, ["pull", "--ff-only", "origin", info.get("branch") or "main"], token, 120), "pull")
        v = self.store.snapshot(app_id, "pulled from the remote", "user")
        return dict(pulled=True, version=v, status=self.status(app_id))


# ---------------------------------------------------------------------------------------------------- hosting it elsewhere
def deploy_plan(store: AppStore, app_id: str) -> dict:
    """The exact commands to package and host the app. Nothing is run: Agent Studio does not deploy apps to a cloud runtime yet."""
    app, m = store.get(app_id), store.manifest(app_id)
    tag = f"{app['slug']}:{m['version']}"
    port = 80 if m["type"] == "static" else m["port"]
    steps = [
        dict(title="Build the image", command=f"docker build -t {tag} .", where="in the app folder (export it first)"),
        dict(title="Try it locally", command=f"docker run --rm -p 8080:{port} {tag}", where="then open http://127.0.0.1:8080"),
        dict(title="Tag it for your registry", command=f"docker tag {tag} <registry>/{tag}", where="replace <registry> with your registry address (Docker Hub, GHCR, ECR, ACR, GAR)"),
        dict(title="Push it", command=f"docker push <registry>/{tag}", where="you log in to your registry yourself (docker login)"),
    ]
    notes = ["Agent Studio does not deploy apps to a cloud runtime itself yet: use the commands above, then run the image on your own host (Cloud Run, Container Apps, App Runner, a VM...)."]
    if m["agents"]:
        notes.append("The app calls agents. Where it runs it must reach your Agent Studio (set AGENT_STUDIO_BASE_URL to a secure address of it) and use a key made with Rotate key; "
                     "never bake a key into the image. Exposing Agent Studio's API beyond this PC needs your own secure proxy.")
    if m["type"] == "static":
        notes.append("A static page cannot hold a key. Put a small server in front of it that holds the key (the Node or Python templates do that).")
    if m["env"]:
        notes.append("Set these environment variables where it runs: " + ", ".join(e["name"] for e in m["env"]) + ".")
    return dict(app=app["name"], image=tag, steps=steps, notes=notes)
