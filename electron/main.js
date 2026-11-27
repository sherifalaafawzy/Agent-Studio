// Agent Studio desktop shell.
// Dev (`npm run dev:desktop`): loads the Next dev server; the API is started by the root dev script.
// Installed: starts the bundled Python server (resources/studio) on a free loopback port. That server also serves the
// static UI, so the window loads http://127.0.0.1:<port>/ (one origin: no CORS exception, no `null` origin).
const { app, BrowserWindow, shell, dialog, session } = require("electron");
const { spawn, execFile } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const http = require("node:http");

const DEV = !app.isPackaged;
const WEB_URL = process.env.AGENT_STUDIO_WEB_URL || "http://127.0.0.1:3100";
// The first start after an install can take minutes (the antivirus checks thousands of new files), so there is no
// short deadline: we wait as long as the server process is alive, and only ask after this long ceiling.
const STARTUP_CEILING_MS = Number(process.env.AGENT_STUDIO_STARTUP_TIMEOUT_MS || 900000);
const SLOW_HINT_MS = 15000;
let sidecar = null;
let quitting = false;
let win = null;
let logFile = null;

app.setAppUserModelId("com.agentstudio.desktop");   // Windows: needed for desktop notifications to show

// Your data (chats, agents, settings, keys, installed tools) lives in %APPDATA%\Agent Studio\data; the window's own
// browser cache stays beside it. AGENT_STUDIO_HOME moves both (tests, a portable copy).
if (process.env.AGENT_STUDIO_HOME) app.setPath("userData", path.join(process.env.AGENT_STUDIO_HOME, "shell"));
const DATA_DIR = process.env.AGENT_STUDIO_HOME || path.join(app.getPath("userData"), "data");

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
  app.whenReady().then(main);
}

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.unref();
    s.on("error", reject);
    s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => resolve(port)); });
  });
}

function health(port) {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port, path: "/api/health", timeout: 3000 }, (res) => { res.resume(); resolve(res.statusCode === 200); });
    req.on("error", () => resolve(false));
    req.on("timeout", () => { req.destroy(); resolve(false); });
  });
}

function tail(file, n = 40) {
  try { return fs.readFileSync(file, "utf8").split(/\r?\n/).slice(-n).join("\n"); } catch { return "(no log)"; }
}

function startSidecar(port) {
  const root = path.join(process.resourcesPath, "studio");
  const py = path.join(root, "python", "python.exe");
  fs.mkdirSync(path.join(DATA_DIR, "logs"), { recursive: true });
  logFile = path.join(DATA_DIR, "logs", "server.log");
  const out = fs.openSync(logFile, "w");
  sidecar = spawn(py, [path.join(root, "backend", "run_server.py")], {
    cwd: path.join(root, "backend"),
    env: {
      ...process.env,
      AGENT_STUDIO_PORT: String(port),
      AGENT_STUDIO_HOME: DATA_DIR,
      AGENT_STUDIO_WEB_DIR: path.join(root, "web"),
      AGENT_STUDIO_DESKTOP: "1",
      PYTHONNOUSERSITE: "1",          // never pick up packages from the user's own Python
      PYTHONUTF8: "1",
      PYTHONUNBUFFERED: "1",
      PYTHONIOENCODING: "utf-8",
    },
    stdio: ["ignore", out, out],
    windowsHide: true,
  });
  sidecar.on("exit", (code) => {
    sidecar = null;
    if (!quitting) showError(`The Agent Studio server stopped (exit code ${code}).`);
  });
}

// Kill the server and everything it started (MCP servers, agents, voice workers): taskkill /T walks the process tree.
function stopSidecar() {
  return new Promise((resolve) => {
    if (!sidecar || !sidecar.pid) return resolve();
    const pid = sidecar.pid;
    execFile("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true }, () => resolve());
  });
}

function splashHtml(msg) {
  return "data:text/html;charset=utf-8," + encodeURIComponent(`<!doctype html><html><body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
    background:#f5f1ea;color:#2b2722;font:15px system-ui,sans-serif"><div style="text-align:center;max-width:560px;padding:0 24px">
    <div style="font:600 22px Georgia,serif;margin-bottom:10px">Agent Studio</div>
    <div id="msg">${msg}</div>
    <div id="hint" style="margin-top:14px;color:#6b6258;font-size:13px;line-height:1.5"></div>
    <div id="log" style="margin-top:14px;color:#8a8076;font:12px ui-monospace,Consolas,monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></div>
    <div id="late" style="display:none;margin-top:18px;font-size:13px;line-height:1.6"><span id="latemsg"></span><br>
      <a href="agentstudio:log" style="color:#b5532f">Show the log file</a> &nbsp;·&nbsp; <a href="agentstudio:quit" style="color:#b5532f">Quit</a></div>
    </div></body></html>`);
}

function fmtElapsed(ms) {
  const s = Math.floor(ms / 1000);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
}

// The last thing the server said (its own progress lines while it loads), shown under the splash text.
function lastLogLine() {
  const lines = tail(logFile, 5).split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  return lines.length ? lines[lines.length - 1].replace(/^\[boot\]\s*/, "").slice(0, 160) : "";
}

function updateSplash(elapsedMs) {
  if (!win || win.isDestroyed()) return;
  const parts = {
    msg: `Starting the local server... ${fmtElapsed(elapsedMs)}`,
    hint: elapsedMs >= SLOW_HINT_MS ? "The first start after installing can take a few minutes while Windows checks the new files. Later starts take seconds. You can leave this window open." : "",
    log: lastLogLine(),
    // Past the ceiling we still wait (the server is alive); the splash only offers the log and a way out. Nothing
    // here blocks: if the server answers a second later, the app opens as usual.
    latemsg: elapsedMs >= STARTUP_CEILING_MS ? "This is taking unusually long. The server is still running, so you can keep waiting." : "",
  };
  win.webContents.executeJavaScript(`(() => { const p = ${JSON.stringify(parts)}; for (const k in p) { const e = document.getElementById(k); if (e) e.textContent = p[k]; }
    const l = document.getElementById("late"); if (l) l.style.display = p.latemsg ? "block" : "none"; })()`).catch(() => {});
}

// The splash's two links ("agentstudio:log", "agentstudio:quit") are handled here; the splash runs no script of its own.
function splashLinks(wc) {
  wc.on("will-navigate", (e, url) => {
    if (!url.startsWith("agentstudio:")) return;
    e.preventDefault();
    if (url === "agentstudio:log" && logFile) shell.showItemInFolder(logFile);
    if (url === "agentstudio:quit") app.quit();          // before-quit stops the server's process tree
  });
}

function showError(msg) {
  const detail = logFile ? `\n\nLast lines of ${logFile}:\n\n${tail(logFile)}` : "";
  dialog.showMessageBoxSync({ type: "error", title: "Agent Studio", message: msg, detail: detail.slice(-3500) });
  quitting = true;
  app.quit();
}

// Window size and position survive restarts.
const STATE_FILE = path.join(DATA_DIR, "window-state.json");
function loadState() { try { return JSON.parse(fs.readFileSync(STATE_FILE, "utf8")); } catch { return { width: 1360, height: 900 }; } }
function saveState() {
  if (!win || win.isDestroyed()) return;
  try { fs.writeFileSync(STATE_FILE, JSON.stringify({ ...win.getNormalBounds(), maximized: win.isMaximized() })); } catch { /* best effort */ }
}

function createWindow() {
  const st = loadState();
  win = new BrowserWindow({ ...st, minWidth: 900, minHeight: 600, title: "Agent Studio", backgroundColor: "#f5f1ea", show: true,
    icon: path.join(__dirname, "build", "icon.png"),
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true } });
  if (st.maximized) win.maximize();
  win.setMenuBarVisibility(false);
  win.on("close", saveState);
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  return win;
}

function allowOwnOrigin(origin) {
  // Microphone (Live mode) and notifications (the bell) only for our own UI; everything else is refused.
  session.defaultSession.setPermissionRequestHandler((wc, permission, cb, details) => {
    const from = (details && (details.requestingUrl || details.securityOrigin)) || wc.getURL();
    cb(from.startsWith(origin) && ["media", "notifications", "clipboard-sanitized-write", "fullscreen"].includes(permission));
  });
  session.defaultSession.setPermissionCheckHandler((wc, permission, requestingOrigin) =>
    (requestingOrigin || "").startsWith(origin) && ["media", "notifications", "clipboard-sanitized-write", "fullscreen"].includes(permission));
}

async function main() {
  createWindow();
  if (DEV) { allowOwnOrigin(new URL(WEB_URL).origin); win.loadURL(WEB_URL); return; }
  win.loadURL(splashHtml("Starting the local server..."));
  const port = await freePort();
  startSidecar(port);
  const origin = `http://127.0.0.1:${port}`;
  allowOwnOrigin(origin);
  splashLinks(win.webContents);
  const t0 = Date.now();
  // No deadline: the only failures are "the process exited" (handled in startSidecar) and the user choosing Quit.
  while (sidecar && !quitting) {
    if (await health(port)) { win.loadURL(origin + "/"); return; }
    updateSplash(Date.now() - t0);
    await new Promise((r) => setTimeout(r, 500));
  }
}

app.on("before-quit", (e) => {
  if (quitting || !sidecar) return;
  e.preventDefault();
  quitting = true;
  saveState();
  stopSidecar().then(() => app.quit());
});
app.on("window-all-closed", () => app.quit());
