"use client";
import { useCallback, useEffect, useState } from "react";
import { api, ProviderBilling, usd } from "@/lib/api";

type Summary = { providers: ProviderBilling[]; by_model: any[]; total_spent_usd: number };

export default function BillingPage() {
  const [s, setS] = useState<Summary | null>(null);
  const [ledger, setLedger] = useState<any[]>([]);
  const [ext, setExt] = useState<any[]>([]);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      setS(await api("/api/billing/summary"));
      setLedger(await api("/api/billing/ledger?limit=50"));
      setExt(await api("/api/acp/usage"));
      setErr("");
    } catch (e) { setErr("Backend offline: " + (e as Error).message); }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [load]);

  return (
    <div>
      <div className="row"><h2 style={{ margin: 0 }}>Billing</h2><span className="mut small">live · refreshes every 3s · caps enforced per provider before each call</span></div>
      {err && <p className="err">{err}</p>}
      {s && <>
        <h3>Spend vs cap — total {usd(s.total_spent_usd)}</h3>
        <div className="grid">
          {s.providers.map((p) => {
            const pct = Math.min(100, p.pct_used);
            return (
              <div className="card" key={p.provider}>
                <div className="row"><b>{p.label}</b><span className={`pill ${p.enabled ? "ok" : ""}`}>{p.enabled ? "on" : "off"}</span></div>
                <div className="bar" style={{ margin: "8px 0" }}><i className={pct > 90 ? "b" : pct > 70 ? "w" : ""} style={{ width: pct + "%" }} /></div>
                <div className="small">{usd(p.spent_usd)} of {usd(p.cap_usd)} <span className="mut">({p.cap_period})</span></div>
                <div className="small mut">remaining {usd(p.remaining_usd)}{p.reserved_usd > 0 ? ` · in flight ${usd(p.reserved_usd)}` : ""}</div>
                <div className="small mut">{p.calls} calls · <span className={p.refused ? "err" : ""}>{p.refused} refused</span></div>
              </div>
            );
          })}
        </div>
        <h3>By model</h3>
        <div className="card"><table><thead><tr><th>Provider</th><th>Model</th><th>Calls</th><th>In tok</th><th>Out tok</th><th>Cost</th></tr></thead>
          <tbody>{s.by_model.map((r, i) => <tr key={i}><td>{r.provider}</td><td>{r.model}</td><td>{r.calls}</td><td>{r.input_tokens}</td><td>{r.output_tokens}</td><td>{usd(r.cost_usd)}</td></tr>)}
            {s.by_model.length === 0 && <tr><td colSpan={6} className="mut">No spend yet.</td></tr>}</tbody></table></div>
        <h3>External agents (ACP) — reported by the agents, NOT capped</h3>
        <div className="card"><p className="mut small" style={{ marginTop: 0 }}>These agents (Claude Code, Cursor, Antigravity, Grok Build…) bill on their own accounts. Agent Studio can only show what they report after each turn; set limits with the vendor.</p>
          <table><thead><tr><th>Agent</th><th>Turns</th><th>In tok</th><th>Out tok</th><th>Reported cost</th><th>Turns w/o usage</th></tr></thead>
          <tbody>{ext.map((r) => <tr key={r.agent}><td>{r.agent}</td><td>{r.turns}</td><td>{r.input_tokens ?? "—"}</td><td>{r.output_tokens ?? "—"}</td>
            <td>{r.reported_cost != null ? `${r.currency ?? ""} ${Number(r.reported_cost).toFixed(4)}` : "not reported"}</td><td>{r.turns_without_usage}</td></tr>)}
            {ext.length === 0 && <tr><td colSpan={6} className="mut">No external-agent turns yet.</td></tr>}</tbody></table></div>
        <h3>Ledger</h3>
        <div className="card"><table><thead><tr><th>Time</th><th>Provider/model</th><th>Result</th><th>Cost</th><th>Projected worst-case</th><th>Note</th></tr></thead>
          <tbody>{ledger.map((r) => <tr key={r.id}><td>{new Date(r.ts * 1000).toLocaleTimeString()}</td><td>{r.provider}/{r.model}</td>
            <td><span className={`pill ${r.kind === "call" ? "ok" : "bad"}`}>{r.kind === "call" ? "call" : "refused"}</span></td>
            <td>{usd(r.cost_usd)}</td><td>{usd(r.projected_usd)}</td><td className="mut">{r.note ?? ""}{r.price_known ? "" : " · price unknown"}</td></tr>)}
            {ledger.length === 0 && <tr><td colSpan={6} className="mut">Empty.</td></tr>}</tbody></table></div>
      </>}
    </div>
  );
}
