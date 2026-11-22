"""The adapter every vector store implements. A store holds named collections of (id, vector, payload) points.

Payload keys the service relies on: doc_id, source, chunk (index), text.
All methods are async so network stores and file stores look the same to callers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class StoreError(Exception):
    """The store refused or could not be reached. The message is safe to show to the user."""


@dataclass(frozen=True)
class Point:
    id: str                       # a UUID string (stable per document chunk)
    vector: list[float]
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Hit:
    id: str
    score: float                  # cosine similarity, higher is closer
    payload: dict = field(default_factory=dict)


class VectorStore(ABC):
    kind: str = ""

    @abstractmethod
    async def health(self) -> dict:
        """{ok: bool, detail: str}. Never raises for an unreachable store; reports it instead."""

    @abstractmethod
    async def ensure(self, collection: str, dim: int) -> None:
        """Create the collection if missing. Raises StoreError if it exists with a different dimension."""

    @abstractmethod
    async def upsert(self, collection: str, points: list[Point]) -> None: ...

    @abstractmethod
    async def search(self, collection: str, vector: list[float], k: int) -> list[Hit]:
        """The k closest points, best first. A missing collection returns []."""

    @abstractmethod
    async def delete_doc(self, collection: str, doc_id: str) -> int:
        """Remove every point of one document. Returns how many were removed."""

    @abstractmethod
    async def drop(self, collection: str) -> None:
        """Remove the whole collection. Missing is fine."""

    @abstractmethod
    async def count(self, collection: str) -> int: ...


@dataclass(frozen=True)
class ManagedRef:
    collection: str               # what the service stores and passes back on every call (a corpus name, a knowledge base id)
    owned: bool                   # True when Agent Studio created it (so deleting our knowledge base may delete it)


class ManagedStore(ABC):
    """A provider that embeds, chunks and stores documents itself (Vertex AI RAG Engine, Amazon Bedrock Knowledge Bases).

    It does NOT fit VectorStore: we never see vectors. Search takes the query TEXT, and a knowledge base has no
    embedding provider, model or dimension. Ingestion is optional: a store that cannot take files from us is
    search-only (can_ingest is False and ingest_note says why).
    """
    kind: str = ""
    managed = True
    ingest_note: str = "this store is search-only: add documents in the provider's own console"

    @property
    def can_ingest(self) -> bool:
        return False

    @abstractmethod
    async def health(self) -> dict:
        """{ok: bool, detail: str}. Never raises for an unreachable store; reports it instead."""

    @abstractmethod
    async def ensure(self, name: str, remote: str) -> ManagedRef:
        """Attach the existing remote resource `remote`, or (when empty and the provider allows it) create one called `name`."""

    @abstractmethod
    async def search(self, collection: str, query: str, k: int) -> list[Hit]:
        """The k best passages for the query text, best first (the provider's own ranking and score scale)."""

    @abstractmethod
    async def drop(self, collection: str) -> None:
        """Delete the remote resource. Only called for resources we own."""

    async def ingest(self, collection: str, doc_id: str, source: str, text: str) -> str:
        """Add one document (already text). Returns a short note for the user ('' if none)."""
        raise StoreError(self.ingest_note)

    async def delete_doc(self, collection: str, doc_id: str) -> int:
        raise StoreError(self.ingest_note)

    async def sync(self, collection: str) -> str:
        """Ask the provider to (re)index. Returns a message for the user."""
        return "nothing to sync: this store indexes uploads immediately"
