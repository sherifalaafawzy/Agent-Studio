"""Turn files into text, and text into overlapping chunks that fit an embedding model."""
from __future__ import annotations

import io
import re
from pathlib import PurePosixPath

MAX_FILE_BYTES = 25_000_000
MIN_CHUNK, MAX_CHUNK = 200, 8000

TEXT_EXT = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".xml", ".html", ".htm", ".css", ".sql", ".sh", ".bat", ".ps1", ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go",
    ".rs", ".c", ".h", ".cpp", ".cs", ".rb", ".php", ".kt", ".swift", ".tex",
}
_TAG = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)
_SPACE = re.compile(r"[ \t]+")
_SENT = re.compile(r"(?<=[.!?])\s+")


def validate_chunking(size: int, overlap: int) -> None:
    if not MIN_CHUNK <= size <= MAX_CHUNK:
        raise ValueError(f"chunk size must be between {MIN_CHUNK} and {MAX_CHUNK} characters")
    if overlap < 0 or overlap > size // 2:
        raise ValueError("chunk overlap must be between 0 and half the chunk size")


def extract_text(name: str, data: bytes) -> str:
    """Readable text from a file's bytes. Raises ValueError for unsupported or empty files."""
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{name} is larger than {MAX_FILE_BYTES // 1_000_000} MB")
    ext = PurePosixPath(name.replace("\\", "/")).suffix.lower()
    if ext == ".pdf":
        text = _pdf_text(data)
    elif ext in TEXT_EXT or ext == "":
        text = data.decode("utf-8", errors="replace")
        if ext in (".html", ".htm"):
            text = _SPACE.sub(" ", _TAG.sub(" ", text))
    else:
        raise ValueError(f"unsupported file type '{ext}' (text, code, markdown, html, csv, json and pdf work)")
    text = text.replace("\r\n", "\n").replace("\x00", "").strip()
    if not text:
        raise ValueError(f"{name} has no readable text")
    return text


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:  # noqa: BLE001 - a damaged PDF is a user-facing, fixable problem
        raise ValueError(f"could not read the PDF: {type(e).__name__}") from e


def _pieces(text: str, size: int) -> list[str]:
    """Split on paragraphs, then sentences, then hard cuts, so no piece is longer than `size`."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= size:
            out.append(para)
            continue
        for sent in _SENT.split(para):
            while len(sent) > size:
                out.append(sent[:size])
                sent = sent[size:]
            if sent:
                out.append(sent)
    return out


def chunk_text(text: str, size: int = 1000, overlap: int = 150) -> list[str]:
    """Greedy, order-preserving chunks of at most `size` characters; each starts with the previous chunk's tail."""
    validate_chunking(size, overlap)
    chunks: list[str] = []
    cur = ""
    for piece in _pieces(text, size):
        joined = f"{cur}\n\n{piece}" if cur else piece
        if len(joined) <= size:
            cur = joined
            continue
        if cur:
            chunks.append(cur)
        tail = cur[-overlap:] if overlap and cur else ""
        cur = f"{tail}\n\n{piece}" if tail and len(tail) + 2 + len(piece) <= size else piece
    if cur:
        chunks.append(cur)
    return chunks
