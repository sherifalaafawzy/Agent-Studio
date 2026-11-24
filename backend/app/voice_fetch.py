"""Downloads the pinned voice models into `<voice home>/models` (run by the installer INSIDE the isolated environment).

`python -m app.voice_fetch` with env VOICE_HOME and VOICE_STT_MODEL. Every file is checked against the size and SHA-256 pinned in
`voice_local` before it is kept; a file that already matches is not downloaded again. Whisper comes from a pinned Hugging Face commit.
Prints plain progress lines (the installer shows them); exits 0 only when everything is in place.
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path
from typing import Callable

from . import voice_spec as VL

CHUNK = 1 << 20


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def matches(path: Path, size: int, sha: str) -> bool:
    return path.is_file() and path.stat().st_size == size and sha256_of(path) == sha


def download(url: str, dest: Path, size: int, sha: str, say: Callable[[str], None], opener: Callable = urllib.request.urlopen) -> None:
    """Fetch `url` to `dest` (through a .part file) and keep it only when size and hash match the pins."""
    if matches(dest, size, sha):
        say(f"{dest.name}: already downloaded and verified")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    got = 0
    with opener(url) as r, part.open("wb") as f:
        for block in iter(lambda: r.read(CHUNK), b""):
            f.write(block)
            got += len(block)
            say(f"{dest.name}: {got / 1e6:.0f} of {size / 1e6:.0f} MB")
    if part.stat().st_size != size or sha256_of(part) != sha:
        part.unlink(missing_ok=True)
        raise SystemExit(f"{dest.name}: the download does not match the pinned size/hash - refusing to use it")
    os.replace(part, dest)
    say(f"{dest.name}: verified")


def snapshot(repo: str, revision: str, target: Path) -> str:  # pragma: no cover - the real Hugging Face download
    from huggingface_hub import snapshot_download
    return snapshot_download(repo_id=repo, revision=revision, local_dir=str(target))


def main(argv: list[str] | None = None, say: Callable[[str], None] | None = None, opener: Callable = urllib.request.urlopen,
         snap: Callable[[str, str, Path], str] = snapshot) -> int:
    say = say or (lambda s: print(s, flush=True))
    stt = os.environ.get("VOICE_STT_MODEL") or "base.en"
    if stt not in VL.STT_MODELS:
        say(f"unknown speech model {stt}")
        return 2
    mdir = VL.models_dir()
    files = {**VL.TTS_FILES, **(VL.TTS_GPU_FILES if os.environ.get("VOICE_DEVICE") == "cuda" else {})}
    for name, f in files.items():
        download(f["url"], mdir / name, f["size"], f["sha256"], say, opener)
    spec = VL.STT_MODELS[stt]
    say(f"Downloading Whisper {stt} (pinned commit {spec['revision'][:8]})")
    snap(spec["repo"], spec["revision"], mdir / f"whisper-{stt}")
    say("all models in place")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
