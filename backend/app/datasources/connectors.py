"""Read-only connectors (abstract factory): SQLite, PostgreSQL, MySQL/MariaDB, Databricks, Snowflake, BigQuery and Salesforce.

Each connector offers the same four calls - `test`, `tables`, `columns`, `query` - and never writes: the SQL/SOQL guard (sqlguard.py) runs
in the service before `query`, and the connectors add a second layer where the platform has one (read-only sessions and transactions,
statement timeouts, a bytes-billed cap on BigQuery). Credentials arrive as secret fields and are never returned.
REST shapes were checked against the vendors' references on 2026-11-22 (Databricks SQL Statement Execution API 2.0, Snowflake SQL API v2,
BigQuery jobs.query v2, Salesforce REST query). `managed_mcp` records each vendor's official hosted MCP server (verified) - Agent Studio's
own adapter is used because those servers need an OAuth sign-in this app cannot do for you yet.
Database drivers and HTTP are injectable, so tests use fakes and never touch a database.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from .base import DataError, F, cell

TRANSPORT: httpx.BaseTransport | None = None            # tests inject httpx.MockTransport
DRIVER: dict[str, Callable[..., Any]] = {}               # tests inject a fake database driver: {"postgres": connect, "mysql": connect}
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,127}$")
QUALIFIED = re.compile(r"^[A-Za-z_][A-Za-z0-9_$-]{0,127}(\.[A-Za-z_][A-Za-z0-9_$-]{0,127}){0,2}$")


def ident(name: str) -> str:
    if not IDENT.match(name or ""):
        raise DataError(f"'{name}' is not a plain name")
    return name


def qualified(name: str) -> str:
    if not QUALIFIED.match(name or ""):
        raise DataError(f"'{name}' is not a table name")
    return name


class Connector:
    kind = ""
    label = ""
    description = ""
    docs = ""
    fields: list[dict] = []
    managed_mcp: dict | None = None
    note = ""                                            # what to tell the user about permissions / cost

    def __init__(self, cfg: dict, secrets: dict, limits: dict):
        self.cfg, self.limits = cfg, limits
        self.val = {**{f["name"]: f["default"] for f in self.fields if f["default"]}, **{k: v for k, v in cfg.items() if v not in (None, "")}, **secrets}
        miss = [f["label"] for f in self.fields if f["required"] and not self.val.get(f["name"])]
        if miss:
            raise DataError(f"{self.label} is not set up: missing {', '.join(miss)}")

    def test(self) -> dict:                              # {"ok": bool, "detail": str}
        raise NotImplementedError

    def tables(self) -> list[dict]:                      # [{"name", "kind"}]
        raise NotImplementedError

    def columns(self, table: str) -> list[dict]:         # [{"name", "type"}]
        raise NotImplementedError

    def query(self, sql: str, max_rows: int) -> tuple[list[str], list[list]]:
        raise NotImplementedError

    @classmethod
    def info(cls) -> dict:
        return dict(kind=cls.kind, label=cls.label, description=cls.description, docs=cls.docs, fields=cls.fields, managed_mcp=cls.managed_mcp, note=cls.note)


# ----------------------------------------------------------------------------------------------------------------- DB-API based
def colname(d: Any) -> str:
    return str(getattr(d, "name", None) or d[0])


class DbApi(Connector):
    param = "%s"
    tables_sql = ""
    columns_sql = ""

    def _connect(self) -> Any:
        raise NotImplementedError

    def _run(self, sql: str, params: tuple = (), max_rows: int = 500) -> tuple[list[str], list[list]]:
        try:
            conn = self._connect()
        except DataError:
            raise
        except Exception as e:  # noqa: BLE001 - driver errors name the problem (bad password, unreachable host)
            raise DataError(f"{self.label}: cannot connect ({type(e).__name__}: {str(e)[:200]})")
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            if cur.description is None:
                return [], []
            return [colname(d) for d in cur.description], [list(r) for r in cur.fetchmany(max_rows + 1)]
        except DataError:
            raise
        except Exception as e:  # noqa: BLE001
            raise DataError(f"{self.label}: the query failed ({type(e).__name__}: {str(e)[:300]})")
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def test(self) -> dict:
        self._run("SELECT 1")
        return dict(ok=True, detail=f"connected to {self.label}")

    def tables(self) -> list[dict]:
        _, rows = self._run(self.tables_sql, (), 500)
        return [dict(name=str(r[0]), kind=str(r[1]).lower()) for r in rows[:500]]

    def columns(self, table: str) -> list[dict]:
        schema, _, name = qualified(table).rpartition(".")
        n = self.columns_sql.count("{p}")
        _, rows = self._run(self.columns_sql.replace("{p}", self.param), (schema, name)[-n:], 500)
        return [dict(name=str(r[0]), type=str(r[1])) for r in rows]

    def query(self, sql: str, max_rows: int) -> tuple[list[str], list[list]]:
        return self._run(sql, (), max_rows)


class Sqlite(DbApi):
    kind, label = "sqlite", "SQLite file"
    description = "A SQLite database file on this PC (opened read-only)."
    docs = "https://www.sqlite.org/uri.html"
    fields = [F("path", "Database file", help="Full path to a .sqlite / .db file.")]
    param = "?"
    tables_sql = "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"

    def _connect(self) -> Any:
        p = Path(self.val["path"]).expanduser()
        if not p.is_file():
            raise DataError(f"the file {p} does not exist")
        conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        conn.execute("PRAGMA query_only=ON")
        deadline = time.time() + self.limits["timeout_s"]
        conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)
        return conn

    def columns(self, table: str) -> list[dict]:
        if table not in {t["name"] for t in self.tables()}:
            raise DataError(f"no table called '{table}'")
        _, rows = self._run(f'PRAGMA table_info("{table}")', (), 500)          # our own statement, with a name checked against the table list
        return [dict(name=r[1], type=r[2] or "") for r in rows]


class Postgres(DbApi):
    kind, label = "postgres", "PostgreSQL"
    description = "A PostgreSQL database. Sessions are opened read-only with a statement timeout."
    docs = "https://www.postgresql.org/docs/current/transaction-iso.html"
    fields = [F("host", "Host"), F("port", "Port", required=False, default="5432"), F("database", "Database"), F("user", "User"),
              F("password", "Password", secret=True, required=False), F("sslmode", "SSL mode", required=False, default="prefer")]
    note = "Use a database user that only has SELECT rights: the guard blocks writes, but permissions are the real protection."
    tables_sql = ("SELECT table_schema || '.' || table_name, table_type FROM information_schema.tables "
                  "WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1 LIMIT 500")
    columns_sql = "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema={p} AND table_name={p} ORDER BY ordinal_position"

    def _connect(self) -> Any:
        v = self.val
        connect = DRIVER.get("postgres")
        if connect is None:
            import psycopg
            connect = psycopg.connect
        conn = connect(host=v["host"], port=int(v["port"]), dbname=v["database"], user=v["user"], password=v.get("password") or None, sslmode=v["sslmode"], connect_timeout=10,
                       options=f"-c default_transaction_read_only=on -c statement_timeout={self.limits['timeout_s'] * 1000}")
        conn.read_only = True
        return conn

    def columns(self, table: str) -> list[dict]:
        if "." not in table:
            table = "public." + table
        return super().columns(table)


class Mysql(DbApi):
    kind, label = "mysql", "MySQL / MariaDB"
    description = "A MySQL or MariaDB database. Sessions are opened read-only with an execution time limit."
    docs = "https://dev.mysql.com/doc/refman/8.4/en/set-transaction.html"
    fields = [F("host", "Host"), F("port", "Port", required=False, default="3306"), F("database", "Database"), F("user", "User"), F("password", "Password", secret=True, required=False)]
    note = "Use a database user that only has SELECT rights: the guard blocks writes, but permissions are the real protection."
    tables_sql = "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY 1 LIMIT 500"
    columns_sql = "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name={p} ORDER BY ordinal_position"

    def _connect(self) -> Any:
        v = self.val
        connect = DRIVER.get("mysql")
        if connect is None:
            import pymysql
            connect = pymysql.connect
        conn = connect(host=v["host"], port=int(v["port"]), database=v["database"], user=v["user"], password=v.get("password") or "", connect_timeout=10,
                       read_timeout=self.limits["timeout_s"])
        cur = conn.cursor()
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        cur.execute(f"SET SESSION MAX_EXECUTION_TIME={int(self.limits['timeout_s']) * 1000}")
        return conn


# ------------------------------------------------------------------------------------------------------------------ REST based
class Rest(Connector):
    def __init__(self, cfg: dict, secrets: dict, limits: dict, token: Callable[[], str] | None = None):
        super().__init__(cfg, secrets, limits)
        self._token = token or self._fetch_token
        self.http = httpx.Client(transport=TRANSPORT, timeout=limits["timeout_s"] + 15)

    def _fetch_token(self) -> str:
        return self.val.get("token", "")

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}"}

    def req(self, method: str, url: str, ok: tuple[int, ...] = (200,), **kw: Any) -> httpx.Response:
        try:
            headers = {**self.headers(), **kw.pop("headers", {})}
        except DataError:
            raise
        except Exception as e:  # noqa: BLE001 - bad key file, expired token
            raise DataError(f"{self.label}: the credentials could not be used ({type(e).__name__}: {str(e)[:200]})")
        try:
            r = self.http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as e:
            raise DataError(f"{self.label}: cannot reach the service ({type(e).__name__})")
        if r.status_code in (401, 403):
            raise DataError(f"{self.label} refused the credentials or the account lacks permission (HTTP {r.status_code}).")
        if r.status_code not in ok:
            try:
                j = r.json()
                err = j.get("error") if isinstance(j, dict) else None
                msg = (err.get("message") if isinstance(err, dict) else err) or (j.get("message") if isinstance(j, dict) else None)
                if isinstance(j, list) and j and isinstance(j[0], dict):          # Salesforce answers with a list of {message, errorCode}
                    msg = j[0].get("message")
            except ValueError:
                msg = None
            raise DataError(f"{self.label} error {r.status_code}: {str(msg or r.text)[:300]}")
        return r

    def json(self, r: httpx.Response) -> Any:
        try:
            return r.json()
        except ValueError:
            raise DataError(f"{self.label} returned something that is not JSON")

    def test(self) -> dict:
        self.tables()
        return dict(ok=True, detail=f"signed in to {self.label}")


class Databricks(Rest):
    kind, label = "databricks", "Databricks SQL"
    description = "A Databricks SQL warehouse, through the SQL Statement Execution API."
    docs = "https://docs.databricks.com/api/workspace/statementexecution"
    fields = [F("host", "Workspace address", help="https://adb-1234567890.1.azuredatabricks.net"), F("warehouse_id", "SQL warehouse id"),
              F("token", "Access token", secret=True), F("catalog", "Catalog (optional)", required=False), F("schema", "Schema (optional)", required=False)]
    note = "Use a token for a service principal that has only SELECT / USE rights on the data this agent needs."
    managed_mcp = dict(url="{host}/api/2.0/mcp/sql", tools="DBSQL (read and write!), Genie spaces, Vector Search, Unity Catalog functions", status="Beta: an admin turns it on in Previews",
                       docs="https://docs.databricks.com/aws/en/generative-ai/mcp/managed-mcp", advice="Prefer the Genie MCP server for questions; the DBSQL one can also write.")

    def _stmt(self, statement: str, max_rows: int) -> tuple[list[str], list[list]]:
        body: dict = {"warehouse_id": self.val["warehouse_id"], "statement": statement, "wait_timeout": f"{min(max(self.limits['timeout_s'], 5), 50)}s", "on_wait_timeout": "CANCEL",
                      "row_limit": max_rows + 1, "byte_limit": self.limits["max_bytes"], "disposition": "INLINE", "format": "JSON_ARRAY"}
        for k in ("catalog", "schema"):
            if self.val.get(k):
                body[k] = self.val[k]
        d = self.json(self.req("POST", self.val["host"].rstrip("/") + "/api/2.0/sql/statements", json=body))
        st = d.get("status", {})
        if st.get("state") != "SUCCEEDED":
            raise DataError(f"Databricks: the query {str(st.get('state', 'failed')).lower()}: {(st.get('error') or {}).get('message', 'it did not finish in time')[:300]}")
        cols = [c["name"] for c in (d.get("manifest", {}).get("schema", {}).get("columns") or [])]
        return cols, [list(r) for r in (d.get("result", {}).get("data_array") or [])]

    def query(self, sql: str, max_rows: int) -> tuple[list[str], list[list]]:
        return self._stmt(sql, max_rows)

    def tables(self) -> list[dict]:
        cat = ident(self.val["catalog"]) if self.val.get("catalog") else "system"
        _, rows = self._stmt(f"SELECT concat(table_catalog, '.', table_schema, '.', table_name), table_type FROM {cat}.information_schema.tables "
                             "WHERE table_schema <> 'information_schema' LIMIT 500", 500)
        return [dict(name=str(r[0]), kind=str(r[1]).lower()) for r in rows]

    def columns(self, table: str) -> list[dict]:
        _, rows = self._stmt(f"DESCRIBE TABLE {qualified(table)}", 500)
        return [dict(name=str(r[0]), type=str(r[1])) for r in rows]


class Snowflake(Rest):
    kind, label = "snowflake", "Snowflake"
    description = "A Snowflake warehouse, through the SQL API."
    docs = "https://docs.snowflake.com/en/developer-guide/sql-api/reference"
    fields = [F("account", "Account", help="e.g. myorg-myaccount"), F("token", "Programmatic access token", secret=True), F("warehouse", "Warehouse"),
              F("database", "Database", required=False), F("schema", "Schema", required=False), F("role", "Role (give it read-only rights)", required=False)]
    note = "Use a role that only has SELECT / USAGE rights: the guard blocks writes, but the role is the real protection."
    managed_mcp = dict(url="https://<account>.snowflakecomputing.com/api/v2/databases/<db>/schemas/<schema>/mcp-servers/<name>", tools="Cortex Analyst, Cortex Search, SQL (as you define it)",
                       status="Generally available; you create it with CREATE MCP SERVER", docs="https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp",
                       advice="Use hyphens, not underscores, in the account host name.")

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}", "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN", "Accept": "application/json"}

    def _stmt(self, statement: str, max_rows: int) -> tuple[list[str], list[list]]:
        v = self.val
        body = {"statement": statement, "timeout": self.limits["timeout_s"], "warehouse": v["warehouse"], **{k: v[k] for k in ("database", "schema", "role") if v.get(k)}}
        r = self.req("POST", f"https://{v['account']}.snowflakecomputing.com/api/v2/statements", ok=(200, 202), json=body)
        d = self.json(r)
        if r.status_code == 202:
            try:
                self.req("POST", f"https://{v['account']}.snowflakecomputing.com/api/v2/statements/{d.get('statementHandle', '')}/cancel", ok=(200, 202, 422))
            except DataError:
                pass
            raise DataError("Snowflake: the query did not finish in time and was cancelled")
        cols = [c["name"] for c in d.get("resultSetMetaData", {}).get("rowType", [])]
        return cols, [list(x) for x in (d.get("data") or [])][: max_rows + 1]

    def query(self, sql: str, max_rows: int) -> tuple[list[str], list[list]]:
        return self._stmt(sql, max_rows)

    def tables(self) -> list[dict]:
        _, rows = self._stmt("SELECT table_schema || '.' || table_name, table_type FROM information_schema.tables WHERE table_schema <> 'INFORMATION_SCHEMA' ORDER BY 1 LIMIT 500", 500)
        return [dict(name=str(r[0]), kind=str(r[1]).lower()) for r in rows]

    def columns(self, table: str) -> list[dict]:
        schema, _, name = qualified(table).rpartition(".")
        where = f"table_name = '{ident(name).upper()}'" + (f" AND table_schema = '{ident(schema).upper()}'" if schema else "")
        _, rows = self._stmt(f"SELECT column_name, data_type FROM information_schema.columns WHERE {where} ORDER BY ordinal_position", 500)
        return [dict(name=str(r[0]), type=str(r[1])) for r in rows]


class BigQuery(Rest):
    kind, label = "bigquery", "Google BigQuery"
    description = "A BigQuery project, through the jobs.query API with a cap on the bytes a query may bill."
    docs = "https://cloud.google.com/bigquery/docs/reference/rest/v2/jobs/query"
    fields = [F("project", "Project id"), F("credentials_file", "Service-account JSON file", help="A key file for an account with only BigQuery Data Viewer and Job User."),
              F("location", "Location (optional)", required=False), F("max_bytes_billed", "Most bytes one query may bill", required=False, default="1000000000")]
    note = "BigQuery bills by the data a query scans. Queries above the cap are refused by BigQuery itself (default 1 GB)."
    managed_mcp = dict(url="https://bigquery.googleapis.com/mcp", tools="execute_sql_readonly, list_dataset_ids, get_dataset_info, list_table_ids, get_table_info",
                       status="Google-hosted; 3 minute and 3,000 row limits", docs="https://docs.cloud.google.com/bigquery/docs/use-bigquery-mcp",
                       advice="Use execute_sql_readonly, not execute_sql.")
    API = "https://bigquery.googleapis.com/bigquery/v2"

    def _fetch_token(self) -> str:
        import google.auth.transport.requests
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(self.val["credentials_file"], scopes=["https://www.googleapis.com/auth/bigquery.readonly"])
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token

    def query(self, sql: str, max_rows: int) -> tuple[list[str], list[list]]:
        body: dict = {"query": sql, "useLegacySql": False, "maxResults": max_rows + 1, "timeoutMs": self.limits["timeout_s"] * 1000, "maximumBytesBilled": str(self.val["max_bytes_billed"])}
        if self.val.get("location"):
            body["location"] = self.val["location"]
        d = self.json(self.req("POST", f"{self.API}/projects/{self.val['project']}/queries", json=body))
        if not d.get("jobComplete"):
            raise DataError("BigQuery: the query did not finish in time")
        cols = [f["name"] for f in d.get("schema", {}).get("fields", [])]
        return cols, [[c.get("v") for c in r["f"]] for r in d.get("rows", [])]

    def tables(self) -> list[dict]:
        out: list[dict] = []
        sets = self.json(self.req("GET", f"{self.API}/projects/{self.val['project']}/datasets", params={"maxResults": "20"})).get("datasets", [])
        for ds in sets:
            did = ds["datasetReference"]["datasetId"]
            for t in self.json(self.req("GET", f"{self.API}/projects/{self.val['project']}/datasets/{did}/tables", params={"maxResults": "100"})).get("tables", []):
                out.append(dict(name=f"{did}.{t['tableReference']['tableId']}", kind=str(t.get("type", "table")).lower()))
        return out

    def columns(self, table: str) -> list[dict]:
        did, _, tid = qualified(table).partition(".")
        d = self.json(self.req("GET", f"{self.API}/projects/{self.val['project']}/datasets/{did}/tables/{tid}"))
        return [dict(name=f["name"], type=f.get("type", "")) for f in d.get("schema", {}).get("fields", [])]


class Salesforce(Rest):
    kind, label = "salesforce", "Salesforce"
    description = "A Salesforce org, read with SOQL through the REST API."
    docs = "https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/dome_query.htm"
    fields = [F("instance_url", "Org address", help="https://yourcompany.my.salesforce.com"), F("access_token", "Access token", secret=True, required=False),
              F("client_id", "Connected app client id (instead of a token)", required=False), F("client_secret", "Client secret", secret=True, required=False),
              F("api_version", "API version", required=False, default="v65.0")]
    note = "Give the integration user a profile with read-only access to the objects this agent needs. Give an access token, or a client id + secret (client credentials flow)."
    managed_mcp = dict(url="https://api.salesforce.com/platform/mcp/v1/platform/sobject-reads", tools="SObject Reads: schema, SOQL, SOSL, relationships (read-only)",
                       status="Generally available since April 2026; an admin activates it in Setup; may bill Flex Credits",
                       docs="https://developer.salesforce.com/docs/platform/hosted-mcp-servers/guide/sobject-reads.html", advice="Use the read-only 'sobject-reads' server.")

    def __init__(self, cfg: dict, secrets: dict, limits: dict, token: Callable[[], str] | None = None):
        super().__init__(cfg, secrets, limits, token)
        if not self.val.get("access_token") and not (self.val.get("client_id") and self.val.get("client_secret")):
            raise DataError("Salesforce is not set up: give an access token, or a client id and secret")

    def _fetch_token(self) -> str:
        if self.val.get("access_token"):
            return self.val["access_token"]
        r = self.http.post(self.val["instance_url"].rstrip("/") + "/services/oauth2/token",
                           data={"grant_type": "client_credentials", "client_id": self.val["client_id"], "client_secret": self.val["client_secret"]})
        if r.status_code >= 400:
            raise DataError(f"Salesforce refused the client credentials (HTTP {r.status_code})")
        return r.json()["access_token"]

    def _url(self, path: str) -> str:
        return f"{self.val['instance_url'].rstrip('/')}/services/data/{self.val['api_version']}{path}"

    def query(self, sql: str, max_rows: int) -> tuple[list[str], list[list]]:
        d = self.json(self.req("GET", self._url("/query"), params={"q": sql}))
        recs = (d.get("records") or [])[: max_rows + 1]
        cols: list[str] = []
        for r in recs:
            for k in r:
                if k != "attributes" and k not in cols:
                    cols.append(k)
        return cols, [[cell(json.dumps(r.get(c)) if isinstance(r.get(c), (dict, list)) else r.get(c)) for c in cols] for r in recs]

    def tables(self) -> list[dict]:
        d = self.json(self.req("GET", self._url("/sobjects")))
        return [dict(name=o["name"], kind="object") for o in d.get("sobjects", []) if o.get("queryable")][:500]

    def columns(self, table: str) -> list[dict]:
        d = self.json(self.req("GET", self._url(f"/sobjects/{ident(table)}/describe")))
        return [dict(name=f["name"], type=f.get("type", "")) for f in d.get("fields", [])]


CONNECTORS: dict[str, type[Connector]] = {c.kind: c for c in (Sqlite, Postgres, Mysql, Databricks, Snowflake, BigQuery, Salesforce)}
