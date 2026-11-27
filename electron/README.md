# Electron shell

- Dev: `npm run dev:desktop` from the repo root (API + Next + Electron). First run: `npm --prefix electron install`.
- Installer: `npm run pack` from the repo root (see the root README, "Build the installer"). Output: `electron/dist`.
- Installed layout: `resources/studio/{python,backend,web,skills,docker,docs}`. `main.js` picks a free loopback port,
  starts `python/python.exe backend/run_server.py` with `AGENT_STUDIO_HOME=%APPDATA%\Agent Studio\data` and
  `AGENT_STUDIO_WEB_DIR=resources/studio/web`, waits for `/api/health`, then loads `http://127.0.0.1:<port>/`.
- Same origin: the server serves the UI, so no CORS exception and no `null` origin is needed. Microphone (Live mode) and
  notifications are allowed only for that origin. `AGENT_STUDIO_HOME` set before launch moves all data (used by tests).
- Start-up: there is no short deadline. While the server process is alive the splash shows the elapsed time and the
  server's last progress line (`[boot] loading: N modules, T s`, printed by `run_server.py` every 3 s). The window only
  reports an error if the process exits; after 15 minutes (`AGENT_STUDIO_STARTUP_TIMEOUT_MS`) the splash adds "Show the
  log file" and "Quit" links and keeps waiting (nothing blocks: if the server answers, the app opens).
- `.pyc` files ship as `unchecked-hash` (see `scripts/pack.mjs`): the installer gives every file a new modified time,
  which would make ordinary `.pyc` files stale and force Python to recompile every module on the first start.
- Quit: `taskkill /T /F` on the server's process tree (MCP servers, agents, voice workers go with it).
- `build/make_icon.py` draws the icon (`icon.png`, `icon.ico`); `build/installer.nsh` asks on uninstall whether to delete
  the data (silent uninstalls keep it).
