// Dev: the API runs on its own port. Desktop build: NEXT_PUBLIC_API_BASE="" and the API serves this UI, so paths stay relative.
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8765";

/** The API's full address (for text users copy, and WebSockets): same-origin in the desktop build. */
export function apiOrigin(base: string = API_BASE): string {
  return base || (typeof window !== "undefined" ? window.location.origin : "");
}

export class ApiError extends Error {
  constructor(public status: number, public detail: any) {
    super(typeof detail === "string" ? detail : detail?.message ?? `HTTP ${status}`);
  }
}

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API_BASE + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? body);
  return body as T;
}

export type Provider = {
  id: string; kind: string; label: string; enabled: boolean;
  has_api_key: boolean; api_key_hint: string | null; api_base: string | null;
  vertex_project: string | null; vertex_location: string | null; vertex_credentials_path: string | null;
  default_model: string | null; pro_model: string | null; flash_model: string | null; cap_usd: number; cap_period: "total" | "daily" | "monthly";
  max_output_tokens: number; catalog: { label: string; needs_key: boolean; note: string; default_model: string; auth_modes: string[]; custom?: boolean };
  auth_mode: string; auth_modes: { id: string; label: string }[]; api_key_env: string | null; key_env_default: string;
  has_service_account_json: boolean; service_account_email: string | null; extra_header_names: string[]; organization: string | null;
  auth_ready: boolean; auth_detail: string;
  custom?: boolean; network_scope?: "this_pc" | "network" | "internet" | null; manual_models?: string[];
  model_prices?: Record<string, { input_per_m?: number; output_per_m?: number; context?: number }>;
  context_window?: number | null; aws_region?: string | null; aws_profile?: string | null; aws_access_key_hint?: string | null; has_aws_secret?: boolean; has_aws_session_token?: boolean;
};

export type ProviderBilling = {
  provider: string; label: string; enabled: boolean; cap_usd: number; cap_period: string;
  spent_usd: number; reserved_usd: number; remaining_usd: number; pct_used: number; calls: number; refused: number;
};

export const usd = (n: number) => `$${n < 0.01 && n > 0 ? n.toFixed(5) : n.toFixed(4)}`;

/** POST and read a server-sent-event stream; calls onEvent for every `data:` frame. Abort via `signal`. */
/** GET an SSE stream (re-attach to a running job). */
export function followSse(path: string, onEvent: (e: any) => void, signal?: AbortSignal): Promise<void> {
  return streamSse(path, undefined, onEvent, signal, "GET");
}

export async function streamSse(path: string, body: unknown, onEvent: (e: any) => void, signal?: AbortSignal, method: "POST" | "GET" = "POST"): Promise<void> {
  const res = await fetch(API_BASE + path, method === "GET" ? { signal } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  if (!res.ok || !res.body) {
    const b = await res.json().catch(() => null);
    throw new ApiError(res.status, b?.detail ?? b);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)));
    }
  }
}

/** Auto mode: what the router decided for a message (also streamed as a `route` event and saved with the answer). */
export type AutoRoute = {
  provider: string; model: string; tier: "flash" | "pro"; engine: string; requested_engine?: string; engine_note?: string; reason: string; confidence: number;
  signals: { complexity: string; code: boolean; reasoning: boolean; tools: boolean; vision: boolean; est_tokens: number };
  price: { input_per_m: number; output_per_m: number; known: boolean };
  alternates: { provider: string; model: string; tier: string }[]; skipped: { provider: string; model?: string; why: string }[]; preference?: string;
};

export type AutoInfo = {
  enabled: boolean; engine: string; preference: string; allow: string[]; deny: string[]; fallback: boolean; timeout_s: number;
  tier_models: { flash: string; pro: string }; engines: string[]; preferences: string[]; available: boolean; reason: string;
  worker: { model: string; state: string; error: string; device: string | null; load_seconds: number | null } | null;
  engine_ready: Record<string, boolean>;
};

export const AUTO_ID = "auto";
