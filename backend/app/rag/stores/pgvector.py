"""PostgreSQL + pgvector: the Docker container we manage, or any Postgres you can reach (Neon, Supabase, RDS, your own).

One table per knowledge base: (id uuid, doc_id text, embedding vector(N), payload jsonb). Cosine distance (<=>), an HNSW
index where pgvector allows it (up to 2000 dimensions; above that search is an exact scan).

Safety: every value goes through query parameters. The only things placed in SQL text are table/schema names, and those
must match a strict pattern first and are then double-quoted. The dimension is an int checked against a range.

The driver is psycopg 3 in its blocking form, run in a worker thread: its async mode does not work on Windows'
default event loop.
"""
from __future__ import annotations

import asyncio
import json
import re

from .base import Hit, Point, StoreError, VectorStore

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,49}$")       # 50 max: index names add a suffix and Postgres stops at 63
MAX_DIM = 16000                                              # pgvector's limit for the vector type
MAX_HNSW_DIM = 2000                                          # HNSW on vector(N) is limited to 2000 dimensions
UPSERT_BATCH = 200


def ident(name: str, what: str = "name") -> str:
    """A safe, double-quoted SQL identifier, or StoreError. Never embeds anything that was not matched by NAME_RE."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise StoreError(f"the {what} must be letters, digits and _ only, starting with a letter or _ (1-50 characters)")
    return f'"{name}"'


def vec_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _default_driver():
    try:
        import psycopg
    except ImportError as e:
        raise StoreError("the PostgreSQL driver is not installed on the server: pip install 'psycopg[binary]'") from e
    return psycopg


class PgVectorStore(VectorStore):
    kind = "pgvector"

    def __init__(self, connect_args: dict, where: str = "PostgreSQL", schema: str = "public", driver=None, timeout: int = 10):
        self._args = {**connect_args, "connect_timeout": timeout, "autocommit": True}
        self._where, self._schema, self._driver = where, schema, driver
        self._schema_q = ident(schema, "schema")

    # ------------------------------------------------------------ plumbing
    def _table(self, collection: str) -> tuple[str, str]:
        quoted = ident(collection, "knowledge base collection")
        return f"{self._schema_q}.{quoted}", quoted

    def _run(self, fn):
        drv = self._driver or _default_driver()
        try:
            with drv.connect(**self._args) as conn:
                return fn(conn)
        except drv.OperationalError as e:
            raise StoreError(self._explain(str(e))) from e
        except drv.Error as e:
            raise StoreError(f"PostgreSQL error: {self._first_line(e)}") from e

    @staticmethod
    def _first_line(e: Exception) -> str:
        return (str(e).strip().splitlines() or ["unknown error"])[0][:300]

    def _explain(self, message: str) -> str:
        low = message.lower()
        if "password authentication failed" in low or "no password supplied" in low:
            return "PostgreSQL rejected the user name or password"
        if "does not exist" in low and "database" in low:
            return f"PostgreSQL has no such database ({self._first_line(Exception(message))})"
        return f"cannot reach PostgreSQL at {self._where}: {self._first_line(Exception(message))}"

    async def _go(self, fn):
        return await asyncio.to_thread(self._run, fn)

    @staticmethod
    def _exists(conn, qualified: str) -> bool:
        return conn.execute("SELECT to_regclass(%s)", (qualified,)).fetchone()[0] is not None

    # ------------------------------------------------------------ adapter
    async def health(self) -> dict:
        def go(conn):
            version = conn.execute("SELECT version()").fetchone()[0]
            ext = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
            return version, ext
        try:
            version, ext = await self._go(go)
        except StoreError as e:
            return {"ok": False, "detail": str(e)}
        short = version.split()[1] if len(version.split()) > 1 else version
        if ext is None:
            return {"ok": True, "detail": f"PostgreSQL {short}; the pgvector extension is not enabled yet (it is enabled when a knowledge base is created)"}
        return {"ok": True, "detail": f"PostgreSQL {short} with pgvector {ext[0]}"}

    async def ensure(self, collection: str, dim: int) -> None:
        if not isinstance(dim, int) or not 1 <= dim <= MAX_DIM:
            raise StoreError(f"pgvector supports 1 to {MAX_DIM} dimensions, not {dim}")
        table, quoted = self._table(collection)
        index = ident(f"{collection}_vec")
        doc_index = ident(f"{collection}_doc")

        def go(conn):
            try:
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            except Exception as e:  # noqa: BLE001 - any failure here means the extension is unusable; say how to fix it
                raise StoreError("the pgvector extension is not available: it must be installed on the server and enabled by a user "
                                 f"allowed to run CREATE EXTENSION vector ({self._first_line(e)})") from e
            if self._schema != "public":
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema_q}")
            row = conn.execute("SELECT atttypmod FROM pg_attribute WHERE attrelid = to_regclass(%s) AND attname = 'embedding'",
                               (table,)).fetchone()
            if row is not None:
                if row[0] != dim:
                    raise StoreError(f"table {collection} already exists with dimension {row[0]}, not {dim}")
                return
            conn.execute(f"CREATE TABLE {table} (id uuid PRIMARY KEY, doc_id text NOT NULL, "
                         f"embedding vector({int(dim)}) NOT NULL, payload jsonb NOT NULL)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS {doc_index} ON {table} (doc_id)")
            if dim <= MAX_HNSW_DIM:
                conn.execute(f"CREATE INDEX IF NOT EXISTS {index} ON {table} USING hnsw (embedding vector_cosine_ops)")
        await self._go(go)

    async def upsert(self, collection: str, points: list[Point]) -> None:
        table, _ = self._table(collection)
        sql = (f"INSERT INTO {table} (id, doc_id, embedding, payload) VALUES (%s::uuid, %s, %s::vector, %s::jsonb) "
               "ON CONFLICT (id) DO UPDATE SET doc_id = EXCLUDED.doc_id, embedding = EXCLUDED.embedding, payload = EXCLUDED.payload")

        def go(conn):
            for i in range(0, len(points), UPSERT_BATCH):
                rows = [(p.id, str(p.payload.get("doc_id", "")), vec_literal(p.vector), json.dumps(p.payload))
                        for p in points[i:i + UPSERT_BATCH]]
                with conn.transaction(), conn.cursor() as cur:
                    cur.executemany(sql, rows)
        await self._go(go)

    async def search(self, collection: str, vector: list[float], k: int) -> list[Hit]:
        table, _ = self._table(collection)

        def go(conn):
            if not self._exists(conn, table):
                return []
            lit = vec_literal(vector)
            return conn.execute(f"SELECT id::text, payload, 1 - (embedding <=> %s::vector) FROM {table} "
                                "ORDER BY embedding <=> %s::vector LIMIT %s", (lit, lit, int(k))).fetchall()
        return [Hit(str(pid), float(score), payload or {}) for pid, payload, score in await self._go(go)]

    async def delete_doc(self, collection: str, doc_id: str) -> int:
        table, _ = self._table(collection)

        def go(conn):
            if not self._exists(conn, table):
                return 0
            return conn.execute(f"DELETE FROM {table} WHERE doc_id = %s", (doc_id,)).rowcount
        return await self._go(go)

    async def drop(self, collection: str) -> None:
        table, _ = self._table(collection)
        await self._go(lambda conn: conn.execute(f"DROP TABLE IF EXISTS {table}"))

    async def count(self, collection: str) -> int:
        table, _ = self._table(collection)

        def go(conn):
            if not self._exists(conn, table):
                return 0
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return int(await self._go(go))
