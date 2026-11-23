// When a component cannot be drawn, show its data as a plain table if it has any (the same idea as the server's fallback_table).
import { parseTable, type Table } from "@/lib/tables";
import { arr, isObj, str } from "./data";

export function tableFromAnything(raw: unknown): Table | null {
  const r: any = typeof raw === "string" ? (() => { try { return JSON.parse(raw); } catch { return null; } })() : raw;
  if (!isObj(r)) return null;
  const title = str(r.title, 100);
  const build = (x: unknown) => { const t = parseTable(x, title).table; return t && t.rows.length ? t : null; };
  if (Array.isArray(r.rows) && r.rows.length) {
    const first = r.rows[0];
    const columns = Array.isArray(r.columns) && r.columns.length ? r.columns : Array.isArray(first) ? first.map((_: unknown, i: number) => `Column ${i + 1}`) : undefined;
    return build({ columns, rows: r.rows });
  }
  if (Array.isArray(r.data) && r.data.length && r.data.every(isObj)) return build({ rows: r.data });
  if (Array.isArray(r.x) && Array.isArray(r.series) && r.series.length && r.series.every((s: any) => isObj(s) && Array.isArray(s.values))) {
    const names = r.series.map((s: any, i: number) => str(s.name || `Series ${i + 1}`, 60));
    return build({ columns: ["", ...names], rows: arr(r.x).map((x, i) => [x, ...r.series.map((s: any) => s.values[i] ?? null)]) });
  }
  return null;
}
