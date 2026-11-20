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
        <label>Pro-tier model<input value={val("pro_model")} onChange={(e) => set("pro_model", e.target.value)} /></label>
        <label>Flash-tier model<input value={val("flash_model")} onChange={(e) => set("flash_model", e.target.value)} /></label>
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

type Mcp = { id: string; label: string; description: string; transport: string; requires_env: string[]; env_set: Record<string, boolean>;
  enabled: boolean; default_on: boolean; status: string; builtin: boolean; ready: boolean };
type Skill = { name: string; description: string; mcp: string[]; source: string };

function McpRow({ m, onSaved }: { m: Mcp; onSaved: () => void }) {
  const [env, setEnv] = useState<Record<string, string>>({});
  const save = async (patch: any) => { await api(`/api/mcp/${m.id}`, { method: "PUT", body: JSON.stringify(patch) }); setEnv({}); onSaved(); };
  return (
    <div className="card">
      <div className="row">
        <b>{m.label}</b>
        <span className={`pill ${m.ready ? "ok" : ""}`}>{m.ready ? "ready" : m.status === "pending_approval" ? "pending your approval" : m.enabled ? "needs config" : "off"}</span>
        {m.default_on && <span className="pill">default</span>}
        <span className="mut small grow">{m.description}</span>
        <button className={m.enabled ? "" : "pri"} onClick={() => save({ enabled: !m.enabled })}>{m.enabled ? "Disable" : m.status === "pending_approval" ? "Review & approve" : "Enable"}</button>
        {!m.builtin && <button onClick={async () => { await api(`/api/mcp/${m.id}`, { method: "DELETE" }); onSaved(); }}>Delete</button>}
      </div>
      {m.requires_env.map((k) => (
        <div className="row" key={k} style={{ marginTop: 8 }}>
          <label className="grow">{k} {m.env_set[k] && <span className="pill ok">stored</span>}
            <input type="password" placeholder={m.env_set[k] ? "(unchanged)" : "paste value"} value={env[k] ?? ""} onChange={(e) => setEnv({ ...env, [k]: e.target.value })} /></label>
          <button disabled={!env[k]} onClick={() => save({ env: { [k]: env[k] }, enabled: true })}>Save &amp; enable</button>
        </div>
      ))}
    </div>
  );
}

export default function SettingsPage() {
  const [ps, setPs] = useState<Provider[]>([]);
  const [mcp, setMcp] = useState<Mcp[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [hs, setHs] = useState<any>(null);
  const [sb, setSb] = useState<any>(null);
  const [err, setErr] = useState("");
  const load = () => Promise.all([api<Provider[]>("/api/providers"), api<Mcp[]>("/api/mcp"), api<Skill[]>("/api/skills"), api("/api/settings"), api("/api/sandbox/status")])
    .then(([p, m, s, h, b]) => { setPs(p); setMcp(m); setSkills(s); setHs(h); setSb(b); setErr(""); }).catch((e) => setErr("Backend offline: " + e.message));
  useEffect(() => { load(); }, []);
  const putSettings = async (patch: any) => { setHs(await api("/api/settings", { method: "PUT", body: JSON.stringify(patch) })); };
  return (
    <div>
      <h2>Settings</h2>
      {err && <p className="err">{err}</p>}
      <h3>Providers, keys &amp; per-provider caps</h3>
      <p className="mut small">Each provider has its own independent cap. A call is refused before it is sent if its worst-case cost (estimated input + full max output tokens) would exceed the remaining cap. Keep caps comfortably below your real billing limits. Pro/Flash tier models are what agents resolve to.</p>
      {ps.map((p) => <ProviderCard key={p.id} p={p} onSaved={load} />)}

      <h3>Agent harness &amp; Docker sandbox</h3>
      {hs && sb && <div className="card">
        <div className="row">
          <span className={`pill ${sb.docker && sb.image ? "ok" : "bad"}`}>sandbox: {sb.detail}</span>
          <span className="mut small">Shell commands never run on the host: if Docker or the image is missing they are refused (fail-closed).</span>
        </div>
        <div className="grid" style={{ marginTop: 10 }}>
          <label>Default workspace directory (absolute path)
            <input defaultValue={hs.workspace_dir} onBlur={(e) => e.target.value !== hs.workspace_dir && putSettings({ workspace_dir: e.target.value })} /></label>
          <label>Max model calls per turn (loop guard)
            <input type="number" min={1} defaultValue={hs.max_llm_calls_per_turn} onBlur={(e) => putSettings({ max_llm_calls_per_turn: Number(e.target.value) })} /></label>
          <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={hs.sandbox_network} onChange={(e) => putSettings({ sandbox_network: e.target.checked })} /> allow network inside sandbox</label>
        </div>
      </div>}

      <h3>MCP connections</h3>
      <p className="mut small">Servers run on your machine (not in the sandbox) at pinned versions. Filesystem/Git are scoped to the workspace. Agent-proposed servers stay disabled until you approve them here.</p>
      {mcp.map((m) => <McpRow key={m.id} m={m} onSaved={load} />)}

      <h3>Skills</h3>
      <div className="card">
        <table><thead><tr><th>Skill</th><th>When to use</th><th>MCP</th><th></th></tr></thead><tbody>
          {skills.map((s) => <tr key={s.name}><td><b>{s.name}</b> {s.source === "user" && <span className="pill">agent/user</span>}</td><td className="mut">{s.description}</td>
            <td>{s.mcp.join(", ")}</td><td>{s.source === "user" && <button onClick={async () => { await api(`/api/skills/${s.name}`, { method: "DELETE" }); load(); }}>Delete</button>}</td></tr>)}
        </tbody></table>
      </div>
    </div>
  );
}
