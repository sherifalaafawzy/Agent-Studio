import { apiOrigin } from "./api";
// Live mode in the browser: the microphone goes to the backend as small 16 kHz frames over one WebSocket, the voice comes back as 24 kHz audio
// that is played the moment it arrives (scheduled gaplessly), and every state change and timing is reported to the screen.
export type LiveState = "idle" | "connecting" | "loading" | "listening" | "hearing" | "thinking" | "speaking";

export type Metrics = {
  turn: number; endpoint_ms: number; stt_ms?: number; stt_wall_ms?: number; llm_first_token_ms?: number; first_chunk_ms?: number; tts_first_ms?: number;
  ttfa_ms?: number; user_perceived_ms?: number; total_ms?: number; interrupted?: boolean; reply_chars?: number;
  /** measured here, in the browser: from the moment you stopped talking to the first sound leaving the speakers */
  heard_ms?: number;
  /** Gemini Live only: server-side time to the first avatar video chunk, the cost estimate so far, and the browser's own first-picture time */
  first_video_ms?: number; cloud?: boolean; cost_usd?: number; seen_video_ms?: number;
};

export type UsageRow = { label: string; tokens: number | null; seconds: number | null; usd: number; kind: "reported" | "estimated" | "mixed" | "local" };
/** Sent about once a second while a session is open. `source` says where the cost comes from: reported by Google, estimated from rates, a mix, or none/local. */
export type Usage = {
  elapsed_s: number; cost_usd: number; per_min_usd: number | null; source: "reported" | "estimated" | "mixed" | "local" | "none"; cloud: boolean; tokens: { in: number; out: number };
  seconds: { audio_in: number; audio_out: number; video: number }; rows: UsageRow[]; budget_usd: number | null; budget_pct: number; level: "ok" | "warn" | "stop"; reports: number; prices_as_of: string | null;
};
export type Budget = { level: "warn" | "stop"; message: string };
export type PriceTable = {
  as_of: string; source: string; model: string; region_class: string; in: Record<string, number>; out: Record<string, number>;
  rates: { audio_in_tokens_per_s: number; audio_out_tokens_per_s: number; video_out_tokens_per_s: number }; rates_note: string;
};
export type SessionRow = {
  id: string; started: number; ended: number; agent: string; engine: string; mode: string; provider: string; model: string; voice: string; seconds: number; cost_usd: number;
  cost_source: string; budget_usd: number | null; ended_by: string; detail: { tokens?: { in: number; out: number }; seconds?: Record<string, number>; rows?: UsageRow[]; prices_as_of?: string };
};
export type LiveProfile = {
  enabled: boolean; engine: "local" | "gemini"; mode: "voice" | "avatar"; voice: string; language: string; avatar_name: string; provider: string; model: string; agent_turns: boolean; budget_usd: number;
};

export type LiveSettings = {
  live_provider: string; live_model: string; live_voice: string; live_speed: number; live_hangover_ms: number; live_barge_in: boolean; live_avatar: boolean; live_agent: string;
  live_engine: "local" | "gemini"; live_gemini_provider: string; live_gemini_model: string; live_gemini_voice: string; live_gemini_language: string; live_gemini_avatar_name: string;
  live_gemini_location: string; live_gemini_endpoint: string; live_gemini_max_minutes: number; live_gemini_cost_cap_usd: number; live_gemini_video_bitrate: number; live_gemini_audio_bitrate: number;
};
export type GeminiStatus = {
  configured: boolean; problems: string[]; provider: string; project: string; location: string; model: string; auth_mode: string | null; voices: string[]; languages: string[];
  endpoint: string; avatar_examples: string[]; notes: string[]; prices: { in: Record<string, number>; out: Record<string, number> };
};
export type LiveStep = { id: string; label: string; size_mb: number; status: string };
export type LocalStatus = {
  state: "not_installed" | "installing" | "ready" | "failed"; ready: boolean; error: string | null; steps: LiveStep[]; percent: number; device: string; stt_model: string; auto_device: string;
  progress_text: string; log_tail: string[]; download_mb: number; free_gb: number | null; expect: string; voices: string[]; licences: { name: string; licence: string; url: string }[];
  hardware: { nvidia?: boolean; gpu?: string | null }; versions: Record<string, string> | null;
};
export type LiveStatus = { local: LocalStatus; engine: { state: string; error: string; device?: string; tts_device?: string; stt_model?: string; warm_s?: number }; settings: LiveSettings };
export type SelfTest = { said: string; turns: (Metrics & { heard: string; reply: string; reply_audio_s: number; error?: string })[]; median: Partial<Metrics>; first: Partial<Metrics>; note: string; local_model: boolean };

export type LiveEvents = {
  onState: (s: LiveState) => void; onUser: (text: string) => void; onAssistant: (delta: string, final?: string) => void; onMetrics: (m: Metrics) => void;
  onError: (message: string) => void; onClear: () => void; onReady: (info: Record<string, unknown>) => void;
  /** Gemini Live avatar video: a chunk of video with its MIME type, base64 */
  onVideo: (mime: string, data: string) => void; onNotice: (message: string) => void;
  /** the first sound of a Gemini answer is about to play (so the avatar video can be muted when the audio comes separately) */
  onAudioStart: () => void;
  onUsage: (u: Usage) => void; onBudget: (b: Budget) => void;
};
export type StartOpts = { provider?: string; model?: string; agent?: string; voice?: string; speed?: number; hangover_ms?: number; barge_in?: boolean; engine?: "local" | "gemini"; avatar?: boolean; voice_name?: string };

export type Deps = {
  newSocket: (url: string) => WebSocket;
  newContext: (rate: number) => AudioContext;
  getMic: () => Promise<MediaStream>;
  now: () => number;
  wsUrl: string;
};

export const IN_RATE = 16000;
export const OUT_RATE = 24000;

export function wsUrlFor(apiBase: string): string {
  return apiOrigin(apiBase).replace(/^http/, "ws").replace(/\/$/, "") + "/api/live/ws";
}

export function realDeps(apiBase: string): Deps {
  return {
    newSocket: (u) => new WebSocket(u),
    newContext: (rate) => new AudioContext({ sampleRate: rate, latencyHint: "interactive" }),
    getMic: () => navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } }),
    now: () => performance.now(),
    wsUrl: wsUrlFor(apiBase),
  };
}

// The microphone worklet: cuts the stream into 20 ms Int16 frames. Sent as a string so it can be loaded from a Blob URL.
export const WORKLET = `class Cap extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Int16Array(320); this.n = 0; this.muted = false; this.port.onmessage = (e) => { if ('muted' in e.data) this.muted = e.data.muted; }; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const v = this.muted ? 0 : Math.max(-1, Math.min(1, ch[i]));
      this.buf[this.n++] = v < 0 ? v * 32768 : v * 32767;
      if (this.n === 320) { this.port.postMessage(this.buf.buffer.slice(0)); this.n = 0; }
    }
    return true;
  }
}
registerProcessor('cap', Cap);`;

export function int16ToFloat32(buf: ArrayBuffer): Float32Array {
  const i16 = new Int16Array(buf.byteLength >> 1 ? buf.slice(0, (buf.byteLength >> 1) << 1) : new ArrayBuffer(0));
  const out = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) out[i] = i16[i] / 32768;
  return out;
}

export class LiveClient {
  state: LiveState = "idle";
  analyser: AnalyserNode | null = null;
  private ws: WebSocket | null = null;
  private inCtx: AudioContext | null = null;
  private outCtx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private next = 0;
  private sources: AudioBufferSourceNode[] = [];
  private muted = false;
  private hangover = 350;
  private bargeIn = true;
  private playTimer: ReturnType<typeof setTimeout> | null = null;
  private thinkAt = 0;
  private firstSoundPending = false;
  private lastHeard: number | undefined;
  private audioSeen = false;

  constructor(private ev: Partial<LiveEvents>, private deps: Deps) {}

  private set(s: LiveState) { this.state = s; this.ev.onState?.(s); }

  async start(opts: StartOpts = {}): Promise<void> {
    if (this.ws) return;
    this.hangover = opts.hangover_ms ?? 350;
    this.bargeIn = opts.barge_in ?? true;
    this.set("connecting");
    try {
      this.stream = await this.deps.getMic();
    } catch (e) {
      this.set("idle");
      this.ev.onError?.("The microphone is not available: " + ((e as Error).message || "permission was refused") + ". Allow it in the browser's address bar and try again.");
      return;
    }
    this.outCtx = this.deps.newContext(OUT_RATE);
    this.analyser = this.outCtx.createAnalyser();
    this.analyser.fftSize = 512;
    this.analyser.connect(this.outCtx.destination);
    this.inCtx = this.deps.newContext(IN_RATE);
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    try { await this.inCtx.audioWorklet.addModule(url); } finally { URL.revokeObjectURL(url); }
    this.node = new AudioWorkletNode(this.inCtx, "cap");
    this.inCtx.createMediaStreamSource(this.stream).connect(this.node);
    await this.outCtx.resume(); await this.inCtx.resume();
    const ws = this.deps.newSocket(this.deps.wsUrl);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => { ws.send(JSON.stringify({ type: "start", ...opts })); this.set("loading"); };
    ws.onmessage = (m) => (typeof m.data === "string" ? this.onJson(JSON.parse(m.data)) : this.play(m.data as ArrayBuffer));
    ws.onerror = () => this.ev.onError?.("Could not reach Agent Studio's live endpoint. Is the backend running?");
    ws.onclose = () => { if (this.ws === ws) this.teardown(); };
    this.node.port.onmessage = (e) => {
      if (ws.readyState !== 1 || this.state === "loading") return;
      if (!this.bargeIn && (this.state === "speaking" || this.state === "thinking" || this.playing())) return;       // not interruptible: do not listen to our own voice
      ws.send(e.data as ArrayBuffer);
    };
  }

  private onJson(m: any) {
    switch (m.type) {
      case "state":
        this.clearPlayTimer();
        if (m.state === "thinking") { this.thinkAt = this.deps.now(); this.firstSoundPending = true; this.audioSeen = false; }
        if (m.state === "hearing" && this.playing()) { this.flush(); this.ev.onClear?.(); }      // you started talking over audio that is still playing: stop it now
        if (m.state === "listening" && this.playing()) {
          // the server sends audio faster than it plays: the answer is "finished" there while it is still being spoken here
          this.set("speaking");
          this.playTimer = setTimeout(() => { this.playTimer = null; if (this.state === "speaking") this.set("listening"); }, Math.max(0, (this.next - (this.outCtx?.currentTime ?? 0)) * 1000) + 50);
          break;
        }
        this.set(m.state as LiveState);
        break;
      case "ready": this.ev.onReady?.(m); break;
      case "video": this.ev.onVideo?.(m.mime, m.data); break;
      case "notice": this.ev.onNotice?.(m.message); break;
      case "usage": { const { type: _u, ...u } = m; this.ev.onUsage?.(u as Usage); break; }
      case "budget": this.ev.onBudget?.({ level: m.level, message: m.message }); break;
      case "transcript": this.ev.onUser?.(m.text); break;
      case "assistant": this.ev.onAssistant?.(m.delta ?? "", m.final ? m.text : undefined); break;
      case "clear": this.flush(); this.ev.onClear?.(); break;
      case "metrics": { const { type: _t, ...rest } = m; this.ev.onMetrics?.({ ...(rest as Metrics), heard_ms: this.lastHeard }); this.lastHeard = undefined; break; }
      case "error": this.ev.onError?.(m.message); break;
    }
  }

  private play(buf: ArrayBuffer) {
    const ctx = this.outCtx;
    if (!ctx || !this.analyser) return;
    if (ctx.state === "suspended") void ctx.resume();                      // a browser may pause the output after a while: wake it, or the voice goes silent while the text keeps coming
    const f = int16ToFloat32(buf);
    if (!f.length) return;
    if (!this.audioSeen) { this.audioSeen = true; this.ev.onAudioStart?.(); }
    const ab = ctx.createBuffer(1, f.length, OUT_RATE);
    ab.copyToChannel(f as Float32Array<ArrayBuffer>, 0);
    const src = ctx.createBufferSource();
    src.buffer = ab;
    src.connect(this.analyser);
    const when = Math.max(ctx.currentTime + 0.02, this.next);
    src.start(when);
    this.next = when + ab.duration;
    this.sources.push(src);
    src.onended = () => { this.sources = this.sources.filter((s) => s !== src); };
    if (this.firstSoundPending) {            // the first sound of this answer: time it from the moment you stopped talking
      this.firstSoundPending = false;
      const outLatency = ((ctx as AudioContext & { outputLatency?: number }).outputLatency ?? 0) + (ctx.baseLatency ?? 0);
      this.lastHeard = Math.round(this.deps.now() + (when - ctx.currentTime + outLatency) * 1000 - this.thinkAt + this.hangover);
    }
  }

  private playing(): boolean { return !!this.outCtx && this.next > this.outCtx.currentTime + 0.03; }
  private clearPlayTimer() { if (this.playTimer) { clearTimeout(this.playTimer); this.playTimer = null; } }

  private flush() {
    this.clearPlayTimer();
    for (const s of this.sources) { try { s.stop(); } catch { /* already finished */ } }
    this.sources = [];
    this.next = 0;
  }

  private send(o: object) { if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(o)); }
  interrupt() { this.flush(); this.send({ type: "interrupt" }); }
  commit() { this.send({ type: "commit" }); }
  say(text: string) { this.send({ type: "say", text }); }
  setMuted(m: boolean) { this.muted = m; this.node?.port.postMessage({ muted: m }); }
  get isMuted() { return this.muted; }

  stop() {
    this.send({ type: "stop" });
    this.teardown();
  }

  private teardown() {
    this.clearPlayTimer();
    const ws = this.ws;
    this.ws = null;
    try { ws?.close(); } catch { /* closing twice is fine */ }
    this.flush();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.node?.disconnect();
    void this.inCtx?.close();
    void this.outCtx?.close();
    this.stream = this.node = this.inCtx = this.outCtx = this.analyser = null;
    this.set("idle");
  }
}

/** Mouth shape from the voice's loudness (0..1) and how much of it is high-pitched (0..1): `open` = how far the mouth opens, `wide` = how wide it stretches. */
export function mouthShape(level: number, bright: number): { open: number; wide: number } {
  const open = Math.min(1, Math.max(0, (level - 0.02) * 5));
  return { open, wide: Math.min(1, Math.max(0, 0.35 + (bright - 0.25) * 1.4)) };
}

/** Level and brightness of whatever the analyser hears right now. */
export function readVoice(a: AnalyserNode): { level: number; bright: number } {
  const t = new Uint8Array(a.fftSize);
  a.getByteTimeDomainData(t);
  let sum = 0;
  for (const v of t) { const x = (v - 128) / 128; sum += x * x; }
  const f = new Uint8Array(a.frequencyBinCount);
  a.getByteFrequencyData(f);
  const cut = Math.floor(f.length * 0.3);
  let lo = 0, hi = 0;
  for (let i = 0; i < f.length; i++) (i < cut ? (lo += f[i]) : (hi += f[i]));
  return { level: Math.sqrt(sum / t.length), bright: hi + lo > 0 ? hi / (hi + lo) : 0 };
}
