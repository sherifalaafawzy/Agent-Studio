import { vi } from "vitest";
import type { Deps, LiveSettings } from "@/lib/live";

export class FakeSocket {
  static last: FakeSocket;
  readyState = 1; binaryType = ""; sent: (string | ArrayBuffer)[] = [];
  onopen: (() => void) | null = null; onmessage: ((m: { data: unknown }) => void) | null = null; onerror: (() => void) | null = null; onclose: (() => void) | null = null;
  close = vi.fn(() => { this.readyState = 3; });
  constructor(public url: string) { FakeSocket.last = this; }
  send(d: string | ArrayBuffer) { this.sent.push(d); }
  json(): any[] { return this.sent.filter((s): s is string => typeof s === "string").map((s) => JSON.parse(s)); }
  emit(o: object) { this.onmessage?.({ data: JSON.stringify(o) }); }
  audio(samples = 480) { const b = new Int16Array(samples).fill(1000); this.onmessage?.({ data: b.buffer }); }
}

export class FakeSource { buffer: unknown; start = vi.fn(); stop = vi.fn(); connect = vi.fn(); onended: (() => void) | null = null; }
export class FakeCtx {
  static all: FakeCtx[] = [];
  currentTime = 1; baseLatency = 0.01; outputLatency = 0.02; sources: FakeSource[] = []; closed = false;
  destination = {};
  audioWorklet = { addModule: vi.fn(async () => undefined) };
  analyser = { fftSize: 8, connect: vi.fn(), frequencyBinCount: 8, getByteTimeDomainData: vi.fn((a: Uint8Array) => a.fill(192)), getByteFrequencyData: vi.fn((a: Uint8Array) => a.fill(100)) };
  constructor(public rate: number) { FakeCtx.all.push(this); }
  createAnalyser() { return this.analyser as unknown as AnalyserNode; }
  createMediaStreamSource() { return { connect: vi.fn() }; }
  createBuffer(_c: number, n: number, rate: number) { return { duration: n / rate, copyToChannel: vi.fn() }; }
  createBufferSource() { const s = new FakeSource(); this.sources.push(s); return s; }
  resume = vi.fn(async () => undefined);
  close = vi.fn(async () => { this.closed = true; });
}
export class FakeNode { static last: FakeNode; port = { onmessage: null as ((e: { data: ArrayBuffer }) => void) | null, postMessage: vi.fn() }; disconnect = vi.fn(); constructor() { FakeNode.last = this; } }

export function fakeDeps(over: Partial<Deps> = {}): Deps & { track: { stop: ReturnType<typeof vi.fn> }; clock: { t: number } } {
  const clock = { t: 1000 };
  const track = { stop: vi.fn() };
  vi.stubGlobal("AudioWorkletNode", FakeNode);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:w"), revokeObjectURL: vi.fn() }));
  FakeCtx.all = [];
  return { newSocket: (u) => new FakeSocket(u) as unknown as WebSocket, newContext: (r) => new FakeCtx(r) as unknown as AudioContext,
    getMic: async () => ({ getTracks: () => [track] }) as unknown as MediaStream, now: () => clock.t, wsUrl: "ws://x/api/live/ws", track, clock, ...over };
}

export const liveSettings: LiveSettings = { live_provider: "ollama", live_model: "qwen3:8b", live_voice: "af_heart", live_speed: 1, live_hangover_ms: 350, live_barge_in: true, live_avatar: false, live_agent: "", live_engine: "local",
  live_gemini_provider: "vertex_ai", live_gemini_model: "gemini-3.8-live", live_gemini_voice: "Puck", live_gemini_language: "en-US", live_gemini_avatar_name: "", live_gemini_location: "", live_gemini_endpoint: "",
  live_gemini_max_minutes: 10, live_gemini_cost_cap_usd: 1, live_gemini_video_bitrate: 0, live_gemini_audio_bitrate: 0 };

export const livePrices = { as_of: "2026-11-23", source: "https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing", model: "gemini-3.8-live", region_class: "non-global",
  in: { TEXT: 0.75, AUDIO: 3, VIDEO: 1, IMAGE: 1 }, out: { TEXT: 4.5, AUDIO: 12, VIDEO: 1 }, rates: { audio_in_tokens_per_s: 25, audio_out_tokens_per_s: 27, video_out_tokens_per_s: 2500 }, rates_note: "video rate measured" };
