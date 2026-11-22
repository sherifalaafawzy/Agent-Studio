"""Static facts about the local zero-shot time-series forecasting capability: the catalog of models (pinned Hugging Face
revisions, exact file sizes, licences, how each is installed), paths and install state. STDLIB ONLY on purpose - it is
imported by the backend *and* by the isolated model environment (which has no FastAPI).

Every entry was checked against huggingface.co / PyPI / GitHub on 2026-11-21 (see `checked`). Nothing here is downloaded
until the user clicks Install in MCP servers > Time-series forecasting.

Security: weights are safetensors (or, for ci-proto, a torch checkpoint loaded with weights_only=True). No model repo here
needs trust_remote_code. Two libraries (Timestral, Kronos) are not on PyPI: their source is fetched at a pinned commit and
only added to the environment's import path, never installed with build scripts.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .db import data_dir

MCP_ID = "timeseries"
CHECKED = "2026-11-21"
AGE_DAYS = 14                                    # no package (or pin) younger than this is installed
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
PIP_VERSION = "26.2.2"                           # 2026-10-20; needed for --uploaded-prior-to

# Pinned, each published >= 14 days before CHECKED. Everything else resolves to the newest release at least 14 days old.
BASE_PINS = ["numpy==2.5.4", "pandas==2.3.3", "safetensors==0.8.0", "huggingface_hub==2.4.0", "einops==0.8.2", "tqdm==4.70.1",
             "mcp==2.3.0"]
BASE_LIBS_MB = 220
REPLACE_TRIES = 6
VENV_MB = 20


@dataclass(frozen=True)
class Source:
    """A GitHub repository fetched at one commit (it has no PyPI release) and used straight from its folder."""
    repo: str                    # owner/name
    sha: str                     # the exact commit
    dest: str                    # folder under <home>/src/
    keep: str                    # the sub-folder of the archive that holds the importable package ("src" or "model")
    committed: str               # commit date (YYYY-MM-DD): must be >= 14 days old
    zip_bytes: int               # size of the archive download


@dataclass(frozen=True)
class Extra:
    """A second Hugging Face repo a model needs (Kronos' tokenizer)."""
    repo: str
    revision: str
    files: dict[str, int]


@dataclass(frozen=True)
class Model:
    id: str
    label: str
    repo: str
    revision: str
    backend: str                              # which adapter in ts_runners runs it
    files: dict[str, int]                     # file name -> bytes, exactly what is downloaded
    params: str
    license_name: str
    license_url: str
    license_summary: tuple[str, ...]
    commercial: str                           # "yes" | "restricted" | "no"
    good_for: str
    caveats: str
    kind: str = "series"                      # "series" (any numeric series) | "ohlcv" (candlesticks only)
    pip: tuple[str, ...] = ()                 # extra exact pins installed for this model
    pip_released: str = ""                    # upload date of the newest of those pins
    pip_mb: int = 0
    source: Source | None = None
    extra: Extra | None = None
    max_context: int | None = None
    max_horizon: int | None = None
    quantile_levels: tuple[float, ...] = ()
    multivariate: bool = False
    covariates: bool = False
    python_min: tuple[int, int] = (3, 10)
    notes: tuple[str, ...] = ()
    skipped: str = ""                         # a file in the repo we deliberately do not download, and why

    @property
    def weights_bytes(self) -> int:
        return sum(self.files.values()) + (sum(self.extra.files.values()) if self.extra else 0)

    @property
    def download_bytes(self) -> int:
        return self.weights_bytes + (self.source.zip_bytes if self.source else 0)

    @property
    def repos(self) -> list[tuple[str, str, list[str]]]:
        out = [(self.repo, self.revision, list(self.files))]
        if self.extra:
            out.append((self.extra.repo, self.extra.revision, list(self.extra.files)))
        return out


def _q(*levels: float) -> tuple[float, ...]:
    return tuple(levels)


Q9 = _q(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
Q21 = _q(0.01, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.99)

APACHE = ("Free to use, modify and redistribute, including commercially (Apache License 2.0).",
          "Keep the licence and notices if you redistribute; patent licence ends if you sue over the Work.",
          "Provided AS IS, no warranty. Plain-language summary, not legal advice.")

TIMESTRAL_LICENSE = (
    "Modified MIT: free to use, copy, modify and distribute the weights.",
    "NOT licensed if your company's (or your employer's) global consolidated MONTHLY revenue exceeds US$20 million for the "
    "preceding month; Mistral AI can grant a commercial licence separately.",
    "Keep the attribution notice and licence in copies. Provided AS IS. Plain-language summary, not legal advice.")

_TS_SRC = Source("mistralai/timestral", "a534bdbd244d64f3d9d743f46c7bf6fc330ed4ff", "timestral", "src", "2026-10-15", 321_544)
_KRONOS_SRC = Source("shiyu-coder/Kronos", "67b630e67f6a18c9e9be918d9b4337c960db1e9a", "kronos", "model", "2026-04-13", 9_458_415)

MODELS: dict[str, Model] = {m.id: m for m in [
    Model(
        id="chronos-2", label="Chronos-2 (Amazon)", repo="amazon/chronos-2", revision="29ec3766d36d6f73f0696f85560a422f50e8498c",
        backend="chronos2", files={"config.json": 1_067, "model.safetensors": 477_930_472}, params="120M",
        license_name="Apache-2.0", license_url="https://huggingface.co/amazon/chronos-2/blob/main/README.md", license_summary=APACHE,
        commercial="yes", pip=("chronos-forecasting==2.3.2", "transformers==5.22.2", "accelerate==1.15.0"), pip_released="2026-09-08", pip_mb=150,
        max_context=8192, max_horizon=1024, quantile_levels=Q21, multivariate=True, covariates=True,
        good_for="A strong general default: univariate, multivariate and covariate-aware forecasting, 21 quantiles, runs on CPU.",
        caveats="Covariates and multivariate inputs are supported by the model but this MCP forecasts one series at a time.",
    ),
    Model(
        id="timesfm-3", label="TimesFM 3.0 (Google)", repo="google/timesfm-3.0-pytorch", revision="43046b85ec22d584a13f8098c2ed39c889e129c2",
        backend="timesfm3", files={"config.json": 1_273, "model.safetensors": 1_322_898_824, "LICENSE": 7_270}, params="about 330M (1.3 GB in fp32)",
        license_name="TimesFM Non-Commercial License v1.0", license_url="https://huggingface.co/google/timesfm-3.0-pytorch/blob/main/LICENSE",
        license_summary=(
            "Weights are free ONLY for non-commercial, non-production use: testing, evaluation, research, internal benchmarking.",
            "Not allowed: any revenue-generating activity, use with end users or in production systems, using results in "
            "commercial decisions, client deliverables or paid products, or training models for commercial use.",
            "You may not distribute the model or derivatives. Commercial use needs a separate licence from Google.",
            "Provided AS IS. Plain-language summary, not legal advice - read the full licence."),
        commercial="no", pip=("timesfm==3.0.2",), pip_released="2026-09-09", pip_mb=5,
        max_context=15360, max_horizon=None, quantile_levels=Q9, multivariate=True, covariates=True,
        good_for="Research and evaluation of a large decoder-only model; long contexts (about 15K steps).",
        caveats="Non-commercial licence: do not use it for work that makes money or feeds a production system.",
    ),
    Model(
        id="toto-2-4m", label="Toto 2.0 4M (Datadog)", repo="Datadog/Toto-2.0-4m", revision="8306a9801cf98c0f5ffe4b2dcc8f496e616d84d9",
        backend="toto2", files={"config.json": 592, "model.safetensors": 16_582_848}, params="4M",
        license_name="Apache-2.0", license_url="https://huggingface.co/Datadog/Toto-2.0-4m/blob/main/README.md", license_summary=APACHE,
        commercial="yes", pip=("toto-2==2.0.0",), pip_released="2026-06-04", pip_mb=300, python_min=(3, 12),
        max_context=None, max_horizon=None, quantile_levels=Q9, multivariate=True,
        good_for="Tiny (16 MB) and fast on CPU; built for observability metrics (latency, traffic, CPU) but a general forecaster.",
        caveats="Needs Python 3.12+. The only size installed here; larger Toto 2.0 checkpoints (22m to 2.5B) exist but are not offered.",
    ),
    Model(
        id="timestral-5m", label="Timestral 5M (Mistral)", repo="mistralai/Timestral-5M", revision="bdd20cfc15a3fea0e4cb5ccef37f8d3ca7aa335f",
        backend="timestral", files={"config.json": 904, "model.safetensors": 20_843_960, "LICENSE": 1_695}, params="5M",
        license_name="Modified MIT (Mistral AI)", license_url="https://huggingface.co/mistralai/Timestral-5M/blob/main/LICENSE",
        license_summary=TIMESTRAL_LICENSE, commercial="restricted", source=_TS_SRC,
        max_context=32768, max_horizon=1024, quantile_levels=Q21, multivariate=True, covariates=True,
        good_for="The smallest Timestral: 21 MB, fast on CPU, up to 32K steps of history.",
        caveats="Source code comes from GitHub at a pinned commit (no PyPI release).",
        notes=("Horizons over 1024 steps are forecast block by block, feeding each block's median back in.",),
    ),
    Model(
        id="timestral-28m", label="Timestral 28M (Mistral)", repo="mistralai/Timestral-28M", revision="49b26409ac87b5935539a5d8ffe7f397b720db47",
        backend="timestral", files={"config.json": 961, "model.safetensors": 111_296_464, "LICENSE": 1_695}, params="28M",
        license_name="Modified MIT (Mistral AI)", license_url="https://huggingface.co/mistralai/Timestral-28M/blob/main/LICENSE",
        license_summary=TIMESTRAL_LICENSE, commercial="restricted", source=_TS_SRC,
        max_context=32768, max_horizon=1024, quantile_levels=Q21, multivariate=True, covariates=True,
        good_for="Balanced Timestral: 111 MB, better accuracy than 5M, still comfortable on CPU.",
        caveats="Source code comes from GitHub at a pinned commit (no PyPI release).",
    ),
    Model(
        id="timestral-209m", label="Timestral 209M (Mistral)", repo="mistralai/Timestral-209M", revision="9874d252ab0aae16f7f7c999f98f98d6ad7d8365",
        backend="timestral", files={"config.json": 1_130, "model.safetensors": 837_271_648, "LICENSE": 1_695}, params="209M",
        license_name="Modified MIT (Mistral AI)", license_url="https://huggingface.co/mistralai/Timestral-209M/blob/main/LICENSE",
        license_summary=TIMESTRAL_LICENSE, commercial="restricted", source=_TS_SRC,
        max_context=32768, max_horizon=1024, quantile_levels=Q21, multivariate=True, covariates=True,
        good_for="The most accurate Timestral (Mistral reports it first on GIFT-Eval, TIME and fev-bench).",
        caveats="837 MB; a GPU helps for long histories. Source code comes from GitHub at a pinned commit.",
    ),
    Model(
        id="ci-proto", label="ci-proto (Continuous Intelligence)", repo="ContinuousIntelligence/ci-proto", revision="ee3031351a151e3f6febf8c62b53e6ea1008bef6",
        backend="ciproto", files={"config.json": 237, "model.ckpt": 668_895_231, "tta.json": 529, "LICENCE": 12_209}, params="167M",
        license_name="ci-proto Code and Model Licence v1.0 (Apache-2.0 based)", license_url="https://huggingface.co/ContinuousIntelligence/ci-proto/blob/main/LICENCE",
        license_summary=(
            "Free for research and commercial use, like Apache-2.0, with extra duties in section 10.",
            "Purely INTERNAL use has no extra duties. If you distribute it, or offer a product or service that uses it (or its outputs "
            "to train another model), you must publicly display 'Uses ci-proto, released by Continuous Intelligence. Continuous "
            "Intelligence does not endorse or approve this product or service.' and notify Continuous Intelligence once by email.",
            "Provided AS IS. Plain-language summary, not legal advice - read the full licence."),
        commercial="yes", pip=("continuousintelligence==0.1.1",), pip_released="2026-11-12", pip_mb=40,
        max_context=16384, max_horizon=None, quantile_levels=Q9, multivariate=True, covariates=True,
        good_for="Reports the top fev-bench score; very long context (2M steps with the long checkpoint) and covariates.",
        caveats="Brand new (released 2026-11-12). The 0.67 GB long-context checkpoint is not downloaded, so history is capped at 16,384 steps.",
        skipped="model-long.ckpt (668,921,710 bytes; histories above 16,384 steps)",
        notes=("Its GPU kernels target Linux x86_64; on Windows and macOS it runs on CPU only and that path has not been verified here.",),
    ),
    Model(
        id="kronos-base", label="Kronos base (candlesticks)", repo="NeoQuasar/Kronos-base", revision="2b554741eca47781b64468546e77fef3e85130e6",
        backend="kronos", kind="ohlcv", files={"config.json": 228, "model.safetensors": 409_264_008}, params="102M",
        extra=Extra("NeoQuasar/Kronos-Tokenizer-base", "0e0117387f39004a9016484a186a908917e22426", {"config.json": 301, "model.safetensors": 15_842_368}),
        license_name="MIT", license_url="https://huggingface.co/NeoQuasar/Kronos-base", commercial="yes",
        license_summary=("Free to use, modify and redistribute, including commercially (MIT).", "Keep the copyright and licence notice. Provided AS IS.",
                         "Plain-language summary, not legal advice."),
        source=_KRONOS_SRC, max_context=512, max_horizon=None, quantile_levels=(), multivariate=True,
        good_for="Financial candlesticks (OHLCV) ONLY: forecasts the next open/high/low/close/volume bars of a market.",
        caveats="NOT a general time-series model: it expects open, high, low, close (and optionally volume, amount) with timestamps, "
                "reads at most 512 bars, and returns a single sampled path without quantiles. Not financial advice.",
        notes=("It is used through forecast_ohlcv, never through forecast, and is left out of 'all'.",),
    ),
]}

assert all(REVISION_RE.match(m.revision) for m in MODELS.values())


def model(model_id: str) -> Model:
    if model_id not in MODELS:
        raise KeyError(f"unknown model '{model_id}' (use one of: {', '.join(MODELS)})")
    return MODELS[model_id]


# ---------------------------------------------------------------- the 14-day rule

def cutoff(today: date | None = None) -> str:
    """`pip --uploaded-prior-to` value: nothing published in the last 14 days is installed."""
    d = (today or date.today()) - timedelta(days=AGE_DAYS)
    return f"{d.isoformat()}T00:00:00Z"


def available_from(m: Model) -> str:
    """The first day every package and source commit this model needs is at least 14 days old ('' = already)."""
    dates = [d for d in (m.pip_released, m.source.committed if m.source else "") if d]
    if not dates:
        return ""
    return (date.fromisoformat(max(dates)) + timedelta(days=AGE_DAYS)).isoformat()


def blocked_reason(m: Model, today: date | None = None, python: tuple[int, int] | None = None) -> str:
    """Why this model cannot be installed right now ('' when it can)."""
    today = today or date.today()
    py = python or sys.version_info[:2]
    if py < m.python_min:
        return f"needs Python {m.python_min[0]}.{m.python_min[1]}+, the backend runs {py[0]}.{py[1]}"
    when = available_from(m)
    if when and today < date.fromisoformat(when):
        return f"its newest package is under {AGE_DAYS} days old; Agent Studio installs it from {when}"
    return ""


# ---------------------------------------------------------------- paths and state

def home() -> Path:
    """Folder holding the isolated environment, the weights, the fetched sources and the install state."""
    return Path(os.environ.get("TS_HOME") or (data_dir() / "envs" / "timeseries"))


def env_python(h: Path | None = None) -> Path:
    h = h or home()
    return h / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def hf_home(h: Path | None = None) -> Path:
    return (h or home()) / "hf"


def src_dir(h: Path | None = None) -> Path:
    return (h or home()) / "src"


def state_file(h: Path | None = None) -> Path:
    return (h or home()) / "state.json"


def read_state(h: Path | None = None) -> dict:
    try:
        d = json.loads(state_file(h).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(data: dict, h: Path | None = None) -> None:
    f = state_file(h)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({**data, "ts": time.time()}), encoding="utf-8")
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, f)
            return
        except PermissionError:                    # Windows: another handle (the UI polling, a scanner) briefly holds the file
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def env_ready(h: Path | None = None) -> bool:
    st = read_state(h)
    return (st.get("env") or {}).get("state") == "ready" and env_python(h).exists()


def is_ready(model_id: str, h: Path | None = None) -> bool:
    """True only when this model finished installing at THIS pinned revision and the environment still exists."""
    st = read_state(h)
    rec = (st.get("models") or {}).get(model_id) or {}
    return model_id in MODELS and rec.get("state") == "ready" and rec.get("revision") == MODELS[model_id].revision and env_ready(h)


def installed_ids(h: Path | None = None) -> list[str]:
    return [i for i in MODELS if is_ready(i, h)]


def any_ready(h: Path | None = None) -> bool:
    return bool(installed_ids(h))


TORCH_CUDA = dict(variant="cuda", index="https://download.pytorch.org/whl/cu132", packages=["torch==2.15.0"],
                  size_mb=2400, label="PyTorch 2.15.0 + CUDA 13.2 build (cu132)")
TORCH_CPU = dict(variant="cpu", index="https://download.pytorch.org/whl/cpu", packages=["torch==2.15.0"],
                 size_mb=240, label="PyTorch 2.15.0 CPU build")
TORCH_DEFAULT = dict(variant="default", index=None, packages=["torch==2.15.0"],
                     size_mb=240, label="PyTorch 2.15.0 from PyPI (Apple GPU / MPS on macOS)")
MIN_CUDA = (13, 2)                               # the cu132 wheels need a driver that supports CUDA >= 13.2


def choose_torch(cuda: tuple[int, int] | None, platform: str) -> dict:
    """The PyTorch build: CUDA when an NVIDIA driver supporting CUDA >= 13.2 exists, the CPU build on other Windows/Linux
    machines, PyPI's default on macOS. Sizes are approximate downloads (MB)."""
    if platform == "darwin":
        return dict(TORCH_DEFAULT)
    if cuda is not None and cuda >= MIN_CUDA:
        return dict(TORCH_CUDA)
    return dict(TORCH_CPU)


def model_dir_name(repo: str) -> str:
    return "models--" + repo.replace("/", "--")


def public(m: Model, today: date | None = None, python: tuple[int, int] | None = None) -> dict:
    """What the UI and list_models show about a model (no paths, no secrets)."""
    return dict(
        id=m.id, label=m.label, repo=m.repo, revision=m.revision, params=m.params, kind=m.kind, backend=m.backend,
        download_bytes=m.download_bytes, weights_bytes=m.weights_bytes, max_context=m.max_context, max_horizon=m.max_horizon,
        quantile_levels=list(m.quantile_levels), multivariate=m.multivariate, covariates=m.covariates,
        license=dict(name=m.license_name, url=m.license_url, summary=list(m.license_summary), commercial=m.commercial),
        good_for=m.good_for, caveats=m.caveats, notes=list(m.notes), skipped=m.skipped,
        blocked=blocked_reason(m, today, python), available_from=available_from(m),
        source=dict(repo=m.source.repo, sha=m.source.sha) if m.source else None)
