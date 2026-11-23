"""The component kit's settings: whether it is on, the size limits and the optional map basemap.

Maps draw a bundled world outline and make no network requests by default. A basemap (street or satellite tiles) is opt-in: the tiles load from the server
you name, which can then see which area you look at, so the page says so. A tile URL may contain a provider key; the browser needs it to load tiles, so it is
not a write-only secret and is shown as you typed it.
"""
from __future__ import annotations

import json
import re

from .. import agui_flags

KEY = "uikit_config"
DEFAULTS = {"enabled": True, "max_kb": 300, "max_rows": 5000, "basemap": {"kind": "none", "tile_url": "", "attribution": "", "pmtiles": ""},
            "new_agents": {"components": True, "endpoint": True}}      # what AG-UI switches a new agent starts with (see agui_flags.py)
LIMITS = {"max_kb": (20, 2000), "max_rows": (100, 20000)}
BASEMAP_KINDS = ("none", "tiles", "pmtiles")
ARTIFACT_ID = re.compile(r"^[0-9a-f]{12}$")


class UiKitError(ValueError):
    pass


class UiKitService:
    def __init__(self, db):
        self.db = db

    def config(self) -> dict:
        try:
            stored = json.loads(self.db.get_setting(KEY, "{}") or "{}")
        except ValueError:
            stored = {}
        stored = stored if isinstance(stored, dict) else {}
        out = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
        out["basemap"] = {**DEFAULTS["basemap"], **(out["basemap"] if isinstance(out["basemap"], dict) else {})}
        out["new_agents"] = agui_flags.flags(out["new_agents"])
        return out

    def limits(self) -> dict:
        c = self.config()
        return {"max_kb": c["max_kb"], "max_rows": c["max_rows"]}

    def set_config(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise UiKitError("send an object")
        cur = self.config()
        for k, v in patch.items():
            if k not in DEFAULTS:
                raise UiKitError(f"unknown component setting '{k}'")
            if k == "enabled":
                if not isinstance(v, bool):
                    raise UiKitError("enabled must be true or false")
                cur[k] = v
            elif k == "basemap":
                cur[k] = self._basemap(v)
            elif k == "new_agents":
                try:
                    cur[k] = agui_flags.normalise(v)
                except ValueError as e:
                    raise UiKitError(str(e))
            else:
                lo, hi = LIMITS[k]
                if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                    raise UiKitError(f"{k} must be a whole number from {lo} to {hi}")
                cur[k] = v
        self.db.set_setting(KEY, json.dumps(cur))
        return cur

    @staticmethod
    def _basemap(v: object) -> dict:
        if not isinstance(v, dict):
            raise UiKitError("basemap must be an object")
        b = {**DEFAULTS["basemap"], **{k: str(x or "").strip() for k, x in v.items() if k in DEFAULTS["basemap"]}}
        if b["kind"] not in BASEMAP_KINDS:
            raise UiKitError("basemap kind must be none, tiles or pmtiles")
        if b["kind"] == "tiles":
            if not b["tile_url"].startswith("https://") or not all(p in b["tile_url"] for p in ("{z}", "{x}", "{y}")):
                raise UiKitError("a tile address must start with https:// and contain {z}, {x} and {y}")
            if not b["attribution"]:
                raise UiKitError("tile providers require credit: fill in the attribution text")
        if b["kind"] == "pmtiles" and not ARTIFACT_ID.match(b["pmtiles"]):
            raise UiKitError("give the 12-character id of the .pmtiles file saved as an artifact")
        return b
