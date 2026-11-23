// How a table cell is shown for a column format. Display only: exports always contain the raw values.
import type { Cell, Col } from "@/lib/tables";
import { cellText } from "@/lib/tables";
import { fmtBytes, fmtDuration, fmtMoney, fmtNum, fmtPct, num } from "./data";
import { safeUrl } from "./safe";

/** The text shown for a cell. */
export function showCell(col: Col, v: Cell): string {
  if (v === null || v === "") return "";
  const n = num(v);
  const d = col.decimals;
  const fixed = (x: number) => (d === undefined ? fmtNum(x) : x.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
  switch (col.format) {
    case "currency": return Number.isFinite(n) ? fmtMoney(n, col.currency || "USD") : cellText(v);
    case "percent": return Number.isFinite(n) ? fmtPct(n, d ?? 1) : cellText(v);
    case "number": return Number.isFinite(n) ? fixed(n) + (col.unit ? ` ${col.unit}` : "") : cellText(v);
    case "bytes": return Number.isFinite(n) ? fmtBytes(n) : cellText(v);
    case "duration": return Number.isFinite(n) ? fmtDuration(n) : cellText(v);
    case "date": { const t = Date.parse(String(v)); return Number.isFinite(t) ? new Date(t).toLocaleDateString() : cellText(v); }
    case "datetime": { const t = Date.parse(String(v)); return Number.isFinite(t) ? new Date(t).toLocaleString() : cellText(v); }
    default: return col.unit && Number.isFinite(n) ? `${cellText(v)} ${col.unit}` : cellText(v);
  }
}

const GOOD = /^(ok|good|done|success|succeeded|passed|pass|on track|healthy|active|ready|yes|true|approved|complete|completed|up)$/i;
const BAD = /^(error|bad|failed|fail|failure|critical|blocked|at risk|down|no|false|rejected|overdue|unhealthy)$/i;
const WARN = /^(warn|warning|pending|waiting|slow|degraded|medium|review|late|partial)$/i;

/** A status badge's tone from its words (the text is always shown too, so colour is never the only signal). */
export function badgeTone(v: string): "ok" | "bad" | "warn" | "" {
  const t = v.trim();
  return GOOD.test(t) ? "ok" : BAD.test(t) ? "bad" : WARN.test(t) ? "warn" : "";
}

export const badgeIcon = (tone: string) => (tone === "ok" ? "✓" : tone === "bad" ? "✕" : tone === "warn" ? "!" : "•");

/** A link cell: the safe address (or null) and the text to show. */
export function linkCell(v: Cell): { href: string | null; text: string } {
  const s = cellText(v);
  return { href: safeUrl(s), text: s };
}

/** 0..1 for a progress cell: values up to 1 are fractions, larger ones percentages. */
export const progressOf = (v: Cell): number => { const n = num(v); return Number.isFinite(n) ? Math.min(1, Math.max(0, n <= 1 ? n : n / 100)) : 0; };

/** Per-column numeric range for data bars and colour scales. */
export function columnRange(col: Col, values: Cell[]): { lo: number; hi: number } {
  const ns = values.map((v) => num(v)).filter(Number.isFinite);
  const lo = col.min ?? (ns.length ? Math.min(0, ...ns) : 0), hi = col.max ?? (ns.length ? Math.max(...ns, lo + 1e-9) : 1);
  return { lo, hi: hi === lo ? lo + 1 : hi };
}
