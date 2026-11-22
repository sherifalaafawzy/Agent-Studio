"""Discovery settings (publisher validation, readiness, access token) and detached-JWS signing."""
from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.ard import settings as S
from app.ard import signing as G


@pytest.fixture
def cfg(state):
    return S.DiscoverySettings(state.db)


# ------------------------------------------------------------------------------------------------ settings
@pytest.mark.parametrize("domain,ok", [
    ("agents.acme-corp.dev", True), ("Agents.Acme-Corp.DEV.", True), ("a.io", True),
    ("", False), ("localhost", False), ("10.0.0.1", False), ("https://x.dev", False), ("nodots", False), ("my-pc.local", False), ("svc.internal", False),
    ("thing.test", False), ("example.com", False), ("a.example.com", False), ("example.org", False), ("-bad-.dev", False), ("x" * 70 + ".dev", False),
    ("host.lan", False), ("x.arpa", False),
])
def test_publisher_domains(domain, ok):
    assert (S.valid_publisher(domain) is None) is ok


def test_push_url_rules():
    assert S.valid_push_url("") is None and S.valid_push_url("https://dt.acme.dev") is None and S.valid_push_url("http://localhost:8081") is None
    assert S.valid_push_url("http://127.0.0.1:8081/") is None
    for bad in ("http://dt.acme.dev", "ftp://x", "https://", "http://[bad", "not a url"):
        assert S.valid_push_url(bad), bad


def test_defaults_are_off_and_not_ready(cfg):
    d = cfg.public()
    assert d["enabled"] is False and d["level"] == "public" and d["publisher"] == "" and not any(d["publish"].values()) and d["has_token"] is False
    assert d["effective_base_url"] == "" and d["port"] == S.DEFAULT_PORT and [t["id"] for t in d["types"]] == list(S.TYPES)
    assert len(d["not_ready"]) == 3 and d["publisher_problem"] is None and "push_api_key" not in d


def test_update_validates_each_field(cfg):
    with pytest.raises(S.SettingsError, match="reserved"):
        cfg.update({"publisher": "example.com"})
    with pytest.raises(S.SettingsError, match="fully qualified"):
        cfg.update({"publisher": "localhost"})
    out = cfg.update({"publisher": " Agents.Acme-Corp.dev. ", "enabled": True, "sign": True, "port": 9000,
                      "publish": {"agents": True, "bom_system": True}, "push_project": " P ", "push_version": "1.2", "push_url": "https://dt.acme.dev"})
    assert out["publisher"] == "agents.acme-corp.dev" and out["effective_base_url"] == "https://agents.acme-corp.dev" and out["publish"]["agents"] and out["push_project"] == "P"
    assert out["not_ready"] == [] and out["sign"] is True
    assert cfg.update({"publisher": ""})["publisher"] == ""                       # clearing is allowed
    cfg.update({"publisher": "agents.acme-corp.dev"})
    for patch, msg in [({"base_url": "http://agents.acme-corp.dev"}, "https://"), ({"base_url": "https://agents.acme-corp.dev/?x=1"}, "https://"),
                       ({"base_url": "https://evil.dev"}, "publisher domain"), ({"level": "secret"}, "redaction level"), ({"port": 80}, "between"),
                       ({"publish": {"ghost": True}}, "unknown resource type"), ({"push_url": "http://dt.acme.dev"}, "https://"), ({"push_level": "x"}, "push redaction")]:
        with pytest.raises(S.SettingsError, match=msg):
            cfg.update(patch)
    assert cfg.update({"base_url": "https://ard.agents.acme-corp.dev/"})["effective_base_url"] == "https://ard.agents.acme-corp.dev"
    assert cfg.update({"base_url": ""})["effective_base_url"] == "https://agents.acme-corp.dev"
    assert cfg.update({"push_level": "internal"})["push_level"] == "internal"
    cfg.update({"base_url": "https://agents.acme-corp.dev"})
    out = cfg.update({"publisher": "other.dev"})
    assert out["base_url"] == "https://agents.acme-corp.dev" and any("public address is not on the publisher domain (other.dev)" in w for w in out["not_ready"])   # caught at readiness


def test_corrupt_stored_settings_fall_back_to_defaults(cfg, state):
    state.db.set_setting(S.KEY, "{not json")
    assert cfg.get()["enabled"] is False
    state.db.set_setting(S.KEY, '{"level": "weird", "publish": {"agents": true, "unknown": true}}')
    g = cfg.get()
    assert g["level"] == "public" and g["publish"]["agents"] is True and "unknown" not in g["publish"]


def test_non_public_levels_need_confirmation_and_a_token(cfg):
    cfg.update({"publisher": "agents.acme-corp.dev", "enabled": True, "publish": {"agents": True}})
    assert cfg.readiness() == []
    out = cfg.update({"level": "internal"})
    assert out["level_confirmed"] is False and any("confirm it" in w for w in out["not_ready"]) and any("access token" in w for w in out["not_ready"])
    cfg.update({"level_confirmed": True})
    assert cfg.readiness() == [next(w for w in cfg.readiness() if "access token" in w)]
    token = cfg.new_token()
    assert token.startswith("asd_") and cfg.readiness() == [] and cfg.public()["has_token"] is True
    assert cfg.check_token(token) and not cfg.check_token("asd_wrong") and not cfg.check_token("")
    assert token not in str(cfg.public()) and token not in (cfg.db.get_setting(S.TOKEN_KEY) or "")                         # only the hash is stored
    assert cfg.update({"level": "full"})["level_confirmed"] is False                                                        # a new level must be confirmed again
    assert cfg.update({"level": "public", "level_confirmed": True})["level_confirmed"] is False                              # nothing to confirm at public
    cfg.clear_token()
    assert not cfg.check_token(token) and cfg.public()["has_token"] is False


def test_push_key_is_write_only(cfg):
    assert cfg.push_key() == "" and cfg.public()["has_push_key"] is False
    out = cfg.update({"push_api_key": "odt_secret"})
    assert out["has_push_key"] is True and "odt_secret" not in str(out) and cfg.push_key() == "odt_secret"
    assert cfg.update({"push_api_key": ""})["has_push_key"] is False


def test_publisher_problem_is_shown_for_a_bad_stored_value(cfg, state):
    state.db.set_setting(S.KEY, '{"publisher": "bad_domain"}')
    assert "fully qualified" in cfg.public()["publisher_problem"]


# ------------------------------------------------------------------------------------------------ signing
@pytest.fixture
def signer(tmp_path):
    return G.Signer(tmp_path / "keys" / "k.pem")


def test_generate_sign_verify_and_tamper(signer):
    assert not signer.exists() and signer.public_jwk() is None
    jwk = signer.generate()
    assert signer.exists() and jwk["kty"] == "OKP" and jwk["crv"] == "Ed25519" and jwk["alg"] == "EdDSA" and signer.public_jwk() == jwk and "d" not in jwk
    trust = {"identity": "https://agents.acme-corp.dev", "attestations": [{"type": "SBOM", "uri": "https://a/b", "digest": "sha256:1"}]}
    sig = signer.sign(trust)
    head, empty, tail = sig.split(".")
    assert empty == "" and G.b64u_decode(head) and len(G.b64u_decode(tail)) == 64                                            # detached: no payload part
    signed = {**trust, "signature": sig}
    assert G.verify(signed, jwk)
    assert G.verify({**signed, "identity": "https://evil.dev"}, jwk) is False                                                # any change breaks it
    assert G.verify({**signed, "signature": sig[:-4] + "AAAA"}, jwk) is False
    assert signer.sign(signed) == sig                                                                                         # a signature member is never signed over (Ed25519 is deterministic)


def test_verify_rejects_malformed_input(signer):
    jwk = signer.generate()
    other = G.Signer(signer.path.with_name("other.pem")).generate()
    sig = signer.sign({"identity": "x"})
    assert not G.verify({"identity": "x", "signature": sig}, other)                                                          # wrong key
    for bad in ("", "a.b", "a.b.c.d", f"{sig.split('..')[0]}.x.{sig.split('..')[1]}", "!!!..!!!"):
        assert G.verify({"identity": "x", "signature": bad}, jwk) is False
    import json
    wrong_alg = G.b64u(json.dumps({"alg": "HS256"}).encode()) + ".." + sig.split("..")[1]
    assert G.verify({"identity": "x", "signature": wrong_alg}, jwk) is False
    assert G.verify({"identity": "x", "signature": sig}, {"nokey": 1}) is False


def test_key_rotation_delete_and_defaults(signer, tmp_path):
    first = signer.generate()
    second = signer.generate()
    assert first["kid"] != second["kid"] and G.canonical({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()
    signer.delete()
    signer.delete()
    assert not signer.exists()
    assert G.Signer().path == G.key_path() and G.key_path().name == "signing-key.pem" and "discovery" in str(G.key_path())


def test_a_non_ed25519_key_is_refused(tmp_path):
    p = tmp_path / "rsa.pem"
    p.write_bytes(rsa.generate_private_key(65537, 2048).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with pytest.raises(ValueError, match="not an Ed25519"):
        G.Signer(p).sign({"identity": "x"})


def test_chmod_failure_is_ignored(signer, monkeypatch):
    monkeypatch.setattr(os, "chmod", lambda *a, **k: (_ for _ in ()).throw(OSError("unsupported")))
    assert signer.generate()["kid"] and signer.exists()
