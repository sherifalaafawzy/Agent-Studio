import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import OllamaSetup from "@/components/OllamaSetup";
import { jsonResponse, routeFetch } from "../helpers/http";

const idle = { state: "idle", step: "", done: 0, total: 0, log: [] as string[], error: "", version: "", running: false, needs_manual: false };
const status = (over: Record<string, unknown> = {}, install: Record<string, unknown> = {}) => ({
  running: false, version: null, installed: false, path: null, install: { ...idle, ...install }, ...over,
});
const PLAN = {
  supported: true, installed: false, manual: { title: "Install Ollama", steps: ["Download it."], url: "https://ollama.com/download" },
  release: { version: "0.40.17", size: 1857946272, source: "GitHub release digest", verified: true, note: "" }, size_gb: 1.86, needs_confirm: false,
  steps: ["Download the official installer", "Check its checksum"],
};

describe("OllamaSetup", () => {
  it("shows a running Ollama and does nothing else", async () => {
    const { calls } = routeFetch({ "GET /api/easy/ollama/status": () => jsonResponse(status({ running: true, installed: true, version: "0.40.16" })) });
    render(<OllamaSetup />);
    expect(await screen.findByText("running · 0.40.16")).toBeInTheDocument();
    expect(screen.getByText(/cost nothing/)).toBeInTheDocument();
    expect(calls.map((c) => c.url.pathname)).toEqual(["/api/easy/ollama/status"]);                 // no plan needed once installed
  });

  it("starts an installed but stopped Ollama", async () => {
    let running = false;
    const onChange = vi.fn();
    const { calls } = routeFetch({
      "GET /api/easy/ollama/status": () => jsonResponse(status({ running, installed: true, version: running ? "0.40.16" : null })),
      "POST /api/easy/ollama/start": () => { running = true; return jsonResponse({ ok: true, message: "Ollama is running." }); },
    });
    render(<OllamaSetup onChange={onChange} />);
    expect(await screen.findByText("installed, not running")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start Ollama" }));
    expect(await screen.findByText("running · 0.40.16")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ running: true }));
  });

  it("explains why it could not start", async () => {
    routeFetch({
      "GET /api/easy/ollama/status": () => jsonResponse(status({ installed: true })),
      "POST /api/easy/ollama/start": () => jsonResponse({ detail: "Ollama was started but is not answering yet." }, { status: 409 }),
    });
    render(<OllamaSetup />);
    await userEvent.click(await screen.findByRole("button", { name: "Start Ollama" }));
    expect(await screen.findByText(/not answering yet/)).toBeInTheDocument();
  });

  it("offers a one-click install with the facts: version, size and a verified checksum", async () => {
    routeFetch({ "GET /api/easy/ollama/status": () => jsonResponse(status()), "GET /api/easy/ollama/plan": () => jsonResponse(PLAN) });
    render(<OllamaSetup />);
    expect(await screen.findByText("not installed")).toBeInTheDocument();
    expect(screen.getByText(/Version 0.40.17, a 1.86 GB download/)).toBeInTheDocument();
    expect(screen.getByText("Checksum verified against the GitHub release digest.")).toBeInTheDocument();
    expect(screen.getByText("Check its checksum")).toBeInTheDocument();
    expect(screen.getByText(/No administrator rights needed/)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("installs, shows progress, and refreshes when it finishes", async () => {
    let phase = 0;
    const onChange = vi.fn();
    const { calls } = routeFetch({
      "GET /api/easy/ollama/status": () => jsonResponse(phase < 2 ? status() : status({ running: true, installed: true, version: "0.40.17" })),
      "GET /api/easy/ollama/plan": () => jsonResponse(PLAN),
      "POST /api/easy/ollama/install": () => { phase = 1; return jsonResponse({ ...idle, state: "downloading", step: "Downloading the installer", done: 928973136, total: 1857946272, running: true, log: ["checksum OK (GitHub release digest)"] }); },
      "GET /api/easy/ollama/install/status": () => { phase = 2; return jsonResponse({ ...idle, state: "ready", step: "Ollama is installed and running.", running: false }); },
    });
    render(<OllamaSetup onChange={onChange} />);
    await userEvent.click(await screen.findByRole("button", { name: "Install Ollama" }));
    expect(await screen.findByText(/Downloading the installer · 50% \(0.93 of 1.86 GB\)/)).toBeInTheDocument();
    expect(document.querySelector(".bar i")).toHaveStyle({ width: "50%" });
    expect(screen.getByText("Details")).toBeInTheDocument();
    expect(calls.find((c) => c.url.pathname === "/api/easy/ollama/install")!.body).toEqual({ accept_unverified: false });
    expect(await screen.findByText("running · 0.40.17", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ running: true }));
  });

  it("a download that cannot be verified needs an explicit yes", async () => {
    const unverified = { ...PLAN, needs_confirm: true, release: { ...PLAN.release, verified: false, source: "", note: "No published checksum was found for this download, so it cannot be verified." } };
    const { calls } = routeFetch({
      "GET /api/easy/ollama/status": () => jsonResponse(status()), "GET /api/easy/ollama/plan": () => jsonResponse(unverified),
      "POST /api/easy/ollama/install": () => jsonResponse({ ...idle, state: "downloading", step: "x", running: true }),
      "GET /api/easy/ollama/install/status": () => jsonResponse({ ...idle, state: "downloading", step: "x", running: true }),
    });
    render(<OllamaSetup />);
    const go = await screen.findByRole("button", { name: "Install Ollama" });
    expect(screen.getByText(/cannot be verified\.$/)).toBeInTheDocument();
    expect(go).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox"));
    expect(go).toBeEnabled();
    await userEvent.click(go);
    await waitFor(() => expect(calls.find((c) => c.url.pathname === "/api/easy/ollama/install")?.body).toEqual({ accept_unverified: true }));
  });

  it("can cancel the download, and says nothing was installed", async () => {
    let cancelled = false;
    const { calls } = routeFetch({
      "GET /api/easy/ollama/status": () => jsonResponse(status({}, cancelled ? { state: "cancelled" } : { state: "downloading", step: "Downloading", running: true, total: 100, done: 10 })),
      "GET /api/easy/ollama/plan": () => jsonResponse(PLAN),
      "GET /api/easy/ollama/install/status": () => jsonResponse({ ...idle, state: "cancelled", running: false }),
      "POST /api/easy/ollama/install/cancel": () => { cancelled = true; return jsonResponse({ ...idle, state: "downloading", running: true }); },
    });
    render(<OllamaSetup />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(calls.some((c) => c.url.pathname === "/api/easy/ollama/install/cancel")).toBe(true);
    expect(await screen.findByText("Cancelled. Nothing was installed.", {}, { timeout: 4000 })).toBeInTheDocument();
  });

  it("while installing (after the download) there is no cancel button and no percentage", async () => {
    routeFetch({ "GET /api/easy/ollama/status": () => jsonResponse(status({}, { state: "installing", step: "Installing Ollama for your user (about a minute; this step cannot be cancelled)", running: true, total: 100, done: 100 })) });
    render(<OllamaSetup />);
    expect(await screen.findByText(/this step cannot be cancelled/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(document.querySelector(".bar")).toBeNull();
  });

  it("shows why an install failed or needs a manual finish, with a way to check again", async () => {
    let state: Record<string, unknown> = { state: "failed", error: "The downloaded file does not match its published checksum." };
    const { calls } = routeFetch({ "GET /api/easy/ollama/status": () => jsonResponse(status({}, state)), "GET /api/easy/ollama/plan": () => jsonResponse(PLAN) });
    render(<OllamaSetup />);
    expect(await screen.findByText(/does not match its published checksum/)).toBeInTheDocument();
    state = { state: "needs_manual", needs_manual: true, error: "The silent install failed (code 5). The normal installer window was opened.", log: ["installer exited with code 5"] };
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    expect(await screen.findByText(/normal installer window was opened/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("Details"));
    expect(screen.getByText("installer exited with code 5")).toBeInTheDocument();
    expect(calls.filter((c) => c.url.pathname === "/api/easy/ollama/status").length).toBeGreaterThanOrEqual(2);
  });

  it("on a Mac or Linux it shows instructions instead of an install button", async () => {
    routeFetch({
      "GET /api/easy/ollama/status": () => jsonResponse(status()),
      "GET /api/easy/ollama/plan": () => jsonResponse({ supported: false, installed: false, manual: { title: "Install Ollama on a Mac", steps: ["Download Ollama for macOS.", "Or run brew install --cask ollama"], url: "https://ollama.com/download" } }),
    });
    render(<OllamaSetup />);
    expect(await screen.findByText("Install Ollama on a Mac")).toBeInTheDocument();
    expect(screen.getByText("Or run brew install --cask ollama")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "https://ollama.com/download" })).toHaveAttribute("href", "https://ollama.com/download");
    expect(screen.queryByRole("button", { name: "Install Ollama" })).toBeNull();
  });

  it("shows a refusal to install, and when the release details are unavailable", async () => {
    routeFetch({
      "GET /api/easy/ollama/status": () => jsonResponse(status()),
      "GET /api/easy/ollama/plan": () => jsonResponse({ ...PLAN, release: undefined, steps: undefined, size_gb: null }),
      "POST /api/easy/ollama/install": () => jsonResponse({ detail: "an install is already running" }, { status: 409 }),
    });
    render(<OllamaSetup />);
    await userEvent.click(await screen.findByRole("button", { name: "Install Ollama" }));
    expect(await screen.findByText("an install is already running")).toBeInTheDocument();
  });

  it("reports when Ollama cannot be checked at all", async () => {
    routeFetch({ "GET /api/easy/ollama/status": () => jsonResponse({ detail: "boom" }, { status: 500 }) });
    render(<OllamaSetup />);
    expect(await screen.findByText("boom")).toBeInTheDocument();
  });

  it("keeps polling through a failed status call", async () => {
    let n = 0;
    routeFetch({
      "GET /api/easy/ollama/status": () => jsonResponse(status({}, { state: "downloading", step: "Downloading", running: true })),
      "GET /api/easy/ollama/install/status": () => { n++; if (n === 1) return jsonResponse({ detail: "blip" }, { status: 500 }); return jsonResponse({ ...idle, state: "failed", error: "gone", running: false }); },
      "GET /api/easy/ollama/plan": () => jsonResponse(PLAN),
    });
    render(<OllamaSetup />);
    await waitFor(() => expect(n).toBeGreaterThanOrEqual(2), { timeout: 4500 });
  });

  it("a cancel request that fails changes nothing on screen", async () => {
    const { calls } = routeFetch({
      "GET /api/easy/ollama/status": () => jsonResponse(status({}, { state: "downloading", step: "Downloading", running: true, total: 100, done: 10 })),
      "GET /api/easy/ollama/plan": () => jsonResponse(PLAN),
      "GET /api/easy/ollama/install/status": () => jsonResponse({ ...idle, state: "downloading", step: "Downloading", running: true, total: 100, done: 20 }),
      "POST /api/easy/ollama/install/cancel": () => jsonResponse({ detail: "no" }, { status: 500 }),
    });
    render(<OllamaSetup />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/easy/ollama/install/cancel")).toBe(true));
    expect(screen.getByText(/Downloading · 10%/)).toBeInTheDocument();
  });

  it("keeps showing 'Working…' while the install is still running, and shows plain facts when the release has no details", async () => {
    let polls = 0;
    routeFetch({
      "GET /api/easy/ollama/status": () => jsonResponse(status({}, { state: "installing", step: "", running: true })),
      "GET /api/easy/ollama/install/status": () => { polls++; return jsonResponse({ ...idle, state: "installing", step: "", running: polls < 2 }); },
      "GET /api/easy/ollama/plan": () => jsonResponse(PLAN),
    });
    render(<OllamaSetup />);
    expect(await screen.findByText("Working…")).toBeInTheDocument();
    await waitFor(() => expect(polls).toBeGreaterThanOrEqual(2), { timeout: 4500 });
  });

  it("describes a release that has no version or size yet", async () => {
    routeFetch({
      "GET /api/easy/ollama/status": () => jsonResponse(status()),
      "GET /api/easy/ollama/plan": () => jsonResponse({ ...PLAN, release: { version: "", size: 0, source: "", verified: true, note: "" }, size_gb: null, steps: undefined }),
    });
    render(<OllamaSetup />);
    expect(await screen.findByText("Version latest")).toBeInTheDocument();
  });
});
