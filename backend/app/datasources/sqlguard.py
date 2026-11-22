"""The read-only gate in front of every data source. An allowlist, not a blocklist: a statement must START like a read and must not
contain anything that writes, changes settings, runs code or locks rows. Comments and quoted text are removed/ignored first so they
can neither hide a write nor trigger a false alarm. This is the first of two layers: connectors also open read-only sessions /
transactions and the user is told to give the account read-only rights (the guard is not a substitute for permissions).
"""
from __future__ import annotations

import re

from .base import DataError

STARTS = {"select", "with", "show", "describe", "desc", "explain", "values", "table"}
# Words that can only mean a write or an action. (Words that are also everyday column or function names - comment, set, list, replace,
# truncate... - are not here: a statement that starts with SELECT/WITH and has no ";" cannot start a second statement with them.)
FORBIDDEN = {"insert", "update", "delete", "merge", "upsert", "drop", "alter", "create", "grant", "revoke", "call", "exec", "execute", "copy", "into",
             "attach", "detach", "pragma", "vacuum", "unload", "outfile", "dumpfile"}
FUNC_PREFIX = ("pg_read_", "pg_ls_", "pg_terminate", "pg_cancel", "pg_reload", "pg_rotate", "pg_sleep", "pg_advisory", "lo_import", "lo_export", "lo_create", "lo_unlink",
               "dblink", "load_extension", "load_file")
FUNC_EXACT = {"nextval", "setval", "set_config", "sleep", "benchmark", "openrowset", "opendatasource"}
LOCKING = re.compile(r"\bfor\s+(no\s+key\s+)?(update|share|key\s+share)\b", re.I)
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def strip(sql: str) -> str:
    """The statement with comments removed and quoted text/identifiers replaced by a placeholder (so words inside them are never read as keywords)."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j < 0:
                raise DataError("the query has an unterminated /* comment")
            i = j + 2
            out.append(" ")
        elif c in "'\"`":
            j = i + 1
            while True:
                j = sql.find(c, j)
                if j < 0:
                    raise DataError("the query has an unterminated quoted string")
                if sql.startswith(c * 2, j):              # doubled quote = an escaped quote
                    j += 2
                    continue
                break
            out.append("''" if c == "'" else '""')
            i = j + 1
        elif c == "$":
            m = re.match(r"\$([A-Za-z_]\w*)?\$", sql[i:])
            if m:
                j = sql.find(m.group(0), i + len(m.group(0)))
                if j < 0:
                    raise DataError("the query has an unterminated $$ string")
                out.append("''")
                i = j + len(m.group(0))
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def check_sql(sql: str) -> str:
    """The comment-free statement when it is a plain read; DataError (with a plain reason) otherwise."""
    if not isinstance(sql, str) or not sql.strip():
        raise DataError("give a SQL query")
    if len(sql) > 20_000:
        raise DataError("the query is too long (20,000 characters at most)")
    body = strip(sql).strip()
    while body.endswith(";"):
        body = body[:-1].rstrip()
    if ";" in body:
        raise DataError("only one statement at a time is allowed")
    words = [w.lower() for w in WORD.findall(body)]
    if not words or words[0] not in STARTS:
        raise DataError(f"only reads are allowed: the query must start with {', '.join(w.upper() for w in sorted(STARTS))}")
    if LOCKING.search(body):
        raise DataError("this data source is read-only: FOR UPDATE / FOR SHARE locks rows and is not allowed")
    bad = sorted({w for w in words if w in FORBIDDEN})
    if bad:
        raise DataError(f"this data source is read-only: '{bad[0].upper()}' is not allowed")
    for w in words:
        if w in FUNC_EXACT or w.startswith(FUNC_PREFIX):
            raise DataError(f"the function '{w}' is not allowed on a read-only data source")
    if words[0] == "explain" and "analyze" in words:
        raise DataError("EXPLAIN ANALYZE runs the statement and is not allowed")
    return body


def check_soql(soql: str) -> str:
    """Salesforce: SOQL can only read, but make sure it really is one SELECT."""
    if not isinstance(soql, str) or not soql.strip():
        raise DataError("give a SOQL query")
    if len(soql) > 20_000:
        raise DataError("the query is too long (20,000 characters at most)")
    body = strip(soql).strip().rstrip(";").strip()
    if ";" in body:
        raise DataError("only one statement at a time is allowed")
    if not re.match(r"(?i)select\b", body):
        raise DataError("Salesforce queries must be SOQL: SELECT fields FROM Object WHERE ...")
    return body
