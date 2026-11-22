"""The security scorecard (live checks over real settings) and the red-team runners (install, runs, the target address). Fakes only."""
from __future__ import annotations

import json
import sys
import threading
import time

import pytest

from app.agents import AgentDef
from app.security import redteam as RT
from app.security import scorecard as SC
from app.security.redteam import RedTeam, RedTeamError


def check(card, cid):
    return next(c for c in card["checks"] if c["id"] == cid)


def status(state, cid):
    return check(SC.build(state), cid)["status"]


# ------------------------------------------------------------------------------------------------- scorecard
def test_every_owasp_risk_is_covered_by_at_least_one_check(state):
    card = SC.build(state)
    assert [f["id"] for f in card["frameworks"]] == ["llm2025", "asi2026"]
    for fw in card["frameworks"]:
        assert len(fw["risks"]) == 10 and fw["covered"] == 10 and all(r["checks"] for r in fw["risks"]), fw["id"]
    ids = [r["id"] for f in card["frameworks"] for r in f["risks"]]
    assert ids == [f"LLM{n:02d}" for n in range(1, 11)] + [f"ASI{n:02d}" for n in range(1, 11)]
    assert 0 <= card["percent"] <= 100 and sum(card["counts"].values()) == len(card["checks"])
    assert all(c["status"] in ("pass", "warn", "fail") and c["detail"] for c in card["checks"])
    maps = {m for c in card["checks"] for m in c["maps"]}
    assert maps == set(ids)                                                    # a check never names a risk that does not exist


def test_default_state_says_where_the_gaps_are(state):
    card = SC.build(state)
    assert check(card, "guard-input")["status"] == "fail" and "switched off" in check(card, "guard-input")["detail"]
    for cid in ("guard-model", "guard-tool", "guard-output", "guard-leaks", "evals", "redteam", "retrieved-text"):
        assert check(card, cid)["status"] == "warn"
    assert all(check(card, c)["status"] == "pass" for c in ("sandbox-network", "sandbox-only", "spawn-rules", "inter-agent", "transparency", "secrets-write-only", "telemetry"))
    assert check(card, "sandbox-only")["kind"] == "built-in" and check(card, "guard-input")["kind"] == "setting"
    risk = {r["id"]: r["status"] for f in card["frameworks"] for r in f["risks"]}
    assert risk["LLM01"] == "fail" and risk["LLM08"] in ("pass", "warn")


def test_guardrail_checks_follow_the_profiles(state):
    g = state.guardrails
    g.save({"id": "watch", "name": "Watch", "mode": "monitor", "default": True, "params": {"detect": ["injection"]}})
    assert status(state, "guard-input") == "fail"                              # still off
    g.set_enabled(True)
    assert status(state, "guard-input") == "warn" and status(state, "guard-leaks") == "warn"        # only injection rules
    g.save({"id": "watch", "mode": "block", "params": {"detect": ["pii", "secrets"]}})
    assert status(state, "guard-input") == "pass" and status(state, "guard-leaks") == "pass"
    assert status(state, "guard-tool") == "pass" and status(state, "guard-output") == "pass" and status(state, "retrieved-text") == "pass"
    g.save({"id": "watch", "stages": {"input": False, "output": True, "tool": False}})
    assert status(state, "guard-input") == "fail" and "no profile looks at inputs" in check(SC.build(state), "guard-input")["detail"]
    assert status(state, "guard-tool") == "warn" and status(state, "guard-output") == "pass"
    g.save({"id": "model1", "name": "Llama", "kind": "model", "mode": "monitor", "default": False})
    state.agents.update("assistant", {"guardrails": ["model1"]})
    g.save({"id": "watch", "default": False})
    g.save({"id": "model2", "name": "Llama 2", "kind": "model", "mode": "block", "default": True})
    assert status(state, "guard-model") == "pass" and "model" in check(SC.build(state), "guard-model")["detail"]


def test_setting_checks(state):
    s = state
    s.db.set_setting("sandbox_network", "1")
    assert status(s, "sandbox-network") == "warn"
    s.agents.create(AgentDef(id="shelly", name="Shelly", tools=["run_shell"]))
    c = check(SC.build(s), "least-privilege")
    assert c["status"] == "warn" and "shelly" in c["detail"]
    s.db.set_setting("max_llm_calls_per_turn", "500")
    assert status(s, "limits") == "warn"
    s.db.set_setting("max_llm_calls_per_turn", "40")
    assert status(s, "limits") == "pass"
    s.db.set_setting("otel_enabled", "0")
    assert status(s, "telemetry") == "fail"
    s.agents.update("super_engineer", {"acp_policy": "reads_auto"})
    assert status(s, "external-agents") == "warn"
    s.agents.update("super_engineer", {"acp_policy": "ask"})
    assert status(s, "external-agents") == "pass"
    assert "External agents may be called" in check(SC.build(s), "external-agents")["detail"]
    s.db.set_setting("agents_can_call_external", "0")
    for a in s.agents.list():
        s.agents.update(a.id, {"acp_agents": []})
    assert "No agent calls external agents" in check(SC.build(s), "external-agents")["detail"]


def test_provider_cap_check(state):
    p = next(x for x in state.providers.list() if x.kind not in ("ollama", "mock"))
    state.db.execute("UPDATE providers SET enabled=1, cap_usd=5000 WHERE id=?", (p.id,))
    c = check(SC.build(state), "limits")
    assert c["status"] == "warn" and "high caps" in c["detail"] and "5000" in c["detail"]


def test_mcp_and_catalog_checks(state):
    state.mcp.add("loose", "Loose", "", "stdio", "npx", ["-y", "somepkg"], None, {}, enabled=True)
    state.mcp.add("tight", "Tight", "", "stdio", "uvx", ["pkg==1.2.3"], None, {}, enabled=True)
    state.mcp.add("remote", "Remote", "", "http", None, [], "https://x.example.com", {}, enabled=True)
    c = check(SC.build(state), "mcp-pinned")
    assert c["status"] == "warn" and "loose" in c["detail"] and "tight" not in c["detail"]
    state.mcp.update("loose", enabled=False)
    assert status(state, "mcp-pinned") == "pass"
    assert status(state, "mcp-writes") == "pass"
    state.db.set_setting("mcp_catalog_installed", json.dumps({"github": {"allow_writes": True}}))
    assert "github" in check(SC.build(state), "mcp-writes")["detail"]


def test_discovery_memory_vector_identity_and_run_checks(state):
    s = state
    assert status(s, "aibom") == "pass"
    s.db.set_setting("ard_settings", json.dumps({"enabled": True, "level": "internal"}))
    c = check(SC.build(s), "aibom")
    assert c["status"] == "fail" and "without an access token" in c["detail"]
    s.db.set_setting("ard_token_hash", "abc")
    assert "publishing is on" in check(SC.build(s), "aibom")["detail"]
    s.memory.set_config({"redact_pii": False})
    assert status(s, "memory-pii") == "warn"
    s.agents.create(AgentDef(id="rememberer", name="R", memory={"types": ["semantic"], "read_scopes": ["agent"], "write_scopes": ["agent"], "auto_recall": True, "auto_save": "auto"}))
    assert status(s, "memory-review") == "warn"
    s.memory.set_config({"review": True})
    assert status(s, "memory-review") == "pass"
    s.rag.add_store("qremote", "Qdrant", "qdrant", {"url": "http://qdrant.example.com:6333"}, {})
    assert "qremote" in check(SC.build(s), "vector-stores")["detail"]
    s.rag.add_store("qlocal", "Local q", "qdrant", {"url": "http://localhost:6333"}, {})
    assert status(s, "identity") == "warn"
    s.identity.set_config({"enforce": True})
    assert status(s, "identity") == "pass"
    s.db.execute("INSERT INTO eval_runs(id,set_id,agent,started,status) VALUES('e1','s','a',?, 'done')", (time.time(),))
    s.db.execute("INSERT INTO redteam_runs(id,tool,agent,started,status) VALUES('r1','garak','a',?, 'done')", (time.time(),))
    assert status(s, "evals") == "pass" and status(s, "redteam") == "pass"
    s.db.set_setting("ocsf_config", json.dumps({"enabled": True, "sinks": [{"id": "ab", "kind": "file", "enabled": True, "level": "", "config": {"file": "x.ndjson"}}]}))
    assert status(s, "ocsf") in ("pass", "warn")


def test_ocsf_check_passes_with_an_enabled_destination(state):
    class FakeOcsf:
        def config(self):
            return {"enabled": True, "sinks": [{"id": "a", "enabled": True}, {"id": "b", "enabled": False}]}
    state.ocsf = FakeOcsf()
    c = check(SC.build(state), "ocsf")
    assert c["status"] == "pass" and "1 destination" in c["detail"]


def test_markdown_and_json_export(state, client):
    card = SC.build(state)
    md = SC.to_markdown(card)
    assert md.startswith("# Agent Studio security scorecard") and "## OWASP Top 10 for Agentic Applications 2026" in md and "### ASI03 Identity and Privilege Abuse" in md
    assert "Fix:" in md and json.loads(SC.to_json(card))["percent"] == card["percent"]
    bare = {"percent": 0, "counts": {"pass": 0, "warn": 0, "fail": 0}, "note": "n", "checks": [],
            "frameworks": [{"id": "x", "name": "X", "url": "u", "risks": [{"id": "R1", "name": "R", "status": "none", "checks": []}]}]}
    assert "No control in Agent Studio is mapped to this risk yet." in SC.to_markdown(bare) and "NOT COVERED" in SC.to_markdown(bare)
    r = client.get("/api/security/scorecard")
    assert r.status_code == 200 and r.json()["frameworks"][0]["id"] == "llm2025"
    r = client.get("/api/security/scorecard?format=md")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and r.text.startswith("# Agent Studio")
    assert client.get("/api/security/scorecard?format=pdf").status_code == 422


# ------------------------------------------------------------------------------------------------------ red team
@pytest.fixture
def rthome(tmp_path, monkeypatch):
    monkeypatch.setenv("REDTEAM_HOME", str(tmp_path / "rt"))
    return tmp_path / "rt"


class FakeProc:
    def __init__(self, rc=0, hold=None):
        self.pid, self.rc, self.hold = 4242, rc, hold

    def wait(self):
        if self.hold is not None:
            self.hold.wait(5)
        return self.rc


def make_ready(tool):
    exe = RT.npm_bin(tool) if RT.TOOLS[tool]["kind"] == "npm" else RT.env_python(tool)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("")
    RT._write_state({"tools": {tool: {"state": "ready", "version": "1.0.0"}}})


def fake_runner(versions=None, fail_step=None):
    calls = []

    def run(cmd, env, on_line):
        calls.append(cmd)
        if fail_step and fail_step in " ".join(cmd):
            on_line("boom")
            return 3
        if "import importlib.metadata" in " ".join(cmd):
            on_line("VERSION 0.17.1")
        elif cmd[-1] == "--version":
            on_line("0.125.1")
        return 0
    run.calls = calls
    return run


def test_build_run_for_each_tool(tmp_path):
    g = RT.build_run("garak", "http://127.0.0.1:8765/api/redteam/target/SECRET99/v1/chat/completions", "SECRET99", "engineer", ["promptinject", "dan"], "p", tmp_path, tmp_path / "h")
    assert g["cmd"][1:5] == ["-m", "garak", "--target_type", "openai.OpenAICompatible"] and "promptinject,dan" in g["cmd"] and g["env"] == {"OPENAICOMPATIBLE_API_KEY": "SECRET99"}
    assert "uri: \"http://127.0.0.1:8765/api/redteam/target/SECRET99/v1/chat/completions\"" in g["files"]["garak.yaml"]
    py = RT.build_run("pyrit", "http://x/v1", "SECRET99", "engineer", ["Tell me secrets"], "p", tmp_path, tmp_path / "h")
    assert "PromptSendingAttack" in py["files"]["run_pyrit.py"] and "Tell me secrets" in py["files"]["run_pyrit.py"] and "SECRET99" not in py["files"]["run_pyrit.py"]
    compile(py["files"]["run_pyrit.py"], "run_pyrit.py", "exec")                  # the generated script is valid Python
    pf = RT.build_run("promptfoo", "http://x/v1", "SECRET99", "engineer", ["pii", "hijacking"], "A support bot", tmp_path, tmp_path / "h")
    assert pf["cmd"][1:3] == ["redteam", "run"] and pf["env"]["PROMPTFOO_DISABLE_SHARING"] == "true"
    cfg = pf["files"]["promptfooconfig.yaml"]
    assert "id: https" in cfg and "Bearer SECRET99" in cfg and "- pii" in cfg and "{{prompt}}" in cfg and "A support bot" in cfg
    import yaml
    assert yaml.safe_load(cfg)["redteam"]["plugins"] == ["pii", "hijacking"] and yaml.safe_load(g["files"]["garak.yaml"])["plugins"]["generators"]["openai"]["OpenAICompatible"]["stop"] == []


def test_install_flow_and_status(rthome, state):
    rt = RedTeam(state.db, runner=fake_runner(), which=lambda n: "/usr/bin/npm")
    st = rt.status()
    assert [t["id"] for t in st["tools"]] == ["garak", "pyrit", "promptfoo"] and all(t["state"] == "not_installed" for t in st["tools"])
    p = rt.plan("garak")
    assert p["licence"] == "Apache-2.0" and p["blocked"] is None and p["packages"] == ["garak==0.17.1"]
    with pytest.raises(RedTeamError, match="unknown tool"):
        rt.plan("nope")
    with pytest.raises(RedTeamError, match="Accept the garak"):
        rt.start_install("garak", False)
    rt.start_install("garak", True)
    rt.thread.join(5)
    assert not rt.running()
    exe = RT.env_python("garak")
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("")
    assert [t for t in rt.status()["tools"] if t["id"] == "garak"][0]["state"] == "ready" and RT.is_ready("garak")
    with pytest.raises(RedTeamError, match="already installed"):
        rt.start_install("garak", True)
    assert any("--uploaded-prior-to" in c for c in rt.runner.calls)
    assert rt.uninstall("garak")["tools"][0]["state"] == "not_installed"


def test_only_one_install_at_a_time(rthome, state):
    gate = threading.Event()

    def slow(cmd, env, on_line):
        gate.wait(5)
        return 1
    rt = RedTeam(state.db, runner=slow, which=lambda n: "npm")
    rt.start_install("garak", True)
    assert rt.status()["install"]["running"] is True and rt.status()["tools"][0]["state"] == "installing"
    with pytest.raises(RedTeamError, match="already running"):
        rt.start_install("pyrit", True)
    gate.set()
    rt.thread.join(5)


def test_install_failures_and_guards(rthome, state, monkeypatch):
    rt = RedTeam(state.db, runner=fake_runner(fail_step="--only-binary"), which=lambda n: None)
    rt.start_install("pyrit", True)
    rt.thread.join(5)
    s = rt.status()["tools"][1]
    assert s["state"] == "failed" and "failed (exit code 3)" in s["error"] and "boom" in s["error"]
    with pytest.raises(RedTeamError, match="Node.js"):
        rt.start_install("promptfoo", True)
    rt.which = lambda n: "/usr/bin/npm"
    rt.runner = fake_runner()
    rt.start_install("promptfoo", True)
    rt.thread.join(5)
    assert [t for t in rt.status()["tools"] if t["id"] == "promptfoo"][0]["state"] == "installing" or RT._read_state()["tools"]["promptfoo"]["state"] == "ready"
    assert any(c[0] == "/usr/bin/npm" and "--before" in " ".join(c) for c in rt.runner.calls)
    rt.runner = lambda cmd, env, on: 0                                           # installs but prints no version
    rt._record("pyrit", state="failed")
    rt.start_install("pyrit", True)
    rt.thread.join(5)
    assert "version could not be read" in rt.status()["tools"][1]["error"]
    monkeypatch.setattr(RT, "free_disk_gb", lambda p: 0.1)
    with pytest.raises(RedTeamError, match="not enough free disk"):
        rt.start_install("garak", True)
    rt._record("garak", state="installing")
    assert rt.status()["tools"][0]["state"] == "failed"                          # the app was closed mid-install
    with pytest.raises(RedTeamError, match="unknown tool"):
        rt.uninstall("zzz")
    rt.installing, rt.thread = "garak", threading.Thread(target=time.sleep, args=(0.3,))
    rt.thread.start()
    with pytest.raises(RedTeamError, match="being installed"):
        rt.uninstall("garak")
    rt.thread.join()


def start_args(**kw):
    a = dict(tool="garak", agent="engineer", provider="mock", model=None, options=None, max_requests=5, purpose="p", base_url="http://127.0.0.1:8765/",
             agent_exists=lambda a: a == "engineer")
    return {**a, **kw}


def test_run_lifecycle_and_target_budget(rthome, state):
    hold = threading.Event()
    seen = {}

    def popen(cmd, env, log, cwd):
        seen.update(cmd=cmd, env=env, cwd=cwd)
        log.write_text("probe 1\nprobe 2\nprobe 3\n")
        return FakeProc(0, hold)
    rt = RedTeam(state.db, popen=popen)
    with pytest.raises(RedTeamError, match="unknown tool"):
        rt.start(**start_args(tool="x"))
    with pytest.raises(RedTeamError, match="not installed"):
        rt.start(**start_args())
    make_ready("garak")
    with pytest.raises(RedTeamError, match="unknown agent"):
        rt.start(**start_args(agent="ghost"))
    for bad in ("x", ["bad probe!"], [f"p{i}" for i in range(13)], [5]):
        with pytest.raises(RedTeamError, match="1 to 12 short items"):
            rt.start(**start_args(options=bad))
    for n in (0, 5000, True, "a"):
        with pytest.raises(RedTeamError, match="between 1 and 1000"):
            rt.start(**start_args(max_requests=n))
    run = rt.start(**start_args())
    assert run["status"] == "running" and run["tool"] == "garak" and run["options"] == ["promptinject", "dan", "encoding"] and run["max_requests"] == 5
    assert (RT.runs_dir() / run["id"] / "garak.yaml").exists() and seen["env"]["OPENAICOMPATIBLE_API_KEY"] and "OPENAI_API_KEY" not in seen["env"]
    with pytest.raises(RedTeamError, match="another red-team run"):
        rt.start(**start_args())
    token = next(iter(rt.targets))
    for i in range(5):
        t = rt.target_for(token)
        rt.note_result(t, blocked=i < 2, error=i == 4)
    with pytest.raises(RedTeamError, match="used up"):
        rt.target_for(token)
    with pytest.raises(RedTeamError, match="not active"):
        rt.target_for("zzz")
    live = rt.run(run["id"], tail=2)
    assert live["requests"] == 5 and live["blocked"] == 2 and live["errors"] == 1 and live["log_tail"] == ["probe 2", "probe 3"]
    assert rt.runs()[0]["requests"] == 5
    hold.set()
    for _ in range(100):
        if rt.run(run["id"])["status"] != "running":
            break
        time.sleep(0.05)
    done = rt.run(run["id"])
    assert done["status"] == "done" and done["requests"] == 5 and done["ended"] and token not in rt.targets
    assert rt.stats()["runs"] == 1
    for bad in ("nope", "ABCDEFGHIJ", ""):
        with pytest.raises(KeyError):
            rt.run(bad)
    with pytest.raises(KeyError):
        rt.run("0123456789")
    assert rt.run(run["id"], tail=0).get("log_tail") is None
    (RT.runs_dir() / run["id"] / "log.txt").unlink()
    assert rt.run(run["id"], tail=5)["log_tail"] == []


def test_failed_cancelled_and_interrupted_runs(rthome, state):
    killed = []
    holds = []

    def popen(cmd, env, log, cwd):
        h = threading.Event()
        holds.append(h)
        return FakeProc(2, h)
    rt = RedTeam(state.db, popen=popen, kill=killed.append)
    make_ready("garak")
    r1 = rt.start(**start_args())
    holds[0].set()
    for _ in range(100):
        if rt.run(r1["id"])["status"] != "running":
            break
        time.sleep(0.05)
    assert rt.run(r1["id"])["status"] == "failed" and "exited with code 2" in rt.run(r1["id"])["error"]
    r2 = rt.start(**start_args())
    assert rt.cancel(r2["id"])["status"] == "cancelled" and killed == [4242]
    with pytest.raises(RedTeamError, match="not running"):
        rt.cancel(r2["id"])
    with pytest.raises(KeyError):
        rt.cancel("zzzzzzzzzz")
    holds[1].set()                                                                 # the watcher must not overwrite "cancelled"
    time.sleep(0.2)
    assert rt.run(r2["id"])["status"] == "cancelled"
    with pytest.raises(RedTeamError, match="still using it"):
        r3 = rt.start(**start_args())
        rt.uninstall("garak")
    rt.close()
    assert rt.run(r3["id"])["status"] == "interrupted"
    state.db.execute("UPDATE redteam_runs SET status='running' WHERE id=?", (r3["id"],))
    RedTeam(state.db)
    assert state.db.one("SELECT status FROM redteam_runs WHERE id=?", (r3["id"],))["status"] == "interrupted"


def test_a_tool_that_cannot_start(rthome, state):
    def popen(cmd, env, log, cwd):
        raise OSError("no such file")
    rt = RedTeam(state.db, popen=popen)
    make_ready("garak")
    with pytest.raises(RedTeamError, match="could not start garak"):
        rt.start(**start_args())
    r = rt.runs()[0]
    assert r["status"] == "failed" and "could not start" in r["error"] and not rt.targets


def test_default_popen_runs_a_real_process(rthome, tmp_path):
    log = tmp_path / "log.txt"
    p = RT._popen([sys.executable, "-c", "print('hello from the tool')"], {}, log, str(tmp_path))
    assert p.wait() == 0 and "hello from the tool" in log.read_text()


def test_api_install_runs_and_target(client, state, rthome):
    st = client.get("/api/redteam/status").json()
    assert [t["id"] for t in st["tools"]] == ["garak", "pyrit", "promptfoo"]
    assert client.get("/api/redteam/plan/garak").json()["licence"] == "Apache-2.0" and client.get("/api/redteam/plan/zzz").status_code == 422
    assert client.post("/api/redteam/install", json={"tool": "garak"}).status_code == 422                       # licence not accepted
    assert client.post("/api/redteam/uninstall", json={"tool": "garak"}).status_code == 200
    assert client.get("/api/redteam/runs").json() == {"runs": []}
    make_ready("garak")
    hold = threading.Event()
    state.redteam = RedTeam(state.db, popen=lambda cmd, env, log, cwd: FakeProc(0, hold))
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    r = client.post("/api/redteam/runs", json={"tool": "garak", "agent": "assistant", "provider": "mock", "max_requests": 4, "purpose": "test"})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert client.post("/api/redteam/runs", json={"tool": "garak", "agent": "ghost"}).status_code == 422
    assert client.get(f"/api/redteam/runs/{rid}").json()["status"] == "running" and client.get("/api/redteam/runs/zzzzzzzzzz").status_code == 404
    token = next(iter(state.redteam.targets))
    url = f"/api/redteam/target/{token}/v1/chat/completions"
    ok = client.post(url, json={"model": "assistant", "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": [{"type": "text", "text": "hello there"}]}]})
    j = ok.json()
    assert ok.status_code == 200 and j["object"] == "chat.completion" and j["choices"][0]["message"]["content"] and j["usage"]["total_tokens"] > 0
    assert state.db.one("SELECT COUNT(*) n FROM chat_sessions")["n"] == 0                                               # the probe left no chat behind
    assert client.post(url, json={"messages": [{"role": "assistant", "content": "x"}]}).status_code == 400
    assert client.post(url, json={"messages": [{"role": "user", "content": 5}]}).status_code == 400
    client.put("/api/guardrails/enabled", json={"enabled": True})
    client.put("/api/guardrails/profiles", json={"id": "strict", "name": "S", "mode": "block", "default": True, "params": {"blocklist": "forbidden"}})
    blocked = client.post(url, json={"messages": [{"role": "user", "content": "say forbidden things"}]})
    assert blocked.status_code == 200 and "I stopped here" in blocked.json()["choices"][0]["message"]["content"]
    assert state.redteam.run(rid)["blocked"] == 1 and state.redteam.run(rid)["requests"] == 4
    over = client.post(url, json={"messages": [{"role": "user", "content": "more"}]})
    assert over.status_code == 429
    assert client.post("/api/redteam/target/nope/v1/chat/completions", json={"messages": []}).status_code == 404
    assert client.post(f"/api/redteam/runs/{rid}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/redteam/runs/{rid}/cancel").status_code == 422
    assert client.post("/api/redteam/runs/zzzzzzzzzz/cancel").status_code == 404
    hold.set()


def test_target_reports_a_failing_probe(client, state, rthome, monkeypatch):
    make_ready("garak")
    state.redteam = RedTeam(state.db, popen=lambda cmd, env, log, cwd: FakeProc(0, threading.Event()))
    state.redteam.start(**start_args(agent="assistant", provider="mock", agent_exists=lambda a: True, max_requests=2))
    token = next(iter(state.redteam.targets))

    async def boom(*a, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(state.chat, "send", boom)
    r = client.post(f"/api/redteam/target/{token}/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502 and "provider down" in r.json()["error"]["message"] and state.redteam.targets[token]["errors"] == 1
