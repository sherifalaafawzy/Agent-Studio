import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import BillingPage from "@/app/billing/page";
import { jsonResponse, routeFetch } from "../helpers/http";

const provider = { provider: "ollama", label: "Ollama", enabled: true, cap_usd: 10, cap_period: "monthly", spent_usd: 1, reserved_usd: 0, remaining_usd: 9, pct_used: 10, calls: 3, refused: 0 };

describe("Billing: edge cases", () => {
  it("leaves the bar uncoloured for a provider far from its cap, and shows tokens and a cost without currency", async () => {
    routeFetch({
      "GET /api/billing/summary": () => jsonResponse({ total_spent_usd: 1, providers: [provider], by_model: [] }),
      "GET /api/billing/ledger": () => jsonResponse([]),
      "GET /api/acp/usage": () => jsonResponse([{ agent: "grok", turns: 4, input_tokens: 1500, output_tokens: 250, reported_cost: 0.5, currency: null, turns_without_usage: 0 }]),
    });
    const { container } = render(<BillingPage />);
    await screen.findByText("Ollama");
    const bar = container.querySelector(".grid .bar i")!;
    expect(bar.className).toBe("");
    expect(bar).toHaveStyle({ width: "10%" });
    const row = screen.getByText("grok").closest("tr")!;
    expect(row).toHaveTextContent("grok41500250 0.50000");
    expect(row).not.toHaveTextContent("—");
    expect(row).not.toHaveTextContent("not reported");
  });
});
