import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as tables from "@/lib/tables";
import * as exporters from "@/lib/uikit/exporters";
import { SAMPLES } from "@/lib/uikit/samples.generated";

const chart = { setOption: vi.fn(), dispatchAction: vi.fn(), resize: vi.fn(), dispose: vi.fn(), getDataURL: vi.fn(() => "data:image/png;base64,AAAA") };
vi.mock("echarts/core", () => ({ use: vi.fn(), init: vi.fn(() => chart) }));
vi.mock("echarts/charts", () => ({ BarChart: {}, LineChart: {}, ScatterChart: {} }));
vi.mock("echarts/components", () => ({ DataZoomComponent: {}, GridComponent: {}, LegendComponent: {}, MarkAreaComponent: {}, MarkLineComponent: {}, TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));
vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));
vi.mock("@/lib/tables", async (orig) => ({ ...(await orig<typeof import("@/lib/tables")>()), downloadFile: vi.fn() }));
vi.mock("@/lib/uikit/exporters", async (orig) => ({ ...(await orig<typeof import("@/lib/uikit/exporters")>()), downloadDataUrl: vi.fn() }));

import * as echarts from "echarts/core";
import UiBlock from "@/components/uikit/UiBlock";

beforeEach(() => { Object.values(chart).forEach((f) => f.mockClear()); vi.mocked(echarts.init).mockClear(); vi.mocked(tables.downloadFile).mockClear(); vi.mocked(exporters.downloadDataUrl).mockClear(); });

const draw = async (spec: any = SAMPLES.timeseries) => { const r = render(<UiBlock spec={spec} />); await screen.findByTestId("timeseries"); return r; };

describe("the time series component", () => {
  it("draws the chart with the option and a summary for screen readers", async () => {
    await draw();
    expect(echarts.init).toHaveBeenCalledTimes(1); expect(chart.setOption).toHaveBeenCalled();
    expect((chart.setOption.mock.calls.at(-1) as any)[0].series.length).toBe(5);
    expect(screen.getByRole("img")).toHaveAttribute("aria-label", expect.stringContaining("Sales from"));
    expect(screen.getByText(/Dashed line: forecast; shaded: 0.1 to 0.9 quantile range/)).toBeInTheDocument();
  });
  it("zooms with the range buttons and resets with All", async () => {
    await draw();
    await userEvent.click(screen.getByRole("button", { name: "1M" }));
    expect(chart.dispatchAction).toHaveBeenLastCalledWith(expect.objectContaining({ type: "dataZoom", startValue: expect.any(Number), endValue: expect.any(Number) }));
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    expect(chart.dispatchAction).toHaveBeenLastCalledWith({ type: "dataZoom", start: 0, end: 100 });
  });
  it("changes the view: grouping, combining, average, index, log and time zone", async () => {
    await draw();
    await userEvent.selectOptions(screen.getByLabelText("Group by"), "week");
    expect(screen.getByLabelText("Combine with")).toBeEnabled();
    await userEvent.selectOptions(screen.getByLabelText("Combine with"), "sum");
    await userEvent.click(screen.getByRole("checkbox", { name: "7-point average" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Index to 100" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Log scale" }));
    const last = (chart.setOption.mock.calls.at(-1) as any)[0];
    expect(last.yAxis.type).toBe("log");
    await userEvent.selectOptions(screen.getByLabelText("Time zone"), "utc");
    expect(echarts.init).toHaveBeenCalledTimes(2); expect(chart.dispose).toHaveBeenCalledTimes(1);
    expect((chart.setOption.mock.calls.at(-1) as any)[0].useUTC).toBe(true);
  });
  it("disables the log scale when a value is not positive", async () => {
    await draw({ type: "timeseries", series: [{ name: "S", points: [["2025-01-01", -1], ["2025-01-02", 2]] }] });
    expect(screen.getByRole("checkbox", { name: "Log scale" })).toBeDisabled(); expect(screen.getByLabelText("Combine with")).toBeDisabled();
  });
  it("shows the statistics table and exports the aligned data and a picture", async () => {
    await draw();
    await userEvent.click(screen.getByRole("button", { name: "Data, all series (CSV)" }));
    expect(tables.downloadFile).toHaveBeenCalledWith("Daily sales-data.csv", expect.stringContaining("Time,Sales"), "text/csv;charset=utf-8");
    await userEvent.click(screen.getByRole("button", { name: "Picture (PNG)" }));
    expect(exporters.downloadDataUrl).toHaveBeenCalledWith("Daily sales.png", "data:image/png;base64,AAAA");
    await userEvent.click(screen.getByRole("button", { name: "Show as table" }));
    expect(screen.getByTestId("data-table")).toHaveTextContent("Sales");
  });
  it("exports nothing for a picture when the chart is gone", async () => {
    await draw();
    chart.getDataURL.mockReturnValueOnce("" as any);
    await userEvent.click(screen.getByRole("button", { name: "Picture (PNG)" }));
    expect(exporters.downloadDataUrl).not.toHaveBeenCalled();
  });
  it("notes when a long series is thinned and survives having no ResizeObserver", async () => {
    vi.stubGlobal("ResizeObserver", undefined);
    await draw({ type: "timeseries", series: [{ name: "Big", points: Array.from({ length: 2500 }, (_, i) => [Date.UTC(2025, 0, 1) + i * 60000, i]) }] });
    expect(screen.getByText(/thinned for speed/)).toBeInTheDocument();
  });
  it("resizes the chart with its box and cleans up", async () => {
    let cb: () => void = () => undefined; const disconnect = vi.fn();
    class RO { constructor(f: () => void) { cb = f; } observe() { /* nothing */ } disconnect = disconnect; }
    vi.stubGlobal("ResizeObserver", RO);
    const { unmount } = await draw();
    cb(); expect(chart.resize).toHaveBeenCalled();
    unmount(); expect(disconnect).toHaveBeenCalled(); expect(chart.dispose).toHaveBeenCalled();
  });
  it("redraws in the new colours when the theme changes", async () => {
    await draw();
    const before = chart.setOption.mock.calls.length;
    await act(async () => { document.documentElement.setAttribute("data-theme", "dark"); await Promise.resolve(); });
    await screen.findByTestId("timeseries");
    await vi.waitFor(() => expect(chart.setOption.mock.calls.length).toBeGreaterThan(before));
  });
  it("listens to the system colour scheme", async () => {
    const add = vi.fn(), remove = vi.fn();
    vi.stubGlobal("matchMedia", () => ({ addEventListener: add, removeEventListener: remove }));
    const { unmount } = await draw();
    expect(add).toHaveBeenCalledWith("change", expect.any(Function)); unmount(); expect(remove).toHaveBeenCalled();
  });
  it("falls back to a table for data it cannot use", async () => {
    render(<UiBlock spec={{ type: "timeseries", series: [{ name: "S", points: [["bad", "x"]] }], rows: [["a", 1]] }} />);
    expect(await screen.findByTestId("ui-fallback")).toHaveTextContent(/needs points with a time and a value/);
  });
});
