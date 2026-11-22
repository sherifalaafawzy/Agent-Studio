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

### Phase 4 — Guardrails, evals, security, identity ✅
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
7a. ⏳ **AG-UI Kit (plan: docs/AGUI-COMPONENTS-PLAN.md)** — follow-on to item 6: typed UI specs + registry, tables, 24+ chart types, time series (zoom, annotations, forecast bands, down-sampling), maps (offline by default; choropleth, heatmap, hexbin, routes/arcs, time slider), 42 cards (weather, quote, KPI, source, timeline, itinerary, recipe, approval…), exports (CSV/JSON/XLSX/MD/PNG/SVG/PDF/GeoJSON), `render_ui` tool, gallery page. Phases G0–G8.
7b. ⏳ **Workflows Runs tab** — third tab on the Workflows page: all runs in one table (workflow, status, trigger, started, duration, nodes failed, cost) with search and filters, run detail with per-node input/output/error, re-run, run-from-failed, cancel, delete, inline approvals under a 'Needs you' filter, live updates.
8. ⏳ **Apps** — agent-built apps (manifest, frontend, optional backend, Dockerfile) that call your agents locally or on cloud runtimes; Apps sidebar page; export zip, git connect, use in system, deploy, versions; Simple mode (prompt plus attachments) and Advanced mode (editor, preview, console, builder chat); sandboxed, scoped per-app keys, secret scan before export.
9. ⏳ **Science Lab** — our own lab with open connectors for biology, chemistry, physics, math and literature; hypothesis, evidence and critique loop with multi-agent debate; provenance and notebook artifacts; sandboxed computation; molecule, protein and equation viewers; optional PaperQA2, Biomni and ToolUniverse installs; hazard guardrails. **Excluded by decision:** Claude Science and Google co-scientist. Design doc: scratchpad `science-harness.md`.
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
