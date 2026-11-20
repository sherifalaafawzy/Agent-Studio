"use client";
import { useEffect, useState } from "react";
import { api, Provider } from "@/lib/api";

function ProviderCard({ p, onSaved }: { p: Provider; onSaved: () => void }) {
  const [f, setF] = useState<Record<string, any>>({});
  const [msg, setMsg] = useState("");
  const val = (k: keyof Provider) => (k in f ? f[k] : (p[k] ?? "")) as any;
  const set = (k: string, v: any) => setF((s) => ({ ...s, [k]: v }));

  async function save() {
    try {
      const patch = { ...f };
      if ("cap_usd" in patch) patch.cap_usd = Number(patch.cap_usd);
      if ("max_output_tokens" in patch) patch.max_output_tokens = Number(patch.max_output_tokens);
      await api(`/api/providers/${p.id}`, { method: "PUT", body: JSON.stringify(patch) });
      setF({}); setMsg("Saved"); onSaved();
    } catch (e) { setMsg("⚠ " + (e as Error).message); }
  }
  const dirty = Object.keys(f).length > 0;

  return (
    <div className="card">
      <div className="row">
        <b>{p.label}</b>
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={!!val("enabled")} onChange={(e) => set("enabled", e.target.checked)} /> enabled
        </label>
        <span className="mut small grow">{p.catalog.note}</span>
      </div>
      <div className="grid" style={{ marginTop: 10 }}>
        <label>Spend cap (USD)<input type="number" min="0" step="0.01" value={val("cap_usd")} onChange={(e) => set("cap_usd", e.target.value)} /></label>
        <label>Cap window
          <select value={val("cap_period")} onChange={(e) => set("cap_period", e.target.value)}>
            <option value="total">lifetime total</option><option value="monthly">per month (UTC)</option><option value="daily">per day (UTC)</option>
          </select></label>
        <label>Max output tokens / call<input type="number" min="16" value={val("max_output_tokens")} onChange={(e) => set("max_output_tokens", e.target.value)} /></label>
        <label>Default model<input value={val("default_model")} onChange={(e) => set("default_model", e.target.value)} /></label>
        {p.kind === "vertex_ai" ? <>
          <label>GCP project ID<input value={val("vertex_project")} placeholder="my-throwaway-project" onChange={(e) => set("vertex_project", e.target.value)} /></label>
          <label>Location<input value={val("vertex_location")} onChange={(e) => set("vertex_location", e.target.value)} /></label>
          <label>Service-account JSON path<input value={val("vertex_credentials_path")} placeholder="blank = Application Default Credentials" onChange={(e) => set("vertex_credentials_path", e.target.value)} /></label>
        </> : p.catalog.needs_key ? <>
          <label>API key {p.has_api_key && <span className="pill ok">stored {p.api_key_hint}</span>}
            <input type="password" placeholder={p.has_api_key ? "(unchanged)" : "paste key"} value={f.api_key ?? ""} onChange={(e) => set("api_key", e.target.value)} /></label>
          <label>API base (optional)<input value={val("api_base")} onChange={(e) => set("api_base", e.target.value)} /></label>
        </> : p.kind === "ollama" ? <label>API base<input value={val("api_base")} placeholder="http://127.0.0.1:11434" onChange={(e) => set("api_base", e.target.value)} /></label> : null}
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="pri" disabled={!dirty} onClick={save}>Save</button><span className="small mut">{msg}</span>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const [ps, setPs] = useState<Provider[]>([]);
  const [err, setErr] = useState("");
  const load = () => api<Provider[]>("/api/providers").then((p) => { setPs(p); setErr(""); }).catch((e) => setErr("Backend offline: " + e.message));
  useEffect(() => { load(); }, []);
  return (
    <div>
      <h2>Settings</h2>
      {err && <p className="err">{err}</p>}
      <h3>Providers, keys &amp; per-provider caps</h3>
      <p className="mut small">Each provider has its own independent cap. A call is refused before it is sent if its worst-case cost (estimated input + full max output tokens) would exceed the remaining cap. Keep caps comfortably below your real billing limits.</p>
      {ps.map((p) => <ProviderCard key={p.id} p={p} onSaved={load} />)}
      <h3>MCP connections &amp; skills</h3>
      <div className="card mut">Curated MCP servers (filesystem, fetch, git, memory + one-click GitHub/Playwright/Brave/Context7) and the starter skills library land in a later phase.</div>
    </div>
  );
}
