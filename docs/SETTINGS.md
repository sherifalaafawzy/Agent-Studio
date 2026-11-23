# Settings reference

Generated from `backend/app/settings_registry.py` (`python -m app.settings_doc --write`). A test fails if this file is out of date, if a setting used by the code is missing from the registry, or if a setting has no control in the UI.

Every change is recorded in **Settings → Backup & history** (secret values are never recorded). Export/import covers everything below except secrets and bookkeeping; secrets (API keys, tokens, passwords) are never exported or imported.

## Auto

| Setting | Where | Default | Allowed | What it does | Enforced |
|---|---|---|---|---|---|
| `auto_config` — Auto mode | Auto | empty | managed by its own page | How Auto chooses a provider and model (engine, preference, allow/deny lists, tier models, timeout). | server |

## Agents & sandbox

| Setting | Where | Default | Allowed | What it does | Enforced |
|---|---|---|---|---|---|
| `workspace_dir` — Default workspace folder | Agents & sandbox > Sandbox | empty | text, at most 500 characters (absolute path) | The one folder agents may read and change (each chat can pick another). | server |
| `sandbox_network` — Allow network inside the sandbox | Agents & sandbox > Sandbox | off | on / off | Lets commands run by agents reach the internet. Off = no network at all. | server |
| `thinking_level` — Model thinking | Agents & sandbox > Agents building agents | "medium" | off, low, medium, high | How hard reasoning models think before answering (thinking tokens are billed as output). | server |
| `auto_debug_mcp` — Debug failing tools automatically | Agents & sandbox | off | on / off | When an MCP server fails to start, an agent investigates in the background (uses model credits). | server |
| `model_fallbacks` — Fallback models | Agents & sandbox | empty | list (at most 5) | If a model fails, the same request continues on the next provider/model listed (at most 5). | server |
| `agent_spawning_enabled` — Let agents create their own sub-agents | Agents & sandbox | on | on / off | Master switch for spawn_agent. Each agent also needs it ticked. | server |
| `max_spawns_per_turn` — Max agent-created sub-agents per turn | Agents & sandbox | 4 | 1 to 20 | Stops runaway agent creation. | server |
| `max_turn_minutes` — Max minutes per turn (0 = no limit) | Agents & sandbox > Long runs | 0 | 0 to 100000 | A turn that runs longer than this is stopped. | server |
| `max_llm_calls_per_turn` — Max model calls per turn | Agents & sandbox > Long runs | 40 | 1 to 100000 | Loop guard: the turn stops after this many model calls. | server |
| `agents_can_call_external` — Let agents call external agents | Agents & sandbox > Agents calling external agents | on | on / off | Master switch for call_external_agent (Claude Code, Codex...). They bill their own accounts. | server |
| `max_external_calls_per_turn` — Max external-agent calls per turn | Agents & sandbox > Agents calling external agents | 6 | 1 to 1000 | Limits hand-offs to external agents in one turn. | server |
| `external_timeout_min` — Max minutes per external-agent call | Agents & sandbox > Agents calling external agents | 120 | 1 to 100000 | An external agent that runs longer is stopped. | server |
| `canvas_config` — Canvas | Agents & sandbox > Canvas | empty | managed by its own page | Whether the canvas is on, whether agent changes are applied straight away (off = you review each one), the size limit and how many versions are kept. | server |
| `apps_config` — Apps | Agents & sandbox > Apps | empty | managed by its own page | Whether apps are on, whether a builder agent's changes are applied straight away (off = you review each one), whether server apps may run in a container, the preview port, size limits, how many versions are kept, the rate limit of each app's key and your name and email for git commits. | server |
| `apps_secrets` (secret) — App keys and variables | Apps | (none) | write-only | Each app's own API key and its environment values, and its git token (write-only; never written into an app's files). | server |
| `apps_token_secret` (secret) — Apps preview token secret | Apps | (none) | write-only | The random secret that signs the short-lived tokens an app preview uses to call your agents (made automatically). | server |
| `science_config` — Science Lab | Science > Settings | empty | managed by its own page | Whether the Science Lab is on, the safety level (it can only be raised, also when imported), which data sources are off, the time between requests, the contact email sent to Crossref, the call budget of an investigation and the cache time. | server |
| `science_secrets` (secret) — Science data source keys | Science > Settings | (none) | write-only | Free keys for NCBI, NASA ADS, OpenAlex and Wolfram|Alpha (write-only). | server |
| `science_components` — Science components | harness | empty | managed by its own page | Which optional science components are installed in their own environments and the licence you accepted (per machine, never exported). | server |
| `uikit_config` — Rich components | Agents & sandbox > Rich components | empty | managed by its own page | Whether agents can show charts, time series, maps and cards, the size limits for one component, and the optional map basemap (off by default: maps then use a built-in world outline and load nothing from the internet). Also which AG-UI switches a new agent starts with (rich components, stream endpoint; both on by default); each agent has its own switches. | server |
| `harness_policy` — Harness rules | Agents & sandbox > Harness rules | empty | managed by its own page | Permission mode, loop guard, project instructions, checkpoints, your hooks, tool-call limits and the web tools. | server |
| `web_secrets` (secret) — Web search key | Agents & sandbox > Harness rules | (none) | write-only | The Brave Search API key used by the web_search tool (write-only). | server |
| `sandbox_config` — Sandbox | Agents & sandbox > Sandbox | empty | managed by its own page | Which kind of sandbox commands run in (Docker container or the experimental micro VM), its memory, CPU and process limits, and the read-only root file system option. | server |
| `workflow_config` — Workflow settings | Workflows > Settings | empty | managed by its own page | Whether the Webhook box can be called from outside (off until you switch it on), how often and how large, how many boxes a workflow may have, how many finished runs are kept, and what the HTTP request box may reach. | server |
| `workflow_secrets` (secret) — Workflow credentials | Workflows > Settings | (none) | write-only | Tokens and keys the HTTP request box signs in with, kept apart from the workflows (write-only). | server |

## Voice

| Setting | Where | Default | Allowed | What it does | Enforced |
|---|---|---|---|---|---|
| `elevenlabs_api_key` (secret) — ElevenLabs API key | Voice | (none) | write-only | Lets Agent Studio speak and listen through ElevenLabs. | server |
| `elevenlabs_voice_id` — Voice | Voice | empty | text, at most 80 characters | Which voice reads answers aloud. | server |
| `elevenlabs_tts_model` — Speech model | Voice | empty | text, at most 80 characters | Text-to-speech model. | server |
| `elevenlabs_stt_model` — Dictation model | Voice | empty | text, at most 80 characters | Speech-to-text model. | server |
| `voice_auto_read` — Read answers aloud automatically | Voice | off | on / off | Every answer is spoken as soon as it arrives. | server |
| `voice_dictation` — Show the dictation button | Voice | on | on / off | Lets you speak your messages. | server |
| `voice_char_cap` — Monthly character cap (0 = none) | Voice | 0 | 0 to 1e+08 | Voice stops for the month once this many characters are spoken. | server |

## Telemetry

| Setting | Where | Default | Allowed | What it does | Enforced |
|---|---|---|---|---|---|
| `otel_enabled` — Store OpenTelemetry data | Telemetry | on | on / off | Records spans for every agent run, model call and tool call. | server |
| `otel_inject_env` — Point external agents at this connector | Telemetry | on | on / off | External agents are launched with OTEL_* variables so they report here. | server |
| `otel_retention_days` — Telemetry retention (days) | Telemetry | 30 | 1 to 3650 | Older telemetry is deleted automatically. | server |
| `otel_receiver_url` — This server's URL as seen by agents | Telemetry | empty | text, at most 300 characters (http:// or https://) | The address external agents send telemetry to. | server |
| `otel_forward_url` — Forward everything to your collector | Telemetry | empty | text, at most 300 characters (http:// or https://) | Also sends every span to your own OTLP/HTTP collector. | server |
| `otel_forward_headers` (secret) — Collector headers | Telemetry | (none) | write-only | Headers sent to your collector (write-only). | server |
| `ocsf_config` — Security export (OCSF) | Telemetry > Security export | empty | managed by its own page | Where Agent Studio sends security events in OCSF format, and how much detail they carry. | server |
| `ocsf_secrets` (secret) — Security export credentials | Telemetry > Security export | (none) | write-only | Tokens for HTTPS / S3 sinks (write-only). | server |

## Assistant

| Setting | Where | Default | Allowed | What it does | Enforced |
|---|---|---|---|---|---|
| `sidebot_enabled` — Side assistant on | Assistant | on | on / off | Shows the helper that can guide you around the app. | server |
| `sidebot_provider` — Assistant provider | Assistant | empty | text, at most 60 characters | Which provider runs the assistant (empty = the first enabled one). | server |
| `sidebot_model` — Assistant model | Assistant | empty | text, at most 200 characters | Which model runs the assistant (empty = the provider's default). | server |
| `sidebot_guidance` — Assistant guidance | Assistant > Its instructions | empty | text, at most 4000 characters | Your own instructions, added after the fixed safety rules (empty = the default guidance). | server |
| `sidebot_tools_off` — Assistant actions switched off | Assistant | empty | list | Actions the assistant may not use. Refused on the server and in the browser. | server+client |

## Discovery & AI-BOM

| Setting | Where | Default | Allowed | What it does | Enforced |
|---|---|---|---|---|---|
| `ard_settings` — Discovery & AI-BOM publishing | Discovery | empty | managed by its own page | Whether and what Agent Studio publishes (ARD catalog, AI-BOM). | server |
| `ard_token_hash` (secret) — Discovery access token (hash) | Discovery | (none) | write-only | Only a hash is stored; the token is shown once when created. | server |
| `ard_push_api_key` (secret) — Dependency-Track API key | Discovery | (none) | write-only | Used only when you press Push (write-only). | server |

## Memory

| Setting | Where | Default | Allowed | What it does | Enforced |
|---|---|---|---|---|---|
| `memory_config` — Memory | Memory > Settings | empty | managed by its own page | Memory types, backend, retention, review queue and embedding model. | server |
| `memory_cloud_secrets` (secret) — Cloud memory credentials | Memory > Settings | (none) | write-only | Credentials for a cloud memory backend (write-only). | server |

## Security

| Setting | Where | Default | Allowed | What it does | Enforced |
|---|---|---|---|---|---|
| `guardrails_enabled` — Guardrails on | Security > Guardrails | off | on / off | Master switch: checks what goes into models, what comes out and what tools are asked to do. Each profile then decides what to look for and whether to block. | server |
| `guardrail_config` — Guardrail profiles | Security > Guardrails | empty | managed by its own page | Named guard setups: which guard (rules, open-source safety model, d1, cloud service), where it looks, and whether it only records or blocks. | server |
| `guardrail_secrets` (secret) — Guardrail credentials | Security > Guardrails | (none) | write-only | Keys for cloud guardrail services (write-only). | server |
| `identity_config` — Agent identity | Security > Identity | empty | managed by its own page | Trust domain, token lifetime, whether hand-overs between agents are enforced, and the identity provider (built-in, Okta, Entra, Keycloak, OIDC or SPIRE). | server |
| `identity_secrets` (secret) — Identity provider credentials | Security > Identity | (none) | write-only | Client secrets for an external identity provider (write-only). | server |

## Artifacts

| Setting | Where | Default | Allowed | What it does | Enforced |
|---|---|---|---|---|---|
| `artifact_config` — Artifacts | Artifacts > Storage and rules | empty | managed by its own page | Whether files agents produce are kept, which ones, where copies go (this PC, S3, Google Cloud, Azure, WebDAV...), the secret scan and how long they are kept. | server |
| `artifact_secrets` (secret) — Artifact storage credentials | Artifacts > Storage and rules | (none) | write-only | Keys for cloud storages that hold artifacts (write-only). | server |

## API & A2A

| Setting | Where | Default | Allowed | What it does | Enforced |
|---|---|---|---|---|---|
| `access_config` — API and A2A access | API & A2A | empty | managed by its own page | Whether the OpenAI-compatible API and A2A are switched on, and which provider API calls use by default. | server |

## Runtimes

| Setting | Where | Default | Allowed | What it does | Enforced |
|---|---|---|---|---|---|
| `runtime_config` — Runtime targets | Runtimes | empty | managed by its own page | Which runtimes agents can be deployed to (Docker on this PC, Amazon Bedrock AgentCore, Vertex AI Agent Engine, Microsoft Foundry), their non-secret settings and the image address template. | server |
| `runtime_secrets` (secret) — Runtime credentials | Runtimes | (none) | write-only | Cloud credentials and the model keys a deployed agent runs with (write-only). | server |

## Data sources

| Setting | Where | Default | Allowed | What it does | Enforced |
|---|---|---|---|---|---|
| `datasource_config` — Data sources | Data sources | empty | managed by its own page | The databases, warehouses and Salesforce orgs agents may read (read-only), their limits and whether personal data is masked. | server |
| `datasource_secrets` (secret) — Data source credentials | Data sources | (none) | write-only | Passwords, tokens and keys for the data sources (write-only). | server |
| `data_products` — Data products | Discovery > Data products | empty | managed by its own page | Named, owned and classified descriptions of what is in a data source; they appear in the AI-BOM and the discovery catalog. | server |

## Bookkeeping (not editable)

- `voice_chars_month` — Bookkeeping for the monthly cap.
- `onboarding` — Where you are in the first-run guide.
- `mcp_catalog_installed` — Which MCP servers came from the catalog, and whether writes were allowed for each.
- `science_components` — Which optional science components are installed in their own environments and the licence you accepted (per machine, never exported).

## Settings kept elsewhere

These live in their own tables or objects and have their own validation; the Settings page shows them in the tab named.

- **Providers** (tab *Model providers*): enabled, spend cap (USD) and window, max output tokens per call (16 to 1,000,000), default / pro / flash model, authentication mode and credentials (write-only), API base, extra headers (write-only), Ollama context window, credits.
- **External agents** (tab *External agents*): command, arguments, environment (write-only), enabled, max turns per session (1 to 1000), monthly turn / minute / reported-cost caps.
- **MCP servers, skills, agents, knowledge bases**: managed on their own pages.
- **Catalog** (page *Catalog*): public MCP servers are added with one click and keep their credentials write-only; servers that can change things need a confirm tick, and servers with a read-only switch start read-only. **Cloud accounts** (Catalog > ML tools): provider credentials are write-only and *Allow create/delete* is off by default. **ML frameworks** (Ludwig, PyTorch, Keras) are installed one at a time after you accept the size and licence.
- **Runtimes** (tab *Runtimes*): which targets are on, their settings, the image address template and the model keys a deployed agent runs with (write-only). Cloud credentials are write-only; deploying, switching traffic, rolling back and deleting always ask for a confirmation. Per agent: *Where it runs* in the agent editor.
- **Sandbox** (tab *Agents & sandbox*): kind (Docker container, or the experimental micro VM, off until switched on and never imported), memory, CPUs, processes and the read-only root option; the network switch is `sandbox_network`. Per agent: *Sandbox* in the agent editor.
- **Data sources** (tab *Data sources*): databases, warehouses and Salesforce orgs, read-only, with row / size / time limits and optional masking of personal data; credentials are write-only and are not exported or imported. **Data products** (page *Discovery*): names, owners and sensitivity labels, shown in the AI-BOM and discovery catalog. Per agent: the sources it may read.
- **Workflows** (page *Workflows* > Settings) fields and defaults: `webhooks_enabled=false`, `hook_rate_per_min=30`, `hook_max_body_kb=256`, `hook_wait_max_s=30`, `max_nodes=60`, `run_retention=100`, `http_timeout_s=30`, `http_max_kb=512`, `http_allow_hosts=[]`. Webhooks are off until switched on and are never switched on by an import; credentials for the HTTP request box are write-only and never exported.
- **Auto** (tab *Auto*) fields and defaults: `enabled=true`, `engine="rules"`, `preference="balanced"`, `allow=[]`, `deny=[]`, `fallback=true`, `timeout_s=4.0`, `tier_models={"flash": "", "pro": ""}`.
- **Artifacts** (tab *Artifacts*) fields and defaults: `enabled=true`, `capture="new"`, `max_file_mb=25`, `max_per_turn=50`, `secret_scan="redact"`, `retention_days=0`, `policy=[]`, `default_storages=["local"]`. Storages: this PC (always), Amazon S3, S3-compatible, Google Cloud Storage, Azure Blob (SAS), WebDAV; credentials are write-only.
- **Discovery** (tab *Discovery & AI-BOM*) fields and defaults: `enabled=false`, `publisher=""`, `base_url=""`, `level="public"`, `level_confirmed=false`, `sign=false`, `port=8766`, `publish={"agents": false, "mcp": false, "skills": false, "bom_system": false, "bom_agents": false, "bom_mcp": false, "bom_models": false}`, `push_url=""`, `push_project="Agent Studio"`, `push_version=""`, `push_level="public"`.
