import { describe, expect, it } from "vitest";
import world from "@/lib/uikit/world-110m.json";
import { arcCoords, boundsOf, classify, hexbin, joinChoropleth, mapTable, popupText, readMap, readValues, toGeoJson, toGpx, toKml, type FC } from "@/lib/uikit/maps";
import { SAMPLES } from "@/lib/uikit/samples.generated";

const W = world as unknown as FC;
const spec = (layers: any[], over: any = {}) => ({ type: "map", layers, ...over });
const pts = [{ lat: 30, lon: 31, label: "Cairo", detail: "HQ", value: 5, t: "2025-01-01" }, { lat: 51, lon: 0, label: "London", t: "2025-02-01" }, { lat: 999, lon: 0 }, { lat: "x", lon: 1 }];

describe("the bundled world outline", () => {
  it("has the countries with codes and names and nothing but a simplified outline", () => {
    expect(W.features.length).toBeGreaterThan(170);
    const egypt = W.features.find((f) => f.properties.a === "EGY")!; expect(egypt.properties.n).toBe("Egypt");
    expect(JSON.stringify(world).length).toBeLessThan(260_000);
  });
});

describe("reading a map spec", () => {
  it("reads points, clusters, heat and bubbles, dropping bad coordinates", () => {
    for (const kind of ["points", "cluster", "heat", "bubble"]) {
      const m = readMap(spec([{ kind, name: "P", points: pts }]), W);
      expect(m.layers[0].fc.features).toHaveLength(2); expect(m.layers[0].fc.features[0].properties).toMatchObject({ label: "Cairo", detail: "HQ", v: 5 });
    }
    const m = readMap(spec([{ kind: "points", points: [{ latitude: 1, lng: 2, name: "n", description: "d", size: 3, group: "g" }] }]), W);
    expect(m.layers[0].fc.features[0].properties).toMatchObject({ label: "n", detail: "d", v: 3, group: "g" });
  });
  it("finds a time range when points carry times", () => {
    const m = readMap(spec([{ kind: "points", points: pts }], { time: { field: "t" }, projection: "globe", zoom: 99, center: [1, 2], title: "T" }), W);
    expect(m.timeField).toBe(true); expect(m.times).toEqual([Date.UTC(2025, 0, 1), Date.UTC(2025, 1, 1)]); expect(m.projection).toBe("globe"); expect(m.zoom).toBe(18); expect(m.center).toEqual([1, 2]); expect(m.title).toBe("T");
    expect(readMap(spec([{ kind: "points", points: pts }]), W).timeField).toBe(false);
    expect(readMap(spec([{ kind: "points", points: [pts[0]] }], { center: [1, 999], zoom: "x" }), W)).toMatchObject({ center: null, zoom: null, projection: "mercator" });
  });
  it("joins a choropleth by code or name, reports misses and classifies", () => {
    const m = readMap(spec([{ kind: "choropleth", values: { USA: 10, france: 20, Nowhere: 5 }, classes: "quantile", legend: "k" }]), W);
    const l = m.layers[0]; expect(l.matched).toBe(2); expect(l.unmatched).toEqual(["nowhere"]); expect(l.range).toEqual([10, 20]); expect(l.steps).toHaveLength(6);
    const items = readMap(spec([{ kind: "choropleth", items: [{ code: "EGY", value: 3 }, { name: "Norway", value: "4" }, { value: 1 }] }]), W); expect(items.layers[0].matched).toBe(2);
    expect(readValues({ a: "x", b: 2 }).size).toBe(1); expect(readValues(null).size).toBe(0);
    expect(joinChoropleth(W, new Map()).matched).toBe(0);
    expect(classify([1, 2, 3, 4], "equal", 2)).toEqual([1, 2.5, 4]); expect(classify([1, 2, 3, 4], "x")).toEqual([1, 4]); expect(classify([5, 1, 3], "quantile", 2)).toEqual([1, 3, 5]);
  });
  it("reads routes, arcs, hex bins and GeoJSON", () => {
    const m = readMap(spec([
      { kind: "route", name: "R", path: [[0, 0], [1, 1], [2, "x"], [999, 1]] }, { kind: "route", routes: [{ name: "A", path: [[0, 0], [1, 1]] }, { path: [[0, 0]] }, { path: [[0, 0], [3, 3]] }] },
      { kind: "arc", arcs: [{ from: [0, 0], to: [10, 10], value: 4, label: "x" }, { from: [0, 0], to: [999, 1] }] },
      { kind: "hexbin", points: Array.from({ length: 30 }, (_, i) => ({ lat: 10 + (i % 5), lon: 20 + (i % 7) })), cell: 2 }, { kind: "hexbin", points: [{ lat: 1, lon: 1 }] },
      { kind: "geojson", data: { type: "FeatureCollection", features: [{ type: "Feature", properties: { a: 1, b: true, c: { x: 1 }, d: null }, geometry: { type: "Point", coordinates: [1, 2] } }, { type: "Feature", geometry: { type: "Nope", coordinates: [] } }, "x"] } },
      { kind: "geojson", data: { type: "Feature", geometry: { type: "LineString", coordinates: [[0, 0], [1, 1]] } } }, { kind: "geojson", data: { type: "Other" } }, { kind: "nonsense" }, { kind: "points", points: [] },
    ]), W);
    expect(m.layers.map((l) => l.kind)).toEqual(["route", "route", "arc", "hexbin", "hexbin", "geojson", "geojson"]);
    expect(m.layers[0].fc.features).toHaveLength(1); expect(m.layers[1].fc.features.map((f) => f.properties.label)).toEqual(["A", "Route 3"]); expect(m.layers[2].fc.features).toHaveLength(1);
    expect(m.layers[5].fc.features[0].properties).toEqual({ a: 1, b: true, c: "[object Object]", d: "" });
  });
  it("refuses a map with nothing on it", () => { expect(() => readMap(spec([{ kind: "points", points: [] }]), W)).toThrow(/at least one layer/); expect(() => readMap({ type: "map" }, W)).toThrow(); });
  it("reads the sample", () => { const m = readMap(SAMPLES.map, W); expect(m.layers.map((l) => l.kind)).toEqual(["choropleth", "points", "arc"]); });
});

describe("geometry helpers", () => {
  it("makes a curved arc between two places", () => {
    const a = arcCoords([0, 0], [10, 0], 8); expect(a).toHaveLength(9); expect(a[0]).toEqual([0, 0]); expect(a[8][0]).toBeCloseTo(10); expect(Math.abs(a[4][1])).toBeGreaterThan(0.5);
    expect(arcCoords([1, 1], [1, 1], 2)).toHaveLength(3);
  });
  it("bins points into hexagons and keeps them inside the map", () => {
    const fc = hexbin([{ lon: 0, lat: 89, label: "", detail: "", value: NaN, group: "", t: NaN }, { lon: 0.1, lat: 89, label: "", detail: "", value: NaN, group: "", t: NaN }, { lon: 50, lat: 1, label: "", detail: "", value: NaN, group: "", t: NaN }], 2);
    expect(fc.features.length).toBe(2); expect(fc.features.map((f) => f.properties.n).sort()).toEqual([1, 2]); expect(Math.max(...fc.features[0].geometry.coordinates[0].map((c: number[]) => c[1]))).toBeLessThanOrEqual(85);
  });
  it("finds bounds", () => {
    const m = readMap(spec([{ kind: "points", points: [{ lat: 10, lon: 20 }, { lat: 30, lon: 40 }] }, { kind: "route", path: [[0, 0], [5, 5]] }]), W);
    expect(boundsOf(m)).toEqual([0, 0, 40, 30]); expect(boundsOf({ ...m, layers: [] })).toBeNull();
  });
});

describe("exports", () => {
  const m = readMap(spec([{ kind: "points", name: "Offices", points: [{ lat: 30, lon: 31, label: "A & B <1>", detail: "d" }] }, { kind: "route", name: "R", routes: [{ name: "Trip", path: [[0, 0], [1, 1]] }] },
    { kind: "choropleth", name: "C", values: { EGY: 2 } }, { kind: "geojson", name: "G", data: { type: "Feature", geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] } } }], { title: "My <map>" }), W);
  it("makes a table with a centre for every feature", () => {
    const t = mapTable(m); expect(t.columns.map((c) => c.label)).toEqual(["Layer", "Name", "Longitude", "Latitude", "Value"]); expect(t.rows[0].slice(0, 4)).toEqual(["Offices", "A & B <1>", 31, 30]); expect(t.rows.length).toBe(4);
  });
  it("makes GeoJSON, KML and GPX that escape text", () => {
    const g = JSON.parse(toGeoJson(m)); expect(g.features[0].properties.layer).toBe("Offices");
    const k = toKml(m); expect(k).toContain("<name>A &amp; B &lt;1&gt;</name>"); expect(k).toContain("My &lt;map&gt;"); expect(k).toContain("<LineString>"); expect(k).toContain("<description>d</description>");
    const x = toGpx(m); expect(x).toContain('<wpt lat="30" lon="31">'); expect(x).toContain("<trk><name>Trip</name>"); expect(x).toContain('<trkpt lat="1" lon="1"/>');
    expect(toKml({ ...m, title: "" })).toContain("<name>Map</name>");
  });
  it("words popups", () => {
    expect(popupText({ label: "A", detail: "d", v: 12345 }, "Users")).toEqual(["A", "d", "Users: 12.3k"]); expect(popupText({ n: "Egypt", v: 3 })).toEqual(["Egypt", "3"]); expect(popupText({})).toEqual([]); expect(popupText({ v: "x" })).toEqual([]);
  });
});
