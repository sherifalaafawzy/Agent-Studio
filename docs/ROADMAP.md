# Agent Studio — Roadmap and Status

_Last updated 2026-11-22. All work is **uncommitted** on top of commit `17589e7`._

Legend: ✅ done and verified · 🔄 in progress · ⏳ queued · ⚠️ built but not verified live

## Verified test state (last independent check)

| Suite | Result |
|---|---|
| Backend (after Phase 5) | 1818 passed, 100% statements (19,160) |
| Backend (agent report, after Phase 6) | 2017 passed, 4 skipped, 100% (21,721) — not re-run by me |
| Frontend (after Phase 6) | 106 files, 1501 tests, `tsc` clean |

Rules in force: 100% backend statement coverage; fakes only (no real installs, downloads or cloud calls); pins only to versions published at least 14 days ago; suites run one at a time.

---

## Done

### Foundations (earlier rounds)
- ✅ RAG, d1 decision models, skills, harness basics
- ✅ Amazon S3 Vectors store
- ✅ Time-series forecasting MCP (8 models, backtests) ⚠️ real models not run
- ✅ Auto model routing (rules / d1-2B / d1-3B) ⚠️ d1-3B not run live
- ✅ ARD discovery and AI-BOM export (CycloneDX 1.7, redaction levels, optional signing)
- ✅ Provider and workspace popovers fixed (separate, close on outside click)

### Phase 1 — Easy setup and onboarding ✅
Setup guide with system check, one-click Ollama install and model library, one-click coding-agent installs, step-by-step agent wizard, about 35 help icons, assistant prompt and tool switches.
⚠️ Not run for real: Ollama and agent installs; later wizard steps seen only in tests.

### Phase 2 — Settings, memory, telemetry, OCSF ✅
Settings registry with generated `docs/SETTINGS.md`, search, reset, import/export; pluggable memory (7 kinds, local SQLite default, opt-in vector, Vertex, AgentCore, Mem0); telemetry overview with anomalies and export; OCSF 1.10.0 export (file, HTTPS/Splunk, S3, syslog).
⚠️ Cloud memory backends and OCSF sinks tested against fakes only.

### Phase 3 — Catalog, MCPs, trainer, cloud ML ✅
24 domain skills; 33 vetted public MCP servers; trainer MCP (Ludwig, PyTorch, Keras); cloud ML MCP (SageMaker, Vertex, Azure ML, DigitalOcean, RunPod, OCI) read-only by default.
⚠️ Real training and cloud calls never run.

### Phase 4 — Guardrails, evals, security, identity ✅  (follow-up: guard models auto-download from Ollama; d1 gate warm-up and wait-on-Try-it)
Guardrail factory (open-source models, d1 gate, Bedrock, Azure, Model Armor); eval runner with exports; OWASP LLM 2025 and Agentic 2026 scorecard (26 live checks) with red-team runners; agent identity (SPIFFE-style IDs, short-lived tokens, RFC 8693 delegation).
⚠️ All external services tested with fakes only.

### Phase 5 — AG-UI and artifact store ✅
`/api/agui` event stream, safe rich renderer in chat, artifact capture, multi-storage store (local, S3, S3-compatible, GCS, Azure Blob, WebDAV), Artifacts page.
⚠️ Chat renderer tested in jsdom only; cloud storages fakes only.

### Phase 6 — Frameworks, harness, workflows, A2A ✅
Framework factory (ADK plus LangGraph, Strands, Pydantic AI, OpenAI Agents SDK, Microsoft Agent Framework, Claude Agent SDK) with a 15-case conformance suite; harness upgrades (permission modes, loop guard, hooks, checkpoints, AGENTS.md, web tools); workflow builder (React Flow); A2A 1.0 server and client; authenticated OpenAI-compatible API.
⚠️ None of the six framework workers has run against its real library.

---

## In progress

### Phase 7 — Runtimes, sandbox, data sources ✅
Runtime targets (local, Docker, Vertex, Azure Foundry, AWS AgentCore) with plan, shadow deploy, health check, traffic switch and rollback; hardened Docker sandbox plus experimental `sbx` microVM; read-only data connectors (SQLite, Postgres, MySQL, Databricks, Snowflake, BigQuery, Salesforce); data products and agent descriptors in ARD. Backend 2161 passed, 100%; frontend 111 files, 1549 tests. Not run for real: any remote deploy, `sbx`, non-SQLite connectors.

---

## Queued (in this order)

1. ✅ **Custom OpenAI-compatible providers** — any number of named endpoints (address, write-only key, headers, organization), model discovery plus typed-in models, per-model prices and context size, own spend cap, Test button with exact failure reasons, "on this PC / on this network / data leaves this PC" label; used by chat, Auto mode, the context meter and the AI-BOM; settings export lists them without secrets. Verified live against a local OpenAI-style server (real HTTP and LiteLLM chat).
2. ✅ **Create-agent entry choice** — "+ New" asks: New agent (wizard) or Connect an A2A agent (agent-card address or link, bearer token or API-key header, card preview, test message, refresh, edit, remove, who may call it; remote agents listed with a Remote A2A badge and a where-it-runs label). Verified live against a local card server; the message round trip is tested against Agent Studio's own A2A server.
3. ✅ **Workflow redesign** (W1-W5 built; see the note below) — design doc is written. Full-screen editor on its own route, manual-or-agent-chat creation, right-hand settings panel, **Flows** and **OpenAPI steps** tabs, about 40 nodes, OpenAPI upload/URL/paste, create/read/update/delete for workflows and endpoints. Defaults: n8n-style `{{ }}` expressions, `dagre` if at least 14 days old, manual and Webhook triggers first, no code node. Phases W1, W3, W2, W4, W5.
4. ✅ **MCP page redesign** — master-detail: list on the left (Needs setup / Installed / Built-in tools / Available from the catalog; search, state, access and area filters, keyboard navigation), details on the right (Overview, Settings, Tools, Health & logs; special panels for Playwright, decision model, forecasting, trainer and cloud accounts live under Settings; rarely used options under Advanced); one Add server flow (catalog, own server, OpenAPI file, then test); `/mcp/?server=<id>` deep links; two screens with Back on a phone. Backend 2233 passed, 100%; frontend 119 files, 1667 tests, `tsc` clean. Checked live at desktop and 375px (no overflow on any panel); no real server was added or tested.
5. ✅ **Per-agent security scorecards** — built. Security page has Overall / Per agent: a ranking (weakest first, top issue, search, sort and filters) and one card per agent that re-judges the same 26 checks on that agent's own settings (not-applicable checks are explained and left out of its score) plus 6 agent-only checks (permission mode, data sources, API/A2A exposure, runtime, framework, remote agents). Score badge in the agent list and a bar in the editor; every fix link opens the editor at the right section (`/agents/?agent=<id>#ag-...`); Markdown/JSON export per agent and for all agents; AI-BOM and ARD carry only the score and counts at `internal`+. Backend 2260 passed, 100% statements; frontend 121 files, 1681 tests, `tsc` clean.
6. ✅ **Enhanced AG-UI components** — built. Tables (```table / csv / tsv): sort, filter, hide columns, paging, expand long cells, copy, export what you see as CSV / JSON / Excel / Markdown. JSON tree viewer (search, copy path/value, download). HTML preview in an empty-sandbox frame with a no-network policy (Preview/Source, download, full screen). Audio / video players for artifact files only (byte-range serving, speed, captions). Keep / Open in Artifacts / Open in canvas on every piece; same viewers on the Artifacts page; `rich.*` AG-UI activity events. Not verified: a real model emitting the blocks, captions across origins, remote AG-UI clients.
7. ✅ **Canvas mode** — built. Side-by-side canvas (CodeMirror 6) for Markdown, code, tables and web pages; autosave with version check and a keep-mine / take-theirs / compare prompt; agent tools (`canvas_create/read/replace_selection/insert/patch`) make proposals shown highlighted with accept / reject / accept all, re-checked on accept; plan mode can only propose; versions with diff and restore; each canvas kept as an artifact; draggable divider, Chat | Canvas tabs on a phone; `/?canvas=<id|latest>`; live updates over SSE and AG-UI `canvas` events; Settings > Agents & sandbox > Canvas. Not done: an assistant tool to open it (the assistant tool list is reworked in the docs phase); not run with a real model.
7a. ✅ **AG-UI Kit (plan: docs/AGUI-COMPONENTS-PLAN.md)** — built G0–G8 with the plan's defaults: typed specs + registry + `render_ui`/`ui_catalog` + fallback-to-table + `ui.component` events + `/components` gallery; table formats, grouping, selection; 26 chart types as hand-drawn SVG (table view, exports, legend, hover); time series on Apache ECharts (zoom, ranges, resample, MA, index, log, annotations, thresholds, anomalies, forecast bands, LTTB thinning); maps on MapLibre (offline by default with a bundled Natural Earth outline; points, clusters, heat, bubbles, choropleth, GeoJSON, routes, arcs, hex bins, time slider, layer list, GeoJSON/KML/GPX/PNG exports; opt-in tile or PMTiles basemap); 42 cards; 5 layouts; media/JSON/HTML views. Not built (see the plan): deck.gl/H3 (G4b), measure/draw/buffer/swipe map tools, globe is supported but unexercised, a 3D molecule viewer (Science Lab), `dataRef` for very large data, a Gantt/scatter small-multiples helper, syntax highlighting in code cards. Tests: backend 2427 passed, 4 skipped, 100% statements; frontend 149 files, 2115 tests, `tsc` clean, lines 99.97%, functions 99.95%, branches 96.4%. Real map rendering was checked once live in the in-app browser (desktop); phone width and dark mode were checked on the gallery only. **Per-agent AG-UI switches** (components, stream endpoint) added afterwards: agent editor, wizard, list badge, global default for new agents, enforced in the ADK and framework paths and on `/api/agui`.
7b. ✅ **Workflows Runs tab** — third tab on the Workflows page: every run of every workflow in one table (result, trigger, started, took, boxes ran/failed/skipped, cost and tokens, version) with search, filters (workflow, result, what started it, when, Needs you), sort and paging; run detail with the graph as it ran, per-box input/output/error/timing, run log, Run again / from the failed box / from a box, Stop, inline approvals, Export JSON, Delete (one or many). Server removes secrets from everything shown. Backend: `workflows/runs.py`, new columns `origin`, `tokens`, `from_node`.
8. ✅ **Apps** — built (see README, "Apps"): standard templates, builder agent with proposals/versions, sandboxed preview on a separate port, per-app scoped key, server apps in a container (off by default), zip export, local git with confirmed push/pull, deploy plan (commands only), Simple and Advanced modes, settings and AI-BOM counts. **Smarter builder studio** (2026-11-23): plan-first builder, preview error capture and bounded auto-fix loop, click-to-change, undo/compare, themes and parts, dashboard and table templates, one-click agent and read-only data-source connections, model per app (see README, "The studio").
9. ✅ **Science Lab** (see `docs/SCIENCE-LAB.md`) — our own lab, built. 13 open read-only data sources (PubMed, Europe PMC, UniProt, AlphaFold, RCSB PDB, PubChem, arXiv, INSPIRE-HEP, NASA ADS, OEIS, Wolfram|Alpha, OpenAlex, Crossref) through one client (per-host limits, cache, Retry-After, size cap, provenance on every answer); local calculator, units, CODATA 2022 constants, molar mass, sequence statistics and numeric calculus; sandbox-only Python with a reproducibility record; investigations with Elo-ranked hypotheses, evidence that must come from a recorded call, server-computed claim labels (established / single source / computed / inference / conflicting / unsupported), citation checks against Crossref and PubMed, a labelled report and notebook artifacts; a layered safety screen (block / confirm / allow, level raise-only, audit to the security export, also a guardrail kind); 11 `science_*` agent tools, a built-in Science Lab agent and 7 skills; PaperQA2 and ToolUniverse installs into isolated environments with licence tick; `/science` page (investigation, search, compute, report, safety, components, settings), AI-BOM entry. **Excluded by decision:** Claude Science and Google co-scientist. **Not built:** 3D molecule/protein/equation viewers, named sci-* sandbox images, autonomy modes gating steps, debate workflow template, eval sets, Edison/AlphaEvolve adapters, science scorecard checks, docs pages with screenshots (see the doc). Tests: backend 2712 passed, 5 skipped, 100% statements (31,810); frontend 160 files, 2235 tests, `tsc` clean. Live: every connector except the key-only ones was called against the real service and the UI was checked at desktop, 375px and in dark mode; no model was called (the agent path is tested with fakes).
10. ⏳ **Phase 8 — Avatar mode** — Gemini Live audio plus a client-side avatar (Gemini's video avatar is enterprise-only).
11. ⏳ **Docs and assistant phase** — docs system with full-text search, auto-captured screenshots (desktop and 375px), full Docs page and slide-in side panel, assistant with page context that can open docs, guide step by step, and propose setup edits as diffs needing approval.
12. ⏳ **Final full UI/UX and system testing.**

---

## Open items and risks

- **Red-team/Ludwig installs fixed (2026-11-22):** garak, PyRIT and Ludwig failed to install because `ecoji`, `langdetect` and `gpustat` publish no wheel and installs were wheels-only. They are now built from source from a short vetted, pinned allowlist (`app/pip_install.py`); failures are explained in plain words; `AGENT_STUDIO_ENV_PYTHON` picks another Python. Not run for real: no actual tool was installed, only pip dry-runs and a scratch build of the three wheels. promptfoo's npm resolve works in a dry run; its own failure cause (if any) is not confirmed.

- **Not committed.** Nothing has been committed or pushed; ask if you want that.
- **Plane tickets** were never created. The `pm` agent must record Phases 1–7 retroactively.
- **14-day rule:** its origin is unclear; you can change or drop it.
- **Unverified live:** real framework workers, cloud accounts, real MCP servers, real model downloads, macOS and Linux.
- **Known deviations:** the chat page keeps its own stream rather than consuming AG-UI; Azure artifact auth is SAS only; non-ADK frameworks do not use MCP servers or sub-agents; Claude Agent SDK bills your Anthropic account outside the spend caps.


## Workflow nodes, round 2 (done)

The "Not built" list in the section below is **superseded**: round 2 built most of it. The editor now has **42 executable boxes** (the earlier 21, plus 21 new ones) and a sticky note.

New boxes: HTTP request (public addresses only, GET unless you confirm, credentials kept apart), MCP tool (read-only unless the server marks the tool read-only or you confirm), Memory (search / save), Knowledge search, Data query (read-only guard), Save as artifact, Guardrail check (refuses to pass when guardrails are off), Filter, Sort, Limit, Remove duplicates, Summarise, Split into a list, Join a list, Merge by key, Date and time, Text pattern, Compare, Webhook and Respond to webhook, and On error. "Ask a person" can now ask for details (text, number, choice, yes/no) that continue as JSON. **No code box**, deliberately.

Also built: **Webhook trigger** (secret address shown once, only a hash kept, off until switched on in Workflow settings, rate limit, size limit, answer immediately / when done / from a Respond box), **On error path** (runs when a box fails; the run still ends as failed), **Run from here** (reuses an earlier run for everything before the box), **Test this box** (one box with typed input; boxes that could change something need "allow side effects"), **copy/paste of several boxes** with the connections between them, **bulk export and multi-workflow import**, **expandable run rows** in the Flows table, **Workflow settings** (webhooks, limits, allowed private hosts, write-only credentials), the settings registry entries and the AI-BOM counts (counts only, never secrets). The floating Assistant button no longer sits on the editor header on a phone.

Still not built: a Schedule trigger (needs a scheduler), a code box, a separate "Notify me" box (use Save as artifact), multi-select drag-to-box layout tools, and auto-layout beyond the simple left-to-right "Tidy up".

Tests after round 2: backend 2309 passed, 4 skipped, 100% statements (27,084); frontend 125 files, 1723 tests, `tsc` clean, functions 100%, lines 99.98% (the few uncovered lines are older catch handlers on the Evals, Settings, Skills, Catalog, Credits, Frameworks and MCP add-server screens). The frontend suite now runs stably: waitFor timeout 8 s, test timeout 30 s, 12 workers.

## Workflow redesign: what shipped and what did not (item 3)

Shipped: tabbed page (Flows table, OpenAPI steps), full-screen editor on its own route, palette, right panel, drawer, builder chat with diff/Apply, 21 executable box kinds plus a sticky note, ports and branch pruning, expressions, per-box retry/timeout/on-error/disable/pin, sub-workflows and For each, saved runs, drafts, version labels, import with clash modes, duplicate, used-by checks, OpenAPI upload/URL/paste with a stronger parser, endpoint create/edit/hide/delete, refresh diff and test calls.

Not built (deliberate): Webhook and Schedule triggers (no scheduler exists), a code box, MCP-tool / HTTP / memory / data-source boxes (use the Tool, API call and Agent boxes), run-from-this-box and per-box "test step", bulk export, expandable run rows in the Flows table (earlier runs live in the editor drawer), multi-select copy, and a minimap that follows the theme. About 21 boxes rather than 40.

## Fixes after the first push

- **promptfoo "Email Verification Required" (2026-11-22).** Root cause (read from the installed promptfoo 0.125.1): `redteam run/generate` prompts for a work e-mail whenever remote generation is enabled, and our runner has no terminal. Fix: promptfoo now runs in Local only mode by default (your own model writes and grades the attacks through a private gateway on this server; no e-mail, nothing sent to promptfoo); the hosted mode needs you to verify yourself once, with a pre-flight check and the exact command shown; failed runs explain themselves. Verified against the real promptfoo with a fake model server; not verified with a real model. We deliberately do not set `CI=true` (it would skip the check with a placeholder e-mail).
- **Scorecard evidence checks.** The red-team, evaluation and security-export checks now require a run that actually worked (see README). Changed: `app/security/evidence.py`, `scorecard.py`, `agentcard.py`.
