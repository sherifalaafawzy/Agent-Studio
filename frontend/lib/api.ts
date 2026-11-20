export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8765";

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
  default_model: string | null; cap_usd: number; cap_period: "total" | "daily" | "monthly";
  max_output_tokens: number; catalog: { label: string; needs_key: boolean; note: string; default_model: string };
};

export type ProviderBilling = {
  provider: string; label: string; enabled: boolean; cap_usd: number; cap_period: string;
  spent_usd: number; reserved_usd: number; remaining_usd: number; pct_used: number; calls: number; refused: number;
};

export const usd = (n: number) => `$${n < 0.01 && n > 0 ? n.toFixed(5) : n.toFixed(4)}`;
