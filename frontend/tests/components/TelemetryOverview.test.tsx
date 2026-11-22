import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TelemetryOverview from "@/components/TelemetryOverview";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

const run = (id: string, o: Record<string, any> = {}) => ({ trace_id: id, agent: "engineer", agents: ["engineer"], started: 1_700_000_000, duration_ms: 2500, status: "ok", errors: 0, cost_usd: 0.012, tokens_in: 100, tokens_out: 20,
  model_calls: 2, tool_calls: 3, models: ["claude-x"], tools: { read_file: 3 }, ...o });
const DATA = {
  days: 7, kpis: { runs: 3, errors: 1, error_rate: 0.333, cost_usd: 0.04, tokens_in: 300, tokens_out: 60, model_calls: 6, tool_calls: 9, p50_ms: 2500, p95_ms: 61000 },
  trend: [{ day: "2026-11-20", runs: 1, errors: 0, cost_usd: 0.01 }, { day: "2026-11-21", runs: 2, errors: 1, cost_usd: 0.03 }],
  top: { agents: [{ name: "engineer", runs: 2, errors: 1, cost_usd: 0.03 }, { name: "reviewer", runs: 1, errors: 0, cost_usd: 0.01 }], models: [{ name: "claude-x", calls: 6, cost_usd: 0.04, tokens: 360 }],
    tools: [{ name: "read_file", calls: 9, errors: 2, avg_ms: 900 }, { name: "ls", calls: 1, errors: 0, avg_ms: 40 }] },
  errors: [{ trace_id: "t2", agent: "engineer", what: "tool.run_shell", cause: "A tool call failed.", at: 1 }],
  anomalies: [{ kind: "cost_spike", severity: "warn", trace_id: "t1aaaaaaaaaa", title: "A run cost much more than usual", detail: "$0.5 against $0.01." },
    { kind: "repeated_failure", severity: "bad", trace_id: null, title: "The same thing keeps failing", detail: "run_shell failed 3 times." }],
  runs: [run("t1aaaaaaaaaa"), run("t2bbbbbbbbbb", { status: "error", errors: 1, duration_ms: 400, cost_usd: 0.0003 }), run("t3cccccccccc", { agent: "reviewer", duration_ms: 45, cost_usd: 0 })],
};
function setup(over: Record<string, any> = {}, extra: Record<string, any> = {}) {
  return routeFetch({ "GET /api/telemetry/insights": () => jsonResponse({ ...DATA, ...over }),
    "GET /api/telemetry/runs/t1aaaaaaaaaa/explain": () => jsonResponse({ trace_id: "t1aaaaaaaaaa", summary: ["This run took 2.5 seconds.", "Nothing went wrong."] }), ...extra });
}
const last = (calls: Call[]) => calls.filter((c) => c.url.pathname === "/api/telemetry/insights").at(-1)!.url.searchParams;
const kpi = (label: string) => screen.getAllByText(label).find((e) => e.className === "kpi-label")!.closest(".kpi") as HTMLElement;

beforeEach(() => { localStorage.clear(); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("TelemetryOverview", () => {
  it("answers what happened, what it cost and what went wrong", async () => {
    setup();
    render(<TelemetryOverview />);
    expect(await screen.findByText("Failed runs")).toBeInTheDocument();
    expect(kpi("Runs")).toHaveTextContent("6 model calls · 9 tool calls");
    expect(kpi("Failed runs")).toHaveTextContent("33% of runs");
    expect(kpi("Cost")).toHaveTextContent("$0.0400");
    expect(kpi("Typical run")).toHaveTextContent("2.5 s");
    expect(kpi("Typical run")).toHaveTextContent("1 in 20: 61.0 s");
    expect(screen.getByRole("img", { name: /Cost per day. Highest: \$0.0300 on 2026-11-21/ })).toBeInTheDocument();
    await userEvent.click(screen.getByText("Show as a table"));
    expect(screen.getByRole("cell", { name: "2026-11-20" })).toBeInTheDocument();
    expect(screen.getByText("A run cost much more than usual")).toBeInTheDocument();
    expect(screen.getByText(/run_shell failed 3 times/)).toBeInTheDocument();
    expect(screen.getByText("tool.run_shell")).toBeInTheDocument();
    expect(screen.getByText("900 ms avg")).toBeInTheDocument();
    expect(screen.getByText("40 ms avg")).toBeInTheDocument();
  });

  it("filters, drills down from a number, and clears", async () => {
    const net = setup();
    render(<TelemetryOverview />);
    await screen.findByText("Failed runs");
    await userEvent.click(kpi("Failed runs"));
    await waitFor(() => expect(last(net.calls).get("status")).toBe("error"));
    await userEvent.click(kpi("Runs"));
    await waitFor(() => expect(last(net.calls).get("status")).toBeNull());
    await userEvent.selectOptions(screen.getByLabelText("Period"), "30");
    await userEvent.selectOptions(screen.getByLabelText("Agent"), "reviewer");
    await userEvent.selectOptions(screen.getByLabelText("Outcome"), "ok");
    await userEvent.selectOptions(screen.getByLabelText("Model"), "claude-x");
    await userEvent.selectOptions(screen.getByLabelText("Tool"), "read_file");
    await waitFor(() => expect(Object.fromEntries(last(net.calls))).toEqual({ days: "30", agent: "reviewer", status: "ok", model: "claude-x", tool: "read_file" }));
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(Object.fromEntries(last(net.calls))).toEqual({ days: "7" }));
    await userEvent.selectOptions(screen.getByLabelText("Period"), "1");
    await waitFor(() => expect(last(net.calls).get("days")).toBe("1"));
  });

  it("saves filter views in this browser, applies them and deletes them", async () => {
    const net = setup();
    render(<TelemetryOverview />);
    await screen.findByText("Failed runs");
    expect(screen.getByRole("button", { name: "Save view" })).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Outcome"), "error");
    await userEvent.type(screen.getByLabelText("Name this view"), "Failures");
    await userEvent.click(screen.getByRole("button", { name: "Save view" }));
    expect(JSON.parse(localStorage.getItem("as.telemetry.views")!)).toEqual([{ name: "Failures", filters: { days: 7, agent: "", status: "error", tool: "", model: "" } }]);
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await userEvent.click(screen.getByRole("button", { name: "Failures" }));
    await waitFor(() => expect(last(net.calls).get("status")).toBe("error"));
    await userEvent.type(screen.getByLabelText("Name this view"), "Failures");                              // saving again replaces it
    await userEvent.click(screen.getByRole("button", { name: "Save view" }));
    expect(JSON.parse(localStorage.getItem("as.telemetry.views")!)).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Delete view Failures" }));
    expect(screen.queryByRole("button", { name: "Failures" })).toBeNull();
    expect(JSON.parse(localStorage.getItem("as.telemetry.views")!)).toEqual([]);
  });

  it("keeps working when saved views are unreadable or cannot be stored", async () => {
    setup();
    localStorage.setItem("as.telemetry.views", "not json");
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<TelemetryOverview />);
    await screen.findByText("Failed runs");
    await userEvent.type(screen.getByLabelText("Name this view"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Save view" }));
    expect(set).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "x" })).toBeInTheDocument();                                  // it works for this visit even if it cannot be kept
  });

  it("explains a run in plain words, from an anomaly, an error or the table", async () => {
    setup({}, { "GET /api/telemetry/runs/t2/explain": () => jsonResponse({ trace_id: "t2", summary: ["It failed."] }), "GET /api/telemetry/runs/t3cccccccccc/explain": () => jsonResponse({ trace_id: "t3", summary: ["Reviewer ran."] }) });
    render(<TelemetryOverview />);
    await screen.findByText("Failed runs");
    await userEvent.click(screen.getAllByRole("button", { name: "Explain" })[0]);
    expect(await screen.findByRole("region", { name: "Run explained" })).toHaveTextContent("Nothing went wrong.");
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("region", { name: "Run explained" })).toBeNull();
    await userEvent.click(within(screen.getByText("tool.run_shell").parentElement!).getByRole("button", { name: "Explain" }));
    expect(await screen.findByText("It failed.")).toBeInTheDocument();
    await userEvent.click(within(screen.getByLabelText("Compare run t3cccccc").closest("tr") as HTMLElement).getByRole("button", { name: "Explain" }));
    expect(await screen.findByText("Reviewer ran.")).toBeInTheDocument();
  });

  it("compares two runs side by side", async () => {
    setup();
    render(<TelemetryOverview />);
    await screen.findByText("Failed runs");
    await userEvent.click(screen.getByLabelText("Compare run t1aaaaaa"));
    expect(screen.queryByRole("table", { name: "Comparison" })).toBeNull();
    await userEvent.click(screen.getByLabelText("Compare run t2bbbbbb"));
    const cmp = screen.getByRole("table", { name: "Comparison" });
    expect(cmp).toHaveTextContent("Outcome");
    expect(cmp).toHaveTextContent("error");
    await userEvent.click(screen.getByLabelText("Compare run t3cccccc"));                                      // a third pick drops the oldest
    expect(within(screen.getByRole("table", { name: "Comparison" })).getByText("t3cccccc")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Compare run t2bbbbbb"));
    expect(screen.queryByRole("table", { name: "Comparison" })).toBeNull();
  });

  it("offers CSV and JSON export of the filtered runs", async () => {
    setup();
    render(<TelemetryOverview />);
    await screen.findByText("Failed runs");
    expect(screen.getByRole("link", { name: "Export CSV" })).toHaveAttribute("href", expect.stringContaining("/api/telemetry/export?days=7&format=csv"));
    expect(screen.getByRole("link", { name: "Export JSON" })).toHaveAttribute("href", expect.stringContaining("format=json"));
  });

  it("says when there is nothing to show, with or without filters, and can be hidden", async () => {
    setup({ kpis: { ...DATA.kpis, runs: 0, errors: 0, error_rate: 0 }, runs: [] });
    render(<TelemetryOverview />);
    expect(await screen.findByText(/No runs in this period yet/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Outcome"), "error");
    expect(await screen.findByText(/No runs in this period with these filters yet/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(screen.queryByLabelText("Period")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(screen.getByLabelText("Period")).toBeInTheDocument();
  });

  it("shows nothing unusual when there are no anomalies or errors, and reports a failed explain", async () => {
    setup({ anomalies: [], errors: [] }, { "GET /api/telemetry/runs/t1aaaaaaaaaa/explain": () => jsonResponse({ detail: "gone" }, { status: 404 }) });
    render(<TelemetryOverview />);
    expect(await screen.findByText("Nothing unusual.")).toBeInTheDocument();
    expect(screen.queryByText("What went wrong")).toBeNull();
    await userEvent.click(screen.getAllByRole("button", { name: "Explain" })[0]);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("reports when the overview cannot be loaded", async () => {
    routeFetch({ "GET /api/telemetry/insights": () => jsonResponse({ detail: "offline" }, { status: 500 }) });
    render(<TelemetryOverview />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
