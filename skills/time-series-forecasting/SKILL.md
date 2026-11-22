---
name: time-series-forecasting
group: Data
description: Forecast a numeric series (sales, traffic, load, prices, metrics) with local zero-shot foundation models via the "timeseries" MCP - one model, several, or all at once - with uncertainty bands, an ensemble and a backtest that shows which model is best on the user's own data. Use when asked to predict, project or forecast future values from history.
mcp: [timeseries]
---

# Time-series forecasting (local, zero-shot)

The `timeseries` MCP runs pretrained forecasting models **on this machine**: no training on the user's data, no API
cost, nothing leaves the PC. Tools (prefixed `timeseries_`): `list_models`, `forecast`, `forecast_file`, `compare`,
`forecast_ohlcv`, `status`.

If the tools are missing or say "not installed", tell the user to open **MCP servers > Time-series forecasting**, tick
the licence and install one or more models. Do not try to install anything yourself.

## Workflow
1. **`list_models` first.** See what is installed, each model's licence and limits. Never name a model you have not seen
   installed. A named model that is not installed comes back under `not_installed` - say so, do not pretend it ran.
2. **Check the data.** Oldest value first, no gaps (the tools reject NaN: fill or drop first and say how), at least 8 points,
   ideally several full cycles (a few weeks of daily data, a few days of hourly data). Pick `horizon` no longer than about
   a quarter to a half of the history - long horizons are mostly guesses.
3. **Choose models.** `models="all"` runs every installed general model; one id or a list runs exactly those. Different
   models make different mistakes, so with 2+ models compare them instead of trusting one.
4. **Ask for a backtest** (`compare`, or `forecast` with `backtest=true`) before presenting numbers the user will act on.
   It holds out the last `horizon` known values and scores each model: MAE, RMSE, sMAPE and MASE (below 1 beats a naive
   repeat-the-last-cycle guess). Set `season` to the steps in one cycle (7 for daily with a weekly pattern, 24 hourly with
   a daily one). One window is noisy: use it to rule models out, not to crown a winner.
5. **Report honestly.** Give the median forecast **and the band** (default 10-90%), which models ran, the ensemble if
   there is one, and the backtest result. Say what you could not do (`errors`, `not_installed`).

## Choosing a model (when the user wants one)
- **chronos-2**: strong all-round default; 21 quantiles; also fine on CPU.
- **timestral-5m / 28m / 209m**: small to large Mistral models; 209m is the most accurate, 5m the fastest.
- **toto-2-4m**: tiny and fast; built for infrastructure metrics.
- **ci-proto**: top benchmark scores and very long history; very new.
- **timesfm-3**: **non-commercial licence** - only for research and evaluation, never for work that makes money or feeds
  a production system. Say this before using it for anything that looks like business work.
- **kronos-base**: **candlesticks only** (open/high/low/close, optional volume) via `forecast_ohlcv`; never for ordinary
  series; it returns one path with no band. Not financial advice - say so.
- Timestral is not licensed for companies above US$20M monthly revenue: if the user's organisation might be that large,
  mention it before relying on it.

## Reading results
- `models.<id>.median` and `.quantiles["0.1"|"0.9"]` are lists with one value per future step. `approximate_levels` means
  that quantile was interpolated between the model's own levels.
- `ensemble` is the plain mean of the models' forecasts (only with 2+ models). It is often at least as good as the best
  single model, but check it in the backtest.
- `timestamps` (when you passed evenly spaced ISO timestamps) labels the future steps.
- `forecast_file` reads a CSV/TSV from the workspace: `path`, `column`, optional `timestamp_column`.
- `{ok:false, error}` means a problem with the input or setup: read the message, fix it, retry. Do not invent numbers.

## What these forecasts are not
Statistical extrapolations of the history. They cannot see events that are not in the data (promotions, outages,
holidays you did not encode, a market shock). Say when the history is short, very noisy or has a recent level change.
