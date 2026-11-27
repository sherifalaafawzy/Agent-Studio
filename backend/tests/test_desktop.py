"""Desktop build: the API server serves the static UI on the same origin; MCP launchers that are missing get a clear reason."""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import desktop, mcp_registry
from app.main import allowed_origins


def _site(tmp_path):
    (tmp_path / "index.html").write_text("<h1>home</h1>", "utf-8")
    (tmp_path / "404.html").write_text("<h1>nope</h1>", "utf-8")
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "index.html").write_text("<h1>agents</h1>", "utf-8")
    (tmp_path / "agents" / "__next._tree.txt").write_text("tree", "utf-8")
    seg = tmp_path / "workflows" / "edit" / "__next.workflows" / "edit"
    seg.mkdir(parents=True)
    (seg / "__PAGE__.txt").write_text("rsc", "utf-8")
    return tmp_path


def test_no_web_dir_means_no_mount_and_no_extra_origins():
    app = FastAPI()
    assert desktop.mount_web(app, {}) is False and desktop.own_origins({}) == [] and desktop.web_dir({"AGENT_STUDIO_WEB_DIR": "  "}) is None
    assert "http://127.0.0.1:8765" not in allowed_origins({})


def test_own_origin_follows_the_port():
    env = {"AGENT_STUDIO_WEB_DIR": "x", "AGENT_STUDIO_PORT": "51234"}
    assert desktop.own_origins(env) == ["http://127.0.0.1:51234", "http://localhost:51234"]
    assert "http://127.0.0.1:51234" in allowed_origins(env)
    assert desktop.own_origins({"AGENT_STUDIO_WEB_DIR": "x"}) == ["http://127.0.0.1:8765", "http://localhost:8765"]


def test_serves_routes_404_and_api_still_wins(tmp_path):
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    assert desktop.mount_web(app, {"AGENT_STUDIO_WEB_DIR": str(_site(tmp_path))}) is True
    c = TestClient(app)
    assert c.get("/").text == "<h1>home</h1>"
    assert c.get("/agents/").text == "<h1>agents</h1>"
    r = c.get("/missing/page/")
    assert r.status_code == 404 and "nope" in r.text
    assert c.get("/api/health").json() == {"ok": True}
    # Next 16 prefetches `__next.a.b.__PAGE__.txt`; the export wrote `__next.a/b/__PAGE__.txt`
    assert c.get("/workflows/edit/__next.workflows.edit.__PAGE__.txt").text == "rsc"
    assert c.get("/agents/__next._tree.txt").text == "tree"                  # literal files are served as they are
    assert c.get("/agents/__next.agents.__PAGE__.txt").status_code == 404     # no such segment file: the normal 404


def test_missing_index_is_an_error(tmp_path):
    with pytest.raises(RuntimeError, match="no index.html"):
        desktop.mount_web(FastAPI(), {"AGENT_STUDIO_WEB_DIR": str(tmp_path)})


def test_missing_launcher_reason(monkeypatch):
    monkeypatch.setattr(mcp_registry.shutil, "which", lambda c: None)
    assert "Node.js" in mcp_registry.missing_launcher("npx") and "nodejs.org" in mcp_registry.missing_launcher("C:/x/npx.cmd")
    assert "uv" in mcp_registry.missing_launcher("uvx") and "Docker" in mcp_registry.missing_launcher("docker.exe")
    assert mcp_registry.missing_launcher("python") is None
    monkeypatch.setattr(mcp_registry.shutil, "which", lambda c: "C:/bin/" + c)
    assert mcp_registry.missing_launcher("npx") is None


def test_build_toolsets_skips_a_server_whose_launcher_is_missing(monkeypatch):
    class S:
        id, label, transport, command, args, env, builtin = "fs", "Files", "stdio", "npx", ["-y", "x"], {}, False

        def ready(self):
            return True

    class Store:
        def get(self, sid):
            return S()

    monkeypatch.setattr(mcp_registry.shutil, "which", lambda c: None)
    warnings: list[str] = []
    assert asyncio.run(mcp_registry.build_toolsets(Store(), ["fs"], "C:/ws", warnings)) == []
    assert warnings == ["MCP 'fs' skipped: needs Node.js, which is not installed on this PC - install Node.js 22 or newer from https://nodejs.org, then restart Agent Studio"]
