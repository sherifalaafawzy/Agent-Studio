"""Running an app: the preview, the sandbox and the way an app reaches your agents.

* A STATIC app has no process. Agent Studio serves its `public/` folder from /apps-run/<id>/ with a strict Content-Security-Policy (no remote scripts,
  no inline scripts except the one Agent Studio adds itself, network limited to what the manifest allows) and the page is shown in an iframe with
  `sandbox="allow-scripts"` only: no same-origin, so it cannot read Agent Studio, its cookies or its storage. The page calls your agents through
  /apps-run/<id>/_agent/v1/chat/completions with a short-lived signed token; Agent Studio checks the token and that the agent is one the manifest lists,
  then forwards the request with the app's own API key. The key is never in the page.
* A SERVER app (node / python) runs in a Docker container on this PC: non-root, no capabilities, no-new-privileges, memory / CPU / process limits, a
  read-only root file system and a read-only mount of the app. Its port is published on 127.0.0.1 only. A published port needs the container to be on
  a bridge network, so its outbound network is OPEN: that is why running server apps is off until you allow it in Settings > Apps. The app gets its key
  and its environment values through an env file that is deleted as soon as the container has started.
"""
from __future__ import annotations

import hashlib
import hmac
import asyncio
import collections
import json
import mimetypes
import re
import secrets
import shlex
import socket
import time
from pathlib import Path
from urllib.parse import urlparse

from ..db import data_dir
from ..harness.sandbox import SandboxUnavailable, _docker
from .manifest import ManifestError  # noqa: F401 - re-exported for callers
from .store import AppError, AppStore, safe_rel
from .studio_js import STUDIO_JS

TOKEN_TTL = 2 * 3600
MAX_BODY = 200_000
IMAGES = {"node": "node:22-alpine", "python": "python:3.12-slim"}
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
        ".webp": "image/webp", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8"}
PROXY_PATHS = {"v1/chat/completions"}
MAX_DATA_BODY = 20_000
DATA_PER_MIN = 60
CROSSORIGIN = re.compile(r"<script\b(?![^>]*\bcrossorigin)([^>]*\bsrc=)", re.I)
CONSOLE_KINDS = {"error", "resource", "blocked", "console.error", "console.warn", "ready"}


def free_port(preferred: int) -> int:
    """A TCP port on 127.0.0.1 that is free now, starting from `preferred`."""
    for p in range(preferred, preferred + 60):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise AppError("no free port was found for the app")


def csp(manifest: dict, nonce: str, frame_ancestors: list[str]) -> str:
    net, allow = manifest["permissions"]["network"], manifest["permissions"]["allow"]
    connect = "'self'"
    if net == "allowlist" and allow:
        connect += " " + " ".join(allow)
    elif net == "open":
        connect += " https:"
    ancestors = " ".join(frame_ancestors) if frame_ancestors else "'none'"
    return "; ".join([
        "default-src 'none'", f"script-src 'self' 'nonce-{nonce}'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data: blob:", "font-src 'self' data:",
        "media-src 'self' data: blob:", f"connect-src {connect}", "base-uri 'none'", "form-action 'none'", f"frame-ancestors {ancestors}"])


class AppRunner:
    def __init__(self, store: AppStore, docker=None, http=None, policy=None, free=free_port, clock=time.time, home: Path | None = None, frame_ancestors: list[str] | None = None):
        self.store, self.docker, self.http, self.free, self.clock = store, docker or _docker, http, free, clock
        self.policy = policy or (lambda: {"memory": "1g", "cpus": "2", "pids": 256})
        self.home = Path(home) if home else data_dir()
        self.frame_ancestors = frame_ancestors or []
        self.data = None                                               # the DataService (set by State): the read-only data sources an app may query
        self._data_hits: dict[str, collections.deque] = {}
        self.api_url = ""                                              # Agent Studio's own API address (set when an app is started from it): where agent calls are forwarded

    # ---- the short-lived token a preview page uses to reach /_agent
    def _secret(self) -> bytes:
        s = self.store.db.get_setting("apps_token_secret")
        if not s:
            s = secrets.token_hex(32)
            self.store.db.set_setting("apps_token_secret", s)
        return bytes.fromhex(s)

    def token(self, app_id: str, ttl: int = TOKEN_TTL) -> str:
        exp = int(self.clock()) + ttl
        sig = hmac.new(self._secret(), f"{app_id}.{exp}".encode(), hashlib.sha256).hexdigest()[:40]
        return f"{exp}.{sig}"

    def verify_token(self, app_id: str, token: str) -> bool:
        try:
            exp, sig = (token or "").split(".", 1)
            ok = hmac.compare_digest(sig, hmac.new(self._secret(), f"{app_id}.{int(exp)}".encode(), hashlib.sha256).hexdigest()[:40])
        except ValueError:
            return False
        return ok and int(exp) >= self.clock()

    # ---- serving a static app
    def serve(self, app_id: str, rel: str, studio_url: str) -> tuple[int, dict, bytes]:
        """(status, headers, body) for one file of the app's public/ folder."""
        if rel.rsplit("/", 1)[-1] == "_studio.js":                      # Agent Studio's own helper (errors and "change this"): never a file of the app
            try:
                self.store.manifest(app_id)
            except AppError as e:
                return 404, {"Content-Type": "text/plain; charset=utf-8"}, str(e).encode()
            return 200, {"Content-Type": MIME[".js"], "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store", "Access-Control-Allow-Origin": "null"}, STUDIO_JS.encode("utf-8")
        try:
            m = self.store.manifest(app_id)
            rel = safe_rel("public/" + (rel.strip("/") or "index.html"))
            f = self.store.read(app_id, rel)
        except AppError as e:
            return 404, {"Content-Type": "text/plain; charset=utf-8"}, str(e).encode()
        if f["binary"]:
            data = (self.store.root(app_id) / rel).read_bytes()
        else:
            data = f["content"].encode("utf-8")
        ctype = MIME.get(Path(rel).suffix.lower()) or mimetypes.guess_type(rel)[0] or "application/octet-stream"
        nonce = secrets.token_urlsafe(16)
        headers = {"Content-Type": ctype, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
                   "Access-Control-Allow-Origin": "null", "Vary": "Origin", "Content-Security-Policy": csp(m, nonce, self.frame_ancestors)}
        if rel.endswith(".html") and not f["binary"]:
            cfg = json.dumps({"base": f"{studio_url.rstrip('/')}/apps-run/{app_id}/_agent", "token": self.token(app_id)}).replace("<", "\\u003c")
            tag = f'<script nonce="{nonce}">window.AGENT_STUDIO={cfg};</script><script src="{studio_url.rstrip("/")}/apps-run/{app_id}/_studio.js"></script>'
            text = CROSSORIGIN.sub(r'<script crossorigin="anonymous"\1', f["content"])          # the page has no origin of its own: without this its script errors arrive as an empty "Script error."
            i = text.lower().find("<head>")
            text = text[:i + 6] + tag + text[i + 6:] if i >= 0 else tag + text
            data = text.encode("utf-8")
        return 200, headers, data

    # ---- the proxy a static app uses to call its agents
    async def forward(self, app_id: str, token: str, rest: str, body: bytes, studio_url: str):
        """(status, content_type, async byte iterator) after checking token, path and agent. Errors come back as OpenAI-style JSON. `studio_url` is Agent Studio's own API address."""
        def err(status: int, message: str):
            async def one():
                yield json.dumps({"error": {"message": message, "type": "app_proxy_error"}}).encode()
            return status, "application/json", one()
        if not self.verify_token(app_id, token):
            return err(401, "This preview has expired. Close it and open the app again from Agent Studio.")
        if rest not in PROXY_PATHS:
            return err(404, "Apps can only call v1/chat/completions.")
        if len(body) > MAX_BODY:
            return err(413, "The request is too large.")
        try:
            allowed = [a["id"] for a in self.store.manifest(app_id)["agents"]]
            payload = json.loads(body or b"{}")
        except (AppError, ValueError):
            return err(400, "The request was not valid JSON, or the app's manifest is broken.")
        if not isinstance(payload, dict) or payload.get("model") not in allowed:
            return err(403, "This app may only call: " + (", ".join(allowed) or "no agents (add one to the manifest)") + ".")
        try:
            key = self.store.ensure_key(app_id)
        except AppError as e:
            return err(403, str(e))
        if not studio_url:
            return err(503, "Start the app from Agent Studio first: it does not know where Agent Studio is.")
        import httpx
        client = (self.http or httpx.AsyncClient)(timeout=httpx.Timeout(300, connect=5))
        try:
            req = client.build_request("POST", f"{studio_url.rstrip('/')}/v1/chat/completions", content=json.dumps(payload).encode(),
                                       headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            resp = await client.send(req, stream=True)
        except httpx.HTTPError as e:
            await client.aclose()
            return err(502, f"Agent Studio could not be reached: {type(e).__name__}")

        async def gen():
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()
        return resp.status_code, resp.headers.get("content-type", "application/json"), gen()

    # ---- what the page reports about itself (errors, "ready") for the builder to read
    def record_console(self, app_id: str, token: str, body: bytes) -> tuple[int, dict]:
        if not self.verify_token(app_id, token):
            return 401, {"error": "this preview has expired"}
        if len(body) > 4000:
            return 413, {"error": "too large"}
        try:
            d = json.loads(body or b"{}")
            if not isinstance(d, dict) or d.get("kind") not in CONSOLE_KINDS:
                raise ValueError
            self.store.add_console(app_id, str(d["kind"]), str(d.get("text", "")), str(d.get("page", "")), str(d.get("source", "")), d.get("line") if isinstance(d.get("line"), int) else 0)
        except (ValueError, AppError):
            return 400, {"error": "not a report"}
        return 204, {}

    # ---- the read-only data sources an app may query (only those its manifest lists)
    async def data_query(self, app_id: str, token: str, source: str, body: bytes) -> tuple[int, dict]:
        def err(status: int, message: str) -> tuple[int, dict]:
            return status, {"error": {"message": message, "type": "app_data_error"}}
        if not self.verify_token(app_id, token):
            return err(401, "This preview has expired. Close it and open the app again from Agent Studio.")
        if len(body) > MAX_DATA_BODY:
            return err(413, "The request is too large.")
        try:
            allowed = [d["id"] for d in self.store.manifest(app_id)["data"]]
            payload = json.loads(body or b"{}")
        except (AppError, ValueError):
            return err(400, "The request was not valid JSON, or the app's manifest is broken.")
        if not isinstance(payload, dict) or not isinstance(payload.get("sql"), str) or not payload["sql"].strip():
            return err(400, "Send {sql, max_rows}.")
        if source not in allowed:
            return err(403, "This app may only read: " + (", ".join(allowed) or "no data sources (connect one in Agent Studio)") + ".")
        if self.data is None:
            return err(503, "Data sources are not available.")
        now = self.clock()
        hits = self._data_hits.setdefault(app_id, collections.deque())
        while hits and hits[0] < now - 60:
            hits.popleft()
        if len(hits) >= DATA_PER_MIN:
            return err(429, f"Too many data requests: at most {DATA_PER_MIN} a minute.")
        hits.append(now)
        from ..datasources.base import DataError
        mr = payload.get("max_rows")
        try:
            out = await asyncio.to_thread(self.data.query, source, payload["sql"], mr if isinstance(mr, int) and not isinstance(mr, bool) else None, f"app:{self.store._row(app_id)['slug']}")
        except DataError as e:
            return err(422, str(e))
        return 200, out

    # ---- running (static: nothing to start; server: a container)
    def _name(self, app_id: str) -> str:
        return f"agent-studio-app-{app_id}"

    def _docker_call(self, args: list[str], timeout: float = 60):
        try:
            return self.docker(args, timeout)
        except SandboxUnavailable as e:
            raise AppError(f"Docker is needed to run server apps: {e}")

    def start(self, app_id: str, api_url: str, preview_url: str) -> dict:
        m = self.store.manifest(app_id)
        self.store._on()
        self.api_url = api_url.rstrip("/")
        if m["type"] == "static":
            return dict(kind="static", status="ready", url=f"{preview_url.rstrip('/')}/apps-run/{app_id}/", detail="Static apps have nothing to start: the preview is served by Agent Studio.")
        cfg = self.store.config()
        if not cfg["allow_server_apps"]:
            raise AppError("Server apps run in a container on an open network (a published port needs it). Allow that in Settings > Apps first.")
        if m["entry"] not in self.store.files_dict(app_id):
            raise AppError(f"the server file '{m['entry']}' is missing")
        env = {"PORT": str(m["port"]), **self.store.env_values(app_id)}
        key = self.store.ensure_key(app_id)
        if key:
            env["AGENT_STUDIO_API_KEY"] = key
            env["AGENT_STUDIO_BASE_URL"] = f"http://host.docker.internal:{urlparse(api_url).port or 80}/v1"
        if any("\n" in v or "\r" in v for v in env.values()):
            raise AppError("environment values cannot contain line breaks")
        port = self.free(18000 + int(app_id, 16) % 1000)
        pol = self.policy()
        envdir = self.home / "apps-run"
        envdir.mkdir(parents=True, exist_ok=True)
        envfile = envdir / f"{app_id}.env"
        envfile.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
        try:
            envfile.chmod(0o600)
        except OSError:                                                # pragma: no cover - not supported on every file system
            pass
        name = self._name(app_id)
        try:
            self._docker_call(["rm", "-f", name], 30)
            args = ["run", "-d", "--name", name, "--label", "agent-studio-app=1", "--label", f"agent-studio-app-id={app_id}", "--network", "bridge",
                    "-p", f"127.0.0.1:{port}:{m['port']}", "--add-host", "host.docker.internal:host-gateway", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--pids-limit", str(pol["pids"]), "--memory", pol["memory"], "--cpus", str(pol["cpus"]), "--read-only", "--tmpfs", "/tmp:rw,size=64m", "--user", "1000:1000",
                    "--mount", f"type=bind,source={self.store.root(app_id).resolve()},target=/app,readonly", "-w", "/app", "--env-file", str(envfile),
                    IMAGES[m["type"]], *shlex.split(m["start"])]
            r = self._docker_call(args, 180)
        finally:
            envfile.unlink(missing_ok=True)
        if r.returncode != 0:
            raise AppError(f"the container did not start: {(r.stderr or r.stdout).strip()[:300]}")
        return dict(kind=m["type"], status="running", url=f"http://127.0.0.1:{port}/", port=port, detail="Running in a container; the port is open on this PC only.")

    def stop(self, app_id: str) -> dict:
        m = self.store.manifest(app_id)
        if m["type"] == "static":
            return dict(status="ready")
        self._docker_call(["rm", "-f", self._name(app_id)], 30)
        return dict(status="stopped")

    def stop_all(self) -> None:
        """Remove every app container (called when Agent Studio shuts down). Quiet when Docker is not there."""
        try:
            r = self.docker(["ps", "-aq", "--filter", "label=agent-studio-app=1"], 15)
            ids = r.stdout.split() if r.returncode == 0 else []
            if ids:
                self.docker(["rm", "-f", *ids], 30)
        except SandboxUnavailable:
            pass

    def status(self, app_id: str, preview_url: str = "") -> dict:
        m = self.store.manifest(app_id)
        if m["type"] == "static":
            return dict(kind="static", status="ready", url=f"{preview_url.rstrip('/')}/apps-run/{app_id}/" if preview_url else "")
        try:
            r = self.docker(["inspect", "-f", "{{.State.Status}}", self._name(app_id)], 15)
            if r.returncode != 0:
                return dict(kind=m["type"], status="stopped")
            state = r.stdout.strip()
            out = dict(kind=m["type"], status="running" if state == "running" else "stopped", detail=f"container is {state}")
            if state == "running":
                pr = self.docker(["port", self._name(app_id), f"{m['port']}/tcp"], 15)
                port = pr.stdout.strip().rsplit(":", 1)[-1] if pr.returncode == 0 else ""
                if port.isdigit():
                    out.update(port=int(port), url=f"http://127.0.0.1:{port}/")
            return out
        except SandboxUnavailable as e:
            return dict(kind=m["type"], status="unavailable", detail=str(e))

    def logs(self, app_id: str, tail: int = 200) -> dict:
        m = self.store.manifest(app_id)
        if m["type"] == "static":
            return dict(logs="Static apps have no process, so there are no logs. Errors from the page appear in your browser's console.")
        r = self._docker_call(["logs", "--tail", str(max(1, min(int(tail), 1000))), self._name(app_id)], 20)
        text = (r.stdout or "") + (r.stderr or "")
        return dict(logs=text[-20000:] if r.returncode == 0 else "(no container is running)")
