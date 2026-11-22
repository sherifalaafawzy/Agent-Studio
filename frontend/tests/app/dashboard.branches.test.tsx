import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import DashboardPage from "@/app/dashboard/page";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const now = () => Date.now() / 1000;

const extAgent = (id: string, label: string, turns: number, cap: number, over: Record<string, any> = {}) => ({
  id, label, installed: true, enabled: true,
  month: { turns, minutes: 2, reported_cost: null, cost_reported_turns: 0, ...over },
  month_turn_cap: cap, month_minutes_cap: 0, month_cost_cap: 0,
});

function dash(over: Record<string, any> = {}) {
  return {
    kpis: { month_spent_usd: 3, month_calls: 5, month_refused: 0, credits_left_usd: 40, credits_tracked: 1, turns_7d: 1, running: 0, external_turns_month: 0 },
    daily: [{ day: "2026-11-20", cost_usd: 0.2, calls: 1, input_tokens: 10, output_tokens: 5 }, { day: "2026-11-21", cost_usd: 0.4, calls: 2, input_tokens: 20, output_tokens: 8 }],
    providers: [{ provider: "openai", label: "OpenAI", kind: "openai", enabled: true, cap_usd: 20, cap_period: "daily", spent_usd: 5, remaining_usd: 15, pct_used: 25, calls: 2, refused: 0, auth_ready: true, credits: { remaining_usd: 5, mode: "manual", source: "manual" } }],
    external: [
      extAgent("a", "Alpha", 8, 10, { reported_cost: null, cost_reported_turns: 2 }),
      extAgent("b", "Beta", 1, 10),
    ],
    recent: [
      { id: "c1", title: "Minutes old", agent: "engineer", provider: "openai", updated: now() - 1000, messages: 3, running: false },
      { id: "c2", title: "Days old", agent: null, provider: "openai", updated: now() - 3 * 86400, messages: 1, running: false },
    ],
    health: { providers_ready: 1, providers_enabled: 1, mcp_ready: 0, mcp_total: 0, agents: 1, skills: 0, otel_records: 0, external_installed: 2, external_total: 2 },
    ...over,
  };
}

describe("Dashboard: edge cases", () => {
  it("words singular counts, per-day caps and relative times", async () => {
    routeFetch({ "GET /api/dashboard": () => jsonResponse(dash()) });
    const { container } = render(<DashboardPage />);
    expect(await screen.findByText("across 1 tracked provider")).toBeInTheDocument();
    expect(screen.getByText(/of \$20\.00/)).toHaveTextContent("of $20.00/day");
    expect(screen.getByText(/Minutes old/).closest("a")).toHaveTextContent("engineer · 17m ago");
    expect(screen.getByText(/Days old/).closest("a")).toHaveTextContent("plain · 3d ago");

    const svg = screen.getByRole("img", { name: "Model spend per day" });
    const hits = svg.querySelectorAll<SVGRectElement>('rect[fill="transparent"]');
    fireEvent.mouseEnter(hits[0].parentElement!);
    expect(container.querySelector(".viz-tip")).toHaveTextContent("$0.2000 · 1 call");
    expect(container.querySelector(".viz-tip")).not.toHaveTextContent("1 calls");
  });

  it("colours an external agent's meter by how much of its turn limit is used, and reports cost as zero when none is given", async () => {
    routeFetch({ "GET /api/dashboard": () => jsonResponse(dash()) });
    const { container } = render(<DashboardPage />);
    await screen.findByText("Alpha");
    const rows = [...container.querySelectorAll(".meter-row")].filter((r) => /Alpha|Beta/.test(r.textContent ?? ""));
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector(".bar i")).toHaveClass("w");
    expect(rows[0].querySelector(".bar i")).toHaveStyle({ width: "80%" });
    expect(rows[0]).toHaveTextContent("8 turns of 10");
    expect(rows[0]).toHaveTextContent("$0.0000 reported");
    expect(rows[1].querySelector(".bar i")!.className).toBe("");
    expect(rows[1]).toHaveTextContent("1 turn of 10");
    expect(rows[1]).toHaveTextContent("cost not reported");
  });
});
