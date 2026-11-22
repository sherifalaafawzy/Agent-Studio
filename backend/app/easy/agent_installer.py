"""One-click install of coding agents (ACP and CLI), driven by the official ACP registry plus our own per-agent overrides.

How an install is chosen (safest first), per agent:
  1. npm  - a version that has been public for at least 14 days (`npm view <pkg> time`), installed globally.
  2. uv   - for Python tools (Aider), pinned.
  3. winget - Windows' package manager (installs the version winget lists).
  4. archive - a release archive from the registry, only when the registry publishes its SHA-256 (verified before unpacking).
  5. npx  - nothing to install: we register the pinned `npx` launch command (npx fetches it on first run).
  6. script - the vendor's own `irm ... | iex` installer. Run ONLY after the user is shown the exact command and confirms it.
  7. manual - instructions only (OpenHands needs WSL; Aider has no ACP mode).
Everything is user-triggered, logged, and injectable for tests. We never run a command the user was not shown.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ..acp_agents import AcpError, AcpStore, _refresh_path
from ..d1_setup import run_streaming
from ..db import data_dir

REGISTRY_URL = "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json"
SNAPSHOT = Path(__file__).with_name("acp_registry_snapshot.json")
REGISTRY_TTL_S = 24 * 3600
MIN_AGE_DAYS = 14
TRANSPORT: httpx.BaseTransport | None = None
PKG_RE = re.compile(r"^(@[a-z0-9][\w.\-]*/)?[a-z0-9][\w.\-]*$")
WIN_KEY = "windows-x86_64"

# Our own knowledge of each agent: how to install it, how to sign in, and who pays. Ids match the built-in external agents.
AGENTS: dict[str, dict] = {
    "claude": dict(label="Claude Code", cli="claude", npm="@anthropic-ai/claude-code", winget="Anthropic.ClaudeCode",
                   script="irm https://claude.ai/install.ps1 | iex", acp_note="Agent Studio talks to it through Anthropic's ACP adapter (fetched by npx on first use; needs Node.js).",
                   signin="Open a terminal, type `claude` and sign in with your Claude account. Or store an ANTHROPIC_API_KEY under Settings > External agents.",
                   cost="Your Claude subscription or Anthropic API key. Not covered by Agent Studio's spend limits.", known=["~/.local/bin/claude.exe", "~/.local/bin/claude"]),
    "codex": dict(label="OpenAI Codex", cli="codex", npm="@openai/codex", winget="OpenAI.Codex", acp_note="Uses the ACP adapter (fetched by npx; needs Node.js).",
                  signin="In a terminal run `codex login` (ChatGPT account), or store an OPENAI_API_KEY under Settings > External agents.",
                  cost="Your ChatGPT plan or OpenAI API key. Not covered by Agent Studio's spend limits.", known=[]),
    "cursor": dict(label="Cursor", cli="cursor-agent", script="irm 'https://cursor.com/install?win32=true' | iex", acp_note="",
                   signin="In a terminal run `cursor-agent login`, or store a CURSOR_API_KEY under Settings > External agents.",
                   cost="Your Cursor plan. Not covered by Agent Studio's spend limits.", known=["%LOCALAPPDATA%/cursor-agent/cursor-agent.cmd"]),
    "antigravity": dict(label="Antigravity", cli="agy", script="irm https://antigravity.google/cli/install.ps1 | iex", acp_note="",
                        signin="Run `agy acp --login` once (Google account), or store a GEMINI_API_KEY under Settings > External agents.",
                        cost="Your Google account or Gemini API key. Not covered by Agent Studio's spend limits.", known=["%LOCALAPPDATA%/agy/bin/agy.exe"]),
    "grok": dict(label="Grok Build", cli="grok", npm="@xai-official/grok", acp_note="",
                 signin="Run `grok` in a terminal and sign in with your xAI account first.", cost="Your xAI account. Not covered by Agent Studio's spend limits.", known=[]),
    "kiro": dict(label="Kiro", cli="kiro-cli", script="irm 'https://cli.kiro.dev/install.ps1' | iex", acp_note="",
                 signin="Run `kiro-cli login` once.", cost="Your Kiro / AWS sign-in. Not covered by Agent Studio's spend limits.", known=[]),
    "gemini": dict(label="Gemini CLI", cli="gemini", npm="@google/gemini-cli", registry="gemini", acp_args=["--acp"], acp_note="",
                   signin="Run `gemini` once in a terminal and sign in with Google, or store a GEMINI_API_KEY.", cost="Your Google account or Gemini API key. Not covered by Agent Studio's spend limits.", known=[]),
    "copilot": dict(label="GitHub Copilot CLI", cli="copilot", npm="@github/copilot", registry="github-copilot-cli", acp_args=["--acp"], acp_note="",
                    signin="Run `copilot` once and sign in with GitHub.", cost="Your GitHub Copilot plan. Not covered by Agent Studio's spend limits.", known=[]),
    "aider": dict(label="Aider", cli="aider", uv="aider-chat==0.86.2", acp_note="Not an ACP agent: Agent Studio runs it one task at a time from the command line.",
                  signin="Add your model provider's key (for example OPENAI_API_KEY) under Settings > External agents.", cost="Your own model provider key. Not covered by Agent Studio's spend limits.", known=[]),
    "openhands": dict(label="OpenHands", cli="openhands", manual="OpenHands only runs on Windows inside WSL (Ubuntu). In WSL run: uv tool install openhands==1.16.0 --python 3.12. "
                      "Then set WSL_DISTRO and your model keys under Settings > External agents. It approves every action by itself, so Agent Studio asks before each run.",
                      acp_note="", signin="Set LLM_MODEL and LLM_API_KEY under Settings > External agents.", cost="Your own model provider key.", known=[]),
}


class InstallError(Exception):
    """A user-facing reason the step cannot run."""


# ------------------------------------------------------------------ registry
class Registry:
    def __init__(self, fetch: Callable[[], dict] | None = None, home: Path | None = None, clock: Callable[[], float] = time.time):
        self._fetch, self._home, self._now = fetch or _fetch_registry, home, clock

    @property
    def _file(self) -> Path:
        return (self._home or data_dir()) / "acp-registry.json"

    def get(self, refresh: bool = False) -> dict:
        """{agents, source: live|cache|snapshot, fetched_at}. Falls back to the cache, then to the snapshot shipped with the app."""
        cached = None
        try:
            cached = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if cached and not refresh and self._now() - cached.get("fetched_at", 0) < REGISTRY_TTL_S:
            return dict(agents=cached["agents"], source="cache", fetched_at=cached["fetched_at"])
        try:
            d = self._fetch()
            agents = d["agents"]
            if not isinstance(agents, list):
                raise ValueError("bad registry")
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(json.dumps(dict(agents=agents, fetched_at=self._now())), encoding="utf-8")
            return dict(agents=agents, source="live", fetched_at=self._now())
        except (httpx.HTTPError, ValueError, KeyError, OSError):
            if cached:
                return dict(agents=cached["agents"], source="cache", fetched_at=cached.get("fetched_at"))
            snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
            return dict(agents=snap["agents"], source="snapshot", fetched_at=None)


def _fetch_registry() -> dict:
    with httpx.Client(transport=TRANSPORT, timeout=15, follow_redirects=True) as c:
        r = c.get(REGISTRY_URL)
        r.raise_for_status()
        return r.json()


# ------------------------------------------------------------------ helpers
def pick_version(times: dict, now: datetime | None = None, min_age_days: int = MIN_AGE_DAYS) -> tuple[str, str] | None:
    """Newest stable version published at least `min_age_days` ago, from `npm view <pkg> time --json`. Returns (version, date)."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=min_age_days)
    best = None
    for v, stamp in times.items():
        if not re.match(r"^\d+\.\d+\.\d+$", v):
            continue
        try:
            when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when <= cutoff:
            key = tuple(int(x) for x in v.split("."))
            if best is None or key > best[0]:
                best = (key, v, when.date().isoformat())
    return (best[1], best[2]) if best else None


def _capture(cmd: list[str], timeout: float = 60) -> str | None:
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


def safe_extract(archive: Path, dest: Path) -> None:
    """Unpack a zip or tar archive, refusing any member that would land outside `dest`."""
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                if not (dest / n).resolve().is_relative_to(dest):
                    raise InstallError("the archive contains a file that would be written outside its folder, so it was not unpacked")
            z.extractall(dest)
        return
    with tarfile.open(archive) as t:
        for m in t.getmembers():
            if not (dest / m.name).resolve().is_relative_to(dest):
                raise InstallError("the archive contains a file that would be written outside its folder, so it was not unpacked")
        t.extractall(dest, filter="data")


# ------------------------------------------------------------------ the installer
class AgentInstaller:
    def __init__(self, store: AcpStore, registry: Registry | None = None, runner: Callable | None = None, which: Callable = shutil.which,
                 capture: Callable[[list[str]], str | None] = _capture, version_of: Callable[[str], str | None] | None = None,
                 home: Path | None = None, download: Callable[[str, Path], str] | None = None, platform: str | None = None,
                 now: Callable[[], datetime] | None = None):
        self.store, self.registry = store, registry or Registry()
        self._run = runner or run_streaming
        self._which, self._capture = which, capture
        self._version = version_of or _cli_version
        self._home, self._download, self.platform = home, download or _download, platform or sys.platform
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._pins: dict[str, tuple[float, tuple[str, str] | None]] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._job: dict = dict(state="idle", agent="", action="", log=[], error="")

    # ---- detection
    def detect(self, aid: str) -> dict:
        _refresh_path()
        meta = AGENTS.get(aid, {})
        cli = meta.get("cli") or aid
        exe = self._which(cli)
        if not exe:
            for k in meta.get("known", []):
                if _expand(k).is_file():
                    exe = str(_expand(k))
                    break
        if not exe:
            return dict(installed=False, path=None, version=None)
        return dict(installed=True, path=exe, version=self._version(exe))

    def _npm_has(self, pkg: str) -> bool:
        raw = self._capture(["npm", "ls", "-g", "--depth=0", "--json"])
        try:
            return pkg in (json.loads(raw or "{}").get("dependencies") or {})
        except ValueError:
            return False

    # ---- planning (what exactly would run)
    def _npm_pin(self, pkg: str) -> tuple[str, str] | None:
        """Newest version public for 14+ days. Cached for an hour: `npm view` takes a second or two per package."""
        if not PKG_RE.match(pkg):
            return None
        hit = self._pins.get(pkg)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
        try:
            times = json.loads(self._capture(["npm", "view", pkg, "time", "--json"]) or "{}")
        except ValueError:
            return None
        pin = pick_version(times, self._now())
        self._pins[pkg] = (time.time(), pin)
        return pin

    def _prefetch(self, registry_agents: list[dict], known: set) -> None:
        """Look up every package's safe version at once (each `npm view` takes a second or two), so the catalog is not slow."""
        if not self._which("npm"):
            return
        pkgs = {m["npm"] for m in AGENTS.values() if m.get("npm")}
        for a in registry_agents:
            n = (a.get("distribution") or {}).get("npx")
            if n:
                pkgs.add(n["package"].rsplit("@", 1)[0])
        todo = [p for p in pkgs if p not in self._pins or time.time() - self._pins[p][0] >= 3600]
        if todo:
            with ThreadPoolExecutor(max_workers=8) as ex:
                list(ex.map(self._npm_pin, todo))

    def methods(self, aid: str) -> list[dict]:
        """The ways this agent can be installed here, safest first. Each has an exact command shown to the user."""
        meta = AGENTS.get(aid)
        if meta is None:
            return self._registry_methods(aid)
        out: list[dict] = []
        if meta.get("manual"):
            return [dict(kind="manual", label="Manual setup", command="", text=meta["manual"], safe=True)]
        if meta.get("npm") and self._which("npm"):
            pin = self._npm_pin(meta["npm"])
            if pin:
                out.append(dict(kind="npm", label=f"Install with npm (version {pin[0]}, public since {pin[1]})", command=f"npm install -g {meta['npm']}@{pin[0]}",
                                argv=["npm", "install", "-g", f"{meta['npm']}@{pin[0]}"], safe=True, version=pin[0]))
        if meta.get("uv") and self._which("uv"):
            out.append(dict(kind="uv", label="Install with uv (pinned)", command=f"uv tool install --python 3.12 {meta['uv']}",
                            argv=["uv", "tool", "install", "--python", "3.12", meta["uv"]], safe=True))
        if meta.get("winget") and self.platform == "win32" and self._which("winget"):
            out.append(dict(kind="winget", label="Install with winget (Windows package manager)", argv=["winget", "install", "-e", "--id", meta["winget"],
                            "--accept-package-agreements", "--accept-source-agreements", "--silent"], safe=True,
                            command=f"winget install -e --id {meta['winget']} --silent", note="Installs the version winget lists; it cannot be pinned to 14 days."))
        reg = self._registry_entry(meta.get("registry"))
        if meta.get("registry") and reg and "npx" in reg.get("distribution", {}):
            n = reg["distribution"]["npx"]
            pkg = n["package"].rsplit("@", 1)[0]
            pin = self._npm_pin(pkg) if self._which("npm") else None
            if pin:
                out.append(dict(kind="npx", label=f"Add to Agent Studio (runs through npx, version {pin[0]}, needs Node.js)", command=f"npx -y {pkg}@{pin[0]} {' '.join(n.get('args', []))}".strip(),
                                safe=True, version=pin[0], pkg=pkg, args=n.get("args", [])))
        if meta.get("script") and self.platform == "win32":
            out.append(dict(kind="script", label="Run the vendor's own installer script", command=meta["script"], safe=False,
                            warning="This downloads a script from the internet and runs it on your PC. Only continue if you trust the vendor. Agent Studio cannot check what it does."))
        if not out:
            out.append(dict(kind="manual", label="Manual setup", command="", safe=True,
                            text=f"No one-click method is available on this PC (needs {'npm (install Node.js first)' if meta.get('npm') else 'a supported installer'}). See the vendor's site for {meta['label']}."))
        return out

    def _registry_entry(self, rid: str | None) -> dict | None:
        return next((a for a in self.registry.get()["agents"] if a.get("id") == rid), None) if rid else None

    def _registry_methods(self, rid: str) -> list[dict]:
        a = self._registry_entry(rid)
        if a is None:
            raise InstallError(f"unknown agent '{rid}'")
        dist = a.get("distribution", {})
        out = []
        if "npx" in dist:
            n = dist["npx"]
            pkg = n["package"].rsplit("@", 1)[0]
            pin = self._npm_pin(pkg) if self._which("npm") else None
            if pin:
                out.append(dict(kind="npx", label=f"Add to Agent Studio (runs through npx, version {pin[0]}, needs Node.js)", command=f"npx -y {pkg}@{pin[0]} {' '.join(n.get('args', []))}".strip(),
                                safe=True, version=pin[0], pkg=pkg, args=n.get("args", [])))
        b = (dist.get("binary") or {}).get(WIN_KEY)
        if b and self.platform == "win32" and b.get("archive", "").startswith("https://") and re.match(r"^[0-9a-f]{64}$", b.get("sha256", "")):
            out.append(dict(kind="archive", label=f"Download {a['name']} {a['version']} and check its checksum", command=f"download {b['archive']} and verify SHA-256 {b['sha256'][:12]}...",
                            safe=True, url=b["archive"], sha256=b["sha256"], cmd=b.get("cmd", ""), args=b.get("args", []), version=a["version"]))
        if not out:
            out.append(dict(kind="manual", label="Manual setup", command="", safe=True,
                            text=f"No verified one-click install is available for {a['name']} on Windows. See {a.get('website') or 'its website'}."))
        return out

    def catalog(self) -> dict:
        """Every agent we know with its state, so the UI can draw one card each."""
        reg = self.registry.get()
        known = {m.get("registry") for m in AGENTS.values()} | {"claude-acp", "codex-acp", "cursor", "antigravity-acp", "grok-build", "kiro"}
        self._prefetch(reg["agents"], known)
        out = []
        for aid, meta in AGENTS.items():
            d = self.detect(aid)
            out.append(dict(id=aid, label=meta["label"], installed=d["installed"], path=d["path"], version=d["version"], signin=meta["signin"], cost=meta["cost"],
                            note=meta["acp_note"], manual=meta.get("manual", ""), registry_extra=False, methods=self.methods(aid)))
        for a in reg["agents"]:
            if a["id"] in known:
                continue
            out.append(dict(id=a["id"], label=a["name"], installed=False, path=None, version=None, signin="Follow the agent's own sign-in after installing.",
                            cost="Its own account or your model keys. Not covered by Agent Studio's spend limits.", note=a.get("description", ""), manual="",
                            registry_extra=True, methods=self._registry_methods(a["id"])))
        return dict(agents=out, registry=dict(source=reg["source"], fetched_at=reg["fetched_at"]))

    # ---- running
    def status(self) -> dict:
        with self._lock:
            return dict(self._job, log=list(self._job["log"][-40:]), running=bool(self._thread and self._thread.is_alive()))

    def join(self, timeout: float = 30) -> None:
        if self._thread:
            self._thread.join(timeout)

    def start(self, aid: str, kind: str, confirm_command: str = "", action: str = "install") -> dict:
        methods = {m["kind"]: m for m in self.methods(aid)}
        m = methods.get(kind)
        if m is None or kind == "manual":
            raise InstallError("that install method is not available here")
        if m["kind"] == "script" and confirm_command.strip() != m["command"]:
            raise InstallError("To run the vendor's installer script you must confirm the exact command shown on screen.")
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise InstallError("another install is already running")
            self._job = dict(state="running", agent=aid, action=action, log=[], error="", method=kind)
        self._thread = threading.Thread(target=self._work, args=(aid, m), daemon=True, name=f"agent-install-{aid}")
        self._thread.start()
        return self.status()

    def _log(self, line: str) -> None:
        with self._lock:
            self._job["log"].append(line)

    def _finish(self, ok: bool, error: str = "") -> None:
        with self._lock:
            self._job.update(state="done" if ok else "failed", error=error)

    def _exec(self, argv: list[str]) -> int:
        self._log("$ " + " ".join(argv))
        return self._run(argv, dict(os.environ), self._log)

    def _work(self, aid: str, m: dict) -> None:
        try:
            if m["kind"] in ("npm", "uv", "winget"):
                code = self._exec(m["argv"])
                if code != 0:
                    return self._finish(False, f"The installer stopped with code {code}. The last lines of its output are shown above.")
            elif m["kind"] == "script":
                ps = shutil.which("powershell") or "powershell"
                code = self._exec([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", m["command"]])
                if code != 0:
                    return self._finish(False, f"The vendor's script stopped with code {code}.")
            elif m["kind"] == "archive":
                self._archive(aid, m)
            _refresh_path()
            self._wire(aid, m)
            self._finish(True)
        except (InstallError, AcpError, OSError, httpx.HTTPError) as e:
            self._finish(False, str(e) or type(e).__name__)

    def _archive(self, aid: str, m: dict) -> None:
        dest = (self._home or data_dir()) / "agents" / aid
        tmp = dest.with_suffix(".download")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        self._log(f"downloading {m['url']}")
        got = self._download(m["url"], tmp)
        if got != m["sha256"]:
            tmp.unlink(missing_ok=True)
            raise InstallError("The downloaded file does not match the checksum published in the ACP registry, so it was deleted and NOT unpacked.")
        self._log("checksum OK")
        if dest.exists():
            shutil.rmtree(dest)
        safe_extract(tmp, dest)
        tmp.unlink(missing_ok=True)

    def _wire(self, aid: str, m: dict) -> None:
        """Make the installed agent usable from Agent Studio (an External agent row), without touching built-in launch commands."""
        meta = AGENTS.get(aid)
        if meta and aid in {r["id"] for r in self.store.list() if r["builtin"]}:
            self.store.update(aid, dict(enabled=True))
            return
        if m["kind"] == "npx":
            cmd, args = "npx", ["-y", f"{m['pkg']}@{m['version']}", *m.get("args", [])]
        elif m["kind"] == "archive":
            cmd, args = str((self._home or data_dir()) / "agents" / aid / m["cmd"]), list(m.get("args", []))
        else:                                                       # npm-installed extras (gemini, copilot): the CLI is on PATH
            cmd, args = (meta or {}).get("cli", aid), list((meta or {}).get("acp_args", []))
        label = (meta or {}).get("label") or aid
        sid = re.sub(r"[^a-z0-9_-]", "-", aid.lower())[:31]
        if not re.match(r"^[a-z]", sid):
            sid = "a" + sid
        try:
            self.store.get(sid)
            self.store.update(sid, dict(command=cmd, args=args, enabled=True))
        except AcpError:
            self.store.create(sid, label, cmd, args, notes="Added by one-click install.")

    def uninstall(self, aid: str) -> dict:
        meta = AGENTS.get(aid) or {}
        d = self.detect(aid)
        if not d["installed"]:
            raise InstallError("it is not installed")
        if meta.get("npm") and self._npm_has(meta["npm"]):
            argv = ["npm", "uninstall", "-g", meta["npm"]]
        elif meta.get("uv"):
            argv = ["uv", "tool", "uninstall", meta["uv"].split("==")[0]]
        else:
            raise InstallError("Agent Studio can only remove agents it installed with npm or uv. Remove this one from Windows' 'Installed apps' list.")
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise InstallError("another install is already running")
            self._job = dict(state="running", agent=aid, action="uninstall", log=[], error="")
        self._thread = threading.Thread(target=lambda: self._finish(self._exec(argv) == 0, "The uninstall command failed."), daemon=True)
        self._thread.start()
        return self.status()


def _cli_version(exe: str) -> str | None:
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=8, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or r.stderr or "").strip().splitlines()
    return out[0][:80] if r.returncode == 0 and out else None


def _download(url: str, dest: Path) -> str:
    h = hashlib.sha256()
    with httpx.Client(transport=TRANSPORT, timeout=60, follow_redirects=True) as c, c.stream("GET", url) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                h.update(chunk)
    return h.hexdigest()
