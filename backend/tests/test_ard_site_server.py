"""The published site (ARD manifest + BOMs + skills) and the read-only discovery server that serves it."""
from __future__ import annotations

import json
import socket
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.aibom.validate import validate_bom, validate_manifest
from app.ard import server as SV
from app.ard import signing
from app.ard import site as ST
from app.ard.settings import DiscoverySettings, SettingsError
from tests.aibom_helpers import forbidden, seed

PUB = "agents.acme-corp.dev"
ALL = {"agents": True, "mcp": True, "skills": True, "bom_system": True, "bom_agents": True, "bom_mcp": True, "bom_models": True}


@pytest.fixture
def rich(state, tmp_path):
    seed(state, tmp_path)
    return state


def make(state, tmp_path, **patch):
    cfg = DiscoverySettings(state.db)
    cfg.update({"publisher": PUB, "enabled": True, "publish": ALL, **patch})
    signer = signing.Signer(tmp_path / "k.pem")
    return cfg, ST.Publisher(state, cfg, signer), signer


def by_id(site, ident):
    return next(e for e in site.manifest["entries"] if e["identifier"].endswith(ident))


# ------------------------------------------------------------------------------------------------ site
def test_site_has_every_enabled_resource_and_validates(rich, tmp_path):
    cfg, pub, signer = make(rich, tmp_path)
    site = pub.build()
    kinds = {}
    for e in site.manifest["entries"]:
        kinds.setdefault(e["identifier"].split(":")[4], []).append(e)
    assert set(kinds) == {"agent", "mcp", "skill", "bom"} and site.publisher == PUB and site.base_url == f"https://{PUB}" and site.level == "public"
    v = pub.validate(site)
    assert v["ok"] and v["manifest"]["warnings"] == [] and all(b["ok"] for b in v["boms"].values()) and len(v["boms"]) > 20
    assert validate_manifest(site.manifest, PUB)["ok"]
    # types and URLs
    agent = by_id(site, "agent:engineer")
    assert agent["type"] == ST.AGENT_TYPE and "data" in agent and "url" not in agent and agent["data"]["tier"] == "pro" and "engineer" in agent["data"]["id"]
    assert by_id(site, "mcp:filesystem")["type"] == ST.MCP_TYPE and by_id(site, "mcp:filesystem")["data"]["packages"] == ["pkg:npm/%40modelcontextprotocol/server-filesystem@2.4.0"]
    skill = next(e for e in kinds["skill"])
    assert skill["type"] == "application/agent-skills+md" and skill["url"] == f"https://{PUB}/ard/skills/{skill['displayName']}/SKILL.md"
    system = by_id(site, "bom:system")
    assert system["type"] == ST.CYCLONEDX_TYPE and system["url"] == f"https://{PUB}/ard/bom/system.json"
    # attestations carry the digest of the very bytes that are served
    for e in site.manifest["entries"]:
        for a in e["trustManifest"].get("attestations", []):
            path = a["uri"].removeprefix(f"https://{PUB}/")
            assert a["digest"] == f"sha256:{site.files[path].sha256}" and a["type"] == "SBOM" and a["mediaType"] == ST.CYCLONEDX_TYPE
    assert by_id(site, "agent:engineer")["trustManifest"]["attestations"][0]["uri"].endswith("ard/bom/agent/engineer.json")
    assert by_id(site, "agent:engineer")["trustManifest"]["trustSchema"]["verificationMethods"] == ["none"] and "signature" not in agent["trustManifest"]
    assert all(2 <= len(e["representativeQueries"]) <= 5 for e in site.manifest["entries"])
    # listing, files, index
    assert {x.kind for x in site.files.values()} == {"manifest", "bom", "skill", "page"} and ".well-known/ai-catalog.json" in site.files
    assert site.files[".well-known/ard.json"].data == site.files[".well-known/ai-catalog.json"].data
    assert 'rel="ard" href="/.well-known/ard.json"' in site.files["index.html"].data.decode()
    assert len(site.entries) == len(site.manifest["entries"]) and site.entries[0]["identifier"].startswith("urn:air:")
    assert all(f.digest == "sha256:" + f.sha256 for f in site.files.values())


def test_nothing_private_is_published_at_the_public_level(rich, tmp_path):
    cfg, pub, _ = make(rich, tmp_path)
    site = pub.build()
    blob = "".join(a.data.decode() for a in site.files.values())
    for needle in forbidden("public"):
        assert needle not in blob, needle
    assert not any(e["displayName"] == "private-skill" for e in site.manifest["entries"]) and "private-skill" not in blob
    assert "Private Ops" in blob                                                                                              # the agent is listed ...
    assert "Deploys the private stack" not in blob                                                                            # ... but not its free-text description


def test_internal_level_adds_user_skills_and_signing_covers_every_entry(rich, tmp_path):
    cfg, pub, signer = make(rich, tmp_path, level="internal", level_confirmed=True, sign=True)
    cfg.new_token()
    site = pub.build()
    assert any(e["displayName"] == "private-skill" for e in site.manifest["entries"]) and "ard/skills/private-skill/SKILL.md" in site.files
    assert "PRIVATE-SKILL-BODY" in site.files["ard/skills/private-skill/SKILL.md"].data.decode()                                # a skill file IS the skill text: the user chose to publish skills
    assert site.problems and "no key exists" in site.problems[0] and not site.signed
    jwk = signer.generate()
    site = pub.build()
    assert site.signed and site.problems == [] and ".well-known/jwks.json" in site.files
    assert json.loads(site.files[".well-known/jwks.json"].data)["keys"] == [jwk]
    for e in site.manifest["entries"]:
        assert signing.verify(e["trustManifest"], jwk), e["identifier"]
        assert e["trustManifest"]["trustSchema"]["jwksUri"] == f"https://{PUB}/.well-known/jwks.json"
    assert pub.validate(site)["ok"]
    tampered = json.loads(json.dumps(site.manifest["entries"][0]["trustManifest"]))
    tampered["identity"] = "https://evil.dev"
    assert not signing.verify(tampered, jwk)


def test_only_chosen_types_are_published(rich, tmp_path):
    cfg, pub, _ = make(rich, tmp_path, publish={t: t == "skills" for t in ALL})
    site = pub.build()
    assert {e["identifier"].split(":")[4] for e in site.manifest["entries"]} == {"skill"} and not any(f.kind == "bom" for f in site.files.values())
    assert all("attestations" not in e["trustManifest"] for e in site.manifest["entries"])
    cfg.update({"publish": {"skills": False, "bom_system": True}})
    site = pub.build()
    assert [e["identifier"] for e in site.manifest["entries"]] == [f"urn:air:{PUB}:agent-studio:bom:system"]
    cfg.update({"publish": {"bom_system": False, "bom_models": True}})
    assert all(":bom:model:" in e["identifier"] for e in pub.build().manifest["entries"])
    cfg.update({"publish": {"bom_models": False}})
    assert pub.build().manifest["entries"] == []


def test_a_publisher_is_required_and_no_fake_domain_is_ever_used(rich, tmp_path):
    cfg = DiscoverySettings(rich.db)
    with pytest.raises(SettingsError, match="set the publisher domain"):
        ST.Publisher(rich, cfg, signing.Signer(tmp_path / "k.pem")).build()


def test_unchanged_boms_keep_their_exact_bytes_across_rebuilds(rich, tmp_path):
    cfg, pub, _ = make(rich, tmp_path)
    first = pub.build()
    second = pub.build(first)
    same = [p for p, a in first.files.items() if a.kind == "bom" and second.files[p] is a]
    assert len(same) == len([1 for a in first.files.values() if a.kind == "bom"])
    cfg.update({"level": "internal", "level_confirmed": True})
    cfg.new_token()
    third = pub.build(first)                                                                  # a different level never reuses another level's bytes
    assert all(third.files[p] is not a for p, a in first.files.items() if a.kind == "bom")
    rich.mcp.add("fresh", "Fresh", "x", "stdio", "npx", ["-y", "zz@1.0.0"], None, {})
    cfg.update({"level": "public"})
    fourth = pub.build(first)
    assert fourth.files["ard/bom/system.json"] is not first.files["ard/bom/system.json"] and fourth.files["ard/bom/mcp/filesystem.json"] is first.files["ard/bom/mcp/filesystem.json"]


def test_entries_without_a_description_get_a_fallback_query(rich, tmp_path):
    cfg, pub, _ = make(rich, tmp_path, publish={"agents": True})
    e = by_id(pub.build(), "agent:custom_model")
    assert e["representativeQueries"][0] == "Use the Custom Model agent" and "description" not in e


def test_helpers():
    assert ST.first_sentence("Does a thing. And more.") == "Does a thing" and ST.first_sentence("x" * 200).endswith("…") and len(ST.first_sentence("x" * 200)) == 140
    assert ST.first_sentence("") == ""
    props = ST.pmap({"properties": [{"name": "agentstudio:a", "value": "[1, 2]"}, {"name": "agentstudio:b", "value": "[broken"}, {"name": "other:c", "value": "x"},
                                    {"name": "agentstudio:d", "value": "plain"}]})
    assert props == {"a": [1, 2], "b": "[broken", "d": "plain"}
    html = ST.Publisher.index_html(ST.Site("public", "x.dev", "https://x.dev", {}, {}, [{"identifier": "urn:air:x.dev:<b>", "displayName": "<script>", "type": "a/b"}]))
    assert "&lt;script&gt;" in html and "<script>" not in html and "&lt;b&gt;" in html


def test_site_cache_rebuilds_on_ttl_force_and_settings_change(rich, tmp_path):
    cfg, pub, signer = make(rich, tmp_path, publish={"skills": True})
    cache = ST.SiteCache(pub, ttl=3600)
    a = cache.get()
    assert cache.get() is a and cache.get(force=True) is not a
    b = cache.get()
    cfg.update({"publish": {"agents": True}})
    c = cache.get()
    assert c is not b
    signer.generate()
    assert cache.get() is not c                                                               # the key's existence is part of the cache key
    short = ST.SiteCache(pub, ttl=0)
    assert short.get() is not short.get()


# ------------------------------------------------------------------------------------------------ discovery app
@pytest.fixture
def web(rich, tmp_path):
    cfg, pub, signer = make(rich, tmp_path)
    cache = ST.SiteCache(pub)
    return cfg, cache, TestClient(SV.create_discovery_app(cache, cfg), base_url="http://ard.acme-corp.dev")


def test_nothing_is_served_until_publishing_is_ready(rich, tmp_path):
    cfg, pub, _ = make(rich, tmp_path, enabled=False)
    c = TestClient(SV.create_discovery_app(ST.SiteCache(pub), cfg))
    assert c.get("/.well-known/ard.json").status_code == 404 and c.get("/").status_code == 404
    cfg.update({"enabled": True})
    assert c.get("/.well-known/ard.json").status_code == 200


def test_public_level_headers_etag_head_and_options(web):
    cfg, cache, c = web
    r = c.get("/.well-known/ard.json")
    assert r.status_code == 200 and r.headers["content-type"] == "application/json" and r.headers["access-control-allow-origin"] == "*"
    assert r.headers["x-content-type-options"] == "nosniff" and "max-age" in r.headers["cache-control"] and r.json()["entries"]
    assert c.get("/.well-known/ai-catalog.json").content == r.content
    etag = r.headers["etag"]
    assert c.get("/.well-known/ard.json", headers={"If-None-Match": etag}).status_code == 304
    assert c.head("/.well-known/ard.json").status_code == 200
    pre = c.options("/.well-known/ard.json")
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == "*" and "GET" in pre.headers["access-control-allow-methods"]
    assert c.get("/").headers["content-type"].startswith("text/html")
    bom = c.get("/ard/bom/system.json")
    assert bom.headers["content-type"] == ST.CYCLONEDX_TYPE and validate_bom(bom.json())["ok"]
    assert c.get("/ard/skills/code-review/SKILL.md").headers["content-type"].startswith("text/markdown")
    assert c.get("/nope").status_code == 404 and c.get("/../etc/passwd").status_code == 404 and c.get("/ard/bom/agent/ghost.json").status_code == 404
    assert c.get("/nope").headers["access-control-allow-origin"] == "*"


def test_only_chosen_resource_types_are_reachable(web):
    cfg, cache, c = web
    cfg.update({"publish": {"bom_system": False, "skills": False}})
    assert c.get("/ard/bom/system.json").status_code == 404 and c.get("/ard/skills/code-review/SKILL.md").status_code == 404 and c.get("/ard/bom/agent/engineer.json").status_code == 200


def test_non_public_levels_need_the_token_and_send_no_cors_header(web):
    cfg, cache, c = web
    cfg.update({"level": "internal", "level_confirmed": True})
    assert c.get("/.well-known/ard.json").status_code == 404                                   # not ready: no token yet
    token = cfg.new_token()
    r = c.get("/.well-known/ard.json")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer" and "access-control-allow-origin" not in r.headers
    assert c.get("/.well-known/ard.json", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/.well-known/ard.json", headers={"Authorization": token}).status_code == 401
    ok = c.get("/.well-known/ard.json", headers={"Authorization": f"bearer {token}"})
    assert ok.status_code == 200 and "access-control-allow-origin" not in ok.headers and ok.headers["cache-control"] == "private, no-store"
    assert c.options("/x").headers.get("access-control-allow-origin") is None


def test_a_build_error_is_a_404_not_a_crash(web, monkeypatch):
    cfg, cache, c = web
    monkeypatch.setattr(cache, "get", lambda force=False: (_ for _ in ()).throw(SettingsError("gone")))
    assert c.get("/.well-known/ard.json").status_code == 404


# ------------------------------------------------------------------------------------------------ real listener
def test_the_server_listens_on_loopback_and_stops(rich, tmp_path):
    cfg, pub, _ = make(rich, tmp_path)
    srv = SV.DiscoveryServer(ST.SiteCache(pub), cfg)
    assert srv.status() == {"running": False, "host": "127.0.0.1", "port": None, "local_url": None, "error": ""} and srv.stop()["running"] is False
    st = srv.start(0)
    try:
        assert st["running"] and st["host"] == "127.0.0.1" and st["local_url"] == f"http://127.0.0.1:{st['port']}/"
        assert srv.start(0)["port"] == st["port"]                                              # already running: same listener
        r = httpx.get(f"{st['local_url']}.well-known/ard.json")
        assert r.status_code == 200 and r.json()["entries"] and r.headers["access-control-allow-origin"] == "*"
        assert httpx.get(f"{st['local_url']}api/settings").status_code == 404                  # the main API is not reachable here
        with socket.socket() as s:                                                              # bound to loopback only
            assert srv.server.servers[0].sockets[0].getsockname()[0] == "127.0.0.1" and s
    finally:
        assert srv.stop()["running"] is False
    with pytest.raises(httpx.TransportError):                                                   # refused, or (on Windows) silently dropped
        httpx.get(f"{st['local_url']}.well-known/ard.json", timeout=3)


def test_start_is_refused_when_not_ready_and_reports_bind_failures(rich, tmp_path, monkeypatch):
    cfg, pub, _ = make(rich, tmp_path, enabled=False)
    srv = SV.DiscoveryServer(ST.SiteCache(pub), cfg)
    with pytest.raises(SettingsError, match="switched off"):
        srv.start()
    cfg.update({"enabled": True})
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        with pytest.raises(SettingsError, match="port already in use"):
            srv.start(busy.getsockname()[1])
    assert srv.error and not srv.running()
    monkeypatch.setattr(SV.uvicorn.Server, "run", lambda self, sockets=None: (_ for _ in ()).throw(RuntimeError("kaboom")))
    with pytest.raises(SettingsError, match="kaboom"):
        srv.start(0)


def test_start_times_out_when_the_listener_never_comes_up(rich, tmp_path, monkeypatch):
    cfg, pub, _ = make(rich, tmp_path)
    srv = SV.DiscoveryServer(ST.SiteCache(pub), cfg)
    clock = {"t": 0.0}
    monkeypatch.setattr(SV.time, "time", lambda: clock.__setitem__("t", clock["t"] + 3) or clock["t"])
    monkeypatch.setattr(SV.time, "sleep", lambda s: None)
    gate = __import__("threading").Event()
    monkeypatch.setattr(SV.uvicorn.Server, "run", lambda self, sockets=None: gate.wait(5))
    try:
        with pytest.raises(SettingsError, match="did not start in time"):
            srv.start(0)
    finally:
        gate.set()
        time.sleep(0.05)
