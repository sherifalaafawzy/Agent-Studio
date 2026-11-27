"use client";
// Workflow settings (webhooks, limits, what the HTTP request box may reach) and the credentials that box signs in with (write-only).
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import InfoTip from "@/components/InfoTip";
import { Modal } from "@/components/Modal";
import { toast } from "@/components/Nav";

type Settings = { webhooks_enabled: boolean; schedules_enabled?: boolean; hook_rate_per_min: number; hook_max_body_kb: number; hook_wait_max_s: number; max_nodes: number; run_retention: number; http_timeout_s: number; http_max_kb: number; http_allow_hosts: string[] };
type Cred = { id: string; label: string; kind: string; header: string; set: boolean };
const NUMBERS: [keyof Settings, string, number, number][] = [
  ["hook_rate_per_min", "Webhook calls per minute", 1, 600], ["hook_max_body_kb", "Largest webhook call (KB)", 1, 4096], ["hook_wait_max_s", "How long a webhook caller may wait (seconds)", 0, 120],
  ["max_nodes", "Most boxes in one workflow", 10, 200], ["run_retention", "Finished runs kept per workflow", 5, 1000], ["http_timeout_s", "HTTP request time limit (seconds)", 1, 120], ["http_max_kb", "Largest HTTP answer read (KB)", 1, 4096],
];

export default function WorkflowSettings({ onClose }: { onClose: () => void }) {
  const [s, setS] = useState<Settings | null>(null);
  const [hosts, setHosts] = useState("");
  const [creds, setCreds] = useState<Cred[]>([]);
  const [err, setErr] = useState("");
  const [nc, setNc] = useState({ id: "", label: "", kind: "bearer", header: "", value: "" });
  useEffect(() => {
    api<{ settings: Settings; credentials: Cred[] }>("/api/workflows/settings").then((r) => { setS(r.settings); setHosts(r.settings.http_allow_hosts.join("\n")); setCreds(r.credentials); }).catch((e) => setErr(e.message));
  }, []);
  const save = async (cur: Settings) => {
    setErr("");
    try { const r = await api<{ settings: Settings }>("/api/workflows/settings", { method: "PUT", body: JSON.stringify({ ...cur, http_allow_hosts: hosts.split("\n").map((h) => h.trim()).filter(Boolean) }) }); setS(r.settings); setHosts(r.settings.http_allow_hosts.join("\n")); toast("Workflow settings saved"); }
    catch (e) { setErr((e as Error).message); }
  };
  const addCred = async () => {
    setErr("");
    try {
      const c = await api<Cred>("/api/workflows/credentials", { method: "POST", body: JSON.stringify(nc) });
      setCreds((cs) => [...cs.filter((x) => x.id !== c.id), c].sort((a, b) => a.id.localeCompare(b.id))); setNc({ id: "", label: "", kind: "bearer", header: "", value: "" }); toast("Credential saved");
    } catch (e) { setErr((e as Error).message); }
  };
  const delCred = async (id: string) => {
    setErr("");
    try { await api(`/api/workflows/credentials/${id}`, { method: "DELETE" }); setCreds((cs) => cs.filter((c) => c.id !== id)); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <Modal title="Workflow settings" onClose={onClose} wide>
      <div className="row" style={{ marginTop: 4 }}><span className="small mut">Webhooks, limits and what the HTTP request box may reach.</span><InfoTip id="wf-settings" /></div>
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", margin: "6px 0" }}>{err}</div>}
      {!s ? <div className="small mut">Loading…</div> : (
        <div className="stack" data-testid="wf-settings">
          <label className="check"><input type="checkbox" checked={s.webhooks_enabled} onChange={(e) => setS({ ...s, webhooks_enabled: e.target.checked })} /> Let Webhook boxes be called from outside (off until you switch it on)</label>
          <label className="check"><input type="checkbox" checked={s.schedules_enabled ?? true} onChange={(e) => setS({ ...s, schedules_enabled: e.target.checked })} /> Let scheduled workflows run (switch off to pause every schedule at once)</label>
          <div className="grid">{NUMBERS.map(([k, label, lo, hi]) => (
            <label key={k}>{label}<input type="number" min={lo} max={hi} value={s[k] as number} aria-label={label} onChange={(e) => setS({ ...s, [k]: Number(e.target.value) })} /></label>))}</div>
          <label>Private hosts the HTTP request box may reach (one per line, like api.internal.example)<textarea rows={3} value={hosts} aria-label="Allowed hosts" onChange={(e) => setHosts(e.target.value)} /></label>
          <div className="row"><button className="pri" onClick={() => save(s)}>Save settings</button></div>
          <hr />
          <b>Credentials for HTTP requests</b>
          <div className="small mut">Kept apart from workflows and never shown again once saved.</div>
          {creds.length === 0 ? <div className="small mut" data-testid="no-creds">No credentials yet.</div> : creds.map((c) => (
            <div key={c.id} className="row small" data-testid="cred"><b>{c.label}</b><span className="mut">{c.id} · {c.kind}{c.header ? ` (${c.header})` : ""}</span><span className="grow" />
              <button className="small" aria-label={`Delete credential ${c.id}`} onClick={() => delCred(c.id)}>Delete</button></div>))}
          <div className="grid">
            <label>Id<input value={nc.id} aria-label="Credential id" placeholder="crm_token" onChange={(e) => setNc({ ...nc, id: e.target.value })} /></label>
            <label>Name<input value={nc.label} aria-label="Credential name" onChange={(e) => setNc({ ...nc, label: e.target.value })} /></label>
            <label>Kind<select value={nc.kind} aria-label="Credential kind" onChange={(e) => setNc({ ...nc, kind: e.target.value })}><option value="bearer">Bearer token</option><option value="header">Custom header</option><option value="basic">Basic (user:password)</option></select></label>
            {nc.kind === "header" && <label>Header name<input value={nc.header} aria-label="Header name" placeholder="X-Api-Key" onChange={(e) => setNc({ ...nc, header: e.target.value })} /></label>}
            <label>Secret<input type="password" value={nc.value} aria-label="Credential secret" autoComplete="off" onChange={(e) => setNc({ ...nc, value: e.target.value })} /></label>
          </div>
          <div className="row"><button onClick={addCred} disabled={!nc.id || !nc.value}>Save credential</button></div>
        </div>)}
    </Modal>
  );
}
