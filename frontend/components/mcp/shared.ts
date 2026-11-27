import type { CatalogServer } from "@/components/CatalogMcp";

export type Mcp = {
  id: string; label: string; description: string; transport: string; command: string | null; args: string[]; url: string | null;
  requires_env: string[]; env_set: Record<string, boolean>; enabled: boolean; default_on: boolean; status: string; builtin: boolean; ready: boolean;
  category: string; used_by: { id: string; name: string }[]; last_test: { ok: boolean; tools?: number; names?: string[]; error?: string; ts: number } | null;
};
export type Test = { ok: boolean; tools?: { name: string; description: string }[]; error?: string };
export type Filter = "all" | "ready" | "off" | "pending";
export type Issue = { id: string; kind: string; source: string; title: string; detail: string; suggestion: string; ts: number; debug_job: string | null };

export type GroupId = "setup" | "installed" | "builtin" | "available";
export const GROUPS: { id: GroupId; title: string; hint: string }[] = [
  { id: "setup", title: "Needs setup", hint: "Switched on but waiting for a credential, an install or your approval." },
  { id: "installed", title: "Installed", hint: "Servers you added, from the Catalog or by hand." },
  { id: "builtin", title: "Built-in tools", hint: "Files, git, browser, memory and local models that come with Agent Studio." },
  { id: "available", title: "Available from the catalog", hint: "Checked public servers you can add with a few clicks." },
];

/** One line in the list: an installed server (m), a catalog server that is not installed yet (c only), or both. */
export type Row = { id: string; label: string; description: string; group: GroupId; tone: string; statusText: string; m?: Mcp; c?: CatalogServer };

const TINT = ["#3f6fcf", "#b5522d", "#1c7f6b", "#946a0c", "#7a55c7", "#b8433b", "#22789f", "#3d8436"];   // each carries white initials at 4.5:1 or better
export const tint = (s: string) => TINT[[...s].reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 7) >>> 0 & 7];
export const initials = (label: string) => label.replace(/[^A-Za-z0-9]/g, "").slice(0, 2);
export const ago = (t: number) => { const s = Date.now() / 1000 - t; return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`; };

/** The status of an installed server, in words and as a tone for the dot and pill. */
export function status(m: Mcp): [string, string] {
  if (m.id === "d1" && !m.ready && m.status !== "pending_approval") return ["not installed", "warn"];
  if (m.id === "timeseries" && m.enabled && !m.ready && m.status !== "pending_approval") return ["no model installed", "warn"];
  if (m.id === "trainer" && m.enabled && !m.ready && m.status !== "pending_approval") return ["no framework installed", "warn"];
  if (m.status === "pending_approval") return ["needs approval", "warn"];
  if (m.ready) return [m.last_test && !m.last_test.ok ? "failed last test" : "ready", m.last_test && !m.last_test.ok ? "bad" : "ok"];
  if (m.enabled) return ["needs credentials", "warn"];
  return ["off", ""];
}

/** Local = the catalog says nothing leaves this PC. Anything else, and every remote URL, sends data somewhere. */
export const leavesPc = (m?: Mcp, c?: CatalogServer) => (c ? !/^nothing leaves/i.test(c.data_leaves) : m?.transport === "http");
/** Access label from the catalog entry; plain servers carry no such information. */
export function access(c?: CatalogServer): "read" | "write" | "unknown" {
  if (!c) return "unknown";
  if (c.read_only === "always" || (c.read_only === "enforced" && !(c.installed && c.allow_writes))) return "read";
  return "write";
}

export function buildRows(list: Mcp[], catalog: CatalogServer[]): Row[] {
  const byId = new Map(catalog.map((c) => [c.id, c]));
  const rows: Row[] = list.map((m) => {
    const [statusText, tone] = status(m);
    const needs = tone === "warn" || m.status === "pending_approval";
    const group: GroupId = needs ? "setup" : m.builtin ? "builtin" : "installed";
    return { id: m.id, label: m.label, description: m.description, group, tone, statusText, m, c: byId.get(m.id) };
  });
  const have = new Set(list.map((m) => m.id));
  for (const c of catalog) if (!have.has(c.id) && !c.installed) rows.push({ id: c.id, label: c.label, description: c.description, group: "available", tone: "", statusText: "not added", c });
  return rows;
}
