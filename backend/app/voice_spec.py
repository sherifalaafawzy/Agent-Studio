"""Pinned engines, model files and paths of the local live voice (pure data + small helpers: imported by the installer AND by the
worker that runs inside the isolated environment, so it must not import anything outside the standard library)."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PIP_VERSION = "26.2.2"                      # 2026-10-20; needed for --uploaded-prior-to
PIP_CUTOFF = "2026-11-09T00:00:00Z"         # nothing published after this (>= 14 days before 2026-11-23)
PACKAGES = ["faster-whisper==1.2.1", "kokoro-onnx==0.6.1"]
ORT_GPU = "onnxruntime-gpu==1.22.0"       # replaces kokoro-onnx's CPU onnxruntime on a GPU install (1.22 is the CUDA 12 line; 1.31 needs CUDA 13)
CUDA_PACKAGES = ["nvidia-cublas-cu12==12.9.2.10", "nvidia-cudnn-cu12==9.28.1.4", "nvidia-cuda-runtime-cu12==12.9.79",
                 "nvidia-cufft-cu12==11.4.1.4", "nvidia-curand-cu12==10.3.10.19"]    # the CUDA 12 libraries ctranslate2 and onnxruntime-gpu load
DEFAULT_VOICE = "af_heart"
VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "am_adam", "am_michael", "bf_emma", "bf_isabella", "bm_george", "bm_lewis"]

STT_MODELS: dict[str, dict] = {
    "small.en": dict(repo="Systran/faster-whisper-small.en", revision="d1d751a5f8271d482d14ca55d9e2deeebbae577f", size_mb=484,
                     note="more accurate; fast on an NVIDIA GPU (about 0.1 s for a sentence), about 1 s on a CPU"),
    "base.en": dict(repo="Systran/faster-whisper-base.en", revision="3d3d5dee26484f91867d81cb899cfcf72b96be6c", size_mb=145,
                    note="lighter; about 0.4 s for a sentence on a CPU, English only"),
}
_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
TTS_FILES: dict[str, dict] = {
    "kokoro-v1.0.int8.onnx": dict(url=_BASE + "kokoro-v1.0.int8.onnx", size=92361271,
                                  sha256="6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb"),
    "voices-v1.0.bin": dict(url=_BASE + "voices-v1.0.bin", size=28214398,
                            sha256="bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
}
TTS_GPU_FILES: dict[str, dict] = {        # the full-precision model: the GPU runs it about 2.5x faster than the CPU runs the int8 one
    "kokoro-v1.0.onnx": dict(url=_BASE + "kokoro-v1.0.onnx", size=325532387,
                             sha256="7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"),
}
LICENSES = [dict(name="faster-whisper", licence="MIT", url="https://github.com/SYSTRAN/faster-whisper"),
            dict(name="Whisper (Systran conversions)", licence="MIT", url="https://huggingface.co/Systran/faster-whisper-small.en"),
            dict(name="kokoro-onnx", licence="MIT", url="https://github.com/thewh1teagle/kokoro-onnx"),
            dict(name="Kokoro-82M weights", licence="Apache-2.0", url="https://huggingface.co/hexgrad/Kokoro-82M"),
            dict(name="NVIDIA cuBLAS / cuDNN (GPU only)", licence="NVIDIA proprietary (redistributable runtime)", url="https://pypi.org/project/nvidia-cudnn-cu12/")]


# ---------------------------------------------------------------- paths and state
def home() -> Path:
    if os.environ.get("VOICE_HOME"):
        return Path(os.environ["VOICE_HOME"])
    from .db import data_dir
    return data_dir() / "envs" / "voice"


def env_python(h: Path | None = None) -> Path:
    h = h or home()
    return h / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def models_dir(h: Path | None = None) -> Path:
    return (h or home()) / "models"


def hf_home(h: Path | None = None) -> Path:
    return (h or home()) / "hf"


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
    os.replace(tmp, f)


def is_ready(h: Path | None = None) -> bool:
    st = read_state(h)
    return st.get("state") == "ready" and st.get("pins") == _pins() and env_python(h).exists()


def _pins() -> dict:
    return dict(stt={k: v["revision"] for k, v in STT_MODELS.items()}, tts={k: v["sha256"] for k, v in {**TTS_FILES, **TTS_GPU_FILES}.items()})


def choose_device(hw: dict, requested: str = "auto") -> str:
    """'cuda' when an NVIDIA GPU with a CUDA 12-capable driver is there and the user did not ask for the CPU; else 'cpu'."""
    cuda = hw.get("cuda") or [0, 0]
    return "cuda" if requested == "auto" and hw.get("nvidia") and cuda[0] >= 12 else "cpu"


def default_stt(device: str) -> str:
    return "small.en" if device == "cuda" else "base.en"


def plan(device: str, stt_model: str) -> list[dict]:
    gpu_mb = round(sum(f["size"] for f in TTS_GPU_FILES.values()) / 1e6) if device == "cuda" else 0
    steps = [dict(id="venv", label="Create a private Python environment", size_mb=15),
             dict(id="pip", label=f"Update pip to {PIP_VERSION}", size_mb=3),
             dict(id="packages", label="Install faster-whisper and kokoro-onnx (ready-made wheels, nothing newer than 14 days)", size_mb=160)]
    if device == "cuda":
        steps.append(dict(id="cuda", label="Install the NVIDIA CUDA 12 libraries and the GPU build of onnxruntime", size_mb=1300))
    steps += [dict(id="models", label=f"Download Whisper {stt_model} and the Kokoro voice model (checked against fixed hashes)",
                   size_mb=STT_MODELS[stt_model]["size_mb"] + gpu_mb + round(sum(f["size"] for f in TTS_FILES.values()) / 1e6)),
              dict(id="verify", label="Load both engines and say a test phrase" + (" (the first GPU start compiles kernels: up to a minute)" if device == "cuda" else ""),
                   size_mb=0)]
    return steps


