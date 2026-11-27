"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import { ISearch } from "@/components/Icons";
import InfoTip from "@/components/InfoTip";
import LegacyGraph from "@/components/LegacyMemoryGraph";
import MemorySettings from "@/components/MemorySettings";
import { SCOPE_LABELS } from "@/components/MemoryUI";
import { useDirty } from "@/lib/dirty";

type Mem = { id: string; type: string; scope: string; scope_id: string; text: string; data: Record<string, any>; key: string; confidence: number; status: string;
  source: { agent?: string; session?: string; origin?: string }; created: number; updated: number; expires: number | null; embedded: boolean; hits: number; redacted: string[] };
type Stats = { total: number; pending: number; by_type: Record<string, number>; backend: string };
type Tab = "memories" | "review" | "notes" | "settings" | "graph";
const TABS: [Tab, string][] = [["memories", "Memories"], ["review", "Review"], ["notes", "Project notes"], ["settings", "Settings"], ["graph", "Older graph"]];
const TYPES: [string, string][] = [["semantic", "Fact"], ["episodic", "Past chat"], ["entity", "Person or thing"], ["procedural", "How-to"]];
const day = (t: number) => new Date(t * 1000).toLocaleDateString();

function MemCard({ m, onChanged, review }: { m: Mem; onChanged: () => void; review?: boolean }) {
  const [edit, setEdit] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try { await fn(); toast(ok); setEdit(null); onChanged(); } catch (e) { toast((e as Error).message, "err"); } finally { setBusy(false); }
  };
  const body = (text: string | undefined) => JSON.stringify(text === undefined ? {} : { text });
  return (
    <div className="card mem-card" data-testid="mem-card">
      {edit === null ? <div style={{ fontSize: 15 }}>{m.text}</div>
        : <textarea rows={3} value={edit} aria-label="Memory text" onChange={(e) => setEdit(e.target.value)} />}
      <div className="row small mut" style={{ flexWrap: "wrap", gap: 8, marginTop: 6 }}>
        <span className="pill">{TYPES.find(([k]) => k === m.type)?.[1] ?? m.type}</span>
        <span className="pill">{SCOPE_LABELS[m.scope] ?? m.scope}</span>
        {m.status !== "active" && <span className="pill warn">{m.status === "pending" ? "waiting for review" : m.status}</span>}
        <span title="How sure it is">{Math.round(m.confidence * 100)}% sure</span>
        {m.source.agent && <span title="Which agent saved it">by {m.source.agent}</span>}
        {m.source.session && <a href={`/?s=${m.source.session}`} title="The chat it came from">from a chat</a>}
        {m.source.origin && <span>({m.source.origin})</span>}
        {m.expires && <span title="It is forgotten automatically">expires {day(m.expires)}</span>}
        {m.redacted.length > 0 && <span title="Personal data was removed before saving">removed: {m.redacted.join(", ")}</span>}
        <span className="grow" />
        {review ? <>
          {edit === null && <button className="ghost small" onClick={() => setEdit(m.text)}>Edit</button>}
          <button className="acc small" disabled={busy} onClick={() => run(() => api(`/api/memories/${m.id}/approve`, { method: "POST", body: body(edit ?? undefined) }), "Memory approved")}>{edit === null ? "Approve" : "Save and approve"}</button>
          <button className="ghost small" disabled={busy} onClick={() => run(() => api(`/api/memories/${m.id}/reject`, { method: "POST" }), "Rejected")}>Reject</button>
        </> : <>
          {edit === null ? <button className="ghost small" onClick={() => setEdit(m.text)}>Edit</button>
            : <><button className="ghost small" onClick={() => setEdit(null)}>Cancel</button>
              <button className="acc small" disabled={busy} onClick={() => run(() => api(`/api/memories/${m.id}`, { method: "PUT", body: JSON.stringify({ text: edit }) }), "Memory updated")}>Save</button></>}
          {m.type === "procedural" && m.status === "active" && <button className="ghost small" title="Turn this how-to into a Skill agents can load"
            onClick={() => run(() => api(`/api/memories/${m.id}/promote`, { method: "POST" }), "Saved as a Skill (see Skills)")}>Make it a Skill</button>}
          <button className="ghost small" disabled={busy} onClick={() => { if (confirm("Forget this memory?")) run(() => api(`/api/memories/${m.id}`, { method: "DELETE" }), "Forgotten"); }}>Forget</button>
        </>}
      </div>
    </div>
  );
}

function Notes() {
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [file, setFile] = useState("AGENTS.md");
  const [err, setErr] = useState("");
  useEffect(() => { api<{ file: string; text: string }>("/api/memories/notes").then((r) => { setText(r.text); setSaved(r.text); setFile(r.file); }).catch((e) => setErr(e.message)); }, []);
  useDirty("memory-notes", text !== saved);
  const save = async () => { try { await api("/api/memories/notes", { method: "PUT", body: JSON.stringify({ text }) }); setSaved(text); toast("Notes saved"); } catch (e) { setErr((e as Error).message); } };
  return (
    <div>
      <p className="page-sub">A notes file called <code>{file}</code> in your workspace folder. Agents with “Project notes” read it at the start of every chat and may add to it. Edit it freely.</p>
      {err && <p className="err" role="alert">{err}</p>}
      <textarea rows={16} value={text} aria-label="Project notes" style={{ width: "100%", fontFamily: "var(--mono, monospace)" }} onChange={(e) => setText(e.target.value)} />
      <div className="row" style={{ marginTop: 8 }}><button className="pri" disabled={text === saved} onClick={save}>Save notes</button></div>
    </div>
  );
}

export default function MemoryPage() {
  const [tab, setTab] = useState<Tab>("memories");
  const [stats, setStats] = useState<Stats | null>(null);
  const [items, setItems] = useState<Mem[]>([]);
  const [pending, setPending] = useState<Mem[]>([]);
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [scope, setScope] = useState("");
  const [err, setErr] = useState("");
  const [add, setAdd] = useState({ type: "semantic", scope: "user", text: "" });
  const [forgetQ, setForgetQ] = useState("");
  const [forgetHits, setForgetHits] = useState<{ id: string; text: string }[] | null>(null);
  const file = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    const qs = new URLSearchParams({ status: "active", limit: "300", ...(q ? { q } : {}), ...(type ? { type } : {}), ...(scope ? { scope } : {}) });
    try {
      const [s, a, p] = await Promise.all([api<Stats>("/api/memories/stats"), api<Mem[]>(`/api/memories?${qs}`), api<Mem[]>("/api/memories?status=pending&limit=300")]);
      setStats(s); setItems(a); setPending(p); setErr("");
    } catch (e) { setErr((e as Error).message); }
  }, [q, type, scope]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);
  useEffect(() => { const h = window.location.hash.slice(1) as Tab; if (TABS.some(([k]) => k === h)) setTab(h); }, []);
  const go = (t: Tab) => { setTab(t); history.replaceState(null, "", `#${t}`); };

  const create = async () => {
    try { await api("/api/memories", { method: "POST", body: JSON.stringify(add) }); setAdd({ ...add, text: "" }); toast("Remembered"); load(); }
    catch (e) { toast((e as Error).message, "err"); }
  };
  const preview = async () => {
    try { setForgetHits((await api<{ forgotten: { id: string; text: string }[] }>("/api/memories/forget", { method: "POST", body: JSON.stringify({ query: forgetQ, dry_run: true }) })).forgotten); }
    catch (e) { toast((e as Error).message, "err"); }
  };
  const forget = async () => {
    try { await api("/api/memories/forget", { method: "POST", body: JSON.stringify({ query: forgetQ, dry_run: false }) }); setForgetHits(null); setForgetQ(""); toast("Forgotten"); load(); }
    catch (e) { toast((e as Error).message, "err"); }
  };
  const download = async () => {
    try {
      const url = URL.createObjectURL(new Blob([JSON.stringify(await api("/api/memories/export"), null, 2)], { type: "application/json" }));
      const a = document.createElement("a"); a.href = url; a.download = "agent-studio-memories.json"; a.click(); URL.revokeObjectURL(url);
    } catch (e) { toast((e as Error).message, "err"); }
  };
  const upload = async (f: File | undefined) => {
    if (!f) return;
    try {
      const r = await api<{ added: number; skipped: number; errors: string[] }>("/api/memories/import", { method: "POST", body: await f.text() });
      toast(`${r.added} added, ${r.skipped} skipped${r.errors.length ? `, ${r.errors.length} could not be read` : ""}`); load();
    } catch (e) { toast((e as Error).message, "err"); }
    if (file.current) file.current.value = "";
  };
  const wipe = async () => {
    const typed = prompt("This erases every memory (here and in any cloud backend). Type DELETE ALL MEMORY to confirm:");
    if (typed !== "DELETE ALL MEMORY") return;
    try { await api("/api/memories/wipe", { method: "POST", body: JSON.stringify({ confirm: typed }) }); toast("All memories erased"); load(); } catch (e) { toast((e as Error).message, "err"); }
  };

  return (
    <div>
      <div className="page-head"><h2>Memory</h2><InfoTip id="memory" /><span className="grow" />
        {stats && <span className="pill" title="Where memories are kept">{stats.backend === "local" ? "on this PC" : stats.backend}</span>}</div>
      <p className="page-sub">What agents remember between chats. See it, correct it, approve new memories, or forget anything.</p>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="tabs" role="tablist">{TABS.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => go(k)}>{label}{k === "review" && (stats?.pending ?? 0) > 0 && <span className="count">{stats?.pending}</span>}</button>))}</div>

      {tab === "memories" && <>
        <div className="kpis" style={{ marginBottom: 14 }}>
          <div className="kpi"><div className="kpi-label">Remembered</div><div className="kpi-value">{stats?.total ?? "—"}</div></div>
          {TYPES.map(([k, label]) => <div key={k} className="kpi"><div className="kpi-label">{label}s</div><div className="kpi-value">{stats?.by_type[k] ?? 0}</div></div>)}
        </div>
        <div className="row" style={{ marginBottom: 12, flexWrap: "wrap" }}>
          <div style={{ position: "relative", flex: 1, minWidth: 180 }}>
            <input className="search" aria-label="Search memories" placeholder="Search memories" value={q} onChange={(e) => setQ(e.target.value)} style={{ paddingLeft: 32, width: "100%" }} />
            <span style={{ position: "absolute", left: 10, top: 9, color: "var(--mut)" }}><ISearch size={15} /></span></div>
          <select aria-label="Filter by kind" value={type} onChange={(e) => setType(e.target.value)}><option value="">All kinds</option>{TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <select aria-label="Filter by who can see it" value={scope} onChange={(e) => setScope(e.target.value)}><option value="">Everyone&apos;s and mine</option>{Object.entries(SCOPE_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        </div>
        <div className="card">
          <div className="card-head"><b>Add a memory</b></div>
          <div className="row" style={{ flexWrap: "wrap", alignItems: "flex-end" }}>
            <label>Kind<select value={add.type} onChange={(e) => setAdd({ ...add, type: e.target.value })}>{TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label>Who can see it<select value={add.scope} onChange={(e) => setAdd({ ...add, scope: e.target.value })}>
              {Object.entries(SCOPE_LABELS).filter(([k]) => k !== "agent").map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label className="grow" style={{ minWidth: 200 }}>What to remember<input value={add.text} placeholder="e.g. Our invoices are sent from Xero" onChange={(e) => setAdd({ ...add, text: e.target.value })} /></label>
            <button className="pri" disabled={!add.text.trim()} onClick={create}>Remember</button>
          </div>
        </div>
        {stats && stats.total === 0 && <div className="empty">Nothing is remembered yet. Give an agent memory in its settings, or add a memory above.</div>}
        <div data-tour="memory-list">{items.map((m) => <MemCard key={m.id} m={m} onChanged={load} />)}
          {stats && stats.total > 0 && items.length === 0 && <div className="small mut" style={{ padding: 10 }}>No matches.</div>}</div>
        <div className="card">
          <div className="card-head"><b>Forget by description</b></div>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <input className="grow" aria-label="What to forget" placeholder="e.g. the billing system" value={forgetQ} onChange={(e) => { setForgetQ(e.target.value); setForgetHits(null); }} />
            <button disabled={!forgetQ.trim()} onClick={preview}>Show what would be forgotten</button></div>
          {forgetHits && <div style={{ marginTop: 8 }}>
            {forgetHits.length === 0 ? <p className="small mut">Nothing matches that.</p> : <>
              {forgetHits.map((h) => <div key={h.id} className="small">• {h.text}</div>)}
              <button className="acc small" style={{ marginTop: 6 }} onClick={forget}>Forget these {forgetHits.length}</button></>}</div>}
        </div>
        <div className="row" style={{ marginTop: 12, flexWrap: "wrap" }}>
          <button onClick={download}>Export memories</button>
          <button onClick={() => file.current?.click()}>Import memories</button>
          <input ref={file} type="file" accept="application/json" hidden aria-label="Import file" onChange={(e) => upload(e.target.files?.[0])} />
          <button onClick={async () => { try { const r = await api<{ expired: number; duplicates_merged: number }>("/api/memories/maintain", { method: "POST" }); toast(`Tidied: ${r.expired} expired, ${r.duplicates_merged} duplicates merged`); load(); } catch (e) { toast((e as Error).message, "err"); } }}>Tidy up now</button>
          <span className="grow" /><button className="ghost small" onClick={wipe} disabled={!stats?.total}>Erase everything</button>
        </div>
      </>}

      {tab === "review" && <>
        <p className="page-sub">New memories agents picked up on their own wait here. Nothing is used until you approve it. <InfoTip id="memory-review" /></p>
        {pending.length === 0 && <div className="empty">Nothing is waiting for your review.</div>}
        {pending.map((m) => <MemCard key={m.id} m={m} review onChanged={load} />)}
      </>}
      {tab === "notes" && <Notes />}
      {tab === "settings" && <MemorySettings />}
      {tab === "graph" && <LegacyGraph />}
    </div>
  );
}
