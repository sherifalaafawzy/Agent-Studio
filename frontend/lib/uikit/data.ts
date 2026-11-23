// Small helpers every component uses to read a spec defensively. A spec is data from a model: any field can be missing or the wrong type, so components read through
// these (never trusting a shape) and always get something drawable or an empty value.

export type Any = any;

export const str = (v: unknown, n = 200): string => (v === null || v === undefined ? "" : String(v).slice(0, n));
export const isObj = (v: unknown): v is Record<string, Any> => !!v && typeof v === "object" && !Array.isArray(v);
export const obj = (v: unknown): Record<string, Any> => (isObj(v) ? v : {});
export const arr = (v: unknown, max = 20000): Any[] => (Array.isArray(v) ? v.slice(0, max) : []);

/** A finite number from a number or numeric text; `d` (NaN by default) otherwise. */
export function num(v: unknown, d = NaN): number {
  if (typeof v === "number") return Number.isFinite(v) ? v : d;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return d;
}

export const finite = (n: number) => Number.isFinite(n);

/** 1234567 -> 1.2M, 12345 -> 12.3k, 0.1234 -> 0.12. */
export function fmtNum(n: number, digits = 2): string {
  if (!Number.isFinite(n)) return "–";
  const a = Math.abs(n);
  if (a >= 1e9) return `${+(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${+(n / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${+(n / 1e3).toFixed(1)}k`;
  return `${+n.toFixed(digits)}`;
}

export function fmtMoney(n: number, currency = "USD"): string {
  if (!Number.isFinite(n)) return "–";
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: Math.abs(n) >= 1000 ? 0 : 2 }).format(n); } catch { return `${currency} ${fmtNum(n)}`; }
}

export const fmtPct = (n: number, digits = 1) => (Number.isFinite(n) ? `${+(n * 100).toFixed(digits)}%` : "–");

export function fmtBytes(n: number): string {
  if (!Number.isFinite(n)) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0, v = Math.abs(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${n < 0 ? "-" : ""}${+v.toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function fmtDuration(seconds: number): string {
  if (!Number.isFinite(seconds)) return "–";
  const s = Math.round(seconds), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${r}s` : `${r}s`;
}

/** "Nice" axis ticks covering [min, max]. */
export function niceScale(min: number, max: number, count = 5): { lo: number; hi: number; ticks: number[] } {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { lo: 0, hi: 1, ticks: [0, 1] };
  if (min === max) { min -= 1; max += 1; }
  const raw = (max - min) / Math.max(1, count), p = 10 ** Math.floor(Math.log10(raw)), f = raw / p;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step, ticks: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
  return { lo, hi, ticks };
}

export type Series = { name: string; values: (number | null)[] };

/** labels + series from either {x, series:[{name, values}]} or {data:[{label, value}]} or {values:[...]}. */
export function seriesOf(spec: Any): { labels: string[]; series: Series[] } {
  if (Array.isArray(spec.data) && spec.data.length) {
    const d = arr(spec.data, 2000);
    return { labels: d.map((r) => str(r?.label ?? r?.name, 60)), series: [{ name: str(spec.name ?? spec.title ?? "Value", 60), values: d.map((r) => { const n = num(r?.value); return finite(n) ? n : null; }) }] };
  }
  if (Array.isArray(spec.series) && spec.series.length) {
    const x = arr(spec.x ?? spec.labels, 2000), series = arr(spec.series, 12).map((s, i) => ({ name: str(s?.name ?? `Series ${i + 1}`, 60), values: arr(s?.values, 2000).map((v) => { const n = num(v); return finite(n) ? n : null; }) }));
    const labels = (x.length ? x : series[0]?.values.map((_, i) => String(i + 1)) ?? []).map((l) => str(l, 60));
    return { labels, series: series.map((s) => ({ ...s, values: labels.map((_, i) => s.values[i] ?? null) })) };
  }
  if (Array.isArray(spec.values)) { const v = arr(spec.values, 5000).map((n) => { const x = num(n); return finite(x) ? x : null; }); return { labels: v.map((_, i) => String(i + 1)), series: [{ name: str(spec.name ?? spec.title ?? "Value", 60), values: v }] }; }
  return { labels: [], series: [] };
}

/** Parse a date or timestamp (ISO text, epoch milliseconds or seconds) to epoch milliseconds, or NaN. */
export function toTime(v: unknown): number {
  if (typeof v === "number") return Number.isFinite(v) ? (v < 1e11 ? v * 1000 : v) : NaN;
  const s = String(v ?? "").trim();
  if (!s) return NaN;
  if (/^\d{9,13}$/.test(s)) return toTime(Number(s));
  return Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(s) ? `${s}T00:00:00Z` : s);
}

export const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/** Categorical colour for series i (the first six are the validated palette, then two more; a 9th is never invented). */
export const seriesColor = (i: number) => `var(--viz-${(i % 8) + 1})`;

/** A sequential colour from the surface to the hue: t in 0..1. */
export const seqColor = (t: number, hue = 1) => `color-mix(in srgb, var(--viz-${hue}) ${Math.round(clamp(t, 0, 1) * 100)}%, var(--raise))`;

/** Sum of an array of maybe-null numbers. */
export const sum = (v: (number | null)[]) => v.reduce<number>((a, b) => a + (b ?? 0), 0);
