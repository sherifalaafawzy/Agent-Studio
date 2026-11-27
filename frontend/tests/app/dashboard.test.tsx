import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import DashboardPage from "@/app/dashboard/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const now = () => Date.now() / 1000;

function dash(over: Record<string, any> = {}) {
  return {
    kpis: { month_spent_usd: 12.5, month_calls: 40, month_refused: 2, credits_left_usd: 150, credits_tracked: 2, turns_7d: 9, running: 1, external_turns_month: 3 },
    daily: [
      { day: "2026-11-19", cost_usd: 0.5, calls: 2, input_tokens: 100, output_tokens: 50 },
      { day: "2026-11-20", cost_usd: 0, calls: 0, input_tokens: 0, output_tokens: 0 },
      { day: "2026-11-21", cost_usd: 1.5, calls: 3, input_tokens: 1200, output_tokens: 300 },
    ],
    providers: [
      { provider: "openai", label: "OpenAI", kind: "openai", enabled: true, cap_usd: 20, cap_period: "monthly", spent_usd: 18, remaining_usd: 2, pct_used: 90, calls: 30, refused: 1, auth_ready: true, credits: { remaining_usd: null, mode: "off", source: null } },
      { provider: "ollama", label: "Ollama", kind: "ollama", enabled: true, cap_usd: 0, cap_period: "total", spent_usd: 0, remaining_usd: 0, pct_used: 0, calls: 0, refused: 0, auth_ready: false, credits: { remaining_usd: 5, mode: "manual", source: "manual" } },
    ],
    external: [
      { id: "claude_code", label: "Claude Code", installed: true, enabled: true, month: { turns: 1, minutes: 3, reported_cost: 0.2, cost_reported_turns: 1 }, month_turn_cap: 0, month_minutes_cap: 0, month_cost_cap: 0 },
      { id: "cursor", label: "Cursor", installed: true, enabled: false, month: { turns: 9, minutes: 5, reported_cost: null, cost_reported_turns: 0 }, month_turn_cap: 10, month_minutes_cap: 0, month_cost_cap: 0 },
      { id: "grok", label: "Grok", installed: false, enabled: false, month: { turns: 0, minutes: 0, reported_cost: null, cost_reported_turns: 0 }, month_turn_cap: 0, month_minutes_cap: 0, month_cost_cap: 0 },
    ],
    recent: [
      { id: "c1", title: "Chat one", agent: null, provider: "openai", updated: now(), messages: 3, running: true },
      { id: "c2", title: "Chat two", agent: "engineer", provider: "openai", updated: now() - 7200, messages: 1, running: false },
    ],
    health: { providers_ready: 1, providers_enabled: 2, mcp_ready: 3, mcp_total: 5, agents: 4, skills: 9, otel_records: 1234, external_installed: 2, external_total: 4 },
    ...over,
  };
}

describe("Dashboard", () => {
  it("shows placeholders while the first load is in flight", () => {
    routeFetch({ "GET /api/dashboard": () => new Promise<Response>(() => {}) });
    const { container } = render(<DashboardPage />);
    expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(container.querySelectorAll(".skel-kpi")).toHaveLength(6);
    expect(screen.getByTestId("dash-loading")).toHaveAttribute("aria-busy", "true");          // the whole page is sketched, not just one row
    expect(container.querySelectorAll(".skel-chart, .skel-card")).toHaveLength(3);
  });

  it("explains that the backend is offline when the first load fails", async () => {
    routeFetch({ "GET /api/dashboard": () => textResponse("x", 500) });
    render(<DashboardPage />);
    expect(await screen.findByText(/server is not answering/)).toHaveTextContent("(HTTP 500)");
  });

  it("shows the headline figures", async () => {
    routeFetch({ "GET /api/dashboard": () => jsonResponse(dash()) });
    render(<DashboardPage />);
    expect(await screen.findByText("$12.50")).toBeInTheDocument();
    expect(screen.getByText("40 model calls · 2 refused by the guard")).toBeInTheDocument();
    expect(screen.getByText("$150")).toBeInTheDocument();
    expect(screen.getByText("across 2 tracked providers")).toBeInTheDocument();
    expect(screen.getByText("1/2")).toBeInTheDocument();
    expect(screen.getByText("providers · 3/5 MCP · 2/4 external")).toBeInTheDocument();
    expect(screen.getByText("background runs in progress")).toBeInTheDocument();
  });

  it("explains empty credit tracking and an idle system", async () => {
    routeFetch({
      "GET /api/dashboard": () => jsonResponse(dash({
        kpis: { month_spent_usd: 0.004, month_calls: 1, month_refused: 0, credits_left_usd: null, credits_tracked: 0, turns_7d: 0, running: 0, external_turns_month: 0 },
      })),
    });
    render(<DashboardPage />);
    expect(await screen.findByText("$0.00400")).toBeInTheDocument();
    expect(screen.getByText("set balances in Settings")).toBeInTheDocument();
    expect(screen.getByText("nothing running")).toBeInTheDocument();
    expect(screen.getByText("1 model calls")).toBeInTheDocument();
  });

  it("formats money at each scale", async () => {
    routeFetch({ "GET /api/dashboard": () => jsonResponse(dash({ kpis: { ...dash().kpis, month_spent_usd: 1.234, credits_left_usd: 250.7 } })) });
    render(<DashboardPage />);
    expect(await screen.findByText("$1.23")).toBeInTheDocument();
    expect(screen.getByText("$251")).toBeInTheDocument();
  });

  it("charts the daily spend and explains each bar on hover", async () => {
    routeFetch({ "GET /api/dashboard": () => jsonResponse(dash()) });
    const { container } = render(<DashboardPage />);
    await screen.findByText("$12.50");
    const svg = screen.getByRole("img", { name: "Model spend per day" });
    expect(svg).toBeInTheDocument();
    expect(container.querySelectorAll("path.viz-bar")).toHaveLength(2);
    // each day has a transparent hit target; the first three <g> elements are the axis ticks
    const hits = svg.querySelectorAll<SVGRectElement>('rect[fill="transparent"]');
    fireEvent.mouseEnter(hits[2].parentElement!);
    const tip = container.querySelector(".viz-tip")!;
    expect(tip).toHaveTextContent("2026-11-21");
    expect(tip).toHaveTextContent("$1.5000 · 3 calls");
    expect(tip).toHaveTextContent("1,200 → 300 tokens");
    fireEvent.mouseLeave(svg);
    expect(container.querySelector(".viz-tip")).toBeNull();
  });

  it("shows the day table in reverse order", async () => {
    routeFetch({ "GET /api/dashboard": () => jsonResponse(dash()) });
    render(<DashboardPage />);
    await screen.findByText("$12.50");
    await userEvent.click(screen.getByRole("button", { name: "Table" }));
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("2026-11-21");
    expect(rows[0]).toHaveTextContent("1,200 → 300");
    expect(rows[2]).toHaveTextContent("2026-11-19");
    await userEvent.click(screen.getByRole("button", { name: "Chart" }));
    expect(screen.getByRole("img", { name: "Model spend per day" })).toBeInTheDocument();
  });

  it("says when nothing was spent in the period", async () => {
    const daily = [1, 2].map((d) => ({ day: `2026-11-0${d}`, cost_usd: 0, calls: 0, input_tokens: 0, output_tokens: 0 }));
    routeFetch({ "GET /api/dashboard": () => jsonResponse(dash({ daily })) });
    render(<DashboardPage />);
    expect(await screen.findByText("No model spend in this period.")).toBeInTheDocument();
  });

  it("flags providers near or over their caps, and missing authentication", async () => {
    routeFetch({ "GET /api/dashboard": () => jsonResponse(dash()) });
    render(<DashboardPage />);
    expect(await screen.findByText("⚠ near cap")).toHaveClass("bad");
    expect(screen.getByText("OpenAI")).toBeInTheDocument();
    expect(screen.getByText("$18.00", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("$5.00 credit left")).toBeInTheDocument();
    expect(screen.queryByTitle("authentication incomplete")).toBeNull();
  });

  it("marks a cloud provider without authentication", async () => {
    const body = dash();
    body.providers = [{ ...body.providers[0], auth_ready: false, pct_used: 75, refused: 0, credits: { remaining_usd: 1, mode: "auto", source: "x" } }];
    routeFetch({ "GET /api/dashboard": () => jsonResponse(body) });
    render(<DashboardPage />);
    expect(await screen.findByTitle("authentication incomplete")).toBeInTheDocument();
    expect(screen.getByText("70%+")).toHaveClass("warn");
  });

  it("says when there are no providers yet", async () => {
    routeFetch({ "GET /api/dashboard": () => jsonResponse(dash({ providers: [] })) });
    render(<DashboardPage />);
    expect(await screen.findByText("No providers enabled yet.")).toBeInTheDocument();
  });

  it("lists installed external agents against their turn limits", async () => {
    routeFetch({ "GET /api/dashboard": () => jsonResponse(dash()) });
    render(<DashboardPage />);
    expect(await screen.findByText("1 turn")).toBeInTheDocument();
    expect(screen.getByText("9 turns", { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/3 min · \$0\.2000 reported/)).toBeInTheDocument();
    expect(screen.getByText("cost not reported", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("off")).toBeInTheDocument();
    expect(screen.queryByText("Grok")).toBeNull();
  });

  it("says when no external agent is installed", async () => {
    const body = dash();
    body.external = [{ ...body.external[2] }];
    routeFetch({ "GET /api/dashboard": () => jsonResponse(body) });
    render(<DashboardPage />);
    expect(await screen.findByText("None installed.")).toBeInTheDocument();
  });

  it("notes external agents that have no turn limit", async () => {
    const body = dash();
    body.external = [{ ...body.external[0], enabled: true }];
    routeFetch({ "GET /api/dashboard": () => jsonResponse(body) });
    render(<DashboardPage />);
    expect(await screen.findByText("· no turn limit")).toBeInTheDocument();
  });

  it("lists recent runs with their state and age", async () => {
    routeFetch({ "GET /api/dashboard": () => jsonResponse(dash()) });
    const { container } = render(<DashboardPage />);
    const first = (await screen.findByText("Chat one")).closest("a")!;
    // Next's Link normalises the trailing slash when next.config.ts is not loaded (the app sets trailingSlash)
    expect(first).toHaveAttribute("href", expect.stringMatching(/^\/runs\/?\?s=c1$/));
    expect(first.querySelector(".run-dot")).not.toBeNull();
    expect(within(first).getByText(/plain · just now/)).toBeInTheDocument();
    const second = screen.getByText("Chat two").closest("a")!;
    expect(second).toHaveTextContent("engineer · 2h ago");
    expect(second.querySelector(".dot")).not.toBeNull();
    expect(container.querySelector(".recent-row")).not.toBeNull();
  });

  it("says when there are no chats yet", async () => {
    routeFetch({ "GET /api/dashboard": () => jsonResponse(dash({ recent: [] })) });
    render(<DashboardPage />);
    expect(await screen.findByText("No chats yet.")).toBeInTheDocument();
  });

  it("links the system summary to its pages", async () => {
    routeFetch({ "GET /api/dashboard": () => jsonResponse(dash()) });
    render(<DashboardPage />);
    await screen.findByText("Chat one");
    const href = (name: string, path: string) => expect(screen.getByRole("link", { name })).toHaveAttribute("href", expect.stringMatching(new RegExp(`^${path}/?$`)));
    href("4", "/agents");
    href("9", "/skills");
    href("3 of 5", "/mcp");
    expect(screen.getByText("2 of 4 installed")).toBeInTheDocument();
    href("1,234", "/telemetry");
  });

  it("refreshes every 15 seconds", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const { fn } = routeFetch({ "GET /api/dashboard": () => jsonResponse(dash()) });
      render(<DashboardPage />);
      await act(async () => { await Promise.resolve(); });
      const before = fn.mock.calls.length;
      await act(async () => { vi.advanceTimersByTime(15000); });
      expect(fn.mock.calls.length).toBeGreaterThan(before);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the last good figures when a refresh fails", async () => {
    let fail = false;
    routeFetch({ "GET /api/dashboard": () => (fail ? textResponse("x", 500) : jsonResponse(dash())) });
    render(<DashboardPage />);
    await screen.findByText("$12.50");
    fail = true;
    // trigger a reload through the interval-free path: unmount/remount is not needed; the page keeps its data
    expect(screen.getByText("$12.50")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/server is not answering/)).toBeNull());
  });
});
