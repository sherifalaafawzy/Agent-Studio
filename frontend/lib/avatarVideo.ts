// Plays the avatar video that Gemini Live sends: base64 chunks of video (documented only as "24 FPS MP4") arriving while it is being made.
// How Google frames those chunks is NOT documented, so this tries the live way first and falls back:
//   1. Media Source Extensions: append each chunk to a SourceBuffer as it arrives (works when the chunks are fragmented MP4).
//   2. If the browser cannot stream that type (or appending fails), collect the chunks and play the whole piece when the answer ends.
// It also times the first picture, for the latency readout.
export type PlayerDeps = { MediaSource?: typeof MediaSource; createUrl: (b: Blob | MediaSource) => string; revokeUrl: (u: string) => void; now: () => number };

export function realPlayerDeps(): PlayerDeps {
  return {
    MediaSource: typeof MediaSource !== "undefined" ? MediaSource : undefined,
    createUrl: (b) => URL.createObjectURL(b as Blob),
    revokeUrl: (u) => URL.revokeObjectURL(u),
    now: () => performance.now(),
  };
}

export const b64ToBytes = (b64: string): Uint8Array<ArrayBuffer> => {
  const bin = atob(b64);
  const out = new Uint8Array(new ArrayBuffer(bin.length));
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
};

/** An MP4 init segment starts with an `ftyp` box (bytes 4-8). */
export const isInitSegment = (b: Uint8Array): boolean => b.length > 8 && b[4] === 0x66 && b[5] === 0x74 && b[6] === 0x79 && b[7] === 0x70;

export class AvatarVideoPlayer {
  mode: "idle" | "stream" | "collect" = "idle";
  chunks = 0;
  private ms: MediaSource | null = null;
  private sb: SourceBuffer | null = null;
  private queue: Uint8Array<ArrayBuffer>[] = [];
  private parts: Uint8Array<ArrayBuffer>[] = [];
  private url: string | null = null;
  private mime = "";
  private firstAt: number | null = null;
  private waitingSince = 0;
  private init: Uint8Array<ArrayBuffer> | null = null;          // the MP4 init segment (ftyp/moov): Google sends it ONCE, at the start of the session
  private restarts = 0;

  constructor(private video: HTMLVideoElement, private deps: PlayerDeps, private onFirstFrame?: (ms: number) => void) {
    video.addEventListener("error", () => { if (this.mode === "stream") this.restart(); });      // a broken decoder: rebuild the stream from the init segment
  }

  /** Call when you stop talking, so the first picture is timed from there. */
  markWaiting() { this.waitingSince = this.deps.now(); this.firstAt = null; }

  push(mime: string, b64: string) {
    const bytes = b64ToBytes(b64);
    this.chunks++;
    if (!this.init && isInitSegment(bytes)) this.init = bytes;
    if (this.mode === "idle") this.open(mime);
    if (this.mode === "stream") { this.queue.push(bytes); this.pump(); } else this.parts.push(bytes);
  }

  private open(mime: string) {
    this.mime = mime;
    const MS = this.deps.MediaSource;
    const candidates = [mime, `${mime.split(";")[0]}; codecs="avc1.42E01E,mp4a.40.2"`, `${mime.split(";")[0]}; codecs="avc1.42E01E"`];
    const type = MS ? candidates.find((c) => MS.isTypeSupported(c)) : undefined;
    if (!MS || !type) { this.mode = "collect"; return; }
    this.mode = "stream";
    this.ms = new MS();
    this.url = this.deps.createUrl(this.ms);
    this.video.src = this.url;
    this.ms.addEventListener("sourceopen", () => {
      try {
        this.sb = this.ms!.addSourceBuffer(type);
        this.sb.mode = "sequence";
        this.sb.addEventListener("updateend", () => this.pump());
        this.sb.addEventListener("error", () => this.fallback());
        this.pump();
      } catch { this.fallback(); }
    }, { once: true });
    this.watchFirstFrame();
    void this.video.play().catch(() => undefined);
  }

  private pump() {
    const sb = this.sb;
    if (!sb || sb.updating || !this.queue.length) return;
    // Google streams avatar video for as long as the session is open: keep only the last few seconds behind the playhead, or the buffer grows without end
    if (sb.buffered.length && this.video.currentTime - sb.buffered.start(0) > 20) {
      try { sb.remove(sb.buffered.start(0), this.video.currentTime - 5); return; } catch { /* the buffer is busy: try again after the next append */ }
    }
    const next = this.queue.shift()!;
    try { sb.appendBuffer(next); } catch { this.queue.unshift(next); this.fallback(); }
  }

  /** The answer was cut off (you interrupted). Google's avatar video is ONE continuous stream for the whole session, so it must NOT be torn down: a new stream would
   *  start without the init segment and show nothing (what happened before). Only what a collect-mode fallback had gathered for the cut answer is dropped. */
  interrupted() { this.parts = []; }

  /** The decoder failed (or the buffer broke): start a new MediaSource and feed it the stored init segment before the rest, a few times at most. */
  private restart() {
    if (!this.init || this.restarts >= 3) { this.fallback(); return; }
    this.restarts++;
    const pending = this.queue.filter((q) => q !== this.init);
    try { if (this.ms && this.ms.readyState === "open") this.ms.endOfStream(); } catch { /* already closed */ }
    this.release();
    this.ms = null; this.sb = null; this.mode = "idle";
    this.queue = [this.init, ...pending];
    this.open(this.mime);
  }

  /** Streaming failed: keep what is queued and play the whole answer when it ends. */
  private fallback() {
    this.parts.push(...this.queue);
    this.queue = [];
    this.mode = "collect";
    this.sb = null;
  }

  private watchFirstFrame() {
    const v = this.video as HTMLVideoElement & { requestVideoFrameCallback?: (cb: () => void) => number };
    const done = () => { if (this.firstAt == null) { this.firstAt = this.deps.now(); this.onFirstFrame?.(Math.round(this.firstAt - this.waitingSince)); } };
    if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(done);     // exact, but a hidden tab does not run it ...
    v.addEventListener("playing", done, { once: true });                      // ... so the playing event is the backup
  }

  /** The answer is over: in collect mode this is when the video starts playing. */
  finish() {
    if (this.mode !== "collect" || !this.parts.length) return;
    const blob = new Blob(this.parts, { type: this.mime.split(";")[0] || "video/mp4" });
    this.parts = [];
    this.release();
    this.url = this.deps.createUrl(blob);
    this.video.src = this.url;
    this.watchFirstFrame();
    void this.video.play().catch(() => undefined);
  }

  /** Throw everything away: the session ended. (An interruption is NOT a reason: see `interrupted`.) */
  reset() {
    this.queue = []; this.parts = []; this.sb = null; this.chunks = 0; this.init = null; this.restarts = 0;
    try { if (this.ms && this.ms.readyState === "open") this.ms.endOfStream(); } catch { /* already closed */ }
    this.ms = null;
    this.mode = "idle";
    this.release();
    this.video.removeAttribute("src");
    try { this.video.load(); } catch { /* not a real element in tests */ }
  }

  private release() { if (this.url) { this.deps.revokeUrl(this.url); this.url = null; } }
}
