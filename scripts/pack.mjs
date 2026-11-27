// Builds the Windows installer: electron/dist/Agent-Studio-Setup-<version>.exe
//   1. the static UI (Next export into frontend/.next-pack, same-origin API) -> electron/stage/studio/web
//   2. a private Python runtime: the Python the backend venv was made from (standard library, venv, tkinter)
//      plus the venv's packages without the test tools        -> electron/stage/studio/python
//   3. the backend source, built-in skills, sandbox Dockerfile -> electron/stage/studio/{backend,skills,docker,docs}
//   4. electron-builder (NSIS, per-user)                       -> electron/dist
// Heavy optional parts (voice, red-team tools, guard and decision models, Ollama) are NOT bundled: the app installs
// them on demand into the data folder, as in development.
// Usage: npm run pack            (all steps)
//        npm run pack -- --skip-web --skip-installer   (reuse the last UI build / stop after staging)
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = path.join(ROOT, "frontend");
const BACK = path.join(ROOT, "backend");
const STAGE = path.join(ROOT, "electron", "stage", "studio");
const WEB_OUT = path.join(FRONT, ".next-pack");      // with output: "export" and a custom distDir, Next writes the export there
const args = new Set(process.argv.slice(2));
const log = (m) => console.log(`[pack] ${m}`);

function run(cmd, argv, opts = {}) {
  log(`${cmd} ${argv.join(" ")}`);
  execFileSync(cmd, argv, { stdio: "inherit", shell: process.platform === "win32" && !cmd.endsWith(".exe"), ...opts });
}

// Test tools and leftovers never ship.
const DROP_PKG = /^(~|_pytest|pytest|py\.py$|coverage|iniconfig|pluggy|a1_coverage\.pth$|respx)/i;
const copy = (from, to, skip = () => false) => fs.cpSync(from, to, {
  recursive: true,
  filter: (src) => { const b = path.basename(src); return b !== "__pycache__" && !b.endsWith(".pyc") && !skip(src, b); },
});

function web() {
  if (args.has("--skip-web") && fs.existsSync(path.join(WEB_OUT, "index.html"))) return log("reusing the last UI build");
  run("npm", ["run", "docs"], { cwd: FRONT });
  const env = { ...process.env, NEXT_PUBLIC_API_BASE: "", NEXT_DIST_DIR: ".next-pack" };   // "" = same origin as the API
  run("npx", ["next", "build"], { cwd: FRONT, env });
  if (!fs.existsSync(path.join(WEB_OUT, "index.html"))) throw new Error(`the UI build has no index.html in ${WEB_OUT}`);
}

function basePython() {
  const cfg = fs.readFileSync(path.join(BACK, ".venv", "pyvenv.cfg"), "utf8");
  const home = /^home\s*=\s*(.+)$/m.exec(cfg)?.[1].trim();
  if (!home || !fs.existsSync(path.join(home, "python.exe"))) throw new Error(`cannot find the venv's base Python (pyvenv.cfg home = ${home})`);
  return home;
}

function stage() {
  fs.rmSync(path.join(ROOT, "electron", "stage"), { recursive: true, force: true });
  fs.mkdirSync(STAGE, { recursive: true });
  const home = basePython();
  const py = path.join(STAGE, "python");
  log(`python runtime from ${home}`);
  fs.mkdirSync(py, { recursive: true });
  for (const f of fs.readdirSync(home)) if (/\.(exe|dll)$/i.test(f) || f === "LICENSE.txt") fs.copyFileSync(path.join(home, f), path.join(py, f));
  for (const d of ["DLLs", "tcl"]) if (fs.existsSync(path.join(home, d))) copy(path.join(home, d), path.join(py, d));
  copy(path.join(home, "Lib"), path.join(py, "Lib"), (src, b) => {
    const rel = path.relative(path.join(home, "Lib"), src).split(path.sep);
    return ["site-packages", "test", "idlelib"].includes(rel[0]) || (rel.length > 1 && (b === "test" || b === "tests") && fs.statSync(src).isDirectory());
  });
  log("site-packages from backend/.venv");
  copy(path.join(BACK, ".venv", "Lib", "site-packages"), path.join(py, "Lib", "site-packages"),
    (src, b) => path.dirname(src) === path.join(BACK, ".venv", "Lib", "site-packages") && DROP_PKG.test(b));

  log("backend, skills, docker, docs");
  copy(path.join(BACK, "app"), path.join(STAGE, "backend", "app"));
  for (const f of ["run_server.py", "requirements.txt"]) fs.copyFileSync(path.join(BACK, f), path.join(STAGE, "backend", f));
  copy(path.join(ROOT, "skills"), path.join(STAGE, "skills"));
  copy(path.join(ROOT, "docker"), path.join(STAGE, "docker"));
  fs.mkdirSync(path.join(STAGE, "docs"), { recursive: true });
  fs.copyFileSync(path.join(ROOT, "docs", "SETTINGS.md"), path.join(STAGE, "docs", "SETTINGS.md"));
  fs.copyFileSync(path.join(ROOT, "package-lock.json"), path.join(STAGE, "package-lock.json"));            // the AI-BOM lists these
  fs.mkdirSync(path.join(STAGE, "frontend"), { recursive: true });
  fs.copyFileSync(path.join(FRONT, "package-lock.json"), path.join(STAGE, "frontend", "package-lock.json"));
  copy(WEB_OUT, path.join(STAGE, "web"), (src, b) => b === "cache" || b === "types");

  // Byte-compile once here so the first start is not slowed by compiling thousands of modules. The installer gives
  // every file a new modified time, which makes ordinary (timestamp-checked) .pyc files look out of date: Python then
  // recompiles and rewrites every module on the first start (measured: all 11,230 shipped .pyc were stale after an
  // install). "unchecked-hash" .pyc files are used as they are, whatever the file times.
  log("byte-compiling");
  try {
    execFileSync(path.join(py, "python.exe"), ["-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "unchecked-hash", "-x", "[\\\\/](test|tests)[\\\\/]", "Lib", path.join(STAGE, "backend")],
      { cwd: py, stdio: "inherit", env: { ...process.env, PYTHONNOUSERSITE: "1" } });
  } catch { log("compileall reported files it could not compile (templates and the like); continuing"); }
  // Smoke test the staged runtime: the server module must import with nothing but what we ship.
  execFileSync(path.join(py, "python.exe"), ["-c", "import sys; sys.path.insert(0, 'backend'); import app.main, venv, tkinter; print('staged runtime ok', sys.version.split()[0])"],
    { cwd: STAGE, stdio: "inherit", env: { ...process.env, PYTHONNOUSERSITE: "1", AGENT_STUDIO_HOME: path.join(ROOT, "electron", "stage", "smoke-home") } });
  fs.rmSync(path.join(ROOT, "electron", "stage", "smoke-home"), { recursive: true, force: true });
}

function installer() {
  if (!fs.existsSync(path.join(ROOT, "electron", "node_modules", "electron-builder"))) run("npm", ["--prefix", "electron", "install"]);
  run("npm", ["run", "dist"], { cwd: path.join(ROOT, "electron") });
  const v = JSON.parse(fs.readFileSync(path.join(ROOT, "electron", "package.json"), "utf8")).version;
  const exe = path.join(ROOT, "electron", "dist", `Agent-Studio-Setup-${v}.exe`);
  log(`installer: ${exe} (${(fs.statSync(exe).size / 1024 / 1024).toFixed(0)} MB)`);
}

web();
stage();
if (!args.has("--skip-installer")) installer();
