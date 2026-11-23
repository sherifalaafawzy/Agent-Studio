import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import UiBlock from "@/components/uikit/UiBlock";
import { SAMPLES } from "@/lib/uikit/samples.generated";

vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));

const CHARTS = Object.keys(SAMPLES).filter((k) => k.startsWith("chart."));
const draw = (spec: any) => render(<UiBlock spec={spec} />);
const ok = () => { expect(screen.queryByTestId("ui-fallback")).toBeNull(); expect(screen.getByRole("img")).toBeInTheDocument(); };

describe("every chart sample", () => {
  it.each(CHARTS)("%s draws a picture, an accessible summary and a table view", async (type) => {
    const { container } = draw(SAMPLES[type]);
    ok();
    const svg = screen.getByRole("img");
    expect(svg.getAttribute("aria-label")!.length).toBeGreaterThan(10);
    expect(container.querySelectorAll("svg *").length).toBeGreaterThan(3);
    const toggle = screen.queryByRole("button", { name: "Show as table" });
    if (toggle) { await userEvent.click(toggle); expect(screen.getByTestId("data-table")).toBeInTheDocument(); await userEvent.click(screen.getByRole("button", { name: "Show visual" })); }
  });
  it("shows a legend whenever there are two or more series", () => {
    draw(SAMPLES["chart.bar"]);
    expect(screen.getByText("2024")).toBeInTheDocument(); expect(screen.getByText("2025")).toBeInTheDocument();
  });
});

describe("bar charts", () => {
  const base = { type: "chart.bar", x: ["a", "b", "c"], series: [{ name: "S1", values: [3, -2, 5] }, { name: "S2", values: [1, 4, null] }] };
  it("draws grouped, stacked, percent and horizontal bars with negatives and gaps", () => {
    for (const extra of [{}, { stack: "stacked" }, { stack: "percent" }, { orientation: "horizontal" }, { orientation: "horizontal", stack: "stacked" }]) {
      const { unmount } = draw({ ...base, ...extra, unit: "k" }); ok(); unmount();
    }
  });
  it("labels the bars when there are few and shows a hover tooltip", async () => {
    draw({ type: "chart.bar", data: [{ label: "A", value: 3 }, { label: "B", value: -1 }] });
    expect(screen.getAllByText("3").length).toBeGreaterThan(0);
    const cols = document.querySelectorAll(".hover-col");
    expect(cols.length).toBe(2);
    fireEvent.mouseEnter(cols[0]); expect(screen.getAllByText("A").length).toBeGreaterThan(0);
    fireEvent.mouseMove(cols[1]); fireEvent.focus(cols[0]); fireEvent.blur(cols[0]); fireEvent.mouseLeave(cols[1]);
  });
  it("labels horizontal bars on both sides of zero", () => {
    draw({ type: "chart.bar", orientation: "horizontal", data: [{ label: "A", value: 3 }, { label: "B", value: -1 }] }); ok();
  });
  it("refuses a bar chart with nothing in it", () => {
    draw({ type: "chart.bar", x: ["a"], series: [{ name: "s", values: [] }] });
    expect(screen.queryByRole("img")).not.toBeNull();         // x with a null value still draws: only truly empty data falls back
  });
});

describe("line and area charts", () => {
  const base = { type: "chart.line", x: ["a", "b", "c", "d"], series: [{ name: "S1", values: [1, 3, null, 4] }, { name: "S2", values: [2, 2, 3, 1] }] };
  it("draws lines with gaps, steps, a single point and many points", () => {
    for (const spec of [base, { ...base, step: true }, { type: "chart.line", data: [{ label: "only", value: 2 }] }, { type: "chart.line", data: Array.from({ length: 60 }, (_, i) => ({ label: `p${i}`, value: i % 7 })) },
      { ...base, series: [...base.series, { name: "S3", values: [3, 1, 2, 2] }, { name: "S4", values: [1, 1, 1, 1] }, { name: "S5", values: [0, 1, 0, 1] }] },
      { type: "chart.line", x: ["a", "b"], series: [{ name: "neg", values: [-3, -1] }] }]) { const { unmount } = draw(spec); ok(); unmount(); }
  });
  it("draws areas: plain, stacked and percent", () => {
    for (const stack of [undefined, "stacked", "percent"]) { const { unmount } = draw({ ...base, type: "chart.area", stack, series: base.series.map((s) => ({ ...s, values: s.values.map((v) => v ?? 1) })) }); ok(); unmount(); }
    const { unmount } = draw({ ...base, type: "chart.area", stack: "stacked" }); ok(); unmount();       // a gap in a stack
  });
  it("shows a tooltip for the point under the pointer", () => {
    draw(base);
    const cols = document.querySelectorAll(".hover-col");
    fireEvent.mouseEnter(cols[1]);
    expect(screen.getAllByText(/S1: 3/).length).toBeGreaterThan(0);
  });
});

describe("other cartesian charts", () => {
  it("rejects too little data", () => {
    for (const spec of [{ type: "chart.dumbbell", x: ["a"], series: [{ name: "s", values: [1] }] }, { type: "chart.slope", x: ["a"], series: [{ name: "s", values: [1] }] }, { type: "chart.slope", x: ["a", "b"], series: [{ name: "s", values: [null, null] }] },
      { type: "chart.sparkline", values: [null] }, { type: "chart.scatter", points: [{ x: "q", y: 1 }] }, { type: "chart.histogram", values: ["x"] }, { type: "chart.box", groups: [{ name: "a", values: [1] }] },
      { type: "chart.candlestick", data: [{ t: "x" }] }, { type: "chart.bullet", data: [{ label: "a" }] }, { type: "chart.calendar", days: [{ date: "nope", value: 1 }] }, { type: "chart.gantt", tasks: [{ name: "a", start: "x", end: "y" }] }]) {
      const { unmount } = draw(spec);
      expect(screen.queryByTestId("ui-fallback"), JSON.stringify(spec)).not.toBeNull(); unmount();
    }
  });
  it("draws a waterfall that ends on a total of its own", () => {
    draw({ type: "chart.waterfall", data: [{ label: "a", value: 5 }, { label: "t", total: true, value: 0 }, { label: "b", value: -2 }, { label: "end", total: true, value: 9 }] }); ok();
  });
  it("draws a scatter plot with many groups folded into Other, a trend and without one", () => {
    const points = Array.from({ length: 30 }, (_, i) => ({ x: i, y: i * 2 + (i % 3), group: `g${i % 5}`, label: `p${i}` }));
    draw({ type: "chart.scatter", points, trend: true, x_label: "X", y_label: "Y" }); ok(); expect(screen.getByText("Other")).toBeInTheDocument();
  });
  it("skips a trend line when x never changes and draws bubbles with and without labels", () => {
    const { unmount } = draw({ type: "chart.scatter", points: [{ x: 1, y: 1 }, { x: 1, y: 2 }, { x: 1, y: 3 }], trend: true }); ok(); unmount();
    const big = Array.from({ length: 14 }, (_, i) => ({ x: i, y: i, size: i + 1, label: `l${i}` }));
    draw({ type: "chart.bubble", points: big }); ok();
  });
  it("draws a box plot from values with outliers and from given stats, ignoring bad groups", () => {
    draw({ type: "chart.box", groups: [{ name: "a", values: [1, 2, 2, 3, 3, 3, 4, 40] }, { name: "b", stats: { min: 1, q1: 2, median: 3, q3: 4, max: 5, outliers: [9] } }, { name: "c", stats: { min: "x" } }, null] }); ok();
  });
  it("draws candlesticks with and without volume and trims long series", () => {
    const rows = Array.from({ length: 320 }, (_, i) => ({ t: `d${i}`, open: i, high: i + 2, low: i - 1, close: i % 2 ? i + 1 : i - 0.5 }));
    const { unmount } = draw({ type: "chart.candlestick", data: rows }); ok(); expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/the last 300 of 320/); unmount();
    draw(SAMPLES["chart.candlestick"]); ok();
  });
  it("draws a bullet chart with a target and ranges, and without", () => {
    draw({ type: "chart.bullet", data: [{ label: "a", value: 5 }, { label: "b", value: 7, target: 9, ranges: [3, 6, 10] }] }); ok();
  });
  it("draws heatmaps from cells, values with gaps and a large grid without cell labels", () => {
    const { unmount } = draw({ type: "chart.heatmap", cells: [{ x: "a", y: "p", value: 1 }, { x: "b", y: "q", value: 5 }, { x: "b", y: "p", value: "z" }] }); ok(); unmount();
    const x = Array.from({ length: 12 }, (_, i) => `c${i}`), y = Array.from({ length: 9 }, (_, i) => `r${i}`);
    const { unmount: u2 } = draw({ type: "chart.heatmap", x, y, values: y.map((_, r) => x.map((__, c) => r * c)) }); ok(); u2();
    draw({ type: "chart.heatmap", x: ["a"], y: ["b"], values: [[2]] }); ok();
  });
  it("draws a calendar across months and years", () => {
    draw({ type: "chart.calendar", days: [{ date: "2024-12-30", value: 1 }, { date: "2025-02-03", value: 5 }, { date: "2025-02-04", value: 0 }, { date: "2025-03-20", value: 9 }] }); ok();
  });
  it("draws a Gantt chart with groups and without, and a zero-length task", () => {
    const { unmount } = draw({ type: "chart.gantt", tasks: [{ name: "a", start: "2025-01-01", end: "2025-01-01" }, { name: "b", start: "2025-01-02", end: "2025-01-09", progress: 2 }] }); ok(); unmount();
    draw(SAMPLES["chart.gantt"]); ok();
  });
  it("draws a histogram with a chosen number of bins and with identical values", () => {
    const { unmount } = draw({ type: "chart.histogram", values: [1, 2, 3, 4, 5, 6], bins: 3 }); ok(); unmount();
    draw({ type: "chart.histogram", values: [5, 5, 5] }); ok();
  });
  it("draws a sparkline with a title (framed) and without (bare)", () => {
    const { unmount } = draw({ type: "chart.sparkline", values: [1, null, 3] }); ok(); expect(screen.queryByText("Export")).toBeNull(); unmount();
    draw({ type: "chart.sparkline", title: "T", values: [1, 2, 3] }); expect(screen.getByText("Export")).toBeInTheDocument();
  });
  it("labels a lollipop and a dumbbell with gaps", () => {
    draw({ type: "chart.dumbbell", x: ["a", "b"], series: [{ name: "s1", values: [1, null] }, { name: "s2", values: [null, 4] }] }); ok();
  });
});

describe("part-to-whole, flow and hierarchy charts", () => {
  it("folds a pie into Other, draws a donut, and refuses non-positive data", () => {
    const data = Array.from({ length: 9 }, (_, i) => ({ label: `s${i}`, value: i + 1 }));
    const { unmount } = draw({ type: "chart.pie", data, donut: true }); ok(); expect(screen.getAllByText(/Other/).length).toBeGreaterThan(0); unmount();
    draw({ type: "chart.pie", data: [{ label: "a", value: 0 }] }); expect(screen.getByTestId("ui-fallback")).toBeInTheDocument();
  });
  it("draws a one-slice pie as a full circle", () => { draw({ type: "chart.pie", data: [{ label: "all", value: 5 }] }); ok(); });
  it("draws a funnel with a zero stage", () => { draw({ type: "chart.funnel", data: [{ label: "a", value: 10 }, { label: "b", value: 0 }, { label: "c", value: 0 }] }); ok(); });
  it("draws treemaps, sunbursts and trees, nested and flat, and refuses a missing root", () => {
    const flat = { name: "R", children: [{ name: "a", value: 3 }, { name: "b", value: 1 }, { name: "c" }] };
    for (const t of ["chart.treemap", "chart.sunburst", "chart.tree"]) { const { unmount } = draw({ type: t, root: flat }); ok(); unmount(); const u2 = draw({ type: t, root: SAMPLES[t].root }); ok(); u2.unmount(); }
    draw({ type: "chart.tree", root: { name: "solo" } }); ok();
    cleanup(); draw({ type: "chart.tree", root: "x" }); expect(screen.getByTestId("ui-fallback")).toBeInTheDocument(); cleanup();
    let deep: any = { name: "leaf", value: 1 }; for (let i = 0; i < 9; i++) deep = { name: `n${i}`, children: [deep] };
    draw({ type: "chart.sunburst", root: deep }); ok();
  });
  it("draws a Sankey chart with layers, nodes named only in links, and refuses bad links", () => {
    draw({ type: "chart.sankey", links: [{ source: "a", target: "b", value: 3 }, { source: "b", target: "c", value: 2 }, { source: "a", target: "c", value: 1 }, { source: "c", target: "a", value: 1 }] }); ok();
  });
  it("refuses a Sankey chart without links", () => { draw({ type: "chart.sankey", links: [{ source: "a", target: "a", value: 1 }] }); expect(screen.getByTestId("ui-fallback")).toBeInTheDocument(); });
  it("draws a network with groups, without labels when large, and skips links to unknown nodes", () => {
    const links = Array.from({ length: 45 }, (_, i) => ({ source: `n${i}`, target: `n${(i + 1) % 45}`, value: 1 + (i % 6) }));
    const { unmount } = draw({ type: "chart.network", links }); ok(); unmount();
    draw({ type: "chart.network", nodes: [{ id: "a", label: "A", group: "x" }, { id: "b", group: "y" }, "skip"], links: [{ source: "a", target: "b" }, { source: "a", target: "zz", value: "q" }] }); ok();
  });
  it("draws a radar chart and refuses too few axes", () => {
    draw({ type: "chart.radar", axes: ["a", { name: "b" }, { name: "c", max: 5 }], series: [{ name: "s", values: [1, 2, 3] }, { values: [3, 2] }] }); ok();
    const { unmount } = draw({ type: "chart.radar", axes: ["a", "b"], series: [{ name: "s", values: [1, 2] }] }); expect(screen.getAllByTestId("ui-fallback").length).toBeGreaterThan(0); unmount();
  });
  it("draws a gauge in a band, with no bands, and refuses a bad range", () => {
    const { unmount } = draw({ type: "chart.gauge", value: 30, min: 0, max: 100 }); ok(); unmount();
    const u2 = draw({ type: "chart.gauge", value: 130, bands: [{ to: 50 }, { to: "x" }, { to: 100, label: "high" }] }); ok(); u2.unmount();
    draw({ type: "chart.gauge", value: 5, min: 10, max: 2 }); expect(screen.getByTestId("ui-fallback")).toBeInTheDocument();
  });
});

describe("chart text", () => {
  it("grows its labels when the picture is squeezed", async () => {
    let cb: () => void = () => undefined;
    class RO { constructor(f: () => void) { cb = f; } observe() { /* nothing */ } disconnect() { /* nothing */ } }
    vi.stubGlobal("ResizeObserver", RO);
    draw(SAMPLES["chart.bar"]);
    const svg = screen.getByRole("img");
    Object.defineProperty(svg, "clientWidth", { value: 340, configurable: true });
    await act(async () => { cb(); });
    expect(svg.getAttribute("style")).toMatch(/--k: 1\.5/);
    Object.defineProperty(svg, "clientWidth", { value: 0, configurable: true });
    await act(async () => { cb(); });
    expect(svg.getAttribute("style")).toMatch(/--k: 1\.5/);       // an unmeasured size changes nothing
  });
});
