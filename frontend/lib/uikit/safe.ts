// Clean a component spec in the browser before anything is drawn. The server already cleaned it; this is a second, independent pass because a spec can
// also arrive in a ```ui block, from an AG-UI client or from saved history. Mirrors backend/app/uikit/validate.py: unknown behaviour-carrying keys are dropped,
// strings and lists are limited, links may only be https / mailto / tel / an Agent Studio artifact / a page of this app, and nothing is ever evaluated.
import { KIND_LIST } from "./samples.generated";

export type Spec = { type: string; version?: number; [k: string]: any };
export const MAX_DEPTH = 10, MAX_STR = 20_000, MAX_HTML = 200_000, MAX_ITEMS = 20_000, MAX_ROWS = 20_000;
const URL_KEYS = new Set(["url", "href", "link", "src", "image", "avatar", "icon", "thumbnail", "poster"]);
const BAD_KEYS = new Set(["__proto__", "constructor", "prototype", "dangerouslysetinnerhtml", "style", "class", "classname", "srcdoc"]);
const ARTIFACT_ID = /^[0-9a-f]{12}$/;
const DATA_IMAGE = /^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+$/;
const SAFE_URL = /^(https:\/\/|mailto:|tel:|artifact:|\/(?!\/)|#)/i;
const HEADER_SECRET = /authori[sz]ation|cookie|token|secret|api[-_]?key|password|passwd|x-auth/i;

export const KINDS = new Map(KIND_LIST.map((k) => [k.type, k]));

/** A link that is safe to put in an href or src, or null. */
export function safeUrl(v: unknown, key = "url"): string | null {
  const s = String(v ?? "").trim();
  if (["image", "avatar", "thumbnail", "poster", "src", "icon"].includes(key) && (DATA_IMAGE.test(s) || ARTIFACT_ID.test(s))) return s;
  return SAFE_URL.test(s) ? s : null;
}

/** `chart.line`, `chart` + kind `line`, `Chart.Line` -> `chart.line`; null when it names no component. */
export function canonical(type: unknown, raw?: Record<string, any>): string | null {
  const t = String(type ?? "").trim().toLowerCase().replace(/\//g, ".").replace(/\s+/g, "");
  if (KINDS.has(t)) return t;
  const kind = String(raw?.kind ?? "").trim().toLowerCase();
  return kind && KINDS.has(`${t}.${kind}`) ? `${t}.${kind}` : null;
}

function walk(v: any, key: string, kind: string, depth: number): any {
  if (depth > MAX_DEPTH) return null;
  if (v === null || typeof v === "boolean") return v;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string") {
    if (URL_KEYS.has(key)) return safeUrl(v, key);
    const cap = kind === "html" && key === "html" ? MAX_HTML : MAX_STR;
    return v.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "").slice(0, cap);
  }
  if (Array.isArray(v)) return v.slice(0, key === "rows" ? MAX_ROWS : MAX_ITEMS).map((x) => walk(x, key, kind, depth + 1));
  if (typeof v === "object") {
    const out: Record<string, any> = {};
    for (const [k0, x] of Object.entries(v)) {
      const k = k0.slice(0, 80), low = k.toLowerCase();
      if (BAD_KEYS.has(low) || /^on[a-z]+$/.test(low) || (low === "html" && kind !== "html")) continue;
      out[k] = kind === "card.request" && ["headers", "request_headers", "response_headers"].includes(key) && HEADER_SECRET.test(k) ? "••••" : walk(x, k, kind, depth + 1);
    }
    return out;
  }
  return String(v).slice(0, 200);
}

/** Clean and check a spec: {spec} when it can be drawn, {error} (with a reason) when it cannot. */
export function normalize(raw: unknown): { spec?: Spec; error?: string } {
  let r: any = raw;
  if (typeof r === "string") { try { r = JSON.parse(r); } catch { return { error: "the component is not valid JSON" }; } }
  if (!r || typeof r !== "object" || Array.isArray(r)) return { error: "a component is a JSON object with a type" };
  const key = canonical(r.type, r);
  if (!key) return { error: `unknown component type "${String(r.type ?? "").slice(0, 40)}"` };
  const { type: _t, version: _v, ...rest } = r;
  const spec = { type: key, version: 1, ...walk(rest, "", key, 0) } as Spec;
  const need = KINDS.get(key)!.need;
  if (!need.some((g) => g.every((f) => { const v = spec[f]; return v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length) && !(typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length); }))) {
    return { error: `${key} needs ${need.map((g) => g.map((f) => `"${f}"`).join(" + ")).join(" or ")}` };
  }
  return { spec };
}
