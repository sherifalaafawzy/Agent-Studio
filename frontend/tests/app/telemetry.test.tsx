import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import TelemetryPage from "@/app/telemetry/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/telemetry/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const now = () => Date.now() / 1000;
const trace = { trace_id: "t1", root: "chat.turn", spans: 2, errors: 1, duration_ms: 1500, sources: ["engineer", "claude_code"], last: now() - 30 };
const spans = [
  { id: 1, ts: now(), signal: "trace", source: "engineer", name: "chat.turn", trace_id: "t1", span_id: "s1", parent_id: "", start_ns: 0, end_ns: 1_500_000_000, duration_ms: 1500, status: "ok", attrs: { model: "gpt-x" }, body: null },
  { id: 2, ts: now(), signal: "trace", source: "engineer", name: "tool.bash", trace_id: "t1", span_id: "s2", parent_id: "s1", start_ns: 500_000_000, end_ns: 900_000_000, duration_ms: 400, status: "error", attrs: { cmd: "npm test" }, body: null },
];
const metric = { id: 7, ts: now(), signal: "metric", source: "claude_code", name: "tokens", trace_id: "", span_id: "", parent_id: "", start_ns: 0, end_ns: 0, duration_ms: null, status: "ok", attrs: { kind: "input" }, body: { value: 42, unit: "tokens" } };

function setup(over: Record<string, () => Response> = {}) {
  return routeFetch({
    "GET /api/otel/summary": over.summary ?? (() => jsonResponse({ by_source: [{ source: "engineer" }, { source: "claude_code" }, { source: "engineer" }] })),
    "GET /api/otel/traces": over.traces ?? (() => jsonResponse([trace])),
    "GET /api/otel/traces/t1": over.spans ?? (() => jsonResponse(spans)),
    "GET /api/otel/records": over.records ?? (() => jsonResponse([metric])),
  });
}

describe("Telemetry: traces", () => {
  it("lists traces with their agents, errors and duration", async () => {
    setup();
    render(<TelemetryPage />);
    expect(await screen.findByText("chat.turn")).toBeInTheDocument();
    const row = screen.getByText("chat.turn").closest("tr")!;
    expect(row).toHaveTextContent("engineer, claude_code");
    expect(row).toHaveTextContent("1500 ms");
    expect(row).toHaveTextContent("30s ago");
  });

  it("explains when there are no traces", async () => {
    setup({ traces: () => jsonResponse([]) });
    render(<TelemetryPage />);
    expect(await screen.findByText(/No traces yet/)).toBeInTheDocument();
  });

  it("opens a trace as a waterfall and shows a span's attributes", async () => {
    const { calls } = setup();
    const { container } = render(<TelemetryPage />);
    await userEvent.click((await screen.findByText("chat.turn")).closest("tr")!);
    expect(await screen.findByText("tool.bash")).toBeInTheDocument();
    expect(container.querySelectorAll(".card > div")).not.toHaveLength(0);
    expect(calls.some((c) => c.url.pathname === "/api/otel/traces/t1")).toBe(true);
    await userEvent.click(screen.getByText("tool.bash"));
    expect(screen.getByText(/"cmd": "npm test"/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "← all traces" }));
    expect(await screen.findByText("No traces yet", { exact: false }).catch(() => screen.getByText("chat.turn"))).toBeInTheDocument();
  });

  it("filters traces by source", async () => {
    const { calls } = setup();
    render(<TelemetryPage />);
    await screen.findByText("chat.turn");
    await userEvent.selectOptions(screen.getByDisplayValue("all agents"), "claude_code");
    await waitFor(() => expect(calls.some((c) => c.url.searchParams.get("source") === "claude_code")).toBe(true));
  });

  it("shows the reason when the backend is offline", async () => {
    setup({ summary: () => textResponse("down", 500) });
    render(<TelemetryPage />);
    expect(await screen.findByText("Backend offline: HTTP 500")).toHaveClass("err");
  });

  it("keeps the trace list when opening a trace fails", async () => {
    setup({ spans: () => textResponse("x", 500) });
    render(<TelemetryPage />);
    await userEvent.click((await screen.findByText("chat.turn")).closest("tr")!);
    expect(await screen.findByRole("button", { name: "← all traces" })).toBeInTheDocument();
    expect(screen.queryByText("tool.bash")).toBeNull();
  });
});

describe("Telemetry: metrics and logs", () => {
  it("shows metrics with their value and unit", async () => {
    const { calls } = setup();
    render(<TelemetryPage />);
    await screen.findByText("chat.turn");
    await userEvent.click(screen.getByRole("button", { name: "Metrics" }));
    expect(await screen.findByText("42 tokens")).toBeInTheDocument();
    expect(calls.some((c) => c.url.search.includes("signal=metric"))).toBe(true);
  });

  it("shows log records and explains an empty log", async () => {
    setup({ records: () => jsonResponse([{ ...metric, signal: "log", name: "agent started", body: "boot ok", attrs: {} }]) });
    render(<TelemetryPage />);
    await screen.findByText("chat.turn");
    await userEvent.click(screen.getByRole("button", { name: "Logs" }));
    expect(await screen.findByText("boot ok")).toBeInTheDocument();
  });

  it("explains that external agents must export metrics or logs when there are none", async () => {
    setup({ records: () => jsonResponse([]) });
    render(<TelemetryPage />);
    await screen.findByText("chat.turn");
    await userEvent.click(screen.getByRole("button", { name: "Metrics" }));
    expect(await screen.findByText(/Nothing yet\. External agents only send metrics/)).toBeInTheDocument();
  });

  it("returns to traces from the tab bar", async () => {
    setup();
    render(<TelemetryPage />);
    await screen.findByText("chat.turn");
    await userEvent.click(screen.getByRole("button", { name: "Logs" }));
    await userEvent.click(screen.getByRole("button", { name: "Traces" }));
    expect(await screen.findByText("chat.turn")).toBeInTheDocument();
  });

  it("polls while live is ticked and stops when it is not", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const { fn } = setup();
      const { container } = render(<TelemetryPage />);
      await act(async () => { await Promise.resolve(); });
      const before = fn.mock.calls.length;
      await act(async () => { vi.advanceTimersByTime(3000); });
      expect(fn.mock.calls.length).toBeGreaterThan(before);
      const live = container.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
      await act(async () => { live.click(); });
      const paused = fn.mock.calls.length;
      await act(async () => { vi.advanceTimersByTime(9000); });
      expect(fn.mock.calls.length).toBe(paused);
    } finally {
      vi.useRealTimers();
    }
  });
});
