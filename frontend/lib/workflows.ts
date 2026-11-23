// Shared types and helpers of the workflow screens (the Flows tab, the editor, the node panel).
export type Settings = { on_error: string; retries: number; retry_wait_s: number; timeout_s: number; disabled: boolean; notes: string; color: string; pin: string | null };
export type WNode = { id: string; type: string; label: string; position: { x: number; y: number }; config: any; settings?: Partial<Settings> };
export type WEdge = { id?: string; source: string; target: string; source_port?: string };
export type Problem = { node: string; severity: string; message: string };
export type Doc = { id: string; name: string; description?: string; autonomy: string; nodes: WNode[]; edges: WEdge[]; version?: number; updated?: number; problems?: Problem[] };
export type Field = { name: string; label: string; kind: string; required?: boolean; default?: unknown; help?: string; options?: string[]; min?: number; max?: number; ref?: string };
export type Ref = { id: string; label: string; ready?: boolean; read_only?: string; kind?: string };
export type Refs = { mcp: Ref[]; knowledge: Ref[]; data_source: Ref[]; guardrail: Ref[]; credential: Ref[] };
export const NO_REFS: Refs = { mcp: [], knowledge: [], data_source: [], guardrail: [], credential: [] };
export type FormField = { name: string; label: string; type: string; options: string[]; required: boolean };
export const ENTRY = ["input", "webhook"];
export const ROOTS = [...ENTRY, "error_handler"];
export const RULE_OPS: [string, string][] = [["equals", "is"], ["not_equals", "is not"], ["contains", "contains"], ["not_contains", "does not contain"], ["starts_with", "starts with"], ["ends_with", "ends with"],
  ["gt", "is greater than"], ["gte", "is at least"], ["lt", "is less than"], ["lte", "is at most"], ["is_empty", "is empty"], ["not_empty", "is not empty"], ["regex", "matches pattern"]];
export type NodeSpec = { type: string; group: string; label: string; desc: string; fields: Field[]; outputs: string[] | string; inputs: number; visual?: boolean };
export type Catalog = { groups: { id: string; label: string }[]; nodes: NodeSpec[] };
export type RunNode = { status: string; output: string; error: string | null; label: string; type: string; ports?: string[]; attempts?: number; input?: string; message?: string; decision?: string; pinned?: boolean; form?: FormField[]; reused?: boolean; flagged?: number; started?: number | null; ended?: number | null; answers?: Record<string, unknown> };
export type Run = { id: string; status: string; final: string; error: string | null; plan: { node: string; label: string; does: string }[]; nodes: Record<string, RunNode>; cost_usd: number; autonomy: string; started?: number; ended?: number | null; task?: string; from_node?: string | null;
  workflow?: string; name?: string; origin?: string; tokens?: number; version?: number; counts?: RunCounts; needs_you?: boolean };
export type RunCounts = { total: number; done: number; error: number; skipped: number; running: number; waiting: number; pending: number; stopped: number };
/** One line of the Runs tab (no box text: that is in the detail). */
export type RunRow = { id: string; workflow: string; name: string; status: string; origin: string; autonomy: string; version: number; started: number; ended: number | null; duration: number; task: string; error: string | null;
  cost_usd: number; tokens: number; from_node: string | null; counts: RunCounts; needs_you: boolean };
export type RunList = { runs: RunRow[]; total: number; counts: Record<string, number>; needs_you: number; retention: number; limit: number; offset: number };
export type TestResult = { status: string; output?: string; error?: string | null; ports?: string[]; decision?: string | null; side_effects?: boolean; message?: string; cost_usd?: number };

export const AUTONOMY: [string, string][] = [["suggest", "Suggest only: show me the plan, run nothing"], ["approve", "Ask me before each step"], ["auto", "Run it all by itself"]];
export const SAFE = new Set(["read_file", "list_dir", "glob_files", "search_text", "repo_map", "rag_search", "memory_search", "web_fetch", "web_search"]);
export const ORIGIN_LABEL: Record<string, string> = { manual: "Run by you", webhook: "Webhook", agent: "An agent", run_from: "From a box" };
export const RUN_STATUS: Record<string, { label: string; icon: string; tone: string }> = {
  running: { label: "Running", icon: "◔", tone: "acc" }, done: { label: "Worked", icon: "✓", tone: "ok" }, error: { label: "Failed", icon: "✕", tone: "bad" }, stopped: { label: "Stopped", icon: "■", tone: "warn" }, plan: { label: "Plan only", icon: "☰", tone: "" },
};
/** "850 ms", "12 s", "3 min 05 s", "2 h 10 min". */
export function fmtDuration(s: number | null | undefined): string {
  if (s == null) return "–";
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, "0")} s`;
  return `${Math.floor(s / 3600)} h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")} min`;
}
export const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^[^a-z]+/, "").slice(0, 36);
export const blankDoc = (name: string): Doc => ({ id: slug(name) || "workflow", name, autonomy: "approve", nodes: [{ id: "start", type: "input", label: "Task", position: { x: 60, y: 160 }, config: {} }], edges: [] });
export const ago = (t?: number | null) => {
  if (!t) return "never";
  const s = Math.max(0, Date.now() / 1000 - t);
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : `${Math.floor(s / 86400)} d ago`;
};

/** The output ports of a box: the catalog's fixed ones, one per label/case (plus "other"), and an "error" port when "on error: branch" is set. */
export function portsOf(n: WNode, spec?: NodeSpec): string[] {
  let out: string[] = Array.isArray(spec?.outputs) ? [...(spec!.outputs as string[])] : spec?.outputs === "labels" ? [...(n.config?.labels ?? []), "other"] : spec?.outputs === "cases" ? [...(n.config?.cases ?? []), "other"] : spec ? [] : ["out"];
  if (n.settings?.on_error === "branch" && out.length) out = [...out, "error"];
  return out;
}

/** The defaults of a new box of this kind (from the catalog's field defaults). */
export function defaultConfig(spec: NodeSpec): Record<string, unknown> {
  const c: Record<string, unknown> = {};
  for (const f of spec.fields) {
    if (f.kind === "steps") c[f.name] = [];
    else if (f.default !== undefined) c[f.name] = JSON.parse(JSON.stringify(f.default));
    else if (f.kind === "json") c[f.name] = {};
    else if (f.kind === "pairs") c[f.name] = [{ name: "field", value: "" }];
    else if (f.kind === "lines" || f.kind === "formfields") c[f.name] = [];
    else if (f.kind === "rules") c[f.name] = [{ field: "", op: "equals", value: "" }];
    else if (f.kind === "conditions") c[f.name] = [{ left: "", op: "equals", right: "" }];
    else if (f.kind === "bool") c[f.name] = false;
  }
  if (spec.type === "agent") return { agent: "", prompt: "{{ $prev }}" };
  if (spec.type === "tool") return { tool: "", args: {}, confirmed: false };
  if (spec.type === "openapi") return { source: "", operation: "", args: {}, confirmed: false };
  return c;
}

export function uniqueId(nodes: WNode[], type: string): string {
  let i = 1;
  while (nodes.some((n) => n.id === `${type}${i}`)) i++;
  return `${type}${i}`;
}

/** A simple left-to-right layered layout: every box sits one column after the latest box that feeds it. */
export function tidy(nodes: WNode[], edges: WEdge[]): WNode[] {
  const depth: Record<string, number> = {};
  const incoming = (id: string) => edges.filter((e) => e.target === id).map((e) => e.source);
  const level = (id: string, seen: string[] = []): number => {
    if (depth[id] !== undefined) return depth[id];
    if (seen.includes(id)) return 0;
    const ins = incoming(id);
    return (depth[id] = ins.length ? 1 + Math.max(...ins.map((s) => level(s, [...seen, id]))) : 0);
  };
  const rows: Record<number, number> = {};
  return nodes.map((n) => {
    if (n.type === "note") return n;
    const l = level(n.id);
    const r = rows[l] ?? 0;
    rows[l] = r + 1;
    return { ...n, position: { x: 60 + l * 290, y: 60 + r * 150 } };
  });
}

const SUM: Record<string, (c: any) => string> = {
  compare: (c) => `${(c.conditions ?? []).length} condition(s), ${c.combine === "or" ? "any" : "all"} must hold`, filter: (c) => `keep items where ${(c.rules ?? []).map((r: any) => `${r.field || "item"} ${r.op}`).join(", ") || "…"}`,
  http_request: (c) => `${c.method ?? "GET"} ${String(c.url ?? "").slice(0, 40) || "set an address"}`, mcp_tool: (c) => (c.server && c.tool ? `${c.server}.${c.tool}` : "choose a server and tool"),
  memory: (c) => `${c.operation ?? "search"} ${c.kind ?? "semantic"} memory`, knowledge: (c) => (c.kb ? `search ${c.kb}` : "choose a knowledge base"), data_query: (c) => (c.source ? `${c.source}: ${String(c.sql ?? "").slice(0, 36)}` : "choose a data source"),
  artifact: (c) => `keep as ${c.name ?? "file"}`, guardrail: (c) => `check the ${c.stage ?? "input"}`, sort: (c) => `by ${c.field || "the item"} ${c.order === "desc" ? "↓" : "↑"}`, limit: (c) => `${c.from === "end" ? "last" : "first"} ${c.count ?? 10}`,
  dedupe: (c) => (c.field ? `by ${c.field}` : "whole items"), aggregate: (c) => `${c.op ?? "count"}${c.field ? ` of ${c.field}` : ""}${c.group_by ? ` per ${c.group_by}` : ""}`, split_out: (c) => `by ${c.mode ?? "lines"}`,
  join: (c) => `join with "${c.separator ?? ""}"`, merge_by_key: (c) => `on ${c.key || "a key"} (${c.mode ?? "inner"})`, date_time: (c) => String(c.operation ?? "now").replace("_", " "), regex: (c) => `${c.mode ?? "first"} /${String(c.pattern ?? "").slice(0, 30)}/`,
  template: (c) => String(c.text ?? "").replace(/\s+/g, " ").slice(0, 50) || "write the text", stop: (c) => String(c.message ?? "").slice(0, 50) || "stop with a message",
  webhook: (c) => `starts when called · answers ${String(c.respond ?? "immediately").replace("_", " ")}`, respond_webhook: (c) => `answers with status ${c.status ?? 200}`, error_handler: () => "runs only if something fails",
};

/** Text a box shows as its one-line summary. */
export function summarise(n: WNode): string {
  const c = n.config ?? {};
  switch (n.type) {
    case "agent": return c.agent ? `agent: ${c.agent}` : "choose an agent";
    case "tool": return c.tool ? `tool: ${c.tool}` : "choose a tool";
    case "openapi": return c.operation ? `${c.source}.${c.operation}` : "choose an API endpoint";
    case "sequential": case "parallel": case "loop": return `${(c.steps ?? []).length} step(s)${n.type === "loop" ? `, up to ${c.max_iterations ?? 3}×` : ""}`;
    case "llm": return String(c.prompt ?? "").slice(0, 60) || "write a prompt";
    case "classifier": return (c.labels ?? []).join(" / ") || "add labels";
    case "if": return String(c.condition ?? "").replace(/[{}]/g, "").trim().slice(0, 50) || "set a condition";
    case "switch": return (c.cases ?? []).join(" / ") || "add cases";
    case "wait": return `${c.seconds ?? 0} s`;
    case "sub_workflow": case "for_each": return c.workflow ? `workflow: ${c.workflow}` : "choose a workflow";
    case "set": return `${(c.fields ?? []).length} field(s)`;
    case "approval": return String(c.message ?? "").slice(0, 50) || "ask a person";
    case "note": return String(c.text ?? "").slice(0, 80);
    case "input": return "the task comes in here";
    case "output": return "the result goes out here";
    default: return SUM[n.type]?.(c) ?? n.type.replace(/_/g, " ");
  }
}
