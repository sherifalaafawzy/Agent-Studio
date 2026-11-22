"""The model adapters (app/ts_runners.py), exercised against fake `torch`, `chronos`, `timesfm3`, `toto2`, `timestral`,
`continuousintelligence`, Kronos' `model` and `pandas` modules. No real library, weights or network is used: the fakes
speak exactly the documented API of each pinned package and return recognisable numbers.
"""
import json
import os
import runpy
import sys
import types

import pytest

from app import ts_runners as R, ts_spec as T
from tests.test_ts_spec_fetch import mark_ready, restore_env_after  # noqa: F401  (fixtures/helpers shared with the spec tests)

H = 4                                                    # horizon used throughout


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "ts"
    monkeypatch.setenv("TS_HOME", str(h))
    restore_env_after(monkeypatch, "HF_HOME", "HF_HUB_OFFLINE")
    monkeypatch.delenv("TS_DEVICE", raising=False)
    return h


def grid(levels, h=H):
    """One path per level: value = level*100 + step."""
    return [[round(lv * 100 + i, 6) for i in range(h)] for lv in levels]


class Tensor:
    """Just enough tensor: holds nested lists, remembers how it was made."""

    def __init__(self, data, **kw):
        self.data, self.kw = data, kw

    def tolist(self):
        return self.data

    def detach(self):
        return self

    def cpu(self):
        return self


def fake_torch(monkeypatch, cuda=False):
    t = types.ModuleType("torch")
    t.__version__ = "2.15.0"
    t.float32, t.bool, t.long = "float32", "bool", "long"
    t.cuda = types.SimpleNamespace(is_available=lambda: cuda, get_device_name=lambda i: "RTX")
    t.tensor = lambda v, dtype=None: Tensor(v, dtype=dtype)
    t.as_tensor = lambda v, dtype=None, device=None: Tensor(v, dtype=dtype, device=device)
    t.ones_like = lambda x, dtype=None: Tensor("ones", dtype=dtype)
    t.zeros = lambda *shape, dtype=None, device=None: Tensor(f"zeros{shape}", dtype=dtype, device=device)
    monkeypatch.setitem(sys.modules, "torch", t)
    return t


def fake_hub(monkeypatch, home, fail=None):
    calls = []
    hub = types.ModuleType("huggingface_hub")

    def snapshot_download(**kw):
        calls.append(kw)
        if fail:
            raise fail
        return str(home / "snap" / kw["repo_id"].replace("/", "_"))
    hub.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    return calls


def install_fake_lib(monkeypatch, name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


# ---------------------------------------------------------------- helpers

def test_to_list_handles_tensors_arrays_lists_and_scalars():
    assert R.to_list(Tensor([[1.0, 2.0]])) == [[1.0, 2.0]]
    assert R.to_list([Tensor([1]), (2, 3)]) == [[1], [2, 3]]
    assert R.to_list(5) == 5
    assert R.to_list(types.SimpleNamespace(tolist=lambda: [9])) == [9]            # an array without detach/cpu
    assert R.transpose([[1, 2, 3], [4, 5, 6]]) == [[1, 4], [2, 5], [3, 6]]


def test_pick_levels_uses_native_levels_interpolates_between_and_clamps_outside():
    native = [0.9, 0.1, 0.5]                              # deliberately unsorted: the adapter must sort
    table = [[90.0, 91.0], [10.0, 11.0], [50.0, 51.0]]
    got, approx = R.pick_levels(native, table, [0.1, 0.5])
    assert got == {0.1: [10.0, 11.0], 0.5: [50.0, 51.0]} and approx == []
    got, approx = R.pick_levels(native, table, [0.3, 0.7])
    assert got[0.3] == pytest.approx([30.0, 31.0]) and got[0.7] == pytest.approx([70.0, 71.0]) and approx == [0.3, 0.7]
    got, approx = R.pick_levels(native, table, [0.01, 0.99])
    assert got == {0.01: [10.0, 11.0], 0.99: [90.0, 91.0]} and approx == [0.01, 0.99]


def test_loaded_rejects_the_wrong_kind_of_call():
    series_only = R.Loaded(T.MODELS["chronos-2"], "cpu", series=lambda v, h: ([0.5], [[1.0] * h]))
    with pytest.raises(R.RunnerError, match="general series model: use forecast, not forecast_ohlcv"):
        series_only.ohlcv({}, [], [], 1)
    candles_only = R.Loaded(T.MODELS["kronos-base"], "cpu", ohlcv=lambda *a: {})
    with pytest.raises(R.RunnerError, match="reads candlesticks, not plain series: use forecast_ohlcv"):
        candles_only.forecast([1.0], 1, [0.5])
    out = series_only.forecast([1.0, 2.0], 3, [0.1, 0.5])                           # 0.1 is not native: interpolated/clamped and reported
    assert out["median"] == [1.0, 1.0, 1.0] and out["quantiles"][0.1] == [1.0, 1.0, 1.0] and out["approximate"] == [0.1]
    assert R.Loaded(T.MODELS["kronos-base"], "cpu", ohlcv=lambda c, s, f, n: {"close": [n]}).ohlcv({}, [], [], 3) == {"close": [3]}


def test_device_follows_cuda_unless_forced(monkeypatch):
    t = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True))
    monkeypatch.delenv("TS_DEVICE", raising=False)
    assert R.device_for(t) == "cuda"
    t.cuda.is_available = lambda: False
    assert R.device_for(t) == "cpu"
    monkeypatch.setenv("TS_DEVICE", "cuda")
    assert R.device_for(t) == "cuda"
    monkeypatch.setenv("TS_DEVICE", "tpu")                                           # not a device we know: ignored
    assert R.device_for(t) == "cpu"


def test_snapshot_resolves_offline_from_the_environments_own_cache(home, monkeypatch):
    calls = fake_hub(monkeypatch, home)
    path = R.snapshot("amazon/chronos-2", "a" * 40, ["config.json"])
    assert path.endswith("amazon_chronos-2") and calls[0]["local_files_only"] is True and calls[0]["revision"] == "a" * 40
    assert calls[0]["cache_dir"] == str(home / "hf" / "hub") and os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["HF_HOME"] == str(home / "hf")
    fake_hub(monkeypatch, home, fail=FileNotFoundError("x"))
    with pytest.raises(R.RunnerError, match="not in the local cache"):
        R.snapshot("amazon/chronos-2", "a" * 40, ["config.json"])


def test_use_source_puts_the_fetched_library_on_the_path(home, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    ts = T.MODELS["timestral-5m"]
    with pytest.raises(R.RunnerError, match="source is missing"):
        R.use_source(ts)
    (home / "src" / "timestral" / "src").mkdir(parents=True)
    R.use_source(ts)
    R.use_source(ts)                                                                 # already there: not added twice
    assert sys.path.count(str(home / "src" / "timestral" / "src")) == 1 and sys.path[0] == str(home / "src" / "timestral" / "src")
    (home / "src" / "kronos").mkdir(parents=True)
    R.use_source(T.MODELS["kronos-base"])
    assert sys.path[0] == str(home / "src" / "kronos")


# ---------------------------------------------------------------- each adapter

def run_adapter(name, m, home, monkeypatch, cuda=False):
    torch = fake_torch(monkeypatch, cuda)
    fake_hub(monkeypatch, home)
    return R.ADAPTERS[name](m, "/local/snap", "cpu", torch)


def test_chronos2_adapter(home, monkeypatch):
    m, seen = T.MODELS["chronos-2"], {}

    class Pipe:
        @staticmethod
        def from_pretrained(path, device_map):
            seen.update(path=path, device=device_map)
            return Pipe()

        def predict_quantiles(self, inputs, prediction_length, quantile_levels):
            seen.update(inputs=inputs, h=prediction_length, levels=quantile_levels)
            return [Tensor([list(zip(*grid(quantile_levels)))])], [Tensor([[0.0] * prediction_length])]       # (variates, horizon, levels)
    install_fake_lib(monkeypatch, "chronos", Chronos2Pipeline=Pipe)
    series, ohlcv = run_adapter("chronos2", m, home, monkeypatch)
    native, table = series([1.0, 2.0, 3.0], H)
    assert ohlcv is None and seen["path"] == "/local/snap" and seen["device"] == "cpu" and seen["h"] == H
    assert seen["inputs"][0].data == [1.0, 2.0, 3.0] and seen["inputs"][0].kw["dtype"] == "float32" and seen["levels"] == list(m.quantile_levels)
    assert native == list(m.quantile_levels) and table == grid(m.quantile_levels)


def test_timesfm3_adapter(home, monkeypatch):
    seen = {}

    class Forecaster:
        config = types.SimpleNamespace(quantiles=[0.1, 0.5, 0.9])

        @staticmethod
        def from_pretrained(path, device):
            seen.update(path=path, device=device)
            return Forecaster()

        def predict(self, context, horizon, return_quantiles):
            seen.update(context=context, h=horizon, rq=return_quantiles)
            return types.SimpleNamespace(quantiles=list(zip(*grid([0.1, 0.5, 0.9], horizon))))            # (horizon, levels)
    install_fake_lib(monkeypatch, "timesfm3", TimesFM3Forecaster=Forecaster)
    series, ohlcv = run_adapter("timesfm3", T.MODELS["timesfm-3"], home, monkeypatch)
    native, table = series([1.0, 2.0], H)
    assert ohlcv is None and seen == dict(path="/local/snap", device="cpu", context=[1.0, 2.0], h=H, rq=True)
    assert native == [0.1, 0.5, 0.9] and table == [list(r) for r in grid([0.1, 0.5, 0.9])]


def test_toto2_adapter(home, monkeypatch):
    seen = {}

    class Model:
        @staticmethod
        def from_pretrained(path):
            seen["path"] = path
            return Model()

        def to(self, device):
            seen["device"] = device
            return self

        def eval(self):
            return self

        def forecast(self, inputs, horizon, decode_block_size, has_missing_values):
            seen.update(inputs=inputs, h=horizon, block=decode_block_size, missing=has_missing_values)
            return Tensor([[[row]] for row in grid(T.MODELS["toto-2-4m"].quantile_levels, horizon)])      # (levels, batch, variates, horizon)
    install_fake_lib(monkeypatch, "toto2", Toto2Model=Model)
    series, ohlcv = run_adapter("toto2", T.MODELS["toto-2-4m"], home, monkeypatch)
    native, table = series([1.0, 2.0, 3.0], H)
    assert ohlcv is None and seen["path"] == "/local/snap" and seen["device"] == "cpu" and seen["h"] == H and seen["block"] == 768 and seen["missing"] is False
    assert seen["inputs"]["target"].data == [[[1.0, 2.0, 3.0]]] and seen["inputs"]["target"].kw["device"] == "cpu"      # (batch, variates, time)
    assert seen["inputs"]["target_mask"].kw["dtype"] == "bool" and "zeros" in seen["inputs"]["series_ids"].data
    assert native == list(T.MODELS["toto-2-4m"].quantile_levels) and table == grid(native)


def test_timestral_adapter(home, monkeypatch):
    (home / "src" / "timestral" / "src").mkdir(parents=True)
    monkeypatch.setattr(sys, "path", list(sys.path))
    seen = {}
    levels = (0.1, 0.5, 0.9)

    class Forecaster:
        @staticmethod
        def from_pretrained(path, device, local_files_only):
            seen.update(path=path, device=device, offline=local_files_only)
            return Forecaster()

        def forecast(self, context, prediction_length):
            seen.update(context=context, h=prediction_length)
            return types.SimpleNamespace(quantile_levels=levels, quantiles=Tensor([[list(zip(*grid(levels, prediction_length)))]]))   # (B, V, horizon, levels)
    install_fake_lib(monkeypatch, "timestral", TimestralForecaster=Forecaster)
    series, ohlcv = run_adapter("timestral", T.MODELS["timestral-5m"], home, monkeypatch)
    native, table = series([5.0, 6.0], H)
    assert ohlcv is None and seen == dict(path="/local/snap", device="cpu", offline=True, context=[5.0, 6.0], h=H)
    assert native == [0.1, 0.5, 0.9] and table == [list(r) for r in grid(levels)]


def test_ciproto_adapter(home, monkeypatch):
    seen = {}
    levels = [0.1, 0.5, 0.9]

    class Fc:
        quantiles = Tensor(levels)

        def forecast(self, history, horizon):
            seen.update(history=history, h=horizon)
            return Tensor([grid(levels, horizon)])                                                       # [variables, levels, horizon]

    def load_model(path, device):
        seen.update(path=str(path), device=device)
        return Fc()
    install_fake_lib(monkeypatch, "continuousintelligence", load_model=load_model)
    series, ohlcv = run_adapter("ciproto", T.MODELS["ci-proto"], home, monkeypatch)
    native, table = series([1.0, 2.0, 3.0], H)
    assert ohlcv is None and seen["path"] == os.path.normpath("/local/snap") and seen["device"] == "cpu" and seen["h"] == H
    assert seen["history"].data == [[1.0, 2.0, 3.0]] and seen["history"].kw["dtype"] == "float32"       # [variables, time]
    assert native == levels and table == grid(levels)


class Frame:
    """The corner of pandas that the Kronos adapter touches."""

    def __init__(self, columns):
        self.columns = list(columns)
        self.data = dict(columns)

    def __getitem__(self, c):
        return types.SimpleNamespace(tolist=lambda: self.data[c])


def fake_pandas(monkeypatch):
    pd = types.ModuleType("pandas")
    pd.DataFrame = lambda cols: Frame(cols)
    pd.Series = lambda v: ("series", list(v))
    pd.to_datetime = lambda v: [f"dt:{x}" for x in v]
    monkeypatch.setitem(sys.modules, "pandas", pd)


def test_kronos_adapter(home, monkeypatch):
    (home / "src" / "kronos").mkdir(parents=True)
    monkeypatch.setattr(sys, "path", list(sys.path))
    fake_pandas(monkeypatch)
    seen = {}

    class Kronos:
        @staticmethod
        def from_pretrained(path):
            seen["model"] = path
            return "MODEL"

    class Tokenizer:
        @staticmethod
        def from_pretrained(path):
            seen["tokenizer"] = path
            return "TOKENIZER"

    class Predictor:
        def __init__(self, model, tokenizer, device, max_context):
            seen.update(parts=(model, tokenizer), device=device, max_context=max_context)

        def predict(self, df, x_timestamp, y_timestamp, pred_len, T, top_p, sample_count, verbose):
            seen.update(df=df, x=x_timestamp, y=y_timestamp, n=pred_len, samples=sample_count, verbose=verbose)
            return Frame({c: [float(i) for i in range(pred_len)] for c in ("open", "high", "low", "close", "volume", "amount", "extra")})
    sys.modules["model"] = types.ModuleType("model")                                  # something else owns the name: it must be replaced
    install_fake_lib_model = types.ModuleType("model")
    install_fake_lib_model.Kronos, install_fake_lib_model.KronosTokenizer, install_fake_lib_model.KronosPredictor = Kronos, Tokenizer, Predictor
    real_import = __import__

    def guarded(name, *a, **k):
        if name == "model":
            sys.modules["model"] = install_fake_lib_model
        return real_import(name, *a, **k)
    monkeypatch.setattr("builtins.__import__", guarded)
    torch = fake_torch(monkeypatch)
    fake_hub(monkeypatch, home)
    series, ohlcv = R._kronos(T.MODELS["kronos-base"], "/local/kronos", "cpu", torch)
    assert series is None
    out = ohlcv({"open": [1.0], "close": [2.0]}, ["2026-01-01", "2026-01-02"], ["2026-01-03", "2026-01-04"], 2)
    assert seen["model"] == "/local/kronos" and seen["tokenizer"].endswith("NeoQuasar_Kronos-Tokenizer-base")
    assert seen["parts"] == ("MODEL", "TOKENIZER") and seen["device"] == "cpu" and seen["max_context"] == 512
    assert seen["df"].data == {"open": [1.0], "close": [2.0]} and seen["x"] == ("series", ["dt:2026-01-01", "dt:2026-01-02"]) and seen["n"] == 2
    assert seen["samples"] == 2 and seen["verbose"] is False
    assert sorted(out) == ["amount", "close", "high", "low", "open", "volume"] and out["open"] == [0.0, 1.0]        # unknown columns are dropped


# ---------------------------------------------------------------- load()

def test_load_refuses_a_model_that_is_not_installed(home):
    with pytest.raises(R.RunnerError, match="Chronos-2 .* is not installed: open MCP servers > Time-series forecasting"):
        R.load(T.MODELS["chronos-2"])


def test_load_builds_the_runner_on_the_chosen_device(home, monkeypatch):
    mark_ready(home, "chronos-2")
    fake_torch(monkeypatch, cuda=True)
    fake_hub(monkeypatch, home)
    seen = {}
    monkeypatch.setitem(R.ADAPTERS, "chronos2", lambda m, path, device, torch: (seen.update(path=path, device=device), (lambda v, h: ([0.5], [[1.0] * h]), None))[1])
    loaded = R.load(T.MODELS["chronos-2"])
    assert loaded.device == "cuda" and seen["path"].endswith("amazon_chronos-2") and loaded.forecast([1.0], 2, [0.5])["median"] == [1.0, 1.0]


def test_load_explains_library_failures_and_passes_runner_errors_through(home, monkeypatch):
    mark_ready(home, "chronos-2")
    fake_torch(monkeypatch)
    fake_hub(monkeypatch, home)

    def boom(m, path, device, torch):
        raise MemoryError("out of memory " + "x" * 400)
    monkeypatch.setitem(R.ADAPTERS, "chronos2", boom)
    with pytest.raises(R.RunnerError, match="failed to load: MemoryError: out of memory") as e:
        R.load(T.MODELS["chronos-2"])
    assert len(str(e.value)) < 380

    def refuse(m, path, device, torch):
        raise R.RunnerError("custom")
    monkeypatch.setitem(R.ADAPTERS, "chronos2", refuse)
    with pytest.raises(R.RunnerError, match="^custom$"):
        R.load(T.MODELS["chronos-2"])
    fake_hub(monkeypatch, home, fail=OSError("nope"))
    with pytest.raises(R.RunnerError, match="not in the local cache"):
        R.load(T.MODELS["chronos-2"])


# ---------------------------------------------------------------- verify (the install-time check)

def fake_metadata(monkeypatch):
    import importlib.metadata as md
    monkeypatch.setattr(md, "version", lambda name: {"chronos-forecasting": "2.3.2", "timesfm": "3.0.2", "toto-2": "2.0.0",
                                                     "continuousintelligence": "0.1.1"}[name])


@pytest.mark.parametrize("model_id,module,library", [
    ("chronos-2", "chronos", "2.3.2"), ("timesfm-3", "timesfm3", "3.0.2"), ("toto-2-4m", "toto2", "2.0.0"), ("ci-proto", "continuousintelligence", "0.1.1")])
def test_verify_imports_the_library_and_reports_versions(home, monkeypatch, model_id, module, library):
    fake_torch(monkeypatch, cuda=True)
    fake_metadata(monkeypatch)
    install_fake_lib(monkeypatch, module)
    assert R.verify(model_id) == {"model": model_id, "torch": "2.15.0", "library": library, "cuda": True, "gpu": "RTX"}


def test_verify_source_models_report_the_commit(home, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    fake_torch(monkeypatch)
    (home / "src" / "timestral" / "src").mkdir(parents=True)
    (home / "src" / "kronos").mkdir(parents=True)
    install_fake_lib(monkeypatch, "timestral")
    install_fake_lib(monkeypatch, "model")
    got = R.verify("timestral-28m")
    assert got["library"] == "mistralai/timestral@a534bdbd244d" and got["cuda"] is False and got["gpu"] is None
    assert R.verify("kronos-base")["library"] == "shiyu-coder/Kronos@67b630e67f6a"


def test_verify_without_any_library_name_leaves_it_blank(home, monkeypatch):
    fake_torch(monkeypatch)
    install_fake_lib(monkeypatch, "chronos")
    bare = T.Model(**{**T.MODELS["chronos-2"].__dict__, "pip": ()})
    monkeypatch.setitem(T.MODELS, "chronos-2", bare)
    assert R.verify("chronos-2")["library"] == ""


def test_main_prints_the_verification_json(home, monkeypatch, capsys):
    monkeypatch.setattr(R, "verify", lambda mid: {"model": mid})
    assert R.main(["verify", "chronos-2"]) == 0
    assert json.loads(capsys.readouterr().out) == {"model": "chronos-2"}
    assert R.main(["nope"]) == 2 and "usage" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["ts_runners"])
    assert R.main() == 2
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        runpy.run_module("app.ts_runners", run_name="__main__")
    assert e.value.code == 2
