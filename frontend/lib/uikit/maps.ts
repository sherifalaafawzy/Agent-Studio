// Maps: reading a map spec into GeoJSON layers, joining values to the bundled world outline, binning, great-circle-style arcs, classification and the exports
// (GeoJSON, CSV table, KML, GPX). Pure functions; the drawing is in components/uikit/MapBlock.tsx. Coordinates are [longitude, latitude].
import { parseTable, type Table } from "@/lib/tables";
import { Any, arr, finite, fmtNum, isObj, num, str, toTime } from "./data";

export type FC = { type: "FeatureCollection"; features: Any[] };
export type Kind = "points" | "cluster" | "heat" | "bubble" | "choropleth" | "geojson" | "route" | "arc" | "hexbin";
export const KINDS: Kind[] = ["points", "cluster", "heat", "bubble", "choropleth", "geojson", "route", "arc", "hexbin"];
export type Layer = { id: string; kind: Kind; name: string; color: number; fc: FC; legend: string; opacity: number; extrude: string; matched?: number; unmatched?: string[]; range?: [number, number]; steps?: number[]; note?: string };
export type MapModel = { center: [number, number] | null; zoom: number | null; projection: "mercator" | "globe"; layers: Layer[]; timeField: boolean; times: [number, number] | null; title: string };

const MAX_FEATURES = 5000;
const point = (lon: number, lat: number, props: Any = {}): Any => ({ type: "Feature", properties: props, geometry: { type: "Point", coordinates: [lon, lat] } });
const validLL = (lon: number, lat: number) => finite(lon) && finite(lat) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180;

type P = { lon: number; lat: number; label: string; detail: string; value: number; group: string; t: number };
function readPoints(raw: unknown): P[] {
  return arr(raw, MAX_FEATURES).map((p) => ({ lon: num(p?.lon ?? p?.lng ?? p?.longitude), lat: num(p?.lat ?? p?.latitude), label: str(p?.label ?? p?.name, 60), detail: str(p?.detail ?? p?.description, 200), value: num(p?.value ?? p?.size ?? p?.weight, NaN), group: str(p?.group, 30), t: toTime(p?.t ?? p?.time ?? p?.date) }))
    .filter((p) => validLL(p.lon, p.lat));
}
const pointFeature = (p: P, i: number) => point(p.lon, p.lat, { label: p.label, detail: p.detail, v: finite(p.value) ? p.value : null, group: p.group, i, ...(finite(p.t) ? { t: p.t } : {}) });

/** The values of a choropleth as a key → number map. Accepts {KEY: n} or [{code|name, value}]. */
export function readValues(raw: unknown): Map<string, number> {
  const m = new Map<string, number>();
  if (isObj(raw)) for (const [k, v] of Object.entries(raw).slice(0, 400)) { const n = num(v); if (finite(n)) m.set(k.trim().toLowerCase(), n); }
  else for (const r of arr(raw, 400)) { const n = num(r?.value), k = str(r?.code ?? r?.iso ?? r?.name ?? r?.country, 60).trim().toLowerCase(); if (k && finite(n)) m.set(k, n); }
  return m;
}

/** Join values to the world outline by ISO-3 code or country name. Returns the matched countries (with their value) and the keys that matched nothing. */
export function joinChoropleth(world: FC, values: Map<string, number>): { fc: FC; matched: number; unmatched: string[] } {
  const used = new Set<string>(), feats: Any[] = [];
  for (const f of world.features) {
    const keys = [String(f.properties.a).toLowerCase(), String(f.properties.n).toLowerCase()], hit = keys.find((k) => values.has(k));
    if (hit) { used.add(hit); feats.push({ type: "Feature", properties: { n: f.properties.n, a: f.properties.a, v: values.get(hit) }, geometry: f.geometry }); }
  }
  return { fc: { type: "FeatureCollection", features: feats }, matched: feats.length, unmatched: [...values.keys()].filter((k) => !used.has(k)).slice(0, 20) };
}

/** Class break values for a choropleth: `linear` is a continuous scale (just min and max); `equal` and `quantile` give n+1 edges. */
export function classify(values: number[], mode: string, n = 5): number[] {
  const v = [...values].sort((a, b) => a - b), lo = v[0], hi = v[v.length - 1];
  if (mode === "quantile") return Array.from({ length: n + 1 }, (_, i) => v[Math.min(v.length - 1, Math.round((i / n) * (v.length - 1)))]);
  if (mode === "equal") return Array.from({ length: n + 1 }, (_, i) => lo + ((hi - lo) * i) / n);
  return [lo, hi];
}

/** A curved line between two places (a quadratic curve in longitude/latitude, lifted to one side) for flow maps. */
export function arcCoords(from: [number, number], to: [number, number], steps = 32): [number, number][] {
  const dx = to[0] - from[0], dy = to[1] - from[1], dist = Math.hypot(dx, dy), lift = dist * 0.22, cx = (from[0] + to[0]) / 2 - (dy / (dist || 1)) * lift, cy = (from[1] + to[1]) / 2 + (dx / (dist || 1)) * lift;
  return Array.from({ length: steps + 1 }, (_, i) => { const t = i / steps; return [(1 - t) ** 2 * from[0] + 2 * (1 - t) * t * cx + t * t * to[0], (1 - t) ** 2 * from[1] + 2 * (1 - t) * t * cy + t * t * to[1]] as [number, number]; });
}

/** Bin points into hexagons of about `cell` degrees; each hexagon carries its count. */
export function hexbin(points: P[], cell: number): FC {
  const h = cell, w = (Math.sqrt(3) / 2) * 2 * h, bins = new Map<string, { n: number; x: number; y: number }>();
  for (const p of points) {
    const row = Math.round(p.lat / (1.5 * h)), off = row % 2 ? w / 2 : 0, col = Math.round((p.lon - off) / w), key = `${col}:${row}`, b = bins.get(key) ?? { n: 0, x: col * w + off, y: row * 1.5 * h };
    b.n++; bins.set(key, b);
  }
  return { type: "FeatureCollection", features: [...bins.values()].map((b) => ({ type: "Feature", properties: { n: b.n }, geometry: { type: "Polygon", coordinates: [Array.from({ length: 7 }, (_, i) => { const a = (Math.PI / 180) * (60 * i - 30); return [b.x + h * Math.cos(a), Math.max(-85, Math.min(85, b.y + h * Math.sin(a)))]; })] } })) };
}

const GEOMS = new Set(["Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"]);
function readGeo(raw: unknown): FC {
  const r = raw as Any, feats = r?.type === "FeatureCollection" ? arr(r.features, MAX_FEATURES) : r?.type === "Feature" ? [r] : [];
  return { type: "FeatureCollection", features: feats.filter((f) => isObj(f) && isObj(f.geometry) && GEOMS.has(f.geometry.type) && Array.isArray(f.geometry.coordinates)).map((f) => ({ type: "Feature", properties: isObj(f.properties) ? Object.fromEntries(Object.entries(f.properties).slice(0, 30).map(([k, v]) => [k, typeof v === "number" || typeof v === "boolean" ? v : String(v ?? "").slice(0, 200)])) : {}, geometry: { type: f.geometry.type, coordinates: f.geometry.coordinates } })) };
}

/** Read a map spec. `world` is the bundled outline (needed for choropleths). Throws when nothing can be drawn. */
export function readMap(spec: Any, world: FC): MapModel {
  const layers: Layer[] = [];
  arr(spec.layers, 12).forEach((l, i) => {
    const kind = str(l?.kind, 20) as Kind;
    if (!KINDS.includes(kind)) return;
    const base = { id: `l${i}`, kind, name: str(l?.name ?? kind, 40), color: i, legend: str(l?.legend, 60), opacity: Math.min(1, Math.max(0.1, num(l?.opacity, 0.85))), extrude: str(l?.extrude, 30) };
    let fc: FC = { type: "FeatureCollection", features: [] }, extra: Partial<Layer> = {};
    if (kind === "points" || kind === "cluster" || kind === "heat" || kind === "bubble") fc = { type: "FeatureCollection", features: readPoints(l.points).map(pointFeature) };
    else if (kind === "hexbin") { const pts = readPoints(l.points), lons = pts.map((p) => p.lon), span = pts.length ? Math.max(Math.max(...lons) - Math.min(...lons), 1) : 1; fc = hexbin(pts, num(l.cell, span / 12)); }
    else if (kind === "route") {
      const routes = Array.isArray(l.routes) ? arr(l.routes, 50) : [{ name: l.name, path: l.path }];
      fc = { type: "FeatureCollection", features: routes.map((r, j) => ({ type: "Feature", properties: { label: str(r?.name, 60) || `Route ${j + 1}` }, geometry: { type: "LineString", coordinates: arr(r?.path, 5000).map((c) => [num(c?.[0]), num(c?.[1])]).filter((c) => validLL(c[0], c[1])) } })).filter((f) => f.geometry.coordinates.length > 1) };
    } else if (kind === "arc") fc = { type: "FeatureCollection", features: arr(l.arcs, 1000).map((a) => ({ f: [num(a?.from?.[0]), num(a?.from?.[1])] as [number, number], t: [num(a?.to?.[0]), num(a?.to?.[1])] as [number, number], v: num(a?.value, 1), label: str(a?.label, 60) })).filter((a) => validLL(...a.f) && validLL(...a.t)).map((a) => ({ type: "Feature", properties: { label: a.label, v: a.v }, geometry: { type: "LineString", coordinates: arcCoords(a.f, a.t) } })) };
    else if (kind === "geojson") fc = readGeo(l.data);
    else {
      const j = joinChoropleth(world, readValues(l.values ?? l.items)), vals = j.fc.features.map((f) => f.properties.v as number);
      fc = j.fc; extra = { matched: j.matched, unmatched: j.unmatched, range: vals.length ? [Math.min(...vals), Math.max(...vals)] : undefined, steps: vals.length ? classify(vals, str(l.classes, 10), 5) : undefined };
    }
    if (fc.features.length) layers.push({ ...base, fc, ...extra });
  });
  if (!layers.length) throw new Error("a map needs at least one layer with places on it");
  const times = layers.flatMap((l) => l.fc.features.map((f) => f.properties?.t as number).filter(finite));
  const c = Array.isArray(spec.center) ? [num(spec.center[0]), num(spec.center[1])] : null;
  return { center: c && validLL(c[0], c[1]) ? [c[0], c[1]] : null, zoom: finite(num(spec.zoom)) ? Math.max(0, Math.min(18, num(spec.zoom))) : null, projection: spec.projection === "globe" ? "globe" : "mercator", layers, timeField: !!spec.time && times.length > 0, times: times.length ? [Math.min(...times), Math.max(...times)] : null, title: str(spec.title, 80) };
}

function walk(c: Any, out: [number, number][]) { if (typeof c[0] === "number") out.push([c[0], c[1]]); else for (const x of c) walk(x, out); }
/** [west, south, east, north] around everything drawn (the choropleth countries count), or null. */
export function boundsOf(m: MapModel): [number, number, number, number] | null {
  const pts: [number, number][] = [];
  for (const l of m.layers) for (const f of l.fc.features) walk(f.geometry.coordinates, pts);
  if (!pts.length) return null;
  return [Math.min(...pts.map((p) => p[0])), Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[1]))];
}

const center = (g: Any): [number, number] => { const pts: [number, number][] = []; walk(g.coordinates, pts); return [pts.reduce((a, p) => a + p[0], 0) / pts.length, pts.reduce((a, p) => a + p[1], 0) / pts.length]; };

/** The features of a map as a table: layer, name, longitude, latitude (the centre for shapes), value. */
export function mapTable(m: MapModel): Table {
  const rows: Any[][] = [];
  for (const l of m.layers) for (const f of l.fc.features.slice(0, 5000)) { const p = f.properties ?? {}, [lon, lat] = center(f.geometry); rows.push([l.name, str(p.label ?? p.n ?? p.name ?? "", 60), +lon.toFixed(4), +lat.toFixed(4), p.v ?? p.n ?? null]); }
  return parseTable({ columns: [{ key: "layer", label: "Layer" }, { key: "name", label: "Name" }, { key: "lon", label: "Longitude", type: "number" }, { key: "lat", label: "Latitude", type: "number" }, { key: "v", label: "Value" }], rows }, m.title || "Map").table!;
}

export const toGeoJson = (m: MapModel): string => JSON.stringify({ type: "FeatureCollection", features: m.layers.flatMap((l) => l.fc.features.map((f) => ({ ...f, properties: { ...f.properties, layer: l.name } }))) }, null, 1);

const xml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
/** KML for points and lines (Google Earth and most map apps open it). */
export function toKml(m: MapModel): string {
  const marks = m.layers.flatMap((l) => l.fc.features.filter((f) => f.geometry.type === "Point" || f.geometry.type === "LineString").map((f) => {
    const name = xml(str(f.properties?.label ?? f.properties?.n ?? l.name, 60)), g = f.geometry.type === "Point" ? `<Point><coordinates>${f.geometry.coordinates[0]},${f.geometry.coordinates[1]}</coordinates></Point>` : `<LineString><coordinates>${f.geometry.coordinates.map((c: number[]) => `${c[0]},${c[1]}`).join(" ")}</coordinates></LineString>`;
    return `<Placemark><name>${name}</name>${f.properties?.detail ? `<description>${xml(String(f.properties.detail))}</description>` : ""}${g}</Placemark>`;
  }));
  return `<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>${xml(m.title || "Map")}</name>${marks.join("")}</Document></kml>`;
}

/** GPX with a waypoint for each point and a track for each route. */
export function toGpx(m: MapModel): string {
  const wpt = m.layers.flatMap((l) => l.fc.features.filter((f) => f.geometry.type === "Point").map((f) => `<wpt lat="${f.geometry.coordinates[1]}" lon="${f.geometry.coordinates[0]}"><name>${xml(str(f.properties?.label ?? l.name, 60))}</name></wpt>`));
  const trk = m.layers.filter((l) => l.kind === "route").flatMap((l) => l.fc.features.map((f) => `<trk><name>${xml(str(f.properties?.label, 60))}</name><trkseg>${f.geometry.coordinates.map((c: number[]) => `<trkpt lat="${c[1]}" lon="${c[0]}"/>`).join("")}</trkseg></trk>`));
  return `<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="Agent Studio" xmlns="http://www.topografix.com/GPX/1/1">${wpt.join("")}${trk.join("")}</gpx>`;
}

/** Text for a map popup: the label and detail of a feature. */
export const popupText = (p: Any, v?: string): string[] => [str(p?.label ?? p?.n, 80), str(p?.detail, 200), p?.v !== null && p?.v !== undefined && finite(Number(p.v)) ? `${v ? v + ": " : ""}${fmtNum(Number(p.v))}` : ""].filter(Boolean);
