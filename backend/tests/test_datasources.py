"""Data sources: the read-only guard, the connectors (real SQLite, fakes for the rest), the service, the agent tools, the API, data products and descriptors."""
from __future__ import annotations

import json
import sqlite3
import sys
import types
from types import SimpleNamespace

import httpx
import pytest

from app.agents import AgentDef
from app.datasources import base as DB
from app.datasources import connectors as CON
from app.datasources import service as DS
from app.datasources import sqlguard as G
from app.datasources.base import DataError
from app.datasources.tools import data_tools

LIM = {"max_rows": 100, "max_bytes": 200_000, "timeout_s": 30}


# ------------------------------------------------------------------------------------------------------------------- the guard
@pytest.mark.parametrize("sql", [
    "SELECT * FROM orders", "select 1;", "  SELECT a FROM t;;  ", "WITH x AS (SELECT 1) SELECT * FROM x", "SHOW TABLES", "DESCRIBE orders", "DESC orders",
    "EXPLAIN SELECT 1", "VALUES (1)", "TABLE t", "SELECT 'a; DROP TABLE x' AS t", 'SELECT "drop" FROM t', "SELECT 'it''s; DELETE' FROM t", "SELECT `delete` FROM t",
    "SELECT 1 -- ; DROP TABLE x", "SELECT /* DELETE FROM x; */ 1", "SELECT $$ drop; $$", "SELECT $q$ insert; $q$", "SELECT replace(name, 'a', 'b'), truncate(x, 2) FROM t",
    "SELECT comment, \"set\", updated_at, created_at FROM t", "SELECT price $ 2 FROM t".replace("$ ", "")])
def test_plain_reads_pass(sql):
    out = G.check_sql(sql)
    assert out and ";" not in out


@pytest.mark.parametrize("sql,why", [
    ("", "give a SQL query"), ("   ", "give a SQL query"), (None, "give a SQL query"), ("x" * 20_001, "too long"),
    ("SELECT 1; SELECT 2", "one statement"), ("SELECT 1; DROP TABLE x", "one statement"), ("DELETE FROM t", "must start with"), ("INSERT INTO t VALUES (1)", "must start with"),
    ("UPDATE t SET a=1", "must start with"), ("DROP TABLE t", "must start with"), ("CALL proc()", "must start with"), ("SET x = 1", "must start with"),
    ("SELECT * INTO newtable FROM t", "'INTO' is not allowed"), ("WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "'DELETE' is not allowed"),
    ("WITH d AS (INSERT INTO t VALUES (1) RETURNING *) SELECT 1", "'INSERT'"), ("SELECT * FROM t FOR UPDATE", "FOR UPDATE"), ("SELECT * FROM t FOR  NO KEY UPDATE", "FOR UPDATE"),
    ("SELECT * FROM t FOR SHARE", "FOR UPDATE"), ("SELECT pg_sleep(10)", "'pg_sleep'"), ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"), ("SELECT nextval('s')", "'nextval'"),
    ("SELECT set_config('a','b',false)", "set_config"), ("SELECT sleep(5)", "'sleep'"), ("SELECT load_file('/etc/passwd')", "load_file"), ("SELECT dblink('x','y')", "dblink"),
    ("EXPLAIN ANALYZE SELECT 1", "EXPLAIN ANALYZE"), ("SELECT 1 /* unterminated", r"unterminated /\* comment"), ("SELECT 'open", "unterminated quoted"),
    ("SELECT $$ open", r"unterminated \$\$"), ("SELECT 1 INTO OUTFILE '/tmp/x'", "'INTO'"), ("PRAGMA writable_schema=1", "must start with"), ("ATTACH 'x' AS y", "must start with"),
    ("SELECT 1 FROM t WHERE a = 1 AND EXECUTE", "'EXECUTE'")])
def test_everything_else_is_refused_with_a_reason(sql, why):
    with pytest.raises(DataError, match=why):
        G.check_sql(sql)


def test_a_dollar_that_is_not_a_quote_and_comment_removal():
    assert G.strip("SELECT $1, $a FROM t -- trailing\nWHERE x") .replace("\n", " ").startswith("SELECT $1, $a FROM t ")
    assert "hidden" not in G.strip("SELECT 1 /* hidden */ + 2")
    assert G.check_sql("SELECT 1 -- ; DROP") == "SELECT 1"


def test_soql():
    assert G.check_soql("SELECT Id, Name FROM Account WHERE Name = 'a;b';") == "SELECT Id, Name FROM Account WHERE Name = ''"
    for bad, why in [("", "give a SOQL"), (None, "give a SOQL"), ("x" * 20_001, "too long"), ("SELECT Id FROM A; SELECT Id FROM B", "one statement"), ("DELETE FROM Account", "must be SOQL")]:
        with pytest.raises(DataError, match=why):
            G.check_soql(bad)


# --------------------------------------------------------------------------------------------------------------- shared pieces
def test_limits_cells_and_result_shaping():
    assert DB.clean_limits(None) == {"max_rows": 100, "max_bytes": 200_000, "timeout_s": 30}
    assert DB.clean_limits({"max_rows": 5}) == {"max_rows": 5, "max_bytes": 200_000, "timeout_s": 30}
    for bad in ({"max_rows": 0}, {"max_rows": 5001}, {"max_rows": True}, {"max_rows": "9"}, {"timeout_s": 500}):
        with pytest.raises(DataError, match="must be a whole number between"):
            DB.clean_limits(bad)
    assert [DB.cell(v) for v in (None, True, 1, 2.5, "s", b"ab", bytearray(3), {"a": 1})] == [None, True, 1, 2.5, "s", "<2 bytes>", "<3 bytes>", "{'a': 1}"]
    out = DB.shape(["id", "email", "api_token", "note"], [[1, "a@b.co", "tok", "call 555-123-4567"], [2, None, "", "ok"], [3, "x", "y", "z"]], 2, 10_000, True)
    assert out["rows"] == [[1, "[email removed]", "[hidden]", "call [phone removed]"], [2, None, "", "ok"]] and out["truncated"] and out["redacted"] and out["row_count"] == 2
    raw = DB.shape(["email"], [["a@b.co"]], 10, 10_000, False)
    assert raw["rows"] == [["a@b.co"]] and not raw["redacted"] and not raw["truncated"]
    big = DB.shape(["t"], [["x" * 500]] * 10, 100, 1_000, False)
    assert big["row_count"] == 1 and big["truncated"] and not DB.shape(["t"], [[1]], 5, 100, True)["redacted"]


# ---------------------------------------------------------------------------------------------------------------- connectors
@pytest.fixture
def sqlite_file(tmp_path):
    p = tmp_path / "shop.sqlite"
    c = sqlite3.connect(p)
    c.executescript("CREATE TABLE orders(id INTEGER PRIMARY KEY, customer TEXT, total REAL); INSERT INTO orders VALUES (1,'ann',9.5),(2,'bob',20); CREATE VIEW big AS SELECT * FROM orders WHERE total>10;")
    c.commit()
    c.close()
    return p


def test_sqlite_connector_reads_and_cannot_write(sqlite_file):
    c = CON.Sqlite({"path": str(sqlite_file)}, {}, LIM)
    assert c.test() == {"ok": True, "detail": "connected to SQLite file"}
    assert c.tables() == [{"name": "big", "kind": "view"}, {"name": "orders", "kind": "table"}]
    assert c.columns("orders") == [{"name": "id", "type": "INTEGER"}, {"name": "customer", "type": "TEXT"}, {"name": "total", "type": "REAL"}]
    assert c.query("SELECT id, customer FROM orders ORDER BY id", 10) == (["id", "customer"], [[1, "ann"], [2, "bob"]])
    assert c.query("SELECT 1 WHERE 0", 10) == (["1"], [])
    with pytest.raises(DataError, match="readonly"):
        c.query("DELETE FROM orders", 10)                          # the engine itself refuses, even if the guard were bypassed
    assert sqlite3.connect(sqlite_file).execute("SELECT count(*) FROM orders").fetchone()[0] == 2
    with pytest.raises(DataError, match="no table called 'nope'"):
        c.columns("nope")
    with pytest.raises(DataError, match="does not exist"):
        CON.Sqlite({"path": str(sqlite_file) + ".missing"}, {}, LIM).test()
    with pytest.raises(DataError, match="missing Database file"):
        CON.Sqlite({}, {}, LIM)
    assert c.query("PRAGMA user_version", 5)[0] == ["user_version"]          # what the guard (not the engine) forbids is a different layer's job


def test_sqlite_stops_a_query_that_runs_too_long(sqlite_file):
    c = CON.Sqlite({"path": str(sqlite_file)}, {}, {**LIM, "timeout_s": 1})
    with pytest.raises(DataError, match="interrupted"):
        c.query("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT count(*) FROM c", 10)


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.description, self.rows = conn, None, []

    def execute(self, sql, params=()):
        self.conn.log.append((sql, tuple(params)))
        if self.conn.boom and self.conn.boom != "close":
            raise self.conn.boom if isinstance(self.conn.boom, Exception) else RuntimeError(self.conn.boom)
        if sql.startswith(("SET SESSION", "SELECT 1")) and sql != "SELECT 1":
            return
        cols, rows = next((v for k, v in self.conn.answers.items() if sql.startswith(k)), self.conn.default)
        self.description = [SimpleNamespace(name=c) if self.conn.named else (c,) for c in cols] if cols else None
        self.rows = rows

    def fetchmany(self, n):
        return self.rows[:n]


class FakeConn:
    def __init__(self, **kw):
        self.kw, self.log, self.boom, self.read_only, self.closed, self.named = kw, [], None, None, False, True
        self.answers = {}
        self.default = (["x"], [[1]])

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True
        if self.boom == "close":
            raise RuntimeError("close failed")


PG = {"host": "h", "database": "d", "user": "u"}


def test_postgres_connector_opens_read_only_sessions(monkeypatch):
    made: list = []

    def connect(**kw):
        c = FakeConn(**kw)
        c.answers = {"SELECT table_schema || '.' || table_nam": (["n", "t"], [["public.orders", "BASE TABLE"], ["sales.v", "VIEW"]]),
                     "SELECT column_name, data_type": (["column_name", "data_type"], [["id", "integer"], ["total", "numeric"]])}
        made.append(c)
        return c
    monkeypatch.setitem(CON.DRIVER, "postgres", connect)
    pg = CON.Postgres(PG, {"password": "pw"}, LIM)
    assert pg.test()["ok"]
    kw = made[-1].kw
    assert kw["host"] == "h" and kw["port"] == 5432 and kw["dbname"] == "d" and kw["password"] == "pw" and kw["sslmode"] == "prefer"
    assert "default_transaction_read_only=on" in kw["options"] and "statement_timeout=30000" in kw["options"] and made[-1].read_only is True and made[-1].closed
    assert pg.tables() == [{"name": "public.orders", "kind": "base table"}, {"name": "sales.v", "kind": "view"}]
    assert pg.columns("orders") == [{"name": "id", "type": "integer"}, {"name": "total", "type": "numeric"}] and made[-1].log[-1][1] == ("public", "orders")
    pg.columns("sales.v")
    assert made[-1].log[-1][1] == ("sales", "v") and "%s" in made[-1].log[-1][0]
    assert CON.Postgres(PG, {}, LIM)._connect().kw["password"] is None
    c = made[-1]
    c.boom = None

    def bad_connect(**kw):
        raise OSError("no route to host")
    monkeypatch.setitem(CON.DRIVER, "postgres", bad_connect)
    with pytest.raises(DataError, match="cannot connect \\(OSError: no route"):
        pg.test()
    boom = FakeConn()
    boom.boom = "permission denied"
    monkeypatch.setitem(CON.DRIVER, "postgres", lambda **kw: boom)
    with pytest.raises(DataError, match="the query failed \\(RuntimeError: permission denied"):
        pg.query("SELECT 1", 5)
    boom.boom = DataError("my own message")
    with pytest.raises(DataError, match="my own message"):
        pg.query("SELECT 1", 5)
    quiet = FakeConn()
    quiet.default = (None, [])
    monkeypatch.setitem(CON.DRIVER, "postgres", lambda **kw: quiet)
    assert pg.query("SELECT 1", 5) == ([], [])
    quiet.boom = "close"
    assert pg.query("SELECT 1", 5) == ([], [])                         # a failing close is not an error
    with pytest.raises(DataError, match="missing Host"):
        CON.Postgres({}, {}, LIM)


def test_postgres_default_driver_is_psycopg(monkeypatch):
    import psycopg
    seen = {}

    def connect(**kw):
        seen.update(kw)
        return FakeConn(**kw)
    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr(CON, "DRIVER", {})
    assert CON.Postgres(PG, {}, LIM)._connect().read_only is True and seen["host"] == "h"


def test_mysql_connector_sets_a_read_only_session(monkeypatch):
    made: list = []

    def connect(**kw):
        c = FakeConn(**kw)
        c.named = False
        c.answers = {"SELECT table_name, table_type": (["n", "t"], [["orders", "BASE TABLE"]]), "SELECT column_name, data_type": (["c", "t"], [["id", "int"]])}
        made.append(c)
        return c
    monkeypatch.setitem(CON.DRIVER, "mysql", connect)
    my = CON.Mysql({"host": "h", "database": "d", "user": "u"}, {}, LIM)
    assert my.tables() == [{"name": "orders", "kind": "base table"}]
    assert [s for s, _ in made[-1].log[:2]] == ["SET SESSION TRANSACTION READ ONLY", "SET SESSION MAX_EXECUTION_TIME=30000"] and made[-1].kw["password"] == "" and made[-1].kw["port"] == 3306
    assert my.columns("orders") == [{"name": "id", "type": "int"}] and made[-1].log[-1][1] == ("orders",)
    assert my.columns("db.orders") == [{"name": "id", "type": "int"}] and made[-1].log[-1][1] == ("orders",)
    assert my.query("SELECT x FROM t", 5) == (["x"], [[1]])
    fake = types.SimpleNamespace(connect=connect)
    monkeypatch.setitem(sys.modules, "pymysql", fake)
    monkeypatch.setattr(CON, "DRIVER", {})
    assert my.test()["ok"]                                                    # the default driver is pymysql


def test_the_database_connectors_refuse_unsafe_names():
    for bad in ("", "a b", "a;b", "1x", "a" * 200, "a.b.c.d"):
        with pytest.raises(DataError):
            CON.qualified(bad)
    assert CON.qualified("c.s.t") == "c.s.t" and CON.ident("abc_1") == "abc_1"
    with pytest.raises(DataError, match="not a plain name"):
        CON.ident("a.b")


def rest(kind, handler, cfg, secrets=None, token="tok"):
    CON.TRANSPORT = httpx.MockTransport(handler)
    return CON.CONNECTORS[kind](cfg, secrets or {}, LIM, token=lambda: token)


@pytest.fixture(autouse=True)
def _reset_transport():
    yield
    CON.TRANSPORT = None


DBX = {"host": "https://adb-1.azuredatabricks.net/", "warehouse_id": "wh1"}


def test_databricks_runs_statements_through_the_sql_api():
    seen: list = []
    answers: list = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, str(req.url), json.loads(req.content), req.headers["authorization"]))
        return httpx.Response(200, json=answers.pop(0))
    c = rest("databricks", h, {**DBX, "catalog": "main", "schema": "sales"}, {"token": "pat"})
    answers.append({"status": {"state": "SUCCEEDED"}, "manifest": {"schema": {"columns": [{"name": "a"}, {"name": "b"}]}}, "result": {"data_array": [["1", "x"]]}})
    assert c.query("SELECT a, b FROM t", 5) == (["a", "b"], [["1", "x"]])
    m, url, body, auth = seen[-1]
    assert url == "https://adb-1.azuredatabricks.net/api/2.0/sql/statements" and auth == "Bearer tok"
    assert body == {"warehouse_id": "wh1", "statement": "SELECT a, b FROM t", "wait_timeout": "30s", "on_wait_timeout": "CANCEL", "row_limit": 6, "byte_limit": 200_000,
                    "disposition": "INLINE", "format": "JSON_ARRAY", "catalog": "main", "schema": "sales"}
    answers.append({"status": {"state": "SUCCEEDED"}, "manifest": {}, "result": {}})
    assert c.query("SELECT 1", 5) == ([], [])
    answers.append({"status": {"state": "FAILED", "error": {"message": "TABLE_NOT_FOUND"}}})
    with pytest.raises(DataError, match="failed: TABLE_NOT_FOUND"):
        c.query("SELECT 1", 5)
    answers.append({"status": {"state": "CANCELED"}})
    with pytest.raises(DataError, match="canceled: it did not finish in time"):
        c.query("SELECT 1", 5)
    answers.append({"status": {"state": "SUCCEEDED"}, "manifest": {"schema": {"columns": [{"name": "n"}, {"name": "t"}]}}, "result": {"data_array": [["main.sales.orders", "MANAGED"]]}})
    assert c.tables() == [{"name": "main.sales.orders", "kind": "managed"}] and "main.information_schema.tables" in seen[-1][2]["statement"]
    answers.append({"status": {"state": "SUCCEEDED"}, "manifest": {}, "result": {"data_array": [["id", "int", None]]}})
    assert c.columns("main.sales.orders") == [{"name": "id", "type": "int"}] and seen[-1][2]["statement"] == "DESCRIBE TABLE main.sales.orders"
    bare = rest("databricks", h, DBX, {"token": "pat"})
    answers.append({"status": {"state": "SUCCEEDED"}, "manifest": {}, "result": {}})
    assert bare.tables() == [] and "system.information_schema.tables" in seen[-1][2]["statement"] and "catalog" not in seen[-1][2]
    answers.append({"status": {"state": "SUCCEEDED"}, "manifest": {}, "result": {}})
    assert bare.test() == {"ok": True, "detail": "signed in to Databricks SQL"}
    with pytest.raises(DataError, match="missing Access token"):
        CON.Databricks(DBX, {}, LIM)
    assert CON.Databricks(DBX, {"token": "pat"}, LIM).headers() == {"Authorization": "Bearer pat"}


SNOW = {"account": "myorg-acct", "warehouse": "wh"}


def test_snowflake_uses_the_sql_api_with_a_programmatic_access_token():
    seen: list = []
    reply: dict = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, str(req.url), json.loads(req.content) if req.content else None, dict(req.headers)))
        return reply["resp"]
    c = rest("snowflake", h, {**SNOW, "database": "DB", "schema": "PUB", "role": "RO"}, {"token": "pat"})
    reply["resp"] = httpx.Response(200, json={"resultSetMetaData": {"rowType": [{"name": "A"}]}, "data": [["1"], ["2"], ["3"]]})
    assert c.query("SELECT a FROM t", 2) == (["A"], [["1"], ["2"], ["3"]])
    m, url, body, hdr = seen[-1]
    assert url == "https://myorg-acct.snowflakecomputing.com/api/v2/statements" and hdr["x-snowflake-authorization-token-type"] == "PROGRAMMATIC_ACCESS_TOKEN" and hdr["authorization"] == "Bearer tok"
    assert body == {"statement": "SELECT a FROM t", "timeout": 30, "warehouse": "wh", "database": "DB", "schema": "PUB", "role": "RO"}
    reply["resp"] = httpx.Response(200, json={})
    assert c.query("SELECT 1", 5) == ([], [])
    reply["resp"] = httpx.Response(202, json={"statementHandle": "h-1"})
    with pytest.raises(DataError, match="did not finish in time and was cancelled"):
        c.query("SELECT slow", 5)
    assert seen[-1][1].endswith("/api/v2/statements/h-1/cancel")
    reply["resp"] = httpx.Response(202, json={})
    flaky = rest("snowflake", lambda r: httpx.Response(202, json={}) if r.url.path.endswith("statements") else httpx.Response(500, text="x"), SNOW, {"token": "p"})
    with pytest.raises(DataError, match="was cancelled"):
        flaky.query("SELECT slow", 5)                                       # a failing cancel does not hide the timeout
    reply["resp"] = httpx.Response(200, json={"resultSetMetaData": {"rowType": [{"name": "n"}, {"name": "t"}]}, "data": [["PUB.ORDERS", "BASE TABLE"]]})
    assert c.tables() == [{"name": "PUB.ORDERS", "kind": "base table"}]
    reply["resp"] = httpx.Response(200, json={"resultSetMetaData": {"rowType": []}, "data": [["ID", "NUMBER"]]})
    assert c.columns("pub.orders") == [{"name": "ID", "type": "NUMBER"}] and "table_name = 'ORDERS' AND table_schema = 'PUB'" in seen[-1][2]["statement"]
    c.columns("orders")
    assert "table_schema" not in seen[-1][2]["statement"]
    with pytest.raises(DataError, match="not a table name"):
        c.columns("pub.or'ders")
    assert CON.Snowflake(SNOW, {"token": "pat"}, LIM).headers()["Authorization"] == "Bearer pat"


BQ = {"project": "proj", "credentials_file": "/k.json"}


def test_bigquery_caps_the_bytes_billed():
    seen: list = []
    reply: dict = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, str(req.url), json.loads(req.content) if req.content else None))
        return reply[req.url.path] if req.url.path in reply else reply["default"]
    c = rest("bigquery", h, {**BQ, "location": "EU"})
    reply["default"] = httpx.Response(200, json={"jobComplete": True, "schema": {"fields": [{"name": "n"}]}, "rows": [{"f": [{"v": "1"}]}, {"f": [{"v": "2"}]}]})
    assert c.query("SELECT n FROM t", 5) == (["n"], [["1"], ["2"]])
    m, url, body = seen[-1]
    assert url.endswith("/projects/proj/queries") and body == {"query": "SELECT n FROM t", "useLegacySql": False, "maxResults": 6, "timeoutMs": 30000,
                                                                "maximumBytesBilled": "1000000000", "location": "EU"}
    reply["default"] = httpx.Response(200, json={"jobComplete": True})
    assert c.query("SELECT 1", 5) == ([], [])
    reply["default"] = httpx.Response(200, json={"jobComplete": False})
    with pytest.raises(DataError, match="did not finish in time"):
        c.query("SELECT 1", 5)
    plain = rest("bigquery", h, BQ)
    reply["default"] = httpx.Response(200, json={"jobComplete": True})
    plain.query("SELECT 1", 5)
    assert "location" not in seen[-1][2]
    reply["/bigquery/v2/projects/proj/datasets"] = httpx.Response(200, json={"datasets": [{"datasetReference": {"datasetId": "sales"}}]})
    reply["/bigquery/v2/projects/proj/datasets/sales/tables"] = httpx.Response(200, json={"tables": [{"tableReference": {"tableId": "orders"}, "type": "TABLE"}, {"tableReference": {"tableId": "v"}}]})
    assert c.tables() == [{"name": "sales.orders", "kind": "table"}, {"name": "sales.v", "kind": "table"}]
    reply["/bigquery/v2/projects/proj/datasets/sales/tables/orders"] = httpx.Response(200, json={"schema": {"fields": [{"name": "id", "type": "INTEGER"}, {"name": "x"}]}})
    assert c.columns("sales.orders") == [{"name": "id", "type": "INTEGER"}, {"name": "x", "type": ""}]
    reply["/bigquery/v2/projects/proj/datasets"] = httpx.Response(200, json={})
    assert c.test() == {"ok": True, "detail": "signed in to Google BigQuery"}


def test_bigquery_signs_in_with_a_service_account_key(monkeypatch):
    import google.auth.transport.requests as gtr
    from google.oauth2 import service_account

    class Creds:
        token = "bq-token"

        def refresh(self, request):
            pass
    seen = {}
    monkeypatch.setattr(service_account.Credentials, "from_service_account_file", staticmethod(lambda p, scopes: seen.update(p=p, scopes=scopes) or Creds()))
    monkeypatch.setattr(gtr, "Request", lambda: object())
    assert CON.BigQuery(BQ, {}, LIM)._fetch_token() == "bq-token" and seen == {"p": "/k.json", "scopes": ["https://www.googleapis.com/auth/bigquery.readonly"]}


SF = {"instance_url": "https://acme.my.salesforce.com/"}


def test_salesforce_reads_with_soql():
    seen: list = []
    reply: dict = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, str(req.url), dict(req.url.params)))
        return reply[req.url.path]
    base = "/services/data/v65.0"
    c = rest("salesforce", h, SF, {"access_token": "at"})
    reply[base + "/query"] = httpx.Response(200, json={"records": [{"attributes": {"type": "Account"}, "Id": "1", "Name": "Acme", "Owner": {"Name": "Ann"}}, {"Id": "2", "Name": None, "Extra": 5}]})
    cols, rows = c.query("SELECT Id, Name FROM Account", 10)
    assert cols == ["Id", "Name", "Owner", "Extra"] and rows == [["1", "Acme", '{"Name": "Ann"}', None], ["2", None, "null", 5]] or rows[0][:2] == ["1", "Acme"]
    assert seen[-1][2] == {"q": "SELECT Id, Name FROM Account"} and seen[-1][1].startswith("https://acme.my.salesforce.com/services/data/v65.0/query")
    reply[base + "/sobjects"] = httpx.Response(200, json={"sobjects": [{"name": "Account", "queryable": True}, {"name": "Hidden", "queryable": False}]})
    assert c.tables() == [{"name": "Account", "kind": "object"}]
    reply[base + "/sobjects/Account/describe"] = httpx.Response(200, json={"fields": [{"name": "Id", "type": "id"}, {"name": "Name"}]})
    assert c.columns("Account") == [{"name": "Id", "type": "id"}, {"name": "Name", "type": ""}]
    reply[base + "/query"] = httpx.Response(400, json=[{"message": "unexpected token", "errorCode": "MALFORMED_QUERY"}])
    with pytest.raises(DataError, match="error 400: unexpected token"):
        c.query("SELECT bad", 5)
    assert c.test()["ok"]
    with pytest.raises(DataError, match="give an access token, or a client id and secret"):
        CON.Salesforce(SF, {}, LIM)


def test_salesforce_client_credentials_flow():
    calls: list = []

    def h(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.path == "/services/oauth2/token":
            return httpx.Response(200, json={"access_token": "minted"}) if b"client_secret=sec" in req.content else httpx.Response(400, text="bad")
        return httpx.Response(200, json={"sobjects": []})
    CON.TRANSPORT = httpx.MockTransport(h)
    c = CON.Salesforce(SF, {"client_id": "cid", "client_secret": "sec"}, LIM)
    assert c._fetch_token() == "minted" and calls[0] == "https://acme.my.salesforce.com/services/oauth2/token"
    assert c.test()["ok"]
    with pytest.raises(DataError, match="refused the client credentials"):
        CON.Salesforce(SF, {"client_id": "cid", "client_secret": "wrong"}, LIM)._fetch_token()
    assert CON.Salesforce(SF, {"access_token": "static"}, LIM)._fetch_token() == "static"


def test_rest_errors_are_explained():
    def with_status(code, **kw):
        return rest("snowflake", lambda r: httpx.Response(code, **kw), SNOW, {"token": "t"})
    for code, kw, why in [(401, {}, "refused the credentials"), (403, {}, "lacks permission"), (500, {"json": {"error": {"message": "deep"}}}, "error 500: deep"),
                          (500, {"json": {"error": "flat"}}, "error 500: flat"), (500, {"json": {"message": "msg"}}, "error 500: msg"), (500, {"text": "raw body"}, "error 500: raw body"),
                          (500, {"json": [1, 2]}, "error 500"), (200, {"text": "<html>"}, "not JSON")]:
        with pytest.raises(DataError, match=why):
            with_status(code, **kw).tables()

    def down(req):
        raise httpx.ConnectError("down")
    with pytest.raises(DataError, match="cannot reach the service"):
        rest("snowflake", down, SNOW, {"token": "t"}).tables()
    bad_token = CON.Snowflake(SNOW, {"token": "t"}, LIM, token=lambda: (_ for _ in ()).throw(ValueError("expired")))
    with pytest.raises(DataError, match="credentials could not be used \\(ValueError: expired"):
        bad_token.tables()
    own = CON.Snowflake(SNOW, {"token": "t"}, LIM, token=lambda: (_ for _ in ()).throw(DataError("my own words")))
    with pytest.raises(DataError, match="my own words"):
        own.tables()


def test_connector_base_class_and_catalog():
    for fn in (lambda: CON.Connector({}, {}, LIM).test(), lambda: CON.Connector({}, {}, LIM).tables(), lambda: CON.Connector({}, {}, LIM).columns("t"),
               lambda: CON.Connector({}, {}, LIM).query("SELECT 1", 1), lambda: CON.DbApi({}, {}, LIM)._connect()):
        with pytest.raises(NotImplementedError):
            fn()
    assert set(CON.CONNECTORS) == {"sqlite", "postgres", "mysql", "databricks", "snowflake", "bigquery", "salesforce"}
    assert all(c.info()["fields"] for c in CON.CONNECTORS.values())
    managed = {k: c.managed_mcp for k, c in CON.CONNECTORS.items() if c.managed_mcp}
    assert set(managed) == {"databricks", "snowflake", "bigquery", "salesforce"} and "execute_sql_readonly" in managed["bigquery"]["tools"] and "sobject-reads" in managed["salesforce"]["url"]
    assert [f["name"] for f in CON.Postgres.fields if f["secret"]] == ["password"]
    assert CON.colname(("a", 1)) == "a" and CON.colname(SimpleNamespace(name="b")) == "b"


# ------------------------------------------------------------------------------------------------------------------- the service
class FakeConnector(CON.Connector):
    kind, label = "sqlite", "Fake"
    fields = [DB.F("path", "File")]

    def __init__(self, cfg, secrets, limits):
        super().__init__(cfg, secrets, limits)
        self.log = []

    def test(self):
        return {"ok": True, "detail": "fine"}

    def tables(self):
        return [{"name": "t", "kind": "table"}]

    def columns(self, table):
        if table == "boom":
            raise DataError("no such table 'boom'")
        return [{"name": "a", "type": "int"}]

    def query(self, sql, max_rows):
        self.log.append((sql, max_rows))
        if "boom" in sql:
            raise DataError("kaboom")
        return ["email", "n"], [["a@b.co", i] for i in range(max_rows + 1)]


@pytest.fixture
def svc(state):
    s = DS.DataService(state.db, factory=lambda kind, cfg, sec, limits: FakeConnector(cfg, sec, limits))
    s.fake = None
    return s


def test_set_source_validates_merges_secrets_and_hides_them(svc):
    ok = svc.set_source({"id": "sales-db", "kind": "sqlite", "config": {"path": "/x.db"}, "label": " Sales  ", "description": "orders"})
    assert ok["label"] == "Sales" and ok["redact"] is True and ok["enabled"] is True and ok["limits"] == LIM and ok["secrets_set"] == []
    for bad, why in [("x", "must be an object"), ({"id": "X", "kind": "sqlite"}, "id must be lowercase"), ({"id": "ab", "kind": "oracle"}, "kind must be one of"),
                     ({"id": "sales-db", "kind": "postgres", "config": {}}, "cannot change"), ({"id": "ab", "kind": "sqlite", "config": {"nope": "1"}}, "unknown or invalid"),
                     ({"id": "ab", "kind": "sqlite", "config": {"path": 5}}, "unknown or invalid"), ({"id": "ab", "kind": "sqlite", "config": {"path": "x" * 401}}, "unknown or invalid"),
                     ({"id": "ab", "kind": "sqlite", "secrets": {"path": "x"}}, "not secret settings"), ({"id": "ab", "kind": "sqlite", "secrets": "x"}, "not secret settings"),
                     ({"id": "ab", "kind": "sqlite", "config": {"path": "/x"}, "limits": {"max_rows": 0}}, "max rows must be")]:
        with pytest.raises(DataError, match=why):
            svc.set_source(bad)
    upd = svc.set_source({"id": "sales-db", "kind": "sqlite", "redact": False, "enabled": False, "limits": {"max_rows": 10}})
    assert upd["config"] == {"path": "/x.db"} and not upd["redact"] and not upd["enabled"] and upd["limits"]["max_rows"] == 10 and upd["label"] == "Sales"
    assert [s["id"] for s in svc.public()] == ["sales-db"] and len(svc.kinds()) == 7
    svc.factory = lambda kind, cfg, sec, limits: CON.CONNECTORS[kind](cfg, sec, limits)
    svc.set_source({"id": "pg", "kind": "postgres", "config": {"host": "h", "database": "d", "user": "u"}, "secrets": {"password": "hunter2"}})
    assert next(s for s in svc.public() if s["id"] == "pg")["secrets_set"] == ["password"] and "hunter2" not in json.dumps(svc.public())
    svc.set_source({"id": "pg", "kind": "postgres", "secrets": {"password": ""}})
    assert next(s for s in svc.public() if s["id"] == "pg")["secrets_set"] == []
    with pytest.raises(DataError, match="not set up: missing Host"):
        svc.set_source({"id": "pg2", "kind": "postgres", "config": {}})


def test_source_limit_and_delete_cascades(svc):
    for i in range(DS.MAX_SOURCES):
        svc.set_source({"id": f"s{i:02d}", "kind": "sqlite", "config": {"path": "/x"}})
    with pytest.raises(DataError, match="at most 30"):
        svc.set_source({"id": "extra", "kind": "sqlite", "config": {"path": "/x"}})
    svc.set_product({"id": "orders", "name": "Orders", "source": "s00"})
    svc.delete_source("s00")
    assert svc.products() == [] and all(s["id"] != "s00" for s in svc.public()) and "s00" not in svc._secrets()
    with pytest.raises(DataError, match="no data source called 's00'"):
        svc.delete_source("s00")


def test_stored_settings_that_are_unreadable_are_ignored(svc, state):
    state.db.set_setting(DS.CONFIG_KEY, "{bad")
    state.db.set_setting(DS.SECRETS_KEY, "[1]")
    state.db.set_setting(DS.PRODUCTS_KEY, "{x")
    assert svc.public() == [] and svc._secrets() == {} and svc.products() == []
    state.db.set_setting(DS.PRODUCTS_KEY, "[1]")
    assert svc.products() == []
    state.db.set_setting(DS.CONFIG_KEY, json.dumps({"sources": "nope"}))
    state.db.set_setting(DS.SECRETS_KEY, "{bad")
    state.db.set_setting(DS.PRODUCTS_KEY, json.dumps({"products": "nope"}))
    assert svc.public() == [] and svc._secrets() == {} and svc.products() == []


def test_queries_are_guarded_bounded_masked_and_audited(svc):
    svc.set_source({"id": "db", "kind": "sqlite", "config": {"path": "/x"}, "limits": {"max_rows": 3}})
    out = svc.query("db", "SELECT email, n FROM t", 50, agent="bot")
    assert out["rows"] == [["[email removed]", 0], ["[email removed]", 1], ["[email removed]", 2]] and out["truncated"] and out["redacted"] and out["row_count"] == 3
    assert svc.query("db", "SELECT 1 FROM t", None)["row_count"] == 3 and svc.query("db", "SELECT 1 FROM t", 0)["row_count"] == 3 and svc.query("db", "SELECT 1 FROM t", 1)["row_count"] == 1
    with pytest.raises(DataError, match="only reads are allowed"):
        svc.query("db", "DROP TABLE t", None, "bot")
    with pytest.raises(DataError, match="read-only"):
        svc.query("db", "SELECT * INTO copy FROM t", None, "bot")
    with pytest.raises(DataError, match="kaboom"):
        svc.query("db", "SELECT boom", None, "bot")
    svc.set_source({"id": "db", "kind": "sqlite", "redact": False})
    assert svc.query("db", "SELECT email, n FROM t", 1)["rows"] == [["a@b.co", 0]]
    svc.set_source({"id": "off", "kind": "sqlite", "config": {"path": "/x"}, "enabled": False})
    with pytest.raises(DataError, match="switched off"):
        svc.query("off", "SELECT 1", None)
    with pytest.raises(DataError, match="no data source called 'ghost'"):
        svc.query("ghost", "SELECT 1", None)
    log = svc.audit()
    assert [a["kind"] for a in log][:3] == ["query", "query", "query"] and any(a["agent"] == "bot" and not a["ok"] and "kaboom" in a["error"] for a in log)
    assert any(a["rows"] == 3 and a["ok"] for a in log) and len(svc.audit("db", 2)) == 2 and svc.audit("nope") == []
    svc.query("db", "SELECT 'ann@corp.com' FROM t", 1)
    assert "ann@corp.com" not in json.dumps(svc.audit()) and "[email removed]" in json.dumps(svc.audit())


def test_salesforce_sources_use_the_soql_guard(state):
    seen = []

    class SfFake(FakeConnector):
        kind, label = "salesforce", "SF"
        fields = [DB.F("instance_url", "URL")]

        def query(self, sql, max_rows):
            seen.append(sql)
            return ["Id"], [["1"]]
    svc = DS.DataService(state.db, factory=lambda kind, cfg, sec, limits: SfFake(cfg, sec, limits))
    svc.set_source({"id": "sf", "kind": "salesforce", "config": {"instance_url": "https://x"}})
    assert svc.query("sf", "SELECT Id FROM Account WHERE Name = 'a;b'", 5)["rows"] == [["1"]] and seen == ["SELECT Id FROM Account WHERE Name = ''"]
    with pytest.raises(DataError, match="must be SOQL"):
        svc.query("sf", "DELETE FROM Account", 5)


def test_schema_and_test_are_audited(svc):
    svc.set_source({"id": "db", "kind": "sqlite", "config": {"path": "/x"}})
    assert svc.schema("db") == {"tables": [{"name": "t", "kind": "table"}]} and svc.schema("db", "t", "bot") == {"columns": [{"name": "a", "type": "int"}]}
    with pytest.raises(DataError, match="no such table"):
        svc.schema("db", "boom", "bot")
    assert svc.test("db") == {"ok": True, "detail": "fine"}
    svc.factory = lambda *a: (_ for _ in ()).throw(DataError("cannot connect"))
    assert svc.test("db") == {"ok": False, "detail": "cannot connect"}
    assert svc.test("ghost")["ok"] is False
    assert {a["kind"] for a in svc.audit()} == {"schema", "test"}


def test_data_products(svc):
    svc.set_source({"id": "db", "kind": "sqlite", "config": {"path": "/x"}})
    p = svc.set_product({"id": "orders", "name": " Orders ", "source": "db", "objects": ["orders", "public.items"], "classification": "confidential", "owner": "Sales Ops",
                         "contains_pii": 1, "tags": ["sales"], "description": "all orders"})
    assert p == {"id": "orders", "name": "Orders", "description": "all orders", "source": "db", "objects": ["orders", "public.items"], "classification": "confidential",
                 "owner": "Sales Ops", "contains_pii": True, "tags": ["sales"]}
    assert svc.set_product({"id": "orders", "name": "Orders v2", "source": "db"})["classification"] == "internal" and len(svc.products()) == 1
    for bad, why in [("x", "must be an object"), ({"id": "X", "name": "n", "source": "db"}, "id must be lowercase"), ({"id": "ab", "name": " ", "source": "db"}, "give the data product a name"),
                     ({"id": "ab", "name": "n", "source": "nope"}, "no data source"), ({"id": "ab", "name": "n", "source": "db", "objects": "x"}, "at most 50"),
                     ({"id": "ab", "name": "n", "source": "db", "objects": ["a"] * 51}, "at most 50"), ({"id": "ab", "name": "n", "source": "db", "objects": ["bad;name"]}, "at most 50"),
                     ({"id": "ab", "name": "n", "source": "db", "classification": "secret"}, "classification must be"), ({"id": "ab", "name": "n", "source": "db", "tags": "x"}, "at most 10"),
                     ({"id": "ab", "name": "n", "source": "db", "tags": ["x" * 41]}, "at most 10")]:
        with pytest.raises(DataError, match=why):
            svc.set_product(bad)
    for i in range(DS.MAX_PRODUCTS - 1):
        svc.set_product({"id": f"p{i:03d}", "name": "n", "source": "db"})
    with pytest.raises(DataError, match="at most 100"):
        svc.set_product({"id": "overflow", "name": "n", "source": "db"})
    svc.delete_product("orders")
    with pytest.raises(DataError, match="no data product called 'orders'"):
        svc.delete_product("orders")


def test_descriptors_say_which_agent_does_what_and_sees_what(svc):
    svc.set_source({"id": "db", "kind": "sqlite", "config": {"path": "/x"}})
    svc.set_source({"id": "off", "kind": "sqlite", "config": {"path": "/x"}, "enabled": False})
    svc.set_product({"id": "orders", "name": "Orders", "source": "db"})
    agents = [AgentDef(id="a1", name="A1", description="does a", data=["db"], framework="adk", runtime="docker", acp_agents=["claude"]), AgentDef(id="a2", name="A2", data=["*"]),
              AgentDef(id="a3", name="A3", data=["off"]), AgentDef(id="a4", name="A4")]
    ext = [{"id": "claude", "label": "Claude Code", "kind": "acp", "enabled": True, "installed": False, "notes": "coding agent"}]
    d = svc.descriptors(agents, ext)
    rows = {r["id"]: r for r in d["agents"]}
    assert rows["a1"]["data_sources"] == ["db"] and rows["a1"]["data_products"] == ["orders"] and rows["a1"]["runtime"] == "docker" and rows["a1"]["sandbox"] == "default" and rows["a1"]["purpose"] == "does a"
    assert rows["a2"]["data_sources"] == ["db"] and rows["a3"]["data_sources"] == [] and rows["a4"]["data_products"] == []          # a switched-off source is not readable
    assert rows["claude"]["kind"] == "external" and rows["claude"]["used_by"] == ["a1"] and "outside Agent Studio" in rows["claude"]["note"] and rows["claude"]["launcher"] == "acp"
    assert [s["id"] for s in d["sources"]] == ["db", "off"] and d["products"][0]["id"] == "orders"
    assert svc.summary() == {"sources": 2, "kinds": ["sqlite"], "products": 1}
    assert DS.DataService.granted_ids(("*",), svc.public()) == ["db"] and DS.DataService.granted_ids((), svc.public()) == []


# ---------------------------------------------------------------------------------------------------------------- agent tools
def tool_ctx(svc, grants):
    return SimpleNamespace(data=svc, data_grants=tuple(grants), agent_id="bot")


@pytest.mark.asyncio
async def test_agent_tools_only_reach_granted_sources_and_never_write(svc):
    svc.set_source({"id": "db", "kind": "sqlite", "config": {"path": "/x"}, "description": "orders"})
    svc.set_source({"id": "secret", "kind": "sqlite", "config": {"path": "/y"}})
    t = data_tools(tool_ctx(svc, ["db"]))
    assert set(t) == {"data_sources", "data_schema", "data_query"}
    listing = await t["data_sources"]()
    assert [s["id"] for s in listing["sources"]] == ["db"] and listing["sources"][0]["personal_data_masked"] is True and listing["sources"][0]["max_rows"] == 100
    assert (await t["data_schema"]("db"))["tables"] == [{"name": "t", "kind": "table"}] and (await t["data_schema"]("db", "t"))["columns"][0]["name"] == "a"
    assert "not one of your data sources (db)" in (await t["data_schema"]("secret"))["error"]
    assert "not one of your data sources" in (await t["data_query"]("secret", "SELECT 1"))["error"]
    q = await t["data_query"]("db", "SELECT email, n FROM t", 2)
    assert q["ok"] and q["row_count"] == 2 and q["rows"][0][0] == "[email removed]" and "not instructions" in q["note"]
    assert "only reads are allowed" in (await t["data_query"]("db", "DELETE FROM t"))["error"]
    assert "no such table" in (await t["data_schema"]("db", "boom"))["error"]
    assert [s["id"] for s in (await data_tools(tool_ctx(svc, ["*"]))["data_sources"]())["sources"]] == ["db", "secret"]
    assert "none" in (await data_tools(tool_ctx(svc, ["nothing"]))["data_query"]("db", "SELECT 1"))["error"]


# ------------------------------------------------------------------------------------------------------------------ HTTP API
def test_api_end_to_end_with_a_real_sqlite_file(client, sqlite_file):
    kinds = client.get("/api/data/sources").json()
    assert [k["kind"] for k in kinds["kinds"]][0] == "sqlite" and kinds["sources"] == []
    assert client.put("/api/data/sources", json={"id": "shop", "kind": "sqlite", "config": {"path": str(sqlite_file)}, "label": "Shop"}).json()["label"] == "Shop"
    assert client.put("/api/data/sources", json={"id": "Bad", "kind": "sqlite"}).status_code == 422
    assert client.post("/api/data/sources/shop/test").json()["ok"]
    assert client.post("/api/data/sources/ghost/test").json()["ok"] is False
    assert [t["name"] for t in client.get("/api/data/sources/shop/schema").json()["tables"]] == ["big", "orders"]
    assert client.get("/api/data/sources/shop/schema?table=orders").json()["columns"][1]["name"] == "customer"
    assert client.get("/api/data/sources/ghost/schema").status_code == 404
    assert client.get("/api/data/sources/shop/schema?table=nope").status_code == 404
    q = client.post("/api/data/sources/shop/query", json={"sql": "SELECT customer, total FROM orders ORDER BY id"}).json()
    assert q["rows"] == [["ann", 9.5], ["bob", 20.0]] and q["columns"] == ["customer", "total"]
    assert client.post("/api/data/sources/shop/query", json={"sql": "DELETE FROM orders"}).status_code == 422
    assert client.post("/api/data/sources/shop/query", json={}).status_code == 422
    audit = client.get("/api/data/audit?source=shop&limit=20").json()["audit"]
    assert audit[0]["ok"] == 0 and audit[0]["agent"] == "you" and any(a["kind"] == "test" for a in audit)
    assert client.put("/api/data/products", json={"id": "orders", "name": "Orders", "source": "shop", "classification": "confidential", "contains_pii": True}).json()["classification"] == "confidential"
    assert client.put("/api/data/products", json={"id": "xy", "name": "n", "source": "ghost"}).status_code == 404
    assert [p["id"] for p in client.get("/api/data/products").json()["products"]] == ["orders"]
    d = client.get("/api/data/descriptors").json()
    assert {"agents", "products", "sources"} == set(d) and any(r["id"] == "claude" and r["kind"] == "external" for r in d["agents"])
    assert client.delete("/api/data/products/orders").json() == {"ok": True} and client.delete("/api/data/products/orders").status_code == 404
    assert client.delete("/api/data/sources/shop").json() == {"ok": True} and client.delete("/api/data/sources/shop").status_code == 404


def test_descriptors_survive_a_broken_cli_registry(client, state, monkeypatch):
    monkeypatch.setattr(state.acp.store, "list", lambda: (_ for _ in ()).throw(RuntimeError("no registry")))
    assert all(r["kind"] == "agent" for r in client.get("/api/data/descriptors").json()["agents"])


def test_agents_get_a_data_grant_that_switches_the_tools_on(client, sqlite_file):
    client.put("/api/data/sources", json={"id": "shop", "kind": "sqlite", "config": {"path": str(sqlite_file)}})
    made = client.post("/api/agents", json={"id": "analyst", "name": "Analyst", "data": ["shop"], "tools": ["read_file"]}).json()
    assert made["data"] == ["shop"] and {"data_sources", "data_schema", "data_query"} <= set(made["tools"])
    for bad in (["Bad Id"], "shop", [5]):
        assert client.put("/api/agents/analyst", json={"data": bad}).status_code == 422
    assert client.put("/api/agents/analyst", json={"data": ["*"]}).json()["data"] == ["*"]
    names = {t["name"] for t in client.get("/api/tools").json()}
    assert {"data_sources", "data_schema", "data_query"} <= names


def test_a_chat_turn_queries_the_data_through_the_tool(client, sqlite_file, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    client.put("/api/data/sources", json={"id": "shop", "kind": "sqlite", "config": {"path": str(sqlite_file)}})
    client.post("/api/agents", json={"id": "analyst", "name": "Analyst", "data": ["shop"]})
    msg = '!tool data_query {"source": "shop", "sql": "SELECT customer FROM orders ORDER BY id"}'
    j = client.post("/api/chat", json={"provider": "mock", "agent": "analyst", "message": msg}).json()
    results = [s for s in j["steps"] if s["type"] == "result" and s["tool"] == "data_query"]
    assert results and "ann" in json.dumps(results[0]) and "bob" in json.dumps(results[0])
    other = client.post("/api/agents", json={"id": "nodata", "name": "NoData"}).json()
    assert "data_query" not in other["tools"]
    assert any(a["agent"] == "analyst" and a["kind"] == "query" for a in client.get("/api/data/audit").json()["audit"])


# ---------------------------------------------------------------------------------------------------------- AI-BOM and discovery
@pytest.fixture
def rich(state, tmp_path, sqlite_file):
    from tests.aibom_helpers import seed
    seed(state, tmp_path)
    state.data.set_source({"id": "crm-prod", "kind": "sqlite", "config": {"path": str(sqlite_file)}, "label": "CRM production", "secrets": {}})
    state.data.set_product({"id": "customer-orders", "name": "Customer orders", "description": "Every order since 2020", "source": "crm-prod", "objects": ["orders"],
                            "classification": "restricted", "owner": "Sales Ops", "contains_pii": True, "tags": ["sales"]})
    state.agents.update("coder", {"data": ["crm-prod"], "sandbox": "docker-microvm"})
    return state


def test_the_bom_describes_runtimes_sandbox_and_data_without_secrets(rich):
    from app.aibom import builder as B
    from app.aibom.validate import validate_bom
    from tests.aibom_helpers import forbidden
    pub, internal = B.build_full(rich, "public"), B.build_full(rich, "internal")
    for bom in (pub, internal):
        assert validate_bom(bom)["ok"]
    by = lambda bom: {x["bom-ref"]: x for x in [*bom.get("services", []), *bom["components"]]}
    prop = lambda item, name: next((x["value"] for x in item.get("properties", []) if x["name"] == f"agentstudio:{name}"), None)
    plat = by(internal)["data-platform"]
    assert prop(plat, "data-sources") == "1" and prop(plat, "data-products") == "1" and prop(plat, "sandbox-kind") == "docker-container" and prop(plat, "sandbox-pids") == "256"
    a_int, a_pub = by(internal)["agent:coder"], by(pub)["agent:coder"]
    assert json.loads(prop(a_int, "data-sources")) == ["crm-prod"] and json.loads(prop(a_int, "data-products")) == ["customer-orders"] and prop(a_int, "data-classification") == "restricted"
    assert prop(a_int, "sandbox") == "docker-microvm" and json.loads(prop(a_int, "data-source-kinds")) == ["sqlite"]
    assert prop(a_pub, "data-sources") is None and prop(a_pub, "data-products") is None and prop(a_pub, "data-classification") == "restricted" and json.loads(prop(a_pub, "data-source-kinds")) == ["sqlite"]
    prod_int, prod_pub = by(internal)["dataproduct:customer-orders"], by(pub)["dataproduct:customer-orders"]
    assert prod_int["name"] == "Customer orders" and prop(prod_int, "owner") == "Sales Ops" and json.loads(prop(prod_int, "objects")) == ["orders"] and prop(prod_int, "contains-pii") == "true"
    assert prod_pub["name"] == "customer-orders" and prop(prod_pub, "owner") is None and prop(prod_pub, "objects") is None and prop(prod_pub, "objects-count") == "1"
    assert "description" not in prod_pub
    text = json.dumps(pub)
    for needle in ("Sales Ops", "Every order since 2020", "CRM production", "shop.sqlite", *forbidden("public")):
        assert needle not in text
    assert "dataproduct:customer-orders" in next(d for d in internal["dependencies"] if d["ref"] == "agent:coder")["dependsOn"]


def test_the_catalog_lists_data_products_and_external_agents(rich, tmp_path):
    from app.aibom.validate import validate_manifest
    from app.ard import signing
    from app.ard import site as ST
    from app.ard.settings import DiscoverySettings
    cfg = DiscoverySettings(rich.db)
    cfg.update({"publisher": "agents.acme-corp.dev", "enabled": True, "publish": {"agents": True, "mcp": True, "skills": True, "bom_system": True, "bom_agents": True, "bom_mcp": True, "bom_models": True}})
    site = ST.Publisher(rich, cfg, signing.Signer(tmp_path / "k.pem")).build()
    assert validate_manifest(site.manifest, "agents.acme-corp.dev")["ok"]
    by = {e["identifier"].split(":", 4)[-1]: e for e in site.manifest["entries"]}
    prod = by["data-product:customer-orders"]
    assert prod["type"] == ST.DATA_PRODUCT_TYPE and prod["data"]["classification"] == "restricted" and prod["data"]["contains_pii"] is True and "coder" in prod["data"]["readable_by"]
    assert "owner" not in prod["data"] and prod["data"]["objects_count"] == "1"
    claude = by["agent:external-claude"]
    assert claude["data"]["external"] is True and claude["data"]["governed_data_access"] is False and "super_engineer" in claude["data"]["used_by"]
    coder = by["agent:coder"]
    assert coder["data"]["sandbox"] == "docker-microvm" and coder["data"]["data_classification"] == "restricted" and coder["data"]["data_source_kinds"] == ["sqlite"]
    assert "data_sources" not in coder["data"] and "Sales Ops" not in json.dumps(site.manifest) and "CRM production" not in json.dumps(site.manifest)
