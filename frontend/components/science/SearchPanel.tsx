"use client";
// Science Lab > Search: ask one open data source (read-only). Every answer shows where it came from and when; "Add as evidence" attaches a result to the open investigation.
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { Connector, DOMAIN_LABEL, ago, recordsTable, SciRecord, SearchOut } from "@/lib/science";
import UiBlock from "../uikit/UiBlock";
import { toast } from "../Nav";

const SAMPLE: Record<string, string> = { pubmed: "p53 tumour suppressor", pubchem: "aspirin", uniprot: "P04637", alphafold: "P04637", rcsb: "1TUP", arxiv: "gravitational waves", oeis: "1,1,2,3,5,8", crossref: "10.1038/nature14539" };

export default function SearchPanel({ connectors, investigationId, onEvidence }: { connectors: Connector[]; investigationId: string; onEvidence: () => void }) {
  const [source, setSource] = useState("pubmed");
  const [op, setOp] = useState("search");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<SearchOut | null>(null);
  const [view, setView] = useState<"cards" | "table">("cards");
  const spec = connectors.find((c) => c.id === source);
  const groups = useMemo(() => {
    const g: Record<string, Connector[]> = {};
    for (const c of connectors) (g[c.domain] ??= []).push(c);
    return g;
  }, [connectors]);
  const pick = (id: string) => { setSource(id); const c = connectors.find((x) => x.id === id); setOp(c && !c.ops.includes(op) ? c.ops[0] : op); };
  const blockedWhy = !spec ? "" : spec.status === "off" ? "This source is switched off in the Settings tab." : spec.status === "needs_key" ? `This source needs a free key (${spec.key_help || "add it in the Settings tab"}).` : "";
  const go = async () => {
    setBusy(true); setOut(null);
    try { setOut(await api<SearchOut>("/api/science/search", { method: "POST", body: JSON.stringify({ source, operation: op, query, limit: 8, investigation_id: investigationId }) })); }
    catch (e) { setOut({ ok: false, error: (e as Error).message }); }
    setBusy(false);
  };
  const add = async (r: SciRecord) => {
    try {
      await api(`/api/science/investigations/${investigationId}/evidence`, { method: "POST", body: JSON.stringify({ kind: "source", run_id: out?.run_id, ref: r.id }) });
      toast("Added as evidence"); onEvidence();
    } catch (e) { toast((e as Error).message, "err"); }
  };
  return (
    <div className="card" data-testid="science-search">
      <div className="card-head"><b>Search an open data source</b></div>
      <div className="card-desc">Read-only. Results are shown exactly as the source sent them, with when they were fetched. Nothing is guessed: if a source has nothing, it says so.</div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <label>Source
          <select value={source} aria-label="Data source" onChange={(e) => pick(e.target.value)}>
            {Object.entries(groups).map(([d, list]) => (
              <optgroup key={d} label={DOMAIN_LABEL[d] ?? d}>{list.map((c) => <option key={c.id} value={c.id}>{c.label}{c.status === "needs_key" ? " (needs a key)" : c.status === "off" ? " (off)" : ""}</option>)}</optgroup>
            ))}
          </select></label>
        {spec && spec.ops.length > 1 && (
          <label>Do
            <select value={op} aria-label="Operation" onChange={(e) => setOp(e.target.value)}>{spec.ops.map((o) => <option key={o} value={o}>{o === "search" ? "Search" : "Look up one id"}</option>)}</select></label>
        )}
        <label style={{ flex: 1, minWidth: 220 }}>{op === "lookup" ? "Identifier" : "Search for"}
          <input value={query} aria-label="Search text" placeholder={SAMPLE[source] ?? ""} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && query.trim() && !blockedWhy) void go(); }} /></label>
        <button className="acc" disabled={busy || !query.trim() || !!blockedWhy} onClick={go} style={{ alignSelf: "end" }}>{busy ? "Asking…" : "Ask"}</button>
      </div>
      {spec && <div className="small mut" style={{ marginTop: 4 }}>{spec.summary} Limit: {spec.limits} Licence: {spec.licence}{!spec.verified ? " (not yet verified from the source)" : ""}</div>}
      {blockedWhy && <div className="small" role="note" style={{ color: "var(--warn)" }}>{blockedWhy}</div>}
      {out && !out.ok && (
        <div className="err small" role="alert" data-testid="science-search-error" style={{ marginTop: 8 }}>
          {out.blocked ? <><b>Not available:</b> {out.safety?.reason} {out.safety?.help}</> : out.error}
          {out.error_kind === "rate_limited" && " Wait a minute and try again."}
        </div>
      )}
      {out?.ok && (
        <div data-testid="science-results" style={{ marginTop: 8 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div className="small mut" role="note">
              {out.records!.length ? `${out.records!.length} from ${out.provenance!.label}` : out.provenance!.note} · fetched {ago(out.provenance!.retrieved_at)} · {out.provenance!.requests ? `${out.provenance!.requests} request(s)` : "from the cache"} · {out.provenance!.licence}
            </div>
            {out.records!.length > 0 && <div className="row" style={{ gap: 4 }}>
              <button className={view === "cards" ? "acc small" : "ghost small"} onClick={() => setView("cards")}>Cards</button>
              <button className={view === "table" ? "acc small" : "ghost small"} onClick={() => setView("table")}>Table</button></div>}
          </div>
          {view === "table" && out.records!.length > 0 ? <UiBlock spec={recordsTable(out.records!)} /> : (
            <div role="list">{out.records!.map((r) => (
              <div key={r.id} role="listitem" className="card sci-rec">
                <div><b>{r.title || r.id}</b> <span className="chip">{r.kind || "record"}</span></div>
                {r.summary && <div className="small">{r.summary}</div>}
                <div className="small mut">{r.id}{r.fields.doi ? ` · doi:${String(r.fields.doi)}` : ""}</div>
                <div className="row" style={{ gap: 6, marginTop: 4 }}>
                  {r.url && /^https?:\/\//.test(r.url) && <a href={r.url} target="_blank" rel="noopener noreferrer" className="small">Open at the source ↗</a>}
                  {investigationId && <button className="ghost small" onClick={() => add(r)}>Add as evidence</button>}
                </div>
              </div>))}</div>
          )}
          {!investigationId && out.records!.length > 0 && <div className="small mut">Open or start an investigation on the left to attach results as evidence.</div>}
        </div>
      )}
    </div>
  );
}
