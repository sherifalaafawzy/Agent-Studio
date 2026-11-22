"""The local decision-model MCP server (app/mcp_d1.py) and the weight fetcher (app/d1_fetch.py).

No real model, torch or network is used: a fake model object is injected through `mcp_d1._loader`, and the real loader is
exercised against fake `torch` / `transformers` modules.
"""
import asyncio
import json
import os
import runpy
import sys
import types

import pytest

from app import d1_fetch, d1_spec
from app import mcp_d1 as M


def _restore_env_after(monkeypatch, *names):
    """Variables the code under test sets in os.environ must not leak into other tests."""
    for n in names:
        monkeypatch.setenv(n, "x")
        monkeypatch.delenv(n)


class FakeModel:
    """Records every call; answers by question type like the real model's documented output shapes."""

    def __init__(self):
        self.calls = []
        self.fail = None

    def system_one(self, state, questions, media=None, **kw):
        self.calls.append(dict(state=state, questions=questions, media=media, kw=kw))
        if self.fail:
            raise self.fail
        answers = {}
        for name, q in questions.items():
            t = q["type"]
            if t == "noul":
                answers[name] = {"noul": 0.93}
            elif t == "choice" and q.get("multiple"):
                answers[name] = {"choices": list(q["criteria"])[:1], "probabilities": {k: 0.5 for k in q["criteria"]}}
            elif t == "choice":
                first = next(iter(q["criteria"]))
                answers[name] = {"choice": first, "confidence": 0.8, "probabilities": {first: 0.8}}
            elif t == "score":
                answers[name] = {"score": 2.0, "confidence": 0.7, "probabilities": {}, "legend": {str(i): l for i, l in enumerate(q["criteria"])},
                                 "interval": (1, 3)}
            else:
                answers[name] = {t: "ACME" if t == "text" else 12.5, "span": [0, 4], "found": 0.99}
        return {"answers": answers, "usage": {"input_tokens": 12, "output_tokens": 0}}


@pytest.fixture
def model(monkeypatch):
    fm = FakeModel()
    monkeypatch.setattr(M, "_loaded", None)
    monkeypatch.setattr(M, "_loader", lambda: M.Loaded(fm, "cuda", "bfloat16"))
    return fm


def test_decide_runs_all_questions_in_one_pass_and_reports_placement(model):
    qs = {"refund": {"type": "noul", "instructions": "Is this a refund request?"},
          "team": {"type": "choice", "instructions": "Which team?", "criteria": {"billing": "money", "tech": "faults"}},
          "urgency": {"type": "score", "instructions": "How urgent?", "criteria": ["low", "medium", "high"]}}
    r = M.decide("I was charged twice", qs)
    assert r["ok"] is True
    assert r["answers"]["refund"] == {"noul": 0.93}
    assert r["answers"]["team"]["choice"] == "billing"
    assert r["answers"]["urgency"]["interval"] == [1, 3]                         # tuples become JSON lists
    assert r["usage"] == {"input_tokens": 12, "output_tokens": 0}
    assert r["meta"]["device"] == "cuda" and r["meta"]["dtype"] == "bfloat16" and r["meta"]["loaded_now"] is True
    assert r["meta"]["latency_ms"] >= 0 and "load_seconds" in r["meta"] and r["meta"]["revision"] == d1_spec.REVISION[:12]
    assert len(model.calls) == 1 and model.calls[0]["questions"] is qs           # ONE forward pass for all questions
    json.dumps(r)                                                                  # the whole result is JSON-safe
    r2 = M.decide({"subject": "hi"}, {"q": {"type": "noul", "instructions": "x?"}}, set_size=8)
    assert r2["meta"]["loaded_now"] is False and "load_seconds" not in r2["meta"]    # kept warm
    assert model.calls[1]["kw"] == {"set_size": 8} and model.calls[1]["media"] is None and model.calls[1]["state"] == {"subject": "hi"}


@pytest.mark.parametrize("state,questions,fragment", [
    (None, {"q": {"type": "noul", "instructions": "x"}}, "state is required"),
    ("x" * (M.MAX_STATE_CHARS + 1), {"q": {"type": "noul", "instructions": "x"}}, "too large"),
    ({"a": {1, 2}}, {"q": {"type": "noul", "instructions": "x"}}, "JSON-serialisable"),
    ("t", {}, "non-empty object"),
    ("t", ["a"], "non-empty object"),
    ("t", {f"q{i}": {"type": "noul", "instructions": "x"} for i in range(M.MAX_QUESTIONS + 1)}, "at most"),
    ("t", {"": {"type": "noul", "instructions": "x"}}, "question names"),
    ("t", {"q": "is it?"}, "type must be one of"),
    ("t", {"q": {"type": "essay", "instructions": "x"}}, "type must be one of"),
    ("t", {"q": {"type": "noul"}}, "instructions"),
    ("t", {"q": {"type": "noul", "instructions": "  "}}, "instructions"),
    ("t", {"q": {"type": "noul", "instructions": "x" * 2001}}, "too long"),
    ("t", {"q": {"type": "choice", "instructions": "x", "criteria": {"only": "one"}}}, "at least 2 options"),
    ("t", {"q": {"type": "choice", "instructions": "x", "criteria": ["a", "b"]}}, "at least 2 options"),
    ("t", {"q": {"type": "score", "instructions": "x", "criteria": ["a"]}}, "2 to 10"),
    ("t", {"q": {"type": "score", "instructions": "x", "criteria": [str(i) for i in range(11)]}}, "2 to 10"),
    ("t", {"q": {"type": "box", "instructions": "where"}}, "need image_paths"),
])
def test_bad_input_is_reported_not_raised_and_never_loads_the_model(model, state, questions, fragment):
    r = M.decide(state, questions)
    assert r["ok"] is False and fragment in r["error"]
    assert model.calls == [] and M._loaded is None


def test_set_size_is_validated(model):
    q = {"q": {"type": "noul", "instructions": "x"}}
    for bad in (0, 65, True, "8"):
        assert "set_size" in M.decide("t", q, set_size=bad)["error"]
    assert M.decide("t", q, set_size=64)["ok"] is True


def test_model_failures_become_error_results_and_are_not_cached(monkeypatch, model):
    q = {"q": {"type": "noul", "instructions": "x"}}
    model.fail = ValueError("bad question")
    r = M.decide("t", q)
    assert r == {"ok": False, "error": "decision failed: ValueError: bad question"}
    model.fail = None
    assert M.decide("t", q)["ok"] is True

    monkeypatch.setattr(M, "_loaded", None)
    monkeypatch.setattr(M, "_loader", lambda: (_ for _ in ()).throw(RuntimeError("out of memory")))
    r = M.decide("t", q)
    assert r["ok"] is False and "decision model unavailable: RuntimeError: out of memory" in r["error"] and M._loaded is None
    monkeypatch.setattr(M, "_loader", lambda: M.Loaded(model, "cpu", "float32"))            # next call retries the load
    assert M.decide("t", q)["meta"]["device"] == "cpu"


def test_plain_makes_model_output_json_safe():
    class Scalar:
        def item(self):
            return 0.25

    class Weird:
        def __str__(self):
            return "weird"

    out = M._plain({"a": (1, 2), 3: [float("nan"), float("inf"), 1.5], "t": Scalar(), "w": Weird(), "n": None, "b": True})
    assert out == {"a": [1, 2], "3": [None, None, 1.5], "t": 0.25, "w": "weird", "n": None, "b": True}


# ---------------------------------------------------------------- convenience wrappers

def test_classify_accepts_a_label_list_or_described_options(model):
    r = M.classify("My parcel never came", ["shipping", "billing"])
    assert r["ok"] and r["choice"] == "shipping" and r["confidence"] == 0.8 and r["meta"]["device"] == "cuda"
    assert model.calls[0]["questions"]["q"]["criteria"] == {"shipping": "shipping", "billing": "billing"}
    r = M.classify("x", {"a": "alpha things", "b": "beta things"}, question="Which?", multiple=True)
    assert r["choices"] == ["a"] and model.calls[1]["questions"]["q"]["multiple"] is True
    assert model.calls[1]["questions"]["q"]["instructions"] == "Which?"
    for bad in ("a,b", ["only"], {"a": "x"}, ["a", ""], None):
        assert M.classify("x", bad)["ok"] is False


def test_yes_no_returns_probability_and_decision(model):
    r = M.yes_no("Give me my money back", "Is it a refund request?")
    assert r["ok"] and r["probability"] == 0.93 and r["answer"] is True
    model.system_one = lambda *a, **k: {"answers": {"q": {"noul": 0.2}}}
    assert M.yes_no("t", "q?")["answer"] is False
    model.system_one = lambda *a, **k: {"answers": {}}                    # a model that answered nothing
    r = M.yes_no("t", "q?")
    assert r["probability"] is None and r["answer"] is None
    assert M.yes_no("t", "")["ok"] is False                               # empty question is refused


def test_extract_normalises_field_specs_and_values(model):
    r = M.extract("Invoice from ACME, 12.5 due 2026-12-01", {
        "issuer": "Who issued the invoice?",
        "amount": {"type": "number", "instructions": "Amount due", "range": [0, 100]},
        "due": {"type": "date"},
    })
    assert r["ok"]
    assert r["fields"]["issuer"] == {"type": "text", "value": "ACME", "found": 0.99, "span": [0, 4], "raw": {"text": "ACME", "span": [0, 4], "found": 0.99}}
    assert r["fields"]["amount"]["value"] == 12.5
    qs = model.calls[0]["questions"]
    assert qs["due"] == {"instructions": "due", "type": "date"}            # a missing instruction defaults to the field name
    assert qs["amount"]["range"] == [0, 100]
    assert M.extract("t", {"x": {"instructions": "y"}})["ok"]              # type defaults to text
    for bad in ({}, None, {"x": {"type": "box"}}, {"x": 5}):
        assert M.extract("t", bad)["ok"] is False
    assert "state is required" in M.extract(None, {"f": "what?"})["error"]          # a failing run is passed through
    model.system_one = lambda *a, **k: {"answers": {"f": "garbage"}}        # a non-dict answer does not crash
    assert M.extract("t", {"f": "what?"})["fields"]["f"]["value"] is None
    model.fail = None
    model.system_one = FakeModel().system_one
    assert M.extract("t", {"f": {"type": "box", "instructions": "x"}})["ok"] is False


def test_score_maps_the_level_to_its_label(model):
    r = M.score("The server is down!", "How urgent is this?", ["low", "medium", "high"])
    assert r["ok"] and r["score"] == 2.0 and r["level"] == "high" and r["legend"]["0"] == "low"
    model.system_one = lambda *a, **k: {"answers": {"q": {"score": None}}}
    assert M.score("t", "q?", ["a", "b"])["level"] is None
    assert M.score("t", "q?", ["only"])["ok"] is False


def test_status_does_not_load_the_model(model, monkeypatch, tmp_path):
    monkeypatch.setenv("D1_HOME", str(tmp_path / "d1"))
    s = M.status()
    assert s["ok"] and s["installed"] is False and s["loaded"] is False and s["device"] is None and s["dtype"] is None
    assert s["model"] == "LiquidAI/d1-2B" and s["revision"] == d1_spec.REVISION
    assert "10M" in " ".join(s["license"]["summary"]) and s["license"]["name"] == d1_spec.LICENSE_NAME
    M.decide("t", {"q": {"type": "noul", "instructions": "x"}})
    s = M.status()
    assert s["loaded"] is True and s["device"] == "cuda" and s["dtype"] == "bfloat16"


# ---------------------------------------------------------------- images

class FakeImage:
    def __init__(self, path):
        self.path = str(path)
        self.loaded = False

    def load(self):
        self.loaded = True

    def convert(self, mode):
        assert self.loaded and mode == "RGB"
        return ("rgb", self.path)


@pytest.fixture
def fake_pil(monkeypatch):
    pil, image = types.ModuleType("PIL"), types.ModuleType("PIL.Image")

    def open_(path):
        if path.name.startswith("corrupt"):
            raise OSError("truncated")
        return FakeImage(path)

    image.open = open_
    pil.Image = image
    monkeypatch.setitem(sys.modules, "PIL", pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", image)


def test_images_are_opened_and_passed_as_media(model, fake_pil, tmp_path):
    a = tmp_path / "cat.png"
    a.write_bytes(b"x")
    q = {"n": {"type": "number", "instructions": "How many cats?", "range": [0, 10], "step": 1},
         "all": {"type": "boxes", "instructions": "Every cat.", "max": 4}}
    r = M.decide(None, q, image_paths=[str(a)])                          # media-only state
    assert r["ok"], r
    assert model.calls[0]["state"] is None and model.calls[0]["media"] == [("rgb", str(a))]


def test_bad_images_are_reported(model, fake_pil, tmp_path, monkeypatch):
    q = {"q": {"type": "noul", "instructions": "x"}}
    txt = tmp_path / "notes.txt"
    txt.write_text("x")
    big = tmp_path / "big.png"
    big.write_bytes(b"x" * 20)
    bad = tmp_path / "corrupt.png"
    bad.write_bytes(b"x")
    assert "not an image" in M.decide("t", q, image_paths=[str(txt)])["error"]
    assert "not an image" in M.decide("t", q, image_paths=[str(tmp_path / "missing.png")])["error"]
    assert "at most" in M.decide("t", q, image_paths=["a.png"] * (M.MAX_IMAGES + 1))["error"]
    assert "at most" in M.decide("t", q, image_paths="cat.png")["error"]
    assert "at most" in M.decide("t", q, image_paths=[5])["error"]
    monkeypatch.setattr(M, "MAX_IMAGE_BYTES", 10)
    assert "too large" in M.decide("t", q, image_paths=[str(big)])["error"]
    monkeypatch.setattr(M, "MAX_IMAGE_BYTES", 10_000)
    assert "cannot read image" in M.decide("t", q, image_paths=[str(bad)])["error"]
    assert model.calls == []


def test_missing_pillow_is_reported(model, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)                         # makes `from PIL import Image` raise ImportError
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    r = M.decide("t", {"q": {"type": "noul", "instructions": "x"}}, image_paths=[str(img)])
    assert r["ok"] is False and "pillow" in r["error"]


# ---------------------------------------------------------------- the real loader (against fake torch / transformers)

class FakeTorch(types.ModuleType):
    bfloat16, float32 = "bf16", "f32"

    def __init__(self, cuda=False, mps=None):
        super().__init__("torch")
        self.cuda = types.SimpleNamespace(is_available=lambda: cuda)
        if mps is not None:
            self.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps))


@pytest.mark.parametrize("cuda,mps,forced,expect", [
    (True, None, "", ("cuda", "bfloat16")),
    (False, True, "", ("mps", "bfloat16")),
    (False, False, "", ("cpu", "float32")),
    (False, None, "", ("cpu", "float32")),                                # a torch build without a backends.mps attribute
    (True, True, "cpu", ("cpu", "float32")),                              # D1_DEVICE overrides detection
    (False, None, "bogus", ("cpu", "float32")),                           # ...and an invalid override is ignored
])
def test_pick_device(monkeypatch, cuda, mps, forced, expect):
    monkeypatch.setenv("D1_DEVICE", forced)
    assert M.pick_device(FakeTorch(cuda, mps)) == expect


def test_real_loader_refuses_when_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("D1_HOME", str(tmp_path / "d1"))
    monkeypatch.setattr(M, "_loaded", None)
    monkeypatch.setattr(M, "_loader", None)
    r = M.decide("t", {"q": {"type": "noul", "instructions": "x"}})
    assert r["ok"] is False and "not installed" in r["error"]


def test_real_loader_pins_the_reviewed_revision_and_stays_offline(tmp_path, monkeypatch):
    home = tmp_path / "d1"
    py = d1_spec.env_python(home)
    py.parent.mkdir(parents=True)
    py.write_text("")
    d1_spec.write_state({"state": "ready", "revision": d1_spec.REVISION}, home)
    monkeypatch.setenv("D1_HOME", str(home))
    monkeypatch.setenv("D1_DEVICE", "")
    _restore_env_after(monkeypatch, "HF_HOME", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    seen = {}

    class Model:
        def to(self, device):
            seen["device"] = device
            return self

        def eval(self):
            seen["eval"] = True

        def system_one(self, state, questions, media=None):
            return {"answers": {"q": {"noul": 0.5}}, "usage": {}}

    class AutoModel:
        @staticmethod
        def from_pretrained(repo, **kw):
            seen["repo"], seen["kw"] = repo, kw
            return Model()

    tr = types.ModuleType("transformers")
    tr.AutoModel = AutoModel
    monkeypatch.setitem(sys.modules, "torch", FakeTorch(cuda=True))
    monkeypatch.setitem(sys.modules, "transformers", tr)
    monkeypatch.setattr(M, "_loaded", None)
    monkeypatch.setattr(M, "_loader", None)

    r = M.decide("t", {"q": {"type": "noul", "instructions": "x"}})
    assert r["ok"], r
    assert seen["repo"] == "LiquidAI/d1-2B"
    assert seen["kw"] == {"revision": d1_spec.REVISION, "trust_remote_code": True, "dtype": "bf16"}   # pinned commit, never "main"
    assert seen["device"] == "cuda" and seen["eval"] is True
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["HF_HOME"] == str(d1_spec.hf_home(home))
    assert r["meta"]["device"] == "cuda"


def test_server_exposes_the_documented_tools_with_schemas():
    tools = asyncio.run(M.server.list_tools())
    assert [t.name for t in tools] == ["decide", "classify", "yes_no", "extract", "score", "status"]
    decide = next(t for t in tools if t.name == "decide")
    assert set(decide.input_schema["properties"]) == {"state", "questions", "image_paths", "set_size"}
    assert "noul" in decide.description and "choice" in decide.description


def test_main_runs_the_stdio_server(monkeypatch):
    ran = []
    monkeypatch.setattr(M.server, "run", lambda t: ran.append(t))
    M.main()
    assert ran == ["stdio"]


def test_module_entrypoint(monkeypatch):
    from mcp.server.mcpserver import MCPServer
    ran = []
    monkeypatch.setattr(MCPServer, "run", lambda self, t: ran.append(t))
    runpy.run_module("app.mcp_d1", run_name="__main__")
    assert ran == ["stdio"]


def test_real_stdio_server_starts_lists_tools_and_reports_not_installed(tmp_path):
    """The actual `python -m app.mcp_d1` process over stdio: protocol, schemas and the not-installed error path."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    async def go():
        env = {**os.environ, "PYTHONPATH": backend, "D1_HOME": str(tmp_path / "d1")}
        params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_d1"], env=env)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = [t.name for t in (await s.list_tools()).tools]
                res = await s.call_tool("yes_no", {"text": "hi", "question": "Is it a greeting?"})
                return names, json.loads(res.content[0].text)

    names, res = asyncio.run(asyncio.wait_for(go(), 60))
    assert names == ["decide", "classify", "yes_no", "extract", "score", "status"]
    assert res["ok"] is False and "not installed" in res["error"]


# ---------------------------------------------------------------- the weight fetcher (runs inside the d1 env)

def _fetch_home(tmp_path, monkeypatch):
    home = tmp_path / "d1"
    monkeypatch.setenv("D1_HOME", str(home))
    _restore_env_after(monkeypatch, "HF_HOME")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    return home


def test_fetch_downloads_the_pinned_revision_and_reports_progress(tmp_path, monkeypatch, capsys):
    home = _fetch_home(tmp_path, monkeypatch)
    monkeypatch.setattr(d1_fetch, "POLL_SECONDS", 0.01)
    seen = {}
    hub = types.ModuleType("huggingface_hub")

    def snapshot_download(**kw):
        seen.update(kw)
        (home / "hf" / "hub" / "blob").write_bytes(b"x" * 1000)             # something on disk for the reporter to count
        import time
        time.sleep(0.15)
        return str(home / "hf" / "hub" / "snap")

    hub.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    (home / "hf" / "hub").mkdir(parents=True)
    assert d1_fetch.main() == 0
    out = capsys.readouterr().out.splitlines()
    assert seen["repo_id"] == "LiquidAI/d1-2B" and seen["revision"] == d1_spec.REVISION
    assert seen["allow_patterns"] == d1_spec.CODE_GLOBS and seen["cache_dir"] == str(home / "hf" / "hub")
    assert any(l.startswith("PROGRESS 1000 ") for l in out)                  # live progress from bytes on disk
    assert out[-1].startswith("DONE ") and out[-2].startswith(f"PROGRESS {d1_spec.WEIGHTS_BYTES + 30_000_000} ")
    assert "HF_HUB_OFFLINE" not in os.environ and os.environ["HF_HOME"] == str(home / "hf")   # online for the download only


def test_fetch_failure_is_reported(tmp_path, monkeypatch, capsys):
    _fetch_home(tmp_path, monkeypatch)
    hub = types.ModuleType("huggingface_hub")

    def boom(**kw):
        raise ConnectionError("no route to host")

    hub.snapshot_download = boom
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    assert d1_fetch.main() == 1
    assert "ERROR ConnectionError: no route to host" in capsys.readouterr().out


def test_dir_bytes_skips_links_and_unreadable_files(tmp_path, monkeypatch):
    (tmp_path / "a").write_bytes(b"12345")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_bytes(b"123")
    (tmp_path / "link").write_bytes(b"x" * 100)
    monkeypatch.setattr(os.path, "islink", lambda p: p.endswith("link"))      # snapshot links point at counted blobs
    assert d1_fetch.dir_bytes(tmp_path) == 8
    real = os.path.getsize
    monkeypatch.setattr(os.path, "getsize", lambda p: (_ for _ in ()).throw(OSError()) if p.endswith("b") else real(p))
    assert d1_fetch.dir_bytes(tmp_path) == 5


def test_fetch_module_entrypoint(tmp_path, monkeypatch):
    _fetch_home(tmp_path, monkeypatch)
    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = lambda **kw: "p"
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    with pytest.raises(SystemExit) as e:
        runpy.run_module("app.d1_fetch", run_name="__main__")
    assert e.value.code == 0
