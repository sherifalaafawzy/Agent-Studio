"""Downloads the pinned d1 model files into the isolated environment's own Hugging Face cache.

Runs INSIDE the d1 environment (`<env python> -m app.d1_fetch`), started by the Install step - never on import, never
automatically. Prints machine-readable lines the installer turns into progress:  PROGRESS <bytes> <total>, DONE, ERROR.
"""
from __future__ import annotations

import os
import sys
import threading

from . import d1_spec

POLL_SECONDS = 2.0


def dir_bytes(path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            p = os.path.join(root, f)
            if os.path.islink(p):            # the snapshot's links point at blobs that are counted already
                continue
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
    return total


def _report(cache, stop: threading.Event, interval: float, total: int) -> None:
    while not stop.wait(interval):
        print(f"PROGRESS {min(dir_bytes(cache), total)} {total}", flush=True)


def pin_main(h, sp) -> None:
    """Point the repo's `main` reference in the offline cache at the pinned commit. The model's own loader asks for the tokenizer
    and processor by repo id alone (no revision); offline, that resolves `main`, which must be the reviewed commit."""
    refs = d1_spec.repo_cache(h, sp.id) / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(sp.revision, encoding="utf-8")


def main() -> int:
    h = d1_spec.home()
    sp = d1_spec.spec(d1_spec.active_model())          # D1_MODEL says which model this install step is for
    total = sp.weights_bytes + 30_000_000
    os.environ["HF_HOME"] = str(d1_spec.hf_home(h))
    os.environ.pop("HF_HUB_OFFLINE", None)
    cache = d1_spec.hf_home(h) / "hub"
    cache.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    reporter = threading.Thread(target=_report, args=(cache, stop, POLL_SECONDS, total), daemon=True)
    reporter.start()
    try:
        from huggingface_hub import snapshot_download
        path = snapshot_download(repo_id=sp.repo, revision=sp.revision, cache_dir=str(cache), allow_patterns=d1_spec.CODE_GLOBS)
        pin_main(h, sp)
    except Exception as e:  # noqa: BLE001 - reported to the installer, which shows it to the user
        stop.set()
        print(f"ERROR {type(e).__name__}: {str(e)[:300]}", flush=True)
        return 1
    stop.set()
    print(f"PROGRESS {total} {total}", flush=True)
    print(f"DONE {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
