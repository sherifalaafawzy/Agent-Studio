"""REST API for knowledge bases (RAG): /api/rag/*.

Pydantic models live at module level (main.py uses postponed annotations; models built inside a function make FastAPI
treat the body as a query parameter).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ..budget import BudgetExceeded
from ..errors import classify
from ..harness.tools import SKIP_DIRS
from . import stack as stack_mod
from .chunking import MAX_FILE_BYTES, TEXT_EXT
from .embeddings import EmbeddingError, embedding_models
from .service import NotFound, RagError, RagService
from .stack import StackError

MAX_FOLDER_FILES = 200
INGESTABLE = TEXT_EXT | {".pdf"}


class StoreIn(BaseModel):
    id: str
    label: str = ""
    kind: str
    config: dict = Field(default_factory=dict)
    secrets: dict = Field(default_factory=dict)


class StorePatch(BaseModel):
    label: str | None = None
    config: dict | None = None
    secrets: dict | None = None            # a missing key keeps the stored secret, "" clears it


class KbIn(BaseModel):
    id: str
    label: str = ""
    store_id: str = "local"
    provider: str = ""                     # vector stores: the embedding provider and model. Managed stores ignore both
    model: str = ""
    description: str = ""
    chunk_size: int = 1000
    chunk_overlap: int = 150
    remote: str = ""                       # managed stores: the existing corpus / Bedrock knowledge base id to attach


class KbPatch(BaseModel):
    label: str | None = None
    description: str | None = None


class TextIn(BaseModel):
    title: str
    text: str = Field(min_length=1, max_length=2_000_000)


class PathIn(BaseModel):
    path: str                              # relative to the workspace folder


class SearchIn(BaseModel):
    query: str
    kbs: list[str]
    k: int = 5


def _rag(request: Request) -> RagService:
    return request.app.state.s.rag


async def _call(coro):
    """Run a service call and turn its errors into HTTP errors the UI can show."""
    try:
        return await coro
    except NotFound as e:
        raise HTTPException(404, str(e))
    except (RagError, EmbeddingError) as e:
        raise HTTPException(422, str(e))
    except StackError as e:
        raise HTTPException(409, str(e))
    except BudgetExceeded:
        raise                                   # becomes a 402 with the spend details (handled app-wide)
    except Exception as e:  # noqa: BLE001 - provider and network failures are explained, not shown as stack traces
        info = classify(e, None)
        raise HTTPException(502, f"{info['title']}. {info.get('hint') or ''}".strip())


def _sync(fn, *a, **kw):
    """Blocking helpers (Docker, file walks) run off the event loop."""
    async def go():
        return await asyncio.to_thread(fn, *a, **kw)
    return go()


def folder_files(root: Path, workspace: Path) -> list[Path]:
    """Ingestable files under `root` (a file or folder inside the workspace), skipping junk folders. Capped."""
    ws = workspace.resolve()
    target = (ws / root).resolve()
    try:
        target.relative_to(ws)
    except ValueError:
        raise RagError("that path is outside the workspace folder")
    if not target.exists():
        raise RagError(f"'{root.as_posix()}' does not exist in the workspace")
    if target.is_file():
        return [target]
    out: list[Path] = []
    for p in sorted(target.rglob("*")):
        if p.is_file() and p.suffix.lower() in INGESTABLE and not (set(p.relative_to(ws).parts) & SKIP_DIRS):
            out.append(p)
            if len(out) >= MAX_FOLDER_FILES:
                break
    return out


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/rag", tags=["rag"])

    # ---- stores ----
    @r.get("/kinds")
    def kinds(rag: RagService = Depends(_rag)):
        return rag.kinds()

    @r.get("/stores")
    def stores(rag: RagService = Depends(_rag)):
        return rag.list_stores()

    @r.post("/stores")
    async def add_store(body: StoreIn, rag: RagService = Depends(_rag)):
        return await _call(_sync(rag.add_store, body.id, body.label, body.kind, body.config, body.secrets))

    @r.put("/stores/{sid}")
    async def update_store(sid: str, body: StorePatch, rag: RagService = Depends(_rag)):
        return await _call(_sync(rag.update_store, sid, body.label, body.config, body.secrets))

    @r.delete("/stores/{sid}")
    async def delete_store(sid: str, rag: RagService = Depends(_rag)):
        await _call(_sync(rag.delete_store, sid))
        return {"ok": True}

    @r.post("/stores/{sid}/test")
    async def test_store(sid: str, rag: RagService = Depends(_rag)):
        return await _call(rag.test_store(sid))

    # ---- Docker-managed stores ----
    @r.get("/stacks")
    async def stacks():
        return await _call(_sync(stack_mod.available))

    @r.post("/stacks/{stack_id}/start")
    async def stack_start(stack_id: str):
        return await _call(_sync(stack_mod.start, stack_id))

    @r.post("/stacks/{stack_id}/stop")
    async def stack_stop(stack_id: str):
        return await _call(_sync(stack_mod.stop, stack_id))

    @r.delete("/stacks/{stack_id}")
    async def stack_remove(stack_id: str, delete_data: bool = False):
        return await _call(_sync(stack_mod.remove, stack_id, delete_data))

    @r.post("/stacks/{stack_id}/register")
    async def stack_register(stack_id: str, rag: RagService = Depends(_rag)):
        """Add (once) a store that points at this Docker container."""
        try:
            spec = stack_mod.STACKS[stack_id]
        except KeyError:
            raise HTTPException(404, f"unknown stack '{stack_id}'")
        sid = f"docker-{spec.id}"
        try:
            return rag.get_store(sid)
        except NotFound:
            return await _call(_sync(rag.add_store, sid, spec.label, spec.store_kind, dict(spec.config), stack_mod.store_secrets(spec)))

    # ---- embedding models ----
    @r.get("/embedding-models/{provider}")
    def models(provider: str, request: Request):
        p = request.app.state.s.providers.get(provider)
        if p is None:
            raise HTTPException(404, f"unknown provider '{provider}'")
        return {"provider": provider, "kind": p.kind, "enabled": p.enabled, "models": embedding_models(p.kind)}

    # ---- knowledge bases ----
    @r.get("/kbs")
    def kbs(rag: RagService = Depends(_rag)):
        return rag.list_kbs()

    @r.post("/kbs")
    async def create_kb(body: KbIn, rag: RagService = Depends(_rag)):
        return await _call(rag.create_kb(body.id, body.label, body.store_id, body.provider, body.model, body.description,
                                         body.chunk_size, body.chunk_overlap, body.remote))

    @r.get("/kbs/{kid}")
    async def get_kb(kid: str, rag: RagService = Depends(_rag)):
        return await _call(_sync(rag.get_kb, kid))

    @r.put("/kbs/{kid}")
    async def update_kb(kid: str, body: KbPatch, rag: RagService = Depends(_rag)):
        return await _call(_sync(rag.update_kb, kid, body.label, body.description))

    @r.delete("/kbs/{kid}")
    async def delete_kb(kid: str, force: bool = False, rag: RagService = Depends(_rag)):
        await _call(rag.delete_kb(kid, force))
        return {"ok": True}

    @r.post("/kbs/{kid}/sync")
    async def sync_kb(kid: str, rag: RagService = Depends(_rag)):
        """Managed stores only: ask the provider to (re)index (Bedrock: start an ingestion job)."""
        return await _call(rag.sync_kb(kid))

    # ---- documents ----
    @r.get("/kbs/{kid}/docs")
    async def docs(kid: str, rag: RagService = Depends(_rag)):
        return await _call(_sync(rag.list_docs, kid))

    @r.post("/kbs/{kid}/docs")
    async def upload(kid: str, files: list[UploadFile], rag: RagService = Depends(_rag)):
        """Upload one or more files. Each result says added / updated / unchanged, or why it was skipped."""
        out = []
        for f in files:
            data = await f.read(MAX_FILE_BYTES + 1)
            name = f.filename or ""                      # FastAPI rejects a nameless part before we get here, so this is always set
            try:
                out.append({"name": name, **await _call(rag.ingest(kid, name, data))})
            except HTTPException as e:
                if e.status_code != 422:                  # unknown knowledge base, spend cap, provider trouble: stop here
                    raise
                out.append({"name": name, "status": "skipped", "error": e.detail})
        return out

    @r.post("/kbs/{kid}/text")
    async def add_text(kid: str, body: TextIn, rag: RagService = Depends(_rag)):
        return await _call(rag.ingest_text(kid, body.title, body.text))

    @r.post("/kbs/{kid}/path")
    async def add_path(kid: str, body: PathIn, request: Request, rag: RagService = Depends(_rag)):
        """Ingest a file, or every supported file in a folder, from the workspace."""
        ws = request.app.state.s.chat.workspace_for(None)
        files = await _call(_sync(folder_files, Path(body.path), ws))
        out = []
        for p in files:
            rel = p.relative_to(ws.resolve()).as_posix()
            try:
                out.append({"name": rel, **await _call(rag.ingest(kid, p.name, await asyncio.to_thread(p.read_bytes), source=rel))})
            except HTTPException as e:
                if e.status_code != 422:
                    raise
                out.append({"name": rel, "status": "skipped", "error": e.detail})
        return out

    @r.delete("/kbs/{kid}/docs/{doc_id}")
    async def delete_doc(kid: str, doc_id: str, rag: RagService = Depends(_rag)):
        return {"ok": True, "removed": await _call(rag.delete_doc(kid, doc_id))}

    # ---- search ----
    @r.post("/search")
    async def search(body: SearchIn, rag: RagService = Depends(_rag)):
        return await _call(rag.search(body.query, body.kbs, body.k))

    app.include_router(r)
