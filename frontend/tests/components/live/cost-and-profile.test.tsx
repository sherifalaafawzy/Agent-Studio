import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AgentLive, { LIVE_OFF, liveBadge, liveOf } from "@/components/AgentLive";
import LiveHistory from "@/components/live/LiveHistory";
import LivePanel from "@/components/live/LivePanel";
import PriceTable from "@/components/live/PriceTable";
import UsageMeter from "@/components/live/UsageMeter";
import { LiveClient, type Usage } from "@/lib/live";
import { FakeSocket, fakeDeps, livePrices, liveSettings } from "../../helpers/liveFakes";
import { jsonResponse, routeFetch } from "../../helpers/http";

afterEach(() => vi.unstubAllGlobals());

const usage = (over: Partial<Usage> = {}): Usage => ({
  elapsed_s: 75, cost_usd: 0.0421, per_min_usd: 0.0337, source: "mixed", cloud: true, tokens: { in: 1200, out: 15000 }, seconds: { audio_in: 12.3, audio_out: 8.1, video: 70 },
  rows: [{ label: "Avatar video (out)", tokens: 12000, seconds: null, usd: 0.012, kind: "mixed" }, { label: "Its voice (audio out)", tokens: 300, seconds: null, usd: 0.0036, kind: "reported" }],
  budget_usd: 0.1, budget_pct: 42.1, level: "ok", reports: 2, prices_as_of: "2026-11-23", ...over,
});

describe("UsageMeter", () => {
  it("shows the cost so far with a ≈ and says where the number comes from", () => {
    render(<UsageMeter u={usage()} />);
    expect(screen.getByTestId("usage-cost")).toHaveTextContent("≈ $0.042");
    expect(screen.getByTestId("usage-permin")).toHaveTextContent("≈ $0.034 per minute");
    expect(screen.getByTestId("usage-source")).toHaveTextContent("reported + estimated since the last report · prices as of 2026-11-23, verify");
    expect(screen.getByTestId("usage-audio-in")).toHaveTextContent("12.3 s");
    expect(screen.getByTestId("usage-video")).toHaveTextContent("70.0 s");
    expect(screen.getByTestId("usage-tokens")).toHaveTextContent("1,200 / 15,000");
    expect(screen.getByTestId("usage-budget")).toHaveTextContent("42% of the $0.10 budget (warning at 80%, stop at 100%)");
    expect(screen.getByTestId("usage-level")).toHaveTextContent("1:15");
    expect(screen.getByTestId("usage-rows")).toHaveTextContent("≈ $0.012");
    expect(screen.getByTestId("usage-rows")).toHaveTextContent("Its voice (audio out)");
  });

  it("drops the ≈ when everything is reported, and marks warning and stop levels", () => {
    const { rerender } = render(<UsageMeter u={usage({ source: "reported", rows: [], per_min_usd: null })} />);
    expect(screen.getByTestId("usage-cost")).toHaveTextContent("$0.042");
    expect(screen.getByTestId("usage-cost").textContent).not.toContain("≈");
    expect(screen.getByTestId("usage-permin")).toHaveTextContent("per minute: soon");
    expect(screen.queryByTestId("usage-rows")).toBeNull();
    rerender(<UsageMeter u={usage({ level: "warn", budget_pct: 83 })} />);
    expect(screen.getByTestId("usage-level")).toHaveTextContent("80% of budget");
    rerender(<UsageMeter u={usage({ level: "stop", budget_pct: 130 })} />);
    expect(screen.getByTestId("usage-level")).toHaveTextContent("budget reached");
  });

  it("a local session shows usage and zero cloud cost; no budget bar when none is set; tiny and zero amounts format", () => {
    const { rerender } = render(<UsageMeter u={usage({ cloud: false, cost_usd: 0, source: "local", seconds: { audio_in: 3, audio_out: 5.5, video: 0 }, budget_usd: null, rows: [{ label: "Model, tokens read", tokens: 90, seconds: null, usd: 0, kind: "local" }, { label: "Its voice (speech synthesis)", tokens: null, seconds: 5.5, usd: 0, kind: "local" }] })} />);
    expect(screen.getByTestId("usage-cost")).toHaveTextContent("$0.00");
    expect(screen.getByTestId("usage-source")).toHaveTextContent("this PC (no cloud cost)");
    expect(screen.queryByTestId("usage-budget")).toBeNull();
    expect(screen.queryByTestId("usage-video")).toBeNull();
    expect(screen.getByTestId("usage-rows")).toHaveTextContent("5.5 s");
    rerender(<UsageMeter u={usage({ cost_usd: 0.0004, source: "estimated" })} />);
    expect(screen.getByTestId("usage-cost")).toHaveTextContent("≈ $0.0004");
    rerender(<UsageMeter u={usage({ source: "bogus" as never, cost_usd: 0 })} />);
    expect(screen.getByTestId("usage-source")).toHaveTextContent("bogus");
  });

  it("waits quietly when no session has started", () => {
    render(<UsageMeter u={null} />);
    expect(screen.getByText(/Appears while a session is running/)).toBeInTheDocument();
  });
});

describe("LiveHistory", () => {
  const row = (over = {}) => ({ id: "a", started: 1_700_000_000, ended: 1_700_000_042, agent: "speaker", engine: "gemini", mode: "avatar", provider: "vertex_ai", model: "gemini-3.8-live", voice: "Puck", seconds: 42.4,
    cost_usd: 0.0421, cost_source: "mixed", budget_usd: 1, ended_by: "budget", detail: {}, ...over });

  it("lists past sessions with cost, whether it was reported or estimated, and why each ended", async () => {
    routeFetch({ "GET /api/live/history": () => jsonResponse([row(), row({ id: "b", agent: "", engine: "local", mode: "voice", cost_usd: 0, cost_source: "local", ended_by: "user" }), row({ id: "c", cost_source: "reported", ended_by: "x" })]) });
    render(<LiveHistory refreshKey={0} />);
    await waitFor(() => expect(screen.getAllByTestId("history-row")).toHaveLength(3));
    expect(screen.getByText(/3 saved · \$0\.0842 total \(cloud\)/)).toBeInTheDocument();
    const rows = screen.getAllByTestId("history-row");
    expect(rows[0]).toHaveTextContent("speaker");
    expect(rows[0]).toHaveTextContent("Gemini · avatar");
    expect(rows[0]).toHaveTextContent("≈ $0.0421");
    expect(rows[0]).toHaveTextContent("budget reached");
    expect(rows[1]).toHaveTextContent("no agent");
    expect(rows[1]).toHaveTextContent("this PC · voice");
    expect(rows[2].textContent).not.toContain("≈");
  });

  it("says when there are none or the list cannot be read, and reloads on a new key", async () => {
    const { calls } = routeFetch({ "GET /api/live/history": () => jsonResponse([]) });
    const { rerender } = render(<LiveHistory refreshKey={0} />);
    expect(await screen.findByText("No sessions yet.")).toBeInTheDocument();
    rerender(<LiveHistory refreshKey={1} />);
    await waitFor(() => expect(calls.length).toBe(2));
    routeFetch({ "GET /api/live/history": () => jsonResponse({ detail: "nope" }, { status: 500 }) });
    rerender(<LiveHistory refreshKey={2} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
  });
});

describe("PriceTable", () => {
  it("shows prices as of a date with 'verify', and edits and saves them", async () => {
    const { calls } = routeFetch({ "GET /api/live/prices": () => jsonResponse(livePrices), "PUT /api/live/prices": (init) => jsonResponse({ ...livePrices, ...JSON.parse(String(init?.body)), as_of: "2027-01-05" }) });
    render(<PriceTable />);
    expect(await screen.findByTestId("prices-label")).toHaveTextContent("prices as of 2026-11-23, verify");
    expect(screen.getByRole("link")).toHaveAttribute("href", livePrices.source);
    fireEvent.change(screen.getByLabelText("Price out AUDIO"), { target: { value: "13.5" } });
    fireEvent.change(screen.getByLabelText("Price in VIDEO"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Avatar video, tokens per second (measured)"), { target: { value: "2600" } });
    await userEvent.click(screen.getByRole("button", { name: "Save prices" }));
    expect(await screen.findByTestId("prices-msg")).toHaveTextContent("Saved.");
    const put = calls.find((c) => c.method === "PUT")!.body;
    expect(put.out.AUDIO).toBe(13.5);
    expect(put.in.VIDEO).toBe(0);
    expect(put.rates.video_out_tokens_per_s).toBe(2600);
    expect(put.source).toBe("edited by hand");
  });

  it("reads Google's pricing page on request, and shows the reason when that fails or a save is refused", async () => {
    let fail = false;
    routeFetch({ "GET /api/live/prices": () => jsonResponse(livePrices), "POST /api/live/prices/refresh": () => (fail ? jsonResponse({ detail: "Google's pricing page answered 503" }, { status: 502 }) : jsonResponse({ ...livePrices, as_of: "2027-02-02", out: { ...livePrices.out, AUDIO: 14 } })),
      "PUT /api/live/prices": () => jsonResponse({ detail: "in.AUDIO must be a number from 0 to 1000" }, { status: 422 }) });
    render(<PriceTable />);
    await userEvent.click(await screen.findByRole("button", { name: /Read Google's pricing page now/ }));
    expect(await screen.findByTestId("prices-msg")).toHaveTextContent("Read from Google's pricing page.");
    expect(screen.getByTestId("prices-label")).toHaveTextContent("2027-02-02");
    expect((screen.getByLabelText("Price out AUDIO") as HTMLInputElement).value).toBe("14");
    fail = true;
    await userEvent.click(screen.getByRole("button", { name: /Read Google's pricing page now/ }));
    await waitFor(() => expect(screen.getByTestId("prices-msg")).toHaveTextContent("answered 503"));
    await userEvent.click(screen.getByRole("button", { name: "Save prices" }));
    await waitFor(() => expect(screen.getByTestId("prices-msg")).toHaveTextContent("0 to 1000"));
    expect(screen.getByTestId("prices-msg")).toHaveAttribute("role", "alert");
  });

  it("shows an error when the table cannot be loaded and a plain source when it is not a link", async () => {
    routeFetch({ "GET /api/live/prices": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    const { unmount } = render(<PriceTable />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
    unmount();
    routeFetch({ "GET /api/live/prices": () => jsonResponse({ ...livePrices, source: "edited by hand" }) });
    render(<PriceTable />);
    expect(await screen.findByText("edited by hand")).not.toHaveAttribute("href");
  });
});

describe("AgentLive (the agent's Live profile)", () => {
  it("has no profile until you switch it on, then offers engine, mode, voice, language, avatar and budget", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<AgentLive agent={{}} onChange={onChange} />);
    expect(screen.getByLabelText("Can be used in live mode")).not.toBeChecked();
    expect(screen.queryByLabelText("Live engine")).toBeNull();
    await userEvent.click(screen.getByLabelText("Can be used in live mode"));
    expect(onChange).toHaveBeenLastCalledWith({ ...LIVE_OFF, enabled: true });
    rerender(<AgentLive agent={{ live: { ...LIVE_OFF, enabled: true } }} onChange={onChange} />);
    expect(screen.getByLabelText("Live engine")).toHaveValue("local");
    expect(screen.queryByLabelText("Live language")).toBeNull();
    expect(screen.getByLabelText("Use the agent's tools in live mode")).toBeInTheDocument();
    expect(screen.getByText(/lowest delay/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Use the agent's tools in live mode"));
    expect(onChange).toHaveBeenLastCalledWith({ ...LIVE_OFF, enabled: true, agent_turns: true });
    await userEvent.selectOptions(screen.getByLabelText("Live voice"), "am_adam");
    expect(onChange).toHaveBeenLastCalledWith({ ...LIVE_OFF, enabled: true, voice: "am_adam" });
    fireEvent.change(screen.getByLabelText("Live budget"), { target: { value: "0.75" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...LIVE_OFF, enabled: true, budget_usd: 0.75 });
    fireEvent.change(screen.getByLabelText("Live budget"), { target: { value: "abc" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...LIVE_OFF, enabled: true, budget_usd: 0 });
    rerender(<AgentLive agent={{ live: { ...LIVE_OFF, enabled: true, agent_turns: true } }} onChange={onChange} />);
    expect(screen.getByText(/full turn of this agent/)).toBeInTheDocument();
  });

  it("Gemini offers its 30 voices, languages and, for an avatar, the avatar name", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<AgentLive agent={{ live: { ...LIVE_OFF, enabled: true } }} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByLabelText("Live engine"), "gemini");
    expect(onChange).toHaveBeenLastCalledWith({ ...LIVE_OFF, enabled: true, engine: "gemini", voice: "", language: "" });
    rerender(<AgentLive agent={{ live: { ...LIVE_OFF, enabled: true, engine: "gemini", mode: "avatar", avatar_name: "Ben" } }} onChange={onChange} />);
    expect(screen.getAllByRole("option").filter((o) => (o as HTMLOptionElement).value === "Aoede")).toHaveLength(1);
    expect(screen.getByLabelText("Live avatar")).toHaveValue("Ben");
    expect(screen.getByLabelText("Live language")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Live language"), "de-DE");
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ language: "de-DE", engine: "gemini" }));
    fireEvent.change(screen.getByLabelText("Live avatar"), { target: { value: "Ada" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ avatar_name: "Ada" }));
    await userEvent.selectOptions(screen.getByLabelText("Live mode"), "voice");
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ mode: "voice" }));
    expect(screen.getByText(/Google bills your project/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Use the agent's tools in live mode")).toBeNull();
    rerender(<AgentLive agent={{ live: { ...LIVE_OFF, enabled: true, engine: "gemini", mode: "voice" } }} onChange={onChange} />);
    expect(screen.queryByLabelText("Live avatar")).toBeNull();
  });

  it("switching a profile off keeps the settings, and the compact form has no card", async () => {
    const onChange = vi.fn();
    const { container } = render(<AgentLive agent={{ live: { ...LIVE_OFF, enabled: true, engine: "gemini", mode: "avatar", avatar_name: "Ben" } }} onChange={onChange} compact />);
    expect(container.querySelector(".card")).toBeNull();
    await userEvent.click(screen.getByLabelText("Can be used in live mode"));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ enabled: false, avatar_name: "Ben" }));
  });

  it("badges: none without a profile, 'Live off', or engine and mode", () => {
    expect(liveBadge({})).toBeNull();
    expect(liveBadge({ live: {} })).toBeNull();
    expect(liveBadge({ live: { enabled: false } })).toBe("Live off");
    expect(liveBadge({ live: { enabled: true, engine: "gemini", mode: "avatar" } })).toBe("Live · Gemini · avatar");
    expect(liveBadge({ live: { enabled: true, engine: "local", mode: "voice" } })).toBe("Live · local · voice");
    expect(liveOf(undefined)).toEqual({});
  });
});

// ------------------------------------------------------------------ the panel: usage events, the budget, and an agent's profile
const h = vi.hoisted(() => ({ clients: [] as any[] }));
vi.mock("@/lib/live", async (orig) => {
  const real = await orig<typeof import("@/lib/live")>();
  class FakeClient {
    analyser = { fftSize: 8, frequencyBinCount: 8, getByteTimeDomainData: (a: Uint8Array) => a.fill(128), getByteFrequencyData: (a: Uint8Array) => a.fill(0) }; started: any = null;
    constructor(public ev: any) { h.clients.push(this); }
    async start(o: any) { this.started = o; }
    stop() { this.ev.onState("idle"); }
    interrupt() {} setMuted() {} say() {}
  }
  return { ...real, LiveClient: FakeClient, realDeps: () => ({}) };
});
vi.mock("@/lib/avatarVideo", () => ({ AvatarVideoPlayer: class { markWaiting() {} push() {} finish() {} reset() {} interrupted() {} }, realPlayerDeps: () => ({}) }));

const local = { state: "ready", ready: true, error: null, steps: [], percent: 100, device: "cuda", stt_model: "small.en", auto_device: "cuda", progress_text: "", log_tail: [], download_mb: 0, free_gb: 10, expect: "",
  voices: ["af_heart"], licences: [], hardware: { nvidia: true, gpu: "RTX" }, versions: null };
const gem = { configured: true, problems: [], provider: "vertex_ai", project: "p", location: "us-central1", model: "gemini-3.8-live", auth_mode: "adc", voices: ["Puck"], languages: ["en-US"], endpoint: "wss://x", avatar_examples: ["Ben"], notes: [], prices: { in: {}, out: {} } };

function panel(agents: any[], st: any = {}) {
  return routeFetch({
    "GET /api/live/status": () => jsonResponse({ local, engine: { state: "idle", error: "" }, settings: { ...liveSettings, ...st } }), "GET /api/providers": () => jsonResponse([]),
    "GET /api/agents": () => jsonResponse(agents), "GET /api/providers/ollama/models": () => jsonResponse({ provider: "ollama", source: "live", error: null, cached: false, models: [] }),
    "PUT /api/live/settings": (init: any) => jsonResponse({ ...liveSettings, ...st, ...JSON.parse(String(init?.body)) }), "GET /api/live/gemini/status": () => jsonResponse(gem),
    "GET /api/live/prices": () => jsonResponse(livePrices), "GET /api/live/history": () => jsonResponse([]),
  });
}
beforeEach(() => { h.clients.length = 0; });
const fire = (fn: () => void) => act(() => { fn(); });

describe("LivePanel: cost meter, budget and agent profiles", () => {
  it("updates the meter from each usage event, warns at 80% and shows the stop message, then refreshes the history", async () => {
    const { calls } = panel([]);
    render(<LivePanel />);
    await userEvent.click(await screen.findByTestId("live-start"));
    const c = h.clients[0];
    expect(screen.getByText(/Appears while a session is running/)).toBeInTheDocument();
    fire(() => c.ev.onState("listening"));
    fire(() => c.ev.onUsage(usage({ cost_usd: 0.01, budget_pct: 10 })));
    expect(screen.getByTestId("usage-cost")).toHaveTextContent("≈ $0.010");
    fire(() => c.ev.onUsage(usage({ cost_usd: 0.085, budget_pct: 85, level: "warn" })));
    expect(screen.getByTestId("usage-cost")).toHaveTextContent("≈ $0.085");
    fire(() => c.ev.onBudget({ level: "warn", message: "This session has used about $0.08 of its $0.10 budget (80%)." }));
    expect(screen.getByTestId("live-budget-banner")).toHaveTextContent("80%");
    expect(screen.getByTestId("live-budget-banner")).toHaveAttribute("role", "status");
    fire(() => c.ev.onBudget({ level: "stop", message: "Stopped: this session reached its $0.10 budget." }));
    expect(screen.getByTestId("live-budget-banner")).toHaveAttribute("role", "alert");
    const before = calls.filter((x) => x.url.pathname === "/api/live/history").length;
    await userEvent.click(screen.getByTestId("live-stop"));
    await waitFor(() => expect(calls.filter((x) => x.url.pathname === "/api/live/history").length).toBeGreaterThan(before));
    expect(screen.getByTestId("usage-cost")).toBeInTheDocument();                       // the last figures stay on screen after the session
    await userEvent.click(screen.getByTestId("live-start"));
    expect(screen.queryByTestId("live-budget-banner")).toBeNull();                      // a new session clears them
  });

  it("an agent's enabled Live profile decides the engine and mode, and says so", async () => {
    panel([{ id: "a1", name: "Pirate Pete", live: { ...LIVE_OFF, enabled: true, engine: "gemini", mode: "avatar", avatar_name: "Ben", budget_usd: 0.4 } }], { live_agent: "a1", live_engine: "local", live_avatar: false });
    render(<LivePanel />);
    expect(await screen.findByTestId("live-profile-note")).toHaveTextContent("Using Pirate Pete's Live profile: Google Gemini Live, with avatar, budget $0.40.");
    expect(screen.getByTestId("live-cloud-banner")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "With avatar" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "With avatar" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByTestId("live-start"));
    expect(h.clients[0].started).toMatchObject({ engine: "gemini", avatar: true, agent: "a1" });
  });

  it("a local profile with agent turns says each answer is a full agent turn; a disabled profile blocks Start", async () => {
    panel([{ id: "a1", name: "Pete", live: { ...LIVE_OFF, enabled: true, engine: "local", mode: "voice", agent_turns: true } }], { live_agent: "a1", live_engine: "gemini" });
    const { unmount } = render(<LivePanel />);
    expect(await screen.findByTestId("live-profile-note")).toHaveTextContent("this PC, voice only");
    expect(screen.queryByTestId("live-cloud-banner")).toBeNull();
    expect(screen.getByText(/Each answer is a full turn of the agent/)).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("live-start"));
    expect(h.clients[0].started).toMatchObject({ engine: "local", avatar: false });
    unmount();
    panel([{ id: "a1", name: "Pete", live: { ...LIVE_OFF, enabled: false } }], { live_agent: "a1" });
    render(<LivePanel />);
    expect(await screen.findByTestId("live-profile-off")).toHaveTextContent("Live mode is switched off for Pete");
    expect(screen.getByTestId("live-start")).toBeDisabled();
  });

  it("an agent without a profile leaves the page's own choice alone", async () => {
    panel([{ id: "a1", name: "Plain" }], { live_agent: "a1", live_engine: "local" });
    render(<LivePanel />);
    expect(await screen.findByTestId("live-start")).not.toBeDisabled();
    expect(screen.queryByTestId("live-profile-note")).toBeNull();
    expect(screen.getByRole("button", { name: "With avatar" })).not.toBeDisabled();
  });
});

describe("LiveClient usage events", () => {
  it("passes the usage and budget messages on", async () => {
    const real = await vi.importActual<typeof import("@/lib/live")>("@/lib/live");
    const deps = fakeDeps();
    const ev = { onUsage: vi.fn(), onBudget: vi.fn() };
    const c = new real.LiveClient(ev, deps);
    await c.start();
    const ws = FakeSocket.last;
    ws.onopen!();
    ws.emit({ type: "usage", cost_usd: 0.5, source: "reported", rows: [] });
    ws.emit({ type: "budget", level: "warn", message: "80%" });
    expect(ev.onUsage).toHaveBeenCalledWith({ cost_usd: 0.5, source: "reported", rows: [] });
    expect(ev.onBudget).toHaveBeenCalledWith({ level: "warn", message: "80%" });
    const quiet = new real.LiveClient({}, fakeDeps());
    await quiet.start();
    FakeSocket.last.onopen!();
    FakeSocket.last.emit({ type: "usage", cost_usd: 1 });
    FakeSocket.last.emit({ type: "budget", level: "stop", message: "x" });
    c.stop(); quiet.stop();
  });
});
