"""The built-in token issuer: Ed25519-signed JWTs (RFC 7519 / RFC 8037) for agent identities.

Tokens are short-lived. The private key is created on first use, kept in the data folder (never in a BOM, never returned by the
API) and its public half is published as a JWKS so another system can check a token. Verification here checks the signature,
issuer, expiry, not-before and (when asked) the audience."""
from __future__ import annotations

import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..ard.signing import Signer, b64u, b64u_decode, canonical, jwk_of
from ..db import data_dir


class TokenError(ValueError):
    """A token that cannot be accepted. The message is safe to show."""


def key_path():
    return data_dir() / "identity" / "issuer-key.pem"


class LocalIssuer:
    def __init__(self, signer: Signer | None = None):
        self.signer = signer or Signer(key_path())

    def _ensure(self) -> None:
        if not self.signer.exists():
            self.signer.generate()

    def jwks(self) -> dict:
        self._ensure()
        return {"keys": [self.signer.public_jwk()]}

    def rotate(self) -> dict:
        """A new key: every token issued so far stops verifying."""
        return {"keys": [{**self.signer.generate()}]}

    def sign(self, claims: dict) -> str:
        self._ensure()
        key = self.signer._key()
        header = b64u(canonical({"alg": "EdDSA", "kid": jwk_of(key.public_key())["kid"], "typ": "JWT"}))
        payload = b64u(canonical(claims))
        return f"{header}.{payload}." + b64u(key.sign(f"{header}.{payload}".encode("ascii")))

    def verify(self, token: str, issuer: str, audience: str | None = None, now: float | None = None) -> dict:
        self._ensure()
        try:
            header_b, payload_b, sig_b = str(token).split(".")
            header, claims = json.loads(b64u_decode(header_b)), json.loads(b64u_decode(payload_b))
        except (ValueError, TypeError) as e:
            raise TokenError("this is not a token this app issued") from e
        if header.get("alg") != "EdDSA" or not isinstance(claims, dict):
            raise TokenError("this is not a token this app issued")
        try:
            pub = Ed25519PublicKey.from_public_bytes(b64u_decode(self.signer.public_jwk()["x"]))
            pub.verify(b64u_decode(sig_b), f"{header_b}.{payload_b}".encode("ascii"))
        except (InvalidSignature, ValueError) as e:
            raise TokenError("the signature does not match (wrong key, or the token was changed)") from e
        t = time.time() if now is None else now
        if claims.get("iss") != issuer:
            raise TokenError("the token was issued by someone else")
        if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] <= t:
            raise TokenError("the token has expired")
        if isinstance(claims.get("nbf"), (int, float)) and claims["nbf"] > t + 5:
            raise TokenError("the token is not valid yet")
        aud = claims.get("aud")
        if audience is not None and audience not in ([aud] if isinstance(aud, str) else aud or []):
            raise TokenError("the token was issued for a different audience")
        return claims
