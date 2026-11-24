// Captures the documentation screenshots from the real app in real Chrome.
//   npm run docs:shots                 all shots in docs-src/shots.json
//   npm run docs:shots -- --only chat-home,mcp-list
// It never touches your own data: it starts a SECOND copy of the backend with an empty, temporary data folder (AGENT_STUDIO_HOME)
// and a second UI dev server, drives Chrome (headless, its own profile) over the DevTools protocol, and deletes everything after.
// No dependencies: Node's built-in WebSocket and fetch, and an installed Chrome or Edge.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(here, "..");
const REPO = path.resolve(FRONTEND, "..");
const OUT = path.join(FRONTEND, "public", "docs", "shots");
const arg = (name, dflt) => { const i = process.argv.indexOf(`--${name}`); return i > 0 ? process.argv[i + 1] : dflt; };
const API_PORT = Number(arg("api-port", 8777)), UI_PORT = Number(arg("ui-port", 3199)), CDP_PORT = Number(arg("cdp-port", 9333));
const ONLY = arg("only", "")?.split(",").filter(Boolean) ?? [];
const WIN = process.platform === "win32";
const log = (...a) => console.log("[shots]", ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome() {
  const env = process.env.CHROME_PATH;
  const cands = [env, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].filter(Boolean);
  const f = cands.find((c) => fs.existsSync(c));
  if (!f) throw new Error("Chrome or Edge was not found. Set CHROME_PATH to its program.");
  return f;
}

const procs = [];
function start(cmd, args, opts) {
  const p = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], windowsHide: true, ...opts });
  p.stdout.on("data", () => {}); p.stderr.on("data", () => {});
  procs.push(p);
  return p;
}
function killTree(p) {
  if (!p || p.exitCode !== null) return;
  if (WIN) spawnSync("taskkill", ["/PID", String(p.pid), "/T", "/F"], { stdio: "ignore" });
  else { try { process.kill(-p.pid); } catch { p.kill("SIGKILL"); } }
}

async function waitHttp(url, ms, what) {
  const end = Date.now() + ms;
  let last = "";
  while (Date.now() < end) {
    try { const r = await fetch(url); if (r.ok) return; last = `HTTP ${r.status}`; } catch (e) { last = e.message; }
    await sleep(500);
  }
  throw new Error(`${what} did not come up at ${url} (${last})`);
}

// ------------------------------------------------------------------------------------------------ DevTools protocol
class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.wait = new Map(); this.on = new Map(); ws.addEventListener("message", (m) => this.onMessage(JSON.parse(m.data))); }
  static async open(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => { ws.addEventListener("open", res, { once: true }); ws.addEventListener("error", () => rej(new Error("cannot reach Chrome")), { once: true }); });
    return new Cdp(ws);
  }
  onMessage(m) {
    if (m.id && this.wait.has(m.id)) { const { res, rej } = this.wait.get(m.id); this.wait.delete(m.id); m.error ? rej(new Error(m.error.message)) : res(m.result); }
    else if (m.method) (this.on.get(m.method) ?? []).forEach((f) => f(m.params));
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((res, rej) => this.wait.set(id, { res, rej }));
  }
  once(method) { return new Promise((res) => { const l = (p) => { this.on.set(method, (this.on.get(method) ?? []).filter((x) => x !== l)); res(p); }; this.on.set(method, [...(this.on.get(method) ?? []), l]); }); }
  async eval(expression) {
    const r = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? "script error");
    return r.result.value;
  }
}

const KEYS = { Escape: [27, "Escape"], Enter: [13, "Enter"], "/": [191, "Slash"], "?": [191, "Slash"] };
async function press(cdp, spec) {
  const parts = spec.split("+");
  const key = parts.pop();
  const mods = parts.reduce((m, p) => m | ({ Alt: 1, Control: 2, Meta: 4, Shift: 8 }[p] ?? 0), 0);
  const [code, name] = KEYS[key] ?? [key.toUpperCase().charCodeAt(0), `Key${key.toUpperCase()}`];
  const base = { key, code: name, windowsVirtualKeyCode: code, modifiers: mods };
  await cdp.send("Input.dispatchKeyEvent", { type: "rawKeyDown", ...base });
  await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
}

const q = (s) => JSON.stringify(s);
async function waitFor(cdp, sel, ms = 90000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await cdp.eval(`(() => { const e = document.querySelector(${q(sel)}); return !!e && (e.offsetParent !== null || getComputedStyle(e).position === "fixed"); })()`)) return;
    await sleep(300);
  }
  throw new Error(`"${sel}" never appeared`);
}

async function runStep(cdp, step) {
  if (step.waitFor) await waitFor(cdp, step.waitFor);
  else if (step.click) { await waitFor(cdp, step.click, 30000); await cdp.eval(`document.querySelector(${q(step.click)}).click()`); }
  else if (step.clickText) {
    const end = Date.now() + 30000;
    for (;;) {
      const ok = await cdp.eval(`(() => { const t = ${q(step.clickText)}; const el = [...document.querySelectorAll("button,a,[role=tab],summary")].find((e) => e.textContent.replace(/\\s+/g, " ").trim() === t && e.offsetParent !== null); if (el) { el.click(); return true; } return false; })()`);
      if (ok) break;
      if (Date.now() > end) throw new Error(`no button called "${step.clickText}"`);
      await sleep(300);
    }
  } else if (step.clickContains) {
    const end = Date.now() + 30000;
    for (;;) {
      const ok = await cdp.eval(`(() => { const t = ${q(step.clickContains)}; const els = [...document.querySelectorAll("button,a,[role=tab],li,div,span")].filter((e) => e.offsetParent !== null && !e.closest(".sa-panel") && e.textContent.replace(/\\s+/g, " ").trim().startsWith(t) && e.textContent.trim().length < t.length + 24); const pick = els.filter((e) => !e.closest(".sa-fab")); const el = pick[pick.length - 1]; if (el) { el.click(); return true; } return false; })()`);
      if (ok) break;
      if (Date.now() > end) throw new Error(`nothing starting with "${step.clickContains}" (buttons: ${(await cdp.eval("[...document.querySelectorAll('button')].map((b) => b.textContent.trim().slice(0, 24)).slice(0, 40).join(' | ')"))})`);
      await sleep(300);
    }
  } else if (step.eval) { await cdp.eval(step.eval);
  } else if (step.type) {
    await waitFor(cdp, step.type.sel, 30000);
    await cdp.eval(`(() => { const e = document.querySelector(${q(step.type.sel)}); e.focus(); const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(e), "value").set; set.call(e, ${q(step.type.text)}); e.dispatchEvent(new Event("input", { bubbles: true })); })()`);
  } else if (step.press) { await cdp.eval("document.activeElement && document.activeElement.blur && document.activeElement.blur()"); await press(cdp, step.press); }
  else if (step.scrollTo) { await waitFor(cdp, step.scrollTo, 30000); await cdp.eval(`document.querySelector(${q(step.scrollTo)}).scrollIntoView({ block: "start" })`); }
  else if (step.wait) await sleep(step.wait);
}

async function shoot(cdp, shot, base) {
  const mobile = !!shot.mobile;
  await cdp.send("Emulation.setDeviceMetricsOverride", mobile ? { width: 375, height: 812, deviceScaleFactor: 2, mobile: true } : { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
  const loaded = cdp.once("Page.loadEventFired");
  await cdp.send("Page.navigate", { url: base + shot.route });
  await Promise.race([loaded, sleep(120000)]);
  await sleep(500);
  if (shot.waitFor) await waitFor(cdp, shot.waitFor);
  else await waitFor(cdp, "main", 90000);
  for (const step of shot.actions ?? []) await runStep(cdp, step);
  if (shot.wait) await sleep(shot.wait);
  await sleep(900);                                              // let data arrive and animations settle
  if (mobile) {                                                 // a phone screen must never scroll sideways
    const w = await cdp.eval("[document.documentElement.scrollWidth, innerWidth, document.body.scrollWidth]");
    if (w[0] > w[1] + 1) throw new Error(`the page scrolls sideways on a phone (content ${w[0]}px wide, screen ${w[1]}px)`);
  }
  const params = { format: "webp", quality: 82, captureBeyondViewport: false };
  if (shot.clip) {
    const r = await cdp.eval(`(() => { const e = document.querySelector(${q(shot.clip)}); if (!e) return null; const b = e.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height }; })()`);
    if (!r) throw new Error(`nothing to crop to: ${shot.clip}`);
    params.clip = { ...r, scale: 1 };
  }
  const { data } = await cdp.send("Page.captureScreenshot", params);
  const file = path.join(OUT, shot.file ?? `${shot.id}.webp`);
  fs.writeFileSync(file, Buffer.from(data, "base64"));
  return fs.statSync(file).size;
}

// ------------------------------------------------------------------------------------------------ main
async function main() {
  const manifest = JSON.parse(fs.readFileSync(path.join(FRONTEND, "docs-src", "shots.json"), "utf8")).shots;
  const todo = manifest.filter((s) => !ONLY.length || ONLY.includes(s.id));
  if (!todo.length) throw new Error("no matching shots");
  fs.mkdirSync(OUT, { recursive: true });
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "as-shots-"));
  const home = path.join(tmp, "home"), profile = path.join(tmp, "chrome");
  fs.mkdirSync(home);
  const py = path.join(REPO, "backend", ".venv", WIN ? "Scripts/python.exe" : "bin/python");
  const apiUrl = `http://127.0.0.1:${API_PORT}`, uiUrl = `http://127.0.0.1:${UI_PORT}`;
  let chrome;
  try {
    log("starting a second backend with an empty data folder on", API_PORT);
    start(py, ["-m", "uvicorn", "app.main:create_app", "--factory", "--host", "127.0.0.1", "--port", String(API_PORT)], {
      cwd: path.join(REPO, "backend"),
      env: { ...process.env, AGENT_STUDIO_HOME: home, AGENT_STUDIO_EXTRA_ORIGINS: uiUrl, AGENT_STUDIO_ENABLE_MOCK: "1", AGENT_STUDIO_PORT: String(API_PORT) },
    });
    await waitHttp(`${apiUrl}/api/health`, 120000, "the backend");
    await fetch(`${apiUrl}/api/easy/onboarding`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "done" }) });
    log("starting a second UI on", UI_PORT);
    start(WIN ? "npx.cmd" : "npx", ["next", "dev", "-H", "127.0.0.1", "-p", String(UI_PORT)], {
      cwd: FRONTEND, shell: WIN,
      env: { ...process.env, NEXT_PUBLIC_API_BASE: apiUrl, NEXT_DIST_DIR: ".next-shots", NEXT_TELEMETRY_DISABLED: "1" },
    });
    await waitHttp(`${uiUrl}/`, 180000, "the UI");
    if (process.argv.includes("--serve")) {                       // keep the throw-away copy running so it can be tried by hand or by a test driver
      const ollama = process.argv.includes("--ollama");
      if (ollama) {                                                // a real local model for the assistant (free): the first model Ollama has that can use tools
        const tags = await (await fetch("http://127.0.0.1:11434/api/tags")).json().catch(() => ({ models: [] }));
        const model = (tags.models ?? []).map((m) => m.name).find((n) => /qwen3|llama3|mistral|gpt-oss/.test(n)) ?? "";
        const put = (p, body) => fetch(`${apiUrl}${p}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        await put("/api/providers/ollama", { enabled: true, default_model: model });
        await put("/api/settings", { sidebot_provider: "ollama", sidebot_model: model });
        log("assistant model:", model || "(none found)");
      }
      log(`READY ui=${uiUrl} api=${apiUrl} (stop with Ctrl+C or by ending this process)`);
      await sleep(Number(arg("serve-minutes", 45)) * 60000);
      return;
    }
    log("starting Chrome");
    chrome = start(findChrome(), [`--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${profile}`, "--headless=new", "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
      "--disable-gpu", "--mute-audio", "--force-color-profile=srgb", "about:blank"], {});
    await waitHttp(`http://127.0.0.1:${CDP_PORT}/json/version`, 30000, "Chrome");
    const targets = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
    const cdp = await Cdp.open(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
    await cdp.send("Page.addScriptToEvaluateOnNewDocument", { source: `
      try { localStorage.setItem("as-theme", "light"); localStorage.setItem("as-tour-done", "1"); sessionStorage.setItem("as-gate-checked", "1"); } catch (e) {}
      document.addEventListener("DOMContentLoaded", () => { const s = document.createElement("style");
        s.textContent = "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}nextjs-portal,[data-nextjs-dev-tools-button]{display:none!important}"; document.head.appendChild(s); });` });
    let ok = 0;
    const failed = [];
    for (const shot of todo) {
      try {
        const bytes = await shoot(cdp, shot, uiUrl);
        ok++;
        log(`ok   ${shot.id} (${Math.round(bytes / 1024)} KB)`);
      } catch (e) {
        failed.push(shot.id);
        let where = "";
        try { where = await cdp.eval("location.pathname + location.search + ' | ' + document.body.innerText.replace(/\\s+/g, ' ').slice(0, 240)"); } catch { /* the page may be gone */ }
        log(`FAIL ${shot.id}: ${e.message}${where ? ` (page: ${where})` : ""}`);
      }
    }
    log(`${ok} captured, ${failed.length} failed${failed.length ? `: ${failed.join(", ")}` : ""}`);
    process.exitCode = failed.length ? 1 : 0;
  } finally {
    for (const p of procs.reverse()) killTree(p);
    await sleep(800);
    try { fs.rmSync(tmp, { recursive: true, force: true }); } catch { /* the temp folder may still be in use; the system cleans it */ }
  }
}

main().catch((e) => { console.error("[shots] stopped:", e.message); process.exitCode = 1; });
