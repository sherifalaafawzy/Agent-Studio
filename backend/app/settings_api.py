"""REST API over the settings registry: /api/settings/registry, /export, /import, /reset, /history.

Secrets are never exported and never imported: a settings file can be shared or kept in git without leaking a key.
Settings that belong to a feature (Auto, Discovery) go back through that feature's own validation on import.
Pydantic models are module-level (main.py uses postponed annotations).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .settings_registry import BY_KEY, SETTINGS, TABS, SettingError, coerce, default_str, public_view, typed

FORMAT = "agent-studio-settings"
VERSION = 1


class ImportIn(BaseModel):
    data: dict
    dry_run: bool = True


class ResetIn(BaseModel):
    keys: list[str] = Field(default_factory=list)       # empty = every user-editable setting
    all: bool = False


def _state(request: Request):
    return request.app.state.s


def _ard(s):
    from .ard.settings import DiscoverySettings
    return DiscoverySettings(s.db)


def _blob_apply(s, key: str, value: dict) -> None:
    """Hand a feature-owned setting back to its owner, so its own validation runs."""
    if key == "auto_config":
        s.auto.set_config(value)
    elif key == "ard_settings":
        patch = {k: v for k, v in value.items() if k in ("publisher", "base_url", "level", "sign", "port", "publish", "push_url", "push_project", "push_version", "push_level")}
        _ard(s).update(patch)               # publishing stays OFF: `enabled` and `level_confirmed` are never imported
    elif key == "memory_config" and getattr(s, "memory", None) is not None:
        s.memory.set_config(value)
    elif key == "ocsf_config" and getattr(s, "ocsf", None) is not None:
        s.ocsf.set_config(value)
    elif key == "identity_config" and getattr(s, "identity", None) is not None:
        s.identity.set_config({k: v for k, v in value.items() if k in ("trust_domain", "issuer", "ttl_s", "enforce", "provider")})
    elif key == "artifact_config" and getattr(s, "artifacts", None) is not None:
        s.artifacts.set_config({k: v for k, v in value.items() if k != "storages" or v})
    elif key == "workflow_config" and getattr(s, "wf_config", None) is not None:
        from .workflows.core import WorkflowError
        try:
            s.wf_config.set_config({k: v for k, v in value.items() if k != "webhooks_enabled"})       # switching webhooks on is never imported
        except WorkflowError as e:
            raise SettingError(str(e))
    elif key == "canvas_config" and getattr(s, "canvas", None) is not None:
        from .canvas.service import CanvasError
        try:
            s.canvas.set_config(value)
        except CanvasError as e:
            raise SettingError(str(e))
    elif key == "apps_config" and getattr(s, "apps", None) is not None:
        from .apps.store import AppError
        try:
            s.apps.set_config(value)
        except AppError as e:
            raise SettingError(str(e))
    elif key == "science_config" and getattr(s, "science", None) is not None:
        from .science.service import ScienceError
        try:
            s.science.set_config(value)               # the safety level can only be raised: an imported file can never lower it
        except ScienceError as e:
            raise SettingError(str(e))
    elif key == "uikit_config" and getattr(s, "uikit", None) is not None:
        from .uikit.service import UiKitError
        try:
            s.uikit.set_config(value)
        except UiKitError as e:
            raise SettingError(str(e))
    elif key == "live_gemini_prices":
        from . import live_cost
        try:
            live_cost.save_prices(s.db, live_cost.validate_prices(value))           # the same checks as the Live page's price editor
        except ValueError as e:
            raise SettingError(str(e))
    elif key == "harness_policy":
        from .harness.policy import set_policy
        set_policy(s.db, value)
    elif key == "access_config":
        from .apiaccess.api import set_access_config
        set_access_config(s.db, {k: v for k, v in value.items() if k not in ("api_enabled", "a2a_enabled")}, s.providers)     # switching access on is never imported
    elif key == "runtime_config" and getattr(s, "runtimes", None) is not None:
        from .runtimes.service import RuntimeFault
        try:
            s.runtimes.set_config({k: v for k, v in value.items() if k in ("targets", "image_template")})      # a target's `enabled` is imported as is; credentials never travel
        except RuntimeFault as e:
            raise SettingError(str(e))
    elif key == "sandbox_config" and getattr(s, "chat", None) is not None:
        try:
            s.chat.sandboxes.set_policy({k: v for k, v in value.items() if k != "microvm_enabled"})              # switching the experimental micro VM on is never imported
        except ValueError as e:
            raise SettingError(str(e))
    elif key == "datasource_config":
        if value.get("sources"):
            raise SettingError("data sources are not imported because their credentials cannot travel: add them again on the Data sources tab")
    elif key == "data_products" and getattr(s, "data", None) is not None:
        from .datasources.base import DataError
        try:
            for prod in value.get("products", []):
                s.data.set_product(prod)
        except DataError as e:
            raise SettingError(str(e))
    elif key == "guardrail_config" and getattr(s, "guardrails", None) is not None:
        s.guardrails.import_profiles(value)
    else:
        raise SettingError(f"'{BY_KEY[key].label}' is managed by its own page")


def _plan_endpoints(s, items: object, changes: list[dict], errors: list[dict]) -> None:
    """Custom OpenAI-compatible endpoints in a settings file: added without a key and switched off, never replacing an existing one."""
    if not isinstance(items, list):
        errors.append({"key": "custom_providers", "error": "custom endpoints must be a list"})
        return
    have = {p.label.lower() for p in s.providers.list() if p.custom}
    for it in items:
        label = str(it.get("label", "")).strip() if isinstance(it, dict) else ""
        if not label:
            errors.append({"key": "custom_providers", "error": "an endpoint has no name (ignored)"})
        elif label.lower() in have:
            errors.append({"key": "custom_providers", "error": f"'{label}' already exists (kept as it is)"})
        else:
            changes.append({"key": "custom_providers", "label": f"Custom endpoint '{label}'", "from": None,
                            "to": {k: v for k, v in it.items() if k in ("label", "api_base", "default_model", "auth_mode", "api_key_env", "organization", "cap_usd",
                                                                          "cap_period", "max_output_tokens", "model_prices", "manual_models")},
                            "note": "added switched off, without a key: add the key and turn it on in Model providers"})
            have.add(label.lower())


def _plan(s, data: dict) -> tuple[list[dict], list[dict]]:
    """What importing `data` would change, and what it cannot accept."""
    changes: list[dict] = []
    errors: list[dict] = []
    if data.get("format") != FORMAT or not isinstance(data.get("settings"), dict):
        return [], [{"key": "", "error": "this is not an Agent Studio settings file"}]
    for key, value in data["settings"].items():
        st = BY_KEY.get(key)
        if st is None:
            errors.append({"key": key, "error": "unknown setting (ignored)"})
            continue
        if st.secret or st.internal:
            errors.append({"key": key, "error": "secrets and bookkeeping are never imported"})
            continue
        try:
            if st.kind == "blob":
                if not isinstance(value, dict):
                    raise SettingError(f"{st.label} must be an object")
                changes.append({"key": key, "label": st.label, "from": typed(st, s.db.get_setting(key)), "to": value})
                continue
            stored = coerce(st, value)
        except SettingError as e:
            errors.append({"key": key, "error": str(e)})
            continue
        old = s.db.get_setting(key)
        if (old if old is not None else default_str(st)) != stored:
            changes.append({"key": key, "label": st.label, "from": typed(st, old), "to": typed(st, stored)})
    if "custom_providers" in data:
        _plan_endpoints(s, data["custom_providers"], changes, errors)
    return changes, errors


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/settings", tags=["settings"])

    @r.get("/registry")
    def registry(request: Request):
        s = _state(request)
        return {"tabs": list(TABS), "settings": [public_view(st, s.db.get_setting(st.key)) for st in SETTINGS]}

    @r.get("/export")
    def export(request: Request):
        s = _state(request)
        out = {st.key: typed(st, s.db.get_setting(st.key)) for st in SETTINGS if not st.secret and not st.internal}
        return {"format": FORMAT, "version": VERSION, "exported": time.time(), "note": "Secrets (API keys, tokens, header values) are never included.",
                "settings": out, "custom_providers": s.providers.export_custom()}

    @r.post("/import")
    def import_(body: ImportIn, request: Request):
        s = _state(request)
        changes, errors = _plan(s, body.data)
        if body.dry_run:
            return {"applied": False, "changes": changes, "errors": errors}
        applied = []
        for c in changes:
            if c["key"] == "custom_providers":
                try:
                    e = c["to"]
                    s.providers.create_custom(e["label"], e["api_base"], e.get("default_model") or "", e.get("auth_mode") or "none", None, e.get("api_key_env"), None,
                                              e.get("organization"), float(e.get("cap_usd", 1.0)), e.get("cap_period", "total"), int(e.get("max_output_tokens", 4096)),
                                              e.get("model_prices"), e.get("manual_models"), enabled=False)
                    applied.append(c)
                except (KeyError, TypeError, ValueError) as ex:
                    errors.append({"key": "custom_providers", "error": f"{c['label']}: {ex}"})
                continue
            st = BY_KEY[c["key"]]
            try:
                if st.kind == "blob":
                    _blob_apply(s, st.key, c["to"])
                else:
                    s.db.set_setting(st.key, coerce(st, c["to"]))
                applied.append(c)
            except (SettingError, ValueError) as e:                         # a feature's own validation said no
                errors.append({"key": st.key, "error": str(e)})
        return {"applied": True, "changes": applied, "errors": errors}

    @r.post("/reset")
    def reset(body: ResetIn, request: Request):
        s = _state(request)
        keys = [st.key for st in SETTINGS if not st.secret and not st.internal and st.kind != "blob"] if body.all else body.keys
        done = []
        for k in keys:
            st = BY_KEY.get(k)
            if st is None or st.secret or st.internal:
                raise HTTPException(422, f"'{k}' cannot be reset here")
            if st.kind == "blob":
                raise HTTPException(422, f"'{st.label}' has its own reset on its page")
            if s.db.get_setting(k) is not None:
                s.db.set_setting(k, default_str(st))
                done.append(k)
        return {"reset": done}

    @r.get("/history")
    def history(request: Request, limit: int = 50):
        s = _state(request)
        rows = s.db.query("SELECT ts,key,old,new FROM settings_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))
        return [{"ts": x["ts"], "key": x["key"], "label": BY_KEY[x["key"]].label if x["key"] in BY_KEY else x["key"], "from": x["old"], "to": x["new"]} for x in rows]

    app.include_router(r)
