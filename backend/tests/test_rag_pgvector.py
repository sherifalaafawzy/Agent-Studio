"""PgVectorStore against an in-memory PostgreSQL (FakePg): behaviour, error mapping and SQL-injection resistance."""
import asyncio
import sys
import types

import pytest

from app.rag.stores import pgvector as PG
from app.rag.stores import registry as R
from app.rag.stores.base import Point, StoreError
from app.rag.stores.pgvector import PgVectorStore
from tests.rag_fakes import DIM, vec_for
from tests.rag_fakes_stores import FakePg

run = asyncio.run
T = '"public"."kb_x"'


def pt(pid, text, doc="d1"):
    return Point(pid, vec_for(text), {"doc_id": doc, "source": "s", "chunk": 0, "text": text})


DOCS = [pt("00000000-0000-0000-0000-000000000001", "red apple pie recipe"), pt("00000000-0000-0000-0000-000000000002", "blue ocean waves"),
        pt("00000000-0000-0000-0000-000000000003", "green apple orchard", doc="d2")]


def store(pg=None, **kw):
    pg = pg or FakePg()
    return PgVectorStore({"host": "h", "password": "pw"}, "h:5432", driver=pg, **kw), pg


def sql_text(pg):
    return "\n".join(s for s, _ in pg.statements)


# --------------------------------------------------------------------------------------------------------------- health
def test_health_reports_version_and_extension():
    s, pg = store()
    assert "not enabled yet" in run(s.health())["detail"]
    run(s.ensure("kb_x", DIM))
    assert run(s.health()) == {"ok": True, "detail": "PostgreSQL 17.6 with pgvector 0.8.6"}
    pg.version = "weird"
    assert run(s.health())["detail"] == "PostgreSQL weird with pgvector 0.8.6"


def test_health_reports_unreachable_and_bad_credentials_instead_of_raising():
    s, pg = store(FakePg(password="right"))
    h = run(s.health())
    assert h == {"ok": False, "detail": "PostgreSQL rejected the user name or password"}
    s, pg = store()
    pg.down = 'connection failed: connection to server at "127.0.0.1", port 5433 failed: Connection refused\nIs the server running?'
    h = run(s.health())
    assert h["ok"] is False and h["detail"].startswith("cannot reach PostgreSQL at h:5432: connection failed") and "Is the server" not in h["detail"]
    pg.down = 'connection failed: FATAL:  database "nope" does not exist'
    assert "no such database" in run(s.health())["detail"]


def test_a_missing_driver_is_explained(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(StoreError, match="driver is not installed"):
        run(PgVectorStore({}).count("kb_x"))


def test_the_default_driver_is_psycopg(monkeypatch):
    pg = FakePg()
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=pg.connect, Error=pg.Error, OperationalError=pg.OperationalError))
    assert run(PgVectorStore({"host": "h"}).health())["ok"] is True
    assert pg.connects[0] == {"host": "h", "connect_timeout": 10, "autocommit": True}


# --------------------------------------------------------------------------------------------------------------- ensure
def test_ensure_creates_the_table_with_cosine_hnsw_and_a_doc_index():
    s, pg = store()
    run(s.ensure("kb_x", DIM))
    run(s.ensure("kb_x", DIM))                                              # idempotent: nothing is created twice
    t = pg.tables[T]
    assert t["dim"] == DIM and pg.statements[0][0] == "CREATE EXTENSION IF NOT EXISTS vector"
    assert [x for x in t["indexes"] if "hnsw" in x and "vector_cosine_ops" in x] and [x for x in t["indexes"] if "(doc_id)" in x]
    assert sql_text(pg).count("CREATE TABLE") == 1 and "CREATE SCHEMA" not in sql_text(pg)


def test_large_vectors_skip_the_hnsw_index_and_absurd_sizes_are_refused():
    s, pg = store()
    run(s.ensure("kb_x", PG.MAX_HNSW_DIM + 1))
    assert not [x for x in pg.tables[T]["indexes"] if "hnsw" in x] and len(pg.tables[T]["indexes"]) == 1
    run(s.ensure("kb_y", PG.MAX_HNSW_DIM))
    assert [x for x in pg.tables['"public"."kb_y"']["indexes"] if "hnsw" in x]
    for bad in (0, -1, PG.MAX_DIM + 1, "32", 3.5):
        with pytest.raises(StoreError, match="pgvector supports 1 to 16000"):
            run(s.ensure("kb_z", bad))


def test_ensure_rejects_a_different_dimension_on_an_existing_table():
    s, _ = store()
    run(s.ensure("kb_x", DIM))
    with pytest.raises(StoreError, match=f"already exists with dimension {DIM}, not 8"):
        run(s.ensure("kb_x", 8))


def test_ensure_explains_a_missing_extension():
    s, _ = store(FakePg(extension_available=False))
    with pytest.raises(StoreError, match="pgvector extension is not available.*CREATE EXTENSION vector"):
        run(s.ensure("kb_x", DIM))


def test_a_custom_schema_is_created_and_used():
    s, pg = store(schema="rag")
    run(s.ensure("kb_x", DIM))
    assert '"rag"."kb_x"' in pg.tables and 'CREATE SCHEMA IF NOT EXISTS "rag"' in sql_text(pg)


# ------------------------------------------------------------------------------------------------ data and parameters
def test_upsert_search_count_delete_and_drop():
    s, pg = store()
    run(s.ensure("kb_x", DIM))
    run(s.upsert("kb_x", DOCS))
    run(s.upsert("kb_x", [pt("00000000-0000-0000-0000-000000000001", "red apple pie recipe")]))      # same id: replaced
    assert run(s.count("kb_x")) == 3
    hits = run(s.search("kb_x", vec_for("apple recipe"), 2))
    assert [h.id for h in hits][0] == "00000000-0000-0000-0000-000000000001" and len(hits) == 2
    assert hits[0].score > hits[1].score and hits[0].payload["doc_id"] == "d1" and hits[0].payload["text"] == "red apple pie recipe"
    assert "<=>" in sql_text(pg) and run(s.delete_doc("kb_x", "d2")) == 1 and run(s.count("kb_x")) == 2
    run(s.drop("kb_x"))
    run(s.drop("kb_x"))
    assert T not in pg.tables


def test_upsert_is_sent_in_batches():
    s, pg = store()
    run(s.ensure("kb_x", DIM))
    run(s.upsert("kb_x", [pt(f"00000000-0000-0000-0000-{i:012d}", f"t{i}") for i in range(PG.UPSERT_BATCH + 1)]))
    assert [p for sql, p in pg.statements if sql.startswith("INSERT")] == [("<many>", PG.UPSERT_BATCH), ("<many>", 1)]


def test_missing_tables_are_empty_not_errors():
    s, _ = store()
    assert run(s.search("kb_x", vec_for("x"), 3)) == [] and run(s.count("kb_x")) == 0 and run(s.delete_doc("kb_x", "d")) == 0
    run(s.drop("kb_x"))


def test_values_travel_as_parameters_never_inside_the_sql_text():
    s, pg = store()
    nasty = "x'); DROP TABLE users; --"
    run(s.ensure("kb_x", DIM))
    run(s.upsert("kb_x", [Point("00000000-0000-0000-0000-000000000009", vec_for("hello"), {"doc_id": nasty, "text": nasty})]))
    run(s.delete_doc("kb_x", nasty))
    run(s.search("kb_x", vec_for("hello"), 1))
    assert "DROP TABLE users" not in sql_text(pg) and "x'" not in sql_text(pg)
    deletes = [p for sql, p in pg.statements if sql.startswith("DELETE")]
    assert deletes == [(nasty,)]


@pytest.mark.parametrize("name", ['x"; DROP TABLE users; --', "kb x", "kb-x", "1kb", "", "a" * 51, 'kb"', "kb.x", "kb;", "kb\nx", None, 5])
def test_table_names_that_are_not_plain_identifiers_never_reach_the_database(name):
    s, pg = store()
    for call in (s.ensure(name, DIM), s.upsert(name, DOCS), s.search(name, vec_for("x"), 1), s.delete_doc(name, "d"), s.drop(name), s.count(name)):
        with pytest.raises(StoreError, match="knowledge base collection must be letters"):
            run(call)
    assert pg.statements == [] and pg.connects == []


@pytest.mark.parametrize("schema", ['public"; DROP SCHEMA x; --', "a b", "", "x" * 51])
def test_schema_names_are_validated_too(schema):
    with pytest.raises(StoreError, match="schema must be letters"):
        PgVectorStore({}, schema=schema)


def test_identifiers_are_quoted():
    assert PG.ident("kb_x") == '"kb_x"' and PG.vec_literal([1, 2.5]) == "[1.0,2.5]"


# ------------------------------------------------------------------------------------------------------- error mapping
def test_a_wrong_vector_size_becomes_a_readable_error():
    s, _ = store()
    run(s.ensure("kb_x", DIM))
    with pytest.raises(StoreError, match="PostgreSQL error: expected 32 dimensions, not 2") as e:
        run(s.upsert("kb_x", [Point("00000000-0000-0000-0000-000000000001", [1.0, 2.0], {"doc_id": "d"})]))
    assert "DETAIL" not in str(e.value)


def test_other_database_errors_are_reported_with_their_first_line():
    s, pg = store()
    pg.fail_on = ("SELECT COUNT", "permission denied for table kb_x")
    run(s.ensure("kb_x", DIM))
    with pytest.raises(StoreError, match="PostgreSQL error: permission denied for table kb_x"):
        run(s.count("kb_x"))


def test_a_database_error_with_no_message_still_reads_well():
    assert PgVectorStore._first_line(Exception("")) == "unknown error"


# ---------------------------------------------------------------------------------------------------------- registry
def test_registry_builds_pgvector_from_host_fields_or_a_dsn(tmp_path):
    a = R.make_store("pgvector", {"host": "db.example.com", "port": "6543", "database": "d", "user": "u", "sslmode": "require", "schema": "rag"},
                     {"password": "pw"}, tmp_path)
    assert a._args == {"host": "db.example.com", "port": 6543, "dbname": "d", "user": "u", "sslmode": "require", "password": "pw",
                       "connect_timeout": 10, "autocommit": True} and a._where == "db.example.com:6543" and a._schema == "rag"
    b = R.make_store("pgvector", {"host": "h", "database": "d", "user": "u"}, {}, tmp_path)
    assert b._args["port"] == 5432 and b._args["sslmode"] == "prefer" and "password" not in b._args and b._schema == "public"
    c = R.make_store("pgvector", {}, {"dsn": "postgresql://u:p@h/d"}, tmp_path)
    assert c._args["conninfo"] == "postgresql://u:p@h/d" and "server in the connection string" in c._where
    with pytest.raises(StoreError, match="port must be a number"):
        R.make_store("pgvector", {"host": "h", "database": "d", "user": "u", "port": "abc"}, {}, tmp_path)


def test_registry_requires_a_dsn_or_host_database_and_user():
    for config in ({}, {"host": "h"}, {"host": "h", "database": "d"}):
        with pytest.raises(StoreError, match="connection string"):
            R.check_fields("pgvector", config, {})
    R.check_fields("pgvector", {"host": "h", "database": "d", "user": "u"}, {})
    R.check_fields("pgvector", {}, {"dsn": "postgresql://x"})
