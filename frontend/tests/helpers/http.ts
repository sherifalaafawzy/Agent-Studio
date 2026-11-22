import { vi } from "vitest";

/** A JSON Response, like the Agent Studio backend returns. */
export function jsonResponse(body: unknown, init: { status?: number } = {}): Response {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** A Response whose body is not JSON (so res.json() rejects). */
export function textResponse(text: string, status = 500): Response {
  return new Response(text, { status });
}

/** A text/event-stream Response built from raw chunks (each chunk is one read). */
export function sseResponse(chunks: string[], status = 200): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
  return new Response(stream, { status, headers: { "Content-Type": "text/event-stream" } });
}

export type Call = { method: string; url: URL; body: any; init?: RequestInit };
type Handler = (init: RequestInit | undefined, url: URL) => Response | Promise<Response>;

/**
 * Replace global fetch with a router keyed on "METHOD /path" (query-specific keys "METHOD /path?query"
 * are tried too). An unrouted request throws, so a test never silently talks to the network.
 */
export function routeFetch(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = typeof init?.body === "string" ? safeJson(init.body) : init?.body;
    calls.push({ method, url, body, init });
    const handler = routes[`${method} ${url.pathname}${url.search}`] ?? routes[`${method} ${url.pathname}`];
    if (!handler) throw new Error(`Unexpected fetch: ${method} ${url.pathname}${url.search}`);
    return handler(init, url);
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

function safeJson(s: string) {
  try { return JSON.parse(s); } catch { return s; }
}
