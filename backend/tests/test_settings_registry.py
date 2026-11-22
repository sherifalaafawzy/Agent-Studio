"""Settings audit: every setting is described once (settings_registry), exposed in the UI, validated, and can be backed up / reset."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app import settings_registry as R
from app.main import SettingsPatch

APP = Path(__file__).resolve().parents[1] / "app"
FRONT = Path(__file__).resolve().parents[2] / "frontend"


def _src(*paths: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in paths)


# ------------------------------------------------------------------------------------------------ the registry is complete
def test_every_key_the_code_reads_or_writes_is_in_the_registry():
    used: set[str] = set()
    for f in APP.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        used |= set(re.findall(r"(?:get_setting|set_setting)\(\s*[\"']([a-z0-9_]+)[\"']", text))
        if re.search(r"(?:get_setting|set_setting)\((?:KEY|CONFIG_KEY|TOKEN_KEY|PUSH_KEY)", text):
            used |= set(re.findall(r"^(?:[A-Z_]*KEY)\s*=\s*[\"']([a-z0-9_]+)[\"']", text, re.M))
    missing = sorted(k for k in used if k not in R.BY_KEY)
    assert not missing, f"settings used in code but not described in settings_registry.py: {missing}"


def test_tuple_literal_keys_of_voice_and_telemetry_are_in_the_registry():
    for k in ("elevenlabs_voice_id", "elevenlabs_tts_model", "elevenlabs_stt_model", "voice_auto_read", "voice_dictation", "voice_char_cap",
              "otel_enabled", "otel_inject_env", "otel_retention_days", "otel_receiver_url", "otel_forward_url", "otel_forward_headers"):
        assert k in R.BY_KEY


def test_each_setting_is_described_and_placed():
    for s in R.SETTINGS:
        assert s.label and s.effect, s.key
        if not s.internal:
            assert s.tab in R.TABS, f"{s.key} points at an unknown Settings tab '{s.tab}'"
            assert s.where, f"{s.key}: say where in the UI it is"
        if s.kind in ("int", "float"):
            assert s.min is not None and s.max is not None and s.min < s.max, f"{s.key}: numbers need bounds"
        if s.kind == "enum":
            assert s.choices and s.default in s.choices, s.key
        if s.kind in ("str", "url", "path"):
            assert s.max_len, f"{s.key}: text needs a length limit"
    assert len(R.BY_KEY) == len(R.SETTINGS)


def test_info_ids_exist_in_the_frontend_help_registry():
    help_ts = (FRONT / "lib" / "help.ts").read_text(encoding="utf-8")
    ids = set(re.findall(r'^\s*"([a-z0-9-]+)":\s*\{', help_ts, re.M))
    missing = sorted({s.help for s in R.SETTINGS if s.help and s.help not in ids})
    assert not missing, f"help ids not in lib/help.ts: {missing}"


def test_every_user_setting_is_reachable_in_the_settings_ui():
    """A backend setting with no control (or a control with no backend setting) is a bug: the field name must appear in the UI code."""
    ui = _src(FRONT / "app" / "settings" / "page.tsx", *(FRONT / "components").glob("*.tsx"), FRONT / "app" / "memory" / "page.tsx")
    for s in R.SETTINGS:
        if s.internal or s.secret or s.kind == "blob":
            continue
        assert (s.field or s.key) in ui, f"{s.key} has no control in the UI (looked for '{s.field or s.key}')"
    tabs_src = (FRONT / "app" / "settings" / "page.tsx").read_text(encoding="utf-8")
    for tab in R.TABS:
        assert f'"{tab}"' in tabs_src, f"Settings tab '{tab}' is not in the page"


def test_registry_bounds_match_the_request_model():
    """The API must refuse what the registry says is out of range (a second, independent check of the same limits)."""
    fields = SettingsPatch.model_fields
    for s in R.SETTINGS:
        if s.key not in fields or s.kind not in ("int", "float"):
            continue
        meta = {type(m).__name__: m for m in fields[s.key].metadata}
        ge = getattr(meta.get("Ge"), "ge", None)
        le = getattr(meta.get("Le"), "le", None)
        assert ge == s.min and le == s.max, f"{s.key}: registry {s.min}..{s.max} but the API accepts {ge}..{le}"


def test_every_settings_patch_field_is_a_registered_setting():
    for name in SettingsPatch.model_fields:
        assert name in R.BY_KEY, f"{name} is accepted by PUT /api/settings but is not in settings_registry.py"


def test_registry_defaults_equal_what_the_api_reports_when_nothing_is_stored(client):
    got = client.get("/api/settings").json()
    for s in R.SETTINGS:
        if s.key in got and s.kind not in ("path",) and s.key not in ("sidebot_provider", "sidebot_model"):
            assert got[s.key] == s.default, f"{s.key}: registry default {s.default!r} but the API says {got[s.key]!r}"


# ------------------------------------------------------------------------------------------------ validation
@pytest.mark.parametrize("key,bad", [("max_llm_calls_per_turn", 0), ("max_llm_calls_per_turn", 100001), ("max_spawns_per_turn", 21), ("max_turn_minutes", -1),
                                     ("max_external_calls_per_turn", 0), ("external_timeout_min", 0.5), ("thinking_level", "extreme"),
                                     ("workspace_dir", "relative/path"), ("sidebot_model", "x" * 201), ("sidebot_guidance", "x" * 4001)])
def test_the_server_refuses_out_of_range_values(client, key, bad):
    assert client.put("/api/settings", json={key: bad}).status_code == 422


def test_coerce_rules():
    c = lambda k, v: R.coerce(R.BY_KEY[k], v)          # noqa: E731
    assert c("sandbox_network", True) == "1" and c("max_spawns_per_turn", 3) == "3" and c("max_turn_minutes", 2) == "2.0" and c("thinking_level", "low") == "low"
    assert c("otel_receiver_url", " http://127.0.0.1:8765 ") == "http://127.0.0.1:8765" and c("workspace_dir", "C:/work") == "C:/work" and c("workspace_dir", "") == ""
    assert json.loads(c("model_fallbacks", [{"provider": "a"}])) == [{"provider": "a"}] and c("voice_char_cap", 5) == "5"
    for k, v in [("sandbox_network", "yes"), ("max_spawns_per_turn", True), ("max_spawns_per_turn", "3"), ("max_spawns_per_turn", 99), ("thinking_level", "x"),
                 ("sidebot_model", 5), ("sidebot_model", "x" * 300), ("otel_forward_url", "ftp://x"), ("workspace_dir", "work"), ("model_fallbacks", "x"),
                 ("model_fallbacks", [1] * 6), ("elevenlabs_api_key", "k"), ("voice_chars_month", "1"), ("auto_config", {})]:
        with pytest.raises(R.SettingError):
            c(k, v)


def test_typed_and_defaults_and_public_view():
    t = lambda k, raw: R.typed(R.BY_KEY[k], raw)       # noqa: E731
    assert t("sandbox_network", None) is False and t("sandbox_network", "1") is True and t("max_spawns_per_turn", "7") == 7 and t("max_turn_minutes", "2.5") == 2.5
    assert t("max_spawns_per_turn", "oops") == 4 and t("model_fallbacks", "not json") == [] and t("model_fallbacks", '{"a":1}') == [] and t("auto_config", None) == {}
    assert t("thinking_level", None) == "medium" and t("otel_forward_url", "http://x") == "http://x"
    assert R.default_str(R.BY_KEY["sandbox_network"]) == "0" and R.default_str(R.BY_KEY["model_fallbacks"]) == "[]" and R.default_str(R.BY_KEY["thinking_level"]) == "medium"
    v = R.public_view(R.BY_KEY["elevenlabs_api_key"], "sk-secret-value")
    assert v["set"] is True and v["value"] is None and "sk-secret" not in json.dumps(v)
    assert R.public_view(R.BY_KEY["max_spawns_per_turn"], "9")["changed"] is True
    assert R.public_view(R.BY_KEY["max_spawns_per_turn"], None)["changed"] is False


# ------------------------------------------------------------------------------------------------ API: registry, export, import, reset, history
def test_registry_endpoint_lists_everything_and_hides_secrets(client, state):
    state.db.set_setting("elevenlabs_api_key", "sk-very-secret")
    body = client.get("/api/settings/registry").json()
    assert set(body["tabs"]) == set(R.TABS) and {s["key"] for s in body["settings"]} == set(R.BY_KEY)
    assert "sk-very-secret" not in json.dumps(body)
    assert next(s for s in body["settings"] if s["key"] == "elevenlabs_api_key")["set"] is True


def test_export_has_no_secrets_and_roundtrips(client, state):
    client.put("/api/settings", json={"max_spawns_per_turn": 9, "thinking_level": "high", "sandbox_network": True})
    state.db.set_setting("elevenlabs_api_key", "sk-very-secret")
    state.db.set_setting("ard_token_hash", "abc")
    exp = client.get("/api/settings/export").json()
    assert exp["format"] == "agent-studio-settings" and exp["settings"]["max_spawns_per_turn"] == 9 and exp["settings"]["thinking_level"] == "high"
    text = json.dumps(exp)
    assert "sk-very-secret" not in text and "elevenlabs_api_key" not in exp["settings"] and "ard_token_hash" not in exp["settings"] and "voice_chars_month" not in exp["settings"]
    client.post("/api/settings/reset", json={"all": True})
    assert client.get("/api/settings").json()["max_spawns_per_turn"] == 4
    dry = client.post("/api/settings/import", json={"data": exp, "dry_run": True}).json()
    assert dry["applied"] is False and {c["key"] for c in dry["changes"]} >= {"max_spawns_per_turn", "thinking_level", "sandbox_network"}
    assert client.get("/api/settings").json()["max_spawns_per_turn"] == 4            # a dry run changes nothing
    done = client.post("/api/settings/import", json={"data": exp, "dry_run": False}).json()
    assert done["applied"] is True and not done["errors"]
    got = client.get("/api/settings").json()
    assert got["max_spawns_per_turn"] == 9 and got["thinking_level"] == "high" and got["sandbox_network"] is True


def test_import_rejects_bad_files_and_values(client, state):
    assert client.post("/api/settings/import", json={"data": {"hello": 1}}).json()["errors"][0]["error"].startswith("this is not an Agent Studio")
    data = {"format": "agent-studio-settings", "settings": {"max_spawns_per_turn": 999, "nope": 1, "elevenlabs_api_key": "stolen", "voice_chars_month": "1",
                                                            "thinking_level": "low", "auto_config": "x", "sandbox_network": False}}
    r = client.post("/api/settings/import", json={"data": data, "dry_run": False}).json()
    msgs = {e["key"]: e["error"] for e in r["errors"]}
    assert "between" in msgs["max_spawns_per_turn"] and "unknown" in msgs["nope"] and "never imported" in msgs["elevenlabs_api_key"] and "never imported" in msgs["voice_chars_month"]
    assert "object" in msgs["auto_config"]
    assert [c["key"] for c in r["changes"]] == ["thinking_level"]                    # the good value still applies
    assert state.db.get_setting("elevenlabs_api_key") is None


def test_import_hands_feature_settings_to_their_owner(client, state):
    data = {"format": "agent-studio-settings", "settings": {"auto_config": {"preference": "quality"}, "ard_settings": {"level": "public", "enabled": True, "level_confirmed": True, "publisher": "agents.acme-corp.com"}}}
    r = client.post("/api/settings/import", json={"data": data, "dry_run": False}).json()
    assert not r["errors"] and state.auto.config()["preference"] == "quality"
    from app.ard.settings import DiscoverySettings
    cur = DiscoverySettings(state.db).get()
    assert cur["publisher"] == "agents.acme-corp.com" and cur["enabled"] is False           # publishing is never switched on by an import
    bad = {"format": "agent-studio-settings", "settings": {"auto_config": {"preference": "nonsense"}, "ard_settings": {"publisher": "example.com"}}}
    r = client.post("/api/settings/import", json={"data": bad, "dry_run": False}).json()
    assert {e["key"] for e in r["errors"]} == {"auto_config", "ard_settings"}
    odd = {"format": "agent-studio-settings", "settings": {"onboarding": {}}}
    assert client.post("/api/settings/import", json={"data": odd, "dry_run": False}).json()["errors"][0]["error"].startswith("secrets and bookkeeping")


def test_import_blob_without_an_owner_is_refused():
    from types import SimpleNamespace

    from app import settings_api as A
    s = SimpleNamespace(auto=None, db=None)
    with pytest.raises(R.SettingError):
        A._blob_apply(s, "memory_config", {"a": 1})           # no memory service on this stand-in state
    with pytest.raises(R.SettingError):
        A._blob_apply(s, "ocsf_config", {"a": 1})


def test_reset_single_and_all(client, state):
    client.put("/api/settings", json={"max_spawns_per_turn": 9, "max_llm_calls_per_turn": 7})
    r = client.post("/api/settings/reset", json={"keys": ["max_spawns_per_turn", "thinking_level"]}).json()
    assert r["reset"] == ["max_spawns_per_turn"]                                       # thinking_level was never changed: nothing to reset
    got = client.get("/api/settings").json()
    assert got["max_spawns_per_turn"] == 4 and got["max_llm_calls_per_turn"] == 7
    for bad in ("elevenlabs_api_key", "voice_chars_month", "auto_config", "nope"):
        assert client.post("/api/settings/reset", json={"keys": [bad]}).status_code == 422
    assert client.post("/api/settings/reset", json={"all": True}).status_code == 200
    assert client.get("/api/settings").json()["max_llm_calls_per_turn"] == 40


def test_history_records_changes_but_never_secret_values(client, state):
    client.put("/api/settings", json={"max_spawns_per_turn": 9})
    client.put("/api/settings", json={"max_spawns_per_turn": 9})                         # same value: no new entry
    state.db.set_setting("elevenlabs_api_key", "sk-very-secret")
    state.db.set_setting("voice_chars_month", "2026-11:5")                                # bookkeeping is not history
    h = client.get("/api/settings/history").json()
    assert [x["key"] for x in h] == ["elevenlabs_api_key", "max_spawns_per_turn"]
    assert h[1]["from"] == "4" or h[1]["from"] is None and h[1]["to"] == "9"
    assert h[0]["to"] == "(changed)" and h[0]["from"] is None and "sk-very-secret" not in json.dumps(h)
    assert h[0]["label"] == "ElevenLabs API key"
    state.db.execute("INSERT INTO settings_log(ts,key,old,new) VALUES(1,'old_removed_setting','a','b')")
    assert client.get("/api/settings/history").json()[0]["label"] == "old_removed_setting"       # keys from older versions still show
    for i in range(520):
        state.db.set_setting("max_spawns_per_turn", str(1 + i % 2 + 10))
    assert state.db.one("SELECT COUNT(*) n FROM settings_log")["n"] <= 501                   # the log is capped


# ------------------------------------------------------------------------------------------------ the generated documentation
def test_settings_doc_is_in_sync_and_covers_every_setting(capsys):
    from app import settings_doc as D
    from app.db import REPO_ROOT
    text = D.render()
    assert (REPO_ROOT / "docs" / "SETTINGS.md").read_text(encoding="utf-8") == text, "run: python -m app.settings_doc --write"
    for s in R.SETTINGS:
        assert f"`{s.key}`" in text
    assert D.main([]) == 0 and "# Settings reference" in capsys.readouterr().out


def test_settings_doc_write(tmp_path, monkeypatch):
    from app import settings_doc as D
    monkeypatch.setattr(D, "REPO_ROOT", tmp_path)
    assert D.main(["--write"]) == 0
    assert (tmp_path / "docs" / "SETTINGS.md").read_text(encoding="utf-8") == D.render()


def test_settings_doc_describes_every_kind_of_allowed_value():
    from app import settings_doc as D
    assert D._allowed(R.BY_KEY["thinking_level"]) == "off, low, medium, high" and D._allowed(R.BY_KEY["sandbox_network"]) == "on / off"
    assert "absolute path" in D._allowed(R.BY_KEY["workspace_dir"]) and "http://" in D._allowed(R.BY_KEY["otel_forward_url"]) and "at most 5" in D._allowed(R.BY_KEY["model_fallbacks"])
    assert D._allowed(R.BY_KEY["sidebot_tools_off"]) == "list" and D._allowed(R.BY_KEY["elevenlabs_api_key"]) == "write-only" and D._allowed(R.BY_KEY["voice_chars_month"]) == "bookkeeping"
    assert D._default(R.BY_KEY["elevenlabs_api_key"]) == "(none)" and D._default(R.BY_KEY["thinking_level"]) == '"medium"' and D._default(R.BY_KEY["sandbox_network"]) == "off"
    assert D._default(R.BY_KEY["agent_spawning_enabled"]) == "on" and D._default(R.BY_KEY["workspace_dir"]) == "empty"
