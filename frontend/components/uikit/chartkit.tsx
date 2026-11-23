"use client";
// Shared parts of the hand-drawn SVG charts: the shell (frame, legend, table view, picture export), scales, axes, the hover crosshair with its tooltip, and helpers.
// Every chart is a pure function from a spec to {svg, table, summary, legend}; the shell does the rest, so a chart is also readable as a table and as a sentence.
import { CSSProperties, ReactNode, useEffect, useRef, useState } from "react";
import { parseTable, type Table } from "@/lib/tables";
import { fmtNum, seriesColor } from "@/lib/uikit/data";
import type { Spec } from "@/lib/uikit/safe";
import UiFrame from "./UiFrame";

export const W = 560;
export const M = { l: 48, r: 14, t: 12, b: 30 };
export type Legend = { name: string; color: string };
export type Built = { h: number; w?: number; cls?: string; svg: ReactNode; table: Table | null; summary: string; legend?: Legend[]; foot?: ReactNode; bare?: boolean };

/** Group a long category list into at most `max` coloured groups; the rest share one neutral "Other" colour (a 9th hue is never invented). */
export function groupColors(names: string[], max = 8): { color: (name: string) => string; legend: Legend[] } {
  const order = [...new Set(names)];
  const keep = order.slice(0, max), other = order.length > max;
  const legend: Legend[] = keep.map((n, i) => ({ name: n, color: seriesColor(i) }));
  if (other) legend.push({ name: "Other", color: "var(--viz-other)" });
  return { color: (n) => legend.find((l) => l.name === n)?.color ?? "var(--viz-other)", legend };
}

export const linear = (d0: number, d1: number, r0: number, r1: number) => (v: number) => (d1 === d0 ? (r0 + r1) / 2 : r0 + ((v - d0) / (d1 - d0)) * (r1 - r0));
export const short = (s: string, n = 12) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

/** Horizontal grid lines and y tick labels for a value scale. */
export function YAxis({ ticks, y, x0 = M.l, x1 = W - M.r, fmt = fmtNum }: { ticks: number[]; y: (v: number) => number; x0?: number; x1?: number; fmt?: (n: number) => string }) {
  return <g>{ticks.map((t, i) => <g key={i}><line className="viz-grid" x1={x0} x2={x1} y1={y(t)} y2={y(t)} /><text className="chart-tick" x={x0 - 6} y={y(t) + 3} textAnchor="end">{fmt(t)}</text></g>)}</g>;
}

/** Vertical grid lines and x tick labels (for horizontal bar charts and scatter plots). */
export function XAxis({ ticks, x, y0 = M.t, y1, fmt = fmtNum }: { ticks: number[]; x: (v: number) => number; y0?: number; y1: number; fmt?: (n: number) => string }) {
  return <g>{ticks.map((t, i) => <g key={i}><line className="viz-grid" x1={x(t)} x2={x(t)} y1={y0} y2={y1} /><text className="chart-tick" x={x(t)} y={y1 + 14} textAnchor="middle">{fmt(t)}</text></g>)}</g>;
}

/** The legend under a chart: shown for two or more series, so identity never rests on colour alone. */
export function LegendRow({ items }: { items: Legend[] }) {
  return items.length > 1 ? <span className="chart-legend">{items.map((l) => <span key={l.name}><i className="chart-swatch" style={{ background: l.color }} />{l.name}</span>)}</span> : null;
}

type Tip = { y: number; lines: string[] };

/** Invisible hover/focus columns over a chart: one per category. The active one draws a crosshair and a small tooltip. Skipped for very large charts. */
export function HoverLayer({ count, xAt, band, top, bottom, tip }: { count: number; xAt: (i: number) => number; band: number; top: number; bottom: number; tip: (i: number) => Tip }) {
  const [at, setAt] = useState<number | null>(null);
  if (count > 400) return null;
  const t = at === null ? null : tip(at);
  const w = t ? Math.max(...t.lines.map((l) => l.length)) * 6 + 16 : 0, h = t ? t.lines.length * 14 + 8 : 0;
  const x = at === null ? 0 : Math.min(W - w - 2, Math.max(2, xAt(at) + 10));
  const y = t ? Math.max(2, Math.min(bottom - h, t.y - h / 2)) : 0;
  return (
    <g>
      {Array.from({ length: count }, (_, i) => <rect key={i} className="hover-col" x={xAt(i) - band / 2} y={top} width={band} height={bottom - top} fill="transparent" tabIndex={0}
        aria-label={tip(i).lines.join(", ")} onMouseEnter={() => setAt(i)} onMouseMove={() => setAt(i)} onMouseLeave={() => setAt(null)} onFocus={() => setAt(i)} onBlur={() => setAt(null)} />)}
      {t && at !== null && <g pointerEvents="none"><line className="viz-axis" x1={xAt(at)} x2={xAt(at)} y1={top} y2={bottom} strokeDasharray="3 3" />
        <g transform={`translate(${x},${y})`}><rect className="chart-tip" width={w} height={h} rx={6} />{t.lines.map((l, i) => <text key={i} className={i ? "chart-tip-t" : "chart-tip-t b"} x={8} y={14 + i * 14}>{l}</text>)}</g></g>}
    </g>
  );
}

/** The frame for one chart: builds it, then draws it with the legend, table view and exports. A chart that cannot be built throws, and the boundary shows its data as a table. */
export function ChartShell({ spec, build }: { spec: Spec; build: (spec: Spec) => Built }) {
  const ref = useRef<SVGSVGElement>(null);
  const [k, setK] = useState(1);                              // text stays readable when the chart is squeezed (a phone): its labels grow as the picture shrinks
  const vw = useRef(W);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => { const w = el.clientWidth; if (w) setK(Math.min(1.5, Math.max(1, vw.current / w))); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const b = build(spec);
  vw.current = b.w ?? W;
  return (
    <UiFrame spec={spec} summary={b.summary} table={b.table} svgRef={ref} bare={b.bare} foot={<>{b.legend && <LegendRow items={b.legend} />}{b.foot}</>} testId="chart">
      <svg ref={ref} viewBox={`0 0 ${b.w ?? W} ${b.h}`} role="img" aria-label={b.summary} className={`uichart${b.cls ? " " + b.cls : ""}`} style={{ "--k": k } as CSSProperties}>{b.svg}</svg>
    </UiFrame>
  );
}

export const chartComponent = (build: (spec: Spec) => Built) => function Chart({ spec }: { spec: Spec }) { return <ChartShell spec={spec} build={build} />; };

/** A table for a chart from a header row and data rows. */
export function mkTable(title: string, head: string[], rows: unknown[][]): Table {
  const t = parseTable({ columns: head.map((h, i) => ({ key: `c${i}`, label: h })), rows }, title).table;
  return t!;     // columns and rows are always lists here
}

export const unitText = (spec: Spec) => (spec.unit ? ` ${spec.unit}` : "");
export const need = (cond: unknown, msg: string): void => { if (!cond) throw new Error(msg); };
