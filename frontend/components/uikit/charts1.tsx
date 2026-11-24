"use client";
// Cartesian charts drawn as plain SVG: bar, line, area, lollipop, dumbbell, slope, sparkline, waterfall, scatter, bubble, histogram, box, candlestick, bullet, heatmap, calendar, gantt.
// Each is a pure function from a spec to a picture, a table and a one-sentence summary (see chartkit). Colours come from the validated palette; values are labelled sparingly.
import { Any, arr, finite, fmtNum, isObj, niceScale, num, seqColor, seriesColor, seriesOf, str, toTime } from "@/lib/uikit/data";
import type { Spec } from "@/lib/uikit/safe";
import { Built, HoverLayer, M, W, XAxis, YAxis, chartComponent, groupColors, linear, mkTable, need, short, unitText } from "./chartkit";

const H = 300;
const ttl = (s: Spec, d: string) => str(s.title, 80) || d;
const pw = W - M.l - M.r;
const labelStep = (n: number, max = 8) => Math.ceil(n / max);
const maxLen = (l: string[]) => l.reduce((a, b) => Math.max(a, b.length), 0);

// ---------------------------------------------------------------------------------------------------- bar
function bar(spec: Spec): Built {
  const { labels, series } = seriesOf(spec);
  need(labels.length && series.length, "a bar chart needs at least one value");
  const mode = spec.stack === "stacked" || spec.stack === "percent" ? (spec.stack as string) : "none", horiz = spec.orientation === "horizontal", stacked = mode !== "none";
  const n = labels.length, k = series.length, unit = unitText(spec);
  const tot = labels.map((_, i) => series.reduce((a, s) => a + Math.abs(s.values[i] ?? 0), 0));
  const val = (si: number, i: number) => { const v = series[si].values[i] ?? 0; return mode === "percent" ? (tot[i] ? (Math.abs(v) / tot[i]) * 100 : 0) : v; };
  let lo = 0, hi = 0;
  if (mode === "percent") hi = 100;
  else if (stacked) { labels.forEach((_, i) => { hi = Math.max(hi, series.reduce((a, _s, si) => a + Math.max(0, val(si, i)), 0)); lo = Math.min(lo, series.reduce((a, _s, si) => a + Math.min(0, val(si, i)), 0)); }); }
  else { for (const s of series) for (const v of s.values) if (v !== null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }
  const sc = niceScale(lo, hi);
  const L = horiz ? Math.min(130, 10 + maxLen(labels) * 6.2) : M.l, h = horiz ? Math.min(700, Math.max(170, n * (stacked ? 26 : k * 13 + 12) + M.t + M.b)) : H;
  const ph = h - M.t - M.b, pw2 = W - L - M.r;
  const valPos = horiz ? linear(sc.lo, sc.hi, L, W - M.r) : linear(sc.lo, sc.hi, h - M.b, M.t);
  const group = (horiz ? ph : pw2) / n, bw = stacked ? Math.min(40, group * 0.64) : Math.max(2, Math.min(34, (group * 0.8) / k - 2));
  const band = (i: number) => (horiz ? M.t : L) + group * (i + 0.5);
  const pos = labels.map(() => 0), neg = labels.map(() => 0), marks: Any[] = [];
  labels.forEach((l, i) => series.forEach((s, si) => {
    const v = val(si, i), raw = s.values[i];
    if (raw === null) return;
    let a: number, b: number;
    if (stacked) { if (v >= 0) { a = pos[i]; b = pos[i] += v; } else { a = neg[i]; b = neg[i] += v; } } else { a = 0; b = v; }
    const off = stacked ? -bw / 2 : -(k * (bw + 2)) / 2 + si * (bw + 2) + 1;
    const p0 = valPos(a), p1 = valPos(b), lo2 = Math.min(p0, p1), len = Math.max(1, Math.abs(p1 - p0));
    const r = horiz ? { x: lo2, y: band(i) + off, width: len, height: bw } : { x: band(i) + off, y: lo2, width: bw, height: len };
    marks.push(<rect key={`${si}-${i}`} {...r} rx={2} stroke={stacked ? "var(--panel)" : "none"} strokeWidth={stacked ? 1.5 : 0} style={{ fill: seriesColor(si) }}><title>{`${l} · ${s.name}: ${mode === "percent" ? fmtNum(v) + "%" : fmtNum(raw) + unit}`}</title></rect>);
    if (!stacked && n * k <= 12) marks.push(horiz ? <text key={`t${si}-${i}`} className="chart-tick" x={p1 + (v >= 0 ? 4 : -4)} y={r.y + bw / 2 + 3} textAnchor={v >= 0 ? "start" : "end"}>{fmtNum(raw)}</text>
      : <text key={`t${si}-${i}`} className="chart-tick" x={r.x + bw / 2} y={v >= 0 ? r.y - 3 : r.y + r.height + 10} textAnchor="middle">{fmtNum(raw)}</text>);
  }));
  const step = labelStep(n, horiz ? 40 : 8), i0 = series.map((s) => s.values.map((v, i) => [v, i] as const).filter(([v]) => v !== null)).flat().sort((a, b) => (b[0] as number) - (a[0] as number))[0];
  const tipFor = (i: number) => ({ y: M.t + 30, lines: [labels[i], ...series.map((s) => `${s.name}: ${s.values[i] === null ? "–" : fmtNum(s.values[i] as number) + unit}`)] });
  return {
    h, table: mkTable(ttl(spec, "Bar chart"), ["", ...series.map((s) => s.name)], labels.map((l, i) => [l, ...series.map((s) => s.values[i])])),
    summary: `${ttl(spec, "Bar chart")}: ${series.map((s) => s.name).join(", ")} across ${n} categories${i0 ? `; highest ${labels[i0[1]]} at ${fmtNum(i0[0] as number)}${unit}` : ""}`,
    legend: k > 1 ? series.map((s, i) => ({ name: s.name, color: seriesColor(i) })) : undefined,
    svg: (<>
      {horiz ? <XAxis ticks={sc.ticks} x={valPos} y0={M.t} y1={h - M.b} /> : <YAxis ticks={sc.ticks} y={valPos} x0={L} />}
      {horiz ? <line className="viz-axis" x1={valPos(0)} x2={valPos(0)} y1={M.t} y2={h - M.b} /> : <line className="viz-axis" x1={L} x2={W - M.r} y1={valPos(0)} y2={valPos(0)} />}
      {labels.map((l, i) => i % step === 0 && (horiz ? <text key={i} className="chart-tick" x={L - 6} y={band(i) + 3} textAnchor="end">{short(l, 20)}</text> : <text key={i} className="chart-tick" x={band(i)} y={h - 10} textAnchor="middle">{short(l, 10)}</text>))}
      {marks}
      {!horiz && <HoverLayer count={n} xAt={band} band={group} top={M.t} bottom={h - M.b} tip={tipFor} />}
    </>),
  };
}

// ---------------------------------------------------------------------------------------------------- line and area
function trend(spec: Spec, area: boolean): Built {
  const { labels, series } = seriesOf(spec);
  need(labels.length && series.length, "a line chart needs at least one value");
  const n = labels.length, k = series.length, unit = unitText(spec), mode = area && (spec.stack === "stacked" || spec.stack === "percent") ? (spec.stack as string) : "none", stepped = !!spec.step;
  const tot = labels.map((_, i) => series.reduce((a, s) => a + Math.abs(s.values[i] ?? 0), 0));
  const v = (si: number, i: number): number | null => { const x = series[si].values[i]; return x === null ? null : mode === "percent" ? (tot[i] ? (Math.abs(x) / tot[i]) * 100 : 0) : x; };
  const cum: number[][] = series.map(() => labels.map(() => 0));
  if (mode !== "none") series.forEach((_, si) => labels.forEach((__, i) => { cum[si][i] = (si ? cum[si - 1][i] : 0) + (v(si, i) ?? 0); }));
  let lo = area ? 0 : Infinity, hi = mode === "percent" ? 100 : -Infinity;
  if (mode !== "none") hi = Math.max(hi, ...cum[k - 1]);
  else for (let si = 0; si < k; si++) for (let i = 0; i < n; i++) { const x = v(si, i); if (x !== null) { lo = Math.min(lo, x); hi = Math.max(hi, x); } }
  if (!finite(lo)) lo = 0;
  if (!finite(hi)) hi = 1;
  const sc = niceScale(lo, hi), R = k > 1 && k <= 4 && mode === "none" ? 84 : M.r, p = W - M.l - R;
  const y = linear(sc.lo, sc.hi, H - M.b, M.t), x = (i: number) => (n === 1 ? M.l + p / 2 : M.l + (p * i) / (n - 1));
  type Pt = [number, number, number];
  const segs = (si: number): Pt[][] => { const out: Pt[][] = [[]]; for (let i = 0; i < n; i++) { const t = mode !== "none" ? cum[si][i] : v(si, i); if (t === null) { if (out[out.length - 1].length) out.push([]); } else out[out.length - 1].push([x(i), y(t), i]); } return out.filter((q) => q.length); };
  const line = (pts: Pt[]) => pts.map(([px, py], j) => (j === 0 ? `M${px},${py}` : stepped ? `L${px},${pts[j - 1][1]}L${px},${py}` : `L${px},${py}`)).join("");
  const paths = series.map((s, si) => {
    const c = seriesColor(si), sg = segs(si);
    return (
      <g key={si}>
        {area && sg.map((pts, j) => {
          const flatY = y(Math.max(sc.lo, Math.min(0, sc.hi)));
          const base = (mode !== "none" && si ? [...pts].reverse().map(([px, , i]) => [px, y(cum[si - 1][i])]) : [[pts[pts.length - 1][0], flatY], [pts[0][0], flatY]]) as [number, number][];
          return <path key={`a${j}`} d={`${line(pts)}L${base.map(([bx, by]) => `${bx},${by}`).join("L")}Z`} style={{ fill: c }} fillOpacity={mode !== "none" ? 0.85 : 0.18} stroke={mode !== "none" ? "var(--panel)" : "none"} strokeWidth={1.5} />;
        })}
        {(!area || mode === "none") && sg.map((pts, j) => <path key={`l${j}`} d={line(pts)} fill="none" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" style={{ stroke: c }} />)}
        {n <= 40 && mode === "none" && s.values.map((val, i) => (val === null ? null : <circle key={i} cx={x(i)} cy={y(val)} r={3} className="chart-dot" style={{ fill: c }} />))}
        {k > 1 && k <= 4 && mode === "none" && (() => { const li = s.values.map((q, i) => (q === null ? -1 : i)).reduce((a, b) => Math.max(a, b), -1); return li < 0 ? null : <text className="chart-lab" x={x(li) + 7} y={y(s.values[li] as number) + 3}>{short(s.name, 12)}</text>; })()}
      </g>);
  });
  const step = labelStep(n), unitS = mode === "percent" ? "%" : unit;
  const tipFor = (i: number) => ({ y: M.t + 30, lines: [labels[i], ...series.map((s, si) => `${s.name}: ${v(si, i) === null ? "–" : fmtNum(v(si, i) as number) + unitS}`)] });
  const best = series.map((s, si) => [s.name, Math.max(...(s.values.filter((q) => q !== null) as number[]))] as const)[0];
  return {
    h: H, table: mkTable(ttl(spec, area ? "Area chart" : "Line chart"), ["", ...series.map((s) => s.name)], labels.map((l, i) => [l, ...series.map((s) => s.values[i])])),
    summary: `${ttl(spec, area ? "Area chart" : "Line chart")}: ${series.map((s) => s.name).join(", ")} across ${n} points${best ? `; ${best[0]} peaks at ${fmtNum(best[1])}${unit}` : ""}`,
    legend: k > 1 ? series.map((s, i) => ({ name: s.name, color: seriesColor(i) })) : undefined,
    svg: (<>
      <YAxis ticks={sc.ticks} y={y} x1={W - R} />
      <line className="viz-axis" x1={M.l} x2={W - R} y1={y(Math.max(sc.lo, Math.min(0, sc.hi)))} y2={y(Math.max(sc.lo, Math.min(0, sc.hi)))} />
      {labels.map((l, i) => (i % step === 0 || i === n - 1) && <text key={i} className="chart-tick" x={x(i)} y={H - 10} textAnchor="middle">{short(l, 10)}</text>)}
      {paths}
      <HoverLayer count={n} xAt={x} band={Math.max(6, p / Math.max(1, n - 1))} top={M.t} bottom={H - M.b} tip={tipFor} />
    </>),
  };
}

// ---------------------------------------------------------------------------------------------------- ranked and paired values
function lollipop(spec: Spec): Built {
  const { labels, series } = seriesOf(spec);
  need(labels.length && series.length, "a lollipop chart needs values");
  const vals = series[0].values.map((q) => q ?? 0), n = labels.length, L = Math.min(130, 10 + maxLen(labels) * 6.2), h = Math.min(700, n * 26 + M.t + M.b), unit = unitText(spec);
  const sc = niceScale(Math.min(0, ...vals), Math.max(0, ...vals)), x = linear(sc.lo, sc.hi, L, W - M.r - 30), band = (h - M.t - M.b) / n;
  return {
    h, table: mkTable(ttl(spec, "Lollipop chart"), ["", series[0].name], labels.map((l, i) => [l, series[0].values[i]])),
    summary: `${ttl(spec, "Lollipop chart")}: ${n} items, highest ${labels[vals.indexOf(Math.max(...vals))]} at ${fmtNum(Math.max(...vals))}${unit}`,
    svg: (<>
      <XAxis ticks={sc.ticks} x={x} y0={M.t} y1={h - M.b} />
      {labels.map((l, i) => { const cy = M.t + band * (i + 0.5); return (
        <g key={i}><text className="chart-tick" x={L - 6} y={cy + 3} textAnchor="end">{short(l, 20)}</text>
          <line x1={x(0)} x2={x(vals[i])} y1={cy} y2={cy} strokeWidth={2} style={{ stroke: seriesColor(0) }} />
          <circle cx={x(vals[i])} cy={cy} r={5} className="chart-dot" style={{ fill: seriesColor(0) }}><title>{`${l}: ${fmtNum(vals[i])}${unit}`}</title></circle>
          <text className="chart-tick" x={x(vals[i]) + 9} y={cy + 3}>{fmtNum(vals[i])}</text></g>); })}
    </>),
  };
}

function dumbbell(spec: Spec): Built {
  const { labels, series } = seriesOf(spec);
  need(labels.length && series.length >= 2, "a dumbbell chart needs two series");
  const a = series[0].values, b = series[1].values, n = labels.length, L = Math.min(130, 10 + maxLen(labels) * 6.2), h = Math.min(700, n * 28 + M.t + M.b);
  const all = [...a, ...b].filter((q): q is number => q !== null), sc = niceScale(Math.min(...all), Math.max(...all)), x = linear(sc.lo, sc.hi, L, W - M.r), band = (h - M.t - M.b) / n;
  return {
    h, table: mkTable(ttl(spec, "Dumbbell chart"), ["", series[0].name, series[1].name], labels.map((l, i) => [l, a[i], b[i]])),
    summary: `${ttl(spec, "Dumbbell chart")}: ${series[0].name} against ${series[1].name} for ${n} items`,
    legend: [series[0], series[1]].map((s, i) => ({ name: s.name, color: seriesColor(i) })),
    svg: (<>
      <XAxis ticks={sc.ticks} x={x} y0={M.t} y1={h - M.b} />
      {labels.map((l, i) => { const cy = M.t + band * (i + 0.5); return (
        <g key={i}><text className="chart-tick" x={L - 6} y={cy + 3} textAnchor="end">{short(l, 20)}</text>
          {a[i] !== null && b[i] !== null && <line x1={x(a[i] as number)} x2={x(b[i] as number)} y1={cy} y2={cy} strokeWidth={3} style={{ stroke: "var(--viz-grid)" }} />}
          {a[i] !== null && <circle cx={x(a[i] as number)} cy={cy} r={5} className="chart-dot" style={{ fill: seriesColor(0) }}><title>{`${l} · ${series[0].name}: ${fmtNum(a[i] as number)}`}</title></circle>}
          {b[i] !== null && <circle cx={x(b[i] as number)} cy={cy} r={5} className="chart-dot" style={{ fill: seriesColor(1) }}><title>{`${l} · ${series[1].name}: ${fmtNum(b[i] as number)}`}</title></circle>}</g>); })}
    </>),
  };
}

function slope(spec: Spec): Built {
  const { labels, series } = seriesOf(spec);
  need(labels.length >= 2 && series.length, "a slope chart needs two moments (x) and some series");
  const rows = series.map((s) => [s.values[0], s.values[1]] as const), all = rows.flat().filter((q): q is number => q !== null);
  need(all.length, "a slope chart needs numbers");
  const sc = niceScale(Math.min(...all), Math.max(...all), 4), y = linear(sc.lo, sc.hi, H - 36, 28), xl = 150, xr = W - 150;
  return {
    h: H, table: mkTable(ttl(spec, "Slope chart"), ["", labels[0], labels[1]], series.map((s) => [s.name, s.values[0], s.values[1]])),
    summary: `${ttl(spec, "Slope chart")}: ${series.length} items from ${labels[0]} to ${labels[1]}`,
    svg: (<>
      <text className="chart-tick" x={xl} y={14} textAnchor="middle">{short(labels[0], 14)}</text><text className="chart-tick" x={xr} y={14} textAnchor="middle">{short(labels[1], 14)}</text>
      {series.map((s, i) => { const [p, q] = rows[i]; if (p === null || q === null) return null; return (
        <g key={i}><line x1={xl} x2={xr} y1={y(p)} y2={y(q)} strokeWidth={2} style={{ stroke: seriesColor(i) }}><title>{`${s.name}: ${fmtNum(p)} → ${fmtNum(q)}`}</title></line>
          <circle cx={xl} cy={y(p)} r={4} className="chart-dot" style={{ fill: seriesColor(i) }} /><circle cx={xr} cy={y(q)} r={4} className="chart-dot" style={{ fill: seriesColor(i) }} />
          <text className="chart-lab" x={xl - 8} y={y(p) + 3} textAnchor="end">{short(s.name, 14)} {fmtNum(p)}</text><text className="chart-lab" x={xr + 8} y={y(q) + 3}>{fmtNum(q)} {short(s.name, 14)}</text></g>); })}
    </>),
  };
}

function sparkline(spec: Spec): Built {
  const { labels, series } = seriesOf(spec);
  need(series.length && series[0].values.some((q) => q !== null), "a sparkline needs values");
  const vals = series[0].values, nums = vals.filter((q): q is number => q !== null), lo = Math.min(...nums), hi = Math.max(...nums), w = 180, h = 44;
  const x = (i: number) => 4 + ((w - 8) * i) / Math.max(1, vals.length - 1), y = linear(lo, hi === lo ? lo + 1 : hi, h - 5, 5);
  const pts = vals.map((q, i) => (q === null ? null : [x(i), y(q)] as [number, number])).filter(Boolean) as [number, number][], last = pts[pts.length - 1];
  return {
    h, w, cls: "spark", bare: !spec.title, table: mkTable(ttl(spec, "Sparkline"), ["", "Value"], labels.map((l, i) => [l, vals[i]])),
    summary: `${ttl(spec, "Sparkline")}: ${vals.length} points from ${fmtNum(nums[0])} to ${fmtNum(nums[nums.length - 1])}, low ${fmtNum(lo)}, high ${fmtNum(hi)}`,
    svg: (<><polyline fill="none" strokeWidth={2} strokeLinejoin="round" style={{ stroke: seriesColor(0) }} points={pts.map((q) => q.join(",")).join(" ")} /><circle cx={last[0]} cy={last[1]} r={3} className="chart-dot" style={{ fill: seriesColor(0) }} /></>),
  };
}

function waterfall(spec: Spec): Built {
  const items = arr(spec.data, 200).map((d) => ({ label: str(d?.label ?? d?.name, 30), value: num(d?.value, 0), total: !!d?.total }));
  need(items.length, "a waterfall chart needs data");
  let run = 0;
  const bars = items.map((it) => { const from = it.total ? 0 : run; run = it.total ? (Number.isFinite(it.value) && it.value !== 0 ? it.value : run) : run + it.value; return { ...it, from, to: run }; });
  const ends = bars.flatMap((b) => [b.from, b.to]), sc = niceScale(Math.min(0, ...ends), Math.max(0, ...ends)), y = linear(sc.lo, sc.hi, H - M.b, M.t), n = bars.length, g = pw / n, bw = Math.min(46, g * 0.66), unit = unitText(spec);
  const col = (b: (typeof bars)[number]) => (b.total ? "var(--fg2)" : b.value >= 0 ? seriesColor(0) : seriesColor(1));
  return {
    h: H, table: mkTable(ttl(spec, "Waterfall chart"), ["Step", "Change", "Running total"], bars.map((b) => [b.label, b.total ? "total" : b.value, b.to])),
    summary: `${ttl(spec, "Waterfall chart")}: from ${fmtNum(bars[0].from)} to ${fmtNum(bars[n - 1].to)}${unit} in ${n} steps`,
    legend: [{ name: "Increase", color: seriesColor(0) }, { name: "Decrease", color: seriesColor(1) }, { name: "Total", color: "var(--fg2)" }],
    svg: (<>
      <YAxis ticks={sc.ticks} y={y} /><line className="viz-axis" x1={M.l} x2={W - M.r} y1={y(0)} y2={y(0)} />
      {bars.map((b, i) => { const cx = M.l + g * (i + 0.5), top = Math.min(y(b.from), y(b.to)), hh = Math.max(1, Math.abs(y(b.to) - y(b.from))); return (
        <g key={i}><rect x={cx - bw / 2} y={top} width={bw} height={hh} rx={2} suppressHydrationWarning style={{ fill: col(b) }}><title>{`${b.label}: ${b.total ? "total " : b.value >= 0 ? "+" : ""}${fmtNum(b.total ? b.to : b.value)}${unit} (running ${fmtNum(b.to)})`}</title></rect>
          <text className="chart-tick" x={cx} y={top - 3} textAnchor="middle">{b.total ? fmtNum(b.to) : `${b.value >= 0 ? "+" : "−"}${fmtNum(Math.abs(b.value))}`}</text>
          <text className="chart-tick" x={cx} y={H - 10} textAnchor="middle">{short(b.label, 10)}</text></g>); })}
    </>),
  };
}

// ---------------------------------------------------------------------------------------------------- scatter and bubble
function points(spec: Spec, bubble: boolean): Built {
  const pts = arr(spec.points, 5000).map((p) => ({ x: num(p?.x), y: num(p?.y), size: num(p?.size, 1), group: str(p?.group ?? "", 40), label: str(p?.label ?? "", 40) })).filter((p) => finite(p.x) && finite(p.y));
  need(pts.length, `a ${bubble ? "bubble" : "scatter"} chart needs points with numeric x and y`);
  // scatter-style charts show at most three coloured groups: with more, no palette keeps every pair apart, so the rest share one neutral colour
  const groups = groupColors(pts.map((p) => p.group || "All"), 3), L = 56;
  const sx = niceScale(Math.min(...pts.map((p) => p.x)), Math.max(...pts.map((p) => p.x))), sy = niceScale(Math.min(...pts.map((p) => p.y)), Math.max(...pts.map((p) => p.y)));
  const x = linear(sx.lo, sx.hi, L, W - M.r), y = linear(sy.lo, sy.hi, H - M.b - 8, M.t), maxSize = Math.max(...pts.map((p) => (finite(p.size) ? p.size : 1)), 1e-9), rad = (s: number) => 4 + 18 * Math.sqrt(Math.max(0, s) / maxSize);
  let line: Any = null;
  if (spec.trend && pts.length > 2) {
    const n = pts.length, mx = pts.reduce((a, p) => a + p.x, 0) / n, my = pts.reduce((a, p) => a + p.y, 0) / n, sxx = pts.reduce((a, p) => a + (p.x - mx) ** 2, 0), sxy = pts.reduce((a, p) => a + (p.x - mx) * (p.y - my), 0);
    if (sxx > 0) { const m = sxy / sxx, b = my - m * mx; line = <line x1={x(sx.lo)} y1={y(m * sx.lo + b)} x2={x(sx.hi)} y2={y(m * sx.hi + b)} className="viz-axis" strokeDasharray="5 4" strokeWidth={1.5}><title>{`Trend: y = ${+m.toFixed(3)}x + ${+b.toFixed(3)}`}</title></line>; }
  }
  const xl = str(spec.x_label, 60), yl = str(spec.y_label, 60);
  return {
    h: H, table: mkTable(ttl(spec, bubble ? "Bubble chart" : "Scatter plot"), ["Group", "Label", xl || "x", yl || "y", ...(bubble ? ["Size"] : [])], pts.map((p) => [p.group, p.label, p.x, p.y, ...(bubble ? [p.size] : [])])),
    summary: `${ttl(spec, bubble ? "Bubble chart" : "Scatter plot")}: ${pts.length} points${xl && yl ? `, ${yl} against ${xl}` : ""}`,
    legend: groups.legend.length > 1 ? groups.legend : undefined,
    svg: (<>
      <YAxis ticks={sy.ticks} y={y} x0={L} /><XAxis ticks={sx.ticks} x={x} y0={M.t} y1={H - M.b - 8} />
      {xl && <text className="chart-tick" x={(L + W) / 2} y={H - 2} textAnchor="middle">{xl}</text>}{yl && <text className="chart-tick" x={10} y={M.t + 4} textAnchor="start">{yl}</text>}
      {line}
      {pts.map((p, i) => <circle key={i} cx={x(p.x)} cy={y(p.y)} r={bubble ? rad(p.size) : 4} className="chart-dot" fillOpacity={bubble ? 0.7 : 0.9} style={{ fill: groups.color(p.group || "All") }}><title>{`${p.label || p.group || "point"}: ${fmtNum(p.x)}, ${fmtNum(p.y)}${bubble ? `, size ${fmtNum(p.size)}` : ""}`}</title></circle>)}
      {bubble && pts.length <= 12 && pts.map((p, i) => p.label && <text key={`l${i}`} className="chart-lab" x={x(p.x)} y={y(p.y) - rad(p.size) - 3} textAnchor="middle">{short(p.label, 12)}</text>)}
    </>),
  };
}

// ---------------------------------------------------------------------------------------------------- distributions
const quantile = (sorted: number[], q: number) => { const p = (sorted.length - 1) * q, i = Math.floor(p), f = p - i; return sorted[i] + (sorted[i + 1] !== undefined ? f * (sorted[i + 1] - sorted[i]) : 0); };

function histogram(spec: Spec): Built {
  const vals = arr(spec.values, 20000).map((v) => num(v)).filter(finite);
  need(vals.length, "a histogram needs numbers");
  const lo = Math.min(...vals), hi = Math.max(...vals), bins = Math.max(2, Math.min(60, Math.round(num(spec.bins, Math.ceil(Math.log2(vals.length) + 1))))), w = (hi - lo || 1) / bins;
  const counts = Array.from({ length: bins }, () => 0);
  for (const v of vals) counts[Math.min(bins - 1, Math.floor((v - lo) / w))]++;
  const sc = niceScale(0, Math.max(...counts)), y = linear(sc.lo, sc.hi, H - M.b, M.t), x = linear(lo, lo + w * bins, M.l, W - M.r), unit = unitText(spec), edge = (i: number) => lo + i * w;
  const ticks = niceScale(lo, lo + w * bins, 6).ticks.filter((t) => t >= lo - 1e-9 && t <= lo + w * bins + 1e-9);
  return {
    h: H, table: mkTable(ttl(spec, "Histogram"), ["From", "To", "Count"], counts.map((c, i) => [+edge(i).toFixed(4), +edge(i + 1).toFixed(4), c])),
    summary: `${ttl(spec, "Histogram")}: ${vals.length} values from ${fmtNum(lo)} to ${fmtNum(hi)}${unit} in ${bins} bins; the tallest bin holds ${Math.max(...counts)}`,
    svg: (<>
      <YAxis ticks={sc.ticks} y={y} /><line className="viz-axis" x1={M.l} x2={W - M.r} y1={y(0)} y2={y(0)} />
      {counts.map((c, i) => <rect key={i} x={x(edge(i)) + 0.75} y={y(c)} width={Math.max(1, x(edge(i + 1)) - x(edge(i)) - 1.5)} height={Math.max(0, y(0) - y(c))} rx={2} suppressHydrationWarning style={{ fill: seriesColor(0) }}><title>{`${fmtNum(edge(i))} to ${fmtNum(edge(i + 1))}${unit}: ${c}`}</title></rect>)}
      {ticks.map((t, i) => <text key={i} className="chart-tick" x={x(t)} y={H - 10} textAnchor="middle">{fmtNum(t)}</text>)}
    </>),
  };
}

function box(spec: Spec): Built {
  const groups = arr(spec.groups, 40).map((g, gi) => {
    const name = str(g?.name ?? `Group ${gi + 1}`, 30);
    if (isObj(g?.stats)) { const s = g.stats, st = { min: num(s.min), q1: num(s.q1), median: num(s.median), q3: num(s.q3), max: num(s.max) }; return Object.values(st).every(finite) ? { name, ...st, out: arr(s.outliers, 50).map((o) => num(o)).filter(finite) } : null; }
    const v = arr(g?.values, 5000).map((q) => num(q)).filter(finite).sort((a, b) => a - b);
    if (v.length < 2) return null;
    const q1 = quantile(v, 0.25), q3 = quantile(v, 0.75), iqr = q3 - q1, inside = v.filter((q) => q >= q1 - 1.5 * iqr && q <= q3 + 1.5 * iqr);
    return { name, min: inside[0], q1, median: quantile(v, 0.5), q3, max: inside[inside.length - 1], out: v.filter((q) => q < q1 - 1.5 * iqr || q > q3 + 1.5 * iqr) };
  }).filter(Boolean) as { name: string; min: number; q1: number; median: number; q3: number; max: number; out: number[] }[];
  need(groups.length, "a box plot needs groups with values or stats");
  const all = groups.flatMap((g) => [g.min, g.max, ...g.out]), sc = niceScale(Math.min(...all), Math.max(...all)), y = linear(sc.lo, sc.hi, H - M.b, M.t), n = groups.length, g = pw / n, bw = Math.min(54, g * 0.5), unit = unitText(spec);
  return {
    h: H, table: mkTable(ttl(spec, "Box plot"), ["Group", "Min", "Q1", "Median", "Q3", "Max", "Outliers"], groups.map((q) => [q.name, q.min, q.q1, q.median, q.q3, q.max, q.out.length])),
    summary: `${ttl(spec, "Box plot")}: ${n} groups; medians ${groups.map((q) => `${q.name} ${fmtNum(q.median)}`).join(", ")}${unit}`,
    svg: (<>
      <YAxis ticks={sc.ticks} y={y} />
      {groups.map((q, i) => { const cx = M.l + g * (i + 0.5), c = seriesColor(0); return (
        <g key={i}><line x1={cx} x2={cx} y1={y(q.min)} y2={y(q.max)} strokeWidth={1.5} style={{ stroke: c }} /><line x1={cx - bw / 4} x2={cx + bw / 4} y1={y(q.min)} y2={y(q.min)} strokeWidth={1.5} style={{ stroke: c }} /><line x1={cx - bw / 4} x2={cx + bw / 4} y1={y(q.max)} y2={y(q.max)} strokeWidth={1.5} style={{ stroke: c }} />
          <rect x={cx - bw / 2} y={y(q.q3)} width={bw} height={Math.max(1, y(q.q1) - y(q.q3))} rx={3} fillOpacity={0.35} strokeWidth={1.5} style={{ fill: c, stroke: c }}><title>{`${q.name}: min ${fmtNum(q.min)}, Q1 ${fmtNum(q.q1)}, median ${fmtNum(q.median)}, Q3 ${fmtNum(q.q3)}, max ${fmtNum(q.max)}${unit}`}</title></rect>
          <line x1={cx - bw / 2} x2={cx + bw / 2} y1={y(q.median)} y2={y(q.median)} strokeWidth={2.5} style={{ stroke: "var(--fg)" }} />
          {q.out.map((o, j) => <circle key={j} cx={cx} cy={y(o)} r={3} fill="none" strokeWidth={1.5} style={{ stroke: c }}><title>{`outlier ${fmtNum(o)}`}</title></circle>)}
          <text className="chart-tick" x={cx} y={H - 10} textAnchor="middle">{short(q.name, 12)}</text></g>); })}
    </>),
  };
}

function candlestick(spec: Spec): Built {
  const all = arr(spec.data, 5000).map((d) => ({ t: str(d?.t ?? d?.date ?? d?.time, 30), o: num(d?.open), h: num(d?.high), l: num(d?.low), c: num(d?.close), v: num(d?.volume) })).filter((d) => finite(d.o) && finite(d.h) && finite(d.l) && finite(d.c));
  need(all.length, "a candlestick chart needs rows with open, high, low and close");
  const data = all.slice(-300), n = data.length, hasVol = data.some((d) => finite(d.v)), top = M.t, priceB = hasVol ? H - M.b - 46 : H - M.b;
  const sc = niceScale(Math.min(...data.map((d) => d.l)), Math.max(...data.map((d) => d.h))), y = linear(sc.lo, sc.hi, priceB, top), g = pw / n, bw = Math.max(1.5, Math.min(14, g * 0.7));
  const vmax = Math.max(...data.map((d) => (finite(d.v) ? d.v : 0)), 1), vy = (v: number) => H - M.b - (v / vmax) * 38, x = (i: number) => M.l + g * (i + 0.5), step = labelStep(n);
  const unit = unitText(spec);
  const tipFor = (i: number) => ({ y: M.t + 40, lines: [data[i].t, `open ${fmtNum(data[i].o)}`, `high ${fmtNum(data[i].h)}`, `low ${fmtNum(data[i].l)}`, `close ${fmtNum(data[i].c)}`, ...(finite(data[i].v) ? [`volume ${fmtNum(data[i].v)}`] : [])] });
  return {
    h: H, table: mkTable(ttl(spec, "Candlestick chart"), ["Time", "Open", "High", "Low", "Close", ...(hasVol ? ["Volume"] : [])], all.map((d) => [d.t, d.o, d.h, d.l, d.c, ...(hasVol ? [d.v] : [])])),
    summary: `${ttl(spec, "Candlestick chart")}: ${n} periods${all.length > n ? ` (the last ${n} of ${all.length})` : ""}; closed at ${fmtNum(data[n - 1].c)}${unit}, range ${fmtNum(Math.min(...data.map((d) => d.l)))} to ${fmtNum(Math.max(...data.map((d) => d.h)))}`,
    legend: [{ name: "Closed higher", color: seriesColor(0) }, { name: "Closed lower", color: seriesColor(1) }],
    svg: (<>
      <YAxis ticks={sc.ticks} y={y} />
      {data.map((d, i) => { const up = d.c >= d.o, c = up ? seriesColor(0) : seriesColor(1); return (
        <g key={i}><line x1={x(i)} x2={x(i)} y1={y(d.h)} y2={y(d.l)} strokeWidth={1} style={{ stroke: c }} /><rect x={x(i) - bw / 2} y={y(Math.max(d.o, d.c))} width={bw} height={Math.max(1, Math.abs(y(d.o) - y(d.c)))} style={{ fill: c }} />
          {hasVol && finite(d.v) && <rect x={x(i) - bw / 2} y={vy(d.v)} width={bw} height={H - M.b - vy(d.v)} fillOpacity={0.5} style={{ fill: c }} />}</g>); })}
      {data.map((d, i) => (i % step === 0 || i === n - 1) && <text key={`x${i}`} className="chart-tick" x={x(i)} y={H - 10} textAnchor="middle">{short(d.t, 10)}</text>)}
      <HoverLayer count={n} xAt={x} band={g} top={M.t} bottom={H - M.b} tip={tipFor} />
    </>),
  };
}

function bullet(spec: Spec): Built {
  const rows = arr(spec.data, 30).map((d) => ({ label: str(d?.label ?? d?.name, 30), value: num(d?.value), target: num(d?.target), ranges: arr(d?.ranges, 5).map((r) => num(r)).filter(finite).sort((a, b) => a - b) })).filter((r) => finite(r.value));
  need(rows.length, "a bullet chart needs rows with a value");
  const L = Math.min(130, 10 + maxLen(rows.map((r) => r.label)) * 6.2), h = Math.min(600, rows.length * 44 + M.t + M.b), band = (h - M.t - M.b) / rows.length;
  const max = Math.max(...rows.flatMap((r) => [r.value, finite(r.target) ? r.target : 0, ...r.ranges])), sc = niceScale(0, max), x = linear(sc.lo, sc.hi, L, W - M.r);
  return {
    h, table: mkTable(ttl(spec, "Bullet chart"), ["", "Value", "Target"], rows.map((r) => [r.label, r.value, finite(r.target) ? r.target : null])),
    summary: `${ttl(spec, "Bullet chart")}: ${rows.map((r) => `${r.label} ${fmtNum(r.value)}${finite(r.target) ? ` of target ${fmtNum(r.target)}` : ""}`).join(", ")}`,
    svg: (<>
      <XAxis ticks={sc.ticks} x={x} y0={M.t} y1={h - M.b} />
      {rows.map((r, i) => { const cy = M.t + band * (i + 0.5), bands = [...r.ranges].reverse(); return (
        <g key={i}><text className="chart-tick" x={L - 6} y={cy + 3} textAnchor="end">{short(r.label, 20)}</text>
          {bands.map((rg, j) => <rect key={j} x={x(0)} y={cy - 14} width={Math.max(0, x(rg) - x(0))} height={28} style={{ fill: "var(--viz-other)" }} fillOpacity={0.12 + 0.12 * (bands.length - j)} />)}
          <rect x={x(0)} y={cy - 5} width={Math.max(1, x(r.value) - x(0))} height={10} rx={2} suppressHydrationWarning style={{ fill: seriesColor(0) }}><title>{`${r.label}: ${fmtNum(r.value)}`}</title></rect>
          {finite(r.target) && <line x1={x(r.target)} x2={x(r.target)} y1={cy - 12} y2={cy + 12} strokeWidth={3} style={{ stroke: "var(--fg)" }}><title>{`target ${fmtNum(r.target)}`}</title></line>}</g>); })}
    </>),
  };
}

// ---------------------------------------------------------------------------------------------------- grids
function heatmap(spec: Spec): Built {
  let xs: string[], ys: string[], grid: (number | null)[][];
  if (Array.isArray(spec.cells) && spec.cells.length) {
    const cells = arr(spec.cells, 20000);
    xs = [...new Set(cells.map((c) => str(c?.x, 30)))]; ys = [...new Set(cells.map((c) => str(c?.y, 30)))];
    grid = ys.map(() => xs.map(() => null)); for (const c of cells) { const v = num(c?.value); if (finite(v)) grid[ys.indexOf(str(c?.y, 30))][xs.indexOf(str(c?.x, 30))] = v; }
  } else {
    xs = arr(spec.x, 200).map((v) => str(v, 30)); ys = arr(spec.y, 200).map((v) => str(v, 30));
    grid = ys.map((_, r) => xs.map((__, c) => { const v = num(arr(spec.values, 200)[r]?.[c]); return finite(v) ? v : null; }));
  }
  need(xs.length && ys.length, "a heatmap needs x, y and values");
  const flat = grid.flat().filter((q): q is number => q !== null), lo = Math.min(...flat), hi = Math.max(...flat), L = Math.min(110, 10 + maxLen(ys) * 6.2), cw = Math.min(60, (W - L - M.r) / xs.length), ch = Math.min(34, 24 + 0), h = M.t + ys.length * ch + 44;
  const t = (v: number) => (hi === lo ? 1 : (v - lo) / (hi - lo)), labelCells = xs.length * ys.length <= 80;
  return {
    h, table: mkTable(ttl(spec, "Heatmap"), ["", ...xs], ys.map((yy, r) => [yy, ...grid[r]])),
    summary: `${ttl(spec, "Heatmap")}: ${ys.length} rows by ${xs.length} columns, values from ${fmtNum(lo)} to ${fmtNum(hi)}`,
    foot: <span className="chart-ramp" aria-hidden="true"><span>{fmtNum(lo)}</span><i suppressHydrationWarning style={{ background: `linear-gradient(90deg, ${seqColor(0)}, ${seqColor(1)})` }} /><span>{fmtNum(hi)}</span></span>,
    svg: (<>
      {xs.map((l, c) => <text key={`x${c}`} className="chart-tick" x={L + cw * (c + 0.5)} y={M.t + ys.length * ch + 14} textAnchor="middle">{short(l, 8)}</text>)}
      {ys.map((l, r) => <text key={`y${r}`} className="chart-tick" x={L - 6} y={M.t + ch * (r + 0.5) + 3} textAnchor="end">{short(l, 16)}</text>)}
      {grid.map((row, r) => row.map((v, c) => v === null ? null : (
        <g key={`${r}-${c}`}><rect x={L + cw * c + 1} y={M.t + ch * r + 1} width={cw - 2} height={ch - 2} rx={3} suppressHydrationWarning style={{ fill: seqColor(0.08 + 0.92 * t(v)) }}><title>{`${ys[r]} · ${xs[c]}: ${fmtNum(v)}`}</title></rect>
          {labelCells && <text className="chart-tick" x={L + cw * (c + 0.5)} y={M.t + ch * (r + 0.5) + 3} textAnchor="middle" style={{ fill: t(v) > 0.55 ? "#fff" : "var(--fg)" }}>{fmtNum(v)}</text>}</g>)))}
    </>),
  };
}

function calendar(spec: Spec): Built {
  const days = arr(spec.days, 3700).map((d) => ({ t: toTime(d?.date), date: str(d?.date, 12), v: num(d?.value) })).filter((d) => finite(d.t) && finite(d.v));
  need(days.length, "a calendar chart needs days with a date and a value");
  const DAY = 86400000, first = Math.min(...days.map((d) => d.t)), start = first - new Date(first).getUTCDay() * DAY, by = new Map(days.map((d) => [Math.round((d.t - start) / DAY), d]));
  const last = Math.max(...days.map((d) => d.t)), weeks = Math.floor((last - start) / DAY / 7) + 1, cell = Math.min(14, (W - 44) / weeks), L = 30, h = M.t + 7 * (cell + 2) + 26;
  const hi = Math.max(...days.map((d) => d.v)), lo = Math.min(0, ...days.map((d) => d.v)), t = (v: number) => (hi === lo ? 1 : (v - lo) / (hi - lo));
  const months: Any[] = []; let lastM = -1;
  for (let w = 0; w < weeks; w++) { const m = new Date(start + w * 7 * DAY).getUTCMonth(); if (m !== lastM) { lastM = m; months.push(<text key={w} className="chart-tick" x={L + w * (cell + 2)} y={M.t + 7 * (cell + 2) + 14}>{new Date(start + w * 7 * DAY).toLocaleString(undefined, { month: "short", timeZone: "UTC" })}</text>); } }
  const cells: Any[] = [];
  for (let i = 0; i < weeks * 7; i++) { const d = by.get(i), w = Math.floor(i / 7), r = i % 7; if (start + i * DAY < first || start + i * DAY > last) continue;
    cells.push(<rect key={i} x={L + w * (cell + 2)} y={M.t + r * (cell + 2)} width={cell} height={cell} rx={2} suppressHydrationWarning style={{ fill: d ? seqColor(0.12 + 0.88 * t(d.v)) : "var(--panel2)" }}><title>{`${new Date(start + i * DAY).toISOString().slice(0, 10)}: ${d ? fmtNum(d.v) : "no data"}`}</title></rect>); }
  return {
    h, table: mkTable(ttl(spec, "Calendar"), ["Date", "Value"], days.map((d) => [d.date, d.v])),
    summary: `${ttl(spec, "Calendar chart")}: ${days.length} days with values from ${fmtNum(Math.min(...days.map((d) => d.v)))} to ${fmtNum(hi)}`,
    foot: <span className="chart-ramp" aria-hidden="true"><span>less</span><i suppressHydrationWarning style={{ background: `linear-gradient(90deg, ${seqColor(0.12)}, ${seqColor(1)})` }} /><span>more</span></span>,
    svg: (<>{["Mon", "Wed", "Fri"].map((d, i) => <text key={d} className="chart-tick" x={L - 6} y={M.t + (i * 2 + 1) * (cell + 2) + cell - 2} textAnchor="end">{d}</text>)}{months}{cells}</>),
  };
}

function gantt(spec: Spec): Built {
  const tasks = arr(spec.tasks, 100).map((t, i) => ({ name: str(t?.name ?? `Task ${i + 1}`, 40), s: toTime(t?.start), e: toTime(t?.end), group: str(t?.group ?? "", 30), p: Math.min(1, Math.max(0, num(t?.progress, 0))) })).filter((t) => finite(t.s) && finite(t.e) && t.e >= t.s);
  need(tasks.length, "a Gantt chart needs tasks with a start and an end date");
  const L = Math.min(150, 10 + maxLen(tasks.map((t) => t.name)) * 6.2), h = Math.min(800, tasks.length * 26 + M.t + M.b), band = (h - M.t - M.b) / tasks.length;
  const t0 = Math.min(...tasks.map((t) => t.s)), t1 = Math.max(...tasks.map((t) => t.e), t0 + 86400000), x = linear(t0, t1, L, W - M.r), groups = groupColors(tasks.map((t) => t.group || "Tasks"), 8);
  const ticks = [0, 1, 2, 3, 4].map((i) => t0 + ((t1 - t0) * i) / 4), fmtD = (t: number) => new Date(t).toISOString().slice(0, 10);
  return {
    h, table: mkTable(ttl(spec, "Gantt chart"), ["Task", "Group", "Start", "End", "Progress"], tasks.map((t) => [t.name, t.group, fmtD(t.s), fmtD(t.e), t.p])),
    summary: `${ttl(spec, "Gantt chart")}: ${tasks.length} tasks from ${fmtD(t0)} to ${fmtD(t1)}`,
    legend: groups.legend.length > 1 ? groups.legend : undefined,
    svg: (<>
      {ticks.map((t, i) => <g key={i}><line className="viz-grid" x1={x(t)} x2={x(t)} y1={M.t} y2={h - M.b} /><text className="chart-tick" x={x(t)} y={h - 12} textAnchor="middle">{fmtD(t).slice(5)}</text></g>)}
      {tasks.map((t, i) => { const cy = M.t + band * (i + 0.5), c = groups.color(t.group || "Tasks"), w = Math.max(3, x(t.e) - x(t.s)); return (
        <g key={i}><text className="chart-tick" x={L - 6} y={cy + 3} textAnchor="end">{short(t.name, 24)}</text>
          <rect x={x(t.s)} y={cy - 8} width={w} height={16} rx={3} fillOpacity={0.35} style={{ fill: c }}><title>{`${t.name}: ${fmtD(t.s)} to ${fmtD(t.e)}${t.p ? `, ${Math.round(t.p * 100)}% done` : ""}`}</title></rect>
          {t.p > 0 && <rect x={x(t.s)} y={cy - 8} width={w * t.p} height={16} rx={3} suppressHydrationWarning style={{ fill: c }} />}</g>); })}
    </>),
  };
}

export const CHARTS1: Record<string, ReturnType<typeof chartComponent>> = {
  "chart.bar": chartComponent(bar), "chart.line": chartComponent((s) => trend(s, false)), "chart.area": chartComponent((s) => trend(s, true)), "chart.lollipop": chartComponent(lollipop),
  "chart.dumbbell": chartComponent(dumbbell), "chart.slope": chartComponent(slope), "chart.sparkline": chartComponent(sparkline), "chart.waterfall": chartComponent(waterfall),
  "chart.scatter": chartComponent((s) => points(s, false)), "chart.bubble": chartComponent((s) => points(s, true)), "chart.histogram": chartComponent(histogram), "chart.box": chartComponent(box),
  "chart.candlestick": chartComponent(candlestick), "chart.bullet": chartComponent(bullet), "chart.heatmap": chartComponent(heatmap), "chart.calendar": chartComponent(calendar), "chart.gantt": chartComponent(gantt),
};
