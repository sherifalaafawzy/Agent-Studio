"""Optional, user-triggered push of the AI-BOM to a Dependency-Track server (PUT /api/v1/bom).

Nothing is ever sent on a schedule or as a side effect. The flow is: PREVIEW (shows the exact address, the fields and a digest of
the body that would be sent, with the redaction level applied) and then SEND with that digest as confirmation. If the system
changed in between, the digests differ and nothing is sent. The API key is write-only, goes only in the `X-Api-Key` header,
and redirects are not followed, so it cannot be forwarded to another host.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import httpx

from ..aibom import builder
from ..aibom.validate import validate_bom
from .settings import DiscoverySettings, SettingsError, valid_push_url

TRANSPORT: httpx.BaseTransport | None = None            # tests inject a MockTransport here
TIMEOUT_S = 30
PATH = "/api/v1/bom"
_last: dict[str, dict] = {}                              # level -> the BOM last built, so an unchanged system gives identical bytes (and digest)


def endpoint(url: str) -> str:
    u = url.strip().rstrip("/")
    return u if u.endswith(PATH) else u + PATH


def plan(state: Any, settings: DiscoverySettings) -> tuple[dict, bytes]:
    """(what would be sent, the request body). Raises SettingsError when the push is not configured."""
    cfg = settings.get()
    if not cfg["push_url"]:
        raise SettingsError("set the Dependency-Track address in the settings first")
    if (bad := valid_push_url(cfg["push_url"])):
        raise SettingsError(bad)
    if not settings.push_key():
        raise SettingsError("set the Dependency-Track API key in the settings first")
    bom = builder.build_full(state, cfg["push_level"], True, publisher=cfg["publisher"])
    old = _last.get(cfg["push_level"])
    bom = old if old is not None and old["serialNumber"] == bom["serialNumber"] else bom      # same content => same serial => keep the first timestamp
    _last[cfg["push_level"]] = bom
    check = validate_bom(bom)
    if not check["ok"]:
        raise SettingsError(f"the BOM does not validate against CycloneDX ({check['error_count']} errors): nothing will be sent")
    raw = builder.serialize(bom)
    version = cfg["push_version"] or builder.app_version()
    body = json.dumps({"projectName": cfg["push_project"] or "Agent Studio", "projectVersion": version, "autoCreate": True,
                       "bom": base64.b64encode(raw).decode("ascii")}, sort_keys=True).encode("utf-8")
    url = endpoint(cfg["push_url"])
    digest = hashlib.sha256(url.encode() + b"\n" + body).hexdigest()
    return ({"method": "PUT", "url": url, "headers": {"X-Api-Key": "(your stored key, not shown)", "Content-Type": "application/json"},
             "project": {"name": cfg["push_project"] or "Agent Studio", "version": version, "autoCreate": True},
             "redaction_level": cfg["push_level"], "bom": {"format": "CycloneDX", "specVersion": bom["specVersion"], "bytes": len(raw),
                                                          "sha256": hashlib.sha256(raw).hexdigest(), "components": len(bom["components"]),
                                                          "services": len(bom.get("services", []))},
             "confirm": digest, "note": "This is everything that will be sent. Nothing leaves this machine until you confirm."}, body)


def send(state: Any, settings: DiscoverySettings, confirm: str) -> dict:
    preview, body = plan(state, settings)
    if confirm != preview["confirm"]:
        raise SettingsError("the BOM or the settings changed since you reviewed them: preview again, then confirm")
    try:
        with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT_S, follow_redirects=False) as c:
            r = c.put(preview["url"], content=body, headers={"X-Api-Key": settings.push_key(), "Content-Type": "application/json"})
    except httpx.HTTPError as e:
        raise PushError(f"could not reach the server: {type(e).__name__}") from e
    if r.status_code in (401, 403):
        raise PushError("the server rejected the API key (it needs the BOM_UPLOAD and PROJECT_CREATION_UPLOAD permissions)")
    if 300 <= r.status_code < 400:
        raise PushError(f"the server answered with a redirect ({r.status_code}); redirects are not followed: use the final address")
    if r.status_code >= 400:
        raise PushError(f"the server answered {r.status_code}: {r.text[:200]}")
    try:
        token = r.json().get("token")
    except ValueError:
        token = None
    return {"ok": True, "status": r.status_code, "token": token, "sent_to": preview["url"], "bom_sha256": preview["bom"]["sha256"]}


class PushError(Exception):
    """The server could not be reached or refused the upload. The message is safe to show."""
