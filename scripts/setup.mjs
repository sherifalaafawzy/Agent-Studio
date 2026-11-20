// One-time setup: Python venv + backend deps + frontend deps (+ root dev deps).
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const run = (cmd, args, cwd) => {
  console.log(`\n> ${cmd} ${args.join(" ")}  (${cwd})`);
  const r = spawnSync(cmd, args, { cwd, stdio: "inherit", shell: true });
  if (r.status !== 0) process.exit(r.status ?? 1);
};
const be = join(root, "backend");
if (!existsSync(join(be, ".venv"))) run("python", ["-m", "venv", ".venv"], be); // needs Python >= 3.11
run(join(be, ".venv", "Scripts", "python"), ["-m", "pip", "install", "-r", "requirements.txt"], be);
run("npm", ["install"], join(root, "frontend"));
console.log("\nSetup complete. Start everything with:  npm run dev");
