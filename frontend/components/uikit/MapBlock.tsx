"use client";
// The `map` component, drawn by MapLibre GL. It works offline: the land is a bundled Natural Earth outline (public domain) and nothing is requested from the internet unless you switch a
// basemap on in Settings (then the tile server you chose can see which area you look at, and the page says so). Layers: points, clusters, heatmaps, bubbles, coloured countries,
// GeoJSON, routes, arcs and hex bins, with a time slider, popups, a layer list, a table of every feature and exports (GeoJSON, KML, GPX, picture).
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Protocol } from "pmtiles";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { artifactUrl } from "@/lib/artifacts";
import { downloadFile, fileSafe } from "@/lib/tables";
import { downloadDataUrl } from "@/lib/uikit/exporters";
import { boundsOf, mapTable, popupText, readMap, toGeoJson, toGpx, toKml, type FC, type Layer, type MapModel } from "@/lib/uikit/maps";
import { fmtNum } from "@/lib/uikit/data";
import type { Spec } from "@/lib/uikit/safe";
import world from "@/lib/uikit/world-110m.json";
import UiFrame from "./UiFrame";
import useColors from "./useColors";

type Basemap = { kind: "none" | "tiles" | "pmtiles"; tile_url: string; attribution: string; pmtiles: string };
const NONE: Basemap = { kind: "none", tile_url: "", attribution: "", pmtiles: "" };
let protocolAdded = false, workerSet = false;

/** Mix two #rrggbb colours (a map layer needs real colours, not CSS variables). */
export function mix(a: string, b: string, t: number): string {
  const p = (h: string) => { const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(h.trim()); if (!m) return null; const s = m[1].length === 3 ? m[1].split("").map((c) => c + c).join("") : m[1]; return [0, 2, 4].map((i) => parseInt(s.slice(i, i + 2), 16)); };
  const x = p(a), y = p(b);
  return x && y ? `#${x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("")}` : b;
}

/** True when a #rrggbb colour is dark (the dark theme's panel), so land and sea can be drawn with enough contrast in either theme. */
export function isDark(hex: string): boolean {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return false;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
  return (0.299 * r + 0.587 * g + 0.114 * b) < 128;
}

const sourceOf = (l: Layer, data: FC) => ({ type: "geojson" as const, data, ...(l.kind === "cluster" ? { cluster: true, clusterRadius: 44, clusterMaxZoom: 10 } : {}) });

/** The MapLibre layers for one spec layer; returns their ids. */
function addLayers(map: maplibregl.Map, l: Layer, c: { series: string[]; panel: string; fg: string }): string[] {
  const color = c.series[l.color % 8], src = l.id, ids: string[] = [];
  const add = (layer: Any) => { map.addLayer({ ...layer, source: src }); ids.push(layer.id); };
  map.addSource(src, sourceOf(l, l.fc) as Any);
  if (l.kind === "points") add({ id: `${src}-pt`, type: "circle", paint: { "circle-radius": 6, "circle-color": color, "circle-stroke-color": c.panel, "circle-stroke-width": 2, "circle-opacity": l.opacity } });
  else if (l.kind === "bubble") {
    const vs = l.fc.features.map((f) => Number(f.properties.v)).filter(Number.isFinite), lo = vs.length ? Math.min(...vs) : 0, hi = vs.length ? Math.max(...vs, lo + 1e-9) : 1;
    add({ id: `${src}-bub`, type: "circle", paint: { "circle-radius": ["interpolate", ["linear"], ["coalesce", ["get", "v"], lo], lo, 5, hi, 26], "circle-color": color, "circle-opacity": 0.6, "circle-stroke-color": color, "circle-stroke-width": 1.5 } });
  } else if (l.kind === "cluster") {
    add({ id: `${src}-cl`, type: "circle", filter: ["has", "point_count"], paint: { "circle-color": color, "circle-opacity": 0.7, "circle-radius": ["step", ["get", "point_count"], 14, 10, 20, 50, 28], "circle-stroke-color": c.panel, "circle-stroke-width": 2 } });
    add({ id: `${src}-pt`, type: "circle", filter: ["!", ["has", "point_count"]], paint: { "circle-radius": 6, "circle-color": color, "circle-stroke-color": c.panel, "circle-stroke-width": 2 } });
  } else if (l.kind === "heat") add({ id: `${src}-heat`, type: "heatmap", paint: { "heatmap-weight": ["coalesce", ["get", "v"], 1], "heatmap-radius": 28, "heatmap-opacity": 0.85, "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"], 0, mix(color, c.panel, 1) + "00", 0.2, mix(c.panel, color, 0.35), 1, color] } });
  else if (l.kind === "choropleth") {
    const [lo, hi] = l.range ?? [0, 1], steps = l.steps ?? [lo, hi], ramp = (t: number) => mix(c.panel, color, 0.12 + 0.88 * t);
    const fill = steps.length === 2 ? ["interpolate", ["linear"], ["get", "v"], steps[0], ramp(0), steps[1] === steps[0] ? steps[0] + 1 : steps[1], ramp(1)] : ["step", ["get", "v"], ramp(0), ...steps.slice(1, -1).flatMap((b, i) => [b, ramp((i + 1) / (steps.length - 1))])];
    add({ id: `${src}-fill`, type: "fill", paint: { "fill-color": fill, "fill-opacity": l.opacity } });
    add({ id: `${src}-line`, type: "line", paint: { "line-color": c.panel, "line-width": 0.8 } });
  } else if (l.kind === "hexbin") {
    const ns = l.fc.features.map((f) => f.properties.n as number), hi = Math.max(...ns, 2);
    add({ id: `${src}-fill`, type: "fill", paint: { "fill-color": ["interpolate", ["linear"], ["get", "n"], 1, mix(c.panel, color, 0.2), hi, color], "fill-opacity": 0.8 } });
    add({ id: `${src}-line`, type: "line", paint: { "line-color": c.panel, "line-width": 0.6 } });
  } else if (l.kind === "route") add({ id: `${src}-line`, type: "line", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": color, "line-width": 3.5 } });
  else if (l.kind === "arc") {
    const vs = l.fc.features.map((f) => Number(f.properties.v)), hi = Math.max(...vs, 1);
    add({ id: `${src}-line`, type: "line", layout: { "line-cap": "round" }, paint: { "line-color": color, "line-opacity": 0.7, "line-width": ["interpolate", ["linear"], ["get", "v"], 0, 1.5, hi, 7] } });
  } else {                                                                      // geojson
    const kinds = new Set(l.fc.features.map((f) => String(f.geometry.type)));
    if ([...kinds].some((k) => k.endsWith("Polygon"))) {
      if (l.extrude) add({ id: `${src}-ext`, type: "fill-extrusion", filter: ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]], paint: { "fill-extrusion-color": color, "fill-extrusion-height": ["to-number", ["get", l.extrude], 0], "fill-extrusion-opacity": 0.8 } });
      else add({ id: `${src}-fill`, type: "fill", filter: ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]], paint: { "fill-color": color, "fill-opacity": 0.35 } });
      add({ id: `${src}-outline`, type: "line", filter: ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]], paint: { "line-color": color, "line-width": 1.5 } });
    }
    if ([...kinds].some((k) => k.endsWith("LineString"))) add({ id: `${src}-line`, type: "line", filter: ["in", ["geometry-type"], ["literal", ["LineString", "MultiLineString"]]], paint: { "line-color": color, "line-width": 3 } });
    if ([...kinds].some((k) => k.endsWith("Point"))) add({ id: `${src}-pt`, type: "circle", filter: ["in", ["geometry-type"], ["literal", ["Point", "MultiPoint"]]], paint: { "circle-radius": 6, "circle-color": color, "circle-stroke-color": c.panel, "circle-stroke-width": 2 } });
  }
  return ids;
}
type Any = any;

const timed = (l: Layer) => l.fc.features.some((f) => Number.isFinite(f.properties?.t));
const upTo = (l: Layer, t: number | null): FC => (t === null || !timed(l) ? l.fc : { type: "FeatureCollection", features: l.fc.features.filter((f) => !Number.isFinite(f.properties?.t) || f.properties.t <= t) });

export default function MapBlock({ spec }: { spec: Spec }) {
  const model: MapModel = useMemo(() => readMap(spec, world as unknown as FC), [spec]);
  const colors = useColors();
  const box = useRef<HTMLDivElement>(null), mapRef = useRef<maplibregl.Map | null>(null), ids = useRef<Record<string, string[]>>({});
  const [basemap, setBasemap] = useState<Basemap>(NONE);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [t, setT] = useState<number | null>(model.times ? model.times[1] : null);
  const [playing, setPlaying] = useState(false);
  const [err, setErr] = useState("");
  const tRef = useRef(t); tRef.current = t;
  const title = model.title || "Map";
  const summary = `${title}: ${model.layers.map((l) => `${l.name} (${l.kind}, ${l.fc.features.length} ${l.fc.features.length === 1 ? "feature" : "features"})`).join("; ")}${model.timeField ? ", with a time slider" : ""}`;

  useEffect(() => { api<{ basemap?: Basemap }>("/api/uikit/config").then((c) => setBasemap({ ...NONE, ...(c.basemap ?? {}) })).catch(() => setBasemap(NONE)); }, []);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    if (!workerSet) { maplibregl.setWorkerUrl?.(new URL("/vendor/maplibre/maplibre-gl-worker.mjs", window.location.href).href); workerSet = true; }       // the worker is served from this app (see public/vendor/maplibre)
    const style: Any = { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": isDark(colors.panel) ? mix(colors.panel, colors.axis, 0.06) : mix(colors.panel, colors.axis, 0.18) } }] };
    if (basemap.kind === "tiles") { style.sources.base = { type: "raster", tiles: [basemap.tile_url], tileSize: 256, attribution: basemap.attribution }; style.layers.push({ id: "base", type: "raster", source: "base" }); }
    if (basemap.kind === "pmtiles") {
      if (!protocolAdded) { maplibregl.addProtocol("pmtiles", new Protocol().tile); protocolAdded = true; }
      style.sources.base = { type: "raster", url: `pmtiles://${new URL(artifactUrl(basemap.pmtiles), window.location.href).href}`, tileSize: 256 };
      style.layers.push({ id: "base", type: "raster", source: "base" });
    }
    const b = boundsOf(model);
    const map = new maplibregl.Map({ container: el, style, center: model.center ?? [0, 20], zoom: model.zoom ?? 1, attributionControl: { compact: true }, canvasContextAttributes: { preserveDrawingBuffer: true } } as Any);
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    const popup = new maplibregl.Popup({ closeButton: true, maxWidth: "260px" });
    map.on("error", (e: Any) => setErr(String(e?.error?.message ?? "the map had a problem").slice(0, 160)));
    map.on("load", () => { try {
      if (basemap.kind === "none") {
        map.addSource("world", { type: "geojson", data: world as Any });
        map.addLayer({ id: "world-fill", type: "fill", source: "world", paint: { "fill-color": isDark(colors.panel) ? mix(colors.panel, colors.fg, 0.17) : mix(colors.panel, colors.axis, 0.05) } });
        map.addLayer({ id: "world-line", type: "line", source: "world", paint: { "line-color": isDark(colors.panel) ? mix(colors.panel, colors.fg, 0.4) : colors.axis, "line-width": 0.6 } });
      }
      const out: Record<string, string[]> = {};
      for (const l of model.layers) {
        out[l.id] = addLayers(map, l, colors);
        const src = map.getSource(l.id) as Any;
        if (timed(l) && src?.setData) src.setData(upTo(l, tRef.current));
        for (const lid of out[l.id]) {
          map.on("click", lid, (e: Any) => {
            const f = e.features?.[0];
            if (!f) return;
            const node = document.createElement("div");
            for (const line of popupText(f.properties, l.legend || l.name)) { const p = document.createElement("div"); p.textContent = line; node.appendChild(p); }
            if (f.properties?.point_count) node.textContent = `${f.properties.point_count} points`;
            popup.setLngLat(e.lngLat).setDOMContent(node).addTo(map);
          });
          map.on("mouseenter", lid, () => { map.getCanvas().style.cursor = "pointer"; });
          map.on("mouseleave", lid, () => { map.getCanvas().style.cursor = ""; });
        }
      }
      ids.current = out;
      el.dataset.ready = String(Object.values(out).flat().length);          // for tests and the screenshot script: the map finished drawing its layers
      if (model.projection === "globe") map.setProjection?.({ type: "globe" } as Any);
      if (!model.center && model.zoom === null && b) map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 36, maxZoom: 7, duration: 0 });
    } catch (e) { setErr(`the map could not draw its layers: ${(e as Error).message}`.slice(0, 200)); } });
    return () => { popup.remove(); map.remove(); mapRef.current = null; };
  }, [model, basemap.kind, basemap.tile_url, basemap.attribution, basemap.pmtiles, colors]);       // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {                                           // layer toggles
    const map = mapRef.current;
    for (const [lid, layerIds] of Object.entries(ids.current)) for (const id of layerIds) if (map?.getLayer(id)) map.setLayoutProperty(id, "visibility", hidden.has(lid) ? "none" : "visible");
  }, [hidden]);
  useEffect(() => {                                           // time slider
    const map = mapRef.current;
    if (t === null) return;
    for (const l of model.layers) if (timed(l)) (map?.getSource(l.id) as Any)?.setData?.(upTo(l, t));
  }, [t, model]);
  useEffect(() => {                                           // play
    if (!playing || !model.times) return;
    const [lo, hi] = model.times, step = (hi - lo) / 12 || 1;
    const h = setInterval(() => setT((cur) => { const n = (cur ?? lo) + step; if (n >= hi) { setPlaying(false); return hi; } return n; }), 700);
    return () => clearInterval(h);
  }, [playing, model.times]);

  const fit = () => { const b = boundsOf(model); if (b) mapRef.current?.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 36, maxZoom: 7 }); };
  const file = fileSafe(title, "map");
  const host = basemap.kind === "tiles" ? (() => { try { return new URL(basemap.tile_url.replace(/\{[^}]+\}/g, "0")).host; } catch { return "the tile server"; } })() : "";
  const toggle = (id: string) => setHidden((h) => { const n = new Set(h); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const toolbar = (
    <>
      <button className="ghost small" onClick={fit}>Fit to data</button>
      <button className="ghost small" onClick={() => box.current?.parentElement?.requestFullscreen?.()}>Full screen</button>
      {model.times && <><button className="ghost small" onClick={() => { if (t !== null && t >= model.times![1]) setT(model.times![0]); setPlaying((p) => !p); }}>{playing ? "Pause" : "Play"}</button>
        <input type="range" aria-label="Time" min={model.times[0]} max={model.times[1]} step={(model.times[1] - model.times[0]) / 100 || 1} value={t ?? model.times[1]} onChange={(e) => { setPlaying(false); setT(Number(e.target.value)); }} />
        <span className="mut">{new Date(t ?? model.times[1]).toISOString().slice(0, 10)}</span></>}
    </>
  );
  return (
    <UiFrame spec={spec} title={title} summary={summary} table={mapTable(model)} toolbar={toolbar} testId="map"
      foot={<>
        <span className="map-layers" role="group" aria-label="Layers">{model.layers.map((l) => <label key={l.id} className="check"><input type="checkbox" checked={!hidden.has(l.id)} onChange={() => toggle(l.id)} /> <i className="chart-swatch" suppressHydrationWarning style={{ background: colors.series[l.color % 8] }} />{l.name}</label>)}</span>
        {model.layers.filter((l) => l.kind === "choropleth" && l.range).map((l) => <span key={l.id} className="chart-ramp"><span>{fmtNum(l.range![0])}</span><i suppressHydrationWarning style={{ background: `linear-gradient(90deg, ${mix(colors.panel, colors.series[l.color % 8], 0.12)}, ${colors.series[l.color % 8]})` }} /><span>{fmtNum(l.range![1])}{l.legend ? ` ${l.legend}` : ""}</span></span>)}
        {model.layers.filter((l) => l.unmatched?.length).map((l) => <span key={`u${l.id}`} className="mut">No match on the map for: {l.unmatched!.join(", ")}.</span>)}
        {basemap.kind === "none" ? <span className="mut">Offline map: nothing is loaded from the internet.</span> : <span className="mut" role="note">Basemap tiles load from {basemap.kind === "tiles" ? host : "a file you saved"}{basemap.kind === "tiles" ? `, which can see which area you are looking at` : ""}.</span>}
        {err && <span className="err small" role="alert">{err}</span>}
      </>}
      exporters={[
        { label: "GeoJSON", run: () => downloadFile(`${file}.geojson`, toGeoJson(model), "application/geo+json") },
        { label: "KML", run: () => downloadFile(`${file}.kml`, toKml(model), "application/vnd.google-earth.kml+xml") },
        { label: "GPX (points and routes)", run: () => downloadFile(`${file}.gpx`, toGpx(model), "application/gpx+xml") },
        { label: "Picture (PNG)", run: () => { const url = mapRef.current?.getCanvas().toDataURL("image/png"); if (url) downloadDataUrl(`${file}.png`, url); } },
      ]}>
      <div ref={box} className="map-box" role="img" aria-label={summary} />
    </UiFrame>
  );
}
