---
name: eda-report
group: Data
mcp:
- duckdb
- excel
description: 'Do an exploratory data analysis and write a short report: distributions, relationships, segments and the three most useful findings. Use when starting on an unfamiliar dataset.'
---

# Exploratory data analysis report

**When to use:** "what is in this data?" or "find something useful".

1. State the question or decision the data should inform. If there is none, say the report is open-ended.
2. Profile first (see data-quality-profiling) and note what was excluded.
3. Explore: distributions of key numeric columns, category frequencies, time trends (check seasonality), segment comparisons, correlations. Prefer a few well-chosen views to many charts.
4. Test before claiming: a difference between groups needs a sample size and an effect size; correlation is not causation; say how robust a finding is (does it hold in sub-periods?).
5. Report: **three findings** in plain language, each with the numbers, a simple chart or table, and what to do or check next; then **limits** (missing data, bias in how the data was collected).

**Safety:** analysis only; do not publish or share the data externally.
