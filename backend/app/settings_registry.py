"""The single list of every setting Agent Studio keeps in its `settings` table: what it is, where the user finds it, what it
defaults to, what values are allowed, and what it does. The Settings page search, the export/import/reset endpoints,
docs/SETTINGS.md and the consistency tests are all driven from this list, so a setting cannot exist without being described.

Pure data + validation (no database import), so db.py can use SECRET_KEYS without a cycle.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Setting:
    key: str                        # the key in the `settings` table
    label: str                      # what the user reads
    tab: str                        # Settings tab id that exposes it ("" = not shown in Settings)
    kind: str                       # bool | int | float | enum | str | url | path | list | blob | secret | counter
    default: object                 # value used when nothing is stored
    effect: str                     # one plain sentence: what changes when it is set
    help: str = ""                  # id in frontend lib/help.ts
    min: float | None = None
    max: float | None = None
    choices: tuple = ()
    max_len: int | None = None
    where: str = ""                 # where the UI control is (page / section), for docs
    internal: bool = False          # bookkeeping the user never edits (counters, onboarding progress)
    field: str = ""                 # the name the UI/API uses for it when that differs from the key (e.g. otel_enabled -> enabled)
    enforced: str = "server"        # who enforces it: "server" (the backend refuses/uses it) or "server+client"

    @property
    def secret(self) -> bool:
        return self.kind == "secret"


def _s(*a, **k) -> Setting:
    return Setting(*a, **k)


SETTINGS: tuple[Setting, ...] = (
    # ---- Agents & sandbox (tab: harness)
    _s("workspace_dir", "Default workspace folder", "harness", "path", "", "The one folder agents may read and change (each chat can pick another).", "workspace",
       max_len=500, where="Agents & sandbox > Sandbox"),
    _s("sandbox_network", "Allow network inside the sandbox", "harness", "bool", False, "Lets commands run by agents reach the internet. Off = no network at all.", "sandbox",
       where="Agents & sandbox > Sandbox"),
    _s("thinking_level", "Model thinking", "harness", "enum", "medium", "How hard reasoning models think before answering (thinking tokens are billed as output).", "reasoning",
       choices=("off", "low", "medium", "high"), where="Agents & sandbox > Agents building agents"),
    _s("auto_debug_mcp", "Debug failing tools automatically", "harness", "bool", False, "When an MCP server fails to start, an agent investigates in the background (uses model credits).", "mcp",
       where="Agents & sandbox"),
    _s("model_fallbacks", "Fallback models", "harness", "list", [], "If a model fails, the same request continues on the next provider/model listed (at most 5).", "provider",
       max_len=5, where="Agents & sandbox"),
    _s("agent_spawning_enabled", "Let agents create their own sub-agents", "harness", "bool", True, "Master switch for spawn_agent. Each agent also needs it ticked.", "spawn",
       where="Agents & sandbox"),
    _s("max_spawns_per_turn", "Max agent-created sub-agents per turn", "harness", "int", 4, "Stops runaway agent creation.", "spawn", min=1, max=20, where="Agents & sandbox"),
    _s("max_turn_minutes", "Max minutes per turn (0 = no limit)", "harness", "float", 0, "A turn that runs longer than this is stopped.", min=0, max=100000, where="Agents & sandbox > Long runs"),
    _s("max_llm_calls_per_turn", "Max model calls per turn", "harness", "int", 40, "Loop guard: the turn stops after this many model calls.", min=1, max=100000, where="Agents & sandbox > Long runs"),
    _s("agents_can_call_external", "Let agents call external agents", "harness", "bool", True, "Master switch for call_external_agent (Claude Code, Codex...). They bill their own accounts.", "acp",
       where="Agents & sandbox > Agents calling external agents"),
    _s("max_external_calls_per_turn", "Max external-agent calls per turn", "harness", "int", 6, "Limits hand-offs to external agents in one turn.", "acp", min=1, max=1000,
       where="Agents & sandbox > Agents calling external agents"),
    _s("external_timeout_min", "Max minutes per external-agent call", "harness", "float", 120, "An external agent that runs longer is stopped.", "acp", min=1, max=100000,
       where="Agents & sandbox > Agents calling external agents"),
    # ---- Assistant (tab: assistant)
    _s("sidebot_enabled", "Side assistant on", "assistant", "bool", True, "Shows the helper that can guide you around the app.", "assistant", where="Assistant"),
    _s("sidebot_page_context", "Assistant can see what is on screen", "assistant", "bool", True,
       "Lets the assistant read the headings, buttons, form fields and errors of the screen you are on, so its help fits. Passwords, keys and other secrets are never included.", "assistant", where="Assistant"),
    _s("sidebot_provider", "Assistant provider", "assistant", "str", "", "Which provider runs the assistant (empty = the first enabled one).", "assistant", max_len=60, where="Assistant"),
    _s("sidebot_model", "Assistant model", "assistant", "str", "", "Which model runs the assistant (empty = the provider's default).", "assistant", max_len=200, where="Assistant"),
    _s("sidebot_guidance", "Assistant guidance", "assistant", "str", "", "Your own instructions, added after the fixed safety rules (empty = the default guidance).", "assistant-rules",
       max_len=4000, where="Assistant > Its instructions"),
    _s("sidebot_tools_off", "Assistant actions switched off", "assistant", "list", [], "Actions the assistant may not use. Refused on the server and in the browser.", "assistant-rules",
       enforced="server+client", where="Assistant"),
    # ---- Telemetry (tab: telemetry)
    _s("otel_enabled", "Store OpenTelemetry data", "telemetry", "bool", True, "Records spans for every agent run, model call and tool call.", "telemetry", where="Telemetry", field="enabled"),
    _s("otel_inject_env", "Point external agents at this connector", "telemetry", "bool", True, "External agents are launched with OTEL_* variables so they report here.", "telemetry", where="Telemetry", field="inject_env"),
    _s("otel_retention_days", "Telemetry retention (days)", "telemetry", "int", 30, "Older telemetry is deleted automatically.", "telemetry", min=1, max=3650, where="Telemetry", field="retention_days"),
    _s("otel_receiver_url", "This server's URL as seen by agents", "telemetry", "url", "", "The address external agents send telemetry to.", "telemetry", max_len=300, where="Telemetry", field="receiver_url"),
    _s("otel_forward_url", "Forward everything to your collector", "telemetry", "url", "", "Also sends every span to your own OTLP/HTTP collector.", "telemetry", max_len=300, where="Telemetry", field="forward_url"),
    _s("otel_forward_headers", "Collector headers", "telemetry", "secret", "", "Headers sent to your collector (write-only).", "telemetry", where="Telemetry", field="forward_headers"),
    # ---- Voice (tab: voice)
    _s("elevenlabs_api_key", "ElevenLabs API key", "voice", "secret", "", "Lets Agent Studio speak and listen through ElevenLabs.", "api-key", where="Voice", field="api_key"),
    _s("elevenlabs_voice_id", "Voice", "voice", "str", "", "Which voice reads answers aloud.", max_len=80, where="Voice", field="voice_id"),
    _s("elevenlabs_tts_model", "Speech model", "voice", "str", "", "Text-to-speech model.", max_len=80, where="Voice", field="tts_model"),
    _s("elevenlabs_stt_model", "Dictation model", "voice", "str", "", "Speech-to-text model.", max_len=80, where="Voice", field="stt_model"),
    _s("voice_auto_read", "Read answers aloud automatically", "voice", "bool", False, "Every answer is spoken as soon as it arrives.", where="Voice", field="auto_read"),
    _s("voice_dictation", "Show the dictation button", "voice", "bool", True, "Lets you speak your messages.", where="Voice", field="dictation"),
    _s("voice_char_cap", "Monthly character cap (0 = none)", "voice", "int", 0, "Voice stops for the month once this many characters are spoken.", "spend-caps", min=0, max=100000000, where="Voice", field="char_cap"),
    _s("live_provider", "Live mode: provider", "voice", "str", "ollama", "Which provider's model talks in live mode (Ollama keeps it on this PC).", max_len=80, where="Live page"),
    _s("live_model", "Live mode: model", "voice", "str", "", "The model for live mode; empty = the provider's default. Pick a fast one.", max_len=120, where="Live page"),
    _s("live_voice", "Live mode: voice", "voice", "str", "af_heart", "The Kokoro voice used in live mode.", max_len=40, where="Live page"),
    _s("live_speed", "Live mode: speaking speed", "voice", "float", 1.0, "1.0 is normal speed.", min=0.6, max=1.6, where="Live page"),
    _s("live_hangover_ms", "Live mode: end-of-speech wait (ms)", "voice", "int", 350, "How long a pause means you finished. Shorter answers sooner but may cut you off.", min=150, max=1500, where="Live page"),
    _s("live_barge_in", "Live mode: let me interrupt", "voice", "bool", True, "Talking over the assistant stops it. Headphones work best.", where="Live page"),
    _s("live_avatar", "Live mode: show the avatar", "voice", "bool", False, "Start live mode with the animated avatar instead of voice only.", where="Live page"),
    _s("live_agent", "Live mode: agent", "voice", "str", "", "An agent whose instructions shape what is said (live mode uses no tools); empty = none.", max_len=80, where="Live page"),
    _s("live_engine", "Live mode: where it runs", "voice", "enum", "local", "This PC (Whisper, Ollama and Kokoro: nothing leaves it) or Google Gemini Live (cloud).", choices=("local", "gemini"), where="Live page"),
    _s("live_gemini_provider", "Gemini Live: Vertex provider", "voice", "str", "vertex_ai", "Which Google Vertex provider supplies the project and the credentials.", max_len=80, where="Live page"),
    _s("live_gemini_model", "Gemini Live: model", "voice", "str", "gemini-3.8-live", "The Gemini Live model id.", max_len=80, where="Live page"),
    _s("live_gemini_voice", "Gemini Live: voice", "voice", "str", "Puck", "One of Gemini Live's 30 prebuilt voices.", max_len=40, where="Live page"),
    _s("live_gemini_language", "Gemini Live: language", "voice", "str", "en-US", "BCP-47 language code of the speech.", max_len=12, where="Live page"),
    _s("live_gemini_avatar_name", "Gemini Live: avatar name", "voice", "str", "", "A prebuilt Live Avatar (Google's documented example is Ben); used when the avatar is on.", max_len=80, where="Live page"),
    _s("live_gemini_location", "Gemini Live: region", "voice", "str", "", "Empty = the provider's region (or us-central1). Gemini Live needs a regional endpoint.", max_len=40, where="Live page"),
    _s("live_gemini_endpoint", "Gemini Live: endpoint override", "voice", "str", "", "Empty = Google. A ws:// or wss:// address for a proxy or a test server.", max_len=200, where="Live page"),
    _s("live_gemini_max_minutes", "Gemini Live: longest session (minutes)", "voice", "float", 10.0, "A session is ended after this long.", min=1, max=60, where="Live page"),
    _s("live_gemini_cost_cap_usd", "Gemini Live: cost limit per session ($)", "voice", "float", 1.0, "A session is ended when Google's usage report puts its cost above this (an estimate at list prices).", min=0.05, max=100, where="Live page"),
    _s("live_gemini_video_bitrate", "Gemini Live: avatar video bitrate (bps, 0 = Google's default)", "voice", "int", 0, "AvatarConfig.video_bitrate_bps.", min=0, max=20000000, where="Live page"),
    _s("live_gemini_audio_bitrate", "Gemini Live: avatar audio bitrate (bps, 0 = Google's default)", "voice", "int", 0, "AvatarConfig.audio_bitrate_bps.", min=0, max=1000000, where="Live page"),
    _s("live_gemini_prices", "Gemini Live: price table", "voice", "blob", {}, "Dollars per million tokens by modality, the token rates used to estimate between usage reports, and the date they were read from Google's pricing page.", where="Live page"),
    _s("voice_chars_month", "Characters spoken this month", "", "counter", "", "Bookkeeping for the monthly cap.", internal=True),
    # ---- Blobs owned by a feature (their own validation lives in the owner)
    _s("auto_config", "Auto mode", "auto", "blob", {}, "How Auto chooses a provider and model (engine, preference, allow/deny lists, tier models, timeout).", "auto", where="Auto"),
    _s("ard_settings", "Discovery & AI-BOM publishing", "discovery", "blob", {}, "Whether and what Agent Studio publishes (ARD catalog, AI-BOM).", where="Discovery"),
    _s("ard_token_hash", "Discovery access token (hash)", "discovery", "secret", "", "Only a hash is stored; the token is shown once when created.", where="Discovery"),
    _s("ard_push_api_key", "Dependency-Track API key", "discovery", "secret", "", "Used only when you press Push (write-only).", where="Discovery"),
    _s("onboarding", "Set-up guide progress", "", "blob", {}, "Where you are in the first-run guide.", internal=True),
    _s("mcp_catalog_installed", "Public MCP servers added from the catalog", "", "blob", {}, "Which MCP servers came from the catalog, and whether writes were allowed for each.", internal=True),
    # ---- Memory + security export (added with their features)
    _s("memory_config", "Memory", "memory", "blob", {}, "Memory types, backend, retention, review queue and embedding model.", "memory", where="Memory > Settings"),
    _s("memory_cloud_secrets", "Cloud memory credentials", "memory", "secret", "", "Credentials for a cloud memory backend (write-only).", "memory", where="Memory > Settings"),
    _s("ocsf_config", "Security export (OCSF)", "telemetry", "blob", {}, "Where Agent Studio sends security events in OCSF format, and how much detail they carry.", "ocsf",
       where="Telemetry > Security export"),
    # ---- Security (tab: security): guardrails, agent identity, evaluations, red-team
    _s("guardrails_enabled", "Guardrails on", "security", "bool", False, "Master switch: checks what goes into models, what comes out and what tools are asked to do. Each profile then decides what to look for and whether to block.", "guardrails",
       where="Security > Guardrails", field="enabled"),
    _s("guardrail_config", "Guardrail profiles", "security", "blob", {}, "Named guard setups: which guard (rules, open-source safety model, d1, cloud service), where it looks, and whether it only records or blocks.", "guardrails",
       where="Security > Guardrails"),
    _s("guardrail_secrets", "Guardrail credentials", "security", "secret", "", "Keys for cloud guardrail services (write-only).", "guardrails", where="Security > Guardrails"),
    _s("identity_config", "Agent identity", "security", "blob", {}, "Trust domain, token lifetime, whether hand-overs between agents are enforced, and the identity provider (built-in, Okta, Entra, Keycloak, OIDC or SPIRE).", "identity",
       where="Security > Identity"),
    _s("identity_secrets", "Identity provider credentials", "security", "secret", "", "Client secrets for an external identity provider (write-only).", "identity", where="Security > Identity"),
    # ---- Artifacts (tab: artifacts): files agents produce, where they are kept
    _s("artifact_config", "Artifacts", "artifacts", "blob", {}, "Whether files agents produce are kept, which ones, where copies go (this PC, S3, Google Cloud, Azure, WebDAV...), the secret scan and how long they are kept.", "artifacts",
       where="Artifacts > Storage and rules"),
    _s("artifact_secrets", "Artifact storage credentials", "artifacts", "secret", "", "Keys for cloud storages that hold artifacts (write-only).", "artifacts", where="Artifacts > Storage and rules"),
    # ---- Canvas (tab: harness): the document you and an agent edit together
    _s("canvas_config", "Canvas", "harness", "blob", {}, "Whether the canvas is on, whether agent changes are applied straight away (off = you review each one), the size limit and how many versions are kept.", "canvas",
       where="Agents & sandbox > Canvas"),
    _s("apps_config", "Apps", "harness", "blob", {}, "Whether apps are on, whether a builder agent's changes are applied straight away (off = you review each one), whether server apps may run in a container, the preview port, size limits, how many versions are kept, the rate limit of each app's key and your name and email for git commits.", "apps",
       where="Agents & sandbox > Apps"),
    _s("apps_secrets", "App keys and variables", "harness", "secret", "", "Each app's own API key and its environment values, and its git token (write-only; never written into an app's files).", "apps", where="Apps"),
    _s("apps_token_secret", "Apps preview token secret", "harness", "secret", "", "The random secret that signs the short-lived tokens an app preview uses to call your agents (made automatically).", "apps", where="Apps"),
    _s("science_config", "Science Lab", "harness", "blob", {}, "Whether the Science Lab is on, the safety level (it can only be raised, also when imported), which data sources are off, the time between requests, the contact email sent to Crossref, the call budget of an investigation and the cache time.", "science",
       where="Science > Settings"),
    _s("science_secrets", "Science data source keys", "harness", "secret", "", "Free keys for NCBI, NASA ADS, OpenAlex and Wolfram|Alpha (write-only).", "science", where="Science > Settings"),
    _s("science_components", "Science components", "harness", "blob", {}, "Which optional science components are installed in their own environments and the licence you accepted (per machine, never exported).", "", internal=True),
    _s("uikit_config", "Rich components", "harness", "blob", {}, "Whether agents can show charts, time series, maps and cards, the size limits for one component, and the optional map basemap (off by default: maps then use a built-in world outline and load nothing from the internet). Also which AG-UI switches a new agent starts with (rich components, stream endpoint; both on by default); each agent has its own switches.", "ui-components",
       where="Agents & sandbox > Rich components"),
    # ---- Harness rules, API access (blobs owned by their features)
    _s("harness_policy", "Harness rules", "harness", "blob", {}, "Permission mode, loop guard, project instructions, checkpoints, your hooks, tool-call limits and the web tools.", "harness-rules",
       where="Agents & sandbox > Harness rules"),
    _s("web_secrets", "Web search key", "harness", "secret", "", "The Brave Search API key used by the web_search tool (write-only).", "harness-rules", where="Agents & sandbox > Harness rules"),
    _s("access_config", "API and A2A access", "access", "blob", {}, "Whether the OpenAI-compatible API and A2A are switched on, and which provider API calls use by default.", "api-access",
       where="API & A2A"),
    # ---- Runtimes, sandbox and data sources (blobs owned by their features)
    _s("runtime_config", "Runtime targets", "runtimes", "blob", {}, "Which runtimes agents can be deployed to (Docker on this PC, Amazon Bedrock AgentCore, Vertex AI Agent Engine, Microsoft Foundry), their non-secret settings and the image address template.", "runtimes",
       where="Runtimes"),
    _s("runtime_secrets", "Runtime credentials", "runtimes", "secret", "", "Cloud credentials and the model keys a deployed agent runs with (write-only).", "runtimes", where="Runtimes"),
    _s("sandbox_config", "Sandbox", "harness", "blob", {}, "Which kind of sandbox commands run in (Docker container or the experimental micro VM), its memory, CPU and process limits, and the read-only root file system option.", "sandbox",
       where="Agents & sandbox > Sandbox"),
    _s("datasource_config", "Data sources", "data", "blob", {}, "The databases, warehouses and Salesforce orgs agents may read (read-only), their limits and whether personal data is masked.", "data-sources", where="Data sources"),
    _s("datasource_secrets", "Data source credentials", "data", "secret", "", "Passwords, tokens and keys for the data sources (write-only).", "data-sources", where="Data sources"),
    _s("data_products", "Data products", "data", "blob", {}, "Named, owned and classified descriptions of what is in a data source; they appear in the AI-BOM and the discovery catalog.", "data-products", where="Discovery > Data products"),
    _s("workflow_config", "Workflow settings", "harness", "blob", {}, "Whether the Webhook box can be called from outside (off until you switch it on), how often and how large, how many boxes a workflow may have, how many finished runs are kept, and what the HTTP request box may reach.", "wf-settings",
       where="Workflows > Settings"),
    _s("workflow_secrets", "Workflow credentials", "harness", "secret", "", "Tokens and keys the HTTP request box signs in with, kept apart from the workflows (write-only).", "wf-settings", where="Workflows > Settings"),
    _s("ocsf_secrets", "Security export credentials", "telemetry", "secret", "", "Tokens for HTTPS / S3 sinks (write-only).", "ocsf", where="Telemetry > Security export"),
)

BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}
SECRET_KEYS = frozenset(s.key for s in SETTINGS if s.secret)
INTERNAL_KEYS = frozenset(s.key for s in SETTINGS if s.internal)
TABS = ("providers", "local", "auto", "harness", "external", "voice", "telemetry", "assistant", "discovery", "memory", "security", "artifacts", "access", "runtimes", "data", "backup")


class SettingError(ValueError):
    """A value the setting does not accept. The message says why and is safe to show."""


def default_str(s: Setting) -> str:
    d = s.default
    if isinstance(d, bool):
        return "1" if d else "0"
    if isinstance(d, (list, dict)):
        return json.dumps(d)
    return str(d)


def typed(s: Setting, raw: str | None) -> object:
    """The stored string as a Python value (the default when nothing is stored or it is unreadable)."""
    if raw is None:
        raw = default_str(s)
    try:
        if s.kind == "bool":
            return raw == "1"
        if s.kind == "int":
            return int(float(raw))
        if s.kind == "float":
            return float(raw)
        if s.kind in ("list", "blob"):
            v = json.loads(raw or ("[]" if s.kind == "list" else "{}"))
            return v if isinstance(v, list if s.kind == "list" else dict) else s.default
    except (ValueError, TypeError):
        return s.default
    return raw


def coerce(s: Setting, value: object) -> str:
    """Validate `value` for this setting and return the string to store. Raises SettingError with a plain explanation."""
    if s.secret or s.internal:
        raise SettingError(f"'{s.label}' cannot be set this way")
    if s.kind == "bool":
        if not isinstance(value, bool):
            raise SettingError(f"{s.label} must be true or false")
        return "1" if value else "0"
    if s.kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingError(f"{s.label} must be a number")
        if s.min is not None and value < s.min or s.max is not None and value > s.max:
            raise SettingError(f"{s.label} must be between {s.min:g} and {s.max:g}")
        return str(int(value)) if s.kind == "int" else str(float(value))
    if s.kind == "enum":
        if value not in s.choices:
            raise SettingError(f"{s.label} must be one of: {', '.join(s.choices)}")
        return str(value)
    if s.kind in ("str", "url", "path"):
        if not isinstance(value, str):
            raise SettingError(f"{s.label} must be text")
        v = value.strip()
        if s.max_len is not None and len(v) > s.max_len:
            raise SettingError(f"{s.label} is too long (at most {s.max_len} characters)")
        if s.kind == "url" and v and not v.startswith(("http://", "https://")):
            raise SettingError(f"{s.label} must start with http:// or https://")
        if s.kind == "path" and v and not (v[1:3] in (":\\", ":/") or v.startswith(("/", "~"))):
            raise SettingError(f"{s.label} must be an absolute folder path")
        return v
    if s.kind == "list":
        if not isinstance(value, list):
            raise SettingError(f"{s.label} must be a list")
        if s.key == "model_fallbacks" and len(value) > (s.max_len or 5):
            raise SettingError(f"{s.label}: at most {s.max_len or 5} entries")
        return json.dumps(value)
    raise SettingError(f"'{s.label}' is managed by its own page")       # blobs: owned by a feature


def public_view(s: Setting, raw: str | None) -> dict:
    """What the API shows for one setting. Secrets show only whether they are set."""
    d = dict(key=s.key, label=s.label, tab=s.tab, kind=s.kind, effect=s.effect, help=s.help, where=s.where, internal=s.internal,
             min=s.min, max=s.max, choices=list(s.choices), max_len=s.max_len, enforced=s.enforced, secret=s.secret, field=s.field or s.key)
    if s.secret:
        return {**d, "default": None, "value": None, "set": bool(raw), "changed": bool(raw)}
    val = typed(s, raw)
    return {**d, "default": s.default, "value": val, "changed": val != s.default}
