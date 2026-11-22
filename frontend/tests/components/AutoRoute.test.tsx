import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import RouteNote from "@/components/AutoRoute";
import type { AutoRoute } from "@/lib/api";

const route = (over: Partial<AutoRoute> = {}): AutoRoute => ({
  provider: "openai", model: "gpt-5-mini", tier: "flash", engine: "rules", reason: "built-in rules read this as simple.", confidence: 0.6,
  signals: { complexity: "simple", code: false, reasoning: false, tools: false, vision: false, est_tokens: 900 },
  price: { input_per_m: 0.5, output_per_m: 2, known: true }, alternates: [], skipped: [], ...over,
});

describe("RouteNote", () => {
  it("shows the chosen model collapsed, with the reason and the signals inside", () => {
    render(<RouteNote route={route()} />);
    expect(screen.getByText("Auto")).toBeInTheDocument();
    expect(screen.getByText("gpt-5-mini")).toBeInTheDocument();
    expect(screen.getByText(/openai · fast tier/)).toBeInTheDocument();
    expect(document.querySelector("details")).not.toHaveAttribute("open");
    expect(screen.getByText("built-in rules read this as simple.")).toBeInTheDocument();
    expect(screen.getByText(/decided by built-in rules · confidence 60% · \$0.5\/\$2 per M tokens/)).toBeInTheDocument();
    expect(screen.queryByText(/Next best/)).toBeNull();
    expect(screen.queryByText(/price unknown/)).toBeNull();
  });

  it("names every flag that applied, the strong tier, and the decision model that read it", () => {
    render(<RouteNote open route={route({ tier: "pro", engine: "d1-3b", confidence: 0.784,
      signals: { complexity: "demanding", code: true, reasoning: true, tools: true, vision: true, est_tokens: 1 } })} />);
    expect(document.querySelector("details")).toHaveAttribute("open");
    expect(screen.getByText(/strong tier/)).toBeInTheDocument();
    expect(screen.getByText(/code, deep reasoning, tools, images/)).toBeInTheDocument();
    expect(screen.getByText(/decided by d1-3B · confidence 78%/)).toBeInTheDocument();
  });

  it("explains a fallback to the rules, an unknown price, the next best models and what was skipped", () => {
    render(<RouteNote open showSkipped route={route({ engine: "mystery", engine_note: "d1-2B could not be used (it is not installed), so the built-in rules decided.",
      price: { input_per_m: 15, output_per_m: 75, known: false },
      alternates: [{ provider: "groq", model: "llama", tier: "flash" }, { provider: "mock", model: "m", tier: "pro" }],
      skipped: [{ provider: "anthropic", why: "turned off in Settings" }, { provider: "openai", model: "gpt-5", why: "over budget" }] })} />);
    expect(screen.getByText(/d1-2B could not be used/)).toBeInTheDocument();
    expect(screen.getByText(/decided by mystery/)).toBeInTheDocument();
    expect(screen.getByText(/price unknown/)).toBeInTheDocument();
    expect(screen.getByText("Next best if it fails: groq/llama, mock/m")).toBeInTheDocument();
    expect(screen.getByText("anthropic")).toBeInTheDocument();
    expect(screen.getByText(/turned off in Settings/)).toBeInTheDocument();
    expect(screen.getByText("openai/gpt-5")).toBeInTheDocument();
  });

  it("hides the skipped list unless asked", () => {
    render(<RouteNote open route={route({ skipped: [{ provider: "anthropic", why: "turned off in Settings" }] })} />);
    expect(screen.queryByText(/turned off in Settings/)).toBeNull();
  });
});
