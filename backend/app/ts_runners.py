"""Model adapters: one small function per library that turns "a list of numbers, a horizon, some quantile levels" into
"median + quantile paths". Runs inside the isolated time-series environment (data/envs/timeseries).

Deliberately free of numpy/pandas at import time (heavy libraries are imported only inside the adapters), so the server
logic and the tests do not need them. Weights load OFFLINE from the pinned revision the Install step downloaded.

The adapters follow each library's documented API (read from the pinned package sources). They have NOT been run against
the real weights on this machine.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any

from . import ts_spec


class RunnerError(Exception):
    """A model could not load or run. The message is shown to the user."""


# ---------------------------------------------------------------- helpers

def to_list(x: Any) -> Any:
    """Tensor / array / nested list -> plain nested lists of floats."""
    if hasattr(x, "detach"):
        x = x.detach()
    if hasattr(x, "cpu"):
        x = x.cpu()
    if hasattr(x, "tolist"):
        return x.tolist()
    return [to_list(v) for v in x] if isinstance(x, (list, tuple)) else x


def transpose(rows: list[list[float]]) -> list[list[float]]:
    return [list(col) for col in zip(*rows)]


def pick_levels(native: list[float], by_level: list[list[float]], wanted: list[float]) -> tuple[dict[float, list[float]], list[float]]:
    """From a model's own quantile grid (`by_level[i]` is the horizon path of `native[i]`) get the requested levels.

    A level between two native levels is linearly interpolated (per step); one outside the grid is clamped to the nearest
    native level. Returns (levels -> path, the levels that were not native)."""
    out: dict[float, list[float]] = {}
    approx: list[float] = []
    order = sorted(range(len(native)), key=native.__getitem__)
    lv = [native[i] for i in order]
    paths = [by_level[i] for i in order]
    for w in wanted:
        hit = next((i for i, v in enumerate(lv) if abs(v - w) < 1e-9), None)
        if hit is not None:
            out[w] = [float(v) for v in paths[hit]]
            continue
        approx.append(w)
        if w <= lv[0]:
            out[w] = [float(v) for v in paths[0]]
        elif w >= lv[-1]:
            out[w] = [float(v) for v in paths[-1]]
        else:
            hi = next(i for i, v in enumerate(lv) if v > w)
            lo = hi - 1
            frac = (w - lv[lo]) / (lv[hi] - lv[lo])
            out[w] = [float(a + (b - a) * frac) for a, b in zip(paths[lo], paths[hi])]
    return out, approx


class Loaded:
    """A model that is loaded and warm: call .forecast() (any series model) or .ohlcv() (Kronos)."""

    def __init__(self, spec: ts_spec.Model, device: str, series: Callable | None = None, ohlcv: Callable | None = None):
        self.spec, self.device = spec, device
        self._series, self._ohlcv = series, ohlcv
        self.load_seconds = 0.0

    def forecast(self, values: list[float], horizon: int, levels: list[float]) -> dict:
        """{median, quantiles: {level: path}, approximate: [levels that were interpolated or clamped]}"""
        if self._series is None:
            raise RunnerError(f"{self.spec.label} reads candlesticks, not plain series: use forecast_ohlcv")
        native, table = self._series(values, horizon)                 # table: one path per native level
        picked, approx = pick_levels(native, table, sorted(set(levels) | {0.5}))
        return {"median": picked[0.5], "quantiles": {lv: picked[lv] for lv in levels}, "approximate": approx}

    def ohlcv(self, columns: dict[str, list[float]], stamps: list[str], future: list[str], samples: int) -> dict[str, list[float]]:
        if self._ohlcv is None:
            raise RunnerError(f"{self.spec.label} is a general series model: use forecast, not forecast_ohlcv")
        return self._ohlcv(columns, stamps, future, samples)


# ---------------------------------------------------------------- loading

def device_for(torch: Any) -> str:
    """cuda when available, else cpu. TS_DEVICE=cpu|cuda forces one."""
    forced = os.environ.get("TS_DEVICE", "").strip().lower()
    if forced in ("cpu", "cuda"):
        return forced
    return "cuda" if torch.cuda.is_available() else "cpu"


def snapshot(repo: str, revision: str, files: list[str]) -> str:
    """The local folder of the pinned revision, resolved OFFLINE from this environment's own cache."""
    os.environ["HF_HOME"] = str(ts_spec.hf_home())
    os.environ["HF_HUB_OFFLINE"] = "1"
    from huggingface_hub import snapshot_download
    try:
        return snapshot_download(repo_id=repo, revision=revision, cache_dir=str(ts_spec.hf_home() / "hub"), local_files_only=True, allow_patterns=files)
    except Exception as e:  # noqa: BLE001
        raise RunnerError(f"the weights of {repo} are not in the local cache ({type(e).__name__}): reinstall it from MCP servers > Time-series forecasting") from e


def use_source(m: ts_spec.Model) -> None:
    """Make a GitHub-sourced library importable: its fetched folder goes on sys.path."""
    assert m.source is not None
    path = str(ts_spec.src_dir() / m.source.dest / (m.source.keep if m.source.keep == "src" else ""))
    if not os.path.isdir(path):
        raise RunnerError(f"the {m.source.repo} source is missing from the environment: reinstall the model")
    if path not in sys.path:
        sys.path.insert(0, path)


def _chronos2(m: ts_spec.Model, path: str, device: str, torch: Any):
    from chronos import Chronos2Pipeline
    pipe = Chronos2Pipeline.from_pretrained(path, device_map=device)
    native = list(m.quantile_levels)

    def series(values, horizon):
        q, _mean = pipe.predict_quantiles([torch.tensor(values, dtype=torch.float32)], prediction_length=horizon, quantile_levels=native)
        return native, transpose(to_list(q[0])[0])               # (variates, horizon, levels) -> levels x horizon
    return series, None


def _timesfm3(m: ts_spec.Model, path: str, device: str, torch: Any):
    from timesfm3 import TimesFM3Forecaster
    fc = TimesFM3Forecaster.from_pretrained(path, device=device)
    native = [float(v) for v in fc.config.quantiles]

    def series(values, horizon):
        out = fc.predict(values, horizon, return_quantiles=True)
        return native, transpose(to_list(out.quantiles))         # (horizon, levels)
    return series, None


def _toto2(m: ts_spec.Model, path: str, device: str, torch: Any):
    from toto2 import Toto2Model
    model = Toto2Model.from_pretrained(path).to(device).eval()
    native = list(m.quantile_levels)

    def series(values, horizon):
        target = torch.as_tensor([[values]], dtype=torch.float32, device=device)            # (batch, variates, time)
        inputs = {"target": target, "target_mask": torch.ones_like(target, dtype=torch.bool),
                  "series_ids": torch.zeros(1, 1, dtype=torch.long, device=device)}
        q = model.forecast(inputs, horizon=horizon, decode_block_size=768, has_missing_values=False)
        return native, [lvl[0][0] for lvl in to_list(q)]         # (levels, batch, variates, horizon)
    return series, None


def _timestral(m: ts_spec.Model, path: str, device: str, torch: Any):
    use_source(m)
    from timestral import TimestralForecaster
    fc = TimestralForecaster.from_pretrained(path, device=device, local_files_only=True)

    def series(values, horizon):
        out = fc.forecast(values, prediction_length=horizon)
        return [float(v) for v in out.quantile_levels], transpose(to_list(out.quantiles)[0][0])      # (B, V, horizon, levels)
    return series, None


def _ciproto(m: ts_spec.Model, path: str, device: str, torch: Any):
    import continuousintelligence as ci
    from pathlib import Path
    fc = ci.load_model(Path(path), device=device)
    native = [float(v) for v in to_list(fc.quantiles)]

    def series(values, horizon):
        history = torch.as_tensor([values], dtype=torch.float32)                              # [variables, time]
        return native, to_list(fc.forecast(history, horizon=horizon))[0]                      # [variables, levels, horizon]
    return series, None


OHLCV_COLUMNS = ("open", "high", "low", "close", "volume", "amount")


def _kronos(m: ts_spec.Model, path: str, device: str, torch: Any):
    use_source(m)
    sys.modules.pop("model", None)                                # a generic name: make sure it is Kronos' own package
    from model import Kronos, KronosPredictor, KronosTokenizer
    import pandas as pd
    assert m.extra is not None
    tok_path = snapshot(m.extra.repo, m.extra.revision, list(m.extra.files))
    predictor = KronosPredictor(Kronos.from_pretrained(path), KronosTokenizer.from_pretrained(tok_path), device=device, max_context=512)

    def ohlcv(columns, stamps, future, samples):
        out = predictor.predict(df=pd.DataFrame(columns), x_timestamp=pd.Series(pd.to_datetime(stamps)), y_timestamp=pd.Series(pd.to_datetime(future)),
                                pred_len=len(future), T=1.0, top_p=0.9, sample_count=samples, verbose=False)
        return {c: [float(v) for v in out[c].tolist()] for c in OHLCV_COLUMNS if c in out.columns}
    return None, ohlcv


ADAPTERS: dict[str, Callable] = {"chronos2": _chronos2, "timesfm3": _timesfm3, "toto2": _toto2, "timestral": _timestral,
                                 "ciproto": _ciproto, "kronos": _kronos}


def load(m: ts_spec.Model) -> Loaded:
    """Load a model from the local cache onto the best device. Raises RunnerError with a user-facing message."""
    if not ts_spec.is_ready(m.id):
        raise RunnerError(f"{m.label} is not installed: open MCP servers > Time-series forecasting and click Install")
    try:
        import torch
        path = snapshot(m.repo, m.revision, list(m.files))
        device = device_for(torch)
        series, ohlcv = ADAPTERS[m.backend](m, path, device, torch)
    except RunnerError:
        raise
    except Exception as e:  # noqa: BLE001 - library, memory or device trouble is explained, not shown as a stack trace
        raise RunnerError(f"{m.label} failed to load: {type(e).__name__}: {str(e)[:300]}") from e
    return Loaded(m, device, series, ohlcv)


# ---------------------------------------------------------------- install-time check

def verify(model_id: str) -> dict:
    """Import the model's library inside the environment and report versions. Raises on failure (the installer shows it)."""
    import importlib
    import importlib.metadata as md
    import torch
    m = ts_spec.model(model_id)
    if m.source:
        use_source(m)
    module = {"chronos2": "chronos", "timesfm3": "timesfm3", "toto2": "toto2", "timestral": "timestral",
              "ciproto": "continuousintelligence", "kronos": "model"}[m.backend]
    importlib.import_module(module)
    pins = [p.split("==")[0] for p in m.pip if p.startswith(("chronos", "timesfm", "toto", "continuous"))]
    lib = md.version(pins[0]) if pins else f"{m.source.repo}@{m.source.sha[:12]}" if m.source else ""
    return {"model": model_id, "torch": torch.__version__, "library": lib, "cuda": bool(torch.cuda.is_available()),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "verify":
        print("usage: python -m app.ts_runners verify <model id>", flush=True)
        return 2
    print(json.dumps(verify(args[1])), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
