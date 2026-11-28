"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import InfoTip from "@/components/InfoTip";
import BrandIcon from "@/components/BrandIcon";

type CField = { name: string; label: string; secret: boolean; required: boolean; help: string; set: boolean };
type Prov = { id: string; label: string; docs: string; cost_note: string; deploy_help: string; kinds: string[]; deletes: string[]; fields: CField[]; configured: boolean };
type Cloud = { providers: Prov[]; allow_writes: boolean; enabled: boolean };

/** Connect cloud accounts for the Cloud ML tool. Credentials are write-only; creating/deleting is off until switched on. */
export default function CloudAccounts() {
  const [c, setC] = useState<Cloud | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [clear, setClear] = useState<string[]>([]);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try { setC(await api<Cloud>("/api/catalog/cloud")); setErr(""); } catch (e) { setErr("Backend offline: " + (e as Error).message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function save(p: Prov) {
    const values: Record<string, string> = {};
    for (const f of p.fields) {
      if (clear.includes(f.name)) values[f.name] = "";
      else if ((vals[f.name] ?? "").trim()) values[f.name] = vals[f.name].trim();
    }
    try { setC(await api<Cloud>("/api/catalog/cloud", { method: "PUT", body: JSON.stringify({ values }) })); setOpen(null); setVals({}); setClear([]); toast(`${p.label} saved`); }
    catch (e) { setErr((e as Error).message); }
  }
  async function writes(on: boolean) {
    if (on && !window.confirm("Allow agents to create and delete cloud resources?\n\nCreating endpoints, servers or GPU machines starts a bill that runs until the resource is deleted. Every change still shows a preview and needs a clear yes in the chat.")) return;
    try { setC(await api<Cloud>("/api/catalog/cloud", { method: "PUT", body: JSON.stringify({ allow_writes: on }) })); toast(on ? "Create/delete allowed" : "Back to read-only"); } catch (e) { toast((e as Error).message, "err"); }
  }
  async function enable(on: boolean) {
    try { await api("/api/mcp/cloudml", { method: "PUT", body: JSON.stringify({ enabled: on }) }); await load(); } catch (e) { toast((e as Error).message, "err"); }
  }

  return (
    <section className="card" aria-labelledby="cloud-h">
      <div className="card-head"><h3 id="cloud-h" style={{ margin: 0 }}>Cloud accounts</h3><InfoTip id="cloud-ml" /><span className="grow" />
        {c && <label style={{ flexDirection: "row", gap: 6, alignItems: "center" }}><input type="checkbox" checked={c.enabled} onChange={(e) => enable(e.target.checked)} />Let agents use it</label>}</div>
      <p className="small mut">Let an agent look at your models, hosted endpoints and training jobs on AWS SageMaker, Google Vertex AI, Azure ML, DigitalOcean, RunPod and Oracle OCI. Keys are stored on this PC and never shown again.</p>
      {err && <p className="err" role="alert">{err}</p>}
      {c && (
        <label className="cloud-writes" style={{ flexDirection: "row", gap: 8, alignItems: "flex-start" }}>
          <input type="checkbox" checked={c.allow_writes} onChange={(e) => writes(e.target.checked)} />
          <span><b>Allow create and delete</b> <InfoTip id="cloud-writes" /><span className="small mut" style={{ display: "block" }}>Off by default: agents can only look. When on, they still must preview each change with its cost warning and get your yes in the chat.</span></span>
        </label>)}
      <div className="cat-grid">
        {(c?.providers ?? []).map((p) => (
          <div key={p.id} className="card cat-card" data-provider={p.id} style={{ boxShadow: "none" }}>
            <div className="card-head"><BrandIcon id={p.id} label={p.label} size={30} /><div className="cat-title"><b>{p.label}</b></div><span className={`pill ${p.configured ? "ok" : ""}`}>{p.configured ? "Connected" : "Not connected"}</span></div>
            <div className="small mut">Can look at: {p.kinds.join(", ")}</div>
            <p className="small" style={{ margin: "6px 0", color: "var(--warn-text)" }}>Cost: {p.cost_note}</p>
            <div className="row cat-actions"><button className={p.configured ? "ghost" : "acc"} onClick={() => { setOpen(open === p.id ? null : p.id); setVals({}); setClear([]); }}>{open === p.id ? "Close" : p.configured ? "Change keys" : "Connect"}</button>
              <a className="small" href={p.docs} target="_blank" rel="noreferrer">API reference</a></div>
            {open === p.id && (
              <div className="cat-panel">
                {p.fields.map((f) => (
                  <label key={f.name} style={{ marginTop: 6 }}>{f.label}{f.required ? "" : " (optional)"}
                    <input type={f.secret ? "password" : "text"} autoComplete="off" value={clear.includes(f.name) ? "" : vals[f.name] ?? ""} disabled={clear.includes(f.name)}
                      placeholder={f.set ? "saved — leave blank to keep" : ""} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })} />
                    {f.help && <span className="small mut">{f.help}</span>}
                    {f.set && <button type="button" className="ghost small" onClick={() => setClear(clear.includes(f.name) ? clear.filter((x) => x !== f.name) : [...clear, f.name])}>{clear.includes(f.name) ? "Keep it" : "Remove saved value"}</button>}
                  </label>))}
                <div className="small mut" style={{ marginTop: 6 }}>To create something an agent needs: <code>{p.deploy_help}</code></div>
                <div className="row" style={{ marginTop: 8, justifyContent: "flex-end" }}><button className="acc" onClick={() => save(p)}>Save</button></div>
              </div>)}
          </div>))}
      </div>
    </section>
  );
}
