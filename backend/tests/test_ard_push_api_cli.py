"""Push to Dependency-Track (fake transport), the REST API, and the `python -m app.aibom` command."""
from __future__ import annotations

import base64
import io
import json
import runpy
import sys
import zipfile

import httpx
import pytest

from app.aibom import __main__ as CLI
from app.aibom import builder as B
from app.aibom.validate import validate_bom, validate_manifest
from app.ard import push as PU
from app.ard import signing
from app.ard.settings import DiscoverySettings, SettingsError
from tests.aibom_helpers import SECRETS, forbidden, seed

DT = "https://dt.acme.dev"


@pytest.fixture
def rich(state, tmp_path):
    seed(state, tmp_path)
    return state


@pytest.fixture
def cfg(rich):
    c = DiscoverySettings(rich.db)
    c.update({"push_url": DT, "push_api_key": "odt_SECRETAPIKEY", "push_project": "Agent Studio", "push_version": "9.9"})
    return c


class Server:
    def __init__(self, status=200, body=None, raise_exc=None):
        self.status, self.body, self.raise_exc, self.requests = status, body if body is not None else {"token": "tok-123"}, raise_exc, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_exc:
            raise self.raise_exc
        return httpx.Response(self.status, json=self.body) if not isinstance(self.body, str) else httpx.Response(self.status, text=self.body)


@pytest.fixture
def server(monkeypatch):
    s = Server()
    monkeypatch.setattr(PU, "TRANSPORT", httpx.MockTransport(s))
    PU._last.clear()
    return s


# ------------------------------------------------------------------------------------------------ push
def test_preview_shows_exactly_what_would_be_sent_and_sends_nothing(rich, cfg, server):
    preview, body = PU.plan(rich, cfg)
    assert preview["method"] == "PUT" and preview["url"] == f"{DT}/api/v1/bom" and preview["project"] == {"name": "Agent Studio", "version": "9.9", "autoCreate": True}
    assert preview["headers"]["X-Api-Key"] != "odt_SECRETAPIKEY" and "odt_SECRETAPIKEY" not in json.dumps(preview) and preview["redaction_level"] == "public"
    assert preview["bom"]["specVersion"] == "1.7" and preview["bom"]["components"] > 100 and len(preview["confirm"]) == 64
    sent = json.loads(body)
    raw = base64.b64decode(sent["bom"])
    assert sent["projectName"] == "Agent Studio" and sent["autoCreate"] is True and preview["bom"]["sha256"] == B.sha256_hex(raw) and preview["bom"]["bytes"] == len(raw)
    assert validate_bom(json.loads(raw))["ok"] and not server.requests


def test_send_requires_the_digest_the_user_reviewed(rich, cfg, server):
    preview, _ = PU.plan(rich, cfg)
    assert PU.plan(rich, cfg)[0]["confirm"] == preview["confirm"]                         # same system, same digest (timestamp is kept)
    with pytest.raises(SettingsError, match="preview again"):
        PU.send(rich, cfg, "0" * 64)
    assert not server.requests
    out = PU.send(rich, cfg, preview["confirm"])
    assert out == {"ok": True, "status": 200, "token": "tok-123", "sent_to": f"{DT}/api/v1/bom", "bom_sha256": preview["bom"]["sha256"]}
    req = server.requests[0]
    assert req.method == "PUT" and str(req.url) == f"{DT}/api/v1/bom" and req.headers["x-api-key"] == "odt_SECRETAPIKEY" and req.headers["content-type"] == "application/json"
    text = req.content.decode() + base64.b64decode(json.loads(req.content)["bom"]).decode()
    assert not any(n in text for n in forbidden("public")) and "odt_SECRETAPIKEY" not in text
    rich.mcp.add("late", "Late", "x", "stdio", "npx", ["-y", "zz@1.0.0"], None, {})        # the system changed after the review
    with pytest.raises(SettingsError, match="changed since you reviewed"):
        PU.send(rich, cfg, preview["confirm"])
    assert len(server.requests) == 1


def test_the_push_level_is_applied(rich, cfg, server):
    cfg.update({"push_level": "internal"})
    assert PU.plan(rich, cfg)[0]["redaction_level"] == "internal"
    cfg.update({"push_url": f"{DT}/api/v1/bom/", "push_version": ""})
    p, _ = PU.plan(rich, cfg)
    assert p["url"] == f"{DT}/api/v1/bom" and p["project"]["version"] == B.app_version()
    cfg.update({"push_project": ""})
    cfg.db.set_setting("ard_settings", json.dumps({**cfg.get(), "push_project": ""}))
    assert PU.plan(rich, cfg)[0]["project"]["name"] == "Agent Studio"


@pytest.mark.parametrize("status,body,match", [(401, {}, "rejected the API key"), (403, {}, "rejected the API key"), (302, {}, "redirect"),
                                               (500, "kaboom", "answered 500: kaboom"), (404, {"x": 1}, "answered 404")])
def test_server_errors_are_explained(rich, cfg, monkeypatch, status, body, match):
    PU._last.clear()
    monkeypatch.setattr(PU, "TRANSPORT", httpx.MockTransport(Server(status, body)))
    preview, _ = PU.plan(rich, cfg)
    with pytest.raises(PU.PushError, match=match):
        PU.send(rich, cfg, preview["confirm"])


def test_unreachable_server_and_unparseable_replies(rich, cfg, monkeypatch):
    PU._last.clear()
    monkeypatch.setattr(PU, "TRANSPORT", httpx.MockTransport(Server(raise_exc=httpx.ConnectError("refused"))))
    preview, _ = PU.plan(rich, cfg)
    with pytest.raises(PU.PushError, match="could not reach the server: ConnectError"):
        PU.send(rich, cfg, preview["confirm"])
    monkeypatch.setattr(PU, "TRANSPORT", httpx.MockTransport(Server(200, "plain text, not json")))
    assert PU.send(rich, cfg, preview["confirm"])["token"] is None


def test_push_is_refused_when_not_configured_or_the_bom_is_invalid(rich, state, monkeypatch):
    c = DiscoverySettings(state.db)
    with pytest.raises(SettingsError, match="Dependency-Track address"):
        PU.plan(rich, c)
    c.update({"push_url": DT})
    with pytest.raises(SettingsError, match="API key"):
        PU.plan(rich, c)
    c.update({"push_api_key": "k"})
    c.db.set_setting("ard_settings", json.dumps({**c.get(), "push_url": "http://remote.acme.dev"}))                 # a bad address stored behind our back
    with pytest.raises(SettingsError, match="https://"):
        PU.plan(rich, c)
    c.update({"push_url": DT})
    monkeypatch.setattr(PU, "validate_bom", lambda bom: {"ok": False, "error_count": 3, "errors": []})
    with pytest.raises(SettingsError, match="3 errors"):
        PU.plan(rich, c)


# ------------------------------------------------------------------------------------------------ API
@pytest.fixture
def api(rich, client):
    return client


def setup_publishing(api, **extra):
    body = {"publisher": "agents.acme-corp.dev", "enabled": True, "publish": {"agents": True, "mcp": True, "skills": True, "bom_system": True, "bom_agents": True}, **extra}
    r = api.put("/api/discovery/settings", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_bom_endpoints(api):
    assert [x["id"] for x in api.get("/api/aibom/levels").json()] == ["public", "internal", "full"]
    r = api.get("/api/aibom")
    assert r.status_code == 200 and r.headers["content-type"] == "application/vnd.cyclonedx+json" and r.headers["x-aibom-sha256"] == B.sha256_hex(r.content) and "X-AIBOM-SHA256" in r.headers["access-control-expose-headers"]
    assert r.json()["metadata"]["properties"][0]["value"] == "public" and "supplier" not in r.json()["metadata"]
    assert api.get("/api/aibom?level=full&scope=agent:engineer").json()["metadata"]["properties"][1]["value"] == "agent:engineer"
    d = api.get("/api/aibom/download?scope=system&level=internal")
    assert d.headers["content-disposition"] == 'attachment; filename="agent-studio-aibom-system-internal.cdx.json"'
    assert "agent-engineer-public" in api.get("/api/aibom/download?scope=agent:engineer").headers["content-disposition"]
    assert api.get("/api/aibom?level=secret").status_code == 422 and api.get("/api/aibom?scope=nope").status_code == 422
    assert api.get("/api/aibom/download?level=secret").status_code == 422
    sc = api.get("/api/aibom/scopes?level=public").json()
    assert {"ref": "agent:engineer", "name": "Engineer"} in sc["agents"] and sc["mcp"] and sc["models"] and sc["skills"]
    assert api.get("/api/aibom/scopes?level=bad").status_code == 422
    v = api.get("/api/aibom/validate").json()
    assert v["ok"] and v["schema"] == "CycloneDX 1.7" and api.get("/api/aibom/validate?scope=agent:engineer").json()["ok"]
    assert api.get("/api/aibom/validate?scope=zzz").status_code == 422 and api.get("/api/aibom/validate?level=x").status_code == 422


def test_secrets_never_leave_through_the_api(api):
    setup_publishing(api)
    for level in ("public", "internal", "full"):
        for scope in ("system", "agent:privops", "mcp:mine"):
            text = api.get(f"/api/aibom?level={level}&scope={scope}").text
            assert not any(n in text for n in forbidden(level)), (level, scope)


def test_settings_endpoints_and_token(api):
    s = api.get("/api/discovery/settings").json()
    assert s["enabled"] is False and s["not_ready"]
    assert api.put("/api/discovery/settings", json={"publisher": "example.com"}).status_code == 422
    out = setup_publishing(api, push_api_key="odt_k")
    assert out["not_ready"] == [] and out["has_push_key"] and "odt_k" not in json.dumps(out)
    assert api.put("/api/discovery/settings", json={"level": "internal"}).json()["not_ready"]
    t = api.post("/api/discovery/token").json()
    assert t["token"].startswith("asd_") and api.get("/api/discovery/settings").json()["has_token"] is True and t["token"] not in api.get("/api/discovery/settings").text
    assert api.delete("/api/discovery/token").json() == {"ok": True} and api.get("/api/discovery/settings").json()["has_token"] is False


def test_manifest_preview_validation_download_and_zip(api):
    m = api.get("/api/discovery/manifest").json()
    assert m["manifest"] is None and m["publishable"] is False and "publisher" in m["reasons"][0]
    assert api.get("/api/discovery/manifest/download").status_code == 422 and api.get("/api/discovery/export.zip").status_code == 422
    setup_publishing(api)
    m = api.get("/api/discovery/manifest").json()
    assert m["publishable"] is True and m["reasons"] == [] and m["validation"]["ok"] and m["entries"] and m["level"] == "public" and m["signed"] is False
    assert {"path": ".well-known/ard.json", "kind": "manifest"}.items() <= next(f for f in m["files"] if f["path"] == ".well-known/ard.json").items()
    assert validate_manifest(m["manifest"], "agents.acme-corp.dev")["ok"]
    api.put("/api/discovery/settings", json={"enabled": False})
    off = api.get("/api/discovery/manifest").json()
    assert off["publishable"] is False and off["reasons"] == ["publishing is switched off"] and off["manifest"]                       # still previewable
    d = api.get("/api/discovery/manifest/download")
    assert d.headers["content-disposition"] == 'attachment; filename="ard.json"' and d.json()["entries"]
    z = zipfile.ZipFile(io.BytesIO(api.get("/api/discovery/export.zip").content))
    names = set(z.namelist())
    assert {".well-known/ard.json", "index.html", "README.txt", "ard/bom/system.json"} <= names
    assert "agents.acme-corp.dev" in z.read("README.txt").decode() and validate_bom(json.loads(z.read("ard/bom/system.json")))["ok"]


def test_signing_key_endpoints(api):
    assert api.get("/api/discovery/keys").json()["exists"] is False
    k = api.post("/api/discovery/keys").json()                                           # no publisher yet: still creates the key
    assert k["exists"] and k["jwk"]["kty"] == "OKP" and "d" not in k["jwk"]
    info = api.get("/api/discovery/keys").json()
    assert info["jwk"] == k["jwk"] and info["scheme"] == signing.SCHEME_ID and "defines no signing procedure" in info["note"]
    setup_publishing(api, sign=True)
    assert api.get("/api/discovery/manifest").json()["signed"] is True
    assert api.post("/api/discovery/keys").json()["jwk"]["kid"] != k["jwk"]["kid"]
    assert api.delete("/api/discovery/keys").json() == {"exists": False} and api.get("/api/discovery/keys").json()["exists"] is False
    assert "no key exists" in api.get("/api/discovery/manifest").json()["problems"][0]


def test_discovery_server_endpoints_and_shutdown(api, client):
    assert api.get("/api/discovery/server").json()["running"] is False and api.get("/api/discovery/server").json()["not_ready"]
    assert api.post("/api/discovery/server/start").status_code == 409
    setup_publishing(api)
    st = api.post("/api/discovery/server/start", json={"port": 0}).json()
    try:
        assert st["running"] and httpx.get(st["local_url"] + ".well-known/ard.json").status_code == 200
        assert api.get("/api/discovery/server").json()["port"] == st["port"]
        out = api.put("/api/discovery/settings", json={"enabled": False}).json()                    # switching publishing off stops serving at once
        assert out["not_ready"] and api.get("/api/discovery/server").json()["running"] is False
    finally:
        api.post("/api/discovery/server/stop")
    assert api.post("/api/discovery/server/stop").json()["running"] is False
    api.put("/api/discovery/settings", json={"enabled": True})
    api.post("/api/discovery/server/start", json={"port": 0})
    assert api.post("/api/discovery/server/start").json()["running"] is True                              # already running
    api.put("/api/discovery/settings", json={"level": "internal"})
    assert api.get("/api/discovery/server").json()["running"] is False
    api.put("/api/discovery/settings", json={"level": "public", "enabled": True})
    api.post("/api/discovery/server/start", json={"port": 0})
    api.put("/api/discovery/settings", json={"level": "internal", "level_confirmed": True})
    api.post("/api/discovery/token")
    api.post("/api/discovery/server/start", json={"port": 0})
    assert api.get("/api/discovery/server").json()["running"] is True
    api.delete("/api/discovery/token")                                                                   # removing the token stops a non-public server
    assert api.get("/api/discovery/server").json()["running"] is False


def test_shutdown_stops_the_server_and_tolerates_apps_without_discovery(client):
    from app.ard import api as A
    setup_publishing(client)
    client.post("/api/discovery/server/start", json={"port": 0})
    assert client.app.state.discovery.server.running()
    A.shutdown(client.app)
    assert not client.app.state.discovery.server.running()

    class Bare:
        class state:
            pass
    A.shutdown(Bare)


def test_push_endpoints(api, monkeypatch):
    assert api.post("/api/aibom/push/preview").status_code == 422
    setup_publishing(api, push_url=DT, push_api_key="odt_k")
    srv = Server()
    monkeypatch.setattr(PU, "TRANSPORT", httpx.MockTransport(srv))
    PU._last.clear()
    prev = api.post("/api/aibom/push/preview").json()
    assert prev["url"] == f"{DT}/api/v1/bom" and not srv.requests
    assert api.post("/api/aibom/push", json={"confirm": "wrong"}).status_code == 409
    ok = api.post("/api/aibom/push", json={"confirm": prev["confirm"]})
    assert ok.status_code == 200 and ok.json()["token"] == "tok-123" and len(srv.requests) == 1
    srv.status = 403
    assert api.post("/api/aibom/push", json={"confirm": prev["confirm"]}).status_code == 502


# ------------------------------------------------------------------------------------------------ CLI
def test_cli_writes_a_valid_bom_file(tmp_path, capsys):
    out = tmp_path / "bom.json"
    assert CLI.main(["--level", "internal", "--out", str(out), "--validate"]) == 0
    doc = json.loads(out.read_text("utf-8"))
    assert doc["bomFormat"] == "CycloneDX" and validate_bom(doc)["ok"]
    err = capsys.readouterr().err
    assert "wrote" in err and "valid CycloneDX 1.7" in err
    assert CLI.main(["--out", str(out), "--scope", "agent:engineer", "--random-serial"]) == 0
    assert json.loads(out.read_text("utf-8"))["metadata"]["properties"][1]["value"] == "agent:engineer"


def test_cli_stdout_and_error_codes(tmp_path, capsysbinary, monkeypatch):
    assert CLI.main(["--level", "public"]) == 0
    assert json.loads(capsysbinary.readouterr().out)["specVersion"] == "1.7"
    assert CLI.main(["--scope", "nope"]) == 2
    assert b"unknown scope" in capsysbinary.readouterr().err
    monkeypatch.setattr("app.aibom.validate.validate_bom", lambda doc: {"ok": False, "errors": [{"path": "/x", "message": "bad"}] * 25, "error_count": 25})
    assert CLI.main(["--out", str(tmp_path / "x.json"), "--validate"]) == 1
    assert capsysbinary.readouterr().err.count(b"invalid:") == 20
    with pytest.raises(SystemExit) as e:
        CLI.main(["--level", "bogus"])
    assert e.value.code == 2


def test_cli_module_entry_point(tmp_path, monkeypatch):
    out = tmp_path / "m.json"
    monkeypatch.setattr(sys, "argv", ["app.aibom", "--out", str(out)])
    sys.modules.pop("app.aibom.__main__", None)
    with pytest.raises(SystemExit) as e:
        runpy.run_module("app.aibom", run_name="__main__")
    assert e.value.code == 0 and json.loads(out.read_text("utf-8"))["bomFormat"] == "CycloneDX"
    assert SECRETS["openai_key"] not in out.read_text("utf-8")
