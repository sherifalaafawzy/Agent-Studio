"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import { IEdit, ISearch, ITrash, IX } from "@/components/Icons";

type Ent = { name: string; entityType: string; observations: string[]; relations: { from: string; to: string; relationType: string }[] };
type Summary = { file: string; entities: number; relations: number; observations: number; types: Record<string, number>; exists: boolean };

export default function LegacyGraph() {
  const [sum, setSum] = useState<Summary | null>(null);
  const [ents, setEnts] = useState<Ent[]>([]);
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [sel, setSel] = useState<Ent | null>(null);
  const [draft, setDraft] = useState<string[] | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, e] = await Promise.all([api<Summary>("/api/memory"), api<Ent[]>(`/api/memory/entities?limit=500${q ? `&q=${encodeURIComponent(q)}` : ""}${type ? `&type=${encodeURIComponent(type)}` : ""}`)]);
      setSum(s); setEnts(e); setErr("");
    } catch (e) { setErr((e as Error).message); }
  }, [q, type]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);
  useEffect(() => { if (sel) { const fresh = ents.find((x) => x.name === sel.name); if (fresh) setSel(fresh); } }, [ents]); // eslint-disable-line react-hooks/exhaustive-deps

  async function saveObs(name: string, obs: string[]) {
    try { await api(`/api/memory/entities/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ observations: obs }) }); setDraft(null); toast("Memory updated (a backup was kept)"); load(); }
    catch (e) { toast((e as Error).message, "err"); }
  }
  async function forget(name: string) {
    if (!confirm(`Forget “${name}” and its relations? A backup is kept.`)) return;
    await api(`/api/memory/entities/${encodeURIComponent(name)}`, { method: "DELETE" }).catch((e) => toast((e as Error).message, "err"));
    setSel(null); toast(`Forgot “${name}”`); load();
  }
  async function dropObs(name: string, i: number) {
    await api(`/api/memory/entities/${encodeURIComponent(name)}/observations/${i}`, { method: "DELETE" }).catch((e) => toast((e as Error).message, "err"));
    load();
  }
  async function clearAll() {
    const typed = prompt('This erases everything agents remember. A backup is kept. Type DELETE ALL MEMORY to confirm:');
    if (typed !== "DELETE ALL MEMORY") return;
    await api("/api/memory/clear", { method: "POST", body: JSON.stringify({ confirm: typed }) }).catch((e) => toast((e as Error).message, "err"));
    setSel(null); toast("Memory cleared (backup kept)"); load();
  }
  const types = Object.keys(sum?.types ?? {});

  return (
    <div>
      <div className="row" style={{ marginBottom: 6 }}><span className="grow" /><button className="ghost small" onClick={clearAll} disabled={!sum?.entities}>Clear all</button></div>
      <p className="page-sub">The older knowledge graph kept by the Memory server (agents that use it keep working). See it, correct it, or forget it. Every change keeps a backup file next to the memory file.</p>
      {err && <p className="err">{err}</p>}
      <div className="kpis" style={{ marginBottom: 14 }}>
        <div className="kpi"><div className="kpi-label">Things remembered</div><div className="kpi-value">{sum?.entities ?? "—"}</div></div>
        <div className="kpi"><div className="kpi-label">Facts</div><div className="kpi-value">{sum?.observations ?? "—"}</div></div>
        <div className="kpi"><div className="kpi-label">Links between them</div><div className="kpi-value">{sum?.relations ?? "—"}</div></div>
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <div style={{ position: "relative", flex: 1, minWidth: 180 }}>
          <input className="search" placeholder="Search memories and facts" value={q} onChange={(e) => setQ(e.target.value)} style={{ paddingLeft: 32, width: "100%" }} />
          <span style={{ position: "absolute", left: 10, top: 9, color: "var(--mut)" }}><ISearch size={15} /></span>
        </div>
        <select value={type} onChange={(e) => setType(e.target.value)}><option value="">All types</option>{types.map((t) => <option key={t} value={t}>{t} ({sum?.types[t]})</option>)}</select>
      </div>
      {sum && !sum.exists && <div className="empty">Nothing is remembered yet. Agents save memories when they are asked to, or when they decide something is worth keeping.</div>}
      <div className="two">
        <div className="list" data-tour="memory-list" style={{ maxHeight: "65vh" }}>
          {ents.map((e) => (
            <div key={e.name} className={`li ${sel?.name === e.name ? "on" : ""}`} onClick={() => { setSel(e); setDraft(null); }}>
              <span className="t">{e.name}</span><span className="s">{e.entityType}</span>
            </div>))}
          {sum?.exists && ents.length === 0 && <div className="small mut" style={{ padding: 10 }}>No matches.</div>}
        </div>
        <div>
          {!sel ? <div className="empty">Pick a memory to see its facts and links.</div> : (
            <div className="card">
              <div className="row"><b style={{ fontFamily: "var(--serif)", fontSize: 20, fontWeight: 500 }}>{sel.name}</b>
                <span className="pill">{sel.entityType}</span><span className="grow" />
                {draft === null && <button className="icon-btn" title="Edit facts" onClick={() => setDraft([...sel.observations])}><IEdit size={15} /></button>}
                <button className="icon-btn" title="Forget" onClick={() => forget(sel.name)}><ITrash size={15} /></button></div>
              {draft === null ? (
                <ul className="facts">
                  {sel.observations.length === 0 && <li className="mut">No facts.</li>}
                  {sel.observations.map((o, i) => (
                    <li key={i}><span>{o}</span><button className="icon-btn" title="Remove this fact" onClick={() => dropObs(sel.name, i)}><IX size={13} /></button></li>))}
                </ul>
              ) : (<>
                {draft.map((o, i) => (
                  <div key={i} className="row" style={{ marginTop: 6 }}>
                    <input value={o} onChange={(e) => setDraft(draft.map((x, j) => (j === i ? e.target.value : x)))} style={{ flex: 1 }} />
                    <button className="icon-btn" onClick={() => setDraft(draft.filter((_, j) => j !== i))}><IX size={13} /></button>
                  </div>))}
                <div className="row" style={{ marginTop: 10 }}>
                  <button className="ghost small" onClick={() => setDraft([...draft, ""])}>+ Add a fact</button><span className="grow" />
                  <button className="ghost small" onClick={() => setDraft(null)}>Cancel</button>
                  <button className="acc small" onClick={() => saveObs(sel.name, draft)}>Save</button>
                </div>
              </>)}
              {sel.relations.length > 0 && <>
                <div className="lbl" style={{ marginTop: 16 }}>Links</div>
                {sel.relations.map((r, i) => <div key={i} className="small mut">{r.from} <b>{r.relationType}</b> {r.to}</div>)}</>}
            </div>)}
        </div>
      </div>
      <style>{`.facts{list-style:none;margin:12px 0 0;padding:0}.facts li{display:flex;align-items:flex-start;gap:8px;padding:9px 0;border-top:1px solid var(--line);font-size:14px}.facts li span{flex:1}`}</style>
    </div>
  );
}
