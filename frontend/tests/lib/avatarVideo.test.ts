import { describe, expect, it, vi } from "vitest";
import { AvatarVideoPlayer, b64ToBytes, isInitSegment, realPlayerDeps, type PlayerDeps } from "@/lib/avatarVideo";

class FakeBuffer {
  updating = false; mode = ""; appended: Uint8Array[] = []; handlers: Record<string, () => void> = {};
  buffered: { length: number; start: (i: number) => number } = { length: 0, start: () => 0 };
  remove = vi.fn();
  appendBuffer = vi.fn((b: Uint8Array) => { if (this.fail) throw new Error("QuotaExceeded"); this.appended.push(b); this.updating = true; });
  fail = false;
  addEventListener(n: string, f: () => void) { this.handlers[n] = f; }
  finish() { this.updating = false; this.handlers.updateend?.(); }
}
class FakeMS {
  static supported = new Set<string>(); static last: FakeMS; static count = 0;
  static isTypeSupported(t: string) { return FakeMS.supported.has(t); }
  readyState = "open"; sb = new FakeBuffer(); handlers: Record<string, () => void> = {}; ended = false; failAdd = false; added = "";
  constructor() { FakeMS.last = this; FakeMS.count++; }
  addEventListener(n: string, f: () => void) { this.handlers[n] = f; }
  addSourceBuffer(t: string) { if (this.failAdd) throw new Error("NotSupported"); this.added = t; return this.sb; }
  endOfStream() { this.ended = true; }
  open() { this.handlers.sourceopen?.(); }
}

function setup(over: Partial<PlayerDeps> = {}) {
  const video = document.createElement("video");
  video.play = vi.fn().mockResolvedValue(undefined);
  video.load = vi.fn();
  let t = 1000;
  const urls: string[] = []; const revoked: string[] = [];
  const deps: PlayerDeps = { MediaSource: FakeMS as unknown as typeof MediaSource, createUrl: () => { const u = `blob:${urls.length}`; urls.push(u); return u; }, revokeUrl: (u) => revoked.push(u), now: () => t, ...over };
  const first = vi.fn();
  const player = new AvatarVideoPlayer(video, deps, first);
  return { video, deps, player, first, urls, revoked, tick: (n: number) => { t += n; } };
}
const b64 = (s: string) => btoa(s);

describe("AvatarVideoPlayer", () => {
  it("decodes base64 into bytes", () => {
    expect(Array.from(b64ToBytes(b64("AB")))).toEqual([65, 66]);
  });

  it("streams chunks into a SourceBuffer one at a time, as they arrive", () => {
    FakeMS.supported = new Set(["video/mp4"]);
    const { player, video, urls } = setup();
    player.push("video/mp4", b64("one"));
    expect(player.mode).toBe("stream") ;
    expect(video.src).toContain("blob:0") ;
    expect(urls).toHaveLength(1);
    FakeMS.last.open();
    expect(FakeMS.last.added).toBe("video/mp4");
    expect(FakeMS.last.sb.mode).toBe("sequence");
    expect(FakeMS.last.sb.appended).toHaveLength(1);                   // the first chunk is appended on open
    player.push("video/mp4", b64("two"));
    expect(FakeMS.last.sb.appended).toHaveLength(1);                   // the buffer is busy: queued
    FakeMS.last.sb.finish();
    expect(FakeMS.last.sb.appended).toHaveLength(2);
    expect(video.play).toHaveBeenCalled();
    expect(player.chunks).toBe(2);
  });

  it("tries the common codecs string when the bare type is not supported", () => {
    FakeMS.supported = new Set(['video/mp4; codecs="avc1.42E01E,mp4a.40.2"']);
    const { player } = setup();
    player.push("video/mp4", b64("x"));
    FakeMS.last.open();
    expect(FakeMS.last.added).toBe('video/mp4; codecs="avc1.42E01E,mp4a.40.2"');
  });

  it("times the first picture from the moment you stopped talking (frame callback, else the playing event)", () => {
    FakeMS.supported = new Set(["video/mp4"]);
    const a = setup();
    let frameCb: () => void = () => undefined;
    (a.video as unknown as { requestVideoFrameCallback: (cb: () => void) => number }).requestVideoFrameCallback = (cb) => { frameCb = cb; return 1; };
    a.player.markWaiting();
    a.tick(640);
    a.player.push("video/mp4", b64("x"));
    frameCb();
    frameCb();
    expect(a.first).toHaveBeenCalledTimes(1);
    expect(a.first).toHaveBeenCalledWith(640);
    a.video.dispatchEvent(new Event("playing"));                       // the backup event after the frame callback already fired: no second report
    expect(a.first).toHaveBeenCalledTimes(1);
    const b = setup();
    b.player.markWaiting(); b.player.push("video/mp4", b64("x")); b.tick(300);
    b.video.dispatchEvent(new Event("playing"));
    expect(b.first).toHaveBeenCalledWith(300);
  });

  it("falls back to collecting and playing the whole answer when streaming is impossible", () => {
    FakeMS.supported = new Set();                                      // nothing streams
    const a = setup();
    a.player.push("video/mp4;codecs=x", b64("aa"));
    a.player.push("video/mp4", b64("bb"));
    expect(a.player.mode).toBe("collect");
    expect(a.video.play).not.toHaveBeenCalled();
    a.player.finish();
    expect(a.video.src).toBe("blob:0");
    expect(a.video.play).toHaveBeenCalled();
    a.player.finish();                                                  // nothing left to play
    expect(a.urls).toHaveLength(1);
    const b = setup({ MediaSource: undefined });                         // no Media Source Extensions at all
    b.player.push("video/webm", b64("x"));
    expect(b.player.mode).toBe("collect");
    b.player.reset();
    expect(b.player.mode).toBe("idle");
    const c = setup({ MediaSource: undefined });
    c.player.finish();                                                  // finishing before anything arrived is harmless
    expect(c.urls).toHaveLength(0);
  });

  it("falls back, keeping queued data, when the buffer cannot be created or an append fails", () => {
    FakeMS.supported = new Set(["video/mp4"]);
    const a = setup();
    a.player.push("video/mp4", b64("x"));
    FakeMS.last.failAdd = true;
    FakeMS.last.open();
    expect(a.player.mode).toBe("collect");
    a.player.finish();
    expect(a.video.play).toHaveBeenCalled();
    const b = setup();
    b.player.push("video/mp4", b64("x"));
    FakeMS.last.open();
    FakeMS.last.sb.finish();
    FakeMS.last.sb.fail = true;
    b.player.push("video/mp4", b64("y"));
    FakeMS.last.sb.handlers.updateend();
    expect(b.player.mode).toBe("collect");
    const c = setup();
    c.player.push("video/mp4", b64("x"));
    FakeMS.last.open();
    FakeMS.last.sb.handlers.error();
    expect(c.player.mode).toBe("collect");
  });

  it("reset ends the stream, releases the address and empties the video", () => {
    FakeMS.supported = new Set(["video/mp4"]);
    const { player, video, revoked } = setup();
    player.push("video/mp4", b64("x"));
    FakeMS.last.open();
    const ms = FakeMS.last;
    player.reset();
    expect(ms.ended).toBe(true);
    expect(revoked).toEqual(["blob:0"]);
    expect(video.getAttribute("src")).toBeNull();
    expect(video.load).toHaveBeenCalled();
    expect(player.mode).toBe("idle");
    player.push("video/mp4", b64("again"));                              // a new answer starts a new stream
    expect(player.mode).toBe("stream");
    ms.readyState = "closed";
    player.reset();
    const d = setup();
    d.video.load = () => { throw new Error("not implemented"); };
    d.player.reset();
  });

  it("the real dependencies use the browser's own APIs", () => {
    vi.stubGlobal("MediaSource", FakeMS);
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:real"), revokeObjectURL: vi.fn() }));
    const d = realPlayerDeps();
    expect(d.MediaSource).toBe(FakeMS);
    expect(d.createUrl(new Blob(["x"]))).toBe("blob:real");
    d.revokeUrl("blob:real");
    expect(typeof d.now()).toBe("number");
    vi.unstubAllGlobals();
    vi.stubGlobal("MediaSource", undefined);
    expect(realPlayerDeps().MediaSource).toBeUndefined();
    vi.unstubAllGlobals();
  });
});

describe("AvatarVideoPlayer buffer trimming", () => {
  it("drops old video behind the playhead so a session that runs for minutes does not fill memory", () => {
    FakeMS.supported = new Set(["video/mp4"]);
    const { player, video } = setup();
    player.push("video/mp4", b64("one"));
    FakeMS.last.open();
    FakeMS.last.sb.finish();
    const sb = FakeMS.last.sb;
    sb.buffered = { length: 1, start: () => 0 };
    sb.remove = vi.fn(() => { sb.updating = true; });
    Object.defineProperty(video, "currentTime", { value: 30, configurable: true });
    player.push("video/mp4", b64("two"));
    expect(sb.remove).toHaveBeenCalledWith(0, 25);                       // everything older than 5 s behind the playhead goes
    expect(sb.appended).toHaveLength(1);                                 // the append waits for the removal to finish
    sb.finish();
    sb.buffered = { length: 1, start: () => 25 };
    sb.finish();
    expect(sb.appended).toHaveLength(2);
    sb.remove = vi.fn(() => { throw new Error("busy"); });
    sb.buffered = { length: 1, start: () => 0 };
    sb.finish();
    player.push("video/mp4", b64("three"));
    expect(sb.appended.length).toBeGreaterThanOrEqual(2);               // a failed removal does not stop the stream
  });
});


// ---------------------------------------------------------------- the barge-in bug: an interruption must not tear down Google's continuous avatar stream
const INIT = "\x00\x00\x00\x1cftypdash\x00\x00\x00\x00iso6avc1";                // what the real service sends first (its MP4 init segment), then movie fragments

describe("AvatarVideoPlayer and interruptions", () => {
  it("recognises an MP4 init segment", () => {
    expect(isInitSegment(b64ToBytes(btoa(INIT)))).toBe(true);
    expect(isInitSegment(b64ToBytes(btoa("\x00\x00\x00\x18moofxxxxxxxx")))).toBe(false);
    expect(isInitSegment(new Uint8Array(3))).toBe(false);
  });

  it("keeps the same stream after an interruption: no new MediaSource, no element reset, the next fragment is appended to the same buffer", () => {
    FakeMS.supported = new Set(["video/mp4"]); FakeMS.count = 0;
    const { player, video } = setup();
    player.push("video/mp4", b64(INIT));
    FakeMS.last.open();
    FakeMS.last.sb.finish();
    player.push("video/mp4", b64("moof-1"));
    FakeMS.last.sb.finish();
    const ms = FakeMS.last;
    player.interrupted();                                              // the person talked over the answer
    player.push("video/mp4", b64("moof-2"));
    ms.sb.finish();
    player.push("video/mp4", b64("moof-3"));
    ms.sb.finish();
    expect(FakeMS.count).toBe(1);
    expect(ms.ended).toBe(false);
    expect(video.load).not.toHaveBeenCalled();
    expect(player.mode).toBe("stream");
    expect(ms.sb.appended).toHaveLength(4);                            // init, then every fragment, before and after the interruption
  });

  it("an interruption only drops what a collect-mode fallback gathered for the cut answer", () => {
    FakeMS.supported = new Set();
    const { player, video } = setup();
    player.push("video/mp4", b64("a"));
    player.interrupted();
    player.finish();                                                    // nothing left to play
    expect(video.play).not.toHaveBeenCalled();
    player.push("video/mp4", b64("b"));
    player.finish();
    expect(video.play).toHaveBeenCalled();
  });

  it("when the decoder fails it rebuilds the stream from the stored init segment, with the waiting fragments after it", () => {
    FakeMS.supported = new Set(["video/mp4"]); FakeMS.count = 0;
    const { player, video } = setup();
    player.push("video/mp4", b64(INIT));
    FakeMS.last.open();                                                // the init segment is being appended (the buffer is busy) ...
    player.push("video/mp4", b64("moof-1"));                           // ... so this fragment waits in the queue
    const first = FakeMS.last;
    video.dispatchEvent(new Event("error"));                           // MEDIA_ERR_SRC_NOT_SUPPORTED, as seen on the real service
    expect(FakeMS.count).toBe(2);
    expect(first.ended).toBe(true);
    const second = FakeMS.last;
    second.open();
    expect(second.sb.appended[0]).toEqual(b64ToBytes(btoa(INIT)));      // the init segment goes in first
    second.sb.finish();
    expect(second.sb.appended[1]).toEqual(b64ToBytes(btoa("moof-1")));
    expect(player.mode).toBe("stream");
  });

  it("gives up after three rebuilds, or when it never saw an init segment, and falls back to collecting", () => {
    FakeMS.supported = new Set(["video/mp4"]);
    const a = setup();
    a.player.push("video/mp4", b64(INIT));
    for (let i = 0; i < 3; i++) { FakeMS.last.open(); a.video.dispatchEvent(new Event("error")); expect(a.player.mode).toBe("stream"); }
    a.video.dispatchEvent(new Event("error"));
    expect(a.player.mode).toBe("collect");
    const b = setup();
    b.player.push("video/mp4", b64("moof-without-init"));
    b.video.dispatchEvent(new Event("error"));
    expect(b.player.mode).toBe("collect");
    const c = setup();
    c.video.dispatchEvent(new Event("error"));                         // an error before anything was streamed is ignored
    expect(c.player.mode).toBe("idle");
  });

  it("a finished session forgets the init segment, so the next session starts clean", () => {
    FakeMS.supported = new Set(["video/mp4"]);
    const { player, video } = setup();
    player.push("video/mp4", b64(INIT));
    player.reset();
    player.push("video/mp4", b64("moof-new-session"));
    video.dispatchEvent(new Event("error"));
    expect(player.mode).toBe("collect");                               // no init stored from the old session to rebuild with
  });
});
