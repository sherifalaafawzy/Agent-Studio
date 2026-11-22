import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import TelemetryPage from "@/app/telemetry/page";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/telemetry/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const now = () => Date.now() / 1000;
const summary = () => jsonResponse({ by_source: [{ source: "engineer" }] });
const rec = (over: Record<string, any>) => ({
  id: 1, ts: now(), signal: "metric", source: "engineer", name: "m", trace_id: "", span_id: "", parent_id: "", start_ns: 0, end_ns: 0, duration_ms: null, status: "ok", attrs: {}, body: null, ...over,
});

describe("Telemetry: edge cases", () => {
  it("shows traces without a root, errors or duration, and ages in minutes and hours", async () => {
    routeFetch({
      "GET /api/otel/summary": summary,
      "GET /api/otel/traces": () => jsonResponse([
        { trace_id: "t1", root: null, spans: 1, errors: 0, duration_ms: null, sources: ["engineer"], last: now() - 600 },
        { trace_id: "t2", root: "chat.turn", spans: 3, errors: 2, duration_ms: 40, sources: [], last: now() - 7200 },
      ]),
    });
    render(<TelemetryPage />);
    const noRoot = (await screen.findByText("(no root)")).closest("tr")!;
    const cells = [...noRoot.querySelectorAll("td")].map((c) => c.textContent);
    expect(cells).toEqual(["(no root)", "engineer", "1", "", "— ms", "10m ago"]);
    const other = screen.getByText("chat.turn").closest("tr")!;
    expect([...other.querySelectorAll("td")].map((c) => c.textContent)).toEqual(["chat.turn", "", "3", "2", "40 ms", "2h ago"]);
  });

  it("renders metrics with a missing value or unit, and logs with no body or a long body", async () => {
    const { calls } = routeFetch({
      "GET /api/otel/summary": summary,
      "GET /api/otel/records": (_i, url) => jsonResponse(url.searchParams.get("signal") === "metric"
        ? [rec({ id: 1, name: "no-body", body: null }), rec({ id: 2, name: "no-unit", body: { value: 3 } }), rec({ id: 3, name: "no-value", body: { unit: "s" } })]
        : [rec({ id: 4, signal: "log", name: "empty-log", body: null }), rec({ id: 5, signal: "log", name: "long-log", body: "x".repeat(300) })]),
    });
    render(<TelemetryPage />);
    await userEvent.click(screen.getByRole("button", { name: "Metrics" }));
    const bodyOf = async (name: string) => (await screen.findByText(name)).closest("tr")!.querySelectorAll("td")[3].textContent;
    expect(await bodyOf("no-body")).toBe(" ");
    expect(await bodyOf("no-unit")).toBe("3 ");
    expect(await bodyOf("no-value")).toBe(" s");

    await userEvent.click(screen.getByRole("button", { name: "Logs" }));
    expect(await bodyOf("empty-log")).toBe("");
    expect(await bodyOf("long-log")).toBe("x".repeat(120));
    expect(calls.some((c) => c.url.searchParams.get("signal") === "log")).toBe(true);
  });

  it("draws a waterfall for spans that never ended or have no duration", async () => {
    routeFetch({
      "GET /api/otel/summary": summary,
      "GET /api/otel/traces": () => jsonResponse([{ trace_id: "t1", root: "turn", spans: 2, errors: 0, duration_ms: 5, sources: ["engineer"], last: now() }]),
      "GET /api/otel/traces/t1": () => jsonResponse([
        rec({ id: 1, signal: "trace", name: "open-span", span_id: "s1", parent_id: "", start_ns: 1_000_000_000, end_ns: 0, duration_ms: null, attrs: { a: 1 } }),
        rec({ id: 2, signal: "trace", name: "child-span", span_id: "s2", parent_id: "s1", start_ns: 1_000_000_000, end_ns: 3_000_000_000, duration_ms: 2000 }),
      ]),
    });
    const { container } = render(<TelemetryPage />);
    await userEvent.click((await screen.findByText("turn")).closest("tr")!);
    const open = (await screen.findByText("open-span")).closest("div[style*='grid']") as HTMLElement;
    expect(open).toHaveTextContent("— ms");
    expect(screen.getByText("child-span").closest("div[style*='grid']")).toHaveTextContent("2000 ms");
    // The span that never ended is drawn as a minimal sliver rather than a negative or missing bar
    const bar = open.querySelectorAll<HTMLElement>("div")[2];
    expect(bar.style.width).toBe("0.6%");
    await userEvent.click(screen.getByText("open-span"));
    await waitFor(() => expect(container.querySelector("pre")).toHaveTextContent('"a": 1'));
  });
});
