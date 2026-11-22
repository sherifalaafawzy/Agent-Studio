import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import GuardrailNote from "@/components/GuardrailNote";

describe("GuardrailNote", () => {
  it("shows nothing when nothing happened", () => {
    const { container } = render(<GuardrailNote events={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
  it("summarises blocks first and lists each event on request", async () => {
    render(<GuardrailNote events={[{ stage: "input", agent: "engineer", profile_name: "Strict", action: "blocked", reason: "contains an email address" },
      { stage: "tool", agent: "", profile: "basic", action: "flagged" }, { stage: "weird", agent: "x", profile: "p", action: "error", reason: "down" }]} />);
    const b = screen.getByRole("button", { name: /1 blocked · 2 other/ });
    expect(screen.queryByRole("list")).toBeNull();
    await userEvent.click(b);
    expect(screen.getByText(/contains an email address/)).toBeInTheDocument();
    expect(screen.getByText(/a message/)).toBeInTheDocument();
    expect(screen.getByText(/a tool call/)).toBeInTheDocument();
    expect(screen.getByText(/weird/)).toBeInTheDocument();
  });
  it("says how many were flagged when none were blocked", () => {
    render(<GuardrailNote events={[{ stage: "output", agent: "a", profile: "p", action: "flagged", reason: "r" }]} />);
    expect(screen.getByRole("button", { name: /1 flagged/ })).toBeInTheDocument();
  });
});
