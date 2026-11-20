// Electron shell. Dev: loads the Next dev server. Packaged (later phase): spawns the
// PyInstaller FastAPI sidecar from resources/ and serves the static Next export.
const { app, BrowserWindow, shell } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");

const DEV = !app.isPackaged;
const API_PORT = process.env.AGENT_STUDIO_PORT || "8765";
const WEB_URL = process.env.AGENT_STUDIO_WEB_URL || "http://127.0.0.1:3100";
let sidecar = null;

function startSidecar() {
  if (DEV) return; // `npm run dev:desktop` already runs the API
  const exe = path.join(process.resourcesPath, "sidecar", "agent-studio-api.exe");
  sidecar = spawn(exe, [], { env: { ...process.env, AGENT_STUDIO_PORT: API_PORT, AGENT_STUDIO_HOME: app.getPath("userData") }, stdio: "inherit" });
  sidecar.on("exit", () => { sidecar = null; });
}

function createWindow() {
  const win = new BrowserWindow({ width: 1280, height: 860, title: "Agent Studio", backgroundColor: "#0f1115",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true } });
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
  if (DEV) win.loadURL(WEB_URL);
  else win.loadFile(path.join(process.resourcesPath, "web", "index.html")); // TODO(packaging): serve via custom app:// scheme
  // ACP (Agent Client Protocol) IDE bridge: later phase - will be a second sidecar endpoint, not Electron code.
}

app.whenReady().then(() => { startSidecar(); createWindow(); });
app.on("window-all-closed", () => { if (sidecar) sidecar.kill(); app.quit(); });
