"use client";
// Science Lab: our own research workspace for biology, chemistry, physics and mathematics. Open data sources, exact local calculations, hypotheses and claims that carry
// their evidence, and a report in which every claim shows how well it is supported. It does not connect to Claude Science or Google's co-scientist.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Config, Connector, DOMAIN_LABEL, DOMAINS, Inv, InvFull, ago } from "@/lib/science";
import InfoTip from "@/components/InfoTip";
import { ConfirmDelete } from "@/components/Modal";
import { toast } from "@/components/Nav";
import ComponentsPanel from "@/components/science/ComponentsPanel";
import ComputePanel from "@/components/science/ComputePanel";
import InvestigationPanel from "@/components/science/InvestigationPanel";
import ReportPanel from "@/components/science/ReportPanel";
import SafetyPanel from "@/components/science/SafetyPanel";
import ScienceSettings from "@/components/science/ScienceSettings";
import SearchPanel from "@/components/science/SearchPanel";

type Tab = "investigation" | "search" | "compute" | "report" | "safety" | "components" | "settings";
const TABS: [Tab, string][] = [["investigation", "Investigation"], ["search", "Search"], ["compute", "Compute"], ["report", "Report"], ["safety", "Safety"], ["components", "Components"], ["settings", "Settings"]];
const DOT: Record<string, string> = { ready: "ok", needs_key: "warn", off: "mut" };

function NewForm({ onCreated, onCancel }: { onCreated: (i: Inv) => void; onCancel: () => void }) {
  const [title, setTitle] = useState("");
  const [domain, setDomain] = useState("cross");
  const [question, setQuestion] = useState("");
  const [err, setErr] = useState("");
  const make = async () => {
    try { onCreated(await api<Inv>("/api/science/investigations", { method: "POST", body: JSON.stringify({ title, domain, question }) })); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="card" data-testid="science-new">
      <label>Title<input value={title} aria-label="Investigation title" onChange={(e) => setTitle(e.target.value)} placeholder="Does aspirin inhibit COX-1?" /></label>
      <label>Area<select value={domain} aria-label="Area" onChange={(e) => setDomain(e.target.value)}>{DOMAINS.map((d) => <option key={d} value={d}>{DOMAIN_LABEL[d]}</option>)}</select></label>
      <label>Question<textarea value={question} rows={3} aria-label="Question" onChange={(e) => setQuestion(e.target.value)} placeholder="What do you want to find out?" /></label>
      {err && <div className="err small" role="alert">{err}</div>}
      <div className="row"><button className="acc" disabled={!title.trim()} onClick={make}>Start</button><button className="ghost" onClick={onCancel}>Cancel</button></div>
    </div>
  );
}

export default function SciencePage() {
  const [cfg, setCfg] = useState<Config | null>(null);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [list, setList] = useState<Inv[]>([]);
  const [domain, setDomain] = useState("");
  const [sel, setSel] = useState("");
  const [inv, setInv] = useState<InvFull | null>(null);
  const [tab, setTab] = useState<Tab>("search");
  const [creating, setCreating] = useState(false);
  const [del, setDel] = useState(false);
  const [err, setErr] = useState("");
  const [tick, setTick] = useState(0);
  const loadAll = useCallback(async () => {
    try {
      const [c, k, l] = await Promise.all([api<Config>("/api/science/config"), api<Connector[]>("/api/science/connectors"), api<Inv[]>("/api/science/investigations")]);
      setCfg(c); setConnectors(k); setList(l); setErr("");
    } catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { void loadAll(); }, [loadAll]);
  const loadInv = useCallback(async () => {
    if (!sel) { setInv(null); return; }
    try { setInv(await api<InvFull>(`/api/science/investigations/${sel}`)); setTick((t) => t + 1); void api<Inv[]>("/api/science/investigations").then(setList).catch(() => undefined); }
    catch (e) { setErr((e as Error).message); setInv(null); }
  }, [sel]);
  useEffect(() => { void loadInv(); }, [loadInv]);
  const open = (id: string) => { setSel(id); setTab("investigation"); };
  const shown = domain ? list.filter((i) => i.domain === domain) : list;
  const remove = async () => {
    try { await api(`/api/science/investigations/${sel}`, { method: "DELETE" }); setSel(""); setInv(null); setDel(false); toast("Investigation deleted"); void loadAll(); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="sci-page" data-testid="science-page">
      <div className="sci-rail" aria-label="Investigations">
        <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ margin: 0 }}>Science Lab <InfoTip id="science" /></h2>
          {cfg && <span className="chip" title="What the safety screen does. It can be raised, never lowered.">safety: {cfg.safety_level}</span>}</div>
        <div className="small mut">Open data, exact calculations, and claims that carry their evidence.</div>
        <div className="row" style={{ gap: 4, flexWrap: "wrap", margin: "8px 0" }} role="group" aria-label="Filter by area">
          {[["", "All"], ...DOMAINS.map((d) => [d, DOMAIN_LABEL[d]])].map(([d, label]) => <button key={d} aria-pressed={domain === d} className={domain === d ? "acc small" : "ghost small"} onClick={() => setDomain(d)}>{label}</button>)}
        </div>
        {creating ? <NewForm onCancel={() => setCreating(false)} onCreated={(i) => { setCreating(false); void loadAll(); open(i.id); }} /> : <button className="acc" onClick={() => setCreating(true)}>New investigation</button>}
        <div role="list" style={{ marginTop: 8 }}>
          {shown.map((i) => (
            <button key={i.id} role="listitem" className={`sci-inv ${sel === i.id ? "sel" : ""}`} aria-current={sel === i.id} onClick={() => open(i.id)}>
              <b>{i.title}</b><span className="small mut">{DOMAIN_LABEL[i.domain]} · {i.claims} claims · {ago(i.updated)}</span></button>))}
          {shown.length === 0 && <div className="small mut">No investigations{domain ? " in this area" : " yet"}. You can search and calculate without one; start one to keep evidence.</div>}
        </div>
        <details style={{ marginTop: 10 }}><summary className="small">Data sources</summary>
          <ul className="sci-list plain small">{connectors.map((c) => <li key={c.id}><span className={`sci-dot ${DOT[c.status]}`} aria-hidden="true" /> {c.label} <span className="mut">{c.status === "ready" ? "ready" : c.status === "needs_key" ? "needs a key" : "off"}</span></li>)}</ul></details>
      </div>
      <div className="sci-main">
        {err && <div className="err small" role="alert">{err}</div>}
        {cfg && !cfg.enabled && <div className="card" role="note">The Science Lab is switched off. Switch it on in the Settings tab.</div>}
        <div className="tabs sci-tabs" role="tablist" aria-label="Science Lab sections">
          {TABS.map(([t, label]) => <button key={t} role="tab" aria-selected={tab === t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{label}</button>)}
        </div>
        {tab === "investigation" && (inv ? <>
          <div className="row" style={{ gap: 6 }}>
            <button className="ghost small" onClick={async () => { try { await api(`/api/science/investigations/${inv.id}`, { method: "PUT", body: JSON.stringify({ status: inv.status === "open" ? "done" : "open" }) }); void loadInv(); } catch (e) { setErr((e as Error).message); } }}>{inv.status === "open" ? "Mark as done" : "Reopen"}</button>
            <button className="ghost small" onClick={() => setDel(true)}>Delete</button></div>
          <InvestigationPanel inv={inv} reload={loadInv} /></> : <div className="card">Pick an investigation on the left, or start one.</div>)}
        {tab === "search" && <SearchPanel connectors={connectors} investigationId={sel} onEvidence={loadInv} />}
        {tab === "compute" && <ComputePanel />}
        {tab === "report" && (sel ? <ReportPanel investigationId={sel} refreshKey={tick} /> : <div className="card">Pick an investigation to see its report.</div>)}
        {tab === "safety" && cfg && <SafetyPanel cfg={cfg} onChange={setCfg} />}
        {tab === "components" && <ComponentsPanel />}
        {tab === "settings" && cfg && <ScienceSettings cfg={cfg} connectors={connectors} onChange={(c, k) => { setCfg(c); setConnectors(k); }} />}
      </div>
      {del && inv && <ConfirmDelete what={`"${inv.title}" and its evidence`} onClose={() => setDel(false)} onConfirm={remove} />}
    </div>
  );
}
