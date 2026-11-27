"use client";
// The settings of a Webhook box that live outside the workflow: its secret address (shown once), when it was last used, and whether webhooks are switched on at all.
import { useCallback, useEffect, useState } from "react";
import { api, apiOrigin } from "@/lib/api";
import InfoTip from "@/components/InfoTip";
import { toast } from "@/components/Nav";
import { ago } from "@/lib/workflows";

type Status = { exists: boolean; created: number | null; last_used: number | null; calls: number; webhooks_enabled: boolean };

export default function WebhookBox({ workflowId, saved }: { workflowId: string; saved: boolean }) {
  const [st, setSt] = useState<Status | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<Status>(`/api/workflows/${workflowId}/hook`).then(setSt).catch((e) => setErr(e.message)), [workflowId]);
  useEffect(() => { load(); }, [load]);
  const make = async () => {
    setErr("");
    try { const r = await api<{ path: string }>(`/api/workflows/${workflowId}/hook`, { method: "POST" }); setSecret(apiOrigin() + r.path); load(); }
    catch (e) { setErr((e as Error).message); }
  };
  const remove = async () => { setErr(""); try { await api(`/api/workflows/${workflowId}/hook`, { method: "DELETE" }); setSecret(null); load(); } catch (e) { setErr((e as Error).message); } };
  return (
    <section className="card" data-testid="webhook-box" style={{ marginTop: 12 }}>
      <div className="row"><b className="small">Secret address</b><InfoTip id="wf-webhook" /></div>
      {st && !st.webhooks_enabled && <div className="small" role="status" style={{ color: "var(--warn-text)" }}>Webhooks are switched off. Turn them on in the workflow settings (the Settings button on the Flows tab).</div>}
      {st?.exists
        ? <div className="small mut">Made {ago(st.created)} · called {st.calls} time(s){st.last_used ? ` · last ${ago(st.last_used)}` : ""}</div>
        : <div className="small mut">No address yet. {saved ? "Make one to let something call this workflow." : "Save a version with this Webhook box first."}</div>}
      {secret && (
        <div className="small" style={{ marginTop: 6 }} data-testid="webhook-secret">
          <b>Copy it now: it is shown only once.</b>
          <input readOnly value={secret} aria-label="Webhook address" onFocus={(e) => e.currentTarget.select()} />
          <button className="small" style={{ marginTop: 4 }} onClick={() => { navigator.clipboard?.writeText(secret); toast("Address copied"); }}>Copy</button>
        </div>)}
      <div className="row" style={{ marginTop: 6 }}>
        <button onClick={make} disabled={!saved}>{st?.exists ? "Make a new address (replaces the old)" : "Make the address"}</button>
        {st?.exists && <button onClick={remove}>Remove</button>}
      </div>
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", marginTop: 6 }}>{err}</div>}
    </section>
  );
}
