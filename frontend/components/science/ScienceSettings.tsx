"use client";
// Science Lab settings: whether it is on, which data sources are used, free keys (write-only), the time between requests, and the budgets. The safety level can be raised, never lowered.
import { useState } from "react";
import { api } from "@/lib/api";
import { Config, Connector } from "@/lib/science";
import { toast } from "../Nav";

export default function ScienceSettings({ cfg, connectors, onChange }: { cfg: Config; connectors: Connector[]; onChange: (c: Config, k: Connector[]) => void }) {
  const [draft, setDraft] = useState<Config>(cfg);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [err, setErr] = useState("");
  const toggle = (id: string, on: boolean) => setDraft({ ...draft, disabled_connectors: on ? draft.disabled_connectors.filter((x) => x !== id) : [...draft.disabled_connectors, id] });
  const save = async () => {
    try {
      let k = connectors;
      for (const [id, key] of Object.entries(keys)) if (key !== "") { const r = await api<{ connectors: Connector[] }>(`/api/science/keys/${id}`, { method: "PUT", body: JSON.stringify({ key }) }); k = r.connectors; }
      const body = { enabled: draft.enabled, safety_level: draft.safety_level, disabled_connectors: draft.disabled_connectors, rate_overrides: draft.rate_overrides, contact_email: draft.contact_email,
        max_calls_per_investigation: draft.max_calls_per_investigation, cache_minutes: draft.cache_minutes, max_investigations: draft.max_investigations };
      const c = await api<Config>("/api/science/config", { method: "PUT", body: JSON.stringify(body) });
      setKeys({}); setErr(""); setDraft(c); onChange(c, k); toast("Science settings saved");
    } catch (e) { setErr((e as Error).message); }
  };
  const num = (k: "max_calls_per_investigation" | "cache_minutes" | "max_investigations", v: string) => setDraft({ ...draft, [k]: Number(v) });
  return (
    <div className="card" data-testid="science-settings">
      <div className="card-head"><b>Science Lab settings</b></div>
      <div className="row" style={{ gap: 18, flexWrap: "wrap" }}>
        <label className="check"><input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} /> The Science Lab is on</label>
        <label>Safety level
          <select value={draft.safety_level} aria-label="Safety level" onChange={(e) => setDraft({ ...draft, safety_level: e.target.value })}>
            {cfg.safety_levels.map((l) => <option key={l} value={l} disabled={cfg.safety_levels.indexOf(l) < cfg.safety_levels.indexOf(cfg.safety_level)}>{l}</option>)}
          </select></label>
      </div>
      <div className="small mut">The level can be raised but never lowered, and there is no switch that turns the safety screen off.</div>
      <div style={{ overflowX: "auto", marginTop: 8 }}>
        <table className="sci-kv" aria-label="Data sources"><thead><tr><th>On</th><th>Source</th><th>Seconds between requests</th><th>Free key (write-only)</th></tr></thead>
          <tbody>{connectors.map((c) => (
            <tr key={c.id}>
              <td><input type="checkbox" aria-label={`Use ${c.label}`} checked={!draft.disabled_connectors.includes(c.id)} onChange={(e) => toggle(c.id, e.target.checked)} /></td>
              <td>{c.label}<div className="small mut">{c.limits}</div></td>
              <td><input type="number" min={c.interval_s} max={60} step={0.1} style={{ width: 90 }} aria-label={`Seconds between requests to ${c.label}`} defaultValue={draft.rate_overrides[c.id] ?? c.interval_s}
                onChange={(e) => { const v = Number(e.target.value); const ro = { ...draft.rate_overrides }; if (v > c.interval_s) ro[c.id] = v; else delete ro[c.id]; setDraft({ ...draft, rate_overrides: ro }); }} /></td>
              <td>{(c.needs_key || c.key_help) ? <input type="password" autoComplete="off" aria-label={`Key for ${c.label}`} placeholder={c.key_set ? "set (type to replace)" : c.needs_key ? "needed" : "optional"} value={keys[c.id] ?? ""} onChange={(e) => setKeys({ ...keys, [c.id]: e.target.value })} /> : <span className="mut small">none needed</span>}</td>
            </tr>))}</tbody></table>
      </div>
      <div className="grid" style={{ marginTop: 8 }}>
        <label>Contact email for Crossref's polite pool (optional; sent to Crossref only)<input type="email" value={draft.contact_email} aria-label="Contact email" onChange={(e) => setDraft({ ...draft, contact_email: e.target.value })} /></label>
        <label>Data-source calls per investigation<input type="number" min={1} max={1000} value={draft.max_calls_per_investigation} aria-label="Calls per investigation" onChange={(e) => num("max_calls_per_investigation", e.target.value)} /></label>
        <label>Keep answers in memory for (minutes)<input type="number" min={0} max={1440} value={draft.cache_minutes} aria-label="Cache minutes" onChange={(e) => num("cache_minutes", e.target.value)} /></label>
        <label>Most investigations kept<input type="number" min={1} max={5000} value={draft.max_investigations} aria-label="Most investigations" onChange={(e) => num("max_investigations", e.target.value)} /></label>
      </div>
      {err && <div className="err small" role="alert">{err}</div>}
      <div className="row" style={{ marginTop: 8 }}><button className="pri" onClick={save}>Save Science settings</button></div>
    </div>
  );
}
