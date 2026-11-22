"""The OCSF schema this build emits, loaded from the trimmed copy vendored in this folder (made by scripts/vendor_ocsf.py).

OCSF 1.10.0 is the version schema.ocsf.io serves by default (checked 2026-11-22). Its `ai_operation` profile adds the ai_agent, ai_model,
ai_capability, ai_status, delegation and message_context attributes to API Activity, Application Lifecycle, Process Activity, Entity Management,
Authentication and Account Change. There is no dedicated AI event class: Detection Finding has no AI profile.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

VERSION = "1.10.0"
FILE = Path(__file__).with_name("schema_1_10_0.json")


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(FILE.read_text(encoding="utf-8"))


def klass(name: str) -> dict:
    return load()["classes"][name]


def enum_caption(attr: dict, value: int) -> str:
    return (attr.get("enum") or {}).get(str(value), "")
