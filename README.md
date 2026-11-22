# Agent Studio

Model-agnostic desktop agent app: **Next.js** UI + **FastAPI** backend on **Google ADK**, providers via **LiteLLM**,
**Electron** shell (packaging later). Per-provider hard spend caps enforced *before* every model call.

## Run (Windows)
Prereqs: Python ≥ 3.11, Node ≥ 20.
```powershell
npm install            # root dev tooling (concurrently)
npm run setup          # backend venv + pip deps + frontend deps  (one time)
npm run dev            # API http://127.0.0.1:8765  +  UI http://127.0.0.1:3100
```
Open http://127.0.0.1:3100 → Chat → provider **Mock** (default; no keys, no spend).

```powershell
npm test               # backend tests (guard, harness, sandbox, agents; Docker tests auto-skip without Docker)
npm run demo:cap       # a call being refused by the cap guard
npm run demo:p2        # Phase 2: sub-agents, verified edits + rollback, sandbox, fail-closed, guard halt
npm run demo:p3        # Phase 3: ACP client (scripted fake agent) + probe of installed real agents  [-- --live claude = 1 tiny real prompt]
npm run build:sandbox  # (once) build the Docker sandbox image agent-studio-sandbox:1
npm run check:mcp      # launch the default-on MCP servers for real and list their tools
npm run dev:desktop    # + Electron window (first: npm --prefix electron install)
```

## Layout
```
backend/app/db.py        SQLite (providers, ledger, reservations)
backend/app/pricing.py   $/1M-token lookup: overrides -> LiteLLM cost map -> conservative fallback (never free)
backend/app/providers.py provider catalog, settings store, LiteLLM kwargs
backend/app/budget.py    cost ledger + BudgetGuard (reserve -> settle/release)   <- the make-or-break piece
backend/app/chat.py      ADK LlmAgent + LiteLlm; guard wired as before/after/on_error model callbacks
backend/app/main.py      FastAPI routes (providers, chat, billing, agents, mcp, skills, settings, sandbox)
backend/app/agents.py    agent definitions + seeded team (engineer[pro] -> explorer/coder/reviewer[flash], assistant)
backend/app/harness/     sandbox.py (Docker, fail-closed), files.py (path jail + edit engine), verify.py, tools.py
backend/app/mcp_registry.py  curated MCP servers (pinned) + ADK McpToolset builder
backend/app/skills.py    SKILL.md registry (built-in skills/ + agent-authored data/skills)
backend/app/mock_llm.py  scriptable offline model for zero-spend demos/tests
backend/app/acp_agents.py ACP client: runs external agents (Claude Code, Codex, Cursor, Antigravity, Grok Build; Aider/OpenHands via cli_agents.py) on a dedicated event loop
docker/sandbox/          sandbox image (python + git + ripgrep + node + ruff + pytest, non-root)
frontend/                Next.js (static export): Chat, Agent Studio, Billing, Settings
electron/                shell structure (dev loads Next; packaged spawns PyInstaller sidecar) - later phase
docs/gcp-backstop/       Budget -> Pub/Sub -> Cloud Function kill-switch + quota guidance
skills/                  starter skills: skill-creator, repo-orientation, code-review, debugging, test-writing, git-workflow, research-and-summarize, plan-and-delegate
scripts/                 setup + cap-guard demo
```

## How the guard works
Per provider, per window (lifetime / daily / monthly): refuse unless
`spent + in-flight reservations + worst-case(call) <= cap`, where worst-case = pessimistic input-token estimate +
the **full** `max_output_tokens` (the request is clamped to it, so the bound is real). Reservations are taken under a
lock (concurrent calls cannot jointly overshoot), then settled to actual provider-reported usage; no usage reported →
charged the worst case; unknown model price → priced at a high fallback, never zero; failed calls release their reservation.

Because it reserves worst case, a cap is only fully usable if `max_output_tokens` is small relative to it — by design.

## Phase 2: agents + harness
- **Agents** (ADK): each agent has tier (pro/flash/custom), tools, MCP servers, skills and sub-agents. Sub-agents are ADK `AgentTool`s
  (own session = isolated context). Tiers resolve per provider (Settings: Pro/Flash model). Every model call of every agent in the tree
  passes the same per-provider budget guard; one refusal halts the whole turn, and a per-turn call limit stops runaway loops.
- **edit_file** = exact search/replace (unique match, whitespace-tolerant fallback, read-before-edit) followed by a MANDATORY verify step
  (python ast + ruff, json/toml/yaml, node --check, bash -n). A failing check rolls the file back and returns the errors; unverifiable
  types report `skipped` rather than pretending.
- **Sandbox**: shell/lint run in a Docker container (non-root, caps dropped, no-new-privileges, mem/cpu/pid limits, network off by default,
  only the workspace mounted). Docker or image missing => refused, never run on the host.
- **MCP**: filesystem/fetch/git/memory default-on; GitHub (remote), Playwright, Brave, Context7 one-click in Settings. They run on the host.

## Phase 3: external agents over ACP
Agent Studio is an **ACP client** ([Agent Client Protocol](https://agentclientprotocol.com): JSON-RPC over stdio). It starts an agent CLI as a
subprocess, streams its text / thinking / tool calls to **External agents**, and answers what the agent asks: permissions, questions, plan approvals,
and file reads/writes (the latter path-jailed to the workspace).

| Agent | Launch command (editable in Settings) | One-time setup |
|---|---|---|
| Claude Code | `npx -y @agentclientprotocol/claude-agent-acp@0.101.0` | `claude` login, or `ANTHROPIC_API_KEY` in its env |
| OpenAI Codex | `npx -y @agentclientprotocol/codex-acp@2.5.0` | `npx -y @openai/codex@0.167.0 login`, or `OPENAI_API_KEY` / `CODEX_API_KEY` in its env |
| Aider (headless CLI adapter, no ACP) | `aider --message-file ...` | `uv tool install --python 3.12 aider-chat==0.86.2`; `--model` in the args + your provider key |
| OpenHands (headless CLI adapter; WSL on Windows) | `openhands --headless --json ...` | in WSL: `uv tool install openhands==1.16.0 --python 3.12`; `LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL` in its env |
| Cursor | `agent acp` | install Cursor CLI, `agent login` |
| Antigravity | `agy acp` | `agy acp --login` |
| Grok Build | `grok agent stdio` | install Grok Build, sign in |

Claude Code is the documented default for large coding tasks. Aider and OpenHands have no usable ACP, so `backend/app/cli_agents.py` drives their
headless CLIs behind the same `call_external_agent` tool (workspace cwd, timeout, kill-tree cancel, output cap, usage parsed if printed else "unknown").
They have no per-action permission protocol: `ask`/`reads_auto` ask once before the whole unattended run, `readonly` works for Aider (`--dry-run`) and is refused
for OpenHands. "Test connection" on these runs only `--version` (no model call). Any other ACP-over-stdio CLI can be added in Settings.

**Read this before using them**
- **The per-provider spend caps do NOT apply.** These agents call their own models on their own accounts. Agent Studio records what they *report*
  (Billing -> External agents) and caps turns per session (default 30). Set real limits with each vendor. A fresh Claude Code session carries ~36k tokens of
  context (~$0.15 for a one-word answer when I measured it).
- Permission policy per run: *ask for everything* (default) | *auto-allow reads, ask the rest* | *read-only* (auto-reject anything that is not a read).
  Network fetches are never auto-allowed. Agent modes such as Claude's `bypassPermissions` remove the prompts entirely; the UI warns when selected.
- The agent's own shell and tools run **as your user, unsandboxed** (as they would in a terminal). Only file access it delegates to us is jailed.
- Agent processes get a minimal environment (no Agent Studio provider keys); only the env you store for that agent is added. Keys are never returned by the API.
- Per-agent facts that were not live-tested here (no CLI installed): Cursor, Antigravity, Grok Build. Claude was tested live end to end.

Implementation notes: all ACP work runs on its own event loop thread (Windows `ProactorEventLoop`), because `uvicorn --reload` on Windows uses a loop that cannot
spawn subprocesses. Cursor's `cursor/ask_question` and `cursor/create_plan` extension requests are supported (rendered as cards); other `cursor/*` notifications are shown as notes.

## Phase 4: streaming, saved chats, agent-built sub-agents, OpenTelemetry

- **Streaming + stop.** `POST /api/chat/stream` (SSE) streams the answer token by token plus live `agent_start`, `tool_call`, `tool_result`, `agent_text`, `model_call` and `agent_end` events. Closing the connection (the Stop button) cancels the turn; a model call that may already have run is charged at the worst case, so no spend is hidden and no reservation leaks.
- **Saved chats.** Every turn is stored in SQLite (`chat_sessions`, `chat_messages`) and listed in the Chat sidebar. After a restart the ADK session is rebuilt from the stored user/assistant text (tool calls and results are not replayed into the model's context).
- **Agents that build agents.** An agent with *Create its own sub-agents* ticked gets a `spawn_agent` tool (name, instruction, task, tools, MCP servers, tier). Rules: it can only grant tools / MCP servers it holds itself, never `create_skill` / `propose_mcp_server`; spawned agents cannot spawn; limit per turn (default 4); all model calls pass the same spend caps. Switches: per agent (Agent Studio page; the seeded `engineer` starts enabled, every other agent off) and a master switch in Settings. Saved with the chat; "Save as permanent agent" promotes one.
- **Runtime visibility.** Chat → *Agents* panel lists every run in the turn (root, built-in sub-agents, agent-created ones) with task, definition, tool calls, output, model calls and cost; saved with the chat so old runs can be reopened.
- **OpenTelemetry connector** (Settings → Telemetry, viewer at `/telemetry/`). Stores spans for every Agent Studio agent (turn → agent → llm/tool calls, sub-agent tree intact) and one span per turn / tool call of each external ACP agent. External agents are launched with `OTEL_EXPORTER_OTLP_*` pointing at the built-in receiver (`POST /v1/traces|metrics|logs`, loopback only, OTLP/HTTP **JSON** only), so whatever they export is stored too (for Claude Code: metrics + events; prompt / tool-content logging is never enabled). Optional forwarding to your own collector; retention setting; header values are write-only.

## AI bill of materials + Agentic Resource Discovery

Agent Studio can describe itself in standard formats. Everything is read-only until you turn publishing on.

- **AI-BOM (CycloneDX 1.7).** *Discovery & AI-BOM* in the sidebar previews, validates (against the official JSON schema, vendored in
  `backend/app/aibom/schemas/`) and downloads a bill of materials of the whole system or of one agent, MCP server, model or skill:
  Python and npm dependencies (versions, purls, licences, npm integrity hashes), hosted and local models (pinned Hugging Face
  revision, licence, SHA-256 of every weight file), agents and their graph, MCP servers, skills, knowledge bases (hashes only),
  vector stores, Docker images and data flows. Everything is discovered from the live registries, so new models or MCP servers
  appear on their own. From a terminal: `cd backend && .venv\Scripts\python -m app.aibom --level public --out bom.json --validate`.
- **Redaction.** `public` (default), `internal`, `full` (adds agent instruction text). At every level: no keys, tokens, passwords,
  environment values, chats, document contents, local paths or user names.
- **Not in the BOM** (it says so inside the document): hashes of Python wheels, Docker image digests, training data and licences
  of hosted models, dependencies of MCP packages fetched at run time, installed versions of external harnesses, vulnerability data.
- **ARD publishing** ([spec v0.91](https://github.com/ards-project/ard-spec), a proposal). Settings > *Discovery & AI-BOM*: name a domain
  you control, pick what to publish and a level. A separate read-only server (127.0.0.1 only, port 8766) serves
  `/.well-known/ard.json` (and the older `ai-catalog.json` name), the BOMs and skill files; or export everything as a zip and host it
  yourself. Agent Studio never exposes anything itself: put your own domain, tunnel or web host in front. Non-public levels need an access token.
  ARD has no bill-of-materials field, so each entry references its CycloneDX BOM through `trustManifest.attestations` (with the sha256).
  Agents and MCP servers use `application/vnd.agent-studio.*+json` because they do not speak A2A or MCP at a URL.
- **Signing (optional).** Detached JWS (EdDSA), key kept in the data folder, public key at `/.well-known/jwks.json`. ARD defines no
  verification procedure, so a signature only shows who signed; nothing is called "verified".
- **Push (optional).** To Dependency-Track (`PUT /api/v1/bom`), only on request, after you review the exact payload.

## Easy setup for non-technical users

- **Set-up guide** (`/setup/`, sidebar footer button, Ctrl+K "Set-up guide"). A first-time visitor is sent there once; it is skippable, resumable (a small "Continue set-up" reminder appears) and re-openable any time. Steps: welcome + PC check, how agents are powered (local / online / both), Ollama, a model, an online provider key (links to Settings; keys are never typed into a chat), work folder, coding agents, side assistant, first chat or agent, summary. People who already have chats are never interrupted.
- **Ollama in one click** (Windows; Settings > *Local models*). Downloads `OllamaSetup.exe` from the official GitHub release, checks its SHA-256 against GitHub's published digest (else the winget manifest; otherwise you must tick "unverified"), runs it silently for your user, then checks `127.0.0.1:11434` and can start Ollama if it is stopped. If the silent run fails it opens the normal installer. macOS/Linux get instructions only; no script is piped to a shell.
- **Model library.** A curated starter list plus *Find new models* (reads the public `ollama.com/search` and `/library/<model>/tags` pages, cached and rate-limited, never `ollama.com/api`; falls back to the built-in list if the pages change). Each size shows whether it should run well on this PC (memory/graphics estimate). Downloads show one overall percentage and can be stopped (a retry resumes).
- **Coding agents in one click** (set-up guide and Settings > *External agents*). Data-driven from the official ACP registry (cached, bundled snapshot as fallback). Install order: npm pinned to a version public for 14+ days, uv, winget, registry archive with verified SHA-256, `npx` registration, and only then the vendor's `irm | iex` script, which needs a ticked confirmation next to the exact command (or "copy it and run it yourself"). OpenHands (needs WSL) and Aider (not ACP) are explained, not installed.
- **Agent wizard** (Agent Studio > *+ New*). Purpose, name, model, abilities in plain words with risk labels, permissions, instructions ("Draft it for me" is one spend-guarded model call), review, then a *Try it* chat. Eight starter profiles. It saves the same definition the *Advanced editor* does.
- **"i" icons.** Plain-language help (`frontend/lib/help.ts`) next to concepts across the app.
- **Assistant** (Settings > Assistant). See the exact prompt, add your own guidance after the locked safety rules, and switch individual assistant actions off (refused server-side and in the browser).
- API: `/api/easy/*` (system, onboarding, ollama, agents, wizard). Backend code: `backend/app/easy/`.


## Settings, memory, telemetry and security export

- **Settings** (`/settings/`): every setting is described once in `backend/app/settings_registry.py`; `docs/SETTINGS.md` is generated from it
  (`python -m app.settings_doc --write`). The page has a search box, per-setting reset, an unsaved-changes guard, and *Backup & history*
  (export / import with a preview, reset all, what changed). Secrets are never exported or imported.
- **Memory** (`/memory/`): kinds (facts, past chats, people and things, how-tos, project notes, working memory), scopes (agent, user, workspace,
  global), a review queue, per-memory provenance, export/import, forget-by-description. Backends: SQLite on this PC (default), a knowledge-base vector
  store, Vertex AI Memory Bank, Bedrock AgentCore Memory, or your own Mem0 server. Cloud backends are opt-in and say that memory text leaves the PC.
  Agents get `memory_search`, `memory_save`, `memory_forget` and `memory_notes` when memory is switched on for them.
- **Telemetry overview** (`/telemetry/`): KPIs, cost per day, anomalies (cost spikes, loops, slow tools, repeated failures), top agents/models/tools,
  plain-language errors, run comparison, "explain this run" (rules, no model), saved views and CSV/JSON export.
- **Security export (OCSF)** (Settings > Telemetry): Agent Studio's events as OCSF 1.10.0 to a rotating file, an HTTPS webhook / Splunk HEC, an S3 bucket
  or syslog. Prompts and answers are never sent. The vendored schema is refreshed with `python scripts/vendor_ocsf.py`.

## Catalog: domain skills, public MCP servers, model trainer, cloud ML

Open **Catalog** in the sidebar (three tabs). Nothing is added until you click, and anything that can change data starts read-only or asks first.

- **Skills** - 24 ready-made skills in eight domains (accounting, sales, marketing, manufacturing, engineering, data, machine learning, deep learning). Each has a "When to use" and a "Safety" paragraph; add one to an agent with one click, or read it first.
- **MCP servers** - 33 vetted public servers (`backend/app/mcp_catalog.py`). Each entry was checked on npm / PyPI (or by contacting the remote endpoint) on 2026-11-22, is pinned to a release at least 14 days old, and says what it can change and where your data goes. Credentials are write-only. How far a server can be limited is shown: *read-only* (cannot change anything), *read-only switch* (starts read-only; turning writes on needs a confirm), *limited by the key* / *can change things* (need a confirm tick; give them a read-only key where you can).
- **ML tools** - the **Model trainer** (Ludwig, PyTorch, Keras/TensorFlow, each in its own isolated environment, installed only after you accept the size and licence; training runs as a cancellable background job on a CSV in your workspace) and **Cloud accounts** (AWS SageMaker, Google Vertex AI, Azure ML, DigitalOcean, RunPod, Oracle OCI Data Science). Cloud access is **read-only by default**; creating or deleting resources costs money, is switched off until you allow it, and always needs a preview plus an explicit confirm.

Agents use the trainer and cloud tools through two built-in MCP servers (`trainer`, `cloudml`), which are off until enabled. The AI-BOM lists catalog servers (publisher, read-only kind, whether writes are allowed) and never credentials.

### The MCP servers page

**MCP servers** is a list on the left and the selected server on the right (two screens with a Back button on a phone).

- **List:** servers are grouped as *Needs setup*, *Installed*, *Built-in tools* and *Available from the catalog*. Each row shows a status dot and badges (*read-only* / *can write*, *data leaves PC*). Search, a state filter (All, Ready, Off, Needs approval) and, under *More filters*, filters by access and by area. Arrow keys, Home, End and Enter work in the list.
- **Details:** tabs for *Overview*, *Settings* (credentials, catalog settings, and the panel for that server: Browser use under Playwright, the decision-model install, forecasting models, the trainer, cloud accounts), *Tools* and *Health & logs* (test button, last result). Rarely used options and *Delete* sit under *Advanced*.
- **Add server:** one button, three steps: choose catalog, your own server (local command or remote URL), or an OpenAPI file; fill in settings (a server that can change things needs an explicit tick); then test it.
- **Links:** `/mcp/?server=<id>` opens a given server (the Catalog page and the harness issue bar use it).

## Security: guardrails, agent identity, scorecard, red team, evaluations

Everything here is off or in "record only" until you switch it on. Settings > **Security** has guardrails and identity; the **Security** page has the scorecard, red-team tools and activity; **Evaluations** has test sets.

- **Guardrails** (`backend/app/guardrails/`) - named profiles that check what goes *into* a model (including tool results, the usual road for indirect prompt injection), what comes *out*, and what *tools* are asked to do. Kinds: built-in rules (personal data, secrets, injection phrases, your own words and patterns); an open-source safety model run through Ollama or any OpenAI-compatible server (Llama Guard, ShieldGemma, Granite Guardian, or your own policy); the local **d1 decision model** (shares Auto's worker); and cloud guardrails - Amazon Bedrock `ApplyGuardrail`, Azure AI Content Safety with Prompt Shields, Google Model Armor (these send the checked text off this PC and say so). Per profile: where it looks, *record only / block*, and whether a guard that cannot answer carries on or stops. Agents use the default profiles, pick their own, or none. Every flag, block and failure is audited (length only, never the text) and sent to the OCSF export. Output blocking is post-hoc for text already streamed to the screen: the saved answer is replaced and flagged.
- **Agent identity** (`backend/app/identity/`) - every agent has a SPIFFE-style id (`spiffe://<trust domain>/agent/<id>`), can be issued short-lived scoped Ed25519 tokens, and hand-overs between agents are checked and logged with **RFC 8693 token exchange** (the `act` claim records who acted for whom; scopes only shrink; chains are limited to 3). `enforce` turns a refused hand-over from a log entry into a refusal. The built-in issuer is the default; Okta, Microsoft Entra, Keycloak, any OIDC server and SPIFFE/SPIRE can issue instead - they authenticate Agent Studio as a workload, they cannot tell agents apart (Entra's delegation is the on-behalf-of grant, SPIRE JWT-SVIDs cannot be exchanged). HTTP MCP servers can opt in with `AGENT_STUDIO_IDENTITY=1` to receive a short-lived token whose audience is that server (RFC 8707 resource indicator). The AI-BOM and discovery catalog list ids and scopes, never tokens or keys.
- **Security scorecard** (`/security/`) - the OWASP Top 10 for LLM Applications (2025) and for Agentic Applications (2026, ASI01-ASI10), each risk mapped to controls with a live pass / partly / missing status computed from your real settings, what to do about it, and a Markdown report. It measures configuration, not whether an attack works.
- **Per-agent scorecards** (`/security/` > Per agent, or `/security/?agent=<id>`) - the same checks judged on one agent's own configuration: its guardrail choice, tools, permission mode, MCP servers, memory, knowledge bases, data sources, sandbox, provider and framework, who can call it over the API or A2A, where it runs, its identity and the remote agents it may call. A check that cannot apply to an agent (for example the sandbox check for an agent that cannot run commands) is marked not applicable, with the reason, and left out of its score; checks about the whole app (telemetry, security export, AI-BOM, built-in protections) read the same for every agent. The overall card judges the app's settings and does not average the agents; an agent that keeps every default scores exactly the overall result on the app-wide checks, and the overall card shows the average and the weakest agent. A ranking lists every agent weakest first with its most serious issue, filters and sorting; each agent has a score badge in the agent list and a bar in its editor, every "what to do" link opens that agent's editor at the right section (`/agents/?agent=<id>#ag-guardrails`), and each card exports as Markdown or JSON (`/api/security/agents/<id>?format=md`, `?download=1`; all agents: `/api/security/agents?format=md|full`). The AI-BOM and the discovery catalog carry only the score and the counts of failing and partly-met checks, and only at the `internal` level and above, never what is weak.
- **Red team** (`/security/#redteam`) - garak, PyRIT and promptfoo, each installed on its own after you accept its size and licence, then pointed at one of your agents through a temporary token-protected OpenAI-style address on this server (the agent keeps its guardrails and spend limits; probes are not kept in your chat history; a hard probe limit applies). The run page counts probes sent, probes a guardrail stopped, and failures. The tool command lines follow each tool's documentation but were not run against a live install.
- **Evaluations** (`/evals/`) - test sets of prompts with checks (contains, equals, regex, JSON, length, or a judge model), run against an agent with results, cost and history. Export a set for promptfoo or Inspect, and a finished run for DeepEval, Ragas, CSV or JSONL.

## Artifacts and rich answers

**Artifacts** are the files and results agents produce (reports, charts, data, code, models). When an agent finishes a turn, files that appeared in its workspace are kept as artifacts; an agent can also keep one on purpose with the `save_artifact` tool, and you can keep any answer with the **Keep** button. The **Artifacts** page (sidebar) searches names, tags and file contents, filters by kind, agent, storage, origin, date and size, previews text, Markdown, CSV, JSON, images and PDFs, downloads, copies a link, opens the originating chat or run, and deletes (after a confirmation).

* **Storages** (Artifacts > Storage and rules, or Settings > Artifacts): a folder on this PC (always on), Amazon S3, S3-compatible services (MinIO, Cloudflare R2, DigitalOcean Spaces...), Google Cloud Storage, Azure Blob (SAS token) and WebDAV. Several can be on at once; rules choose which kinds of file go where (first match wins). Credentials are write-only. Copies to the cloud are made in the background with retries and staged on disk until they land.
* **Secret scan** before a copy leaves this PC: remove secret-looking values from text (the default), or refuse to send that file. The copy on this PC is never changed. Binary files cannot be scanned.
* **Safe serving**: files are served with `nosniff` and a sandboxing CSP; HTML, XML and scripts are shown as text, never run. Retention (days) deletes old artifacts from every storage. The AI-BOM lists counts and storage kinds only, never names or content.
* **Not tested live**: only the folder on this PC has been used for real. S3, GCS, Azure and WebDAV are tested against in-memory fakes of their APIs.

**AG-UI.** `POST /api/agui` runs a chat turn and streams it as [AG-UI](https://docs.ag-ui.com) events (text, reasoning, tool calls, sub-agent runs, `STATE_SNAPSHOT`/`STATE_DELTA`, activity messages for files, memory and guardrails, custom events). An external agent's question ends the AG-UI run with an `interrupt` outcome while the run keeps waiting; post again with `resume` to answer it. The chat page itself uses Agent Studio's own stream, and renders answers with a safe whitelist: Markdown, code with copy, tables, ` ```chart ` (bar and line, with a legend and a table view), ` ```diff `, ` ```csv `, ` ```form ` (the answers are sent as your next message), embedded or artifact images, file cards and a run-state panel. Remote images are never loaded (their address can leak your data); HTML and scripts are never rendered.


### Richer answers: tables, JSON, web pages, audio and video

Besides charts, diffs, forms and CSV, an answer can contain these fenced blocks (agents are told about them in their instructions). Everything is drawn from validated data in a fixed set of pieces; nothing from a model is ever added to the page as HTML.

* ```` ```table ```` (JSON `{"title", "columns": ["a", {"key", "label", "type"}], "rows": [[...]]}`, rows as lists or objects) and ```` ```csv ```` / ```` ```tsv ````: a table you can **sort, filter, page through, hide columns of, expand long cells in and copy**, and **export (what you see) as CSV, JSON, Excel (.xlsx) or Markdown**. Text cells that start with `=`, `+`, `-` or `@` get a leading apostrophe in CSV so a spreadsheet cannot read them as a formula. Up to 5000 rows and 50 columns.
* ```` ```jsonview ````: a collapsible JSON tree with search (it opens the branches that match), copy path / copy value, download, and "show 100 more" for long lists. A document over 200,000 characters is shown as text.
* ```` ```html-preview ````: a small web page shown **only inside a frame with an empty `sandbox`** (no scripts, no forms, no popups, no access to this app) and a `Content-Security-Policy` meta tag that blocks every network request. There is a Preview / Source toggle, download and full screen.
* ```` ```audio ```` / ```` ```video ````: a player for a file an agent saved. The body is the 12-character **artifact id** (or JSON `{"artifact", "title", "captions"}` with a `.vtt` artifact for captions). Only Agent Studio's own artifacts play; a remote address is shown as a note. The server sends audio and video inline with byte ranges (so seeking works), `nosniff` and a sandboxing policy; mkv and avi stay downloads.
* Every piece has **Keep** (save it as an artifact), **Open in Artifacts** (saves it if needed, then opens the page in a new tab) and, in the chat, **Open in canvas**. The Artifacts page uses the same viewers for CSV, JSON, HTML and audio / video files.
* For AG-UI clients the same blocks arrive as `ACTIVITY_SNAPSHOT` events right after the message that held them: `rich.table`, `rich.chart`, `rich.json`, `rich.html` (`{html, sandbox: "", csp}`: render it in an iframe with an empty sandbox), `rich.audio`, `rich.video`, `rich.diff`; shapes are in `backend/app/rich.py`.

## Canvas: edit a document with an agent, side by side

The **Canvas** button above a chat (or **Edit in canvas** on an answer, or **Open in canvas** on a code block, table, JSON or page) opens a canvas beside the chat. It holds a document (Markdown), code (syntax colours for Python, JavaScript, TypeScript, JSON, HTML, CSS, SQL, YAML), a table (CSV) or a web page, edited in CodeMirror 6. Nothing in it is ever run; HTML is shown only in the locked preview.

* **You type anywhere**; it saves a moment later with the version you were looking at. If someone else changed it meanwhile (the agent, another tab) you are asked: keep your text, take the new version, or compare.
* **The agent edits through tools** (`canvas_create`, `canvas_read`, `canvas_replace_selection`, `canvas_insert`, `canvas_patch`; exact-text replacements checked against the version it read). Its changes arrive as **proposals** highlighted in the text, with **Accept / Reject / Accept all / Reject all**. A proposal is checked again when you accept it; a part that no longer fits is reported and you can apply the parts that do. Select text and ask ("make this shorter") and only that selection is targeted.
* **Plan mode**: an agent in plan mode can only propose, whatever the setting. Settings > Agents & sandbox > Canvas can switch on "apply agent changes straight away" (still a version you can bring back), and sets the size limit and how many versions are kept.
* **Versions**: every change is a version with its author; compare any version with the current text and bring it back. Each canvas is also kept as an artifact (a new version of the same file), so it shows on the Artifacts page and goes to your storages.
* **Layout**: chat left, canvas right with a draggable divider (arrow keys work too); on a phone a Chat | Canvas switch. `/?canvas=<id>` opens a canvas and `/?canvas=latest` the newest one. Live updates come over server-sent events (`GET /api/canvas/<id>/events`); in an AG-UI stream canvas changes are `ACTIVITY_SNAPSHOT` events of type `canvas` and appear in the state under `/canvases`. The REST API is `/api/canvas/*`.
* **Not tested live**: the agent-edit flow ran in tests with the mock model, not a real one.

## Agent frameworks, harness rules, workflows, API and A2A

* **Agent frameworks** (Settings > Agents & sandbox, and the Framework picker in the agent editor): Google ADK is built in. LangGraph, Strands Agents, Pydantic AI, the OpenAI Agents SDK, Microsoft Agent Framework and the Claude Agent SDK install on demand into their own folders (exact pins, nothing younger than 14 days, licence and size accepted first). Whichever runs an agent, Agent Studio keeps the model (through a private per-run gateway: spend caps, guardrails, run record), the tools (a tool bridge into the sandbox), memory, knowledge bases and artifacts. A capability matrix shows what each framework can do here; the editor warns about what an agent would lose. Adding a framework is one catalog entry plus one worker function, and `tests/framework_conformance.py` is the contract every adapter must pass. The Claude Agent SDK bills on your own Anthropic account, so spend caps do not cover it.
* **Harness rules** (Settings > Agents & sandbox): permission modes (full / accept edits / plan), a loop guard, your own block-or-remind hooks (declarative, no code), checkpoints with rewind (`/api/harness/checkpoints`), `AGENTS.md` / `CLAUDE.md` loading, a to-do tool, a repository map, and web fetch / search tools (off by default, public addresses only, optional allow-list).
* **Workflows** (sidebar, two tabs). **Flows** is a table of your workflows (search, last run, duplicate, rename, export, import with clash handling, delete with a used-by warning). **New workflow** asks how to start: build it yourself, talk to an agent that proposes it, or start from a template. Editing is full screen: a canvas (React Flow) with a box palette (press `/`), a right-hand settings panel (Settings / Input / Output / Notes with Text / JSON / Table views and pinned output), undo/redo, copy/paste, tidy-up, a run drawer with approvals, saved runs and versions, and autosaved drafts. There are 21 kinds of box the engine really runs: Task input, Agent, AI prompt, Classifier, Tool, API call, Sequence, Parallel, Loop, If, Switch, Merge, Wait, Ask a person, Stop with error, Run a workflow, For each item, Set fields, Text template, Parse JSON, Result (plus a sticky note). Boxes with several outputs (If, Switch, Classifier, Ask a person, or any box set to 'on error: branch') follow one branch and skip the rest. Fields use a small safe expression language: `{{ $input }}`, `{{ $node["Name"].json.field }}`, `{{ len($prev) > 3 ? "long" : "short" }}` (no code is ever run). Each box can retry, time out, carry on after a failure, or be switched off. The builder chat only proposes edits as a diff you Apply; it can never tick 'I confirm' or run anything. **OpenAPI steps** is where APIs live: upload a file, give an address or paste the description (OpenAPI 3.0/3.1 and Swagger 2.0, JSON or YAML, local `$ref`s), then create, edit, switch off, hide, test and delete endpoints (including ones you add by hand), refresh with a diff, duplicate, and delete with a used-by check. Keys are write-only and only the API's own server is called. Workflows are versioned, validated in plain language, exportable as JSON, and can be turned into an agent.
* **API and A2A** (Settings > API & A2A): an authenticated OpenAI-compatible API (`/v1/models`, `/v1/chat/completions`, streaming) and A2A 1.0 (official `a2a-sdk`; each agent has a card at `/a2a/<agent>/.well-known/agent-card.json`). Both are off until switched on, need a key (shown once, stored as a hash, scoped to agents, rate-limited, revocable, rotatable) and only reach agents that opted in. Agents can also call remote A2A agents you register.


## Workflow boxes, webhooks and testing

- **42 kinds of box** (the palette opens with `/`): triggers (Task input, Webhook, On error), AI (Agent, AI prompt, Classifier, Guardrail check), flow (If, Compare, Switch, Merge, Wait, Ask a person, Stop, Run a workflow, For each, Sequence, Parallel, Loop), data (Set fields, Text template, Parse JSON, Filter, Sort, Limit, Remove duplicates, Summarise, Split, Join, Merge by key, Date and time, Text pattern), integrations (Tool, API call, HTTP request, MCP tool, Memory, Knowledge search, Data query) and output (Result, Respond to webhook, Save as artifact). There is no code box.
- **Safety defaults:** HTTP request calls public addresses only (a private host must be allowed in *Workflows > Settings*), uses GET unless you pick another method and tick *I confirm*, and never takes secrets as plain headers (use a credential: write-only, kept apart from the workflow). An MCP tool that its server does not mark read-only needs *I confirm*. Data query uses the same read-only guard as agents. A Guardrail check fails instead of passing when guardrails are switched off.
- **Webhook:** switch the start of a workflow to *Webhook* in the right-hand panel, save a version, then *Make the address*. The address is shown once; only a hash is kept. Webhooks are **off** until you tick them on in *Workflows > Settings* (an import never switches them on). Calls are rate-limited and size-limited, and the workflow's *asks first* setting still applies.
- **Try it:** *Test this box* runs one box with the text you type (a box that could change something only runs with *Allow side effects* ticked); *Run from here* starts at a box and reuses the last run for everything before it. Select several boxes (Shift-click) to copy and paste them together with their connections.
- **On error:** an *On error* box starts when something fails and nothing else caught it (the failed box is its input as JSON); the run still ends as failed.
- **Flows table:** tick workflows and *Export selected* for one file; *Import* accepts one workflow or a file of several; the arrow on a row shows its earlier runs. Settings: `docs/SETTINGS.md` (*Workflows*).

## Runtimes, sandboxes and data sources (Phase 7)

- **Where an agent runs** (agent editor, Settings > Runtimes): this PC (default), a Docker container on this PC, Amazon Bedrock AgentCore Runtime, Vertex AI Agent Engine or Microsoft Foundry hosted agents. A deployed agent is a plain model loop (no tools, memory or files). Deploying shows a compatibility report, the exact build/push commands and a cost warning, and always needs a confirmation. Editing a deployed agent never changes it by itself: deploy the change as a **shadow version**, test it, **switch traffic**, and **roll back** if needed. Cloud credentials and the model keys a runtime uses are write-only.
- **Sandbox kinds** (Settings > Agents & sandbox): a hardened Docker container (default; memory/CPU/process limits, optional read-only root) or the experimental Docker Sandbox micro VM (`sbx`, proprietary, off until switched on). Each agent may pick its own.
- **Data sources** (Settings > Data sources): SQLite, PostgreSQL, MySQL/MariaDB, Databricks, Snowflake, BigQuery and Salesforce, strictly read-only (SQL/SOQL guard, read-only sessions, row/size/time limits, optional masking of personal data, audit log). Tick the sources each agent may read in its editor.
- **Data products and agent descriptors** (AI-BOM > Data & agents): name, own and classify data; see which agent does what (including external CLI/ACP agents) and which data it can reach. They appear in the AI-BOM and the ARD catalog, honouring the redaction level.

## Custom model endpoints and remote A2A agents

- **Any OpenAI-compatible endpoint** (Settings > Model providers > *+ Add OpenAI-compatible endpoint*): vLLM, LM Studio, a LiteLLM proxy, OpenRouter, Together, Groq, Fireworks or a company gateway. Give a name, the address (a bare host gets `/v1` added), a key (write-only) or an environment variable, optional extra headers and organization. *Find models* reads `GET {address}/models`; models can also be typed in by hand. Optional prices per million tokens (a default row plus per-model rows; `0`/`0` for a model you run yourself) and a context size feed the spend cap, Auto mode and the context meter; a model with no price is treated as expensive, never free. Each endpoint has its own spend cap. *Test* sends one tiny chat request through the spend guard and names the problem when it fails (wrong key, unreachable, certificate, wrong address, model not found, rate limit). The address is labelled **on this PC**, **on this network** or **data leaves this PC**. Endpoints appear in the provider picker, Auto mode, the wizard, the agent editor and the AI-BOM (never keys). A settings export lists them without secrets; importing adds them switched off.
- **Create an agent** (Agents > *+ New*) first asks: *New agent (wizard)* or *Connect an A2A agent*. For A2A, paste the agent's address (or the link to its `agent-card.json`), pick the sign-in (bearer token or an API key in a header; write-only), read its card (name, skills, sign-in, streaming), then connect. Remote agents are listed under the agents with a **Remote A2A** badge and a where-it-runs label, can be refreshed, tested with a message, turned off, edited and removed, and you choose which of your agents may call them (those agents get the `call_remote_agent` tool). They run on their own servers and billing, outside the spend caps.

## How tool installs work (red team, trainer, agent frameworks)
Each tool gets its own isolated Python environment. Installs use ready-made wheels only (`--only-binary=:all:`) and nothing published in the last
14 days. The only exception is a short, checked list in `backend/app/pip_install.py` (`SOURCE_BUILDS`): small pure-Python packages that publish no
usable wheel. Today that is `ecoji` and `langdetect` (garak, PyRIT) and `gpustat` (Ludwig), each built at one exact version, only for the tool that
needs it; the install plan says so ("Builds ecoji, langdetect from source"). If a step fails, the message says in plain words what pip could not
find. The environment uses the same Python as Agent Studio; to use another one, set `AGENT_STUDIO_ENV_PYTHON` to the full path of a `python.exe`,
restart Agent Studio and install again.
