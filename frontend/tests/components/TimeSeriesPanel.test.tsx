import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import TimeSeriesPanel, { parseSeries } from "@/components/TimeSeriesPanel";
import { jsonResponse, routeFetch } from "../helpers/http";

const lic = (commercial: "yes" | "restricted" | "no", name = "Apache-2.0") => ({ name, url: "https://hf.test/LICENSE", summary: [`${name} summary line`], commercial });
const model = (id: string, over: Record<string, unknown> = {}) => ({
  id, label: `Model ${id}`, repo: `org/${id}`, params: "120M", kind: "series", state: "not_installed", error: null, download_bytes: 478_000_000, max_context: 8192, max_horizon: 1024,
  quantile_levels: [0.1, 0.5, 0.9], multivariate: true, covariates: true, license: lic("yes"), good_for: `Good for ${id}.`, caveats: `Caveat of ${id}.`, notes: [] as string[], skipped: "",
  blocked: "", available_from: "", revision: "29ec3766d36d6f73f0696f85560a422f50e8498c", source: null as any, versions: null as any, ...over });
const steps = (statuses: string[] = []) => ["venv", "weights", "verify"].map((id, i) => ({ id, label: `Step ${id}`, size_mb: id === "verify" ? 0 : 100, model: null, status: statuses[i] ?? "pending" }));
const install = (over: Record<string, unknown> = {}) => ({ state: "idle", step: null, steps: [] as any[], percent: 0, progress_text: "", log_tail: [] as string[], error: null, queue: [] as string[], ...over });
const gpu = { nvidia: true, gpu: "RTX 5080", cuda: [13, 3], driver: "610.88", torch_build: "PyTorch 2.15.0 + CUDA 13.2 build", variant: "cuda" };
const cpu = { nvidia: false, gpu: null, cuda: null, driver: null, torch_build: "PyTorch 2.15.0 CPU build", variant: "cpu" };
const status = (models: any[], over: Record<string, unknown> = {}) => ({
  env: { ready: false, home: "C:/data/envs/timeseries", torch: null, python: null, free_gb: 120.5 }, hardware: gpu, expect: "RTX expectation text", install: install(), models, ...over });
const plan = (over: Record<string, unknown> = {}) => ({ steps: [], download_mb: 3100, disk_gb_needed: 6.4, free_gb: 100, torch_build: "x", variant: "cuda", env_needed: true, expect: "", ...over });

const CHRONOS = model("chronos-2", { label: "Chronos-2 (Amazon)" });
const TIMESFM = model("timesfm-3", { label: "TimesFM 3.0 (Google)", license: lic("no", "TimesFM Non-Commercial License v1.0"), download_bytes: 1_323_000_000, max_context: null, max_horizon: null, quantile_levels: [] });
const CI = model("ci-proto", { label: "ci-proto", state: "blocked", error: "its newest package is under 14 days old; Agent Studio installs it from 2026-11-26", skipped: "model-long.ckpt (668,921,710 bytes)" });
const TIMESTRAL = model("timestral-5m", { label: "Timestral 5M", license: lic("restricted", "Modified MIT"), source: { repo: "mistralai/timestral", sha: "a534bdbd244d64f3d9d743f46c7bf6fc330ed4ff" }, notes: ["Horizons over 1024 are chunked."], multivariate: false, download_bytes: 21_000_000 });
const KRONOS = model("kronos-base", { label: "Kronos base", kind: "ohlcv", state: "ready", params: "102M" });

type Over = Record<string, (init: RequestInit | undefined, url: URL) => Response | Promise<Response>>;
function setup(st: any, over: Over = {}) {
  return routeFetch({ "GET /api/ts/status": () => jsonResponse(st), ...over });
}
const toasts = () => {
  const seen: { text: string; tone: string }[] = [];
  const on = (e: Event) => seen.push((e as CustomEvent).detail);
  window.addEventListener("as:toast", on);
  return { seen, stop: () => window.removeEventListener("as:toast", on) };
};
const panel = async () => within(await waitFor(() => { const e = document.querySelector("[data-tour='ts-card']") as HTMLElement; expect(e).toBeTruthy(); return e; }));
const count = (calls: { method: string; url: URL }[], m: string, p: string) => calls.filter((c) => c.method === m && c.url.pathname === p).length;

afterEach(() => { vi.useRealTimers(); });

describe("parseSeries", () => {
  it("accepts commas, spaces, semicolons, new lines and JSON arrays", () => {
    expect(parseSeries("1, 2 3;4\n5,6,7,8").values).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(parseSeries("[1,2,3,4,5,6,7,\"8\"]").values).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
  });
  it("explains what is wrong", () => {
    expect(parseSeries("   ").error).toMatch(/Paste the history/);
    expect(parseSeries("[1,2").error).toMatch(/does not parse/);
    expect(parseSeries("1 2 x 4").error).toBe('Value 3 ("x") is not a number.');
    expect(parseSeries("[1, null, 3]").error).toBe('Value 2 ("null") is not a number.');
    expect(parseSeries('[1, "", 3]').error).toBe('Value 2 ("") is not a number.');
    expect(parseSeries("[1, {}, 3]").error).toBe('Value 2 ("[object Object]") is not a number.');
    expect(parseSeries("1 2 3").error).toBe("Need at least 8 values (got 3).");
  });
});

describe("Time-series panel: the model list", () => {
  it("renders nothing until the status arrives, and nothing when it cannot be fetched", async () => {
    routeFetch({});
    const { container } = render(<TimeSeriesPanel onChange={() => {}} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector("[data-tour='ts-card']")).toBeNull();
  });

  it("shows hardware, every model with its state, size and licence, and the details", async () => {
    setup(status([CHRONOS, TIMESFM, TIMESTRAL, CI, KRONOS]));
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    expect(p.getByText("zero-shot · local")).toBeInTheDocument();
    expect(p.getByText("1 of 5 installed")).toBeInTheDocument();
    expect(p.getByText("RTX expectation text")).toBeInTheDocument();
    expect(p.getByText(/GPU: RTX 5080 \(CUDA 13\.3, driver 610\.88\) · build: PyTorch 2\.15\.0 \+ CUDA 13\.2 build · 120\.5 GB free/)).toBeInTheDocument();
    const row = (id: string) => within(document.querySelector(`[data-model='${id}']`) as HTMLElement);
    expect(row("chronos-2").getByText("120M · any series · 478 MB")).toBeInTheDocument();
    expect(row("chronos-2").getByText("commercial use OK")).toBeInTheDocument();
    expect(row("timesfm-3").getByText("non-commercial only")).toBeInTheDocument();
    expect(row("timesfm-3").getByText(/1\.3 GB/)).toBeInTheDocument();
    expect(row("timestral-5m").getByText("commercial use limited")).toBeInTheDocument();
    expect(row("kronos-base").getByText(/candlesticks only/)).toBeInTheDocument();
    expect(row("ci-proto").getByText("blocked")).toBeInTheDocument();
    expect(row("ci-proto").getByText(/Not installable yet: its newest package is under 14 days old/)).toBeInTheDocument();
    expect(row("ci-proto").getByText("Not downloaded: model-long.ckpt (668,921,710 bytes).")).toBeInTheDocument();
    expect(row("chronos-2").getByText("Caveat of chronos-2.")).toBeInTheDocument();
    expect(row("chronos-2").getByText(/Reads up to 8,192 steps history, forecasts up to 1,024 steps at once; 3 quantile levels; multivariate-capable\./)).toBeInTheDocument();
    expect(row("timesfm-3").getByText(/Reads up to a very long history; no quantile band; multivariate-capable\./)).toBeInTheDocument();
    expect(row("timestral-5m").getByText("Horizons over 1024 are chunked.")).toBeInTheDocument();
    expect(row("timestral-5m").getByText("a534bdbd", { exact: false, selector: "code" })).toBeInTheDocument();
    expect(row("timestral-5m").getByText(/3 quantile levels\./)).toBeInTheDocument();                       // not multivariate: no suffix
    expect(row("chronos-2").getByRole("link", { name: /Full licence text \(Apache-2\.0\)/ })).toHaveAttribute("href", "https://hf.test/LICENSE");
    expect(screen.getByLabelText("Select Chronos-2 (Amazon)")).toBeEnabled();
    expect(screen.getByLabelText("Select ci-proto")).toBeDisabled();                                         // blocked
    expect(screen.getByLabelText("Select Kronos base")).toBeDisabled();                                      // already installed
    expect(p.getByText(/Tick the models you want, accept each licence/)).toBeInTheDocument();
    expect(p.getByText(/Turn the "Time-series forecasting" server on below/)).toBeInTheDocument();
  });

  it("shows versions of installed models, a CPU-only note, and failed models with their error", async () => {
    setup(status([model("a", { state: "ready", versions: { library: "2.3.2", torch: "2.15.0", cuda: true, gpu: "RTX" } }), model("b", { state: "ready", versions: { library: "1", torch: "2", cuda: false } }),
      model("c", { state: "failed", error: "Step 'download weights' failed (exit code 1)" }), model("d", { state: "ready", versions: null })], { hardware: cpu, env: { ready: true, home: "h", torch: "cpu", python: "3.12", free_gb: null } }));
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    expect(p.getByText("library 2.3.2, torch 2.15.0, CUDA GPU RTX")).toBeInTheDocument();
    expect(p.getByText("library 1, torch 2, CPU only")).toBeInTheDocument();
    expect(p.getByText("Step 'download weights' failed (exit code 1)")).toBeInTheDocument();
    expect(p.getByText(/No NVIDIA GPU detected · build: PyTorch 2\.15\.0 CPU build/)).toBeInTheDocument();
    expect(p.queryByText(/GB free/)).toBeNull();
    expect(screen.getByLabelText("Select Model c")).toBeEnabled();                                           // a failed model can be retried
  });

  it("labels the GPU build option and copes with an unknown CUDA version", async () => {
    setup(status([CHRONOS], { hardware: { ...gpu, cuda: null } }));
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    expect(p.getByText(/\(CUDA \?, driver/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Select Chronos-2 (Amazon)"));
    expect(await p.findByRole("option", { name: "GPU (CUDA) build" })).toBeInTheDocument();
  });

  it("says nothing about picking models once everything is installed", async () => {
    setup(status([model("a", { state: "ready" })]));
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    expect(p.queryByText(/Tick the models you want/)).toBeNull();
  });
});

describe("Time-series panel: installing", () => {
  it("needs the licence of every selected model, shows the plan, then sends the choice", async () => {
    const t = toasts();
    const { calls } = setup(status([CHRONOS, TIMESFM, CI]), {
      "GET /api/ts/plan": (_i, url) => jsonResponse(plan({ download_mb: url.searchParams.get("models") === "chronos-2" ? 3100 : 4400 })),
      "POST /api/ts/install": () => jsonResponse(status([{ ...CHRONOS, state: "installing" }, TIMESFM], { install: install({ state: "installing", steps: steps(["done", "running"]), percent: 10, progress_text: "x" }) })) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await userEvent.click(screen.getByLabelText("Select Chronos-2 (Amazon)"));
    expect(await p.findByText(/Download about 3\.1 GB \(including the shared Python environment\); needs about 6\.4 GB of disk\. Nothing downloads until you click Install\./)).toBeInTheDocument();
    expect(calls.find((c) => c.url.pathname === "/api/ts/plan")!.url.searchParams.get("models")).toBe("chronos-2");
    expect(p.getByRole("button", { name: "Install 1 selected" })).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Select TimesFM 3.0 (Google)"));
    await waitFor(() => expect(p.getByText(/Download about 4\.4 GB/)).toBeInTheDocument());
    await userEvent.click(p.getByLabelText("Accept the Chronos-2 (Amazon) licence"));
    expect(p.getByRole("button", { name: "Install 2 selected" })).toBeDisabled();                           // TimesFM's licence is still open
    await userEvent.click(p.getByLabelText("Accept the TimesFM 3.0 (Google) licence"));
    await userEvent.click(p.getByLabelText("Accept the TimesFM 3.0 (Google) licence"));                       // un-accept again ...
    expect(p.getByRole("button", { name: "Install 2 selected" })).toBeDisabled();
    await userEvent.click(p.getByLabelText("Accept the TimesFM 3.0 (Google) licence"));                       // ... and accept
    await userEvent.selectOptions(p.getByLabelText("Device"), "cpu");
    await userEvent.click(p.getByRole("button", { name: "Install 2 selected" }));
    await waitFor(() => expect(count(calls, "POST", "/api/ts/install")).toBe(1));
    expect(calls.find((c) => c.url.pathname === "/api/ts/install")!.body).toEqual({ models: ["chronos-2", "timesfm-3"], accepted: ["chronos-2", "timesfm-3"], device: "cpu" });
    expect(await p.findByRole("progressbar")).toBeInTheDocument();
    expect(t.seen.some((x) => x.text === "Installing…")).toBe(true);
    t.stop();
  });

  it("deselecting removes the plan; a plan error is shown; a plan without the environment says so", async () => {
    let n = 0;
    setup(status([CHRONOS]), { "GET /api/ts/plan": () => (++n === 1 ? jsonResponse({ detail: "choose at least one model" }, { status: 422 }) : jsonResponse(plan({ env_needed: false }))) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    const box = screen.getByLabelText("Select Chronos-2 (Amazon)");
    await userEvent.click(box);
    expect(await p.findByText("choose at least one model")).toBeInTheDocument();
    await userEvent.click(box);
    expect(p.queryByText("choose at least one model")).toBeNull();
    await userEvent.click(box);
    const sum = await p.findByText(/Download about 3\.1 GB; needs about 6\.4 GB/);
    expect(sum.textContent).not.toMatch(/including the shared/);
    await userEvent.selectOptions(p.getByLabelText("Device"), "cpu");
    await waitFor(() => expect(n).toBe(3));
  });

  it("shows the checking text before the plan arrives", async () => {
    let release: (r: Response) => void = () => {};
    setup(status([CHRONOS]), { "GET /api/ts/plan": () => new Promise<Response>((res) => { release = res; }) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await userEvent.click(screen.getByLabelText("Select Chronos-2 (Amazon)"));
    expect(p.getByText("Checking the size…")).toBeInTheDocument();
    await act(async () => { release(jsonResponse(plan())); });
    expect(await p.findByText(/Download about/)).toBeInTheDocument();
  });

  it("ignores a plan that arrives after the selection changed", async () => {
    const waiting: ((r: Response) => void)[] = [];
    setup(status([CHRONOS]), { "GET /api/ts/plan": () => new Promise<Response>((res) => { waiting.push(res); }) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    const box = screen.getByLabelText("Select Chronos-2 (Amazon)");
    await userEvent.click(box);                                                                              // request 1 (never answered in time)
    await userEvent.click(box);                                                                              // deselect: request 1 is stale
    await userEvent.click(box);                                                                              // request 2
    await userEvent.click(box);                                                                              // deselect again
    await act(async () => { waiting[0](jsonResponse(plan({ download_mb: 1 }))); waiting[1](jsonResponse({ detail: "late failure" }, { status: 422 })); });
    expect(p.queryByText(/Download about/)).toBeNull();
    expect(p.queryByText("late failure")).toBeNull();
  });

  it("offers the automatic device choice when there is no NVIDIA GPU", async () => {
    setup(status([CHRONOS], { hardware: cpu }), { "GET /api/ts/plan": () => jsonResponse(plan()) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await userEvent.click(screen.getByLabelText("Select Chronos-2 (Amazon)"));
    expect(await p.findByRole("option", { name: "Automatic" })).toBeInTheDocument();
  });

  it("reports a refused install and lets the user try again", async () => {
    const t = toasts();
    let n = 0;
    const { calls } = setup(status([CHRONOS]), { "GET /api/ts/plan": () => jsonResponse(plan()),
      "POST /api/ts/install": () => (++n === 1 ? jsonResponse({ detail: "not enough free disk space" }, { status: 422 }) : jsonResponse(status([{ ...CHRONOS, state: "installing" }], { install: install({ state: "installing" }) }))) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await userEvent.click(screen.getByLabelText("Select Chronos-2 (Amazon)"));
    await userEvent.click(p.getByLabelText("Accept the Chronos-2 (Amazon) licence"));
    await userEvent.click(p.getByRole("button", { name: "Install 1 selected" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "not enough free disk space" && x.tone === "err")).toBe(true));
    await waitFor(() => expect(p.getByRole("button", { name: "Install 1 selected" })).toBeEnabled());
    expect(calls.find((c) => c.url.pathname === "/api/ts/install")!.body.device).toBe("auto");
    await userEvent.click(p.getByRole("button", { name: "Install 1 selected" }));
    expect(await p.findByRole("progressbar")).toBeInTheDocument();
    t.stop();
  });

  it("shows the steps and progress while installing and disables selecting", async () => {
    setup(status([{ ...CHRONOS, state: "installing" }, TIMESFM], { install: install({ state: "installing", step: "weights", steps: steps(["done", "running"]), percent: 42, progress_text: "Downloading: 2 of 3 MB", log_tail: ["== Step weights", "Collecting x"] }) }));
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    expect(p.getByText("✓ Step venv · ~100 MB")).toBeInTheDocument();
    expect(p.getByText("… Step weights · ~100 MB")).toBeInTheDocument();
    expect(p.getByText("○ Step verify")).toBeInTheDocument();
    expect(p.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
    expect(p.getByText(/42% · Downloading: 2 of 3 MB\. You can leave this page/)).toBeInTheDocument();
    expect(p.getByText(/Collecting x/)).toBeInTheDocument();
    expect(screen.getByLabelText("Select TimesFM 3.0 (Google)")).toBeDisabled();
    expect(p.queryByRole("button", { name: /Install/ })).toBeNull();
  });

  it("hides an empty log and shows a failed install's error", async () => {
    setup(status([{ ...CHRONOS, state: "failed", error: "boom" }], { install: install({ state: "failed", error: "Step 'x' failed (exit code 1). No matching distribution" }) }));
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    expect(p.getByText("Step 'x' failed (exit code 1). No matching distribution")).toBeInTheDocument();
    expect(p.queryByRole("progressbar")).toBeNull();
  });
});

describe("Time-series panel: progress polling", () => {
  const advance = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(2100); }); };
  const running = () => status([{ ...CHRONOS, state: "installing" }], { install: install({ state: "installing", steps: steps(["running"]), percent: 5 }) });

  it("follows the install until it is done, then reloads and tells the app", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const t = toasts();
    const onChange = vi.fn();
    let polls = 0;
    const { calls } = setup(running(), {
      "GET /api/ts/install/progress": () => (++polls === 1 ? jsonResponse({ ...install({ state: "installing", percent: 60, progress_text: "Downloading" }), models: {} })
        : jsonResponse({ ...install({ state: "idle" }), models: { "chronos-2": "ready" } })) });
    render(<TimeSeriesPanel onChange={onChange} />);
    const p = await panel();
    await advance();
    expect(await p.findByText(/60% · Downloading/)).toBeInTheDocument();
    routeFetch({ "GET /api/ts/status": () => jsonResponse(status([{ ...CHRONOS, state: "ready" }])), "GET /api/ts/install/progress": () => jsonResponse({ ...install({ state: "idle" }), models: {} }) });
    await advance();
    await waitFor(() => expect(p.getByText("1 of 1 installed")).toBeInTheDocument());
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(t.seen.some((x) => x.text === "Models installed" && x.tone === "ok")).toBe(true);
    expect(count(calls, "GET", "/api/ts/install/progress")).toBe(1);
    t.stop();
  });

  it("reports a failed install and keeps polling through backend hiccups", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const t = toasts();
    let polls = 0;
    setup(running(), { "GET /api/ts/install/progress": () => { if (++polls === 1) throw new Error("backend restarting"); return jsonResponse({ ...install({ state: "failed", error: "pip failed" }), models: {} }); } });
    render(<TimeSeriesPanel onChange={() => {}} />);
    await panel();
    await advance();
    expect(t.seen).toEqual([]);                                                                              // the first poll failed: nothing reported
    routeFetch({ "GET /api/ts/status": () => jsonResponse(status([{ ...CHRONOS, state: "failed", error: "pip failed" }], { install: install({ state: "failed", error: "pip failed" }) })),
      "GET /api/ts/install/progress": () => jsonResponse({ ...install({ state: "failed", error: "pip failed" }), models: {} }) });
    await advance();
    await waitFor(() => expect(t.seen.some((x) => x.text === "Model install failed" && x.tone === "err")).toBe(true));
    t.stop();
  });
});

describe("Time-series panel: uninstall", () => {
  it("removes a model only after confirmation and reports errors", async () => {
    const t = toasts();
    const onChange = vi.fn();
    let fail = true;
    const { calls } = setup(status([{ ...CHRONOS, state: "ready" }]), { "POST /api/ts/uninstall/chronos-2": () => (fail ? jsonResponse({ detail: "an install is running" }, { status: 409 }) : jsonResponse({ removed: true })) });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<TimeSeriesPanel onChange={onChange} />);
    const p = await panel();
    await userEvent.click(p.getByRole("button", { name: "Uninstall Chronos-2 (Amazon)" }));
    expect(confirm).toHaveBeenCalledWith("Remove Chronos-2 (Amazon) and its downloaded weights?");
    expect(count(calls, "POST", "/api/ts/uninstall/chronos-2")).toBe(0);
    confirm.mockReturnValue(true);
    await userEvent.click(p.getByRole("button", { name: "Uninstall Chronos-2 (Amazon)" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "an install is running" && x.tone === "err")).toBe(true));
    fail = false;
    await userEvent.click(p.getByRole("button", { name: "Uninstall Chronos-2 (Amazon)" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "Chronos-2 (Amazon) removed")).toBe(true));
    expect(onChange).toHaveBeenCalledTimes(1);
    t.stop();
  });
});

describe("Time-series panel: try it", () => {
  const history = Array.from({ length: 10 }, (_, i) => 10 + i).join(", ");
  const fc = (label: string, base: number, h: number) => ({ label, median: Array.from({ length: h }, (_, i) => base + i), quantiles: { "0.1": Array.from({ length: h }, (_, i) => base + i - 1), "0.9": Array.from({ length: h }, (_, i) => base + i + 1) },
    device: "cpu", latency_ms: 1500, loaded_now: true });
  const ready = [{ ...CHRONOS, state: "ready" }, { ...model("toto-2-4m", { label: "Toto 4M" }), state: "ready" }, KRONOS, TIMESFM];

  async function fill(p: ReturnType<typeof within>, text = history, horizon = "3") {
    await userEvent.clear(p.getByLabelText("Series"));
    await userEvent.type(p.getByLabelText("Series"), text);
    await userEvent.clear(p.getByLabelText("Horizon"));
    await userEvent.type(p.getByLabelText("Horizon"), horizon);
  }

  it("is only offered once a model is installed, and lists only installed series models", async () => {
    setup(status([CHRONOS]));
    const { unmount } = render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    expect(p.queryByText("Try it")).toBeNull();
    unmount();
    setup(status(ready));
    render(<TimeSeriesPanel onChange={() => {}} />);
    const q = await panel();
    expect(q.getByText("Try it")).toBeInTheDocument();
    expect(q.getByLabelText("Use Chronos-2 (Amazon)")).toBeChecked();
    expect(q.getByLabelText("Use Toto 4M")).toBeChecked();
    expect(q.queryByLabelText("Use Kronos base")).toBeNull();                                                // candlesticks have their own tool
    expect(q.queryByLabelText("Use TimesFM 3.0 (Google)")).toBeNull();                                       // not installed
  });

  it("validates the input before calling the backend", async () => {
    const { calls } = setup(status(ready));
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    expect(await p.findByRole("alert")).toHaveTextContent(/Paste the history/);
    await fill(p, history, "0");
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    expect(p.getByRole("alert")).toHaveTextContent("Horizon must be a whole number of steps, 1 or more.");
    await fill(p, history, "2.5");
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    expect(p.getByRole("alert")).toHaveTextContent("Horizon must be a whole number");
    await fill(p, history, "3");
    await userEvent.click(p.getByRole("button", { name: "None" }));
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    expect(p.getByRole("alert")).toHaveTextContent("Pick at least one installed model.");
    expect(count(calls, "POST", "/api/ts/forecast")).toBe(0);
    await userEvent.click(p.getByRole("button", { name: "All" }));
    expect(p.getByLabelText("Use Chronos-2 (Amazon)")).toBeChecked();
  });

  it("forecasts with the chosen models and draws the chart, ensemble and table", async () => {
    const { calls } = setup(status(ready), { "POST /api/ts/forecast": () => jsonResponse({ ok: true, horizon: 3, n_context: 10, quantile_levels: [0.1, 0.5, 0.9],
      models: { "chronos-2": fc("Chronos-2 (Amazon)", 20, 3), "toto-2-4m": { ...fc("Toto 4M", 22, 3), loaded_now: false } },
      ensemble: { median: [21, 22, 23], quantiles: { "0.1": [20, 21, 22], "0.9": [22, 23, 24] }, members: ["chronos-2", "toto-2-4m"] }, errors: { "timestral-5m": "Timestral failed to load" } }) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await fill(p);
    await userEvent.click(p.getByLabelText("Use Toto 4M"));                                                  // one model fewer ...
    await userEvent.click(p.getByLabelText("Use Toto 4M"));                                                  // ... and back
    await userEvent.click(p.getByLabelText(/Also backtest/));
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    expect(await p.findByTestId("ts-line-chronos-2")).toBeInTheDocument();
    expect(calls.find((c) => c.url.pathname === "/api/ts/forecast")!.body).toEqual({ series: [10, 11, 12, 13, 14, 15, 16, 17, 18, 19], horizon: 3, models: ["chronos-2", "toto-2-4m"], quantiles: [0.1, 0.5, 0.9], backtest: true });
    expect(p.getByTestId("ts-line-toto-2-4m")).toBeInTheDocument();
    expect(p.getByTestId("ts-line-ensemble")).toBeInTheDocument();
    expect(p.getByTestId("ts-band")).toBeInTheDocument();
    expect(p.getByRole("img", { name: /Forecast chart/ })).toBeInTheDocument();
    expect(p.getByText("shaded: 0.1–0.9 band of the ensemble")).toBeInTheDocument();
    expect(p.getByText("ensemble (mean)")).toBeInTheDocument();
    expect(p.getByText("20.00 (19.0–21.0)")).toBeInTheDocument();
    expect(p.getByText("1.5 s on cpu (loaded)")).toBeInTheDocument();
    expect(p.getByText("1.5 s on cpu")).toBeInTheDocument();
    expect(p.getByText("Ensemble (mean)")).toBeInTheDocument();
    expect(p.getByText("2 models")).toBeInTheDocument();
    expect(p.getByText("timestral-5m: Timestral failed to load")).toBeInTheDocument();
  });

  it("draws one model's own band and handles a one-step horizon", async () => {
    setup(status(ready), { "POST /api/ts/forecast": () => jsonResponse({ ok: true, horizon: 1, n_context: 10, quantile_levels: [0.1, 0.5, 0.9], models: { "chronos-2": fc("Chronos-2 (Amazon)", 20, 1) }, ensemble: null }) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await fill(p, history, "1");
    await userEvent.click(p.getByLabelText("Use Toto 4M"));
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    expect(await p.findByText("shaded: 0.1–0.9 band of the model")).toBeInTheDocument();
    expect(p.getByTestId("ts-band")).toBeInTheDocument();
    expect(p.queryByTestId("ts-line-ensemble")).toBeNull();
    expect(p.getAllByText("–").length).toBeGreaterThanOrEqual(2);                                            // steps 2 and 3 do not exist
  });

  it("draws no band when several models ran without an ensemble, and a two-step horizon fills step 2 only", async () => {
    setup(status(ready), { "POST /api/ts/forecast": () => jsonResponse({ ok: true, horizon: 2, quantile_levels: [0.1, 0.5, 0.9], models: { "chronos-2": fc("A", 20, 2), "toto-2-4m": fc("B", 30, 2) } }) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await fill(p, history, "2");
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    await p.findByTestId("ts-line-chronos-2");
    expect(p.queryByTestId("ts-band")).toBeNull();
    expect(p.getAllByText("–").length).toBeGreaterThanOrEqual(2);
  });

  it("shows ensemble steps 2 and 3 only when they exist", async () => {
    setup(status(ready), { "POST /api/ts/forecast": () => jsonResponse({ ok: true, horizon: 1, quantile_levels: [0.1, 0.5, 0.9], models: { "chronos-2": fc("A", 20, 1), "toto-2-4m": fc("B", 30, 1) },
      ensemble: { median: [25], quantiles: { "0.1": [24], "0.9": [26] }, members: ["chronos-2", "toto-2-4m"] } }) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await fill(p, history, "1");
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    expect((await p.findAllByText("25.00")).length).toBe(2);                                                  // step 1 and "last" are the same value; steps 2-3 are dashes
  });

  it("shows the backtest ranking, and a backtest that could not run", async () => {
    const result = (backtest: any) => ({ ok: true, horizon: 2, quantile_levels: [0.1, 0.5, 0.9], models: { "chronos-2": fc("Chronos-2 (Amazon)", 20, 2) }, ensemble: null, backtest });
    let body: any = result({ ok: true, holdout: 2, ranked_by: "mase", ranking: ["chronos-2", "ensemble"], caution: "One window is noisy.",
      scores: { "chronos-2": { mae: 1.5, rmse: 2, smape_pct: 3.2, mase: 0.8 }, ensemble: { mae: 2, rmse: 2.5, smape_pct: 4, mase: null } } });
    setup(status(ready), { "POST /api/ts/forecast": () => jsonResponse(body) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await fill(p, history, "2");
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    expect(await p.findByText(/Backtest on the last 2 points \(lower is better, ranked by mase\)/)).toBeInTheDocument();
    expect(p.getByText("One window is noisy.")).toBeInTheDocument();
    const rows = within(p.getByText(/Backtest on the last/).closest("table") as HTMLElement).getAllByRole("row");
    expect(rows.map((r) => r.textContent)).toEqual(["#ModelMAERMSEsMAPE %MASE", "1Chronos-2 (Amazon)1.523.20.8", "2Ensemble (mean)22.54–"]);
    body = result({ ok: false, error: "a backtest needs at least 8 values" });
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    expect(await p.findByText("Backtest: a backtest needs at least 8 values")).toBeInTheDocument();
    body = result({ ok: true, holdout: 2, scores: { "chronos-2": { mae: 1, rmse: 1, smape_pct: 1, mase: 1 } } });                // no ranking field: an empty table, not a crash
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    await waitFor(() => expect(p.queryByText("Backtest: a backtest needs at least 8 values")).toBeNull());
  });

  it("shows the backend's reason when the forecast fails, or a default text, or a network error", async () => {
    let body: any = { ok: false, error: "none of the requested models is installed" };
    let boom = false;
    setup(status(ready), { "POST /api/ts/forecast": () => { if (boom) throw new Error("offline"); return jsonResponse(body); } });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await fill(p);
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    expect(await p.findByRole("alert")).toHaveTextContent("none of the requested models is installed");
    body = { ok: false };
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    await waitFor(() => expect(p.getByRole("alert")).toHaveTextContent("The forecast failed."));
    boom = true;
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    await waitFor(() => expect(p.getByRole("alert")).toHaveTextContent("offline"));
    expect(p.getByRole("button", { name: "Forecast" })).toBeEnabled();
  });

  it("fills in sample data and shows a busy state while the models run", async () => {
    let release: (r: Response) => void = () => {};
    setup(status(ready), { "POST /api/ts/forecast": () => new Promise<Response>((res) => { release = res; }) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await userEvent.click(p.getByRole("button", { name: "Use sample data" }));
    expect((p.getByLabelText("Series") as HTMLTextAreaElement).value.split(",").length).toBe(72);
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    expect(await p.findByRole("button", { name: "Forecasting…" })).toBeDisabled();
    await act(async () => { release(jsonResponse({ ok: true, horizon: 24, quantile_levels: [0.1, 0.5, 0.9], models: { "chronos-2": fc("Chronos-2 (Amazon)", 100, 24) } })); });
    expect(await p.findByTestId("ts-line-chronos-2")).toBeInTheDocument();
    expect(p.getByRole("button", { name: "Forecast" })).toBeEnabled();
  });

  it("a flat history still draws (no division by zero)", async () => {
    setup(status(ready), { "POST /api/ts/forecast": () => jsonResponse({ ok: true, horizon: 1, quantile_levels: [0.1, 0.5, 0.9], models: { "chronos-2": { ...fc("A", 5, 1), median: [5], quantiles: { "0.1": [5], "0.9": [5] } } } }) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await fill(p, "5 5 5 5 5 5 5 5", "1");
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    const line = await p.findByTestId("ts-line-chronos-2");
    expect(line.getAttribute("d")).not.toMatch(/NaN/);
  });

  it("draws nothing for a model the result does not contain", async () => {
    setup(status(ready), { "POST /api/ts/forecast": () => jsonResponse({ ok: true, horizon: 2, quantile_levels: [0.1, 0.5, 0.9], models: { "chronos-2": fc("A", 5, 2) } }) });
    render(<TimeSeriesPanel onChange={() => {}} />);
    const p = await panel();
    await fill(p, history, "2");
    await userEvent.click(p.getByRole("button", { name: "Forecast" }));
    await p.findByTestId("ts-line-chronos-2");
    expect(p.queryByTestId("ts-line-toto-2-4m")).toBeNull();                                                 // toto was selected but is missing from the answer
  });
});
