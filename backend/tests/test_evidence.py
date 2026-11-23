"""Evidence checks on the scorecards: "attacked on purpose", "tested regularly" and "security events reach your tools" are green only when the
thing really WORKED (a finished run that sent probes / answered cases / a destination that received events), never because a record exists.
Overall and per-agent cards must agree. Fakes only."""
from __future__ import annotations

import json
import time

import pytest

from app.agents import AgentDef
from app.security import agentcard as AC
from app.security import evidence as EV
from app.security import redteam as RT
from app.security import scorecard as SC

DAY = 86400
EMAIL_LOG = ["? Work email: Warning: Detected unsettled top-level await at file:///x/entrypoint.js:90"]


@pytest.fixture
def rthome(tmp_path, monkeypatch):
    monkeypatch.setenv("REDTEAM_HOME", str(tmp_path / "rt"))
    monkeypatch.setattr(RT, "data_dir", lambda: tmp_path)
    return tmp_path


def run_row(state, rid, agent="ag", status="done", requests=5, age_days=0, tool="garak", error=""):
    t = time.time() - age_days * DAY
    state.db.execute("INSERT INTO redteam_runs(id,tool,agent,started,ended,status,requests,error) VALUES(?,?,?,?,?,?,?,?)", (rid, tool, agent, t, t, status, requests, error))


def overall(state):
    return next(c for c in SC.build(state)["checks"] if c["id"] == "redteam")


def per_agent(state, aid="ag"):
    if state.agents.get(aid) is None:
        state.agents.create(AgentDef(id=aid, name=aid))
    return next(c for c in AC.card(state, state.agents.get(aid))["checks"] if c["id"] == "redteam")


def both(state, aid="ag"):
    return overall(state), per_agent(state, aid)


def test_no_run_at_all_is_a_warning(state):
    for c in both(state):
        assert c["status"] == "warn" and "No red-team run" in c["detail"]


def test_a_failed_run_is_tried_but_failed_not_a_pass(state, rthome):
    run_row(state, "r1", status="failed", requests=0, error="the tool exited with code 1")
    for c in both(state):
        assert c["status"] == "warn" and "did not work" in c["detail"] and "0 probes sent" in c["detail"] and "exited with code 1" in c["detail"]
        assert "garak and PyRIT" in c["fix"]


def test_a_failed_promptfoo_run_gives_the_plain_reason_from_its_log(state, rthome):
    run_row(state, "r2", tool="promptfoo", status="failed", requests=0, error="the tool exited with code 1")
    d = RT.runs_dir() / "r2"
    d.mkdir(parents=True)
    (d / "log.txt").write_text("\n".join(EMAIL_LOG), encoding="utf-8")
    for c in both(state):
        assert c["status"] == "warn" and "work e-mail" in c["detail"]


def test_a_run_that_finished_but_sent_no_probes_does_not_count(state):
    run_row(state, "r1", status="done", requests=0)
    for c in both(state):
        assert c["status"] == "warn" and "finished but sent no probes" in c["detail"]


@pytest.mark.parametrize("status,words", [("cancelled", "was cancelled"), ("interrupted", "was interrupted")])
def test_cancelled_and_interrupted_runs_do_not_count(state, status, words):
    run_row(state, "r1", status=status, requests=3)
    for c in both(state):
        assert c["status"] == "warn" and words in c["detail"]


def test_a_running_run_is_not_evidence_yet(state):
    run_row(state, "r1", status="running", requests=3)
    for c in both(state):
        assert c["status"] == "warn" and "No red-team run" in c["detail"]


def test_a_successful_run_passes(state):
    run_row(state, "r1", status="done", requests=7)
    for c in both(state):
        assert c["status"] == "pass" and "1 successful" in c["detail"] and "7 probe(s)" in c["detail"]


def test_a_successful_run_beats_later_failures_but_a_later_failure_alone_does_not(state):
    run_row(state, "ok", status="done", requests=2, age_days=3)
    run_row(state, "bad", status="failed", requests=0, age_days=1)
    assert all(c["status"] == "pass" for c in both(state))


def test_a_stale_successful_run_is_out_of_date(state):
    run_row(state, "r1", status="done", requests=9, age_days=EV.REDTEAM_FRESH_DAYS + 30)
    for c in both(state):
        assert c["status"] == "warn" and "Out of date" in c["detail"] and f"{EV.REDTEAM_FRESH_DAYS + 30} days ago" in c["detail"]


def test_a_run_against_another_agent_does_not_count_for_this_one(state):
    run_row(state, "r1", agent="other", status="done", requests=4)
    state.agents.create(AgentDef(id="other", name="o"))
    assert overall(state)["status"] == "pass"
    assert per_agent(state, "ag")["status"] == "warn" and "No red-team run against this agent" in per_agent(state, "ag")["detail"]
    assert per_agent(state, "other")["status"] == "pass"


# ------------------------------------------------------------------------------------------------ evaluations
def eval_row(state, eid, agent="ag", status="done", done=3, errors=0, age_days=0, summary=None):
    state.db.execute("INSERT INTO eval_runs(id,set_id,agent,started,status,summary) VALUES(?,?,?,?,?,?)",
                     (eid, "s", agent, time.time() - age_days * DAY, status, summary if summary is not None else json.dumps({"done": done, "errors": errors})))


def evals_checks(state, aid="ag"):
    if state.agents.get(aid) is None:
        state.agents.create(AgentDef(id=aid, name=aid))
    return (next(c for c in SC.build(state)["checks"] if c["id"] == "evals"), next(c for c in AC.card(state, state.agents.get(aid))["checks"] if c["id"] == "evals"))


def test_an_evaluation_run_counts_only_when_a_case_really_answered(state):
    for c in evals_checks(state):
        assert c["status"] == "warn" and "evaluat" in c["detail"].lower()
    eval_row(state, "e1", done=4, errors=4)                                         # every case errored
    eval_row(state, "e2", status="cancelled")
    eval_row(state, "e3", status="error")
    eval_row(state, "e4", summary="not json")
    eval_row(state, "e5", summary="{}")
    for c in evals_checks(state):
        assert c["status"] == "warn" and "did not work" in c["detail"]
    eval_row(state, "e6", done=4, errors=1)
    for c in evals_checks(state):
        assert c["status"] == "pass" and "evaluation run(s)" in c["detail"]


def test_an_old_evaluation_run_does_not_count(state):
    eval_row(state, "e1", age_days=EV.EVAL_FRESH_DAYS + 5)
    for c in evals_checks(state):
        assert c["status"] == "warn"


# ------------------------------------------------------------------------------------------------ OCSF destination
def test_ocsf_that_never_delivered_is_not_green(state):
    state.db.set_setting("ocsf_config", json.dumps({"enabled": True, "sinks": [{"id": "ab", "kind": "https", "enabled": True, "level": "", "config": {"url": "https://x.example/y"}}]}))
    state.ocsf.sink_stats["ab"] = dict(sent=0, failed=4, dropped=0, last_error="HTTP 403 from the receiver", last_ok=None)
    c = next(c for c in SC.build(state)["checks"] if c["id"] == "ocsf")
    assert c["status"] == "warn" and "no destination has received an event" in c["detail"] and "403" in c["detail"]
    state.ocsf.sink_stats["ab"] = dict(sent=2, failed=4, dropped=0, last_error="", last_ok=time.time())
    assert next(c for c in SC.build(state)["checks"] if c["id"] == "ocsf")["status"] == "pass"
    state.ocsf.sink_stats["ab"] = dict(sent=0, failed=0, dropped=0, last_error="", last_ok=None)       # nothing sent yet: cannot tell, stays green
    assert next(c for c in SC.build(state)["checks"] if c["id"] == "ocsf")["status"] == "pass"
