"""Apps: the preview (CSP, token, proxy to the agents), the server-app container, and the separate preview server."""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import types

import httpx
import pytest
from fastapi.testclient import TestClient

from app.apiaccess.keys import ApiKeys
from app.apps import runner as R
from app.apps.server import AppsServer, create_apps_app
from app.apps.store import AppError, AppStore
from app.db import Database
from app.harness.sandbox import SandboxUnavailable


def run(c):
    return asyncio.run(c)


class Agents:
    def __init__(self, *ids):
        self.items = {i: types.SimpleNamespace(id=i, name=i, description="", api_access=True) for i in ids}

    def get(self, i):
        return self.items.get(i)

    def list(self):
        return list(self.items.values())


class Docker:
    """A fake `docker`: records every call, answers from `replies[verb]` (a CompletedProcess or an exception)."""

    def __init__(self, **replies):
        self.calls, self.replies = [], replies

    def __call__(self, args, timeout=30):
        self.calls.append(list(args))
        r = self.replies.get(args[0], subprocess.CompletedProcess(args, 0, "", ""))
        if isinstance(r, BaseException):
            raise r
        return r

    def verbs(self):
        return [c[0] for c in self.calls]


def cp(out="", err="", rc=0):
    return subprocess.CompletedProcess([], rc, out, err)


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "t.db")
    return AppStore(db, home=tmp_path / "home", keys=ApiKeys(db), agents=Agents("coder"))


@pytest.fixture
def now():
    return types.SimpleNamespace(t=1_000_000.0)


@pytest.fixture
def runner(store, tmp_path, now):
    return R.AppRunner(store, docker=Docker(), home=tmp_path / "home", clock=lambda: now.t, frame_ancestors=["http://127.0.0.1:3100"])


@pytest.fixture
def app(store):
    a = store.create("Hello", "static-chat", "d", "coder")
    return a


# ------------------------------------------------------------------------------------------------ the token
def test_token_round_trip_expiry_and_tampering(runner, app, now):
    t = runner.token(app["id"])
    assert runner.verify_token(app["id"], t) and not runner.verify_token("other", t)
    exp, sig = t.split(".")
    assert not runner.verify_token(app["id"], f"{exp}.{'0' * 40}") and not runner.verify_token(app["id"], f"{int(exp) + 5}.{sig}")
    for bad in ("", "x", "a.b", None, "12"):
        assert not runner.verify_token(app["id"], bad)
    now.t += R.TOKEN_TTL + 1
    assert not runner.verify_token(app["id"], t)
    assert runner.verify_token(app["id"], runner.token(app["id"]))
    assert runner._secret() == runner._secret()


# ------------------------------------------------------------------------------------------------ the policy header
def test_csp_follows_the_manifest():
    m = {"permissions": {"network": "none", "allow": []}}
    c = R.csp(m, "N", ["http://127.0.0.1:3100", "http://localhost:3100"])
    assert "default-src 'none'" in c and "script-src 'self' 'nonce-N'" in c and "connect-src 'self';" in c and "frame-ancestors http://127.0.0.1:3100 http://localhost:3100" in c
    assert "frame-ancestors 'none'" in R.csp(m, "N", [])
    assert "connect-src 'self' https://a.com https://b.com:8443;" in R.csp({"permissions": {"network": "allowlist", "allow": ["https://a.com", "https://b.com:8443"]}}, "N", [])
    assert "connect-src 'self';" in R.csp({"permissions": {"network": "allowlist", "allow": []}}, "N", [])
    assert "connect-src 'self' https:;" in R.csp({"permissions": {"network": "open", "allow": []}}, "N", [])


# ------------------------------------------------------------------------------------------------ serving the page
def test_the_index_gets_the_address_and_a_token_and_a_nonce(runner, app, store):
    code, h, body = runner.serve(app["id"], "", "http://127.0.0.1:8767")
    text = body.decode()
    assert code == 200 and h["Content-Type"].startswith("text/html") and h["X-Content-Type-Options"] == "nosniff" and h["Access-Control-Allow-Origin"] == "null"
    nonce = h["Content-Security-Policy"].split("'nonce-")[1].split("'")[0]
    assert f'<script nonce="{nonce}">window.AGENT_STUDIO=' in text and f'/apps-run/{app["id"]}/_agent' in text and text.index("AGENT_STUDIO") < text.index("agent-client.js")
    cfg = json.loads(text.split("window.AGENT_STUDIO=")[1].split(";</script>")[0])
    assert cfg["base"] == f"http://127.0.0.1:8767/apps-run/{app['id']}/_agent" and runner.verify_token(app["id"], cfg["token"])
    assert runner.serve(app["id"], "index.html", "http://x")[1]["Content-Security-Policy"] != h["Content-Security-Policy"]          # a fresh nonce each time
    store.write(app["id"], "public/index.html", "<p>no head</p>")
    assert runner.serve(app["id"], "/", "http://x/")[2].decode().startswith("<script nonce=")
    store.write(app["id"], "public/index.html", "<HEAD><title>x</title></HEAD>")
    assert runner.serve(app["id"], "", "http://x")[2].decode().lower().startswith("<head><script nonce=")


def test_other_files_are_served_with_the_right_type_and_without_the_token(runner, app, store):
    code, h, body = runner.serve(app["id"], "app.js", "http://x")
    assert code == 200 and h["Content-Type"].startswith("text/javascript") and b"AGENT_STUDIO" not in body
    assert runner.serve(app["id"], "style.css", "http://x")[1]["Content-Type"].startswith("text/css")
    (store.root(app["id"]) / "public" / "logo.png").write_bytes(b"\x89PNG\x00\x01")
    code, h, body = runner.serve(app["id"], "logo.png", "http://x")
    assert code == 200 and h["Content-Type"] == "image/png" and body == b"\x89PNG\x00\x01"
    store.write(app["id"], "public/data.weird", "text")
    assert runner.serve(app["id"], "data.weird", "http://x")[1]["Content-Type"] == "application/octet-stream"
    store.write(app["id"], "public/notes.txt", "t")
    assert runner.serve(app["id"], "notes.txt", "http://x")[1]["Content-Type"].startswith("text/plain")


@pytest.mark.parametrize("path", ["missing.html", "../app.json", "../../etc/passwd", "a/../../app.json", ".git/config"])
def test_files_outside_public_are_not_served(runner, app, path):
    code, h, body = runner.serve(app["id"], path, "http://x")
    assert code == 404 and h["Content-Type"].startswith("text/plain")


def test_an_unknown_app_is_a_404(runner):
    assert runner.serve("nope", "", "http://x")[0] == 404


# ------------------------------------------------------------------------------------------------ the proxy
def streamed(body: bytes, ctype: str = "application/json"):
    """A response whose body is still a stream (what a real transport gives), so the proxy can pass it on piece by piece."""
    return httpx.Response(200, stream=httpx.ByteStream(body), headers={"content-type": ctype})


def with_http(runner, handler):
    runner.http = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)
    runner.api_url = "http://studio"


def chat(model="coder", **extra):
    return json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}], **extra}).encode()


def collect(res):
    status, ctype, it = res

    async def go():
        return b"".join([c async for c in it])
    return status, ctype, run(go())


def test_the_proxy_forwards_with_the_apps_own_key(runner, app, store):
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), auth=request.headers["authorization"], body=json.loads(request.content))
        return streamed(b'data: {"x":1}\n\ndata: [DONE]\n\n', "text/event-stream")
    with_http(runner, handler)
    status, ctype, body = collect(run(runner.forward(app["id"], runner.token(app["id"]), "v1/chat/completions", chat(stream=True), "http://studio/")))
    assert status == 200 and ctype == "text/event-stream" and b"[DONE]" in body
    key = store.ensure_key(app["id"])
    assert seen["url"] == "http://studio/v1/chat/completions" and seen["auth"] == f"Bearer {key}" and seen["body"]["model"] == "coder" and seen["body"]["stream"] is True


@pytest.mark.parametrize("token_ok,rest,body,url,status,text", [
    (False, "v1/chat/completions", chat(), "http://s", 401, "expired"),
    (True, "v1/models", chat(), "http://s", 404, "only call"),
    (True, "v1/chat/completions", b"x" * 200_001, "http://s", 413, "too large"),
    (True, "v1/chat/completions", b"{nope", "http://s", 400, "not valid JSON"),
    (True, "v1/chat/completions", b"[1]", "http://s", 403, "may only call: coder"),
    (True, "v1/chat/completions", chat("other"), "http://s", 403, "may only call: coder"),
    (True, "v1/chat/completions", chat(), "", 503, "Start the app"),
])
def test_the_proxy_refuses_what_it_should(runner, app, token_ok, rest, body, url, status, text):
    with_http(runner, lambda r: httpx.Response(200))
    tok = runner.token(app["id"]) if token_ok else "1.bad"
    st, ctype, out = collect(run(runner.forward(app["id"], tok, rest, body, url)))
    assert st == status and ctype == "application/json" and text in json.loads(out)["error"]["message"]


def test_the_proxy_refuses_an_app_with_no_agents_a_revoked_key_and_a_broken_manifest(runner, store, app):
    with_http(runner, lambda r: httpx.Response(200))
    store.set_manifest(app["id"], {"agents": []})
    assert collect(run(runner.forward(app["id"], runner.token(app["id"]), "v1/chat/completions", chat(), "http://s")))[0] == 403
    store.set_manifest(app["id"], {"agents": [{"id": "coder"}]})
    store.ensure_key(app["id"])
    store.revoke_key(app["id"])
    st, _, out = collect(run(runner.forward(app["id"], runner.token(app["id"]), "v1/chat/completions", chat(), "http://s")))
    assert st == 403 and "revoked" in json.loads(out)["error"]["message"]
    (store.root(app["id"]) / "app.json").write_text("{bad")
    assert collect(run(runner.forward(app["id"], runner.token(app["id"]), "v1/chat/completions", chat(), "http://s")))[0] == 400


def test_the_proxy_reports_an_unreachable_studio(runner, app):
    def boom(request):
        raise httpx.ConnectError("nope", request=request)
    with_http(runner, boom)
    st, _, out = collect(run(runner.forward(app["id"], runner.token(app["id"]), "v1/chat/completions", chat(), "http://s")))
    assert st == 502 and "could not be reached" in json.loads(out)["error"]["message"]


def test_the_default_http_client_is_httpx(runner, app, monkeypatch):
    made = {}

    class Fake(httpx.AsyncClient):
        def __init__(self, **kw):
            made.update(kw)
            super().__init__(transport=httpx.MockTransport(lambda r: streamed(b"{}")))
    monkeypatch.setattr(httpx, "AsyncClient", Fake)
    runner.http = None
    assert collect(run(runner.forward(app["id"], runner.token(app["id"]), "v1/chat/completions", chat(), "http://s")))[0] == 200 and made["timeout"].connect == 5


# ------------------------------------------------------------------------------------------------ running
def test_a_static_app_has_nothing_to_start(runner, app):
    out = runner.start(app["id"], "http://127.0.0.1:8765/", "http://127.0.0.1:8767/")
    assert out["kind"] == "static" and out["status"] == "ready" and out["url"] == f"http://127.0.0.1:8767/apps-run/{app['id']}/" and runner.api_url == "http://127.0.0.1:8765"
    assert runner.stop(app["id"]) == {"status": "ready"} and runner.status(app["id"], "http://127.0.0.1:8767")["url"].endswith(f"/apps-run/{app['id']}/") and runner.status(app["id"])["url"] == ""
    assert "no process" in runner.logs(app["id"])["logs"]
    assert runner.docker.calls == []


@pytest.fixture
def server_app(store, runner):
    store.set_config({"allow_server_apps": True})
    a = store.create("Srv", "node-api", agent_id="coder")
    store.set_manifest(a["id"], {"env": [{"name": "TOKEN"}]})
    store.set_env(a["id"], {"TOKEN": "tok-123"})
    return a


def test_a_server_app_needs_the_switch_and_a_server_file(store, runner):
    a = store.create("Srv", "python-api")
    with pytest.raises(AppError, match="Allow that in Settings"):
        runner.start(a["id"], "http://127.0.0.1:8765", "http://x")
    store.set_config({"allow_server_apps": True})
    store.delete_file(a["id"], "server/main.py")
    with pytest.raises(AppError, match="server file 'server/main.py' is missing"):
        runner.start(a["id"], "http://127.0.0.1:8765", "http://x")
    store.set_config({"enabled": False})
    with pytest.raises(AppError, match="switched off"):
        runner.start(a["id"], "http://127.0.0.1:8765", "http://x")


def test_a_server_app_runs_hardened_with_its_key_in_a_temporary_env_file(runner, store, server_app, tmp_path):
    aid = server_app["id"]
    seen, calls = {}, []

    def fake(args, timeout=30):
        calls.append(list(args))
        if args[0] == "run":
            seen["env"] = open(args[args.index("--env-file") + 1], encoding="utf-8").read()
        return cp("abc123\n")
    runner.docker = fake
    runner.free = lambda pref: 18123
    out = runner.start(aid, "http://127.0.0.1:8765/", "http://x")
    assert out == dict(kind="node", status="running", url="http://127.0.0.1:18123/", port=18123, detail=out["detail"])
    run_call = next(c for c in calls if c[0] == "run")
    flat = " ".join(run_call)
    for must in ("--cap-drop ALL", "--security-opt no-new-privileges", "--read-only", "--user 1000:1000", "-p 127.0.0.1:18123:8080", "--network bridge", "--pids-limit 256",
                 "--memory 1g", "--cpus 2", "readonly", "node:22-alpine node server/index.js", "--add-host host.docker.internal:host-gateway"):
        assert must in flat
    assert calls[0][:2] == ["rm", "-f"] and "agent-studio-app-" + aid in run_call
    key = store.ensure_key(aid)
    assert f"AGENT_STUDIO_API_KEY={key}" in seen["env"] and "AGENT_STUDIO_BASE_URL=http://host.docker.internal:8765/v1" in seen["env"] and "TOKEN=tok-123" in seen["env"] and "PORT=8080" in seen["env"]
    assert not (tmp_path / "home" / "apps-run" / f"{aid}.env").exists()
    assert key not in " ".join(run_call) and "tok-123" not in " ".join(run_call)


def test_a_failing_container_start_is_explained_and_cleaned_up(runner, store, server_app, tmp_path):
    runner.free = lambda pref: 18200
    runner.docker = Docker(run=cp("", "Unable to find image\nboom", 125))
    with pytest.raises(AppError, match="did not start: Unable to find image"):
        runner.start(server_app["id"], "http://127.0.0.1:8765", "http://x")
    assert not list((tmp_path / "home" / "apps-run").glob("*.env"))
    runner.docker = Docker(rm=SandboxUnavailable("docker CLI not found"))
    with pytest.raises(AppError, match="Docker is needed to run server apps: docker CLI not found"):
        runner.start(server_app["id"], "http://127.0.0.1:8765", "http://x")
    assert not list((tmp_path / "home" / "apps-run").glob("*.env"))


def test_env_values_with_line_breaks_are_refused(runner, store, server_app):
    store._save_secret(server_app["id"], {"env": {"TOKEN": "a\nb"}})
    runner.docker = Docker()
    with pytest.raises(AppError, match="line breaks"):
        runner.start(server_app["id"], "http://127.0.0.1:8765", "http://x")
    assert runner.docker.calls == []


def test_a_server_app_without_agents_runs_without_a_key(runner, store):
    store.set_config({"allow_server_apps": True})
    a = store.create("NoAgent", "python-api")
    runner.free = lambda pref: 18300
    runner.docker = Docker()
    runner.start(a["id"], "http://127.0.0.1:8765", "http://x")
    assert "python:3.12-slim python server/main.py" in " ".join(next(c for c in runner.docker.calls if c[0] == "run"))


def test_status_stop_logs_and_stop_all(runner, store, server_app):
    aid = server_app["id"]
    runner.docker = Docker(inspect=cp("running\n"), port=cp("127.0.0.1:18123\n"))
    assert runner.status(aid) == dict(kind="node", status="running", detail="container is running", port=18123, url="http://127.0.0.1:18123/")
    runner.docker = Docker(inspect=cp("running\n"), port=cp("", "no", 1))
    assert "url" not in runner.status(aid)
    runner.docker = Docker(inspect=cp("exited\n"))
    assert runner.status(aid)["status"] == "stopped" and "exited" in runner.status(aid)["detail"]
    runner.docker = Docker(inspect=cp("", "no such container", 1))
    assert runner.status(aid) == dict(kind="node", status="stopped")
    runner.docker = Docker(inspect=SandboxUnavailable("Docker daemon not running"))
    assert runner.status(aid)["status"] == "unavailable"
    runner.docker = Docker(logs=cp("line1\n", "warn\n"))
    assert runner.logs(aid, 5)["logs"] == "line1\nwarn\n" and runner.docker.calls[0][:3] == ["logs", "--tail", "5"]
    assert runner.logs(aid, 99999)["logs"] and runner.docker.calls[-1][2] == "1000" and runner.logs(aid, 0) and runner.docker.calls[-1][2] == "1"
    runner.docker = Docker(logs=cp("", "x", 1))
    assert "no container" in runner.logs(aid)["logs"]
    runner.docker = Docker()
    assert runner.stop(aid) == {"status": "stopped"} and runner.docker.calls[0] == ["rm", "-f", f"agent-studio-app-{aid}"]
    runner.docker = Docker(ps=cp("id1\nid2\n"))
    runner.stop_all()
    assert runner.docker.calls[-1] == ["rm", "-f", "id1", "id2"]
    runner.docker = Docker(ps=cp(""))
    runner.stop_all()
    assert runner.docker.verbs() == ["ps"]
    runner.docker = Docker(ps=cp("", "x", 1))
    runner.stop_all()
    assert runner.docker.verbs() == ["ps"]
    runner.docker = Docker(ps=SandboxUnavailable("no docker"))
    runner.stop_all()


def test_free_port_skips_busy_ports_and_gives_up():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        s.listen()
        assert R.free_port(busy) != busy
    assert R.free_port(40000) >= 40000


def test_free_port_lets_the_os_choose_when_the_whole_stretch_is_reserved(monkeypatch):
    """Windows reserves 100-port blocks, so every port from `preferred` on can refuse a bind (this made the test above flaky)."""
    real = socket.socket

    class Reserved:
        def __init__(self):
            self.s = real()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.s.close()
            return False

        def bind(self, addr):
            if addr[1] != 0:
                raise OSError("reserved")
            self.s.bind(addr)

        def getsockname(self):
            return self.s.getsockname()
    monkeypatch.setattr(R.socket, "socket", lambda: Reserved())
    assert R.free_port(50000) > 0


def test_free_port_gives_up_when_nothing_is_free(monkeypatch):
    class Never:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def bind(self, addr):
            raise OSError("busy")
    monkeypatch.setattr(R.socket, "socket", lambda: Never())
    with pytest.raises(AppError, match="no free port"):
        R.free_port(30000)


# ------------------------------------------------------------------------------------------------ the preview server
def test_the_preview_app_serves_and_forwards(runner, app, store):
    with_http(runner, lambda r: streamed(b'{"ok":true}'))
    c = TestClient(create_apps_app(runner), base_url="http://127.0.0.1:8767")
    r = c.get(f"/apps-run/{app['id']}/")
    assert r.status_code == 200 and "AGENT_STUDIO" in r.text and "frame-ancestors http://127.0.0.1:3100" in r.headers["content-security-policy"]
    tok = json.loads(r.text.split("window.AGENT_STUDIO=")[1].split(";</script>")[0])["token"]
    assert c.head(f"/apps-run/{app['id']}/").content == b"" and c.get(f"/apps-run/{app['id']}/nope.js").status_code == 404
    pre = c.options(f"/apps-run/{app['id']}/_agent/v1/chat/completions")
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == "null" and "X-App-Token" in pre.headers["access-control-allow-headers"]
    ok = c.post(f"/apps-run/{app['id']}/_agent/v1/chat/completions", content=chat(), headers={"X-App-Token": tok})
    assert ok.status_code == 200 and ok.json() == {"ok": True} and ok.headers["access-control-allow-origin"] == "null" and ok.headers["cache-control"] == "no-store"
    assert c.post(f"/apps-run/{app['id']}/_agent/v1/chat/completions", content=chat()).status_code == 401


def test_the_preview_app_only_answers_to_the_loopback_host(runner, app):
    c = TestClient(create_apps_app(runner), base_url="http://evil.example.com")
    assert c.get(f"/apps-run/{app['id']}/").status_code == 404
    assert c.options(f"/apps-run/{app['id']}/_agent/v1/chat/completions").status_code == 404
    assert c.post(f"/apps-run/{app['id']}/_agent/v1/chat/completions", content=chat()).status_code == 404
    assert TestClient(create_apps_app(runner), base_url="http://localhost:8767").get(f"/apps-run/{app['id']}/").status_code == 200
    assert TestClient(create_apps_app(runner), base_url="http://127.0.0.1:8767").get("/").status_code == 404
    assert TestClient(create_apps_app(runner), base_url="http://[::1]:8767").get(f"/apps-run/{app['id']}/").status_code == 404


def test_the_preview_server_starts_reports_and_stops(runner, app):
    srv = AppsServer(runner)
    assert srv.status() == dict(running=False, host="127.0.0.1", port=None, url="", error="") and srv.url() == ""
    url = srv.ensure(0)
    try:
        assert url.startswith("http://127.0.0.1:") and srv.running() and srv.ensure(0) == url and srv.status()["port"] == srv.port()
        assert httpx.get(f"{url}/apps-run/{app['id']}/").status_code == 200
    finally:
        assert srv.stop()["running"] is False
    srv.stop()


def test_the_preview_server_explains_a_busy_port(runner):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        srv = AppsServer(runner)
        with pytest.raises(RuntimeError, match="port already in use|could not start"):
            srv.ensure(s.getsockname()[1])
        assert srv.running() is False and srv.error
