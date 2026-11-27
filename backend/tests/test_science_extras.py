"""Science Lab extras: the ready-made evaluation sets and the science checks on the security scorecard."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

from app.evals import assertions as A
from app.evals.starters import starters
from app.science import safety
from app.security import scorecard


def test_ready_made_sets_are_valid_and_match_the_safety_screen():
    sets = {s["id"]: s for s in starters()}
    assert set(sets) == {"science-citations", "science-refusal"}
    for s in sets.values():
        for c in s["cases"]:
            assert [A.clean(a) for a in c["expect"]] == c["expect"]
    refusal = sets["science-refusal"]["cases"]
    blocked = [c for c in refusal if safety.screen(c["prompt"]).action == "block"]
    assert len(blocked) == 4 and all(c["expect"][0]["type"] == "regex" for c in blocked)              # what the screen blocks, the agent must refuse
    assert all(safety.screen(c["prompt"]).action == "allow" for c in refusal if c not in blocked)       # and what it allows must not be over-refused
    cite = {c["id"]: c for c in sets["science-citations"]["cases"]}
    assert A.check_exact(cite["watson_crick"]["expect"][0], "doi: 10.1038/171737a0")[0]
    assert A.check_exact(cite["invented_paper"]["expect"][0], "I could not find such a paper; it does not exist.")[0]
    assert not A.check_exact(cite["invented_paper"]["expect"][1], "It is 10.1038/fake123")[0]
    assert A.check_exact(refusal[0]["expect"][0], "I can't help with that.")[0] and not A.check_exact(refusal[-1]["expect"][0], "I cannot answer")[0]


def test_ready_made_sets_api(client):
    got = client.get("/api/evals/starters").json()["starters"]
    assert [s["id"] for s in got] == ["science-citations", "science-refusal"] and not any(s["added"] for s in got) and got[0]["cases"] == 6
    r = client.post("/api/evals/starters/science-refusal")
    assert r.status_code == 200 and r.json()["case_count"] == 7
    assert client.get("/api/evals/starters").json()["starters"][1]["added"] is True
    assert client.post("/api/evals/starters/nope").status_code == 404


def test_science_checks_on_the_scorecard(client, state):
    card = client.get("/api/security/scorecard").json()
    sci = card["science"]
    assert sci["enabled"] is True and [c["id"] for c in sci["checks"]] == ["science-safety", "science-compute", "science-budget", "science-citations-eval", "science-refusal-eval"]
    st = {c["id"]: c["status"] for c in sci["checks"]}
    assert st == {"science-safety": "warn", "science-compute": "pass", "science-budget": "pass", "science-citations-eval": "warn", "science-refusal-eval": "warn"}
    assert sum(sci["counts"].values()) == 5
    client.put("/api/science/config", json={"safety_level": "strict", "max_calls_per_investigation": 500})
    state.db.set_setting("sandbox_network", "1")
    client.post("/api/evals/starters/science-citations")
    state.db.execute("INSERT INTO eval_runs(id,set_id,agent,provider,started,status,summary) VALUES('r1','science-citations','a','p',?,'done',?)", (time.time(), json.dumps({"done": 2, "errors": 0})))
    state.db.execute("INSERT INTO eval_runs(id,set_id,agent,provider,started,status,summary) VALUES('r2','science-refusal','a','p',?,'done','not json')", (time.time(),))
    state.db.execute("INSERT INTO eval_runs(id,set_id,agent,provider,started,status,summary) VALUES('r3','science-refusal','a','p',?,'done',?)", (time.time(), json.dumps({"done": 1, "errors": 1})))
    st = {c["id"]: c["status"] for c in client.get("/api/security/scorecard").json()["science"]["checks"]}
    assert st == {"science-safety": "pass", "science-compute": "warn", "science-budget": "warn", "science-citations-eval": "pass", "science-refusal-eval": "warn"}
    md = client.get("/api/security/scorecard", params={"format": "md"}).text
    assert "## Science Lab" in md and "**PASS** The science safety screen" in md and "Fix: Lower 'calls per investigation'" in md
    client.put("/api/science/config", json={"enabled": False})
    card = client.get("/api/security/scorecard").json()
    assert card["science"] == dict(enabled=False, checks=[], note="The Science Lab is switched off, so these checks do not apply.")
    assert "## Science Lab" not in scorecard.to_markdown(card)
    assert scorecard.science_checks(SimpleNamespace())["enabled"] is False                             # a state without the lab at all
