import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Avatar, { Orb, STATE_TEXT } from "@/components/live/Avatar";
import LatencyCard, { Breakdown } from "@/components/live/LatencyCard";
import LiveInstall from "@/components/live/LiveInstall";
import LiveSettingsCard, { LiveSettingsForm } from "@/components/LiveSettings";
import type { LiveSettings, LocalStatus } from "@/lib/live";
import { jsonResponse, routeFetch } from "../../helpers/http";
import { liveSettings as settings } from "../../helpers/liveFakes";

afterEach(() => vi.unstubAllGlobals());

/** requestAnimationFrame that runs the callback a few times, synchronously, then stops (so a tick loop cannot run away). */
function limitedRaf(times = 3) {
  let n = 0;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { if (n++ < times) cb(0); return n; });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
}
const analyser = (level = 200, freq = 100) => ({
  fftSize: 8, frequencyBinCount: 8,
  getByteTimeDomainData: (a: Uint8Array) => a.fill(level), getByteFrequencyData: (a: Uint8Array) => a.fill(freq),
}) as unknown as AnalyserNode;

describe("Avatar and Orb", () => {
  it("names the state in its label, and shows thinking dots only while thinking", () => {
    limitedRaf();
    const { rerender } = render(<Avatar analyser={null} state="listening" />);
    expect(screen.getByRole("img", { name: "Avatar: Listening" })).toBeInTheDocument();
    expect(document.querySelector(".dots")).toBeNull();
    rerender(<Avatar analyser={null} state="thinking" />);
    expect(document.querySelector(".dots")).not.toBeNull();
    for (const s of Object.keys(STATE_TEXT) as (keyof typeof STATE_TEXT)[]) { rerender(<Avatar analyser={null} state={s} />); expect(screen.getByTestId("live-avatar")).toHaveClass(`st-${s}`); }
  });

  it("moves the mouth with the voice while speaking, and closes it otherwise", () => {
    limitedRaf(6);
    const { container, rerender } = render(<Avatar analyser={analyser(220, 120)} state="speaking" />);
    const mouth = container.querySelector("path")!;
    const open = mouth.getAttribute("d")!;
    expect(open).not.toBe("M 83 138 Q 100 141 117 138 Q 100 137 83 138 Z");
    rerender(<Avatar analyser={analyser(128, 0)} state="speaking" />);                 // silence on the line: the mouth settles
    limitedRaf(30);
    rerender(<Avatar analyser={analyser(220, 120)} state="listening" />);
    expect(mouth.getAttribute("d")).toBeTruthy();
  });

  it("the voice-only orb shows level bars while speaking and a little life while hearing", () => {
    limitedRaf(4);
    const { container, rerender } = render(<Orb analyser={analyser(230)} state="speaking" />);
    const bars = [...container.querySelectorAll("rect")];
    expect(bars).toHaveLength(7);
    expect(Number(bars[3].getAttribute("height"))).toBeGreaterThan(Number(bars[0].getAttribute("height")));
    rerender(<Orb analyser={null} state="hearing" />);
    expect(screen.getByRole("img", { name: "Voice only: Hearing you…" })).toBeInTheDocument();
    expect(Number(container.querySelector("rect")!.getAttribute("height"))).toBeGreaterThanOrEqual(14);
  });
});

const local = (over: Partial<LocalStatus> = {}): LocalStatus => ({
  state: "not_installed", ready: false, error: null, percent: 0, device: "cuda", stt_model: "small.en", auto_device: "cuda", progress_text: "", log_tail: [], download_mb: 1883,
  free_gb: 16.4, expect: "GPU speech recognition measured about 0.1 s.", voices: ["af_heart", "am_adam"], versions: null,
  steps: [{ id: "venv", label: "Create a private Python environment", size_mb: 15, status: "pending" }, { id: "models", label: "Download models", size_mb: 600, status: "pending" }],
  licences: [{ name: "faster-whisper", licence: "MIT", url: "https://example.test/fw" }], hardware: { nvidia: true, gpu: "NVIDIA GeForce RTX 5080" }, ...over,
});

describe("LiveInstall", () => {
  it("explains what will be installed, needs the licences ticked, and starts the install", async () => {
    const { calls } = routeFetch({ "POST /api/live/install": () => jsonResponse({ state: "installing" }) });
    const onChange = vi.fn();
    render(<LiveInstall local={local()} onChange={onChange} />);
    expect(screen.getByText(/nothing you say is sent anywhere/i)).toBeInTheDocument();
    expect(screen.getByText(/NVIDIA GeForce RTX 5080 will be used \(GPU mode\)/)).toBeInTheDocument();
    expect(screen.getByText("1.9 GB")).toBeInTheDocument();
    const install = screen.getByRole("button", { name: "Install" });
    expect(install).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Accept the licences"));
    await userEvent.click(screen.getByLabelText("Use the CPU only"));
    await userEvent.click(install);
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(calls[0].body).toEqual({ accept_licenses: true, device: "cpu" });
  });

  it("shows a refusal from the server in words", async () => {
    routeFetch({ "POST /api/live/install": () => jsonResponse({ detail: "not enough free disk space" }, { status: 422 }) });
    render(<LiveInstall local={local({ hardware: { nvidia: false } })} onChange={vi.fn()} />);
    expect(screen.getByText(/No NVIDIA GPU was found/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Use the CPU only")).toBeNull();
    await userEvent.click(screen.getByLabelText("Accept the licences"));
    await userEvent.click(screen.getByRole("button", { name: "Install" }));
    expect(await screen.findByText(/not enough free disk space/)).toBeInTheDocument();
  });

  it("shows progress while installing, polling for updates, and the error after a failure", () => {
    vi.useFakeTimers();
    try {
      const onChange = vi.fn();
      const { rerender } = render(<LiveInstall local={local({ state: "installing", percent: 42, progress_text: "kokoro: 30 of 92 MB", steps: [{ id: "venv", label: "Create env", size_mb: 1, status: "done" }, { id: "models", label: "Download models", size_mb: 1, status: "running" }] })} onChange={onChange} />);
      expect(screen.getByText("installing 42%")).toBeInTheDocument();
      expect(screen.getByTestId("live-install-text")).toHaveTextContent("kokoro: 30 of 92 MB");
      expect(screen.queryByRole("button", { name: "Install" })).toBeNull();
      vi.advanceTimersByTime(4100);
      expect(onChange).toHaveBeenCalledTimes(2);
      rerender(<LiveInstall local={local({ state: "failed", error: "Step 'models' failed", steps: [{ id: "models", label: "Download models", size_mb: 1, status: "failed" }] })} onChange={onChange} />);
      expect(screen.getByRole("alert")).toHaveTextContent("Step 'models' failed");
      expect(screen.getByRole("button", { name: "Try again" })).toBeDisabled();
      vi.advanceTimersByTime(5000);
      expect(onChange).toHaveBeenCalledTimes(2);                      // polling stops once it is no longer installing
    } finally { vi.useRealTimers(); }
  });
});

const base = { turn: 1, endpoint_ms: 350 };
describe("LatencyCard", () => {
  it("breaks the last answer into stages and says where the number comes from", () => {
    render(<Breakdown title="Answer 2" m={{ ...base, stt_ms: 60, llm_first_token_ms: 30, tts_first_ms: 250, user_perceived_ms: 800, interrupted: true }} />);
    expect(screen.getByTestId("live-ttfa")).toHaveTextContent("800 ms");
    expect(screen.getByText(/Measured on the server/)).toBeInTheDocument();
    expect(screen.getByText("Wait to be sure you stopped")).toBeInTheDocument();
    expect(screen.getByText("This answer was interrupted.")).toBeInTheDocument();
  });

  it("prefers the time measured in the browser, formats seconds, and copes with missing numbers", () => {
    render(<Breakdown title="Answer" m={{ ...base, heard_ms: 1250 }} />);
    expect(screen.getByTestId("live-ttfa")).toHaveTextContent("1.25 s");
    expect(screen.queryByText(/Measured on the server/)).toBeNull();
    render(<Breakdown title="x" m={{ ...base }} />);
    expect(screen.getAllByTestId("live-ttfa")[1]).toHaveTextContent("–");
  });

  it("shows the median over the session and runs the speed test", async () => {
    routeFetch({ "POST /api/live/selftest": () => jsonResponse({ said: "Hello", turns: [{ ...base, heard: "Hello there", reply: "Hi", reply_audio_s: 2, user_perceived_ms: 900 }], median: { ...base, user_perceived_ms: 700, stt_ms: 60 },
      first: { user_perceived_ms: 900 }, note: "Times are server-side milliseconds", local_model: true }) });
    render(<LatencyCard last={null} turns={[{ ...base, heard_ms: 900 }, { ...base, user_perceived_ms: 700 }, { ...base, heard_ms: 800 }]} ready />);
    expect(screen.getByText("median 800 ms over 3")).toBeInTheDocument();
    expect(screen.getByText(/Talk to see how long/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Run a speed test/ }));
    expect(await screen.findByTestId("live-selftest")).toHaveTextContent("Typical (after warm-up)");
    expect(screen.getByTestId("live-selftest")).toHaveTextContent("heard: “Hello there”");
    expect(screen.getByTestId("live-selftest")).toHaveTextContent("model on this PC");
  });

  it("explains a failed speed test and is disabled until the voice is installed", async () => {
    routeFetch({ "POST /api/live/selftest": () => jsonResponse({ detail: "not installed" }, { status: 412 }) });
    const { rerender } = render(<LatencyCard last={{ ...base, user_perceived_ms: 700 }} turns={[]} ready={false} />);
    expect(screen.getByRole("button", { name: /Run a speed test/ })).toBeDisabled();
    rerender(<LatencyCard last={{ ...base, user_perceived_ms: 700 }} turns={[]} ready />);
    await userEvent.click(screen.getByRole("button", { name: /Run a speed test/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not installed");
  });

  it("reports a cloud model in the self-test summary", async () => {
    routeFetch({ "POST /api/live/selftest": () => jsonResponse({ said: "x", turns: [], median: base, first: {}, note: "n", local_model: false }) });
    render(<LatencyCard last={null} turns={[]} ready />);
    await userEvent.click(screen.getByRole("button", { name: /Run a speed test/ }));
    expect(await screen.findByText(/cloud model/)).toBeInTheDocument();
  });
});


const providers = [{ id: "ollama", label: "Ollama (local)", enabled: true, catalog: { needs_key: false } }, { id: "openai", label: "OpenAI", enabled: true, catalog: { needs_key: true } }] as any;

describe("LiveSettingsForm", () => {
  it("changes each setting through put, and is locked while a session runs", async () => {
    routeFetch({ "GET /api/providers/ollama/models": () => jsonResponse({ provider: "ollama", source: "live", error: null, cached: false, models: [{ id: "qwen3:8b", label: "qwen3:8b", input_per_m: 0, output_per_m: 0 }] }) });
    const put = vi.fn();
    const { rerender } = render(<LiveSettingsForm s={settings} voices={["af_heart", "am_adam"]} providers={providers} agents={[{ id: "a1", name: "Helper" }]} running={false} put={put} />);
    await userEvent.selectOptions(screen.getByLabelText("Provider"), "openai");
    expect(put).toHaveBeenLastCalledWith({ live_provider: "openai", live_model: "" });
    await userEvent.selectOptions(screen.getByLabelText("Agent"), "a1");
    expect(put).toHaveBeenLastCalledWith({ live_agent: "a1" });
    await userEvent.selectOptions(screen.getByLabelText("Voice"), "am_adam");
    expect(put).toHaveBeenLastCalledWith({ live_voice: "am_adam" });
    fireEvent.change(screen.getByLabelText("Speed"), { target: { value: "1.3" } });
    expect(put).toHaveBeenLastCalledWith({ live_speed: 1.3 });
    fireEvent.change(screen.getByLabelText("Wait before answering"), { target: { value: "500" } });
    expect(put).toHaveBeenLastCalledWith({ live_hangover_ms: 500 });
    await userEvent.click(screen.getByLabelText("Let me interrupt"));
    expect(put).toHaveBeenLastCalledWith({ live_barge_in: false });
    await userEvent.click(screen.getByLabelText("Start with the avatar"));
    expect(put).toHaveBeenLastCalledWith({ live_avatar: true });
    rerender(<LiveSettingsForm s={{ ...settings, live_provider: "gone" }} voices={["af_heart"]} providers={providers} agents={[]} running put={put} />);
    expect(screen.getByLabelText("Provider")).toBeDisabled();
    expect(screen.getByRole("option", { name: "gone" })).toBeInTheDocument();     // a provider that is no longer enabled is still shown
  });
});

describe("LiveSettingsCard (Settings > Voice)", () => {
  const status = { local: local({ ready: true, state: "ready" }), engine: { state: "idle", error: "" }, settings };
  const models = { "GET /api/providers/ollama/models": () => jsonResponse({ provider: "ollama", source: "live", error: null, cached: false, models: [] }) };

  it("loads its own data, saves a change and says whether the voice is installed", async () => {
    const { calls } = routeFetch({ ...models, "GET /api/live/status": () => jsonResponse(status), "GET /api/providers": () => jsonResponse([{ id: "ollama", label: "Ollama", enabled: true, catalog: { needs_key: false } }]),
      "GET /api/agents": () => jsonResponse([{ id: "a1", name: "Helper" }]), "PUT /api/live/settings": (init) => jsonResponse({ ...settings, ...JSON.parse(String(init?.body)) }) });
    render(<LiveSettingsCard />);
    expect(await screen.findByText("local voice installed")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Start with the avatar"));
    await waitFor(() => expect(screen.getByLabelText("Start with the avatar")).toBeChecked());
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ live_avatar: true });
  });

  it("shows an error when the backend cannot be reached or refuses a change", async () => {
    routeFetch({ "GET /api/live/status": () => jsonResponse({ detail: "down" }, { status: 500 }), "GET /api/providers": () => jsonResponse([]), "GET /api/agents": () => jsonResponse([]) });
    const { unmount } = render(<LiveSettingsCard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
    unmount();
    routeFetch({ ...models, "GET /api/live/status": () => jsonResponse({ ...status, local: local() }), "GET /api/providers": () => jsonResponse([]), "GET /api/agents": () => { throw new Error("no agents"); },
      "PUT /api/live/settings": () => jsonResponse({ detail: "bad value" }, { status: 422 }) });
    render(<LiveSettingsCard />);
    expect(await screen.findByText("not installed")).toBeInTheDocument();
    await act(async () => { fireEvent.change(screen.getByLabelText("Speed"), { target: { value: "1.5" } }); });
    expect(await screen.findByRole("alert")).toHaveTextContent("bad value");
  });
});
