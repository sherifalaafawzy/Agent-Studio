"""Downloads one time-series model into the isolated environment: the pinned weights (Hugging Face cache) and, for the two
libraries that are not on PyPI, the pinned GitHub source.

Runs INSIDE the environment (`<env python> -m app.ts_fetch <model id>`), started by the Install step - never on import,
never automatically. Prints machine-readable lines the installer turns into progress:  PROGRESS <bytes> <total>, DONE, ERROR.
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

from . import ts_spec

POLL_SECONDS = 2.0
CHUNK = 1 << 20


def dir_bytes(path) -> int:
    """Bytes under a folder, ignoring links (a Hugging Face snapshot's links point at blobs that are counted already)."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            p = os.path.join(root, f)
            if os.path.islink(p):
                continue
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
    return total


def safe_extract(archive: zipfile.ZipFile, wanted_prefix: str, dest: Path) -> int:
    """Extract only the members under `wanted_prefix` (a folder inside the archive's single top folder) into `dest`,
    refusing any path that would leave `dest`. Returns the number of files written."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    n = 0
    for info in archive.infolist():
        if info.is_dir() or not info.filename.startswith(wanted_prefix):
            continue
        rel = info.filename[len(wanted_prefix):].lstrip("/")
        target = (root / rel).resolve()
        if root not in target.parents:
            raise ValueError(f"unsafe path in the archive: {info.filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        n += 1
    return n


def fetch_source(src: ts_spec.Source, h: Path, progress=None) -> Path:
    """Download the archive of the pinned commit and keep only its importable package folder under <home>/src/<dest>/."""
    url = f"https://github.com/{src.repo}/archive/{src.sha}.zip"
    buf = io.BytesIO()
    with urllib.request.urlopen(url, timeout=60) as resp:             # noqa: S310 - fixed https://github.com URL built from pinned values
        while chunk := resp.read(CHUNK):
            buf.write(chunk)
            if progress:
                progress(buf.tell())
    with zipfile.ZipFile(buf) as z:
        top = z.namelist()[0].split("/")[0]
        if src.sha not in top:
            raise ValueError(f"the archive is not commit {src.sha[:12]} (top folder '{top}')")
        dest = ts_spec.src_dir(h) / src.dest
        if dest.exists():
            shutil.rmtree(dest)
        if safe_extract(z, f"{top}/{src.keep}/", dest / src.keep) == 0:
            raise ValueError(f"the archive has no '{src.keep}/' folder")
        return dest


def _report(paths: list[Path], offset: list[int], total: int, stop: threading.Event, interval: float) -> None:
    while not stop.wait(interval):
        print(f"PROGRESS {min(offset[0] + sum(dir_bytes(p) for p in paths), total)} {total}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        m = ts_spec.model(args[0])
    except (IndexError, KeyError) as e:
        print(f"ERROR {e if args else 'usage: python -m app.ts_fetch <model id>'}", flush=True)
        return 2
    h = ts_spec.home()
    os.environ["HF_HOME"] = str(ts_spec.hf_home(h))
    os.environ.pop("HF_HUB_OFFLINE", None)
    cache = ts_spec.hf_home(h) / "hub"
    cache.mkdir(parents=True, exist_ok=True)
    total = m.download_bytes
    stop = threading.Event()
    watched = [cache / ts_spec.model_dir_name(repo) for repo, _rev, _files in m.repos]
    offset = [0]                                     # bytes of the source archive already fetched
    threading.Thread(target=_report, args=(watched, offset, total, stop, POLL_SECONDS), daemon=True).start()
    try:
        if m.source:
            fetch_source(m.source, h)
            offset[0] = m.source.zip_bytes
            print(f"PROGRESS {offset[0]} {total}", flush=True)
        from huggingface_hub import snapshot_download
        paths = [snapshot_download(repo_id=repo, revision=rev, cache_dir=str(cache), allow_patterns=files) for repo, rev, files in m.repos]
    except Exception as e:  # noqa: BLE001 - reported to the installer, which shows it to the user
        stop.set()
        print(f"ERROR {type(e).__name__}: {str(e)[:300]}", flush=True)
        return 1
    stop.set()
    print(f"PROGRESS {total} {total}", flush=True)
    print(f"DONE {paths[0]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
