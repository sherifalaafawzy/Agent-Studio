"""Easy setup, coding agents: the ACP registry, safe version choice, install methods, wiring, uninstall. No real installs or downloads."""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.acp_agents import AcpStore
from app.db import Database
from app.easy import agent_installer as AI
from app.easy.agent_installer import AGENTS, AgentInstaller, InstallError, Registry, pick_version, safe_extract

NOW = datetime(2026, 11, 22, tzinfo=timezone.utc)
TIMES = {"created": "2026-01-01T00:00:00Z", "modified": "2026-11-21T00:00:00Z", "1.0.0": "2026-10-01T00:00:00Z", "1.1.0": "2026-11-20T00:00:00Z",
         "0.9.0": "2026-09-01T00:00:00Z", "2.0.0-beta.1": "2026-09-15T00:00:00Z", "bad": "2026-01-01T00:00:00Z", "0.8.0": "not a date"}


def zip_bytes(files: dict[str, bytes]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return b.getvalue()


ZIP = zip_bytes({"opencode.exe": b"MZ-fake"})
ZIP_SHA = hashlib.sha256(ZIP).hexdigest()
REG = {"agents": [
    {"id": "gemini", "name": "Gemini CLI", "version": "0.70.0", "distribution": {"npx": {"package": "@google/gemini-cli@0.70.0", "args": ["--acp"]}}},
    {"id": "github-copilot-cli", "name": "GitHub Copilot", "version": "1.0.114", "distribution": {"npx": {"package": "@github/copilot@1.0.114", "args": ["--acp"]}}},
    {"id": "claude-acp", "name": "Claude Agent", "version": "0.1", "distribution": {}},
    {"id": "opencode", "name": "OpenCode", "version": "2.3.2", "website": "https://opencode.ai", "description": "Open coding agent",
     "distribution": {"binary": {"windows-x86_64": {"archive": "https://example.test/oc.zip", "cmd": "./opencode.exe", "args": ["acp"], "sha256": ZIP_SHA}}}},
    {"id": "nowin", "name": "No Windows", "version": "1", "distribution": {"binary": {"linux-x86_64": {"archive": "https://x.test/a", "cmd": "x", "sha256": "a" * 64}}}},
    {"id": "badpkg", "name": "Bad pkg", "version": "1", "distribution": {"npx": {"package": "BAD PKG@1"}}},
    {"id": "nosha", "name": "No checksum", "version": "1", "website": "https://nosha.test", "distribution": {"binary": {"windows-x86_64": {"archive": "https://x.test/a", "cmd": "x"}}}},
]}


class Box:
    """Everything the installer touches, faked: which programs exist, what npm says, and what a 'run' does."""

    def __init__(self, tmp_path, programs=("npm", "winget", "uv"), platform="win32", code=0, download=None):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp = tmp_path
        self.programs, self.code, self.runs = set(programs), code, []
        self.times = {"@anthropic-ai/claude-code": TIMES, "@openai/codex": TIMES, "@xai-official/grok": TIMES, "@google/gemini-cli": TIMES, "@github/copilot": TIMES}
        self.globals: dict = {}
        self.versions: dict = {}
        self.db = Database(tmp_path / "t.db")
        self.store = AcpStore(self.db)
        self.registry = Registry(fetch=lambda: REG, home=tmp_path)
        self.inst = AgentInstaller(self.store, self.registry, runner=self._run, which=lambda c: f"C:/bin/{c}.exe" if c in self.programs else None,
                                   capture=self._capture, version_of=lambda exe: self.versions.get(exe, "9.9.9"), home=tmp_path,
                                   download=download or self._download, platform=platform, now=lambda: NOW)

    def _capture(self, cmd):
        if cmd[:2] == ["npm", "view"]:
            t = self.times.get(cmd[2])
            return None if t is None else json.dumps(t)
        if cmd[:2] == ["npm", "ls"]:
            return json.dumps({"dependencies": self.globals})
        return None

    def _run(self, argv, env, on_line):
        self.runs.append(argv)
        on_line("working...")
        return self.code

    def _download(self, url, dest):
        Path(dest).write_bytes(ZIP)
        return hashlib.sha256(ZIP).hexdigest()

    def wait(self):
        self.inst.join()
        return self.inst.status()


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


# ------------------------------------------------------------------------------------------------ pure helpers
def test_pick_version_takes_the_newest_stable_release_that_is_old_enough():
    assert pick_version(TIMES, NOW) == ("1.0.0", "2026-10-01")
    assert pick_version({"1.0.0": "2026-11-21T00:00:00Z"}, NOW) is None
    assert pick_version({}, NOW) is None
    assert pick_version(TIMES, NOW, min_age_days=1)[0] == "1.1.0"
    assert pick_version({"1.0.0": "2020-01-01T00:00:00Z", "1.10.0": "2020-01-02T00:00:00Z", "1.9.0": "2020-01-03T00:00:00Z"})[0] == "1.10.0"     # real clock, semver order


def test_registry_prefers_live_then_cache_then_the_snapshot(tmp_path):
    t = {"now": 1000.0}
    reg = Registry(fetch=lambda: REG, home=tmp_path, clock=lambda: t["now"])
    g = reg.get()
    assert g["source"] == "live" and len(g["agents"]) == len(REG["agents"])
    t["now"] += 60
    assert reg.get()["source"] == "cache"
    t["now"] += AI.REGISTRY_TTL_S

    def down():
        raise httpx.ConnectError("offline")
    stale = Registry(fetch=down, home=tmp_path, clock=lambda: t["now"]).get()
    assert stale["source"] == "cache" and stale["fetched_at"] == 1000.0
    snap = Registry(fetch=down, home=tmp_path / "empty").get()
    assert snap["source"] == "snapshot" and snap["fetched_at"] is None and any(a["id"] == "gemini" for a in snap["agents"])
    bad = Registry(fetch=lambda: {"agents": "nope"}, home=tmp_path / "bad").get()
    assert bad["source"] == "snapshot"
    forced = Registry(fetch=lambda: REG, home=tmp_path, clock=lambda: 5.0).get(refresh=True)
    assert forced["source"] == "live"
    (tmp_path / "acp-registry.json").write_text("garbage", encoding="utf-8")
    assert Registry(fetch=lambda: REG, home=tmp_path, clock=lambda: 9.0).get()["source"] == "live"


def test_the_bundled_snapshot_is_valid():
    snap = json.loads(AI.SNAPSHOT.read_text(encoding="utf-8"))
    assert len(snap["agents"]) >= 10 and all(a["id"] and a["name"] and "distribution" in a for a in snap["agents"])


def test_default_registry_fetch_reads_the_cdn(monkeypatch):
    monkeypatch.setattr(AI, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, json={"agents": []}) if r.url.host == "cdn.agentclientprotocol.com" else httpx.Response(404)))
    assert AI._fetch_registry() == {"agents": []}


def test_safe_extract_unpacks_and_refuses_paths_that_escape(tmp_path):
    z = tmp_path / "ok.zip"
    z.write_bytes(zip_bytes({"a/b.txt": b"hi"}))
    safe_extract(z, tmp_path / "out1")
    assert (tmp_path / "out1" / "a" / "b.txt").read_text() == "hi"
    evil = tmp_path / "evil.zip"
    evil.write_bytes(zip_bytes({"../../escape.txt": b"x"}))
    with pytest.raises(InstallError, match="outside its folder"):
        safe_extract(evil, tmp_path / "out2")
    assert not (tmp_path / "escape.txt").exists()
    tgz = tmp_path / "ok.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        info = tarfile.TarInfo("dir/file.txt")
        data = b"tar"
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    safe_extract(tgz, tmp_path / "out3")
    assert (tmp_path / "out3" / "dir" / "file.txt").read_bytes() == b"tar"
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as t:
        info = tarfile.TarInfo("../../tar-escape.txt")
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(InstallError, match="outside its folder"):
        safe_extract(bad, tmp_path / "out4")


def test_capture_and_cli_version_run_real_commands(monkeypatch):
    assert AI._capture(["definitely-not-a-program-xyz"]) is None
    import sys
    monkeypatch.setattr(AI.shutil, "which", lambda c: sys.executable)
    assert AI._capture(["python", "-c", "print('hello')"]).strip() == "hello"
    assert AI._capture(["python", "-c", "import sys; sys.exit(2)"]) is None
    monkeypatch.setattr(AI.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 1)))
    assert AI._capture(["python"]) is None
    assert AI._cli_version("x") is None
    monkeypatch.undo()
    assert AI._cli_version(sys.executable).startswith("Python 3")
    assert AI._cli_version("definitely-not-a-program-xyz") is None
    import os
    assert AI._expand("~").is_dir() and "%" not in str(AI._expand("%TEMP%")) or os.name != "nt"


def test_real_download_hashes_what_it_saves(tmp_path, monkeypatch):
    monkeypatch.setattr(AI, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, content=ZIP)))
    assert AI._download("https://example.test/a.zip", tmp_path / "a.zip") == ZIP_SHA and (tmp_path / "a.zip").read_bytes() == ZIP


# ------------------------------------------------------------------------------------------------ detection and planning
def test_detect_by_path_known_folder_or_not_at_all(box, monkeypatch, tmp_path):
    box.programs = {"claude"}
    box.versions["C:/bin/claude.exe"] = "2.1.170 (Claude Code)"
    assert box.inst.detect("claude") == {"installed": True, "path": "C:/bin/claude.exe", "version": "2.1.170 (Claude Code)"}
    assert box.inst.detect("codex") == {"installed": False, "path": None, "version": None}
    known = tmp_path / "agy.exe"
    known.write_text("x")
    monkeypatch.setitem(AGENTS["antigravity"], "known", [str(known)])
    assert box.inst.detect("antigravity")["path"] == str(known)
    assert box.inst.detect("not-in-our-list") == {"installed": False, "path": None, "version": None}          # unknown ids look for a program with that name


def test_npm_listing_detects_global_packages(box):
    box.globals = {"@openai/codex": {"version": "0.167.0"}}
    assert box.inst._npm_has("@openai/codex") and not box.inst._npm_has("@google/gemini-cli")
    box.inst._capture = lambda c: "not json"
    assert box.inst._npm_has("x") is False
    box.inst._capture = lambda c: None
    assert box.inst._npm_has("x") is False


def kinds(methods):
    return [m["kind"] for m in methods]


def test_methods_are_ordered_safest_first_and_show_the_exact_command(box):
    claude = box.inst.methods("claude")
    assert kinds(claude) == ["npm", "winget", "script"]
    assert claude[0]["command"] == "npm install -g @anthropic-ai/claude-code@1.0.0" and "public since 2026-10-01" in claude[0]["label"]       # never the 2-day-old 1.1.0
    assert claude[2]["safe"] is False and "downloads a script" in claude[2]["warning"] and claude[2]["command"] == AGENTS["claude"]["script"]
    assert "cannot be pinned" in claude[1]["note"]
    assert kinds(box.inst.methods("codex")) == ["npm", "winget"]
    assert kinds(box.inst.methods("gemini")) == ["npm", "npx"] and box.inst.methods("gemini")[1]["command"] == "npx -y @google/gemini-cli@1.0.0 --acp"
    assert kinds(box.inst.methods("aider")) == ["uv"] and kinds(box.inst.methods("cursor")) == ["script"]
    oh = box.inst.methods("openhands")
    assert kinds(oh) == ["manual"] and "WSL" in oh[0]["text"]


def test_missing_tools_and_other_platforms_change_the_options(tmp_path):
    no_node = Box(tmp_path / "a", programs=("winget",))
    assert kinds(no_node.inst.methods("claude")) == ["winget", "script"]
    nothing = Box(tmp_path / "b", programs=())
    m = nothing.inst.methods("codex")
    assert kinds(m) == ["manual"] and "npm (install Node.js first)" in m[0]["text"]
    assert "supported installer" in nothing.inst.methods("grok")[0]["text"] or "npm" in nothing.inst.methods("grok")[0]["text"]
    mac = Box(tmp_path / "c", platform="darwin")
    assert kinds(mac.inst.methods("claude")) == ["npm"]                                   # no winget, no PowerShell script on a Mac
    assert kinds(mac.inst.methods("opencode")) == ["manual"]                              # the Windows archive is not offered elsewhere
    unpinnable = Box(tmp_path / "d")
    unpinnable.times = {}
    assert kinds(unpinnable.inst.methods("claude")) == ["winget", "script"] and kinds(unpinnable.inst.methods("gemini")) == ["manual"]
    badjson = Box(tmp_path / "e")
    badjson.inst._capture = lambda c: "not json"
    assert kinds(badjson.inst.methods("claude")) == ["winget", "script"]


def test_registry_only_agents_get_npx_or_a_verified_archive(box):
    assert kinds(box.inst.methods("opencode")) == ["archive"] and box.inst.methods("opencode")[0]["sha256"] == ZIP_SHA
    assert kinds(box.inst.methods("nowin")) == ["manual"] and kinds(box.inst.methods("nosha")) == ["manual"] and "nosha.test" in box.inst.methods("nosha")[0]["text"]
    assert kinds(box.inst.methods("badpkg")) == ["manual"]                                 # a package name that is not a plain npm name is refused
    with pytest.raises(InstallError, match="unknown agent"):
        box.inst.methods("no-such-agent")
    registry_npx = Box(box.tmp / "r")
    registry_npx.registry = Registry(fetch=lambda: {"agents": [{"id": "extra", "name": "Extra", "version": "1", "distribution": {"npx": {"package": "@google/gemini-cli@1"}}}]}, home=box.tmp / "r")
    registry_npx.inst.registry = registry_npx.registry
    m = registry_npx.inst.methods("extra")
    assert kinds(m) == ["npx"] and m[0]["pkg"] == "@google/gemini-cli" and m[0]["args"] == []


def test_catalog_lists_ours_then_the_rest_of_the_registry(box):
    box.programs = {"npm", "claude"}
    cat = box.inst.catalog()
    ids = [a["id"] for a in cat["agents"]]
    assert ids[:len(AGENTS)] == list(AGENTS) and ids[len(AGENTS):] == ["opencode", "nowin", "badpkg", "nosha"]               # gemini/copilot/claude-acp are 'ours'
    assert cat["registry"]["source"] == "live"
    claude, opencode = cat["agents"][0], cat["agents"][len(AGENTS)]
    assert claude["installed"] and claude["signin"] and claude["cost"] and not claude["registry_extra"]
    assert opencode["registry_extra"] and opencode["methods"][0]["kind"] == "archive" and opencode["note"] == "Open coding agent"
    offline = Box(box.tmp / "o", programs=())
    assert offline.inst.catalog()["agents"]                                              # works without npm at all (no version lookups)


def test_prefetch_looks_each_package_up_once(tmp_path):
    b = Box(tmp_path)
    seen = []
    orig = b.inst._capture
    b.inst._capture = lambda c: (seen.append(c[2]) if c[:2] == ["npm", "view"] else None) or orig(c)
    b.inst.catalog()
    first = len(seen)
    assert first >= 4 and len(set(seen)) == first
    b.inst.catalog()
    assert len(seen) == first                                                              # the hour-long cache answers the second time


# ------------------------------------------------------------------------------------------------ running installs
def test_start_checks_the_method_and_a_confirmation_for_scripts(box):
    with pytest.raises(InstallError, match="not available"):
        box.inst.start("codex", "script")
    with pytest.raises(InstallError, match="not available"):
        box.inst.start("openhands", "manual")
    with pytest.raises(InstallError, match="confirm the exact command"):
        box.inst.start("claude", "script")
    with pytest.raises(InstallError, match="confirm the exact command"):
        box.inst.start("claude", "script", confirm_command="something else")
    assert box.runs == []


def test_npm_install_runs_the_pinned_command_and_switches_the_agent_on(box):
    box.store.update("codex", dict(enabled=False))
    box.inst.start("codex", "npm")
    st = box.wait()
    assert st["state"] == "done" and st["agent"] == "codex" and st["action"] == "install"
    assert box.runs == [["npm", "install", "-g", "@openai/codex@1.0.0"]] and st["log"][0] == "$ npm install -g @openai/codex@1.0.0" and "working..." in st["log"]
    assert box.store.get("codex")["enabled"] is True


def test_a_failing_install_says_so_and_keeps_the_output(tmp_path):
    b = Box(tmp_path, code=1)
    b.inst.start("codex", "winget")
    st = b.wait()
    assert st["state"] == "failed" and "code 1" in st["error"] and "working..." in st["log"]


def test_only_one_job_at_a_time(box):
    box.programs |= {"codex"}
    box.globals = {"@openai/codex": {}}
    gate = threading.Event()
    box.inst._thread = threading.Thread(target=gate.wait)
    box.inst._thread.start()
    try:
        assert box.inst.status()["running"]
        with pytest.raises(InstallError, match="already running"):
            box.inst.start("codex", "npm")
        with pytest.raises(InstallError, match="already running"):
            box.inst.uninstall("codex")
    finally:
        gate.set()
        box.inst._thread.join()


def test_the_vendor_script_only_runs_after_the_exact_command_is_confirmed(box, monkeypatch):
    monkeypatch.setattr(AI.shutil, "which", lambda c: "C:/Windows/powershell.exe")
    cmd = AGENTS["kiro"]["script"]
    box.inst.start("kiro", "script", confirm_command=cmd)
    st = box.wait()
    assert st["state"] == "done" and box.runs[0][:5] == ["C:/Windows/powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"] and box.runs[0][5] == cmd


def test_script_failure_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(AI.shutil, "which", lambda c: None)                        # no PowerShell found: fall back to the bare name
    b = Box(tmp_path, code=7)
    b.inst.start("kiro", "script", confirm_command=AGENTS["kiro"]["script"])
    st = b.wait()
    assert st["state"] == "failed" and "code 7" in st["error"] and b.runs[0][0] == "powershell"


def test_npx_and_global_cli_extras_are_added_as_external_agents(box):
    box.inst.start("gemini", "npx")
    assert box.wait()["state"] == "done" and box.runs == []                      # npx needs no install: only the launch command is recorded
    row = box.store.get("gemini")
    assert row["command"] == "npx" and row["args"] == ["-y", "@google/gemini-cli@1.0.0", "--acp"] and not row["builtin"] and row["enabled"]
    box.store.update("gemini", dict(enabled=False, command="old"))
    box.inst.start("gemini", "npm")                                              # now through a global npm install: the CLI is on PATH
    assert box.wait()["state"] == "done"
    row = box.store.get("gemini")
    assert row["command"] == "gemini" and row["args"] == ["--acp"] and row["enabled"]
    box.inst.start("copilot", "npm")
    box.wait()
    assert box.store.get("copilot")["command"] == "copilot"


def test_registry_archive_install_checks_the_checksum_before_unpacking(box, tmp_path):
    box.inst.start("opencode", "archive")
    st = box.wait()
    assert st["state"] == "done" and "checksum OK" in st["log"]
    exe = tmp_path / "agents" / "opencode" / "opencode.exe"
    assert exe.read_bytes() == b"MZ-fake"
    row = box.store.get("opencode")
    assert row["command"] == str(tmp_path / "agents" / "opencode" / "./opencode.exe") and row["args"] == ["acp"]
    box.inst.start("opencode", "archive")                                         # reinstalling replaces the old folder
    assert box.wait()["state"] == "done"


def test_a_wrong_checksum_deletes_the_download_and_unpacks_nothing(tmp_path):
    b = Box(tmp_path, download=lambda url, dest: (Path(dest).write_bytes(b"tampered"), "0" * 64)[1])
    b.inst.start("opencode", "archive")
    st = b.wait()
    assert st["state"] == "failed" and "NOT unpacked" in st["error"] and not (tmp_path / "agents" / "opencode").exists() and not (tmp_path / "agents" / "opencode.download").exists()


def test_a_download_error_is_reported(tmp_path):
    def boom(url, dest):
        raise httpx.ConnectError("offline")
    b = Box(tmp_path, download=boom)
    b.inst.start("opencode", "archive")
    assert b.wait()["state"] == "failed"


def test_odd_agent_ids_become_valid_external_agent_ids(box):
    box.inst._wire("9-lives", dict(kind="npx", pkg="pkg", version="1.0.0", args=[]))
    assert box.store.get("a9-lives")["command"] == "npx"
    box.inst._wire("Fancy Agent!", dict(kind="archive", cmd="./x.exe", args=[]))
    assert box.store.get("fancy-agent-")["label"] == "Fancy Agent!" and box.store.get("fancy-agent-")["notes"] == "Added by one-click install."


# ------------------------------------------------------------------------------------------------ uninstall
def test_uninstall_only_removes_what_it_can_remove_safely(box):
    with pytest.raises(InstallError, match="not installed"):
        box.inst.uninstall("codex")
    box.programs |= {"codex"}
    box.globals = {"@openai/codex": {}}
    box.inst.uninstall("codex")
    st = box.wait()
    assert st["state"] == "done" and st["action"] == "uninstall" and box.runs[-1] == ["npm", "uninstall", "-g", "@openai/codex"]
    box.programs |= {"aider"}
    box.inst.uninstall("aider")
    assert box.wait()["state"] == "done" and box.runs[-1] == ["uv", "tool", "uninstall", "aider-chat"]
    box.programs |= {"cursor-agent"}
    with pytest.raises(InstallError, match="Installed apps"):
        box.inst.uninstall("cursor")
    box.code = 3
    box.globals = {"@openai/codex": {}}
    box.inst.uninstall("codex")
    st = box.wait()
    assert st["state"] == "failed" and "uninstall command failed" in st["error"]


def test_default_wiring_uses_real_helpers(tmp_path):
    inst = AgentInstaller(AcpStore(Database(tmp_path / "x.db")))
    assert inst._run is AI.run_streaming and isinstance(inst.registry, Registry) and inst.platform
    inst.join()                                                                    # nothing started: a no-op
