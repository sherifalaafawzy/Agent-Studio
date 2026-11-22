"use client";
// Settings > Agents & sandbox > Sandbox kind: which kind of sandbox commands run in (a hardened Docker container, or the experimental Docker Sandbox
// micro VM), its limits, and a button that proves each kind works. Network on/off is the existing switch in the card above.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";
import { NumField } from "./SettingsKit";

type Kind = { id: string; label: string; available: boolean; detail: string; experimental: boolean; notice: string };
type Policy = { kind: string; memory: string; cpus: string; pids: number; read_only_root: boolean; microvm_enabled: boolean };
type Status = { policy: Policy; network: boolean; kinds: Kind[]; default: string };

export default function SandboxSettings() {
  const [st, setSt] = useState<Status | null>(null);
  const [err, setErr] = useState("");
  const [mem, setMem] = useState("");
  const [cpus, setCpus] = useState("");
  const [tests, setTests] = useState<Record<string, { ok: boolean; detail: string }>>({});
  const load = useCallback(() => api<Status>("/api/sandboxes").then((s) => { setSt(s); setMem(s.policy.memory); setCpus(s.policy.cpus); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  if (!st) return err ? <p className="err" role="alert">{err}</p> : null;
  const save = async (patch: Partial<Policy>) => {
    try { const s = await api<Status>("/api/sandboxes/config", { method: "PUT", body: JSON.stringify(patch) }); setSt(s); setMem(s.policy.memory); setCpus(s.policy.cpus); setErr(""); toast("Saved"); }
    catch (e) { setErr((e as Error).message); toast((e as Error).message, "err"); setMem(st.policy.memory); setCpus(st.policy.cpus); }
  };
  const test = async (id: string) => { try { setTests({ ...tests, [id]: await api(`/api/sandboxes/${id}/test`, { method: "POST" }) }); } catch (e) { setTests({ ...tests, [id]: { ok: false, detail: (e as Error).message } }); } };
  const p = st.policy;
  return (
    <div className="card" data-testid="sandbox-settings">
      <div className="card-head"><b>Sandbox kind and limits</b><InfoTip id="sandbox" /></div>
      <div className="card-desc">Commands agents run never touch your PC directly. Pick the default kind and how much it may use; an agent can choose its own kind in its editor.</div>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="grid">
        <label>Default kind
          <select value={p.kind} aria-label="Default sandbox kind" onChange={(e) => save({ kind: e.target.value })}>{st.kinds.map((k) => <option key={k.id} value={k.id}>{k.label}{k.experimental ? " (experimental)" : ""}</option>)}</select></label>
        <label>Memory (for example 512m or 2g)<input value={mem} aria-label="Memory" onChange={(e) => setMem(e.target.value)} onBlur={() => { if (mem !== p.memory) void save({ memory: mem }); }} /></label>
        <label>CPUs<input value={cpus} aria-label="CPUs" onChange={(e) => setCpus(e.target.value)} onBlur={() => { if (cpus !== p.cpus) void save({ cpus }); }} /></label>
        <NumField label="Most processes" value={p.pids} min={32} max={4096} onSave={(n) => save({ pids: n })} />
      </div>
      <div className="row" style={{ padding: "8px 0" }}>
        <div className="grow"><b>Read-only root file system</b><div className="small mut">The container cannot change its own system files, only the workspace and a small temporary area. Some tools that install things for the user need this off.</div></div>
        <label className="switch"><input type="checkbox" aria-label="Read-only root file system" checked={p.read_only_root} onChange={(e) => save({ read_only_root: e.target.checked })} /><i /></label>
      </div>
      <div className="row" style={{ padding: "8px 0" }}>
        <div className="grow"><b>Allow the micro VM sandbox (experimental)</b><InfoTip id="micro-vm" /><div className="small mut">Off by default and never switched on by an import. Needs Docker&apos;s <code>sbx</code> program.</div></div>
        <label className="switch"><input type="checkbox" aria-label="Allow the micro VM sandbox" checked={p.microvm_enabled} onChange={(e) => save({ microvm_enabled: e.target.checked })} /><i /></label>
      </div>
      <div className="lbl">Status</div>
      {st.kinds.map((k) => (
        <div key={k.id} data-testid={`sandbox-kind-${k.id}`} style={{ padding: "6px 0" }}>
          <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
            <b>{k.label}</b><span className={`pill ${k.available ? "ok" : "bad"}`}>{k.available ? "ready" : "not ready"}</span><span className="grow" />
            <button className="small" onClick={() => test(k.id)}>Test</button>
            {tests[k.id] && <span className={`pill ${tests[k.id].ok ? "ok" : "bad"}`} role="status">{tests[k.id].detail}</span>}
          </div>
          <div className="small mut">{k.detail}</div>
        </div>))}
    </div>
  );
}
