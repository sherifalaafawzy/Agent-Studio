import { afterEach, describe, expect, it, vi } from "vitest";
import { LiveClient, WORKLET, int16ToFloat32, mouthShape, readVoice, realDeps, wsUrlFor, type LiveState } from "@/lib/live";
import { FakeCtx, FakeNode, FakeSocket, fakeDeps } from "../helpers/liveFakes";

afterEach(() => vi.unstubAllGlobals());

async function started(opts = {}) {
  const deps = fakeDeps();
  const ev = { onState: vi.fn(), onUser: vi.fn(), onAssistant: vi.fn(), onMetrics: vi.fn(), onError: vi.fn(), onClear: vi.fn(), onReady: vi.fn() };
  const c = new LiveClient(ev, deps);
  await c.start(opts);
  const ws = FakeSocket.last;
  return { c, ws, ev, deps };
}

describe("helpers", () => {
  it("builds the websocket address from the API address", () => {
    expect(wsUrlFor("http://127.0.0.1:8765")).toBe("ws://127.0.0.1:8765/api/live/ws");
    expect(wsUrlFor("https://x.test/")).toBe("wss://x.test/api/live/ws");
    expect(wsUrlFor("")).toBe(window.location.origin.replace(/^http/, "ws") + "/api/live/ws");   // desktop build: same origin
  });
  it("converts 16-bit audio to floats and ignores a stray odd byte", () => {
    const i = new Int16Array([0, 16384, -32768]);
    expect(Array.from(int16ToFloat32(i.buffer))).toEqual([0, 0.5, -1]);
    expect(int16ToFloat32(new ArrayBuffer(0)).length).toBe(0);
    expect(int16ToFloat32(new ArrayBuffer(3)).length).toBe(1);
  });
  it("opens the mouth with loudness and widens it with brightness, within 0..1", () => {
    expect(mouthShape(0, 0.3).open).toBe(0);
    expect(mouthShape(0.5, 0.3).open).toBe(1);
    expect(mouthShape(0.1, 0.9).wide).toBeGreaterThan(mouthShape(0.1, 0.1).wide);
    expect(mouthShape(0.1, 5).wide).toBe(1);
    expect(mouthShape(0.1, -5).wide).toBe(0);
  });
  it("reads level and brightness from an analyser, and copes with silence", () => {
    const a = new FakeCtx(24000).analyser as unknown as AnalyserNode;
    const v = readVoice(a);
    expect(v.level).toBeCloseTo(0.5, 1);
    expect(v.bright).toBeGreaterThan(0.5);
    (a.getByteFrequencyData as any).mockImplementation((f: Uint8Array) => f.fill(0));
    expect(readVoice(a).bright).toBe(0);
  });
  it("the worklet source cuts 20 ms frames", () => {
    expect(WORKLET).toContain("registerProcessor('cap'");
    expect(WORKLET).toContain("320");
  });
  it("real deps use the browser's own APIs", async () => {
    class W { constructor(public url: string) {} }
    class AC { constructor(public o: unknown) {} }
    vi.stubGlobal("WebSocket", W);
    vi.stubGlobal("AudioContext", AC);
    const getUserMedia = vi.fn(async () => "stream");
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    const d = realDeps("http://h:1");
    expect((d.newSocket("ws://a") as any).url).toBe("ws://a");
    expect((d.newContext(24000) as any).o).toMatchObject({ sampleRate: 24000 });
    await d.getMic();
    expect(getUserMedia).toHaveBeenCalledWith({ audio: expect.objectContaining({ echoCancellation: true, channelCount: 1 }) });
    expect(typeof d.now()).toBe("number");
    expect(d.wsUrl).toBe("ws://h:1/api/live/ws");
  });
});

describe("LiveClient", () => {
  it("reports a refused microphone in plain words and stays idle", async () => {
    const deps = fakeDeps({ getMic: async () => { throw new Error("Permission denied"); } });
    const ev = { onState: vi.fn(), onError: vi.fn() };
    const c = new LiveClient(ev, deps);
    await c.start();
    expect(ev.onError).toHaveBeenCalledWith(expect.stringContaining("microphone is not available: Permission denied"));
    expect(c.state).toBe("idle");
    const e2 = { onError: vi.fn() };
    await new LiveClient(e2, fakeDeps({ getMic: async () => { throw new Error(""); } })).start();
    expect(e2.onError).toHaveBeenCalledWith(expect.stringContaining("permission was refused"));
  });

  it("opens the socket, sends the start message with the options and waits for the models", async () => {
    const { c, ws, ev } = await started({ provider: "ollama", model: "qwen3:8b", hangover_ms: 400 });
    expect(ws.url).toBe("ws://x/api/live/ws");
    expect(ws.binaryType).toBe("arraybuffer");
    ws.onopen!();
    expect(ws.json()[0]).toEqual({ type: "start", provider: "ollama", model: "qwen3:8b", hangover_ms: 400 });
    expect(c.state).toBe("loading");
    expect(ev.onState).toHaveBeenCalledWith("loading");
    expect(FakeCtx.all.map((x) => x.rate)).toEqual([24000, 16000]);
    await c.start();                                                  // a second start while connected does nothing
    expect(FakeSocket.last).toBe(ws);
  });

  it("sends microphone frames only once the models are ready, and mute is passed to the worklet", async () => {
    const { c, ws } = await started();
    ws.onopen!();
    const frame = new Int16Array(320).buffer;
    FakeNode.last.port.onmessage!({ data: frame });
    expect(ws.sent.some((s) => s instanceof ArrayBuffer)).toBe(false);          // still loading
    ws.emit({ type: "state", state: "listening" });
    FakeNode.last.port.onmessage!({ data: frame });
    expect(ws.sent.filter((s) => s instanceof ArrayBuffer)).toHaveLength(1);
    c.setMuted(true);
    expect(c.isMuted).toBe(true);
    expect(FakeNode.last.port.postMessage).toHaveBeenCalledWith({ muted: true });
  });

  it("turns server messages into events", async () => {
    const { ws, ev, c } = await started({ hangover_ms: 350 });
    ws.onopen!();
    const states: LiveState[] = [];
    ev.onState.mockImplementation((s: LiveState) => states.push(s));
    ws.emit({ type: "ready", voice: "af_heart" });
    ws.emit({ type: "transcript", role: "user", text: "hello" });
    ws.emit({ type: "assistant", delta: "Hi" });
    ws.emit({ type: "assistant", final: true, text: "Hi there" });
    ws.emit({ type: "error", message: "boom" });
    ws.emit({ type: "state", state: "hearing" });
    expect(ev.onReady).toHaveBeenCalled();
    expect(ev.onUser).toHaveBeenCalledWith("hello");
    expect(ev.onAssistant).toHaveBeenNthCalledWith(1, "Hi", undefined);
    expect(ev.onAssistant).toHaveBeenNthCalledWith(2, "", "Hi there");
    expect(ev.onError).toHaveBeenCalledWith("boom");
    expect(states).toEqual(["hearing"]);
    expect(c.state).toBe("hearing");
  });

  it("plays audio gaplessly and measures the first sound from the moment you stopped talking", async () => {
    const { ws, ev, deps } = await started({ hangover_ms: 350 });
    ws.onopen!();
    deps.clock.t = 5000;
    ws.emit({ type: "state", state: "thinking" });                   // the server decided you stopped (350 ms after you really did)
    deps.clock.t = 5100;
    ws.audio(2400);                                                  // 0.1 s of audio arrives 100 ms later
    ws.audio(2400);
    const out = FakeCtx.all[0];
    expect(out.sources).toHaveLength(2);
    const [a, b] = out.sources;
    expect(a.start).toHaveBeenCalledWith(1.02);                      // now + 20 ms
    expect(b.start.mock.calls[0][0]).toBeCloseTo(1.12, 5);           // right after the first one: no gap
    ws.emit({ type: "metrics", turn: 1, endpoint_ms: 350, ttfa_ms: 600 });
    const m = ev.onMetrics.mock.calls[0][0];
    // 100 ms of waiting + 20 ms scheduling + 30 ms device latency + the 350 ms end-of-speech wait
    expect(m.heard_ms).toBe(500);
    expect(m.turn).toBe(1);
    expect(m).not.toHaveProperty("type");
    a.onended!();
    ws.audio(0);                                                     // empty audio is ignored
    expect(out.sources).toHaveLength(2);
  });

  it("stays in the speaking state until the queued audio has really finished playing", async () => {
    vi.useFakeTimers();
    try {
      const { c, ws, ev } = await started();
      ws.onopen!();
      ws.emit({ type: "state", state: "speaking" });
      ws.audio(48000);                                              // 2 s of audio, sent at once
      ws.emit({ type: "state", state: "listening" });               // the server is done sending
      expect(c.state).toBe("speaking");
      vi.advanceTimersByTime(1000);
      expect(c.state).toBe("speaking");
      FakeCtx.all[0].currentTime = 4;                               // playback has reached the end
      vi.advanceTimersByTime(2100);
      expect(c.state).toBe("listening");
      expect(ev.onState).toHaveBeenLastCalledWith("listening");
      ws.audio(48000);
      ws.emit({ type: "state", state: "listening" });
      ws.emit({ type: "state", state: "thinking" });                // a new turn cancels the pending change
      vi.advanceTimersByTime(5000);
      expect(c.state).toBe("thinking");
    } finally { vi.useRealTimers(); }
  });

  it("talking over audio that is still playing stops it at once", async () => {
    const { c, ws, ev } = await started();
    ws.onopen!();
    ws.audio(48000);
    const src = FakeCtx.all[0].sources[0];
    ws.emit({ type: "state", state: "listening" });
    ws.emit({ type: "state", state: "hearing" });
    expect(src.stop).toHaveBeenCalled();
    expect(ev.onClear).toHaveBeenCalled();
    expect(c.state).toBe("hearing");
    ws.emit({ type: "state", state: "hearing" });                   // nothing is playing now: no second clear
    expect(ev.onClear).toHaveBeenCalledTimes(1);
  });

  it("without barge-in the microphone is not sent while the assistant is talking or its audio is playing", async () => {
    const { c, ws } = await started({ barge_in: false });
    ws.onopen!();
    const frame = new Int16Array(320).buffer;
    ws.emit({ type: "state", state: "listening" });
    FakeNode.last.port.onmessage!({ data: frame });
    expect(ws.sent.filter((x) => x instanceof ArrayBuffer)).toHaveLength(1);
    ws.emit({ type: "state", state: "thinking" });
    FakeNode.last.port.onmessage!({ data: frame });
    ws.emit({ type: "state", state: "speaking" });
    FakeNode.last.port.onmessage!({ data: frame });
    ws.audio(48000);
    ws.emit({ type: "state", state: "listening" });                 // still playing
    FakeNode.last.port.onmessage!({ data: frame });
    expect(ws.sent.filter((x) => x instanceof ArrayBuffer)).toHaveLength(1);
    FakeCtx.all[0].currentTime = 10;
    ws.emit({ type: "state", state: "listening" });
    FakeNode.last.port.onmessage!({ data: frame });
    expect(ws.sent.filter((x) => x instanceof ArrayBuffer)).toHaveLength(2);
    c.stop();
  });

  it("interrupting stops what is queued; the server's clear does the same", async () => {
    const { c, ws, ev } = await started();
    ws.onopen!();
    ws.audio(); ws.audio();
    const out = FakeCtx.all[0];
    c.interrupt();
    expect(out.sources.every((s) => s.stop.mock.calls.length === 1)).toBe(true);
    expect(ws.json().some((m) => m.type === "interrupt")).toBe(true);
    ws.audio();
    ws.emit({ type: "clear" });
    expect(ev.onClear).toHaveBeenCalled();
    expect(out.sources[2].stop).toHaveBeenCalled();
    ws.audio();
    out.sources[3].stop.mockImplementation(() => { throw new Error("already finished"); });
    ws.emit({ type: "clear" });                                      // a source that already ended cannot be stopped: ignored
  });

  it("sends commit and say, and nothing when the socket is gone", async () => {
    const { c, ws } = await started();
    ws.onopen!();
    c.commit(); c.say("hi there");
    expect(ws.json().slice(1)).toEqual([{ type: "commit" }, { type: "say", text: "hi there" }]);
    ws.readyState = 3;
    c.say("lost");
    expect(ws.json()).toHaveLength(3);
  });

  it("stop closes everything and releases the microphone; a closed socket tears down too", async () => {
    const { c, ws, deps } = await started();
    ws.onopen!();
    c.stop();
    expect(ws.json().some((m) => m.type === "stop")).toBe(true);
    expect(ws.close).toHaveBeenCalled();
    expect(deps.track.stop).toHaveBeenCalled();
    expect(FakeCtx.all.every((x) => x.closed)).toBe(true);
    expect(c.state).toBe("idle");
    expect(c.analyser).toBeNull();
    const two = await started();                                      // the server closes the socket
    two.ws.onopen!();
    two.ws.onclose!();
    expect(two.c.state).toBe("idle");
    const three = await started();                                    // the socket reports an error
    three.ws.onerror!();
    expect(three.ev.onError).toHaveBeenCalledWith(expect.stringContaining("live endpoint"));
  });

  it("works without any event handlers", async () => {
    const deps = fakeDeps();
    const c = new LiveClient({}, deps);
    await c.start();
    const ws = FakeSocket.last;
    ws.onopen!();
    for (const m of [{ type: "state", state: "thinking" }, { type: "ready" }, { type: "transcript", text: "x" }, { type: "assistant", delta: "x" }, { type: "clear" }, { type: "metrics", turn: 1, endpoint_ms: 1 }, { type: "error", message: "e" }, { type: "other" }]) ws.emit(m);
    ws.onerror!();
    c.stop();
    expect(c.state).toBe("idle");
  });
});

describe("playback after an interruption (the barge-in bug)", () => {
  it("audio of the next answer is scheduled and played after a clear, over several turns", async () => {
    const { ws } = await started();
    ws.onopen!();
    const out = FakeCtx.all[0];
    for (let turn = 1; turn <= 3; turn++) {
      out.currentTime = 10 * turn;
      ws.emit({ type: "state", state: "thinking" });
      ws.audio(2400); ws.audio(2400);
      const live = out.sources.filter((x) => x.start.mock.calls.length).slice(-2);
      expect(live[0].start).toHaveBeenCalledWith(10 * turn + 0.02);         // every turn starts at 'now', not at a stale time left over from the cut answer
      ws.emit({ type: "clear" });                                         // interrupted mid-answer
      expect(live.every((x) => x.stop.mock.calls.length === 1)).toBe(true);
    }
    expect(out.sources).toHaveLength(6);
  });

  it("wakes a playback context the browser has suspended instead of staying silent", async () => {
    const { ws } = await started();
    ws.onopen!();
    const out = FakeCtx.all[0] as FakeCtx & { state: string };
    out.state = "suspended";
    out.resume.mockClear();
    ws.audio();
    expect(out.resume).toHaveBeenCalledTimes(1);
    out.state = "running";
    ws.audio();
    expect(out.resume).toHaveBeenCalledTimes(1);
  });
});
