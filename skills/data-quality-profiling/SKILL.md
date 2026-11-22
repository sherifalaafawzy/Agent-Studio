---
name: data-quality-profiling
group: Data
mcp:
- duckdb
- excel
description: Profile a dataset for quality problems (missing values, duplicates, outliers, inconsistent formats, referential gaps) and report fixes. Use before analysis, modelling or a data migration.
---

# Data quality profiling

**When to use:** a new or suspicious dataset (CSV, Excel, table) is about to be used.

1. Load read-only. Report shape, column types, key candidates, memory/size.
2. Per column: % missing, distinct count, min/max/quantiles, top values, format patterns (dates, phone, ids), obvious type mismatches (numbers stored as text).
3. Across the table: duplicate rows and duplicate keys, impossible values (negative ages, future dates), outliers (IQR or domain limits), inconsistent categories ("USA"/"US"/"United States").
4. Across tables (if several): orphan foreign keys, row-count mismatches after joins.
5. Report as a prioritised list: issue, affected rows (count and %), example rows, suggested fix, and whether the fix is safe to automate. Do not silently clean - show the proposed rules.

**Safety:** never modify the source. Write cleaned output to a new file only when asked.
