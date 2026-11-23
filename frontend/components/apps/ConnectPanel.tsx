"use client";
// Connect the app to your agents (local or in the cloud) and to your data sources in one click each. An agent is listed in the app's manifest so the page may call it (and only
// those); a data source is listed so the page may READ it (SELECT only, limited rows, every query audited). Turning on an agent's API access is its own explicit click.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { AgentConn, AppRow, Connections, DataConn } from "@/lib/apps";
import { toast } from "@/components/Nav";

const RUNS: Record<string, string> = { local: "runs here", docker: "runs in Docker" };

export default function ConnectPanel({ app, onChanged }: { app: AppRow; onChanged: () => void }) {
  const [c, setC] = useState<Connections | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const load = useCallback(() => api<Connections>(`/api/apps/${app.id}/connections`).then((r) => { setC(r); setErr(""); }).catch((e) => setErr(e.message)), [app.id]);
  useEffect(() => { load(); }, [load]);
  if (!c) return <div className="mut small">{err ? <span role="alert" className="err">{err}</span> : "Loading connections…"}</div>;

  const act = async (key: string, fn: () => Promise<unknown>, done: string) => {
    setBusy(key);
    try { await fn(); toast(done); onChanged(); await load(); setErr(""); } catch (e) { setErr((e as Error).message); }
    setBusy("");
  };
  const connectAgent = (a: AgentConn, enable: boolean) => act(`a:${a.id}`, () => api(`/api/apps/${app.id}/connect/agent`, { method: "POST", body: JSON.stringify({ id: a.id, enable_api: enable }) }), `${a.name} connected`);
  const dropAgent = (a: AgentConn) => act(`a:${a.id}`, () => api(`/api/apps/${app.id}/connect/agent/${a.id}`, { method: "DELETE" }), `${a.name} disconnected`);
  const connectData = (d: DataConn) => act(`d:${d.id}`, () => api(`/api/apps/${app.id}/connect/data`, { method: "POST", body: JSON.stringify({ id: d.id }) }), `${d.label} connected`);
  const dropData = (d: DataConn) => act(`d:${d.id}`, () => api(`/api/apps/${app.id}/connect/data/${d.id}`, { method: "DELETE" }), `${d.label} disconnected`);
  return (
    <div data-testid="connect-panel">
      {err && <div className="err small" role="alert">{err}</div>}
      <h4 style={{ margin: "6px 0" }}>Agents</h4>
      <div className="small mut">The page can call only the agents connected here, using this app&apos;s own key. Cloud agents work the same way as local ones.</div>
      <ul className="conn-list" aria-label="Agents">
        {c.agents.length === 0 && <li className="mut small">You have no agents yet.</li>}
        {c.agents.map((a) => (
          <li key={a.id} className="conn-row">
            <div className="grow"><b>{a.name}</b> <span className="chip small">{RUNS[a.runtime] ?? `runs on ${a.runtime}`}</span>{a.connected && <span className="chip small" style={{ color: "var(--ok)" }}>connected</span>}
              {a.description && <div className="small mut">{a.description}</div>}
              {a.connected && !a.api_access && <div className="small" role="note" style={{ color: "var(--warn)" }}>API access is off for this agent, so the page cannot call it yet.</div>}</div>
            {a.connected
              ? <><button disabled={busy === `a:${a.id}`} onClick={() => dropAgent(a)} aria-label={`Disconnect ${a.name}`}>Disconnect</button>
                {!a.api_access && <button className="acc" disabled={busy === `a:${a.id}`} onClick={() => connectAgent(a, true)} aria-label={`Turn on API access for ${a.name}`}>Turn on API access</button>}</>
              : <><button className="acc" disabled={busy === `a:${a.id}`} onClick={() => connectAgent(a, false)} aria-label={`Connect ${a.name}`}>Connect</button>
                {!a.api_access && <button disabled={busy === `a:${a.id}`} onClick={() => connectAgent(a, true)} aria-label={`Connect ${a.name} and turn on API access`}>Connect and turn on API access</button>}</>}
          </li>
        ))}
      </ul>
      <h4 style={{ margin: "14px 0 6px" }}>Data sources</h4>
      <div className="small mut">The page can read only what is connected here: SELECT queries only, a limited number of rows, and every query is written to the audit log.</div>
      <ul className="conn-list" aria-label="Data sources">
        {c.data.length === 0 && <li className="mut small">No data source is set up yet. <a href="/settings/">Add one in Settings</a> (Data).</li>}
        {c.data.map((d) => (
          <li key={d.id} className="conn-row">
            <div className="grow"><b>{d.label}</b> <span className="chip small">{d.kind}</span>{d.connected && <span className="chip small" style={{ color: "var(--ok)" }}>connected</span>}{!d.enabled && <span className="chip small">switched off</span>}
              {d.description && <div className="small mut">{d.description}</div>}</div>
            {d.connected
              ? <button disabled={busy === `d:${d.id}`} onClick={() => dropData(d)} aria-label={`Disconnect ${d.label}`}>Disconnect</button>
              : <button className="acc" disabled={!d.enabled || busy === `d:${d.id}`} onClick={() => connectData(d)} aria-label={`Connect ${d.label}`}>Connect</button>}
          </li>
        ))}
      </ul>
    </div>
  );
}
