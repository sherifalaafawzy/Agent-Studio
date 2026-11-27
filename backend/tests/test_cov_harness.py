"""Coverage for app/harness/{tools,files,sandbox,verify}.py - error and edge branches.

The Docker sandbox is replaced by a fake object (or by monkeypatched docker CLI calls), so these
tests never start a container.
"""
import asyncio
import json
import subprocess

import pytest

from app.harness import files as F
from app.harness import sandbox as sbx
from app.harness import verify as V
from app.harness.sandbox import Sandbox, SandboxUnavailable
from app.harness.tools import ALL_TOOLS, ToolContext, build_tools
from app.skills import SkillRegistry


class FakeSandbox:
    """Stands in for the Docker sandbox. `reply` is a dict, or an exception to raise."""

    def __init__(self, reply=None):
        self.reply = reply if reply is not None else dict(exit_code=0, stdout="ok\n", stderr="", timed_out=False, truncated=False)
        self.commands = []

    def exec(self, command, timeout_s=60):
        self.commands.append(command)
        if isinstance(self.reply, Exception):
            raise self.reply
        return dict(self.reply)

    def stop(self):
        pass


@pytest.fixture
def tenv(tmp_path, state):
    ws = tmp_path / "ws"
    ws.mkdir()
    skills = SkillRegistry(user_dir=tmp_path / "uskills")
    sb = FakeSandbox()
    ctx = ToolContext(ws, sb, skills, state.mcp)
    tools = {f.__name__: f for f in build_tools(ctx, ALL_TOOLS)}
    return ws, ctx, tools, sb


def run(coro):
    return asyncio.run(coro)


# ---------- files.py ----------

def test_jail_rejects_empty_nul_and_accepts_absolute_inside(tmp_path):
    ws = tmp_path / "w"
    ws.mkdir()
    with pytest.raises(F.ToolError, match="invalid path"):
        F.jail(ws, "")
    with pytest.raises(F.ToolError, match="invalid path"):
        F.jail(ws, "a\x00b")
    inside = ws / "x.txt"
    assert F.jail(ws, str(inside)) == inside.resolve()
    assert F.jail(ws, ".") == ws.resolve()


def test_read_text_rejects_too_large_and_binary(tmp_path):
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * (F.MAX_READ_BYTES * 4 + 1))
    with pytest.raises(F.ToolError, match="too large"):
        F.read_text(big)
    binf = tmp_path / "bin.dat"
    binf.write_bytes(b"abc\x00def")
    with pytest.raises(F.ToolError, match="binary"):
        F.read_text(binf)


def test_read_text_normalises_crlf(tmp_path):
    p = tmp_path / "c.txt"
    p.write_bytes(b"one\r\ntwo\r\n")
    assert F.read_text(p) == ("one\ntwo\n", "\r\n")


def test_write_text_rejects_oversize_and_keeps_newline_style(tmp_path):
    with pytest.raises(F.ToolError, match="too large"):
        F.write_text(tmp_path / "huge.txt", "x" * (F.MAX_WRITE_BYTES + 1), "\n")
    assert not (tmp_path / "huge.txt").exists()
    F.write_text(tmp_path / "sub" / "w.txt", "a\nb\n", "\r\n")
    assert (tmp_path / "sub" / "w.txt").read_bytes() == b"a\r\nb\r\n"


def test_numbered_pages_and_truncates_long_output():
    text = "\n".join(f"line{i}" for i in range(1, 6))
    assert F.numbered(text, 2, 3) == "    2\tline2\n    3\tline3"
    assert F.numbered(text, 0, 0).splitlines()[0] == "    1\tline1"       # start clamped to 1
    long_text = "\n".join("y" * 1000 for _ in range(300))
    out = F.numbered(long_text)
    assert "truncated at line" in out and out.count("\n") < 300


def test_apply_edit_guards_and_whitespace_ambiguity():
    with pytest.raises(F.ToolError, match="must not be empty"):
        F.apply_edit("abc", "", "x")
    with pytest.raises(F.ToolError, match="identical"):
        F.apply_edit("abc", "b", "b")
    # whitespace-tolerant match hits two places -> must ask for more context
    text = "x  = 1\ny\nx =  1\nz\n"          # exact "x = 1" never occurs; ignoring whitespace it occurs twice
    with pytest.raises(F.ToolError, match="ignoring whitespace"):
        F.apply_edit(text, "x = 1", "x = 2")


def test_unified_diff_is_capped():
    old = "".join(f"old line {i} with padding text\n" for i in range(800))
    new = "".join(f"new line {i} with padding text\n" for i in range(800))
    d = F.unified(old, new, "f.txt")
    assert d.startswith("--- a/f.txt") and d.endswith("[diff truncated]")


# ---------- sandbox.py (docker CLI faked) ----------

def test_docker_timeout_becomes_sandbox_unavailable(monkeypatch):
    monkeypatch.setattr(sbx.shutil, "which", lambda _: "docker.exe")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=1)

    monkeypatch.setattr(sbx.subprocess, "run", slow)
    with pytest.raises(SandboxUnavailable, match="did not respond"):
        sbx._docker(["version"], timeout=1)


def test_status_reports_daemon_down(monkeypatch):
    monkeypatch.setattr(sbx, "_docker", lambda args, timeout=30: subprocess.CompletedProcess(args, 1, "", "down"))
    st = sbx.status()
    assert st["docker"] is False and "daemon not running" in st["detail"]


def test_status_reports_ready_or_missing_image_without_a_real_docker(monkeypatch):
    for image_rc, detail in ((0, "ready"), (1, "image missing")):
        monkeypatch.setattr(sbx, "_docker", lambda args, timeout=30, rc=image_rc: subprocess.CompletedProcess(args, 0 if args[0] == "version" else rc, "27.1.0\n", ""))
        st = sbx.status()
        assert st["docker"] is True and st["server"] == "27.1.0" and st["image"] is (image_rc == 0) and st["detail"].startswith(detail)


def test_clip_truncates_middle_of_huge_output():
    s = "A" * (sbx.MAX_OUT // 2) + "MIDDLE" + "B" * (sbx.MAX_OUT)
    out, truncated = sbx._clip(s)
    assert truncated is True
    assert "truncated" in out and out.startswith("A") and out.endswith("B")
    assert sbx._clip("short") == ("short", False)


def _fake_docker(responses):
    """responses: list of (predicate(args) -> bool, CompletedProcess or Exception)."""
    calls = []

    def fake(args, timeout=30):
        calls.append(list(args))
        for pred, res in responses:
            if pred(args):
                if isinstance(res, Exception):
                    raise res
                return res
        return subprocess.CompletedProcess(args, 0, "", "")

    return fake, calls


def test_ensure_restarts_stopped_container(monkeypatch, tmp_path):
    ok = lambda o="": subprocess.CompletedProcess([], 0, o, "")  # noqa: E731
    fake, calls = _fake_docker([
        (lambda a: a[0] == "inspect", ok("false\n")),           # exists but not running
    ])
    monkeypatch.setattr(sbx, "status", lambda: dict(docker=True, image=True, detail="ready"))
    monkeypatch.setattr(sbx, "_docker", fake)
    Sandbox(tmp_path / "ws").ensure()
    assert ["rm", "-f", Sandbox(tmp_path / "ws").name] in calls
    assert any(a[0] == "run" for a in calls)


def test_ensure_reports_run_failure(monkeypatch, tmp_path):
    fake, _ = _fake_docker([
        (lambda a: a[0] == "inspect", subprocess.CompletedProcess([], 1, "", "")),
        (lambda a: a[0] == "run", subprocess.CompletedProcess([], 125, "", "port conflict  \n")),
    ])
    monkeypatch.setattr(sbx, "status", lambda: dict(docker=True, image=True, detail="ready"))
    monkeypatch.setattr(sbx, "_docker", fake)
    with pytest.raises(SandboxUnavailable, match="could not start sandbox container: port conflict"):
        Sandbox(tmp_path / "ws").ensure()


def test_exec_returns_timed_out_when_docker_unreachable(monkeypatch, tmp_path):
    def fake(args, timeout=30):
        if args[0] == "exec":
            raise SandboxUnavailable("docker did not respond in time")
        return subprocess.CompletedProcess(args, 0, "true", "")      # container reported running

    monkeypatch.setattr(sbx, "status", lambda: dict(docker=True, image=True, detail="ready"))
    monkeypatch.setattr(sbx, "_docker", fake)
    r = Sandbox(tmp_path / "ws").exec("sleep 1", 5)
    assert r["timed_out"] is True and r["exit_code"] == -1


def test_exec_clips_large_output_and_flags_truncation(monkeypatch, tmp_path):
    big = "o" * (sbx.MAX_OUT + 10)
    monkeypatch.setattr(sbx, "status", lambda: dict(docker=True, image=True, detail="ready"))
    monkeypatch.setattr(sbx, "_docker", lambda args, timeout=30: (
        subprocess.CompletedProcess(args, 137, big, "")
        if args[0] == "exec" else subprocess.CompletedProcess(args, 0, "true", "")))
    r = Sandbox(tmp_path / "ws").exec("yes", 5)
    assert r["truncated"] is True and r["timed_out"] is True and "truncated" in r["stdout"]


def test_stop_swallows_unavailable_docker(monkeypatch, tmp_path):
    def gone(args, timeout=30):
        raise SandboxUnavailable("docker CLI not found")

    monkeypatch.setattr(sbx, "_docker", gone)
    Sandbox(tmp_path / "ws").stop()          # must not raise


def test_sandbox_name_depends_on_workspace_and_network(tmp_path):
    a = Sandbox(tmp_path / "a")
    assert a.name != Sandbox(tmp_path / "a", network=True).name
    assert a.name.startswith("agent-studio-") and len(a.name) == len("agent-studio-") + 12


# ---------- verify.py ----------

def test_verify_summary_and_python_pass_without_sandbox(tmp_path):
    p = tmp_path / "m.py"
    v = V.verify(p, "m.py", "x = 1\n", None)
    assert v.ok is True
    assert v.summary() == "python-syntax:pass; ruff:skipped"
    assert v.checks[1]["detail"] == "no sandbox configured"


def test_verify_sandbox_pass_fail_and_missing_tool(tmp_path):
    p = tmp_path / "m.py"
    sb = FakeSandbox(dict(exit_code=0, stdout="All checks passed\n", stderr="", timed_out=False, truncated=False))
    v = V.verify(p, "m.py", "x = 1\n", sb)
    assert v.checks[1] == dict(check="ruff", status="pass", detail="All checks passed")

    sb = FakeSandbox(dict(exit_code=1, stdout="", stderr="", timed_out=False, truncated=False))
    v = V.verify(p, "m.py", "x = 1\n", sb)
    assert v.ok is False and v.checks[1]["detail"] == "exit 1"

    sb = FakeSandbox(dict(exit_code=127, stdout="", stderr="ruff: not found", timed_out=False, truncated=False))
    v = V.verify(p, "m.py", "x = 1\n", sb)
    assert v.ok is True and v.checks[1]["status"] == "skipped"


def test_verify_sandbox_unavailable_is_skipped_not_failed(tmp_path):
    v = V.verify(tmp_path / "m.py", "m.py", "x = 1\n", FakeSandbox(SandboxUnavailable("no docker")))
    assert v.ok is True and v.checks[1]["status"] == "skipped"
    assert "sandbox unavailable" in v.checks[1]["detail"]


def test_verify_json_toml_yaml(tmp_path):
    p = tmp_path / "x"
    good = V.verify(p.with_suffix(".json"), "x.json", '{"a": 1}', None)
    assert good.checks == [dict(check="json-syntax", status="pass", detail="")]
    bad = V.verify(p.with_suffix(".json"), "x.json", "{", None)
    assert bad.ok is False and bad.checks[0]["check"] == "json-syntax"

    assert V.verify(p.with_suffix(".toml"), "x.toml", 'a = "b"', None).checks[0]["status"] == "pass"
    bad = V.verify(p.with_suffix(".toml"), "x.toml", "a = = 1", None)
    assert bad.ok is False and bad.checks[0]["check"] == "toml-syntax"

    assert V.verify(p.with_suffix(".yaml"), "x.yaml", "a: 1\n---\nb: 2\n", None).checks[0]["status"] == "pass"
    bad = V.verify(p.with_suffix(".yml"), "x.yml", "a: [unclosed", None)
    assert bad.ok is False and bad.checks[0]["check"] == "yaml-syntax"


def test_verify_python_syntax_error_short_circuits(tmp_path):
    v = V.verify(tmp_path / "bad.py", "bad.py", "def f(:\n", FakeSandbox())
    assert v.ok is False and [c["check"] for c in v.checks] == ["python-syntax"]
    assert ":1:" in v.checks[0]["detail"]


def test_verify_routes_js_and_shell_to_sandbox(tmp_path):
    sb = FakeSandbox()
    V.verify(tmp_path / "a.mjs", "a.mjs", "export {}", sb)
    V.verify(tmp_path / "run.sh", "run.sh", "echo hi", sb)
    assert sb.commands == ["node --check a.mjs", "bash -n run.sh"]


def test_verify_unknown_extension_is_reported_skipped(tmp_path):
    v = V.verify(tmp_path / "notes.txt", "notes.txt", "hello", None)
    assert v.ok is True and v.checks[0]["status"] == "skipped"
    assert "NOT checked" in v.checks[0]["detail"]


# ---------- tools.py ----------

def test_read_file_reports_missing_and_pages(tenv):
    ws, ctx, t, _ = tenv
    assert run(t["read_file"]("nope.txt"))["ok"] is False
    assert "not a file" in run(t["read_file"]("nope.txt"))["error"]
    (ws / "a.txt").write_text("l1\nl2\nl3\n")
    r = run(t["read_file"]("a.txt", 2, 3))
    assert r["ok"] and r["total_lines"] == 4
    assert r["content"] == "    2\tl2\n    3\tl3"
    assert "a.txt" in ctx.read_files


def test_list_dir_entries_sizes_and_errors(tenv):
    ws, ctx, t, _ = tenv
    (ws / "src").mkdir()
    (ws / "src" / "inner.py").write_text("x = 1")
    (ws / ".git").mkdir()
    (ws / "b.txt").write_text("hello")
    r = run(t["list_dir"]("."))
    assert r["ok"] and r["path"] == "."
    names = [e["name"] for e in r["entries"]]
    assert names == ["src/", "b.txt"]                       # folders sort before files, .git hidden
    assert r["entries"][0]["size"] is None and r["entries"][1] == {"name": "b.txt", "size": 5}
    sub = run(t["list_dir"]("src"))
    assert sub["path"] == "src" and sub["entries"] == [{"name": "inner.py", "size": 5}]
    assert run(t["list_dir"]("b.txt"))["error"].startswith("not a directory")
    assert run(t["list_dir"]("../outside"))["ok"] is False


def test_list_dir_caps_at_300_entries(tenv):
    ws, _, t, _ = tenv
    for i in range(305):
        (ws / f"f{i:03d}.txt").write_text("")
    r = run(t["list_dir"]("."))
    assert len(r["entries"]) == 300 and r["truncated"] is True


def test_glob_files_rules(tenv):
    ws, _, t, _ = tenv
    (ws / "src").mkdir()
    (ws / "src" / "a.py").write_text("")
    (ws / "src" / "b.txt").write_text("")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "dep.py").write_text("")
    (ws / "dir.py").mkdir()                                  # directories are not matches
    assert run(t["glob_files"]("/etc/*"))["ok"] is False
    assert "relative" in run(t["glob_files"]("..\\*.py"))["error"] or run(t["glob_files"]("../*.py"))["ok"] is False
    assert run(t["glob_files"]("../*.py"))["ok"] is False
    assert run(t["glob_files"]("**/*.py"))["matches"] == ["src/a.py"]
    assert run(t["glob_files"](""))["ok"] is False           # invalid pattern from pathlib


def test_glob_files_caps_at_300(tenv):
    ws, _, t, _ = tenv
    for i in range(305):
        (ws / f"g{i:03d}.log").write_text("")
    r = run(t["glob_files"]("*.log"))
    assert len(r["matches"]) == 300 and r["matches"] == sorted(r["matches"])


def test_search_text_single_file_glob_and_skip_rules(tenv):
    ws, _, t, _ = tenv
    (ws / "one.py").write_text("needle here\nno\n")
    (ws / "two.md").write_text("needle too\n")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "x.py").write_text("needle hidden\n")
    (ws / "binary.py").write_bytes(b"\xff\xfe needle")                # undecodable -> skipped
    (ws / "huge.py").write_bytes(b"needle " * 200_000)                # > 1 MB -> skipped
    (ws / "long.py").write_text("needle " + "z" * 400 + "\n")
    single = run(t["search_text"]("needle", "one.py"))
    assert single["matches"] == ["one.py:1:needle here"] and single["truncated"] is False
    py = run(t["search_text"]("needle", ".", "*.py"))
    files = sorted(m.split(":")[0] for m in py["matches"])
    assert files == ["long.py", "one.py"]
    long_hit = next(m for m in py["matches"] if m.startswith("long.py"))
    assert len(long_hit.split(":", 2)[2]) == 200                         # clipped to 200 chars


def test_search_text_truncates_at_100_and_reports_bad_regex(tenv):
    ws, _, t, _ = tenv
    (ws / "many.txt").write_text("hit\n" * 150)
    r = run(t["search_text"]("hit"))
    assert len(r["matches"]) == 100 and r["truncated"] is True
    assert run(t["search_text"]("(unclosed"))["error"].startswith("bad regex")
    assert run(t["search_text"]("x", "../..")) == {"ok": False, "error": "path escapes the workspace: ../.."}


def test_write_file_create_overwrite_and_rollback(tenv):
    ws, _, t, _ = tenv
    r = run(t["write_file"]("app.py", "x = 1\n"))
    assert r["ok"] and r["verified"] is True and r["verify"][0]["status"] == "pass"
    assert (ws / "app.py").read_text() == "x = 1\n"
    refused = run(t["write_file"]("app.py", "x = 2\n"))
    assert refused["ok"] is False and "file exists" in refused["error"]
    assert (ws / "app.py").read_text() == "x = 1\n"

    (ws / "cfg.json").write_text('{"a": 1}\n')
    bad = run(t["write_file"]("cfg.json", "{broken", overwrite=True))
    assert bad["ok"] is False and bad["rolled_back"] is True
    assert (ws / "cfg.json").read_text() == '{"a": 1}\n'     # original restored byte for byte

    good = run(t["write_file"]("cfg.json", '{"a": 2}', overwrite=True))
    assert good["ok"] and (ws / "cfg.json").read_text() == '{"a": 2}'


def test_write_file_oversize_and_escape_are_refused(tenv):
    ws, _, t, _ = tenv
    big = run(t["write_file"]("big.txt", "q" * (F.MAX_WRITE_BYTES + 1)))
    assert big == {"ok": False, "error": "content too large"}
    assert not (ws / "big.txt").exists()
    assert run(t["write_file"]("../x.py", "x=1"))["ok"] is False


def test_edit_file_missing_oversize_and_identical_edits(tenv):
    ws, ctx, t, _ = tenv
    assert "file not found" in run(t["edit_file"]("ghost.py", "a", "b"))["error"]
    (ws / "e.py").write_text("v = 1\n")
    run(t["read_file"]("e.py"))
    same = run(t["edit_file"]("e.py", "v = 1", "v = 1"))
    assert same["ok"] is False and "identical" in same["error"]
    assert (ws / "e.py").read_text() == "v = 1\n"


def test_edit_file_failed_verification_has_no_diff(tenv):
    ws, ctx, t, _ = tenv
    (ws / "cfg.json").write_text('{"a": 1}\n')
    run(t["read_file"]("cfg.json"))
    r = run(t["edit_file"]("cfg.json", '"a": 1', '"a": '))
    assert r["ok"] is False and r["rolled_back"] is True and "diff" not in r
    assert (ws / "cfg.json").read_text() == '{"a": 1}\n'


def test_edit_file_success_returns_diff_and_match_mode(tenv):
    ws, ctx, t, _ = tenv
    (ws / "s.py").write_text("def f():\n    return 1\n")
    run(t["read_file"]("s.py"))
    r = run(t["edit_file"]("s.py", "return 1", "return 3"))
    assert r["ok"] and r["match"] == "exact" and "+    return 3" in r["diff"]


def test_run_shell_uses_sandbox_and_maps_exit_code(tenv):
    _, _, t, sb = tenv
    r = run(t["run_shell"]("echo hi"))
    assert r["ok"] is True and r["stdout"] == "ok\n" and sb.commands == ["echo hi"]
    sb.reply = dict(exit_code=2, stdout="", stderr="boom", timed_out=False, truncated=False)
    r = run(t["run_shell"]("false", 5))
    assert r["ok"] is False and r["exit_code"] == 2 and r["stderr"] == "boom"
    sb.reply = SandboxUnavailable("docker CLI not found")
    r = run(t["run_shell"]("ls"))
    assert r["ok"] is False and r["error"].startswith("SANDBOX UNAVAILABLE (refusing to run on host)")


def test_load_skill_known_and_unknown(tenv):
    _, _, t, _ = tenv
    ok = run(t["load_skill"]("debugging"))
    assert ok["ok"] and ok["name"] == "debugging" and ok["instructions"].strip()
    miss = run(t["load_skill"]("no-such-skill"))
    assert miss["ok"] is False and "debugging" in miss["available"]


def test_create_skill_success_and_validation_errors(tenv):
    _, ctx, t, _ = tenv
    r = run(t["create_skill"]("triage-bugs", "Triage new bug reports. Use when a bug is filed.", "1. read it"))
    assert r["ok"] and r["skill"]["name"] == "triage-bugs"
    assert ctx.skills.get("triage-bugs") is not None
    bad = run(t["create_skill"]("Bad Name", "d", "b"))
    assert bad["ok"] is False and "lowercase" in bad["error"]
    dup = run(t["create_skill"]("debugging", "d", "b"))
    assert dup["ok"] is False and "built-in" in dup["error"]


def test_propose_mcp_server_rejects_bad_id(tenv):
    _, _, t, _ = tenv
    r = run(t["propose_mcp_server"]("A", "Label", "why", "npx", []))
    assert r["ok"] is False and "lowercase" in r["error"]


def _write_spec(path, with_op=True):
    doc = {"openapi": "3.0.0", "info": {"title": "T", "version": "1"}, "servers": [{"url": "https://api.example.com"}],
           "paths": {"/items": {"get": {"operationId": "listItems", "responses": {"200": {"description": "ok"}}}}}} if with_op else {}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_propose_openapi_mcp_validates_spec_path_and_duplicates(tenv, tmp_path, state):
    _, _, t, _ = tenv
    assert run(t["propose_openapi_mcp"]("billing", "B", "d", "spec.json"))["ok"] is False      # relative path
    notjson = tmp_path / "bad.json"
    notjson.write_text("this is not an OpenAPI document", encoding="utf-8")
    r = run(t["propose_openapi_mcp"]("billing", "B", "d", str(notjson)))
    assert r["ok"] is False and r["error"].startswith("not a usable OpenAPI document")

    spec = _write_spec(tmp_path / "api.json")
    r = run(t["propose_openapi_mcp"]("billing", "Billing", "Invoices", str(spec), base_url="https://billing.example.com"))
    assert r["ok"] is True and r["status"] == "pending_approval" and r["operations"] == 1
    stored = state.mcp.get("billing")
    assert stored is not None and not stored.enabled
    assert "--base" in stored.args and "https://billing.example.com" in stored.args

    dup = run(t["propose_openapi_mcp"]("billing", "Billing", "Invoices", str(spec)))
    assert dup["ok"] is False and "already exists" in dup["error"]
