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
