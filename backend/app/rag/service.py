"""Knowledge bases: where vectors live (stores), what they hold (knowledge bases and documents), and how to search them.

store   = a place that holds vectors (local file, Qdrant, pgvector, Chroma, Pinecone, Amazon S3 Vectors, ...). One store can host many knowledge bases.
          A MANAGED store (Vertex AI RAG Engine, Amazon Bedrock Knowledge Bases) embeds and stores documents itself.
kb      = a named collection in a store, tied to ONE embedding model (vectors from different models cannot be mixed).
          A managed knowledge base has no embedding provider/model/dimension (they are "" / "" / 0) and is searched by text.
doc     = one ingested file or text; replaced when its content changes, skipped when it has not.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from itertools import zip_longest
from pathlib import Path

from ..budget import BudgetGuard
from ..db import Database, data_dir
from ..providers import ProviderStore
from .chunking import chunk_text, extract_text, validate_chunking
from .embeddings import Embedder
from .stores.base import ManagedStore, Point, StoreError, VectorStore
from .stores.registry import KINDS, check_fields, kind_info, make_store

SCHEMA = """
CREATE TABLE IF NOT EXISTS rag_stores(
  id TEXT PRIMARY KEY, label TEXT NOT NULL, kind TEXT NOT NULL,
  config TEXT NOT NULL DEFAULT '{}',          -- non-secret settings (URL, file name)
  secrets TEXT NOT NULL DEFAULT '{}',         -- API keys: stored here, never returned by the API
  builtin INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rag_kbs(
  id TEXT PRIMARY KEY, label TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', store_id TEXT NOT NULL,
  collection TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL,
  chunk_size INTEGER NOT NULL, chunk_overlap INTEGER NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rag_docs(
  kb_id TEXT NOT NULL, doc_id TEXT NOT NULL, source TEXT NOT NULL, sha256 TEXT NOT NULL,
  chunks INTEGER NOT NULL, bytes INTEGER NOT NULL, added REAL NOT NULL, PRIMARY KEY(kb_id, doc_id));
"""

# rag_kbs gained these columns after the first release; existing databases are upgraded in place (old rows = vector KBs).
MIGRATIONS = (("managed", "ALTER TABLE rag_kbs ADD COLUMN managed INTEGER NOT NULL DEFAULT 0"),
              ("owned", "ALTER TABLE rag_kbs ADD COLUMN owned INTEGER NOT NULL DEFAULT 0"))

ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")
LOCAL_ID = "local"
MAX_K = 20
MAX_HIT_CHARS = 2000


class RagError(Exception):
    """A problem the user can fix. The message is safe to show."""


class NotFound(RagError):
    pass


def _check_id(value: str, what: str) -> str:
    if not ID_RE.match(value or ""):
        raise RagError(f"{what} id must be lowercase letters, digits, - or _ and start with a letter (2-41 characters)")
    return value


class RagService:
    def __init__(self, db: Database, providers: ProviderStore, guard: BudgetGuard, home: Path | None = None,
                 embedder: Embedder | None = None, factory=make_store):
        self.db, self.home, self._factory = db, Path(home) if home else data_dir(), factory
        self.embedder = embedder or Embedder(providers, guard)
        self._stores: dict[str, VectorStore] = {}
        db.conn.executescript(SCHEMA)
        have = {r["name"] for r in db.query("PRAGMA table_info(rag_kbs)")}
        for column, ddl in MIGRATIONS:
            if column not in have:
                db.execute(ddl)
        if not db.one("SELECT 1 FROM rag_stores WHERE id=?", (LOCAL_ID,)):
            db.execute("INSERT INTO rag_stores(id,label,kind,config,secrets,builtin,created) VALUES(?,?,?,?,?,1,?)",
                       (LOCAL_ID, "Local file", "local", "{}", "{}", time.time()))

    # ------------------------------------------------------------------ stores
    def _store_row(self, store_id: str):
        row = self.db.one("SELECT * FROM rag_stores WHERE id=?", (store_id,))
        if row is None:
            raise NotFound(f"unknown store '{store_id}'")
        return row

    def _public_store(self, r) -> dict:
        info = kind_info(r["kind"])
        secrets = json.loads(r["secrets"])
        return {"id": r["id"], "label": r["label"], "kind": r["kind"], "kind_label": info.label, "builtin": bool(r["builtin"]),
                "config": json.loads(r["config"]), "secrets_set": {f.name: bool(secrets.get(f.name)) for f in info.fields if f.secret},
                "docker_stack": info.docker_stack, "managed": info.managed, **self._ingest_state(r["id"], info),
                "kbs": self.db.one("SELECT COUNT(*) AS n FROM rag_kbs WHERE store_id=?", (r["id"],))["n"]}

    def _ingest_state(self, store_id: str, info) -> dict:
        """Can documents be added to knowledge bases on this store, and if not, why. {can_ingest, ingest_note}."""
        if not info.managed:
            return {"can_ingest": True, "ingest_note": ""}
        try:
            store = self._store(store_id)
        except RagError as e:
            return {"can_ingest": False, "ingest_note": str(e)}
        return {"can_ingest": store.can_ingest, "ingest_note": info.ingest_note if store.can_ingest else store.ingest_note}

    @staticmethod
    def _field(f) -> dict:
        return {"name": f.name, "label": f.label, "secret": f.secret, "required": f.required,
                "placeholder": f.placeholder, "default": f.default, "help": f.help}

    def kinds(self) -> list[dict]:
        return [{"id": k.id, "label": k.label, "description": k.description, "docker_stack": k.docker_stack,
                 "managed": k.managed, "ingest": k.ingest, "ingest_note": k.ingest_note,
                 "remote": self._field(k.remote) if k.remote else None,
                 "fields": [self._field(f) for f in k.fields]} for k in KINDS.values()]

    def list_stores(self) -> list[dict]:
        return [self._public_store(r) for r in self.db.query("SELECT * FROM rag_stores ORDER BY builtin DESC, created")]

    def get_store(self, store_id: str) -> dict:
        return self._public_store(self._store_row(store_id))

    def _validated(self, kind: str, config: dict, secrets: dict) -> None:
        try:
            check_fields(kind, config, secrets)
            self._factory(kind, config, secrets, self.home)           # building it proves the settings are usable
        except StoreError as e:
            raise RagError(str(e)) from e

    def add_store(self, store_id: str, label: str, kind: str, config: dict | None = None, secrets: dict | None = None) -> dict:
        _check_id(store_id, "store")
        if self.db.one("SELECT 1 FROM rag_stores WHERE id=?", (store_id,)):
            raise RagError(f"a store named '{store_id}' already exists")
        try:
            kind_info(kind)
        except StoreError as e:
            raise RagError(str(e)) from e
        config, secrets = dict(config or {}), dict(secrets or {})
        self._validated(kind, config, secrets)
        self.db.execute("INSERT INTO rag_stores(id,label,kind,config,secrets,builtin,created) VALUES(?,?,?,?,?,0,?)",
                        (store_id, label.strip() or store_id, kind, json.dumps(config), json.dumps(secrets), time.time()))
        return self.get_store(store_id)

    def update_store(self, store_id: str, label: str | None = None, config: dict | None = None, secrets: dict | None = None) -> dict:
        """secrets: a missing key is left alone, an empty string clears it."""
        row = self._store_row(store_id)
        new_config = {**json.loads(row["config"]), **(config or {})}
        new_secrets = {**json.loads(row["secrets"]), **(secrets or {})}
        new_secrets = {k: v for k, v in new_secrets.items() if v}
        self._validated(row["kind"], new_config, new_secrets)
        self.db.execute("UPDATE rag_stores SET label=?, config=?, secrets=? WHERE id=?",
                        ((label or row["label"]).strip(), json.dumps(new_config), json.dumps(new_secrets), store_id))
        self._stores.pop(store_id, None)
        return self.get_store(store_id)

    def delete_store(self, store_id: str) -> None:
        row = self._store_row(store_id)
        if row["builtin"]:
            raise RagError("the built-in local store cannot be removed")
        n = self.db.one("SELECT COUNT(*) AS n FROM rag_kbs WHERE store_id=?", (store_id,))["n"]
        if n:
            raise RagError(f"{n} knowledge base{'s' if n != 1 else ''} still use this store - delete them first")
        self.db.execute("DELETE FROM rag_stores WHERE id=?", (store_id,))
        self._stores.pop(store_id, None)

    def _store(self, store_id: str) -> VectorStore:
        if store_id not in self._stores:
            row = self._store_row(store_id)
            try:
                self._stores[store_id] = self._factory(row["kind"], json.loads(row["config"]), json.loads(row["secrets"]), self.home)
            except StoreError as e:
                raise RagError(str(e)) from e
        return self._stores[store_id]

    async def test_store(self, store_id: str) -> dict:
        try:
            return await self._store(store_id).health()
        except NotFound:
            raise
        except RagError as e:
            return {"ok": False, "detail": str(e)}

    # --------------------------------------------------------- knowledge bases
    def _kb_row(self, kb_id: str):
        row = self.db.one("SELECT * FROM rag_kbs WHERE id=?", (kb_id,))
        if row is None:
            raise NotFound(f"unknown knowledge base '{kb_id}'")
        return row

    def _public_kb(self, r) -> dict:
        stats = self.db.one("SELECT COUNT(*) AS docs, COALESCE(SUM(chunks),0) AS chunks FROM rag_docs WHERE kb_id=?", (r["id"],))
        store = self.db.one("SELECT label, kind FROM rag_stores WHERE id=?", (r["store_id"],))
        ingest = self._ingest_state(r["store_id"], kind_info(store["kind"]))
        return {"id": r["id"], "label": r["label"], "description": r["description"], "store_id": r["store_id"],
                "store_label": store["label"], "store_kind": store["kind"], "provider": r["provider"], "model": r["model"],
                "dim": r["dim"], "chunk_size": r["chunk_size"], "chunk_overlap": r["chunk_overlap"],
                "docs": stats["docs"], "chunks": stats["chunks"], "created": r["created"],
                "managed": bool(r["managed"]), "owned": bool(r["owned"]), "remote": r["collection"] if r["managed"] else "", **ingest}

    def list_kbs(self) -> list[dict]:
        return [self._public_kb(r) for r in self.db.query("SELECT * FROM rag_kbs ORDER BY created")]

    def get_kb(self, kb_id: str) -> dict:
        return self._public_kb(self._kb_row(kb_id))

    async def create_kb(self, kb_id: str, label: str, store_id: str, provider: str = "", model: str = "", description: str = "",
                        chunk_size: int = 1000, chunk_overlap: int = 150, remote: str = "") -> dict:
        """Vector stores: provider + model pick the embedding model. Managed stores: provider/model are ignored (the provider
        embeds) and `remote` names the existing resource to attach (empty = create one, where the provider allows it)."""
        _check_id(kb_id, "knowledge base")
        if self.db.one("SELECT 1 FROM rag_kbs WHERE id=?", (kb_id,)):
            raise RagError(f"a knowledge base named '{kb_id}' already exists")
        try:
            validate_chunking(chunk_size, chunk_overlap)
        except ValueError as e:
            raise RagError(str(e)) from e
        store = self._store(store_id)
        collection = "kb_" + kb_id.replace("-", "_")
        if isinstance(store, ManagedStore):
            try:
                ref = await store.ensure(collection, remote.strip())
            except StoreError as e:
                raise RagError(str(e)) from e
            self.db.execute("INSERT INTO rag_kbs(id,label,description,store_id,collection,provider,model,dim,chunk_size,chunk_overlap,created,managed,owned)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?)", (kb_id, label.strip() or kb_id, description, store_id, ref.collection,
                                                                   "", "", 0, chunk_size, chunk_overlap, time.time(), int(ref.owned)))
            return self.get_kb(kb_id)
        if remote.strip():
            raise RagError("an existing remote resource only applies to managed stores (Vertex AI RAG, Bedrock); leave it empty here")
        if not model.strip():
            raise RagError("choose an embedding model")
        dim = len((await self.embedder.embed(provider, model, ["dimension probe"]))[0])      # the model decides the vector size
        try:
            await store.ensure(collection, dim)
        except StoreError as e:
            raise RagError(str(e)) from e
        self.db.execute("INSERT INTO rag_kbs(id,label,description,store_id,collection,provider,model,dim,chunk_size,chunk_overlap,created)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?)", (kb_id, label.strip() or kb_id, description, store_id, collection,
                                                           provider, model.strip(), dim, chunk_size, chunk_overlap, time.time()))
        return self.get_kb(kb_id)

    def update_kb(self, kb_id: str, label: str | None = None, description: str | None = None) -> dict:
        row = self._kb_row(kb_id)
        self.db.execute("UPDATE rag_kbs SET label=?, description=? WHERE id=?",
                        ((label if label is not None else row["label"]).strip() or row["label"],
                         description if description is not None else row["description"], kb_id))
        return self.get_kb(kb_id)

    async def delete_kb(self, kb_id: str, force: bool = False) -> None:
        """force: forget the knowledge base even if its store cannot be reached (the vectors stay in that store)."""
        row = self._kb_row(kb_id)
        try:
            store = self._store(row["store_id"])
            if row["managed"] and not row["owned"]:        # a resource that is not ours stays; only what we uploaded is removed
                if store.can_ingest:
                    for d in self.db.query("SELECT doc_id FROM rag_docs WHERE kb_id=?", (kb_id,)):
                        await store.delete_doc(row["collection"], d["doc_id"])
            else:
                await store.drop(row["collection"])
        except (StoreError, RagError) as e:
            if not force:
                raise RagError(f"could not remove its vectors ({e}). Fix the store, or delete anyway with force") from e
        self.db.execute("DELETE FROM rag_docs WHERE kb_id=?", (kb_id,))
        self.db.execute("DELETE FROM rag_kbs WHERE id=?", (kb_id,))

    # --------------------------------------------------------------- documents
    def list_docs(self, kb_id: str) -> list[dict]:
        self._kb_row(kb_id)
        return [dict(r) for r in self.db.query(
            "SELECT doc_id, source, chunks, bytes, added FROM rag_docs WHERE kb_id=? ORDER BY added DESC", (kb_id,))]

    async def ingest(self, kb_id: str, name: str, data: bytes, source: str | None = None) -> dict:
        """Add or replace one document. Returns {doc_id, source, chunks, status: added | updated | unchanged}."""
        kb = self._kb_row(kb_id)
        managed = self._managed(kb)
        if managed is not None and not managed.can_ingest:
            raise RagError(f"'{kb['label']}' is search-only: {managed.ingest_note}")
        source = (source or name).strip()
        try:
            text = extract_text(name, data)
        except ValueError as e:
            raise RagError(str(e)) from e
        doc_id = hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]
        sha = hashlib.sha256(data).hexdigest()
        old = self.db.one("SELECT sha256, chunks FROM rag_docs WHERE kb_id=? AND doc_id=?", (kb_id, doc_id))
        if old and old["sha256"] == sha:
            return {"doc_id": doc_id, "source": source, "chunks": old["chunks"], "status": "unchanged"}
        if managed is not None:
            return await self._ingest_managed(kb, managed, old, doc_id, source, sha, text, len(data))
        pieces = chunk_text(text, kb["chunk_size"], kb["chunk_overlap"])
        vectors = await self.embedder.embed(kb["provider"], kb["model"], pieces)        # embed first: a failure keeps the old copy
        if len(vectors[0]) != kb["dim"]:
            raise RagError(f"{kb['provider']}/{kb['model']} now returns {len(vectors[0])}-dimension vectors, "
                           f"but this knowledge base was built with {kb['dim']}. Create a new knowledge base for the new model.")
        store = self._store(kb["store_id"])
        try:
            if old:
                await store.delete_doc(kb["collection"], doc_id)
            await store.upsert(kb["collection"], [
                Point(str(uuid.uuid5(uuid.NAMESPACE_URL, f"{kb_id}:{doc_id}:{i}")), vec,
                      {"doc_id": doc_id, "source": source, "chunk": i, "text": piece})
                for i, (piece, vec) in enumerate(zip(pieces, vectors))])
        except StoreError as e:
            raise RagError(str(e)) from e
        self.db.execute("INSERT OR REPLACE INTO rag_docs(kb_id,doc_id,source,sha256,chunks,bytes,added) VALUES(?,?,?,?,?,?,?)",
                        (kb_id, doc_id, source, sha, len(pieces), len(data), time.time()))
        return {"doc_id": doc_id, "source": source, "chunks": len(pieces), "status": "updated" if old else "added"}

    def _managed(self, kb) -> ManagedStore | None:
        """The managed store behind this knowledge base, or None for a vector knowledge base."""
        return self._store(kb["store_id"]) if kb["managed"] else None

    async def _ingest_managed(self, kb, store: ManagedStore, old, doc_id: str, source: str, sha: str, text: str, size: int) -> dict:
        """The provider chunks and embeds: we hand over the extracted text under our document id (replace = delete then add)."""
        try:
            if old:
                await store.delete_doc(kb["collection"], doc_id)
            note = await store.ingest(kb["collection"], doc_id, source, text)
        except StoreError as e:
            raise RagError(str(e)) from e
        self.db.execute("INSERT OR REPLACE INTO rag_docs(kb_id,doc_id,source,sha256,chunks,bytes,added) VALUES(?,?,?,?,?,?,?)",
                        (kb["id"], doc_id, source, sha, 0, size, time.time()))          # chunks: the provider decides, we do not know
        return {"doc_id": doc_id, "source": source, "chunks": 0, "status": "updated" if old else "added", **({"note": note} if note else {})}

    async def sync_kb(self, kb_id: str) -> dict:
        """Ask a managed store to (re)index (Bedrock: start an ingestion job). {ok, detail}."""
        kb = self._kb_row(kb_id)
        store = self._managed(kb)
        if store is None:
            raise RagError("this knowledge base is not managed: documents are indexed as they are added")
        try:
            return {"ok": True, "detail": await store.sync(kb["collection"])}
        except StoreError as e:
            raise RagError(str(e)) from e

    async def ingest_text(self, kb_id: str, title: str, text: str) -> dict:
        if not title.strip():
            raise RagError("give the text a title (it becomes the source shown with each answer)")
        return await self.ingest(kb_id, f"{title.strip()}.txt", text.encode("utf-8"), source=title.strip())

    async def delete_doc(self, kb_id: str, doc_id: str) -> int:
        kb = self._kb_row(kb_id)
        if not self.db.one("SELECT 1 FROM rag_docs WHERE kb_id=? AND doc_id=?", (kb_id, doc_id)):
            raise NotFound(f"unknown document '{doc_id}'")
        try:
            removed = await self._store(kb["store_id"]).delete_doc(kb["collection"], doc_id)
        except StoreError as e:
            raise RagError(str(e)) from e
        self.db.execute("DELETE FROM rag_docs WHERE kb_id=? AND doc_id=?", (kb_id, doc_id))
        return removed

    # ------------------------------------------------------------------ search
    async def search(self, query: str, kb_ids: list[str], k: int = 5) -> list[dict]:
        """The best chunks across the given knowledge bases: [{kb, kb_label, doc_id, source, chunk, text, score}]."""
        if not query.strip():
            raise RagError("the search text is empty")
        if not 1 <= k <= MAX_K:
            raise RagError(f"k must be between 1 and {MAX_K}")
        if not kb_ids:
            raise RagError("choose at least one knowledge base")
        kbs = [self._kb_row(i) for i in dict.fromkeys(kb_ids)]
        groups: dict[tuple[str, str], list] = {}
        for kb in kbs:
            if not kb["managed"]:
                groups.setdefault((kb["provider"], kb["model"]), []).append(kb)
        vectors = dict(zip(groups, await asyncio.gather(*(self.embedder.embed(p, m, [query]) for p, m in groups))))

        def source_of(kb, payload: dict):
            known = self.db.one("SELECT source FROM rag_docs WHERE kb_id=? AND doc_id=?", (kb["id"], payload.get("doc_id")))
            return known["source"] if known else payload.get("source")

        async def one(kb) -> list[dict]:
            store = self._store(kb["store_id"])
            try:
                if kb["managed"]:
                    hits = await store.search(kb["collection"], query, k)
                else:
                    hits = await store.search(kb["collection"], vectors[(kb["provider"], kb["model"])][0], k)
            except StoreError as e:
                raise RagError(f"{kb['label']}: {e}") from e
            return [{"kb": kb["id"], "kb_label": kb["label"], "doc_id": h.payload.get("doc_id"),
                     "source": source_of(kb, h.payload) if kb["managed"] else h.payload.get("source"),
                     "chunk": h.payload.get("chunk"), "text": str(h.payload.get("text", ""))[:MAX_HIT_CHARS], "score": round(h.score, 4),
                     "score_kind": "provider" if kb["managed"] else "cosine"}
                    for h in hits]
        parts = await asyncio.gather(*(one(kb) for kb in kbs))
        # Cosine scores from any vector store are comparable, so they form ONE group sorted by score. Each managed knowledge base
        # is its own group in the provider's own order (its scores are on a different scale). Groups are then interleaved
        # round-robin: the best of each, then the second best of each... so a managed hit is never ranked by comparing scores.
        ranked = [sorted((h for kb, part in zip(kbs, parts) if not kb["managed"] for h in part), key=lambda h: h["score"], reverse=True)]
        ranked += [part for kb, part in zip(kbs, parts) if kb["managed"]]
        merged = [hit for tier in zip_longest(*ranked) for hit in tier if hit is not None]
        return merged[:k]
