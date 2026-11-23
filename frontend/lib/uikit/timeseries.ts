// Time series: reading a spec, resampling, moving averages, indexing, statistics and the ECharts option. Pure functions, so everything but the drawing is testable.
import type { Cell, Table } from "@/lib/tables";
import { parseTable } from "@/lib/tables";
import { arr, Any, finite, fmtNum, isObj, num, str, toTime } from "./data";

export type Pt = [number, number];                      // [epoch ms, value]
export type TSeries = { name: string; unit: string; kind: "line" | "area" | "step" | "bar"; points: Pt[] };
export type Forecast = { name: string; times: number[]; median: number[]; lo: number[]; hi: number[]; loKey: string; hiKey: string };
export type Model = {
  title: string; series: TSeries[]; annotations: { t: number; end: number; label: string }[]; anomalies: { series: string; t: number; v: number; label: string }[];
  thresholds: { value: number; label: string }[]; forecast: Forecast | null; total: number;
};

export const DAY = 86400000;

/** The time series in a spec. Points are [time, value] pairs or parallel `t` and `v` lists; bad points are dropped and the points are put in time order. */
export function readModel(spec: Any): Model {
  const series: TSeries[] = arr(spec.series, 12).map((s, i) => {
    let pts: Pt[] = [];
    if (Array.isArray(s?.points)) pts = s.points.map((p: Any) => (Array.isArray(p) ? [toTime(p[0]), num(p[1])] : isObj(p) ? [toTime(p.t ?? p.time ?? p.date), num(p.v ?? p.value)] : [NaN, NaN])) as Pt[];
    else if (Array.isArray(s?.t) && Array.isArray(s?.v)) pts = s.t.map((t: Any, k: number) => [toTime(t), num(s.v[k])]) as Pt[];
    pts = pts.filter((p) => finite(p[0]) && finite(p[1])).sort((a, b) => a[0] - b[0]);
    const kind = ["line", "area", "step", "bar"].includes(s?.kind) ? s.kind : "line";
    return { name: str(s?.name ?? `Series ${i + 1}`, 40), unit: str(s?.unit, 12), kind, points: pts };
  }).filter((s) => s.points.length);
  if (!series.length) throw new Error("a time series needs points with a time and a value");
  const annotations = arr(spec.annotations, 50).map((a) => ({ t: toTime(a?.t), end: toTime(a?.end), label: str(a?.label, 40) })).filter((a) => finite(a.t));
  const anomalies = arr(spec.anomalies, 200).map((a) => ({ series: str(a?.series, 40), t: toTime(a?.t), v: num(a?.v), label: str(a?.label, 40) })).filter((a) => finite(a.t));
  const thresholds = arr(spec.thresholds, 10).map((x) => ({ value: num(x?.value), label: str(x?.label, 40) })).filter((x) => finite(x.value));
  let forecast: Forecast | null = null;
  const f = spec.forecast;
  if (isObj(f) && Array.isArray(f.median) && f.median.length) {
    const host = series.find((s) => s.name === f.series) ?? series[0], last = host.points[host.points.length - 1][0];
    const step = host.points.length > 1 ? last - host.points[host.points.length - 2][0] : DAY;
    const times = (Array.isArray(f.timestamps) && f.timestamps.length ? f.timestamps.map(toTime) : f.median.map((_: unknown, i: number) => last + (i + 1) * step)).slice(0, f.median.length);
    const keys = Object.keys(isObj(f.quantiles) ? f.quantiles : {}).filter((k) => Array.isArray(f.quantiles[k])).sort((a, b) => Number(a) - Number(b));
    const lo = keys.length > 1 ? arr(f.quantiles[keys[0]]).map((v) => num(v)) : [], hi = keys.length > 1 ? arr(f.quantiles[keys[keys.length - 1]]).map((v) => num(v)) : [];
    const median = f.median.map((v: Any) => num(v)).slice(0, times.length);
    if (times.every(finite) && median.every(finite)) forecast = { name: host.name, times, median, lo: lo.length === median.length ? lo : [], hi: hi.length === median.length ? hi : [], loKey: keys[0] ?? "", hiKey: keys[keys.length - 1] ?? "" };
  }
  return { title: str(spec.title, 80), series, annotations, anomalies, thresholds, forecast, total: series.reduce((a, s) => a + s.points.length, 0) };
}

export type Resample = "none" | "hour" | "day" | "week" | "month";
export type AggFn = "mean" | "sum" | "last" | "min" | "max" | "count";

function bucketStart(t: number, by: Resample): number {
  const d = new Date(t);
  if (by === "hour") return Math.floor(t / 3600000) * 3600000;
  if (by === "day") return Math.floor(t / DAY) * DAY;
  if (by === "week") { const day = Math.floor(t / DAY) * DAY; return day - ((new Date(day).getUTCDay() + 6) % 7) * DAY; }      // weeks start on Monday
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
}

/** Group points into hour / day / week / month buckets (UTC) and combine each bucket with `fn`. */
export function resample(points: Pt[], by: Resample, fn: AggFn): Pt[] {
  if (by === "none") return points;
  const groups = new Map<number, number[]>();
  for (const [t, v] of points) { const k = bucketStart(t, by); (groups.get(k) ?? groups.set(k, []).get(k)!).push(v); }
  const agg = (v: number[]) => (fn === "sum" ? v.reduce((a, b) => a + b, 0) : fn === "last" ? v[v.length - 1] : fn === "min" ? Math.min(...v) : fn === "max" ? Math.max(...v) : fn === "count" ? v.length : v.reduce((a, b) => a + b, 0) / v.length);
  return [...groups.entries()].sort((a, b) => a[0] - b[0]).map(([t, v]) => [t, agg(v)] as Pt);
}

/** A simple moving average over `window` points (shorter at the start). */
export function movingAverage(points: Pt[], window: number): Pt[] {
  return points.map(([t], i) => { const from = Math.max(0, i - window + 1), seg = points.slice(from, i + 1); return [t, seg.reduce((a, p) => a + p[1], 0) / seg.length] as Pt; });
}

/** Every series divided by its first value, times 100: lets series of different size share one axis. */
export const indexTo100 = (points: Pt[]): Pt[] => { const base = points[0]?.[1]; return base ? points.map(([t, v]) => [t, (v / base) * 100] as Pt) : points; };

export type Stats = { name: string; n: number; min: number; max: number; mean: number; last: number; change: number | null };
export function stats(s: TSeries): Stats {
  const v = s.points.map((p) => p[1]), first = v[0], last = v[v.length - 1];
  return { name: s.name, n: v.length, min: Math.min(...v), max: Math.max(...v), mean: v.reduce((a, b) => a + b, 0) / v.length, last, change: first ? (last - first) / Math.abs(first) : null };
}

export const statsTable = (title: string, ss: TSeries[]): Table => parseTable({ columns: [{ key: "n", label: "Series" }, { key: "p", label: "Points", type: "number" }, { key: "mi", label: "Min", type: "number" }, { key: "ma", label: "Max", type: "number" }, { key: "me", label: "Mean", type: "number" }, { key: "l", label: "Last", type: "number" }, { key: "c", label: "Change", type: "number", format: "percent" }],
  rows: ss.map(stats).map((s) => [s.name, s.n, s.min, s.max, +s.mean.toFixed(4), s.last, s.change === null ? null : +s.change.toFixed(4)]) }, title).table!;

/** All series aligned on their timestamps (one row per time), for the CSV export. */
export function alignedTable(title: string, ss: TSeries[]): Table {
  const times = [...new Set(ss.flatMap((s) => s.points.map((p) => p[0])))].sort((a, b) => a - b).slice(0, 20000), maps = ss.map((s) => new Map(s.points));
  return parseTable({ columns: [{ key: "t", label: "Time" }, ...ss.map((s, i) => ({ key: `s${i}`, label: s.name }))], rows: times.map((t) => [new Date(t).toISOString(), ...maps.map((m) => (m.has(t) ? m.get(t)! : null))] as Cell[]) }, title).table!;
}

/** The sentence a screen reader gets in place of the picture. */
export function summarize(m: Model): string {
  const first = Math.min(...m.series.map((s) => s.points[0][0])), last = Math.max(...m.series.map((s) => s.points[s.points.length - 1][0])), d = (t: number) => new Date(t).toISOString().slice(0, 10);
  return `${m.title || "Time series"}: ${m.series.map((s) => { const st = stats(s); return `${s.name} from ${fmtNum(s.points[0][1])} to ${fmtNum(st.last)}`; }).join("; ")}, ${d(first)} to ${d(last)}${m.forecast ? `, with a forecast of ${m.forecast.median.length} steps` : ""}${m.anomalies.length ? `, ${m.anomalies.length} unusual point${m.anomalies.length === 1 ? "" : "s"} marked` : ""}`;
}

export type Colors = { series: string[]; fg: string; grid: string; axis: string; panel: string; other: string };
export type View = { resample: Resample; fn: AggFn; ma: boolean; index: boolean; log: boolean };
export const DEFAULT_VIEW: View = { resample: "none", fn: "mean", ma: false, index: false, log: false };

/** The series as shown under the chosen view (resampled, averaged, indexed). */
export function viewSeries(m: Model, v: View): TSeries[] {
  return m.series.map((s) => { let p = resample(s.points, v.resample, v.fn); if (v.ma) p = movingAverage(p, 7); if (v.index) p = indexTo100(p); return { ...s, points: p }; });
}

/** The ECharts option for a model under a view. `lttb` sampling keeps a 100,000-point series fast. */
export function buildOption(m: Model, v: View, c: Colors, utc: boolean): Any {
  const ss = viewSeries(m, v), valid = ss.flatMap((s) => s.points.map((p) => p[1]));
  const logOk = v.log && valid.every((x) => x > 0), out: Any[] = [];
  ss.forEach((s, i) => {
    const color = c.series[i % 8] ?? c.other, big = s.points.length > 2000, step = s.kind === "step";
    const mark: Any = {};
    if (i === 0) {
      const lines = [...m.annotations.filter((a) => !finite(a.end)).map((a) => ({ xAxis: a.t, name: a.label, label: { formatter: a.label, color: c.fg, position: "insideEndTop" } })), ...m.thresholds.map((t) => ({ yAxis: v.index ? undefined : t.value, name: t.label, label: { formatter: t.label, color: c.fg, position: "insideStartTop" } })).filter((t) => t.yAxis !== undefined)];
      if (lines.length) mark.markLine = { symbol: "none", silent: true, lineStyle: { color: c.axis, type: "dashed" }, data: lines };
      const bands = m.annotations.filter((a) => finite(a.end)).map((a) => [{ xAxis: a.t, name: a.label }, { xAxis: a.end }]);
      if (bands.length) mark.markArea = { silent: true, itemStyle: { color: c.grid, opacity: 0.5 }, label: { color: c.fg }, data: bands };
    }
    out.push({ name: s.name, type: s.kind === "bar" ? "bar" : "line", data: s.points, showSymbol: !big && s.points.length < 80, step: step ? "end" : undefined, areaStyle: s.kind === "area" ? { opacity: 0.18 } : undefined,
      sampling: big ? "lttb" : undefined, large: big, lineStyle: { width: 2, color }, itemStyle: { color }, emphasis: { focus: "series" }, ...mark });
  });
  const f = m.forecast;
  if (f && !v.index && v.resample === "none") {
    const host = ss.find((s) => s.name === f.name) ?? ss[0], hi = ss.indexOf(host), color = c.series[hi % 8] ?? c.other, anchor = host.points[host.points.length - 1];
    out.push({ name: `${host.name} (forecast)`, type: "line", data: [anchor, ...f.times.map((t, k) => [t, f.median[k]])], showSymbol: false, lineStyle: { width: 2, type: "dashed", color }, itemStyle: { color } });
    if (f.lo.length && f.hi.length) {
      out.push({ name: `band lower`, type: "line", data: f.times.map((t, k) => [t, f.lo[k]]), stack: "band", showSymbol: false, lineStyle: { opacity: 0 }, tooltip: { show: false }, silent: true, emphasis: { disabled: true } });
      out.push({ name: `${host.name} (${f.loKey}–${f.hiKey} range)`, type: "line", data: f.times.map((t, k) => [t, f.hi[k] - f.lo[k]]), stack: "band", showSymbol: false, lineStyle: { opacity: 0 }, areaStyle: { color, opacity: 0.16 }, silent: true, emphasis: { disabled: true } });
    }
  }
  if (m.anomalies.length) {
    out.push({ name: "Unusual points", type: "scatter", symbolSize: 13, itemStyle: { color: c.panel, borderColor: c.fg, borderWidth: 2 }, label: { show: true, formatter: (p: Any) => p.data[2] ?? "", position: "top", color: c.fg },
      data: m.anomalies.map((a) => { const host = ss.find((s) => s.name === a.series) ?? ss[0], near = host.points.reduce((b, p) => (Math.abs(p[0] - a.t) < Math.abs(b[0] - a.t) ? p : b), host.points[0]); return [a.t, finite(a.v) && !v.index ? a.v : near[1], a.label]; }) });
  }
  const names = out.filter((o) => !["band lower"].includes(o.name)).map((o) => o.name);
  return {
    animation: m.total < 5000, color: c.series, textStyle: { color: c.fg },
    legend: { data: names, top: 0, textStyle: { color: c.fg }, show: names.length > 1 }, grid: { left: 8, right: 16, top: 40, bottom: 52, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "cross" }, valueFormatter: (x: number) => (typeof x === "number" ? fmtNum(x) : String(x)) },
    xAxis: { type: "time", axisLine: { lineStyle: { color: c.axis } }, axisLabel: { color: c.fg }, splitLine: { show: false } },
    yAxis: { type: logOk ? "log" : "value", scale: !logOk, axisLabel: { color: c.fg, formatter: (x: number) => fmtNum(x) }, splitLine: { lineStyle: { color: c.grid } } },
    dataZoom: [{ type: "inside", filterMode: "none" }, { type: "slider", height: 20, bottom: 6, filterMode: "none", textStyle: { color: c.fg } }],
    series: out, useUTC: utc,
  };
}

/** Start and end (ms) for a range button that ends at the last point. */
export function rangeFor(m: Model, key: string): [number, number] | null {
  const end = Math.max(...m.series.map((s) => s.points[s.points.length - 1][0])), start = Math.min(...m.series.map((s) => s.points[0][0])), days = ({ "1W": 7, "1M": 30, "3M": 91, "6M": 182, "1Y": 365 } as Record<string, number>)[key];
  return days ? [Math.max(start, end - days * DAY), end] : null;
}

/** Statistics, indexed and log views need positive, non-empty data: which switches make sense. */
export const canLog = (m: Model) => m.series.every((s) => s.points.every((p) => p[1] > 0));
