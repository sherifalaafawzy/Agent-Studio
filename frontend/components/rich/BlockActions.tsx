"use client";
// The two buttons every rich block carries: Keep (save it as an artifact) and Open in Artifacts (save it if needed, then open the Artifacts page in a new tab so the chat stays put).
import { createContext, useContext, useState } from "react";
import { api } from "@/lib/api";
import type { Artifact } from "@/lib/artifacts";
import { ICheck, ISave } from "../Icons";

/** The chat provides the agent and session so a kept block is filed under the conversation it came from. */
export const KeepContext = createContext<{ agent?: string; session?: string }>({});

/** What the chat needs to open a canvas with some text in it. */
export type OpenCanvas = { title: string; type: string; content: string; language?: string };
/** The chat provides this; where it is missing (a page that is not the chat) the "Open in canvas" button is not shown. */
export const CanvasContext = createContext<((o: OpenCanvas) => Promise<void>) | null>(null);

export default function BlockActions({ label, name, content, artifactId, canvas }: { label: string; name: string; content?: () => string; artifactId?: string; canvas?: { type: string; language?: string } }) {
  const ctx = useContext(KeepContext), openCanvas = useContext(CanvasContext);
  const [id, setId] = useState(artifactId ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function keep(): Promise<string> {
    if (id) return id;
    setBusy(true); setErr("");
    try {
      const a = await api<Artifact>("/api/artifacts", { method: "POST", body: JSON.stringify({ name, content: content!(), agent: ctx.agent ?? "", session_id: ctx.session ?? "" }) });
      setId(a.id);
      return a.id;
    } catch (e) { setErr((e as Error).message); return ""; }
    finally { setBusy(false); }
  }
  async function open() { const i = await keep(); if (i) window.open(`/artifacts/?id=${i}`, "_blank", "noopener"); }
  return (
    <span className="block-actions">
      <button className="ghost small" disabled={busy || !!id} aria-label={`Keep ${label}`} onClick={() => { void keep(); }}>{id ? <><ICheck size={12} /> Kept</> : <><ISave size={12} /> Keep</>}</button>
      <button className="ghost small" disabled={busy} aria-label={`Open ${label} in Artifacts`} onClick={() => { void open(); }}>Open in Artifacts</button>
      {canvas && openCanvas && <button className="ghost small" aria-label={`Open ${label} in the canvas`} onClick={() => { void openCanvas({ title: name.replace(/\.[^.]+$/, ""), type: canvas.type, language: canvas.language, content: content!() }); }}>Open in canvas</button>}
      {err && <span className="small err" role="alert">{err}</span>}
    </span>
  );
}
