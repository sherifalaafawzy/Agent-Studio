// The Science Lab: types for the API and the small helpers the panels share.
export type Domain = "biology" | "chemistry" | "physics" | "mathematics" | "cross";
export const DOMAINS: Domain[] = ["biology", "chemistry", "physics", "mathematics", "cross"];
export const DOMAIN_LABEL: Record<string, string> = { biology: "Biology", chemistry: "Chemistry", physics: "Physics", mathematics: "Mathematics", cross: "Cross-domain", literature: "Literature" };

export type Connector = {
  id: string; label: string; domain: string; summary: string; interval_s: number; limits: string; licence: string; ops: string[];
  needs_key: boolean; key_set: boolean; key_help: string; enabled: boolean; status: "ready" | "needs_key" | "off"; cache: boolean; verified: boolean;
};
export type Policy = { blocked: string[]; confirm: string[]; allowed: string[]; tools_not_offered: string[]; limits: string[] };
export type Config = {
  enabled: boolean; safety_level: string; disabled_connectors: string[]; rate_overrides: Record<string, number>; contact_email: string; max_calls_per_investigation: number;
  cache_minutes: number; max_investigations: number; safety_levels: string[]; domains: string[]; labels: Record<string, string>; policy: Policy; key_set: string[];
};
export type Inv = {
  id: string; title: string; domain: Domain; question: string; status: "open" | "done"; autonomy: string; session_id: string; agent: string; created: number; updated: number;
  evidence: number; claims: number; hypotheses: number; source_calls: number;
};
export type Evidence = {
  id: string; inv_id: string; kind: "source" | "computed" | "inference"; source: string; ref: string; title: string; url: string; tool: string; query: string; quote: string; value: string;
  run_id: string; retrieved_at: number; verified: -1 | 0 | 1; verified_note: string;
};
export type Claim = { id: string; text: string; evidence: string[]; conflicting: boolean; label: string; label_text: string; flags: string[] };
export type Hypothesis = { id: string; text: string; elo: number; wins: number; losses: number };
export type Run = { id: string; tool: string; created: number; record: Record<string, any> };
export type InvFull = Inv & { claims_list: Claim[]; evidence_list: Evidence[]; hypotheses_list: Hypothesis[]; runs_list: Run[] };
export type SciRecord = { id: string; title: string; summary: string; url: string; kind: string; fields: Record<string, unknown> };
export type SearchOut = { ok: boolean; records?: SciRecord[]; provenance?: { connector: string; label: string; retrieved_at: number; licence: string; requests: number; note: string; query: string };
  run_id?: string; error?: string; error_kind?: string; blocked?: boolean; safety?: { reason: string; help: string; action: string } };
export type Component = {
  id: string; name: string; kind: string; summary: string; can_install: boolean; reasons: string[]; licence: string; licence_verified: boolean; licence_source: string; hardware: string; disk: string;
  keys: string; egress: string; cost: string; caution: string; link: string; steps: string[]; state: string; installed_version: string; error: string; installing: boolean; command: string; version: string; package: string;
};

/** How a claim label is shown: a word and a symbol, so colour is never the only signal. */
export const LABEL_SHOW: Record<string, { icon: string; tone: "ok" | "warn" | "bad" | "mut" }> = {
  established: { icon: "✓✓", tone: "ok" }, single_source: { icon: "✓", tone: "warn" }, computed: { icon: "∑", tone: "ok" },
  model_inference: { icon: "?", tone: "mut" }, conflicting: { icon: "≠", tone: "bad" }, unsupported: { icon: "!", tone: "bad" },
};
export const labelShow = (l: string) => LABEL_SHOW[l] ?? { icon: "·", tone: "mut" as const };

export const VERIFIED_TEXT: Record<number, string> = { 1: "verified", 0: "could not verify", [-1]: "not checked" };

export const ago = (t: number, now = Date.now() / 1000) => {
  const s = Math.max(0, now - t);
  return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
};
export const when = (t: number) => (t ? new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "unknown");

/** The records of a search as a table spec for the shared table component (sort, filter, export). */
export function recordsTable(records: SciRecord[]) {
  const keys = Array.from(new Set(records.flatMap((r) => Object.keys(r.fields)))).slice(0, 6);
  return {
    type: "table", title: "Results",
    columns: [{ key: "id", label: "Id", type: "text" }, { key: "title", label: "Title", type: "text" }, ...keys.map((k) => ({ key: k, label: k.replace(/_/g, " "), type: "text" }))],
    rows: records.map((r) => [r.id, r.title, ...keys.map((k) => (r.fields[k] == null ? "" : String(r.fields[k])))]),
  };
}

/** A `table` result of the calculator as a line chart spec for the shared chart component. */
export function tableChart(x: number[], y: number[], expression: string) {
  const round = (n: number) => Number(n.toPrecision(6));
  return { type: "chart.line", title: expression, x: x.map((v) => String(round(v))), series: [{ name: expression, values: y.map(round) }] };
}

/** Parse a JSON object typed by the user; returns an error text instead of throwing. */
export function parseObject(text: string): { value?: Record<string, unknown>; error?: string } {
  if (!text.trim()) return { value: {} };
  try {
    const v = JSON.parse(text);
    return v && typeof v === "object" && !Array.isArray(v) ? { value: v } : { error: "Write the variables as a JSON object, for example {\"m\": 2}." };
  } catch { return { error: "Write the variables as a JSON object, for example {\"m\": 2}." }; }
}
