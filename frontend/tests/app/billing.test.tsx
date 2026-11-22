import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import BillingPage from "@/app/billing/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

const summary = {
  total_spent_usd: 1.2345,
  providers: [
    { provider: "openai", label: "OpenAI", enabled: true, cap_usd: 10, cap_period: "monthly", spent_usd: 7, reserved_usd: 0.5, remaining_usd: 3, pct_used: 75, calls: 12, refused: 2 },
    { provider: "anthropic", label: "Anthropic", enabled: false, cap_usd: 5, cap_period: "total", spent_usd: 0, reserved_usd: 0, remaining_usd: 5, pct_used: 95, calls: 0, refused: 0 },
  ],
  by_model: [{ provider: "openai", model: "gpt-x", calls: 12, input_tokens: 1000, output_tokens: 200, cost_usd: 0.5 }],
};
const ledger = [
  { id: 1, ts: 1700000000, provider: "openai", model: "gpt-x", kind: "call", cost_usd: 0.01, projected_usd: 0.02, note: "fine", price_known: false },
  { id: 2, ts: 1700000100, provider: "openai", model: "gpt-x", kind: "refused", cost_usd: 0, projected_usd: 9, note: null, price_known: true },
];
const ext = [{ agent: "claude_code", turns: 2, input_tokens: null, output_tokens: null, reported_cost: 0.0123, currency: "USD", turns_without_usage: 1 }];

function routes(over: Record<string, () => Response> = {}) {
  return {
    "GET /api/billing/summary": over["summary"] ?? (() => jsonResponse(summary)),
    "GET /api/billing/ledger": over["ledger"] ?? (() => jsonResponse(ledger)),
    "GET /api/acp/usage": over["ext"] ?? (() => jsonResponse(ext)),
  };
}

describe("Billing", () => {
  it("shows every provider's spend against its cap", async () => {
    routeFetch(routes());
    render(<BillingPage />);
    expect(await screen.findByText("Spend vs cap — total $1.2345")).toBeInTheDocument();
    expect(screen.getByText("OpenAI")).toBeInTheDocument();
    expect(screen.getByText("$7.0000 of $10.0000")).toBeInTheDocument();
    expect(screen.getByText("remaining $3.0000 · in flight $0.5000")).toBeInTheDocument();
    expect(screen.getByText(/12 calls/)).toHaveTextContent("12 calls · 2 refused");
    expect(screen.getByText("off")).toHaveClass("pill");
    expect(screen.getByText("on")).toHaveClass("pill", "ok");
  });

  it("colours a bar by how close the provider is to its cap", async () => {
    routeFetch(routes());
    const { container } = render(<BillingPage />);
    await screen.findByText("OpenAI");
    const bars = container.querySelectorAll(".grid .bar i");
    expect(bars[0]).toHaveClass("w");
    expect(bars[1]).toHaveClass("b");
    expect(bars[1]).toHaveStyle({ width: "95%" });
  });

  it("lists spend by model, and says so when there is none", async () => {
    routeFetch(routes());
    const { unmount } = render(<BillingPage />);
    expect(await screen.findByRole("cell", { name: "gpt-x" })).toBeInTheDocument();
    unmount();
    routeFetch(routes({ summary: () => jsonResponse({ ...summary, by_model: [] }) }));
    render(<BillingPage />);
    expect(await screen.findByText("No spend yet.")).toBeInTheDocument();
  });

  it("shows external agent usage, with the reported cost when there is one", async () => {
    routeFetch(routes());
    render(<BillingPage />);
    expect(await screen.findByText("USD 0.0123")).toBeInTheDocument();
    expect(screen.getByText("claude_code")).toBeInTheDocument();
  });

  it("says when external agents have reported nothing", async () => {
    routeFetch(routes({ ext: () => jsonResponse([]) }));
    render(<BillingPage />);
    expect(await screen.findByText("No external-agent turns yet.")).toBeInTheDocument();
  });

  it("marks a turn that reported no cost", async () => {
    routeFetch(routes({ ext: () => jsonResponse([{ ...ext[0], reported_cost: null, currency: null }]) }));
    render(<BillingPage />);
    expect(await screen.findByText("not reported")).toBeInTheDocument();
  });

  it("lists the ledger with calls, refusals and unknown prices", async () => {
    routeFetch(routes());
    render(<BillingPage />);
    expect(await screen.findByText("call")).toHaveClass("pill", "ok");
    expect(screen.getByText("refused")).toHaveClass("pill", "bad");
    expect(screen.getByText("fine · price unknown")).toBeInTheDocument();
    expect(screen.getAllByText("openai/gpt-x")).toHaveLength(2);
  });

  it("says when the ledger is empty", async () => {
    routeFetch(routes({ ledger: () => jsonResponse([]) }));
    render(<BillingPage />);
    expect(await screen.findByText("Empty.")).toBeInTheDocument();
  });

  it("reports when the backend cannot be reached", async () => {
    routeFetch(routes({ summary: () => textResponse("down", 500) }));
    render(<BillingPage />);
    expect(await screen.findByText("Backend offline: HTTP 500")).toHaveClass("err");
  });

  it("keeps polling every three seconds", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const { fn } = routeFetch(routes());
      render(<BillingPage />);
      await act(async () => { await Promise.resolve(); });
      const before = fn.mock.calls.length;
      await act(async () => { vi.advanceTimersByTime(3000); });
      expect(fn.mock.calls.length).toBeGreaterThan(before);
    } finally {
      vi.useRealTimers();
    }
  });
});
