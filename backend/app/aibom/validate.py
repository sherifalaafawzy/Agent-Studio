"""Validation against the official schemas, offline (they are vendored in ./schemas).

* CycloneDX 1.7 JSON schema (Draft-07) plus the integrity rules the schema cannot express: unique bom-refs and a dependency
  graph with no dangling references.
* ARD (Agentic Resource Discovery v0.91) `ArdManifest` / `ArdEntry` (JSON Schema 2020-12) plus the discovery constraints of
  the spec's appendix D.2 (representativeQueries 2-5 as a warning, publisher/identity alignment).

Known discrepancy in the official ARD schema: it declares the trust envelope under the key `TrustManifest` (capital T) but
the specification text and the JSON-LD context both call it `trustManifest`. Because entries allow additional properties,
a lowercase `trustManifest` is not checked by the official schema at all, so this module validates it explicitly against the
schema's own `TrustManifest` definition. Entries are emitted with the lowercase name.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, Draft202012Validator
from referencing import Registry, Resource

DIR = Path(__file__).parent / "schemas"
MAX_ERRORS = 60
URN_RE = re.compile(r"^urn:air:([a-zA-Z0-9.-]+)(:[a-zA-Z0-9._-]+)+$")
DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
URL_RE = re.compile(r"^https?://[^\s/]+(/\S*)?$")


def _load(name: str) -> dict:
    return json.loads((DIR / name).read_text("utf-8"))


def _registry() -> Registry:
    reg = Registry()
    for name in ("bom-1.7.schema.json", "spdx.schema.json", "jsf-0.82.schema.json", "cryptography-defs.schema.json", "ard-entry.schema.json"):
        doc = _load(name)
        reg = reg.with_resource(doc["$id"], Resource.from_contents(doc))
    return reg


_REG: Registry | None = None
_BOM: Draft7Validator | None = None
_ARD: Draft202012Validator | None = None
_ENTRY_ID = "https://raw.githubusercontent.com/ards-project/ard-spec/main/spec/schemas/ard-entry.schema.json"


def _bom_validator() -> Draft7Validator:
    global _REG, _BOM
    if _BOM is None:
        _REG = _REG or _registry()
        _BOM = Draft7Validator(_load("bom-1.7.schema.json"), registry=_REG)
    return _BOM


def _ard_validator() -> Draft202012Validator:
    global _REG, _ARD
    if _ARD is None:
        _REG = _REG or _registry()
        _ARD = Draft202012Validator({"$ref": f"{_ENTRY_ID}#/$defs/ArdManifest"}, registry=_REG)
    return _ARD


def _where(path) -> str:
    return "".join(f"/{p}" for p in path)


def _errors(validator: Any, doc: Any, prefix: str = "") -> list[dict]:
    errs = sorted(validator.iter_errors(doc), key=lambda e: (list(map(str, e.absolute_path)), e.message))
    return [{"path": prefix + _where(e.absolute_path) or "/", "message": e.message[:300]} for e in errs]


def check_bom_integrity(bom: dict) -> list[dict]:
    """bom-refs are unique; every dependency edge points at something that exists."""
    out: list[dict] = []
    seen: dict[str, str] = {}

    def walk(items: list[dict], where: str) -> None:
        for i, it in enumerate(items or []):
            ref = it.get("bom-ref")
            if ref is not None:
                if ref in seen:
                    out.append({"path": f"{where}/{i}", "message": f"duplicate bom-ref '{ref}' (also at {seen[ref]})"})
                seen[ref] = f"{where}/{i}"
            walk(it.get("components") or [], f"{where}/{i}/components")
            walk(it.get("services") or [], f"{where}/{i}/services")

    app = bom.get("metadata", {}).get("component")
    if app:
        walk([app], "/metadata/component")
    walk(bom.get("components", []), "/components")
    walk(bom.get("services", []), "/services")
    deps = bom.get("dependencies", [])
    listed: set[str] = set()
    for i, d in enumerate(deps):
        if d["ref"] not in seen:
            out.append({"path": f"/dependencies/{i}/ref", "message": f"dependency for unknown bom-ref '{d['ref']}'"})
        if d["ref"] in listed:
            out.append({"path": f"/dependencies/{i}/ref", "message": f"'{d['ref']}' has more than one dependency entry"})
        listed.add(d["ref"])
        for j, x in enumerate(d.get("dependsOn", [])):
            if x not in seen:
                out.append({"path": f"/dependencies/{i}/dependsOn/{j}", "message": f"dangling reference '{x}'"})
    return out


def validate_bom(bom: dict) -> dict:
    errors = _errors(_bom_validator(), bom) + check_bom_integrity(bom)
    return {"ok": not errors, "schema": f"CycloneDX {bom.get('specVersion', '?')}", "errors": errors[:MAX_ERRORS], "error_count": len(errors),
            "warnings": [], "components": len(bom.get("components", [])), "services": len(bom.get("services", []))}


def _trust_defs() -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"{_ENTRY_ID}#/$defs/TrustManifest"}, registry=_REG or _registry())


def validate_manifest(manifest: dict, publisher: str = "") -> dict:
    """Errors: structural validity (official schema + the lowercase trustManifest the schema misses). Warnings: appendix D.2
    constraints and style issues. `publisher` (optional) enables the publisher-authority alignment check."""
    errors = _errors(_ard_validator(), manifest)
    warnings: list[dict] = []
    trust = _trust_defs()
    for i, e in enumerate(manifest.get("entries", []) if isinstance(manifest, dict) else []):
        if not isinstance(e, dict):
            continue
        at = f"/entries/{i}"
        if "trustManifest" in e:
            errors += _errors(trust, e["trustManifest"], f"{at}/trustManifest")
        if "TrustManifest" in e:
            warnings.append({"path": f"{at}/TrustManifest", "message": "capital-T 'TrustManifest' follows the schema but the spec text and JSON-LD context use 'trustManifest'"})
        q = e.get("representativeQueries")
        if not isinstance(q, list) or not 2 <= len(q) <= 5:
            warnings.append({"path": f"{at}/representativeQueries", "message": "should hold 2-5 sample queries so a registry can find this entry (spec D.2)"})
        if "url" in e and not (isinstance(e["url"], str) and URL_RE.match(e["url"])):
            errors.append({"path": f"{at}/url", "message": "url must be an absolute http(s) URL"})
        if "updatedAt" in e and not (isinstance(e["updatedAt"], str) and DATE_TIME.match(e["updatedAt"])):
            errors.append({"path": f"{at}/updatedAt", "message": "updatedAt must be an ISO 8601 date-time"})
        if "@id" in e and e["@id"] != e.get("identifier"):
            errors.append({"path": f"{at}/@id", "message": "@id and identifier must denote the same resource"})
        ident = e.get("identifier", "")
        m = URN_RE.match(ident) if isinstance(ident, str) else None
        tm = e.get("trustManifest") or {}
        if m and tm.get("identity"):
            dom = re.sub(r"^[a-z]+://", "", str(tm["identity"])).split("/")[0].split(":")[0].lower()
            if dom != m.group(1).lower() and not dom.endswith("." + m.group(1).lower()):
                errors.append({"path": f"{at}/trustManifest/identity", "message": f"trust domain '{dom}' does not align with the identifier's publisher '{m.group(1)}' (spec 4.5.1)"})
        if publisher and m and m.group(1).lower() != publisher.lower():
            errors.append({"path": f"{at}/identifier", "message": f"publisher '{m.group(1)}' is not the configured publisher '{publisher}'"})
    return {"ok": not errors, "schema": "ARD v0.91 ArdManifest", "errors": errors[:MAX_ERRORS], "error_count": len(errors),
            "warnings": warnings[:MAX_ERRORS], "entries": len(manifest.get("entries", [])) if isinstance(manifest, dict) else 0}
