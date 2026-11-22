import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ContextMeter, { type CtxInfo, useContext as useContextInfo } from "@/components/ContextMeter";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

const base: CtxInfo = {
  model: "gpt-test", window_tokens: 128000, used_tokens: 64000, percent: 50, level: "ok", source: "Estimated from the transcript",
  messages: 12, summarized: false, summarized_messages: 0, messages_after_summary: 3,
};

function captureToasts() {
  const seen: { text: string; tone: string }[] = [];
  const on = (e: Event) => seen.push((e as CustomEvent).detail);
  window.addEventListener("as:toast", on);
  return { seen, stop: () => window.removeEventListener("as:toast", on) };
}

describe("useContext hook", () => {
  function Probe({ sid, refreshKey }: { sid?: string; refreshKey?: unknown }) {
    const { info, reload } = useContextInfo(sid, refreshKey);
    return <div><span data-testid="model">{info ? info.model : "none"}</span><button onClick={reload}>reload</button></div>;
  }

  it("does not query without a session", () => {
    const { fn } = routeFetch({});
    render(<Probe />);
    expect(screen.getByTestId("model")).toHaveTextContent("none");
    expect(fn).not.toHaveBeenCalled();
  });

  it("loads the window info for a session and reloads on demand", async () => {
    const { calls } = routeFetch({ "GET /api/chat/sessions/s1/context": () => jsonResponse(base) });
    render(<Probe sid="s1" />);
    expect(await screen.findByText("gpt-test")).toBeInTheDocument();
    await userEvent.click(screen.getByText("reload"));
    expect(calls.filter((c) => c.url.pathname === "/api/chat/sessions/s1/context")).toHaveLength(2);
  });

  it("reloads when the refresh key changes", async () => {
    const { calls } = routeFetch({ "GET /api/chat/sessions/s1/context": () => jsonResponse(base) });
    const { rerender } = render(<Probe sid="s1" refreshKey={1} />);
    await screen.findByText("gpt-test");
    rerender(<Probe sid="s1" refreshKey={2} />);
    await waitFor(() => expect(calls).toHaveLength(2));
  });

  it("clears the info when the request fails", async () => {
    routeFetch({ "GET /api/chat/sessions/s1/context": () => textResponse("x", 404) });
    render(<Probe sid="s1" />);
    await waitFor(() => expect(screen.getByTestId("model")).toHaveTextContent("none"));
  });

  it("clears the info when the session is removed", async () => {
    routeFetch({ "GET /api/chat/sessions/s1/context": () => jsonResponse(base) });
    const { rerender } = render(<Probe sid="s1" />);
    await screen.findByText("gpt-test");
    rerender(<Probe />);
    expect(screen.getByTestId("model")).toHaveTextContent("none");
  });
});

describe("ContextMeter", () => {
  it("renders nothing without a session or without info", () => {
    const { container, rerender } = render(<ContextMeter info={base} busy={false} onSummarized={() => {}} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ContextMeter sid="s1" info={null} busy={false} onSummarized={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the percentage in the button with its tooltip", () => {
    render(<ContextMeter sid="s1" info={base} busy={false} onSummarized={() => {}} />);
    const btn = screen.getByRole("button", { name: "Context window usage" });
    expect(btn).toHaveAttribute("title", "Context: 50% of the window used");
    expect(btn).toHaveTextContent("50%");
  });

  it("caps the displayed percentage at 100", () => {
    render(<ContextMeter sid="s1" info={{ ...base, percent: 140, level: "critical" }} busy={false} onSummarized={() => {}} />);
    expect(screen.getByRole("button", { name: "Context window usage" })).toHaveTextContent("100%");
  });

  it("colours the ring by level", () => {
    const { rerender } = render(<ContextMeter sid="s1" info={{ ...base, level: "warn" }} busy={false} onSummarized={() => {}} />);
    const colour = () => screen.getByText("50%").getAttribute("style") ?? "";
    expect(colour()).toContain("var(--warn)");
    rerender(<ContextMeter sid="s1" info={{ ...base, level: "critical" }} busy={false} onSummarized={() => {}} />);
    expect(colour()).toContain("var(--bad)");
    rerender(<ContextMeter sid="s1" info={base} busy={false} onSummarized={() => {}} />);
    expect(colour()).toContain("var(--mut)");
  });

  it("opens a popover with usage figures and the transcript breakdown", async () => {
    render(<ContextMeter sid="s1" info={base} busy={false} onSummarized={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Context window usage" }));
    const dialog = screen.getByRole("dialog", { name: "Context window" });
    expect(dialog).toHaveTextContent("gpt-test");
    expect(dialog).toHaveTextContent("64k used");
    expect(dialog).toHaveTextContent("of 128k tokens");
    expect(dialog).toHaveTextContent("50.0%");
    expect(dialog).toHaveTextContent("Estimated from the transcript.");
    expect(dialog).toHaveTextContent("none yet");
    expect(dialog).toHaveTextContent("Summarize context");
    expect(screen.queryByText(/Getting full|Nearly full/)).toBeNull();
  });

  it.each([
    [2500, 1_500_000, "3k used", "of 1.5M tokens"],
    [999, 10_000_000, "999 used", "of 10M tokens"],
    [0, 2_000_000, "0 used", "of 2.0M tokens"],
  ])("formats %i used of %i tokens", async (used, window, usedText, windowText) => {
    render(<ContextMeter sid="s1" info={{ ...base, used_tokens: used, window_tokens: window }} busy={false} onSummarized={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Context window usage" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(usedText);
    expect(dialog).toHaveTextContent(windowText);
  });

  it("explains a warning and a critical state", async () => {
    const { rerender } = render(<ContextMeter sid="s1" info={{ ...base, level: "warn" }} busy={false} onSummarized={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Context window usage" }));
    expect(screen.getByText(/Getting full/)).toHaveClass("ctx-tip", "warn");
    rerender(<ContextMeter sid="s1" info={{ ...base, level: "critical", summarized: true, summarized_messages: 4 }} busy={false} onSummarized={() => {}} />);
    expect(screen.getByText(/Nearly full/)).toHaveClass("ctx-tip", "critical");
    expect(screen.getByText("4 messages condensed")).toBeInTheDocument();
  });

  it("closes when the user clicks outside but not inside", async () => {
    render(<ContextMeter sid="s1" info={base} busy={false} onSummarized={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Context window usage" }));
    fireEvent.mouseDown(screen.getByRole("dialog"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("toggles the popover from the button", async () => {
    render(<ContextMeter sid="s1" info={base} busy={false} onSummarized={() => {}} />);
    const btn = screen.getByRole("button", { name: "Context window usage" });
    await userEvent.click(btn);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.click(btn);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("summarises the context, reports the result and notifies the parent", async () => {
    const toasts = captureToasts();
    const { calls } = routeFetch({ "POST /api/chat/sessions/s1/summarize": () => jsonResponse({ covered_messages: 8, cost_usd: 0.0012 }) });
    const onSummarized = vi.fn();
    render(<ContextMeter sid="s1" info={base} busy={false} onSummarized={onSummarized} />);
    await userEvent.click(screen.getByRole("button", { name: "Context window usage" }));
    await userEvent.click(screen.getByRole("button", { name: "Summarize context" }));
    await waitFor(() => expect(onSummarized).toHaveBeenCalledTimes(1));
    expect(calls[0].method).toBe("POST");
    expect(toasts.seen[0]).toMatchObject({ tone: "ok" });
    expect(toasts.seen[0].text).toContain("Summarised 8 messages");
    expect(screen.queryByRole("dialog")).toBeNull();
    toasts.stop();
  });

  it("shows progress while summarising and disables the button", async () => {
    let finish!: (r: Response) => void;
    routeFetch({ "POST /api/chat/sessions/s1/summarize": () => new Promise<Response>((r) => { finish = r; }) });
    render(<ContextMeter sid="s1" info={base} busy={false} onSummarized={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Context window usage" }));
    await userEvent.click(screen.getByRole("button", { name: "Summarize context" }));
    const working = await screen.findByRole("button", { name: "Summarising…" });
    expect(working).toBeDisabled();
    await act(async () => { finish(jsonResponse({ covered_messages: 1, cost_usd: 0 })); });
  });

  it.each([
    ["a string detail", { detail: "Nothing to summarise yet" }, "Nothing to summarise yet"],
    ["an object detail", { detail: { message: "Provider refused" } }, "Provider refused"],
  ])("shows %s from the server as an error toast", async (_label, body, expected) => {
    const toasts = captureToasts();
    routeFetch({ "POST /api/chat/sessions/s1/summarize": () => jsonResponse(body, { status: 400 }) });
    const onSummarized = vi.fn();
    render(<ContextMeter sid="s1" info={base} busy={false} onSummarized={onSummarized} />);
    await userEvent.click(screen.getByRole("button", { name: "Context window usage" }));
    await userEvent.click(screen.getByRole("button", { name: "Summarize context" }));
    await waitFor(() => expect(toasts.seen.some((t) => t.tone === "err")).toBe(true));
    expect(toasts.seen.find((t) => t.tone === "err")?.text).toBe(expected);
    expect(onSummarized).not.toHaveBeenCalled();
    toasts.stop();
  });

  it("shows a network failure as an error toast", async () => {
    const toasts = captureToasts();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<ContextMeter sid="s1" info={base} busy={false} onSummarized={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Context window usage" }));
    await userEvent.click(screen.getByRole("button", { name: "Summarize context" }));
    await waitFor(() => expect(toasts.seen.some((t) => t.text === "Failed to fetch")).toBe(true));
    toasts.stop();
  });

  it("disables summarising when nothing new has happened or while busy", async () => {
    const { rerender } = render(<ContextMeter sid="s1" info={{ ...base, messages_after_summary: 0 }} busy={false} onSummarized={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Context window usage" }));
    expect(screen.getByRole("button", { name: "Summarize context" })).toBeDisabled();
    rerender(<ContextMeter sid="s1" info={base} busy={true} onSummarized={() => {}} />);
    expect(screen.getByRole("button", { name: "Summarize context" })).toBeDisabled();
  });
});
