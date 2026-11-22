"""Regenerates backend/app/aibom/model_hashes.json: the SHA-256 of every file of every pinned local model, read from the
Hugging Face API at the pinned revision (git-LFS files carry a real sha256; small git files carry their git blob id, which is
not a SHA-256 and is skipped). Run: the backend venv python on this script   (needs network)."""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import d1_spec, ts_spec  # noqa: E402


def lfs_hashes(repo: str, rev: str) -> dict[str, dict]:
    url = f"https://huggingface.co/api/models/{repo}/revision/{rev}?blobs=true"
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.load(r)
    out = {}
    for s in data.get("siblings", []):
        lfs = s.get("lfs") or {}
        if lfs.get("sha256"):
            out[s["rfilename"]] = {"sha256": lfs["sha256"], "size": s.get("size")}
    return out


def main() -> None:
    wanted: dict[tuple[str, str], None] = {}
    for m in d1_spec.MODELS.values():
        wanted[(m.repo, m.revision)] = None
    for t in ts_spec.MODELS.values():
        for repo, rev, _files in t.repos:
            wanted[(repo, rev)] = None
    table = {}
    for repo, rev in wanted:
        table[f"{repo}@{rev}"] = lfs_hashes(repo, rev)
        print(repo, rev[:8], len(table[f"{repo}@{rev}"]), "lfs files")
    dest = ROOT / "backend" / "app" / "aibom" / "model_hashes.json"
    dest.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", dest)


main()
