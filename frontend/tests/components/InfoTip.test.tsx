import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import InfoTip, { place } from "@/components/InfoTip";
import { HELP } from "@/lib/help";

describe("place: the card always stays on screen", () => {
  const vp = { w: 400, h: 700 };
  it("sits under the button when there is room", () => {
    expect(place({ left: 50, right: 70, top: 100, bottom: 120 }, vp)).toEqual({ left: 50, width: 288, top: 126, bottom: undefined });
  });
  it("slides left near the right edge and right near the left edge", () => {
    expect(place({ left: 380, right: 398, top: 100, bottom: 120 }, vp).left).toBe(400 - 288 - 8);
    expect(place({ left: -20, right: 0, top: 100, bottom: 120 }, vp).left).toBe(8);
  });
  it("shrinks on a narrow phone", () => {
    expect(place({ left: 0, right: 20, top: 10, bottom: 30 }, { w: 300, h: 700 }).width).toBe(284);
  });
  it("opens upwards near the bottom of the screen, unless there is no room above either", () => {
    expect(place({ left: 50, right: 70, top: 600, bottom: 620 }, vp)).toEqual({ left: 50, width: 288, top: undefined, bottom: 106 });
    expect(place({ left: 50, right: 70, top: 20, bottom: 40 }, { w: 400, h: 120 }).top).toBe(46);
  });
});

describe("InfoTip", () => {
  const btn = () => screen.getByRole("button", { name: "More information" });

  it("shows nothing for an unknown topic, and the plain text it is given otherwise", () => {
    const { container, rerender } = render(<InfoTip id="no-such-topic" />);
    expect(container).toBeEmptyDOMElement();
    rerender(<InfoTip text="Plain words." title="Hello" />);
    fireEvent.click(btn());
    expect(screen.getByRole("tooltip")).toHaveTextContent("HelloPlain words.");
    rerender(<InfoTip text="No title." />);
    expect(screen.getByRole("tooltip")).toHaveTextContent("No title.");
  });

  it("opens on hover and closes when the mouse leaves", async () => {
    render(<InfoTip id="spend-caps" />);
    expect(screen.queryByRole("tooltip")).toBeNull();
    await userEvent.hover(btn());
    expect(screen.getByRole("tooltip")).toHaveTextContent(HELP["spend-caps"].body);
    expect(btn()).toHaveAttribute("aria-expanded", "true");
    expect(btn().getAttribute("aria-describedby")).toBe(screen.getByRole("tooltip").id);
    await userEvent.unhover(btn());
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("opens on keyboard focus and closes on blur or Escape", async () => {
    render(<InfoTip id="sandbox" />);
    await userEvent.tab();
    expect(btn()).toHaveFocus();
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();
    act(() => btn().blur());
    act(() => btn().focus());                                                    // focusing again re-opens it
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    act(() => btn().blur());
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("a click pins it open (touch screens) until you click elsewhere, press Escape, or tap it again", async () => {
    render(<div><InfoTip id="mcp" /><button>elsewhere</button></div>);
    await userEvent.click(btn());
    await userEvent.unhover(btn());
    expect(screen.getByRole("tooltip")).toHaveTextContent("MCP servers");
    expect(screen.getByRole("link", { name: /MCP servers/ })).toHaveAttribute("href", "/mcp/");
    await userEvent.click(screen.getByRole("tooltip"));                          // clicking inside keeps it open
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "elsewhere" }));
    expect(screen.queryByRole("tooltip")).toBeNull();
    await userEvent.click(btn());
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(btn()).toHaveFocus();
    await userEvent.click(btn());
    await userEvent.click(btn());                                                // tapping again closes it
    await userEvent.unhover(btn());
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("is positioned inside the screen once it knows where the button is", async () => {
    render(<InfoTip id="tiers" />);
    btn().getBoundingClientRect = () => ({ left: 1000, right: 1020, top: 10, bottom: 30, width: 20, height: 20, x: 1000, y: 10, toJSON: () => ({}) });
    await userEvent.hover(btn());
    const card = screen.getByRole("tooltip");
    expect(parseInt(card.style.left)).toBeLessThanOrEqual(window.innerWidth - 8);
    expect(card.style.visibility).not.toBe("hidden");
  });

  it("tips without a link show just the words", () => {
    render(<InfoTip id="tiers" />);
    fireEvent.click(btn());
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("ignores other keys while open", async () => {
    render(<InfoTip id="sandbox" />);
    await userEvent.hover(btn());
    await userEvent.keyboard("a");
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    await userEvent.click(btn());
    await userEvent.keyboard("b");
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
  });
});
