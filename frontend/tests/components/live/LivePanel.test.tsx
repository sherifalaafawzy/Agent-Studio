import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LivePanel from "@/components/live/LivePanel";
import { jsonResponse, routeFetch } from "../../helpers/http";
import { livePrices, liveSettings as settings } from "../../helpers/liveFakes";

// The browser client and the video player are replaced by fakes that record what the panel asks of them.
const h = vi.hoisted(() => ({ clients: [] as any[], players: [] as any[] }));
vi.mock("@/lib/live", async (orig) => {
  const real = await orig<typeof import("@/lib/live")>();
  class FakeClient {
    analyser = { fftSize: 8, frequencyBinCount: 8, getByteTimeDomainData: (a: Uint8Array) => a.fill(128), getByteFrequencyData: (a: Uint8Array) => a.fill(0) }; started: any = null; muted = false; calls: string[] = [];
    constructor(public ev: any) { h.clients.push(this); }
    async start(o: any) { this.started = o; }
    stop() { this.calls.push("stop"); this.ev.onState("idle"); }
    interrupt() { this.calls.push("interrupt"); }
    setMuted(m: boolean) { this.muted = m; }
    say(t: string) { this.calls.push("say:" + t); }
  }
  return { ...real, LiveClient: FakeClient, realDeps: () => ({}) };
});
vi.mock("@/lib/avatarVideo", () => {
  class FakePlayer {
    log: string[] = [];
    constructor(public video: HTMLVideoElement, public deps: unknown, public onFirst: (ms: number) => void) { h.players.push(this); }
    markWaiting() { this.log.push("waiting"); }
    push(mime: string, data: string) { this.log.push(`push:${mime}:${data}`); }
    finish() { this.log.push("finish"); }
    reset() { this.log.push("reset"); }
    interrupted() { this.log.push("interrupted"); }
  }
  return { AvatarVideoPlayer: FakePlayer, realPlayerDeps: () => ({}) };
});

const local = { state: "ready", ready: true, error: null, steps: [], percent: 100, device: "cuda", stt_model: "small.en", auto_device: "cuda", progress_text: "", log_tail: [], download_mb: 0, free_gb: 10, expect: "",
  voices: ["af_heart"], licences: [], hardware: { nvidia: true, gpu: "RTX" }, versions: null };
const providers = [{ id: "ollama", label: "Ollama", enabled: true, kind: "ollama", catalog: { needs_key: false } }, { id: "vertex_ai", label: "Google Vertex AI", enabled: false, kind: "vertex_ai", catalog: { needs_key: false } }];
const gem = { configured: true, problems: [], provider: "vertex_ai", project: "p", location: "us-central1", model: "gemini-3.8-live", auth_mode: "adc", voices: ["Puck", "Aoede"], languages: ["en-US"], endpoint: "wss://x", avatar_examples: ["Ben"], notes: ["data leaves this PC"], prices: { in: {}, out: {} } };

function routes(over: Record<string, any> = {}, st: any = {}) {
  return routeFetch({
    "GET /api/live/status": () => jsonResponse({ local, engine: { state: "idle", error: "" }, settings: { ...settings, ...st } }),
    "GET /api/providers": () => jsonResponse(providers), "GET /api/agents": () => jsonResponse([{ id: "a1", name: "Helper" }]),
    "GET /api/providers/ollama/models": () => jsonResponse({ provider: "ollama", source: "live", error: null, cached: false, models: [] }),
    "PUT /api/live/settings": (init: any) => jsonResponse({ ...settings, ...st, ...JSON.parse(String(init?.body)) }),
    "GET /api/live/gemini/status": () => jsonResponse(gem), "GET /api/live/prices": () => jsonResponse(livePrices), "GET /api/live/history": () => jsonResponse([]), ...over,
  });
}

beforeEach(() => { h.clients.length = 0; h.players.length = 0; });
afterEach(() => vi.unstubAllGlobals());

const fire = (fn: () => void) => act(() => { fn(); });

describe("LivePanel (local engine)", () => {
  it("starts a session with the saved settings, shows the conversation and its timings, and ends it", async () => {
    routes();
    render(<LivePanel />);
    await userEvent.click(await screen.findByTestId("live-start"));
    const c = h.clients[0];
    expect(c.started).toMatchObject({ engine: "local", avatar: false, provider: "ollama", model: "qwen3:8b", voice: "af_heart", hangover_ms: 350, barge_in: true });
    fire(() => c.ev.onState("listening"));
    expect(screen.getByTestId("live-state")).toHaveTextContent("Listening");
    fire(() => c.ev.onReady({}));
    fire(() => c.ev.onUser("what is the moon"));
    fire(() => c.ev.onAssistant("The moon"));
    fire(() => c.ev.onAssistant(" is a rock"));
    expect(screen.getByTestId("live-log")).toHaveTextContent("The moon is a rock");
    fire(() => c.ev.onAssistant("", "The moon is a rock."));
    fire(() => c.ev.onAssistant("", "Standalone final."));                  // a final with no streamed deltas before it
    fire(() => c.ev.onMetrics({ turn: 1, endpoint_ms: 350, user_perceived_ms: 700, heard_ms: 820, stt_ms: 60 }));
    expect(screen.getByTestId("live-ttfa")).toHaveTextContent("820 ms");
    fire(() => c.ev.onClear());
    fire(() => c.ev.onError("boom"));
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    await userEvent.type(screen.getByLabelText("Type a message"), "  hello  ");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(c.calls).toContain("say:hello");
    await userEvent.click(screen.getByRole("button", { name: "Mute" }));
    expect(c.muted).toBe(true);
    fire(() => c.ev.onState("speaking"));
    await userEvent.click(screen.getByRole("button", { name: "Interrupt" }));
    expect(c.calls).toContain("interrupt");
    await userEvent.click(screen.getByTestId("live-stop"));
    expect(c.calls).toContain("stop");
    expect(screen.getByTestId("live-start")).toBeInTheDocument();
  });

  it("saves the mode switch and the settings, and starts the voice-only orb or the avatar face", async () => {
    const { calls } = routes();
    render(<LivePanel />);
    expect(await screen.findByTestId("live-orb")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "With avatar" }));
    expect(screen.getByTestId("live-avatar")).toBeInTheDocument();
    expect(calls.filter((x) => x.method === "PUT").pop()?.body).toEqual({ live_avatar: true });
    await userEvent.click(screen.getByRole("button", { name: "Voice only" }));
    expect(screen.getByTestId("live-orb")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Voice"), "af_heart");
  });

  it("asks for the local install when it is missing and disables Start", async () => {
    routes({ "GET /api/live/status": () => jsonResponse({ local: { ...local, ready: false, state: "not_installed" }, engine: { state: "idle", error: "" }, settings }) });
    render(<LivePanel />);
    expect(await screen.findByTestId("live-install")).toBeInTheDocument();
    expect(screen.getByTestId("live-start")).toBeDisabled();
  });

  it("shows a load error, and a failed settings save", async () => {
    routeFetch({ "GET /api/live/status": () => jsonResponse({ detail: "down" }, { status: 500 }), "GET /api/providers": () => jsonResponse([]), "GET /api/agents": () => jsonResponse([]) });
    const { unmount } = render(<LivePanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
    unmount();
    routes({ "PUT /api/live/settings": () => jsonResponse({ detail: "nope" }, { status: 422 }) });
    render(<LivePanel />);
    await userEvent.click(await screen.findByRole("button", { name: "With avatar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
  });

  it("shows the avatar by default when the setting says so, and tells you while the model loads", async () => {
    routes({}, { live_avatar: true });
    render(<LivePanel />);
    expect(await screen.findByTestId("live-avatar")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("live-start"));
    fire(() => h.clients[0].ev.onState("loading"));
    expect(screen.getByText(/Loading the model and the voice/)).toBeInTheDocument();
  });
});

describe("LivePanel (Google Gemini Live)", () => {
  it("shows the cloud banner, needs no local install, and starts the avatar session with the video player", async () => {
    routes({}, { live_engine: "gemini", live_avatar: true });
    render(<LivePanel />);
    expect(await screen.findByTestId("live-cloud-banner")).toHaveTextContent("go to Google Cloud");
    expect(screen.queryByTestId("live-install")).toBeNull();
    expect(screen.getByTestId("gemini-section")).toBeInTheDocument();
    expect(screen.queryByText(/Run a speed test/)).toBeNull();           // the self-test is for the local engine
    await userEvent.click(screen.getByTestId("live-start"));
    const c = h.clients[0];
    expect(c.started).toMatchObject({ engine: "gemini", avatar: true });
    fire(() => c.ev.onState("loading"));
    expect(screen.getByTestId("live-video-wait")).toBeInTheDocument();
    const p = h.players[0];
    fire(() => c.ev.onState("thinking"));
    expect(p.log).toContain("waiting");
    fire(() => c.ev.onVideo("video/mp4", "QUJD"));
    expect(p.log).toContain("push:video/mp4:QUJD");
    expect(screen.getByTestId("live-video").style.display).toBe("block");
    fire(() => c.ev.onAudioStart());
    expect((screen.getByTestId("live-video") as HTMLVideoElement).muted).toBe(true);
    p.onFirst(310);
    fire(() => c.ev.onMetrics({ turn: 1, endpoint_ms: 350, user_perceived_ms: 800, first_video_ms: 500, cloud: true, cost_usd: 0.0123 }));
    expect(p.log).toContain("finish");
    expect(screen.getByTestId("live-cloud-note")).toHaveTextContent("$0.0123");
    expect(screen.getByText("Avatar's first picture (seen)")).toBeInTheDocument();
    fire(() => c.ev.onNotice("Gemini Live sent data that is not JSON"));
    expect(screen.getByTestId("live-notice")).toHaveTextContent("not JSON");
    fire(() => c.ev.onClear());
    expect(p.log).toContain("interrupted");                                         // an interruption does NOT tear the continuous stream down ...
    expect(p.log).not.toContain("reset");
    fire(() => c.ev.onVideo("video/mp4", "REVG"));                                    // ... so the next answer's video still arrives and is pushed
    expect(p.log[p.log.length - 1]).toBe("push:video/mp4:REVG");
    await userEvent.click(screen.getByTestId("live-stop"));
    expect(p.log).toContain("reset");                                                 // only ending the session does
  });

  it("voice-only Gemini needs no video element and passes avatar=false", async () => {
    routes({}, { live_engine: "gemini", live_avatar: false });
    render(<LivePanel />);
    await userEvent.click(await screen.findByTestId("live-start"));
    expect(h.clients[0].started).toMatchObject({ engine: "gemini", avatar: false });
    expect(screen.queryByTestId("live-video")).toBeNull();
    expect(h.players).toHaveLength(0);
    expect(screen.getByTestId("live-orb")).toBeInTheDocument();
  });

  it("shows the avatar face before a session starts so the page is never empty", async () => {
    routes({}, { live_engine: "gemini", live_avatar: true });
    render(<LivePanel />);
    expect(await screen.findByTestId("live-avatar")).toBeInTheDocument();
  });
});

describe("LivePanel: interrupting and talking again on Gemini (avatar)", () => {
  it("the Interrupt button silences the video's sound, and the next answer makes it audible again; the video element is never rebuilt in between", async () => {
    routes({}, { live_engine: "gemini", live_avatar: true });
    render(<LivePanel />);
    await userEvent.click(await screen.findByTestId("live-start"));
    const c = h.clients[0];
    fire(() => c.ev.onState("loading"));
    const video = screen.getByTestId("live-video") as HTMLVideoElement;
    fire(() => c.ev.onState("listening"));
    fire(() => c.ev.onState("thinking"));
    fire(() => c.ev.onVideo("video/mp4", "QUJD"));
    fire(() => c.ev.onState("speaking"));
    await userEvent.click(screen.getByRole("button", { name: "Interrupt" }));
    expect(video.muted).toBe(true);
    fire(() => c.ev.onClear());
    fire(() => c.ev.onState("listening"));
    fire(() => c.ev.onState("hearing"));
    fire(() => c.ev.onState("thinking"));                                            // the second question is being answered
    expect(video.muted).toBe(false);
    fire(() => c.ev.onVideo("video/mp4", "REVG"));
    expect(h.players[0].log.filter((x: string) => x.startsWith("push:"))).toEqual(["push:video/mp4:QUJD", "push:video/mp4:REVG"]);
    expect(h.players[0].log).not.toContain("reset");
    expect(screen.getByTestId("live-video").style.display).toBe("block");
  });
});

describe("LivePanel: a notice goes away when the next answer starts", () => {
  it("shows 'ended without replying' and clears it when the person is answered", async () => {
    routes({}, { live_engine: "gemini", live_avatar: false });
    render(<LivePanel />);
    await userEvent.click(await screen.findByTestId("live-start"));
    const c = h.clients[0];
    fire(() => c.ev.onState("listening"));
    fire(() => c.ev.onNotice("Gemini ended that turn without replying (it sometimes does this). Say it again."));
    expect(screen.getByTestId("live-notice")).toHaveTextContent("without replying");
    fire(() => c.ev.onState("thinking"));
    expect(screen.queryByTestId("live-notice")).toBeNull();
    fire(() => c.ev.onNotice("again"));
    fire(() => c.ev.onState("speaking"));
    expect(screen.queryByTestId("live-notice")).toBeNull();
  });
});
