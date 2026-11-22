"""One-click Ollama install (Windows), start, and honest instructions elsewhere.

Safety rules this module follows:
  * Nothing runs until the user clicks Install. The download goes to Agent Studio's data folder.
  * The installer is only fetched from github.com/ollama/ollama release assets (or ollama.com's own redirect when GitHub is
    unreachable) and its SHA-256 is checked against GitHub's published digest, else against the winget manifest. With neither
    the install is refused unless the user explicitly accepts "unverified checksum".
  * The silent flags are third-party-documented (Ollama documents only /DIR), so if the silent run fails we open the normal
    installer window and say what to click, instead of pretending.
  * macOS/Linux: instructions only. We never run a download-and-pipe-to-shell script.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from ..db import data_dir
from .syscheck import OLLAMA_URL, ollama_state

RELEASE_API = "https://api.github.com/repos/ollama/ollama/releases/latest"
ASSET_NAME = "OllamaSetup.exe"
ASSET_PREFIX = "https://github.com/ollama/ollama/releases/download/"
OFFICIAL_DOWNLOAD = "https://ollama.com/download/OllamaSetup.exe"
WINGET_MANIFEST = "https://raw.githubusercontent.com/microsoft/winget-pkgs/master/manifests/o/Ollama/Ollama/{v}/Ollama.Ollama.installer.yaml"
SILENT_ARGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
TRANSPORT: httpx.BaseTransport | None = None
HEADERS = {"User-Agent": "AgentStudio-setup/1.0 (local app)", "Accept": "application/vnd.github+json"}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class SetupError(Exception):
    """A user-facing reason the step cannot run."""


def default_paths() -> list[Path]:
    local = os.environ.get("LOCALAPPDATA", "")
    return [p for p in (Path(local) / "Programs" / "Ollama" / "ollama.exe" if local else None, Path("/usr/local/bin/ollama"),
                        Path("/opt/homebrew/bin/ollama"), Path("/Applications/Ollama.app/Contents/Resources/ollama")) if p]


def find_ollama() -> str | None:
    """Where the ollama program is on this PC (PATH first, then the usual install folders)."""
    exe = shutil.which("ollama")
    if exe:
        return exe
    for p in default_paths():
        if p.is_file():
            return str(p)
    return None


def manual_instructions(platform: str) -> dict:
    if platform == "darwin":
        return dict(title="Install Ollama on a Mac", steps=["Download Ollama for macOS from https://ollama.com/download and open it.",
                                                             "Or, if you use Homebrew, run: brew install --cask ollama"], url="https://ollama.com/download")
    if platform.startswith("linux"):
        return dict(title="Install Ollama on Linux", steps=["Follow the manual install steps at https://docs.ollama.com/linux (download the archive, unpack it, start `ollama serve`).",
                                                            "Ollama also offers a one-line install script; Agent Studio does not run scripts like that for you. "
                                                            "Read it first at https://ollama.com/install.sh if you choose to run it yourself."], url="https://docs.ollama.com/linux")
    return dict(title="Install Ollama", steps=["Download it from https://ollama.com/download and run the installer."], url="https://ollama.com/download")


def _client(timeout: float = 20) -> httpx.Client:
    return httpx.Client(transport=TRANSPORT, timeout=timeout, headers=HEADERS, follow_redirects=True)


def resolve_release() -> dict:
    """What we would download: {version, url, size, sha256, source, verified, note}. Never raises; says what is missing instead."""
    try:
        with _client() as c:
            r = c.get(RELEASE_API)
            r.raise_for_status()
            rel = r.json()
            asset = next((a for a in rel.get("assets", []) if a.get("name") == ASSET_NAME), None)
            if not asset or not str(asset.get("browser_download_url", "")).startswith(ASSET_PREFIX):
                raise ValueError("the latest release has no Windows installer")
            version = str(rel.get("tag_name", "")).lstrip("v")
            digest = str(asset.get("digest") or "")
            sha = digest.removeprefix("sha256:").lower() if digest.startswith("sha256:") else ""
            source = "GitHub release digest"
            if not SHA_RE.match(sha):
                sha, source = "", ""
                try:
                    m = c.get(WINGET_MANIFEST.format(v=version))
                    found = re.search(r"InstallerSha256:\s*([0-9A-Fa-f]{64})", m.text) if m.status_code == 200 else None
                    if found:
                        sha, source = found.group(1).lower(), "winget manifest"
                except httpx.HTTPError:
                    pass
            return dict(version=version, url=asset["browser_download_url"], size=int(asset.get("size") or 0), sha256=sha, source=source,
                        verified=bool(sha), note="" if sha else "No published checksum was found for this download, so it cannot be verified.")
    except (httpx.HTTPError, ValueError, KeyError) as e:
        return dict(version="", url=OFFICIAL_DOWNLOAD, size=0, sha256="", source="", verified=False,
                    note=f"Could not read the release details ({type(e).__name__}); falling back to ollama.com's download link, which cannot be verified.")


class OllamaInstaller:
    """One background install at a time. status() is the single source for the UI."""

    def __init__(self, resolve: Callable[[], dict] | None = None, runner: Callable[[list[str]], int] | None = None,
                 probe: Callable[[], dict] | None = None, platform: str | None = None, home: Path | None = None,
                 sleep: Callable[[float], None] = time.sleep, opener: Callable[[str], None] | None = None):
        self._resolve = resolve or resolve_release
        self._runner = runner or _run_installer
        self._probe = probe or ollama_state
        self.platform = platform or sys.platform
        self.home = home
        self._sleep = sleep
        self._open = opener or _open_file
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._s: dict = self._fresh()

    @staticmethod
    def _fresh() -> dict:
        return dict(state="idle", step="", done=0, total=0, log=[], error="", version="", needs_manual=False)

    # ---- public
    def plan(self) -> dict:
        info = manual_instructions(self.platform)
        if self.platform != "win32":
            return dict(supported=False, manual=info, installed=bool(find_ollama()))
        rel = self._resolve()
        return dict(supported=True, installed=bool(find_ollama()), manual=info, release=rel,
                    size_gb=round(rel["size"] / 1e9, 2) if rel["size"] else None, needs_confirm=not rel["verified"],
                    steps=["Download the official installer", "Check its checksum", "Install for this user (no administrator needed)", "Start Ollama and check it answers"])

    def status(self) -> dict:
        with self._lock:
            return dict(self._s, log=list(self._s["log"][-30:]), running=self._running())

    def _running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, accept_unverified: bool = False) -> dict:
        if self.platform != "win32":
            raise SetupError("One-click install is only available on Windows. " + " ".join(manual_instructions(self.platform)["steps"]))
        with self._lock:
            if self._running():
                raise SetupError("an install is already running")
            self._s = self._fresh()
            self._s.update(state="downloading", step="Looking up the latest version")
            self._cancel.clear()
        rel = self._resolve()
        if not rel["verified"] and not accept_unverified:
            with self._lock:
                self._s = self._fresh()
            raise SetupError(rel["note"] + " Tick the box to install anyway, or download Ollama from https://ollama.com/download yourself.")
        self._thread = threading.Thread(target=self._work, args=(rel,), daemon=True, name="ollama-install")
        self._thread.start()
        return self.status()

    def cancel(self) -> dict:
        self._cancel.set()
        return self.status()

    def join(self, timeout: float = 30) -> None:
        if self._thread:
            self._thread.join(timeout)

    # ---- worker
    def _set(self, **kw) -> None:
        with self._lock:
            self._s.update(kw)

    def _log(self, line: str) -> None:
        with self._lock:
            self._s["log"].append(line)

    def _work(self, rel: dict) -> None:
        dest = (self.home or data_dir()) / "downloads"
        dest.mkdir(parents=True, exist_ok=True)
        part, final = dest / (ASSET_NAME + ".part"), dest / ASSET_NAME
        try:
            self._set(version=rel["version"], step="Downloading the installer", total=rel["size"])
            sha = self._download(rel["url"], part)
            if self._cancel.is_set():
                part.unlink(missing_ok=True)
                self._set(state="cancelled", step="Cancelled. Nothing was installed.")
                return
            self._set(state="verifying", step="Checking the download")
            if rel["verified"] and sha != rel["sha256"]:
                part.unlink(missing_ok=True)
                raise SetupError("The downloaded file does not match its published checksum, so it was deleted and NOT run. Try again, or download Ollama from ollama.com.")
            self._log("checksum OK (" + rel["source"] + ")" if rel["verified"] else "checksum not verified (you accepted this)")
            os.replace(part, final)
            self._set(state="installing", step="Installing Ollama for your user (about a minute; this step cannot be cancelled)")
            code = self._runner([str(final), *SILENT_ARGS])
            self._log(f"installer exited with code {code}")
            if code != 0:
                self._fallback(final, f"The silent install failed (code {code}).")
                return
            self._set(state="waiting", step="Waiting for Ollama to start")
            if self._wait_up(60):
                self._set(state="ready", step="Ollama is installed and running.")
            elif find_ollama():
                self._set(state="ready", step="Ollama is installed. It is not running yet: use 'Start Ollama'.")
            else:
                self._fallback(final, "The installer finished but Ollama was not found.")
        except SetupError as e:
            self._set(state="failed", error=str(e), step="")
        except (OSError, httpx.HTTPError) as e:
            part.unlink(missing_ok=True)
            self._set(state="failed", error=f"{type(e).__name__}: {e}", step="")

    def _download(self, url: str, path: Path) -> str:
        h = hashlib.sha256()
        with _client(timeout=60) as c, c.stream("GET", url) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            if total:
                self._set(total=total)
            done = 0
            with open(path, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    if self._cancel.is_set():
                        break
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    self._set(done=done)
        return h.hexdigest()

    def _wait_up(self, seconds: int) -> bool:
        for _ in range(seconds):
            if self._probe()["running"]:
                return True
            self._sleep(1)
        return False

    def _fallback(self, installer: Path, why: str) -> None:
        """Open the normal installer window so the user can finish it by hand."""
        try:
            self._open(str(installer))
            opened = " The normal installer window was opened: click Install, then come back here."
        except OSError:
            opened = f" Run this file yourself: {installer}"
        self._set(state="needs_manual", needs_manual=True, error=why + opened, step="")

    # ---- starting an installed-but-stopped server
    def start_server(self, popen: Callable | None = None) -> dict:
        if self._probe()["running"]:
            return dict(ok=True, message="Ollama is already running.")
        exe = find_ollama()
        if not exe:
            raise SetupError("Ollama is not installed yet.")
        popen = popen or subprocess.Popen
        app = Path(exe).with_name("ollama app.exe")
        cmd = [str(app)] if self.platform == "win32" and app.is_file() else [exe, "serve"]
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
        except OSError as e:
            raise SetupError(f"Could not start Ollama: {e}") from e
        if self._wait_up(20):
            return dict(ok=True, message="Ollama is running.")
        raise SetupError("Ollama was started but is not answering yet. Wait a few seconds and press Check again.")


def _run_installer(cmd: list[str]) -> int:
    try:
        return subprocess.run(cmd, timeout=900, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode
    except (OSError, subprocess.SubprocessError):
        return 127


def _open_file(path: str) -> None:
    os.startfile(path)              # type: ignore[attr-defined]  # Windows only; a visible window for the user
