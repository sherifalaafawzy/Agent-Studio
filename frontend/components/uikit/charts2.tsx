"use client";
// Part-to-whole, flow and hierarchy charts drawn as plain SVG: pie, funnel, treemap, sunburst, tree, sankey, network, radar, gauge.
import { Any, arr, finite, fmtNum, isObj, num, seqColor, seriesColor, str } from "@/lib/uikit/data";
import type { Spec } from "@/lib/uikit/safe";
import { Built, W, chartComponent, groupColors, mkTable, need, short } from "./chartkit";

const ttl = (s: Spec, d: string) => str(s.title, 80) || d;
const TAU = Math.PI * 2;
const polar = (cx: number, cy: number, r: number, a: number): [number, number] => [cx + r * Math.cos(a), cy + r * Math.sin(a)];

/** An annular sector from angle a0 to a1 (radians, 0 = 3 o'clock). A full circle is drawn as two halves. */
function arcPath(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number): string {
  const end = Math.min(a1, a0 + TAU - 0.0001), large = end - a0 > Math.PI ? 1 : 0;
  const [x0, y0] = polar(cx, cy, r1, a0), [x1, y1] = polar(cx, cy, r1, end), [x2, y2] = polar(cx, cy, r0, end), [x3, y3] = polar(cx, cy, r0, a0);
  return `M${x0},${y0}A${r1},${r1} 0 ${large} 1 ${x1},${y1}L${x2},${y2}A${r0},${r0} 0 ${large} 0 ${x3},${y3}Z`;
}

// ---------------------------------------------------------------------------------------------------- pie
function pie(spec: Spec): Built {
  const all = arr(spec.data, 200).map((d) => ({ label: str(d?.label ?? d?.name, 40), value: num(d?.value) })).filter((d) => finite(d.value) && d.value > 0);
  need(all.length, "a pie chart needs positive values");
  const MAX = 6, sorted = [...all].sort((a, b) => b.value - a.value), slices = sorted.length > MAX ? [...sorted.slice(0, MAX - 1), { label: "Other", value: sorted.slice(MAX - 1).reduce((a, d) => a + d.value, 0) }] : sorted;
  const total = slices.reduce((a, d) => a + d.value, 0), donut = !!spec.donut, h = 250, cx = W / 2, cy = 126, R = 92, r0 = donut ? 52 : 0;
  let a = -Math.PI / 2;
  const segs = slices.map((d, i) => { const a0 = a; a += (d.value / total) * TAU; return { ...d, a0, a1: a, i, color: d.label === "Other" && sorted.length > MAX ? "var(--viz-other)" : seriesColor(i) }; });
  return {
    h, table: mkTable(ttl(spec, "Pie chart"), ["Slice", "Value", "Share"], sorted.map((d) => [d.label, d.value, `${+((d.value / total) * 100).toFixed(1)}%`])),
    summary: `${ttl(spec, "Pie chart")}: ${slices.map((d) => `${d.label} ${Math.round((d.value / total) * 100)}%`).join(", ")}`,
    legend: segs.map((s) => ({ name: `${s.label} (${Math.round((s.value / total) * 100)}%)`, color: s.color })),
    svg: (<>
      {segs.map((s) => <path key={s.i} d={arcPath(cx, cy, r0, R, s.a0, s.a1)} stroke="var(--panel)" strokeWidth={2} style={{ fill: s.color }}><title>{`${s.label}: ${fmtNum(s.value)} (${+((s.value / total) * 100).toFixed(1)}%)`}</title></path>)}
      {segs.filter((s) => s.value / total >= 0.05).map((s) => { const m = (s.a0 + s.a1) / 2, [lx, ly] = polar(cx, cy, R + 14, m); return <text key={`t${s.i}`} className="chart-lab" x={lx} y={ly + 3} textAnchor={Math.cos(m) >= 0 ? "start" : "end"}>{short(s.label, 14)}</text>; })}
      {donut && <><text className="chart-big" x={cx} y={cy + 2} textAnchor="middle">{fmtNum(total)}</text><text className="chart-tick" x={cx} y={cy + 18} textAnchor="middle">total</text></>}
    </>),
  };
}

function funnel(spec: Spec): Built {
  const rows = arr(spec.data, 20).map((d) => ({ label: str(d?.label ?? d?.name, 30), value: num(d?.value) })).filter((d) => finite(d.value) && d.value >= 0);
  need(rows.length, "a funnel needs stages with values");
  const h = rows.length * 40 + 20, max = Math.max(...rows.map((r) => r.value), 1e-9), cx = W / 2 + 40, bw = W - 260;
  return {
    h, table: mkTable(ttl(spec, "Funnel"), ["Stage", "Value", "Of first", "From previous"], rows.map((r, i) => [r.label, r.value, rows[0].value ? `${+((r.value / rows[0].value) * 100).toFixed(1)}%` : "", i && rows[i - 1].value ? `${+((r.value / rows[i - 1].value) * 100).toFixed(1)}%` : ""])),
    summary: `${ttl(spec, "Funnel")}: ${rows.map((r) => `${r.label} ${fmtNum(r.value)}`).join(" → ")}`,
    svg: (<>
      {rows.map((r, i) => { const w = Math.max(3, (r.value / max) * bw), y = 10 + i * 40; return (
        <g key={i}><text className="chart-tick" x={cx - bw / 2 - 10} y={y + 20} textAnchor="end">{short(r.label, 18)}</text>
          <rect x={cx - w / 2} y={y} width={w} height={32} rx={4} style={{ fill: seriesColor(0) }} fillOpacity={1 - i * (0.55 / Math.max(1, rows.length))}><title>{`${r.label}: ${fmtNum(r.value)}`}</title></rect>
          <text className="chart-tick" x={cx + bw / 2 + 10} y={y + 20}>{fmtNum(r.value)}{i ? ` · ${rows[i - 1].value ? Math.round((r.value / rows[i - 1].value) * 100) : 0}%` : ""}</text></g>); })}
    </>),
  };
}

// ---------------------------------------------------------------------------------------------------- hierarchies
type Node = { name: string; value: number; children: Node[] };
function readTree(raw: Any, depth = 0, budget = { n: 400 }): Node | null {
  if (!isObj(raw) || budget.n-- <= 0) return null;
  const kids = depth < 6 ? arr(raw.children, 100).map((c) => readTree(c, depth + 1, budget)).filter(Boolean) as Node[] : [];
  const own = num(raw.value, NaN);
  return { name: str(raw.name ?? raw.label, 40), value: kids.length ? kids.reduce((a, k) => a + k.value, 0) : finite(own) && own > 0 ? own : 1, children: kids };
}
const flatten = (n: Node, path: string[] = []): [string, number][] => (n.children.length ? n.children.flatMap((c) => flatten(c, [...path, n.name])) : [[[...path, n.name].slice(1).join(" / ") || n.name, n.value]]);
const need2 = (spec: Spec) => { const t = readTree(spec.root); need(t, "give a root with a name and children"); return t as Node; };

function squarify(items: { node: Node; v: number }[], x: number, y: number, w: number, h: number) {
  const total = items.reduce((a, i) => a + i.v, 0) || 1, scale = (w * h) / total;
  let rest = items.map((i) => ({ ...i, a: i.v * scale })).sort((a, b) => b.a - a.a);
  const out: { node: Node; x: number; y: number; w: number; h: number }[] = [];
  const worst = (row: { a: number }[], side: number) => { const s = row.reduce((t, r) => t + r.a, 0), mx = Math.max(...row.map((r) => r.a)), mn = Math.min(...row.map((r) => r.a)); return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn)); };
  while (rest.length) {
    const side = Math.min(w, h) || 1, row = [rest[0]];
    let i = 1;
    while (i < rest.length && worst([...row, rest[i]], side) <= worst(row, side)) row.push(rest[i++]);
    const s = row.reduce((t, r) => t + r.a, 0);
    if (w >= h) { const cw = s / (h || 1); let yy = y; for (const r of row) { const rh = r.a / (cw || 1); out.push({ node: r.node, x, y: yy, w: cw, h: rh }); yy += rh; } x += cw; w -= cw; }
    else { const rh = s / (w || 1); let xx = x; for (const r of row) { const rw = r.a / (rh || 1); out.push({ node: r.node, x: xx, y, w: rw, h: rh }); xx += rw; } y += rh; h -= rh; }
    rest = rest.slice(row.length);
  }
  return out;
}

function treemap(spec: Spec): Built {
  const root = need2(spec), h = 320;
  const top = squarify(root.children.map((c) => ({ node: c, v: c.value })), 0, 0, W, h - 0);
  return {
    h, table: mkTable(ttl(spec, "Treemap"), ["Path", "Value"], flatten(root).map(([p, v]) => [p, v])),
    summary: `${ttl(spec, "Treemap")}: ${root.name} of ${fmtNum(root.value)} split into ${root.children.map((c) => `${c.name} ${Math.round((c.value / root.value) * 100)}%`).join(", ")}`,
    legend: root.children.length > 1 ? root.children.slice(0, 8).map((c, i) => ({ name: c.name, color: seriesColor(i) })) : undefined,
    svg: (<>{top.map((t, i) => {
      const inner = t.node.children.length ? squarify(t.node.children.map((c) => ({ node: c, v: c.value })), t.x + 2, t.y + 16, t.w - 4, t.h - 18) : [];
      return (
        <g key={i}><rect x={t.x + 1} y={t.y + 1} width={Math.max(0, t.w - 2)} height={Math.max(0, t.h - 2)} rx={4} fillOpacity={0.18} stroke="var(--panel)" style={{ fill: seriesColor(i) }}><title>{`${t.node.name}: ${fmtNum(t.node.value)}`}</title></rect>
          {t.w > 46 && t.h > 20 && <text className="chart-lab b" x={t.x + 6} y={t.y + 13}>{short(t.node.name, Math.floor(t.w / 6.5))}</text>}
          {inner.map((c, j) => <g key={j}><rect x={c.x + 1} y={c.y + 1} width={Math.max(0, c.w - 2)} height={Math.max(0, c.h - 2)} rx={3} fillOpacity={0.75 - (j % 3) * 0.1} stroke="var(--panel)" style={{ fill: seriesColor(i) }}><title>{`${t.node.name} / ${c.node.name}: ${fmtNum(c.node.value)}`}</title></rect>
            {c.w > 52 && c.h > 18 && <text className="chart-lab inv" x={c.x + 5} y={c.y + 14}>{short(c.node.name, Math.floor(c.w / 6.5))}</text>}</g>)}
          {!t.node.children.length && t.w > 52 && t.h > 30 && <text className="chart-lab" x={t.x + 6} y={t.y + 30}>{fmtNum(t.node.value)}</text>}</g>); })}</>),
  };
}

function sunburst(spec: Spec): Built {
  const root = need2(spec), h = 340, cx = W / 2, cy = 170, rings = 4, rw = 140 / rings;
  const arcs: Any[] = [];
  const walk = (n: Node, depth: number, a0: number, a1: number, color: number) => {
    if (depth > 0 && depth <= rings) arcs.push(<path key={arcs.length} d={arcPath(cx, cy, 30 + (depth - 1) * rw, 30 + depth * rw - 1, a0, a1)} stroke="var(--panel)" strokeWidth={1.5} fillOpacity={1 - (depth - 1) * 0.16} style={{ fill: seriesColor(color) }}><title>{`${n.name}: ${fmtNum(n.value)}`}</title></path>);
    let a = a0;
    n.children.forEach((c, i) => { const span = ((a1 - a0) * c.value) / (n.value || 1); walk(c, depth + 1, a, a + span, depth === 0 ? i : color); a += span; });
  };
  walk(root, 0, -Math.PI / 2, -Math.PI / 2 + TAU, 0);
  return {
    h, table: mkTable(ttl(spec, "Sunburst"), ["Path", "Value"], flatten(root).map(([p, v]) => [p, v])),
    summary: `${ttl(spec, "Sunburst")}: ${root.name} of ${fmtNum(root.value)} split into ${root.children.map((c) => `${c.name} ${Math.round((c.value / root.value) * 100)}%`).join(", ")}`,
    legend: root.children.length > 1 ? root.children.slice(0, 8).map((c, i) => ({ name: c.name, color: seriesColor(i) })) : undefined,
    svg: (<>{arcs}<text className="chart-big" x={cx} y={cy + 2} textAnchor="middle">{short(root.name, 8)}</text><text className="chart-tick" x={cx} y={cy + 18} textAnchor="middle">{fmtNum(root.value)}</text></>),
  };
}

function tree(spec: Spec): Built {
  const root = need2(spec);
  const rows: { n: Node; d: number; y: number; parent: number }[] = [];
  let leaf = 0, maxD = 0;
  const place = (n: Node, d: number, parent: number): number => {
    const idx = rows.length; rows.push({ n, d, y: 0, parent }); maxD = Math.max(maxD, d);
    if (!n.children.length) rows[idx].y = leaf++;
    else { const ys = n.children.map((c) => place(c, d + 1, idx)); rows[idx].y = (rows[ys[0]].y + rows[ys[ys.length - 1]].y) / 2; }
    return idx;
  };
  place(root, 0, -1);
  const step = 26, h = Math.max(120, leaf * step + 30), colW = (W - 140) / Math.max(1, maxD), x = (d: number) => 20 + d * colW, y = (v: number) => 18 + v * step;
  return {
    h, table: mkTable(ttl(spec, "Tree"), ["Path", "Value"], flatten(root).map(([p, v]) => [p, v])),
    summary: `${ttl(spec, "Tree")}: ${rows.length} nodes under ${root.name}, ${maxD} levels deep`,
    svg: (<>{rows.map((r, i) => r.parent >= 0 && <path key={`e${i}`} d={`M${x(rows[r.parent].d)},${y(rows[r.parent].y)}C${x(rows[r.parent].d) + colW / 2},${y(rows[r.parent].y)} ${x(r.d) - colW / 2},${y(r.y)} ${x(r.d)},${y(r.y)}`} fill="none" className="viz-axis" strokeWidth={1.5} />)}
      {rows.map((r, i) => <g key={i}><circle cx={x(r.d)} cy={y(r.y)} r={5} className="chart-dot" style={{ fill: seriesColor(Math.min(r.d, 7)) }}><title>{`${r.n.name}${r.n.children.length ? "" : `: ${fmtNum(r.n.value)}`}`}</title></circle><text className="chart-lab" x={x(r.d) + 9} y={y(r.y) + 3}>{short(r.n.name, 22)}</text></g>)}</>),
  };
}

// ---------------------------------------------------------------------------------------------------- flows
function sankey(spec: Spec): Built {
  const links = arr(spec.links, 400).map((l) => ({ s: str(l?.source, 40), t: str(l?.target, 40), v: num(l?.value) })).filter((l) => l.s && l.t && l.s !== l.t && finite(l.v) && l.v > 0);
  need(links.length, "a Sankey chart needs links with a source, a target and a positive value");
  const names = [...new Set([...arr(spec.nodes, 200).map((n) => str(n?.name ?? n, 40)), ...links.flatMap((l) => [l.s, l.t])].filter(Boolean))];
  const layer = new Map(names.map((n) => [n, 0]));
  for (let pass = 0; pass < names.length; pass++) { let changed = false; for (const l of links) { if ((layer.get(l.t) ?? 0) <= (layer.get(l.s) ?? 0) && (layer.get(l.s) ?? 0) < names.length) { layer.set(l.t, (layer.get(l.s) ?? 0) + 1); changed = true; } } if (!changed) break; }
  const cols = Math.max(...layer.values()) + 1, inV = new Map<string, number>(), outV = new Map<string, number>();
  for (const l of links) { outV.set(l.s, (outV.get(l.s) ?? 0) + l.v); inV.set(l.t, (inV.get(l.t) ?? 0) + l.v); }
  const size = (n: string) => Math.max(inV.get(n) ?? 0, outV.get(n) ?? 0), byCol = Array.from({ length: cols }, (_, c) => names.filter((n) => layer.get(n) === c));
  const maxCol = Math.max(...byCol.map((col) => col.reduce((a, n) => a + size(n), 0))), maxN = Math.max(...byCol.map((c) => c.length)), h = Math.max(260, maxN * 34 + 40), pad = 10, scale = (h - 30 - pad * (maxN - 1)) / maxCol;
  const nw = 12, x = (c: number) => 80 + ((W - 80 - 100 - nw) * c) / Math.max(1, cols - 1), pos = new Map<string, { y: number; h: number; inO: number; outO: number }>();
  byCol.forEach((col) => { let y = 15; col.forEach((n) => { const hh = Math.max(3, size(n) * scale); pos.set(n, { y, h: hh, inO: 0, outO: 0 }); y += hh + pad; }); });
  const color = (n: string) => seriesColor(names.indexOf(n));
  return {
    h, table: mkTable(ttl(spec, "Sankey"), ["From", "To", "Value"], links.map((l) => [l.s, l.t, l.v])),
    summary: `${ttl(spec, "Sankey")}: ${links.length} flows between ${names.length} nodes, total out of the first stage ${fmtNum(byCol[0].reduce((a, n) => a + (outV.get(n) ?? 0), 0))}`,
    svg: (<>
      {links.map((l, i) => { const a = pos.get(l.s)!, b = pos.get(l.t)!, w = Math.max(1, l.v * scale), y0 = a.y + a.outO + w / 2, y1 = b.y + b.inO + w / 2; a.outO += w; b.inO += w;
        const x0 = x(layer.get(l.s) ?? 0) + nw, x1 = x(layer.get(l.t) ?? 0), mx = (x0 + x1) / 2;
        return <path key={i} d={`M${x0},${y0}C${mx},${y0} ${mx},${y1} ${x1},${y1}`} fill="none" strokeWidth={w} strokeOpacity={0.38} style={{ stroke: color(l.s) }}><title>{`${l.s} → ${l.t}: ${fmtNum(l.v)}`}</title></path>; })}
      {names.map((n, i) => { const p = pos.get(n)!, c = layer.get(n) ?? 0, last = c === cols - 1; return (
        <g key={n}><rect x={x(c)} y={p.y} width={nw} height={p.h} rx={2} style={{ fill: color(n) }}><title>{`${n}: ${fmtNum(size(n))}`}</title></rect>
          <text className="chart-lab" x={last ? x(c) - 6 : x(c) + nw + 6} y={p.y + p.h / 2 + 3} textAnchor={last ? "end" : "start"}>{short(n, 18)}</text></g>); })}
    </>),
  };
}

function network(spec: Spec): Built {
  const links = arr(spec.links, 600).map((l) => ({ s: str(l?.source, 40), t: str(l?.target, 40), v: Math.max(0.1, num(l?.value, 1)) })).filter((l) => l.s && l.t && l.s !== l.t);
  need(links.length, "a network chart needs links with a source and a target");
  const info = new Map<string, { label: string; group: string }>();
  for (const n of arr(spec.nodes, 150)) if (isObj(n)) info.set(str(n.id ?? n.name, 40), { label: str(n.label ?? n.id ?? n.name, 30), group: str(n.group ?? "", 30) });
  const ids = [...new Set([...info.keys(), ...links.flatMap((l) => [l.s, l.t])])].slice(0, 150), n = ids.length, h = 340;
  const pos = ids.map((_, i) => ({ x: Math.cos((i / n) * TAU) * 100 + (i % 3), y: Math.sin((i / n) * TAU) * 100 + (i % 5), vx: 0, vy: 0 })), ix = new Map(ids.map((id, i) => [id, i]));
  for (let it = 0; it < 220; it++) {                                  // a small deterministic force layout: nodes repel, links pull, everything drifts to the middle
    const cool = 1 - it / 220;
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) { const dx = pos[i].x - pos[j].x, dy = pos[i].y - pos[j].y, d2 = dx * dx + dy * dy + 0.01, f = (2600 / d2) * cool; pos[i].vx += dx * f; pos[i].vy += dy * f; pos[j].vx -= dx * f; pos[j].vy -= dy * f; }
    for (const l of links) { const a = pos[ix.get(l.s)!], b = pos[ix.get(l.t)!]; if (!a || !b) continue; const dx = b.x - a.x, dy = b.y - a.y, f = 0.02 * cool; a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f; }
    for (const p of pos) { p.vx -= p.x * 0.01; p.vy -= p.y * 0.01; p.x += Math.max(-8, Math.min(8, p.vx)); p.y += Math.max(-8, Math.min(8, p.vy)); p.vx *= 0.6; p.vy *= 0.6; }
  }
  const minX = Math.min(...pos.map((p) => p.x)), maxX = Math.max(...pos.map((p) => p.x)), minY = Math.min(...pos.map((p) => p.y)), maxY = Math.max(...pos.map((p) => p.y));
  const sx = (v: number) => 40 + ((v - minX) / (maxX - minX || 1)) * (W - 80), sy = (v: number) => 24 + ((v - minY) / (maxY - minY || 1)) * (h - 48);
  const deg = new Map<string, number>(); for (const l of links) { deg.set(l.s, (deg.get(l.s) ?? 0) + 1); deg.set(l.t, (deg.get(l.t) ?? 0) + 1); }
  const groups = groupColors(ids.map((id) => info.get(id)?.group || "Nodes"), 8);
  return {
    h, table: mkTable(ttl(spec, "Network"), ["From", "To", "Value"], links.map((l) => [l.s, l.t, l.v])),
    summary: `${ttl(spec, "Network")}: ${n} nodes and ${links.length} links; most connected ${[...deg.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? ""}`,
    legend: groups.legend.length > 1 ? groups.legend : undefined,
    svg: (<>
      {links.map((l, i) => { const a = pos[ix.get(l.s)!], b = pos[ix.get(l.t)!]; return a && b ? <line key={i} x1={sx(a.x)} y1={sy(a.y)} x2={sx(b.x)} y2={sy(b.y)} className="viz-axis" strokeWidth={Math.min(5, 1 + l.v / 4)}><title>{`${l.s} — ${l.t}${l.v !== 1 ? `: ${fmtNum(l.v)}` : ""}`}</title></line> : null; })}
      {ids.map((id, i) => <g key={id}><circle cx={sx(pos[i].x)} cy={sy(pos[i].y)} r={5 + Math.min(8, (deg.get(id) ?? 1))} className="chart-dot" style={{ fill: groups.color(info.get(id)?.group || "Nodes") }}><title>{`${info.get(id)?.label ?? id}: ${deg.get(id) ?? 0} links`}</title></circle>
        {n <= 40 && <text className="chart-lab" x={sx(pos[i].x)} y={sy(pos[i].y) - 11} textAnchor="middle">{short(info.get(id)?.label ?? id, 14)}</text>}</g>)}
    </>),
  };
}

// ---------------------------------------------------------------------------------------------------- radar and gauge
function radar(spec: Spec): Built {
  const axes = arr(spec.axes, 12).map((a) => ({ name: str(a?.name ?? a, 24), max: num(a?.max, NaN) }));
  const series = arr(spec.series, 6).map((s, i) => ({ name: str(s?.name ?? `Series ${i + 1}`, 30), values: axes.map((_, k) => num(arr(s?.values)[k], 0)) }));
  need(axes.length >= 3 && series.length, "a radar chart needs at least three axes and a series");
  const mx = axes.map((a, k) => (finite(a.max) ? a.max : Math.max(...series.map((s) => s.values[k]), 1e-9))), h = 330, cx = W / 2, cy = 166, R = 112, ang = (k: number) => -Math.PI / 2 + (k / axes.length) * TAU;
  const pt = (k: number, f: number) => polar(cx, cy, R * Math.max(0, Math.min(1, f)), ang(k));
  return {
    h, table: mkTable(ttl(spec, "Radar chart"), ["Axis", ...series.map((s) => s.name)], axes.map((a, k) => [a.name, ...series.map((s) => s.values[k])])),
    summary: `${ttl(spec, "Radar chart")}: ${series.map((s) => s.name).join(", ")} across ${axes.map((a) => a.name).join(", ")}`,
    legend: series.length > 1 ? series.map((s, i) => ({ name: s.name, color: seriesColor(i) })) : undefined,
    svg: (<>
      {[0.25, 0.5, 0.75, 1].map((f) => <polygon key={f} fill="none" className="viz-grid" points={axes.map((_, k) => pt(k, f).join(",")).join(" ")} />)}
      {axes.map((a, k) => { const [x, y] = pt(k, 1), [lx, ly] = pt(k, 1.12); return <g key={k}><line className="viz-grid" x1={cx} y1={cy} x2={x} y2={y} /><text className="chart-tick" x={lx} y={ly + 3} textAnchor={Math.abs(lx - cx) < 8 ? "middle" : lx > cx ? "start" : "end"}>{short(a.name, 14)}</text></g>; })}
      {series.map((s, i) => <polygon key={i} points={s.values.map((v, k) => pt(k, v / mx[k]).join(",")).join(" ")} fillOpacity={0.15} strokeWidth={2} style={{ fill: seriesColor(i), stroke: seriesColor(i) }}><title>{`${s.name}: ${s.values.map((v, k) => `${axes[k].name} ${fmtNum(v)}`).join(", ")}`}</title></polygon>)}
    </>),
  };
}

function gauge(spec: Spec): Built {
  const value = num(spec.value), lo = num(spec.min, 0), hi = num(spec.max, 100), unit = str(spec.unit, 8);
  need(finite(value) && hi > lo, "a gauge needs a value and a max above its min");
  const bands = arr(spec.bands, 6).map((b, i) => ({ to: num(b?.to), label: str(b?.label ?? `Band ${i + 1}`, 24) })).filter((b) => finite(b.to)).sort((a, b) => a.to - b.to), cx = W / 2, cy = 190, R = 150, r0 = 108, h = 240;
  const f = (v: number) => Math.max(0, Math.min(1, (v - lo) / (hi - lo))), ang = (v: number) => Math.PI + f(v) * Math.PI;
  const edges = bands.length ? bands : [{ to: hi, label: "Range" }];
  let prev = lo;
  const arcs = edges.map((b, i) => { const a0 = ang(prev), a1 = ang(Math.min(b.to, hi)); prev = b.to; return { ...b, a0, a1, color: seqColor(0.25 + (0.75 * (i + 1)) / edges.length) }; });
  const [nx, ny] = polar(cx, cy, r0 - 6, ang(value)), band = bands.find((b) => value <= b.to);
  return {
    h, table: mkTable(ttl(spec, "Gauge"), ["Band", "Up to"], [["Value", value], ...edges.map((b) => [b.label, b.to])]),
    summary: `${ttl(spec, "Gauge")}: ${fmtNum(value)}${unit} on a scale of ${fmtNum(lo)} to ${fmtNum(hi)}${band ? `, in the "${band.label}" band` : ""}`,
    legend: bands.length > 1 ? arcs.map((a) => ({ name: `${a.label} (to ${fmtNum(a.to)})`, color: a.color })) : undefined,
    svg: (<>
      {arcs.map((a, i) => <path key={i} d={arcPath(cx, cy, r0, R, a.a0, a.a1)} stroke="var(--panel)" strokeWidth={2} style={{ fill: a.color }}><title>{`${a.label}: up to ${fmtNum(a.to)}`}</title></path>)}
      <line x1={cx} y1={cy} x2={nx} y2={ny} strokeWidth={4} strokeLinecap="round" style={{ stroke: "var(--fg)" }} /><circle cx={cx} cy={cy} r={8} style={{ fill: "var(--fg)" }} />
      <text className="chart-big" x={cx} y={cy - 40} textAnchor="middle">{fmtNum(value)}{unit}</text>{band && <text className="chart-tick" x={cx} y={cy - 22} textAnchor="middle">{band.label}</text>}
      <text className="chart-tick" x={cx - (R + r0) / 2} y={cy + 16} textAnchor="middle">{fmtNum(lo)}</text><text className="chart-tick" x={cx + (R + r0) / 2} y={cy + 16} textAnchor="middle">{fmtNum(hi)}</text>
    </>),
  };
}

export const CHARTS2: Record<string, ReturnType<typeof chartComponent>> = {
  "chart.pie": chartComponent(pie), "chart.funnel": chartComponent(funnel), "chart.treemap": chartComponent(treemap), "chart.sunburst": chartComponent(sunburst), "chart.tree": chartComponent(tree),
  "chart.sankey": chartComponent(sankey), "chart.network": chartComponent(network), "chart.radar": chartComponent(radar), "chart.gauge": chartComponent(gauge),
};
