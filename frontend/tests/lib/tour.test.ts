import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DONE_KEY,
  STATE_KEY,
  TOUR_NAMES,
  TOURS,
  firstRunDone,
  markFirstRunDone,
  normPath,
  readState,
  samePage,
  slice,
  writeState,
} from "@/lib/tour";

function memStore(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: vi.fn((k: string) => (data.has(k) ? data.get(k)! : null)),
    setItem: vi.fn((k: string, v: string) => void data.set(k, v)),
    removeItem: vi.fn((k: string) => void data.delete(k)),
  };
}

function throwingStore() {
  const boom = () => {
    throw new Error("storage blocked");
  };
  return { getItem: boom, setItem: boom, removeItem: boom };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("tour catalogue", () => {
  it("exposes every tour by name and each step names a page", () => {
    expect(TOUR_NAMES).toEqual(Object.keys(TOURS));
    expect(TOUR_NAMES).toEqual(expect.arrayContaining(["welcome", "chat", "agents", "mcp"]));
    for (const t of Object.values(TOURS)) {
      expect(t.steps.length).toBeGreaterThan(0);
      for (const s of t.steps) expect(s.page.startsWith("/")).toBe(true);
    }
  });
});

describe("normPath", () => {
  it("strips trailing slashes, query and hash", () => {
    expect(normPath("/agents/")).toBe("/agents");
    expect(normPath("/agents//")).toBe("/agents");
    expect(normPath("/agents/?tab=2#top")).toBe("/agents");
    expect(normPath("/mcp?x=1")).toBe("/mcp");
  });

  it("keeps the root and treats empty input as root", () => {
    expect(normPath("/")).toBe("/");
    expect(normPath("//")).toBe("/");
    expect(normPath("")).toBe("/");
    expect(normPath("?only=query")).toBe("/");
  });
});

describe("samePage", () => {
  it("treats trailing-slash variants as the same page and different pages as different", () => {
    expect(samePage("/agents", "/agents/")).toBe(true);
    expect(samePage("/", "")).toBe(true);
    expect(samePage("/mcp/", "/settings/")).toBe(false);
  });
});

describe("readState", () => {
  it("returns null when storage is missing or empty", () => {
    expect(readState(null)).toBeNull();
    expect(readState(undefined)).toBeNull();
    expect(readState(memStore())).toBeNull();
  });

  it("reads a valid saved state", () => {
    const store = memStore({ [STATE_KEY]: JSON.stringify({ tour: "chat", index: 1 }) });
    expect(readState(store)).toEqual({ tour: "chat", index: 1 });
    expect(store.getItem).toHaveBeenCalledWith(STATE_KEY);
  });

  it("rejects unknown tours, non-numeric indexes and corrupt JSON", () => {
    expect(readState(memStore({ [STATE_KEY]: JSON.stringify({ tour: "nope", index: 0 }) }))).toBeNull();
    expect(readState(memStore({ [STATE_KEY]: JSON.stringify({ tour: "chat", index: "2" }) }))).toBeNull();
    expect(readState(memStore({ [STATE_KEY]: "{not json" }))).toBeNull();
    expect(readState(memStore({ [STATE_KEY]: "null" }))).toBeNull();
  });

  it("clamps the index into the range of the tour's steps", () => {
    const len = TOURS.chat.steps.length;
    expect(readState(memStore({ [STATE_KEY]: JSON.stringify({ tour: "chat", index: 999 }) }))).toEqual({ tour: "chat", index: len });
    expect(readState(memStore({ [STATE_KEY]: JSON.stringify({ tour: "chat", index: -5 }) }))).toEqual({ tour: "chat", index: 0 });
  });

  it("returns null instead of throwing when storage throws", () => {
    expect(readState(throwingStore())).toBeNull();
  });

  it("falls back to window.localStorage when no store is passed", () => {
    localStorage.setItem(STATE_KEY, JSON.stringify({ tour: "mcp", index: 0 }));
    expect(readState()).toEqual({ tour: "mcp", index: 0 });
  });

  it("returns null when window.localStorage itself throws on access", () => {
    const desc = Object.getOwnPropertyDescriptor(window, "localStorage")!;
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new Error("denied");
      },
    });
    try {
      expect(readState()).toBeNull();
      expect(() => writeState({ tour: "chat", index: 0 })).not.toThrow();
      // safeStorage() returns null here; the welcome tour must not auto-start on every visit.
      expect(firstRunDone()).toBe(true);
    } finally {
      Object.defineProperty(window, "localStorage", desc);
    }
  });
});

describe("writeState", () => {
  it("stores the state as JSON under the tour key", () => {
    const store = memStore();
    writeState({ tour: "welcome", index: 3 }, store);
    expect(store.setItem).toHaveBeenCalledWith(STATE_KEY, JSON.stringify({ tour: "welcome", index: 3 }));
    expect(JSON.parse(store.data.get(STATE_KEY)!)).toEqual({ tour: "welcome", index: 3 });
  });

  it("removes the key when given null", () => {
    const store = memStore({ [STATE_KEY]: "{}" });
    writeState(null, store);
    expect(store.removeItem).toHaveBeenCalledWith(STATE_KEY);
    expect(store.data.has(STATE_KEY)).toBe(false);
  });

  it("does nothing when there is no store", () => {
    expect(() => writeState({ tour: "chat", index: 0 }, null)).not.toThrow();
  });

  it("swallows storage errors (private windows)", () => {
    expect(() => writeState({ tour: "chat", index: 0 }, throwingStore())).not.toThrow();
    expect(() => writeState(null, throwingStore())).not.toThrow();
  });

  it("writes to window.localStorage by default and round-trips through readState", () => {
    writeState({ tour: "agents", index: 1 });
    expect(localStorage.getItem(STATE_KEY)).toBe(JSON.stringify({ tour: "agents", index: 1 }));
    writeState(null);
    expect(localStorage.getItem(STATE_KEY)).toBeNull();
  });
});

describe("slice", () => {
  it("returns the steps on the current page and where the tour continues", () => {
    const welcome = TOURS.welcome.steps;
    const res = slice("welcome", 0, "/");
    expect(res.steps.map((s) => s.title)).toEqual(welcome.slice(0, 4).map((s) => s.title));
    expect(res.next).toBe(4);
    expect(res.nextPage).toBe("/agents/");
  });

  it("starts mid-tour when index points past the first page", () => {
    const res = slice("welcome", 4, "/agents");
    expect(res.steps).toHaveLength(1);
    expect(res.steps[0].title).toBe("Design your agents");
    expect(res.next).toBe(5);
    expect(res.nextPage).toBe("/knowledge/");
  });

  it("reports the end of the tour on the last page", () => {
    const len = TOURS.welcome.steps.length;
    const res = slice("welcome", len - 1, "/settings/");
    expect(res.steps.map((s) => s.title)).toEqual(["Settings"]);
    expect(res.next).toBeNull();
    expect(res.nextPage).toBeNull();
  });

  it("returns no steps when the current page is not the one the tour expects", () => {
    const res = slice("chat", 0, "/mcp/");
    expect(res.steps).toEqual([]);
    expect(res.next).toBe(0);
    expect(res.nextPage).toBe("/");
  });

  it("is empty for an unknown tour", () => {
    expect(slice("missing", 0, "/")).toEqual({ steps: [], next: null, nextPage: null });
  });
});

describe("first-run flag", () => {
  it("is not done until the flag is written", () => {
    expect(firstRunDone(memStore())).toBe(false);
    // without any storage the tour must not auto-start on every visit
    expect(firstRunDone(null)).toBe(true);
  });

  it("marks done with the value '1' and reads it back", () => {
    const store = memStore();
    markFirstRunDone(store);
    expect(store.setItem).toHaveBeenCalledWith(DONE_KEY, "1");
    expect(firstRunDone(store)).toBe(true);
  });

  it("treats any value other than '1' as not done", () => {
    expect(firstRunDone(memStore({ [DONE_KEY]: "0" }))).toBe(false);
  });

  it("treats a blocked store as done so the tour never nags", () => {
    expect(firstRunDone(throwingStore())).toBe(true);
  });

  it("ignores write errors and missing stores", () => {
    expect(() => markFirstRunDone(throwingStore())).not.toThrow();
    expect(() => markFirstRunDone(null)).not.toThrow();
  });

  it("uses window.localStorage by default", () => {
    markFirstRunDone();
    expect(localStorage.getItem(DONE_KEY)).toBe("1");
    expect(firstRunDone()).toBe(true);
  });
});
