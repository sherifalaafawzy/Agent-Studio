"use client";
// Settings > API & A2A: switch the OpenAI-compatible API and A2A on, choose which agents are reachable, manage keys (shown once,
// stored only as hashes) and register remote A2A agents that your agents may call.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";

type Key = { id: string; name: string; prefix: string; agents: string[]; rate_per_min: number; created: number; last_used: number | null; expires: number | null; revoked: boolean; uses: number };
type Remote = { id: string; label: string; url: string; enabled: boolean; has_token: boolean; name?: string | null };
type Cfg = { api_enabled: boolean; a2a_enabled: boolean; api_default_provider: string; base_url: string; a2a_base: string;
  agents: { id: string; name: string; api_access: boolean; a2a: boolean }[]; keys: Key[]; remotes: Remote[] };
type Provider = { id: string; label: string; enabled: boolean };
const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : "never");

export default function AccessSettings() {
  const [c, setC] = useState<Cfg | null>(null);
  const [ps, setPs] = useState<Provider[]>([]);
  const [err, setErr] = useState("");
  const [fresh, setFresh] = useState<{ id: string; key: string } | null>(null);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<string[] | "all">("all");
  const [rate, setRate] = useState(60);
  const [days, setDays] = useState("");
  const [rem, setRem] = useState({ id: "", label: "", url: "", token: "" });
  const [tests, setTests] = useState<Record<string, string>>({});
  const load = useCallback(() => api<Cfg>("/api/access/config").then((x) => { setC(x); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); api<Provider[]>("/api/providers").then(setPs).catch(() => setPs([])); }, [load]);
  const run = async (fn: () => Promise<unknown>, ok?: string) => { try { await fn(); if (ok) toast(ok); await load(); setErr(""); } catch (e) { setErr((e as Error).message); } };
  if (!c) return <div className="card" data-testid="access-settings">{err ? <span role="status" style={{ color: "var(--bad-text)" }}>Access settings could not be loaded: {err}</span> : "Loading…"}</div>;
  const put = (patch: object) => run(() => api("/api/access/config", { method: "PUT", body: JSON.stringify(patch) }));
  const createKey = () => run(async () => {
    const k = await api<{ id: string; key: string }>("/api/access/keys", { method: "POST", body: JSON.stringify({ name, agents: scope === "all" ? ["*"] : scope, rate_per_min: rate, expires_days: days ? Number(days) : null }) });
    setFresh({ id: k.id, key: k.key }); setName("");
  });
  const rotate = (k: Key) => run(async () => { const r = await api<{ id: string; key: string }>(`/api/access/keys/${k.id}/rotate`, { method: "POST" }); setFresh({ id: r.id, key: r.key }); });
  const snippet = `curl ${c.base_url}/chat/completions \\\n  -H "Authorization: Bearer asx_..." -H "Content-Type: application/json" \\\n  -d '{"model": "${c.agents.find((a) => a.api_access)?.id ?? "your_agent"}", "messages": [{"role": "user", "content": "Hello"}]}'`;
  return (
    <div data-testid="access-settings">
      <p className="page-sub">Let other programs and agents use your agents. Everything here is <b>off until you switch it on</b>, needs a key, and only reaches the agents you tick. Calls run through the normal engine, so spend limits, guardrails and memory still apply.</p>
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", margin: "6px 0" }}>{err}</div>}
      <div className="card">
        <div className="card-head"><b>Switches</b><InfoTip id="api-access" /></div>
        <label className="check"><input type="checkbox" checked={c.api_enabled} onChange={(e) => put({ api_enabled: e.target.checked })} /> <span>OpenAI-compatible API (<code>{c.base_url}</code>)</span></label>
        <label className="check" style={{ marginTop: 6 }}><input type="checkbox" checked={c.a2a_enabled} onChange={(e) => put({ a2a_enabled: e.target.checked })} /> <span>A2A (<code>{c.a2a_base}/&lt;agent&gt;/</code>)</span></label>
        <label style={{ marginTop: 8 }}>Provider for API calls to agents that have none of their own
          <select value={c.api_default_provider} aria-label="Default provider" onChange={(e) => put({ api_default_provider: e.target.value })}><option value="">first enabled provider</option>{ps.filter((p) => p.enabled).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
        <div className="small mut" style={{ marginTop: 6 }}>The server listens on this PC only. To reach it from elsewhere, put your own secure proxy in front of it.</div>
      </div>

      <div className="card">
        <div className="card-head"><b>Agents others may call</b></div>
        {c.agents.map((a) => (
          <div className="row" key={a.id} style={{ gap: 18 }} data-testid="agent-row">
            <span style={{ minWidth: 140 }}>{a.name}</span>
            <label className="check"><input type="checkbox" checked={a.api_access} onChange={(e) => run(() => api(`/api/agents/${a.id}`, { method: "PUT", body: JSON.stringify({ api_access: e.target.checked }) }))} aria-label={`${a.name} API access`} /> API</label>
            <label className="check"><input type="checkbox" checked={a.a2a} onChange={(e) => run(() => api(`/api/agents/${a.id}`, { method: "PUT", body: JSON.stringify({ a2a: e.target.checked }) }))} aria-label={`${a.name} A2A`} /> A2A</label>
          </div>))}
      </div>

      <div className="card">
        <div className="card-head"><b>API keys</b></div>
        {fresh && (
          <div className="card" role="status" data-testid="fresh-key" style={{ background: "var(--ok-soft)" }}>
            <b>Copy this key now: it is shown only once.</b>
            <div className="row" style={{ marginTop: 6 }}><code style={{ wordBreak: "break-all" }}>{fresh.key}</code><button onClick={() => { navigator.clipboard?.writeText(fresh.key); toast("Key copied"); }}>Copy</button><button onClick={() => setFresh(null)}>Done</button></div>
          </div>)}
        {c.keys.length === 0 && <div className="small mut">No keys yet.</div>}
        {c.keys.map((k) => (
          <div key={k.id} className="row" data-testid="key-row" style={{ gap: 10, flexWrap: "wrap", margin: "6px 0" }}>
            <b>{k.name}</b><code>{k.prefix}…</code>
            <span className="small mut">{k.agents.includes("*") ? "all agents" : k.agents.join(", ")} · {k.rate_per_min}/min · used {k.uses}× · last {when(k.last_used)}{k.expires ? ` · expires ${when(k.expires)}` : ""}</span>
            {k.revoked && <span className="pill bad">revoked</span>}
            <button onClick={() => rotate(k)}>Rotate</button>
            <button onClick={() => run(() => api(`/api/access/keys/${k.id}`, { method: "PUT", body: JSON.stringify({ revoked: !k.revoked }) }))}>{k.revoked ? "Re-enable" : "Revoke"}</button>
            <button onClick={() => run(() => api(`/api/access/keys/${k.id}`, { method: "DELETE" }), "Key deleted")}>Delete</button>
          </div>))}
        <div className="grid" style={{ marginTop: 10 }}>
          <label>New key name<input value={name} placeholder="e.g. my laptop script" aria-label="Key name" onChange={(e) => setName(e.target.value)} /></label>
          <label>Which agents<select value={scope === "all" ? "all" : "pick"} aria-label="Key scope" onChange={(e) => setScope(e.target.value === "all" ? "all" : [])}><option value="all">every agent with API access</option><option value="pick">only the ones I pick</option></select></label>
          <label>Requests per minute<input type="number" min={1} max={100000} value={rate} aria-label="Requests per minute" onChange={(e) => setRate(Number(e.target.value))} /></label>
          <label>Expires after (days, optional)<input type="number" min={1} value={days} aria-label="Expiry days" onChange={(e) => setDays(e.target.value)} /></label>
        </div>
        {scope !== "all" && <div className="row" style={{ flexWrap: "wrap", gap: 12, marginTop: 6 }}>{c.agents.map((a) => (
          <label key={a.id} className="check"><input type="checkbox" checked={scope.includes(a.id)} onChange={() => setScope(scope.includes(a.id) ? scope.filter((x) => x !== a.id) : [...scope, a.id])} /> {a.name}</label>))}</div>}
        <div className="row" style={{ marginTop: 8 }}><button className="pri" disabled={!name.trim() || (scope !== "all" && scope.length === 0)} onClick={createKey}>Create key</button></div>
        <pre className="small" style={{ whiteSpace: "pre-wrap", marginTop: 10 }} data-testid="snippet">{snippet}</pre>
      </div>

      <div className="card">
        <div className="card-head"><b>Remote agents your agents may call (A2A)</b></div>
        {c.remotes.length === 0 && <div className="small mut">None yet. Agents with the <code>call_remote_agent</code> tool can call the ones you add here.</div>}
        {c.remotes.map((r) => (
          <div key={r.id} className="row" data-testid="remote-row" style={{ gap: 10, flexWrap: "wrap", margin: "6px 0" }}>
            <b>{r.label}</b><code>{r.id}</code><span className="small mut">{r.url}{r.has_token ? " · token stored" : ""}</span>
            {tests[r.id] && <span className="small">{tests[r.id]}</span>}
            <button onClick={async () => { const t = await api<{ ok: boolean; name?: string; error?: string }>(`/api/access/remotes/${r.id}/test`, { method: "POST" }); setTests({ ...tests, [r.id]: t.ok ? `reachable: ${t.name}` : `failed: ${t.error}` }); }}>Test</button>
            <button onClick={() => run(() => api(`/api/access/remotes/${r.id}`, { method: "PUT", body: JSON.stringify({ enabled: !r.enabled }) }))}>{r.enabled ? "Disable" : "Enable"}</button>
            <button onClick={() => run(() => api(`/api/access/remotes/${r.id}`, { method: "DELETE" }))}>Remove</button>
          </div>))}
        <div className="grid" style={{ marginTop: 8 }}>
          <label>Id<input value={rem.id} placeholder="e.g. travel_agent" aria-label="Remote id" onChange={(e) => setRem({ ...rem, id: e.target.value.toLowerCase() })} /></label>
          <label>Name<input value={rem.label} aria-label="Remote name" onChange={(e) => setRem({ ...rem, label: e.target.value })} /></label>
          <label>Address<input value={rem.url} placeholder="https://agents.example.com/travel/" aria-label="Remote address" onChange={(e) => setRem({ ...rem, url: e.target.value })} /></label>
          <label>Bearer token (write-only)<input type="password" autoComplete="off" value={rem.token} aria-label="Remote token" onChange={(e) => setRem({ ...rem, token: e.target.value })} /></label>
        </div>
        <div className="row" style={{ marginTop: 8 }}><button className="pri" disabled={!rem.id || !rem.url} onClick={() => run(async () => { await api("/api/access/remotes", { method: "POST", body: JSON.stringify(rem) }); setRem({ id: "", label: "", url: "", token: "" }); }, "Remote agent added")}>Add remote agent</button></div>
      </div>
    </div>
  );
}
