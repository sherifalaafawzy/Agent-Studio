"""Static facts about the local d1 "decision model" capability: pinned model revision, pinned install plan, paths,
licence summary. STDLIB ONLY on purpose - it is imported by the backend *and* by the isolated model environment
(which has no FastAPI), so keep it dependency-free.

Security: the model repo ships Python that `trust_remote_code=True` executes. REVISION is the exact commit whose code was
reviewed (see REVIEW_NOTES); it is always passed as `revision=` and never "main". Bump it only after reading the diff.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .db import data_dir

MCP_ID = "d1"
REPO_ID = "LiquidAI/d1-2B"
REVISION = "a6bce774e6dde338dcc3036ceb5d91f78bf4c19e"          # HF commit, repo last modified 2026-11-14
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
WEIGHTS_BYTES = 3_836_417_920                                  # model.safetensors at REVISION (3.84 GB)
CODE_GLOBS = ["*.py", "*.json", "*.jinja", "*.safetensors", "LICENSE", "README.md"]   # skips assets/ and .eval_results/
PIP_CUTOFF = "2026-11-07T00:00:00Z"                            # nothing published after this (>= 14 days before 2026-11-21)

LICENSE_NAME = "LFM Open License v1.0"
LICENSE_URL = f"https://huggingface.co/{REPO_ID}/blob/{REVISION}/LICENSE"
LICENSE_SUMMARY = [
    "Free to use, modify and redistribute (perpetual, worldwide, royalty-free copyright and patent licence).",
    "Commercial use is licensed ONLY if you (and every company you control or that controls you) are under US$10M annual "
    "revenue. At or above that threshold, commercial use is NOT licensed - you need a separate agreement with Liquid AI. "
    "Qualified non-profits are exempt for non-commercial or research use.",
    "If you redistribute it: include the licence, mark files you changed, keep copyright/attribution notices.",
    "Your patent licence ends if you sue anyone claiming the Work infringes a patent.",
    "No trademark rights; provided AS IS, no warranty, limited liability.",
    "This is a plain-language summary, not legal advice. Read the full licence before commercial use.",
]

REVIEW_NOTES = [
    f"Reviewed {REPO_ID} at commit {REVISION} (all 26 .py files pattern-scanned for network, process, eval/exec, "
    "pickle, ctypes, env and file access; modeling_d1.py read in full, lfm3.py and integrations_lfm3.py read in part).",
    "No network access, subprocess, os.system, eval/exec, pickle, ctypes or file writes in the custom code.",
    "Only environment variable read: D1_VISION_MAX_PIXELS (a pixel budget). Optional imports: triton (GPU kernels) and "
    "importlib.util.find_spec('causal_conv1d').",
    "It monkey-patches transformers/torch module attributes at runtime (conv / vision-tower speed patches in lfm3.py and "
    "integrations_lfm3.py) - invasive, but confined to the model's own modules.",
    "A state may name an image file path (media.py Image.open): this server only passes paths you give the tools.",
    "Weights are model.safetensors (no pickle). Downloaded at the pinned commit and loaded offline afterwards.",
]

# ---------------------------------------------------------------- the models you can choose between
DEFAULT_MODEL = "d1-2b"
REPO_ID_3B = "LiquidAI/d1-3B"
REVISION_3B = "05b80faf55536582ad5ffb2da7456f6a653acf71"      # HF commit, repo last modified 2026-11-11
WEIGHTS_BYTES_3B = 6_247_065_504                                # model.safetensors at REVISION_3B (6.25 GB)
LICENSE_URL_3B = f"https://huggingface.co/{REPO_ID_3B}/blob/{REVISION_3B}/LICENSE"

REVIEW_NOTES_3B = [
    f"Reviewed {REPO_ID_3B} at commit {REVISION_3B}: all 6 .py files (api, hybrid, lfm2_vl, modeling_d1, prompt, runner; about 1,100 "
    "lines) read and pattern-scanned for network, subprocess, eval/exec, pickle, ctypes, file writes, environment use and URLs.",
    "Nothing found: no network access, subprocess, os/sys/shutil imports, eval/exec/compile of strings, pickle, ctypes, file writes, "
    "environment variables or URLs. Imports are torch, transformers and the standard library (json, math, re, dataclasses, functools, typing).",
    "Unlike d1-2B it does NOT monkey-patch transformers or torch at import time; it defines its own model classes "
    "(a hybrid conv/attention language model) on top of transformers' LFM2 classes.",
    "torch.compile is only used when SystemOne(compile=True) is asked for, and modeling_d1 never asks for it.",
    "The model's loader calls AutoTokenizer/AutoProcessor.from_pretrained(<repo id>) WITHOUT a revision, so Agent Studio records the pinned "
    "commit as the repo's 'main' reference in its own offline cache; the weights, tokenizer and code all come from the reviewed commit.",
    "Weights are model.safetensors (no pickle). Downloaded at the pinned commit and loaded offline afterwards. The LICENSE file is "
    "byte-identical to d1-2B's.",
    "NOT verified: this code has not been run on this machine with the pinned transformers (5.22.2); the repo asks for transformers >= 5.14.",
]

ALL_TYPES = ("noul", "choice", "score", "text", "number", "date", "box", "boxes")


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    repo: str
    revision: str
    weights_bytes: int
    state_name: str                 # file in the environment folder that records this model's install
    license_url: str
    review_notes: list
    types: tuple                    # question types this model can answer
    image_kw: str                   # the keyword its system_one() takes for pictures
    set_size: bool                  # does system_one() take set_size?
    note: str                       # one honest sentence for the UI


MODELS: dict[str, ModelSpec] = {
    "d1-2b": ModelSpec(
        "d1-2b", "d1-2B", REPO_ID, REVISION, WEIGHTS_BYTES, "state.json", LICENSE_URL, REVIEW_NOTES, ALL_TYPES, "media", True,
        "The newer, smaller model (2.3B). Answers every question type: yes/no, choices, scores, text/number/date extraction and boxes."),
    "d1-3b": ModelSpec(
        "d1-3b", "d1-3B", REPO_ID_3B, REVISION_3B, WEIGHTS_BYTES_3B, "state-d1-3b.json", LICENSE_URL_3B, REVIEW_NOTES_3B,
        ("noul", "choice", "score"), "images", False,
        "The older, larger generation (3.1B; Liquid AI's page points to d1-2B as the newer version). Yes/no, choice and score questions "
        "only (no extraction or boxes). Bigger download, and not verified here against the newer d1-2B."),
}


def spec(model: str | None = None) -> ModelSpec:
    """The model's facts. Raises ValueError for an unknown id."""
    m = model or DEFAULT_MODEL
    if m not in MODELS:
        raise ValueError(f"unknown decision model '{m}' (use one of: {', '.join(MODELS)})")
    return MODELS[m]


def active_model() -> str:
    """The model THIS process serves (set by whatever launched it through D1_MODEL; d1-2B when unset or unknown)."""
    m = os.environ.get("D1_MODEL", "")
    return m if m in MODELS else DEFAULT_MODEL


# Pinned, each published >= 14 days before 2026-11-21 (checked on PyPI / download.pytorch.org on 2026-11-21).
PIP_VERSION = "26.2.2"                      # 2026-10-20; needed for --uploaded-prior-to
LIB_PINS = ["transformers==5.22.2", "huggingface_hub==2.4.0", "tokenizers==0.23.3", "safetensors==0.8.0",
            "pillow==12.3.0", "numpy==2.5.4", "mcp==2.3.0"]
TORCH_CUDA = dict(variant="cuda", index="https://download.pytorch.org/whl/cu132", packages=["torch==2.15.0", "torchvision==0.29.2"],
                  size_mb=2500, label="PyTorch 2.15.0 + CUDA 13.2 build (cu132)")
TORCH_CPU = dict(variant="cpu", index="https://download.pytorch.org/whl/cpu", packages=["torch==2.15.0", "torchvision==0.29.2"],
                 size_mb=250, label="PyTorch 2.15.0 CPU build")
TORCH_DEFAULT = dict(variant="default", index=None, packages=["torch==2.15.0", "torchvision==0.29.2"],
                     size_mb=250, label="PyTorch 2.15.0 from PyPI (Apple GPU / MPS on macOS)")
LIBS_SIZE_MB = 200
WEIGHTS_MB = round(WEIGHTS_BYTES / 1_000_000)
VENV_MB = 20
MIN_CUDA = (13, 2)                          # the cu132 wheels need a driver that supports CUDA >= 13.2


def home() -> Path:
    """Folder holding the isolated environment, the weights and the install state."""
    return Path(os.environ.get("D1_HOME") or (data_dir() / "envs" / "d1"))


def env_python(h: Path | None = None) -> Path:
    h = h or home()
    return h / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def hf_home(h: Path | None = None) -> Path:
    return (h or home()) / "hf"


def state_file(h: Path | None = None, model: str | None = None) -> Path:
    return (h or home()) / spec(model).state_name


def read_state(h: Path | None = None, model: str | None = None) -> dict:
    try:
        d = json.loads(state_file(h, model).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(data: dict, h: Path | None = None, model: str | None = None) -> None:
    f = state_file(h, model)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({**data, "ts": time.time()}), encoding="utf-8")
    os.replace(tmp, f)


def is_ready(h: Path | None = None, model: str | None = None) -> bool:
    """True only when an install completed for THIS model's pinned revision and the environment's python still exists."""
    st = read_state(h, model)
    return st.get("state") == "ready" and st.get("revision") == spec(model).revision and env_python(h).exists()


def ready_models(h: Path | None = None) -> list[str]:
    return [m for m in MODELS if is_ready(h, m)]


def preferred_model(h: Path | None = None) -> str:
    """The model the MCP server and tools use: d1-2B when it is installed, else d1-3B if that is, else d1-2B."""
    ready = ready_models(h)
    return ready[0] if ready else DEFAULT_MODEL


def env_usable(h: Path | None = None, model: str | None = None) -> bool:
    """Can `model` reuse the isolated environment another installed model already built (so only its weights are downloaded)?"""
    return any(m != spec(model).id and is_ready(h, m) for m in MODELS)


def repo_cache(h: Path | None = None, model: str | None = None) -> Path:
    """This model's folder inside the shared Hugging Face cache."""
    return hf_home(h) / "hub" / ("models--" + spec(model).repo.replace("/", "--"))


def choose_torch(cuda: tuple[int, int] | None, platform: str) -> dict:
    """Pick the PyTorch build: CUDA when an NVIDIA driver supporting CUDA >= 13.2 exists (Windows/Linux), the CPU build
    on other Windows/Linux machines, PyPI's default on macOS (it includes Apple-GPU support)."""
    if platform == "darwin":
        return dict(TORCH_DEFAULT)
    if cuda is not None and cuda >= MIN_CUDA:
        return dict(TORCH_CUDA)
    return dict(TORCH_CPU)


def plan(torch_choice: dict, model: str | None = None, shared_env: bool = False) -> list[dict]:
    """The ordered install steps with download-size estimates (MB). With `shared_env` the environment already exists
    (another model built it), so only the weights are downloaded and the environment is re-checked."""
    t, sp = torch_choice, spec(model)
    steps = [
        dict(id="venv", label="Create the isolated Python environment", size_mb=VENV_MB),
        dict(id="pip", label=f"Update pip to {PIP_VERSION}", size_mb=3),
        dict(id="torch", label=f"Install {t['label']}", size_mb=t["size_mb"]),
        dict(id="libs", label="Install transformers, huggingface_hub, tokenizers, safetensors, pillow, numpy, mcp", size_mb=LIBS_SIZE_MB),
        dict(id="weights", label=f"Download the model weights ({sp.repo} @ {sp.revision[:8]})", size_mb=round(sp.weights_bytes / 1_000_000)),
        dict(id="verify", label="Check the environment imports and detect the GPU", size_mb=0),
    ]
    return steps[4:] if shared_env else steps
