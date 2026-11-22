"""The discovery server: a small READ-ONLY app, separate from Agent Studio's API, that serves only what the user chose to publish.

Why separate: the main API (port 8765) can read and change settings, keys and chats, so it must never be reachable from outside.
This one serves a fixed set of static documents (the ARD manifest, BOMs, skill files) from memory. It listens on 127.0.0.1 only:
to make it discoverable the user puts their own domain or tunnel in front of it (that is deliberately not set up here).

* Nothing is served until publishing is enabled and ready (publisher domain, resource types, access token for non-public levels).
* The `public` level needs no token and sends `Access-Control-Allow-Origin: *`, as the ARD publishing guide asks.
  `internal` and `full` require `Authorization: Bearer <token>` and send no CORS header.
"""
from __future__ import annotations

import threading
import time

import uvicorn
from fastapi import FastAPI, Request, Response

from .settings import DiscoverySettings, SettingsError
from .site import SiteCache

HOST = "127.0.0.1"


def create_discovery_app(cache: SiteCache, settings: DiscoverySettings) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def base_headers(cfg: dict) -> dict:
        h = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
        if cfg["level"] == "public":
            h["Access-Control-Allow-Origin"] = "*"
        return h

    def not_found(cfg: dict) -> Response:
        return Response("not found\n", status_code=404, media_type="text/plain", headers=base_headers(cfg))

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "OPTIONS"], include_in_schema=False)
    async def serve(path: str, request: Request) -> Response:
        cfg = settings.get()
        headers = base_headers(cfg)
        if request.method == "OPTIONS":
            return Response(status_code=204, headers={**headers, "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS", "Access-Control-Allow-Headers": "*"})
        if settings.readiness(cfg):
            return not_found(cfg)
        if cfg["level"] != "public":
            auth = request.headers.get("authorization", "")
            if not (auth.lower().startswith("bearer ") and settings.check_token(auth[7:].strip())):
                return Response("unauthorized\n", status_code=401, media_type="text/plain", headers={**headers, "WWW-Authenticate": "Bearer"})
        try:
            site = cache.get()
        except SettingsError:
            return not_found(cfg)
        art = site.files.get(path.strip("/") or "index.html")
        if art is None:
            return not_found(cfg)
        etag = f'"{art.sha256}"'
        headers.update({"ETag": etag, "Cache-Control": "public, max-age=300" if cfg["level"] == "public" else "private, no-store"})
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(art.data, media_type=art.content_type, headers=headers)

    return app


class DiscoveryServer:
    """Runs the discovery app on a loopback port in a background thread, started and stopped on request."""

    def __init__(self, cache: SiteCache, settings: DiscoverySettings):
        self.cache, self.settings = cache, settings
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self.error = ""

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and self.server and self.server.started)

    def port(self) -> int | None:
        if not self.running():
            return None
        return self.server.servers[0].sockets[0].getsockname()[1]

    def status(self) -> dict:
        port = self.port()
        return {"running": port is not None, "host": HOST, "port": port, "local_url": f"http://{HOST}:{port}/" if port else None, "error": self.error}

    def start(self, port: int | None = None) -> dict:
        if self.running():
            return self.status()
        why = self.settings.readiness()
        if why:
            raise SettingsError("; ".join(why))
        cfg = self.settings.get()
        self.error = ""
        config = uvicorn.Config(create_discovery_app(self.cache, self.settings), host=HOST, port=cfg["port"] if port is None else port,
                                log_level="warning", access_log=False, lifespan="off")
        server = uvicorn.Server(config)

        def run() -> None:
            try:
                server.run()
            except BaseException as e:  # noqa: BLE001 - a failed bind exits the thread; report it instead of dying silently
                self.error = f"could not start: {e}" if not isinstance(e, SystemExit) else "could not start: is the port already in use?"

        self.server = server
        self.thread = threading.Thread(target=run, name="ard-discovery", daemon=True)
        self.thread.start()
        deadline = time.time() + 8
        while time.time() < deadline and not server.started and self.thread.is_alive():
            time.sleep(0.05)
        if not server.started:
            self.thread.join(timeout=1)
            raise SettingsError(self.error or "the discovery server did not start in time")
        return self.status()

    def stop(self) -> dict:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=8)
        self.server, self.thread = None, None
        return self.status()
