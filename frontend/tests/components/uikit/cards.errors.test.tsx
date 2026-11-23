import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import UiBlock from "@/components/uikit/UiBlock";

// The two helper libraries fail to load: the cards still show their content as plain text.
vi.mock("katex", () => { throw new Error("no katex"); });
vi.mock("katex/dist/katex.min.css", () => ({}));
vi.mock("qrcode-generator", () => { throw new Error("no qr"); });

describe("cards whose helper library cannot load", () => {
  it("shows the equation as plain LaTeX", async () => {
    render(<UiBlock spec={{ type: "card.equation", latex: "a^2+b^2=c^2" }} />);
    expect(await screen.findByText("a^2+b^2=c^2", { selector: "code" })).toBeInTheDocument(); await waitFor(() => expect(document.querySelector(".katex")).toBeNull());
  });
  it("keeps saying the QR code is being made when it cannot be, and still shows the text", async () => {
    render(<UiBlock spec={{ type: "card.qr", text: "https://x.co" }} />);
    expect(await screen.findByText("Making the code…")).toBeInTheDocument(); expect(screen.getAllByText("https://x.co").length).toBeGreaterThan(0);
  });
});
