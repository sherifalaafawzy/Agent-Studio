---
name: oee-analysis
group: Manufacturing
mcp:
- excel
- duckdb
description: Calculate Overall Equipment Effectiveness (availability x performance x quality) from production data and find the biggest losses. Use for plant performance reviews and downtime analysis.
---

# OEE analysis

**When to use:** machine/line data (planned time, downtime events, ideal cycle time, units produced, units rejected) is available.

1. Define the period and the **planned production time**; confirm what counts as planned stop (breaks, changeovers) - state your rule.
2. Compute: **Availability** = run time / planned time; **Performance** = (ideal cycle time x total units) / run time; **Quality** = good units / total units; **OEE** = A x P x Q. Show the numbers, not just the percentage.
3. Rank the **six big losses** (breakdowns, setup/adjustments, small stops, slow cycles, startup rejects, production rejects) by lost minutes or units. A Pareto table is enough.
4. Point to the top three loss causes with the evidence rows, and suggest where to dig next (which shifts, products, machines). Do not claim a root cause from OEE alone.
5. Note data gaps (missing downtime reasons, unrealistic ideal cycle times) that make the figure unreliable.

**Safety:** read-only analysis. Never send commands to machines or PLCs.
