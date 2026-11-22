"""Optional signing of ARD trust manifests: a detached JWS (RFC 7515 appendix F) with EdDSA / Ed25519 (RFC 8037).

* The private key is generated here, stored as PKCS#8 PEM in the data folder (never in a BOM, never returned by the API).
* The public key is published as a JWK at /.well-known/jwks.json; `kid` is the RFC 7638 thumbprint.
* What is signed: the trust manifest WITHOUT its `signature` member, serialised as canonical JSON (keys sorted, no
  whitespace, UTF-8). The JWS is `<protected>..<signature>`; the payload is rebuilt by the verifier from the entry.

ARD v0.91 defines no signing or verification procedure of its own ("the declared trust framework does"). A signature made
here therefore proves only that whoever holds the key signed this exact trust manifest; a consumer has to agree to this
scheme (it is declared in `trustSchema`) and must obtain the public key from a source it trusts.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from ..db import data_dir

SCHEME_ID = "agent-studio:detached-jws-eddsa"
SCHEME_VERSION = "1"


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def key_path() -> Path:
    return data_dir() / "discovery" / "signing-key.pem"


def jwk_of(pub: Ed25519PublicKey) -> dict:
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    core = {"crv": "Ed25519", "kty": "OKP", "x": b64u(raw)}
    kid = b64u(hashlib.sha256(canonical(core)).digest())
    return {**core, "alg": "EdDSA", "use": "sig", "kid": kid}


class Signer:
    def __init__(self, path: Path | None = None):
        self.path = path or key_path()

    def exists(self) -> bool:
        return self.path.is_file()

    def generate(self) -> dict:
        """Create (or replace) the key. Returns the public JWK."""
        key = Ed25519PrivateKey.generate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass                                    # Windows ignores POSIX modes; the folder is already user-private
        os.replace(tmp, self.path)
        return jwk_of(key.public_key())

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)

    def _key(self) -> Ed25519PrivateKey:
        key = serialization.load_pem_private_key(self.path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("the signing key is not an Ed25519 key")
        return key

    def public_jwk(self) -> dict | None:
        return jwk_of(self._key().public_key()) if self.exists() else None

    def sign(self, trust: dict) -> str:
        """A detached JWS over the trust manifest (its own `signature` member excluded)."""
        key = self._key()
        header = b64u(canonical({"alg": "EdDSA", "kid": jwk_of(key.public_key())["kid"], "typ": "JOSE"}))
        payload = b64u(canonical({k: v for k, v in trust.items() if k != "signature"}))
        return f"{header}..{b64u(key.sign(f'{header}.{payload}'.encode('ascii')))}"


def verify(trust: dict, jwk: dict) -> bool:
    """Check a trust manifest's detached JWS against a public JWK (used by the tests and by anyone re-implementing a consumer)."""
    try:
        header, empty, sig = str(trust.get("signature", "")).split(".")
        if empty != "" or json.loads(b64u_decode(header)).get("alg") != "EdDSA":
            return False
        pub = Ed25519PublicKey.from_public_bytes(b64u_decode(jwk["x"]))
        payload = b64u(canonical({k: v for k, v in trust.items() if k != "signature"}))
        pub.verify(b64u_decode(sig), f"{header}.{payload}".encode("ascii"))
        return True
    except (ValueError, KeyError, InvalidSignature):
        return False
