import { describe, expect, it, vi } from "vitest";
import { DONE_KEY, STATE_KEY, firstRunDone, markFirstRunDone, readState, writeState } from "@/lib/tour";

describe("default storage lookup", () => {
  it("treats a localStorage getter that throws as no storage at all", () => {
    vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(firstRunDone()).toBe(true);       // no storage: never auto-start the tour
    expect(() => markFirstRunDone()).not.toThrow();
    expect(() => writeState({ tour: "chat", index: 0 })).not.toThrow();
    expect(readState()).toBeNull();
  });

  it("treats a missing window (server render) as no storage at all", () => {
    vi.stubGlobal("window", undefined);
    expect(firstRunDone()).toBe(true);
    expect(() => markFirstRunDone()).not.toThrow();
    expect(() => writeState({ tour: "chat", index: 0 })).not.toThrow();
    expect(readState()).toBeNull();
    vi.unstubAllGlobals();
    expect(localStorage.getItem(DONE_KEY)).toBeNull();
    expect(localStorage.getItem(STATE_KEY)).toBeNull();
  });

  it("survives getItem and setItem throwing on the real storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
    expect(firstRunDone()).toBe(true);
    expect(() => markFirstRunDone()).not.toThrow();
    expect(() => writeState({ tour: "chat", index: 1 })).not.toThrow();
    expect(readState()).toBeNull();
  });
});
