"""What kind of thing a file is: its artifact type, its media type, and how the Artifacts page can preview it. Extension-based and deterministic."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

TYPES = ("document", "code", "image", "data", "model", "archive", "audio", "video", "other")
TYPE_LABELS = {"document": "Documents and reports", "code": "Code", "image": "Images and plots", "data": "Data", "model": "Model files",
               "archive": "Archives", "audio": "Audio", "video": "Video", "other": "Other"}

_BY_EXT: dict[str, tuple[str, str, str]] = {}      # extension -> (type, media type, preview)


def _add(kind: str, mime: str, preview: str, *exts: str) -> None:
    for e in exts:
        _BY_EXT[e] = (kind, mime, preview)


_add("document", "text/markdown", "markdown", "md", "markdown")
_add("document", "text/plain", "text", "txt", "log", "rst", "text")
_add("document", "application/pdf", "pdf", "pdf")
_add("document", "text/html", "text", "html", "htm")
_add("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "none", "docx")
_add("document", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "none", "pptx")
_add("document", "application/rtf", "none", "rtf")
_add("code", "text/plain", "text", "py", "js", "jsx", "ts", "tsx", "java", "go", "rs", "c", "h", "cpp", "hpp", "cs", "rb", "php", "sh", "ps1", "bat", "sql", "css", "scss",
     "kt", "swift", "r", "lua", "dart", "toml", "ini", "cfg", "dockerfile", "ipynb", "diff", "patch", "tf")
_add("image", "image/png", "image", "png")
_add("image", "image/jpeg", "image", "jpg", "jpeg")
_add("image", "image/gif", "image", "gif")
_add("image", "image/webp", "image", "webp")
_add("image", "image/svg+xml", "image", "svg")
_add("image", "image/bmp", "none", "bmp")
_add("data", "text/csv", "csv", "csv", "tsv")
_add("data", "application/json", "json", "json", "jsonl", "ndjson", "geojson")
_add("data", "text/plain", "text", "yaml", "yml", "xml")
_add("data", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "none", "xlsx", "xls")
_add("data", "application/octet-stream", "none", "parquet", "feather", "arrow", "db", "sqlite", "sqlite3", "npy", "npz", "pkl", "pickle", "h5", "hdf5")
_add("model", "application/octet-stream", "none", "pt", "pth", "safetensors", "onnx", "gguf", "ckpt", "pb", "tflite", "keras", "joblib", "bin", "mlmodel", "ggml")
_add("archive", "application/zip", "none", "zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar", "whl")
_add("audio", "audio/mpeg", "none", "mp3")
_add("audio", "audio/wav", "none", "wav")
_add("audio", "audio/ogg", "none", "ogg")
_add("audio", "audio/flac", "none", "flac")
_add("audio", "audio/mp4", "none", "m4a")
_add("video", "video/mp4", "none", "mp4")
_add("video", "video/quicktime", "none", "mov")
_add("video", "video/webm", "none", "webm")
_add("video", "video/x-matroska", "none", "mkv")
_add("video", "video/x-msvideo", "none", "avi")
_add("document", "text/vtt", "text", "vtt")             # captions for a video

# Types whose content is text we can search, preview and secret-scan.
TEXT_PREVIEWS = ("markdown", "text", "csv", "json")
# Served by the API with their real media type (the browser can show them safely); everything else is sent as plain text or a download.
INLINE_IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
# Audio and video a browser can play; served inline (with byte ranges, so seeking works). Other containers (mkv, avi) stay downloads.
INLINE_MEDIA = {"audio/mpeg", "audio/wav", "audio/ogg", "audio/flac", "audio/mp4", "video/mp4", "video/quicktime", "video/webm"}


def classify(name: str) -> tuple[str, str, str]:
    """(artifact type, media type, preview kind) for a file name. Unknown extensions are 'other' / octet-stream / no preview."""
    p = PurePosixPath(name.replace("\\", "/"))
    ext = p.suffix.lower().lstrip(".") or p.name.lower()
    return _BY_EXT.get(ext, ("other", "application/octet-stream", "none"))


def safe_name(name: str) -> str:
    """A file name that is safe in a URL, a header and on every file system (never empty, never a path)."""
    base = PurePosixPath(str(name).replace("\\", "/")).name.strip().strip(".")
    base = re.sub(r"[^A-Za-z0-9._ +()=@,-]", "_", base)[:120].strip()
    return base or "artifact"


def looks_binary(head: bytes) -> bool:
    return b"\x00" in head
