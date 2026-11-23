import { describe, expect, it } from "vitest";
import { arr, clamp, fmtBytes, fmtDuration, fmtMoney, fmtNum, fmtPct, isObj, niceScale, num, obj, seqColor, seriesColor, seriesOf, str, sum, toTime } from "@/lib/uikit/data";

describe("reading a spec defensively", () => {
  it("coerces text, objects, arrays and numbers", () => {
    expect(str(null)).toBe(""); expect(str("abcdef", 3)).toBe("abc"); expect(str(12)).toBe("12");
    expect(isObj({})).toBe(true); expect(isObj([])).toBe(false); expect(isObj(null)).toBe(false); expect(obj("x")).toEqual({}); expect(obj({ a: 1 })).toEqual({ a: 1 });
    expect(arr("x")).toEqual([]); expect(arr([1, 2, 3], 2)).toEqual([1, 2]);
    expect(num(3)).toBe(3); expect(num("4.5")).toBe(4.5); expect(num(NaN, 7)).toBe(7); expect(num("x", 1)).toBe(1); expect(num(" ")).toBeNaN(); expect(num(null, 2)).toBe(2); expect(num(Infinity)).toBeNaN();
    expect(clamp(5, 0, 3)).toBe(3); expect(clamp(-1, 0, 3)).toBe(0);
    expect(sum([1, null, 2])).toBe(3);
  });
  it("formats numbers, money, percent, bytes and durations", () => {
    expect(fmtNum(NaN)).toBe("–"); expect(fmtNum(1.2345)).toBe("1.23"); expect(fmtNum(12345)).toBe("12.3k"); expect(fmtNum(2_500_000)).toBe("2.5M"); expect(fmtNum(3e9)).toBe("3B");
    expect(fmtMoney(1234, "USD")).toContain("1,234"); expect(fmtMoney(NaN)).toBe("–"); expect(fmtMoney(5, "XXXXX")).toContain("5");
    expect(fmtPct(0.123)).toBe("12.3%"); expect(fmtPct(NaN)).toBe("–");
    expect(fmtBytes(NaN)).toBe("–"); expect(fmtBytes(512)).toBe("512 B"); expect(fmtBytes(2048)).toBe("2 KB"); expect(fmtBytes(-2048)).toBe("-2 KB");
    expect(fmtDuration(NaN)).toBe("–"); expect(fmtDuration(42)).toBe("42s"); expect(fmtDuration(125)).toBe("2m 5s"); expect(fmtDuration(3700)).toBe("1h 1m");
  });
  it("makes nice axis ticks", () => {
    expect(niceScale(NaN, 1)).toEqual({ lo: 0, hi: 1, ticks: [0, 1] });
    const s = niceScale(0, 97); expect(s.lo).toBe(0); expect(s.hi).toBeGreaterThanOrEqual(97); expect(s.ticks.length).toBeGreaterThan(2);
    const flat = niceScale(5, 5); expect(flat.lo).toBeLessThan(5); expect(flat.hi).toBeGreaterThan(5);
  });
  it("reads series from data, x + series or values", () => {
    expect(seriesOf({ data: [{ label: "a", value: 1 }, { name: "b", value: "x" }], title: "T" })).toEqual({ labels: ["a", "b"], series: [{ name: "T", values: [1, null] }] });
    expect(seriesOf({ x: ["a", "b"], series: [{ name: "S", values: [1, "2", "z"] }, { values: [3] }] })).toEqual({ labels: ["a", "b"], series: [{ name: "S", values: [1, 2] }, { name: "Series 2", values: [3, null] }] });
    expect(seriesOf({ series: [{ name: "S", values: [4, 5] }] }).labels).toEqual(["1", "2"]);
    expect(seriesOf({ values: [1, "q", 3], name: "V" })).toEqual({ labels: ["1", "2", "3"], series: [{ name: "V", values: [1, null, 3] }] });
    expect(seriesOf({})).toEqual({ labels: [], series: [] });
    expect(seriesOf({ series: [{ name: "S" }], x: [] }).labels).toEqual([]);
  });
  it("parses dates and timestamps", () => {
    expect(toTime("2025-01-02")).toBe(Date.UTC(2025, 0, 2)); expect(toTime("2025-01-02T10:00:00Z")).toBe(Date.UTC(2025, 0, 2, 10));
    expect(toTime(1735689600)).toBe(1735689600000); expect(toTime(1735689600000)).toBe(1735689600000); expect(toTime("1735689600")).toBe(1735689600000);
    expect(toTime("")).toBeNaN(); expect(toTime(NaN)).toBeNaN(); expect(toTime("nope")).toBeNaN(); expect(toTime(null)).toBeNaN();
  });
  it("picks series colours from the palette and a sequential ramp", () => {
    expect(seriesColor(0)).toBe("var(--viz-1)"); expect(seriesColor(7)).toBe("var(--viz-8)"); expect(seriesColor(8)).toBe("var(--viz-1)");
    expect(seqColor(2)).toContain("100%"); expect(seqColor(-1, 2)).toContain("--viz-2");
  });
});
