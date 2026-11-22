import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ListenButton, MicButton, VoiceCard, loadVoice, speak, stopSpeaking, useVoice, type VoiceCfg } from "@/components/Voice";
import { ApiError } from "@/lib/api";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

const cfg: VoiceCfg = {
  configured: true, key_source: "store", key_hint: "…abcd", voice_id: "v1", tts_model: "m1", stt_model: "s1",
  auto_read: false, dictation: true, char_cap: 1000, chars_used_month: 950,
};
const voices = [
  { voice_id: "v1", name: "Rachel", category: "premade", preview_url: "https://cdn.test/rachel.mp3" },
  { voice_id: "v2", name: "Adam", category: "premade", preview_url: null },
];
const models = [{ model_id: "m1", name: "Flash", description: "" }, { model_id: "m2", name: "Multilingual", description: "" }];
const subscription = { tier: "creator", character_count: 1200, character_limit: 100000 };

/** An HTMLAudioElement stand-in that records each clip the app creates. */
class FakeAudio {
  static instances: FakeAudio[] = [];
  onended: (() => void) | null = null;
  pause = vi.fn();
  play = vi.fn(() => Promise.resolve());
  constructor(public src: string) { FakeAudio.instances.push(this); }
}

beforeEach(async () => {
  FakeAudio.instances = [];
  vi.stubGlobal("Audio", FakeAudio);
  vi.stubGlobal("URL", Object.assign(URL, {
    createObjectURL: vi.fn(() => `blob:clip-${Math.random()}`),
    revokeObjectURL: vi.fn(),
  }));
  await loadVoice(true);
});

afterEach(() => {
  stopSpeaking();
});

const audioBlob = () => new Response(new Blob(["mp3"]), { status: 200 });

describe("loadVoice / useVoice", () => {
  it("loads the settings once and shares them", async () => {
    const { calls } = routeFetch({ "GET /api/voice/settings": () => jsonResponse(cfg) });
    const a = await loadVoice(true);
    const b = await loadVoice();
    expect(a).toEqual(cfg);
    expect(b).toBe(a);
    expect(calls.filter((c) => c.url.pathname === "/api/voice/settings")).toHaveLength(1);
  });

  it("treats unreadable settings as absent", async () => {
    routeFetch({ "GET /api/voice/settings": () => textResponse("down", 500) });
    await expect(loadVoice(true)).resolves.toBeNull();
  });

  function Probe() {
    const v = useVoice();
    return <div>{v ? (v.configured ? "configured" : "unset") : "loading"}</div>;
  }

  it("gives components the current settings and refreshes on as:voice", async () => {
    let current = cfg;
    routeFetch({ "GET /api/voice/settings": () => jsonResponse(current) });
    render(<Probe />);
    expect(await screen.findByText("configured")).toBeInTheDocument();
    current = { ...cfg, configured: false };
    act(() => { window.dispatchEvent(new Event("as:voice")); });
    expect(await screen.findByText("unset")).toBeInTheDocument();
  });
});

describe("speak / stopSpeaking", () => {
  it("does nothing to stop when nothing is playing", () => {
    expect(() => stopSpeaking()).not.toThrow();
  });

  it("fetches the audio, plays it, and calls back when it ends", async () => {
    const { calls } = routeFetch({ "POST /api/voice/tts": () => audioBlob() });
    const onEnd = vi.fn();
    await speak("Hello there", onEnd);
    expect(calls[0].body).toEqual({ text: "Hello there" });
    const clip = FakeAudio.instances[0];
    expect(clip.play).toHaveBeenCalled();
    expect(clip.src).toMatch(/^blob:/);
    act(() => { clip.onended?.(); });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(clip.src);
    expect(onEnd).toHaveBeenCalledTimes(1);
  });

  it("stops the current clip when a new one starts", async () => {
    routeFetch({ "POST /api/voice/tts": () => audioBlob() });
    const onEndFirst = vi.fn();
    await speak("first", onEndFirst);
    await speak("second");
    const [first, second] = FakeAudio.instances;
    expect(first.pause).toHaveBeenCalled();
    expect(onEndFirst).toHaveBeenCalled();
    expect(second.play).toHaveBeenCalled();
  });

  it("stopSpeaking pauses the clip and calls back", async () => {
    routeFetch({ "POST /api/voice/tts": () => audioBlob() });
    const onEnd = vi.fn();
    await speak("stop me", onEnd);
    stopSpeaking();
    expect(FakeAudio.instances[0].pause).toHaveBeenCalled();
    expect(onEnd).toHaveBeenCalled();
  });

  it("ignores an ended event from a clip that was already replaced", async () => {
    routeFetch({ "POST /api/voice/tts": () => audioBlob() });
    await speak("one");
    const old = FakeAudio.instances[0];
    await speak("two");
    act(() => { old.onended?.(); });
    // the newer clip is still the current one, so stopSpeaking still reaches it
    stopSpeaking();
    expect(FakeAudio.instances[1].pause).toHaveBeenCalled();
  });

  it("raises the server's detail when synthesis is refused", async () => {
    routeFetch({ "POST /api/voice/tts": () => jsonResponse({ detail: "character cap reached" }, { status: 402 }) });
    const err = await speak("x").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("character cap reached");
  });

  it("raises an HTTP error when the failure body is not JSON", async () => {
    routeFetch({ "POST /api/voice/tts": () => textResponse("nope", 500) });
    await expect(speak("x")).rejects.toThrow("HTTP 500");
  });
});

describe("ListenButton", () => {
  it("reads the text aloud, shows Stop, and returns to Listen when the clip ends", async () => {
    routeFetch({ "POST /api/voice/tts": () => audioBlob() });
    render(<ListenButton text="Read me" />);
    await userEvent.click(screen.getByRole("button", { name: "Listen" }));
    const stop = await screen.findByRole("button", { name: "Stop" });
    expect(stop).toBeInTheDocument();
    act(() => { FakeAudio.instances[0].onended?.(); });
    expect(await screen.findByRole("button", { name: "Listen" })).toBeInTheDocument();
  });

  it("shows loading while the audio is fetched", async () => {
    let finish!: (r: Response) => void;
    routeFetch({ "POST /api/voice/tts": () => new Promise<Response>((r) => { finish = r; }) });
    render(<ListenButton text="Read me" />);
    await userEvent.click(screen.getByRole("button", { name: "Listen" }));
    expect(screen.getByRole("button", { name: "Loading…" })).toBeInTheDocument();
    await act(async () => { finish(audioBlob()); });
    await screen.findByRole("button", { name: "Stop" });
  });

  it("stops playback when pressed again while playing", async () => {
    routeFetch({ "POST /api/voice/tts": () => audioBlob() });
    render(<ListenButton text="Read me" />);
    await userEvent.click(screen.getByRole("button", { name: "Listen" }));
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    expect(FakeAudio.instances[0].pause).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Listen" })).toBeInTheDocument();
  });

  it("shows a short error when speaking fails, and a truncated long one", async () => {
    routeFetch({ "POST /api/voice/tts": () => jsonResponse({ detail: "No ElevenLabs key" }, { status: 400 }) });
    const { unmount } = render(<ListenButton text="x" />);
    await userEvent.click(screen.getByRole("button", { name: "Listen" }));
    expect(await screen.findByText("No ElevenLabs key")).toHaveClass("err");
    unmount();

    const long = "This is a considerably longer explanation of why the voice service refused the request";
    routeFetch({ "POST /api/voice/tts": () => jsonResponse({ detail: long }, { status: 400 }) });
    render(<ListenButton text="x" />);
    await userEvent.click(screen.getByRole("button", { name: "Listen" }));
    const shown = await screen.findByTitle(long);
    expect(shown.textContent).toBe(`${long.slice(0, 60)}…`);
  });

  it("starts automatically once when autoPlay is set", async () => {
    const { calls } = routeFetch({ "POST /api/voice/tts": () => audioBlob() });
    const { rerender } = render(<ListenButton text="auto" autoPlay />);
    await screen.findByRole("button", { name: "Stop" });
    rerender(<ListenButton text="auto" autoPlay />);
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });
});

describe("MicButton", () => {
  type Rec = { mimeType: string; ondataavailable: ((e: { data: Blob }) => void) | null; onstop: (() => void) | null; start: () => void; stop: () => void };
  let recorder: Rec;
  let trackStop: ReturnType<typeof vi.fn>;

  function stubMic(reject?: Error) {
    trackStop = vi.fn();
    const stream = { getTracks: () => [{ stop: trackStop }] };
    const getUserMedia = reject ? vi.fn().mockRejectedValue(reject) : vi.fn().mockResolvedValue(stream);
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
    class FakeRecorder {
      mimeType = "";
      ondataavailable: Rec["ondataavailable"] = null;
      onstop: Rec["onstop"] = null;
      start = vi.fn();
      stop = vi.fn(() => { this.onstop?.(); });
      constructor() { recorder = this as unknown as Rec; }
    }
    vi.stubGlobal("MediaRecorder", FakeRecorder);
  }

  it("records, transcribes, and hands the text to the composer", async () => {
    stubMic();
    const onText = vi.fn();
    const { calls } = routeFetch({ "POST /api/voice/stt": () => jsonResponse({ text: "hello world" }) });
    render(<MicButton onText={onText} />);
    await userEvent.click(screen.getByTitle("Dictate (ElevenLabs)"));
    expect(await screen.findByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(recorder.start).toHaveBeenCalled();
    recorder.ondataavailable?.({ data: new Blob([]) });
    recorder.ondataavailable?.({ data: new Blob(["abc"]) });
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(onText).toHaveBeenCalledWith("hello world"));
    expect(trackStop).toHaveBeenCalled();
    const body = calls[0].body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect((body.get("file") as File).name).toBe("speech.webm");
  });

  it("does not forward an empty transcript", async () => {
    stubMic();
    const onText = vi.fn();
    routeFetch({ "POST /api/voice/stt": () => jsonResponse({ text: "" }) });
    render(<MicButton onText={onText} />);
    await userEvent.click(screen.getByTitle("Dictate (ElevenLabs)"));
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.getByTitle("Dictate (ElevenLabs)")).toBeInTheDocument());
    expect(onText).not.toHaveBeenCalled();
  });

  it("shows a transcribing state and ignores presses until it finishes", async () => {
    stubMic();
    let finish!: (r: Response) => void;
    const { calls } = routeFetch({ "POST /api/voice/stt": () => new Promise<Response>((r) => { finish = r; }) });
    render(<MicButton onText={() => {}} />);
    await userEvent.click(screen.getByTitle("Dictate (ElevenLabs)"));
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    const busy = screen.getByTitle("Dictate (ElevenLabs)");
    expect(busy).toHaveTextContent("…");
    await userEvent.click(busy);
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    await act(async () => { finish(jsonResponse({ text: "" })); });
  });

  it("reports a transcription failure", async () => {
    stubMic();
    routeFetch({ "POST /api/voice/stt": () => jsonResponse({ detail: "audio too short" }, { status: 422 }) });
    render(<MicButton onText={() => {}} />);
    await userEvent.click(screen.getByTitle("Dictate (ElevenLabs)"));
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    expect(await screen.findByText("audio too short")).toHaveClass("err");
  });

  it("reports a transcription failure without a detail message", async () => {
    stubMic();
    routeFetch({ "POST /api/voice/stt": () => textResponse("bad gateway", 502) });
    render(<MicButton onText={() => {}} />);
    await userEvent.click(screen.getByTitle("Dictate (ElevenLabs)"));
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    expect(await screen.findByText("HTTP 502")).toBeInTheDocument();
  });

  it("explains when the microphone is not available", async () => {
    stubMic(new Error("Permission denied"));
    render(<MicButton onText={() => {}} className="mic" />);
    await userEvent.click(screen.getByTitle("Dictate (ElevenLabs)"));
    expect(await screen.findByText("Microphone unavailable: Permission denied")).toBeInTheDocument();
  });
});

describe("VoiceCard", () => {
  function routes(over: Partial<Record<"settings" | "voices" | "models" | "subscription", () => Response>> = {}) {
    return {
      "GET /api/voice/settings": over.settings ?? (() => jsonResponse(cfg)),
      "GET /api/voice/voices": over.voices ?? (() => jsonResponse(voices)),
      "GET /api/voice/models": over.models ?? (() => jsonResponse(models)),
      "GET /api/voice/subscription": over.subscription ?? (() => jsonResponse(subscription)),
    };
  }

  it("shows the connection, plan and monthly usage", async () => {
    routeFetch(routes());
    render(<VoiceCard />);
    expect(await screen.findByText("connected …abcd")).toBeInTheDocument();
    expect(screen.getByText("creator plan · 1,200 / 100,000 credits used")).toBeInTheDocument();
    expect(screen.getByText(/950 characters read aloud of 1,000/)).toBeInTheDocument();
    expect(document.querySelector(".bar i")).toHaveClass("b");
  });

  it("describes a key supplied by the environment", async () => {
    routeFetch(routes({ settings: () => jsonResponse({ ...cfg, key_hint: null, key_source: "env" }) }));
    render(<VoiceCard />);
    expect(await screen.findByText("connected (env)")).toBeInTheDocument();
  });

  it("is not set up without a key, and does not ask for voices", async () => {
    const { calls } = routeFetch({ "GET /api/voice/settings": () => jsonResponse({ ...cfg, configured: false, key_hint: null, key_source: null }) });
    render(<VoiceCard />);
    expect(await screen.findByText("not set up")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Add a key first" })).toBeInTheDocument();
    expect(calls.map((c) => c.url.pathname)).toEqual(["/api/voice/settings"]);
    expect(screen.queryByRole("button", { name: "Listen" })).toBeNull();
  });

  it("saves a new key, clears the field and tells the app", async () => {
    const seen = vi.fn();
    window.addEventListener("as:voice", seen);
    const { calls } = routeFetch({
      ...routes(),
      "PUT /api/voice/settings": () => jsonResponse({ ...cfg, key_hint: "…wxyz" }),
    });
    render(<VoiceCard />);
    await screen.findByText("connected …abcd");
    const key = screen.getByLabelText(/API key/) as HTMLInputElement;
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await userEvent.type(key, "xi-secret");
    await userEvent.click(save);
    expect(await screen.findByText("connected …wxyz")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ api_key: "xi-secret" });
    expect(key.value).toBe("");
    expect(seen).toHaveBeenCalled();
    expect(screen.getByText("Saved")).toBeInTheDocument();
    window.removeEventListener("as:voice", seen);
  });

  it("reports a save that the server refuses", async () => {
    routeFetch({
      ...routes(),
      "PUT /api/voice/settings": () => jsonResponse({ detail: "key rejected" }, { status: 400 }),
    });
    render(<VoiceCard />);
    await screen.findByText("connected …abcd");
    await userEvent.type(screen.getByLabelText(/API key/), "bad");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("⚠ key rejected")).toBeInTheDocument();
  });

  it("changes the voice, speech model, and toggles", async () => {
    const { calls } = routeFetch({
      ...routes(),
      "PUT /api/voice/settings": () => jsonResponse(cfg),
    });
    render(<VoiceCard />);
    await screen.findByText("connected …abcd");
    await userEvent.selectOptions(screen.getByLabelText("Voice"), "v2");
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && (c.body as any).voice_id === "v2")).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText("Speech model"), "m2");
    await waitFor(() => expect(calls.some((c) => (c.body as any)?.tts_model === "m2")).toBe(true));
    const [autoRead, dictation] = screen.getAllByRole("checkbox");
    await userEvent.click(autoRead);
    await waitFor(() => expect(calls.some((c) => (c.body as any)?.auto_read === true)).toBe(true));
    await userEvent.click(dictation);
    await waitFor(() => expect(calls.some((c) => (c.body as any)?.dictation === false)).toBe(true));
  });

  it("saves a new monthly character cap when the field loses focus", async () => {
    const { calls } = routeFetch({
      ...routes(),
      "PUT /api/voice/settings": () => jsonResponse(cfg),
    });
    render(<VoiceCard />);
    await screen.findByText("connected …abcd");
    const cap = screen.getByLabelText(/Monthly character cap/);
    await userEvent.clear(cap);
    await userEvent.type(cap, "500");
    fireEvent.blur(cap);
    await waitFor(() => expect(calls.some((c) => (c.body as any)?.char_cap === 500)).toBe(true));
  });

  it("reports a setting the server will not store", async () => {
    routeFetch({
      ...routes(),
      "PUT /api/voice/settings": () => jsonResponse({ detail: "unknown voice" }, { status: 400 }),
    });
    render(<VoiceCard />);
    await screen.findByText("connected …abcd");
    await userEvent.selectOptions(screen.getByLabelText("Voice"), "v2");
    expect(await screen.findByText("⚠ unknown voice")).toBeInTheDocument();
  });

  it("offers a preview of the chosen voice when one exists", async () => {
    routeFetch(routes());
    render(<VoiceCard />);
    await screen.findByText("connected …abcd");
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(FakeAudio.instances.at(-1)?.src).toBe("https://cdn.test/rachel.mp3");
  });

  it("does not offer a preview for a voice without one", async () => {
    routeFetch({ ...routes(), "GET /api/voice/settings": () => jsonResponse({ ...cfg, voice_id: "v2" }) });
    render(<VoiceCard />);
    await screen.findByText("connected …abcd");
    expect(screen.queryByRole("button", { name: "Preview" })).toBeNull();
  });

  it("keeps a speech model that is not in the catalogue visible", async () => {
    routeFetch({ ...routes(), "GET /api/voice/settings": () => jsonResponse({ ...cfg, tts_model: "legacy-v1" }) });
    render(<VoiceCard />);
    expect(await screen.findByRole("option", { name: "legacy-v1" })).toBeInTheDocument();
  });

  it("offers a test read-out once a voice is chosen", async () => {
    routeFetch({ ...routes(), "POST /api/voice/tts": () => audioBlob() });
    render(<VoiceCard />);
    await screen.findByText("connected …abcd");
    await userEvent.click(await screen.findByRole("button", { name: "Listen" }));
    await screen.findByRole("button", { name: "Stop" });
  });

  it("shows the message when the first settings request fails", async () => {
    routeFetch({ ...routes(), "GET /api/voice/settings": () => textResponse("down", 500) });
    render(<VoiceCard />);
    expect(await screen.findByText("HTTP 500")).toHaveClass("err");
  });

  it("explains when the voice list cannot be loaded", async () => {
    routeFetch({ ...routes(), "GET /api/voice/voices": () => jsonResponse({ detail: "voices unavailable" }, { status: 503 }) });
    render(<VoiceCard />);
    expect(await screen.findByText("voices unavailable")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Loading voices…" })).toBeInTheDocument();
  });

  it("tolerates missing models and subscription details", async () => {
    routeFetch({
      ...routes(),
      "GET /api/voice/models": () => textResponse("x", 500),
      "GET /api/voice/subscription": () => textResponse("x", 500),
    });
    render(<VoiceCard />);
    expect(await screen.findByText("connected …abcd")).toBeInTheDocument();
    expect(screen.queryByText(/plan ·/)).toBeNull();
  });
});
