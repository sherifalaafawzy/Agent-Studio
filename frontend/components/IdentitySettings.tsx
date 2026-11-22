"use client";
// Agent identity: every agent has a SPIFFE-style id and can hold short-lived scoped passes; hand-overs between agents are checked and logged
// (RFC 8693 token exchange). The built-in issuer is the default; Okta, Entra, Keycloak, any OIDC server or SPIRE can issue instead.
import { useCallback, useEffect, useState } from "react";
import { API_BASE, api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";
import KindFields, { type KField } from "./KindFields";
import { NumField } from "./SettingsKit";

type Prov = { id: string; label: string; summary: string; exchange: string; fields: KField[] };
type Cfg = { trust_domain: string; issuer: string; ttl_s: number; enforce: boolean; active: string; provider: { kind: string; params: Record<string, any>; secrets_set: string[] }; providers: Prov[] };
type AgentId = { id: string; name: string; spiffe_id: string; scopes: string[]; scopes_automatic: boolean; delegates: string[] };
type Ev = { ts: number; event: string; subject: string; actor: string; audience: string; scopes: string; detail: string };
const EXCHANGE: Record<string, string> = { rfc8693: "Hand-overs use RFC 8693 token exchange.", obo: "Hand-overs use Microsoft's on-behalf-of grant, which works only for signed-in users' tokens.", none: "Tokens cannot be exchanged: hand-overs are still checked and logged here." };

export default function IdentitySettings() {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [agents, setAgents] = useState<AgentId[]>([]);
  const [events, setEvents] = useState<Ev[]>([]);
  const [err, setErr] = useState("");
  const [kind, setKind] = useState("local");
  const [params, setParams] = useState<Record<string, any>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [domain, setDomain] = useState("");
  const [issuer, setIssuer] = useState("");

  const load = useCallback(() => Promise.all([api<Cfg>("/api/identity/config"), api<{ agents: AgentId[] }>("/api/identity/agents"), api<{ events: Ev[] }>("/api/identity/events?limit=8")])
    .then(([c, a, e]) => { setCfg(c); setKind(c.provider.kind); setParams(c.provider.params); setDomain(c.trust_domain); setIssuer(c.issuer); setAgents(a.agents); setEvents(e.events); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);

  const save = async (patch: Record<string, unknown>, ok = "Saved") => {
    try { await api("/api/identity/config", { method: "PUT", body: JSON.stringify(patch) }); toast(ok); await load(); return true; }
    catch (e) { setErr((e as Error).message); toast((e as Error).message, "err"); return false; }
  };
  if (!cfg) return err ? <p className="err" role="alert">{err}</p> : null;

  const prov = cfg.providers.find((p) => p.id === kind)!;
  const rotate = async () => {
    if (!confirm("Create a new signing key? Every token issued so far stops being valid.")) return;
    try { await api("/api/identity/rotate-key", { method: "POST" }); toast("New signing key created"); await load(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="card" data-tour="identity">
      <div className="card-head"><b>Agent identity</b><InfoTip id="identity" /><span className="pill">{cfg.active === "local" ? "built-in issuer" : prov.label}</span></div>
      <p className="mut small">Each agent has an identity of its own, like <code>spiffe://{cfg.trust_domain}/agent/engineer</code>. Passes are short-lived and say what the agent may do; when one agent hands work to another, the pass can only get narrower and the hand-over is logged.</p>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="grid">
        <label>Trust domain<input value={domain} onChange={(e) => setDomain(e.target.value)} onBlur={() => domain !== cfg.trust_domain && save({ trust_domain: domain })} /></label>
        <label>Issuer address<input value={issuer} onChange={(e) => setIssuer(e.target.value)} onBlur={() => issuer !== cfg.issuer && save({ issuer })} /></label>
        <NumField label="Pass lifetime (seconds)" value={cfg.ttl_s} min={30} max={3600} onSave={(n) => save({ ttl_s: n })} />
      </div>
      <div className="row" style={{ padding: "10px 0" }}>
        <div className="grow"><b>Enforce hand-overs</b><div className="small mut">Off: a hand-over an agent is not allowed to make is only recorded. On: it is refused.</div></div>
        <label className="switch"><input type="checkbox" aria-label="Enforce hand-overs" checked={cfg.enforce} onChange={(e) => save({ enforce: e.target.checked })} /><i /></label>
      </div>

      <div className="lbl">Who issues the passes</div>
      <label>Identity provider
        <select value={kind} onChange={(e) => { setKind(e.target.value); setParams(e.target.value === cfg.provider.kind ? cfg.provider.params : {}); setSecrets({}); }}>
          {cfg.providers.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
      <p className="small mut">{prov.summary} {EXCHANGE[prov.exchange]}{kind !== "local" && " It identifies Agent Studio to that system; telling agents apart stays with the local log."}</p>
      {kind !== "local" && <KindFields fields={prov.fields} params={params} onParams={setParams} secrets={secrets} onSecrets={setSecrets} secretsSet={kind === cfg.provider.kind ? cfg.provider.secrets_set : []} />}
      <div className="row" style={{ marginTop: 8 }}>
        <button className="pri" disabled={kind === cfg.provider.kind && kind === "local"} onClick={async () => { if (await save({ provider: { kind, params, secrets: Object.fromEntries(Object.entries(secrets).filter(([, v]) => v)) } }, "Identity provider saved")) setSecrets({}); }}>Save provider</button>
        <button onClick={rotate}>New signing key</button>
        <a className="small" href={`${API_BASE}/api/identity/jwks.json`} target="_blank" rel="noreferrer">Public keys (JWKS)</a>
      </div>

      <div className="lbl" style={{ marginTop: 16 }}>Agent identities</div>
      <table className="tbl"><thead><tr><th>Agent</th><th>Identity</th><th>May do</th></tr></thead><tbody>
        {agents.map((a) => <tr key={a.id}><td>{a.name}</td><td className="small" style={{ overflowWrap: "anywhere" }}>{a.spiffe_id}</td>
          <td className="small">{a.scopes.length ? a.scopes.join(", ") : "nothing"}{a.scopes_automatic ? " (from its tools)" : ""}</td></tr>)}</tbody></table>
      <p className="small mut">Change what one agent may do in <a href="/agents/">Agent Studio</a>.</p>

      <div className="lbl" style={{ marginTop: 12 }}>Recent passes and hand-overs</div>
      {events.length === 0 && <div className="small mut">Nothing yet.</div>}
      {events.map((e, i) => <div key={i} className="small" style={{ padding: "3px 0" }}><span className="mut">{new Date(e.ts * 1000).toLocaleString()}</span> · <span className={`pill ${e.event === "refused" ? "bad" : ""}`}>{e.event}</span> {e.detail || e.audience}{e.scopes ? ` · ${e.scopes}` : ""}</div>)}
    </div>
  );
}
