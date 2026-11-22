---
name: sql-analysis
group: Data
mcp:
- duckdb
- postgres
- clickhouse
description: 'Answer a business question with careful read-only SQL: inspect the schema, write the query, sanity-check the result and explain it. Use for ad-hoc analysis on a connected database or files.'
---

# SQL analysis

**When to use:** a question that can be answered with data in a SQL database (Postgres, ClickHouse, DuckDB files, ...).

1. Restate the question as a metric, grain and time window ("monthly active customers, by country, last 12 months"). Ask if the definition is ambiguous.
2. Inspect before querying: list tables, columns, types, row counts, keys. Sample a few rows. Never assume a column's meaning from its name - check values.
3. Write **read-only** SQL (SELECT only). Add a LIMIT while exploring; avoid full scans of huge tables without a date filter. Show the final SQL.
4. Sanity-check: totals reconcile to a known figure, no duplicated join keys (compare counts before/after joins), NULL handling, time zones, test/internal rows excluded.
5. Present the answer first, then the table, then caveats (data freshness, definitions, what was excluded).

**Safety:** do not run INSERT/UPDATE/DELETE/DDL. If the connection is not read-only, say so and stick to SELECT. Do not export personal data beyond what the question needs.
