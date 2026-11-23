"""The apps preview server: a small web server on its own port (127.0.0.1 only) that serves the static apps and carries their calls to your agents.

Why separate: the main API (port 8765) can read and change settings, keys and chats. App pages are written by agents, so they must not share an origin
(or a port) with it. The page is shown in an iframe with `sandbox="allow-scripts"` and a Content-Security-Policy that lets it talk only to THIS server;
this server hands a call to an agent on only when it carries a valid short-lived token, only for an agent the app's manifest lists, with the app's own key.
Requests whose Host header is not the loopback address are refused (so a web page cannot reach it through DNS rebinding).
"""
from __future__ import annotations

import json
import threading
import time

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

HOST = "127.0.0.1"
ALLOWED_HOSTS = ("127.0.0.1", "localhost")


def create_apps_app(runner) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def host_ok(request: Request) -> bool:
        return (request.headers.get("host", "").rsplit(":", 1)[0].strip("[]")) in ALLOWED_HOSTS

    def refuse() -> Response:
        return Response("not found\n", status_code=404, media_type="text/plain")

    cors = {"Access-Control-Allow-Origin": "null", "Vary": "Origin"}

    @app.api_route("/apps-run/{app_id}/_agent/{rest:path}", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def agent(app_id: str, rest: str, request: Request):
        if not host_ok(request):
            return refuse()
        if request.method == "OPTIONS":
            return Response(status_code=204, headers={**cors, "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type, X-App-Token", "Access-Control-Max-Age": "600"})
        status, ctype, body = await runner.forward(app_id, request.headers.get("x-app-token", ""), rest, await request.body(), runner.api_url)
        return StreamingResponse(body, status_code=status, media_type=ctype, headers={**cors, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.api_route("/apps-run/{app_id}/_console", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def console(app_id: str, request: Request):
        if not host_ok(request):
            return refuse()
        if request.method == "OPTIONS":
            return Response(status_code=204, headers={**cors, "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type, X-App-Token", "Access-Control-Max-Age": "600"})
        status, body = runner.record_console(app_id, request.headers.get("x-app-token", ""), await request.body())
        return Response(status_code=status, content=json.dumps(body) if body else b"", media_type="application/json", headers={**cors, "Cache-Control": "no-store"})

    @app.api_route("/apps-run/{app_id}/_data/{source}", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def data(app_id: str, source: str, request: Request):
        if not host_ok(request):
            return refuse()
        if request.method == "OPTIONS":
            return Response(status_code=204, headers={**cors, "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type, X-App-Token", "Access-Control-Max-Age": "600"})
        status, body = await runner.data_query(app_id, request.headers.get("x-app-token", ""), source, await request.body())
        return Response(status_code=status, content=json.dumps(body), media_type="application/json", headers={**cors, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.api_route("/apps-run/{app_id}/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def serve(app_id: str, path: str, request: Request):
        if not host_ok(request):
            return refuse()
        status, headers, body = runner.serve(app_id, path, str(request.base_url).rstrip("/"))
        return Response(body if request.method == "GET" else b"", status_code=status, headers=headers)

    return app


class AppsServer:
    """Runs the preview app on a loopback port in a background thread, started on first use and stopped on request."""

    def __init__(self, runner, port: int = 8767):
        self.runner, self.port_wanted = runner, port
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self.error = ""

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and self.server and self.server.started)

    def port(self) -> int | None:
        return self.server.servers[0].sockets[0].getsockname()[1] if self.running() else None

    def url(self) -> str:
        p = self.port()
        return f"http://{HOST}:{p}" if p else ""

    def status(self) -> dict:
        return {"running": self.running(), "host": HOST, "port": self.port(), "url": self.url(), "error": self.error}

    def ensure(self, port: int | None = None) -> str:
        """Start the server if it is not running; returns its address (raises RuntimeError with the reason when it cannot start)."""
        if self.running():
            return self.url()
        self.error = ""
        server = uvicorn.Server(uvicorn.Config(create_apps_app(self.runner), host=HOST, port=self.port_wanted if port is None else port, log_level="warning", access_log=False, lifespan="off"))

        def run() -> None:
            try:
                server.run()
            except BaseException as e:  # noqa: BLE001 - a failed bind exits the thread; report it instead of dying silently
                self.error = f"could not start: {e}" if not isinstance(e, SystemExit) else "could not start: is the port already in use?"

        self.server = server
        self.thread = threading.Thread(target=run, name="apps-preview", daemon=True)
        self.thread.start()
        deadline = time.time() + 8
        while time.time() < deadline and not server.started and self.thread.is_alive():
            time.sleep(0.05)
        if not server.started:
            self.thread.join(timeout=1)
            raise RuntimeError(self.error or "the apps preview server did not start in time")
        return self.url()

    def stop(self) -> dict:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=8)
        self.server, self.thread = None, None
        return self.status()
