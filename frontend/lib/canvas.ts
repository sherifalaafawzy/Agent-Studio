// The canvas: types, small helpers and the line diff used to compare versions.
export type CanvasType = "markdown" | "code" | "table" | "html" | "text";
export type Canvas = {
  id: string; title: string; type: CanvasType; language: string; version: number; session_id: string; agent: string; artifact_id: string;
  size: number; created: number; updated: number; pending: number; content?: string;
};
export type Edit = { n: number; old?: string; at?: "start" | "end"; new: string; fits: boolean; start: number | null; end: number | null };
export type Proposal = { id: string; canvas_id: string; base_version: number; author: string; note: string; status: string; created: number; resolved: number; edits: Edit[] };
export type VersionRow = { version: number; author: string; note: string; created: number; size: number };

export const TYPE_LABEL: Record<CanvasType, string> = { markdown: "Document", code: "Code", table: "Table", html: "Web page", text: "Text" };
export const LANGUAGES = ["python", "javascript", "typescript", "json", "html", "css", "sql", "yaml", "bash", "markdown", "text"];
export const MIN_WIDTH = 320, MAX_WIDTH = 900, DEFAULT_WIDTH = 520;

/** Who made a version, in words. */
export const authorText = (a: string) => (a === "user" ? "You" : a === "restore" ? "A restore" : a.startsWith("agent:") ? `Agent ${a.slice(6)}` : a);

export type DiffLine = { kind: "same" | "add" | "del"; text: string };

/** A line diff of two texts (longest common subsequence). Texts over 1500 lines are compared as whole blocks to keep it quick. */
export function lineDiff(a: string, b: string): DiffLine[] {
  const x = a.split("\n"), y = b.split("\n");
  if (x.length > 1500 || y.length > 1500) return a === b ? x.map((t) => ({ kind: "same", text: t })) : [...x.map((t) => ({ kind: "del" as const, text: t })), ...y.map((t) => ({ kind: "add" as const, text: t }))];
  const m = x.length, n = y.length;
  const L: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) for (let j = n - 1; j >= 0; j--) L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out: DiffLine[] = [];
  let i = 0, j = 0;
  while (i < m && j < n) {
    if (x[i] === y[j]) { out.push({ kind: "same", text: x[i] }); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push({ kind: "del", text: x[i++] });
    else out.push({ kind: "add", text: y[j++] });
  }
  while (i < m) out.push({ kind: "del", text: x[i++] });
  while (j < n) out.push({ kind: "add", text: y[j++] });
  return out;
}

/** Where an edit's old text sits in the text the user is looking at: only when it is found exactly once. */
export function findRange(text: string, e: Pick<Edit, "old" | "at">): { from: number; to: number } | null {
  if (e.at) return null;
  const old = e.old ?? "";
  const i = old ? text.indexOf(old) : -1;
  return i >= 0 && text.indexOf(old, i + 1) < 0 ? { from: i, to: i + old.length } : null;
}

/** The message sent to the chat when the user selects text in the canvas and asks the agent about it. */
export function askMessage(title: string, canvasId: string, prompt: string, selected: string): string {
  return `About the canvas "${title}" (id ${canvasId}): ${prompt.trim()}\n\nSelected text:\n"""\n${selected}\n"""\nChange only the selected text, with canvas_replace_selection.`;
}

/** A file extension for a canvas, for a download. */
export const fileExt = (c: Pick<Canvas, "type" | "language">) => (c.type === "markdown" ? "md" : c.type === "table" ? "csv" : c.type === "html" ? "html" : c.type === "code" ? ({ python: "py", javascript: "js", typescript: "ts", json: "json", html: "html", css: "css", sql: "sql", yaml: "yaml", bash: "sh", markdown: "md" } as Record<string, string>)[c.language] ?? "txt" : "txt");

/** A guess at what kind of canvas a piece of text should open as, from a code fence's language. */
export function canvasKindFor(lang: string): { type: CanvasType; language: string } {
  const l = lang.toLowerCase();
  if (l === "md" || l === "markdown") return { type: "markdown", language: "" };
  if (l === "csv" || l === "tsv") return { type: "table", language: "" };
  if (l === "html" || l === "html-preview") return { type: "html", language: "" };
  const map: Record<string, string> = { py: "python", python: "python", js: "javascript", javascript: "javascript", jsx: "javascript", ts: "typescript", typescript: "typescript", tsx: "typescript", json: "json", jsonview: "json",
    css: "css", sql: "sql", yaml: "yaml", yml: "yaml", sh: "bash", bash: "bash", shell: "bash", diff: "text" };
  return map[l] ? { type: "code", language: map[l] === "text" ? "" : map[l] } : { type: "text", language: "" };
}
