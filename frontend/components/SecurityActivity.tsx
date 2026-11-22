"use client";
// What the guardrails caught and what the identity system issued, handed over or refused. Never the text itself or a token.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type G = { id: number; ts: number; stage: string; agent: string; profile: string; action: string; category: string; reason: string; chars: number };
type I = { id: number; ts: number; event: string; subject: string; actor: string; audience: string; scopes: string; detail: string };
const when = (t: number) => new Date(t * 1000).toLocaleString();

export default function SecurityActivity() {
  const [g, setG] = useState<G[]>([]);
  const [i, setI] = useState<I[]>([]);
  const [action, setAction] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { api<{ events: G[] }>(`/api/guardrails/events?limit=100${action ? `&action=${action}` : ""}`).then((r) => setG(r.events)).catch((e) => setErr(e.message)); }, [action]);
  useEffect(() => { api<{ events: I[] }>("/api/identity/events?limit=100").then((r) => setI(r.events)).catch((e) => setErr(e.message)); }, []);
  return (
    <div data-testid="activity">
      {err && <p className="err" role="alert">{err}</p>}
      <div className="card">
        <div className="card-head"><b>Guardrail activity</b><span className="grow" />
          <select aria-label="Show" value={action} onChange={(e) => setAction(e.target.value)}><option value="">Everything</option><option value="blocked">Blocked</option><option value="flagged">Flagged</option><option value="error">Could not answer</option></select></div>
        <p className="small mut">The text that was checked is never stored, only its length.</p>
        {g.length === 0 ? <p className="small mut">Nothing recorded.</p> : (
          <table className="tbl"><thead><tr><th>When</th><th>What</th><th>Where</th><th>Agent</th><th>Why</th></tr></thead><tbody>
            {g.map((e) => <tr key={e.id}><td className="small">{when(e.ts)}</td><td><span className={`pill ${e.action === "blocked" ? "bad" : "warn"}`}>{e.action}</span></td><td>{e.stage} · {e.profile}</td><td>{e.agent || "–"}</td><td className="small">{e.category ? `${e.category}: ` : ""}{e.reason} ({e.chars} characters)</td></tr>)}</tbody></table>)}
      </div>
      <div className="card">
        <div className="card-head"><b>Identity activity</b></div>
        {i.length === 0 ? <p className="small mut">Nothing recorded.</p> : (
          <table className="tbl"><thead><tr><th>When</th><th>What</th><th>Who</th><th>For</th><th>Detail</th></tr></thead><tbody>
            {i.map((e) => <tr key={e.id}><td className="small">{when(e.ts)}</td><td><span className={`pill ${e.event === "refused" ? "bad" : ""}`}>{e.event}</span></td>
              <td className="small" style={{ overflowWrap: "anywhere" }}>{e.subject}{e.actor ? ` ← ${e.actor}` : ""}</td><td className="small">{e.audience || "–"}{e.scopes ? ` · ${e.scopes}` : ""}</td><td className="small">{e.detail}</td></tr>)}</tbody></table>)}
      </div>
    </div>
  );
}
