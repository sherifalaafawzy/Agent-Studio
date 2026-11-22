"""The time-series MCP server (app/mcp_ts.py): model selection, forecasting several models at once, ensemble, backtest metrics,
CSV input, candlesticks, loading/eviction, and the real stdio process.

No real model is used: a fake loader returns predictable forecasts (last value + slope * step + a per-model bias).
"""
import asyncio
import dataclasses
import json
import os
import runpy
import sys

import pytest

from app import mcp_ts as M, ts_runners as R, ts_spec as T
from tests.test_ts_spec_fetch import mark_ready

BIAS = {"chronos-2": 0.0, "toto-2-4m": 3.0, "timestral-5m": 1.0, "timesfm-3": 5.0}
LINE = [float(i) for i in range(1, 41)]                  # 1..40: slope 1, so slope-aware fakes are exact


class FakeModels:
    """Counts loads and forecast calls; `fail` names models whose forecast raises."""

    def __init__(self):
        self.loads, self.calls, self.fail, self.short = [], [], {}, set()

    def loader(self, spec):
        self.loads.append(spec.id)
        bias = BIAS.get(spec.id, 0.0)

        def series(values, horizon):
            if spec.id in self.fail:
                raise self.fail[spec.id]
            self.calls.append((spec.id, len(values), horizon))
            slope = values[-1] - values[-2]
            n = horizon - 1 if spec.id in self.short else horizon
            med = [values[-1] + slope * (i + 1) + bias for i in range(n)]
            return [0.1, 0.5, 0.9], [[m - 1 for m in med], med, [m + 1 for m in med]]
        return R.Loaded(spec, "cpu", series=series)


@pytest.fixture
def fm(tmp_path, monkeypatch):
    monkeypatch.setenv("TS_HOME", str(tmp_path / "ts"))
    monkeypatch.setenv("TS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("TS_MAX_LOADED", raising=False)
    (tmp_path / "ws").mkdir()
    f = FakeModels()
    monkeypatch.setattr(M, "_loader", f.loader)
    monkeypatch.setattr(M, "_loaded", M.OrderedDict())
    mark_ready(tmp_path / "ts", "chronos-2", "toto-2-4m", "timestral-5m")
    f.home, f.ws = tmp_path / "ts", tmp_path / "ws"
    return f


# ---------------------------------------------------------------- numbers

def test_metrics_match_hand_calculation():
    m = M.metrics([1.0, 2.0, 3.0], [2.0, 2.0, 5.0], [1.0, 2.0, 3.0, 4.0])
    assert m == {"mae": 1.0, "rmse": 1.290994, "smape_pct": 38.8889, "mase": 1.0}
    assert M.metrics([0.0], [0.0], [1.0, 2.0])["smape_pct"] == 0.0                     # 0/0 counts as a perfect hit
    assert M.metrics([1.0], [1.0], [5.0])["mase"] is None                                # history shorter than one season
    assert M.metrics([1.0], [2.0], [3.0, 3.0, 3.0])["mase"] is None                      # constant history: no scale
    assert M.metrics([10.0], [10.0], [1.0, 2.0, 3.0, 4.0, 5.0], season=2)["mase"] == 0.0


def test_ensemble_is_the_mean_and_needs_two_models():
    one = {"a": {"median": [1.0, 2.0], "quantiles": {"0.5": [1.0, 2.0]}}}
    assert M.ensemble(one, [0.5]) is None
    two = {"a": {"median": [1.0, 2.0], "quantiles": {"0.5": [1.0, 2.0]}}, "b": {"median": [3.0, 6.0], "quantiles": {"0.5": [3.0, 4.0]}}}
    e = M.ensemble(two, [0.5])
    assert e["members"] == ["a", "b"] and e["median"] == [2.0, 4.0] and e["quantiles"] == {"0.5": [2.0, 3.0]} and "mean" in e["method"]


def test_future_timestamps_only_for_evenly_spaced_input():
    assert M.future_stamps(["2026-01-01", "2026-01-02", "2026-01-03"], 2) == ["2026-01-04T00:00:00", "2026-01-05T00:00:00"]
    assert M.future_stamps(["2026-01-01T00:00:00", "2026-01-01T01:30:00"], 1) == ["2026-01-01T03:00:00"]
    assert M.future_stamps(["2026-01-01", "2026-01-02", "2026-01-04"], 2) is None       # irregular
    assert M.future_stamps(["2026-01-03", "2026-01-02"], 2) is None                     # going backwards
    assert M.future_stamps(["2026-01-01"], 2) is None and M.future_stamps(None, 2) is None
    assert M.future_stamps(["soon", "later"], 2) is None


# ---------------------------------------------------------------- validation

@pytest.mark.parametrize("bad,msg", [
    ("x", "non-empty list"), ([], "non-empty list"), ([1.0, "a"], "series\\[1\\] is not a finite number"), ([True] * 3, "series\\[0\\]"),
    ([1.0, float("nan")], "finite"), ([1.0, float("inf")], "finite")])
def test_series_must_be_finite_numbers(bad, msg):
    assert M._numbers(bad)[0] is None
    assert __import__("re").search(msg, M._numbers(bad)[1])


def test_series_size_limit(monkeypatch):
    monkeypatch.setattr(M, "MAX_VALUES", 3)
    assert "limit is 3" in M._numbers([1, 2, 3, 4])[1]
    assert M._numbers([1, 2.5, 3]) == ([1.0, 2.5, 3.0], None)


def test_horizon_and_quantile_validation():
    assert M._horizon(5) == (5, None)
    for bad in (0, -1, 10_000, 2.5, "3", True):
        assert M._horizon(bad)[0] is None
    assert M._levels(None) == ([0.1, 0.5, 0.9], None) and M._levels([0.9, 0.1, 0.1]) == ([0.1, 0.9], None)
    for bad in ([], "x", [0], [1], [0.5, True], [0.5, "a"], [i / 20 for i in range(1, 11)]):
        assert M._levels(bad)[0] is None


def test_selection_all_one_list_unknown_wrong_kind_and_not_installed(fm):
    ids = lambda r: [m.id for m in r]  # noqa: E731
    assert ids(M.select_models("all")[0]) == ["chronos-2", "toto-2-4m", "timestral-5m"] and M.select_models(None)[0] and M.select_models("")[0]
    assert ids(M.select_models("chronos-2")[0]) == ["chronos-2"]
    specs, missing, err = M.select_models(["chronos-2", "timesfm-3", "chronos-2", "ci-proto"])
    assert ids(specs) == ["chronos-2"] and missing == ["timesfm-3", "ci-proto"] and err is None          # named but not installed: reported
    assert "unknown model(s) nope" in M.select_models(["nope", "chronos-2"])[2]
    assert "use forecast_ohlcv" in M.select_models(["kronos-base"])[2] and "use forecast" in M.select_models(["chronos-2"], kind="ohlcv")[2]
    for bad in (5, [], [1], {"a": 1}):
        assert "must be" in M.select_models(bad)[2]


def test_selection_all_when_nothing_is_installed(fm, monkeypatch):
    mark_ready(fm.home)
    assert "no time-series model is installed" in M.select_models("all")[2]
    assert "Kronos is not installed" in M.select_models("all", kind="ohlcv")[2]


# ---------------------------------------------------------------- forecast

def test_one_model_forecast_has_median_band_and_no_ensemble(fm):
    r = M.forecast(LINE, 3, "chronos-2")
    assert r["ok"] and list(r["models"]) == ["chronos-2"] and r["ensemble"] is None and r["horizon"] == 3 and r["n_context"] == 40
    m = r["models"]["chronos-2"]
    assert m["median"] == [41.0, 42.0, 43.0] and m["quantiles"]["0.1"] == [40.0, 41.0, 42.0] and m["quantiles"]["0.9"] == [42.0, 43.0, 44.0]
    assert m["label"] == "Chronos-2 (Amazon)" and m["device"] == "cpu" and m["loaded_now"] is True and "load_seconds" in m
    assert r["not_installed"] == [] and r["errors"] == {} and r["quantile_levels"] == [0.1, 0.5, 0.9] and "approximate_levels" not in m
    again = M.forecast(LINE, 3, "chronos-2")
    assert again["models"]["chronos-2"]["loaded_now"] is False and "load_seconds" not in again["models"]["chronos-2"] and fm.loads == ["chronos-2"]   # warm


def test_several_models_at_once_give_side_by_side_results_and_an_ensemble(fm):
    r = M.forecast(LINE, 2, ["chronos-2", "toto-2-4m", "timesfm-3"], quantiles=[0.1, 0.25, 0.9])
    assert r["ok"] and list(r["models"]) == ["chronos-2", "toto-2-4m"] and r["not_installed"] == ["timesfm-3"]
    assert r["models"]["toto-2-4m"]["median"] == [44.0, 45.0]
    assert r["models"]["chronos-2"]["approximate_levels"] == ["0.25"]                      # 0.25 is not one of the fake's native levels
    e = r["ensemble"]
    assert e["members"] == ["chronos-2", "toto-2-4m"] and e["median"] == [42.5, 43.5] and "0.25" in e["quantiles"]
    all_ = M.forecast(LINE, 2, "all")
    assert list(all_["models"]) == ["chronos-2", "toto-2-4m", "timestral-5m"] and all_["ensemble"]["median"] == pytest.approx([42.333333, 43.333333])


def test_each_model_failure_is_reported_not_hidden_and_the_rest_still_run(fm):
    fm.fail["toto-2-4m"] = RuntimeError("out of memory")
    fm.fail["timestral-5m"] = R.RunnerError("Timestral failed to load: x")
    r = M.forecast(LINE, 2, "all")
    assert r["ok"] and list(r["models"]) == ["chronos-2"] and r["ensemble"] is None
    assert r["errors"] == {"toto-2-4m": "RuntimeError: out of memory", "timestral-5m": "Timestral failed to load: x"}


def test_every_model_failing_is_an_error_with_the_reasons(fm):
    fm.fail["chronos-2"] = ValueError("bad")
    r = M.forecast(LINE, 2, ["chronos-2", "timesfm-3"])
    assert r == {"ok": False, "error": "every selected model failed", "errors": {"chronos-2": "ValueError: bad"}, "not_installed": ["timesfm-3"]}


def test_naming_only_models_that_are_not_installed_is_an_error_not_a_silent_skip(fm):
    r = M.forecast(LINE, 2, ["timesfm-3", "ci-proto"])
    assert r["ok"] is False and r["not_installed"] == ["timesfm-3", "ci-proto"] and "install them" in r["error"]
    mark_ready(fm.home)
    assert "no time-series model is installed" in M.forecast(LINE, 2)["error"] and "not_installed" not in M.forecast(LINE, 2)


@pytest.mark.parametrize("kw,msg", [
    (dict(series=[1.0] * 3, horizon=2), "at least 8 values"), (dict(series=LINE, horizon=0), "horizon must be"),
    (dict(series=LINE, horizon=2, quantiles=[2]), "strictly between"), (dict(series=LINE, horizon=2, season=0), "season must be"),
    (dict(series=LINE, horizon=2, season=True), "season must be"), (dict(series=LINE, horizon=2, timestamps=["2026-01-01"]), "one ISO timestamp per value"),
    (dict(series=LINE, horizon=2, timestamps="x"), "one ISO timestamp per value"), (dict(series=LINE, horizon=2, models=["nope"]), "unknown model"),
    (dict(series="abc", horizon=2), "non-empty list")])
def test_bad_input_is_explained(fm, kw, msg):
    r = M.forecast(**kw)
    assert r["ok"] is False and msg in r["error"]
    assert fm.loads == [] and fm.calls == []                                               # nothing ran


def test_wrong_length_from_a_model_is_caught(fm):
    fm.short.add("chronos-2")
    r = M.forecast(LINE, 3, ["chronos-2", "toto-2-4m"])
    assert list(r["models"]) == ["toto-2-4m"] and "returned 2 steps instead of 3" in r["errors"]["chronos-2"]


def test_future_timestamps_are_added_for_evenly_spaced_history(fm):
    days = [f"2026-02-{d:02d}" for d in range(1, 29)] + [f"2026-03-{d:02d}" for d in range(1, 13)]
    r = M.forecast(LINE, 2, "chronos-2", timestamps=days)
    assert r["timestamps"] == ["2026-03-13T00:00:00", "2026-03-14T00:00:00"]
    skipped = M.forecast(LINE, 2, "chronos-2", timestamps=["soon"] * 40)
    assert "timestamps" not in skipped                                                      # not evenly spaced ISO: no labels, no failure


def test_long_history_notes_the_models_own_limit(fm, monkeypatch):
    monkeypatch.setitem(T.MODELS, "chronos-2", dataclasses.replace(T.MODELS["chronos-2"], max_context=10))
    r = M.forecast(LINE, 2, "chronos-2")
    assert "reads at most 10 steps of history" in r["models"]["chronos-2"]["note"]


# ---------------------------------------------------------------- backtest and compare

def test_backtest_scores_every_model_and_the_ensemble_and_ranks_them(fm):
    r = M.forecast(LINE, 4, "all", backtest=True)
    bt = r["backtest"]
    assert bt["ok"] and bt["holdout"] == 4 and bt["context_used"] == 36 and bt["ranked_by"] == "mase" and bt["errors"] == {}
    assert bt["ranking"] == ["chronos-2", "timestral-5m", "ensemble", "toto-2-4m"]         # biases 0, 1, mean=1.33, 3
    assert bt["scores"]["chronos-2"] == {"mae": 0.0, "rmse": 0.0, "smape_pct": 0.0, "mase": 0.0}
    assert bt["scores"]["toto-2-4m"]["mae"] == 3.0 and bt["scores"]["toto-2-4m"]["mase"] == 3.0 and "noisy" in bt["caution"]
    assert (("chronos-2", 36, 4) in fm.calls) and (("chronos-2", 40, 4) in fm.calls)         # one forecast on the full data, one on the shortened


def test_backtest_with_one_model_has_no_ensemble_entry(fm):
    bt = M.forecast(LINE, 4, "chronos-2", backtest=True, season=3)["backtest"]
    assert list(bt["scores"]) == ["chronos-2"] and bt["season"] == 3


def test_backtest_falls_back_to_mae_when_mase_is_undefined(fm):
    flat = [5.0] * 20 + [6.0] * 20
    bt = M.forecast(flat[:20] + [5.0] * 20, 4, "all", backtest=True)["backtest"]
    assert bt["ranked_by"] == "mae" and bt["scores"]["chronos-2"]["mase"] is None


def test_backtest_needs_enough_history_before_the_holdout(fm):
    r = M.forecast(LINE[:12], 6, "chronos-2", backtest=True)
    assert r["ok"] and r["backtest"] == {"ok": False, "error": "a backtest needs at least 8 values before the 6 held-out ones (got 6)"}


def test_backtest_records_models_that_fail_on_the_shorter_series(fm):
    fm.fail["toto-2-4m"] = RuntimeError("boom")
    r = M.compare(LINE, 4)
    assert r["ok"] and r["errors"] == {"toto-2-4m": "RuntimeError: boom"} and "toto-2-4m" not in r["scores"] and r["ranking"][0] == "chronos-2"


def test_compare_tool_runs_only_the_backtest_and_lists_missing_models(fm):
    r = M.compare(LINE, 4, ["chronos-2", "timestral-5m", "timesfm-3"], season=7)
    assert r["ok"] and r["not_installed"] == ["timesfm-3"] and r["season"] == 7 and set(r["scores"]) == {"chronos-2", "timestral-5m", "ensemble"}
    assert all(h == 4 and n == 36 for _, n, h in fm.calls) and len(fm.calls) == 2           # no second, full-data forecast
    assert M.compare(LINE[:10], 6, "chronos-2") == {"ok": False, "error": "a backtest needs at least 8 values before the 6 held-out ones (got 4)"}
    assert M.compare(LINE, 0)["ok"] is False
    assert M.compare(LINE, 4, ["timesfm-3"]) == {"ok": False, "error": "none of the requested models is installed: open MCP servers > Time-series forecasting and install them",
                                                 "not_installed": ["timesfm-3"]}


# ---------------------------------------------------------------- loading and eviction

def test_least_recently_used_models_are_evicted_beyond_the_limit(fm, monkeypatch):
    monkeypatch.setenv("TS_MAX_LOADED", "2")
    M.forecast(LINE, 2, "chronos-2")
    M.forecast(LINE, 2, "toto-2-4m")
    M.forecast(LINE, 2, "chronos-2")                                                         # touch: toto is now the oldest
    M.forecast(LINE, 2, "timestral-5m")
    assert list(M._loaded) == ["chronos-2", "timestral-5m"]
    M.forecast(LINE, 2, "toto-2-4m")
    assert fm.loads == ["chronos-2", "toto-2-4m", "timestral-5m", "toto-2-4m"]               # toto had to be loaded again


def test_max_loaded_setting(monkeypatch):
    monkeypatch.delenv("TS_MAX_LOADED", raising=False)
    assert M.max_loaded() == 3
    monkeypatch.setenv("TS_MAX_LOADED", "0")
    assert M.max_loaded() == 1
    monkeypatch.setenv("TS_MAX_LOADED", "many")
    assert M.max_loaded() == 3


def test_the_real_loader_is_used_when_none_is_injected(fm, monkeypatch):
    monkeypatch.setattr(M, "_loader", None)
    called = []
    monkeypatch.setattr(R, "load", lambda spec: (called.append(spec.id), fm.loader(spec))[1])
    assert M.forecast(LINE, 2, "chronos-2")["ok"] and called == ["chronos-2"]


# ---------------------------------------------------------------- list_models / status

def test_list_models_shows_install_state_licences_and_blocks(fm):
    r = M.list_models()
    assert r["ok"] and r["installed"] == ["chronos-2", "toto-2-4m", "timestral-5m"] and len(r["models"]) == 8
    by = {m["id"]: m for m in r["models"]}
    assert by["timesfm-3"]["commercial_use"] == "no" and by["timesfm-3"]["installed"] is False and by["kronos-base"]["kind"] == "ohlcv"
    assert by["chronos-2"]["download_mb"] == 478 and by["chronos-2"]["quantile_levels"][0] == 0.01 and by["chronos-2"]["loaded"] is False
    assert isinstance(by["ci-proto"]["install_blocked"], str)
    M.forecast(LINE, 2, "chronos-2")
    assert {m["id"]: m for m in M.list_models()["models"]}["chronos-2"]["loaded"] is True


def test_status_reports_installed_loaded_and_licences(fm):
    M.forecast(LINE, 2, "toto-2-4m")
    s = M.status()
    assert s["installed"] == ["chronos-2", "toto-2-4m", "timestral-5m"] and s["loaded"] == ["toto-2-4m"] and s["max_loaded"] == 3
    assert set(s["licences"]) == set(s["installed"]) and s["licences"]["chronos-2"]["commercial_use"] == "yes"


# ---------------------------------------------------------------- CSV input

def write(fm, name, text, enc="utf-8"):
    (fm.ws / name).write_bytes(text.encode(enc))


def test_forecast_file_reads_a_column_and_timestamps(fm):
    rows = "\n".join(f"2026-01-{d:02d},{d * 2}" for d in range(1, 21))
    write(fm, "sales.csv", "day,units\n" + rows + "\n")
    r = M.forecast_file("sales.csv", "units", 2, "chronos-2", timestamp_column="day")
    assert r["ok"] and r["n_context"] == 20 and r["models"]["chronos-2"]["median"] == [42.0, 44.0] and r["timestamps"] == ["2026-01-21T00:00:00", "2026-01-22T00:00:00"]
    assert M.forecast_file("sales.csv", "units", 2, "chronos-2", backtest=True, quantiles=[0.5])["backtest"]["ok"]


def test_forecast_file_handles_tsv_bom_and_separate_workspace_argument(fm):
    write(fm, "t.tsv", "﻿x\ty\n" + "\n".join(f"{i}\t{i}" for i in range(1, 15)) + "\n")
    values, stamps, err = M.read_series_file("t.tsv", "y", workspace=str(fm.ws))
    assert err is None and stamps is None and values == [float(i) for i in range(1, 15)]


@pytest.mark.parametrize("path,column,ts,msg", [
    ("", "a", "", "path and column are required"), ("x.csv", "", "", "path and column are required"), (5, "a", "", "path and column are required"),
    ("../outside.csv", "a", "", "inside the workspace"), ("missing.csv", "a", "", "not a .csv/.tsv/.txt file"), ("notes.md", "a", "", "not a .csv"),
    ("d.csv", "nope", "", "column(s) not in the file: nope (it has: day, v)"), ("d.csv", "v", "when", "column(s) not in the file: when")])
def test_forecast_file_input_errors(fm, path, column, ts, msg):
    write(fm, "d.csv", "day,v\n2026-01-01,1\n")
    write(fm, "notes.md", "x")
    (fm.ws.parent / "outside.csv").write_text("a\n1\n")
    values, stamps, err = M.read_series_file(path, column, ts)
    assert values is None and msg in err
    assert M.forecast_file(path, column, 2)["ok"] is False


def test_forecast_file_rejects_bad_cells_big_files_and_unreadable_files(fm, monkeypatch):
    write(fm, "bad.csv", "v\n1\nabc\n")
    assert "line 3: 'abc' in column v is not a number" in M.read_series_file("bad.csv", "v")[2]
    write(fm, "nan.csv", "v\n1\nnan\n")
    assert "line 3: 'nan' in column v is not a finite number" in M.read_series_file("nan.csv", "v")[2]
    write(fm, "blank.csv", 'v\n1\n""\n')
    assert "line 3: ''" in M.read_series_file("blank.csv", "v")[2]
    write(fm, "many.csv", "v\n" + "\n".join("1" for _ in range(5)))
    monkeypatch.setattr(M, "MAX_VALUES", 3)
    assert "more than 3 rows" in M.read_series_file("many.csv", "v")[2]
    monkeypatch.setattr(M, "MAX_FILE_BYTES", 4)
    assert "file too large (max 0 MB)" in M.read_series_file("many.csv", "v")[2]
    monkeypatch.setattr(M, "MAX_FILE_BYTES", 10_000)
    write(fm, "latin.csv", "v\n\xe9\n", enc="latin-1")
    assert "cannot read latin.csv: UnicodeDecodeError" in M.read_series_file("latin.csv", "v")[2]


def test_workspace_defaults_to_the_environment_variable_then_the_current_folder(fm, monkeypatch):
    write(fm, "w.csv", "v\n" + "\n".join("1" for _ in range(3)))
    assert M.read_series_file("w.csv", "v")[2] is None
    monkeypatch.delenv("TS_WORKSPACE")
    monkeypatch.chdir(fm.ws)
    assert M.read_series_file("w.csv", "v")[2] is None


def test_csv_error_during_read_is_reported(fm, monkeypatch):
    write(fm, "e.csv", "v\n1\n")
    import csv

    class Boom:
        fieldnames = ["v"]

        def __iter__(self):
            raise csv.Error("bad quoting")
    monkeypatch.setattr(M.csv, "DictReader", lambda *a, **k: Boom())
    assert "cannot read e.csv: Error" in M.read_series_file("e.csv", "v")[2]


# ---------------------------------------------------------------- candlesticks (Kronos)

def candles(n=12, volume=False, step_days=1):
    return [{"timestamp": f"2026-01-{i * step_days + 1:02d}T00:00:00", "open": 1.0 + i, "high": 2.0 + i, "low": 0.5 + i, "close": 1.5 + i,
             **({"volume": 100.0 + i} if volume else {})} for i in range(n)]


class FakeKronos:
    def __init__(self):
        self.seen = []

    def loader(self, spec):
        def ohlcv(cols, stamps, future, samples):
            self.seen.append((cols, stamps, future, samples))
            return {c: [v[-1] + i + 1 for i in range(len(future))] for c, v in cols.items()}
        return R.Loaded(spec, "cpu", ohlcv=ohlcv)


@pytest.fixture
def kronos(fm, monkeypatch):
    k = FakeKronos()
    mark_ready(fm.home, "chronos-2", "kronos-base")
    monkeypatch.setattr(M, "_loader", lambda spec: k.loader(spec) if spec.kind == "ohlcv" else fm.loader(spec))
    return k


def test_forecast_ohlcv_returns_one_path_per_column(kronos):
    r = M.forecast_ohlcv(candles(12, volume=True), 3)
    assert r["ok"] and r["model"] == "kronos-base" and r["timestamps"] == ["2026-01-13T00:00:00", "2026-01-14T00:00:00", "2026-01-15T00:00:00"]
    assert r["forecast"]["close"] == [13.5, 14.5, 15.5] and set(r["forecast"]) == {"open", "high", "low", "close", "volume"}
    assert r["context_used"] == 12 and r["samples"] == 1 and "Not financial advice" in r["caution"] and r["loaded_now"] is True
    assert M.forecast_ohlcv(candles(12), 2, samples=4)["samples"] == 4 and kronos.seen[-1][3] == 4


def test_forecast_ohlcv_reads_at_most_512_candles(kronos, monkeypatch):
    from datetime import datetime, timedelta
    big = [{"timestamp": (datetime(2026, 1, 1) + timedelta(hours=i)).isoformat(), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5} for i in range(560)]
    r = M.forecast_ohlcv(big, 1)
    assert r["ok"] and r["context_used"] == 512 and len(kronos.seen[-1][1]) == 512 and len(kronos.seen[-1][0]["close"]) == 512
    assert kronos.seen[-1][1][-1] == big[-1]["timestamp"]                                    # the most recent 512, not the oldest


def test_forecast_ohlcv_needs_future_timestamps_when_spacing_is_irregular(kronos):
    weekdays = [{**c, "timestamp": ts} for c, ts in zip(candles(8), ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12", "2026-01-13", "2026-01-14"])]
    r = M.forecast_ohlcv(weekdays, 2)
    assert r["ok"] is False and "pass future_timestamps with 2 ISO timestamps" in r["error"]
    ok = M.forecast_ohlcv(weekdays, 2, future_timestamps=["2026-01-15", "2026-01-16"])
    assert ok["ok"] and ok["timestamps"] == ["2026-01-15", "2026-01-16"]


@pytest.mark.parametrize("rows,msg", [
    ("x", "candles must be a list of 8 to 2048 rows"), (candles(3), "candles must be a list of 8 to 2048 rows"),
    ([5] * 8, "candles[0] needs an ISO 'timestamp' string"), ([{"open": 1}] * 8, "candles[0] needs an ISO 'timestamp' string"),
    ([{**c, "close": "x"} for c in candles(8)], "candles[0].close must be a finite number"),
    ([{**c, "high": True} for c in candles(8)], "candles[0].high must be a finite number"),
    ([{**c, "open": float("nan")} for c in candles(8)], "candles[0].open must be a finite number"),
    ([{**c, "volume": "lots"} for c in candles(8)], "candles[0].volume must be a finite number"),
    (candles(8, volume=True)[:7] + candles(8)[7:], "volume and amount must be given on every candle or on none"),
    ([{**c, "timestamp": "yesterday"} for c in candles(8)], "timestamps must be ISO 8601")])
def test_forecast_ohlcv_validates_candles(kronos, rows, msg):
    r = M.forecast_ohlcv(rows, 2)
    assert r["ok"] is False and msg in r["error"]
    assert kronos.seen == []


@pytest.mark.parametrize("future,msg", [(["2026-02-01"], "exactly 2 ISO timestamps"), ("x", "exactly 2 ISO timestamps"), ([1, 2], "exactly 2 ISO timestamps"),
                                        (["a", "b"], "future_timestamps must be ISO 8601")])
def test_forecast_ohlcv_validates_future_timestamps(kronos, future, msg):
    assert msg in M.forecast_ohlcv(candles(12), 2, future_timestamps=future)["error"]


def test_forecast_ohlcv_validates_horizon_and_samples(kronos):
    assert "horizon must be" in M.forecast_ohlcv(candles(12), 0)["error"]
    for bad in (0, 11, True, "2"):
        assert "samples must be an integer from 1 to 10" in M.forecast_ohlcv(candles(12), 2, samples=bad)["error"]


def test_forecast_ohlcv_when_kronos_is_not_installed(fm):
    r = M.forecast_ohlcv(candles(12), 2)
    assert r["ok"] is False and "Kronos is not installed" in r["error"]
    mark_ready(fm.home, "chronos-2", "kronos-base")
    mark_ready(fm.home, "chronos-2")                                                       # the explicit-name path: named but not installed
    assert "Kronos is not installed" in M.forecast_ohlcv(candles(12), 2)["error"]


def test_forecast_ohlcv_reports_model_errors(kronos, monkeypatch):
    def boom(spec):
        raise R.RunnerError("Kronos base failed to load: x")
    monkeypatch.setattr(M, "_loader", boom)
    assert M.forecast_ohlcv(candles(12), 2) == {"ok": False, "error": "Kronos base failed to load: x"}
    monkeypatch.setattr(M, "_loader", lambda spec: R.Loaded(spec, "cpu", ohlcv=lambda *a: (_ for _ in ()).throw(KeyError("close"))))
    assert M.forecast_ohlcv(candles(12), 2) == {"ok": False, "error": "KeyError: 'close'"}


def test_forecast_and_ohlcv_models_do_not_mix(kronos):
    assert "use forecast_ohlcv" in M.forecast(LINE, 2, "kronos-base")["error"]
    assert list(M.forecast(LINE, 2, "all")["models"]) == ["chronos-2"]                      # "all" leaves Kronos out


# ---------------------------------------------------------------- the process

def test_main_and_module_entrypoint_run_the_stdio_server(monkeypatch):
    from mcp.server.mcpserver import MCPServer
    ran = []
    monkeypatch.setattr(M.server, "run", lambda t: ran.append(t))
    M.main()
    monkeypatch.setattr(MCPServer, "run", lambda self, t: ran.append(t))
    runpy.run_module("app.mcp_ts", run_name="__main__")
    assert ran == ["stdio", "stdio"]


def test_real_stdio_server_lists_tools_and_reports_nothing_installed(tmp_path):
    """The actual `python -m app.mcp_ts` process over stdio: protocol, schemas and the not-installed path."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    async def go():
        env = {**os.environ, "PYTHONPATH": backend, "TS_HOME": str(tmp_path / "ts"), "TS_WORKSPACE": str(tmp_path)}
        params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_ts"], env=env)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = [t.name for t in (await s.list_tools()).tools]
                listed = json.loads((await s.call_tool("list_models", {})).content[0].text)
                res = json.loads((await s.call_tool("forecast", {"series": [float(i) for i in range(20)], "horizon": 3, "models": "all"})).content[0].text)
                return names, listed, res

    names, listed, res = asyncio.run(asyncio.wait_for(go(), 60))
    assert names == ["list_models", "forecast", "forecast_file", "compare", "forecast_ohlcv", "status"]
    assert listed["installed"] == [] and len(listed["models"]) == 8
    assert res["ok"] is False and "no time-series model is installed" in res["error"]
