"""Download the parts of the OCSF schema Agent Studio uses and save a trimmed copy for offline validation.

    python scripts/vendor_ocsf.py [version]        (default 1.10.0)  ->  backend/app/ocsf/schema_<version>.json

Why not the whole schema: schema.ocsf.io's export endpoint refuses to build the whole thing in one file (the 'process' object is extended by two
profiles), so the classes we emit are fetched one by one, then every object they use, recursively. Descriptions and references are dropped.
The schema is Apache-2.0 (https://github.com/ocsf/ocsf-schema).
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

VERSION = sys.argv[1] if len(sys.argv) > 1 else "1.10.0"
CLASSES = ["api_activity", "detection_finding", "process_activity", "entity_management", "authentication", "account_change", "application_lifecycle"]
BASE = f"https://schema.ocsf.io/{VERSION}/api"
KEEP = ("caption", "name", "uid", "category", "category_uid", "extends", "profiles")


def get(path: str):
    with urllib.request.urlopen(f"{BASE}/{path}", timeout=60) as r:       # noqa: S310 - a fixed public schema host
        return json.load(r)


def trim_attr(a: dict) -> dict:
    out = {k: a[k] for k in ("type", "requirement", "object_type", "is_array", "profiles", "caption") if k in a}
    if "enum" in a:
        out["enum"] = {k: v.get("caption", "") for k, v in a["enum"].items()}
    return out


def trim(d: dict) -> dict:
    out = {k: d[k] for k in KEEP if k in d}
    out["attributes"] = {n: trim_attr(a) for n, a in d.get("attributes", {}).items()}
    return out


def main() -> None:
    classes, objects = {}, {}
    todo: list[str] = []
    for c in CLASSES:
        d = get(f"classes/{c}")
        classes[c] = trim(d)
        todo += [a["object_type"] for a in d["attributes"].values() if a.get("object_type")]
    while todo:
        name = todo.pop()
        if name in objects:
            continue
        d = get(f"objects/{name}")
        objects[name] = trim(d)
        todo += [a["object_type"] for a in d["attributes"].values() if a.get("object_type") and a["object_type"] not in objects]
    profiles = {k: {"caption": v.get("caption", ""), "attributes": sorted(v.get("attributes", {}))} for k, v in get("profiles").items()}
    out = {"version": VERSION, "source": "https://schema.ocsf.io (Apache-2.0)", "classes": classes, "objects": objects, "profiles": profiles}
    path = Path(__file__).resolve().parents[1] / "backend" / "app" / "ocsf" / f"schema_{VERSION.replace('.', '_')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"wrote {path} ({path.stat().st_size // 1024} KB): {len(classes)} classes, {len(objects)} objects")


if __name__ == "__main__":
    main()
