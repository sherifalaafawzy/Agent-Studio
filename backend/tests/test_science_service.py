"""Science Lab service: settings, data-source calls, investigations, evidence rules, claim labels, citation checks, ranking, reports and notebooks."""
from __future__ import annotations

import json

import pytest

from app.db import Database
from app.science import safety
from app.science.service import LABELS, ScienceError, ScienceService, claim_label, elo_update
from tests.science_fakes import make_fetcher
from tests.test_science_core import abstract


class Tel:
    def __init__(self):
        self.events = []

    def emit(self, name, **kw):
        self.events.append((name, kw))


class Art:
    def __init__(self, fail=False):
        self.saved, self.fail = [], fail

    def save_text(self, name, content, **kw):
        if self.fail:
            raise RuntimeError("artifacts are switched off in Settings")
        self.saved.append((name, content, kw))
        return {"id": f"art{len(self.saved)}", "name": name}


@pytest.fixture
def mk(tmp_path):
    def make(mode=None, artifacts=None, tel=None):
        fx, mode, clock = make_fetcher(mode)
        db = Database(tmp_path / "s.db")
        return ScienceService(db, artifacts, tel, fx, now=clock.now), mode, clock, db
    return make


def ev(**kw):
    return {"kind": kw.pop("kind", "source"), "verified": kw.pop("verified", -1), "source": kw.pop("source", "pubmed"), "url": kw.pop("url", "https://pubmed.ncbi.nlm.nih.gov/1/"), "ref": kw.pop("ref", "pmid:1"),
            "title": kw.pop("title", "A paper")}


def test_labels_are_computed_from_evidence_not_chosen():
    assert claim_label([]) == "unsupported" and claim_label([ev(verified=0)]) == "unsupported"
    assert claim_label([ev()]) == "single_source"
    assert claim_label([ev(), ev(source="europepmc", url="https://europepmc.org/a", title="Another paper")]) == "established"
    assert claim_label([ev(), ev(url="https://pubmed.ncbi.nlm.nih.gov/2/")]) == "single_source"          # the same paper twice is one work
    assert claim_label([ev(), ev(source="europepmc", url="https://europepmc.org/a", title="a PAPER.")]) == "single_source"   # the same paper found in two databases is one work
    assert claim_label([ev(title=""), ev(title="", url="", ref="pmid:2")]) == "established" and claim_label([ev(title="", url="", ref=""), ev(title="", url="", ref="")]) == "single_source"
    assert claim_label([ev(), ev(source="arxiv", url="https://arxiv.org/abs/1", title="Other")], conflicting=True) == "conflicting"
    assert claim_label([ev(kind="inference")]) == "model_inference" and claim_label([ev(kind="computed")]) == "computed"
    assert claim_label([ev(kind="inference"), ev(kind="computed")]) == "computed" and set(LABELS) >= {"established", "unsupported", "computed"}
    assert claim_label([ev(), ev(verified=0, source="x", url="https://x.org/1", title="Bad")]) == "single_source"       # a failed citation is not counted
    w, l = elo_update(1200, 1200)
    assert w == 1216 and l == 1184 and elo_update(1600, 1200)[0] < 1616


def test_config_is_validated_and_the_safety_level_only_goes_up(mk):
    s, *_ = mk()
    assert s.config()["safety_level"] == "standard" and s.config()["enabled"] is True
    assert s.set_config({"safety_level": "strict"})["safety_level"] == "strict"
    assert s.set_config({"safety_level": "standard"})["safety_level"] == "strict"      # asking for lower keeps the current level
    for patch, msg in [("x", "send an object"), ({"nope": 1}, "unknown"), ({"enabled": "yes"}, "true or false"), ({"safety_level": "off"}, "safety level"),
                       ({"disabled_connectors": ["zzz"]}, "list of data source ids"), ({"disabled_connectors": "pubmed"}, "list of data source ids"), ({"rate_overrides": []}, "object"),
                       ({"rate_overrides": {"arxiv": 1.0}}, "longer than"), ({"rate_overrides": {"zzz": 5}}, "longer than"), ({"rate_overrides": {"arxiv": True}}, "longer than"),
                       ({"rate_overrides": {"arxiv": 61}}, "longer than"), ({"contact_email": "nope"}, "name@example.org"), ({"contact_email": 5}, "name@example.org"),
                       ({"max_calls_per_investigation": 0}, "whole number"), ({"cache_minutes": True}, "whole number"), ({"max_investigations": 9999}, "whole number")]:
        with pytest.raises(ScienceError, match=msg):
            s.set_config(patch)
    ok = s.set_config({"disabled_connectors": ["arxiv", "arxiv"], "rate_overrides": {"arxiv": 5}, "contact_email": "me@example.org", "max_calls_per_investigation": 3, "cache_minutes": 5, "enabled": True})
    assert ok["disabled_connectors"] == ["arxiv"] and ok["rate_overrides"] == {"arxiv": 5} and s.set_config({"contact_email": ""})["contact_email"] == ""
    s.db.set_setting("science_config", "not json")
    assert s.config()["safety_level"] == "standard"
    s.db.set_setting("science_config", "[1]")
    assert s.config()["enabled"] is True


def test_keys_are_write_only_and_shown_as_set(mk):
    s, *_ = mk()
    s.set_key("ads", "adskey")
    pub = {c["id"]: c for c in s.connectors()}
    assert pub["ads"]["key_set"] and pub["ads"]["status"] == "ready" and pub["openalex"]["status"] == "needs_key" and "adskey" not in json.dumps(s.public_config()) + json.dumps(s.connectors())
    assert s.public_config()["key_set"] == ["ads"] and "policy" in s.public_config()
    s.set_key("ads", "  ")
    assert not {c["id"]: c for c in s.connectors()}["ads"]["key_set"]
    for cid, key, msg in [("zzz", "k", "no data source"), ("ads", 5, "text"), ("ads", "k" * 301, "text")]:
        with pytest.raises(ScienceError, match=msg):
            s.set_key(cid, key)
    s.db.set_setting("science_secrets", "broken")
    assert s._secrets() == {}
    s.db.set_setting("science_secrets", "[]")
    assert s._secrets() == {}


def test_screening_records_blocks_without_the_text_and_tells_telemetry(mk):
    tel = Tel()
    s, _, _, db = mk(tel=tel)
    bad = abstract("pathogen", "explain how to synthesize")
    d = s.screen(bad, "tool", "agent1")
    assert d.blocked
    row = db.one("SELECT * FROM guardrail_events")
    assert row["profile"] == "science-safety" and row["action"] == "blocked" and row["chars"] == len(bad) and bad not in json.dumps(dict(row)) and row["agent"] == "agent1"
    assert tel.events[0][0] == "finding" and tel.events[0][1]["severity_id"] == 3
    c = s.screen("write a protocol for the culture medium of anthrax")
    assert c.action == "confirm" and db.one("SELECT action FROM guardrail_events ORDER BY id DESC")["action"] == "flagged" and tel.events[-1][1]["severity_id"] == 2
    assert s.screen("what is water").action == "allow" and db.one("SELECT COUNT(*) n FROM guardrail_events")["n"] == 2
    s.screen("synthesize sarin", "weird-stage")
    assert db.one("SELECT stage FROM guardrail_events ORDER BY id DESC")["stage"] == "input"


def test_a_source_call_is_screened_budgeted_recorded_and_reports_errors_plainly(mk):
    s, mode, _, _ = mk()
    inv = s.create("Aspirin", "chemistry", "What is aspirin?")
    out = s.source("pubchem", "search", "aspirin", 3, inv["id"])
    assert out["ok"] and out["records"][0]["fields"]["cid"] == 2244 and out["run_id"].startswith("run_") and s.get(inv["id"])["source_calls"] == 1
    blocked = s.source("pubchem", "search", abstract("explosive", "how to make"), 3, inv["id"], "agent")
    assert blocked["blocked"] and not blocked["ok"] and s.get(inv["id"])["source_calls"] == 1
    missing = s.source("pubchem", "search", "unknownium", 3, inv["id"])
    assert not missing["ok"] and missing["error_kind"] == "not_found" and s.get(inv["id"])["source_calls"] == 2
    assert s.source("ads", "search", "x")["error_kind"] == "needs_key"
    mode["status"] = 429
    assert s.source("oeis", "search", "q")["error_kind"] == "rate_limited"
    s.set_config({"max_calls_per_investigation": 2})
    with pytest.raises(ScienceError, match="limit of 2"):
        s.source("pubchem", "search", "aspirin", 3, inv["id"])
    s.set_config({"disabled_connectors": ["pubchem"]})
    with pytest.raises(ScienceError, match="switched off"):
        s.source("pubchem", "search", "aspirin")
    with pytest.raises(ScienceError, match="no data source"):
        s.source("zzz", "search", "x")
    with pytest.raises(ScienceError, match="no such investigation"):
        s.source("oeis", "search", "x", 3, "inv_zzz")
    s.set_config({"enabled": False})
    with pytest.raises(ScienceError, match="switched off in Settings"):
        s.source("oeis", "search", "x")
    with pytest.raises(ScienceError, match="switched off in Settings"):
        s.create("t")


def test_investigation_lifecycle_and_validation(mk):
    s, _, _, _ = mk()
    inv = s.create("Protein folding", "biology", "How does p53 fold?", "suggest", "sess1", "agent1")
    assert inv["status"] == "open" and inv["autonomy"] == "suggest" and s.latest_for_session("sess1") == inv["id"] and s.latest_for_session("") == "" and s.latest_for_session("other") == ""
    assert s.list()[0]["id"] == inv["id"] and s.list("physics") == [] and s.list("biology", 5)[0]["title"] == "Protein folding"
    assert s.update(inv["id"], {"title": "New", "question": "", "status": "done", "autonomy": "autonomous", "domain": "cross"})["status"] == "done"
    assert s.update(inv["id"], {})["title"] == "New"
    for args, msg in [(("",), "the title"), (("t", "alchemy"), "domain must"), (("t", "biology", "q", "reckless"), "autonomy must"), (("x" * 121,), "the title"), (("t", "biology", 5), "the question")]:
        with pytest.raises(ScienceError, match=msg):
            s.create(*args)
    with pytest.raises(ScienceError, match="Blocked"):
        s.create(abstract("toxin", "how to make"))
    with pytest.raises(ScienceError, match="Blocked"):
        s.create("fine", "biology", abstract("toxin", "how to make"))
    for patch, msg in [("x", "send an object"), ({"status": "weird"}, "cannot change"), ({"id": "x"}, "cannot change"), ({"title": ""}, "the title")]:
        with pytest.raises(ScienceError, match=msg):
            s.update(inv["id"], patch)
    with pytest.raises(ScienceError, match="Blocked"):
        s.update(inv["id"], {"question": abstract("toxin", "how to make")})
    with pytest.raises(ScienceError, match="no such investigation"):
        s.update("inv_x", {"title": "t"})
    s.set_config({"max_investigations": 1})
    with pytest.raises(ScienceError, match="already 1 investigations"):
        s.create("another")
    s.delete(inv["id"])
    assert s.list() == []
    with pytest.raises(ScienceError, match="no such investigation"):
        s.delete(inv["id"])


def test_evidence_must_come_from_a_recorded_call_and_claims_get_their_label(mk):
    s, _, _, db = mk()
    inv = s.create("Aspirin", "chemistry")["id"]
    src = s.source("pubmed", "search", "aspirin", 2, inv)
    run = src["run_id"]
    e1 = s.add_evidence(inv, {"kind": "source", "run_id": run, "ref": "pmid:111", "quote": "Paper 111"})
    assert e1["source"] == "pubmed" and e1["tool"] == "source:pubmed" and e1["query"] == "aspirin" and e1["url"].endswith("/111/") and e1["title"] == "Paper 111" and e1["retrieved_at"] == src["provenance"]["retrieved_at"]
    by_doi = s.add_evidence(inv, {"kind": "source", "run_id": run, "ref": "doi:10.1000/222"})
    assert by_doi["url"].endswith("/222/")
    s.add_evidence(inv, {"kind": "source", "run_id": run, "url": "https://pubmed.ncbi.nlm.nih.gov/222/", "ref": "x"})
    for body, msg in [({"kind": "x"}, "kind must"), ("s", "send an object"), ({"kind": "source", "ref": "pmid:111"}, "needs the run_id"), ({"kind": "source", "run_id": run, "ref": "pmid:999999"}, "not returned"),
                      ({"kind": "source", "run_id": run}, "needs a reference"), ({"kind": "source", "run_id": "run_zzz", "ref": "pmid:111"}, "no recorded run"),
                      ({"kind": "source", "run_id": run, "ref": "pmid:111", "url": "ftp://x"}, "must start with http"), ({"kind": "computed", "run_id": run}, "not a data-source call"),
                      ({"kind": "computed"}, "needs the run_id")]:
        with pytest.raises(ScienceError, match=msg):
            s.add_evidence(inv, body)
    failed = s.source("pubchem", "search", "unknownium", 3, inv)
    assert "run_id" not in failed
    bad_run = s.record_run(inv, "source:pubchem", {"ok": False, "error": "x"})
    with pytest.raises(ScienceError, match="succeeded"):
        s.add_evidence(inv, {"kind": "source", "run_id": bad_run, "ref": "x"})
    py = s.record_run(inv, "python", {"code_sha256": "ab" * 32, "seed": 1, "output_sha256": "cd" * 32})
    comp = s.add_evidence(inv, {"kind": "computed", "run_id": py, "value": "42 J", "title": "energy"})
    assert comp["source"] == "sandbox" and comp["value"] == "42 J"
    with pytest.raises(ScienceError, match="data-source"):
        s.add_evidence(inv, {"kind": "source", "run_id": py, "ref": "x"})
    inf = s.add_evidence(inv, {"kind": "inference", "quote": "I guess"})
    assert inf["source"] == "model" and inf["run_id"] == ""
    assert len(s.evidence(inv)) == 5
    with pytest.raises(ScienceError, match="no such investigation"):
        s.record_run("inv_zzz", "python", {})
    assert s.record_run("", "python", {"a": 1}).startswith("run_")
    other = s.create("Other")["id"]
    with pytest.raises(ScienceError, match="no recorded run"):
        s.add_evidence(other, {"kind": "computed", "run_id": py})

    c1 = s.add_claim(inv, "Aspirin is C9H8O4", [e1["id"]])
    assert c1["label"] == "single_source" and c1["flags"] == [] and "Single source" in c1["label_text"]
    cross = s.source("europepmc", "search", "aspirin", 1, inv)
    e3 = s.add_evidence(inv, {"kind": "source", "run_id": cross["run_id"], "ref": "doi:10.1/epmc"})
    assert s.add_claim(inv, "two sources", [e1["id"], e3["id"]])["label"] == "established"
    assert s.add_claim(inv, "no evidence")["label"] == "unsupported"
    assert s.add_claim(inv, "disagree", [e1["id"], e3["id"]], True)["label"] == "conflicting"
    assert s.add_claim(inv, "calc", [comp["id"]])["label"] == "computed" and s.add_claim(inv, "guess", [inf["id"]])["label"] == "model_inference"
    for args, msg in [(("",), "the claim"), (("t", "notalist"), "evidence_ids"), (("t", [1]), "evidence_ids"), (("t", ["ev_zzz"]), "no such evidence"), (("t", ["ev_" + str(i) for i in range(51)]), "up to 50")]:
        with pytest.raises(ScienceError, match=msg):
            s.add_claim(inv, *args)
    assert len(s.claims(inv)) == 6
    s.delete_claim(inv, c1["id"])
    with pytest.raises(ScienceError, match="no such claim"):
        s.delete_claim(inv, c1["id"])
    assert s._evidence_row("ev_zzz") == {}


def test_citations_are_checked_and_unverifiable_ones_are_flagged_not_dropped(mk):
    s, mode, _, db = mk()
    inv = s.create("Cites")["id"]
    run = s.source("pubmed", "search", "x", 2, inv)["run_id"]
    good = s.add_evidence(inv, {"kind": "source", "run_id": run, "ref": "doi:10.1000/111"})
    pm = s.add_evidence(inv, {"kind": "source", "run_id": run, "ref": "pmid:111"})
    gone = s.add_evidence(inv, {"kind": "source", "run_id": run, "ref": "pmid:222"})
    db.execute("UPDATE science_evidence SET ref=? WHERE id=?", ("doi:10.404/missing", gone["id"]))
    plain = s.add_evidence(inv, {"kind": "inference"})
    res = {r["id"]: r for r in s.verify_citations(inv)}
    assert res[good["id"]]["verified"] == 1 and res[pm["id"]]["verified"] == 1 and res[gone["id"]]["verified"] == 0 and "could not verify" in res[gone["id"]]["note"] and plain["id"] not in res
    claim = s.add_claim(inv, "bad cite", [gone["id"]])
    assert claim["label"] == "unsupported" and "could not be verified" in claim["flags"][0]
    mode["down"] = True
    s.fx._cache.clear()
    res = {r["id"]: r for r in s.verify_citations(inv)}
    assert res[good["id"]]["verified"] == -1 and "not checked" in res[good["id"]]["note"]
    mode["down"] = False
    mode["status"] = 404
    s.fx._cache.clear()
    assert {r["verified"] for r in s.verify_citations(inv)} == {0}        # the source said "not found" for every one


def test_hypotheses_are_ranked_by_pairwise_judgements(mk):
    s, *_ = mk()
    inv = s.create("Why?")["id"]
    a, b, c = (s.add_hypothesis(inv, t)["id"] for t in ("A is the cause", "B is the cause", "C is the cause"))
    r = s.compare(inv, a, b)
    assert r[0]["id"] == a and r[0]["elo"] == 1216.0 and r[0]["wins"] == 1 and {h["id"]: h["losses"] for h in r}[b] == 1
    s.compare(inv, a, c)
    s.compare(inv, c, b)
    ranked = s.hypotheses(inv)
    assert [h["id"] for h in ranked][0] == a and ranked[-1]["id"] == b
    for args, msg in [((a, a), "two different"), ((a, "hy_zzz"), "two different")]:
        with pytest.raises(ScienceError, match=msg):
            s.compare(inv, *args)
    with pytest.raises(ScienceError, match="Blocked"):
        s.add_hypothesis(inv, abstract("toxin", "how to make"))
    with pytest.raises(ScienceError, match="the hypothesis"):
        s.add_hypothesis(inv, "")
    for i in range(27):
        s.add_hypothesis(inv, f"h{i}")
    with pytest.raises(ScienceError, match="at most 30"):
        s.add_hypothesis(inv, "one more")


def test_the_report_labels_everything_and_lists_the_limits(mk):
    s, mode, clock, db = mk()
    inv = s.create("Aspirin | study", "chemistry", "What is aspirin?")["id"]
    run = s.source("pubmed", "search", "aspirin", 1, inv)["run_id"]
    e = s.add_evidence(inv, {"kind": "source", "run_id": run, "ref": "pmid:111"})
    py = s.record_run(inv, "python", {"code_sha256": "ab" * 32, "seed": 3, "output_sha256": "cd" * 32})
    ce = s.add_evidence(inv, {"kind": "computed", "run_id": py})
    s.verify_citations(inv)
    s.add_claim(inv, "Supported claim", [e["id"], ce["id"]])
    s.add_claim(inv, "Unsupported claim")
    h1, h2 = s.add_hypothesis(inv, "H1 | x")["id"], s.add_hypothesis(inv, "H2")["id"]
    s.compare(inv, h1, h2)
    gone = s.add_evidence(inv, {"kind": "source", "run_id": run, "ref": "pmid:111"})
    db.execute("UPDATE science_evidence SET verified=0, ref='doi:10.404/x' WHERE id=?", (gone["id"],))
    s.add_claim(inv, "Cites something unverifiable", [gone["id"]])
    rep = s.report(inv)
    md = rep["markdown"]
    assert "COULD NOT VERIFY" in md and "⚠ a citation could not be verified: doi:10.404/x" in md
    assert "# Aspirin | study" in md and "Unsupported: no usable evidence" in md and "**[Single source]** Supported claim" in md and "verified" in md and "## Limits" in md and "can be bypassed" in md
    assert "H1 / x" in md and "| 1 |" in md and "code sha256 `abababababab`" in md and "not medical, legal or safety advice" in md and "What is aspirin?" in md
    assert rep["unsupported"] == 2 and rep["label_counts"]["single_source"] == 1 and rep["evidence_json"]["investigation"]["id"] == inv and len(rep["run_json"]["runs"]) == 2
    empty = s.create("Empty")["id"]
    md = s.report(empty)["markdown"]
    assert "No claims were recorded." in md and "| none |" in md and "None." in md and "Question" not in md
    assert s.stats() == {"investigations": 2, "evidence": 3, "claims": 3, "safety_events": 0}


def test_the_notebook_is_kept_as_three_artifacts(mk):
    art = Art()
    s, *_ = mk(artifacts=art)
    inv = s.create("My Study!", "physics")["id"]
    out = s.save_notebook(inv, "agent1", "sess")
    assert [f["name"] for f in out["files"]] == ["My-Study/report.md".split("/")[1], "evidence.json", "run.json"] or len(out["files"]) == 3
    assert [x[0] for x in art.saved] == ["report.md", "evidence.json", "run.json"] and art.saved[0][2]["path"] == "my-study/report.md" and art.saved[0][2]["source"] == "science" and len(out["sha256"]) == 64
    json.loads(art.saved[1][1])
    with pytest.raises(ScienceError, match="not available"):
        mk()[0].save_notebook(inv)
    s2, *_ = mk(artifacts=Art(fail=True))
    inv2 = s2.create("T")["id"]
    with pytest.raises(ScienceError, match="could not save the notebook"):
        s2.save_notebook(inv2)
    odd = s.create("!!!")["id"]
    assert s.save_notebook(odd)["files"]
    assert art.saved[-1][2]["path"].startswith(odd)
