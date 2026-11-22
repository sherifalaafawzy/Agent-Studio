import { describe, expect, it, vi } from "vitest";
import { ApiError, api, API_BASE, followSse, streamSse, usd } from "@/lib/api";
import { jsonResponse, routeFetch, sseResponse, textResponse } from "../helpers/http";

describe("API_BASE", () => {
  it("defaults to the local backend port", () => {
    expect(API_BASE).toBe("http://127.0.0.1:8765");
  });
});

describe("ApiError", () => {
  it("uses a string detail as the message", () => {
    const e = new ApiError(400, "bad input");
    expect(e).toBeInstanceOf(Error);
    expect(e.status).toBe(400);
    expect(e.detail).toBe("bad input");
    expect(e.message).toBe("bad input");
  });

  it("uses detail.message when the detail is an object", () => {
    expect(new ApiError(422, { message: "field missing" }).message).toBe("field missing");
  });

  it("falls back to the HTTP status when there is no usable message", () => {
    expect(new ApiError(503, undefined).message).toBe("HTTP 503");
    expect(new ApiError(500, { code: 1 }).message).toBe("HTTP 500");
    expect(new ApiError(500, null).message).toBe("HTTP 500");
  });
});

describe("usd", () => {
  it("formats normal amounts with four decimals", () => {
    expect(usd(1.5)).toBe("$1.5000");
    expect(usd(0)).toBe("$0.0000");
  });

  it("uses five decimals for tiny positive amounts", () => {
    expect(usd(0.004)).toBe("$0.00400");
  });

  it("does not switch to five decimals for negatives", () => {
    expect(usd(-0.004)).toBe("$-0.0040");
  });
});

describe("api()", () => {
  it("prefixes the path with API_BASE and sends JSON headers", async () => {
    const { calls } = routeFetch({ "GET /api/ping": () => jsonResponse({ ok: true }) });
    await expect(api("/api/ping")).resolves.toEqual({ ok: true });
    expect(calls[0].url.href).toBe(`${API_BASE}/api/ping`);
    expect(calls[0].init?.headers).toEqual({ "Content-Type": "application/json" });
  });

  it("merges caller headers over the default content type", async () => {
    const { calls } = routeFetch({ "POST /api/x": () => jsonResponse({}) });
    await api("/api/x", { method: "POST", headers: { "X-Trace": "1", "Content-Type": "text/plain" } });
    expect(calls[0].init?.headers).toEqual({ "Content-Type": "text/plain", "X-Trace": "1" });
  });

  it("throws an ApiError carrying the server detail on a non-2xx response", async () => {
    routeFetch({ "POST /api/x": () => jsonResponse({ detail: "cap reached" }, { status: 402 }) });
    const err = await api("/api/x", { method: "POST" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(402);
    expect(err.message).toBe("cap reached");
  });

  it("falls back to the whole body when there is no detail field", async () => {
    routeFetch({ "GET /api/x": () => jsonResponse({ error: "nope" }, { status: 400 }) });
    const err = await api("/api/x").catch((e) => e);
    expect(err.detail).toEqual({ error: "nope" });
    expect(err.message).toBe("HTTP 400");
  });

  it("treats a non-JSON error body as a bare HTTP error", async () => {
    routeFetch({ "GET /api/x": () => textResponse("<html>boom</html>", 502) });
    const err = await api("/api/x").catch((e) => e);
    expect(err.status).toBe(502);
    expect(err.detail).toBeNull();
    expect(err.message).toBe("HTTP 502");
  });

  it("returns null for a successful response whose body is not JSON", async () => {
    routeFetch({ "GET /api/x": () => textResponse("ok", 200) });
    await expect(api("/api/x")).resolves.toBeNull();
  });

  it("propagates network failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api("/api/x")).rejects.toThrow("Failed to fetch");
  });
});

describe("streamSse()", () => {
  it("POSTs the JSON body and delivers every data frame, across chunk boundaries", async () => {
    const { calls } = routeFetch({
      "POST /api/run": () => sseResponse(['data: {"type":"a"}\n', '\ndata: {"ty', 'pe":"b"}\n\n', "event: ignored\n\n", 'data: {"type":"c"}\n\n']),
    });
    const seen: any[] = [];
    await streamSse("/api/run", { model: "m" }, (e) => seen.push(e));
    expect(seen).toEqual([{ type: "a" }, { type: "b" }, { type: "c" }]);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].body).toEqual({ model: "m" });
    expect(calls[0].init?.headers).toEqual({ "Content-Type": "application/json" });
  });

  it("ignores frames that are not data lines and keeps the rest of the buffer", async () => {
    routeFetch({ "POST /api/run": () => sseResponse([": keep-alive\n\n", "retry: 10\ndata: {\"n\":1}\n\n"]) });
    const seen: any[] = [];
    await streamSse("/api/run", {}, (e) => seen.push(e));
    expect(seen).toEqual([{ n: 1 }]);
  });

  it("throws the server detail when the response is not ok", async () => {
    routeFetch({ "POST /api/run": () => jsonResponse({ detail: "provider missing" }, { status: 400 }) });
    const onEvent = vi.fn();
    const err = await streamSse("/api/run", {}, onEvent).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("provider missing");
    expect(onEvent).not.toHaveBeenCalled();
  });

  it("throws an ApiError with no detail when the error body is not JSON", async () => {
    routeFetch({ "POST /api/run": () => textResponse("gateway down", 504) });
    const err = await streamSse("/api/run", {}, () => {}).catch((e) => e);
    expect(err.status).toBe(504);
    expect(err.message).toBe("HTTP 504");
  });

  it("passes the abort signal through and rejects when it fires", async () => {
    const ac = new AbortController();
    const fn = vi.fn(async (_u: string, init?: RequestInit) => {
      ac.abort();
      if (init?.signal?.aborted) throw new DOMException("aborted", "AbortError");
      return sseResponse([]);
    });
    vi.stubGlobal("fetch", fn);
    await expect(streamSse("/api/run", {}, () => {}, ac.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fn.mock.calls[0][1]?.signal).toBe(ac.signal);
  });
});

describe("followSse()", () => {
  it("issues a GET with no body and streams frames", async () => {
    const { calls } = routeFetch({ "GET /api/jobs/1/events": () => sseResponse(['data: {"type":"done"}\n\n']) });
    const seen: any[] = [];
    await followSse("/api/jobs/1/events", (e) => seen.push(e));
    expect(seen).toEqual([{ type: "done" }]);
    expect(calls[0].method).toBe("GET");
    expect(calls[0].body).toBeUndefined();
  });

  it("forwards the abort signal to fetch", async () => {
    const { calls } = routeFetch({ "GET /api/jobs/1/events": () => sseResponse([]) });
    const ac = new AbortController();
    await followSse("/api/jobs/1/events", () => {}, ac.signal);
    expect(calls[0].init?.signal).toBe(ac.signal);
  });
});
