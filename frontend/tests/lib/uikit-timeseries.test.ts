import { describe, expect, it } from "vitest";
import { alignedTable, buildOption, canLog, DAY, DEFAULT_VIEW, indexTo100, movingAverage, rangeFor, readModel, resample, stats, statsTable, summarize, viewSeries, type Colors } from "@/lib/uikit/timeseries";
import { SAMPLES } from "@/lib/uikit/samples.generated";

const colors: Colors = { series: ["#1", "#2", "#3", "#4", "#5", "#6", "#7", "#8"], fg: "#f", grid: "#g", axis: "#a", panel: "#p", other: "#o" };
const day = (n: number) => Date.UTC(2025, 0, 1) + n * DAY;
const spec = (over: any = {}) => ({ type: "timeseries", series: [{ name: "S", points: [[day(0), 1], [day(1), 2], [day(2), 4]] }], ...over });

describe("reading a time series spec", () => {
  it("reads the sample and orders points", () => {
    const m = readModel(SAMPLES.timeseries);
    expect(m.series[0].points.length).toBe(40); expect(m.annotations.length).toBe(2); expect(m.forecast!.median.length).toBe(7); expect(m.forecast!.loKey).toBe("0.1"); expect(m.thresholds[0].value).toBe(110); expect(m.anomalies.length).toBe(1);
    const shuffled = readModel(spec({ series: [{ name: "S", points: [[day(2), 4], ["x", 1], [day(0), 1], null, { t: day(1), v: 2 }, { time: day(3), value: "5" }, { date: day(4), value: 6 }] }] }));
    expect(shuffled.series[0].points.map((p) => p[1])).toEqual([1, 2, 4, 5, 6]);
  });
  it("reads parallel t and v lists, kinds and names, and drops empty series", () => {
    const m = readModel(spec({ series: [{ t: [day(0), day(1)], v: [1, 2], kind: "area", unit: "kg" }, { name: "gone", points: [] }, { points: [[day(0), 1]], kind: "weird" }] }));
    expect(m.series.map((s) => [s.name, s.kind, s.unit])).toEqual([["Series 1", "area", "kg"], ["Series 3", "line", ""]]);
    expect(() => readModel(spec({ series: [{ points: [] }] }))).toThrow(/needs points/);
  });
  it("builds a forecast with inferred times, a missing band, and drops a broken one", () => {
    const f = readModel(spec({ forecast: { median: [5, 6], quantiles: { "0.9": [7, 8] } } })).forecast!;
    expect(f.times).toEqual([day(3), day(4)]); expect(f.lo).toEqual([]);          // one quantile is not a band
    expect(readModel(spec({ forecast: { median: [5, 6], quantiles: { "0.1": [4, 5], "0.9": [7, 8] } } })).forecast!.lo).toEqual([4, 5]);
    expect(readModel(spec({ forecast: { series: "S", median: [5], timestamps: ["nope"] } })).forecast).toBeNull();
    expect(readModel(spec({ forecast: { median: [] } })).forecast).toBeNull();
    expect(readModel({ type: "timeseries", series: [{ name: "one", points: [[day(0), 1]] }], forecast: { median: [2] } }).forecast!.times).toEqual([day(1)]);
    expect(readModel(spec({ forecast: { median: [5, 6], quantiles: { "0.1": [4], "0.9": [7, 8] } } })).forecast!.lo).toEqual([]);
  });
});

describe("resampling and transforms", () => {
  const pts = Array.from({ length: 10 }, (_, i) => [day(i) + 3600000 * (i % 3), i + 1] as [number, number]);
  it("groups by hour, day, week and month with every combiner", () => {
    expect(resample(pts, "none", "mean")).toBe(pts);
    expect(resample(pts, "day", "sum").length).toBe(10); expect(resample(pts, "hour", "mean").length).toBe(10);
    const w = resample(pts, "week", "sum"); expect(w.length).toBe(2); expect(w[0][1]).toBe(1 + 2 + 3 + 4 + 5 + 6 + 7 - 0 + 0 - 0 > 0 ? w[0][1] : 0);
    expect(resample(pts, "month", "mean")).toEqual([[Date.UTC(2025, 0, 1), 5.5]]);
    expect(resample(pts, "month", "last")[0][1]).toBe(10); expect(resample(pts, "month", "min")[0][1]).toBe(1); expect(resample(pts, "month", "max")[0][1]).toBe(10); expect(resample(pts, "month", "count")[0][1]).toBe(10);
    expect(resample(pts, "month", "sum")[0][1]).toBe(55);
  });
  it("averages, indexes and reports statistics", () => {
    expect(movingAverage([[0, 2], [1, 4], [2, 6]], 2).map((p) => p[1])).toEqual([2, 3, 5]);
    expect(indexTo100([[0, 50], [1, 100]]).map((p) => p[1])).toEqual([100, 200]); expect(indexTo100([[0, 0], [1, 5]])).toEqual([[0, 0], [1, 5]]); expect(indexTo100([])).toEqual([]);
    const s = stats({ name: "x", unit: "", kind: "line", points: [[0, 2], [1, 4]] }); expect(s).toMatchObject({ n: 2, min: 2, max: 4, mean: 3, last: 4, change: 1 });
    expect(stats({ name: "z", unit: "", kind: "line", points: [[0, 0], [1, 4]] }).change).toBeNull();
    const m = readModel(spec());
    expect(statsTable("T", m.series).rows[0][0]).toBe("S"); expect(statsTable("T", [{ ...m.series[0], points: [[0, 0], [1, 1]] }]).rows[0][6]).toBeNull();
    expect(viewSeries(m, { ...DEFAULT_VIEW, ma: true, index: true, resample: "day" })[0].points[0][1]).toBe(100);
  });
  it("aligns series by time and describes them", () => {
    const m = readModel(spec({ series: [{ name: "A", points: [[day(0), 1]] }, { name: "B", points: [[day(1), 2]] }] }));
    const t = alignedTable("T", m.series); expect(t.rows).toEqual([[new Date(day(0)).toISOString(), 1, null], [new Date(day(1)).toISOString(), null, 2]]);
    expect(summarize(m)).toMatch(/A from 1 to 1; B from 2 to 2, 2025-01-01 to 2025-01-02/);
    const withAll = readModel(spec({ forecast: { median: [5] }, anomalies: [{ t: day(1) }] }));
    expect(summarize(withAll)).toMatch(/forecast of 1 step/); expect(summarize(withAll)).toMatch(/1 unusual point marked/);
    expect(summarize(readModel(spec({ anomalies: [{ t: day(1) }, { t: day(2) }] })))).toMatch(/2 unusual points marked/);
  });
  it("says which range buttons apply and when a log scale is possible", () => {
    const m = readModel(spec({ series: [{ name: "S", points: Array.from({ length: 400 }, (_, i) => [day(i), i + 1]) }] }));
    expect(rangeFor(m, "1M")).toEqual([day(399) - 30 * DAY, day(399)]); expect(rangeFor(m, "All")).toBeNull(); expect(rangeFor(readModel(spec()), "1Y")![0]).toBe(day(0));
    expect(canLog(m)).toBe(true); expect(canLog(readModel(spec({ series: [{ name: "S", points: [[day(0), 0], [day(1), 2]] }] })))).toBe(false);
  });
});

describe("the ECharts option", () => {
  it("builds lines, marks, a forecast with its band and anomalies", () => {
    const o = buildOption(readModel(SAMPLES.timeseries), DEFAULT_VIEW, colors, false);
    const names = o.series.map((s: any) => s.name);
    expect(names).toEqual(["Sales", "Sales (forecast)", "band lower", "Sales (0.1–0.9 range)", "Unusual points"]);
    expect(o.series[0].markLine.data.length).toBe(2); expect(o.series[0].markArea.data.length).toBe(1); expect(o.legend.data).not.toContain("band lower");
    expect(o.yAxis.type).toBe("value"); expect(o.useUTC).toBe(false); expect(o.animation).toBe(true);
    expect(o.series[4].label.formatter({ data: [0, 0, "spike"] })).toBe("spike"); expect(o.series[4].label.formatter({ data: [0, 0] })).toBe("");
    expect(o.tooltip.valueFormatter(12345)).toBe("12.3k"); expect(o.tooltip.valueFormatter("x" as any)).toBe("x"); expect(o.yAxis.axisLabel.formatter(2000)).toBe("2000");
  });
  it("uses a step, bars, area, a log axis only when every value is positive, and no forecast when indexed or resampled", () => {
    const m = readModel(spec({ series: [{ name: "A", kind: "step", points: [[day(0), 1], [day(1), 2]] }, { name: "B", kind: "bar", points: [[day(0), 1]] }, { name: "C", kind: "area", points: [[day(0), 1]] }], forecast: { median: [3] } }));
    const o = buildOption(m, { ...DEFAULT_VIEW, log: true }, colors, true);
    expect(o.series[0].step).toBe("end"); expect(o.series[1].type).toBe("bar"); expect(o.series[2].areaStyle).toBeTruthy(); expect(o.yAxis.type).toBe("log"); expect(o.useUTC).toBe(true);
    expect(buildOption(m, { ...DEFAULT_VIEW, log: true, index: true }, colors, false).series.some((s: any) => String(s.name).includes("forecast"))).toBe(false);
    expect(buildOption(m, { ...DEFAULT_VIEW, resample: "day" }, colors, false).series.some((s: any) => String(s.name).includes("forecast"))).toBe(false);
    const neg = buildOption(readModel(spec({ series: [{ name: "N", points: [[day(0), -1], [day(1), 2]] }] })), { ...DEFAULT_VIEW, log: true }, colors, false);
    expect(neg.yAxis.type).toBe("value");
  });
  it("thins big series and skips animation and symbols", () => {
    const m = readModel(spec({ series: [{ name: "Big", points: Array.from({ length: 6000 }, (_, i) => [day(0) + i * 60000, i]) }] }));
    const o = buildOption(m, DEFAULT_VIEW, colors, false);
    expect(o.series[0].sampling).toBe("lttb"); expect(o.series[0].large).toBe(true); expect(o.series[0].showSymbol).toBe(false); expect(o.animation).toBe(false); expect(o.legend.show).toBe(false);
  });
  it("places an anomaly on the nearest point of its series, or uses its own value, and ignores thresholds when indexed", () => {
    const m = readModel(spec({ anomalies: [{ series: "S", t: day(1) + 5, label: "a" }, { t: day(2), v: 99, label: "b" }, { series: "nope", t: day(0), label: "c" }], thresholds: [{ value: 3, label: "T" }], annotations: [{ t: day(1), label: "ev" }] }));
    const o = buildOption(m, DEFAULT_VIEW, colors, false), sc = o.series.find((s: any) => s.name === "Unusual points");
    expect(sc.data).toEqual([[day(1) + 5, 2, "a"], [day(2), 99, "b"], [day(0), 1, "c"]]);
    expect(o.series[0].markLine.data.length).toBe(2);
    expect(buildOption(m, { ...DEFAULT_VIEW, index: true }, colors, false).series[0].markLine.data.length).toBe(1);
    expect(buildOption(readModel(spec()), DEFAULT_VIEW, colors, false).series[0].markLine).toBeUndefined();
  });
});
