# Electron shell (structure only in Phase 1)

- Dev: `npm run dev:desktop` from the repo root (API + Next + Electron). First run: `npm --prefix electron install` (downloads Electron; not installed by default).
- Packaged (later phase): `electron-builder` bundles the static Next export (`frontend/out`) as `resources/web` and the
  PyInstaller-built `backend/run_server.py` as `resources/sidecar/agent-studio-api.exe`; main.js spawns it with
  `AGENT_STUDIO_HOME=%APPDATA%/Agent Studio` so data/keys live in the user profile.

## Backend CORS and the `null` origin
The backend no longer trusts the `null` origin by default: `file://` pages and every sandboxed iframe on any website send it,
so allowing it would let any web page read the local API. A packaged build that loads `file://` must start the backend with
`AGENT_STUDIO_ALLOW_NULL_ORIGIN=1`; prefer the custom `app://agent-studio` scheme (see the TODO in `main.js`).
