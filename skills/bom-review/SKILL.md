---
name: bom-review
group: Manufacturing
mcp:
- excel
- duckdb
description: Review a bill of materials for completeness, duplicates, unit-of-measure errors, single-source risk and cost roll-up. Use before releasing a BOM or quoting a product.
---

# Bill of materials review

**When to use:** a BOM (CSV/XLSX, possibly multi-level) must be checked before release or costing.

1. Load it read-only and rebuild the hierarchy. Check each line has part number, description, quantity, unit of measure, revision and a supplier/cost where relevant.
2. Find problems: duplicate part numbers with different descriptions, missing or zero quantities, mixed units (mm vs m, each vs kg), orphan sub-assemblies, circular references, obsolete revisions, parts with no supplier.
3. Roll up cost and weight per level using the data given; show lines where the cost is missing instead of treating them as zero.
4. Risk view: single-source parts, long-lead items (if lead times are present), parts with one price break only.
5. Output: issue table (line, issue, severity), the cost roll-up with confidence ("3 lines have no cost"), and a short release recommendation (ready / ready with fixes / not ready).

**Safety:** do not edit the BOM or push changes to ERP/PLM.
