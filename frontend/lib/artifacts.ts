import { API_BASE } from "./api";

export type Copy = { storage: string; status: "pending" | "synced" | "failed" | "blocked"; detail: string; size: number; updated: number };
export type Artifact = {
  id: string; name: string; path: string; type: string; mime: string; preview: string; size: number; sha256: string; version: number; created: number;
  agent: string; session_id: string; trace_id: string; workspace: string; source: string; tags: string[]; summary: string; secrets: boolean; copies: Copy[];
};
export type Facets = { agents: { value: string; count: number }[]; types: { value: string; count: number }[]; sources: { value: string; count: number }[]; storages: { value: string; count: number }[]; total: number; bytes: number };

export const TYPE_LABEL: Record<string, string> = { document: "Documents", code: "Code", image: "Images", data: "Data", model: "Models", archive: "Archives", audio: "Audio", video: "Video", other: "Other" };
export const SOURCE_LABEL: Record<string, string> = { auto: "Found in the workspace", tool: "Saved by an agent", chat: "Saved from an answer", upload: "Uploaded", canvas: "Edited in the canvas" };
export const COPY_WORD: Record<string, string> = { synced: "stored", pending: "waiting to copy", failed: "failed", blocked: "not sent" };

export const fmtBytes = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB` : n < 1024 ** 3 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${(n / 1024 ** 3).toFixed(2)} GB`);
export const artifactUrl = (id: string, download = false) => `${API_BASE}/api/artifacts/${id}/content${download ? "?download=true" : ""}`;
export const isOurArtifactUrl = (url: string) => url.startsWith(`${API_BASE}/api/artifacts/`) && /^\/api\/artifacts\/[0-9a-f]{12}\/content$/.test(url.slice(API_BASE.length).split("?")[0]);
export const agoText = (ts: number, now = Date.now() / 1000) => {
  const s = Math.max(0, now - ts);
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : `${Math.floor(s / 86400)} d ago`;
};
/** The overall state of an artifact's copies in one word, for a badge. */
export function copyState(a: Artifact): { tone: "ok" | "warn" | "bad"; text: string } {
  const bad = a.copies.filter((c) => c.status === "failed"), blocked = a.copies.filter((c) => c.status === "blocked"), wait = a.copies.filter((c) => c.status === "pending");
  if (bad.length) return { tone: "bad", text: `${bad.length} copy failed` };
  if (blocked.length) return { tone: "warn", text: `${blocked.length} not sent` };
  if (wait.length) return { tone: "warn", text: "copying…" };
  return { tone: "ok", text: a.copies.length > 1 ? `in ${a.copies.length} places` : "stored" };
}
