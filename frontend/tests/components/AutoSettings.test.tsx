import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AutoSettings from "@/components/AutoSettings";
import { forgetModels } from "@/components/ModelSelect";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

type Routes = Parameters<typeof routeFetch>[0];

const INFO = (over: Record<string, unknown> = {}): any => ({
  enabled: true, engine: "rules", preference: "balanced", allow: [], deny: [], fallback: true, timeout_s: 4, tier_models: { flash: "", pro: "" },
  engines: ["rules", "d1-2b", "d1-3b"], preferences: ["cost", "balanced", "quality"], available: true, reason: "", worker: null,
  engine_ready: { "d1-2b": false, "d1-3b": false }, ...over,
});
const d1m = (id: string, over: Record<string, unknown> = {}): any => ({
  model: id, label: id === "d1-2b" ? "d1-2B" : "d1-3B", note: `note for ${id}`, state: "not_installed", ready: false, error: null, percent: 0, progress_text: "",
  steps: [], weights_gb: id === "d1-2b" ? 3.84 : 6.25, download_mb: id === "d1-2b" ? 3900 : 6300, disk_gb_needed: 4.2, free_gb: 100, shared_env: false,
  question_types: ["noul"], expect: "expectation text", busy_with: null, revision: "a6bce774e6dde338dcc3036ceb5d91f78bf4c19e",
  license: { name: "LFM Open License v1.0", url: "https://hf.test/LICENSE", summary: ["Free to use."] }, review_notes: ["No network."], ...over,
});
const MODELS = (a: Record<string, unknown> = {}, b: Record<string, unknown> = {}, installing: string | null = null): any => ({
  default: "d1-2b", installing, models: { "d1-2b": d1m("d1-2b", a), "d1-3b": d1m("d1-3b", b) },
});
const PROVIDERS: any[] = [{ id: "openai", label: "OpenAI", enabled: true }, { id: "gemini", label: "Gemini", enabled: false }];
const ROUTE = {
  provider: "openai", model: "gpt-5-mini", tier: "flash", engine: "rules", reason: "built-in rules read this as simple.", confidence: 0.6,
  signals: { complexity: "simple", code: false, reasoning: false, tools: false, vision: false, est_tokens: 10 },
  price: { input_per_m: 0.5, output_per_m: 2, known: true }, alternates: [], skipped: [],
};

function setup(info: any = INFO(), models: any = MODELS(), extra: Routes = {}) {
  return routeFetch({
    "GET /api/auto": () => jsonResponse(info),
    "GET /api/d1/models": () => jsonResponse(models),
    "GET /api/providers/openai/models": () => jsonResponse({ provider: "openai", source: "live", error: null, cached: false, models: [{ id: "gpt-x", label: "GPT X", input_per_m: 1, output_per_m: 2 }] }),
    ...extra,
  });
}
const puts = (calls: Call[]) => calls.filter((c) => c.method === "PUT" && c.url.pathname === "/api/auto");
const count = (calls: Call[], m: string, p: string) => calls.filter((c) => c.method === m && c.url.pathname === p).length;
const ready = async () => { render(<AutoSettings providers={PROVIDERS} />); await screen.findByText("Auto mode"); };
const row = (id: string) => within(screen.getByTestId(`d1-${id}`));
const toasts = () => { const seen: string[] = []; const on = (e: Event) => seen.push((e as CustomEvent).detail.text); window.addEventListener("as:toast", on); return seen; };

beforeEach(() => { forgetModels("openai"); });
afterEach(() => { vi.useRealTimers(); });

describe("Auto settings: loading and the main switches", () => {
  it("says when it is loading and reports an unreachable backend", async () => {
    routeFetch({});
    render(<AutoSettings providers={PROVIDERS} />);
    expect(screen.getByText("Loading Auto settings…")).toBeInTheDocument();
    expect(await screen.findByText(/⚠ Unexpected fetch: GET \/api\/auto/)).toBeInTheDocument();
  });

  it("shows Auto as ready, lists the engines and marks the ones that are not installed", async () => {
    setup(INFO({ engine_ready: { "d1-2b": true, "d1-3b": false }, engines: ["rules", "d1-2b", "d1-3b", "d1-9b"] }));
    await ready();
    expect(within(screen.getByLabelText("Engine")).getByRole("option", { name: "d1-9b — not installed" })).toBeInTheDocument();   // an engine this page has no label for
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByText(/Auto will use any enabled provider/)).toBeInTheDocument();
    const engine = screen.getByLabelText("Engine");
    expect(within(engine).getByRole("option", { name: "Built-in rules (no download)" })).toBeInTheDocument();
    expect(within(engine).getByRole("option", { name: "d1-2B decision model (local)" })).toBeInTheDocument();
    expect(within(engine).getByRole("option", { name: "d1-3B decision model (local) — not installed" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("says why Auto cannot be offered, or shows the router's own note when it has one", async () => {
    setup(INFO({ available: false, reason: "No model provider is ready for Auto (openai: API key not set)." }));
    await ready();
    expect(screen.getByText("unavailable")).toBeInTheDocument();
    expect(screen.getByText("No model provider is ready for Auto (openai: API key not set).")).toBeInTheDocument();
  });

  it("shows a reason next to a ready state", async () => {
    setup(INFO({ reason: "2 models are usable." }));
    await ready();
    expect(screen.getByText("2 models are usable.")).toBeInTheDocument();
  });

  it("saves the two switches immediately and reports a refusal", async () => {
    let cur = INFO();
    const { calls } = setup(INFO(), MODELS(), { "PUT /api/auto": (init) => jsonResponse((cur = { ...cur, ...JSON.parse(String(init?.body)) })) });
    await ready();
    await userEvent.click(screen.getByLabelText("Offer Auto in the chat"));
    await userEvent.click(screen.getByLabelText("If the chosen model fails, retry once on the next best"));
    expect(puts(calls).map((c) => c.body)).toEqual([{ enabled: false }, { fallback: false }]);
    expect(screen.getByLabelText("Offer Auto in the chat")).not.toBeChecked();
    expect(screen.getByLabelText("If the chosen model fails, retry once on the next best")).not.toBeChecked();
  });

  it("shows the server's message when a change is refused", async () => {
    setup(INFO(), MODELS(), { "PUT /api/auto": () => jsonResponse({ detail: "engine must be one of: rules" }, { status: 422 }) });
    await ready();
    await userEvent.click(screen.getByLabelText("Offer Auto in the chat"));
    expect(await screen.findByText("⚠ engine must be one of: rules")).toBeInTheDocument();
  });
});

describe("Auto settings: choosing how it decides", () => {
  it("saves the engine, time limit, preference and the allow and block lists", async () => {
    const seen = toasts();
    const { calls } = setup(INFO({ allow: ["openai"], deny: ["x/y"] }), MODELS(), { "PUT /api/auto": (init) => jsonResponse(INFO(JSON.parse(String(init?.body)))) });
    await ready();
    expect(screen.getByLabelText(/Only use these/)).toHaveValue("openai");
    expect(screen.getByLabelText(/Never use these/)).toHaveValue("x/y");
    expect(screen.getByText(/The strong tier for reasoning, agent-style coding/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Engine"), "d1-3b");
    await userEvent.clear(screen.getByLabelText("Time allowed for it (seconds)"));
    await userEvent.type(screen.getByLabelText("Time allowed for it (seconds)"), "7");
    await userEvent.selectOptions(screen.getByLabelText("Preference"), "quality");
    expect(screen.getByText(/priciest known strong model/)).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText(/Only use these/));
    await userEvent.type(screen.getByLabelText(/Only use these/), "openai{Enter} groq/llama {Enter}");
    await userEvent.clear(screen.getByLabelText(/Never use these/));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(puts(calls)).toHaveLength(1));
    expect(puts(calls)[0].body).toEqual({ engine: "d1-3b", timeout_s: 7, preference: "quality", allow: ["openai", "groq/llama"], deny: [] });
    expect(seen).toContain("Auto settings saved");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("keeps the unsaved changes when saving is refused", async () => {
    const { calls } = setup(INFO(), MODELS(), { "PUT /api/auto": () => jsonResponse({ detail: "timeout must be between 0.5 and 30 seconds" }, { status: 422 }) });
    await ready();
    await userEvent.selectOptions(screen.getByLabelText("Preference"), "cost");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("⚠ timeout must be between 0.5 and 30 seconds")).toBeInTheDocument();
    expect(screen.getByLabelText("Preference")).toHaveValue("cost");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    expect(puts(calls)).toHaveLength(1);
  });

  it("pins each tier to one model and can clear it again", async () => {
    const { calls } = setup(INFO({ tier_models: { flash: "", pro: "openai/gpt-x" } }), MODELS(), { "PUT /api/auto": (init) => jsonResponse(INFO(JSON.parse(String(init?.body)))) });
    await ready();
    const flashProv = screen.getByLabelText("Fast-tier model provider");
    expect(within(flashProv).queryByRole("option", { name: "Gemini" })).toBeNull();                    // only enabled providers
    expect(within(flashProv).getByRole("option", { name: /Automatic/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Strong-tier model provider")).toHaveValue("openai");
    await userEvent.selectOptions(flashProv, "openai");
    const flashModel = (await screen.findAllByRole("combobox")).find((c) => within(c).queryByRole("option", { name: "Choose a model" }))!;
    await within(flashModel).findByRole("option", { name: /GPT X/ });
    await userEvent.selectOptions(flashModel, "gpt-x");
    await userEvent.selectOptions(screen.getByLabelText("Strong-tier model provider"), "");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(puts(calls)).toHaveLength(1));
    expect(puts(calls)[0].body).toEqual({ tier_models: { flash: "openai/gpt-x", pro: "" } });
  });

  it("clears a tier when its model is cleared", async () => {
    setup(INFO({ tier_models: { flash: "openai/gpt-x", pro: "" } }));
    await ready();
    const sel = (await screen.findAllByRole("combobox")).find((c) => within(c).queryByRole("option", { name: "Choose a model" }))!;
    await within(sel).findByRole("option", { name: /GPT X/ });
    await userEvent.selectOptions(sel, "");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });
});

describe("Auto settings: the decision model", () => {
  const worker = (over: Record<string, unknown>) => ({ model: "d1-2b", state: "idle", error: "", device: null, load_seconds: null, ...over });
  const withWorker = async (w: any, ready_ = true) => {
    setup(INFO({ engine: "d1-2b", engine_ready: { "d1-2b": ready_, "d1-3b": false }, worker: w }));
    await ready();
  };

  it("reports a loaded model, one that is still loading, one that failed and one that is not loaded", async () => {
    await withWorker(worker({ state: "ready", device: "cuda", load_seconds: 12.5 }));
    expect(screen.getByTestId("worker")).toHaveTextContent("d1-2b is loaded on cuda (took 12.5 s).");
    expect(screen.queryByRole("button", { name: "Load it now" })).toBeNull();
  });

  it("describes a loaded model without placement details", async () => {
    await withWorker(worker({ state: "ready" }));
    expect(screen.getByTestId("worker")).toHaveTextContent("d1-2b is loaded.");
  });

  it("explains that the rules decide while it loads", async () => {
    await withWorker(worker({ state: "loading" }));
    expect(screen.getByTestId("worker")).toHaveTextContent("is loading in the background; until it is ready the rules decide.");
    expect(screen.queryByRole("button", { name: "Load it now" })).toBeNull();
  });

  it("shows why a load failed and offers to try again", async () => {
    await withWorker(worker({ state: "failed", error: "RuntimeError: weights are missing" }));
    expect(screen.getByTestId("worker")).toHaveTextContent("could not load: RuntimeError: weights are missing");
    expect(screen.getByRole("button", { name: "Load it now" })).toBeInTheDocument();
  });

  it("loads the model on request and refreshes", async () => {
    const { calls } = setup(INFO({ engine: "d1-2b", engine_ready: { "d1-2b": true, "d1-3b": false }, worker: worker({ state: "idle" }) }), MODELS(), {
      "POST /api/auto/warm": () => jsonResponse({ model: "d1-2b", state: "loading" }) });
    await ready();
    expect(screen.getByTestId("worker")).toHaveTextContent("d1-2b is not loaded yet.");
    const seen = toasts();
    await userEvent.click(screen.getByRole("button", { name: "Load it now" }));
    await waitFor(() => expect(count(calls, "GET", "/api/auto")).toBe(2));
    expect(count(calls, "POST", "/api/auto/warm")).toBe(1);
    expect(seen).toContain("Loading the decision model…");
  });

  it("reports a refused load", async () => {
    setup(INFO({ engine: "d1-2b", engine_ready: { "d1-2b": true, "d1-3b": false }, worker: worker({ state: "idle" }) }), MODELS(), {
      "POST /api/auto/warm": () => jsonResponse({ detail: "d1-2B is not installed yet" }, { status: 409 }) });
    await ready();
    await userEvent.click(screen.getByRole("button", { name: "Load it now" }));
    expect(await screen.findByText("⚠ d1-2B is not installed yet")).toBeInTheDocument();
  });

  it("shows no loading controls for the rules engine or for a model that is not installed", async () => {
    setup(INFO({ engine: "rules", worker: worker({ state: "idle" }) }));
    await ready();
    expect(screen.queryByTestId("worker")).toBeNull();
    expect(screen.queryByRole("button", { name: "Load it now" })).toBeNull();
  });

  it("does not offer to load a model that is not installed, and has nothing to say when no worker exists yet", async () => {
    await withWorker(null, false);
    expect(screen.queryByTestId("worker")).toBeNull();
    expect(screen.queryByRole("button", { name: "Load it now" })).toBeNull();
  });
});

describe("Auto settings: installing the decision models", () => {
  it("shows both models with what they are, their licence and what installing needs", async () => {
    setup(INFO(), MODELS({}, { shared_env: true }));
    await ready();
    expect(row("d1-2b").getByText("d1-2B")).toBeInTheDocument();
    expect(row("d1-2b").getByText("3.84 GB weights")).toBeInTheDocument();
    expect(row("d1-3b").getByText(/6.25 GB weights · reuses the other model's environment/)).toBeInTheDocument();
    expect(row("d1-2b").getByText("note for d1-2b")).toBeInTheDocument();
    expect(row("d1-2b").getAllByText("not installed")[0]).toBeInTheDocument();
    expect(row("d1-2b").getByText("expectation text")).toBeInTheDocument();
    expect(row("d1-2b").getByRole("link", { name: "read the full licence" })).toHaveAttribute("href", "https://hf.test/LICENSE");
    expect(row("d1-2b").getByText("Free to use.")).toBeInTheDocument();
    expect(row("d1-2b").getByText("No network.")).toBeInTheDocument();
    expect(row("d1-2b").getByText(/pinned commit a6bce774/)).toBeInTheDocument();
    expect(row("d1-2b").getByText("Needs about 4.2 GB of disk (100 GB free). Nothing downloads until you press Install.")).toBeInTheDocument();
    expect(row("d1-2b").getByRole("button", { name: "Install · about 3.9 GB" })).toBeDisabled();             // not until the licence is accepted
  });

  it("does not mention free space it does not know", async () => {
    setup(INFO(), MODELS({ free_gb: null }));
    await ready();
    expect(row("d1-2b").getByText("Needs about 4.2 GB of disk. Nothing downloads until you press Install.")).toBeInTheDocument();
  });

  it("installs the chosen model once the licence is accepted, on the chosen device", async () => {
    const seen = toasts();
    const { calls } = setup(INFO(), MODELS(), { "POST /api/d1/install": () => jsonResponse({ state: "installing" }) });
    await ready();
    await userEvent.click(row("d1-3b").getByLabelText(/I accept the LFM Open License v1.0/));
    await userEvent.selectOptions(row("d1-3b").getByLabelText("Device for d1-3B"), "cpu");
    await userEvent.click(row("d1-3b").getByRole("button", { name: /Install · about 6.3 GB/ }));
    await waitFor(() => expect(count(calls, "POST", "/api/d1/install")).toBe(1));
    expect(calls.find((c) => c.url.pathname === "/api/d1/install")!.body).toEqual({ accept_license: true, device: "cpu", model: "d1-3b" });
    expect(seen).toContain("Installing d1-3B…");
    await waitFor(() => expect(count(calls, "GET", "/api/d1/models")).toBe(2));                          // the panel refreshes
  });

  it("shows why an install was refused", async () => {
    setup(INFO(), MODELS(), { "POST /api/d1/install": () => jsonResponse({ detail: "not enough free disk space" }, { status: 422 }) });
    await ready();
    await userEvent.click(row("d1-2b").getByLabelText(/I accept/));
    await userEvent.click(row("d1-2b").getByRole("button", { name: /Install/ }));
    expect(await row("d1-2b").findByText("⚠ not enough free disk space")).toBeInTheDocument();
  });

  it("blocks a second install while another model is installing", async () => {
    setup(INFO(), MODELS({}, { busy_with: "d1-2b" }));
    await ready();
    await userEvent.click(row("d1-3b").getByLabelText(/I accept/));
    expect(row("d1-3b").getByRole("button", { name: /Install/ })).toBeDisabled();
    expect(row("d1-3b").getByRole("button", { name: /Install/ })).toHaveAttribute("title", "Another model is installing");
  });

  it("shows progress while a model installs and keeps it fresh", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { calls } = setup(INFO(), MODELS({ state: "installing", percent: 42, progress_text: "Downloading model weights: 1.6 of 3.9 GB" }, {}, "d1-2b"));
    await ready();
    expect(row("d1-2b").getByText("42% · Downloading model weights: 1.6 of 3.9 GB")).toBeInTheDocument();
    expect(row("d1-2b").queryByRole("button", { name: /Install/ })).toBeNull();
    expect(count(calls, "GET", "/api/d1/models")).toBe(1);
    await act(async () => { vi.advanceTimersByTime(2100); });
    await waitFor(() => expect(count(calls, "GET", "/api/d1/models")).toBeGreaterThanOrEqual(2));
  });

  it("shows a failed install and lets the user retry", async () => {
    setup(INFO(), MODELS({ state: "failed", error: "Step 'weights' failed (exit code 1)" }));
    await ready();
    expect(row("d1-2b").getByText("Step 'weights' failed (exit code 1)")).toBeInTheDocument();
    expect(row("d1-2b").getByRole("button", { name: /Install/ })).toBeInTheDocument();
  });

  it("removes an installed model", async () => {
    const seen = toasts();
    const { calls } = setup(INFO(), MODELS({ state: "ready", ready: true }), { "POST /api/d1/uninstall?model=d1-2b": () => jsonResponse({ removed: true }) });
    await ready();
    expect(row("d1-2b").getByText("ready")).toBeInTheDocument();
    expect(row("d1-2b").queryByRole("button", { name: /Install/ })).toBeNull();
    await userEvent.click(row("d1-2b").getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(seen).toContain("d1-2B removed"));
    expect(count(calls, "POST", "/api/d1/uninstall")).toBe(1);
  });

  it("shows why a removal failed", async () => {
    setup(INFO(), MODELS({ state: "ready", ready: true }), { "POST /api/d1/uninstall?model=d1-2b": () => jsonResponse({ detail: "an install is running; wait for it to finish" }, { status: 409 }) });
    await ready();
    await userEvent.click(row("d1-2b").getByRole("button", { name: "Remove" }));
    expect(await row("d1-2b").findByText("⚠ an install is running; wait for it to finish")).toBeInTheDocument();
  });
});

describe("Auto settings: try it", () => {
  it("shows what Auto would pick, without spending anything", async () => {
    const { calls } = setup(INFO(), MODELS(), { "POST /api/auto/route": () => jsonResponse({ ...ROUTE, skipped: [{ provider: "anthropic", why: "turned off in Settings" }] }) });
    await ready();
    const go = screen.getByRole("button", { name: "Show the routing" });
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText(/Refactor this function/), "hello there");
    await userEvent.click(go);
    expect(await within(screen.getByTestId("try-it")).findByText("gpt-5-mini")).toBeInTheDocument();
    expect(calls.find((c) => c.url.pathname === "/api/auto/route")!.body).toEqual({ message: "hello there", agent: false });
    expect(screen.getByText(/turned off in Settings/)).toBeInTheDocument();                              // skipped providers are listed here
  });

  it("tries unsaved choices and an agent-style request", async () => {
    const { calls } = setup(INFO(), MODELS(), { "POST /api/auto/route": () => jsonResponse(ROUTE) });
    await ready();
    await userEvent.selectOptions(screen.getByLabelText("Engine"), "d1-2b");
    await userEvent.selectOptions(screen.getByLabelText("Preference"), "cost");
    await userEvent.click(screen.getByLabelText("as if an agent were selected"));
    await userEvent.type(screen.getByPlaceholderText(/Refactor this function/), "x");
    await userEvent.click(screen.getByRole("button", { name: "Show the routing" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/auto/route")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/auto/route")!.body).toEqual({ message: "x", agent: true, engine: "d1-2b", preference: "cost" });
  });

  it("shows why a message could not be routed", async () => {
    setup(INFO(), MODELS(), { "POST /api/auto/route": () => jsonResponse({ detail: "Auto could not find a model that can take this message" }, { status: 422 }) });
    await ready();
    await userEvent.type(screen.getByPlaceholderText(/Refactor this function/), "hi");
    await userEvent.click(screen.getByRole("button", { name: "Show the routing" }));
    expect(await screen.findByText("Auto could not find a model that can take this message")).toBeInTheDocument();
  });
});
