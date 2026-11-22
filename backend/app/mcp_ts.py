"""Zero-shot time-series forecasting as an MCP server (stdio): several pretrained foundation models, run LOCALLY.

"Zero-shot" = no training on your data: you pass the history of a series, the models forecast the next `horizon` steps with
uncertainty bands. You choose which models run - one, any subset, or all installed - and get each model's forecast side by
side, an ensemble (mean of the models), and, if you ask, a backtest (the models forecast the last `horizon` points you
already have, so you can see which one is most accurate on YOUR data).

Models that are not installed are reported as not installed - never skipped silently and never faked. Kronos is a
candlestick (OHLCV) model and has its own tool, forecast_ohlcv.

Runs inside the ISOLATED environment created by the Install step (data/envs/timeseries), never in the backend venv.
Launch:  <env python> -m app.mcp_ts   (env: TS_HOME=<env folder>, TS_WORKSPACE=<workspace>, PYTHONPATH=<backend>)
"""
from __future__ import annotations

import csv
import gc
import math
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import ts_runners, ts_spec

server = MCPServer("agent-studio-timeseries", instructions=(
    "Zero-shot time-series forecasting with local foundation models (Chronos-2, TimesFM 3, Toto 2, Timestral, ci-proto; Kronos for "
    "candlesticks). Give the history as a list of numbers and a horizon; choose models=\"all\", one id, or a list of ids. Use "
    "list_models first to see what is installed. Use compare (or forecast with backtest=true) to see which model is best on the "
    "user's own data before trusting one. Forecasts are statistical estimates, not facts: always show the uncertainty band. "
    "Tools: list_models, forecast, forecast_file, compare, forecast_ohlcv, status."))

MIN_VALUES = 8
MAX_VALUES = 100_000
MAX_HORIZON = 5_000
MAX_LEVELS = 9
MAX_CANDLES = 2_048
MAX_FILE_BYTES = 50_000_000
DEFAULT_LEVELS = [0.1, 0.5, 0.9]
SERIES_SUFFIXES = (".csv", ".tsv", ".txt")
REQUIRED_CANDLE_COLUMNS = ("open", "high", "low", "close")

_loader = None                       # tests inject a fake: callable(spec) -> ts_runners.Loaded
_loaded: "OrderedDict[str, ts_runners.Loaded]" = OrderedDict()
_lock = threading.RLock()            # one forward pass at a time; also guards loading


def _fail(msg: str, **extra: Any) -> dict:
    return {"ok": False, "error": msg, **extra}


def max_loaded() -> int:
    try:
        return max(1, int(os.environ.get("TS_MAX_LOADED", "3")))
    except ValueError:
        return 3


def _get(spec: ts_spec.Model) -> tuple[ts_runners.Loaded, bool]:
    """The warm model (loading it on first use, evicting the least recently used when too many are loaded)."""
    with _lock:
        if spec.id in _loaded:
            _loaded.move_to_end(spec.id)
            return _loaded[spec.id], False
        t0 = time.perf_counter()
        loaded = (_loader or ts_runners.load)(spec)
        loaded.load_seconds = round(time.perf_counter() - t0, 2)
        _loaded[spec.id] = loaded
        while len(_loaded) > max_loaded():
            _loaded.popitem(last=False)
            gc.collect()
        return loaded, True


# ---------------------------------------------------------------- inputs

def _numbers(values: Any, what: str = "series") -> tuple[list[float] | None, str | None]:
    if not isinstance(values, list) or not values:
        return None, f"{what} must be a non-empty list of numbers"
    if len(values) > MAX_VALUES:
        return None, f"{what} has {len(values)} values; the limit is {MAX_VALUES}"
    out = []
    for i, v in enumerate(values):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None, f"{what}[{i}] is not a finite number ({v!r}): fill or remove missing values first"
        out.append(float(v))
    return out, None


def _horizon(h: Any) -> tuple[int | None, str | None]:
    if isinstance(h, bool) or not isinstance(h, int) or not 1 <= h <= MAX_HORIZON:
        return None, f"horizon must be an integer from 1 to {MAX_HORIZON}"
    return h, None


def _levels(q: Any) -> tuple[list[float] | None, str | None]:
    if q is None:
        return list(DEFAULT_LEVELS), None
    if not isinstance(q, list) or not q or len(q) > MAX_LEVELS:
        return None, f"quantiles must be a list of 1 to {MAX_LEVELS} levels between 0 and 1, e.g. [0.1, 0.5, 0.9]"
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 1 for v in q):
        return None, "every quantile level must be a number strictly between 0 and 1"
    return sorted({float(v) for v in q}), None


def select_models(models: Any, kind: str = "series") -> tuple[list[ts_spec.Model] | None, list[str], str | None]:
    """Resolve "all" / one id / a list of ids to specs. Returns (specs to run, not-installed ids, error).
    "all" means every INSTALLED model of that kind; named models that are not installed are reported, not dropped silently."""
    if models in (None, "", "all"):
        names = ["all"]
    elif isinstance(models, str):
        names = [models]
    elif isinstance(models, list) and models and all(isinstance(m, str) for m in models):
        names = list(dict.fromkeys(models))
    else:
        return None, [], 'models must be "all", a model id, or a list of model ids'
    if names == ["all"]:
        run = [m for m in ts_spec.MODELS.values() if m.kind == kind and ts_spec.is_ready(m.id)]
        if not run:
            return None, [], ("no time-series model is installed: open MCP servers > Time-series forecasting in Agent Studio and install one"
                              if kind == "series" else "Kronos is not installed: open MCP servers > Time-series forecasting and install it")
        return run, [], None
    unknown = [n for n in names if n not in ts_spec.MODELS]
    if unknown:
        return None, [], f"unknown model(s) {', '.join(unknown)}; use one of: {', '.join(ts_spec.MODELS)}"
    wrong = [n for n in names if ts_spec.MODELS[n].kind != kind]
    if wrong:
        tool = "forecast_ohlcv" if kind == "series" else "forecast"
        return None, [], f"{', '.join(wrong)} cannot be used here: use {tool} for it"
    specs = [ts_spec.MODELS[n] for n in names]
    return [m for m in specs if ts_spec.is_ready(m.id)], [m.id for m in specs if not ts_spec.is_ready(m.id)], None


# ---------------------------------------------------------------- numbers

def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def metrics(actual: list[float], pred: list[float], history: list[float], season: int = 1) -> dict:
    """MAE, RMSE, sMAPE (%) and MASE of a forecast against what really happened. MASE scales by the in-sample seasonal-naive error
    (None when the history is too short or constant)."""
    err = [a - p for a, p in zip(actual, pred)]
    denom = [(abs(a) + abs(p)) / 2 for a, p in zip(actual, pred)]
    smape = mean([abs(e) / d if d else 0.0 for e, d in zip(err, denom)]) * 100
    scale = mean([abs(history[i] - history[i - season]) for i in range(season, len(history))]) if len(history) > season else 0.0
    return {"mae": round(mean([abs(e) for e in err]), 6), "rmse": round(math.sqrt(mean([e * e for e in err])), 6),
            "smape_pct": round(smape, 4), "mase": round(mean([abs(e) for e in err]) / scale, 6) if scale > 0 else None}


def ensemble(results: dict[str, dict], levels: list[float]) -> dict | None:
    """Mean across the models that produced a forecast (needs two or more). Per step, mean of the medians and of every quantile path."""
    if len(results) < 2:
        return None
    paths = list(results.values())
    h = len(paths[0]["median"])
    return {"method": "mean of the models' medians and quantiles", "members": list(results),
            "median": [round(mean([p["median"][i] for p in paths]), 6) for i in range(h)],
            "quantiles": {str(lv): [round(mean([p["quantiles"][str(lv)][i] for p in paths]), 6) for i in range(h)] for lv in levels}}


def future_stamps(stamps: list[str] | None, n: int) -> list[str] | None:
    """The next n timestamps, when the given ones are evenly spaced (constant step). None otherwise."""
    if not stamps or len(stamps) < 2:
        return None
    try:
        ts = [datetime.fromisoformat(s) for s in stamps]
    except (TypeError, ValueError):
        return None
    steps = {b - a for a, b in zip(ts, ts[1:])}
    if len(steps) != 1 or next(iter(steps)) <= timedelta(0):
        return None
    step = next(iter(steps))
    return [(ts[-1] + step * (i + 1)).isoformat() for i in range(n)]


def _round(path: list[float]) -> list[float]:
    return [round(v, 6) for v in path]


# ---------------------------------------------------------------- running models

def _run_one(spec: ts_spec.Model, values: list[float], horizon: int, levels: list[float]) -> dict:
    """Forecast with one model. Raises ts_runners.RunnerError (or any model error) - callers turn it into a per-model message."""
    loaded, fresh = _get(spec)
    t0 = time.perf_counter()
    with _lock:
        out = loaded.forecast(values, horizon, levels)
    got = {"label": spec.label, "median": _round(out["median"]), "quantiles": {str(lv): _round(out["quantiles"][lv]) for lv in levels},
           "device": loaded.device, "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "loaded_now": fresh}
    if len(got["median"]) != horizon:
        raise ts_runners.RunnerError(f"{spec.label} returned {len(got['median'])} steps instead of {horizon}")
    if out.get("approximate"):
        got["approximate_levels"] = [str(v) for v in out["approximate"]]
    if spec.max_context and len(values) > spec.max_context:
        got["note"] = f"{spec.label} reads at most {spec.max_context} steps of history; older values were ignored"
    if fresh:
        got["load_seconds"] = loaded.load_seconds
    return got


def _run_many(specs: list[ts_spec.Model], values: list[float], horizon: int, levels: list[float]) -> tuple[dict, dict]:
    ok: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for spec in specs:
        try:
            ok[spec.id] = _run_one(spec, values, horizon, levels)
        except Exception as e:  # noqa: BLE001 - one model failing must not hide the others
            errors[spec.id] = f"{type(e).__name__}: {str(e)[:300]}" if not isinstance(e, ts_runners.RunnerError) else str(e)[:300]
    return ok, errors


def _backtest(specs: list[ts_spec.Model], values: list[float], horizon: int, levels: list[float], season: int) -> dict:
    """Hold out the last `horizon` points, forecast them from the rest, score each model (and the ensemble)."""
    context, truth = values[:-horizon], values[-horizon:]
    if len(context) < MIN_VALUES:
        return {"ok": False, "error": f"a backtest needs at least {MIN_VALUES} values before the {horizon} held-out ones (got {len(context)})"}
    ok, errors = _run_many(specs, context, horizon, levels)
    scores = {mid: metrics(truth, r["median"], context, season) for mid, r in ok.items()}
    ens = ensemble(ok, levels)
    if ens:
        scores["ensemble"] = metrics(truth, ens["median"], context, season)
    ranked = sorted(scores, key=lambda k: (scores[k]["mase"] if scores[k]["mase"] is not None else scores[k]["mae"]))
    return {"ok": True, "holdout": horizon, "context_used": len(context), "season": season, "scores": scores,
            "ranking": ranked, "ranked_by": "mase" if all(s["mase"] is not None for s in scores.values()) else "mae",
            "errors": errors,
            "caution": "One holdout window is a noisy estimate: a model that wins here may not win next month. Use it to rule models out, not to crown one."}


# ---------------------------------------------------------------- tools

def _prepare(series: Any, horizon: Any, models: Any, quantiles: Any, timestamps: Any, season: Any) -> tuple:
    """Validate everything and pick the models. Returns (values, horizon, levels, specs, not_installed, error)."""
    values, err = _numbers(series)
    h, err2 = _horizon(horizon)
    levels, err3 = _levels(quantiles)
    err = err or err2 or err3
    if not err and len(values or []) < MIN_VALUES:
        err = f"series needs at least {MIN_VALUES} values (got {len(values or [])})"
    if not err and (isinstance(season, bool) or not isinstance(season, int) or not 1 <= season <= 400):
        err = "season must be an integer from 1 to 400 (the number of steps in one cycle, e.g. 7 for daily data with a weekly pattern)"
    if not err and timestamps is not None and (not isinstance(timestamps, list) or len(timestamps) != len(values)):
        err = "timestamps must be a list with one ISO timestamp per value"
    if err:
        return None, None, None, None, [], err
    specs, missing, err = select_models(models)
    if not err and not specs:
        err = "none of the requested models is installed: open MCP servers > Time-series forecasting and install them"
    return values, h, levels, specs, missing, err


def run_forecast(series: Any, horizon: Any, models: Any = "all", quantiles: Any = None, timestamps: Any = None,
                 backtest: bool = False, season: Any = 1) -> dict:
    """Validate, run the chosen models, return {ok, models, ensemble, not_installed, errors, backtest?}. Never raises."""
    values, h, levels, specs, missing, err = _prepare(series, horizon, models, quantiles, timestamps, season)
    if err:
        return _fail(err, **({"not_installed": missing} if missing else {}))
    ok, errors = _run_many(specs, values, h, levels)
    if not ok:
        return _fail("every selected model failed", errors=errors, not_installed=missing)
    out: dict[str, Any] = {"ok": True, "horizon": h, "n_context": len(values), "quantile_levels": levels, "models": ok,
                           "ensemble": ensemble(ok, levels), "not_installed": missing, "errors": errors}
    future = future_stamps(timestamps, h) if timestamps else None
    if future:
        out["timestamps"] = future
    if backtest:
        out["backtest"] = _backtest(specs, values, h, levels, season)
    return out


@server.tool(description=(
    "List the zero-shot forecasting models: which are installed, their licence (some forbid commercial use), size, context and "
    "horizon limits, and what each is good for. Does not load any model."))
def list_models() -> dict:
    rows = []
    for m in ts_spec.MODELS.values():
        info = ts_spec.public(m)
        rows.append({"id": m.id, "label": m.label, "installed": ts_spec.is_ready(m.id), "kind": m.kind, "params": m.params,
                     "loaded": m.id in _loaded, "download_mb": round(m.download_bytes / 1e6), "max_context": m.max_context,
                     "max_horizon": m.max_horizon, "quantile_levels": info["quantile_levels"], "license": m.license_name,
                     "commercial_use": m.commercial, "good_for": m.good_for, "caveats": m.caveats, "install_blocked": info["blocked"]})
    return {"ok": True, "models": rows, "installed": [r["id"] for r in rows if r["installed"]]}


@server.tool(description=(
    "Forecast one numeric series with one or several models at once. series: list of numbers (oldest first, no gaps; 8+ values). "
    "horizon: steps ahead. models: \"all\" (every installed general model), one id (\"chronos-2\"), or a list of ids. "
    "quantiles: levels for the uncertainty band, default [0.1,0.5,0.9]. timestamps: optional ISO timestamps, one per value, to "
    "get the future timestamps when evenly spaced. backtest=true also scores each model (and the ensemble) on the last `horizon` "
    "points you already have, so you can see which is most accurate on this data; season = steps per cycle for MASE. Returns per-model "
    "median + quantile paths, an ensemble (mean) when 2+ models ran, not_installed (named but not installed) and errors."))
def forecast(series: list[float], horizon: int, models: Any = "all", quantiles: list[float] | None = None,
             timestamps: list[str] | None = None, backtest: bool = False, season: int = 1) -> dict:
    return run_forecast(series, horizon, models, quantiles, timestamps, backtest, season)


def read_series_file(path: Any, column: Any, timestamp_column: Any = "", workspace: str | None = None) -> tuple[list[float] | None, list[str] | None, str | None]:
    """Read one numeric column (and optionally a timestamp column) from a CSV/TSV inside the workspace."""
    root = Path(workspace if workspace is not None else os.environ.get("TS_WORKSPACE") or ".").resolve()
    if not isinstance(path, str) or not path.strip() or not isinstance(column, str) or not column.strip():
        return None, None, "path and column are required"
    f = (root / path).resolve()
    if root != f and root not in f.parents:
        return None, None, "the file must be inside the workspace folder"
    if f.suffix.lower() not in SERIES_SUFFIXES or not f.is_file():
        return None, None, f"not a {'/'.join(SERIES_SUFFIXES)} file in the workspace: {path}"
    if f.stat().st_size > MAX_FILE_BYTES:
        return None, None, f"file too large (max {MAX_FILE_BYTES // 1_000_000} MB)"
    values: list[float] = []
    stamps: list[str] = []
    try:
        with f.open(newline="", encoding="utf-8-sig") as fh:
            rd = csv.DictReader(fh, delimiter="\t" if f.suffix.lower() == ".tsv" else ",")
            need = [column] + ([timestamp_column] if timestamp_column else [])
            missing = [c for c in need if c not in (rd.fieldnames or [])]
            if missing:
                return None, None, f"column(s) not in the file: {', '.join(missing)} (it has: {', '.join(rd.fieldnames or [])})"
            for line, row in enumerate(rd, start=2):
                raw = (row.get(column) or "").strip()
                try:
                    v = float(raw)
                except ValueError:
                    return None, None, f"line {line}: '{raw}' in column {column} is not a number (fill or remove missing values first)"
                if not math.isfinite(v):
                    return None, None, f"line {line}: '{raw}' in column {column} is not a finite number"
                values.append(v)
                if timestamp_column:
                    stamps.append((row.get(timestamp_column) or "").strip())
                if len(values) > MAX_VALUES:
                    return None, None, f"more than {MAX_VALUES} rows; send the most recent part"
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        return None, None, f"cannot read {path}: {type(e).__name__}"
    return values, (stamps or None), None


@server.tool(description=(
    "Like forecast, but reads the series from a CSV/TSV file inside the workspace: path (relative to the workspace), column "
    "(the numeric column to forecast) and optionally timestamp_column (ISO timestamps, used to label the future steps)."))
def forecast_file(path: str, column: str, horizon: int, models: Any = "all", quantiles: list[float] | None = None,
                  timestamp_column: str = "", backtest: bool = False, season: int = 1) -> dict:
    values, stamps, err = read_series_file(path, column, timestamp_column)
    if err:
        return _fail(err)
    return run_forecast(values, horizon, models, quantiles, stamps, backtest, season)


@server.tool(description=(
    "Which model is most accurate on THIS series? Holds out the last `horizon` values, forecasts them with the chosen models "
    "(\"all\" installed, one id, or a list), and returns MAE, RMSE, sMAPE and MASE per model and for the ensemble, plus a ranking. "
    "season: steps per cycle (7 for daily data with a weekly pattern). One window is a noisy estimate."))
def compare(series: list[float], horizon: int, models: Any = "all", season: int = 1) -> dict:
    values, h, levels, specs, missing, err = _prepare(series, horizon, models, [0.5], None, season)
    if err:
        return _fail(err, **({"not_installed": missing} if missing else {}))
    bt = _backtest(specs, values, h, levels, season)
    return {**bt, "not_installed": missing} if bt["ok"] else bt


# ---- Kronos (candlesticks)

def _candles(rows: Any, horizon: int, future: Any) -> tuple[dict | None, list[str] | None, list[str] | None, str | None]:
    if not isinstance(rows, list) or len(rows) < MIN_VALUES or len(rows) > MAX_CANDLES:
        return None, None, None, f"candles must be a list of {MIN_VALUES} to {MAX_CANDLES} rows {{timestamp, open, high, low, close, volume?, amount?}}"
    cols: dict[str, list[float]] = {c: [] for c in REQUIRED_CANDLE_COLUMNS}
    stamps: list[str] = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or not isinstance(r.get("timestamp"), str):
            return None, None, None, f"candles[{i}] needs an ISO 'timestamp' string"
        stamps.append(r["timestamp"])
        for c in REQUIRED_CANDLE_COLUMNS:
            v = r.get(c)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                return None, None, None, f"candles[{i}].{c} must be a finite number"
            cols[c].append(float(v))
        for c in ("volume", "amount"):
            if c in r:
                v = r[c]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    return None, None, None, f"candles[{i}].{c} must be a finite number"
                cols.setdefault(c, []).append(float(v))
    if any(0 < len(v) < len(rows) for v in cols.values()):
        return None, None, None, "volume and amount must be given on every candle or on none"
    try:
        [datetime.fromisoformat(s) for s in stamps]
    except ValueError:
        return None, None, None, "timestamps must be ISO 8601 (for example 2026-01-05T09:30:00)"
    if future is not None:
        if not isinstance(future, list) or len(future) != horizon or not all(isinstance(s, str) for s in future):
            return None, None, None, f"future_timestamps must be a list of exactly {horizon} ISO timestamps"
        try:
            [datetime.fromisoformat(s) for s in future]
        except ValueError:
            return None, None, None, "future_timestamps must be ISO 8601"
        return cols, stamps, future, None
    nxt = future_stamps(stamps, horizon)
    if nxt is None:
        return None, None, None, ("the candles are not evenly spaced (weekends, holidays, gaps), so the future timestamps cannot be inferred: "
                                  f"pass future_timestamps with {horizon} ISO timestamps")
    return cols, stamps, nxt, None


@server.tool(description=(
    "Forecast CANDLESTICKS (open/high/low/close, optional volume/amount) with Kronos, a model trained only on financial K-lines - "
    "not a general series model and not financial advice. candles: rows {timestamp (ISO), open, high, low, close, volume?, amount?}, "
    "oldest first, 8-2048 rows (only the last 512 are read). horizon: bars ahead. future_timestamps: needed when the candles are not "
    "evenly spaced (e.g. daily bars with weekends). samples: paths averaged (more = slower, smoother). Returns one forecast path per "
    "column, no uncertainty band."))
def forecast_ohlcv(candles: list[dict], horizon: int, future_timestamps: list[str] | None = None, samples: int = 1) -> dict:
    h, err = _horizon(horizon)
    if not err and (isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= 10):
        err = "samples must be an integer from 1 to 10"
    cols, stamps, future, cerr = (None, None, None, None) if err else _candles(candles, h, future_timestamps)
    err = err or cerr
    if err:
        return _fail(err)
    specs, missing, _ = select_models(["kronos-base"], kind="ohlcv")          # a known id of the right kind: never an error
    if not specs:
        return _fail("Kronos is not installed: open MCP servers > Time-series forecasting and click Install on Kronos base", not_installed=missing)
    spec = specs[0]
    try:
        loaded, fresh = _get(spec)
        t0 = time.perf_counter()
        with _lock:
            out = loaded.ohlcv({c: v[-512:] for c, v in cols.items()}, stamps[-512:], future, samples)
    except Exception as e:  # noqa: BLE001
        return _fail(str(e)[:300] if isinstance(e, ts_runners.RunnerError) else f"{type(e).__name__}: {str(e)[:300]}")
    return {"ok": True, "model": spec.id, "horizon": h, "timestamps": future, "forecast": {c: _round(v) for c, v in out.items()},
            "context_used": min(len(stamps), 512), "samples": samples, "device": loaded.device,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "loaded_now": fresh,
            "caution": "Statistical estimate from a model trained on market candles. Not financial advice."}


@server.tool(description="Which forecasting models are installed and loaded, and what the licences allow. Does not load any model.")
def status() -> dict:
    return {"ok": True, "installed": ts_spec.installed_ids(), "loaded": list(_loaded), "max_loaded": max_loaded(),
            "licences": {m.id: {"name": m.license_name, "commercial_use": m.commercial, "url": m.license_url} for m in ts_spec.MODELS.values()
                         if ts_spec.is_ready(m.id)}}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
