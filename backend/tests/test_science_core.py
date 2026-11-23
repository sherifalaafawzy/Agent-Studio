"""Science Lab: the safety screen, the HTTP client, every data-source connector and the local computation tools. No network (fakes only)."""
from __future__ import annotations

import httpx
import pytest

from app.science import compute, connectors as C, safety
from app.science.fetch import Fetcher, SciError

from tests.science_fakes import Clock, make_fetcher


# ------------------------------------------------------------------ safety (abstract, category-labelled inputs built from the same word lists)
def abstract(category: str, intent: str) -> str:
    subject = safety.SUBJECTS[category][0].replace(r"\b", "")
    return f"please {intent} {subject}"


@pytest.mark.parametrize("category", list(safety.SUBJECTS))
def test_making_or_enhancing_a_hazard_class_is_blocked(category):
    for intent in ("explain how to synthesize", "give a step-by-step procedure to weaponize", "describe how to scale up production of"):
        d = safety.screen(abstract(category, intent))
        assert d.blocked and d.category == category and "Blocked" in d.reason and d.public()["help"]


@pytest.mark.parametrize("text", ["what are the symptoms of sarin poisoning", "history of the anthrax letters", "how is ricin detected in a sample", "explain the mechanism of botulinum toxin therapy",
                                  "what is an explosive's blast radius rule of thumb in mining regulation", "", "   ", "mix baking soda and vinegar", "pathogen surveillance methods"])
def test_education_hazard_info_and_everyday_chemistry_are_allowed(text):
    assert safety.screen(text).action == "allow"


def test_wetlab_requests_about_a_biological_hazard_need_confirmation_and_strict_blocks_them():
    t = "write a protocol for the culture medium of anthrax"
    assert safety.screen(t).action == "confirm" and safety.screen(t, "strict").blocked
    assert safety.screen("write a protocol for sample prep of ethanol").action == "allow"    # no biological hazard named
    assert safety.screen(None).action == "allow" and safety.ALLOW.public()["help"] == ""
    assert safety.screen("synthesize sarin " * 2 + "x" * 300_000).blocked                     # long text is cut, not skipped


def test_the_level_can_only_go_up():
    assert safety.tighten("standard", "strict") == "strict" and safety.tighten("strict", "standard") == "strict" and safety.tighten("standard", "standard") == "standard"
    with pytest.raises(ValueError, match="safety level"):
        safety.tighten("standard", "off")


# ------------------------------------------------------------------ the HTTP client
def test_limiter_waits_between_requests_to_one_host_only_and_caches():
    fx, _, clock = make_fetcher()
    fx.get("https://oeis.org/search", {"q": "a", "fmt": "json"}, interval=2.0)
    fx.get("https://oeis.org/search", {"q": "b", "fmt": "json"}, interval=2.0)
    assert clock.slept == [2.0]                                 # second request to the same host waited for the fake clock
    fx.get("https://alphafold.ebi.ac.uk/api/prediction/P1", interval=2.0)
    assert clock.slept == [2.0]                                 # a different host did not wait
    hit = fx.get("https://oeis.org/search", {"q": "a", "fmt": "json"}, interval=2.0)
    assert hit.cached and fx.calls == 3
    clock.t += 7200
    assert not fx.get("https://oeis.org/search", {"q": "a", "fmt": "json"}, interval=2.0, ttl=3600).cached
    assert not fx.get("https://oeis.org/search", {"q": "a", "fmt": "json"}, interval=0, cache=False).cached


def test_retry_after_is_honoured_then_a_plain_message_is_given():
    fx, mode, clock = make_fetcher({"status": 429, "headers": {"retry-after": "5"}})
    with pytest.raises(SciError) as e:
        fx.get("https://oeis.org/search", interval=0.1)
    assert e.value.kind == "rate_limited" and "rate-limiting" in str(e.value) and 5.0 in clock.slept and fx.calls == 3
    mode["headers"] = {"retry-after": "soon"}
    fx2, mode2, clock2 = make_fetcher({"status": 503, "headers": {"retry-after": "soon"}}, retries=1)
    with pytest.raises(SciError):
        fx2.get("https://oeis.org/search", interval=0.1)
    assert fx2.calls == 2 and max(clock2.slept) <= 30


@pytest.mark.parametrize("status,kind", [(401, "needs_key"), (403, "needs_key"), (404, "not_found"), (500, "error")])
def test_other_statuses_become_typed_errors(status, kind):
    fx, _, _ = make_fetcher({"status": status})
    with pytest.raises(SciError) as e:
        fx.get("https://oeis.org/search")
    assert e.value.kind == kind


def test_unreachable_oversized_and_non_json_answers():
    fx, mode, _ = make_fetcher({"down": True})
    with pytest.raises(SciError) as e:
        fx.get("https://oeis.org/search")
    assert e.value.kind == "unreachable" and "oeis.org" in str(e.value)
    big = Fetcher(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 2_100_000)), sleep=lambda s: None)
    with pytest.raises(SciError, match="larger than"):
        big.get("https://example.org/x")
    plain = Fetcher(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="not json")), sleep=lambda s: None)
    with pytest.raises(SciError, match="not JSON"):
        plain.get("https://example.org/x").json()


def test_cache_is_bounded():
    fx = Fetcher(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="{}")), sleep=lambda s: None)
    for i in range(505):
        fx.get("https://example.org/x", {"i": i}, interval=0)
    assert len(fx._cache) <= 501


# ------------------------------------------------------------------ connectors
def ask(cid, op, arg, **kw):
    fx, mode, clock = make_fetcher()
    return C.query(fx, cid, op, arg, **kw)


def test_pubmed_search_and_lookup_and_empty_and_broken_records():
    out = ask("pubmed", "search", "p53", limit=2, key="k")
    r = out["records"][0]
    assert r["id"] == "111" and r["fields"]["doi"] == "10.1000/111" and r["url"].endswith("/111/") and out["provenance"]["connector"] == "pubmed" and out["provenance"]["requests"] == 2
    assert ask("pubmed", "search", "nothing")["records"] == [] and "No data found" in ask("pubmed", "search", "nothing")["provenance"]["note"]
    assert [x["id"] for x in ask("pubmed", "lookup", "111")["records"]] == ["111"]
    assert ask("pubmed", "lookup", "999")["records"] == []         # the service answered with an error row for that id


def test_each_other_source_returns_normalised_records():
    assert ask("europepmc", "search", "x")["records"][0]["fields"]["doi"] == "10.1/epmc"
    up = ask("uniprot", "search", "p53")["records"]
    assert up[0]["fields"]["gene"] == "TP53" and up[0]["title"] == "Cellular tumor antigen p53" and up[1]["title"] == "Sub name" and "393 aa" in up[0]["summary"]
    assert ask("uniprot", "lookup", "P04637")["records"][0]["fields"]["accession"] == "P04637"
    af = ask("alphafold", "lookup", "P04637")["records"][0]
    assert af["fields"]["mean_plddt"] == 76.9 and "pLDDT" in af["summary"] and af["kind"] == "structure_prediction"
    assert ask("rcsb", "search", "p53")["records"][0]["id"] == "1TUP" and len(ask("rcsb", "search", "p53")["records"]) == 1
    rc = ask("rcsb", "lookup", "1tup")["records"][0]
    assert rc["fields"]["resolution_angstrom"] == 2.2 and "X-RAY" in rc["summary"]
    pc = ask("pubchem", "search", "aspirin")["records"][0]
    assert pc["fields"]["formula"] == "C9H8O4" and pc["fields"]["smiles"].startswith("CC(=O)") and ask("pubchem", "lookup", "2244")["records"][0]["id"] == "2244"
    assert "2244" in pc["url"]
    ax = ask("arxiv", "search", "black holes")["records"][0]
    assert ax["id"] == "2101.00001v1" and ax["title"] == "A fixture paper about black holes" and "Ada Fixture, Bo Test" in ax["summary"]
    assert ask("inspire", "search", "higgs")["records"][0]["fields"]["arxiv_id"] == "1207.7214"
    assert ask("ads", "search", "star", key="adskey")["records"][0]["fields"]["bibcode"] == "2020ApJ...1A"
    assert ask("oeis", "search", "fibonacci")["records"][0]["id"] == "A000045" and ask("oeis", "lookup", "A000045")["records"][0]["fields"]["terms"].startswith("0,1")
    assert ask("oeis", "search", "other")["records"][0]["id"] == "A000045"            # the older {"results": [...]} shape
    wa = ask("wolfram", "search", "integrate x^2", key="wolfkey")
    assert "x^3/3" in wa["records"][0]["summary"] and C.SPECS["wolfram"].cache is False
    oa = ask("openalex", "search", "q", key="k")["records"][0]
    assert oa["fields"]["doi"] == "10.1/oa" and oa["fields"]["cited_by"] == 5
    assert ask("crossref", "lookup", "10.1000/111", email="me@example.org")["records"][0]["fields"]["year"] == 2018
    assert ask("crossref", "search", "q")["records"][0]["title"] == "A Crossref paper"


def test_arxiv_refuses_a_document_type_declaration_and_broken_xml():
    with pytest.raises(SciError, match="document type"):
        ask("arxiv", "search", "dtd")
    with pytest.raises(SciError, match="not valid XML"):
        ask("arxiv", "search", "broken")


def test_query_refuses_unknown_sources_operations_missing_keys_and_bad_input():
    for args, kind in [(("nope", "search", "x"), "not_found"), (("alphafold", "search", "x"), "error"), (("ads", "search", "x"), "needs_key"), (("pubmed", "search", ""), "error"), (("pubmed", "search", "x" * 501), "error")]:
        with pytest.raises(SciError) as e:
            ask(*args)
        assert e.value.kind == kind
    assert C._limit(None) == 5 and C._limit(500) == C.MAX_LIMIT and C._limit(0) == 5 and C._limit(-3) == 1
    assert C._clip("a" * 500, 10).endswith("…")


def test_specs_describe_state_and_every_spec_has_an_implementation():
    assert set(C.SPECS) == set(C.FUNCS) and set(C.DEFAULT_INTERVALS) == set(C.SPECS)
    s = C.SPECS["ads"]
    assert s.public(False, True, 1.0)["status"] == "needs_key" and s.public(True, True, 1.0)["status"] == "ready" and s.public(True, False, 1.0)["status"] == "off"
    assert all(sp.domain in C.DOMAINS for sp in C.SPECS.values()) and C.SPECS["arxiv"].interval == 3.0 and C.SPECS["pubchem"].interval <= 0.25


# ------------------------------------------------------------------ computation
def test_constants_have_sources_and_exact_flags():
    c = compute.constant("c")
    assert c["value"] == 299792458.0 and c["exact"] and "CODATA" in c["source"]
    assert compute.constant("K_b".replace("K_b", "k_b"))["symbol"] == "k_B" and compute.constant("G")["uncertainty"] > 0
    with pytest.raises(ValueError, match="unknown constant"):
        compute.constant("zzz")


def test_unit_conversion_is_dimension_checked():
    assert compute.convert(1, "eV", "J")["result"] == pytest.approx(1.602176634e-19)
    assert compute.convert(1, "km/h", "m/s")["result"] == pytest.approx(1 / 3.6)
    assert compute.convert(1, "atm", "kPa")["result"] == pytest.approx(101.325)
    assert compute.convert(2, "kg*m^2/s^2", "J")["result"] == pytest.approx(2)
    assert compute.convert(8.314, "J/(mol*K)", "cal/mol/K")["result"] == pytest.approx(8.314 / 4.184)
    assert compute.convert(1, "1/s", "Hz")["result"] == pytest.approx(1) and compute.convert(1, "ug", "kg")["result"] == pytest.approx(1e-9)
    assert compute.convert(0, "degC", "K")["result"] == pytest.approx(273.15) and compute.convert(212, "degF", "degC")["result"] == pytest.approx(100)
    assert compute.convert(300, "K", "degF")["result"] == pytest.approx(80.33, abs=0.01)
    for bad, msg in [(("J", "m"), "different things"), (("foo", "m"), "unknown unit"), (("m/", "m"), "ends too early"), (("(m", "m"), "not closed"), (("m^x", "m"), "whole number"),
                     (("m)", "m"), "unexpected"), (("", "m"), "write a unit"), (("m²", "m"), "unexpected"), (("m^", "m"), "whole number")]:
        with pytest.raises(ValueError, match=msg):
            compute.convert(1, *bad)
    with pytest.raises(ValueError, match="finite"):
        compute.convert(float("inf"), "m", "m")
    with pytest.raises(ValueError, match="finite"):
        compute.convert(True, "m", "m")
    with pytest.raises(ValueError, match="write a unit"):
        compute.parse_units("m" * 100)
    with pytest.raises(ValueError, match="unexpected"):
        compute.parse_units("*m")
    assert compute.parse_units("m ")[1][0] == 1                 # trailing spaces are ignored
    assert compute.parse_units("2*m")[0] == 2.0 and compute.parse_units("m^-1")[1][0] == -1


def test_calculator_evaluates_safely():
    assert compute.calc("2*pi*r", {"r": 2}) == pytest.approx(12.566370614)
    assert compute.calc("sqrt(16) + factorial(5) + 7 // 2 + 7 % 3 - -1") == 4 + 120 + 3 + 1 + 1
    assert compute.calc("2**10") == 1024 and compute.calc("+3") == 3 and compute.calc("max(1, 5, 3)") == 5
    for bad, msg in [("__import__('os')", "only numbers"), ("open('x')", "only numbers"), ("x.y", "only numbers"), ("1 +", "not a valid"), ("", "1 to"), ("y", "unknown name"), ("2**100000", "too large"),
                     ("10**999", "too large"), ("1/0", "division by zero"), ("log(-1)", "cannot compute"), ("factorial(5000)", "limited"), ("'a'", "only numbers"), ("True", "only numbers"),
                     ("1 << 2", "not allowed"), ("sqrt(-1)", "cannot compute"), ("1 if 1 else 2", "only numbers"), ("a" * 1001, "1 to"), ("+".join(["1"] * 400), "too long"), ("exp(1000)", "cannot compute"),
                     ("1e308*10", "not a finite"), ("[1]", "only numbers"), ("sqrt(1, 2)", "cannot compute"), ("sin(x=1)", "only numbers")]:
        with pytest.raises(compute.CalcError, match=msg):
            compute.calc(bad)
    assert compute.calc("x*2", {"x": 3, "bad": "str", "flag": True}) == 6


def test_numeric_calculus():
    assert compute.numeric("integrate", "x**2", "x", 0, 3)["result"] == pytest.approx(9.0)
    assert compute.numeric("integrate", "sin(x)", "x", 0, 3.141592653589793, n=7)["result"] == pytest.approx(2.0, abs=1e-3)
    assert compute.numeric("derivative", "x**3", "x", 2)["result"] == pytest.approx(12.0, abs=1e-4)
    assert compute.numeric("root", "x**2-2", "x", 0, 2)["result"] == pytest.approx(2 ** 0.5)
    assert compute.numeric("root", "x", "x", 0, 2)["result"] == 0 and compute.numeric("root", "x-2", "x", 0, 2)["result"] == 2 and compute.numeric("root", "x-1", "x", 0, 2)["result"] == pytest.approx(1)
    assert compute.numeric("root", "x-1", "x", 0, 3)["result"] == pytest.approx(1)
    t = compute.numeric("table", "k*x", "x", 0, 1, {"k": 3}, n=5)
    assert t["x"] == [0.0, 0.25, 0.5, 0.75, 1.0] and t["y"][-1] == 3.0
    for args, msg in [(("root", "x**2+1", "x", 0, 2), "same sign"), (("fly", "x"), "op must be"), (("integrate", "x", "1bad"), "short word"), (("integrate", "x", "x", float("nan"), 1), "finite")]:
        with pytest.raises(compute.CalcError, match=msg):
            compute.numeric(*args)


def test_formula_mass():
    g = compute.formula_mass("C6H12O6")
    assert g["molar_mass_g_per_mol"] == pytest.approx(180.156, abs=0.01) and g["atoms"] == {"C": 6, "H": 12, "O": 6} and sum(g["percent"].values()) == pytest.approx(100, abs=0.01)
    assert compute.formula_mass("Ca(OH)2")["molar_mass_g_per_mol"] == pytest.approx(74.092, abs=0.01)
    assert compute.formula_mass("CuSO4.5H2O")["molar_mass_g_per_mol"] == pytest.approx(249.677, abs=0.01)
    assert compute.formula_mass("Mg(OH)2 · 2H2O".replace(" ", ""))["atoms"]["H"] == 6 and compute.formula_mass("Al2(SO4)3")["atoms"]["O"] == 12
    for bad, msg in [("", "write a formula"), ("Xx2", "not in the table"), ("C6(H", "not closed"), ("C6)H", "no opening"), ("C6h", "cannot read"), ("$", "write a formula"), ("2", "cannot read|no atoms"), ("0H", "no atoms")]:
        with pytest.raises(ValueError, match=msg):
            compute.formula_mass(bad)


def test_sequence_statistics():
    s = compute.sequence_stats("atg gcc\ntaa")
    assert s["length"] == 9 and s["translation_frame1"] == "MA*" and s["gc_fraction"] == pytest.approx(4 / 9, abs=1e-3) and s["reverse_complement"] == "TTAGGCCAT"
    assert len(compute.CODONS) == 64 and compute.CODONS["TGG"] == "W" and compute.CODONS["CAT"] == "H" and compute.CODONS["AAA"] == "K" and compute.CODONS["GGG"] == "G" and compute.CODONS["TTT"] == "F"
    assert compute.sequence_stats("ANNGGG")["translation_frame1"].startswith("X")
    for bad in ("", "ACGX", "A" * 100_001):
        with pytest.raises(ValueError, match="DNA or RNA"):
            compute.sequence_stats(bad)


def test_dispatch_covers_every_kind():
    assert compute.dispatch("calc", "1+1")["result"] == 2 and compute.dispatch("convert", "1", unit_from="m", unit_to="cm")["result"] == pytest.approx(100)
    assert compute.dispatch("constant", "h")["exact"] and compute.dispatch("formula_mass", "H2O")["atoms"]["H"] == 2 and compute.dispatch("sequence", "ATG")["length"] == 3
    assert compute.dispatch("integrate", "x", a=0, b=2)["result"] == pytest.approx(2.0) and compute.dispatch("table", "x", a=0, b=1)["x"][0] == 0
    for kind, args in [("nope", ()), ("convert", ("abc",))]:
        with pytest.raises(ValueError):
            compute.dispatch(kind, *args)


class FakeSandbox:
    kind, network = "docker-container", False

    def __init__(self, **res):
        self.res, self.commands = {"exit_code": 0, "stdout": "42\n", "stderr": "", "timed_out": False, **res}, []

    def exec(self, command, timeout_s=60):
        self.commands.append((command, timeout_s))
        return self.res


def test_python_runs_only_in_the_sandbox_with_a_reproducibility_record():
    sb = FakeSandbox()
    ticks = iter([10.0, 10.5])
    out = compute.run_python(sb, "print(INPUTS['x'] * 2)", {"x": 21}, seed=7, now=lambda: next(ticks))
    assert out["ok"] and out["stdout"] == "42\n" and out["record"]["seed"] == 7 and out["record"]["inputs"] == {"x": 21} and len(out["record"]["code_sha256"]) == 64 and out["record"]["duration_s"] == 0.5
    assert out["record"]["output_sha256"] and "network off" in out["record"]["environment"] and sb.commands[0][0].startswith("python3 -c")
    bad = compute.run_python(FakeSandbox(exit_code=1, stderr="Traceback"), "1/0")
    assert not bad["ok"] and bad["exit_code"] == 1
    assert not compute.run_python(FakeSandbox(timed_out=True, exit_code=-1), "while True: pass")["ok"]
    blocked = compute.run_python(sb, "# " + abstract("toxin", "synthesize"))
    assert blocked["blocked"] and blocked["safety"]["action"] == "block" and len(sb.commands) == 1
    for code, inputs, msg in [("", {}, "1 to"), ("x" * 20_001, {}, "1 to"), ("print(1)", {"a": object()}, "plain JSON"), ("print(1)", {"a": "x" * 100_001}, "too large")]:
        with pytest.raises(ValueError, match=msg):
            compute.run_python(sb, code, inputs)
