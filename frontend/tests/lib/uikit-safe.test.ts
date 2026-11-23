import { describe, expect, it } from "vitest";
import { KINDS, canonical, normalize, safeUrl } from "@/lib/uikit/safe";
import { SAMPLES } from "@/lib/uikit/samples.generated";
import { tableFromAnything } from "@/lib/uikit/fallback";

describe("cleaning a component spec in the browser", () => {
  it("accepts every built-in sample unchanged in type", () => {
    for (const [type, sample] of Object.entries(SAMPLES)) { const r = normalize(sample); expect(r.error, type).toBeUndefined(); expect(r.spec!.type).toBe(type); }
    expect(KINDS.size).toBe(Object.keys(SAMPLES).length);
  });
  it("resolves names", () => {
    expect(canonical("Chart/Line")).toBe("chart.line"); expect(canonical("chart", { kind: "pie" })).toBe("chart.pie"); expect(canonical("chart", { kind: "zzz" })).toBeNull(); expect(canonical(undefined)).toBeNull();
  });
  it("refuses what is not a component", () => {
    expect(normalize("{bad").error).toMatch(/not valid JSON/); expect(normalize([1]).error).toMatch(/JSON object/); expect(normalize(null).error).toMatch(/JSON object/);
    expect(normalize({ type: "nope" }).error).toMatch(/unknown component type "nope"/); expect(normalize({}).error).toMatch(/unknown component type ""/);
    expect(normalize({ type: "chart.bar" }).error).toMatch(/needs "x" \+ "series" or "data"/);
    expect(normalize({ type: "chart.bar", x: [], series: [], data: {} }).error).toMatch(/needs/);
    expect(normalize(JSON.stringify(SAMPLES["card.kpi"])).spec!.type).toBe("card.kpi");
  });
  it("drops behaviour-carrying keys, limits strings and numbers, and cuts deep nesting", () => {
    let deep: any = "bottom"; for (let i = 0; i < 14; i++) deep = { n: deep };
    const r = normalize({ type: "json", value: { onclick: "x", style: "y", ok: "a\u0000b", n: NaN, deep, big: "z".repeat(30000), html: "<b>", list: [1, null, true], obj: Symbol.for("q") } }).spec!;
    const v = r.value;
    expect(v.onclick).toBeUndefined(); expect(v.style).toBeUndefined(); expect(v.html).toBeUndefined(); expect(v.ok).toBe("ab"); expect(v.n).toBeNull(); expect(v.big.length).toBe(20000); expect(v.list).toEqual([1, null, true]);
    expect(JSON.stringify(v.deep)).toContain("null"); expect(typeof v.obj).toBe("string");
  });
  it("keeps html only in the html type and lets it be long", () => {
    expect(normalize({ type: "html", html: "x".repeat(250000) }).spec!.html.length).toBe(200000);
    expect(normalize({ type: "card.alert", text: "t", html: "<b>" }).spec!.html).toBeUndefined();
  });
  it("filters links and masks API card headers", () => {
    expect(safeUrl("https://a.co")).toBe("https://a.co"); expect(safeUrl("javascript:alert(1)")).toBeNull(); expect(safeUrl("//evil")).toBeNull(); expect(safeUrl("/runs/")).toBe("/runs/");
    expect(safeUrl("0123456789ab", "image")).toBe("0123456789ab"); expect(safeUrl("0123456789ab", "url")).toBeNull(); expect(safeUrl("data:image/png;base64,AAAA", "avatar")).toBe("data:image/png;base64,AAAA");
    const p = normalize({ type: "card.person", name: "A", url: "javascript:1", image: "https://x.co/a.png", links: [{ url: "mailto:a@b.c" }] }).spec!;
    expect(p.url).toBeNull(); expect(p.image).toBe("https://x.co/a.png"); expect(p.links[0].url).toBe("mailto:a@b.c");
    const q = normalize({ type: "card.request", method: "GET", url: "https://x.co", headers: { Authorization: "Bearer a", Accept: "x" } }).spec!;
    expect(q.headers).toEqual({ Authorization: "••••", Accept: "x" });
  });
  it("cuts long lists", () => {
    expect(normalize({ type: "table", rows: Array.from({ length: 5200 }, (_, i) => [i]) }).spec!.rows.length).toBe(5200);     // the table limit is 20000 here; the server applies its own, lower setting first
    expect(normalize({ type: "chart.histogram", values: Array.from({ length: 20100 }, (_, i) => i) }).spec!.values.length).toBe(20000);
  });
});

describe("the fallback table", () => {
  it("makes a table from rows, objects or x + series, else null", () => {
    expect(tableFromAnything("{bad")).toBeNull(); expect(tableFromAnything([1])).toBeNull(); expect(tableFromAnything({ type: "x" })).toBeNull();
    expect(tableFromAnything({ title: "T", columns: ["a"], rows: [[1]] })!.title).toBe("T");
    expect(tableFromAnything(JSON.stringify({ rows: [{ a: 1 }] }))!.columns[0].key).toBe("a");
    expect(tableFromAnything({ data: [{ label: "a", value: 1 }] })!.rows).toEqual([["a", 1]]);
    expect(tableFromAnything({ data: [1] })).toBeNull();
    const t = tableFromAnything({ x: ["a", "b"], series: [{ name: "S", values: [1, 2] }, { values: [3] }] })!;
    expect(t.columns.map((c) => c.label)).toEqual(["", "S", "Series 2"]); expect(t.rows[1]).toEqual(["b", 2, null]);
    expect(tableFromAnything({ x: ["a"], series: [5] })).toBeNull(); expect(tableFromAnything({ rows: [5] })).toBeNull();
  });
});
