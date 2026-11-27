"""Desktop build: the API server also serves the static web UI from one loopback address.

The installed app starts the server with AGENT_STUDIO_WEB_DIR pointing at the static Next export, and the window loads
http://127.0.0.1:<port>/. UI and API share one origin, so no CORS exception and no `null` origin is needed."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from starlette.staticfiles import StaticFiles

_SEGMENT = re.compile(r"^(?P<dir>(?:.*/)?)__next\.(?P<inner>.+)\.txt$")


class WebFiles(StaticFiles):
    """StaticFiles plus one mapping the Next 16 export needs: the client prefetches `<route>/__next.a.b.__PAGE__.txt`
    while the export writes it as `<route>/__next.a/b/__PAGE__.txt` (static hosts are expected to rewrite this)."""

    async def get_response(self, path: str, scope):
        m = _SEGMENT.match(path.replace("\\", "/"))
        if m is not None and "/" not in m["inner"]:
            alt = f"{m['dir']}__next.{m['inner'].replace('.', '/')}.txt"
            if alt != path and Path(self.directory, alt).is_file():      # e.g. the literal __next._tree.txt exists and is served as is
                return await super().get_response(alt, scope)
        return await super().get_response(path, scope)


def web_dir(env: Mapping[str, str]) -> Path | None:
    d = (env.get("AGENT_STUDIO_WEB_DIR") or "").strip()
    return Path(d) if d else None


def own_origins(env: Mapping[str, str]) -> list[str]:
    """The UI's own origin when this server serves it (the live WebSocket and the apps preview check the origin)."""
    if web_dir(env) is None:
        return []
    port = (env.get("AGENT_STUDIO_PORT") or "8765").strip()
    return [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]


def mount_web(app, env: Mapping[str, str]) -> bool:
    """Serve the export at "/" (after every API route, so /api, /v1, /a2a... still win). html=True serves
    <route>/index.html and the export's 404.html."""
    d = web_dir(env)
    if d is None:
        return False
    if not (d / "index.html").is_file():
        raise RuntimeError(f"AGENT_STUDIO_WEB_DIR has no index.html: {d}")
    app.mount("/", WebFiles(directory=str(d), html=True), name="web")
    return True
