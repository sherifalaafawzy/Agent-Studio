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
npm test               # 12 backend tests (guard, ledger, ADK/LiteLLM round-trip)
npm run demo:cap       # prints a call being refused by the cap guard
npm run dev:desktop    # + Electron window (first: npm --prefix electron install)
```

## Layout
```
backend/app/db.py        SQLite (providers, ledger, reservations)
backend/app/pricing.py   $/1M-token lookup: overrides -> LiteLLM cost map -> conservative fallback (never free)
backend/app/providers.py provider catalog, settings store, LiteLLM kwargs
backend/app/budget.py    cost ledger + BudgetGuard (reserve -> settle/release)   <- the make-or-break piece
backend/app/chat.py      ADK LlmAgent + LiteLlm; guard wired as before/after/on_error model callbacks
backend/app/main.py      FastAPI routes (/api/providers, /api/chat, /api/billing/*)
frontend/                Next.js (static export): Chat, Agent Studio (stub), Billing, Settings
electron/                shell structure (dev loads Next; packaged spawns PyInstaller sidecar) - later phase
docs/gcp-backstop/       Budget -> Pub/Sub -> Cloud Function kill-switch + quota guidance
skills/ mcp/             reserved for Phase 2+ (starter skills, curated MCP servers)
scripts/                 setup + cap-guard demo
```

## How the guard works
Per provider, per window (lifetime / daily / monthly): refuse unless
`spent + in-flight reservations + worst-case(call) <= cap`, where worst-case = pessimistic input-token estimate +
the **full** `max_output_tokens` (the request is clamped to it, so the bound is real). Reservations are taken under a
lock (concurrent calls cannot jointly overshoot), then settled to actual provider-reported usage; no usage reported →
charged the worst case; unknown model price → priced at a high fallback, never zero; failed calls release their reservation.

Because it reserves worst case, a cap is only fully usable if `max_output_tokens` is small relative to it — by design.
