"""Easy setup, Ollama side: system check, one-click install, local API, model library. No real installers, models or websites."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from app.easy import ollama_library as L
from app.easy import ollama_local as OL
from app.easy import ollama_setup as OS
from app.easy import syscheck as SC

FIX = Path(__file__).parent / "fixtures"


def mock(handler):
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Every module-level transport starts as 'nothing reachable'; tests install their own."""
    def down(request):
        raise httpx.ConnectError("offline", request=request)
    for mod in (SC, OS, OL, L):
        monkeypatch.setattr(mod, "TRANSPORT", mock(down))
    monkeypatch.setattr(OL, "ATRANSPORT", httpx.MockTransport(down))


# ------------------------------------------------------------------------------------------------ system check
def test_run_line_reports_first_line_or_none(monkeypatch):
    monkeypatch.setattr(SC.shutil, "which", lambda c: None)
    assert SC.run_line(["nothing"]) is None
    monkeypatch.setattr(SC.shutil, "which", lambda c: "/bin/x")
    ok = subprocess.CompletedProcess([], 0, stdout="v1.2.3\nmore\n", stderr="")
    monkeypatch.setattr(SC.subprocess, "run", lambda *a, **k: ok)
    assert SC.run_line(["x", "--version"]) == "v1.2.3"
    monkeypatch.setattr(SC.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 1, stdout="", stderr="bad"))
    assert SC.run_line(["x"]) is None
    monkeypatch.setattr(SC.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 1)))
    assert SC.run_line(["x"]) is None


def test_ram_reads_this_machine_and_other_platforms(monkeypatch):
    assert SC.ram_gb() and SC.ram_gb() > 0                           # the real value on whatever runs the tests
    monkeypatch.setattr(SC.os, "name", "posix")
    monkeypatch.setattr(SC.os, "sysconf", lambda k: 4096 if k == "SC_PAGE_SIZE" else 2_000_000, raising=False)
    assert SC.ram_gb() == round(4096 * 2_000_000 / 1e9, 1)

    def broken(k):
        raise ValueError(k)
    monkeypatch.setattr(SC.os, "sysconf", broken, raising=False)
    assert SC.ram_gb() is None


def test_ollama_state_and_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(SC, "TRANSPORT", mock(lambda r: httpx.Response(200, json={"version": "0.40.16"})))
    assert SC.ollama_state() == {"running": True, "version": "0.40.16"}
    monkeypatch.setattr(SC, "TRANSPORT", mock(lambda r: httpx.Response(503)))
    assert SC.ollama_state()["running"] is False
    monkeypatch.setattr(SC, "TRANSPORT", mock(lambda r: httpx.Response(200, content=b"not json")))
    assert SC.ollama_state()["running"] is False
    assert SC.ollama_state("http://127.0.0.1:9")["running"] is False        # the default 'offline' transport
    assert SC.free_gb(tmp_path / "a" / "b" / "c") > 0                       # walks up to a folder that exists
    monkeypatch.setattr(SC.shutil, "disk_usage", lambda p: (_ for _ in ()).throw(OSError("x")))
    assert SC.free_gb(tmp_path) is None


def test_check_lists_every_probe_with_a_plain_reason(tmp_path):
    base = dict(line=lambda c: None, ram=lambda: 4.0, ollama=lambda: {"running": False, "version": None}, gpu=lambda: {"nvidia": False}, ollama_path=lambda: None)
    r = SC.check(tmp_path, tmp_path, **base)
    by = {i["id"]: i for i in r["items"]}
    assert [i["id"] for i in r["items"]] == ["python", "docker", "ollama", "node", "disk", "ram", "gpu"]
    assert by["docker"]["state"] == "missing" and by["ollama"]["state"] == "missing" and by["node"]["state"] == "missing"
    assert by["ram"]["state"] == "warn" and by["gpu"]["state"] == "missing" and r["vram_gb"] is None and r["ollama"]["installed"] is False
    assert all(i["why"] for i in r["items"])
    good = dict(base, line=lambda c: "v1" if c[0] != "docker" else "29.8", ram=lambda: 32.0, ollama=lambda: {"running": True, "version": "0.4"},
                gpu=lambda: {"nvidia": True, "gpu": "RTX", "vram_mb": 16384}, ollama_path=lambda: "C:/ollama.exe")
    r = SC.check(tmp_path, tmp_path, **good)
    by = {i["id"]: i for i in r["items"]}
    assert by["docker"]["state"] == "ok" and by["ollama"]["state"] == "ok" and by["node"]["state"] == "ok" and by["gpu"]["detail"] == "RTX (16 GB)"
    assert r["vram_gb"] == 16.0
    stopped = SC.check(tmp_path, tmp_path, **dict(good, ollama=lambda: {"running": False, "version": None}))
    assert {i["id"]: i for i in stopped["items"]}["ollama"]["detail"] == "installed but not running"
    unknown = SC.check(tmp_path, tmp_path, **dict(good, ram=lambda: None))
    assert {i["id"]: i for i in unknown["items"]}["ram"]["state"] == "missing"
    low = SC.check(tmp_path, tmp_path, **good)                                # the real disk: only the shape matters here
    assert {i["id"]: i for i in low["items"]}["disk"]["state"] in ("ok", "warn")


def test_check_defaults_use_the_real_probes(tmp_path):
    r = SC.check(tmp_path, tmp_path)                                         # real gpu probe + real PATH search: must simply not crash
    assert len(r["items"]) == 7


def test_disk_warning_and_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(SC, "free_gb", lambda p: 3.0)
    r = SC.check(tmp_path, tmp_path, line=lambda c: None, ram=lambda: 16, ollama=lambda: {"running": False, "version": None}, gpu=lambda: {}, ollama_path=lambda: None)
    assert {i["id"]: i for i in r["items"]}["disk"]["state"] == "warn"
    monkeypatch.setattr(SC, "free_gb", lambda p: None)
    r = SC.check(tmp_path, tmp_path, line=lambda c: None, ram=lambda: 16, ollama=lambda: {"running": False, "version": None}, gpu=lambda: {}, ollama_path=lambda: None)
    assert {i["id"]: i for i in r["items"]}["disk"]["detail"] == "unknown"


# ------------------------------------------------------------------------------------------------ installer: lookup
def test_find_ollama_and_default_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert any("Programs" in str(p) for p in OS.default_paths())
    monkeypatch.delenv("LOCALAPPDATA")
    assert not any("Programs" in str(p) for p in OS.default_paths())
    monkeypatch.setattr(OS.shutil, "which", lambda c: "C:/bin/ollama.exe")
    assert OS.find_ollama() == "C:/bin/ollama.exe"
    monkeypatch.setattr(OS.shutil, "which", lambda c: None)
    exe = tmp_path / "ollama.exe"
    monkeypatch.setattr(OS, "default_paths", lambda: [tmp_path / "missing.exe", exe])
    assert OS.find_ollama() is None
    exe.write_text("x")
    assert OS.find_ollama() == str(exe)


def test_manual_instructions_never_run_scripts():
    mac, linux, other = OS.manual_instructions("darwin"), OS.manual_instructions("linux"), OS.manual_instructions("freebsd")
    assert "brew install --cask ollama" in " ".join(mac["steps"]) and "does not run scripts" in " ".join(linux["steps"]) and other["url"] == "https://ollama.com/download"


def release_json(digest="sha256:" + "a" * 64, url=None, name="OllamaSetup.exe"):
    return {"tag_name": "v0.40.17", "assets": [{"name": name, "size": 1234, "digest": digest,
                                                "browser_download_url": url or "https://github.com/ollama/ollama/releases/download/v0.40.17/OllamaSetup.exe"}]}


def test_resolve_release_prefers_githubs_digest_then_winget_then_says_unverified(monkeypatch):
    def routes(rel, manifest):
        def h(req):
            if req.url.host == "api.github.com":
                return httpx.Response(200, json=rel)
            if manifest == "boom":
                raise httpx.ConnectError("x", request=req)
            return httpx.Response(manifest[0], text=manifest[1])
        return mock(h)
    monkeypatch.setattr(OS, "TRANSPORT", routes(release_json(), (404, "")))
    r = OS.resolve_release()
    assert r["verified"] and r["source"] == "GitHub release digest" and r["sha256"] == "a" * 64 and r["version"] == "0.40.17" and r["size"] == 1234
    monkeypatch.setattr(OS, "TRANSPORT", routes(release_json(digest=""), (200, "InstallerSha256: " + "B" * 64)))
    r = OS.resolve_release()
    assert r["verified"] and r["source"] == "winget manifest" and r["sha256"] == "b" * 64
    monkeypatch.setattr(OS, "TRANSPORT", routes(release_json(digest="md5:zz"), (404, "")))
    r = OS.resolve_release()
    assert not r["verified"] and "cannot be verified" in r["note"]
    monkeypatch.setattr(OS, "TRANSPORT", routes(release_json(digest=""), "boom"))
    assert not OS.resolve_release()["verified"]


def test_resolve_release_falls_back_to_the_official_link(monkeypatch):
    assert OS.resolve_release()["url"] == OS.OFFICIAL_DOWNLOAD                                    # GitHub unreachable
    for rel in (release_json(name="Other.exe"), release_json(url="https://evil.example/OllamaSetup.exe")):
        monkeypatch.setattr(OS, "TRANSPORT", mock(lambda req, rel=rel: httpx.Response(200, json=rel)))
        r = OS.resolve_release()
        assert r["url"] == OS.OFFICIAL_DOWNLOAD and not r["verified"] and "falling back" in r["note"]


# ------------------------------------------------------------------------------------------------ installer: flow
PAYLOAD = b"MZ" + b"\x00" * 5000


def verified(payload=PAYLOAD, sha=None):
    return dict(version="0.40.17", url="https://github.com/ollama/ollama/releases/download/v0.40.17/OllamaSetup.exe", size=len(payload),
                sha256=sha or hashlib.sha256(payload).hexdigest(), source="GitHub release digest", verified=True, note="")


def installer(tmp_path, *, rel=None, code=0, probe=None, opener=None, payload=PAYLOAD, platform="win32", cancel_midway=False, found=True, monkeypatch=None):
    calls = {"runs": [], "opened": []}
    inst = OS.OllamaInstaller(resolve=lambda: rel or verified(payload), runner=lambda cmd: calls["runs"].append(cmd) or code,
                              probe=probe or (lambda: {"running": True, "version": "0.40.17"}), platform=platform, home=tmp_path,
                              sleep=lambda s: None, opener=opener or (lambda p: calls["opened"].append(p)))

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield payload[:2000]
            if cancel_midway:
                inst.cancel()
            yield payload[2000:]
    OS.TRANSPORT = mock(lambda req: httpx.Response(200, headers={"content-length": str(len(payload))}, stream=Stream()))
    if monkeypatch:
        monkeypatch.setattr(OS, "find_ollama", lambda: "C:/ollama.exe" if found else None)
    return inst, calls


def test_install_downloads_verifies_runs_silently_and_confirms_it_is_up(tmp_path, monkeypatch):
    inst, calls = installer(tmp_path, monkeypatch=monkeypatch)
    inst.start()
    inst.join()
    st = inst.status()
    assert st["state"] == "ready" and st["done"] == len(PAYLOAD) and st["version"] == "0.40.17" and not st["running"]
    assert calls["runs"] == [[str(tmp_path / "downloads" / "OllamaSetup.exe"), *OS.SILENT_ARGS]] and (tmp_path / "downloads" / "OllamaSetup.exe").is_file()
    assert any("checksum OK" in l for l in st["log"])


def test_install_refuses_a_download_that_does_not_match_its_checksum(tmp_path, monkeypatch):
    inst, calls = installer(tmp_path, rel=verified(sha="0" * 64), monkeypatch=monkeypatch)
    inst.start()
    inst.join()
    st = inst.status()
    assert st["state"] == "failed" and "NOT run" in st["error"] and calls["runs"] == [] and not (tmp_path / "downloads" / "OllamaSetup.exe.part").exists()


def test_unverified_downloads_need_an_explicit_yes(tmp_path, monkeypatch):
    rel = dict(verified(), sha256="", verified=False, source="", note="No published checksum was found for this download, so it cannot be verified.")
    inst, calls = installer(tmp_path, rel=rel, monkeypatch=monkeypatch)
    with pytest.raises(OS.SetupError, match="cannot be verified"):
        inst.start()
    assert inst.status()["state"] == "idle"
    inst.start(accept_unverified=True)
    inst.join()
    assert inst.status()["state"] == "ready" and any("not verified" in l for l in inst.status()["log"])


def test_cancel_stops_the_download_and_installs_nothing(tmp_path, monkeypatch):
    inst, calls = installer(tmp_path, cancel_midway=True, monkeypatch=monkeypatch)
    inst.start()
    inst.join()
    assert inst.status()["state"] == "cancelled" and calls["runs"] == [] and not list((tmp_path / "downloads").iterdir())


def test_a_failed_silent_install_opens_the_normal_installer(tmp_path, monkeypatch):
    inst, calls = installer(tmp_path, code=5, monkeypatch=monkeypatch)
    inst.start()
    inst.join()
    st = inst.status()
    assert st["state"] == "needs_manual" and st["needs_manual"] and "code 5" in st["error"] and "normal installer window" in st["error"] and len(calls["opened"]) == 1

    def cannot_open(path):
        raise OSError("no shell")
    inst, _ = installer(tmp_path, code=5, opener=cannot_open, monkeypatch=monkeypatch)
    inst.start()
    inst.join()
    assert "Run this file yourself" in inst.status()["error"]


def test_after_install_ollama_may_not_be_running_yet(tmp_path, monkeypatch):
    inst, _ = installer(tmp_path, probe=lambda: {"running": False, "version": None}, monkeypatch=monkeypatch)
    inst.start()
    inst.join()
    assert inst.status()["state"] == "ready" and "not running yet" in inst.status()["step"]
    inst, calls = installer(tmp_path, probe=lambda: {"running": False, "version": None}, found=False, monkeypatch=monkeypatch)
    inst.start()
    inst.join()
    assert inst.status()["state"] == "needs_manual" and "not found" in inst.status()["error"] and calls["opened"]


def test_wait_polls_until_the_server_answers(tmp_path, monkeypatch):
    seen = iter([False, False, True])
    inst, _ = installer(tmp_path, probe=lambda: {"running": next(seen), "version": "1"}, monkeypatch=monkeypatch)
    inst.start()
    inst.join()
    assert inst.status()["state"] == "ready" and "installed and running" in inst.status()["step"]


def test_download_errors_are_reported(tmp_path, monkeypatch):
    inst, _ = installer(tmp_path, monkeypatch=monkeypatch)
    OS.TRANSPORT = mock(lambda req: httpx.Response(404))
    inst.start()
    inst.join()
    st = inst.status()
    assert st["state"] == "failed" and "404" in st["error"]


def test_only_windows_installs_and_only_one_at_a_time(tmp_path, monkeypatch):
    inst, _ = installer(tmp_path, platform="darwin", monkeypatch=monkeypatch)
    with pytest.raises(OS.SetupError, match="only available on Windows"):
        inst.start()
    plan = inst.plan()
    assert plan["supported"] is False and "brew" in " ".join(plan["manual"]["steps"])
    inst, _ = installer(tmp_path, monkeypatch=monkeypatch)
    plan = inst.plan()
    assert plan["supported"] and plan["size_gb"] == round(len(PAYLOAD) / 1e9, 2) and plan["needs_confirm"] is False and len(plan["steps"]) == 4
    assert inst.plan()["release"]["verified"]
    slow = OS.OllamaInstaller(resolve=lambda: verified(), runner=lambda c: 0, platform="win32", home=tmp_path)
    import threading
    gate = threading.Event()
    slow._thread = threading.Thread(target=gate.wait)
    slow._thread.start()
    try:
        with pytest.raises(OS.SetupError, match="already running"):
            slow.start()
    finally:
        gate.set()
        slow._thread.join()
    nosize = dict(verified(), size=0)
    assert OS.OllamaInstaller(resolve=lambda: nosize, platform="win32").plan()["size_gb"] is None
    slow.join(0.1)
    OS.OllamaInstaller(platform="win32").join()                                        # nothing started: join is a no-op


def test_start_the_server(tmp_path, monkeypatch):
    started = []

    def popen(cmd, **kw):
        started.append(cmd)
    up = {"running": True, "version": "1"}
    down = {"running": False, "version": None}
    inst = OS.OllamaInstaller(probe=lambda: up, platform="win32", sleep=lambda s: None)
    assert inst.start_server(popen)["message"] == "Ollama is already running."
    monkeypatch.setattr(OS, "find_ollama", lambda: None)
    inst = OS.OllamaInstaller(probe=lambda: down, platform="win32", sleep=lambda s: None)
    with pytest.raises(OS.SetupError, match="not installed"):
        inst.start_server(popen)
    exe = tmp_path / "ollama.exe"
    exe.write_text("x")
    monkeypatch.setattr(OS, "find_ollama", lambda: str(exe))
    state = iter([down, down, up])
    inst = OS.OllamaInstaller(probe=lambda: next(state), platform="win32", sleep=lambda s: None)
    assert inst.start_server(popen)["ok"] and started[-1] == [str(exe), "serve"]                 # no tray app next to it: `ollama serve`
    (tmp_path / "ollama app.exe").write_text("x")
    state = iter([down, down, up])
    inst = OS.OllamaInstaller(probe=lambda: next(state), platform="win32", sleep=lambda s: None)
    inst.start_server(popen)
    assert started[-1] == [str(tmp_path / "ollama app.exe")]

    def refuse(cmd, **kw):
        raise OSError("blocked")
    inst = OS.OllamaInstaller(probe=lambda: down, platform="win32", sleep=lambda s: None)
    with pytest.raises(OS.SetupError, match="Could not start"):
        inst.start_server(refuse)
    with pytest.raises(OS.SetupError, match="not answering"):
        inst.start_server(popen)


def test_real_runner_and_opener(monkeypatch):
    assert OS._run_installer([sys.executable, "-c", "import sys; sys.exit(3)"]) == 3
    assert OS._run_installer(["definitely-not-a-program-xyz"]) == 127
    opened = []
    monkeypatch.setattr(os, "startfile", opened.append, raising=False)
    OS._open_file("C:/x.exe")
    assert opened == ["C:/x.exe"]


def test_default_installer_wiring_uses_the_real_helpers():
    inst = OS.OllamaInstaller()
    assert inst.platform == sys.platform and inst._resolve is OS.resolve_release and inst._runner is OS._run_installer


# ------------------------------------------------------------------------------------------------ local API
def local(monkeypatch, handler):
    monkeypatch.setattr(OL, "TRANSPORT", mock(handler))


def test_local_api_reads_installed_loaded_and_details(monkeypatch):
    def h(req):
        p = req.url.path
        if p == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b", "size": 5225388164, "modified_at": "t", "details": {"family": "qwen3", "parameter_size": "8.2B", "quantization_level": "Q4_K_M"}, "capabilities": ["tools"]},
                                                         {"name": "bare", "details": None}]})
        if p == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b", "size": 100, "size_vram": 60, "expires_at": "x"}]})
        if p == "/api/show":
            return httpx.Response(200, json={"capabilities": ["tools"], "details": {"family": "qwen3"}, "model_info": {"qwen3.context_length": 40960, "general.parameter_count": 8190735360}})
        if p == "/api/delete":
            return httpx.Response(200)
        return httpx.Response(500)
    local(monkeypatch, h)
    got = OL.installed()
    assert got[0]["name"] == "qwen3:8b" and got[0]["parameters"] == "8.2B" and got[1]["size"] == 0 and got[1]["family"] is None
    assert OL.loaded() == [{"name": "qwen3:8b", "size": 100, "vram": 60, "expires": "x"}]
    d = OL.show("qwen3:8b")
    assert d["context_length"] == 40960 and d["parameter_count"] == 8190735360 and d["capabilities"] == ["tools"]
    OL.delete("qwen3:8b")
    for bad in ("bad name; rm", ""):
        with pytest.raises(OL.LocalError, match="invalid model name"):
            OL.show(bad)
        with pytest.raises(OL.LocalError, match="invalid model name"):
            OL.delete(bad)


def test_local_api_errors_are_plain(monkeypatch):
    with pytest.raises(OL.LocalError, match="isn't reachable"):
        OL.installed()
    local(monkeypatch, lambda r: httpx.Response(404, json={"error": "model 'x' not found"}))
    with pytest.raises(OL.LocalError, match="model 'x' not found"):
        OL.show("x")
    local(monkeypatch, lambda r: httpx.Response(500, text="plain failure"))
    with pytest.raises(OL.LocalError, match="plain failure"):
        OL.installed()
    local(monkeypatch, lambda r: httpx.Response(200, content=b""))
    assert OL.loaded() == []
    local(monkeypatch, lambda r: httpx.Response(200, content=b"<html>"))
    assert OL.loaded() == []


def test_progress_folds_layers_into_one_percentage():
    p = OL.Progress()
    assert p.add({"status": "pulling manifest"})["percent"] is None
    assert p.add({"status": "pulling a", "digest": "a", "total": 100, "completed": 50})["percent"] == 50.0
    e = p.add({"status": "pulling b", "digest": "b", "total": 100, "completed": 0})
    assert e["completed"] == 50 and e["total"] == 200 and e["percent"] == 25.0
    assert p.add({"digest": "b", "total": 100, "completed": 100})["percent"] == 75.0 and p.status == "pulling b"
    assert p.add({"digest": "a", "total": 100, "completed": 500})["completed"] == 200          # never more than the layer's total


def collect(gen):
    async def go():
        return [e async for e in gen]
    return asyncio.run(go())


def test_pull_streams_progress_then_done(monkeypatch):
    lines = [{"status": "pulling manifest"}, {"status": "pulling a", "digest": "a", "total": 10, "completed": 5}, {"status": "success"}]
    body = ("\n".join(json.dumps(x) for x in lines) + "\n\n").encode()
    monkeypatch.setattr(OL, "ATRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, content=body)))
    evs = collect(OL.pull("qwen3:8b"))
    assert [e["type"] for e in evs] == ["progress", "progress", "progress", "done"] and evs[1]["percent"] == 50.0 and evs[-1]["model"] == "qwen3:8b"


def test_pull_reports_every_kind_of_failure(monkeypatch):
    assert collect(OL.pull("bad name"))[0]["type"] == "error"
    assert "isn't reachable" in collect(OL.pull("x"))[0]["message"]
    monkeypatch.setattr(OL, "ATRANSPORT", httpx.MockTransport(lambda r: httpx.Response(500, text="server broke")))
    assert "Ollama 500: server broke" in collect(OL.pull("x"))[0]["message"]
    body = (json.dumps({"status": "x"}) + "\n" + json.dumps({"error": "pull model manifest: file does not exist"}) + "\n").encode()
    monkeypatch.setattr(OL, "ATRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, content=body)))
    evs = collect(OL.pull("nope"))
    assert evs[-1] == {"type": "error", "message": "pull model manifest: file does not exist"}


# ------------------------------------------------------------------------------------------------ library
SEARCH = (FIX / "ollama_search.html").read_text(encoding="utf-8")
TAGS = (FIX / "ollama_tags.html").read_text(encoding="utf-8")


def test_fit_estimates():
    assert L.fit(None, 16, 8)["level"] == "unknown"
    assert L.fit(2, 16, 8)["level"] == "great"
    assert L.fit(2, None, None)["level"] == "unknown"
    assert L.fit(5, 32, None)["level"] == "ok"
    assert L.fit(5, 8.5, None)["level"] == "tight"
    assert L.fit(20, 16, None)["level"] == "too_big" and "Probably too big" in L.fit(20, 16, None)["text"]
    assert L.fit(2, None, 1)["level"] == "too_big"                              # a tiny GPU and no readable RAM still says no


def test_search_page_parsing_on_real_markup():
    items = L.parse_search(SEARCH)
    assert [i["name"] for i in items] == ["qwen3.8", "qwen3.5"]
    a, b = items
    assert a["caps"] == ["vision", "tools", "thinking"] and a["pulls"] == 7174284 and a["updated"] == "November 8, 2026" and a["tags_count"] == 13 and a["sizes"] == ["27b"]
    assert b["sizes"][:3] == ["0.8b", "2b", "4b"] and b["desc"].startswith("Qwen 3.5")
    assert L.parse_search("<html><body>nothing here</body></html>") == []
    with pytest.raises(L.ParseError):
        L.parse_search('<html><a href="/library/">changed markup</a> /library/ <li></li></html>')
    odd = L.parse_search('<ul><li><a href="/library/x"><span title="other">z</span><span class="bg-indigo-50">weird</span><p>d</p><p>second</p></a></li></ul>')
    assert odd[0]["caps"] == [] and odd[0]["desc"] == "d"


def test_tag_page_parsing_on_real_markup():
    rows = L.parse_tags(TAGS, "qwen3")
    assert rows[0] == {"tag": "latest", "size_gb": 5.2, "context": "40K"} and rows[1]["tag"] == "0.6b" and rows[1]["size_gb"] == 0.52 and len(rows) == 3
    units = L.parse_tags('href="/library/m:a" 500MB 8K context href="/library/m:a" href="/library/m:b" 2TB href="/library/m:c" nothing', "m")
    assert [(r["tag"], r["size_gb"]) for r in units] == [("a", 0.5), ("b", 2000.0), ("c", None)] and units[2]["context"] is None
    with pytest.raises(L.ParseError):
        L.parse_tags("<html></html>", "qwen3")
    with pytest.raises(L.ParseError):
        L.parse_tags('href="/library/m:"', "m")


def test_curated_list_is_valid():
    cur = L.load_curated()
    assert len(cur["models"]) >= 15 and all(m["tags"] and m["best_for"] and m["label"] for m in cur["models"])
    assert {c for m in cur["models"] for c in m["caps"]} <= {"tools", "thinking", "vision", "embedding"}


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def lib(tmp_path, fetch=None, clock=None):
    return L.Library(fetch=fetch or (lambda url: SEARCH), home=tmp_path, clock=clock or Clock())


def test_refresh_caches_throttles_and_flags_new_models(tmp_path):
    clock = Clock()
    pages = {"n": SEARCH}
    lb = lib(tmp_path, fetch=lambda url: pages["n"], clock=clock)
    r = lb.refresh()
    assert r["ok"] and not r["throttled"] and r["count"] == 2
    again = lb.refresh()
    assert again["throttled"] and "recently" in again["message"]
    clock.t += L.MANUAL_MIN_S + 1
    assert lb.refresh(force=True)["throttled"] is False                          # a manual press is allowed after 10 minutes
    clock.t += L.CACHE_TTL_S + 1
    pages["n"] = SEARCH.replace("/library/qwen3.8", "/library/brandnew").replace('title="qwen3.8"', 'title="brandnew"')
    assert lb.refresh()["count"] == 2
    found = {f["name"]: f for f in json.loads((tmp_path / "ollama" / "library.json").read_text())["found"]}
    assert found["qwen3.5"]["first_seen"] is None                                # part of the first (baseline) read: not 'new'
    assert found["brandnew"]["first_seen"] == clock.t                            # appeared later: new
    cat = lb.catalog([], 16, None)
    new = {m["name"]: m for m in cat if m["source"] == "ollama.com"}
    assert new["brandnew"]["new"] is True and new["qwen3.5"]["new"] is False and new["qwen3.5"]["downloads"] == 27044896


def test_refresh_by_query_has_its_own_clock_and_fails_soft(tmp_path):
    clock = Clock()
    seen = []
    lb = lib(tmp_path, fetch=lambda url: seen.append(url) or SEARCH, clock=clock)
    assert lb.refresh("q wen")["ok"] and seen[-1].endswith("/search?q=q%20wen")
    assert lb.refresh("Q WEN")["throttled"]
    assert not lb.refresh("other")["throttled"]

    def down(url):
        raise httpx.ConnectError("offline")
    lb = lib(tmp_path / "x", fetch=down, clock=clock)
    r = lb.refresh()
    assert r["ok"] is False and "Could not read ollama.com" in r["message"] and r["count"] == 0

    def changed(url):
        return '<html><a href="/library/">x</a> /library/</html>'
    r = lib(tmp_path / "y", fetch=changed, clock=clock).refresh()
    assert r["ok"] is False and "no longer looks" in r["message"]


def test_tags_are_cached_and_fail_soft(tmp_path):
    clock = Clock()
    calls = []
    lb = lib(tmp_path, fetch=lambda url: calls.append(url) or TAGS, clock=clock)
    assert lb.tags("bad name")["ok"] is False
    r = lb.tags("qwen3")
    assert r["ok"] and r["tags"][0]["tag"] == "latest" and calls == ["https://ollama.com/library/qwen3/tags"]
    assert lb.tags("qwen3")["ok"] and len(calls) == 1                              # cached
    clock.t += L.CACHE_TTL_S + 1

    def down(url):
        raise httpx.ConnectError("offline")
    stale = L.Library(fetch=down, home=tmp_path, clock=clock).tags("qwen3")
    assert stale["ok"] is False and stale["tags"] and "Could not read" in stale["message"]
    assert L.Library(fetch=down, home=tmp_path / "other", clock=clock).tags("llama3")["tags"] == []


def test_catalog_merges_curated_and_discovered_and_filters(tmp_path):
    lb = lib(tmp_path, fetch=lambda url: TAGS.replace("library/qwen3:", "library/qwen3.8:") if url.endswith("/tags") else SEARCH)
    lb.refresh()
    assert lb.tags("qwen3.8")["ok"]                                                  # per-tag sizes for one discovered model
    cat = lb.catalog(["qwen3:8b", "llama3.2:3b"], 16, 8)
    by = {m["name"]: m for m in cat}
    assert by["qwen3"]["installed"] and by["qwen3"]["tags"][1]["installed"] and not by["qwen3"]["tags"][0]["installed"]
    assert by["llama3.2"]["installed"] and by["gpt-oss"]["tags"][1]["fit"]["level"] == "too_big"
    assert by["qwen3.8"]["source"] == "ollama.com" and by["qwen3.8"]["tags"][0]["tag"] == "latest" and by["qwen3.8"]["tags"][0]["size_gb"] == 5.2
    assert by["qwen3.5"]["tags"][0] == {"tag": "0.8b", "size_gb": None, "fit": L.fit(None, 16, 8), "installed": False}
    assert [m["name"] for m in lb.catalog([], 16, 8, "embed") if m["source"] == "curated"] == ["nomic-embed-text", "mxbai-embed-large", "bge-m3"]
    assert [m["name"] for m in lb.catalog([], 16, 8, "  VISION ")][:2] == ["gemma3", "llama3.2-vision"]
    assert lb.catalog([], None, None, "zzzz-nothing") == []


def test_library_survives_a_corrupt_cache_and_reads_the_real_page_through_httpx(tmp_path, monkeypatch):
    d = tmp_path / "ollama"
    d.mkdir()
    (d / "library.json").write_text("{not json", encoding="utf-8")
    assert lib(tmp_path).catalog([], 8, None)
    (d / "library.json").write_text("[1,2]", encoding="utf-8")
    assert lib(tmp_path).catalog([], 8, None)
    monkeypatch.setattr(L, "TRANSPORT", mock(lambda r: httpx.Response(200, text=SEARCH) if r.url.path == "/search" else httpx.Response(404)))
    assert len(L.Library(home=tmp_path / "z", clock=Clock()).refresh()["count"] * [1]) == 2
    monkeypatch.setattr(L, "TRANSPORT", mock(lambda r: httpx.Response(404)))
    assert L.Library(home=tmp_path / "w", clock=Clock()).refresh()["ok"] is False
    assert L.Library(home=tmp_path / "v")._now() > 0


def test_a_discovered_model_already_in_the_curated_list_is_not_shown_twice(tmp_path):
    page = SEARCH.replace("/library/qwen3.8", "/library/qwen3").replace('title="qwen3.8"', 'title="qwen3"')
    lb = lib(tmp_path, fetch=lambda url: page)
    lb.refresh()
    assert [m["name"] for m in lb.catalog([], 16, None)].count("qwen3") == 1
