"use client";
// The `table` component: typed columns with formats (currency, percent, badge, link, progress...), data bars and colour scales, grouping with subtotals, a pinned first column.
import { parseTable } from "@/lib/tables";
import type { Spec } from "@/lib/uikit/safe";
import DataTable from "../rich/DataTable";

export default function TableBlock({ spec }: { spec: Spec }) {
  const { table, error } = parseTable(spec, String(spec.title ?? ""));
  if (!table) throw new Error(error);
  const g = table.columns.findIndex((c) => c.key === spec.group_by || c.label === spec.group_by);
  return <DataTable table={table} opts={{ groupBy: g >= 0 ? g : undefined, pinFirst: !!spec.pin_first, density: spec.density === "compact" ? "compact" : undefined, select: !!spec.select }} />;
}
