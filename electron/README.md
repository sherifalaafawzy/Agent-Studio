# Electron shell (structure only in Phase 1)

- Dev: `npm run dev:desktop` from the repo root (API + Next + Electron). First run: `npm --prefix electron install` (downloads Electron; not installed by default).
- Packaged (later phase): `electron-builder` bundles the static Next export (`frontend/out`) as `resources/web` and the
  PyInstaller-built `backend/run_server.py` as `resources/sidecar/agent-studio-api.exe`; main.js spawns it with
  `AGENT_STUDIO_HOME=%APPDATA%/Agent Studio` so data/keys live in the user profile.
