"""promptfoo's e-mail gate: Local only mode (the attacks are written by YOUR model through a private address), the pre-flight check, the plain-language
failure reasons and the e-mail rule (Agent Studio never enters, reads out or submits an address). Fakes only: no promptfoo process is started."""
from __future__ import annotations

import threading
import time

import pytest
import yaml

from app.security import redteam as RT
from app.security.redteam import RedTeam, RedTeamError
from tests.test_security import FakeProc, make_ready, start_args

EMAIL_LOG = ["", "  Email Verification Required", "  Red team scans require email verification to continue.", "? Work email: Warning: Detected unsettled top-level await at file:///x:90"]


@pytest.fixture
def rthome(tmp_path, monkeypatch):
    monkeypatch.setenv("REDTEAM_HOME", str(tmp_path / "rt"))
    monkeypatch.setenv("PROMPTFOO_CONFIG_DIR", str(tmp_path / "pf"))
    return tmp_path


def set_account(tmp_path, **acct):
    (tmp_path / "pf").mkdir(exist_ok=True)
    (tmp_path / "pf" / "promptfoo.yaml").write_text(yaml.safe_dump({"account": acct}), encoding="utf-8")


# ------------------------------------------------------------------------------------------------ diagnosis
def test_diagnose_reads_the_tools_own_log():
    d = RT.diagnose("promptfoo", EMAIL_LOG)
    assert d["code"] == "needs_email_verification" and "work e-mail" in d["short"] and set(d["options"]) == {"local_only", "verify_yourself", "other_tools"}
    assert "cannot answer that question for you" in d["message"]
    assert RT.diagnose("garak", EMAIL_LOG) is None                                    # only promptfoo asks for an e-mail
    crash = RT.diagnose("garak", ["Traceback (most recent call last):", "  File x", "ModuleNotFoundError: no module named y"])
    assert crash["code"] == "tool_crashed"
    assert RT.diagnose("promptfoo", ["all good"]) is None and RT.diagnose("promptfoo", []) is None


def test_log_tail_and_row_tool(rthome, state):
    assert RT.log_tail("0123456789") == []
    d = RT.runs_dir() / "0123456789"
    d.mkdir(parents=True)
    (d / "log.txt").write_text("a\nb\nc\n", encoding="utf-8")
    assert RT.log_tail("0123456789", 2) == ["b", "c"]
    assert RT.row_tool(state.db, "nope") == ""
    state.db.execute("INSERT INTO redteam_runs(id,tool,agent,started,status) VALUES('x1','garak','ag',?,'done')", (time.time(),))
    assert RT.row_tool(state.db, "x1") == "garak"


# ------------------------------------------------------------------------------------------------ account state (never reads the address out)
def test_account_state_is_yes_or_no_only(rthome):
    assert RT.promptfoo_account_state() == "none"                                     # no settings file
    set_account(rthome)
    assert RT.promptfoo_account_state() == "none"                                     # no address saved
    set_account(rthome, email="someone@example.com", emailNeedsValidation=True, emailValidated=False)
    assert RT.promptfoo_account_state() == "needs_verification"
    set_account(rthome, email="someone@example.com", emailValidated=True)
    assert RT.promptfoo_account_state() == "verified"
    (rthome / "pf" / "promptfoo.yaml").write_text("- not: a mapping", encoding="utf-8")
    assert RT.promptfoo_account_state() == "none"


def test_preflight_and_the_verify_command(rthome, state):
    rt = RedTeam(state.db)
    g = rt.preflight("garak")
    assert g == dict(tool="garak", needs_email=False, local_only_available=False)
    with pytest.raises(RedTeamError, match="unknown tool"):
        rt.preflight("zzz")
    p = rt.preflight()
    assert p["needs_email"] and p["local_only_available"] and p["account"] == "none" and p["hosted_ready"] is False
    assert p["verify_command"].startswith('& "') and "redteam generate" in p["verify_command"] and "verify.yaml" in p["verify_command"]
    cfg = yaml.safe_load(open(p["verify_config"], encoding="utf-8"))
    assert cfg["redteam"]["plugins"] == ["prompt-extraction"] and "e-mail" not in str(cfg).replace("e-mail check", "")
    assert "someone@example.com" not in str(p)
    assert p["local_defaults"] == RT.PROMPTFOO_LOCAL_DEFAULTS and set(p["local_defaults"]) <= set(p["local_plugins"])
    set_account(rthome, email="someone@example.com", emailValidated=True)
    p = rt.preflight()
    assert p["account"] == "verified" and p["hosted_ready"] is True and "someone@example.com" not in str(p)


# ------------------------------------------------------------------------------------------------ the generated config
def test_local_only_config_points_the_attack_writer_at_our_private_address(tmp_path):
    spec = RT.build_run("promptfoo", "http://x/target/T/v1/chat/completions", "T", "engineer", ["pii", "prompt-extraction"], "A bot", tmp_path, tmp_path / "h",
                        gen_url="http://127.0.0.1:8765/api/redteam/gen/G/v1", gen_token="G")
    cfg = yaml.safe_load(spec["files"]["promptfooconfig.yaml"])
    assert cfg["redteam"]["provider"]["id"].startswith("openai:chat:") and cfg["redteam"]["provider"]["config"] == {"apiBaseUrl": "http://127.0.0.1:8765/api/redteam/gen/G/v1", "apiKey": "G"}
    assert cfg["defaultTest"]["options"]["provider"] == cfg["redteam"]["provider"] and cfg["redteam"]["plugins"] == ["pii", "prompt-extraction"]
    # `jailbreak` (= jailbreak:meta) only runs on promptfoo's hosted service; the tree search is kept small (its default is 25 x 4 x 250 per test)
    assert cfg["redteam"]["strategies"] == ["jailbreak-templates", {"id": "jailbreak:tree", "config": {"maxDepth": 2, "branchingFactor": 2, "maxAttempts": 4}}]
    assert spec["cmd"][-3:] == ["-j", "2", "--no-progress-bar"] and "--trace-uncaught" in spec["env"]["NODE_OPTIONS"]
    env = spec["env"]
    assert env["PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION"] == "true" and env["OPENAI_API_KEY"] == "G" and env["PROMPTFOO_DISABLE_TELEMETRY"] == "1"
    assert env["PROMPTFOO_DISABLE_UPDATE"] == "1" and env["PROMPTFOO_DISABLE_SHARING"] == "true" and "CI" not in env      # we never pretend to be a CI server to skip the e-mail check
    hosted = RT.build_run("promptfoo", "http://x/v1", "T", "engineer", ["pii"], "A bot", tmp_path, tmp_path / "h")
    assert yaml.safe_load(hosted["files"]["promptfooconfig.yaml"])["redteam"]["strategies"] == ["jailbreak", "prompt-injection"]
    assert "provider" not in yaml.safe_load(hosted["files"]["promptfooconfig.yaml"])["redteam"] and "PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION" not in hosted["env"]
    assert hosted["env"]["PROMPTFOO_DISABLE_UPDATE"] == "1"


# ------------------------------------------------------------------------------------------------ starting runs
def run_to_end(rt, rid):
    for _ in range(100):
        if rt.run(rid)["status"] != "running":
            return rt.run(rid)
        time.sleep(0.05)
    raise AssertionError("run did not end")


def test_promptfoo_defaults_to_local_only(rthome, state):
    make_ready("promptfoo")
    seen = {}

    def popen(cmd, env, log, cwd):
        seen.update(env=env)
        return FakeProc(0, threading.Event())
    rt = RedTeam(state.db, popen=popen)
    run = rt.start(**start_args(tool="promptfoo", options=None))
    assert run["options"] == RT.PROMPTFOO_LOCAL_DEFAULTS and seen["env"]["PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION"] == "true"
    g = next(iter(rt.gens.values()))
    assert g["run"] == run["id"] and g["provider"] == "mock" and g["left"] == RT.GEN_CALLS_PER_PROBE * 5 + RT.GEN_CALLS_BASE
    cfg = (RT.runs_dir() / run["id"] / "promptfooconfig.yaml").read_text(encoding="utf-8")
    assert "/api/redteam/gen/" in cfg
    rt.cancel(run["id"])
    assert rt.gens == {}                                                              # the attack writer address dies with the run


def test_local_only_refuses_risks_that_need_promptfoos_service(rthome, state):
    make_ready("promptfoo")
    rt = RedTeam(state.db, popen=lambda *a: FakeProc(0, threading.Event()))
    with pytest.raises(RedTeamError, match="hosted service.*hijacking"):
        rt.start(**start_args(tool="promptfoo", options=["pii", "hijacking"]))
    assert rt.gens == {}


def test_hosted_mode_needs_a_verified_email_the_user_gave_themselves(rthome, state):
    make_ready("promptfoo")
    seen = {}

    def popen(cmd, env, log, cwd):
        seen.update(env=env)
        return FakeProc(0, threading.Event())
    rt = RedTeam(state.db, popen=popen)
    with pytest.raises(RedTeamError, match="will not enter one for you.*redteam generate"):
        rt.start(**start_args(tool="promptfoo", local_only=False, options=["hijacking"]))
    set_account(rthome, email="someone@example.com", emailNeedsValidation=True, emailValidated=False)
    with pytest.raises(RedTeamError, match="verified work e-mail"):
        rt.start(**start_args(tool="promptfoo", local_only=False, options=["hijacking"]))
    set_account(rthome, email="someone@example.com", emailValidated=True)
    run = rt.start(**start_args(tool="promptfoo", local_only=False, options=["hijacking"]))
    assert run["status"] == "running" and rt.gens == {} and "PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION" not in seen["env"]
    assert "someone@example.com" not in str(seen["env"]) and "CI" not in seen["env"]


def test_other_tools_ignore_the_local_only_switch(rthome, state):
    make_ready("garak")
    rt = RedTeam(state.db, popen=lambda *a: FakeProc(0, threading.Event()))
    run = rt.start(**start_args(tool="garak", local_only=True))
    assert run["options"] == ["promptinject", "dan", "encoding"] and rt.gens == {}


def test_gen_calls_are_counted_and_limited(rthome, state):
    make_ready("promptfoo")
    rt = RedTeam(state.db, popen=lambda *a: FakeProc(0, threading.Event()))
    rt.start(**start_args(tool="promptfoo", max_requests=1, gen_provider="mock", gen_model="m1"))
    token = next(iter(rt.gens))
    g = rt.gen_for(token)
    assert g["calls"] == 1 and g["provider"] == "mock" and g["model"] == "m1"
    rt.gen_error(g, "model said no")
    rt.gen_error(g)
    assert g["errors"] == 2 and g["last_error"] == "model said no"
    rt.gens[token]["left"] = 0
    with pytest.raises(RedTeamError, match="limit of this run is used up"):
        rt.gen_for(token)
    with pytest.raises(RedTeamError, match="not active"):
        rt.gen_for("zzz")


def test_a_failed_promptfoo_run_says_why(rthome, state):
    make_ready("promptfoo")

    def popen(cmd, env, log, cwd):
        log.write_text("\n".join(EMAIL_LOG), encoding="utf-8")
        return FakeProc(1)
    rt = RedTeam(state.db, popen=popen)
    run = rt.start(**start_args(tool="promptfoo", options=["pii"]))
    done = run_to_end(rt, run["id"])
    assert done["status"] == "failed" and "work e-mail" in done["error"] and "exit code 1" in done["error"]
    assert done["help"]["code"] == "needs_email_verification" and "local_only" in done["help"]["options"]
    assert rt.runs()[0]["help"]["code"] == "needs_email_verification"


def test_a_failed_run_with_an_unknown_cause_keeps_the_plain_message(rthome, state):
    make_ready("garak")
    rt = RedTeam(state.db, popen=lambda cmd, env, log, cwd: FakeProc(3))
    run = rt.start(**start_args())
    done = run_to_end(rt, run["id"])
    assert done["status"] == "failed" and done["error"] == "the tool exited with code 3" and done["help"] is None


# ------------------------------------------------------------------------------------------------ the HTTP side
def test_preflight_route(client, state, rthome):
    j = client.get("/api/redteam/preflight").json()
    assert j["tool"] == "promptfoo" and j["local_only_available"] and "verify_command" in j
    assert client.get("/api/redteam/preflight", params={"tool": "garak"}).json()["needs_email"] is False
    assert client.get("/api/redteam/preflight", params={"tool": "zzz"}).status_code == 422


def test_start_route_passes_local_only(client, state, rthome):
    make_ready("promptfoo")
    state.redteam = RedTeam(state.db, popen=lambda *a: FakeProc(0, threading.Event()))
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    r = client.post("/api/redteam/runs", json={"tool": "promptfoo", "agent": "assistant", "provider": "mock", "max_requests": 2, "options": ["hijacking"], "local_only": False})
    assert r.status_code == 422 and "will not enter one for you" in r.json()["detail"]
    r = client.post("/api/redteam/runs", json={"tool": "promptfoo", "agent": "assistant", "provider": "mock", "max_requests": 2, "options": ["pii"], "local_only": "yes"})
    assert r.status_code == 200 and r.json()["options"] == ["pii"] and len(state.redteam.gens) == 1


def test_the_attack_writer_endpoint(client, state, rthome):
    make_ready("promptfoo")
    state.redteam = RedTeam(state.db, popen=lambda *a: FakeProc(0, threading.Event()))
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.post("/api/redteam/runs", json={"tool": "promptfoo", "agent": "assistant", "provider": "mock", "max_requests": 1, "options": ["pii"]})
    token = next(iter(state.redteam.gens))
    url = f"/api/redteam/gen/{token}/v1/chat/completions"
    ok = client.post(url, json={"model": "x", "max_tokens": 50, "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": [{"type": "text", "text": "write attacks"}]},
                                                                           {"role": "tool", "content": "odd role is treated as user"}]})
    j = ok.json()
    assert ok.status_code == 200 and j["object"] == "chat.completion" and j["choices"][0]["message"]["content"].startswith("[mock]") and j["usage"]["total_tokens"] >= 0
    assert client.post(url, json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": True}).status_code == 200
    assert client.post(url, json={"messages": []}).status_code == 400
    assert state.redteam.gens[token]["errors"] == 1
    assert client.post("/api/redteam/gen/nope/v1/chat/completions", json={"messages": []}).status_code == 404
    state.redteam.gens[token]["left"] = 0
    assert client.post(url, json={"messages": [{"role": "user", "content": "x"}]}).status_code == 429


def test_the_attack_writer_reports_a_failing_model(client, state, rthome, monkeypatch):
    make_ready("promptfoo")
    state.redteam = RedTeam(state.db, popen=lambda *a: FakeProc(0, threading.Event()))
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.post("/api/redteam/runs", json={"tool": "promptfoo", "agent": "assistant", "provider": "mock", "max_requests": 1, "options": ["pii"]})
    token = next(iter(state.redteam.gens))

    async def boom(*a, **k):
        raise RuntimeError("model down")
    import litellm
    monkeypatch.setattr(litellm, "acompletion", boom)
    r = client.post(f"/api/redteam/gen/{token}/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502 and "model down" in r.json()["error"]["message"] and state.redteam.gens[token]["errors"] == 1


# ------------------------------------------------------------------------------------------------ real-run lessons (garak / PyRIT / promptfoo, 2026-11)
def test_a_tool_that_exits_cleanly_without_a_single_probe_is_a_failure(rthome, state):
    make_ready("garak")
    rt = RedTeam(state.db, popen=lambda cmd, env, log, cwd: FakeProc(0))                  # garak used to "finish" after a 404 on every probe
    done = run_to_end(rt, rt.start(**start_args())["id"])
    assert done["status"] == "failed" and "without sending a single probe" in done["error"] and "attack writer" not in done["error"]


def test_a_failed_attack_writer_is_named_in_the_failure(rthome, state):
    make_ready("promptfoo")
    hold = threading.Event()
    rt = RedTeam(state.db, popen=lambda cmd, env, log, cwd: FakeProc(0, hold))
    run = rt.start(**start_args(tool="promptfoo", options=["pii"], gen_provider="vertex_ai"))
    g = rt.gen_for(next(iter(rt.gens)))
    assert g["provider"] == "vertex_ai" and g["model"] is None                          # the target's model name is never sent to another provider
    rt.gen_error(g, "BadRequestError: no such model")
    hold.set()
    done = run_to_end(rt, run["id"])
    assert done["status"] == "failed" and "attack writer (vertex_ai/default model) failed 1 of 1 calls: BadRequestError: no such model" in done["error"]


def test_the_attack_writer_keeps_the_target_model_when_it_is_the_same_provider(rthome, state):
    make_ready("promptfoo")
    rt = RedTeam(state.db, popen=lambda cmd, env, log, cwd: FakeProc(0, threading.Event()))
    rt.start(**start_args(tool="promptfoo", options=["pii"], provider="ollama", model="qwen3:8b"))
    g = next(iter(rt.gens.values()))
    assert g["provider"] == "ollama" and g["model"] == "qwen3:8b"


def test_cancel_keeps_the_counts_even_when_the_watcher_wakes_first(rthome, state):
    make_ready("garak")
    hold = threading.Event()
    rt = RedTeam(state.db, popen=lambda cmd, env, log, cwd: FakeProc(1, hold), kill=lambda pid: hold.set())   # killing the tool wakes the watcher at once
    run = rt.start(**start_args(max_requests=9))
    t = rt.target_for(next(iter(rt.targets)))
    rt.note_result(t, blocked=True)
    done = rt.cancel(run["id"])
    time.sleep(0.2)
    final = rt.run(run["id"])
    assert done["status"] == final["status"] == "cancelled" and final["requests"] == 1 and final["blocked"] == 1
    rt._finish(run["id"], "", "failed", "late")                                         # a second closer (limit stop racing a cancel) changes nothing
    assert rt.run(run["id"])["status"] == "cancelled" and rt.run(run["id"])["requests"] == 1 and rt.run(run["id"])["error"] == ""


def test_stopping_at_the_limit_without_a_process_is_safe(rthome, state):
    make_ready("garak")
    rt = RedTeam(state.db, popen=lambda cmd, env, log, cwd: FakeProc(0, threading.Event()), kill=lambda pid: (_ for _ in ()).throw(AssertionError("no kill expected")))
    run = rt.start(**start_args(max_requests=1))
    rt.procs.clear()
    rt.note_result(rt.target_for(next(iter(rt.targets))))
    assert rt.run(run["id"])["status"] == "done"
