"use client";
// Science Lab > Investigation: the question, competing hypotheses (ranked by pairwise judgements), the claims with the label their evidence earns, and the evidence itself.
// A claim's label is worked out by the server from its evidence; nobody (not the model, not you) picks it. A claim with no evidence reads "unsupported".
import { useState } from "react";
import { api } from "@/lib/api";
import { Claim, Evidence, Hypothesis, InvFull, labelShow, VERIFIED_TEXT, when } from "@/lib/science";
import { toast } from "../Nav";

function Hypotheses({ inv, reload }: { inv: InvFull; reload: () => void }) {
  const [text, setText] = useState("");
  const [pick, setPick] = useState<string[]>([]);
  const call = async (path: string, body: unknown) => { try { await api(`/api/science/investigations/${inv.id}/${path}`, { method: "POST", body: JSON.stringify(body) }); reload(); return true; } catch (e) { toast((e as Error).message, "err"); return false; } };
  const toggle = (id: string) => setPick((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id].slice(-2)));
  return (
    <div className="card" data-testid="science-hypotheses">
      <div className="card-head"><b>Hypotheses</b></div>
      <div className="card-desc">Write the competing explanations, then judge pairs: which is better supported by the evidence? Scores are Elo ratings, so they are judgements, not measurements.</div>
      {inv.hypotheses_list.length === 0 && <div className="small mut">No hypotheses yet.</div>}
      <ol className="sci-list">{inv.hypotheses_list.map((h: Hypothesis) => (
        <li key={h.id}><label className="check"><input type="checkbox" checked={pick.includes(h.id)} aria-label={`Select hypothesis: ${h.text}`} onChange={() => toggle(h.id)} /> {h.text}</label>
          <span className="small mut"> · Elo {h.elo} · {h.wins} won, {h.losses} lost</span></li>))}</ol>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <input value={text} aria-label="New hypothesis" placeholder="A candidate explanation, one sentence" style={{ flex: 1, minWidth: 200 }} onChange={(e) => setText(e.target.value)} />
        <button className="ghost" disabled={!text.trim()} onClick={async () => { if (await call("hypotheses", { text })) setText(""); }}>Add</button>
        <button className="acc" disabled={pick.length !== 2} title="The first one you ticked is the one better supported by the evidence"
          onClick={async () => { if (await call("compare", { winner: pick[0], loser: pick[1] })) setPick([]); }}>First beats second</button>
      </div>
    </div>
  );
}

function ClaimRow({ c, byId, onDelete }: { c: Claim; byId: Record<string, Evidence>; onDelete: () => void }) {
  const s = labelShow(c.label);
  return (
    <li className="card sci-claim" data-testid={`claim-${c.label}`}>
      <div><span className={`sci-badge ${s.tone}`} title={c.label_text}><span aria-hidden="true">{s.icon}</span> {c.label_text}</span></div>
      <div>{c.text}</div>
      {c.evidence.map((id) => byId[id] && <div key={id} className="small mut">– {byId[id].title || byId[id].ref || byId[id].source} ({byId[id].source}{byId[id].kind === "source" ? `, ${VERIFIED_TEXT[byId[id].verified]}` : `, ${byId[id].kind}`})</div>)}
      {c.flags.map((f) => <div key={f} className="small" role="note" style={{ color: "var(--warn-text)" }}>⚠ {f}</div>)}
      <button className="ghost small" onClick={onDelete}>Remove claim</button>
    </li>
  );
}

export default function InvestigationPanel({ inv, reload }: { inv: InvFull; reload: () => void }) {
  const [text, setText] = useState("");
  const [ids, setIds] = useState<string[]>([]);
  const [conflict, setConflict] = useState(false);
  const [guess, setGuess] = useState("");
  const [busy, setBusy] = useState(false);
  const byId = Object.fromEntries(inv.evidence_list.map((e) => [e.id, e]));
  const addClaim = async () => {
    try { await api(`/api/science/investigations/${inv.id}/claims`, { method: "POST", body: JSON.stringify({ text, evidence_ids: ids, conflicting: conflict }) }); setText(""); setIds([]); setConflict(false); reload(); }
    catch (e) { toast((e as Error).message, "err"); }
  };
  const verify = async () => {
    setBusy(true);
    try { const r = await api<{ verified: number }[]>(`/api/science/investigations/${inv.id}/verify`, { method: "POST" }); toast(r.length ? `Checked ${r.length} citation(s)` : "No DOIs or PMIDs to check"); reload(); }
    catch (e) { toast((e as Error).message, "err"); }
    setBusy(false);
  };
  return (
    <div data-testid="science-investigation">
      <div className="card">
        <div className="card-head"><b>{inv.title}</b> <span className="chip">{inv.status}</span></div>
        {inv.question ? <p>{inv.question}</p> : <div className="small mut">No question written yet.</div>}
        <div className="small mut">{inv.evidence} evidence · {inv.claims} claims · {inv.source_calls} data-source calls</div>
      </div>
      <Hypotheses inv={inv} reload={reload} />
      <div className="card">
        <div className="card-head"><b>Claims</b></div>
        {inv.claims_list.length === 0 ? <div className="small mut">No claims yet. A claim needs evidence from a search or a computation to be more than "unsupported".</div> :
          <ul className="sci-list plain">{inv.claims_list.map((c) => <ClaimRow key={c.id} c={c} byId={byId} onDelete={async () => { try { await api(`/api/science/investigations/${inv.id}/claims/${c.id}`, { method: "DELETE" }); reload(); } catch (e) { toast((e as Error).message, "err"); } }} />)}</ul>}
        <fieldset className="sci-form"><legend className="small">Record a claim</legend>
          <input value={text} aria-label="Claim" placeholder="One sentence" onChange={(e) => setText(e.target.value)} />
          {inv.evidence_list.length > 0 && <div className="small">Rests on:{inv.evidence_list.map((e) => (
            <label key={e.id} className="check"><input type="checkbox" checked={ids.includes(e.id)} aria-label={`Evidence ${e.title || e.ref || e.source}`} onChange={() => setIds((l) => (l.includes(e.id) ? l.filter((x) => x !== e.id) : [...l, e.id]))} /> {e.title || e.ref || e.source}</label>))}</div>}
          <label className="check"><input type="checkbox" checked={conflict} onChange={(e) => setConflict(e.target.checked)} /> The evidence disagrees</label>
          <button className="acc" disabled={!text.trim()} onClick={addClaim}>Add claim</button>
        </fieldset>
      </div>
      <div className="card">
        <div className="card-head"><b>Evidence</b></div>
        {inv.evidence_list.length === 0 ? <div className="small mut">Nothing yet. Search on the Search tab and use "Add as evidence", or compute something.</div> : (
          <div style={{ overflowX: "auto" }}><table className="sci-kv" aria-label="Evidence"><thead><tr><th>Source</th><th>Reference</th><th>Query</th><th>Retrieved</th><th>Check</th></tr></thead>
            <tbody>{inv.evidence_list.map((e) => (
              <tr key={e.id}><td>{e.source}</td><td>{e.url && /^https?:\/\//.test(e.url) ? <a href={e.url} target="_blank" rel="noopener noreferrer">{e.ref || e.title || e.url}</a> : e.ref || e.title}</td><td>{e.query || e.value}</td><td>{when(e.retrieved_at)}</td>
                <td>{e.kind === "source" ? VERIFIED_TEXT[e.verified] : e.kind}</td></tr>))}</tbody></table></div>)}
        <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
          <button className="ghost" disabled={busy || inv.evidence_list.length === 0} onClick={verify}>{busy ? "Checking…" : "Check citations (Crossref, PubMed)"}</button>
          <input value={guess} aria-label="Your own reasoning" placeholder="Add your own reasoning (kept as inference, not a finding)" style={{ flex: 1, minWidth: 200 }} onChange={(e) => setGuess(e.target.value)} />
          <button className="ghost" disabled={!guess.trim()} onClick={async () => { try { await api(`/api/science/investigations/${inv.id}/evidence`, { method: "POST", body: JSON.stringify({ kind: "inference", quote: guess, title: guess.slice(0, 80) }) }); setGuess(""); reload(); } catch (e) { toast((e as Error).message, "err"); } }}>Add as inference</button>
        </div>
      </div>
      {inv.runs_list.length > 0 && (
        <details className="card"><summary><b>Record of every call and run ({inv.runs_list.length})</b></summary>
          <ul className="sci-list plain small">{inv.runs_list.map((r) => <li key={r.id}>{when(r.created)} · {r.tool} · {r.record.ok === false ? `failed: ${r.record.error}` : r.record.query ?? `code ${String(r.record.code_sha256 ?? "").slice(0, 10)}`}</li>)}</ul></details>)}
    </div>
  );
}
