import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DataTable from "@/components/rich/DataTable";
import UiBlock from "@/components/uikit/UiBlock";
import UiFrame from "@/components/uikit/UiFrame";
import { REGISTRY } from "@/components/uikit/registry";
import * as tables from "@/lib/tables";
import * as exporters from "@/lib/uikit/exporters";
import { SAMPLES } from "@/lib/uikit/samples.generated";

vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));
vi.mock("@/lib/tables", async (orig) => ({ ...(await orig<typeof import("@/lib/tables")>()), downloadFile: vi.fn() }));
vi.mock("@/lib/uikit/exporters", async (orig) => ({ ...(await orig<typeof import("@/lib/uikit/exporters")>()), printElement: vi.fn(), exportSvg: vi.fn(), exportPng: vi.fn(async () => undefined) }));

beforeEach(() => { vi.mocked(tables.downloadFile).mockClear(); vi.mocked(exporters.printElement).mockClear(); });
afterEach(() => { vi.restoreAllMocks(); });

describe("UiBlock", () => {
  it("draws a table component and cleans the spec first", () => {
    render(<UiBlock spec={SAMPLES.table} />);
    expect(screen.getByTestId("data-table")).toBeInTheDocument(); expect(screen.getByText("Q3 sales by region")).toBeInTheDocument();
  });
  it("accepts the spec as JSON text", () => {
    render(<UiBlock spec={JSON.stringify(SAMPLES.table)} />);
    expect(screen.getByTestId("data-table")).toBeInTheDocument();
  });
  it("shows a reason and the data as a table when a component cannot be drawn", () => {
    render(<UiBlock spec={{ type: "chart.scatter", rows: [["a", 1]], x: [1], series: [{ name: "s", values: [1] }] }} />);
    expect(screen.getByTestId("ui-fallback")).toHaveTextContent(/Could not draw this component: chart\.scatter needs "points"/); expect(screen.getByTestId("ui-fallback")).toHaveTextContent("shown as a table");
    expect(screen.getByTestId("data-table")).toBeInTheDocument();
  });
  it("shows the data as JSON when there is no table in it", () => {
    render(<UiBlock spec={{ type: "nonsense", a: 1 }} />);
    expect(screen.getByTestId("ui-fallback")).toHaveTextContent(/unknown component type "nonsense".*shown as JSON/);
  });
  it("shows text that is not even JSON", () => {
    render(<UiBlock spec={"not json {"} />);
    expect(screen.getByTestId("ui-fallback")).toHaveTextContent("not valid JSON");
  });
  it("falls back when a type has no drawing yet", () => {
    const saved = REGISTRY.table; delete REGISTRY.table;
    try { render(<UiBlock spec={SAMPLES.table} />); expect(screen.getByTestId("ui-fallback")).toHaveTextContent("there is no drawing for table"); } finally { REGISTRY.table = saved; }
  });
  it("catches a component that throws and shows its data instead", () => {
    const saved = REGISTRY.table; REGISTRY.table = () => { throw new Error("boom"); };
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    try { render(<UiBlock spec={SAMPLES.table} />); expect(screen.getByTestId("ui-fallback")).toHaveTextContent("boom"); expect(screen.getByTestId("data-table")).toBeInTheDocument(); } finally { REGISTRY.table = saved; }
  });
  it("catches a throw with no message", () => {
    const saved = REGISTRY.table; REGISTRY.table = () => { throw new Error(""); };
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    try { render(<UiBlock spec={SAMPLES.table} />); expect(screen.getByTestId("ui-fallback")).toHaveTextContent("it failed to draw"); } finally { REGISTRY.table = saved; }
  });
  it("shows a loading note while a lazy component loads", async () => {
    const { lazy } = await import("react");
    const saved = REGISTRY.table; let release: (v: any) => void = () => undefined;
    REGISTRY.table = lazy(() => new Promise((r) => { release = r; })) as any;
    try {
      render(<UiBlock spec={SAMPLES.table} />);
      expect(screen.getByRole("status")).toHaveTextContent("Loading table");
      release({ default: () => <div>drawn</div> });
      expect(await screen.findByText("drawn")).toBeInTheDocument();
    } finally { REGISTRY.table = saved; }
  });
});

describe("table formats", () => {
  const spec = (over: any = {}) => ({
    type: "table", title: "T", columns: [
      { key: "g", label: "Group" }, { key: "m", label: "Money", type: "number", format: "currency", currency: "USD", color: "bar" }, { key: "p", label: "Pct", type: "number", format: "percent", color: "scale" },
      { key: "s", label: "Status", format: "badge" }, { key: "l", label: "Link", format: "link" }, { key: "pr", label: "Prog", format: "progress" }],
    rows: [["a", 100, 0.1, "ok", "https://x.co", 0.5], ["a", 50, -0.2, "failed", "javascript:1", 80], ["b", 25, 0.05, "pending", "https://y.co", ""]], ...over });
  it("shows currency, percent, badges with icons, links and progress", () => {
    const { container } = render(<UiBlock spec={spec()} />);
    expect(screen.getAllByText(/\$100/)[0]).toBeInTheDocument(); expect(screen.getByText("10%")).toBeInTheDocument(); expect(screen.getByText("-20%")).toBeInTheDocument();
    expect(screen.getByText("ok").closest(".pill")).toHaveClass("ok"); expect(screen.getByText("failed").closest(".pill")).toHaveClass("bad"); expect(screen.getByText("pending").closest(".pill")).toHaveClass("warn");
    expect(screen.getByRole("link", { name: "https://x.co" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByRole("link", { name: "javascript:1" })).toBeNull(); expect(screen.getByText("javascript:1")).toBeInTheDocument();
    expect(screen.getAllByRole("progressbar")).toHaveLength(2); expect(container.querySelectorAll(".dt-bar-cell").length).toBe(3); expect(container.querySelectorAll(".dt-scale-cell").length).toBe(3);
  });
  it("groups rows with a header and a subtotal and pins the first column", () => {
    const { container } = render(<UiBlock spec={spec({ group_by: "g", pin_first: true, density: "compact" })} />);
    expect(container.querySelectorAll(".dt-group").length).toBe(2);
    expect(screen.getByText(/Subtotal · a/)).toBeInTheDocument(); expect(screen.getByText(/Subtotal · b/)).toBeInTheDocument(); expect(screen.getByText(/2 rows/)).toBeInTheDocument(); expect(screen.getByText(/1 row$/)).toBeInTheDocument();
    expect(container.querySelector(".data-table")).toHaveClass("pin-first", "dense");
    expect(screen.getByRole("button", { name: "Comfortable" })).toBeInTheDocument();
  });
  it("lets you toggle the density", async () => {
    const { container } = render(<UiBlock spec={spec()} />);
    await userEvent.click(screen.getByRole("button", { name: "Compact" }));
    expect(container.querySelector(".data-table")).toHaveClass("dense");
    await userEvent.click(screen.getByRole("button", { name: "Comfortable" }));
    expect(container.querySelector(".data-table")).not.toHaveClass("dense");
  });
  it("selects rows and copies only those", async () => {
    const writeText = vi.fn(async () => undefined); Object.assign(navigator, { clipboard: { writeText } });
    render(<UiBlock spec={spec({ select: true })} />);
    const boxes = screen.getAllByRole("checkbox", { name: "Select row" });
    await userEvent.click(boxes[1]);
    await userEvent.click(screen.getByRole("button", { name: "Copy 1 selected" }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("-0.2")); expect((writeText.mock.calls[0] as any[])[0]).not.toContain("https://y.co");
    await userEvent.click(boxes[1]);
    expect(screen.getByRole("button", { name: /Copied|Copy$/ })).toBeInTheDocument();
  });
  it("ignores an unknown group column and expands long text only for unformatted columns", async () => {
    render(<UiBlock spec={{ type: "table", columns: ["a"], rows: [["x".repeat(300)]], group_by: "nope" }} />);
    await userEvent.click(screen.getByRole("button", { name: "more" }));
    expect(screen.getByText(/x{300}/)).toBeInTheDocument();
  });
  it("exports a web page and prints", async () => {
    render(<UiBlock spec={spec()} />);
    const group = screen.getByRole("group", { name: "Export the rows and columns shown" });
    await userEvent.click(within(group).getByRole("button", { name: "Web page (HTML)" }));
    expect(tables.downloadFile).toHaveBeenCalledWith("T.html", expect.stringContaining("<table"), "text/html;charset=utf-8");
    await userEvent.click(within(group).getByRole("button", { name: "PDF (print)" }));
    expect(exporters.printElement).toHaveBeenCalled();
  });
  it("raises a refused table to the boundary", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(<UiBlock spec={{ type: "table", rows: [5] }} />);
    expect(screen.getByTestId("ui-fallback")).toHaveTextContent("give the columns, or rows as objects");
  });
  it("shows a plain table when passed directly with a group on an out-of-range column", () => {
    const t: tables.Table = { title: "", truncated: false, columns: [{ key: "a", label: "A", type: "text" }], rows: [["x"]] };
    render(<DataTable table={t} opts={{ groupBy: 5 }} />);
    expect(screen.queryByText(/Subtotal/)).toBeNull();
  });
});

describe("UiFrame", () => {
  const table: tables.Table = { title: "", truncated: false, columns: [{ key: "a", label: "A", type: "text" }, { key: "n", label: "N", type: "number" }], rows: [["x", 1], ["y", 2]] };
  const spec: any = { type: "chart.bar", version: 1, title: "My chart", description: "about it" };
  it("shows a title, a screen-reader summary and switches between the visual and its table", async () => {
    render(<UiFrame spec={spec} summary="Bar chart of two things" table={table} foot={<span>legend</span>} toolbar={<span>tools</span>}><div>VISUAL</div></UiFrame>);
    expect(screen.getByText("My chart")).toBeInTheDocument(); expect(screen.getByText("Bar chart of two things")).toHaveClass("sr-only"); expect(screen.getByText("VISUAL")).toBeInTheDocument(); expect(screen.getByText("legend")).toBeInTheDocument(); expect(screen.getByText("tools")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show as table" }));
    expect(screen.queryByText("VISUAL")).toBeNull(); expect(screen.getByTestId("data-table")).toBeInTheDocument(); expect(screen.queryByText("legend")).toBeNull(); expect(screen.queryByText("tools")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show visual" }));
    expect(screen.getByText("VISUAL")).toBeInTheDocument();
  });
  it("offers every export that applies and runs it", async () => {
    const svgRef = { current: document.createElementNS("http://www.w3.org/2000/svg", "svg") } as React.RefObject<SVGSVGElement>;
    const extra = vi.fn();
    render(<UiFrame spec={spec} summary="s" table={table} svgRef={svgRef} exporters={[{ label: "GeoJSON", run: extra }]}><div>v</div></UiFrame>);
    const menu = screen.getByRole("group", { name: "Export" });
    for (const label of ["Table as CSV", "Table as JSON", "Table as Excel (.xlsx)", "Table as Markdown", "Web page (HTML)", "Component (JSON)"]) await userEvent.click(within(menu).getByRole("button", { name: label }));
    expect(tables.downloadFile).toHaveBeenCalledTimes(6); expect(vi.mocked(tables.downloadFile).mock.calls.map((c) => c[0])).toEqual(["My chart.csv", "My chart.json", "My chart.xlsx", "My chart.md", "My chart.html", "My chart.component.json"]);
    await userEvent.click(within(menu).getByRole("button", { name: "Picture (SVG)" })); expect(exporters.exportSvg).toHaveBeenCalled();
    await userEvent.click(within(menu).getByRole("button", { name: "Picture (PNG)" })); expect(exporters.exportPng).toHaveBeenCalled();
    await userEvent.click(within(menu).getByRole("button", { name: "GeoJSON" })); expect(extra).toHaveBeenCalled();
    await userEvent.click(within(menu).getByRole("button", { name: "PDF (print)" })); expect(exporters.printElement).toHaveBeenCalled();
  });
  it("does nothing for picture exports when there is no picture yet, and prints nothing without a frame", async () => {
    const svgRef = { current: null } as React.RefObject<SVGSVGElement | null>;
    render(<UiFrame spec={{ ...spec, title: undefined, type: "map" }} summary="s" svgRef={svgRef}><div>v</div></UiFrame>);
    const menu = screen.getByRole("group", { name: "Export" });
    await userEvent.click(within(menu).getByRole("button", { name: "Picture (SVG)" })); await userEvent.click(within(menu).getByRole("button", { name: "Picture (PNG)" }));
    expect(exporters.exportSvg).not.toHaveBeenCalled(); expect(exporters.exportPng).not.toHaveBeenCalled();
    await userEvent.click(within(menu).getByRole("button", { name: "Web page (HTML)" }));
    expect(tables.downloadFile).toHaveBeenCalledWith("map.html", expect.not.stringContaining("<svg"), "text/html;charset=utf-8");
  });
  it("shows an export error", async () => {
    render(<UiFrame spec={spec} summary="s" exporters={[{ label: "Bad", run: () => { throw new Error("no way"); } }]}><div>v</div></UiFrame>);
    await userEvent.click(screen.getByRole("button", { name: "Bad" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no way");
  });
  it("can be bare, without exports", () => {
    render(<UiFrame spec={{ type: "card.kpi", version: 1 }} summary="s" bare testId="mine"><div>v</div></UiFrame>);
    expect(screen.getByTestId("mine")).toBeInTheDocument(); expect(screen.queryByText("Export")).toBeNull(); expect(screen.queryByRole("button", { name: /Keep/ })).toBeNull();
  });
});
