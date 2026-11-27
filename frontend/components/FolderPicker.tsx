"use client";
// Folder chooser: the browser can't see real disk paths, but the Agent Studio server runs on this PC, so "Browse…"
// opens the native Windows/macOS folder dialog there. If that's not possible it falls back to an in-app browser.
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { IFolder, IX } from "./Icons";

type Listing = { path: string | null; parent: string | null; dirs: { name: string; path: string }[]; home: string };

function Browser({ start, onPick, onClose }: { start?: string; onPick: (p: string) => void; onClose: () => void }) {
  const [l, setL] = useState<Listing | null>(null);
  const [err, setErr] = useState("");
  const go = (p?: string | null) => api<Listing>(`/api/fs/dirs${p ? `?path=${encodeURIComponent(p)}` : ""}`).then((x) => { setL(x); setErr(""); }).catch((e) => setErr(e.message));
  if (!l && !err) go(start);
  return (
    <div className="backdrop" onClick={onClose}><div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: "min(640px,100%)" }}>
      <div className="row"><h3 style={{ margin: 0 }}>Choose a folder</h3><span className="grow" /><button className="icon-btn" onClick={onClose}><IX size={16} /></button></div>
      <div className="row" style={{ margin: "10px 0" }}>
        <button className="ghost small" onClick={() => go(null)}>This PC</button>
        <button className="ghost small" disabled={!l?.parent} onClick={() => go(l?.parent)}>↑ Up</button>
        <code className="small grow" style={{ wordBreak: "break-all" }}>{l?.path ?? "This PC"}</code>
      </div>
      {err && <div className="small err">{err}</div>}
      <div className="list" style={{ position: "static", maxHeight: 360, boxShadow: "none" }}>
        {(l?.dirs ?? []).map((d) => <div key={d.path} className="li" onDoubleClick={() => go(d.path)} onClick={() => go(d.path)}><IFolder size={15} /><span className="t">{d.name}</span></div>)}
        {l && l.dirs.length === 0 && <div className="small mut" style={{ padding: 10 }}>No sub-folders.</div>}
      </div>
      <div className="row" style={{ marginTop: 14, justifyContent: "flex-end" }}>
        <button className="ghost" onClick={onClose}>Cancel</button>
        <button className="acc" disabled={!l?.path} onClick={() => l?.path && onPick(l.path)}>Use this folder</button>
      </div>
    </div></div>
  );
}

export default function FolderPicker({ value, onChange, onPicked, placeholder, compact }: { value: string; onChange: (p: string) => void; onPicked?: () => void; placeholder?: string; compact?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [fallback, setFallback] = useState(false);
  const [msg, setMsg] = useState("");
  async function browse() {
    setBusy(true); setMsg("");
    try {
      const r = await api<{ path: string | null; cancelled: boolean }>("/api/fs/pick-folder", { method: "POST", body: JSON.stringify({ initial: value || null }) });
      if (r.path) { onChange(r.path); onPicked?.(); }
    } catch (e) {
      if (e instanceof ApiError && e.status === 501) setFallback(true);     // no native dialog available -> in-app browser
      else setMsg((e as Error).message);
    }
    setBusy(false);
  }
  return (
    <span style={{ display: "flex", flexDirection: "column", gap: 4, width: "100%" }}>
      <span style={{ display: "flex", gap: 6, minWidth: 0 }}>
        <input value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") onPicked?.(); }} style={{ flex: `1 1 ${compact ? 120 : 160}px`, minWidth: 0 }} />
        <button onClick={browse} disabled={busy} title="Pick a folder on this PC" style={{ display: "inline-flex", gap: 6, alignItems: "center", whiteSpace: "nowrap" }}>
          <IFolder size={14} />{busy ? "Choose in the dialog…" : "Browse…"}</button>
      </span>
      {busy && <span className="small mut">A folder dialog opened on your desktop (it may be behind this window).</span>}
      {msg && <span className="small err">{msg}</span>}
      {fallback && <Browser start={value || undefined} onClose={() => setFallback(false)} onPick={(p) => { onChange(p); setFallback(false); onPicked?.(); }} />}
    </span>
  );
}
