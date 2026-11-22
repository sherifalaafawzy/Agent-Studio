import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import McpPage from "@/app/mcp/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/mcp/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const mcpBase = { description: "Local model", env_set: {}, default_on: false, builtin: true, used_by: [] as any[], last_test: null as any, url: null as string | null,
  args: ["-m", "app.mcp_d1"], command: "{d1_python}", requires_env: [] as string[], transport: "stdio", enabled: false, status: "active", ready: false, category: "Local models" };
const d1Card = (over: Record<string, unknown> = {}) => ({ ...mcpBase, id: "d1", label: "Decision model (d1-2B, local)", ...over });
const browser = { browser: "chromium", headless: true, isolated: true, viewport: "1280x720", allowed_origins: [], blocked_origins: [] };

const gpu = { nvidia: true, gpu: "NVIDIA GeForce RTX 5080", vram_mb: 16303, driver: "610.88", cuda: [13, 3], torch_build: "PyTorch 2.15.0 + CUDA 13.2 build (cu132)", variant: "cuda" };
const cpu = { nvidia: false, gpu: null, vram_mb: null, driver: null, cuda: null, torch_build: "PyTorch 2.15.0 CPU build", variant: "cpu" };
const steps = (statuses: string[] = []) => ["venv", "pip", "torch", "libs", "weights", "verify"].map((id, i) => ({
  id, label: `Step ${id}`, size_mb: id === "verify" ? 0 : id === "torch" ? 2500 : 100, status: statuses[i] ?? "pending" }));
const d1 = (over: Record<string, unknown> = {}) => ({
  state: "not_installed", ready: false, error: null, step: null, steps: steps(), percent: 0, progress_text: "", log_tail: [] as string[],
  repo: "LiquidAI/d1-2B", revision: "a6bce774e6dde338dcc3036ceb5d91f78bf4c19e", home: "C:/data/envs/d1", versions: null as any, download_mb: 6559, disk_gb_needed: 9.6,
  free_gb: 120.5 as number | null, hardware: gpu as any, expect: "RTX 5080 expectation text", review_notes: ["No network access in the model code."],
  license: { name: "LFM Open License v1.0", url: "https://hf.test/LICENSE", summary: ["Free to use and modify.", "Commercial use only under US$10M revenue."] }, ...over });

type Over = Record<string, (init: RequestInit | undefined) => Response>;
function setup(status: any, over: Over = {}, list: any[] = [d1Card()]) {
  return routeFetch({
    "GET /api/mcp": () => jsonResponse(list),
    "GET /api/issues": () => jsonResponse([]),
    "GET /api/browser/settings": () => jsonResponse(browser),
    "GET /api/d1/status": over.status ?? (() => jsonResponse(status)),
    ...(over.install ? { "POST /api/d1/install": over.install } : {}),
    ...(over.progress ? { "GET /api/d1/install/progress": over.progress } : {}),
    ...(over.uninstall ? { "POST /api/d1/uninstall": over.uninstall } : {}),
  });
}

function toasts() {
  const seen: { text: string; tone: string }[] = [];
  const on = (e: Event) => seen.push((e as CustomEvent).detail);
  window.addEventListener("as:toast", on);
  return { seen, stop: () => window.removeEventListener("as:toast", on) };
}

const panel = () => document.querySelector("[data-tour='d1-card']") as HTMLElement;
const count = (calls: { method: string; url: URL }[], m: string, p: string) => calls.filter((c) => c.method === m && c.url.pathname === p).length;


/** The panel lives under the Decision model server's Settings tab; the only server in these lists is selected by itself. */
async function openSettings() {
  render(<McpPage />);
  await userEvent.click(await screen.findByRole("tab", { name: "Settings" }));
}

afterEach(() => { vi.useRealTimers(); });

describe("Decision model panel: install", () => {
  it("shows hardware, sizes, license and the security note before anything is downloaded", async () => {
    const { calls } = setup(d1());
    await openSettings();
    const p = within(await waitFor(() => { const e = panel(); expect(e).toBeTruthy(); return e; }));
    expect(p.getByText("d1-2B · local")).toBeInTheDocument();
    expect(p.getByText("not installed", { selector: ".pill" })).toBeInTheDocument();
    expect(p.getByText("RTX 5080 expectation text")).toBeInTheDocument();
    expect(p.getByText(/GPU: NVIDIA GeForce RTX 5080 \(CUDA 13\.3, driver 610\.88\) · build: PyTorch 2\.15\.0 \+ CUDA 13\.2 build/)).toBeInTheDocument();
    expect(p.getByText(/120\.5 GB free/)).toBeInTheDocument();
    expect(p.getByText(/Download about 6\.6 GB; needs about 9\.6 GB of disk\. Nothing downloads until you click Install\./)).toBeInTheDocument();
    expect(p.getByText("Commercial use only under US$10M revenue.")).toBeInTheDocument();
    expect(p.getByRole("link", { name: "Full license text" })).toHaveAttribute("href", "https://hf.test/LICENSE");
    expect(p.getByText("a6bce774e6dd", { selector: "code" })).toBeInTheDocument();
    expect(p.getByText("No network access in the model code.")).toBeInTheDocument();
    expect(p.getByText(/○ Step torch · ~2\.5 GB/)).toBeInTheDocument();
    expect(p.getByText("○ Step verify")).toBeInTheDocument();                                  // a zero-size step shows no size
    expect(p.getByText("○ Step pip · ~100 MB")).toBeInTheDocument();
    expect(count(calls, "POST", "/api/d1/install")).toBe(0);                                  // nothing happens by itself
  });

  it("will not install until the license is accepted, then sends the choice and shows progress", async () => {
    const t = toasts();
    const { calls } = setup(d1(), {
      install: () => jsonResponse(d1({ state: "installing", step: "pip", percent: 10, progress_text: "Collecting torch", log_tail: ["== Step pip", "Collecting torch"],
        steps: steps(["done", "running"]) })) });
    await openSettings();
    const p = within(await waitFor(() => { const e = panel(); expect(e).toBeTruthy(); return e; }));
    const btn = p.getByRole("button", { name: "Install" });
    expect(btn).toBeDisabled();
    await userEvent.click(p.getByLabelText(/I accept the LFM Open License v1\.0 terms/));
    expect(btn).toBeEnabled();
    await userEvent.selectOptions(p.getByLabelText("Device"), "cpu");
    await userEvent.click(btn);
    await waitFor(() => expect(count(calls, "POST", "/api/d1/install")).toBe(1));
    expect(calls.find((c) => c.url.pathname === "/api/d1/install")!.body).toEqual({ accept_license: true, device: "cpu" });
    expect(await p.findByText("10% · Collecting torch")).toBeInTheDocument();
    expect(p.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "10");
    expect(p.getByText(/== Step pip/)).toBeInTheDocument();
    expect(p.getByText("installing…", { selector: ".pill" })).toBeInTheDocument();
    expect(p.getByText(/you can leave this page/)).toBeInTheDocument();
    expect(p.queryByRole("button", { name: "Install" })).toBeNull();
    expect(p.getByText("✓ Step venv · ~100 MB")).toBeInTheDocument();
    expect(p.getByText("… Step pip · ~100 MB")).toBeInTheDocument();
    expect(t.seen.some((x) => x.text === "Installing the decision model…")).toBe(true);
    t.stop();
  });

  it("reports a refused install (for example not enough disk) and lets the user try again", async () => {
    const t = toasts();
    let n = 0;
    const { calls } = setup(d1(), { install: () => (++n === 1 ? jsonResponse({ detail: "not enough free disk space" }, { status: 422 }) : jsonResponse(d1({ state: "installing" }))) });
    await openSettings();
    const p = within(await waitFor(() => { const e = panel(); expect(e).toBeTruthy(); return e; }));
    await userEvent.click(p.getByLabelText(/I accept/));
    await userEvent.click(p.getByRole("button", { name: "Install" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "not enough free disk space" && x.tone === "err")).toBe(true));
    await waitFor(() => expect(p.getByRole("button", { name: "Install" })).toBeEnabled());
    expect(calls.find((c) => c.url.pathname === "/api/d1/install")!.body.device).toBe("auto");
    await userEvent.click(p.getByRole("button", { name: "Install" }));
    expect(await p.findByRole("progressbar")).toBeInTheDocument();
    t.stop();
  });

  it("offers the automatic build when no NVIDIA GPU exists and copes with unknown free space", async () => {
    setup(d1({ hardware: cpu, free_gb: null }));
    await openSettings();
    const p = within(await waitFor(() => { const e = panel(); expect(e).toBeTruthy(); return e; }));
    expect(p.getByText(/No NVIDIA GPU detected · build: PyTorch 2\.15\.0 CPU build/)).toBeInTheDocument();
    expect(p.queryByText(/GB free/)).toBeNull();
    expect(p.getByRole("option", { name: "Automatic" })).toBeInTheDocument();
  });

  it("labels the GPU build option when an NVIDIA GPU exists, even if the CUDA version is unknown", async () => {
    setup(d1({ hardware: { ...gpu, cuda: null } }));
    await openSettings();
    const p = within(await waitFor(() => { const e = panel(); expect(e).toBeTruthy(); return e; }));
    expect(p.getByRole("option", { name: "GPU (CUDA) build" })).toBeInTheDocument();
    expect(p.getByText(/\(CUDA \?, driver 610\.88\)/)).toBeInTheDocument();
  });
});

describe("Decision model panel: progress polling", () => {
  const advance = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(2100); }); };

  it("follows the install until it is ready, then refreshes the MCP list", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const t = toasts();
    let phase = 0;
    const { calls } = setup(d1({ state: "installing", step: "torch", percent: 20, progress_text: "Downloading torch", steps: steps(["done", "done", "running"]) }), {
      progress: () => jsonResponse(phase === 0
        ? { state: "installing", ready: false, error: null, step: "weights", steps: steps(["done", "done", "done", "done", "running"]), percent: 64, progress_text: "Downloading model weights: 1.50 of 3.87 GB", log_tail: [] }
        : { state: "ready", ready: true, error: null, step: null, steps: steps(), percent: 100, progress_text: "", log_tail: [] }),
      status: () => jsonResponse(phase === 0 ? d1({ state: "installing", step: "torch", percent: 20, progress_text: "Downloading torch", steps: steps(["done", "done", "running"]) })
        : d1({ state: "ready", ready: true, percent: 100, versions: { torch: "2.15.0+cu132", transformers: "5.22.2", cuda: true, gpu: "NVIDIA GeForce RTX 5080" } })),
    });
    await openSettings();
    const p = within(await waitFor(() => { const e = panel(); expect(e).toBeTruthy(); return e; }));
    await waitFor(() => expect(p.getByText("20% · Downloading torch")).toBeInTheDocument());
    const listBefore = count(calls, "GET", "/api/mcp");
    await advance();
    await waitFor(() => expect(p.getByText("64% · Downloading model weights: 1.50 of 3.87 GB")).toBeInTheDocument());
    expect(count(calls, "GET", "/api/mcp")).toBe(listBefore);                                 // still installing: no list reload
    phase = 1;
    await advance();
    await waitFor(() => expect(p.getByText("ready", { selector: ".pill" })).toBeInTheDocument());
    expect(count(calls, "GET", "/api/mcp")).toBe(listBefore + 1);                             // the catalogue card re-reads its readiness
    expect(t.seen.some((x) => x.text === "Decision model installed" && x.tone === "ok")).toBe(true);
    expect(p.getByText(/Installed in/)).toHaveTextContent("torch 2.15.0+cu132, transformers 5.22.2, CUDA GPU NVIDIA GeForce RTX 5080");
    expect(p.getByRole("button", { name: /Uninstall/ })).toBeInTheDocument();
    expect(p.queryByText(/License:/)).toBeNull();                                            // consent UI is gone once installed
    t.stop();
  });

  it("shows a failed install with its error and a retry, and keeps polling through a network blip", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const t = toasts();
    let phase = 0;
    setup(d1({ state: "installing", percent: 5 }), {
      progress: () => (phase === 0 ? textResponse("boom", 500) : jsonResponse({ state: "failed", ready: false, error: "x", step: null, steps: steps(), percent: 5, progress_text: "", log_tail: [] })),
      status: () => jsonResponse(phase === 0 ? d1({ state: "installing", percent: 5 })
        : d1({ state: "failed", error: "Step 'Install PyTorch' failed (exit code 1). No matching distribution", steps: steps(["done", "done", "failed"]), log_tail: ["ERROR: No matching distribution"] })),
    });
    await openSettings();
    const p = within(await waitFor(() => { const e = panel(); expect(e).toBeTruthy(); return e; }));
    await waitFor(() => expect(p.getByRole("progressbar")).toBeInTheDocument());
    await advance();                                                                          // the poll fails: nothing breaks
    expect(p.getByRole("progressbar")).toBeInTheDocument();
    phase = 1;
    await advance();
    expect(await p.findByText(/No matching distribution/, { selector: ".err" })).toBeInTheDocument();
    expect(p.getByText("failed", { selector: ".pill" })).toHaveClass("bad");
    expect(p.getByRole("button", { name: "Retry install" })).toBeInTheDocument();
    expect(p.getByText("✗ Step torch · ~2.5 GB")).toBeInTheDocument();
    expect(t.seen.some((x) => x.text === "Decision model install failed" && x.tone === "err")).toBe(true);
    expect(p.getByRole("button", { name: /Uninstall/ })).toBeInTheDocument();
    t.stop();
  });
});

describe("Decision model panel: installed", () => {
  it("shows a CPU-only install and works without recorded versions", async () => {
    setup(d1({ state: "ready", ready: true, versions: { torch: "2.15.0+cpu", transformers: "5.22.2", cuda: false, gpu: null } }));
    await openSettings();
    const p = within(await waitFor(() => { const e = panel(); expect(e).toBeTruthy(); return e; }));
    expect(await p.findByText(/Installed in/)).toHaveTextContent("CPU only");
    expect(p.queryByRole("button", { name: "Install" })).toBeNull();
  });

  it("omits the version line when none was recorded", async () => {
    setup(d1({ state: "ready", ready: true, versions: null }));
    await openSettings();
    const p = within(await waitFor(() => { const e = panel(); expect(e).toBeTruthy(); return e; }));
    expect(await p.findByText(/Installed in/)).not.toHaveTextContent("torch");
  });

  it("uninstalls only after confirmation and reports errors", async () => {
    const t = toasts();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    let n = 0;
    const { calls } = setup(d1({ state: "ready", ready: true }), { uninstall: () => (++n === 1 ? jsonResponse({ removed: true }) : jsonResponse({ detail: "an install is running" }, { status: 409 })) });
    await openSettings();
    const p = within(await waitFor(() => { const e = panel(); expect(e).toBeTruthy(); return e; }));
    await userEvent.click(await p.findByRole("button", { name: /Uninstall/ }));
    expect(confirm).toHaveBeenCalledWith("Remove the decision model environment and its downloaded weights?");
    expect(count(calls, "POST", "/api/d1/uninstall")).toBe(0);
    confirm.mockReturnValue(true);
    const listBefore = count(calls, "GET", "/api/mcp");
    await userEvent.click(p.getByRole("button", { name: /Uninstall/ }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "Decision model removed")).toBe(true));
    await waitFor(() => expect(count(calls, "GET", "/api/mcp")).toBe(listBefore + 1));
    await userEvent.click(p.getByRole("button", { name: /Uninstall/ }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "an install is running" && x.tone === "err")).toBe(true));
    t.stop();
  });

  it("renders no panel when the backend has no decision-model API", async () => {
    const { calls } = setup(d1(), { status: () => textResponse("nope", 404) });
    await openSettings();
    await screen.findByRole("heading", { name: "Decision model (d1-2B, local)" });          // the server itself still shows
    await waitFor(() => expect(count(calls, "GET", "/api/d1/status")).toBe(1));
    expect(panel()).toBeNull();
  });
});

describe("Decision model in the list", () => {
  it("is grouped under Needs setup and says it is not installed instead of asking for credentials", async () => {
    setup(d1(), {}, [d1Card({ enabled: true }), d1Card({ id: "d1b", label: "Second", enabled: false })]);
    render(<McpPage />);
    const row = await screen.findByRole("option", { name: /Decision model/ });
    const list = screen.getByRole("listbox", { name: "MCP servers" });
    expect(within(screen.getByRole("group", { name: "Needs setup" })).getByText("Decision model (d1-2B, local)")).toBeInTheDocument();
    expect(within(row).getByText("not installed", { selector: ".pill" })).toHaveClass("warn");
    expect(screen.queryByText("needs credentials")).toBeNull();
    await userEvent.click(await screen.findByRole("tab", { name: /Health/ }));
    expect(screen.getByRole("button", { name: /Test/ })).toBeDisabled();
    const other = within(list).getByRole("option", { name: /Second/ });
    expect(within(other).getByText("off", { selector: ".pill" })).toBeInTheDocument();       // only the d1 id gets the install wording
  });

  it("is ready once installed and switched on", async () => {
    setup(d1(), {}, [d1Card({ enabled: true, ready: true })]);
    render(<McpPage />);
    const ok = await screen.findByRole("option", { name: /Decision model/ });
    expect(within(ok).getByText("ready", { selector: ".pill" })).toBeInTheDocument();
  });

  it("keeps the approval wording for an agent-proposed server that reuses the id", async () => {
    setup(d1(), {}, [d1Card({ label: "Proposed d1", status: "pending_approval", enabled: false })]);
    render(<McpPage />);
    const pending = await screen.findByRole("option", { name: /Proposed d1/ });
    expect(within(pending).getByText("needs approval")).toBeInTheDocument();
  });
});
