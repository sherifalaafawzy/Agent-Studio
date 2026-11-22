"""Validation against the vendored official schemas, plus the checks the schemas cannot express."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.aibom import validate as V

GOOD_BOM = {
    "bomFormat": "CycloneDX", "specVersion": "1.7", "serialNumber": "urn:uuid:3e671687-395b-41f5-a30f-a58921a69b79", "version": 1,
    "metadata": {"component": {"type": "application", "bom-ref": "app", "name": "App"}},
    "components": [{"type": "library", "bom-ref": "lib", "name": "lib", "version": "1"}],
    "services": [{"bom-ref": "svc", "name": "svc"}],
    "dependencies": [{"ref": "app", "dependsOn": ["lib", "svc"]}, {"ref": "lib"}, {"ref": "svc"}],
}


def entry(**kw) -> dict:
    e = {"identifier": "urn:air:agents.acme-corp.dev:agent-studio:agent:x", "displayName": "X", "type": "application/json", "url": "https://agents.acme-corp.dev/x",
         "representativeQueries": ["one", "two"]}
    e.update(kw)
    return {k: v for k, v in e.items() if v is not None}


def test_vendored_schemas_are_the_official_files():
    names = {p.name for p in V.DIR.glob("*")}
    assert {"bom-1.7.schema.json", "spdx.schema.json", "jsf-0.82.schema.json", "cryptography-defs.schema.json", "ard-entry.schema.json",
            "ard.context.jsonld", "NOTICE.md"} <= names
    assert json.loads((V.DIR / "bom-1.7.schema.json").read_text("utf-8"))["$id"] == "http://cyclonedx.org/schema/bom-1.7.schema.json"
    assert "Apache-2.0" in (V.DIR / "NOTICE.md").read_text("utf-8")


def test_a_correct_bom_passes_and_reports_counts():
    v = V.validate_bom(GOOD_BOM)
    assert v["ok"] and v["error_count"] == 0 and v["components"] == 1 and v["services"] == 1 and v["schema"] == "CycloneDX 1.7"
    assert V.validate_bom({k: v for k, v in GOOD_BOM.items() if k != "services"} | {"dependencies": [{"ref": "app", "dependsOn": ["lib"]}, {"ref": "lib"}]})["ok"]


def test_schema_violations_are_reported_with_a_path():
    bad = copy.deepcopy(GOOD_BOM)
    bad["components"][0]["type"] = "gadget"
    del bad["specVersion"]
    v = V.validate_bom(bad)
    assert not v["ok"] and any(e["path"] == "/components/0/type" for e in v["errors"]) and v["schema"] == "CycloneDX ?"


def test_integrity_duplicates_dangling_and_unknown_refs():
    bad = copy.deepcopy(GOOD_BOM)
    bad["components"].append({"type": "file", "bom-ref": "lib", "name": "dup"})
    bad["components"][0]["components"] = [{"type": "file", "bom-ref": "svc", "name": "nested dup of a service"}]
    bad["services"][0]["services"] = [{"bom-ref": "inner", "name": "inner"}, {"bom-ref": "inner", "name": "inner again"}]
    bad["dependencies"] += [{"ref": "ghost", "dependsOn": ["lib"]}, {"ref": "lib"}, {"ref": "svc", "dependsOn": ["nope"]}]
    msgs = [e["message"] for e in V.check_bom_integrity(bad)]
    assert any("duplicate bom-ref 'lib'" in m for m in msgs) and any("duplicate bom-ref 'svc'" in m for m in msgs) and any("duplicate bom-ref 'inner'" in m for m in msgs)
    assert any("unknown bom-ref 'ghost'" in m for m in msgs) and any("more than one dependency entry" in m for m in msgs) and any("dangling reference 'nope'" in m for m in msgs)
    assert V.check_bom_integrity({"components": [], "dependencies": []}) == [] and V.check_bom_integrity({"components": [{"type": "file", "name": "no ref"}]}) == []


def test_error_list_is_capped():
    bad = copy.deepcopy(GOOD_BOM)
    bad["components"] = [{"type": "nope", "bom-ref": f"c{i}", "name": "x"} for i in range(V.MAX_ERRORS + 20)]
    bad["dependencies"] = []
    v = V.validate_bom(bad)
    assert len(v["errors"]) == V.MAX_ERRORS and v["error_count"] > V.MAX_ERRORS


# ------------------------------------------------------------------------------------------------ ARD
def trust(**kw):
    return {"identity": "https://agents.acme-corp.dev", **kw}


def test_a_correct_manifest_passes():
    m = {"entries": [entry(trustManifest=trust(attestations=[{"type": "SBOM", "uri": "https://agents.acme-corp.dev/b.json", "digest": "sha256:ab"}]),
                           updatedAt="2026-11-21T10:00:00Z", **{"@id": "urn:air:agents.acme-corp.dev:agent-studio:agent:x"}),
                    entry(identifier="urn:air:agents.acme-corp.dev:y", url=None, data={"a": 1}, representativeQueries=["a", "b", "c"])]}
    v = V.validate_manifest(m, "agents.acme-corp.dev")
    assert v["ok"] and v["errors"] == [] and v["warnings"] == [] and v["entries"] == 2 and v["schema"].startswith("ARD v0.91")


def test_structural_errors_come_from_the_official_schema():
    m = {"entries": [entry(identifier="not-a-urn"), entry(url=None), entry(data={"a": 1}), {"displayName": "x"}, "string"]}
    v = V.validate_manifest(m)
    assert not v["ok"] and {e["path"].split("/")[2] for e in v["errors"] if e["path"].startswith("/entries/")} >= {"0", "1", "2", "3"}
    assert not V.validate_manifest({})["ok"] and not V.validate_manifest([])["ok"] and V.validate_manifest([])["entries"] == 0


def test_the_lowercase_trust_manifest_is_validated_even_though_the_official_schema_skips_it():
    bad = entry(trustManifest={"identityType": "https"})                       # no `identity`
    official_only = V._ard_validator().is_valid({"entries": [bad]})
    assert official_only is True                                                # the schema alone misses it (capital-T quirk)
    v = V.validate_manifest({"entries": [bad]})
    assert not v["ok"] and any(e["path"] == "/entries/0/trustManifest" and "identity" in e["message"] for e in v["errors"])
    capital = V.validate_manifest({"entries": [entry(TrustManifest={"identity": "https://agents.acme-corp.dev"})]})
    assert capital["ok"] and any("capital-T" in w["message"] for w in capital["warnings"])
    assert not V.validate_manifest({"entries": [entry(TrustManifest={})]})["ok"]                   # the schema does check the capital spelling


def test_discovery_constraints_warn_and_binding_errors():
    v = V.validate_manifest({"entries": [entry(representativeQueries=None), entry(representativeQueries=["only one"]), entry(representativeQueries=["1", "2", "3", "4", "5", "6"])]})
    assert v["ok"] and [w["path"] for w in v["warnings"]] == [f"/entries/{i}/representativeQueries" for i in range(3)]
    v = V.validate_manifest({"entries": [entry(url="ftp://x/y"), entry(url="relative/path"), entry(updatedAt="yesterday"), entry(updatedAt=5),
                                        entry(**{"@id": "urn:air:other.dev:z"})]})
    paths = [e["path"] for e in v["errors"]]
    assert "/entries/0/url" in paths and "/entries/1/url" in paths and "/entries/2/updatedAt" in paths and "/entries/3/updatedAt" in paths and "/entries/4/@id" in paths
    mismatch = V.validate_manifest({"entries": [entry(trustManifest={"identity": "https://evil.example-vendor.io"})]}, "agents.acme-corp.dev")
    assert any("does not align" in e["message"] for e in mismatch["errors"])
    sub = V.validate_manifest({"entries": [entry(trustManifest={"identity": "spiffe://sub.agents.acme-corp.dev/workload"})]})
    assert sub["ok"]                                                               # a subdomain of the publisher is aligned
    other = V.validate_manifest({"entries": [entry()]}, "other-domain.dev")
    assert any("not the configured publisher" in e["message"] for e in other["errors"])
    assert V.validate_manifest({"entries": [entry(identifier=5)]}, "x.dev")["ok"] is False


def test_error_list_capped_for_manifests():
    v = V.validate_manifest({"entries": [entry(url="relative") for _ in range(V.MAX_ERRORS + 5)]})
    assert len(v["errors"]) == V.MAX_ERRORS and v["error_count"] >= V.MAX_ERRORS + 5
    assert Path(V.DIR / "ard-entry.schema.json").is_file()
