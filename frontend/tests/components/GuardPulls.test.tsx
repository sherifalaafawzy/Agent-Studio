import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import GuardDownloads, { PullRow, type PullState } from "@/components/GuardPulls";
import { jsonResponse, routeFetch } from "../helpers/http";

const base: PullState = { key: "k1", model: "llama-guard3:8b", base: "http://127.0.0.1:11434", where: "on this PC", status: "downloading", completed: 1_000_000_000, total: 4_900_000_000,
  percent: 20.4, speed_bps: 5_000_000, eta_s: 780, size_hint_gb: 4.9, licence: "Llama community licence", message: "", error_kind: "", profile: "lg" };
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("PullRow", () => {
  it("shows the size, progress, speed, time left and the licence once, and can cancel", async () => {
    const onCancel = vi.fn();
    render(<PullRow s={base} onCancel={onCancel} />);
    expect(screen.getByText("Downloading llama-guard3:8b")).toBeInTheDocument();
    expect(screen.getByText(/4\.9 GB, on this PC\) · 20\.4% · 5\.0 MB\/s · .*13 min left|4\.9 GB, on this PC\) · 20\.4% · 5 MB\/s/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Downloading llama-guard3:8b" })).toHaveAttribute("aria-valuenow", "20.4");
    expect(screen.getByText(/Licence: Llama community licence/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel download" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("copes with an unknown size and no progress yet, and a remote server", () => {
    render(<PullRow s={{ ...base, total: 0, percent: null, speed_bps: null, eta_s: null, size_hint_gb: null, licence: "", where: "on this network" }} />);
    expect(screen.getByText(/a few GB, on this network\) · starting…/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel download" })).toBeNull();
    expect(screen.queryByText(/Licence/)).toBeNull();
  });

  it("uses the hint before the real size is known and MB for small downloads and short waits", () => {
    render(<PullRow s={{ ...base, total: 0, speed_bps: 800_000, eta_s: 30, percent: 1 }} />);
    expect(screen.getByText(/about 4\.9 GB/)).toBeInTheDocument();
    expect(screen.getByText(/1 MB\/s · 30 s left/)).toBeInTheDocument();
  });

  it("says when a download is ready, failed (with a way to start Ollama) or cancelled, and offers try again", async () => {
    const onRetry = vi.fn();
    const { rerender } = render(<PullRow s={{ ...base, status: "done" }} />);
    expect(screen.getByText("ready")).toBeInTheDocument();
    rerender(<PullRow s={{ ...base, status: "error", error_kind: "ollama_down", message: "Ollama is not running." }} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("download failed");
    expect(screen.getByRole("link", { name: "Open Setup to start Ollama" })).toHaveAttribute("href", "/setup/");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalled();
    rerender(<PullRow s={{ ...base, status: "cancelled", message: "Download cancelled." }} />);
    expect(screen.getByText("cancelled")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("GuardDownloads", () => {
  it("shows nothing when there are no downloads or the list cannot be read", async () => {
    routeFetch({ "GET /api/guardrails/pulls": () => jsonResponse({ pulls: [] }) });
    const { container } = render(<GuardDownloads reload={0} />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    vi.unstubAllGlobals();
    routeFetch({});
    const second = render(<GuardDownloads reload={0} />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(second.container).toBeEmptyDOMElement();
  });

  it("follows a running download until it is done, and cancels it", async () => {
    let cancelled = false;
    const net = routeFetch({
      "GET /api/guardrails/pulls": () => jsonResponse({ pulls: [cancelled ? { ...base, status: "done", percent: 100 } : base] }),
      "POST /api/guardrails/pulls/cancel": () => { cancelled = true; return jsonResponse({ cancelled: true }); },
    });
    render(<GuardDownloads reload={0} />);
    const row = await screen.findByTestId("guard-pull");
    await userEvent.click(within(row).getByRole("button", { name: "Cancel download" }));
    await waitFor(() => expect(net.calls.some((c) => c.method === "POST" && c.url.pathname === "/api/guardrails/pulls/cancel")).toBe(true));
    expect(net.calls.find((c) => c.method === "POST")?.body).toEqual({ base: base.base, model: base.model });
    expect(await screen.findByText("ready")).toBeInTheDocument();
  });

  it("offers try again for a failed download that a profile asked for, and re-reads when told to", async () => {
    const net = routeFetch({
      "GET /api/guardrails/pulls": () => jsonResponse({ pulls: [{ ...base, status: "error", error_kind: "network", message: "No internet." }, { ...base, key: "k2", model: "x:1", status: "error", message: "gone", profile: "" }] }),
      "POST /api/guardrails/pulls/retry": () => jsonResponse({ status: "downloading" }),
    });
    const { rerender } = render(<GuardDownloads reload={0} />);
    const rows = await screen.findAllByTestId("guard-pull");
    expect(within(rows[1]).queryByRole("button", { name: "Try again" })).toBeNull();           // no profile to retry through
    await userEvent.click(within(rows[0]).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(net.calls.find((c) => c.url.pathname.endsWith("/retry"))?.body).toEqual({ profile: "lg" }));
    const before = net.calls.filter((c) => c.method === "GET").length;
    rerender(<GuardDownloads reload={1} />);
    await waitFor(() => expect(net.calls.filter((c) => c.method === "GET").length).toBeGreaterThan(before));
  });

  it("ignores a failing cancel or retry", async () => {
    routeFetch({
      "GET /api/guardrails/pulls": () => jsonResponse({ pulls: [base, { ...base, key: "k3", status: "error", message: "x" }] }),
      "POST /api/guardrails/pulls/cancel": () => jsonResponse({ detail: "no" }, { status: 500 }),
      "POST /api/guardrails/pulls/retry": () => jsonResponse({ detail: "no" }, { status: 500 }),
    });
    render(<GuardDownloads reload={0} />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel download" }));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findAllByTestId("guard-pull")).toHaveLength(2);
  });
});
