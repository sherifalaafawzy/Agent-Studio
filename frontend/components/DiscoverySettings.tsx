"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";

// Settings > Discovery & AI-BOM: publishing the AI bill of materials and an Agentic Resource Discovery (ARD) catalog.
// Publishing is OFF by default. Everything shown here is checked by the backend; this component only edits and displays it.

export type DiscoveryCfg = {
  enabled: boolean; publisher: string; base_url: string; level: string; level_confirmed: boolean; sign: boolean; port: number;
  publish: Record<string, boolean>; push_url: string; push_project: string; push_version: string; push_level: string;
  types: { id: string; label: string }[]; has_token: boolean; has_push_key: boolean; effective_base_url: string; not_ready: string[]; publisher_problem: string | null;
};
export type Level = { id: string; description: string };
type ServerState = { running: boolean; port: number | null; local_url: string | null; error: string; not_ready?: string[] };
type Keys = { exists: boolean; jwk: { kid: string } | null; scheme: string; note: string };

const msg = (e: unknown) => (e as Error).message;

function Switch({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <div className="row" style={{ alignItems: "flex-start", gap: 12, padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
      <div className="grow dc-wrap"><div style={{ fontWeight: 500 }}>{label}</div>{hint && <div className="small mut" style={{ marginTop: 2 }}>{hint}</div>}</div>
      <label className="switch"><input type="checkbox" aria-label={label} checked={checked} onChange={(e) => onChange(e.target.checked)} /><i /></label>
    </div>
  );
}

export default function DiscoverySettings() {
  const [cfg, setCfg] = useState<DiscoveryCfg | null>(null);
  const [draft, setDraft] = useState<Partial<DiscoveryCfg>>({});
  const [pushKey, setPushKey] = useState("");
  const [levels, setLevels] = useState<Level[]>([]);
  const [server, setServer] = useState<ServerState | null>(null);
  const [keys, setKeys] = useState<Keys | null>(null);
  const [token, setToken] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, l, s, k] = await Promise.all([api<DiscoveryCfg>("/api/discovery/settings"), api<Level[]>("/api/aibom/levels"),
        api<ServerState>("/api/discovery/server"), api<Keys>("/api/discovery/keys")]);
      setCfg(c); setLevels(l); setServer(s); setKeys(k); setDraft({});
    } catch (e) { setNote("⚠ " + msg(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!cfg) return <div className="card"><span className="mut">{note || "Loading…"}</span></div>;
  const v = { ...cfg, ...draft } as DiscoveryCfg;
  const set = (patch: Partial<DiscoveryCfg>) => setDraft((d) => ({ ...d, ...patch }));
  const dirty = Object.keys(draft).length > 0 || pushKey !== "";
  const levelInfo = levels.find((l) => l.id === v.level);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(true); setNote("");
    try { await fn(); } catch (e) { setNote(`⚠ ${label}: ${msg(e)}`); } finally { setBusy(false); }
  }
  const save = () => run("Save", async () => {
    const body: Record<string, unknown> = { ...draft };
    if (pushKey) body.push_api_key = pushKey;
    await api("/api/discovery/settings", { method: "PUT", body: JSON.stringify(body) });
    setPushKey(""); await load(); setNote("Saved");
  });
  const newToken = () => run("Token", async () => { const r = await api<{ token: string }>("/api/discovery/token", { method: "POST" }); setToken(r.token); await load(); });
  const dropToken = () => run("Token", async () => { await api("/api/discovery/token", { method: "DELETE" }); setToken(""); await load(); });
  const genKey = () => run("Key", async () => { await api("/api/discovery/keys", { method: "POST" }); await load(); setNote("New signing key created. Entries are signed with it from the next build."); });
  const delKey = () => run("Key", async () => { await api("/api/discovery/keys", { method: "DELETE" }); await load(); });
  const start = () => run("Server", async () => { await api("/api/discovery/server/start", { method: "POST", body: JSON.stringify({}) }); await load(); });
  const stop = () => run("Server", async () => { await api("/api/discovery/server/stop", { method: "POST" }); await load(); });
  const copy = (text: string) => { navigator.clipboard?.writeText(text); toast("Copied"); };

  return (<>
    <div className="card" data-tour="discovery-settings">
      <div className="card-head"><b>Discovery &amp; AI-BOM</b><span className={`pill ${cfg.enabled && !cfg.not_ready.length ? "ok" : ""}`}>{cfg.enabled ? (cfg.not_ready.length ? "on, not ready" : "on") : "off"}</span></div>
      <p className="card-desc">
        Publish an <b>AI bill of materials</b> (CycloneDX 1.7: dependencies, models, agents, MCP servers, skills, knowledge bases) and an
        <b> Agentic Resource Discovery</b> catalog (<code>/.well-known/ard.json</code>, spec v0.91, still a proposal) so other tools can find what you run.
        Nothing is published unless you switch it on here. You can always preview and download the documents on the <Link href="/discovery/">Discovery page</Link> without publishing.
      </p>
      <Switch checked={v.enabled} onChange={(x) => set({ enabled: x })} label="Publishing on" hint="Off: the discovery server refuses to start and serves nothing." />
      <div className="grid" style={{ marginTop: 12 }}>
        <label>Publisher domain
          <input aria-label="Publisher domain" value={v.publisher} placeholder="agents.yourcompany.com" onChange={(e) => set({ publisher: e.target.value })} />
          <span className="small mut">A domain you control. ARD identifiers look like <code>urn:air:&lt;domain&gt;:…</code>. No placeholder domains are accepted.</span></label>
        <label>Public address (optional)
          <input aria-label="Public address" value={v.base_url} placeholder={`https://${v.publisher || "agents.yourcompany.com"}`} onChange={(e) => set({ base_url: e.target.value })} />
          <span className="small mut">Where the files will be reachable, if not at the root of the domain. Must be https on your domain.</span></label>
      </div>
      {cfg.publisher_problem && <div className="dc-banner bad" role="alert" style={{ marginTop: 8 }}>{cfg.publisher_problem}</div>}

      <h4 className="kn-h">What to publish</h4>
      {cfg.types.map((t) => <Switch key={t.id} checked={!!v.publish[t.id]} label={t.label} onChange={(x) => set({ publish: { ...v.publish, [t.id]: x } })} />)}

      <h4 className="kn-h">Redaction level</h4>
      <label>Level
        <select aria-label="Redaction level" value={v.level} onChange={(e) => set({ level: e.target.value })}>
          {levels.map((l) => <option key={l.id} value={l.id}>{l.id}</option>)}
        </select></label>
      {levelInfo && <div className="small mut" style={{ marginTop: 6 }}>{levelInfo.description}</div>}
      <div className="small mut" style={{ marginTop: 6 }}>At every level: no API keys, tokens, passwords, environment values, chats, document contents, local paths or user names.</div>
      {v.level !== "public" && (<>
        <Switch checked={v.level_confirmed} label={`I understand the '${v.level}' level shares more than a public BOM`} onChange={(x) => set({ level_confirmed: x })}
          hint="It is served only with an access token, and without the cross-origin header the public level uses." />
        <div className="row" style={{ marginTop: 8 }}>
          <button onClick={newToken} disabled={busy}>{cfg.has_token ? "Replace access token" : "Generate access token"}</button>
          {cfg.has_token && <button onClick={dropToken} disabled={busy}>Remove token</button>}
          <span className="small mut">{cfg.has_token ? "A token is set (only its hash is kept)." : "No token yet."}</span>
        </div>
        {token && <div className="dc-banner warn" role="status" style={{ marginTop: 8 }}>Copy this token now; it is shown once: <code className="dc-wrap">{token}</code>
          <button className="chip small" style={{ marginLeft: 8 }} onClick={() => copy(token)}>Copy</button></div>}
      </>)}

      <h4 className="kn-h">Signing</h4>
      <Switch checked={v.sign} onChange={(x) => set({ sign: x })} label="Sign each catalog entry (detached JWS, EdDSA)"
        hint="ARD defines no signing or verification procedure of its own. A signature here only shows that the holder of this key signed that exact entry; consumers must agree to the scheme and get the public key from a source they trust." />
      <div className="row" style={{ marginTop: 8 }}>
        <button onClick={genKey} disabled={busy}>{keys?.exists ? "Rotate key" : "Generate key"}</button>
        {keys?.exists && <button onClick={delKey} disabled={busy}>Delete key</button>}
        <span className="small mut dc-wrap">{keys?.exists ? `Key id ${keys.jwk?.kid}. The private key stays in Agent Studio's data folder.` : "No signing key yet."}</span>
      </div>

      <div className="row" style={{ marginTop: 14 }}>
        <button className="pri" disabled={!dirty || busy} onClick={save}>Save</button>
        <span className="small mut" role="status">{note}</span>
      </div>
      {cfg.not_ready.length > 0 && <ul className="small mut dc-list" aria-label="Not ready because">{cfg.not_ready.map((r) => <li key={r}>{r}</li>)}</ul>}
    </div>

    <div className="card">
      <div className="card-head"><b>Discovery server</b><span className={`pill ${server?.running ? "ok" : ""}`}>{server?.running ? "running" : "stopped"}</span></div>
      <p className="card-desc">
        A small read-only server, separate from Agent Studio&apos;s own API, that serves only what you chose above. It listens on this PC (127.0.0.1) only.
        To make it discoverable, put <b>your own domain or tunnel</b> in front of it; Agent Studio does not set that up and never exposes anything else.
        Or skip the server and upload the exported files to your web host.
      </p>
      <div className="row">
        {server?.running
          ? <button onClick={stop} disabled={busy}>Stop server</button>
          : <button className="acc" onClick={start} disabled={busy || cfg.not_ready.length > 0}>Start server</button>}
        {server?.running && server.local_url && <span className="small dc-wrap">Local address <code>{server.local_url}</code> <button className="chip small" onClick={() => copy(server.local_url!)}>Copy</button></span>}
        {!server?.running && <span className="small mut">Port {v.port}</span>}
      </div>
      {server?.error && <div className="dc-banner bad" role="alert" style={{ marginTop: 8 }}>{server.error}</div>}
    </div>

    <div className="card">
      <div className="card-head"><b>Push to Dependency-Track</b><span className="pill">{cfg.push_url ? "configured" : "not set"}</span></div>
      <p className="card-desc">Optionally send the BOM to a Dependency-Track server. Nothing is sent until you press Send on the Discovery page, after seeing exactly what will go and where.</p>
      <div className="grid">
        <label>Server address<input aria-label="Dependency-Track address" value={v.push_url} placeholder="https://dependency-track.yourcompany.com" onChange={(e) => set({ push_url: e.target.value })} /></label>
        <label>API key<input aria-label="Dependency-Track API key" type="password" value={pushKey} placeholder={cfg.has_push_key ? "set (not shown)" : "odt_…"} onChange={(e) => setPushKey(e.target.value)} /></label>
        <label>Project name<input aria-label="Project name" value={v.push_project} onChange={(e) => set({ push_project: e.target.value })} /></label>
        <label>Project version<input aria-label="Project version" value={v.push_version} placeholder="the app version" onChange={(e) => set({ push_version: e.target.value })} /></label>
        <label>Redaction level of what is sent
          <select aria-label="Push redaction level" value={v.push_level} onChange={(e) => set({ push_level: e.target.value })}>{levels.map((l) => <option key={l.id} value={l.id}>{l.id}</option>)}</select></label>
      </div>
      <div className="small mut" style={{ marginTop: 8 }}>Use Save above to store these. The key is write-only: it is never shown again.</div>
    </div>
  </>);
}
