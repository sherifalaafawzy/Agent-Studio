import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as tables from "@/lib/tables";
import * as exporters from "@/lib/uikit/exporters";
import { SAMPLES } from "@/lib/uikit/samples.generated";
import { jsonResponse, routeFetch } from "../../helpers/http";

const { state, FakeMap, FakePopup } = vi.hoisted(() => {
  type Handler = (e?: any) => void;
  const state = { maps: [] as any[], popups: [] as any[], protocols: [] as any[], workerUrl: "" };
class FakeMap {
  sources: Record<string, any> = {}; layers: any[] = []; handlers: Record<string, Handler[]> = {}; layerHandlers: Record<string, Record<string, Handler>> = {}; removed = false; fit: any = null; projection: any = null; controls: any[] = [];
  canvas = { style: { cursor: "" }, toDataURL: vi.fn(() => "data:image/png;base64,QQ==") };
  constructor(public opts: any) { state.maps.push(this); }
  addControl(c: any) { this.controls.push(c); }
  on(type: string, a: any, b?: Handler) { if (b) (this.layerHandlers[a] ??= {})[type] = b; else (this.handlers[type] ??= []).push(a); }
  fire(type: string, e?: any) { (this.handlers[type] ?? []).forEach((h) => h(e)); }
  addSource(id: string, s: any) { this.sources[id] = { ...s, setData: vi.fn() }; }
  getSource(id: string) { return this.sources[id]; }
  addLayer(l: any) { this.layers.push(l); }
  getLayer(id: string) { return this.layers.find((l) => l.id === id); }
  setLayoutProperty = vi.fn();
  fitBounds(b: any, o: any) { this.fit = [b, o]; }
  setProjection(p: any) { this.projection = p; }
  getCanvas() { return this.canvas; }
  remove() { this.removed = true; }
}
class FakePopup { el: any = null; lngLat: any = null; added = false; constructor() { state.popups.push(this); } setLngLat(l: any) { this.lngLat = l; return this; } setDOMContent(e: any) { this.el = e; return this; } addTo() { this.added = true; return this; } remove() { /* nothing */ } }
  return { state, FakeMap, FakePopup };
});

vi.mock("maplibre-gl", () => ({ Map: FakeMap, NavigationControl: class { constructor(public o: any) { /* nothing */ } }, Popup: FakePopup, addProtocol: (...a: any[]) => state.protocols.push(a), setWorkerUrl: (u: string) => { state.workerUrl = u; } }));
vi.mock("maplibre-gl/dist/maplibre-gl.css", () => ({}));
vi.mock("pmtiles", () => ({ Protocol: class { tile = vi.fn(); } }));
vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));
vi.mock("@/lib/tables", async (orig) => ({ ...(await orig<typeof import("@/lib/tables")>()), downloadFile: vi.fn() }));
vi.mock("@/lib/uikit/exporters", async (orig) => ({ ...(await orig<typeof import("@/lib/uikit/exporters")>()), downloadDataUrl: vi.fn() }));

import UiBlock from "@/components/uikit/UiBlock";
import { mix } from "@/components/uikit/MapBlock";

const BASE = { basemap: { kind: "none", tile_url: "", attribution: "", pmtiles: "" } };
const draw = async (spec: any = SAMPLES.map, config: any = BASE) => {
  routeFetch({ "GET /api/uikit/config": () => jsonResponse(config) });
  const r = render(<UiBlock spec={spec} />); await screen.findByTestId("map");
  await waitFor(() => expect(state.maps.length).toBeGreaterThan(0));
  return { ...r, map: () => state.maps[state.maps.length - 1] as any };
};
const loaded = async (h: Awaited<ReturnType<typeof draw>>) => { await act(async () => { h.map().fire("load"); }); return h.map(); };

beforeEach(() => { state.maps.length = 0; state.popups.length = 0; state.protocols.length = 0; vi.mocked(tables.downloadFile).mockClear(); vi.mocked(exporters.downloadDataUrl).mockClear(); });

describe("the map component", () => {
  it("serves the worker from this app and draws the bundled world offline", async () => {
    const h = await draw(); const map = await loaded(h);
    expect(state.workerUrl).toMatch(/\/vendor\/maplibre\/maplibre-gl-worker\.mjs$/);
    expect(map.opts.style.sources).toEqual({}); expect(map.sources.world).toBeTruthy(); expect(map.layers.map((l: any) => l.id)).toEqual(expect.arrayContaining(["world-fill", "world-line", "l0-fill", "l0-line", "l1-pt", "l2-line"]));
    expect(map.controls).toHaveLength(1); expect(screen.getByText(/nothing is loaded from the internet/)).toBeInTheDocument(); expect(screen.getByTestId("map").querySelector(".map-box")).toHaveAttribute("data-ready", "4");
    expect(map.fit).toBeNull();                       // the sample gives its own centre and zoom
  });
  it("fits to the data when no view is given", async () => {
    const h = await draw({ type: "map", layers: [{ kind: "points", points: [{ lat: 1, lon: 2 }, { lat: 3, lon: 4 }] }] }); const map = await loaded(h);
    expect(map.fit[0]).toEqual([[2, 1], [4, 3]]); await userEvent.click(screen.getByRole("button", { name: "Fit to data" })); expect(map.fit[1].duration).toBeUndefined();
  });
  it("draws every kind of layer", async () => {
    const spec = { type: "map", projection: "globe", layers: [
      { kind: "points", points: [{ lat: 1, lon: 1 }] }, { kind: "bubble", points: [{ lat: 1, lon: 1, value: 3 }, { lat: 2, lon: 2 }] }, { kind: "bubble", points: [{ lat: 1, lon: 1 }] }, { kind: "cluster", points: [{ lat: 1, lon: 1 }] }, { kind: "heat", points: [{ lat: 1, lon: 1 }] },
      { kind: "choropleth", values: { USA: 1, FRA: 2, EGY: 3, DEU: 4 }, classes: "equal" }, { kind: "choropleth", values: { USA: 1 } }, { kind: "hexbin", points: [{ lat: 1, lon: 1 }, { lat: 1.1, lon: 1.1 }] },
      { kind: "route", path: [[0, 0], [1, 1]] }, { kind: "arc", arcs: [{ from: [0, 0], to: [5, 5] }] },
      { kind: "geojson", data: { type: "FeatureCollection", features: [{ type: "Feature", properties: { h: 5 }, geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] } }, { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [[0, 0], [1, 1]] } }, { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [0, 0] } }] } },
      { kind: "geojson", extrude: "h", data: { type: "Feature", properties: { h: 5 }, geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] } } }] };
    const h = await draw(spec); const map = await loaded(h);
    const types = map.layers.map((l: any) => `${l.type}`); expect(types).toEqual(expect.arrayContaining(["circle", "heatmap", "fill", "line", "fill-extrusion"]));
    expect(map.projection).toEqual({ type: "globe" }); expect(map.sources.l3.cluster).toBe(true);
    expect(map.layers.find((l: any) => l.id === "l5-fill").paint["fill-color"][0]).toBe("step"); expect(map.layers.find((l: any) => l.id === "l6-fill").paint["fill-color"][0]).toBe("interpolate");
  });
  it("shows popups, pointer cursors and cluster counts", async () => {
    const h = await draw({ type: "map", layers: [{ kind: "cluster", name: "C", legend: "Things", points: [{ lat: 1, lon: 1, label: "L", detail: "D", value: 4 }] }] }); const map = await loaded(h);
    const hl = map.layerHandlers["l0-pt"]; hl.mouseenter(); expect(map.canvas.style.cursor).toBe("pointer"); hl.mouseleave(); expect(map.canvas.style.cursor).toBe("");
    hl.click({ features: [] }); expect(state.popups[0].added).toBe(false);
    hl.click({ features: [{ properties: { label: "L", detail: "D", v: 4 } }], lngLat: [1, 1] });
    expect(state.popups[0].el.textContent).toBe("LDThings: 4"); expect(state.popups[0].added).toBe(true);
    map.layerHandlers["l0-cl"].click({ features: [{ properties: { point_count: 12 } }], lngLat: [1, 1] }); expect(state.popups[0].el.textContent).toBe("12 points");
  });
  it("toggles layers from the list", async () => {
    const h = await draw(); const map = await loaded(h);
    await userEvent.click(screen.getByRole("checkbox", { name: /Offices/ }));
    expect(map.setLayoutProperty).toHaveBeenCalledWith("l1-pt", "visibility", "none");
    await userEvent.click(screen.getByRole("checkbox", { name: /Offices/ }));
    expect(map.setLayoutProperty).toHaveBeenCalledWith("l1-pt", "visibility", "visible");
  });
  it("has a time slider that filters features and a play button", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const spec = { type: "map", time: { field: "t" }, layers: [{ kind: "points", points: [{ lat: 1, lon: 1, t: "2025-01-01" }, { lat: 2, lon: 2, t: "2025-06-01" }, { lat: 3, lon: 3 }] }] };
    const h = await draw(spec); const map = await loaded(h);
    expect(map.sources.l0.setData).toHaveBeenCalled(); const slider = screen.getByLabelText("Time") as HTMLInputElement;
    const lo = Number(slider.min); await act(async () => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!; set.call(slider, String(lo)); slider.dispatchEvent(new Event("input", { bubbles: true })); });
    const last = map.sources.l0.setData.mock.calls.at(-1)[0]; expect(last.features.map((f: any) => f.properties.i)).toEqual([0, 2]);
    await userEvent.click(screen.getByRole("button", { name: "Play" })); expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(700 * 14); });
    await waitFor(() => expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Play" }));            // at the end: starts again from the beginning
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    vi.useRealTimers();
  });
  it("exports GeoJSON, KML, GPX and a picture, and shows the table of features", async () => {
    const h = await draw(); await loaded(h);
    for (const [label, name] of [["GeoJSON", "Offices and routes.geojson"], ["KML", "Offices and routes.kml"], ["GPX (points and routes)", "Offices and routes.gpx"]] as const) {
      await userEvent.click(screen.getByRole("button", { name: label })); expect(tables.downloadFile).toHaveBeenLastCalledWith(name, expect.any(String), expect.any(String));
    }
    await userEvent.click(screen.getByRole("button", { name: "Picture (PNG)" })); expect(exporters.downloadDataUrl).toHaveBeenCalledWith("Offices and routes.png", "data:image/png;base64,QQ==");
    await userEvent.click(screen.getByRole("button", { name: "Show as table" })); expect(screen.getByTestId("data-table")).toHaveTextContent("Cairo");
  });
  it("exports no picture when the map is gone and survives full screen being unavailable", async () => {
    const h = await draw(); const map = await loaded(h); map.canvas.toDataURL.mockReturnValueOnce("" as any);
    await userEvent.click(screen.getByRole("button", { name: "Picture (PNG)" })); expect(exporters.downloadDataUrl).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));
    const req = vi.fn(); Object.defineProperty(screen.getByTestId("map"), "requestFullscreen", { value: req, configurable: true });
    await userEvent.click(screen.getByRole("button", { name: "Full screen" })); expect(req).toHaveBeenCalled();
  });
  it("reports a map error and a drawing failure", async () => {
    const h = await draw(); h.map().fire("error", { error: { message: "tile failed" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("tile failed"); h.map().fire("error", {}); await screen.findByText(/the map had a problem/);
    h.map().addSource = () => { throw new Error("bad style"); }; await act(async () => { h.map().fire("load"); });
    expect(await screen.findByText(/could not draw its layers: bad style/)).toBeInTheDocument();
  });
  it("uses a tile basemap only when you switched one on and says who can see where you look", async () => {
    const h = await draw(SAMPLES.map, { basemap: { kind: "tiles", tile_url: "https://tiles.example.com/{z}/{x}/{y}.png", attribution: "© Example", pmtiles: "" } });
    await waitFor(() => expect(state.maps.length).toBe(2));
    const map = h.map(); expect(map.opts.style.sources.base).toMatchObject({ type: "raster", tiles: ["https://tiles.example.com/{z}/{x}/{y}.png"], attribution: "© Example" });
    await act(async () => { map.fire("load"); }); expect(map.sources.world).toBeUndefined();
    expect(screen.getByRole("note")).toHaveTextContent("tiles.example.com, which can see which area you are looking at");
  });
  it("uses a PMTiles file from the artifact store and registers the protocol once", async () => {
    const h = await draw(SAMPLES.map, { basemap: { kind: "pmtiles", tile_url: "", attribution: "", pmtiles: "0123456789ab" } });
    await waitFor(() => expect(state.maps.length).toBe(2));
    expect(h.map().opts.style.sources.base.url).toMatch(/^pmtiles:\/\/.*\/api\/artifacts\/0123456789ab\/content/); expect(screen.getByRole("note")).toHaveTextContent("a file you saved");
    expect(state.protocols.length).toBeLessThanOrEqual(1);
  });
  it("falls back to the offline map when the settings cannot be read, and cleans up", async () => {
    routeFetch({ "GET /api/uikit/config": () => jsonResponse({ detail: "no" }, { status: 500 }) });
    const { unmount } = render(<UiBlock spec={SAMPLES.map} />); await screen.findByTestId("map");
    await waitFor(() => expect(state.maps.length).toBeGreaterThan(0)); const map = state.maps.at(-1);
    expect(screen.getByText(/Offline map/)).toBeInTheDocument(); unmount(); expect(map.removed).toBe(true);
  });
  it("names an unparseable tile address and shows what matched nothing", async () => {
    await draw({ type: "map", layers: [{ kind: "choropleth", values: { USA: 1, Zzz: 2 } }] }, { basemap: { kind: "tiles", tile_url: "not a url", attribution: "x", pmtiles: "" } });
    await waitFor(() => expect(state.maps.length).toBe(2)); expect(screen.getByRole("note")).toHaveTextContent("the tile server"); expect(screen.getByText(/No match on the map for: zzz/)).toBeInTheDocument();
  });
  it("redraws when the theme changes", async () => {
    await draw(); const before = state.maps.length;
    await act(async () => { document.documentElement.setAttribute("data-theme", "dark"); await Promise.resolve(); });
    await waitFor(() => expect(state.maps.length).toBeGreaterThan(before));
  });
});

describe("colour mixing", () => {
  it("mixes hex colours and falls back for anything else", () => {
    expect(mix("#000000", "#ffffff", 0.5)).toBe("#808080"); expect(mix("#000", "#fff", 1)).toBe("#ffffff"); expect(mix("red", "#fff", 0.5)).toBe("#fff");
  });
});

describe("the vendored worker", () => {
  it("is the same file as the installed MapLibre's, so an upgrade must refresh it", () => {
    const a = readFileSync("node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs"), b = readFileSync("public/vendor/maplibre/maplibre-gl-worker.mjs");
    expect(b.equals(a)).toBe(true);
  });
});
